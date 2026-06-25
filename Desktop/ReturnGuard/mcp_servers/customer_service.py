"""customer-service MCP server (read-only): get_customer, get_return_history (MCP-1).

Run standalone::  python -m mcp_servers.customer_service
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_servers._base import build_data_access, get_logger

log = get_logger("mcp.customer")
_data = build_data_access()
mcp = FastMCP("customer-service")


@mcp.tool
def get_customer(customer_id: str) -> dict | None:
    """Return the customer record for ``customer_id`` (or null if not found)."""
    log.info("get_customer id=%s", customer_id)
    return _data.get_customer(customer_id)


@mcp.tool
def get_return_history(customer_id: str) -> dict:
    """Return aggregate return history for ``customer_id``."""
    log.info("get_return_history id=%s", customer_id)
    return _data.get_return_history(customer_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8102)
