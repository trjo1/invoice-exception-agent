"""Invoice extraction domain types.

Mirrors the ground-truth JSON shape produced by the corpus pipeline so the
extractor's output can be diffed against the sidecar JSON for any of the
490 invoices in `test_corpus/synthetic/invoices/`.

`field_confidence` is the only field with no ground-truth counterpart: it's
the extractor's self-reported per-field certainty, used downstream by the
classifier as a signal.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LineItemExtraction(BaseModel):
    line_no: int
    sku: str
    description: str
    quantity: float
    unit_price: float
    line_total: float


class HeaderFieldsExtraction(BaseModel):
    vendor_name: str
    vendor_address: str
    vendor_tax_id: str
    buyer_name: str
    buyer_address: str
    buyer_po_contact: str


class TaxLineExtraction(BaseModel):
    jurisdiction: str
    rate: float
    amount: float


class InvoiceExtraction(BaseModel):
    invoice_id: str
    po_reference: str
    invoice_date: str            # ISO format
    currency: str                # ISO 4217
    payment_terms: str
    header_fields: HeaderFieldsExtraction
    line_items: list[LineItemExtraction]
    subtotal: float
    tax: list[TaxLineExtraction] = Field(default_factory=list)
    total: float

    # Keyed by dotted path: "po_reference", "header_fields.vendor_tax_id",
    # "line_items[0].unit_price". 0.0–1.0. Missing keys = no claim.
    field_confidence: dict[str, float] = Field(default_factory=dict)
