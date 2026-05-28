"""Pipeline-run store — persists every /demo invoice upload + its full trace.

Shares the same SQLite DB (and SQLAlchemy engine) as the HITL queue. Lets the
demo UI offer a "history of past runs" list and re-open any past trace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from p2p_agent.hitl.models import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_RUNNING,
    Base,
    PipelineRun,
)
from p2p_agent.hitl.queue import DEFAULT_DB_URL, _enable_sqlite_wal


class PipelineRunStore:
    """Thin SQLAlchemy-backed store for /demo pipeline runs."""

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
        self._SessionMaker = sessionmaker(bind=self.engine, expire_on_commit=False)

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

    def create(self, *, uploaded_filename: str, stored_pdf_path: str) -> PipelineRun:
        """Reserve a run row before kicking off the pipeline."""
        with self._session() as s:
            run = PipelineRun(
                uploaded_filename=uploaded_filename,
                stored_pdf_path=stored_pdf_path,
                status=RUN_RUNNING,
            )
            s.add(run)
            s.flush()
            s.refresh(run)
            s.expunge(run)
            return run

    def complete(
        self,
        run_id: str,
        *,
        class_label: str | None,
        recommended_action: str | None,
        tier: int | None,
        routed_to: str | None,
        hitl_item_id: str | None,
        cost_usd: float,
        latency_ms: int,
        result_json: dict[str, Any],
    ) -> PipelineRun:
        """Mark a run completed and persist the full result snapshot."""
        with self._session() as s:
            run = s.get(PipelineRun, run_id)
            if run is None:
                raise KeyError(f"PipelineRun {run_id!r} not found")
            run.status = RUN_COMPLETED
            run.completed_at = datetime.now(UTC)
            run.class_label = class_label
            run.recommended_action = recommended_action
            run.tier = tier
            run.routed_to = routed_to
            run.hitl_item_id = hitl_item_id
            run.cost_usd = cost_usd
            run.latency_ms = latency_ms
            run.result_json = result_json
            s.flush()
            s.refresh(run)
            s.expunge(run)
            return run

    def set_stored_path(self, run_id: str, stored_pdf_path: str) -> None:
        """Update the on-disk path after the upload has been saved."""
        with self._session() as s:
            run = s.get(PipelineRun, run_id)
            if run is None:
                raise KeyError(f"PipelineRun {run_id!r} not found")
            run.stored_pdf_path = stored_pdf_path

    def fail(self, run_id: str, *, error_message: str) -> PipelineRun:
        with self._session() as s:
            run = s.get(PipelineRun, run_id)
            if run is None:
                raise KeyError(f"PipelineRun {run_id!r} not found")
            run.status = RUN_FAILED
            run.completed_at = datetime.now(UTC)
            run.error_message = error_message[:2000]
            s.flush()
            s.refresh(run)
            s.expunge(run)
            return run

    def get(self, run_id: str) -> PipelineRun | None:
        with self._session() as s:
            run = s.get(PipelineRun, run_id)
            if run is None:
                return None
            s.expunge(run)
            return run

    def list(self, *, limit: int = 100) -> list[PipelineRun]:
        with self._session() as s:
            stmt = select(PipelineRun).order_by(PipelineRun.uploaded_at.desc()).limit(limit)
            rows = list(s.scalars(stmt).all())
            for row in rows:
                s.expunge(row)
            return rows

    def clear(self) -> None:
        """Wipe every run row. Used by tests / `make demo-clear`."""
        with self._session() as s:
            s.query(PipelineRun).delete()
