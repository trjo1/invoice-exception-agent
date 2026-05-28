"""Estimate cost of running the golden test suite at current model assignments.

Reads config/models.yaml to know which models are assigned to which tasks,
then estimates total cost of running the golden set against them.

Status: SKELETON. Implementation lands alongside the cost calculator in
src/p2p_agent/llm/cost_calculator.py.

Usage:
    uv run python scripts/estimate_cost.py [--suite golden|unit|integration]
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="golden", choices=["golden", "unit", "integration"])
    args = parser.parse_args()

    print(f"Estimating cost for the '{args.suite}' suite at current model assignments.")
    print("Implementation pending — depends on cost_calculator.py landing.")
    print()
    print("Reference targets (from docs/model_strategy.md):")
    print(f"  Per-case end-to-end:  $0.50 (test) / $1.00 (prod)")
    print(f"  Golden-set run (40):  $10 (test)  / $40 (prod)")


if __name__ == "__main__":
    main()
