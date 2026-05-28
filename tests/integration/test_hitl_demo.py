"""Integration tests for the /demo upload+run flow.

Uses a stubbed `pipeline_runner` so the tests don't need API keys or LLM calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p2p_agent.hitl import HITLQueue, PipelineRunStore
from p2p_agent.hitl.webapp.server import create_app
from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.extraction import HeaderFieldsExtraction, InvoiceExtraction
from p2p_agent.models.pipeline import PipelineResult, StepTrace
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


def _fake_extraction() -> InvoiceExtraction:
    return InvoiceExtraction(
        invoice_id="INV-STUB-001",
        po_reference="PO-FAKE-123",
        invoice_date="2026-05-13",
        currency="USD",
        subtotal=1000.0,
        total=1100.0,
        payment_terms="NET-30",
        header_fields=HeaderFieldsExtraction(
            vendor_name="Stub Vendor",
            vendor_address="1 Test St",
            vendor_tax_id="11-1111111",
            buyer_name="Stub Buyer",
            buyer_address="2 Buyer Ave",
            buyer_po_contact="po@buyer.example",
        ),
        line_items=[],
        tax=[],
        field_confidence={},
    )


def _make_stub_pipeline(
    *,
    queue_to_use: HITLQueue,
    tier: HITLTier = HITLTier.APPROVER_REVIEW,
    action: RecommendedAction = RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO,
    cls: ExceptionCategory = ExceptionCategory.THREE_WAY_MATCH_PRICE_VARIANCE,
):
    """Returns a coroutine fn matching `run_invoice_pipeline`'s signature."""

    async def stub(pdf_path: Path, *, case_id: str | None = None, **_kw) -> PipelineResult:
        classification = Classification(
            class_label=cls, confidence=0.9, evidence=["stub"], rationale="stub",
        )
        recommendation = Recommendation(
            action=action, rationale="stub", counterfactual="cf", confidence=0.88,
        )
        routing_decision = RoutingDecision(
            tier=tier, routed_to="buyer", reason="stub routing",
        )
        draft = Draft(
            draft_type=DraftType.SUPPLIER_EMAIL,
            recipient="ap@v.example",
            subject="Subj",
            body="Body.",
        )
        hitl_item_id = None
        if int(tier) >= 2:
            item = queue_to_use.enqueue(
                case_id=case_id or "stub",
                classification=classification,
                recommendation=recommendation,
                routing_decision=routing_decision,
                draft=draft,
            )
            hitl_item_id = item.id
        return PipelineResult(
            case_id=case_id,
            extraction=_fake_extraction(),
            classification=classification,
            recommendation=recommendation,
            routing_decision=routing_decision,
            draft=draft,
            hitl_item_id=hitl_item_id,
            steps=[
                StepTrace(name="extract", latency_ms=120, cost_usd=0.0),
                StepTrace(name="classify", latency_ms=210, cost_usd=0.0),
            ],
        )

    return stub


@pytest.fixture
def client_factory(tmp_path: Path):
    def _build(**kwargs) -> tuple[TestClient, HITLQueue, PipelineRunStore]:
        db = tmp_path / "demo.db"
        uploads = tmp_path / "uploads"
        queue = HITLQueue(db_url=f"sqlite:///{db}")
        runs = PipelineRunStore(db_url=f"sqlite:///{db}")
        stub = _make_stub_pipeline(queue_to_use=queue, **kwargs)
        app = create_app(
            queue=queue, runs=runs, uploads_dir=uploads, pipeline_runner=stub,
        )
        return TestClient(app), queue, runs

    return _build


def _fake_pdf_bytes() -> bytes:
    # Minimal but valid-ish PDF header — extractor stub doesn't actually parse it.
    return b"%PDF-1.4\n%fake test pdf for upload\n%%EOF"


def test_index_redirects_to_demo(client_factory) -> None:
    client, _, _ = client_factory()
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/demo")


