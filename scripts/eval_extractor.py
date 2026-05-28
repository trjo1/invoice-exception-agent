"""Corpus-driven evaluation of the invoice extractor.

Runs `extract_invoice` against each invoice PDF, diffs the result against
the matching ground-truth JSON sidecar, reports per-field accuracy +
total cost.

Field-aware matchers:
- Exact: po_reference, currency, vendor_tax_id, invoice_id, sku
- ISO-date: invoice_date (parses both sides, compares date components)
- Money (±1% rel tol): subtotal, total, unit_price, line_total, tax.amount
- Normalized: payment_terms (uppercase, spaces ↔ dashes), quantity (numeric)
- Fuzzy: vendor_name, buyer_name, addresses (token-set ratio via rapidfuzz)
- Structural: line_items (count + per-line match by line_no), tax (count + per-line)

Usage:
    uv run python scripts/eval_extractor.py                # 100-invoice random sample
    uv run python scripts/eval_extractor.py --sample 25
    uv run python scripts/eval_extractor.py --full         # all 490
    uv run python scripts/eval_extractor.py --persona P003 # filter by persona

Cost reference: ~$0.001 per PDF on DeepSeek V4-Flash.
100 samples ≈ $0.10. Full 490 ≈ $0.50.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from p2p_agent.extractors import ExtractorError, extract_invoice  # noqa: E402
from p2p_agent.llm.client import ModelClient  # noqa: E402

CORPUS_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"
LOG_PATH = REPO_ROOT / "logs" / "llm_calls.jsonl"


# ---------------------------------------------------------------------------
# Per-field matchers
# ---------------------------------------------------------------------------

def _norm_text(v: Any) -> str:
    return str(v or "").strip()


def _exact(a: Any, b: Any) -> bool:
    return _norm_text(a) == _norm_text(b)


def _parse_iso_date(value: Any) -> date | None:
    s = _norm_text(value)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _date_eq(a: Any, b: Any) -> bool:
    da, db = _parse_iso_date(a), _parse_iso_date(b)
    if da is None or db is None:
        return _exact(a, b)
    return da == db


def _money_eq(a: Any, b: Any, rel_tol: float = 0.01, abs_tol: float = 0.01) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if fa == fb:
        return True
    if abs(fa - fb) <= abs_tol:
        return True
    denom = max(abs(fa), abs(fb), 1.0)
    return abs(fa - fb) / denom <= rel_tol


def _norm_payment_terms(v: Any) -> str:
    s = _norm_text(v).upper().replace(" ", "-")
    s = re.sub(r"-+", "-", s)
    return s


def _payment_terms_eq(a: Any, b: Any) -> bool:
    return _norm_payment_terms(a) == _norm_payment_terms(b)


# Common tax-id label prefixes that appear on invoices but aren't part of the
# ID value itself. The ground truth sometimes includes the prefix ("EIN: 98-...")
# and sometimes doesn't; the extractor strips them by default. Normalize both
# sides before comparing so we measure the actual ID value.
_TAX_ID_PREFIX_RE = re.compile(
    r"^(?:EIN|VAT(?:\s*ID)?|GSTIN?|PAN|CNPJ|CPF|TIN|TAX\s*ID)[\s:.\-]*",
    re.IGNORECASE,
)


def _norm_tax_id(v: Any) -> str:
    s = _norm_text(v)
    s = _TAX_ID_PREFIX_RE.sub("", s).strip()
    return s


def _tax_id_eq(a: Any, b: Any) -> bool:
    return _norm_tax_id(a) == _norm_tax_id(b)


def _norm_words(text: str) -> set[str]:
    tokens = re.findall(r"\w+", text.lower())
    return {t for t in tokens if len(t) > 1}


def _fuzzy_eq(a: Any, b: Any, threshold: float = 0.7) -> bool:
    """Jaccard token similarity. Good for vendor names + addresses."""
    sa, sb = _norm_words(_norm_text(a)), _norm_words(_norm_text(b))
    if not sa and not sb:
        return True
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    union = len(sa | sb)
    return (inter / union) >= threshold


# ---------------------------------------------------------------------------
# Field-by-field diff
# ---------------------------------------------------------------------------

@property
def scalar_fields() -> list[tuple[str, Any]]:
    return []


def diff_invoice(
    actual: dict[str, Any],
    truth: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return a dict {field_path: {"actual": ..., "truth": ..., "match": bool}}."""
    out: dict[str, dict[str, Any]] = {}

    def record(path: str, a: Any, t: Any, match: bool) -> None:
        out[path] = {"actual": a, "truth": t, "match": match}

    record("po_reference", actual.get("po_reference"), truth.get("po_reference"),
           _exact(actual.get("po_reference"), truth.get("po_reference")))
    record("invoice_date", actual.get("invoice_date"), truth.get("invoice_date"),
           _date_eq(actual.get("invoice_date"), truth.get("invoice_date")))
    record("currency", actual.get("currency"), truth.get("currency"),
           _exact(actual.get("currency"), truth.get("currency")))
    record("payment_terms", actual.get("payment_terms"), truth.get("payment_terms"),
           _payment_terms_eq(actual.get("payment_terms"), truth.get("payment_terms")))
    record("subtotal", actual.get("subtotal"), truth.get("subtotal"),
           _money_eq(actual.get("subtotal"), truth.get("subtotal")))
    record("total", actual.get("total"), truth.get("total"),
           _money_eq(actual.get("total"), truth.get("total")))

    a_header = actual.get("header_fields") or {}
    t_header = truth.get("header_fields") or {}
    record(
        "header_fields.vendor_name",
        a_header.get("vendor_name"), t_header.get("vendor_name"),
        _fuzzy_eq(a_header.get("vendor_name"), t_header.get("vendor_name")),
    )
    record(
        "header_fields.vendor_tax_id",
        a_header.get("vendor_tax_id"), t_header.get("vendor_tax_id"),
        _tax_id_eq(a_header.get("vendor_tax_id"), t_header.get("vendor_tax_id")),
    )
    record(
        "header_fields.vendor_address",
        a_header.get("vendor_address"), t_header.get("vendor_address"),
        _fuzzy_eq(a_header.get("vendor_address"), t_header.get("vendor_address")),
    )
    record(
        "header_fields.buyer_name",
        a_header.get("buyer_name"), t_header.get("buyer_name"),
        _fuzzy_eq(a_header.get("buyer_name"), t_header.get("buyer_name")),
    )
    record(
        "header_fields.buyer_address",
        a_header.get("buyer_address"), t_header.get("buyer_address"),
        _fuzzy_eq(a_header.get("buyer_address"), t_header.get("buyer_address")),
    )
    record(
        "header_fields.buyer_po_contact",
        a_header.get("buyer_po_contact"), t_header.get("buyer_po_contact"),
        _fuzzy_eq(a_header.get("buyer_po_contact"), t_header.get("buyer_po_contact")),
    )

    # line_items
    a_lines = actual.get("line_items") or []
    t_lines = truth.get("line_items") or []
    record(
        "line_items.count",
        len(a_lines), len(t_lines),
        len(a_lines) == len(t_lines),
    )
    truth_by_no = {li.get("line_no"): li for li in t_lines if isinstance(li, dict)}
    line_all_match = True
    for li in a_lines:
        if not isinstance(li, dict):
            line_all_match = False
            continue
        no = li.get("line_no")
        match = truth_by_no.get(no)
        if not match:
            line_all_match = False
            continue
        if not (
            _exact(li.get("sku"), match.get("sku"))
            and _money_eq(li.get("quantity"), match.get("quantity"))
            and _money_eq(li.get("unit_price"), match.get("unit_price"))
            and _money_eq(li.get("line_total"), match.get("line_total"))
        ):
            line_all_match = False
    record("line_items.all_match", line_all_match, True, line_all_match)

    # tax
    a_tax = actual.get("tax") or []
    t_tax = truth.get("tax") or []
    record("tax.count", len(a_tax), len(t_tax), len(a_tax) == len(t_tax))
    tax_all_match = True
    for at, tt in zip(sorted(a_tax, key=lambda x: x.get("jurisdiction", "")),
                      sorted(t_tax, key=lambda x: x.get("jurisdiction", ""))):
        if not isinstance(at, dict) or not isinstance(tt, dict):
            tax_all_match = False
            continue
        if not (
            _exact(at.get("jurisdiction"), tt.get("jurisdiction"))
            and _money_eq(at.get("rate"), tt.get("rate"), rel_tol=0.02)
            and _money_eq(at.get("amount"), tt.get("amount"))
        ):
            tax_all_match = False
    record("tax.all_match", tax_all_match if t_tax or a_tax else True,
           True, tax_all_match if t_tax or a_tax else True)

    return out


