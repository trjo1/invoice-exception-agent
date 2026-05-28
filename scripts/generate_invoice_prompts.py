"""Generate invoice-generation prompts for the subscription-mode workflow.

Reads config/personas.yaml, produces batched prompts for Claude.ai / ChatGPT.
Each batch is a self-contained Markdown file the user pastes into a chat
session; the user copies the response back into the matching JSON file.

See docs/subscription_mode_workflow.md for the full workflow.

Usage:
    uv run python scripts/generate_invoice_prompts.py [options]

Options:
    --count N           total invoices to generate (default: 500)
    --batch-size N      invoices per prompt batch (default: 5)
    --seed N            random seed for reproducibility (default: 42)
    --error-rate F      fraction of invoices with injected errors (default: 0.35)
    --output-dir PATH   where to write the prompt files
                        (default: scripts/subscription_workflow/prompts/)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
PERSONAS_PATH = REPO_ROOT / "config" / "personas.yaml"
DEFAULT_OUT = REPO_ROOT / "scripts" / "subscription_workflow" / "prompts"


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
# Invoice generation — batch {batch_no} of {total_batches}

You are generating realistic supplier-invoice data for a P2P (procure-to-pay) AI
agent test corpus. Each invoice you generate becomes input the agent's invoice
extractor will be tested against.

This batch contains {n_invoices} invoice specifications. For each one, generate
a complete invoice as a single JSON object matching the schema at the bottom.

**Critical rules:**
- Respond with a single JSON ARRAY containing exactly {n_invoices} invoice objects.
- No prose, no markdown, no commentary outside the JSON.
- Wrap the JSON in a ```json code block.
- Every field in the schema is required. Use realistic values consistent with
  the supplier persona for each invoice.
- When the spec says `error_injected: <error_mode>`, deliberately introduce
  that error so the test agent has something to catch. Record the error in
  `ground_truth_note`. When `error_injected: null`, the invoice is clean.

---

## Invoices to generate

{invoice_specs}

---

## Output schema (one object per invoice)

```json
{schema}
```

---

## Reminder

Output: a single JSON array of {n_invoices} objects, in a ```json block. Nothing else.
"""

INVOICE_SCHEMA: dict[str, Any] = {
    "invoice_id": "string — format: <persona_id>_INV_<YYYYMMDD>_<NNN>",
    "persona_id": "string — the persona this invoice is from",
    "po_reference": "string — format: PO-<YYYY>-<MM>-<NNNNN>",
    "invoice_date": "ISO date — YYYY-MM-DD",
    "currency": "ISO currency code (USD, EUR, GBP, INR, BRL, etc.)",
    "payment_terms": "string — e.g. NET-30, NET-45, 2/10-NET-30",
    "header_fields": {
        "vendor_name": "string — matches persona name",
        "vendor_address": "string — multi-line OK, plausible for persona's region",
        "vendor_tax_id": "string — region-appropriate (US EIN, EU VAT ID, IN GSTIN, etc.)",
        "buyer_name": "string — invented buyer organization, kept consistent across batches",
        "buyer_address": "string",
        "buyer_po_contact": "string — name + email",
    },
    "line_items": [
        {
            "line_no": "integer, 1-indexed",
            "sku": "string — supplier-specific SKU",
            "description": "string — concrete product/service description",
            "quantity": "number",
            "unit_price": "number — in invoice currency",
            "line_total": "number — quantity × unit_price; rounding per persona quirk",
        },
    ],
    "subtotal": "number — sum of line totals",
    "tax": [
        {
            "jurisdiction": "string — e.g. US-CA, EU-DE-VAT, IN-IGST",
            "rate": "number 0–1",
            "amount": "number",
        },
    ],
    "total": "number — subtotal + tax",
    "error_injected": "string|null — which error mode was deliberately injected, e.g. 'PO_REFERENCE_TYPO', 'MISSING_TAX_LINE', null if clean",
    "ground_truth_note": "string — 1-2 sentences explaining what the extractor should pull and any deliberate errors",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_personas() -> list[dict[str, Any]]:
    with PERSONAS_PATH.open() as f:
        config = yaml.safe_load(f)
    return config.get("personas", [])


def pick_persona(personas: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    """Weighted by persona; could later weight by realistic supplier mix."""
    return rng.choice(personas)


def pick_error_mode(persona: dict[str, Any], rng: random.Random) -> str | None:
    """Pick one of the persona's typical error modes. Returns None only if the
    persona has no error modes defined. The decision to inject *any* error is
    made upstream in build_invoice_spec; this function just picks which one.
    """
    error_modes = persona.get("typical_error_modes", [])
    if not error_modes:
        return None
    return rng.choice(error_modes)


def build_invoice_spec(
    persona: dict[str, Any],
    invoice_idx: int,
    rng: random.Random,
    error_rate: float,
) -> dict[str, Any]:
    """One invoice specification — what the chat should produce."""
    inject_error = rng.random() < error_rate
    error_mode = pick_error_mode(persona, rng) if inject_error else None
    base_date = datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 120))
    return {
        "spec_index": invoice_idx,
        "persona_id": persona["id"],
        "persona_name": persona["name"],
        "persona_region": persona["region"],
        "persona_language": persona["language"],
        "persona_currency": persona["currency"],
        "persona_layout_style": persona["layout_style"],
        "persona_payment_terms": persona["payment_terms_default"],
        "persona_quirks": persona.get("quirks", []),
        "invoice_date_hint": base_date.strftime("%Y-%m-%d"),
        "line_item_count_hint": rng.choice([1, 2, 3, 5, 8]),
        "error_injected": error_mode,
    }


