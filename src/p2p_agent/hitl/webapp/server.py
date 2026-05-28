"""FastAPI demo app — HITL approval queue + end-to-end /demo upload flow.

HTML routes:
- GET  /                              → redirect to /demo
- GET  /demo                          → upload form + recent runs
- POST /demo/run                      → accept PDF, run pipeline, redirect to result
- GET  /demo/runs                     → list of past runs
- GET  /demo/run/{id}                 → full trace for one run
- GET  /demo/run/{id}/pdf             → re-download the uploaded PDF
- GET  /queue                         → pending HITL items table
- GET  /queue/all                     → all-status items table
- GET  /item/{id}                     → item detail + action buttons
- POST /item/{id}/approve             → mark approved
- POST /item/{id}/reject               → mark rejected
- POST /item/{id}/approve-with-edit   → store edited draft + mark edited_approved
- GET  /stats                         → counts dashboard

JSON API (same logic, parallel routes):
- GET  /api/queue
- GET  /api/item/{id}
- POST /api/item/{id}/approve
- POST /api/item/{id}/reject
- POST /api/item/{id}/approve-with-edit
- GET  /api/stats
- GET  /api/runs
- GET  /api/run/{id}
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from p2p_agent.context import CaseContextBuilder
from p2p_agent.executor import ActionExecutor
from p2p_agent.hitl import HITLQueue, HITLQueueError, PipelineRunStore
from p2p_agent.hitl.models import HITLItem, PipelineRun
from p2p_agent.hitl.webapp.samples import (
    SAMPLES,
    curated_samples,
    find_sample,
    load_sample_metadata,
    resolve_pdf,
)
from p2p_agent.llm.client import CostCeilingExceeded, ModelClient
from p2p_agent.orchestrator import run_invoice_pipeline
from p2p_agent.retrieval import PolicyRetriever, get_default_retriever
from p2p_agent.stage9 import WINDOWS, Stage9Aggregator, Stage9Reader

TEMPLATES_DIR = Path(__file__).parent / "templates"

# ─── Persistent storage paths ───────────────────────────────────────────────
# `DATA_DIR` is the single source of truth for all on-disk state we want to
# survive container restarts (SQLite DB, uploaded PDFs, LLM call log). On
# Railway, mount a persistent volume at `/data` and set `DATA_DIR=/data` —
# everything below then writes inside the volume. Locally it defaults to
# ./logs/ so dev behavior is unchanged.
#
# The individual env vars (HITL_DB_URL, UPLOADS_DIR, LLM_CALL_LOG_PATH) still
# override the derived defaults if you need to point pieces elsewhere.
DATA_DIR = Path(os.environ.get("DATA_DIR") or "./logs")

DEFAULT_DB_URL = os.environ.get("HITL_DB_URL") or f"sqlite:///{DATA_DIR}/hitl_queue.db"
DEFAULT_UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR") or str(DATA_DIR / "demo_uploads"))
DEFAULT_LLM_LOG_PATH = Path(os.environ.get("LLM_CALL_LOG_PATH") or str(DATA_DIR / "llm_calls.jsonl"))
# Repo root, used for serving documentation HTML files (status.html, etc.)
# Path: <repo>/src/p2p_agent/hitl/webapp/server.py → parents[4] is <repo>
_REPO_ROOT = Path(__file__).resolve().parents[4]
MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB


def _sse_event(payload: dict[str, Any]) -> str:
    """Format a Python dict as a single Server-Sent Event."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_filename": run.uploaded_filename,
        "uploaded_at": run.uploaded_at.isoformat() if run.uploaded_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "status": run.status,
        "error_message": run.error_message,
        "class_label": run.class_label,
        "recommended_action": run.recommended_action,
        "tier": run.tier,
        "routed_to": run.routed_to,
        "hitl_item_id": run.hitl_item_id,
        "cost_usd": run.cost_usd,
        "latency_ms": run.latency_ms,
        "result": run.result_json,
    }


