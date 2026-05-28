"""Unit tests for the HITLQueue class.

Backed by an isolated SQLite file per test so runs don't interfere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from p2p_agent.hitl import (
    STATUS_APPROVED,
    STATUS_EDITED_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    HITLQueue,
    HITLQueueError,
)
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


@pytest.fixture
def queue(tmp_path: Path) -> HITLQueue:
    return HITLQueue(db_url=f"sqlite:///{tmp_path / 'q.db'}")


def _sample(
    *,
    cls: ExceptionCategory = ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
    action: RecommendedAction = RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
    tier: HITLTier = HITLTier.APPROVER_REVIEW,
    routed_to: str = "buyer",
    with_draft: bool = True,
) -> dict:
    return {
        "classification": Classification(
            class_label=cls, confidence=0.9, evidence=["x"], rationale="r",
        ),
        "recommendation": Recommendation(
            action=action, rationale="r", counterfactual="cf", confidence=0.85,
        ),
        "routing_decision": RoutingDecision(tier=tier, routed_to=routed_to, reason="why"),
        "draft": Draft(
            draft_type=DraftType.SUPPLIER_EMAIL,
            recipient="ap@v.example",
            subject="Subj",
            body="Body.",
        ) if with_draft else None,
    }


def test_enqueue_returns_pending_item(queue: HITLQueue) -> None:
    item = queue.enqueue(case_id="C-1", **_sample())
    assert item.status == STATUS_PENDING
    assert item.case_id == "C-1"
    assert item.tier == 2
    assert item.routed_to == "buyer"
    assert item.class_label == "three_way_match_price_variance"
    assert item.recommended_action == "request_supplier_credit_memo"
    assert "classification" in item.payload_json
    assert "draft" in item.payload_json


def test_list_filters_by_status_tier_routed_to(queue: HITLQueue) -> None:
    queue.enqueue(case_id="A", **_sample(tier=HITLTier.APPROVER_REVIEW, routed_to="buyer"))
    queue.enqueue(case_id="B", **_sample(tier=HITLTier.SUPERVISOR_REVIEW, routed_to="treasury"))
    queue.enqueue(case_id="C", **_sample(tier=HITLTier.SUPERVISOR_REVIEW, routed_to="ap_fraud_team"))

    assert len(queue.list(status="pending")) == 3
    assert len(queue.list(tier=3)) == 2
    assert len(queue.list(routed_to="treasury")) == 1
    assert len(queue.list(status=None)) == 3


def test_approve_transitions_status_and_writes_audit(queue: HITLQueue) -> None:
    item = queue.enqueue(case_id="C", **_sample())
    approved = queue.approve(item.id, by="tj", note="ok")
    assert approved.status == STATUS_APPROVED
    assert approved.resolved_by == "tj"
    assert approved.resolution_note == "ok"

    got = queue.get(item.id)
    assert got is not None
    assert len(got.audit_entries) == 2  # enqueue + approve
    assert got.audit_entries[-1].to_status == STATUS_APPROVED


def test_reject(queue: HITLQueue) -> None:
    item = queue.enqueue(case_id="C", **_sample())
    rejected = queue.reject(item.id, by="tj", note="not legit")
    assert rejected.status == STATUS_REJECTED


def test_approve_with_edit_persists_edited_draft(queue: HITLQueue) -> None:
    item = queue.enqueue(case_id="C", **_sample())
    edited = {"subject": "New subj", "body": "Edited body.", "recipient": "ap@v.example"}
    out = queue.approve_with_edit(item.id, edited_draft=edited, by="tj", note="cleaned tone")
    assert out.status == STATUS_EDITED_APPROVED
    assert out.edited_draft_json == edited


def test_double_resolve_raises(queue: HITLQueue) -> None:
    item = queue.enqueue(case_id="C", **_sample())
    queue.approve(item.id)
    with pytest.raises(HITLQueueError):
        queue.approve(item.id)


def test_get_missing_returns_none(queue: HITLQueue) -> None:
    assert queue.get("does-not-exist") is None


def test_stats_counts_by_status_tier_and_routed_to(queue: HITLQueue) -> None:
    a = queue.enqueue(case_id="A", **_sample(tier=HITLTier.APPROVER_REVIEW, routed_to="buyer"))
    b = queue.enqueue(case_id="B", **_sample(tier=HITLTier.SUPERVISOR_REVIEW, routed_to="treasury"))
    queue.approve(a.id)
    queue.reject(b.id)

    s = queue.stats()
    assert s["total"] == 2
    assert s["by_status"]["approved"] == 1
    assert s["by_status"]["rejected"] == 1
    assert s["by_tier"][2] == 1
    assert s["by_tier"][3] == 1
    assert s["by_routed_to"]["buyer"] == 1
    assert s["by_routed_to"]["treasury"] == 1


def test_clear(queue: HITLQueue) -> None:
    queue.enqueue(case_id="A", **_sample())
    queue.enqueue(case_id="B", **_sample())
    assert queue.stats()["total"] == 2
    queue.clear()
    assert queue.stats()["total"] == 0
