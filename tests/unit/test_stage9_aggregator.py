"""Unit tests for Stage9Aggregator (ops metrics from queue + runs stores)."""

from __future__ import annotations

from pathlib import Path

import pytest

from p2p_agent.hitl import HITLQueue, PipelineRunStore
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision
from p2p_agent.stage9 import Stage9Aggregator


@pytest.fixture
def stores(tmp_path: Path) -> tuple[HITLQueue, PipelineRunStore]:
    db = tmp_path / "stage9.db"
    return HITLQueue(db_url=f"sqlite:///{db}"), PipelineRunStore(db_url=f"sqlite:///{db}")


def _seed_run(
    runs: PipelineRunStore,
    *,
    class_label: str = "none",
    action: str = "auto_resolve",
    tier: int = 1,
    routed_to: str = "none",
    hitl_item_id: str | None = None,
    status: str = "completed",
) -> None:
    run = runs.create(uploaded_filename="x.pdf", stored_pdf_path="/tmp/x.pdf")
    if status == "completed":
        runs.complete(
            run.id,
            class_label=class_label,
            recommended_action=action,
            tier=tier,
            routed_to=routed_to,
            hitl_item_id=hitl_item_id,
            cost_usd=0.005,
            latency_ms=12000,
            result_json={"x": 1},
        )
    elif status == "failed":
        runs.fail(run.id, error_message="oops")


def _enqueue(queue: HITLQueue, case_id: str = "C") -> str:
    cls = Classification(
        class_label=ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
        confidence=0.9, evidence=["x"], rationale="r",
    )
    rec = Recommendation(
        action=RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
        rationale="r", counterfactual="cf", confidence=0.85,
    )
    rd = RoutingDecision(tier=HITLTier.APPROVER_REVIEW, routed_to="buyer", reason="r")
    draft = Draft(
        draft_type=DraftType.SUPPLIER_EMAIL,
        recipient="ap@v.example", subject="Subj", body="B",
    )
    item = queue.enqueue(
        case_id=case_id,
        classification=cls,
        recommendation=rec,
        routing_decision=rd,
        draft=draft,
    )
    return item.id


def test_empty_stores_zero_metrics(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    agg = Stage9Aggregator(queue=queue, runs=runs)
    ops = agg.ops_summary(window="all")
    assert ops["cases_processed"] == 0
    assert ops["auto_pass_rate"]["rate"] == 0.0
    assert ops["hitl_resolution"]["total"] == 0


def test_auto_pass_rate_counts_tier1(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    _seed_run(runs, tier=1)
    _seed_run(runs, tier=1)
    _seed_run(runs, tier=2, hitl_item_id="x")
    _seed_run(runs, tier=3, hitl_item_id="y")
    agg = Stage9Aggregator(queue=queue, runs=runs)
    apr = agg.ops_summary(window="all")["auto_pass_rate"]
    assert apr["total"] == 4
    assert apr["auto_passed"] == 2
    assert apr["rate"] == 0.5


def test_classification_distribution(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    _seed_run(runs, class_label="none")
    _seed_run(runs, class_label="none")
    _seed_run(runs, class_label="fraud_signal")
    agg = Stage9Aggregator(queue=queue, runs=runs)
    dist = agg.ops_summary(window="all")["classification_distribution"]
    assert dist == {"none": 2, "fraud_signal": 1}


def test_tier_breakdown(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    _seed_run(runs, tier=1)
    _seed_run(runs, tier=2)
    _seed_run(runs, tier=2)
    _seed_run(runs, tier=3)
    agg = Stage9Aggregator(queue=queue, runs=runs)
    tb = agg.ops_summary(window="all")["tier_breakdown"]
    assert tb == {1: 1, 2: 2, 3: 1}


def test_hitl_resolution_breakdown(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    a = _enqueue(queue, "A")
    b = _enqueue(queue, "B")
    c = _enqueue(queue, "C")
    _enqueue(queue, "D")  # remains pending
    queue.approve(a)
    queue.approve_with_edit(b, edited_draft={"subject": "x"})
    queue.reject(c)

    agg = Stage9Aggregator(queue=queue, runs=runs)
    hr = agg.ops_summary(window="all")["hitl_resolution"]
    assert hr["total"] == 4
    assert hr["pending"] == 1
    assert hr["approved"] == 1
    assert hr["edited_approved"] == 1
    assert hr["rejected"] == 1
    # Resolved = 3 (1 rejected + 1 approved + 1 edited_approved). Approved=2 of 3.
    assert hr["approval_rate"] == pytest.approx(2 / 3, abs=0.01)


def test_action_distribution(stores: tuple[HITLQueue, PipelineRunStore]) -> None:
    queue, runs = stores
    _seed_run(runs, action="auto_resolve")
    _seed_run(runs, action="auto_resolve")
    _seed_run(runs, action="request_supplier_credit_memo")
    agg = Stage9Aggregator(queue=queue, runs=runs)
    dist = agg.ops_summary(window="all")["action_distribution"]
    assert dist == {"auto_resolve": 2, "request_supplier_credit_memo": 1}


def test_failed_runs_excluded_from_completed_counts(
    stores: tuple[HITLQueue, PipelineRunStore],
) -> None:
    queue, runs = stores
    _seed_run(runs, status="completed", tier=1)
    _seed_run(runs, status="failed")
    agg = Stage9Aggregator(queue=queue, runs=runs)
    ops = agg.ops_summary(window="all")
    assert ops["cases_processed"] == 1
    assert ops["auto_pass_rate"]["total"] == 1
    # run_status counter should still show the failure
    assert ops["run_status"].get("failed") == 1
