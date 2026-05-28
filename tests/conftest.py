"""Shared pytest fixtures for the agent test suite.

Conventions:
- Tests marked @pytest.mark.golden run on every build (the regression set).
- Tests marked @pytest.mark.integration require ERP sandbox connectivity.
- Tests marked @pytest.mark.slow are deselected by default in CI.
- Tests marked @pytest.mark.needs_api_key require real LLM API keys.

See pyproject.toml [tool.pytest.ini_options] for marker registration.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CASES_DIR = REPO_ROOT / "tests" / "golden_cases"
TEST_CORPUS_DIR = REPO_ROOT / "test_corpus"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def golden_cases_dir() -> Path:
    return GOLDEN_CASES_DIR


@pytest.fixture(scope="session")
def test_corpus_dir() -> Path:
    return TEST_CORPUS_DIR


@pytest.fixture
def has_openrouter_key() -> bool:
    """Whether the env has an OpenRouter API key set. Use in skipif."""
    return bool(os.environ.get("OPENROUTER_API_KEY"))


@pytest.fixture
def has_erp_sandbox() -> bool:
    """Whether ERP sandbox env vars are set. Use in skipif for integration tests."""
    return all(os.environ.get(k) for k in ("SAP_BASE_URL", "SAP_CLIENT_ID"))
