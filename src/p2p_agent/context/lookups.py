"""JSON-backed lookups for cross-case context.

In the test phase these read from `test_corpus/synthetic/context/*.json`.
In production the same interfaces front SAP OData (vendor master via
`API_BUSINESS_PARTNER`, PO via `API_PURCHASEORDER_PROCESS_SRV`, etc.) —
swap-in is a backend change, the lookup API stays the same.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from p2p_agent.models.context import (
    GoodsReceipt,
    InvoiceSummary,
    POPaymentStatus,
    PORecord,
    VendorChangeEvent,
    VendorRecord,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTEXT_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "context"


_TAX_ID_PREFIX_RE = re.compile(
    r"^(?:EIN|VAT(?:\s*ID)?|GSTIN?|PAN|CNPJ|CPF|TIN|TAX\s*ID)[\s:.\-]*",
    re.IGNORECASE,
)


def _norm_tax_id(v: str | None) -> str:
    return _TAX_ID_PREFIX_RE.sub("", v or "").strip()


def _safe_load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


_AGG_TAX_RE = re.compile(r"[^0-9a-z]")
_DIGITS_RE = re.compile(r"\D")


def _agg_tax_id_key(s: str) -> str:
    return _AGG_TAX_RE.sub("", (s or "").lower())


def _last6_digits(s: str) -> str:
    digits = _DIGITS_RE.sub("", s or "")
    return digits[-6:] if len(digits) >= 6 else digits


_NAME_STOP_WORDS: frozenset[str] = frozenset({
    # English / generic
    "inc", "ltd", "llc", "llp", "the", "and", "co", "corp", "company",
    "group", "holdings", "international",
    # German
    "gmbh", "ag", "kg", "ohg",
    # Romance / LatAm
    "sa", "srl", "ltda", "lda", "spa", "sl", "sas",
    # Common English suffixes
    "limited", "plc",
})


def _strip_accents(s: str) -> str:
    """NFD-normalize then drop combining marks — 'São' → 'Sao', 'Müller' → 'Muller'."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _name_tokens(s: str) -> set[str]:
    norm = _strip_accents(s).lower()
    tokens = re.findall(r"\w+", norm)
    return {t for t in tokens if len(t) > 2 and t not in _NAME_STOP_WORDS}


class VendorMasterLookup:
    """Lookup vendors by id, by normalized tax_id, or via fallback chain.

    Resolution order:
      1. exact normalized tax_id (strip prefix label like "EIN:")
      2. aggressively normalized tax_id (only alphanumerics, lowercase)
      3. last 6 digits of tax_id
      4. fuzzy name match (Jaccard ≥ 0.7 on token sets)

    Each stage tightens precision; we walk from strictest to loosest.
    """

    def __init__(self, context_dir: Path | None = None) -> None:
        self._path = (context_dir or DEFAULT_CONTEXT_DIR) / "vendor_master.json"
        self._data: dict[str, Any] = _safe_load_json(self._path) or {}

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    def get_by_id(self, vendor_id: str) -> VendorRecord | None:
        rec = (self._data.get("by_id") or {}).get(vendor_id)
        return VendorRecord.model_validate(rec) if rec else None

    def get_by_tax_id(self, tax_id: str) -> VendorRecord | None:
        if not tax_id:
            return None

        # Stage 1: exact normalized
        norm = _norm_tax_id(tax_id)
        rec = (self._data.get("by_tax_id") or {}).get(norm)
        if rec:
            return VendorRecord.model_validate(rec)

        # Stage 2: aggressively normalized (handles separator / case drift)
        agg = _agg_tax_id_key(tax_id)
        rec = (self._data.get("by_tax_id_aggressive") or {}).get(agg)
        if rec:
            return VendorRecord.model_validate(rec)

        # Stage 3: last-6-digit fallback (handles label-prefix and minor reformat)
        last6 = _last6_digits(tax_id)
        if last6 and len(last6) == 6:
            rec = (self._data.get("by_tax_id_last6") or {}).get(last6)
            if rec:
                return VendorRecord.model_validate(rec)

        return None

    def get_by_name(self, vendor_name: str, *, threshold: float = 0.6) -> VendorRecord | None:
        """Fuzzy name match (Jaccard on token sets). Returns the best match
        above `threshold`, or None.
        """
        if not vendor_name:
            return None
        query_tokens = _name_tokens(vendor_name)
        if not query_tokens:
            return None

        best_score = 0.0
        best_rec: dict[str, Any] | None = None
        for entry in self._data.get("by_name_tokens") or []:
            other = set(entry.get("tokens") or [])
            if not other:
                continue
            inter = len(query_tokens & other)
            union = len(query_tokens | other)
            score = inter / union if union else 0.0
            if score > best_score:
                best_score = score
                best_rec = entry.get("vendor")

        if best_rec and best_score >= threshold:
            return VendorRecord.model_validate(best_rec)
        return None


