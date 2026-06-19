"""Context Gatherer node (FR-CTX-1..3).

Fetches order/customer context via the data-access seam (MCP in production). On a missing
order/customer it routes to a graceful "cannot locate" reply and **never fabricates facts**
— it returns ``None`` contexts, not invented values.
"""

from __future__ import annotations

from agent.deps import get_deps
from agent.state import ResolutionState, ResolutionStatus


def context(state: ResolutionState) -> dict:
    da = get_deps().data_access
    order_id = state.get("order_id")
    customer_id = state.get("customer_id")

    order = da.get_order(order_id) if order_id else None
    if order and not customer_id:
        customer_id = order["customer_id"]
    customer = da.get_customer(customer_id) if customer_id else None

    if order is None or customer is None:
        return {
            "order_context": None,
            "customer_context": None,
            "customer_id": customer_id,
            "status": ResolutionStatus.not_found.value,
            "rationale": "Order or customer could not be located.",
        }
    return {"order_context": order, "customer_context": customer, "customer_id": customer_id}
