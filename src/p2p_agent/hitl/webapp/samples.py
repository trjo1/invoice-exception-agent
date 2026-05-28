"""Curated sample invoices for the /demo gallery.

Hand-picked one invoice per persona-error pattern so the demo shows the full
range without you having to remember which file in `test_corpus/synthetic/`
demonstrates which condition.

Each sample also carries an emoji-flag `region` hint and an `expected_tier`
label so the gallery cards can color-code at a glance. Other invoice metadata
(vendor name, total, currency, tax breakdown) is loaded from the JSON sidecar
at render time via `load_sample_metadata()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SamplePdf:
    """Curated reference into the synthetic corpus."""

    sample_id: str           # short stable key used in the URL form
    case_id: str             # corpus filename without extension
    label: str               # human-readable label for the gallery card
    expected_signal: str     # what we expect the agent to do — sets demo expectations
    region: str              # "🇺🇸 US" / "🇩🇪 EU" / "🇮🇳 India" / "🇧🇷 Brazil" — for the badge
    expected_tier: str       # "tier-1" | "tier-2" | "tier-3" — for badge color


CORPUS_DIR = Path("test_corpus/synthetic/invoices")


# Order matters — first entry is the default selected.
SAMPLES: list[SamplePdf] = [
    SamplePdf(
        sample_id="clean_us",
        case_id="P001_idx0002",
        label="Clean US invoice",
        expected_signal="Tier 1 auto-pass. No exception expected.",
        region="🇺🇸 US",
        expected_tier="tier-1",
    ),
    SamplePdf(
        sample_id="multipage_us",
        case_id="P001_idx0000",
        label="Multi-page US invoice",
        expected_signal="Line items span pages. Extraction edge case; class typically `none`.",
        region="🇺🇸 US",
        expected_tier="tier-1",
    ),
    SamplePdf(
        sample_id="po_typo_us",
        case_id="P001_idx0003",
        label="US invoice — PO reference typo",
        expected_signal="May trigger missing_po; depends on PO lookup match.",
        region="🇺🇸 US",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="missing_po_us",
        case_id="P002_idx0021",
        label="US invoice — PO reference missing",
        expected_signal="Should classify as missing_po → request_missing_po_from_supplier.",
        region="🇺🇸 US SMB",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="missing_tax_us",
        case_id="P002_idx0016",
        label="US invoice — tax line absent (in-state)",
        expected_signal="Should classify as tax_field_mismatch (or none if extractor handles).",
        region="🇺🇸 US SMB",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="fx_edge_eu",
        case_id="P003_idx0020",
        label="EUR invoice billed against USD PO",
        expected_signal="Should classify as cross_currency_mismatch → escalate_for_fx_review.",
        region="🇩🇪 EU",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="vat_missing_eu",
        case_id="P003_idx0089",
        label="Intra-EU shipment — VAT field missing",
        expected_signal="Should classify as tax_field_mismatch → request_supplier_correction.",
        region="🇩🇪 EU",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="gst_misrouted_in",
        case_id="P004_idx0060",
        label="India invoice — GST misrouted IGST/CGST/SGST",
        expected_signal="Should classify as tax_field_mismatch (jurisdiction-specific).",
        region="🇮🇳 India",
        expected_tier="tier-2",
    ),
    SamplePdf(
        sample_id="hsn_format_in",
        case_id="P004_idx0006",
        label="India invoice — HSN code formatting quirk",
        expected_signal="Extraction edge case; class typically `none`.",
        region="🇮🇳 India",
        expected_tier="tier-1",
    ),
    SamplePdf(
        sample_id="multi_tax_br",
        case_id="P005_idx0008",
        label="Brazil invoice — ICMS + IPI + PIS/COFINS",
        expected_signal="Tax complexity; should resolve cleanly via extractor.",
        region="🇧🇷 Brazil",
        expected_tier="tier-1",
    ),
]


def find_sample(sample_id: str) -> SamplePdf | None:
    for s in SAMPLES:
        if s.sample_id == sample_id:
            return s
    return None


def resolve_pdf(sample: SamplePdf, corpus_dir: Path | None = None) -> Path:
    """Resolve the on-disk PDF path for a sample. Returns the file path or raises."""
    base = corpus_dir or CORPUS_DIR
    pdf = base / f"{sample.case_id}.pdf"
    if not pdf.exists():
        raise FileNotFoundError(f"Sample PDF missing: {pdf}")
    return pdf


def _resolve_sidecar(sample: SamplePdf, corpus_dir: Path | None = None) -> Path:
    base = corpus_dir or CORPUS_DIR
    return base / f"{sample.case_id}.json"


def load_sample_metadata(
    sample: SamplePdf,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """Read the JSON sidecar and return a compact metadata dict for gallery cards.

    Returns: {vendor_name, invoice_id, currency, total, line_count}.

    Missing or unreadable sidecar returns an empty dict — the template falls
    back to showing just the sample's static fields (label, expected_signal,
    region). Never raises.
    """
    path = _resolve_sidecar(sample, corpus_dir)
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    header = data.get("header_fields") or {}
    return {
        "vendor_name": header.get("vendor_name") or "",
        "invoice_id": data.get("invoice_id") or "",
        "currency": data.get("currency") or "",
        "total": data.get("total"),
        "line_count": len(data.get("line_items") or []),
    }
