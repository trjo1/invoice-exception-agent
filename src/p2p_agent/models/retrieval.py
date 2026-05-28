"""Retrieval domain types — what the RAG layer returns."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievedDoc(BaseModel):
    id: str
    title: str
    text: str
    score: float
    tags: list[str] = Field(default_factory=list)
