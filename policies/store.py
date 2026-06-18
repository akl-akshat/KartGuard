"""Policy chunk stores with metadata-filtered similarity search (DR-RAG-2).

Filtering is applied **in addition to** semantic similarity: a chunk is a candidate only
when its category matches (or is the wildcard ``*``) and its payment_mode matches (or is
mode-agnostic). Candidates are then ranked by cosine similarity and the top-k returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from policies.corpus_loader import Chunk
from policies.embedder import cosine, embed_text


@dataclass
class ScoredChunk:
    policy_id: str
    chunk_id: str
    chunk_text: str
    score: float
    metadata: dict[str, Any]


class PolicyStore(Protocol):
    def search(self, category: str, payment_mode: str | None, issue_type: str | None, query: str, k: int) -> list[ScoredChunk]: ...


def _passes_filter(meta: dict[str, Any], category: str, payment_mode: str | None) -> bool:
    cat = meta.get("category", "*")
    if cat not in ("*", category):
        return False
    pm = meta.get("payment_mode")
    if pm is not None and payment_mode is not None and pm != payment_mode:
        return False
    return True


class InMemoryPolicyStore:
    """Offline pgvector stand-in: cosine search over in-process chunk embeddings."""

    def __init__(self, chunks: list[Chunk], dim: int | None = None) -> None:
        self._chunks = chunks
        self._embeddings = {c.id: embed_text(c.chunk_text, dim) for c in chunks}

    def search(self, category: str, payment_mode: str | None, issue_type: str | None,
               query: str, k: int) -> list[ScoredChunk]:
        q_emb = embed_text(query)
        candidates = [c for c in self._chunks if _passes_filter(c.metadata, category, payment_mode)]
        scored: list[ScoredChunk] = []
        for c in candidates:
            sim = cosine(q_emb, self._embeddings[c.id])
            # Light issue_type affinity boost (still filter-first, similarity-second).
            if issue_type and issue_type in (c.metadata.get("issue_type") or []):
                sim += 0.15
            scored.append(ScoredChunk(c.policy_id, c.id, c.chunk_text, sim, c.metadata))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:k]


class PgPolicyStore:
    """Production pgvector store (psycopg). Metadata filter in SQL, similarity by `<=>`."""

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(dsn, row_factory=dict_row, autocommit=True)

    def search(self, category: str, payment_mode: str | None, issue_type: str | None,
               query: str, k: int) -> list[ScoredChunk]:
        from pgvector.psycopg import register_vector

        q_emb = embed_text(query)
        sql = (
            "SELECT id, policy_id, chunk_text, metadata, "
            "1 - (embedding <=> %s::vector) AS score FROM policy_chunks "
            "WHERE (metadata->>'category' = %s OR metadata->>'category' = '*') "
            "AND (metadata->>'payment_mode' IS NULL OR metadata->>'payment_mode' = %s "
            "OR %s IS NULL) ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        with self._connect() as conn:
            register_vector(conn)
            rows = conn.execute(
                sql, (q_emb, category, payment_mode, payment_mode, q_emb, k)
            ).fetchall()
        return [
            ScoredChunk(r["policy_id"], r["id"], r["chunk_text"], float(r["score"]), r["metadata"])
            for r in rows
        ]