def test_demo_landing_renders(client_factory) -> None:
    client, _, _ = client_factory()
    r = client.get("/demo")
    assert r.status_code == 200
    assert "Upload an invoice PDF" in r.text
    assert "Run pipeline" in r.text


def test_upload_rejects_non_pdf(client_factory) -> None:
    client, _, _ = client_factory()
    r = client.post(
        "/demo/run",
        files={"file": ("not.txt", b"hello", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_upload_pdf_runs_pipeline_and_redirects_to_detail(client_factory) -> None:
    client, queue, runs = client_factory()
    r = client.post(
        "/demo/run",
        files={"file": ("invoice.pdf", _fake_pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/demo/run/" in r.headers["location"]

    run_id = r.headers["location"].split("/")[-1]
    run = runs.get(run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.uploaded_filename == "invoice.pdf"
    assert run.class_label == "three_way_match_price_variance"
    assert run.recommended_action == "request_supplier_credit_memo"
    assert run.tier == 2
    assert run.hitl_item_id is not None  # tier 2 enqueues
    assert queue.stats()["total"] == 1


def test_run_detail_renders_all_node_outputs(client_factory) -> None:
    client, _, _ = client_factory()
    r = client.post(
        "/demo/run",
        files={"file": ("invoice.pdf", _fake_pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    run_id = r.headers["location"].split("/")[-1]

    detail = client.get(f"/demo/run/{run_id}")
    assert detail.status_code == 200
    body = detail.text
    # Each node section present
    assert "Extract" in body
    assert "Classify" in body
    assert "Decide" in body
    assert "Route" in body
    assert "Draft" in body
    # Key values render
    assert "PO-FAKE-123" in body
    assert "Stub Vendor" in body
    assert "three_way_match_price_variance" in body
    assert "request_supplier_credit_memo" in body


def test_tier1_run_no_queue_link(client_factory) -> None:
    client, queue, runs = client_factory(
        tier=HITLTier.AUTO_PASS,
        action=RecommendedAction.AUTO_RESOLVE,
        cls=ExceptionCategory.NONE,
    )
    r = client.post(
        "/demo/run",
        files={"file": ("clean.pdf", _fake_pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    run_id = r.headers["location"].split("/")[-1]
    run = runs.get(run_id)
    assert run.tier == 1
    assert run.hitl_item_id is None
    assert queue.stats()["total"] == 0


def test_runs_list_html(client_factory) -> None:
    client, _, _ = client_factory()
    client.post("/demo/run", files={"file": ("a.pdf", _fake_pdf_bytes(), "application/pdf")})
    client.post("/demo/run", files={"file": ("b.pdf", _fake_pdf_bytes(), "application/pdf")})

    r = client.get("/demo/runs")
    assert r.status_code == 200
    assert "a.pdf" in r.text
    assert "b.pdf" in r.text


def test_api_runs_returns_json(client_factory) -> None:
    client, _, _ = client_factory()
    client.post("/demo/run", files={"file": ("x.pdf", _fake_pdf_bytes(), "application/pdf")})
    r = client.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["uploaded_filename"] == "x.pdf"
    assert data[0]["status"] == "completed"


def test_api_run_detail_returns_full_result(client_factory) -> None:
    client, _, _ = client_factory()
    post = client.post(
        "/demo/run",
        files={"file": ("z.pdf", _fake_pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    run_id = post.headers["location"].split("/")[-1]
    r = client.get(f"/api/run/{run_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["result"]["classification"]["class_label"] == "three_way_match_price_variance"


def test_pdf_download(client_factory) -> None:
    client, _, _ = client_factory()
    post = client.post(
        "/demo/run",
        files={"file": ("dl.pdf", _fake_pdf_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    run_id = post.headers["location"].split("/")[-1]
    r = client.get(f"/demo/run/{run_id}/pdf")
    assert r.status_code == 200
    assert r.content == _fake_pdf_bytes()


def test_run_detail_404(client_factory) -> None:
    client, _, _ = client_factory()
    r = client.get("/demo/run/missing-id")
    assert r.status_code == 404
