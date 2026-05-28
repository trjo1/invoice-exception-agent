"""Vector store — protocol + in-memory implementation.

Same interface for in-memory and a future pgvector backend. For the test
phase (small policy corpus, ~25 docs) in-memory cosine similarity is fast
and avoids the Postgres dependency. For production, swap in pgvector with
the same `add` / `query` shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from p2p_agent.models.retrieval import RetrievedDoc

if TYPE_CHECKING:
    import numpy as np


class VectorStore(Protocol):
    def add(self, docs: list[RetrievedDoc], vectors: "np.ndarray") -> None: ...
    def query(self, vec: "np.ndarray", k: int) -> list[RetrievedDoc]: ...
    def __len__(self) -> int: ...


class InMemoryVectorStore:
    """Numpy-backed cosine-similarity store.

    Vectors must be L2-normalized at insertion time (the `Embedder` does this
    by default); we score with a dot product against the stored matrix.
    """

    def __init__(self) -> None:
        import numpy as np

        self._docs: list[RetrievedDoc] = []
        self._vectors: "np.ndarray" = np.zeros((0, 0), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, docs: list[RetrievedDoc], vectors: "np.ndarray") -> None:
        import numpy as np

        if len(docs) != len(vectors):
            raise ValueError(
                f"add: docs ({len(docs)}) and vectors ({len(vectors)}) must match in length",
            )
        self._docs.extend(docs)
        if self._vectors.size == 0:
            self._vectors = vectors.astype(np.float32, copy=True)
        else:
            self._vectors = np.vstack([self._vectors, vectors.astype(np.float32)])

    def query(self, vec: "np.ndarray", k: int) -> list[RetrievedDoc]:
        import numpy as np

        if len(self._docs) == 0:
            return []
        scores = self._vectors @ vec
        top_k = min(k, len(self._docs))
        idx = np.argpartition(-scores, top_k - 1)[:top_k]
        idx = idx[np.argsort(-scores[idx])]
        # Return RetrievedDoc copies with the score filled in.
        return [
            RetrievedDoc(
                id=self._docs[i].id,
                title=self._docs[i].title,
                text=self._docs[i].text,
                score=float(scores[i]),
                tags=list(self._docs[i].tags),
            )
            for i in idx
        ]
