"""Decision-support domain types — what the recommend_action node returns.

`RecommendedAction` enumerates the actions the agent can recommend. The
string values appear verbatim in the golden cases' `expected.recommendation.action`
field, so the enum and the YAMLs must stay in sync.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RecommendedAction(StrEnum):
    # Auto-resolution / approval
    AUTO_RESOLVE = "auto_resolve"
    APPROVE_PENDING_REVIEW = "approve_pending_review"

    # Supplier-side correction asks
    REQUEST_SUPPLIER_CREDIT_MEMO = "request_supplier_credit_memo"
    REQUEST_SUPPLIER_CORRECTION = "request_supplier_correction"
    REQUEST_MISSING_PO_FROM_SUPPLIER = "request_missing_po_from_supplier"
    REQUEST_PO_AMENDMENT = "request_po_amendment"

    # Buyer-side routing
    ROUTE_TO_VENDOR_MASTER_ONBOARDING = "route_to_vendor_master_onboarding"
    ROUTE_TO_VP_FINANCE_APPROVAL = "route_to_vp_finance_approval"
    ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY = "escalate_to_buyer_for_short_delivery"
    ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO = "escalate_to_buyer_for_retroactive_po"

    # Fraud / control
    ESCALATE_TO_FRAUD = "escalate_to_fraud"
    HALT_REQUIRE_SUPERVISOR = "halt_require_supervisor"

    # Treasury / FX
    ESCALATE_FOR_FX_REVIEW = "escalate_for_fx_review"

    # Notifications / holds
    NOTIFY_BUYER_OF_SUPPLIER_DELAY = "notify_buyer_of_supplier_delay"
    HOLD_FOR_GOODS_RECEIPT = "hold_for_goods_receipt"

    # Fallback
    OTHER = "other"


class Recommendation(BaseModel):
    action: RecommendedAction
    rationale: str = ""              # 1-2 sentences citing the case facts
    counterfactual: str = ""         # "if X were different, the action would be Y"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    cited_policy_ids: list[str] = Field(default_factory=list)
