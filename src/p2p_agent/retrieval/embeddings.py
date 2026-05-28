"""Embedder — thin wrapper around sentence-transformers.

Default model is `BAAI/bge-large-en-v1.5` per `config/models.yaml::embedding`.
Lazy model load on first `embed()` call so import is cheap.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"


class Embedder:
    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = (
            model_name
            or os.environ.get("EMBEDDING_MODEL")
            or DEFAULT_MODEL
        )
        self._model: object | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _ensure_loaded(self) -> object:
        if self._model is None:
            # Lazy import — sentence-transformers is heavy and pulls torch.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> "np.ndarray":
        """Return an (N, D) array of L2-normalized embeddings."""
        import numpy as np

        model = self._ensure_loaded()
        vectors = model.encode(  # type: ignore[attr-defined]
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_one(self, text: str) -> "np.ndarray":
        return self.embed([text])[0]


# --- Process-wide singleton ------------------------------------------------
#
# Loading bge-large-en-v1.5 + torch costs ~10-15s on CPU. We want to pay that
# once per process, not once per pipeline call. Callers that don't need a
# bespoke embedder should use `get_default_embedder()`.

_DEFAULT_EMBEDDER: Embedder | None = None


def get_default_embedder() -> Embedder:
    """Return the process-wide singleton Embedder.

    First call constructs it; subsequent calls return the cached instance. The
    underlying sentence-transformers model is still lazy-loaded on first
    `.embed()` call — call `_ensure_loaded()` directly if you want to pay the
    load cost at a controlled moment (e.g. server startup).
    """
    global _DEFAULT_EMBEDDER
    if _DEFAULT_EMBEDDER is None:
        _DEFAULT_EMBEDDER = Embedder()
    return _DEFAULT_EMBEDDER
