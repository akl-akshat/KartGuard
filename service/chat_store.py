"""Persistent chat store (SQLite).

Real server-side persistence for support conversations: sessions + messages survive page
reloads, session switches, AND server restarts (a file-backed DB, no Docker needed). Each
session carries its own JSON ``state`` (phase, collected fields, proposed/executed action),
so conversations are independent and stateful.

A connection is opened per call (FastAPI runs sync routes in a threadpool), guarded by a
module lock for writes — simple and safe for the demo's concurrency.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

DB_PATH = os.environ.get("RG_CHAT_DB", os.path.join(os.path.dirname(__file__), "..", ".rg_chat.db"))
_LOCK = threading.Lock()
_INIT = False


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    global _INIT
    if _INIT:
        return
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                order_id    TEXT,
                title       TEXT NOT NULL,
                phase       TEXT NOT NULL DEFAULT 'greeting',
                status      TEXT NOT NULL DEFAULT 'open',
                state       TEXT NOT NULL DEFAULT '{}',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role       TEXT NOT NULL,            -- user | assistant
                text       TEXT NOT NULL,
                meta       TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_sess_cust ON sessions(customer_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS credibility (
                customer_id   TEXT PRIMARY KEY,
                score         REAL NOT NULL,
                genuine_count INTEGER NOT NULL DEFAULT 0,
                denied_count  INTEGER NOT NULL DEFAULT 0,
                false_count   INTEGER NOT NULL DEFAULT 0,
                updated_at    REAL NOT NULL
            );
            """
        )
        # migration: sessions gained a per-tenant policy binding (multi-company support)
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN company_id TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    _INIT = True


def _now() -> float:
    return time.time()


# ------------------------------------------------------------------ sessions
def create_session(customer_id: str, order_id: str | None, title: str,
                   state: dict[str, Any] | None = None, company_id: str | None = None) -> dict[str, Any]:
    init()
    sid = "sess_" + uuid.uuid4().hex[:12]
    now = _now()
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO sessions (id, customer_id, order_id, title, phase, status, state, "
            "company_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, customer_id, order_id, title, "greeting", "open", json.dumps(state or {}),
             company_id, now, now),
        )
    return get_session(sid)


def get_session(session_id: str, with_messages: bool = False) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    s = _sess_row(row)
    if with_messages:
        s["messages"] = get_messages(session_id)
    return s


def list_sessions(customer_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT s.*, (SELECT text FROM messages m WHERE m.session_id=s.id ORDER BY m.id DESC LIMIT 1) AS last "
            "FROM sessions s WHERE customer_id=? ORDER BY updated_at DESC",
            (customer_id,),
        ).fetchall()
    out = []
    for r in rows:
        s = _sess_row(r)
        s["last_message"] = r["last"]
        s.pop("state", None)
        out.append(s)
    return out


def list_reviews() -> list[dict[str, Any]]:
    """Chat sessions awaiting a human review (escalated), newest first — for the ops console."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM sessions WHERE status='escalated' ORDER BY updated_at DESC").fetchall()
    return [_sess_row(r) for r in rows]


def update_session(session_id: str, *, phase: str | None = None, status: str | None = None,
                   order_id: str | None = None, title: str | None = None,
                   state: dict[str, Any] | None = None) -> None:
    init()
    sets, vals = [], []
    for col, v in (("phase", phase), ("status", status), ("title", title)):
        if v is not None:
            sets.append(f"{col}=?")
            vals.append(v)
    if order_id is not None:
        sets.append("order_id=?")
        vals.append(order_id)
    if state is not None:
        sets.append("state=?")
        vals.append(json.dumps(state))
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(session_id)
    with _LOCK, _conn() as c:
        c.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", vals)


def delete_session(session_id: str) -> None:
    init()
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        c.execute("DELETE FROM sessions WHERE id=?", (session_id,))


# ------------------------------------------------------------------ messages
def add_message(session_id: str, role: str, text: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    init()
    now = _now()
    with _LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (session_id, role, text, meta, created_at) VALUES (?,?,?,?,?)",
            (session_id, role, text, json.dumps(meta or {}), now),
        )
        mid = cur.lastrowid
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
    return {"id": mid, "session_id": session_id, "role": role, "text": text, "meta": meta or {}, "created_at": now}


def get_messages(session_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
    return [{"id": r["id"], "role": r["role"], "text": r["text"],
             "meta": json.loads(r["meta"]), "created_at": r["created_at"]} for r in rows]


# ------------------------------------------------------------------ credibility
# A durable, mutable per-customer "credit score" for claim trustworthiness. Survives
# restart (file-backed). Read at decision time; written when a claim outcome is known
# (typically a human review). Never surfaced to the customer.
def get_credibility(customer_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM credibility WHERE customer_id=?", (customer_id,)).fetchone()
    if not row:
        return None
    return {"customer_id": row["customer_id"], "score": row["score"],
            "genuine_count": row["genuine_count"], "denied_count": row["denied_count"],
            "false_count": row["false_count"]}


def save_credibility(cred: dict[str, Any]) -> None:
    init()
    now = _now()
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO credibility (customer_id, score, genuine_count, denied_count, false_count, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(customer_id) DO UPDATE SET score=excluded.score, "
            "genuine_count=excluded.genuine_count, denied_count=excluded.denied_count, "
            "false_count=excluded.false_count, updated_at=excluded.updated_at",
            (cred["customer_id"], float(cred["score"]), int(cred.get("genuine_count", 0)),
             int(cred.get("denied_count", 0)), int(cred.get("false_count", 0)), now),
        )


def _sess_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"], "customer_id": r["customer_id"], "order_id": r["order_id"],
        "title": r["title"], "phase": r["phase"], "status": r["status"],
        "state": json.loads(r["state"]),
        "company_id": r["company_id"] if "company_id" in r.keys() else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }
