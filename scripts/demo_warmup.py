"""Pre-warm the demo before a live presentation.

What this does:
- Loads the singleton PolicyRetriever (pulls bge-large-en into memory).
- Runs 2 curated sample invoices end-to-end through `run_invoice_pipeline`.
- Reports per-step latency for each.

Why: DeepSeek's prompt cache on OpenRouter has a ~5-minute TTL. Running 2
invoices ~3 min before the demo seeds the system-prompt cache for extract /
classify / decide / draft, plus confirms the model isn't having a bad-latency
day. The live demo invoice that follows is then materially faster (cached
prefix processing) and you've sanity-checked the pipeline.

Run: `make demo-warmup` or `uv run python scripts/demo_warmup.py`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from p2p_agent.hitl.webapp.samples import SAMPLES, resolve_pdf
from p2p_agent.llm.client import ModelClient
from p2p_agent.orchestrator import run_invoice_pipeline
from p2p_agent.retrieval import get_default_retriever

# Two samples: a clean auto-pass + a Tier-2 case. Together they exercise the
# full system-prompt set across extract, classify, retrieve, decide, route,
# and draft, so every prompt prefix ends up in DeepSeek's cache.
WARMUP_SAMPLE_IDS = ["clean_us", "po_typo_us"]


async def _warmup() -> None:
    print("=" * 64)
    print("Demo warm-up — primes the LLM prompt cache + verifies live latency")
    print("=" * 64)

    # 1) Warm the embedding model + policy library.
    t0 = time.monotonic()
    retriever = get_default_retriever()
    retriever.retrieve("warm-up", k=1)
    embed_warm_ms = int((time.monotonic() - t0) * 1000)
    print(f"\n[1/3] Embedder + policy library: {embed_warm_ms / 1000:.1f}s "
          f"({retriever.policy_count} policies)")

    # 2) Run each warmup sample.
    client = ModelClient()
    for i, sample_id in enumerate(WARMUP_SAMPLE_IDS, start=2):
        sample = next((s for s in SAMPLES if s.sample_id == sample_id), None)
        if sample is None:
            print(f"[{i}/3] sample '{sample_id}' not in SAMPLES — skipping")
            continue

        try:
            pdf = resolve_pdf(sample)
        except FileNotFoundError as e:
            print(f"[{i}/3] {sample.label}: PDF missing ({e})")
            continue

        print(f"\n[{i}/3] {sample.label}")
        print(f"      {pdf.name}")
        t_run = time.monotonic()
        try:
            result = await run_invoice_pipeline(
                pdf_path=pdf,
                client=client,
                retriever=retriever,
                queue=None,  # don't enqueue warm-up runs
                case_id=f"warmup::{sample.sample_id}",
            )
        except Exception as e:  # noqa: BLE001
            print(f"      FAILED: {type(e).__name__}: {e}")
            continue

        wall_ms = int((time.monotonic() - t_run) * 1000)
        print(f"      wall {wall_ms / 1000:5.1f}s  "
              f"class={result.classification.class_label.value}  "
              f"action={result.recommendation.action.value if result.recommendation else '-'}  "
              f"tier={int(result.routing_decision.tier) if result.routing_decision else '-'}")
        for step in result.steps:
            marker = "·"
            if step.status == "skipped":
                marker = "−"
            elif step.status == "error":
                marker = "✗"
            print(f"        {marker} {step.name:10s} {step.latency_ms / 1000:5.1f}s")

    print("\n" + "=" * 64)
    print("Done. The demo cache is warm. Run /demo within ~5 minutes for")
    print("maximum cache benefit on the live invoice.")
    print("=" * 64)


def main() -> None:
    asyncio.run(_warmup())


if __name__ == "__main__":
    main()
