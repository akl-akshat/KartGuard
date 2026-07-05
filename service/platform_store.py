"""Central-platform registry: universal customers (phone-keyed) and per-client order DBs.

ReturnGuard as a platform: client companies (Zomato, Swiggy, Blinkit, Flipkart, Amazon…)
onboard with their policy documents (``service.policy_store``) **and their order data**
(mock here — clients add/remove orders through their portal). The platform builds a
**universal customer identity keyed by phone number**: the same phone seen across different
clients maps to ONE platform user, so their orders — and, critically, their **credibility
score** — follow them across every brand. A customer who files false claims on Zomato faces
stricter scrutiny on Amazon too.

Durable in the same SQLite file as the chat store (survives restarts; ephemeral only on
free-tier redeploys, where the demo seed repopulates it).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from service import chat_store
from service.chat_store import _LOCK, _conn

# Path-aware init guard: tests (and tools) repoint chat_store.DB_PATH at fresh files; the
# schema must be (re)applied per DB file, not once per process.
_INIT_PATH: str | None = None

# Order categories reuse db.dataset.CATEGORIES semantics so the whole decision core
# (eligibility, windows, perishable handling) applies unchanged to client orders.


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS platform_users (
                id         TEXT PRIMARY KEY,
                phone      TEXT NOT NULL UNIQUE,
                name       TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS client_orders (
                id          TEXT PRIMARY KEY,
                company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                phone       TEXT NOT NULL,
                title       TEXT NOT NULL,
                category    TEXT NOT NULL,
                price       REAL NOT NULL,
                payment_mode TEXT NOT NULL DEFAULT 'PREPAID',
                delivery_date TEXT,
                return_window_end TEXT,
                status      TEXT NOT NULL DEFAULT 'delivered',
                created_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corder_phone ON client_orders(phone);
            CREATE INDEX IF NOT EXISTS idx_corder_company ON client_orders(company_id);
            """
        )
    _INIT_PATH = chat_store.DB_PATH


def _norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


# ------------------------------------------------------------------ users
def upsert_user(phone: str, name: str) -> dict[str, Any]:
    """Phone number is the universal identity: the same phone always resolves to one user."""
    init()
    p = _norm_phone(phone)
    existing = get_user_by_phone(p)
    if existing:
        return existing
    uid = "PU-" + uuid.uuid4().hex[:8]
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO platform_users (id, phone, name, created_at) VALUES (?,?,?,?)",
                  (uid, p, name.strip(), chat_store._now()))
    return {"id": uid, "phone": p, "name": name.strip()}


def get_user(user_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM platform_users WHERE id=?", (user_id,)).fetchone()
    return {"id": r["id"], "phone": r["phone"], "name": r["name"]} if r else None


def get_user_by_phone(phone: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM platform_users WHERE phone=?", (_norm_phone(phone),)).fetchone()
    return {"id": r["id"], "phone": r["phone"], "name": r["name"]} if r else None


def list_users() -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM platform_users ORDER BY name").fetchall()
    return [{"id": r["id"], "phone": r["phone"], "name": r["name"]} for r in rows]


# ------------------------------------------------------------------ client orders
def add_order(company_id: str, phone: str, title: str, category: str, price: float,
              payment_mode: str = "PREPAID", delivery_date: str | None = None,
              return_window_end: str | None = None, status: str = "delivered") -> dict[str, Any]:
    init()
    oid = "PORD-" + uuid.uuid4().hex[:8].upper()
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO client_orders (id, company_id, phone, title, category, price, payment_mode, "
            "delivery_date, return_window_end, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (oid, company_id, _norm_phone(phone), title.strip(), category, float(price),
             payment_mode, delivery_date, return_window_end, status, chat_store._now()),
        )
    return get_order(oid)


def get_order(order_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM client_orders WHERE id=?", (order_id,)).fetchone()
    return _order_row(r) if r else None


def remove_order(order_id: str) -> bool:
    init()
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM client_orders WHERE id=?", (order_id,))
    return cur.rowcount > 0


def orders_for_phone(phone: str) -> list[dict[str, Any]]:
    """Every order this customer placed, across ALL client brands (the universal view)."""
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT co.*, comp.name AS brand FROM client_orders co "
            "JOIN companies comp ON comp.id = co.company_id "
            "WHERE co.phone=? ORDER BY co.created_at DESC", (_norm_phone(phone),)
        ).fetchall()
    return [_order_row(r) for r in rows]


def orders_for_company(company_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT co.*, comp.name AS brand FROM client_orders co "
            "JOIN companies comp ON comp.id = co.company_id "
            "WHERE co.company_id=? ORDER BY co.created_at DESC", (company_id,)
        ).fetchall()
    return [_order_row(r) for r in rows]


def _order_row(r) -> dict[str, Any]:
    keys = r.keys()
    return {
        "id": r["id"], "company_id": r["company_id"], "phone": r["phone"],
        "title": r["title"], "category": r["category"], "price": r["price"],
        "payment_mode": r["payment_mode"], "delivery_date": r["delivery_date"],
        "return_window_end": r["return_window_end"], "status": r["status"],
        "brand": r["brand"] if "brand" in keys else None,
        # shape-compat with the core order dict so the audited action tools work unchanged:
        # audit attribution uses the universal (phone) identity; the order id doubles as SKU.
        "customer_id": r["phone"], "sku": r["id"],
    }


# ------------------------------------------------------------------ refunds view
def refunds_summary(user_id: str) -> dict[str, Any]:
    """Per-brand resolution history for the customer dashboard (charts across all brands)."""
    init()
    user = get_user(user_id)
    if not user:
        return {"brands": [], "total_amount": 0.0, "count": 0}
    with _conn() as c:
        rows = c.execute(
            "SELECT s.state, s.company_id, (SELECT name FROM companies WHERE id=s.company_id) AS brand "
            "FROM sessions s WHERE s.customer_id=? AND s.status IN ('resolved','denied')",
            (user_id,)
        ).fetchall()
    import json as _json
    per: dict[str, dict[str, Any]] = {}
    total, count = 0.0, 0
    for r in rows:
        st = _json.loads(r["state"] or "{}")
        res = st.get("resolution")
        brand = r["brand"] or "ReturnGuard"
        b = per.setdefault(brand, {"brand": brand, "count": 0, "amount": 0.0, "actions": {}})
        if res:
            amt = float(res.get("amount") or 0)
            b["count"] += 1
            b["amount"] += amt
            act = res.get("action_type") or "other"
            b["actions"][act] = b["actions"].get(act, 0) + 1
            total += amt
            count += 1
    return {"brands": sorted(per.values(), key=lambda x: -x["amount"]),
            "total_amount": round(total, 2), "count": count}
