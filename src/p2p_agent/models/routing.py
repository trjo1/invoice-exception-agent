"""HITL routing decision — output of the (rules-based) HITL router."""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel


class HITLTier(IntEnum):
    AUTO_PASS = 1
    APPROVER_REVIEW = 2
    SUPERVISOR_REVIEW = 3


class RoutingDecision(BaseModel):
    tier: HITLTier
    routed_to: str                # named role: "none" | "buyer" | "vp_finance" | "treasury" | "ap_fraud_team" | "vendor_master_team"
    reason: str = ""
