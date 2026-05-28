"""Generate synthetic invoices via OpenRouter API (API-mode corpus generation).

Reuses the subscription-mode prompt machinery in
`scripts/generate_invoice_prompts.py`, but instead of writing prompts to disk
for manual paste, calls the LLM via `src/p2p_agent/llm/client.py` and writes
responses straight to `scripts/subscription_workflow/responses/`. After this
script runs, `make corpus-ingest-invoices` renders the PDFs.

Usage:
    uv run python scripts/generate_invoices.py --count 25
    uv run python scripts/generate_invoices.py --count 500 --batch-size 5 --seed 42

Cost reference (DeepSeek V4-Flash @ $0.14/$0.28 per 1M tokens):
    - 25 invoices  ≈ ~$0.03
    - 500 invoices ≈ ~$0.50
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

# Reuse the subscription-mode prompt generation machinery verbatim.
from scripts.generate_invoice_prompts import (  # noqa: E402
    INVOICE_SCHEMA,
    PROMPT_TEMPLATE,
    PERSONAS_PATH,
    build_invoice_spec,
    load_personas,
    pick_persona,
    render_invoice_specs,
)
from p2p_agent.llm.client import ModelClient  # noqa: E402


PROMPTS_DIR = REPO_ROOT / "scripts" / "subscription_workflow" / "prompts"
RESPONSES_DIR = REPO_ROOT / "scripts" / "subscription_workflow" / "responses"


SYSTEM_PROMPT = (
    "You generate realistic synthetic supplier invoice data for a test corpus. "
    "Output strictly as a single JSON array wrapped in a ```json code block. "
    "No prose, no commentary. Every field in the schema is required."
)


def build_batch(
    batch_no: int,
    total_batches: int,
    personas: list[dict[str, Any]],
    rng: random.Random,
    batch_size: int,
    remaining: int,
    error_rate: float,
    start_idx: int,
) -> tuple[str, dict[str, Any]]:
    """Build one batch prompt + its spec sidecar."""
    n_invoices = min(batch_size, remaining)
    specs = [
        build_invoice_spec(
            pick_persona(personas, rng),
            start_idx + i,
            rng,
            error_rate,
        )
        for i in range(n_invoices)
    ]
    prompt = PROMPT_TEMPLATE.format(
        batch_no=batch_no,
        total_batches=total_batches,
        n_invoices=n_invoices,
        invoice_specs=render_invoice_specs(specs),
        schema=json.dumps(INVOICE_SCHEMA, indent=2),
    )
    sidecar = {"batch_no": batch_no, "n_invoices": n_invoices, "specs": specs}
    return prompt, sidecar


async def generate_one_batch(
    client: ModelClient,
    prompt: str,
    batch_no: int,
    temperature: float,
    max_tokens: int,
) -> tuple[str, float, int]:
    """Call the API for one batch; return (raw_text, cost_usd, latency_ms)."""
    result = await client.complete(
        task="corpus_generation",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        case_id=f"corpus_invoice_batch_{batch_no:03d}",
    )
    return result.output_text, result.cost_usd, result.latency_ms


async def run(
    count: int,
    batch_size: int,
    seed: int,
    error_rate: float,
    temperature: float,
    max_tokens: int,
    skip_existing: bool,
    concurrency: int,
) -> dict[str, Any]:
    personas = load_personas()
    if not personas:
        raise SystemExit(f"No personas found in {PERSONAS_PATH}")

    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    total_batches = (count + batch_size - 1) // batch_size

    manifest = {
        "seed": seed,
        "count": count,
        "batch_size": batch_size,
        "error_rate": error_rate,
        "total_batches": total_batches,
        "mode": "api",
        "personas_hash": hashlib.sha256(PERSONAS_PATH.read_bytes()).hexdigest()[:16],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (PROMPTS_DIR / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    # Build all batches sequentially first — keeps RNG consumption deterministic
    # from the seed even when API calls are dispatched concurrently below.
    plan: list[tuple[int, Path, Path, str]] = []
    for batch_no in range(1, total_batches + 1):
        start_idx = (batch_no - 1) * batch_size
        remaining = count - start_idx
        prompt, sidecar = build_batch(
            batch_no, total_batches, personas, rng, batch_size,
            remaining, error_rate, start_idx,
        )
        prompt_path = PROMPTS_DIR / f"invoice_batch_{batch_no:03d}.md"
        spec_path = PROMPTS_DIR / f"invoice_batch_{batch_no:03d}.spec.json"
        response_path = RESPONSES_DIR / f"invoice_batch_{batch_no:03d}.json"
        prompt_path.write_text(prompt)
        spec_path.write_text(json.dumps(sidecar, indent=2))
        plan.append((batch_no, prompt_path, response_path, prompt))

    client = ModelClient()
    semaphore = asyncio.Semaphore(concurrency)

    stats = {
        "batches_planned": total_batches,
        "batches_called": 0,
        "batches_skipped": 0,
        "total_cost_usd": 0.0,
        "total_latency_ms": 0,
        "errors": [],
    }

    async def process(batch_no: int, response_path: Path, prompt: str) -> None:
        if skip_existing and response_path.exists() and response_path.stat().st_size > 0:
            stats["batches_skipped"] += 1
            print(f"[{batch_no:03d}] skipped (response exists)")
            return

        async with semaphore:
            try:
                text, cost, latency = await generate_one_batch(
                    client, prompt, batch_no, temperature, max_tokens,
                )
            except Exception as e:  # noqa: BLE001 — surface to user
                stats["errors"].append({"batch_no": batch_no, "error": repr(e)})
                print(f"[{batch_no:03d}] FAILED: {e!r}")
                return

        if not text or not text.strip():
            stats["errors"].append({
                "batch_no": batch_no,
                "error": "empty response (likely truncated by max_tokens — try --max-tokens 16384)",
            })
            print(f"[{batch_no:03d}] EMPTY response — likely truncated. Cost ${cost:.4f}.")
            return

        response_path.write_text(text)
        stats["batches_called"] += 1
        stats["total_cost_usd"] += cost
        stats["total_latency_ms"] += latency
        print(f"[{batch_no:03d}] ok  cost=${cost:.4f}  latency={latency}ms")

    await asyncio.gather(*(
        process(batch_no, response_path, prompt)
        for batch_no, _, response_path, prompt in plan
    ))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500, help="Total invoices to generate")
    parser.add_argument("--batch-size", type=int, default=5, help="Invoices per API call")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (reproducible)")
    parser.add_argument("--error-rate", type=float, default=0.35,
                        help="Fraction of invoices with injected errors")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Sampling temperature; higher = more variety")
    parser.add_argument("--max-tokens", type=int, default=16384,
                        help="Max tokens per API response (per batch). "
                             "5 invoices needs ~10-14K depending on persona verbosity.")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Re-call API even when a response file already exists")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Concurrent API calls (4 is safe on OpenRouter)")
    args = parser.parse_args()

    if args.count <= 0 or args.batch_size <= 0:
        print("count and batch-size must be positive", file=sys.stderr)
        sys.exit(2)

    print(
        f"Plan: {args.count} invoices in batches of {args.batch_size} "
        f"(seed={args.seed}, error_rate={args.error_rate}, "
        f"temp={args.temperature}, concurrency={args.concurrency})",
    )
    stats = asyncio.run(run(
        count=args.count,
        batch_size=args.batch_size,
        seed=args.seed,
        error_rate=args.error_rate,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        skip_existing=not args.no_skip_existing,
        concurrency=args.concurrency,
    ))

    print()
    print("=== Summary ===")
    print(f"  batches called:  {stats['batches_called']}")
    print(f"  batches skipped: {stats['batches_skipped']}")
    print(f"  total cost:      ${stats['total_cost_usd']:.4f}")
    print(f"  total latency:   {stats['total_latency_ms']} ms")
    if stats["errors"]:
        print(f"  errors:          {len(stats['errors'])}")
        for err in stats["errors"]:
            print(f"    - batch {err['batch_no']}: {err['error']}")
    print()
    print("Next: render the PDFs with `make corpus-ingest-invoices`.")


if __name__ == "__main__":
    main()
