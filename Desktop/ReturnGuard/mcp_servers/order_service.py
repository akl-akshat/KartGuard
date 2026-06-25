"""order-service MCP server (read-only): get_order, get_order_status (MCP-1).

Run standalone::  python -m mcp_servers.order_service
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_servers._base import build_data_access, get_logger

log = get_logger("mcp.order")
_data = build_data_access()
mcp = FastMCP("order-service")


@mcp.tool
def get_order(order_id: str) -> dict | None:
    """Return the full order record for ``order_id`` (or null if it does not exist)."""
    log.info("get_order id=%s", order_id)
    return _data.get_order(order_id)


@mcp.tool
def get_order_status(order_id: str) -> dict | None:
    """Return delivery status + window facts for ``order_id`` (or null)."""
    log.info("get_order_status id=%s", order_id)
    return _data.get_order_status(order_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8101)
