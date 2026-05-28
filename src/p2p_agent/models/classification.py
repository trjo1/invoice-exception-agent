"""Classification domain types.

The agent's classifier node produces a `Classification`. The 13-member
`ExceptionCategory` enum mirrors the taxonomy in
`docs/authoring_golden_cases.md` and matches the YAML strings in
`tests/golden_cases/GTC-*.yaml::expected.classification.class_label`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ExceptionCategory(StrEnum):
    NONE = "none"
    THREE_WAY_MATCH_PRICE_VARIANCE = "three_way_match_price_variance"
    THREE_WAY_MATCH_QUANTITY_VARIANCE = "three_way_match_quantity_variance"
    MISSING_PO = "missing_po"
    MISSING_GOODS_RECEIPT = "missing_goods_receipt"
    MISSING_APPROVAL = "missing_approval"
    DUPLICATE_INVOICE = "duplicate_invoice"
    FRAUD_SIGNAL = "fraud_signal"
    VENDOR_MASTER_GAP = "vendor_master_gap"
    CROSS_CURRENCY_MISMATCH = "cross_currency_mismatch"
    TAX_FIELD_MISMATCH = "tax_field_mismatch"
    PAYMENT_TERM_MISMATCH = "payment_term_mismatch"
    OTHER = "other"


class Classification(BaseModel):
    class_label: ExceptionCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    rationale: str = ""
