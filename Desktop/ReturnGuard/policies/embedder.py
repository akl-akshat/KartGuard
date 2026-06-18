"""Embedding providers.

The default ``stub`` embedder is a deterministic, dependency-free signed-hashing
bag-of-words encoder. It captures lexical overlap well enough for the policy corpus and,
crucially, lets RAG run fully offline and reproducibly (no network, no API key). A real
provider can be plugged in behind the same ``embed_text`` interface for production.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

from config.settings import settings

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def stub_embed(text: str, dim: int) -> list[float]:
    """Deterministic signed-hash bag-of-words embedding, L2-normalised."""
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 7) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_text(text: str, dim: int | None = None) -> list[float]:
    dim = dim or settings.EMBEDDING_DIM
    if settings.EMBEDDING_PROVIDER == "stub":
        return stub_embed(text, dim)
    # Hook for a real embedding model (e.g. a hosted embeddings API). Kept out of the
    # offline path on purpose; wire it here when EMBEDDING_PROVIDER=anthropic.
    raise NotImplementedError(
        f"Embedding provider '{settings.EMBEDDING_PROVIDER}' is not configured. "
        "Use EMBEDDING_PROVIDER=stub for offline runs, or implement the provider here."
    )


def embed_batch(texts: Iterable[str], dim: int | None = None) -> list[list[float]]:
    return [embed_text(t, dim) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    # Inputs are L2-normalised, so cosine == dot product.
    return sum(x * y for x, y in zip(a, b))
