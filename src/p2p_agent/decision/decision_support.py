"""Decision-support node — recommends an action with rationale + counterfactual.

Inputs: Classification (from the classifier), the invoice (extraction or
ground-truth), optional PO and goods-receipt context, and a list of retrieved
policy snippets from the RAG layer.

Output: a validated `Recommendation` pydantic instance. Same retry-on-bad-output
pattern as the classifier and extractor.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from p2p_agent.llm.client import ModelClient
from p2p_agent.llm.json_utils import extract_json_from_response
from p2p_agent.llm.prompts import load_prompt
from p2p_agent.models.classification import Classification
from p2p_agent.models.context import CaseContext
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.retrieval import RetrievedDoc


class DecisionError(Exception):
    """Raised when the model output cannot be parsed into a Recommendation."""


SYSTEM_PROMPT = load_prompt("decision_support")


def _format_policies(policies: list[RetrievedDoc]) -> str:
    if not policies:
        return "(none retrieved — reason from case facts alone)"
    lines: list[str] = []
    for p in policies:
        lines.append(f"### {p.id} — {p.title}\n{p.text}\n")
    return "\n".join(lines)


def _derive_po_context(case_context: CaseContext | None) -> dict[str, Any] | None:
    """Pull a PO-context-shaped dict out of the cross-case `case_context`.

    The decide prompt was originally written when the only way to pass PO data
    was as a flat dict. Now the pipeline carries the typed `CaseContext` — this
    helper bridges so decide always sees a populated PO context when one exists,
    fixing the bug where `escalate_to_buyer_for_retroactive_po` fired despite a
    valid PO being on file.
    """
    if case_context is None or case_context.po_record is None:
        return None
    po = case_context.po_record
    return {
        "id": po.id,
        "vendor_id": po.vendor_id,
        "total_authorized": po.total_authorized,
        "currency": po.currency,
        "payment_terms": po.payment_terms,
        "status": po.status.value if hasattr(po.status, "value") else str(po.status),
        "is_emergency": po.is_emergency,
        "fx_clause": po.fx_clause,
        "fx_rate": po.fx_rate,
        "department": po.department,
        "line_items": [
            {
                "line_no": li.line_no,
                "sku": li.sku,
                "quantity_authorized": li.quantity_authorized,
                "unit_price": li.unit_price,
                "line_total": li.line_total,
            }
            for li in po.line_items[:20]
        ],
        "approver_chain": [a.model_dump(mode="json") for a in po.approver_chain],
    }


def _derive_gr_context(case_context: CaseContext | None) -> dict[str, Any] | None:
    if case_context is None or case_context.goods_receipt is None:
        return None
    gr = case_context.goods_receipt
    return {
        "id": gr.id,
        "po_id": gr.po_id,
        "receipt_date": str(gr.receipt_date) if gr.receipt_date else None,
        "warehouse": gr.warehouse,
        "receiver": gr.receiver,
        "line_items": [
            {"line_no": gl.line_no, "sku": gl.sku, "quantity_received": gl.quantity_received}
            for gl in gr.line_items[:20]
        ],
    }


def _build_user_message(
    classification: Classification,
    invoice: dict[str, Any],
    po_context: dict[str, Any] | None,
    gr_context: dict[str, Any] | None,
    policies: list[RetrievedDoc],
    case_context: CaseContext | None = None,
) -> str:
    # If the caller didn't pass an old-style po_context dict but the case_context
    # has a PO record, derive po_context from it. Same for GR.
    if po_context is None and case_context is not None:
        po_context = _derive_po_context(case_context)
    if gr_context is None and case_context is not None:
        gr_context = _derive_gr_context(case_context)

    parts = [
        "Recommend an action for the following P2P case.\n",
        "## Classification\n",
        f"```json\n{classification.model_dump_json(indent=2)}\n```\n",
        "## Invoice\n",
        f"```json\n{json.dumps(invoice, indent=2, default=str)}\n```\n",
        "## PO context\n",
        f"```json\n{json.dumps(po_context, indent=2, default=str)}\n```\n",
        "## Goods receipt context\n",
        f"```json\n{json.dumps(gr_context, indent=2, default=str)}\n```\n",
    ]

    # Surface vendor master + signals + payment status if case_context is present.
    if case_context is not None:
        signals = case_context.summary_signals()
        if signals:
            parts.append("## Cross-case signals\n")
            parts.append("\n".join(f"- {s}" for s in signals) + "\n")
        if case_context.vendor_record is not None:
            vr = case_context.vendor_record
            vendor_summary = {
                "name": vr.name,
                "country": vr.country,
                "tier": vr.tier.value if hasattr(vr.tier, "value") else str(vr.tier),
                "status": vr.status,
                "sanctions_check_passed": vr.sanctions_check_passed,
            }
            parts.append("## Vendor master\n")
            parts.append(f"```json\n{json.dumps(vendor_summary, indent=2, default=str)}\n```\n")
        if case_context.po_payment_status is not None:
            parts.append("## PO payment status\n")
            parts.append(
                f"```json\n{case_context.po_payment_status.model_dump_json(indent=2)}\n```\n",
            )

    parts.append("## Retrieved policies\n")
    parts.append(_format_policies(policies))
    return "\n".join(parts)


def _coerce_recommendation(raw: Any) -> Recommendation:
    if not isinstance(raw, dict):
        raise DecisionError(f"Expected JSON object, got {type(raw).__name__}")

    cited = raw.get("cited_policy_ids") or []
    if isinstance(cited, str):
        cited = [cited]
    elif not isinstance(cited, list):
        cited = []

    payload = {
        "action": raw.get("action"),
        "rationale": str(raw.get("rationale") or ""),
        "counterfactual": str(raw.get("counterfactual") or ""),
        "confidence": float(raw.get("confidence", 0.5)),
        "cited_policy_ids": [str(c) for c in cited],
    }
    try:
        return Recommendation.model_validate(payload)
    except ValidationError as e:
        allowed = ", ".join(a.value for a in RecommendedAction)
        raise DecisionError(
            f"Model returned invalid Recommendation (action={raw.get('action')!r}). "
            f"Allowed: {allowed}. Underlying: {e}",
        ) from e


async def recommend_action(
    *,
    classification: Classification,
    invoice: dict[str, Any],
    po_context: dict[str, Any] | None = None,
    gr_context: dict[str, Any] | None = None,
    case_context: CaseContext | None = None,
    retrieved_policies: list[RetrievedDoc] | None = None,
    client: ModelClient | None = None,
    case_id: str | None = None,
) -> Recommendation:
    """Pick an action given a classification + the case context + retrieved policies.

    `case_context` is the preferred way to pass cross-case data — when set,
    po_context and gr_context are derived from it automatically. The flat
    dict params remain for backward compat with eval scripts.

    Returns a validated `Recommendation`. Raises `DecisionError` if the model
    output can't be parsed after one retry.
    """
    client = client or ModelClient()
    policies = retrieved_policies or []
    user_msg = _build_user_message(
        classification, invoice, po_context, gr_context, policies, case_context,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = await client.complete(
        task="decision_support_reasoning",
        messages=messages,
        # Slightly raised from 0.0 — gives the model headroom to disagree with
        # the classifier when case facts conflict. Lower than the generative
        # tasks but non-zero on purpose.
        temperature=0.2,
        max_tokens=2048,
        case_id=case_id,
    )

    try:
        parsed = extract_json_from_response(result.output_text)
        return _coerce_recommendation(parsed)
    except (ValueError, DecisionError) as first_err:
        retry_messages = [
            *messages,
            {"role": "assistant", "content": result.output_text},
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed. Reply with ONLY a "
                    "single JSON object wrapped in a ```json code block matching the "
                    "schema in the system prompt. The `action` MUST be one of: "
                    + ", ".join(a.value for a in RecommendedAction)
                    + ". No prose outside the JSON."
                ),
            },
        ]
        retry = await client.complete(
            task="decision_support_reasoning",
            messages=retry_messages,
            temperature=0.0,  # retry: tighter output, no creative leeway
            max_tokens=2048,
            case_id=f"{case_id}::retry" if case_id else None,
        )
        try:
            parsed = extract_json_from_response(retry.output_text)
        except ValueError as e:
            raise DecisionError(
                f"Model output unparseable on retry. First error: {first_err}",
            ) from e
        return _coerce_recommendation(parsed)
