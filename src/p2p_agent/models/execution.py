"""Action executor domain types.

`ExecutionStep` describes a single simulated downstream call (e.g., "EMAIL the
supplier", "POST the invoice to SAP", "HALT the pay run"). `ExecutionResult`
wraps a list of steps with status + timestamp.

Mock executor today; the same shape will carry real connector outputs when SAP
credentials land — only the backend swaps.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(StrEnum):
    SIMULATED_SUCCESS = "simulated_success"
    SIMULATED_FAILED = "simulated_failed"
    SKIPPED = "skipped"
    EXECUTED = "executed"          # reserved for the real backend


class ExecutionStep(BaseModel):
    """One simulated downstream call."""

    system: str                    # SAP | Ariba | ServiceNow | Email | Slack | PagerDuty | Treasury | Internal
    verb: str                      # POST | PUT | EMAIL | NOTIFY | CREATE_TICKET | HALT_PAY_RUN | SCHEDULE_RECHECK
    target: str                    # endpoint, recipient, channel name
    payload_summary: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """The full record of what the executor (mock or real) did for one approved item."""

    status: ExecutionStatus
    steps: list[ExecutionStep] = Field(default_factory=list)
    note: str = ""
    executed_at: datetime
