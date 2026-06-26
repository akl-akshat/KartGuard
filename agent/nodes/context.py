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
    supplied_customer_id = state.get("customer_id")

    order = da.get_order(order_id) if order_id else None
    if order is None:
        return {
            "order_context": None,
            "customer_context": None,
            "customer_id": supplied_customer_id,
            "status": ResolutionStatus.not_found.value,
            "rationale": "Order could not be located.",
        }

    # D-01: the AUTHORITATIVE customer for risk and policy is the order's true owner — never
    # the caller-supplied id (customer input is untrusted, NFR-SEC-2). A supplied id that
    # disagrees is an ownership mismatch: use the true owner AND route to human verification
    # so an attacker cannot attach a low-risk identity to another customer's order (FR-RSK-1).
    true_owner = order["customer_id"]
    customer = da.get_customer(true_owner)
    if customer is None:
        return {
            "order_context": None,
            "customer_context": None,
            "customer_id": true_owner,
            "status": ResolutionStatus.not_found.value,
            "rationale": "Customer for the order could not be located.",
        }

    out: dict = {"order_context": order, "customer_context": customer, "customer_id": true_owner}
    if supplied_customer_id and supplied_customer_id != true_owner:
        out["requires_human"] = True
        out["risk_factors"] = ["ownership_mismatch"]
        out["rationale"] = "Supplied customer does not own this order; routed for verification."
    return out
