"""Ingest subscription-mode chat responses into the test corpus.

Reads JSON responses pasted by TJ into
`scripts/subscription_workflow/responses/`, validates them, renders the
invoice PDFs via weasyprint, writes the final assets to
`test_corpus/synthetic/`.

Idempotent — re-running skips already-ingested batches.

See docs/subscription_mode_workflow.md.

Usage:
    uv run python scripts/ingest_subscription_responses.py [--asset invoices|emails|master_data]

Status: SKELETON for the validation + PDF rendering layer. Validation logic
written; weasyprint rendering stubbed (lands when first responses arrive).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from p2p_agent.llm.json_utils import extract_json_from_response  # noqa: E402

PROMPTS_DIR = REPO_ROOT / "scripts" / "subscription_workflow" / "prompts"
RESPONSES_DIR = REPO_ROOT / "scripts" / "subscription_workflow" / "responses"
BATCH_LOGS_DIR = REPO_ROOT / "scripts" / "subscription_workflow" / "batch_logs"
CORPUS_INVOICES = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"
CORPUS_EMAILS = REPO_ROOT / "test_corpus" / "synthetic" / "emails"
CORPUS_MASTER = REPO_ROOT / "test_corpus" / "synthetic" / "master_data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_INVOICE_FIELDS = {
    "invoice_id", "persona_id", "po_reference", "invoice_date", "currency",
    "payment_terms", "header_fields", "line_items", "subtotal", "tax",
    "total", "error_injected", "ground_truth_note",
}

REQUIRED_HEADER_FIELDS = {
    "vendor_name", "vendor_address", "vendor_tax_id",
    "buyer_name", "buyer_address", "buyer_po_contact",
}

REQUIRED_LINE_ITEM_FIELDS = {
    "line_no", "sku", "description", "quantity", "unit_price", "line_total",
}


def validate_invoice(inv: dict[str, Any], expected_persona: str) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors: list[str] = []
    missing = REQUIRED_INVOICE_FIELDS - set(inv.keys())
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
        return errors

    if inv["persona_id"] != expected_persona:
        errors.append(
            f"persona mismatch: expected {expected_persona!r}, got {inv['persona_id']!r}",
        )

    if not isinstance(inv["header_fields"], dict):
        errors.append("header_fields not an object")
    else:
        missing_header = REQUIRED_HEADER_FIELDS - set(inv["header_fields"].keys())
        if missing_header:
            errors.append(f"header missing: {sorted(missing_header)}")

    line_items = inv.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        errors.append("line_items must be a non-empty list")
    else:
        for i, li in enumerate(line_items):
            missing_li = REQUIRED_LINE_ITEM_FIELDS - set(li.keys())
            if missing_li:
                errors.append(f"line_item[{i}] missing: {sorted(missing_li)}")
            elif not isinstance(li.get("quantity"), (int, float)) or li["quantity"] <= 0:
                errors.append(f"line_item[{i}] quantity not positive number")

    if not isinstance(inv.get("total"), (int, float)):
        errors.append("total not a number")

    return errors


INVOICE_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Helvetica", "Arial", sans-serif; font-size: 10pt;
       color: #1a1a1a; }
.header { display: flex; justify-content: space-between;
          border-bottom: 2px solid #1F3A68; padding-bottom: 8mm;
          margin-bottom: 6mm; }
.vendor { width: 55%; }
.meta   { width: 40%; text-align: right; }
.vendor h1 { margin: 0 0 2mm 0; color: #1F3A68; font-size: 16pt; }
.vendor .addr { white-space: pre-line; font-size: 9pt; color: #444; }
.meta .invno { font-size: 14pt; font-weight: bold; color: #1F3A68; }
.meta .small { font-size: 9pt; color: #444; }
.parties { display: flex; justify-content: space-between; margin-bottom: 6mm; }
.party { width: 48%; border: 1px solid #ddd; padding: 3mm; }
.party h3 { margin: 0 0 1mm 0; font-size: 9pt; color: #1F3A68;
            text-transform: uppercase; letter-spacing: 0.3pt; }
.party .body { white-space: pre-line; font-size: 9pt; }
table.items { width: 100%; border-collapse: collapse; margin-bottom: 4mm; }
table.items th { background: #E8EEF5; color: #1F3A68; text-align: left;
                 font-size: 9pt; padding: 2mm; border-bottom: 1px solid #1F3A68; }
table.items td { padding: 2mm; border-bottom: 1px solid #eee; font-size: 9pt;
                 vertical-align: top; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.totals { width: 50%; margin-left: auto; margin-top: 2mm; }
.totals tr td { padding: 1.5mm 2mm; font-size: 10pt; }
.totals tr.grand td { border-top: 1.5px solid #1F3A68;
                      font-weight: bold; color: #1F3A68; }
.footer { margin-top: 8mm; font-size: 8pt; color: #666;
          border-top: 1px solid #ddd; padding-top: 2mm; }
"""


