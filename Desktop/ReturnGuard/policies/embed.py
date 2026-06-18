"""Chunk + embed the policy corpus into pgvector (DR-RAG-1).

* Offline: :func:`build_inmemory_store` builds an in-process store from the corpus.
* Production: :func:`embed_to_postgres` writes chunks + embeddings into ``policy_chunks``.

Both are idempotent. Run ``python -m policies.embed`` to (re)build; it targets Postgres
if reachable, otherwise reports the in-memory chunk count.
"""

from __future__ import annotations

import sys
from functools import lru_cache

from config.settings import settings
from policies.corpus_loader import load_chunks
from policies.embedder import embed_text
from policies.store import InMemoryPolicyStore


@lru_cache(maxsize=1)
def build_inmemory_store() -> InMemoryPolicyStore:
    return InMemoryPolicyStore(load_chunks())


def embed_to_postgres(dsn: str | None = None) -> int:
    import psycopg
    from pgvector.psycopg import register_vector

    dsn = dsn or settings.DATABASE_URL
    chunks = load_chunks()
    with psycopg.connect(dsn, autocommit=True) as conn:
        register_vector(conn)
        # Idempotent: clear and re-insert all chunks.
        conn.execute("DELETE FROM policy_chunks")
        with conn.cursor() as cur:
            for c in chunks:
                emb = embed_text(c.chunk_text)
                cur.execute(
                    "INSERT INTO policy_chunks (id, policy_id, chunk_text, embedding, metadata) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (c.id, c.policy_id, c.chunk_text, emb, _jsonb(c.metadata)),
                )
    return len(chunks)


def _jsonb(meta: dict) -> str:
    import json

    return json.dumps(meta)


if __name__ == "__main__":
    try:
        n = embed_to_postgres()
        print(f"embedded {n} policy chunks into Postgres (dim={settings.EMBEDDING_DIM})")
    except Exception as exc:  # noqa: BLE001
        store = build_inmemory_store()
        n = len(store._chunks)  # noqa: SLF001 - intentional introspection for the CLI summary
        print(f"Postgres unavailable ({exc.__class__.__name__}); built in-memory store: "
              f"{n} chunks (dim={settings.EMBEDDING_DIM})", file=sys.stderr)
