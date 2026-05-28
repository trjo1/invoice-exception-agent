"""Mock action executor.

Reads an approved `HITLItem`, looks up the recommended action, and produces an
`ExecutionResult` with 1-3 simulated downstream steps. No real HTTP / SMTP /
SAP calls happen — every step describes what *would* have been called.

Each `RecommendedAction` enum value maps to a builder that constructs the steps
from the item's payload (classification, recommendation, routing, draft).
When the reviewer edited the draft before approving, the executor uses the
edited version for any email-type steps.

The real backend will implement the same `execute(item)` signature and return
the same `ExecutionResult` shape — only the steps will reference real
endpoints + carry real response status codes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from p2p_agent.hitl.models import HITLItem
from p2p_agent.models.execution import ExecutionResult, ExecutionStatus, ExecutionStep
from p2p_agent.models.recommendation import RecommendedAction


class ExecutorError(Exception):
    """Raised on irrecoverable execution problems (mostly real-backend only)."""


def _draft_from_item(item: HITLItem) -> dict[str, Any] | None:
    """Return the live draft for this item, preferring the reviewer's edit."""
    edited = item.edited_draft_json
    if edited and (edited.get("subject") or edited.get("body")):
        return edited
    payload = item.payload_json or {}
    return payload.get("draft")


def _short_body(body: str, max_chars: int = 240) -> str:
    body = (body or "").strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rstrip() + "…"


# ----- per-action builders -----
# Each takes the HITLItem and returns a list[ExecutionStep] + an optional note.

def _build_email_step(item: HITLItem, fallback_recipient: str, fallback_subject: str) -> ExecutionStep:
    draft = _draft_from_item(item) or {}
    return ExecutionStep(
        system="Email",
        verb="EMAIL",
        target=str(draft.get("recipient") or fallback_recipient),
        payload_summary={
            "subject": str(draft.get("subject") or fallback_subject),
            "body_excerpt": _short_body(str(draft.get("body", ""))),
            "cc": draft.get("cc") or [],
        },
    )


def _build_sap_post(item: HITLItem, *, note_suffix: str = "") -> ExecutionStep:
    return ExecutionStep(
        system="SAP S/4HANA Cloud",
        verb="POST",
        target="/api/v1/financials/AP/postInvoice",
        payload_summary={
            "case_id": item.case_id,
            "amount_basis": "from PipelineResult.extraction.total",
            "status": f"approved{note_suffix}",
        },
    )


def _build_servicenow_ticket(item: HITLItem, *, ticket_kind: str, assigned_to: str) -> ExecutionStep:
    return ExecutionStep(
        system="ServiceNow",
        verb="CREATE_TICKET",
        target=f"workflow:{ticket_kind}",
        payload_summary={
            "case_id": item.case_id,
            "assigned_to": assigned_to,
            "short_description": f"{ticket_kind.replace('_', ' ').title()} for {item.case_id}",
            "linked_invoice": item.case_id,
        },
    )


def _build_hold_invoice(item: HITLItem, reason: str) -> ExecutionStep:
    return ExecutionStep(
        system="SAP S/4HANA Cloud",
        verb="PUT",
        target="/api/v1/financials/AP/holdInvoice",
        payload_summary={
            "case_id": item.case_id,
            "hold_reason": reason,
        },
    )


def _build_slack(channel: str, message: str) -> ExecutionStep:
    return ExecutionStep(
        system="Slack",
        verb="NOTIFY",
        target=f"#{channel}",
        payload_summary={"message": message},
    )


def _build_pagerduty(service: str, severity: str = "high") -> ExecutionStep:
    return ExecutionStep(
        system="PagerDuty",
        verb="NOTIFY",
        target=f"service:{service}",
        payload_summary={"severity": severity},
    )


def _build_halt_pay_run(item: HITLItem) -> ExecutionStep:
    return ExecutionStep(
        system="SAP S/4HANA Cloud",
        verb="HALT_PAY_RUN",
        target="/api/v1/financials/AP/payRun/halt",
        payload_summary={"case_id": item.case_id, "reason": "halt requested by HITL approval"},
    )


