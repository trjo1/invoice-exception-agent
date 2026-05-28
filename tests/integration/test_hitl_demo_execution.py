"""Integration tests for the approve → execute → render flow.

Verifies that when a user clicks Approve in the FastAPI app, the executor
runs, the result is persisted on the HITL item, and the detail page renders
the execution card.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p2p_agent.hitl import HITLQueue, PipelineRunStore
from p2p_agent.hitl.webapp.server import create_app
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


@pytest.fixture
def client_and_queue(tmp_path: Path) -> tuple[TestClient, HITLQueue]:
    queue = HITLQueue(db_url=f"sqlite:///{tmp_path / 'q.db'}")
    runs = PipelineRunStore(db_url=f"sqlite:///{tmp_path / 'q.db'}")
    app = create_app(queue=queue, runs=runs, uploads_dir=tmp_path / "up")
    return TestClient(app), queue


def _enqueue_credit_memo(queue: HITLQueue, case_id: str = "C-1") -> str:
    item = queue.enqueue(
        case_id=case_id,
        classification=Classification(
            class_label=ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
            confidence=0.92, evidence=["x"], rationale="price mismatch",
        ),
        recommendation=Recommendation(
            action=RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
            rationale="r", counterfactual="c", confidence=0.88,
        ),
        routing_decision=RoutingDecision(
            tier=HITLTier.APPROVER_REVIEW, routed_to="buyer", reason="r",
        ),
        draft=Draft(
            draft_type=DraftType.SUPPLIER_EMAIL,
            recipient="ap@v.example",
            subject="Credit memo needed",
            body="Please issue a credit memo.",
        ),
    )
    return item.id


def _enqueue_fraud(queue: HITLQueue, case_id: str = "C-FR") -> str:
    item = queue.enqueue(
        case_id=case_id,
        classification=Classification(
            class_label=ExceptionCategory.FRAUD_SIGNAL,
            confidence=0.91, evidence=[], rationale="r",
        ),
        recommendation=Recommendation(
            action=RecommendedAction.ESCALATE_TO_FRAUD,
            rationale="r", counterfactual="c", confidence=0.93,
        ),
        routing_decision=RoutingDecision(
            tier=HITLTier.SUPERVISOR_REVIEW, routed_to="ap_fraud_team", reason="r",
        ),
        draft=None,
    )
    return item.id


def test_api_approve_fires_executor_and_returns_execution_fields(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_credit_memo(queue, "C-API")

    r = client.post(f"/api/item/{item_id}/approve", json={"by": "tj"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "approved"
    assert data["execution_status"] == "simulated_success"
    assert data["execution_result"] is not None
    assert len(data["execution_result"]["steps"]) >= 1
    # The Email + hold-invoice steps should be there
    systems = {step["system"] for step in data["execution_result"]["steps"]}
    assert "Email" in systems


def test_html_approve_executes_and_detail_page_renders_card(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_credit_memo(queue, "C-HTML")

    # Approve via the HTML form route
    r = client.post(f"/item/{item_id}/approve", data={"note": "ok"}, follow_redirects=False)
    assert r.status_code == 303

    # Detail page now shows the execution card
    detail = client.get(f"/item/{item_id}")
    assert detail.status_code == 200
    body = detail.text
    assert "Action execution (simulated)" in body
    assert "simulated_success" in body
    assert "Email" in body
    # Confirm draft body excerpt rendered
    assert "credit memo" in body.lower()


def test_fraud_case_renders_halt_pay_run_step(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_fraud(queue, "C-FRD")
    client.post(f"/api/item/{item_id}/approve", json={})

    detail = client.get(f"/item/{item_id}")
    body = detail.text
    assert "HALT_PAY_RUN" in body
    assert "PagerDuty" in body
    assert "audit_trail" in body


def test_rejected_item_does_not_get_executed(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_credit_memo(queue, "C-RJ")
    client.post(f"/api/item/{item_id}/reject", json={"note": "no"})

    persisted = queue.get(item_id)
    assert persisted.status == "rejected"
    assert persisted.execution_status is None
    assert persisted.execution_result_json is None


def test_approve_with_edit_uses_edited_draft_in_execution(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_credit_memo(queue, "C-EDIT")
    edited = {
        "subject": "EDITED — tightened wording",
        "body": "EDITED body content with specific dollar amount.",
        "recipient": "ap-precise@v.example",
    }
    r = client.post(
        f"/api/item/{item_id}/approve-with-edit",
        json={"edited_draft": edited, "note": "polished tone"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "edited_approved"
    assert data["execution_status"] == "simulated_success"
    # The edited draft should drive the email step
    email_step = next(
        s for s in data["execution_result"]["steps"] if s["system"] == "Email"
    )
    assert email_step["target"] == "ap-precise@v.example"
    assert "EDITED" in email_step["payload_summary"]["subject"]


def test_queue_list_shows_executed_indicator(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    a = _enqueue_credit_memo(queue, "C-X")
    _enqueue_credit_memo(queue, "C-Y")
    client.post(f"/api/item/{a}/approve", json={})

    all_view = client.get("/queue/all")
    body = all_view.text
    # The executed-status column should render at least once
    assert "Executed" in body
    # Approved item shows the checkmark glyph
    assert "✓" in body
