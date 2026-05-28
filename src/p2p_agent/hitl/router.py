"""HITL Router — rules-based, no LLM call.

Maps the (recommendation, classification, case context) triple to a
`RoutingDecision` (tier + named role + reason). This is intentionally
deterministic — the agent should always route the same case the same way
given the same inputs, and the routing should be auditable.

Policy reflects the standard tier definitions:
- Tier 1 (auto_pass): money-moving auto-resolves only; high-confidence clean cases.
- Tier 2 (approver_review): buyer or named team reviewer; default for most exceptions.
- Tier 3 (supervisor_review): fraud, treasury, VP-Finance, vendor master onboarding.
"""

from __future__ import annotations

from p2p_agent.models.classification import Classification, ExceptionCategory
from p2p_agent.models.context import CaseContext
from p2p_agent.models.recommendation import Recommendation, RecommendedAction
from p2p_agent.models.routing import HITLTier, RoutingDecision


# Action → (tier, routed_to, reason-template) mapping.
_ROUTING_TABLE: dict[RecommendedAction, tuple[HITLTier, str, str]] = {
    RecommendedAction.AUTO_RESOLVE: (
        HITLTier.AUTO_PASS, "none",
        "Clean 3-way match; auto-pass per Tier-1 policy.",
    ),
    RecommendedAction.APPROVE_PENDING_REVIEW: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Looks acceptable but needs a sanity check before posting.",
    ),
    RecommendedAction.REQUEST_SUPPLIER_CREDIT_MEMO: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Outbound supplier email requires buyer approval before send.",
    ),
    RecommendedAction.REQUEST_SUPPLIER_CORRECTION: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Outbound supplier email requires buyer approval before send.",
    ),
    RecommendedAction.REQUEST_MISSING_PO_FROM_SUPPLIER: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Outbound supplier email requires buyer approval before send.",
    ),
    RecommendedAction.REQUEST_PO_AMENDMENT: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "PO amendment requires buyer's procurement team review.",
    ),
    RecommendedAction.ROUTE_TO_VENDOR_MASTER_ONBOARDING: (
        HITLTier.APPROVER_REVIEW, "vendor_master_team",
        "Vendor onboarding workflow; payment held until vendor active.",
    ),
    RecommendedAction.ROUTE_TO_VP_FINANCE_APPROVAL: (
        HITLTier.SUPERVISOR_REVIEW, "vp_finance",
        "Spend exceeds PO authorization; needs next-tier approval.",
    ),
    RecommendedAction.ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Quantity short of GR; buyer must confirm.",
    ),
    RecommendedAction.ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Retroactive PO required; buyer decides accept vs reject.",
    ),
    RecommendedAction.ESCALATE_TO_FRAUD: (
        HITLTier.SUPERVISOR_REVIEW, "ap_fraud_team",
        "Fraud signal detected; halt pay and escalate to fraud team.",
    ),
    RecommendedAction.HALT_REQUIRE_SUPERVISOR: (
        HITLTier.SUPERVISOR_REVIEW, "ap_fraud_team",
        "Halt-pay event requiring supervisor review.",
    ),
    RecommendedAction.ESCALATE_FOR_FX_REVIEW: (
        HITLTier.SUPERVISOR_REVIEW, "treasury",
        "FX variance above tolerance; treasury sign-off required.",
    ),
    RecommendedAction.NOTIFY_BUYER_OF_SUPPLIER_DELAY: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Supplier delay notification; buyer's procurement liaison handles.",
    ),
    RecommendedAction.HOLD_FOR_GOODS_RECEIPT: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Hold invoice pending goods receipt; buyer recheck after window.",
    ),
    RecommendedAction.OTHER: (
        HITLTier.APPROVER_REVIEW, "buyer",
        "Non-standard case; buyer reviews and decides.",
    ),
}


class HITLRouter:
    """Pure-function router. Construct once; call route() per case."""

    def route(
        self,
        *,
        recommendation: Recommendation,
        classification: Classification | None = None,
        case_context: CaseContext | None = None,
    ) -> RoutingDecision:
        """Return a RoutingDecision for this case.

        Auto-pass is the most restricted tier and gets extra guards:
        - Classification must be `none` with confidence ≥ 0.85.
        - Recommendation's own confidence must be ≥ 0.85.
        Otherwise downgrade to Tier 2 review even if the action is auto_resolve.
        """
        tier, routed_to, reason = _ROUTING_TABLE.get(
            recommendation.action,
            (HITLTier.APPROVER_REVIEW, "buyer", "Default routing for unmapped action."),
        )

        if tier == HITLTier.AUTO_PASS:
            classifier_ok = (
                classification is not None
                and classification.class_label == ExceptionCategory.NONE
                and classification.confidence >= 0.85
            )
            recommender_ok = recommendation.confidence >= 0.85
            if not (classifier_ok and recommender_ok):
                return RoutingDecision(
                    tier=HITLTier.APPROVER_REVIEW,
                    routed_to="buyer",
                    reason=(
                        "Action says auto_resolve but classifier or recommender "
                        f"confidence below threshold (cls={classification.confidence:.2f}"
                        if classification is not None
                        else "Action says auto_resolve but classification missing"
                    ) + f", rec={recommendation.confidence:.2f}); downgrading to Tier 2.",
                )

        return RoutingDecision(tier=tier, routed_to=routed_to, reason=reason)


def route(
    recommendation: Recommendation,
    classification: Classification | None = None,
    case_context: CaseContext | None = None,
) -> RoutingDecision:
    """Stateless convenience wrapper around `HITLRouter().route(...)`."""
    return HITLRouter().route(
        recommendation=recommendation,
        classification=classification,
        case_context=case_context,
    )