def _build_recheck(item: HITLItem, days: int) -> ExecutionStep:
    return ExecutionStep(
        system="Internal",
        verb="SCHEDULE_RECHECK",
        target="agent1.scheduler",
        payload_summary={
            "case_id": item.case_id,
            "recheck_in_days": days,
            "trigger": "goods_receipt_arrival_or_timeout",
        },
    )


def _build_treasury_email(item: HITLItem) -> ExecutionStep:
    return ExecutionStep(
        system="Email",
        verb="EMAIL",
        target="treasury@buyer.example",
        payload_summary={
            "subject": f"FX review needed: {item.case_id}",
            "body_excerpt": (
                "Invoice/PO currency mismatch flagged. Please confirm spot vs hedged rate "
                "before AP posts the invoice."
            ),
            "cc": [],
        },
    )


def _build_audit_record(item: HITLItem, kind: str) -> ExecutionStep:
    return ExecutionStep(
        system="Internal",
        verb="CREATE_TICKET",
        target="audit_trail",
        payload_summary={
            "case_id": item.case_id,
            "kind": kind,
            "tier": item.tier,
            "routed_to": item.routed_to,
            "resolved_by": item.resolved_by,
        },
    )


# Per-action recipe map. Each callable returns (steps, note).
_RECIPES: dict[
    RecommendedAction,
    Callable[[HITLItem], tuple[list[ExecutionStep], str]],
] = {
    RecommendedAction.AUTO_RESOLVE: lambda i: (
        [_build_sap_post(i)],
        "Auto-resolved: invoice posted to SAP without HITL touch.",
    ),
    RecommendedAction.APPROVE_PENDING_REVIEW: lambda i: (
        [_build_sap_post(i, note_suffix=" with reviewer sanity-check")],
        "Approved after Tier-2 reviewer confirmed; posted to SAP.",
    ),
    RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO: lambda i: (
        [
            _build_email_step(
                i,
                fallback_recipient="ap@supplier.example",
                fallback_subject=f"Credit memo request — {i.case_id}",
            ),
            _build_hold_invoice(i, reason="awaiting supplier credit memo"),
        ],
        "Credit-memo request emailed; invoice held pending receipt of the memo.",
    ),
    RecommendedAction.REQUEST_SUPPLIER_CORRECTION: lambda i: (
        [
            _build_email_step(
                i,
                fallback_recipient="ap@supplier.example",
                fallback_subject=f"Corrected invoice needed — {i.case_id}",
            ),
            _build_hold_invoice(i, reason="awaiting corrected invoice"),
        ],
        "Correction request emailed; invoice held until corrected version arrives.",
    ),
    RecommendedAction.REQUEST_MISSING_PO_FROM_SUPPLIER: lambda i: (
        [
            _build_email_step(
                i,
                fallback_recipient="ap@supplier.example",
                fallback_subject=f"PO reference needed — {i.case_id}",
            ),
            _build_hold_invoice(i, reason="awaiting PO reference from supplier"),
        ],
        "PO-reference request emailed; invoice held.",
    ),
    RecommendedAction.REQUEST_PO_AMENDMENT: lambda i: (
        [
            _build_servicenow_ticket(i, ticket_kind="po_amendment", assigned_to="procurement"),
            _build_hold_invoice(i, reason="awaiting PO amendment"),
        ],
        "ServiceNow ticket opened for procurement to amend the PO.",
    ),
    RecommendedAction.ROUTE_TO_VENDOR_MASTER_ONBOARDING: lambda i: (
        [
            _build_servicenow_ticket(i, ticket_kind="vendor_onboarding", assigned_to="vendor_master_team"),
            _build_hold_invoice(i, reason="awaiting vendor master record"),
        ],
        "Vendor onboarding workflow kicked off; invoice held until vendor active.",
    ),
    RecommendedAction.ROUTE_TO_VP_FINANCE_APPROVAL: lambda i: (
        [
            ExecutionStep(
                system="Email",
                verb="EMAIL",
                target="vp.finance@buyer.example",
                payload_summary={
                    "subject": f"Approval needed: {i.case_id} above spend threshold",
                    "body_excerpt": (
                        "PO above your approval threshold. Goods delivered. Please review and approve "
                        "before AP posts the invoice."
                    ),
                },
            ),
            _build_slack("finance-approvals", f"VP-Finance approval needed for {i.case_id}"),
            _build_hold_invoice(i, reason="awaiting VP-Finance approval"),
        ],
        "VP-Finance notified via email + Slack; invoice held.",
    ),
    RecommendedAction.ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY: lambda i: (
        [
            _build_servicenow_ticket(i, ticket_kind="short_delivery", assigned_to="buyer"),
            _build_hold_invoice(i, reason="quantity short of GR"),
        ],
        "Buyer notified of short delivery; invoice held pending resolution.",
    ),
    RecommendedAction.ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO: lambda i: (
        [
            _build_servicenow_ticket(i, ticket_kind="retroactive_po", assigned_to="buyer"),
            _build_hold_invoice(i, reason="retroactive PO required"),
        ],
        "Buyer routed for retroactive PO decision; invoice held.",
    ),
    RecommendedAction.ESCALATE_TO_FRAUD: lambda i: (
        [
            _build_halt_pay_run(i),
            _build_pagerduty("ap-fraud-response", severity="critical"),
            _build_audit_record(i, kind="fraud_escalation"),
        ],
        "Pay run halted; AP fraud team paged; audit record created.",
    ),
    RecommendedAction.HALT_REQUIRE_SUPERVISOR: lambda i: (
        [
            _build_halt_pay_run(i),
            ExecutionStep(
                system="Email",
                verb="EMAIL",
                target="ap.supervisor@buyer.example",
                payload_summary={
                    "subject": f"Supervisor review halt: {i.case_id}",
                    "body_excerpt": "Halt-pay event triggered. Please review the case in the queue before any further action.",
                },
            ),
            _build_audit_record(i, kind="supervisor_halt"),
        ],
        "Pay run halted; AP supervisor notified; audit record created.",
    ),
    RecommendedAction.ESCALATE_FOR_FX_REVIEW: lambda i: (
        [
            _build_treasury_email(i),
            _build_hold_invoice(i, reason="awaiting treasury FX confirmation"),
        ],
        "Treasury notified for FX-rate confirmation; invoice held.",
    ),
    RecommendedAction.NOTIFY_BUYER_OF_SUPPLIER_DELAY: lambda i: (
        [
            ExecutionStep(
                system="Email",
                verb="EMAIL",
                target="procurement@buyer.example",
                payload_summary={
                    "subject": f"Supplier delay notification: {i.case_id}",
                    "body_excerpt": "Supplier reported a delivery delay. Forwarding for procurement awareness.",
                },
            ),
        ],
        "Delay notification forwarded to buyer's procurement liaison.",
    ),
    RecommendedAction.HOLD_FOR_GOODS_RECEIPT: lambda i: (
        [
            _build_hold_invoice(i, reason="no goods receipt on file"),
            _build_recheck(i, days=7),
        ],
        "Invoice held; scheduled to re-check for GR in 7 days.",
    ),
    RecommendedAction.OTHER: lambda i: (
        [_build_servicenow_ticket(i, ticket_kind="manual_review", assigned_to="ap_team")],
        "Routed to manual review (non-standard case).",
    ),
}


class ActionExecutor:
    """Consumes an approved HITLItem and produces an ExecutionResult.

    `mode='mock'` (default) — produces simulated steps with no real side effects.
    `mode='real'` — reserved for the SAP/Ariba/ServiceNow backend (not implemented).
    """

    def __init__(self, mode: str = "mock") -> None:
        if mode not in {"mock", "real"}:
            raise ValueError(f"Unknown executor mode: {mode!r}")
        if mode == "real":
            raise ExecutorError(
                "Real-backend executor not yet implemented; SAP credentials pending.",
            )
        self.mode = mode

    def execute(self, item: HITLItem) -> ExecutionResult:
        """Produce an ExecutionResult for one approved item."""
        action_value = item.recommended_action
        try:
            action = RecommendedAction(action_value)
        except ValueError:
            # Unknown action — fall through to OTHER recipe
            action = RecommendedAction.OTHER
        recipe = _RECIPES.get(action) or _RECIPES[RecommendedAction.OTHER]
        steps, note = recipe(item)
        return ExecutionResult(
            status=ExecutionStatus.SIMULATED_SUCCESS,
            steps=steps,
            note=note,
            executed_at=datetime.now(UTC),
        )
