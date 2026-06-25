"""Data-access seam between cognition (graph nodes) and the data layer (D4).

The graph nodes depend only on the :class:`DataAccess` Protocol, never on Postgres or a
live MCP server directly:

* :class:`LocalDataAccess` — repository-backed, in-process (offline tests, eval, demos).
* :class:`MCPDataAccess` — calls the read-only FastMCP servers over streamable HTTP
  (production), preserving the SRS's MCP boundary (MCP-1).

Both return plain dicts so nodes are transport-agnostic.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol

from db.repository import InMemoryRepository, Repository
from mcp_servers.signals import compute_risk_signals


class DataAccess(Protocol):
    def get_order(self, order_id: str) -> dict[str, Any] | None: ...
    def get_order_status(self, order_id: str) -> dict[str, Any] | None: ...
    def get_customer(self, customer_id: str) -> dict[str, Any] | None: ...
    def get_return_history(self, customer_id: str) -> dict[str, Any]: ...
    def get_risk_signals(self, customer_id: str, order_id: str) -> dict[str, Any] | None: ...


def _order_dict(order: Any) -> dict[str, Any]:
    d = asdict(order)
    for k in ("order_date", "dispatch_date", "delivery_date", "return_window_end"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _customer_dict(cust: Any) -> dict[str, Any]:
    d = asdict(cust)
    d["signup_date"] = d["signup_date"].isoformat()
    d["ltv"] = float(d["ltv"])
    d["return_rate"] = float(d["return_rate"])
    return d


class LocalDataAccess:
    """In-process data access used offline; mirrors the MCP server contracts exactly."""

    def __init__(self, repo: Repository | None = None) -> None:
        self.repo: Repository = repo or InMemoryRepository()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        o = self.repo.get_order(order_id)
        return _order_dict(o) if o else None

    def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        o = self.repo.get_order(order_id)
        if not o:
            return None
        return {
            "order_id": o.id,
            "delivery_status": o.delivery_status,
            "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
            "return_window_end": o.return_window_end.isoformat() if o.return_window_end else None,
        }

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        c = self.repo.get_customer(customer_id)
        return _customer_dict(c) if c else None

    def get_return_history(self, customer_id: str) -> dict[str, Any]:
        return self.repo.get_return_history(customer_id)

    def get_risk_signals(self, customer_id: str, order_id: str) -> dict[str, Any] | None:
        c = self.repo.get_customer(customer_id)
        o = self.repo.get_order(order_id)
        if not c or not o:
            return None
        return compute_risk_signals(c, o)


class MCPDataAccess:
    """Production data access: calls the read-only MCP servers (streamable HTTP).

    Imported lazily so the offline path never needs the MCP client installed.
    """

    def __init__(self, order_url: str, customer_url: str, fraud_url: str) -> None:
        self._urls = {"order": order_url, "customer": customer_url, "fraud": fraud_url}

    def _call(self, server: str, tool: str, **kwargs: Any) -> Any:
        from fastmcp import Client  # lazy

        import anyio

        async def _run() -> Any:
            async with Client(self._urls[server]) as client:
                res = await client.call_tool(tool, kwargs)
                return res.data if hasattr(res, "data") else res

        return anyio.run(_run)

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self._call("order", "get_order", order_id=order_id)

    def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        return self._call("order", "get_order_status", order_id=order_id)

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return self._call("customer", "get_customer", customer_id=customer_id)

    def get_return_history(self, customer_id: str) -> dict[str, Any]:
        return self._call("customer", "get_return_history", customer_id=customer_id)

    def get_risk_signals(self, customer_id: str, order_id: str) -> dict[str, Any] | None:
        return self._call("fraud", "get_risk_signals", customer_id=customer_id, order_id=order_id)
