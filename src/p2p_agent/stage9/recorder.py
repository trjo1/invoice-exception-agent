"""Stage 9 — read-side over `logs/llm_calls.jsonl`.

Every `ModelClient.complete()` call writes a row with timestamp, task, model,
input/output tokens, cost, latency, case_id. `Stage9Reader` parses that file
and produces aggregate views (cost summary, latency p50/p95/p99, tail).

In-memory cache invalidated by file mtime so the dashboard doesn't re-parse
on every request unless the log has actually grown.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path("./logs/llm_calls.jsonl")


# Time-window labels used by the dashboard
WINDOWS: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def _parse_ts(s: str) -> datetime | None:
    try:
        # Tolerate both 'Z' and timezone-aware ISO strings
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Linear interpolation between adjacent indices
    pos = (len(sorted_values) - 1) * p
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = pos - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * frac


class Stage9Reader:
    """Cached reader for `logs/llm_calls.jsonl`."""

    def __init__(self, log_path: Path | str = DEFAULT_LOG_PATH) -> None:
        self.log_path = Path(log_path)
        self._cache_mtime: float | None = None
        self._cached_rows: list[dict[str, Any]] = []

    # ----- cache -----

    def _load(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        mtime = self.log_path.stat().st_mtime
        if self._cache_mtime is not None and mtime == self._cache_mtime:
            return self._cached_rows

        rows: list[dict[str, Any]] = []
        with self.log_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obj["_ts"] = _parse_ts(obj.get("timestamp", ""))
                rows.append(obj)
        # Sort by timestamp ascending so window filters are linear-scan friendly
        rows.sort(key=lambda r: r.get("_ts") or datetime.min.replace(tzinfo=UTC))
        self._cached_rows = rows
        self._cache_mtime = mtime
        return rows

    def _windowed(self, window: str | None = "7d") -> list[dict[str, Any]]:
        rows = self._load()
        if not rows or not window or window == "all":
            return rows
        delta = WINDOWS.get(window)
        if delta is None:
            return rows
        cutoff = datetime.now(UTC) - delta
        # Linear scan from the end (newest first); rows are sorted asc
        idx = bisect.bisect_left(
            [r.get("_ts") or datetime.min.replace(tzinfo=UTC) for r in rows],
            cutoff,
        )
        return rows[idx:]

    # ----- summaries -----

    def cost_summary(self, window: str = "7d") -> dict[str, Any]:
        """Total cost in USD + per-task and per-model breakdowns."""
        rows = self._windowed(window)
        total = 0.0
        by_task: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0},
        )
        by_model: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"cost_usd": 0.0, "calls": 0},
        )
        for r in rows:
            cost = float(r.get("cost_usd", 0.0) or 0.0)
            total += cost
            task = str(r.get("task") or "unknown")
            model = str(r.get("model") or "unknown")
            t = by_task[task]
            t["cost_usd"] = float(t["cost_usd"]) + cost
            t["calls"] = int(t["calls"]) + 1
            t["input_tokens"] = int(t["input_tokens"]) + int(r.get("input_tokens", 0) or 0)
            t["output_tokens"] = int(t["output_tokens"]) + int(r.get("output_tokens", 0) or 0)
            m = by_model[model]
            m["cost_usd"] = float(m["cost_usd"]) + cost
            m["calls"] = int(m["calls"]) + 1
        return {
            "window": window,
            "total_calls": len(rows),
            "total_usd": round(total, 4),
            "by_task": {k: dict(v) for k, v in sorted(by_task.items())},
            "by_model": {k: dict(v) for k, v in sorted(by_model.items())},
        }

    def latency_summary(self, window: str = "7d") -> dict[str, Any]:
        """Per-task p50/p95/p99 latency in milliseconds."""
        rows = self._windowed(window)
        per_task: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            lat = r.get("latency_ms")
            if lat is None:
                continue
            try:
                per_task[str(r.get("task") or "unknown")].append(float(lat))
            except (TypeError, ValueError):
                continue
        out: dict[str, dict[str, float | int]] = {}
        for task, values in sorted(per_task.items()):
            sv = sorted(values)
            out[task] = {
                "calls": len(sv),
                "p50_ms": round(_percentile(sv, 0.50), 1),
                "p95_ms": round(_percentile(sv, 0.95), 1),
                "p99_ms": round(_percentile(sv, 0.99), 1),
                "max_ms": round(sv[-1], 1) if sv else 0.0,
                "mean_ms": round(sum(sv) / len(sv), 1) if sv else 0.0,
            }
        # Overall p95 across all tasks
        all_lats = sorted(lat for v in per_task.values() for lat in v)
        return {
            "window": window,
            "overall_p50_ms": round(_percentile(all_lats, 0.50), 1),
            "overall_p95_ms": round(_percentile(all_lats, 0.95), 1),
            "overall_p99_ms": round(_percentile(all_lats, 0.99), 1),
            "by_task": out,
        }

    def per_run_cost(self, last_n_runs: int = 20) -> dict[str, Any]:
        """Average $-cost of a single pipeline run, derived from llm_calls.jsonl.

        Each LLM call's `case_id` is `{run_id}::{node}` (e.g.
        `ab12cd::classify`). Bucket calls by the run-id prefix, sum cost per
        run, average over the most recent `last_n_runs` distinct run-ids.

        Returns a small dict the demo template can render. Empty / "no data"
        when there aren't enough completed runs yet.
        """
        rows = self._load()
        # Bucket cost by run_id (the part before "::")
        per_run: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"cost_usd": 0.0, "calls": 0, "last_ts": None},
        )
        for r in rows:
            case_id = r.get("case_id") or ""
            if "::" not in case_id:
                continue
            run_id = case_id.split("::", 1)[0]
            if not run_id or run_id.startswith("warmup"):
                continue  # skip the demo-warmup runs
            bucket = per_run[run_id]
            bucket["cost_usd"] = float(bucket["cost_usd"]) + float(r.get("cost_usd", 0.0) or 0.0)
            bucket["calls"] = int(bucket["calls"]) + 1
            ts = r.get("_ts")
            if ts is not None and (bucket["last_ts"] is None or ts > bucket["last_ts"]):
                bucket["last_ts"] = ts

        if not per_run:
            return {
                "sample_size": 0,
                "avg_usd": 0.0,
                "min_usd": 0.0,
                "max_usd": 0.0,
                "total_runs_seen": 0,
            }

        # Sort runs by recency (last_ts desc), take the most recent N
        sorted_runs = sorted(
            per_run.values(),
            key=lambda b: b["last_ts"] or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        sample = sorted_runs[:last_n_runs]
        costs = [float(b["cost_usd"]) for b in sample]
        return {
            "sample_size": len(sample),
            "avg_usd": round(sum(costs) / len(costs), 5) if costs else 0.0,
            "min_usd": round(min(costs), 5) if costs else 0.0,
            "max_usd": round(max(costs), 5) if costs else 0.0,
            "total_runs_seen": len(per_run),
        }

    def tail(self, n: int = 50, window: str | None = None) -> list[dict[str, Any]]:
        """Most recent N calls. Strips the internal `_ts` field."""
        rows = self._windowed(window) if window else self._load()
        recent = rows[-n:][::-1]
        return [
            {k: v for k, v in r.items() if k != "_ts"}
            for r in recent
        ]
