"""Stage 9 — ops-level aggregator over the SQLite stores.

Pulls from `PipelineRunStore` (each /demo upload) and `HITLQueue` (each tier ≥ 2
case) to produce the metrics buyers ask for: auto-pass rate, HITL resolution
breakdown, classification mix, and case throughput.

Time windows mirror `Stage9Reader.WINDOWS`.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from p2p_agent.hitl.models import (
    RUN_COMPLETED,
    RUN_FAILED,
    STATUS_APPROVED,
    STATUS_EDITED_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from p2p_agent.hitl.queue import HITLQueue
from p2p_agent.hitl.runs import PipelineRunStore
from p2p_agent.stage9.recorder import WINDOWS


def _within(at: datetime | None, window: str | None) -> bool:
    if window is None or window == "all" or at is None:
        return True
    delta = WINDOWS.get(window)
    if delta is None:
        return True
    # Coerce to UTC-aware for comparison
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at >= datetime.now(UTC) - delta


class Stage9Aggregator:
    """Ops-level metrics for the dashboard."""

    def __init__(self, queue: HITLQueue, runs: PipelineRunStore) -> None:
        self.queue = queue
        self.runs = runs

    def ops_summary(self, window: str = "7d") -> dict[str, Any]:
        """Bundle every ops metric the dashboard renders."""
        run_rows = [
            r for r in self.runs.list(limit=10_000)
            if _within(r.uploaded_at, window)
        ]
        queue_rows = [
            i for i in self.queue.list(status=None, limit=10_000)
            if _within(i.created_at, window)
        ]
        return {
            "window": window,
            "cases_processed": self._cases_processed(run_rows),
            "auto_pass_rate": self._auto_pass_rate(run_rows),
            "classification_distribution": self._classification_distribution(run_rows),
            "action_distribution": self._action_distribution(run_rows),
            "hitl_resolution": self._hitl_resolution(queue_rows),
            "tier_breakdown": self._tier_breakdown(run_rows),
            "run_status": self._run_status(run_rows),
        }

    # ----- runs-derived metrics -----

    def _cases_processed(self, run_rows: list) -> int:
        return sum(1 for r in run_rows if r.status == RUN_COMPLETED)

    def _auto_pass_rate(self, run_rows: list) -> dict[str, Any]:
        """Tier-1 share of completed runs (the auto-pass-rate KPI)."""
        completed = [r for r in run_rows if r.status == RUN_COMPLETED]
        total = len(completed)
        if total == 0:
            return {"total": 0, "auto_passed": 0, "rate": 0.0}
        auto = sum(1 for r in completed if r.tier == 1)
        return {
            "total": total,
            "auto_passed": auto,
            "rate": round(auto / total, 4),
        }

    def _classification_distribution(self, run_rows: list) -> dict[str, int]:
        c = Counter(
            r.class_label for r in run_rows
            if r.status == RUN_COMPLETED and r.class_label
        )
        return dict(c.most_common())

    def _action_distribution(self, run_rows: list) -> dict[str, int]:
        c = Counter(
            r.recommended_action for r in run_rows
            if r.status == RUN_COMPLETED and r.recommended_action
        )
        return dict(c.most_common())

    def _tier_breakdown(self, run_rows: list) -> dict[int, int]:
        c = Counter(
            r.tier for r in run_rows
            if r.status == RUN_COMPLETED and r.tier is not None
        )
        return {int(k): v for k, v in sorted(c.items())}

    def _run_status(self, run_rows: list) -> dict[str, int]:
        c = Counter(r.status for r in run_rows)
        return dict(c)

    # ----- queue-derived metrics -----

    def _hitl_resolution(self, queue_rows: list) -> dict[str, Any]:
        c = Counter(r.status for r in queue_rows)
        total = sum(c.values())
        pending = c.get(STATUS_PENDING, 0)
        approved = c.get(STATUS_APPROVED, 0) + c.get(STATUS_EDITED_APPROVED, 0)
        rejected = c.get(STATUS_REJECTED, 0)
        resolved = approved + rejected
        return {
            "total": total,
            "pending": pending,
            "approved": c.get(STATUS_APPROVED, 0),
            "edited_approved": c.get(STATUS_EDITED_APPROVED, 0),
            "rejected": rejected,
            "approval_rate": round(approved / resolved, 4) if resolved else 0.0,
            "rejection_rate": round(rejected / resolved, 4) if resolved else 0.0,
        }