def _esc(value: Any) -> str:
    """Light HTML escape — invoice text rarely has markup, but be safe."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _money(amount: Any, currency: str) -> str:
    try:
        return f"{float(amount):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


def _invoice_html(invoice: dict[str, Any]) -> str:
    h = invoice.get("header_fields", {})
    currency = invoice.get("currency", "")
    line_rows: list[str] = []
    for li in invoice.get("line_items", []):
        line_rows.append(
            "<tr>"
            f"<td>{_esc(li.get('line_no'))}</td>"
            f"<td>{_esc(li.get('sku'))}</td>"
            f"<td>{_esc(li.get('description'))}</td>"
            f"<td class='num'>{_esc(li.get('quantity'))}</td>"
            f"<td class='num'>{_esc(li.get('unit_price'))}</td>"
            f"<td class='num'>{_money(li.get('line_total'), currency)}</td>"
            "</tr>",
        )
    tax_rows: list[str] = []
    for t in invoice.get("tax", []) or []:
        tax_rows.append(
            "<tr><td>"
            f"Tax — {_esc(t.get('jurisdiction'))} ({float(t.get('rate', 0)) * 100:.2f}%)"
            f"</td><td class='num'>{_money(t.get('amount'), currency)}</td></tr>",
        )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{INVOICE_CSS}</style></head><body>
<div class="header">
  <div class="vendor">
    <h1>{_esc(h.get('vendor_name'))}</h1>
    <div class="addr">{_esc(h.get('vendor_address'))}</div>
    <div class="addr">Tax ID: {_esc(h.get('vendor_tax_id'))}</div>
  </div>
  <div class="meta">
    <div class="invno">INVOICE {_esc(invoice.get('invoice_id'))}</div>
    <div class="small">Date: {_esc(invoice.get('invoice_date'))}</div>
    <div class="small">PO Reference: {_esc(invoice.get('po_reference'))}</div>
    <div class="small">Terms: {_esc(invoice.get('payment_terms'))}</div>
    <div class="small">Currency: {_esc(currency)}</div>
  </div>
</div>

<div class="parties">
  <div class="party">
    <h3>Bill To</h3>
    <div class="body">{_esc(h.get('buyer_name'))}
{_esc(h.get('buyer_address'))}
Contact: {_esc(h.get('buyer_po_contact'))}</div>
  </div>
  <div class="party">
    <h3>Remit To</h3>
    <div class="body">{_esc(h.get('vendor_name'))}
{_esc(h.get('vendor_address'))}</div>
  </div>
</div>

<table class="items">
<thead><tr>
  <th>#</th><th>SKU</th><th>Description</th>
  <th class="num">Qty</th><th class="num">Unit Price</th><th class="num">Line Total</th>
</tr></thead>
<tbody>
{''.join(line_rows)}
</tbody></table>

<table class="totals">
<tr><td>Subtotal</td><td class="num">{_money(invoice.get('subtotal'), currency)}</td></tr>
{''.join(tax_rows)}
<tr class="grand"><td>Total Due</td><td class="num">{_money(invoice.get('total'), currency)}</td></tr>
</table>

<div class="footer">
  Generated synthetic invoice — TruVs P2P Agent test corpus.
  Persona: {_esc(invoice.get('persona_id'))}.
</div>
</body></html>"""


_WEASYPRINT_STATE: dict[str, Any] = {"checked": False, "available": False, "error": None}


def _weasyprint_available() -> tuple[bool, str | None]:
    """Probe weasyprint once; cache the result for subsequent calls."""
    if not _WEASYPRINT_STATE["checked"]:
        try:
            from weasyprint import HTML  # noqa: F401
            _WEASYPRINT_STATE["available"] = True
        except (ImportError, OSError) as e:
            _WEASYPRINT_STATE["available"] = False
            _WEASYPRINT_STATE["error"] = f"{type(e).__name__}: {e}"
        _WEASYPRINT_STATE["checked"] = True
    return _WEASYPRINT_STATE["available"], _WEASYPRINT_STATE["error"]


def render_invoice_pdf(invoice: dict[str, Any], out_path: Path) -> None:
    """Render the invoice JSON to a PDF via weasyprint.

    Always writes the sidecar `.json` (ground truth). If weasyprint's native
    libs aren't installed, writes a `.pdf.pending` marker instead of a PDF
    and continues — letting the validation pass complete end-to-end. To get
    real PDFs, install pango/cairo/glib (on macOS: `brew install pango`).
    """
    sidecar_path = out_path.with_suffix(".json")
    sidecar_path.write_text(json.dumps(invoice, indent=2))

    available, err = _weasyprint_available()
    if not available:
        out_path.with_suffix(".pdf.pending").write_text(
            f"PDF rendering skipped: weasyprint unavailable.\n"
            f"Reason: {err}\n"
            f"Fix on macOS: brew install pango\n"
            f"Then re-run: make corpus-ingest-invoices",
        )
        return

    from weasyprint import HTML  # type: ignore[import-not-found]

    html_str = _invoice_html(invoice)
    HTML(string=html_str).write_pdf(str(out_path))


