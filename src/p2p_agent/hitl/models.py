"""SQLAlchemy ORM for the HITL approval queue.

Two tables:
- `HITLItem` — one row per case that lands at tier ≥ 2. Carries a denormalized
  snapshot of the classification + recommendation + routing for fast list views,
  plus the full payload as JSON for the detail page.
- `HITLAuditEntry` — append-only audit log of every status transition.

SQLite-backed by default (`logs/hitl_queue.db`). Same models work against
Postgres — change only the `db_url` passed to `HITLQueue`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# Status values — kept as plain strings for SQLite compatibility / easier JSON serialization.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EDITED_APPROVED = "edited_approved"

TERMINAL_STATUSES = frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_EDITED_APPROVED})


class HITLItem(Base):
    __tablename__ = "hitl_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )

    # Routing snapshot (denormalized for fast filtering)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    routed_to: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_reason: Mapped[str] = mapped_column(String(512), default="")

    # Classification snapshot
    class_label: Mapped[str] = mapped_column(String(64), nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Recommendation snapshot
    recommended_action: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Status
    status: Mapped[str] = mapped_column(String(32), default=STATUS_PENDING, nullable=False)

    # Full payload (Classification + Recommendation + RoutingDecision + Draft) as JSON
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # If the reviewer edited the draft before approving
    edited_draft_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Action execution (mock today; real connector backend later)
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    audit_entries: Mapped[list[HITLAuditEntry]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="HITLAuditEntry.at",
    )

    __table_args__ = (
        Index("ix_hitl_items_status", "status"),
        Index("ix_hitl_items_tier", "tier"),
        Index("ix_hitl_items_routed_to", "routed_to"),
        Index("ix_hitl_items_created_at", "created_at"),
    )


class HITLAuditEntry(Base):
    __tablename__ = "hitl_audit_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("hitl_items.id", ondelete="CASCADE"), nullable=False,
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(1024), default="")

    item: Mapped[HITLItem] = relationship(back_populates="audit_entries")

    __table_args__ = (
        Index("ix_hitl_audit_item_id", "item_id"),
    )


# Pipeline-run statuses (separate namespace from HITL item statuses).
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"


class PipelineRun(Base):
    """One row per invoice uploaded via the /demo flow.

    Stores the full `PipelineResult` as JSON for re-render, plus denormalized
    columns for the run list view. Linked to a `HITLItem` if the case was
    enqueued (tier ≥ 2).
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    uploaded_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    stored_pdf_path: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    status: Mapped[str] = mapped_column(String(16), default=RUN_RUNNING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Denormalized snapshot — null until the pipeline completes
    class_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    routed_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hitl_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Full PipelineResult.model_dump(mode='json') — drives the detail page
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_pipeline_runs_status", "status"),
        Index("ix_pipeline_runs_uploaded_at", "uploaded_at"),
    )
