"""Retrieval module — RAG layer for policy / context lookup.

Production architecture, test-phase content. The `PolicyRetriever` loads
mock snippets from `config/policy_library.yaml`, embeds them via
`sentence-transformers` (bge-large-en-v1.5), stores in an in-memory cosine
index, and exposes `retrieve(query, k)`. The same interface fronts a
pgvector backend in production.
"""

from p2p_agent.models.retrieval import RetrievedDoc
from p2p_agent.retrieval.embeddings import Embedder, get_default_embedder
from p2p_agent.retrieval.retriever import PolicyRetriever
from p2p_agent.retrieval.store import InMemoryVectorStore, VectorStore

__all__ = [
    "Embedder",
    "InMemoryVectorStore",
    "PolicyRetriever",
    "RetrievedDoc",
    "VectorStore",
    "get_default_embedder",
    "get_default_retriever",
]


# --- Process-wide PolicyRetriever singleton --------------------------------
#
# A single PolicyRetriever holds the embedded policy library in memory. Building
# it costs the embedder load (~10s on CPU) plus the policy embedding pass.
# `get_default_retriever()` caches the instance for the life of the process.

_DEFAULT_RETRIEVER: PolicyRetriever | None = None


def get_default_retriever() -> PolicyRetriever:
    """Return the process-wide singleton PolicyRetriever.

    First call constructs it (sharing the singleton Embedder). The policy
    library is embedded lazily on the first `.retrieve()` call — call
    `.retrieve("warm-up")` if you want to pay the load cost at startup.
    """
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None:
        _DEFAULT_RETRIEVER = PolicyRetriever(embedder=get_default_embedder())
    return _DEFAULT_RETRIEVER
