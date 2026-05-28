"""Drafter node — generates supplier emails or internal notes per recommendation.

Returns None for recommendations that don't require a draft (auto_resolve,
escalations without comms, etc.). The action → draft-type mapping is
explicit and predictable; the LLM only writes the text, not the routing.
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
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.recommendation import Recommendation, RecommendedAction


class DraftError(Exception):
    """Raised when the model output cannot be parsed into a Draft."""


SYSTEM_PROMPT = load_prompt("drafter")


# Which actions require a draft, and which draft type they produce.
_ACTION_TO_DRAFT_TYPE: dict[RecommendedAction, DraftType] = {
    # Outbound supplier emails
    RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO: DraftType.SUPPLIER_EMAIL,
    RecommendedAction.REQUEST_SUPPLIER_CORRECTION: DraftType.SUPPLIER_EMAIL,
    RecommendedAction.REQUEST_MISSING_PO_FROM_SUPPLIER: DraftType.SUPPLIER_EMAIL,
    # Internal notes (buyer / specialized teams)
    RecommendedAction.REQUEST_PO_AMENDMENT: DraftType.INTERNAL_NOTE,
    RecommendedAction.NOTIFY_BUYER_OF_SUPPLIER_DELAY: DraftType.INTERNAL_NOTE,
    RecommendedAction.ROUTE_TO_VENDOR_MASTER_ONBOARDING: DraftType.INTERNAL_NOTE,
    RecommendedAction.ROUTE_TO_VP_FINANCE_APPROVAL: DraftType.INTERNAL_NOTE,
    RecommendedAction.ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY: DraftType.INTERNAL_NOTE,
    RecommendedAction.ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO: DraftType.INTERNAL_NOTE,
    RecommendedAction.ESCALATE_TO_FRAUD: DraftType.INTERNAL_NOTE,
    RecommendedAction.HALT_REQUIRE_SUPERVISOR: DraftType.INTERNAL_NOTE,
}


def action_needs_draft(action: RecommendedAction) -> bool:
    return action in _ACTION_TO_DRAFT_TYPE


def _build_user_message(
    recommendation: Recommendation,
    classification: Classification,
    invoice: dict[str, Any],
    case_context: CaseContext | None,
) -> str:
    expected_type = _ACTION_TO_DRAFT_TYPE.get(recommendation.action)
    parts = [
        "Draft the communication for the following P2P case.",
        "",
        f"## Recommended action: `{recommendation.action.value}`",
        f"Expected `draft_type`: **{expected_type.value if expected_type else '?'}**",
        "",
        f"## Recommendation rationale\n{recommendation.rationale}",
        f"\n## Counterfactual\n{recommendation.counterfactual}",
        "",
        f"## Classification\n```json\n{classification.model_dump_json(indent=2)}\n```",
        "",
        f"## Invoice\n```json\n{json.dumps(invoice, indent=2, default=str)}\n```",
    ]
    if case_context is not None:
        parts.extend([
            "",
            "## Cross-case context signals",
            "",
            " | ".join(case_context.summary_signals()),
        ])
    return "\n".join(parts)


def _coerce_draft(raw: Any) -> Draft:
    if not isinstance(raw, dict):
        raise DraftError(f"Expected JSON object, got {type(raw).__name__}")

    cc = raw.get("cc") or []
    if isinstance(cc, str):
        cc = [cc]
    elif not isinstance(cc, list):
        cc = []

    refs = raw.get("references") or []
    if isinstance(refs, str):
        refs = [refs]
    elif not isinstance(refs, list):
        refs = []

    payload = {
        "draft_type": raw.get("draft_type"),
        "recipient": str(raw.get("recipient") or ""),
        "subject": str(raw.get("subject") or ""),
        "body": str(raw.get("body") or ""),
        "cc": [str(c) for c in cc],
        "references": [str(r) for r in refs],
    }
    try:
        return Draft.model_validate(payload)
    except ValidationError as e:
        raise DraftError(
            f"Model output failed pydantic validation: {e}",
        ) from e


async def draft_communication(
    *,
    recommendation: Recommendation,
    classification: Classification,
    invoice: dict[str, Any],
    case_context: CaseContext | None = None,
    client: ModelClient | None = None,
    case_id: str | None = None,
) -> Draft | None:
    """Generate a draft if the recommendation requires one.

    Returns None for actions that don't need drafting (`auto_resolve`,
    `approve_pending_review`, `escalate_to_fraud`, etc.).
    """
    if not action_needs_draft(recommendation.action):
        return None

    client = client or ModelClient()
    user_msg = _build_user_message(recommendation, classification, invoice, case_context)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = await client.complete(
        task="drafting_supplier_comms",
        messages=messages,
        temperature=0.4,    # slightly higher than reasoning tasks — drafting benefits from variety
        max_tokens=1024,
        case_id=case_id,
    )

    try:
        parsed = extract_json_from_response(result.output_text)
        return _coerce_draft(parsed)
    except (ValueError, DraftError) as first_err:
        retry_messages = [
            *messages,
            {"role": "assistant", "content": result.output_text},
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed. Reply with ONLY a single JSON "
                    "object wrapped in a ```json code block matching the schema in the system "
                    "prompt. No prose outside the JSON."
                ),
            },
        ]
        retry = await client.complete(
            task="drafting_supplier_comms",
            messages=retry_messages,
            temperature=0.0,
            max_tokens=1024,
            case_id=f"{case_id}::retry" if case_id else None,
        )
        try:
            parsed = extract_json_from_response(retry.output_text)
        except ValueError as e:
            raise DraftError(
                f"Model output unparseable on retry. First error: {first_err}",
            ) from e
        return _coerce_draft(parsed)
