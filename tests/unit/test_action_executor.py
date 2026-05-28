"""Unit tests for the mock ActionExecutor.

Validates that every `RecommendedAction` enum value maps to a sensible
simulation: right systems are involved, right verbs are used, draft content
is referenced for email-type actions, and high-severity cases pull in the
expected escalation channels.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from p2p_agent.executor import ActionExecutor, ExecutorError
from p2p_agent.hitl import HITLQueue
from p2p_agent.hitl.models import HITLItem
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.execution import ExecutionStatus
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


@pytest.fixture
def queue(tmp_path: Path) -> HITLQueue:
    return HITLQueue(db_url=f"sqlite:///{tmp_path / 'q.db'}")


def _make_item(
    queue: HITLQueue,
    *,
    action: RecommendedAction,
    cls: ExceptionCategory = ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
    tier: HITLTier = HITLTier.APPROVER_REVIEW,
    routed_to: str = "buyer",
    with_draft: bool = True,
    draft_body: str = "Default draft body.",
    edited_draft: dict | None = None,
) -> HITLItem:
    draft = Draft(
        draft_type=DraftType.SUPPLIER_EMAIL,
        recipient="ap@supplier.example",
        subject="Default subject",
        body=draft_body,
    ) if with_draft else None
    item = queue.enqueue(
        case_id="C-EXEC",
        classification=Classification(class_label=cls, confidence=0.9, evidence=[], rationale="r"),
        recommendation=Recommendation(action=action, rationale="r", counterfactual="c", confidence=0.88),
        routing_decision=RoutingDecision(tier=tier, routed_to=routed_to, reason="r"),
        draft=draft,
    )
    if edited_draft:
        queue.approve_with_edit(item.id, edited_draft=edited_draft)
    else:
        queue.approve(item.id)
    return queue.get(item.id)


def test_executor_rejects_real_mode_today() -> None:
    with pytest.raises(ExecutorError):
        ActionExecutor(mode="real")


def test_executor_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        ActionExecutor(mode="bogus")


def test_auto_resolve_posts_to_sap(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.AUTO_RESOLVE, with_draft=False)
    result = ActionExecutor().execute(item)
    assert result.status == ExecutionStatus.SIMULATED_SUCCESS
    assert len(result.steps) == 1
    assert result.steps[0].system.startswith("SAP")
    assert result.steps[0].verb == "POST"
    assert isinstance(result.executed_at, datetime)


def test_request_credit_memo_emails_supplier_and_holds_invoice(queue: HITLQueue) -> None:
    item = _make_item(
        queue,
        action=RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
        draft_body="Please issue a credit memo for $200 of variance on INV-1.",
    )
    result = ActionExecutor().execute(item)
    systems = [s.system for s in result.steps]
    verbs = [s.verb for s in result.steps]
    assert "Email" in systems
    assert "EMAIL" in verbs
    assert any(s.verb == "PUT" and "hold" in s.target.lower() for s in result.steps)
    # The email step references the actual draft body
    email_step = next(s for s in result.steps if s.system == "Email")
    assert "$200" in email_step.payload_summary["body_excerpt"]
    assert email_step.target == "ap@supplier.example"


def test_edited_draft_overrides_original(queue: HITLQueue) -> None:
    item = _make_item(
        queue,
        action=RecommendedAction.REQUEST_SUPPLIER_CORRECTION,
        draft_body="ORIGINAL body that should be ignored.",
        edited_draft={
            "subject": "EDITED — please correct invoice line item 2",
            "body": "EDITED body referencing tighter wording.",
            "recipient": "billing-correct@supplier.example",
        },
    )
    result = ActionExecutor().execute(item)
    email_step = next(s for s in result.steps if s.system == "Email")
    assert email_step.target == "billing-correct@supplier.example"
    assert email_step.payload_summary["subject"].startswith("EDITED")
    assert "EDITED body" in email_step.payload_summary["body_excerpt"]
    assert "ORIGINAL" not in email_step.payload_summary["body_excerpt"]


def test_request_missing_po_emails_and_holds(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.REQUEST_MISSING_PO_FROM_SUPPLIER)
    result = ActionExecutor().execute(item)
    assert any(s.system == "Email" for s in result.steps)
    assert any("hold" in s.target.lower() for s in result.steps)


def test_request_po_amendment_creates_servicenow_ticket(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.REQUEST_PO_AMENDMENT, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system == "ServiceNow" for s in result.steps)
    sn_step = next(s for s in result.steps if s.system == "ServiceNow")
    assert "po_amendment" in sn_step.target
    assert sn_step.payload_summary["assigned_to"] == "procurement"


def test_vendor_onboarding_creates_servicenow_and_holds(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.ROUTE_TO_VENDOR_MASTER_ONBOARDING, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system == "ServiceNow" and "vendor_onboarding" in s.target for s in result.steps)
    assert any("hold" in s.target.lower() for s in result.steps)


def test_vp_finance_approval_pings_three_channels(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.ROUTE_TO_VP_FINANCE_APPROVAL, with_draft=False)
    result = ActionExecutor().execute(item)
    systems = {s.system for s in result.steps}
    assert "Email" in systems
    assert "Slack" in systems
    assert any("hold" in s.target.lower() for s in result.steps)


def test_short_delivery_creates_servicenow_and_holds(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system == "ServiceNow" and "short_delivery" in s.target for s in result.steps)


def test_retroactive_po_creates_servicenow(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system == "ServiceNow" and "retroactive_po" in s.target for s in result.steps)


def test_fraud_escalation_halts_and_pages(queue: HITLQueue) -> None:
    item = _make_item(
        queue,
        action=RecommendedAction.ESCALATE_TO_FRAUD,
        cls=ExceptionCategory.FRAUD_SIGNAL,
        tier=HITLTier.SUPERVISOR_REVIEW,
        routed_to="ap_fraud_team",
        with_draft=False,
    )
    result = ActionExecutor().execute(item)
    verbs = {s.verb for s in result.steps}
    systems = {s.system for s in result.steps}
    assert "HALT_PAY_RUN" in verbs
    assert "PagerDuty" in systems
    # The PagerDuty step is high-severity
    pd_step = next(s for s in result.steps if s.system == "PagerDuty")
    assert pd_step.payload_summary["severity"] == "critical"


def test_halt_require_supervisor_halts_and_emails(queue: HITLQueue) -> None:
    item = _make_item(
        queue,
        action=RecommendedAction.HALT_REQUIRE_SUPERVISOR,
        tier=HITLTier.SUPERVISOR_REVIEW,
        routed_to="ap_fraud_team",
        with_draft=False,
    )
    result = ActionExecutor().execute(item)
    verbs = {s.verb for s in result.steps}
    assert "HALT_PAY_RUN" in verbs
    assert any(s.system == "Email" and "supervisor" in s.target.lower() for s in result.steps)


def test_fx_review_emails_treasury_and_holds(queue: HITLQueue) -> None:
    item = _make_item(
        queue,
        action=RecommendedAction.ESCALATE_FOR_FX_REVIEW,
        cls=ExceptionCategory.CROSS_CURRENCY_MISMATCH,
        tier=HITLTier.SUPERVISOR_REVIEW,
        routed_to="treasury",
        with_draft=False,
    )
    result = ActionExecutor().execute(item)
    treasury_step = next(s for s in result.steps if s.system == "Email")
    assert "treasury" in treasury_step.target.lower()
    assert any("hold" in s.target.lower() for s in result.steps)


def test_supplier_delay_emails_procurement(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.NOTIFY_BUYER_OF_SUPPLIER_DELAY, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system == "Email" and "procurement" in s.target.lower() for s in result.steps)


def test_hold_for_goods_receipt_holds_and_schedules_recheck(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.HOLD_FOR_GOODS_RECEIPT, with_draft=False)
    result = ActionExecutor().execute(item)
    verbs = {s.verb for s in result.steps}
    assert "PUT" in verbs        # the hold call
    assert "SCHEDULE_RECHECK" in verbs


def test_approve_pending_review_posts_to_sap(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.APPROVE_PENDING_REVIEW, with_draft=False)
    result = ActionExecutor().execute(item)
    assert any(s.system.startswith("SAP") and s.verb == "POST" for s in result.steps)


def test_other_action_routes_to_manual_review(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.OTHER, with_draft=False)
    result = ActionExecutor().execute(item)
    sn = next(s for s in result.steps if s.system == "ServiceNow")
    assert "manual_review" in sn.target


def test_executor_carries_case_id_into_every_step(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO)
    result = ActionExecutor().execute(item)
    # At least one step (the SAP hold or audit record) carries the case_id
    case_id_carriers = [s for s in result.steps if s.payload_summary.get("case_id") == item.case_id]
    assert case_id_carriers, "expected at least one step to embed case_id"


def test_mark_executed_persists_columns_and_audit(queue: HITLQueue) -> None:
    item = _make_item(queue, action=RecommendedAction.AUTO_RESOLVE, with_draft=False)
    result = ActionExecutor().execute(item)
    queue.mark_executed(item.id, result)
    persisted = queue.get(item.id)
    assert persisted.execution_status == "simulated_success"
    assert persisted.execution_result_json is not None
    assert persisted.executed_at is not None
    # Audit entries: enqueue + approve + execute = 3
    assert len(persisted.audit_entries) == 3
    assert persisted.audit_entries[-1].to_status == persisted.status  # no status change
    assert "executed" in persisted.audit_entries[-1].note
