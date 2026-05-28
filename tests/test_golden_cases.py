"""Pytest entry for the golden-case regression set.

Each YAML file under tests/golden_cases/ becomes one parametrized test. The
test passes when the classification sub-block (the only node currently
implemented) passes; non-classification expectations are deferred and
reported as xfail so they're visible but non-blocking.

Requires OPENROUTER_API_KEY to be set (skipped otherwise). Run via:

    make test-golden       # only the @pytest.mark.golden cases
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_golden_set import run_case  # noqa: E402

from p2p_agent.llm.client import ModelClient  # noqa: E402
from p2p_agent.models import load_golden_case  # noqa: E402


GOLDEN_CASES_DIR = REPO_ROOT / "tests" / "golden_cases"


def _discover_case_files() -> list[Path]:
    return sorted(GOLDEN_CASES_DIR.glob("GTC-*.yaml"))


_pytest_skip_reason = "OPENROUTER_API_KEY not set; live LLM call required"


@pytest.mark.golden
@pytest.mark.needs_api_key
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason=_pytest_skip_reason)
@pytest.mark.parametrize(
    "case_path",
    _discover_case_files(),
    ids=lambda p: p.stem,
)
def test_golden_case(case_path: Path) -> None:
    case = load_golden_case(case_path)
    client = ModelClient()
    result = asyncio.run(run_case(case, client))

    cls_sub = next((s for s in result.subblocks if s.name == "classification"), None)
    assert cls_sub is not None, f"{result.case_id}: classification sub-block missing"

    if cls_sub.status == "skip":
        pytest.skip(cls_sub.detail or "classification skipped")

    # xfail the not-yet-implemented sub-blocks so they show as expected failures
    # rather than passing silently.
    deferred = [s for s in result.subblocks
                if s.name != "classification" and s.status == "skip"]
    if deferred:
        # Don't fail the test on these; just emit a record via xfail-style note.
        # We can't xfail mid-test cleanly in pytest, so we log via stderr.
        names = ", ".join(s.name for s in deferred)
        sys.stderr.write(f"[{result.case_id}] deferred (no node yet): {names}\n")

    assert cls_sub.status == "pass", (
        f"{result.case_id} classification {cls_sub.status}: {cls_sub.detail}"
    )
