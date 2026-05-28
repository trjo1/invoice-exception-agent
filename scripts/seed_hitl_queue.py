"""Populate the HITL queue by running the pipeline on N corpus invoices.

Prefers invoices with `error_injected != null` so the queue has visible variety
(price variance, missing PO, fraud signal, etc.). Falls back to clean invoices
to fill the requested count.

Usage:
    uv run python scripts/seed_hitl_queue.py                # 10 invoices, default DB
    uv run python scripts/seed_hitl_queue.py --n 25
    uv run python scripts/seed_hitl_queue.py --db sqlite:///./logs/hitl_queue.db
    uv run python scripts/seed_hitl_queue.py --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from p2p_agent.context import CaseContextBuilder  # noqa: E402
from p2p_agent.hitl import DEFAULT_DB_URL, HITLQueue  # noqa: E402
from p2p_agent.llm.client import ModelClient  # noqa: E402
from p2p_agent.orchestrator import run_invoice_pipeline  # noqa: E402
from p2p_agent.retrieval import PolicyRetriever  # noqa: E402

CORPUS_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"


def pick_sidecars(n: int, seed: int) -> list[Path]:
    """Sample n sidecars, preferring those with an injected error label."""
    rng = random.Random(seed)
    all_sidecars = sorted(p for p in CORPUS_DIR.glob("*.json") if not p.name.startswith("_"))
    with_error: list[Path] = []
    clean: list[Path] = []
    for p in all_sidecars:
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        (with_error if data.get("error_injected") else clean).append(p)
    rng.shuffle(with_error)
    rng.shuffle(clean)
    picked = with_error[: max(0, n - 2)]  # leave room for at least 2 clean
    picked.extend(clean[: max(0, n - len(picked))])
    return picked[:n]


async def seed(sidecars: list[Path], queue: HITLQueue, concurrency: int) -> dict:
    client = ModelClient()
    retriever = PolicyRetriever()
    context_builder = CaseContextBuilder()
    semaphore = asyncio.Semaphore(concurrency)

    enqueued = 0
    cleared_at_tier1 = 0
    failures: list[str] = []

    async def process(sidecar: Path) -> None:
        nonlocal enqueued, cleared_at_tier1
        pdf = sidecar.with_suffix(".pdf")
        if not pdf.exists():
            failures.append(f"{sidecar.name}: PDF missing")
            return
        async with semaphore:
            try:
                result = await run_invoice_pipeline(
                    pdf_path=pdf,
                    client=client,
                    retriever=retriever,
                    context_builder=context_builder,
                    queue=queue,
                    case_id=sidecar.stem,
                )
            except Exception as e:  # noqa: BLE001 — seed is best-effort
                failures.append(f"{sidecar.name}: {type(e).__name__}: {e}")
                return
            if result.hitl_item_id is None:
                cleared_at_tier1 += 1
            else:
                enqueued += 1

    t0 = time.monotonic()
    await asyncio.gather(*(process(s) for s in sidecars))
    elapsed = time.monotonic() - t0

    return {
        "enqueued": enqueued,
        "cleared_at_tier1": cleared_at_tier1,
        "failures": failures,
        "elapsed_s": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10, help="Number of invoices to seed (default 10)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_URL, help="HITL DB URL")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--clear", action="store_true", help="Wipe queue first")
    args = parser.parse_args()

    queue = HITLQueue(db_url=args.db)
    if args.clear:
        queue.clear()
        print(f"Cleared {args.db}")

    sidecars = pick_sidecars(args.n, args.seed)
    print(f"Plan: run pipeline on {len(sidecars)} invoices (concurrency={args.concurrency})")
    print(f"DB: {args.db}")

    result = asyncio.run(seed(sidecars, queue, args.concurrency))

    print("")
    print(f"Enqueued (tier ≥ 2): {result['enqueued']}")
    print(f"Cleared at Tier 1 (auto-pass): {result['cleared_at_tier1']}")
    print(f"Failures: {len(result['failures'])}")
    for f in result["failures"][:5]:
        print(f"  {f}")
    print(f"Elapsed: {result['elapsed_s']:.1f}s")
    print("")
    print(f"Stats: {queue.stats()}")
    print("")
    print("Run `make hitl-serve` to open the demo UI at http://localhost:8080/queue")


if __name__ == "__main__":
    main()
