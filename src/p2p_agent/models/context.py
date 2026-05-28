"""Cross-case context types.

The classifier today reads a single invoice + maybe a PO + maybe a GR. With
this module it also reads the surrounding context: the vendor record (or
absence thereof), prior invoices on the same PO, the PO's payment status,
recent vendor changes. These are the signals needed to decide
`duplicate_invoice`, `fraud_signal`, `vendor_master_gap`, and several other
categories that are otherwise undecidable from invoice-alone data.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VendorTier(StrEnum):
    STRATEGIC = "strategic"
    TACTICAL = "tactical"
    NEW = "new"          # onboarded < 90 days; higher fraud-watch
    INACTIVE = "inactive"


class VendorContractType(StrEnum):
    MSA = "msa"
    SPOT = "spot"
    RECURRING = "recurring"


class VendorRecord(BaseModel):
    id: str                            # internal vendor id, e.g. "VEN-001"
    name: str
    tax_id: str                        # EIN / VAT / GSTIN / CNPJ, stripped of label
    country: str                       # ISO-2 (US, DE, IN, BR, etc.)
    addresses: list[str] = Field(default_factory=list)
    bank_account_last4: str = ""       # store only last 4 for fraud-detection comparisons
    contract_type: VendorContractType = VendorContractType.SPOT
    tier: VendorTier = VendorTier.TACTICAL
    onboarding_date: date | str | None = None
    status: str = "active"             # "active" | "inactive" | "blocked"
    sanctions_check_passed: bool = True
    phi_access: bool = False           # HIPAA flag
    sbe_classification: str = ""       # SBE/MBE/WBE/VOSB or empty
    notes: str = ""


class POLineItem(BaseModel):
    line_no: int
    sku: str
    description: str
    quantity_authorized: float
    unit_price: float
    line_total: float


class POApprover(BaseModel):
    role: str                          # e.g. "manager", "vp_finance"
    name: str
    approved: bool
    approved_at: str | None = None     # ISO timestamp


class POStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_RECEIVED = "partially_received"
    FULLY_RECEIVED = "fully_received"
    INVOICED = "invoiced"
    PAID = "paid"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class PORecord(BaseModel):
    id: str                            # PO reference, e.g. "PO-2025-12-12345"
    vendor_id: str                     # FK to VendorRecord.id
    line_items: list[POLineItem] = Field(default_factory=list)
    total_authorized: float
    currency: str
    payment_terms: str = "NET-30"
    approver_chain: list[POApprover] = Field(default_factory=list)
    status: POStatus = POStatus.OPEN
    created_date: date | str | None = None
    department: str = ""
    is_emergency: bool = False
    fx_clause: str = "spot"            # "spot" | "fixed" | "collar"
    fx_rate: float | None = None       # used when fx_clause == "fixed"


class GoodsReceiptLine(BaseModel):
    line_no: int
    sku: str
    quantity_received: float


class GoodsReceipt(BaseModel):
    id: str                            # e.g. "GR-2026-04-00789"
    po_id: str                         # FK to PORecord.id
    receipt_date: date | str | None = None
    warehouse: str = ""
    receiver: str = ""
    line_items: list[GoodsReceiptLine] = Field(default_factory=list)


class InvoiceSummary(BaseModel):
    """Lightweight summary of a prior invoice — for duplicate / split-invoice checks."""

    invoice_id: str
    supplier_invoice_number: str       # the number printed on the invoice (used for duplicate match)
    vendor_id: str
    po_id: str
    total: float
    currency: str
    invoice_date: date | str | None = None
    status: str = "pending"            # "pending" | "paid" | "rejected" | "duplicate_voided"


class POPaymentStatus(BaseModel):
    po_id: str
    total_authorized: float
    total_invoiced: float = 0.0
    total_paid: float = 0.0
    n_invoices: int = 0
    last_payment_date: date | str | None = None

    @property
    def fully_paid(self) -> bool:
        return self.total_paid >= self.total_authorized * 0.999


class VendorChangeEvent(BaseModel):
    vendor_id: str
    field: str                         # e.g. "bank_account", "address", "tax_id"
    changed_on: date | str
    note: str = ""


class CaseContext(BaseModel):
    """Everything the classifier should know about a case beyond the invoice itself."""

    vendor_record: VendorRecord | None = None
    po_record: PORecord | None = None
    goods_receipt: GoodsReceipt | None = None
    prior_invoices_same_supplier_number: list[InvoiceSummary] = Field(default_factory=list)
    prior_invoices_same_po: list[InvoiceSummary] = Field(default_factory=list)
    po_payment_status: POPaymentStatus | None = None
    vendor_recent_changes: list[VendorChangeEvent] = Field(default_factory=list)
    # Free-form signal map for one-off observations the lookups want to surface.
    aggregate_signals: dict[str, Any] = Field(default_factory=dict)

    def summary_signals(self) -> list[str]:
        """Short human-readable signal list — used in classifier prompt and for debugging.

        Signal emission is gated by real-world heuristics so that natural
        multi-shipment / multi-line PO scenarios don't read as fraud:
        - DUPLICATE only fires when a prior invoice with the same supplier_invoice_number
          AND the same total exists (true business duplicate).
        - SPLIT WATCH only fires when ≥3 prior invoices on the same PO cumulatively
          approach the PO authorization (≥60% of total_authorized), which is the
          actual split-invoice fraud signature.
        """
        signals: list[str] = []
        if self.vendor_record is None:
            signals.append("vendor NOT in master file")
        else:
            signals.append(
                f"vendor in master: {self.vendor_record.name} (tier={self.vendor_record.tier.value})",
            )
        if self.po_record is None:
            signals.append("PO NOT found")
        else:
            signals.append(f"PO {self.po_record.id} status={self.po_record.status.value}")
        if self.goods_receipt is None:
            signals.append("no goods receipt recorded")
        else:
            signals.append(f"GR {self.goods_receipt.id} on file")

        # DUPLICATE — only fire when prior invoice matches BOTH supplier_invoice_number
        # AND total (real duplicate, not a naming collision).
        if self.prior_invoices_same_supplier_number:
            # Find priors whose totals are also suspiciously close to suggest real duplicate.
            real_dups: list = []
            this_total = 0.0
            try:
                # Best-effort: po_record carries the relevant amount; otherwise compare priors to each other
                if self.po_record is not None:
                    this_total = self.po_record.total_authorized
            except AttributeError:
                pass
            for prior in self.prior_invoices_same_supplier_number:
                if this_total and abs(prior.total - this_total) / max(this_total, 1.0) < 0.05:
                    real_dups.append(prior)
            if real_dups:
                signals.append(
                    f"DUPLICATE: {len(real_dups)} prior invoice(s) with same supplier_invoice_number "
                    f"AND matching total",
                )

        # SPLIT WATCH — only fire when ≥3 priors on same PO AND cumulative ≥ 60% of PO total.
        if len(self.prior_invoices_same_po) >= 3 and self.po_record is not None:
            cumulative = sum(p.total for p in self.prior_invoices_same_po)
            po_auth = self.po_record.total_authorized or 1.0
            if cumulative >= 0.6 * po_auth:
                signals.append(
                    f"SPLIT WATCH: {len(self.prior_invoices_same_po)} prior invoices on same PO "
                    f"totaling {cumulative:.2f} vs PO authorization {po_auth:.2f} "
                    f"({100.0 * cumulative / po_auth:.0f}% of PO)",
                )

        if self.po_payment_status and self.po_payment_status.fully_paid:
            signals.append("PO ALREADY FULLY PAID")
        if self.vendor_recent_changes:
            signals.append(
                f"VENDOR CHANGED: {len(self.vendor_recent_changes)} change(s) in last 30 days",
            )
        return signals