class POLookup:
    def __init__(self, context_dir: Path | None = None) -> None:
        self._path = (context_dir or DEFAULT_CONTEXT_DIR) / "po_master.json"
        self._data: dict[str, Any] = _safe_load_json(self._path) or {}

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    def get(self, po_id: str) -> PORecord | None:
        rec = self._data.get(po_id) if po_id else None
        return PORecord.model_validate(rec) if rec else None


class GoodsReceiptLookup:
    def __init__(self, context_dir: Path | None = None) -> None:
        self._path = (context_dir or DEFAULT_CONTEXT_DIR) / "goods_receipts.json"
        self._data: dict[str, Any] = _safe_load_json(self._path) or {}

    def get_for_po(self, po_id: str) -> GoodsReceipt | None:
        rec = self._data.get(po_id) if po_id else None
        return GoodsReceipt.model_validate(rec) if rec else None


class PaymentStatusLookup:
    def __init__(self, context_dir: Path | None = None) -> None:
        self._path = (context_dir or DEFAULT_CONTEXT_DIR) / "payment_status.json"
        self._data: dict[str, Any] = _safe_load_json(self._path) or {}

    def get_for_po(self, po_id: str) -> POPaymentStatus | None:
        rec = self._data.get(po_id) if po_id else None
        return POPaymentStatus.model_validate(rec) if rec else None


class InvoiceHistoryLookup:
    """Lookup invoices by supplier_invoice_number (duplicate detection) and by
    po_id (split-invoice / aggregate checks).

    Loads the base history file plus any optional extras (used by the golden
    harness to opt into duplicate/fraud signal fixtures the corpus eval should
    NOT see).
    """

    def __init__(
        self,
        context_dir: Path | None = None,
        *,
        extra_history_files: list[Path] | None = None,
    ) -> None:
        base = context_dir or DEFAULT_CONTEXT_DIR
        self._paths: list[Path] = [base / "invoice_history.json"]
        if extra_history_files:
            self._paths.extend(extra_history_files)

        self._by_supplier_number: dict[str, list[dict]] = {}
        self._by_po: dict[str, list[dict]] = {}
        for path in self._paths:
            records: list[dict] = _safe_load_json(path) or []
            for rec in records:
                sn = rec.get("supplier_invoice_number") or ""
                po = rec.get("po_id") or ""
                if sn:
                    self._by_supplier_number.setdefault(sn, []).append(rec)
                if po:
                    self._by_po.setdefault(po, []).append(rec)

    def get_same_supplier_number(
        self,
        supplier_invoice_number: str,
        *,
        exclude_invoice_id: str | None = None,
    ) -> list[InvoiceSummary]:
        if not supplier_invoice_number:
            return []
        out: list[InvoiceSummary] = []
        for r in self._by_supplier_number.get(supplier_invoice_number, []):
            if exclude_invoice_id and r.get("invoice_id") == exclude_invoice_id:
                continue
            out.append(InvoiceSummary.model_validate(r))
        return out

    def get_same_po_within(
        self,
        po_id: str,
        *,
        window_days: int = 30,
        exclude_invoice_id: str | None = None,
        reference_date: date | None = None,
    ) -> list[InvoiceSummary]:
        if not po_id:
            return []
        ref = reference_date or date.today()
        out: list[InvoiceSummary] = []
        for r in self._by_po.get(po_id, []):
            if exclude_invoice_id and r.get("invoice_id") == exclude_invoice_id:
                continue
            d_raw = r.get("invoice_date")
            try:
                d = datetime.fromisoformat(d_raw).date() if d_raw else None
            except (ValueError, TypeError):
                d = None
            if d is None or (ref - d) <= timedelta(days=window_days):
                out.append(InvoiceSummary.model_validate(r))
        return out


class VendorChangeLookup:
    def __init__(self, context_dir: Path | None = None) -> None:
        self._path = (context_dir or DEFAULT_CONTEXT_DIR) / "vendor_changes.json"
        records: list[dict] = _safe_load_json(self._path) or []
        self._by_vendor: dict[str, list[dict]] = {}
        for rec in records:
            v = rec.get("vendor_id") or ""
            self._by_vendor.setdefault(v, []).append(rec)

    def get_recent(
        self,
        vendor_id: str,
        *,
        window_days: int = 30,
        reference_date: date | None = None,
    ) -> list[VendorChangeEvent]:
        if not vendor_id:
            return []
        ref = reference_date or date.today()
        out: list[VendorChangeEvent] = []
        for r in self._by_vendor.get(vendor_id, []):
            d_raw = r.get("changed_on")
            try:
                d = datetime.fromisoformat(d_raw).date() if d_raw else None
            except (ValueError, TypeError):
                d = None
            if d is None or (ref - d) <= timedelta(days=window_days):
                out.append(VendorChangeEvent.model_validate(r))
        return out
