"""FastAPI webapp round-trip tests for the HITL queue.

Uses `TestClient` against `create_app(queue)` with an isolated SQLite DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p2p_agent.hitl import HITLQueue
from p2p_agent.hitl.webapp.server import create_app
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


def _enqueue_sample(queue: HITLQueue, case_id: str = "C-1") -> str:
    cls = Classification(
        class_label=ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
        confidence=0.92,
        evidence=["unit_price_mismatch"],
        rationale="Price differs from PO.",
    )
    rec = Recommendation(
        action=RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
        rationale="Price variance; ask credit memo.",
        counterfactual="If price matched, auto.",
        confidence=0.88,
    )
    rd = RoutingDecision(
        tier=HITLTier.APPROVER_REVIEW, routed_to="buyer", reason="needs approval",
    )
    draft = Draft(
        draft_type=DraftType.SUPPLIER_EMAIL,
        recipient="ap@vendor.example",
        subject="Credit memo",
        body="Please issue a credit memo.",
    )
    item = queue.enqueue(
        case_id=case_id,
        classification=cls,
        recommendation=rec,
        routing_decision=rd,
        draft=draft,
    )
    return item.id


@pytest.fixture
def client_and_queue(tmp_path: Path) -> tuple[TestClient, HITLQueue]:
    queue = HITLQueue(db_url=f"sqlite:///{tmp_path / 'q.db'}")
    app = create_app(queue)
    return TestClient(app), queue


def test_index_redirects_to_demo(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, _queue = client_and_queue
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/demo")


def test_queue_html_renders_items(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    _enqueue_sample(queue, "C-A")
    _enqueue_sample(queue, "C-B")

    r = client.get("/queue")
    assert r.status_code == 200
    body = r.text
    assert "C-A" in body
    assert "C-B" in body
    assert "three_way_match_price_variance" in body
    assert "Pending" in body


def test_api_queue_returns_json(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-X")

    r = client.get("/api/queue")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == item_id
    assert data[0]["status"] == "pending"


def test_api_approve_round_trip(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-Y")

    r = client.post(f"/api/item/{item_id}/approve", json={"by": "tj", "note": "ok"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "approved"
    assert payload["resolved_by"] == "tj"

    # Pending list now empty
    r2 = client.get("/api/queue")
    assert r2.json() == []

    # All-list shows the approved item
    r3 = client.get("/api/queue?status=approved")
    assert len(r3.json()) == 1


def test_api_reject(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-Z")

    r = client.post(f"/api/item/{item_id}/reject", json={"note": "not legit"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_api_approve_with_edit_persists_draft(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-E")

    edited = {"subject": "Revised", "body": "Updated body.", "recipient": "ap@v.example"}
    r = client.post(
        f"/api/item/{item_id}/approve-with-edit",
        json={"edited_draft": edited, "note": "tightened wording"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "edited_approved"
    assert payload["edited_draft"] == edited


def test_api_double_resolve_returns_409(
    client_and_queue: tuple[TestClient, HITLQueue],
) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-D")

    client.post(f"/api/item/{item_id}/approve", json={})
    r = client.post(f"/api/item/{item_id}/reject", json={})
    assert r.status_code == 409


def test_html_approve_redirects(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-H")
    r = client.post(
        f"/item/{item_id}/approve", data={"note": "via UI"}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/queue")


def test_item_detail_html_renders(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    item_id = _enqueue_sample(queue, "C-D")
    r = client.get(f"/item/{item_id}")
    assert r.status_code == 200
    body = r.text
    assert "Classification" in body
    assert "Recommendation" in body
    assert "Draft" in body
    assert "Approve" in body


def test_item_detail_404(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, _ = client_and_queue
    r = client.get("/item/nope")
    assert r.status_code == 404


def test_api_stats(client_and_queue: tuple[TestClient, HITLQueue]) -> None:
    client, queue = client_and_queue
    a = _enqueue_sample(queue, "A")
    _enqueue_sample(queue, "B")
    client.post(f"/api/item/{a}/approve", json={})
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["by_status"]["pending"] == 1
    assert data["by_status"]["approved"] == 1
