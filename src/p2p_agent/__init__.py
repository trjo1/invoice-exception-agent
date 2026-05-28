"""TruVs Agent 1 — P2P Exception Orchestrator.

A Coordination + Routing + Decision-Support agent that handles
procure-to-pay exceptions end-to-end across SAP / Ariba / ServiceNow.

See CLAUDE.md and docs/ for the project context.
"""

# Auto-load .env so `make hitl-serve`, scripts, and tests all see the same
# OPENROUTER_API_KEY without needing `set -a && . ./.env && set +a` first.
# Real environment variables always win — `load_dotenv` does not overwrite.
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — dotenv is pulled in transitively
    pass
else:
    _ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE, override=False)

__version__ = "0.1.0"
