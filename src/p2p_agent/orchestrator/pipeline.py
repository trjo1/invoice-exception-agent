"""End-to-end invoice pipeline.

Plain async function for now. LangGraph will graphify this same flow later;
nodes are designed to be composable plain async functions so the eventual
graph wrapping is mechanical.

Phase 3 wiring: extract → classify → retrieve → decide.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from p2p_agent.classifiers import classify_exception
from p2p_agent.context import CaseContextBuilder
from p2p_agent.decision import recommend_action
from p2p_agent.drafter import action_needs_draft, draft_communication
from p2p_agent.extractors import extract_invoice
from p2p_agent.hitl import HITLQueue, HITLRouter
from p2p_agent.hitl.models import HITLItem
from p2p_agent.llm.client import ModelClient
from p2p_agent.models.pipeline import PipelineResult, StepTrace
from p2p_agent.retrieval import PolicyRetriever, get_default_retriever

# Type alias for the SSE / event-stream callback. The orchestrator calls this
# at the start/end of each node so the demo UI (and any other observer) can
# stream progress. None is a valid value — the pipeline becomes a no-op
# event-wise in that case.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _emit(on_event: EventCallback | None, payload: dict[str, Any]) -> None:
    """Call the event callback if one is wired. Cheap no-op when None."""
    if on_event is not None:
        try:
            await on_event(payload)
        except Exception:  # noqa: BLE001 — observers must never break the pipeline
            pass


def _build_retrieval_query(extraction_payload: dict, classification) -> str:
    """Build a retrieval query from the classification + key invoice signals."""
    parts: list[str] = [
        f"Exception category: {classification.class_label.value}",
        f"Rationale: {classification.rationale}",
    ]
    currency = extraction_payload.get("currency")
    if currency:
        parts.append(f"Currency: {currency}")
    po_ref = extraction_payload.get("po_reference")
    if po_ref:
        parts.append(f"PO reference present: yes")
    else:
        parts.append("PO reference missing")
    if classification.evidence:
        parts.append("Evidence: " + ", ".join(classification.evidence))
    return "\n".join(parts)


async def run_invoice_pipeline(
    pdf_path: Path,
    *,
    po_context: dict | None = None,
    gr_context: dict | None = None,
    client: ModelClient | None = None,
    retriever: PolicyRetriever | None = None,
    context_builder: CaseContextBuilder | None = None,
    queue: HITLQueue | None = None,
    case_id: str | None = None,
    include_decision: bool = True,
    include_case_context: bool = True,
    top_k_policies: int = 5,
    on_event: EventCallback | None = None,
) -> PipelineResult:
    """Run the pipeline on one invoice PDF end-to-end.

    Steps: extract → (build case context) → classify → retrieve → decide → route → (draft).

    `include_decision=False` runs just extract + (optional case context) + classify.
    `include_case_context=False` skips the cross-case context lookup (legacy behavior).

    When `queue` is provided AND the routing decision lands at tier ≥ 2, the
    case is auto-enqueued for HITL review. Eval scripts pass `queue=None` to
    keep the demo queue clean.

    Pass `on_event` to receive `step.start` / `step.end` / `step.skipped` /
    `run.done` events as the pipeline progresses. Used by the SSE streaming
    endpoint to render the live trace; None disables emission with no overhead.
    """
    client = client or ModelClient()
    case_id = case_id or pdf_path.stem
    steps: list[StepTrace] = []
    t_pipeline_start = time.monotonic()

    await _emit(on_event, {
        "type": "run.start",
        "case_id": case_id,
        "ts": time.time(),
    })

    # 1 — extract
    await _emit(on_event, {"type": "step.start", "name": "extract", "ts": time.time()})
    t0 = time.monotonic()
    extraction = await extract_invoice(
        pdf_path=pdf_path,
        client=client,
        case_id=f"{case_id}::extract",
    )
    extract_ms = int((time.monotonic() - t0) * 1000)
    steps.append(StepTrace(name="extract", latency_ms=extract_ms, cost_usd=0.0))
    hf = extraction.header_fields
    await _emit(on_event, {
        "type": "step.end",
        "name": "extract",
        "latency_ms": extract_ms,
        "summary": {
            "vendor_name": getattr(hf, "vendor_name", None) if hf else None,
            "invoice_id": getattr(extraction, "invoice_id", None),
            "total": getattr(extraction, "total", None),
            "currency": extraction.currency,
            "line_items": len(extraction.line_items) if extraction.line_items else 0,
        },
    })

    # 1b — build cross-case context (local; no LLM call)
    case_context = None
    if include_case_context:
        await _emit(on_event, {"type": "step.start", "name": "context", "ts": time.time()})
        t_ctx = time.monotonic()
        context_builder = context_builder or CaseContextBuilder()
        case_context = await context_builder.build_async(
            extraction.model_dump(),
            invoice_id=case_id,
        )
        ctx_ms = int((time.monotonic() - t_ctx) * 1000)
        steps.append(StepTrace(name="context", latency_ms=ctx_ms, cost_usd=0.0))
        signals = case_context.summary_signals() if case_context else []
        await _emit(on_event, {
            "type": "step.end",
            "name": "context",
            "latency_ms": ctx_ms,
            "summary": {
                "vendor_found": case_context.vendor_record is not None if case_context else False,
                "po_found": case_context.po_record is not None if case_context else False,
                "gr_found": case_context.goods_receipt is not None if case_context else False,
                "signals": signals[:3],  # cap to keep the SSE message small
            },
        })

    # 2 — classify
    await _emit(on_event, {"type": "step.start", "name": "classify", "ts": time.time()})
    t1 = time.monotonic()
    classification = await classify_exception(
        invoice=extraction.model_dump(),
        po_context=po_context,
        gr_context=gr_context,
        case_context=case_context,
        client=client,
        case_id=f"{case_id}::classify",
    )
    classify_ms = int((time.monotonic() - t1) * 1000)
    steps.append(StepTrace(name="classify", latency_ms=classify_ms, cost_usd=0.0))
    await _emit(on_event, {
        "type": "step.end",
        "name": "classify",
        "latency_ms": classify_ms,
        "summary": {
            "category": classification.class_label.value,
            "confidence": round(classification.confidence, 2),
        },
    })

    retrieved: list = []
    recommendation = None
    routing_decision = None
    draft = None
    hitl_item_id: str | None = None

    if include_decision:
        # 3 — retrieve (local; no LLM call)
        await _emit(on_event, {"type": "step.start", "name": "retrieve", "ts": time.time()})
        t2 = time.monotonic()
        retriever = retriever or get_default_retriever()
        query = _build_retrieval_query(extraction.model_dump(), classification)
        retrieved = retriever.retrieve(query, k=top_k_policies)
        retrieve_ms = int((time.monotonic() - t2) * 1000)
        steps.append(StepTrace(name="retrieve", latency_ms=retrieve_ms, cost_usd=0.0))
        await _emit(on_event, {
            "type": "step.end",
            "name": "retrieve",
            "latency_ms": retrieve_ms,
            "summary": {
                "top_policies": [
                    {"id": d.id, "title": d.title, "score": round(d.score, 3)}
                    for d in retrieved[:3]
                ],
            },
        })

        # 4 — decide
        await _emit(on_event, {"type": "step.start", "name": "decide", "ts": time.time()})
        t3 = time.monotonic()
        recommendation = await recommend_action(
            classification=classification,
            invoice=extraction.model_dump(),
            po_context=po_context,
            gr_context=gr_context,
            case_context=case_context,
            retrieved_policies=retrieved,
            client=client,
            case_id=f"{case_id}::decide",
        )
        decide_ms = int((time.monotonic() - t3) * 1000)
        steps.append(StepTrace(name="decide", latency_ms=decide_ms, cost_usd=0.0))
        await _emit(on_event, {
            "type": "step.end",
            "name": "decide",
            "latency_ms": decide_ms,
            "summary": {
                "action": recommendation.action.value,
                "confidence": round(recommendation.confidence, 2),
                "rationale_snippet": (recommendation.rationale or "")[:160],
            },
        })

        # 5 — route (local; rules-based, no LLM call)
        await _emit(on_event, {"type": "step.start", "name": "route", "ts": time.time()})
        t4 = time.monotonic()
        routing_decision = HITLRouter().route(
            recommendation=recommendation,
            classification=classification,
            case_context=case_context,
        )
        route_ms = int((time.monotonic() - t4) * 1000)
        steps.append(StepTrace(name="route", latency_ms=route_ms, cost_usd=0.0))
        await _emit(on_event, {
            "type": "step.end",
            "name": "route",
            "latency_ms": route_ms,
            "summary": {
                "tier": int(routing_decision.tier),
                "routed_to": routing_decision.routed_to,
            },
        })

        # 6 — draft (only on Tier ≥ 2 AND when the action actually needs one)
        # Tier 1 = auto-pass: no human reads the draft, so don't spend the LLM
        # call to produce one. Saves ~10-25s on the most common path.
        tier_for_draft = int(routing_decision.tier) if routing_decision else 99
        if tier_for_draft >= 2 and action_needs_draft(recommendation.action):
            await _emit(on_event, {"type": "step.start", "name": "draft", "ts": time.time()})
            t5 = time.monotonic()
            draft = await draft_communication(
                recommendation=recommendation,
                classification=classification,
                invoice=extraction.model_dump(),
                case_context=case_context,
                client=client,
                case_id=f"{case_id}::draft",
            )
            draft_ms = int((time.monotonic() - t5) * 1000)
            steps.append(StepTrace(name="draft", latency_ms=draft_ms, cost_usd=0.0))
            await _emit(on_event, {
                "type": "step.end",
                "name": "draft",
                "latency_ms": draft_ms,
                "summary": {
                    "draft_type": draft.draft_type.value if draft else None,
                    "subject": draft.subject if draft else None,
                },
            })
        elif action_needs_draft(recommendation.action):
            # Action wants a draft but tier is 1 — skip and record why.
            reason = f"Tier {tier_for_draft} auto-pass — no human review"
            steps.append(StepTrace(
                name="draft",
                latency_ms=0,
                cost_usd=0.0,
                status="skipped",
                skip_reason=reason,
            ))
            await _emit(on_event, {
                "type": "step.skipped",
                "name": "draft",
                "reason": reason,
            })

        # 7 — enqueue for HITL (only when a queue is wired AND tier ≥ 2)
        if queue is not None and routing_decision is not None and int(routing_decision.tier) >= 2:
            await _emit(on_event, {"type": "step.start", "name": "enqueue", "ts": time.time()})
            t6 = time.monotonic()
            item: HITLItem = queue.enqueue(
                case_id=case_id,
                classification=classification,
                recommendation=recommendation,
                routing_decision=routing_decision,
                draft=draft,
            )
            hitl_item_id = item.id
            enqueue_ms = int((time.monotonic() - t6) * 1000)
            steps.append(StepTrace(name="enqueue", latency_ms=enqueue_ms, cost_usd=0.0))
            await _emit(on_event, {
                "type": "step.end",
                "name": "enqueue",
                "latency_ms": enqueue_ms,
                "summary": {"hitl_item_id": hitl_item_id},
            })

    total_latency_ms = int((time.monotonic() - t_pipeline_start) * 1000)

    await _emit(on_event, {
        "type": "run.done",
        "case_id": case_id,
        "total_latency_ms": total_latency_ms,
        "hitl_item_id": hitl_item_id,
    })

    return PipelineResult(
        case_id=case_id,
        extraction=extraction,
        case_context=case_context,
        classification=classification,
        retrieved_policies=retrieved,
        recommendation=recommendation,
        routing_decision=routing_decision,
        draft=draft,
        hitl_item_id=hitl_item_id,
        total_cost_usd=0.0,
        total_latency_ms=sum(s.latency_ms for s in steps),
        steps=steps,
    )
