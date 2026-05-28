"""Pydantic models for golden test cases.

Only `ExpectedClassification` is fully typed. The other expected sub-blocks
(recommendation, hitl, drafting, execution, stage9) accept `dict[str, Any]`
so the harness can load any case even before those nodes are implemented.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from p2p_agent.models.classification import ExceptionCategory


class ExpectedClassification(BaseModel):
    class_label: ExceptionCategory
    min_confidence: float = Field(ge=0.0, le=1.0)
    must_contain_evidence: list[str] = Field(default_factory=list)


class ExpectedRecommendation(BaseModel):
    """Partial typing for the YAML `expected.recommendation` sub-block."""

    action: str | None = None
    rationale_must_mention: list[str] = Field(default_factory=list)
    counterfactual_should_exist: bool = False

    model_config = {"extra": "allow"}


class ExpectedHITL(BaseModel):
    """Partial typing for the YAML `expected.hitl` sub-block."""

    tier: int | None = None
    routed_to: str | None = None
    must_include_in_payload: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class ExpectedDrafting(BaseModel):
    """Partial typing for the YAML `expected.drafting` sub-block."""

    must_produce_draft: bool = False
    draft_type: str | None = None
    draft_recipient: str | None = None
    draft_content_must_mention: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class Expected(BaseModel):
    classification: ExpectedClassification
    recommendation: ExpectedRecommendation | None = None
    hitl: ExpectedHITL | None = None
    drafting: ExpectedDrafting | None = None
    execution: dict[str, Any] | None = None
    stage9: dict[str, Any] | None = None


class GoldenCase(BaseModel):
    id: str
    title: str
    exception_category: str
    difficulty: str | None = None
    created: date | str | None = None     # YAML parses ISO dates as datetime.date
    notes: str | None = None
    input: dict[str, Any]
    expected: Expected
    pass_criteria: list[str] = Field(default_factory=list)


def load_golden_case(path: Path) -> GoldenCase:
    return GoldenCase.model_validate(yaml.safe_load(path.read_text()))
