"""Per-company (multi-tenant) policy store — upload, chunk, embed, semantic search.

Any company (e.g. Zomato, Swiggy) can upload its refund/replacement/guideline documents.
Each document is split into paragraphs, embedded (via the same offline-capable embedder the
core RAG uses — a real embedding API plugs in behind ``EMBEDDING_PROVIDER``), and stored
durably in the chat SQLite DB. A customer query is embedded and cosine-matched against the
selected company's chunks; the **top-5 most relevant paragraphs** become the grounding
context the agent answers from — so every tenant's support runs on *their* policy, not ours.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from policies.embedder import cosine, embed_text
from service import chat_store
from service.chat_store import _LOCK, _conn

TOP_K = 5
_MAX_CHUNK_CHARS = 1200
_MIN_CHUNK_CHARS = 40
_MAX_CHUNKS_PER_DOC = 400
_MAX_DOC_CHARS = 400_000

# Path-aware init guard: tests repoint chat_store.DB_PATH at fresh files; the schema must be
# (re)applied per DB file, not once per process.
_INIT_PATH: str | None = None


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_chunks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                doc_name   TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                text       TEXT NOT NULL,
                embedding  TEXT NOT NULL,          -- JSON array (L2-normalised)
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_company ON policy_chunks(company_id);
            """
        )
    _INIT_PATH = chat_store.DB_PATH


# ------------------------------------------------------------------ chunking
def chunk_document(text: str) -> list[str]:
    """Split a policy document into paragraph chunks suitable for retrieval.

    Paragraphs (blank-line separated) are the unit; a markdown heading is glued onto the
    paragraph that follows it so retrieved chunks stay self-describing. Tiny fragments merge
    into their neighbour; over-long paragraphs split on sentence boundaries.
    """
    text = (text or "")[:_MAX_DOC_CHARS].replace("\r\n", "\n")
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    pending_heading = ""
    for p in raw:
        if re.fullmatch(r"#{1,6}\s+.{1,120}", p):  # a lone heading → prefix to the next chunk
            pending_heading = p.lstrip("# ").strip()
            continue
        if pending_heading:
            p = f"{pending_heading} — {p}"
            pending_heading = ""
        while len(p) > _MAX_CHUNK_CHARS:  # split long paragraphs on sentence boundaries
            cut = p.rfind(". ", 0, _MAX_CHUNK_CHARS)
            cut = cut + 1 if cut > _MIN_CHUNK_CHARS else _MAX_CHUNK_CHARS
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            if len(p) < _MIN_CHUNK_CHARS and chunks:
                chunks[-1] = chunks[-1] + " " + p
            else:
                chunks.append(p)
    return chunks[:_MAX_CHUNKS_PER_DOC]


# ------------------------------------------------------------------ companies
def create_company(name: str) -> dict[str, Any]:
    init()
    name = name.strip()
    existing = get_company_by_name(name)
    if existing:
        return existing
    cid = "co_" + uuid.uuid4().hex[:10]
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO companies (id, name, created_at) VALUES (?,?,?)",
                  (cid, name, chat_store._now()))
    return {"id": cid, "name": name}


def get_company(company_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    return {"id": r["id"], "name": r["name"]} if r else None


def get_company_by_name(name: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM companies WHERE name=? COLLATE NOCASE", (name.strip(),)).fetchone()
    return {"id": r["id"], "name": r["name"]} if r else None


def list_companies() -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT co.id, co.name, COUNT(DISTINCT pc.doc_name) AS docs, COUNT(pc.id) AS chunks "
            "FROM companies co LEFT JOIN policy_chunks pc ON pc.company_id = co.id "
            "GROUP BY co.id ORDER BY co.name"
        ).fetchall()
    return [{"id": r["id"], "name": r["name"], "docs": r["docs"], "chunks": r["chunks"]} for r in rows]


# ------------------------------------------------------------------ documents
def upload_policy(company_id: str, doc_name: str, text: str) -> dict[str, Any]:
    """Chunk + embed + store a policy document (replacing any same-named doc)."""
    from service import cache

    init()
    chunks = chunk_document(text)
    now = chat_store._now()
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM policy_chunks WHERE company_id=? AND doc_name=?", (company_id, doc_name))
        for i, ch in enumerate(chunks):
            c.execute(
                "INSERT INTO policy_chunks (company_id, doc_name, seq, text, embedding, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (company_id, doc_name, i, ch, json.dumps(embed_text(ch)), now),
            )
    cache.invalidate(f"chunks:{company_id}")   # fresh corpus on the next turn
    return {"company_id": company_id, "doc_name": doc_name, "chunks": len(chunks)}


def delete_document(company_id: str, doc_name: str) -> bool:
    """Remove one policy document; the RAG corpus updates immediately."""
    from service import cache

    init()
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM policy_chunks WHERE company_id=? AND doc_name=?",
                        (company_id, doc_name))
    cache.invalidate(f"chunks:{company_id}")
    return cur.rowcount > 0


def list_documents(company_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT doc_name, COUNT(*) AS chunks FROM policy_chunks WHERE company_id=? "
            "GROUP BY doc_name ORDER BY doc_name", (company_id,)
        ).fetchall()
    return [{"doc_name": r["doc_name"], "chunks": r["chunks"]} for r in rows]


# ------------------------------------------------------------------ search (RAG)
def _chunks_for(company_id: str) -> list[dict[str, Any]]:
    """The company's chunk corpus with parsed embeddings — cached (hit on every chat turn)."""
    from service import cache

    key = f"chunks:{company_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT doc_name, seq, text, embedding FROM policy_chunks WHERE company_id=?",
            (company_id,)
        ).fetchall()
    out = [{"doc_name": r["doc_name"], "seq": r["seq"], "text": r["text"],
            "emb": json.loads(r["embedding"])} for r in rows]
    cache.set(key, out)
    return out


def search(company_id: str, query: str, k: int = TOP_K) -> list[dict[str, Any]]:
    """Semantic search: embed the query, cosine-rank the company's chunks, return top-k."""
    q = embed_text(query or "")
    scored = [
        {"doc_name": ch["doc_name"], "seq": ch["seq"], "text": ch["text"],
         "score": round(cosine(q, ch["emb"]), 4)}
        for ch in _chunks_for(company_id)
    ]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:k]
