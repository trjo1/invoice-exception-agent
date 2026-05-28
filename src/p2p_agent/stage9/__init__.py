"""Stage 9 — measurement layer.

`Stage9Reader` reads `logs/llm_calls.jsonl` (every LLM call) and produces cost
+ latency aggregates. `Stage9Aggregator` reads the SQLite stores (pipeline
runs, HITL queue) and produces ops-level metrics: auto-pass rate, HITL
resolution breakdown, classification mix.

Stage 9 is the recurring-revenue moat per CLAUDE.md — measurement is shipped
from day one, not bolted on later.
"""

from p2p_agent.stage9.aggregator import Stage9Aggregator
from p2p_agent.stage9.recorder import WINDOWS, Stage9Reader

__all__ = ["Stage9Aggregator", "Stage9Reader", "WINDOWS"]
