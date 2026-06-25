"""fraud-service MCP server (read-only): get_risk_signals (MCP-1).

Returns deterministic risk signals derived from seeded fields. Run standalone::
    python -m mcp_servers.fraud_service
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_servers._base import build_data_access, get_logger

log = get_logger("mcp.fraud")
_data = build_data_access()
mcp = FastMCP("fraud-service")


@mcp.tool
def get_risk_signals(customer_id: str, order_id: str) -> dict | None:
    """Return raw fraud/abuse signals for (customer_id, order_id), or null if missing."""
    log.info("get_risk_signals customer=%s order=%s", customer_id, order_id)
    return _data.get_risk_signals(customer_id, order_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8103)
