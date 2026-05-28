"""Build cross-case context fixtures (vendor master, PO master, GR records,
invoice history, payment status, vendor changes) from the existing synthetic
invoice corpus.

Programmatic (no API cost) — derives consistent context records from each
invoice's ground-truth JSON so the classifier's lookups are well-aligned.
Plus a small set of deliberate signals for fraud / duplicate / payment-status
coverage.

Output: `test_corpus/synthetic/context/*.json`

Usage:
    uv run python scripts/build_context_data.py [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

INVOICES_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"
PERSONAS_PATH = REPO_ROOT / "config" / "personas.yaml"
OUT_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "context"


_TAX_ID_PREFIX_RE = re.compile(
    r"^(?:EIN|VAT(?:\s*ID)?|GSTIN?|PAN|CNPJ|CPF|TIN|TAX\s*ID)[\s:.\-]*",
    re.IGNORECASE,
)


def _norm_tax_id(v: str) -> str:
    return _TAX_ID_PREFIX_RE.sub("", v or "").strip()


def load_personas() -> dict[str, dict]:
    raw = yaml.safe_load(PERSONAS_PATH.read_text())
    return {p["id"]: p for p in raw.get("personas", [])}


PERSONA_TO_COUNTRY = {
    "P001": "US", "P002": "US", "P003": "DE", "P004": "IN", "P005": "BR",
}
PERSONA_TIER = {
    "P001": "strategic", "P002": "tactical", "P003": "strategic",
    "P004": "strategic", "P005": "tactical",
}
PERSONA_CONTRACT_TYPE = {
    "P001": "msa", "P002": "spot", "P003": "msa",
    "P004": "msa", "P005": "spot",
}


def build_vendor_master(invoices: list[dict], personas: dict[str, dict]) -> dict:
    """Build one vendor record per persona, plus a few generic non-persona vendors
    so the corpus can exercise `vendor_master_gap` cases later. Returns a dict
    keyed by normalized tax_id for fast lookup.
    """
    by_persona: dict[str, dict] = {}
    for inv in invoices:
        pid = inv.get("persona_id")
        if not pid or pid in by_persona:
            continue
        header = inv.get("header_fields") or {}
        by_persona[pid] = {
            "id": f"VEN-{pid}",
            "name": header.get("vendor_name", "") or personas.get(pid, {}).get("name", ""),
            "tax_id": _norm_tax_id(header.get("vendor_tax_id", "")),
            "country": PERSONA_TO_COUNTRY.get(pid, "US"),
            "addresses": [header.get("vendor_address", "")],
            "bank_account_last4": f"{ord(pid[-1]) * 137 % 10000:04d}",  # deterministic dummy
            "contract_type": PERSONA_CONTRACT_TYPE.get(pid, "spot"),
            "tier": PERSONA_TIER.get(pid, "tactical"),
            "onboarding_date": "2024-01-15",
            "status": "active",
            "sanctions_check_passed": True,
            "phi_access": False,
            "sbe_classification": "",
            "notes": f"Persona {pid} canonical vendor.",
        }
    return by_persona


def build_po_record(invoice: dict, persona_id: str) -> dict:
    """Build a PO that matches the invoice. For dirty invoices we deliberately
    mis-align so the classifier sees the variance signals.

    The PO `id` matches the invoice's `po_reference` UNLESS the invoice has
    `error_injected == "Occasional PO reference typo (one digit transposed)"`,
    in which case the PO uses the typo-corrected reference (so lookup by the
    typo'd reference returns None and the classifier sees missing_po).
    """
    err = invoice.get("error_injected")
    inv_po_ref = invoice.get("po_reference") or ""

    # For PO_REFERENCE_TYPO: the invoice has a typo; the PO master holds the
    # CORRECT reference (with one digit fixed). Lookup by the typo'd reference
    # will return None — that's the signal.
    if err == "Occasional PO reference typo (one digit transposed)":
        po_id = _fix_typo(inv_po_ref)
    elif err in (
        "Missing PO reference (must be inferred from delivery note)",
    ):
        # Invoice po_reference may be empty; we still record a PO under the
        # delivery-note-implied number — but the classifier won't find it from
        # the empty invoice ref.
        po_id = inv_po_ref or f"PO-INFERRED-{persona_id}-{abs(hash(invoice.get('invoice_id', '')))%99999:05d}"
    else:
        po_id = inv_po_ref or f"PO-DERIVED-{persona_id}"

    line_items = []
    for li in invoice.get("line_items") or []:
        line_items.append({
            "line_no": li.get("line_no", 0),
            "sku": li.get("sku", ""),
            "description": li.get("description", ""),
            "quantity_authorized": float(li.get("quantity") or 0.0),
            "unit_price": float(li.get("unit_price") or 0.0),
            "line_total": float(li.get("line_total") or 0.0),
        })

    total_authorized = float(invoice.get("subtotal") or 0.0)

    return {
        "id": po_id,
        "vendor_id": f"VEN-{persona_id}",
        "line_items": line_items,
        "total_authorized": total_authorized,
        "currency": invoice.get("currency", "USD"),
        "payment_terms": invoice.get("payment_terms", "NET-30"),
        "approver_chain": [
            {"role": "manager", "name": "M. Khan", "approved": True, "approved_at": "2025-12-15T10:00:00Z"},
        ],
        "status": "open",
        "created_date": "2025-12-10",
        "department": "Procurement",
        "is_emergency": False,
        "fx_clause": "spot",
        "fx_rate": None,
    }


def _fix_typo(po_ref: str) -> str:
    """Reverse the canonical 'one digit transposed' typo: swap last two digits."""
    if len(po_ref) < 2:
        return po_ref
    return po_ref[:-2] + po_ref[-1] + po_ref[-2]


def build_goods_receipt(invoice: dict, po_id: str) -> dict | None:
    """Build a GR. Returns None for `missing_goods_receipt` error category.

    Quantity over-delivery / variance is reflected by the GR having quantities
    that differ from the invoice's line-items. Today's corpus doesn't have a
    `MISSING_GR` injected error, so all clean and most dirty cases get a GR.
    """
    line_items = []
    for li in invoice.get("line_items") or []:
        line_items.append({
            "line_no": li.get("line_no", 0),
            "sku": li.get("sku", ""),
            "quantity_received": float(li.get("quantity") or 0.0),
        })

    inv_date = invoice.get("invoice_date") or "2026-01-01"
    try:
        d = datetime.fromisoformat(inv_date).date() - timedelta(days=3)
        receipt_date = d.isoformat()
    except (TypeError, ValueError):
        receipt_date = "2026-01-01"

    return {
        "id": f"GR-{po_id.split('-')[-1]}",
        "po_id": po_id,
        "receipt_date": receipt_date,
        "warehouse": "WH-01",
        "receiver": "G. Sato",
        "line_items": line_items,
    }


def build_invoice_summary(invoice: dict, persona_id: str, sidecar_stem: str) -> dict:
    """Compact summary record used by InvoiceHistoryLookup."""
    return {
        "invoice_id": sidecar_stem,
        "supplier_invoice_number": invoice.get("invoice_id", ""),
        "vendor_id": f"VEN-{persona_id}",
        "po_id": invoice.get("po_reference", ""),
        "total": float(invoice.get("total") or 0.0),
        "currency": invoice.get("currency", "USD"),
        "invoice_date": invoice.get("invoice_date", ""),
        "status": "pending",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sidecars = sorted(INVOICES_DIR.glob("*.json"))
    if not sidecars:
        print(f"No invoice sidecars in {INVOICES_DIR}", file=sys.stderr)
        sys.exit(1)

    personas = load_personas()
    invoices: list[tuple[Path, dict]] = []
    for p in sidecars:
        try:
            invoices.append((p, json.loads(p.read_text())))
        except json.JSONDecodeError as e:
            print(f"  skip {p.name}: {e}")
            continue
    print(f"Loaded {len(invoices)} invoice sidecars")

    # Vendor master — index by id, normalized tax_id, last-6 digits, and name-token-set
    vendor_master = build_vendor_master([inv for _, inv in invoices], personas)

    def _agg_tax_id_key(s: str) -> str:
        # very aggressive: lowercase, only alphanumerics
        return re.sub(r"[^0-9a-z]", "", s.lower())

    def _last6_digits(s: str) -> str:
        digits = re.sub(r"\D", "", s)
        return digits[-6:] if len(digits) >= 6 else digits

    _STOP = {
        "inc", "ltd", "llc", "llp", "the", "and", "co", "corp", "company",
        "group", "holdings", "international",
        "gmbh", "ag", "kg", "ohg",
        "sa", "srl", "ltda", "lda", "spa", "sl", "sas",
        "limited", "plc",
    }

    def _strip_accents(s: str) -> str:
        nfkd = unicodedata.normalize("NFKD", s or "")
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    def _name_token_set(s: str) -> list[str]:
        norm = _strip_accents(s).lower()
        tokens = re.findall(r"\w+", norm)
        return sorted(t for t in tokens if len(t) > 2 and t not in _STOP)

    vendor_data = {
        "by_id": {v["id"]: v for v in vendor_master.values()},
        "by_tax_id": {v["tax_id"]: v for v in vendor_master.values() if v["tax_id"]},
        "by_tax_id_aggressive": {
            _agg_tax_id_key(v["tax_id"]): v for v in vendor_master.values() if v["tax_id"]
        },
        "by_tax_id_last6": {
            _last6_digits(v["tax_id"]): v
            for v in vendor_master.values()
            if v["tax_id"] and len(_last6_digits(v["tax_id"])) == 6
        },
        "by_name_tokens": [
            {"tokens": _name_token_set(v["name"]), "vendor": v}
            for v in vendor_master.values()
            if v["name"]
        ],
    }
    (OUT_DIR / "vendor_master.json").write_text(json.dumps(vendor_data, indent=2))
    print(f"  vendor_master.json: {len(vendor_master)} vendors "
          f"({len(vendor_data['by_tax_id_last6'])} indexed by last6)")

    # PO master + GR records — keyed by po_id
    po_master: dict[str, dict] = {}
    gr_records: dict[str, dict] = {}
    summaries: list[dict] = []
    po_invoice_counts: dict[str, int] = defaultdict(int)

    for sidecar, inv in invoices:
        pid = inv.get("persona_id") or "P001"
        po = build_po_record(inv, pid)
        po_id = po["id"]
        # If multiple invoices share po_id (shouldn't happen often in our corpus
        # but can on collisions), keep first wins.
        if po_id and po_id not in po_master:
            po_master[po_id] = po
            gr = build_goods_receipt(inv, po_id)
            if gr is not None:
                gr_records[po_id] = gr
        summaries.append(build_invoice_summary(inv, pid, sidecar.stem))
        if po_id:
            po_invoice_counts[po_id] += 1

    (OUT_DIR / "po_master.json").write_text(json.dumps(po_master, indent=2))
    (OUT_DIR / "goods_receipts.json").write_text(json.dumps(gr_records, indent=2))
    print(f"  po_master.json: {len(po_master)} POs")
    print(f"  goods_receipts.json: {len(gr_records)} GRs")

    # Payment status — random subset of POs marked fully paid
    paid_pos = set(rng.sample(list(po_master.keys()), min(5, len(po_master))))
    payment_status: dict[str, dict] = {}
    for po_id, po in po_master.items():
        is_paid = po_id in paid_pos
        payment_status[po_id] = {
            "po_id": po_id,
            "total_authorized": po["total_authorized"],
            "total_invoiced": po["total_authorized"] if is_paid else 0.0,
            "total_paid": po["total_authorized"] if is_paid else 0.0,
            "n_invoices": 1 if is_paid else 0,
            "last_payment_date": "2026-01-15" if is_paid else None,
        }
    (OUT_DIR / "payment_status.json").write_text(json.dumps(payment_status, indent=2))
    print(f"  payment_status.json: {len(payment_status)} entries ({len(paid_pos)} fully paid)")

    # Invoice history — base summaries + deliberate duplicates + split clusters
    # Pick 10 random invoices and mark each as having a prior occurrence
    dup_invoices = rng.sample(summaries, min(10, len(summaries)))
    duplicate_extras: list[dict] = []
    for s in dup_invoices:
        # Add a "prior" summary with the SAME supplier_invoice_number but
        # different invoice_id, dated 30 days earlier
        prior = dict(s)
        prior["invoice_id"] = s["invoice_id"] + "_PRIOR"
        prior["status"] = "paid"
        try:
            d = datetime.fromisoformat(s["invoice_date"]).date() - timedelta(days=30)
            prior["invoice_date"] = d.isoformat()
        except ValueError:
            pass
        duplicate_extras.append(prior)

    # Split-invoice clusters: pick 5 POs and add 3 synthetic prior invoices
    # under the same po_id within the last 14 days. Use existing PO totals
    # split into roughly equal fractions to look split-suspicious.
    split_extras: list[dict] = []
    chosen_pos = rng.sample(list(po_master.keys()), min(5, len(po_master)))
    for po_id in chosen_pos:
        po = po_master[po_id]
        for i in range(3):
            split_extras.append({
                "invoice_id": f"SPLIT-{po_id}-{i+1}",
                "supplier_invoice_number": f"INV-SPLIT-{po_id}-{i+1}",
                "vendor_id": po["vendor_id"],
                "po_id": po_id,
                "total": round(po["total_authorized"] * 0.3, 2),
                "currency": po["currency"],
                "invoice_date": (date.today() - timedelta(days=rng.randint(1, 14))).isoformat(),
                "status": "pending",
            })

    # Split outputs: corpus eval reads only `invoice_history.json` (clean base);
    # golden harness opt-in to `golden_history_signals.json` for duplicate/fraud cases.
    (OUT_DIR / "invoice_history.json").write_text(json.dumps(summaries, indent=2))
    golden_signals = duplicate_extras + split_extras
    (OUT_DIR / "golden_history_signals.json").write_text(json.dumps(golden_signals, indent=2))
    print(
        f"  invoice_history.json: {len(summaries)} base summaries (clean)",
    )
    print(
        f"  golden_history_signals.json: {len(golden_signals)} entries "
        f"({len(duplicate_extras)} duplicate + {len(split_extras)} split) — "
        f"loaded only by golden harness",
    )

    # Vendor changes — randomly pick a few vendors with bank-detail changes
    changing = rng.sample(list(vendor_master.values()), min(2, len(vendor_master)))
    vendor_changes = [
        {
            "vendor_id": v["id"],
            "field": "bank_account",
            "changed_on": (date.today() - timedelta(days=rng.randint(1, 25))).isoformat(),
            "note": "Bank account updated by supplier email request.",
        }
        for v in changing
    ]
    (OUT_DIR / "vendor_changes.json").write_text(json.dumps(vendor_changes, indent=2))
    print(f"  vendor_changes.json: {len(vendor_changes)} change events")

    # Manifest
    manifest = {
        "seed": args.seed,
        "n_invoices": len(invoices),
        "n_vendors": len(vendor_master),
        "n_pos": len(po_master),
        "n_grs": len(gr_records),
        "n_summaries": len(summaries),
        "n_duplicates": len(duplicate_extras),
        "n_split": len(split_extras),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (OUT_DIR / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    print()
    print("Done. Files written to:", OUT_DIR)


if __name__ == "__main__":
    main()