# ---------------------------------------------------------------------------
# Eval driver
# ---------------------------------------------------------------------------

def discover_invoices(persona: str | None = None) -> list[Path]:
    pattern = f"{persona}_*.json" if persona else "*.json"
    return sorted(p for p in CORPUS_DIR.glob(pattern) if not p.name.startswith("_"))


def pdf_for_sidecar(sidecar: Path) -> Path:
    return sidecar.with_suffix(".pdf")


async def evaluate(
    sidecars: list[Path],
    concurrency: int,
) -> dict[str, Any]:
    client = ModelClient()
    semaphore = asyncio.Semaphore(concurrency)

    field_totals: dict[str, list[bool]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    per_persona_correct: dict[str, list[bool]] = defaultdict(list)
    all_pass = 0
    processed = 0

    async def process(sidecar: Path) -> None:
        nonlocal all_pass, processed
        truth = json.loads(sidecar.read_text())
        pdf = pdf_for_sidecar(sidecar)
        if not pdf.exists():
            failures.append({"path": sidecar.name, "error": "PDF missing"})
            return

        async with semaphore:
            try:
                extracted = await extract_invoice(
                    pdf_path=pdf,
                    client=client,
                    case_id=sidecar.stem,
                )
            except ExtractorError as e:
                failures.append({"path": pdf.name, "error": str(e)[:200]})
                return
            except Exception as e:  # noqa: BLE001
                failures.append({"path": pdf.name, "error": f"{type(e).__name__}: {e}"})
                return

        diff = diff_invoice(extracted.model_dump(), truth)
        for field, info in diff.items():
            field_totals[field].append(bool(info["match"]))

        all_match = all(info["match"] for info in diff.values())
        if all_match:
            all_pass += 1
        per_persona_correct[truth.get("persona_id", "?")].append(all_match)
        processed += 1

    await asyncio.gather(*(process(s) for s in sidecars))

    return {
        "field_totals": field_totals,
        "failures": failures,
        "all_pass": all_pass,
        "processed": processed,
        "per_persona": per_persona_correct,
    }


def cost_since(start_ts_iso_local: str) -> tuple[float, int]:
    if not LOG_PATH.exists():
        return 0.0, 0
    total = 0.0
    count = 0
    with LOG_PATH.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp", "")
            if ts and ts >= start_ts_iso_local and rec.get("task") == "invoice_extraction":
                total += float(rec.get("cost_usd", 0))
                count += 1
    return total, count


def print_report(sidecars: list[Path], summary: dict[str, Any], start_ts_iso_local: str) -> None:
    print()
    print(f"Evaluated {len(sidecars)} invoices ({summary['processed']} processed; "
          f"{len(summary['failures'])} failed before diff).")

    print()
    print("Per-field accuracy:")
    field_totals = summary["field_totals"]
    print(f"  {'field':38s}  {'samples':>8s}  {'correct':>8s}  {'accuracy':>9s}")
    field_order = [
        "po_reference", "invoice_date", "currency", "payment_terms",
        "subtotal", "total",
        "header_fields.vendor_name", "header_fields.vendor_tax_id",
        "header_fields.vendor_address", "header_fields.buyer_name",
        "header_fields.buyer_address", "header_fields.buyer_po_contact",
        "line_items.count", "line_items.all_match",
        "tax.count", "tax.all_match",
    ]
    for field in field_order:
        results = field_totals.get(field, [])
        if not results:
            continue
        correct = sum(1 for r in results if r)
        n = len(results)
        print(f"  {field:38s}  {n:>8d}  {correct:>8d}  {correct/n:>9.2%}")

    print()
    if summary["processed"]:
        print(f"All-field-correct rate: {summary['all_pass']}/{summary['processed']} = "
              f"{summary['all_pass'] / summary['processed']:.2%}")

    print()
    print("Per-persona all-field-correct rate:")
    per_persona = summary["per_persona"]
    for persona in sorted(per_persona):
        results = per_persona[persona]
        if not results:
            continue
        correct = sum(1 for r in results if r)
        n = len(results)
        print(f"  {persona}:  {correct}/{n} = {correct / n:.2%}")

    total_cost, n_calls = cost_since(start_ts_iso_local)
    print()
    print(f"API cost for this run: ${total_cost:.4f} across {n_calls} extraction calls")

    if summary["failures"]:
        print()
        print(f"Failures ({len(summary['failures'])}):")
        for f in summary["failures"][:8]:
            print(f"  {f['path']}: {f['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--persona", help="Filter by persona id (e.g. P003)")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    all_sidecars = discover_invoices(persona=args.persona)
    if not all_sidecars:
        print(f"No invoices found in {CORPUS_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.full or args.sample >= len(all_sidecars):
        sidecars = all_sidecars
    else:
        rng = random.Random(args.seed)
        sidecars = rng.sample(all_sidecars, args.sample)

    print(f"Plan: extract from {len(sidecars)} PDFs (persona={args.persona or 'all'}, "
          f"concurrency={args.concurrency})")

    start_ts_iso_local = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    summary = asyncio.run(evaluate(sidecars, args.concurrency))
    print_report(sidecars, summary, start_ts_iso_local)


if __name__ == "__main__":
    main()
