"""Data-access repository: domain reads + resolution/audit/escalation persistence.

Two interchangeable implementations behind one Protocol (dependency inversion):

* :class:`InMemoryRepository` — seeded from :mod:`db.dataset`; used by tests, the eval
  harness, and offline demos. No infrastructure required.
* :class:`PostgresRepository` — the production store (psycopg), same Postgres that holds
  the pgvector index and the LangGraph checkpointer.

``audit_log`` is **append-only**: there is no update/delete method anywhere here, which
is how the insert-only invariant (FR-LOG-2, TOOL-1) is enforced at the application layer.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from db.dataset import Customer, Dataset, Order, build_dataset


@runtime_checkable
class Repository(Protocol):
    # --- domain reads (back the MCP servers) ---
    def get_order(self, order_id: str) -> Order | None: ...
    def get_customer(self, customer_id: str) -> Customer | None: ...
    def get_orders_for_customer(self, customer_id: str) -> list[Order]: ...
    def get_return_history(self, customer_id: str) -> dict[str, Any]: ...

    # --- resolutions ---
    def save_resolution(self, resolution: dict[str, Any]) -> None: ...
    def get_resolution(self, request_id: str) -> dict[str, Any] | None: ...
    def list_resolutions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...

    # --- audit (append-only) ---
    def append_audit(self, entry: dict[str, Any]) -> None: ...
    def get_audit(self, request_id: str, action_type: str | None = None) -> list[dict[str, Any]]: ...
    def count_auto_refunds_since(self, customer_id: str, since: date) -> int: ...

    # --- escalations ---
    def upsert_escalation(self, request_id: str, recommendation: dict[str, Any], assigned_to: str | None = None) -> None: ...
    def get_escalation(self, request_id: str) -> dict[str, Any] | None: ...
    def list_escalations(self, status: str = "pending") -> list[dict[str, Any]]: ...
    def set_escalation_decided(self, request_id: str, decision: str, reviewer_id: str | None) -> None: ...


class InMemoryRepository:
    """In-process repository seeded from the deterministic dataset."""

    def __init__(self, dataset: Dataset | None = None) -> None:
        ds = dataset or build_dataset()
        self._orders: dict[str, Order] = {o.id: o for o in ds.orders}
        self._customers: dict[str, Customer] = {c.id: c for c in ds.customers}
        self._resolutions: dict[str, dict[str, Any]] = {}
        self._audit: list[dict[str, Any]] = []
        self._escalations: dict[str, dict[str, Any]] = {}
        self.dataset = ds

    # --- domain reads ---
    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_customer(self, customer_id: str) -> Customer | None:
        return self._customers.get(customer_id)

    def get_orders_for_customer(self, customer_id: str) -> list[Order]:
        return [o for o in self._orders.values() if o.customer_id == customer_id]

    def get_return_history(self, customer_id: str) -> dict[str, Any]:
        c = self._customers.get(customer_id)
        if not c:
            return {"customer_id": customer_id, "found": False}
        return {
            "customer_id": customer_id,
            "found": True,
            "total_orders": c.total_orders,
            "total_returns": c.total_returns,
            "return_rate": c.return_rate,
            "cod_orders": c.cod_orders,
            "cod_refusals": c.cod_refusals,
            "risk_flags": list(c.risk_flags),
        }

    # --- resolutions ---
    def save_resolution(self, resolution: dict[str, Any]) -> None:
        self._resolutions[resolution["request_id"]] = dict(resolution)

    def get_resolution(self, request_id: str) -> dict[str, Any] | None:
        r = self._resolutions.get(request_id)
        return dict(r) if r else None

    def list_resolutions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = list(self._resolutions.values())
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [dict(r) for r in rows[:limit]]

    # --- audit (append-only) ---
    def append_audit(self, entry: dict[str, Any]) -> None:
        row = dict(entry)
        row.setdefault("created_at", datetime.now().isoformat())
        row["id"] = len(self._audit) + 1
        self._audit.append(row)

    def get_audit(self, request_id: str, action_type: str | None = None) -> list[dict[str, Any]]:
        rows = [a for a in self._audit if a["request_id"] == request_id]
        if action_type:
            rows = [a for a in rows if a["action_type"] == action_type]
        return [dict(a) for a in rows]

    def count_auto_refunds_since(self, customer_id: str, since: date) -> int:
        refund_actions = {"instant_refund", "store_credit_refund", "partial_refund"}
        count = 0
        for a in self._audit:
            if a.get("actor") != "agent":
                continue
            if a.get("action_type") not in refund_actions:
                continue
            if a.get("customer_id") != customer_id:
                continue
            created = a.get("created_at", "")
            try:
                when = datetime.fromisoformat(created).date()
            except (ValueError, TypeError):
                continue
            if when >= since:
                count += 1
        return count

    # --- escalations ---
    def upsert_escalation(self, request_id: str, recommendation: dict[str, Any], assigned_to: str | None = None) -> None:
        self._escalations[request_id] = {
            "request_id": request_id,
            "status": "pending",
            "recommendation": recommendation,
            "assigned_to": assigned_to,
            "decided_at": None,
            "decision": None,
        }

    def get_escalation(self, request_id: str) -> dict[str, Any] | None:
        e = self._escalations.get(request_id)
        return dict(e) if e else None

    def list_escalations(self, status: str = "pending") -> list[dict[str, Any]]:
        return [dict(e) for e in self._escalations.values() if e["status"] == status]

    def set_escalation_decided(self, request_id: str, decision: str, reviewer_id: str | None) -> None:
        e = self._escalations.get(request_id)
        if not e:
            return
        e["status"] = "decided"
        e["decision"] = decision
        e["assigned_to"] = reviewer_id or e.get("assigned_to")
        e["decided_at"] = datetime.now().isoformat()


def default_window_start(window_days: int) -> date:
    return date.today() - timedelta(days=window_days)


# ----------------------------------------------------------------------------
# Production Postgres implementation. Imported lazily so the offline path never
# needs psycopg installed.
# ----------------------------------------------------------------------------
class PostgresRepository:
    """psycopg-backed repository (production). Mirrors :class:`InMemoryRepository`."""

    def __init__(self, dsn: str) -> None:
        import psycopg  # lazy: only needed in production
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(dsn, row_factory=dict_row, autocommit=True)

    def _row_to_order(self, r: dict[str, Any]) -> Order:
        return Order(**{k: r[k] for k in Order.__dataclass_fields__})

    def _row_to_customer(self, r: dict[str, Any]) -> Customer:
        return Customer(**{k: r[k] for k in Customer.__dataclass_fields__})

    def get_order(self, order_id: str) -> Order | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = %s", (order_id,)).fetchone()
        return self._row_to_order(row) if row else None

    def get_customer(self, customer_id: str) -> Customer | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = %s", (customer_id,)).fetchone()
        return self._row_to_customer(row) if row else None

    def get_orders_for_customer(self, customer_id: str) -> list[Order]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orders WHERE customer_id = %s", (customer_id,)).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_return_history(self, customer_id: str) -> dict[str, Any]:
        c = self.get_customer(customer_id)
        if not c:
            return {"customer_id": customer_id, "found": False}
        return {
            "customer_id": customer_id, "found": True,
            "total_orders": c.total_orders, "total_returns": c.total_returns,
            "return_rate": float(c.return_rate), "cod_orders": c.cod_orders,
            "cod_refusals": c.cod_refusals, "risk_flags": list(c.risk_flags),
        }

    def save_resolution(self, resolution: dict[str, Any]) -> None:
        import json
        cols = [
            "request_id", "order_id", "customer_id", "issue_type", "root_cause",
            "risk_score", "risk_factors", "proposed_action", "executed_action", "amount",
            "expected_return_cost", "expected_saving", "requires_human", "human_decision",
            "rationale", "status", "trace_id", "resolved_at",
        ]
        jsonb = {"risk_factors", "proposed_action", "executed_action"}
        vals = [json.dumps(resolution.get(c)) if c in jsonb else resolution.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "request_id")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO resolutions ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT (request_id) DO UPDATE SET {updates}",
                vals,
            )

    def get_resolution(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM resolutions WHERE request_id = %s", (request_id,)).fetchone()
        return dict(row) if row else None

    def list_resolutions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = "SELECT * FROM resolutions"
        params: tuple[Any, ...] = ()
        if status:
            q += " WHERE status = %s"
            params = (status,)
        q += " ORDER BY created_at DESC LIMIT %s"
        params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def append_audit(self, entry: dict[str, Any]) -> None:
        import json
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (request_id, action_type, amount, actor, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                (entry["request_id"], entry["action_type"], entry.get("amount"),
                 entry["actor"], json.dumps(entry.get("payload", {}))),
            )

    def get_audit(self, request_id: str, action_type: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM audit_log WHERE request_id = %s"
        params: tuple[Any, ...] = (request_id,)
        if action_type:
            q += " AND action_type = %s"
            params += (action_type,)
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def count_auto_refunds_since(self, customer_id: str, since: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM audit_log a "
                "JOIN resolutions r ON r.request_id = a.request_id "
                "WHERE r.customer_id = %s AND a.actor = 'agent' "
                "AND a.action_type IN ('instant_refund','store_credit_refund','partial_refund') "
                "AND a.created_at >= %s",
                (customer_id, since),
            ).fetchone()
        return int(row["n"]) if row else 0

    def upsert_escalation(self, request_id: str, recommendation: dict[str, Any], assigned_to: str | None = None) -> None:
        import json
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO escalations (request_id, status, recommendation, assigned_to) "
                "VALUES (%s, 'pending', %s, %s) ON CONFLICT (request_id) DO UPDATE "
                "SET recommendation = EXCLUDED.recommendation, status = 'pending'",
                (request_id, json.dumps(recommendation), assigned_to),
            )

    def get_escalation(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM escalations WHERE request_id = %s", (request_id,)).fetchone()
        return dict(row) if row else None

    def list_escalations(self, status: str = "pending") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM escalations WHERE status = %s", (status,)).fetchall()
        return [dict(r) for r in rows]

    def set_escalation_decided(self, request_id: str, decision: str, reviewer_id: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE escalations SET status = 'decided', decision = %s, assigned_to = %s, "
                "decided_at = now() WHERE request_id = %s",
                (decision, reviewer_id, request_id),
            )