def discover_response_batches() -> list[tuple[Path, Path]]:
    """Pair each response file with its matching spec sidecar."""
    pairs: list[tuple[Path, Path]] = []
    if not RESPONSES_DIR.exists():
        return pairs
    for response in sorted(RESPONSES_DIR.glob("invoice_batch_*.json")):
        batch_id = response.stem  # e.g. invoice_batch_001
        spec = PROMPTS_DIR / f"{batch_id}.spec.json"
        if not spec.exists():
            print(f"WARN: response {response.name} has no matching spec at {spec}",
                  file=sys.stderr)
            continue
        pairs.append((response, spec))
    return pairs


def log_batch(batch_id: str, summary: dict[str, Any]) -> None:
    BATCH_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = BATCH_LOGS_DIR / f"{batch_id}.log.json"
    summary["ingested_at"] = datetime.now().isoformat(timespec="seconds")
    log_path.write_text(json.dumps(summary, indent=2))


def already_ingested(batch_id: str) -> bool:
    return (BATCH_LOGS_DIR / f"{batch_id}.log.json").exists()


# ---------------------------------------------------------------------------
# Per-asset processors
# ---------------------------------------------------------------------------

def process_invoice_batches() -> dict[str, int]:
    pairs = discover_response_batches()
    if not pairs:
        print("No invoice response batches found.")
        return {"batches_seen": 0, "invoices_written": 0, "errors": 0}

    CORPUS_INVOICES.mkdir(parents=True, exist_ok=True)

    stats = {"batches_seen": 0, "batches_skipped": 0,
             "invoices_written": 0, "invoices_failed": 0}

    for response_path, spec_path in pairs:
        batch_id = response_path.stem
        stats["batches_seen"] += 1
        if already_ingested(batch_id):
            stats["batches_skipped"] += 1
            continue

        spec = json.loads(spec_path.read_text())
        try:
            raw = response_path.read_text()
            parsed = extract_json_from_response(raw)
        except Exception as e:
            print(f"[{batch_id}] could not parse JSON: {e}")
            stats["invoices_failed"] += spec["n_invoices"]
            continue

        if not isinstance(parsed, list):
            print(f"[{batch_id}] response is not a JSON array; got {type(parsed).__name__}")
            stats["invoices_failed"] += spec["n_invoices"]
            continue

        if len(parsed) != spec["n_invoices"]:
            print(
                f"[{batch_id}] expected {spec['n_invoices']} invoices, got {len(parsed)}",
            )

        batch_errors: list[dict[str, Any]] = []
        batch_written = 0
        for i, invoice in enumerate(parsed):
            this_spec = spec["specs"][i] if i < len(spec["specs"]) else {}
            expected_persona = this_spec.get("persona_id")
            errors = validate_invoice(invoice, expected_persona) if expected_persona else \
                     validate_invoice(invoice, invoice.get("persona_id", ""))
            if errors:
                batch_errors.append({"index": i, "errors": errors})
                stats["invoices_failed"] += 1
                continue

            # Overwrite the model's `error_injected` with the spec's canonical
            # value. The model sometimes paraphrases or slugifies the error-mode
            # string; the spec is the authoritative source we asked the model
            # to inject, so it's the right ground truth for downstream tests.
            invoice["error_injected"] = this_spec.get("error_injected")

            # Filename uses deterministic spec_index so multiple batches with
            # colliding LLM-generated invoice_ids don't overwrite each other.
            spec_index = this_spec.get("spec_index", i)
            persona_id = this_spec.get("persona_id", invoice.get("persona_id", "UNK"))
            stem = f"{persona_id}_idx{spec_index:04d}"
            pdf_path = CORPUS_INVOICES / f"{stem}.pdf"
            render_invoice_pdf(invoice, pdf_path)
            batch_written += 1

        stats["invoices_written"] += batch_written
        log_batch(batch_id, {
            "n_expected": spec["n_invoices"],
            "n_written": batch_written,
            "errors": batch_errors,
            "spec_hash": hashlib.sha256(spec_path.read_bytes()).hexdigest()[:16],
        })
        print(f"[{batch_id}] {batch_written}/{spec['n_invoices']} invoices written"
              f"{', ' + str(len(batch_errors)) + ' failed' if batch_errors else ''}")

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset",
        choices=["invoices", "emails", "master_data"],
        default="invoices",
    )
    args = parser.parse_args()

    if args.asset == "invoices":
        stats = process_invoice_batches()
        print()
        print(f"Summary: {stats}")
    else:
        print(f"Asset type '{args.asset}' not yet implemented; only 'invoices' is wired up.")


if __name__ == "__main__":
    main()
