"""Decision-support node — picks one action with rationale + counterfactual."""

from p2p_agent.decision.decision_support import (
    DecisionError,
    recommend_action,
)
from p2p_agent.models.recommendation import Recommendation, RecommendedAction

__all__ = [
    "DecisionError",
    "Recommendation",
    "RecommendedAction",
    "recommend_action",
]
