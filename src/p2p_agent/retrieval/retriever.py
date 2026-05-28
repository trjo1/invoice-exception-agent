"""PolicyRetriever — loads `config/policy_library.yaml`, embeds it once, and
exposes `retrieve(query, k)`. Singleton-friendly: build one per process.

Architecture is production-shape (embed query → cosine top-k → optional
cross-encoder rerank). For the test phase the backend is the in-memory
store; pgvector swaps in for production by replacing the store implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from p2p_agent.models.retrieval import RetrievedDoc
from p2p_agent.retrieval.embeddings import Embedder
from p2p_agent.retrieval.store import InMemoryVectorStore

if TYPE_CHECKING:
    pass

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "policy_library.yaml"


class PolicyRetriever:
    """Embeds the policy library at construction and exposes retrieve()."""

    def __init__(
        self,
        policy_path: Path | None = None,
        embedder: Embedder | None = None,
        store: InMemoryVectorStore | None = None,
    ) -> None:
        self._policy_path = policy_path or DEFAULT_POLICY_PATH
        self._embedder = embedder or Embedder()
        self._store = store or InMemoryVectorStore()
        self._loaded = False

    @property
    def policy_count(self) -> int:
        return len(self._store)

    def _load(self) -> None:
        raw = yaml.safe_load(self._policy_path.read_text()) or {}
        policies_raw = raw.get("policies", [])
        if not policies_raw:
            raise RuntimeError(f"Policy library at {self._policy_path} is empty")

        docs: list[RetrievedDoc] = []
        texts: list[str] = []
        for p in policies_raw:
            doc = RetrievedDoc(
                id=str(p["id"]),
                title=str(p.get("title", "")),
                text=str(p.get("text", "")).strip(),
                score=0.0,
                tags=list(p.get("tags") or []),
            )
            # Embed title + body together so headings carry weight.
            texts.append(f"{doc.title}\n{doc.text}")
            docs.append(doc)

        vectors = self._embedder.embed(texts)
        self._store.add(docs, vectors)
        self._loaded = True

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedDoc]:
        if not self._loaded:
            self._load()
        vec = self._embedder.embed_one(query)
        return self._store.query(vec, k)
