"""Integration tests for /stage9 routes (HTML + JSON)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p2p_agent.hitl import HITLQueue, PipelineRunStore
from p2p_agent.hitl.webapp.server import create_app


def _write_calls(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": (datetime.now(UTC) - timedelta(minutes=i)).isoformat(),
            "task": "exception_classification" if i % 2 == 0 else "invoice_extraction",
            "model": "deepseek/deepseek-v4-flash",
            "provider": "openrouter",
            "input_tokens": 1000 + i,
            "output_tokens": 200 + i,
            "cost_usd": 0.001 + i * 0.0001,
            "latency_ms": 100.0 + i,
            "case_id": f"c-{i}",
        }
        for i in range(10)
    ]
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "stage9.db"
    log = tmp_path / "calls.jsonl"
    _write_calls(log)
    queue = HITLQueue(db_url=f"sqlite:///{db}")
    runs = PipelineRunStore(db_url=f"sqlite:///{db}")
    # Seed a pipeline run so ops metrics aren't zero
    run = runs.create(uploaded_filename="x.pdf", stored_pdf_path="/tmp/x.pdf")
    runs.complete(
        run.id,
        class_label="none",
        recommended_action="auto_resolve",
        tier=1,
        routed_to="none",
        hitl_item_id=None,
        cost_usd=0.005,
        latency_ms=12000,
        result_json={"x": 1},
    )
    app = create_app(queue=queue, runs=runs, uploads_dir=tmp_path / "up", llm_log_path=log)
    return TestClient(app)


def test_stage9_html_renders(client: TestClient) -> None:
    r = client.get("/stage9")
    assert r.status_code == 200
    body = r.text
    assert "Stage 9" in body
    assert "Classification mix" in body
    assert "Cost breakdown" in body
    assert "Latency" in body
    assert "exception_classification" in body


def test_stage9_window_query(client: TestClient) -> None:
    r = client.get("/stage9?window=1h")
    assert r.status_code == 200
    # The filterbar shows the active window
    assert 'href="?window=1h" class="active"' in r.text


def test_stage9_invalid_window_falls_back_to_7d(client: TestClient) -> None:
    r = client.get("/stage9?window=bogus")
    assert r.status_code == 200
    assert 'href="?window=7d" class="active"' in r.text


def test_api_stage9_cost(client: TestClient) -> None:
    r = client.get("/api/stage9/cost?window=all")
    assert r.status_code == 200
    data = r.json()
    assert data["total_calls"] == 10
    assert "by_task" in data
    assert "exception_classification" in data["by_task"]
    assert "by_model" in data


def test_api_stage9_latency(client: TestClient) -> None:
    r = client.get("/api/stage9/latency?window=all")
    assert r.status_code == 200
    data = r.json()
    assert "overall_p95_ms" in data
    assert "by_task" in data
    assert data["by_task"]["exception_classification"]["calls"] >= 1


def test_api_stage9_ops(client: TestClient) -> None:
    r = client.get("/api/stage9/ops?window=all")
    assert r.status_code == 200
    data = r.json()
    assert data["cases_processed"] == 1
    assert data["auto_pass_rate"]["rate"] == 1.0  # the seeded run is Tier 1


def test_api_stage9_tail(client: TestClient) -> None:
    r = client.get("/api/stage9/tail?n=5")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 5
    # Newest first
    assert data[0]["case_id"] == "c-0"


def test_stage9_link_in_base_nav(client: TestClient) -> None:
    r = client.get("/demo")
    assert r.status_code == 200
    assert "Stage 9 metrics" in r.text
    assert 'href="/stage9"' in r.text
