"""CaseContextBuilder — given an invoice (extracted or ground-truth), assemble
the cross-case `CaseContext` the classifier will reason over.

Singleton-friendly: build one per process; each lookup loads its JSON file
once at init. Same pattern as `PolicyRetriever`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from p2p_agent.context.lookups import (
    GoodsReceiptLookup,
    InvoiceHistoryLookup,
    POLookup,
    PaymentStatusLookup,
    VendorChangeLookup,
    VendorMasterLookup,
)
from p2p_agent.models.context import CaseContext


class CaseContextBuilder:
    def __init__(
        self,
        context_dir: Path | None = None,
        *,
        extra_history_files: list[Path] | None = None,
    ) -> None:
        self.vendors = VendorMasterLookup(context_dir)
        self.pos = POLookup(context_dir)
        self.grs = GoodsReceiptLookup(context_dir)
        self.payments = PaymentStatusLookup(context_dir)
        self.history = InvoiceHistoryLookup(
            context_dir, extra_history_files=extra_history_files,
        )
        self.vendor_changes = VendorChangeLookup(context_dir)

    def build(
        self,
        invoice: dict[str, Any],
        *,
        invoice_id: str | None = None,
    ) -> CaseContext:
        """Look up everything around this invoice."""
        header = invoice.get("header_fields") or {}
        tax_id = str(header.get("vendor_tax_id") or "")
        vendor_name = str(header.get("vendor_name") or "")
        po_ref = str(invoice.get("po_reference") or "")
        supplier_number = str(invoice.get("invoice_id") or "")
        invoice_currency = str(invoice.get("currency") or "")

        # Vendor: try tax_id first (4-stage fallback inside the lookup), then
        # fall back to fuzzy name match.
        vendor = self.vendors.get_by_tax_id(tax_id) if tax_id else None
        if vendor is None and vendor_name:
            vendor = self.vendors.get_by_name(vendor_name)
        po = self.pos.get(po_ref) if po_ref else None
        gr = self.grs.get_for_po(po_ref) if po_ref else None
        payment = self.payments.get_for_po(po_ref) if po_ref else None
        dup_history = self.history.get_same_supplier_number(
            supplier_number, exclude_invoice_id=invoice_id,
        )
        split_history = self.history.get_same_po_within(
            po_ref, exclude_invoice_id=invoice_id,
        )
        vendor_changes = self.vendor_changes.get_recent(vendor.id) if vendor else []

        aggregate_signals: dict[str, Any] = {}
        if po and invoice_currency and po.currency != invoice_currency:
            aggregate_signals["currency_mismatch"] = {
                "po_currency": po.currency,
                "invoice_currency": invoice_currency,
            }

        return CaseContext(
            vendor_record=vendor,
            po_record=po,
            goods_receipt=gr,
            prior_invoices_same_supplier_number=dup_history,
            prior_invoices_same_po=split_history,
            po_payment_status=payment,
            vendor_recent_changes=vendor_changes,
            aggregate_signals=aggregate_signals,
        )

    async def build_async(
        self,
        invoice: dict[str, Any],
        *,
        invoice_id: str | None = None,
    ) -> CaseContext:
        """Parallelized variant of `build`.

        Lookups today are sync in-memory dict accesses (mock data); we wrap
        each in `asyncio.to_thread` so they run concurrently. The thread
        overhead is single-digit milliseconds — negligible today, and the
        structure is in place so the moment a lookup becomes a real httpx
        SAP call (0.5-2s each), parallelism actually pays off.

        Two stages because `vendor_changes` depends on the vendor lookup:
          Stage 1 (parallel): vendor, po, gr, payment, dup_history, split_history
          Stage 2 (depends on vendor): vendor_changes
        """
        header = invoice.get("header_fields") or {}
        tax_id = str(header.get("vendor_tax_id") or "")
        vendor_name = str(header.get("vendor_name") or "")
        po_ref = str(invoice.get("po_reference") or "")
        supplier_number = str(invoice.get("invoice_id") or "")
        invoice_currency = str(invoice.get("currency") or "")

        def _vendor_lookup() -> Any:
            v = self.vendors.get_by_tax_id(tax_id) if tax_id else None
            if v is None and vendor_name:
                v = self.vendors.get_by_name(vendor_name)
            return v

        # Stage 1 — all independent lookups in parallel.
        # Lambdas because several lookups have keyword-only args.
        vendor, po, gr, payment, dup_history, split_history = await asyncio.gather(
            asyncio.to_thread(_vendor_lookup),
            asyncio.to_thread(self.pos.get, po_ref) if po_ref else _none_async(),
            asyncio.to_thread(self.grs.get_for_po, po_ref) if po_ref else _none_async(),
            asyncio.to_thread(self.payments.get_for_po, po_ref) if po_ref else _none_async(),
            asyncio.to_thread(
                lambda: self.history.get_same_supplier_number(
                    supplier_number, exclude_invoice_id=invoice_id,
                ),
            ),
            asyncio.to_thread(
                lambda: self.history.get_same_po_within(
                    po_ref, exclude_invoice_id=invoice_id,
                ),
            ),
        )

        # Stage 2 — vendor_changes depends on the vendor lookup.
        if vendor is not None:
            vendor_changes = await asyncio.to_thread(
                self.vendor_changes.get_recent, vendor.id,
            )
        else:
            vendor_changes = []

        aggregate_signals: dict[str, Any] = {}
        if po and invoice_currency and po.currency != invoice_currency:
            aggregate_signals["currency_mismatch"] = {
                "po_currency": po.currency,
                "invoice_currency": invoice_currency,
            }

        return CaseContext(
            vendor_record=vendor,
            po_record=po,
            goods_receipt=gr,
            prior_invoices_same_supplier_number=dup_history,
            prior_invoices_same_po=split_history,
            po_payment_status=payment,
            vendor_recent_changes=vendor_changes,
            aggregate_signals=aggregate_signals,
        )


async def _none_async() -> None:
    """Tiny coroutine returning None — lets us use `asyncio.gather` over a
    mix of "do the lookup" and "no PO ref, skip this one" branches without
    branching the gather call itself.
    """
    return None
