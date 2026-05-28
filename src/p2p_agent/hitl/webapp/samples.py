"""Curated sample invoices for the /demo browse page and scenarios dropdown.

20 hand-picked synthetic invoices across 5 personas (US large, US SMB, EU,
India, Brazil). 10 are flagged ``curated=True`` and surface in the
"Run a curated scenario" dropdown on /demo with their full ``label`` /
``expected_signal`` metadata. The other 10 are "neutral" entries that only
appear in /demo/browse — they have no label hints so the browse picker
feels like a real production run with unknown outcomes.

Both lists share the same SamplePdf shape so the same /demo/run-sample-streaming
route runs them identically.
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
    label: str               # human-readable label for the dropdown
    expected_signal: str = ""  # what we expect the agent to do — sets demo expectations
    region: str = ""         # emoji flag + short region name (e.g. "🇺🇸 US")
    expected_tier: str = ""  # "tier-1" / "tier-2" / "tier-3" for the badge color
    curated: bool = False    # True → appears in the curated-scenario dropdown


CORPUS_DIR = Path("test_corpus/synthetic/invoices")


# Full library — order is the display order on /demo/browse.
# The 10 entries with ``curated=True`` are ALSO surfaced in the dropdown.
SAMPLES: list[SamplePdf] = [
    # ─── Persona 1: US large (Global Tech Components Inc.) ───
    SamplePdf(
        sample_id="clean_us",
        case_id="P001_idx0002",
        label="Clean US invoice — auto-pass expected",
        expected_signal="Tier 1 auto-pass. Class: none.",
        region="🇺🇸 US",
        expected_tier="tier-1",
        curated=True,
    ),
    SamplePdf(
        sample_id="multipage_us",
        case_id="P001_idx0000",
        label="Multi-page US invoice — line items span pages",
        expected_signal="Extraction edge case; class typically none.",
        region="🇺🇸 US",
        expected_tier="tier-1",
        curated=True,
    ),
    SamplePdf(
        sample_id="po_typo_us",
        case_id="P001_idx0003",
        label="US invoice with PO reference typo",
        expected_signal="May trigger missing_po; depends on PO lookup match.",
        region="🇺🇸 US",
        expected_tier="tier-2",
        curated=True,
    ),
    SamplePdf(
        sample_id="us_p1_007",
        case_id="P001_idx0007",
        label="Global Tech Components Inc. — INV 20260215-001",
        region="🇺🇸 US",
    ),
    SamplePdf(
        sample_id="us_p1_013",
        case_id="P001_idx0013",
        label="Global Tech Components Inc. — INV 20260228-001",
        region="🇺🇸 US",
    ),
    # ─── Persona 2: US SMB (Maple Logistics SMB) ───
    SamplePdf(
        sample_id="missing_po_us",
        case_id="P002_idx0021",
        label="US invoice with PO reference missing",
        expected_signal="Should classify as missing_po → request_missing_po_from_supplier.",
        region="🇺🇸 US SMB",
        expected_tier="tier-2",
        curated=True,
    ),
    SamplePdf(
        sample_id="missing_tax_us",
        case_id="P002_idx0016",
        label="US invoice with tax line absent (in-state)",
        expected_signal="Should classify as tax_field_mismatch or none if extractor handles.",
        region="🇺🇸 US SMB",
        expected_tier="tier-2",
        curated=True,
    ),
    SamplePdf(
        sample_id="us_p2_001",
        case_id="P002_idx0001",
        label="Maple Logistics SMB — INV 20260328-001",
        region="🇺🇸 US SMB",
    ),
    SamplePdf(
        sample_id="us_p2_014",
        case_id="P002_idx0014",
        label="Maple Logistics SMB — INV 20260127-001",
        region="🇺🇸 US SMB",
    ),
    # ─── Persona 3: EU (Frankfurt Industriebedarf GmbH) ───
    SamplePdf(
        sample_id="fx_edge_eu",
        case_id="P003_idx0020",
        label="EUR-denominated invoice with USD-buyer PO",
        expected_signal="Should classify as cross_currency_mismatch → escalate_for_fx_review.",
        region="🇩🇪 EU",
        expected_tier="tier-3",
        curated=True,
    ),
    SamplePdf(
        sample_id="vat_missing_eu",
        case_id="P003_idx0089",
        label="Intra-EU shipment, VAT field missing on non-DE buyer",
        expected_signal="Should classify as tax_field_mismatch → request_supplier_correction.",
        region="🇩🇪 EU",
        expected_tier="tier-2",
        curated=True,
    ),
    SamplePdf(
        sample_id="eu_p3_005",
        case_id="P003_idx0005",
        label="Frankfurt Industriebedarf GmbH — INV 20260101-001",
        region="🇩🇪 EU",
    ),
    SamplePdf(
        sample_id="eu_p3_030",
        case_id="P003_idx0030",
        label="Frankfurt Industriebedarf GmbH — INV 20260408-001",
        region="🇩🇪 EU",
    ),
    # ─── Persona 4: India (Mumbai Materials Co.) ───
    SamplePdf(
        sample_id="gst_misrouted_in",
        case_id="P004_idx0060",
        label="India invoice — GST misrouted between IGST and CGST/SGST",
        expected_signal="Should classify as tax_field_mismatch (jurisdiction-specific).",
        region="🇮🇳 India",
        expected_tier="tier-2",
        curated=True,
    ),
    SamplePdf(
        sample_id="hsn_format_in",
        case_id="P004_idx0006",
        label="India invoice — HSN code formatting inconsistency",
        expected_signal="Extraction edge case; class typically none.",
        region="🇮🇳 India",
        expected_tier="tier-1",
        curated=True,
    ),
    SamplePdf(
        sample_id="in_p4_019",
        case_id="P004_idx0019",
        label="Mumbai Materials Co. — INV 20260427-001",
        region="🇮🇳 India",
    ),
    SamplePdf(
        sample_id="in_p4_023",
        case_id="P004_idx0023",
        label="Mumbai Materials Co. — INV 20260118-001",
        region="🇮🇳 India",
    ),
    # ─── Persona 5: Brazil (São Paulo Componentes Ltda.) ───
    SamplePdf(
        sample_id="multi_tax_br",
        case_id="P005_idx0008",
        label="Brazil invoice — ICMS + IPI + PIS/COFINS stacked tax",
        expected_signal="Tax complexity; should resolve cleanly via extractor.",
        region="🇧🇷 Brazil",
        expected_tier="tier-1",
        curated=True,
    ),
    SamplePdf(
        sample_id="br_p5_004",
        case_id="P005_idx0004",
        label="São Paulo Componentes Ltda. — INV 20260227-001",
        region="🇧🇷 Brazil",
    ),
    SamplePdf(
        sample_id="br_p5_009",
        case_id="P005_idx0009",
        label="São Paulo Componentes Ltda. — INV 20260111-001",
        region="🇧🇷 Brazil",
    ),
]


def curated_samples() -> list[SamplePdf]:
    """Return the subset of SAMPLES that surface in the scenarios dropdown."""
    return [s for s in SAMPLES if s.curated]


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
    sample: SamplePdf, corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """Read the JSON sidecar for a sample and return demo-relevant fields.

    Returns a flat dict with: ``vendor_name``, ``invoice_id``, ``currency``,
    ``total``, ``line_count``. Missing fields fall back to ``None`` / 0 so the
    template can render defensively.

    Sidecar absence is non-fatal — returns a dict with all fields as ``None``.
    """
    path = _resolve_sidecar(sample, corpus_dir)
    if not path.exists():
        return {
            "vendor_name": None,
            "invoice_id": None,
            "currency": None,
            "total": None,
            "line_count": 0,
        }
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — defensive: don't break /demo on bad JSON
        return {
            "vendor_name": None,
            "invoice_id": None,
            "currency": None,
            "total": None,
            "line_count": 0,
        }
    header = data.get("header_fields") or {}
    return {
        "vendor_name": header.get("vendor_name"),
        "invoice_id": data.get("invoice_id"),
        "currency": data.get("currency"),
        "total": data.get("total"),
        "line_count": len(data.get("line_items") or []),
    }
