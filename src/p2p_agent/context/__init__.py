"""Cross-case context layer — lookups + CaseContextBuilder.

Test-phase: JSON-backed lookups reading `test_corpus/synthetic/context/`.
Production-phase: same interfaces front SAP OData reads.
"""

from p2p_agent.context.builder import CaseContextBuilder
from p2p_agent.context.lookups import (
    GoodsReceiptLookup,
    InvoiceHistoryLookup,
    POLookup,
    PaymentStatusLookup,
    VendorChangeLookup,
    VendorMasterLookup,
)
from p2p_agent.models.context import CaseContext

__all__ = [
    "CaseContext",
    "CaseContextBuilder",
    "GoodsReceiptLookup",
    "InvoiceHistoryLookup",
    "POLookup",
    "PaymentStatusLookup",
    "VendorChangeLookup",
    "VendorMasterLookup",
]
