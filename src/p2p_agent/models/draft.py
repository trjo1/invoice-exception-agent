"""Draft (supplier email / internal note) — output of the drafter node."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DraftType(StrEnum):
    SUPPLIER_EMAIL = "supplier_email"        # outbound to supplier; needs HITL Tier 2 approval before send
    INTERNAL_NOTE = "internal_note"          # to buyer's team (procurement / treasury / fraud)


class Draft(BaseModel):
    draft_type: DraftType
    recipient: str                           # email address or named role
    subject: str
    body: str                                # 2-4 paragraphs typically
    cc: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)   # PO#, invoice#, supplier name, etc.
