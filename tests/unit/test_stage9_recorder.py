"""Unit tests for Stage9Reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from p2p_agent.stage9 import Stage9Reader
from p2p_agent.stage9.recorder import _percentile


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _row(
    *,
    minutes_ago: float,
    task: str,
    model: str = "deepseek/deepseek-v4-flash",
    cost: float = 0.001,
    latency_ms: float = 200.0,
    in_tok: int = 100,
    out_tok: int = 50,
) -> dict:
    return {
        "timestamp": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
        "task": task,
        "model": model,
        "provider": "openrouter",
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost,
        "latency_ms": latency_ms,
        "case_id": "x",
    }


def test_empty_log_returns_zero_summaries(tmp_path: Path) -> None:
    r = Stage9Reader(tmp_path / "calls.jsonl")
    cost = r.cost_summary(window="all")
    assert cost["total_calls"] == 0
    assert cost["total_usd"] == 0.0
    assert cost["by_task"] == {}
    latency = r.latency_summary(window="all")
    assert latency["overall_p95_ms"] == 0.0


def test_cost_summary_aggregates_by_task_and_model(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _row(minutes_ago=5, task="classify", model="m1", cost=0.001, in_tok=100, out_tok=50),
        _row(minutes_ago=4, task="classify", model="m1", cost=0.002, in_tok=200, out_tok=80),
        _row(minutes_ago=3, task="extract", model="m2", cost=0.003, in_tok=400, out_tok=100),
    ])
    r = Stage9Reader(log)
    s = r.cost_summary(window="all")
    assert s["total_calls"] == 3
    assert s["total_usd"] == pytest.approx(0.006)
    assert s["by_task"]["classify"]["calls"] == 2
    assert s["by_task"]["classify"]["cost_usd"] == pytest.approx(0.003)
    assert s["by_task"]["classify"]["input_tokens"] == 300
    assert s["by_task"]["extract"]["calls"] == 1
    assert s["by_model"]["m1"]["calls"] == 2
    assert s["by_model"]["m2"]["calls"] == 1


def test_window_filters_old_rows(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _row(minutes_ago=120, task="old", cost=10.0),  # 2h ago — outside 1h
        _row(minutes_ago=30, task="new", cost=1.0),    # 30m ago — inside 1h
    ])
    r = Stage9Reader(log)
    one_hour = r.cost_summary(window="1h")
    assert one_hour["total_calls"] == 1
    assert one_hour["total_usd"] == pytest.approx(1.0)
    all_ = r.cost_summary(window="all")
    assert all_["total_calls"] == 2


def test_latency_percentiles(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    # 100 rows for one task with latencies 1..100 ms
    _write_jsonl(log, [
        _row(minutes_ago=1, task="t", latency_ms=float(i)) for i in range(1, 101)
    ])
    r = Stage9Reader(log)
    s = r.latency_summary(window="all")
    by = s["by_task"]["t"]
    assert by["calls"] == 100
    # p50 = 50.5, p95 ≈ 95.05, p99 ≈ 99.01
    assert 49.5 <= by["p50_ms"] <= 51.5
    assert 94.0 <= by["p95_ms"] <= 96.0
    assert 98.0 <= by["p99_ms"] <= 100.0
    assert by["max_ms"] == 100.0


def test_tail_returns_most_recent(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _row(minutes_ago=10 - i, task=f"task-{i}") for i in range(5)
    ])
    r = Stage9Reader(log)
    out = r.tail(n=3)
    assert len(out) == 3
    # Newest first
    assert out[0]["task"] == "task-4"
    assert out[1]["task"] == "task-3"
    assert "_ts" not in out[0]  # internal field is stripped


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    log.write_text(
        "\n".join([
            json.dumps(_row(minutes_ago=1, task="good")),
            "not json at all",
            json.dumps(_row(minutes_ago=1, task="good2")),
            "",
        ]) + "\n",
    )
    r = Stage9Reader(log)
    s = r.cost_summary(window="all")
    assert s["total_calls"] == 2


def test_cache_invalidates_on_mtime_change(tmp_path: Path) -> None:
    import time
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [_row(minutes_ago=1, task="t", cost=0.001)])
    r = Stage9Reader(log)
    assert r.cost_summary(window="all")["total_calls"] == 1
    # Wait briefly to ensure mtime advances; then append
    time.sleep(0.01)
    with log.open("a") as f:
        f.write(json.dumps(_row(minutes_ago=0, task="t", cost=0.002)) + "\n")
    # Force a touch so st_mtime is definitely different on filesystems with coarse resolution
    import os
    os.utime(log, None)
    assert r.cost_summary(window="all")["total_calls"] == 2


def test_percentile_helper() -> None:
    assert _percentile([], 0.5) == 0.0
    assert _percentile([42.0], 0.99) == 42.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == pytest.approx(3.0)
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 1.0) == 5.0
