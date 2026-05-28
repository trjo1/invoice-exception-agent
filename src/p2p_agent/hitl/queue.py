"""HITL approval queue — thin SQLAlchemy-backed inbox.

Same interface against SQLite (default) or Postgres. Every status transition
writes an `HITLAuditEntry` in the same transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from p2p_agent.hitl.models import (
    STATUS_APPROVED,
    STATUS_EDITED_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    TERMINAL_STATUSES,
    Base,
    HITLAuditEntry,
    HITLItem,
)
from p2p_agent.models.classification import Classification
from p2p_agent.models.draft import Draft
from p2p_agent.models.execution import ExecutionResult
from p2p_agent.models.recommendation import Recommendation
from p2p_agent.models.routing import RoutingDecision


DEFAULT_DB_URL = "sqlite:///./logs/hitl_queue.db"


class HITLQueueError(Exception):
    """Raised on illegal state transitions (e.g., approving a rejected item)."""


def _enable_sqlite_wal(dbapi_conn, _conn_record) -> None:  # pragma: no cover
    """Turn on WAL mode for SQLite — better concurrent-write behaviour."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


class HITLQueue:
    """Approval queue, persisted via SQLAlchemy.

    Default DB lives at `logs/hitl_queue.db`. Pass a Postgres URL to swap
    backends — schema is identical.
    """

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        if db_url.startswith("sqlite:///./"):
            db_path = Path(db_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        connect_args: dict[str, Any] = {}
        if db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self.engine: Engine = create_engine(
            db_url, future=True, connect_args=connect_args,
        )
        if db_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_wal)

        Base.metadata.create_all(self.engine)
        self._migrate_add_execution_columns()
        self._SessionMaker = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _migrate_add_execution_columns(self) -> None:
        """Add execution_* columns to an existing hitl_items table if missing.

        SQLite supports ALTER TABLE ADD COLUMN with default NULL. Idempotent —
        skips columns that already exist. Lets pre-Phase-9 databases pick up
        the new columns without dropping data.
        """
        try:
            inspector = inspect(self.engine)
            if "hitl_items" not in inspector.get_table_names():
                return
            existing = {col["name"] for col in inspector.get_columns("hitl_items")}
            adds: list[str] = []
            if "execution_status" not in existing:
                adds.append("ALTER TABLE hitl_items ADD COLUMN execution_status VARCHAR(32)")
            if "execution_result_json" not in existing:
                adds.append("ALTER TABLE hitl_items ADD COLUMN execution_result_json JSON")
            if "executed_at" not in existing:
                adds.append("ALTER TABLE hitl_items ADD COLUMN executed_at DATETIME")
            if not adds:
                return
            with self.engine.begin() as conn:
                for stmt in adds:
                    conn.execute(text(stmt))
        except Exception:
            # Best-effort migration; if the engine doesn't support it (some non-SQLite
            # dialects in odd configs), users can wipe the DB via make hitl-clear.
            pass

    @contextmanager
    def _session(self) -> Iterator[Session]:
        s = self._SessionMaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ----- write paths ----------------------------------------------------

    def enqueue(
        self,
        *,
        case_id: str,
        classification: Classification,
        recommendation: Recommendation,
        routing_decision: RoutingDecision,
        draft: Draft | None = None,
    ) -> HITLItem:
        """Add a new pending item. Returns the persisted row (detached)."""
        payload = {
            "classification": classification.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json"),
            "routing_decision": routing_decision.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json") if draft is not None else None,
        }
        with self._session() as s:
            item = HITLItem(
                case_id=case_id,
                tier=int(routing_decision.tier),
                routed_to=routing_decision.routed_to,
                routing_reason=routing_decision.reason,
                class_label=classification.class_label.value,
                classification_confidence=classification.confidence,
                recommended_action=recommendation.action.value,
                confidence=recommendation.confidence,
                status=STATUS_PENDING,
                payload_json=payload,
            )
            s.add(item)
            s.flush()
            s.add(HITLAuditEntry(
                item_id=item.id,
                actor="system",
                from_status="(new)",
                to_status=STATUS_PENDING,
                note="enqueued from pipeline",
            ))
            # Refresh to pull defaults
            s.refresh(item)
            s.expunge(item)
            return item

    def _resolve(
        self,
        item_id: str,
        *,
        new_status: str,
        actor: str,
        note: str,
        edited_draft: dict[str, Any] | None = None,
    ) -> HITLItem:
        with self._session() as s:
            item = s.get(HITLItem, item_id)
            if item is None:
                raise HITLQueueError(f"Item {item_id!r} not found")
            if item.status in TERMINAL_STATUSES:
                raise HITLQueueError(
                    f"Item {item_id!r} already resolved (status={item.status!r})",
                )
            prev_status = item.status
            item.status = new_status
            item.resolved_at = datetime.now(UTC)
            item.resolved_by = actor
            item.resolution_note = note
            if edited_draft is not None:
                item.edited_draft_json = edited_draft
            s.add(HITLAuditEntry(
                item_id=item.id,
                actor=actor,
                from_status=prev_status,
                to_status=new_status,
                note=note,
            ))
            s.flush()
            s.refresh(item)
            s.expunge(item)
            return item

    def approve(self, item_id: str, *, by: str = "demo_user", note: str = "") -> HITLItem:
        return self._resolve(item_id, new_status=STATUS_APPROVED, actor=by, note=note)

    def reject(self, item_id: str, *, by: str = "demo_user", note: str = "") -> HITLItem:
        return self._resolve(item_id, new_status=STATUS_REJECTED, actor=by, note=note)

    def approve_with_edit(
        self,
        item_id: str,
        *,
        edited_draft: dict[str, Any],
        by: str = "demo_user",
        note: str = "",
    ) -> HITLItem:
        return self._resolve(
            item_id,
            new_status=STATUS_EDITED_APPROVED,
            actor=by,
            note=note,
            edited_draft=edited_draft,
        )

    def mark_executed(
        self,
        item_id: str,
        result: ExecutionResult,
        *,
        actor: str = "executor",
    ) -> HITLItem:
        """Persist the executor's result on the queue item + write an audit entry."""
        with self._session() as s:
            item = s.get(HITLItem, item_id)
            if item is None:
                raise HITLQueueError(f"Item {item_id!r} not found")
            item.execution_status = result.status.value
            item.execution_result_json = result.model_dump(mode="json")
            item.executed_at = result.executed_at
            s.add(HITLAuditEntry(
                item_id=item.id,
                actor=actor,
                from_status=item.status,
                to_status=item.status,
                note=f"executed: {result.status.value} ({len(result.steps)} step(s))",
            ))
            s.flush()
            s.refresh(item)
            s.expunge(item)
            return item

    # ----- read paths -----------------------------------------------------

    def list(
        self,
        *,
        status: str | None = STATUS_PENDING,
        tier: int | None = None,
        routed_to: str | None = None,
        limit: int = 100,
    ) -> list[HITLItem]:
        """Return items matching the filter. `status=None` returns all statuses."""
        with self._session() as s:
            stmt = select(HITLItem)
            if status is not None:
                stmt = stmt.where(HITLItem.status == status)
            if tier is not None:
                stmt = stmt.where(HITLItem.tier == tier)
            if routed_to is not None:
                stmt = stmt.where(HITLItem.routed_to == routed_to)
            stmt = stmt.order_by(HITLItem.created_at.desc()).limit(limit)
            rows = list(s.scalars(stmt).all())
            for row in rows:
                s.expunge(row)
            return rows

    def get(self, item_id: str) -> HITLItem | None:
        with self._session() as s:
            item = s.get(HITLItem, item_id)
            if item is None:
                return None
            # Pre-load audit entries before expunging
            _ = list(item.audit_entries)
            for entry in item.audit_entries:
                s.expunge(entry)
            s.expunge(item)
            return item

    def stats(self) -> dict[str, Any]:
        with self._session() as s:
            total = s.scalar(select(func.count(HITLItem.id))) or 0
            by_status: dict[str, int] = {}
            for status_row in s.execute(
                select(HITLItem.status, func.count(HITLItem.id)).group_by(HITLItem.status),
            ).all():
                by_status[status_row[0]] = status_row[1]
            by_tier: dict[int, int] = {}
            for tier_row in s.execute(
                select(HITLItem.tier, func.count(HITLItem.id)).group_by(HITLItem.tier),
            ).all():
                by_tier[int(tier_row[0])] = tier_row[1]
            by_routed_to: dict[str, int] = {}
            for routed_row in s.execute(
                select(HITLItem.routed_to, func.count(HITLItem.id)).group_by(HITLItem.routed_to),
            ).all():
                by_routed_to[routed_row[0]] = routed_row[1]
            return {
                "total": total,
                "by_status": by_status,
                "by_tier": by_tier,
                "by_routed_to": by_routed_to,
            }

    def clear(self) -> None:
        """Wipe every row. Intended for tests and `make hitl-clear`."""
        with self._session() as s:
            s.query(HITLAuditEntry).delete()
            s.query(HITLItem).delete()
