"""Orchestrator — wires per-archetype nodes into a runnable pipeline.

Today: a plain async function. Later: LangGraph state machine on the same
nodes. Both expose `run_invoice_pipeline` as the single entry point.
"""

from p2p_agent.models.pipeline import PipelineResult, StepTrace
from p2p_agent.orchestrator.pipeline import run_invoice_pipeline

__all__ = ["PipelineResult", "StepTrace", "run_invoice_pipeline"]
