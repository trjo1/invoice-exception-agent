"""PipelineResult — the single object returned by the orchestrator pipeline.

Aggregates each node's output plus aggregate cost and per-step latency. As
more nodes come online, this model grows to include them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from p2p_agent.models.classification import Classification
from p2p_agent.models.context import CaseContext
from p2p_agent.models.draft import Draft
from p2p_agent.models.extraction import InvoiceExtraction
from p2p_agent.models.recommendation import Recommendation
from p2p_agent.models.retrieval import RetrievedDoc
from p2p_agent.models.routing import RoutingDecision


class StepTrace(BaseModel):
    """Per-step trace — used for Stage 9 measurement and debugging."""

    name: str
    latency_ms: int
    cost_usd: float
    status: str = "ok"  # "ok" | "skipped" | "error"
    skip_reason: str | None = None  # populated when status == "skipped"


class PipelineResult(BaseModel):
    case_id: str | None = None

    extraction: InvoiceExtraction
    case_context: CaseContext | None = None
    classification: Classification
    retrieved_policies: list[RetrievedDoc] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    routing_decision: RoutingDecision | None = None
    draft: Draft | None = None

    hitl_item_id: str | None = None  # set when the case was enqueued to the HITL queue

    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    steps: list[StepTrace] = Field(default_factory=list)