def _serialize_item(item: HITLItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "tier": item.tier,
        "routed_to": item.routed_to,
        "routing_reason": item.routing_reason,
        "class_label": item.class_label,
        "classification_confidence": item.classification_confidence,
        "recommended_action": item.recommended_action,
        "confidence": item.confidence,
        "status": item.status,
        "payload": item.payload_json,
        "edited_draft": item.edited_draft_json,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolved_by": item.resolved_by,
        "resolution_note": item.resolution_note,
        "execution_status": item.execution_status,
        "execution_result": item.execution_result_json,
        "executed_at": item.executed_at.isoformat() if item.executed_at else None,
    }


def create_app(
    queue: HITLQueue | None = None,
    runs: PipelineRunStore | None = None,
    uploads_dir: Path | None = None,
    pipeline_runner: Any = None,
    llm_log_path: Path | None = None,
    executor: ActionExecutor | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Pass `queue` / `runs` for tests; otherwise the default DB (`logs/hitl_queue.db`)
    is used. `pipeline_runner` is an injection point for tests — defaults to the
    real `run_invoice_pipeline`. `llm_log_path` overrides the Stage 9 jsonl source.
    `executor` defaults to the mock `ActionExecutor`; pass a stub in tests.
    """
    # Persistent state goes under DATA_DIR (or its explicit overrides). The
    # parent directory must exist for SQLite to create the DB file on first run.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_url = DEFAULT_DB_URL
    queue = queue or HITLQueue(db_url=db_url)
    runs = runs or PipelineRunStore(db_url=db_url)
    uploads_dir = uploads_dir or DEFAULT_UPLOADS_DIR
    uploads_dir.mkdir(parents=True, exist_ok=True)
    pipeline_runner = pipeline_runner or run_invoice_pipeline
    stage9_reader = Stage9Reader(llm_log_path or DEFAULT_LLM_LOG_PATH)
    stage9_agg = Stage9Aggregator(queue=queue, runs=runs)
    executor = executor or ActionExecutor(mode="mock")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["pretty_json"] = lambda v: json.dumps(v, indent=2, default=str)

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Pay the ~10s bge-large-en + policy library embedding cost ONCE at
        server startup so the first user-driven invoice doesn't eat it.

        Skipped if `HITL_SKIP_WARMUP=1` is set (useful in tests).
        """
        if os.environ.get("HITL_SKIP_WARMUP") != "1":
            t0 = time.monotonic()
            try:
                retriever = _retriever()
                # Triggers the embedder load + policy library embedding pass.
                retriever.retrieve("warm-up", k=1)
                took = time.monotonic() - t0
                print(f"[startup] policy retriever warm ({took:.1f}s, {retriever.policy_count} policies)")
            except Exception as e:  # noqa: BLE001 — best effort: don't block boot
                print(f"[startup] retriever warm-up failed: {e}")
        yield

    # Disable FastAPI's built-in Swagger UI + ReDoc routes so our own /docs
    # (documentation landing page) takes precedence. The OpenAPI schema itself
    # is still available at /openapi.json if anyone wants it.
    app = FastAPI(
        title="P2P Agent — HITL Demo",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )

    # ------------------------------ Docs routing ------------------------
    # Serve the three public HTML docs and the landing page at clean URLs.
    # The HTML files live in the repo root (not under /templates), so we
    # use direct FileResponse instead of the Jinja templates layer.
    _DOC_FILES = {
        "status":            _REPO_ROOT / "status.html",
        "agent_overview":    _REPO_ROOT / "agent_overview.html",
        "detailed_workflow": _REPO_ROOT / "detailed_workflow.html",
    }
    _DOCS_INDEX = _REPO_ROOT / "docs_index.html"

    @app.get("/docs", include_in_schema=False)
    def docs_landing():  # type: ignore[no-untyped-def]
        if _DOCS_INDEX.exists():
            return FileResponse(_DOCS_INDEX, media_type="text/html")
        # Fallback if the landing page hasn't been built yet — minimal nav stub
        return HTMLResponse(
            "<h1>Docs</h1><ul>"
            "<li><a href='/docs/status'>Engineering status</a></li>"
            "<li><a href='/docs/agent_overview'>Agent overview (non-technical)</a></li>"
            "<li><a href='/docs/detailed_workflow'>Detailed workflow (technical)</a></li>"
            "</ul>"
        )

    @app.get("/docs/{doc_name}", include_in_schema=False)
    def docs_page(doc_name: str):  # type: ignore[no-untyped-def]
        path = _DOC_FILES.get(doc_name)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"Doc not found: {doc_name}")
        return FileResponse(path, media_type="text/html")

    # ------------------------------ Health check ------------------------
    # Lightweight liveness probe for Railway + the portfolio's Vercel
    # rewrite sanity check. Returns 200 once the app is up; reports
    # retriever-warm status so monitoring can distinguish "app up but
    # still loading the embedding model" from "fully ready."
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        retriever_warm = "retriever" in _state
        return JSONResponse(
            {
                "status": "ok",
                "retriever_warm": retriever_warm,
                "data_dir": str(DATA_DIR),
                "uploads_dir": str(DEFAULT_UPLOADS_DIR),
                "db_url_kind": "sqlite" if DEFAULT_DB_URL.startswith("sqlite") else "other",
            },
        )

    # Lazy singletons for retriever + context builder — the retriever loads
    # the embedding model on first init, ~1.5s. Don't pay it per request.
    _state: dict[str, Any] = {}

    def _retriever() -> PolicyRetriever:
        # Reuse the process-wide singleton so the embedding model is loaded
        # exactly once per process (not once per app instance).
        if "retriever" not in _state:
            _state["retriever"] = get_default_retriever()
        return _state["retriever"]

    def _ctx_builder() -> CaseContextBuilder:
        if "ctx_builder" not in _state:
            _state["ctx_builder"] = CaseContextBuilder()
        return _state["ctx_builder"]

    def _client() -> ModelClient:
        if "client" not in _state:
            _state["client"] = ModelClient()
        return _state["client"]

    # ------------------------------ HTML --------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/demo", status_code=302)

    # ---- /demo upload + run flow ----

    @app.get("/demo", response_class=HTMLResponse)
    def demo_home(
        request: Request,
        selected: str | None = Query(default=None),
    ) -> HTMLResponse:
        recent = runs.list(limit=10)
        # Build gallery rows: each row = static SamplePdf + per-invoice metadata
        # (vendor, total, currency, line count) lifted from the JSON sidecar.
        gallery = [
            {"sample": s, "meta": load_sample_metadata(s)} for s in SAMPLES
        ]
        selected_row = None
        if selected:
            for row in gallery:
                if row["sample"].sample_id == selected:
                    selected_row = row
                    break
        return templates.TemplateResponse(
            request,
            "demo_upload.html",
            {
                "recent_runs": recent,
                "queue_stats": queue.stats(),
                "samples": SAMPLES,
                "curated": curated_samples(),
                "gallery": gallery,
                "selected_row": selected_row,
                "cost_estimate": stage9_reader.per_run_cost(last_n_runs=20),
            },
        )

    @app.get("/demo/browse", response_class=HTMLResponse)
    def demo_browse(request: Request) -> HTMLResponse:
        """Browse the curated invoice library. Click to preview, click again
        to select — selection redirects back to /demo?selected=<sample_id>
        where the user can launch the pipeline."""
        gallery = [
            {"sample": s, "meta": load_sample_metadata(s)} for s in SAMPLES
        ]
        return templates.TemplateResponse(
            request,
            "demo_browse.html",
            {"gallery": gallery},
        )

    @app.get("/demo/sample/{sample_id}/pdf", include_in_schema=False)
    def demo_sample_pdf(sample_id: str) -> FileResponse:
        """Serve a curated sample PDF inline for iframe preview on /demo/browse."""
        sample = find_sample(sample_id)
        if sample is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown sample_id: {sample_id!r}",
            )
        try:
            path = resolve_pdf(sample)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return FileResponse(path, media_type="application/pdf")

    # Per-run asyncio.Lock so concurrent SSE openers don't double-run the same
    # pipeline. Keyed by run_id. Pruned only on process restart — fine for the
    # demo footprint.
    _run_locks: dict[str, asyncio.Lock] = {}

    def _get_run_lock(run_id: str) -> asyncio.Lock:
        if run_id not in _run_locks:
            _run_locks[run_id] = asyncio.Lock()
        return _run_locks[run_id]

    async def _run_pipeline_on_pdf(
        run_id: str,
        pdf_path: Path,
        on_event: Any = None,
    ) -> None:
        """Shared pipeline-runner: marks the run completed or failed.

        `on_event` is forwarded to the pipeline for SSE streaming. None means
        no events emitted (legacy /demo/run path).
        """
        t0 = time.monotonic()
        try:
            result = await pipeline_runner(
                pdf_path=pdf_path,
                client=_client(),
                retriever=_retriever(),
                context_builder=_ctx_builder(),
                queue=queue,
                case_id=run_id,
                on_event=on_event,
            )
        except CostCeilingExceeded as e:
            # Friendly message for the hosted-demo daily budget cap. The
            # /demo/run/{run_id} page renders this in a callout so visitors
            # know to come back tomorrow rather than think the agent crashed.
            runs.fail(
                run_id,
                error_message=(
                    "Daily demo budget reached — try again tomorrow, or browse "
                    f"past runs at /demo/runs. ({e})"
                ),
            )
            return
        except Exception as e:  # noqa: BLE001 — capture for the run row
            runs.fail(run_id, error_message=f"{type(e).__name__}: {e}")
            return

        latency_ms = int((time.monotonic() - t0) * 1000)
        rec_action = result.recommendation.action.value if result.recommendation else None
        rd = result.routing_decision
        runs.complete(
            run_id,
            class_label=result.classification.class_label.value,
            recommended_action=rec_action,
            tier=int(rd.tier) if rd else None,
            routed_to=rd.routed_to if rd else None,
            hitl_item_id=result.hitl_item_id,
            cost_usd=0.0,
            latency_ms=latency_ms,
            result_json=result.model_dump(mode="json"),
        )

    @app.post("/demo/run")
    async def demo_run(file: UploadFile = File(...)) -> RedirectResponse:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Upload must be a .pdf file")

        # Reserve a run id first so we can name the file deterministically
        # without reading it twice.
        run = runs.create(uploaded_filename=file.filename, stored_pdf_path="")
        stored = uploads_dir / f"{run.id}.pdf"

        bytes_written = 0
        try:
            with stored.open("wb") as out:
                while chunk := await file.read(64 * 1024):
                    bytes_written += len(chunk)
                    if bytes_written > MAX_PDF_BYTES:
                        out.close()
                        stored.unlink(missing_ok=True)
                        runs.fail(run.id, error_message="PDF over 10 MB cap.")
                        raise HTTPException(
                            status_code=413, detail="PDF over 10 MB cap.",
                        )
                    out.write(chunk)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — best effort: surface to user
            stored.unlink(missing_ok=True)
            runs.fail(run.id, error_message=f"upload failed: {e}")
            raise HTTPException(status_code=500, detail="Upload failed.") from e

        runs.set_stored_path(run.id, str(stored))
        await _run_pipeline_on_pdf(run.id, stored)
        return RedirectResponse(url=f"/demo/run/{run.id}", status_code=303)

    @app.post("/demo/run-sample")
    async def demo_run_sample(sample_id: str = Form(...)) -> RedirectResponse:
        sample = find_sample(sample_id)
        if sample is None:
            raise HTTPException(status_code=400, detail=f"Unknown sample_id: {sample_id!r}")
        try:
            corpus_pdf = resolve_pdf(sample)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        run = runs.create(
            uploaded_filename=f"sample-{sample.sample_id}-{sample.case_id}.pdf",
            stored_pdf_path="",
        )
        stored = uploads_dir / f"{run.id}.pdf"
        # Copy the corpus PDF into the uploads dir so /demo/run/{id}/pdf works the
        # same way as upload-driven runs. Small file (corpus PDFs are well under MAX).
        stored.write_bytes(corpus_pdf.read_bytes())
        runs.set_stored_path(run.id, str(stored))

        await _run_pipeline_on_pdf(run.id, stored)
        return RedirectResponse(url=f"/demo/run/{run.id}", status_code=303)

    # -------------------------------------------------------------------
    # Streaming flow — POST saves the file + creates the run row, but does
    # NOT run the pipeline. Instead it redirects to a "watch" page that
    # opens an EventSource on the SSE endpoint below, which is where the
    # pipeline actually runs. Saves the user from staring at a blank screen
    # for 1-3 minutes.
    # -------------------------------------------------------------------

    @app.post("/demo/run-streaming")
    async def demo_run_streaming(file: UploadFile = File(...)) -> RedirectResponse:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Upload must be a .pdf file")

        run = runs.create(uploaded_filename=file.filename, stored_pdf_path="")
        stored = uploads_dir / f"{run.id}.pdf"

        bytes_written = 0
        try:
            with stored.open("wb") as out:
                while chunk := await file.read(64 * 1024):
                    bytes_written += len(chunk)
                    if bytes_written > MAX_PDF_BYTES:
                        out.close()
                        stored.unlink(missing_ok=True)
                        runs.fail(run.id, error_message="PDF over 10 MB cap.")
                        raise HTTPException(
                            status_code=413, detail="PDF over 10 MB cap.",
                        )
                    out.write(chunk)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            stored.unlink(missing_ok=True)
            runs.fail(run.id, error_message=f"upload failed: {e}")
            raise HTTPException(status_code=500, detail="Upload failed.") from e

        runs.set_stored_path(run.id, str(stored))
        # Don't run the pipeline here — the SSE endpoint will, so the user
        # sees streamed progress instead of waiting on a blocking POST.
        return RedirectResponse(url=f"/demo/run-stream-view/{run.id}", status_code=303)

    @app.post("/demo/run-sample-streaming")
    async def demo_run_sample_streaming(sample_id: str = Form(...)) -> RedirectResponse:
        sample = find_sample(sample_id)
        if sample is None:
            raise HTTPException(status_code=400, detail=f"Unknown sample_id: {sample_id!r}")
        try:
            corpus_pdf = resolve_pdf(sample)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        run = runs.create(
            uploaded_filename=f"sample-{sample.sample_id}-{sample.case_id}.pdf",
            stored_pdf_path="",
        )
        stored = uploads_dir / f"{run.id}.pdf"
        stored.write_bytes(corpus_pdf.read_bytes())
        runs.set_stored_path(run.id, str(stored))

        return RedirectResponse(url=f"/demo/run-stream-view/{run.id}", status_code=303)

    @app.get("/demo/run-stream-view/{run_id}", response_class=HTMLResponse)
    def demo_run_stream_view(request: Request, run_id: str) -> HTMLResponse:
        """The page that holds the live timeline. Renders 8 pending rows and
        opens an EventSource on /demo/run/{run_id}/stream."""
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return templates.TemplateResponse(
            request,
            "demo_run_stream.html",
            {
                "run": run,
                "stream_url": f"/demo/run/{run_id}/stream",
                "detail_url": f"/demo/run/{run_id}",
            },
        )

    @app.get("/demo/run/{run_id}/stream")
    async def demo_run_stream(run_id: str) -> StreamingResponse:
        """Server-Sent Events stream. Runs the pipeline (if not already done)
        and yields events as each node completes."""
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if not run.stored_pdf_path:
            raise HTTPException(status_code=400, detail="run has no PDF on disk")

        pdf_path = Path(run.stored_pdf_path)

        async def event_generator():
            # Early-exit: if the pipeline already finished (refresh, second tab),
            # emit a single run.done with the redirect target and close.
            current = runs.get(run_id)
            if current and current.status == "completed":
                yield _sse_event({
                    "type": "run.done",
                    "case_id": run_id,
                    "total_latency_ms": current.latency_ms or 0,
                    "hitl_item_id": current.hitl_item_id,
                    "redirect_url": f"/demo/run/{run_id}",
                    "already_done": True,
                })
                return
            if current and current.status == "failed":
                yield _sse_event({
                    "type": "run.error",
                    "case_id": run_id,
                    "error": current.error_message or "unknown error",
                })
                return

            # Producer/consumer: pipeline (producer) writes events into the
            # queue; this generator (consumer) yields them to the SSE channel.
            event_queue: asyncio.Queue = asyncio.Queue()
            DONE = object()

            async def publish(event: dict) -> None:
                await event_queue.put(event)

            async def run_and_finalize() -> None:
                lock = _get_run_lock(run_id)
                async with lock:
                    # Re-check inside the lock: another SSE opener may have
                    # already finished the run between our pre-check and now.
                    inner = runs.get(run_id)
                    if inner and inner.status == "completed":
                        await event_queue.put({
                            "type": "run.done",
                            "case_id": run_id,
                            "total_latency_ms": inner.latency_ms or 0,
                            "hitl_item_id": inner.hitl_item_id,
                            "redirect_url": f"/demo/run/{run_id}",
                            "already_done": True,
                        })
                        return
                    try:
                        await _run_pipeline_on_pdf(run_id, pdf_path, on_event=publish)
                        # Pipeline already emitted its own run.done; we tack on
                        # the redirect_url so the client knows where to navigate.
                        finalized = runs.get(run_id)
                        await event_queue.put({
                            "type": "run.finalized",
                            "case_id": run_id,
                            "status": finalized.status if finalized else "unknown",
                            "redirect_url": f"/demo/run/{run_id}",
                            "error": finalized.error_message if finalized else None,
                        })
                    except Exception as e:  # noqa: BLE001
                        await event_queue.put({
                            "type": "run.error",
                            "case_id": run_id,
                            "error": f"{type(e).__name__}: {e}",
                        })

            task = asyncio.create_task(run_and_finalize())

            # Keep the SSE connection alive with periodic comments while we
            # wait for events. SSE comments start with ":" and are ignored
            # by EventSource — they keep proxies / browsers from timing out.
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is DONE:
                        break
                    yield _sse_event(event)
                    if event.get("type") in ("run.finalized", "run.error"):
                        break
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disables nginx buffering if any
            },
        )

    @app.get("/demo/runs", response_class=HTMLResponse)
    def demo_runs_list(request: Request) -> HTMLResponse:
        rows = runs.list(limit=200)
        return templates.TemplateResponse(
            request, "demo_runs.html", {"runs": rows},
        )

    @app.get("/demo/run/{run_id}", response_class=HTMLResponse)
    def demo_run_detail(request: Request, run_id: str) -> HTMLResponse:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        result = run.result_json or {}
        case_context = result.get("case_context")

        # Reconstruct a typed CaseContext from the persisted dict so we can
        # call .summary_signals() — that method captures the "why" of the
        # classifier's decision in a form the template can render as chips.
        signals: list[str] = []
        if case_context:
            try:
                from p2p_agent.models.context import CaseContext as _CC
                signals = _CC.model_validate(case_context).summary_signals()
            except Exception:  # noqa: BLE001 — old runs may have partial data
                signals = []

        return templates.TemplateResponse(
            request,
            "demo_run_detail.html",
            {
                "run": run,
                "extraction": result.get("extraction") or {},
                "case_context": case_context,
                "signals": signals,
                "classification": result.get("classification") or {},
                "retrieved_policies": result.get("retrieved_policies") or [],
                "recommendation": result.get("recommendation"),
                "routing_decision": result.get("routing_decision"),
                "draft": result.get("draft"),
                "steps": result.get("steps") or [],
            },
        )

    @app.get("/demo/run/{run_id}/pdf")
    def demo_run_pdf(run_id: str) -> FileResponse:
        run = runs.get(run_id)
        if run is None or not run.stored_pdf_path:
            raise HTTPException(status_code=404, detail="PDF not found")
        path = Path(run.stored_pdf_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="PDF file missing on disk")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=run.uploaded_filename,
        )

    @app.get("/api/runs")
    def api_runs() -> JSONResponse:
        rows = runs.list(limit=200)
        return JSONResponse([_serialize_run(r) for r in rows])

    @app.get("/api/run/{run_id}")
    def api_run(run_id: str) -> JSONResponse:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return JSONResponse(_serialize_run(run))

    # ---- /stage9 measurement dashboard ----

    def _window(value: str | None) -> str:
        w = value or "7d"
        return w if w in WINDOWS else "7d"

    @app.get("/stage9", response_class=HTMLResponse)
    def stage9_view(
        request: Request,
        window: str = Query(default="7d"),
    ) -> HTMLResponse:
        w = _window(window)
        cost = stage9_reader.cost_summary(window=w)
        latency = stage9_reader.latency_summary(window=w)
        ops = stage9_agg.ops_summary(window=w)
        tail = stage9_reader.tail(n=20, window=w)
        return templates.TemplateResponse(
            request,
            "stage9.html",
            {
                "window": w,
                "available_windows": list(WINDOWS.keys()),
                "cost": cost,
                "latency": latency,
                "ops": ops,
                "tail": tail,
            },
        )

    @app.get("/api/stage9/cost")
    def api_stage9_cost(window: str = Query(default="7d")) -> JSONResponse:
        return JSONResponse(stage9_reader.cost_summary(window=_window(window)))

    @app.get("/api/stage9/latency")
    def api_stage9_latency(window: str = Query(default="7d")) -> JSONResponse:
        return JSONResponse(stage9_reader.latency_summary(window=_window(window)))

    @app.get("/api/stage9/ops")
    def api_stage9_ops(window: str = Query(default="7d")) -> JSONResponse:
        return JSONResponse(stage9_agg.ops_summary(window=_window(window)))

    @app.get("/api/stage9/tail")
    def api_stage9_tail(
        n: int = Query(default=50, ge=1, le=500),
        window: str = Query(default="all"),
    ) -> JSONResponse:
        return JSONResponse(stage9_reader.tail(n=n, window=_window(window)))

    @app.get("/queue", response_class=HTMLResponse)
    def queue_view(
        request: Request,
        tier: int | None = Query(default=None),
        routed_to: str | None = Query(default=None),
    ) -> HTMLResponse:
        items = queue.list(status="pending", tier=tier, routed_to=routed_to, limit=200)
        return templates.TemplateResponse(
            request,
            "queue_list.html",
            {
                "items": items,
                "title": "Pending",
                "show_all": False,
                "filter_tier": tier,
                "filter_routed_to": routed_to,
                "stats": queue.stats(),
            },
        )

    @app.get("/queue/all", response_class=HTMLResponse)
    def queue_all_view(
        request: Request,
        tier: int | None = Query(default=None),
        routed_to: str | None = Query(default=None),
    ) -> HTMLResponse:
        items = queue.list(status=None, tier=tier, routed_to=routed_to, limit=500)
        return templates.TemplateResponse(
            request,
            "queue_list.html",
            {
                "items": items,
                "title": "All items",
                "show_all": True,
                "filter_tier": tier,
                "filter_routed_to": routed_to,
                "stats": queue.stats(),
            },
        )

    @app.get("/item/{item_id}", response_class=HTMLResponse)
    def item_view(request: Request, item_id: str) -> HTMLResponse:
        item = queue.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        payload = item.payload_json or {}
        return templates.TemplateResponse(
            request,
            "item_detail.html",
            {
                "item": item,
                "classification": payload.get("classification") or {},
                "recommendation": payload.get("recommendation") or {},
                "routing_decision": payload.get("routing_decision") or {},
                "draft": payload.get("draft"),
                "edited_draft": item.edited_draft_json,
                "audit_entries": item.audit_entries,
                "execution_result": item.execution_result_json,
                "execution_status": item.execution_status,
                "executed_at": item.executed_at,
            },
        )

    def _execute_after_approve(item: HITLItem) -> None:
        """Fire the action executor + persist its result. Errors are swallowed for v1."""
        try:
            result = executor.execute(item)
            queue.mark_executed(item.id, result)
        except Exception:  # noqa: BLE001 — mock can't really fail; real backend will retry
            pass

    @app.post("/item/{item_id}/approve")
    def approve_html(
        item_id: str,
        note: str = Form(default=""),
        by: str = Form(default="demo_user"),
    ) -> RedirectResponse:
        try:
            item = queue.approve(item_id, by=by, note=note)
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _execute_after_approve(item)
        return RedirectResponse(url="/queue", status_code=303)

    @app.post("/item/{item_id}/reject")
    def reject_html(
        item_id: str,
        note: str = Form(default=""),
        by: str = Form(default="demo_user"),
    ) -> RedirectResponse:
        try:
            queue.reject(item_id, by=by, note=note)
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return RedirectResponse(url="/queue", status_code=303)

    @app.post("/item/{item_id}/approve-with-edit")
    def approve_with_edit_html(
        item_id: str,
        edited_subject: str = Form(default=""),
        edited_body: str = Form(default=""),
        edited_recipient: str = Form(default=""),
        note: str = Form(default=""),
        by: str = Form(default="demo_user"),
    ) -> RedirectResponse:
        edited = {
            "subject": edited_subject,
            "body": edited_body,
            "recipient": edited_recipient,
        }
        try:
            item = queue.approve_with_edit(item_id, edited_draft=edited, by=by, note=note)
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _execute_after_approve(item)
        return RedirectResponse(url="/queue", status_code=303)

    @app.get("/stats", response_class=HTMLResponse)
    def stats_view(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "stats.html", {"stats": queue.stats()},
        )

    # ------------------------------ JSON --------------------------------

    @app.get("/api/queue")
    def api_queue(
        status: str | None = Query(default="pending"),
        tier: int | None = Query(default=None),
        routed_to: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        items = queue.list(status=status, tier=tier, routed_to=routed_to, limit=limit)
        return JSONResponse([_serialize_item(it) for it in items])

    @app.get("/api/item/{item_id}")
    def api_item(item_id: str) -> JSONResponse:
        item = queue.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return JSONResponse(_serialize_item(item))

    @app.post("/api/item/{item_id}/approve")
    def api_approve(item_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        body = body or {}
        try:
            item = queue.approve(
                item_id, by=body.get("by", "demo_user"), note=body.get("note", ""),
            )
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _execute_after_approve(item)
        # Re-fetch so the JSON response carries the execution_* fields
        item = queue.get(item_id) or item
        return JSONResponse(_serialize_item(item))

    @app.post("/api/item/{item_id}/reject")
    def api_reject(item_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        body = body or {}
        try:
            item = queue.reject(
                item_id, by=body.get("by", "demo_user"), note=body.get("note", ""),
            )
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return JSONResponse(_serialize_item(item))

    @app.post("/api/item/{item_id}/approve-with-edit")
    def api_approve_with_edit(item_id: str, body: dict[str, Any]) -> JSONResponse:
        edited = body.get("edited_draft")
        if not isinstance(edited, dict):
            raise HTTPException(status_code=400, detail="edited_draft (dict) required")
        try:
            item = queue.approve_with_edit(
                item_id,
                edited_draft=edited,
                by=body.get("by", "demo_user"),
                note=body.get("note", ""),
            )
            _execute_after_approve(item)
            item = queue.get(item_id) or item
        except HITLQueueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return JSONResponse(_serialize_item(item))

    @app.get("/api/stats")
    def api_stats() -> JSONResponse:
        return JSONResponse(queue.stats())

    return app


# Module-level app for `uvicorn p2p_agent.hitl.webapp.server:app`.
app = create_app()