def render_invoice_specs(specs: list[dict[str, Any]]) -> str:
    """Render the invoice specs as a numbered list for the prompt."""
    out: list[str] = []
    for i, spec in enumerate(specs, 1):
        quirks_str = "\n      - ".join(spec["persona_quirks"]) if spec["persona_quirks"] else "(none)"
        error_str = spec["error_injected"] or "null (clean invoice)"
        out.append(
            f"### Invoice {i}\n"
            f"- persona_id: {spec['persona_id']}\n"
            f"- persona_name: {spec['persona_name']}\n"
            f"- region: {spec['persona_region']}\n"
            f"- language: {spec['persona_language']}\n"
            f"- currency: {spec['persona_currency']}\n"
            f"- layout_style: {spec['persona_layout_style']}\n"
            f"- payment_terms: {spec['persona_payment_terms']}\n"
            f"- line_items: about {spec['line_item_count_hint']}\n"
            f"- invoice_date around: {spec['invoice_date_hint']}\n"
            f"- error_injected: {error_str}\n"
            f"- persona quirks to reflect:\n      - {quirks_str}",
        )
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--error-rate", type=float, default=0.35)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.batch_size <= 0 or args.count <= 0:
        print("count and batch-size must be positive", file=sys.stderr)
        sys.exit(2)

    personas = load_personas()
    if not personas:
        print(f"No personas found in {PERSONAS_PATH}", file=sys.stderr)
        sys.exit(2)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    total_batches = (args.count + args.batch_size - 1) // args.batch_size
    written = 0

    # Persist the seed and config in a manifest so the ingester can verify.
    manifest = {
        "seed": args.seed,
        "count": args.count,
        "batch_size": args.batch_size,
        "error_rate": args.error_rate,
        "total_batches": total_batches,
        "personas_hash": hashlib.sha256(
            PERSONAS_PATH.read_bytes(),
        ).hexdigest()[:16],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_path = args.output_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    for batch_no in range(1, total_batches + 1):
        remaining = args.count - (batch_no - 1) * args.batch_size
        n_invoices = min(args.batch_size, remaining)
        specs = [
            build_invoice_spec(pick_persona(personas, rng),
                               (batch_no - 1) * args.batch_size + i,
                               rng,
                               args.error_rate)
            for i in range(n_invoices)
        ]
        prompt = PROMPT_TEMPLATE.format(
            batch_no=batch_no,
            total_batches=total_batches,
            n_invoices=n_invoices,
            invoice_specs=render_invoice_specs(specs),
            schema=json.dumps(INVOICE_SCHEMA, indent=2),
        )
        # Also write a sidecar JSON with the spec — ingester uses it to validate
        sidecar = {
            "batch_no": batch_no,
            "n_invoices": n_invoices,
            "specs": specs,
        }
        prompt_path = args.output_dir / f"invoice_batch_{batch_no:03d}.md"
        sidecar_path = args.output_dir / f"invoice_batch_{batch_no:03d}.spec.json"
        prompt_path.write_text(prompt)
        sidecar_path.write_text(json.dumps(sidecar, indent=2))
        written += 1

    print(f"Wrote {written} prompt files to {args.output_dir}")
    print(f"  + manifest: {manifest_path}")
    print()
    print("Next steps:")
    print("  1. Open the first prompt: scripts/subscription_workflow/prompts/invoice_batch_001.md")
    print("  2. Paste into Claude.ai or ChatGPT (see docs/subscription_mode_workflow.md)")
    print("  3. Save the JSON response to: scripts/subscription_workflow/responses/invoice_batch_001.json")
    print("  4. Repeat for the remaining batches; ingest with `make corpus-ingest-invoices`")


if __name__ == "__main__":
    main()
