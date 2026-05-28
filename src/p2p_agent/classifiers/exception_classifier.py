"""Exception classifier — picks one of 13 categories for a P2P case.

Inputs: the invoice as a dict, plus optional PO and goods-receipt context.
Output: a validated `Classification` pydantic instance. Every call goes
through `ModelClient` so cost is logged and the open-source-first model
discipline is enforced.

Usage:
    from p2p_agent.classifiers import classify_exception
    result = await classify_exception(invoice=inv_json, po_context=po, gr_context=gr)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from p2p_agent.llm.client import ModelClient
from p2p_agent.llm.json_utils import extract_json_from_response
from p2p_agent.llm.prompts import load_prompt
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.context import CaseContext

logger = logging.getLogger(__name__)


class ClassifierError(Exception):
    """Raised when the model output cannot be parsed into a Classification."""


SYSTEM_PROMPT = load_prompt("exception_classification")


_ACTIONABLE_SIGNAL_TOKENS = (
    "DUPLICATE:",
    "SPLIT WATCH:",
    "PO ALREADY FULLY PAID",
    "PO NOT found",
    "vendor NOT in master file",
    "VENDOR CHANGED",
)


def _has_actionable_signal(signals: list[str]) -> bool:
    return any(any(tok in s for tok in _ACTIONABLE_SIGNAL_TOKENS) for s in signals)


def _anchor_summary(case_context: CaseContext) -> dict[str, Any]:
    """Compact vendor + PO + GR summary. Always sent to the classifier, even
    when no actionable signal fires — the classifier still needs these to
    catch invoice-vs-PO defects (payment term mismatch, currency mismatch,
    price/qty variance, FX issues).
    """
    payload: dict[str, Any] = {}

    vr = case_context.vendor_record
    if vr is not None:
        payload["vendor_summary"] = {
            "name": vr.name,
            "country": vr.country,
            "tier": vr.tier.value if hasattr(vr.tier, "value") else str(vr.tier),
            "status": vr.status,
        }
    else:
        payload["vendor_summary"] = None

    po = case_context.po_record
    if po is not None:
        payload["po_summary"] = {
            "id": po.id,
            "total_authorized": po.total_authorized,
            "currency": po.currency,
            "status": po.status.value if hasattr(po.status, "value") else str(po.status),
            "payment_terms": po.payment_terms,
            "n_line_items": len(po.line_items),
            "is_emergency": po.is_emergency,
            "fx_clause": po.fx_clause,
            # Line items inline (capped) so the classifier can do line-level
            # price/qty comparisons without a separate retrieval step.
            "line_items": [
                {
                    "line_no": li.line_no,
                    "sku": li.sku,
                    "quantity_authorized": li.quantity_authorized,
                    "unit_price": li.unit_price,
                    "line_total": li.line_total,
                }
                for li in po.line_items[:10]  # cap to keep prompt size sane
            ],
        }
    else:
        payload["po_summary"] = None

    gr = case_context.goods_receipt
    if gr is not None:
        payload["gr_summary"] = {
            "id": gr.id,
            "po_id": gr.po_id,
            "receipt_date": str(gr.receipt_date) if gr.receipt_date else None,
            "warehouse": gr.warehouse,
            "line_items": [
                {"line_no": gl.line_no, "sku": gl.sku, "quantity_received": gl.quantity_received}
                for gl in gr.line_items[:10]
            ],
        }
    else:
        payload["gr_summary"] = None

    return payload


def _trimmed_context_payload(case_context: CaseContext, signals: list[str]) -> dict[str, Any]:
    """Return the rich history / payment / change-event payload when an
    actionable cross-case signal fired. Layered on top of the always-on
    anchor summary so the classifier sees BOTH.
    """
    payload: dict[str, Any] = dict(_anchor_summary(case_context))

    # Conditional fields — only when a signal fires
    if any("DUPLICATE:" in s for s in signals):
        payload["prior_invoices_same_supplier_number"] = [
            i.model_dump(mode="json") for i in case_context.prior_invoices_same_supplier_number
        ]
    if any("SPLIT WATCH:" in s for s in signals):
        payload["prior_invoices_same_po"] = [
            i.model_dump(mode="json") for i in case_context.prior_invoices_same_po
        ]
    if case_context.po_payment_status is not None and any(
        "PO ALREADY FULLY PAID" in s for s in signals
    ):
        payload["po_payment_status"] = case_context.po_payment_status.model_dump(mode="json")
    if any("VENDOR CHANGED" in s for s in signals):
        payload["vendor_recent_changes"] = [
            e.model_dump(mode="json") for e in case_context.vendor_recent_changes
        ]
    if case_context.aggregate_signals:
        payload["aggregate_signals"] = case_context.aggregate_signals

    return payload


def _build_user_message(
    invoice: dict[str, Any],
    po_context: dict[str, Any] | None,
    gr_context: dict[str, Any] | None,
    case_context: CaseContext | None,
) -> str:
    base = {
        "invoice": invoice,
        "po_context": po_context,
        "gr_context": gr_context,
    }
    parts = [
        "Classify the following P2P case. Apply the Decision order from the system "
        "prompt: read the invoice + PO + GR first, then consult cross-case context "
        "only as supplementary evidence. Default to `none` unless the invoice itself "
        "shows a defect OR a smart cross-case SUMMARY SIGNAL fires.",
        "",
        "## Primary input — invoice (+ optional PO and GR)",
        "",
        "```json",
        json.dumps(base, indent=2, default=str),
        "```",
    ]
    if case_context is not None:
        signals = case_context.summary_signals()
        signal_line = " | ".join(signals) if signals else "(no signals)"
        actionable = _has_actionable_signal(signals)
        parts.extend([
            "",
            "## Supplementary — cross-case context",
            "",
            "SMART SIGNALS (smart-filtered; only fires on real exceptions — trust this line): "
            + signal_line,
            "",
        ])

        # ALWAYS surface the vendor + PO + GR anchor summary, even when no
        # actionable signal fired. The classifier needs the PO's payment_terms,
        # currency, and line items to catch invoice-vs-PO defects like
        # payment_term_mismatch, cross_currency_mismatch, price/qty variance.
        # Without this, the LLM sees "po_context: null" in the structured input
        # above and concludes "no PO data to compare" — the bug we hit on
        # P002_idx0216 where the classifier returned `none` despite a clear
        # NET-45 vs NET-30 payment-term mismatch.
        anchor = _anchor_summary(case_context)
        if any(v is not None for v in anchor.values()):
            parts.extend([
                "Vendor + PO + GR summary from master-data lookups (compare against the invoice fields above):",
                "```json",
                json.dumps(anchor, indent=2, default=str),
                "```",
                "",
            ])

        if not actionable:
            parts.append(
                "No high-severity cross-case signal fired (no duplicate, no split-watch, "
                "no PO-already-paid, no vendor change). Still compare the invoice against "
                "the vendor / PO / GR summary above to catch payment-term, currency, "
                "price-variance, or quantity-variance defects.",
            )
        else:
            trimmed = _trimmed_context_payload(case_context, signals)
            parts.extend([
                "Detailed cross-case payload (history + payment + changes that drove the signal):",
                "```json",
                json.dumps(trimmed, indent=2, default=str),
                "```",
            ])
    return "\n".join(parts)


_GUARDRAIL_THRESHOLD = 0.70
_GUARDRAIL_DOWNGRADED_CONFIDENCE = 0.80


def _apply_confidence_guardrail(
    classification: Classification,
    case_context: CaseContext | None,
) -> Classification:
    """Downgrade low-confidence non-`none` classes to `none` when no smart signal fired.

    Catches the residual rich-context bias: the model sometimes returns
    `cross_currency_mismatch` or `fraud_signal` at 0.55 confidence on perfectly
    clean invoices. If NO smart cross-case signal fired AND the model's own
    confidence is below 0.70, the classification gets pulled back to `none`.

    Returns the (possibly modified) Classification. Original is not mutated.
    """
    if classification.class_label == ExceptionCategory.NONE:
        return classification
    if classification.confidence >= _GUARDRAIL_THRESHOLD:
        return classification
    if case_context is None:
        return classification
    signals = case_context.summary_signals()
    if _has_actionable_signal(signals):
        return classification

    return Classification(
        class_label=ExceptionCategory.NONE,
        confidence=_GUARDRAIL_DOWNGRADED_CONFIDENCE,
        evidence=[*classification.evidence, "guardrail_low_conf_no_signal_downgrade"],
        rationale=(
            f"Guardrail: model returned {classification.class_label.value} at "
            f"{classification.confidence:.2f} but no smart cross-case signal fired. "
            f"Downgraded to `none`. Original rationale: {classification.rationale}"
        ),
    )


def _coerce_classification(raw: Any) -> Classification:
    """Convert a parsed JSON dict into a `Classification`.

    Tolerates a few rough edges the model produces — extra keys, evidence as a
    string instead of a list. Falls back to ClassifierError if class_label is
    not one of the enum values.
    """
    if not isinstance(raw, dict):
        raise ClassifierError(f"Expected JSON object, got {type(raw).__name__}")

    evidence = raw.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence]
    elif evidence is None:
        evidence = []
    elif not isinstance(evidence, list):
        evidence = [str(evidence)]

    payload = {
        "class_label": raw.get("class_label"),
        "confidence": raw.get("confidence", 0.5),
        "evidence": [str(e) for e in evidence],
        "rationale": str(raw.get("rationale") or ""),
    }
    try:
        return Classification.model_validate(payload)
    except ValidationError as e:
        label = raw.get("class_label")
        allowed = ", ".join(c.value for c in ExceptionCategory)
        raise ClassifierError(
            f"Model returned invalid Classification (class_label={label!r}). "
            f"Allowed: {allowed}. Underlying error: {e}",
        ) from e


async def classify_exception(
    *,
    invoice: dict[str, Any],
    po_context: dict[str, Any] | None = None,
    gr_context: dict[str, Any] | None = None,
    case_context: CaseContext | None = None,
    client: ModelClient | None = None,
    case_id: str | None = None,
) -> Classification:
    """Run the classifier node for a single case.

    Returns a validated `Classification`. Raises `ClassifierError` if the model
    response can't be parsed into the schema after one retry with a stricter
    reminder.

    When `case_context` is provided, the classifier also reasons over cross-case
    signals (vendor master, PO record, prior invoices, payment status, etc.).
    """
    client = client or ModelClient()
    user_msg = _build_user_message(invoice, po_context, gr_context, case_context)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = await client.complete(
        task="exception_classification",
        messages=messages,
        temperature=0.0,
        max_tokens=1024,
        case_id=case_id,
    )

    try:
        parsed = extract_json_from_response(result.output_text)
        classification = _coerce_classification(parsed)
        return _apply_confidence_guardrail(classification, case_context)
    except ValueError as first_err:
        # Only retry on parse failure (no JSON found). Schema validation errors
        # (ClassifierError) bubble — re-asking the model rarely fixes them and
        # doubles latency for ~zero benefit.
        logger.warning(
            "classifier_retry_fired",
            extra={
                "case_id": case_id,
                "reason": str(first_err),
                "first_response_snippet": result.output_text[:200],
            },
        )
        retry_messages = [
            *messages,
            {"role": "assistant", "content": result.output_text},
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed. Reply with ONLY a "
                    "single JSON object wrapped in a ```json code block. The "
                    "`class_label` MUST be one of: "
                    + ", ".join(c.value for c in ExceptionCategory)
                    + ". No prose outside the JSON."
                ),
            },
        ]
        retry = await client.complete(
            task="exception_classification",
            messages=retry_messages,
            temperature=0.0,
            max_tokens=1024,
            case_id=f"{case_id}::retry" if case_id else None,
        )
        try:
            parsed = extract_json_from_response(retry.output_text)
        except ValueError as e:
            raise ClassifierError(
                f"Model output unparseable on retry. First error: {first_err}",
            ) from e
        classification = _coerce_classification(parsed)
        return _apply_confidence_guardrail(classification, case_context)
