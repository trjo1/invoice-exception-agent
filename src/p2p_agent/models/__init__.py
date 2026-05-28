"""Pydantic data models shared across the agent.

Domain types: Classification, GoldenCase. Every other module imports from
here rather than re-defining shapes.
"""

from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.context import (
    CaseContext,
    GoodsReceipt,
    InvoiceSummary,
    PORecord,
    POPaymentStatus,
    POStatus,
    VendorChangeEvent,
    VendorContractType,
    VendorRecord,
    VendorTier,
)
from p2p_agent.models.draft import Draft, DraftType
from p2p_agent.models.golden_case import (
    ExpectedClassification,
    ExpectedDrafting,
    ExpectedHITL,
    ExpectedRecommendation,
    GoldenCase,
    load_golden_case,
)
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.retrieval import RetrievedDoc
from p2p_agent.models.routing import HITLTier, RoutingDecision

__all__ = [
    "CaseContext",
    "Classification",
    "Draft",
    "DraftType",
    "ExceptionCategory",
    "ExpectedClassification",
    "ExpectedDrafting",
    "ExpectedHITL",
    "ExpectedRecommendation",
    "GoldenCase",
    "GoodsReceipt",
    "HITLTier",
    "InvoiceSummary",
    "PORecord",
    "POPaymentStatus",
    "POStatus",
    "Recommendation",
    "RecommendedAction",
    "RetrievedDoc",
    "RoutingDecision",
    "VendorChangeEvent",
    "VendorContractType",
    "VendorRecord",
    "VendorTier",
    "load_golden_case",
]
