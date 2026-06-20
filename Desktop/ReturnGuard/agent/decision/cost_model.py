"""Cost model (SRS §9.1) — pure, deterministic, config-driven.

* ``expected_return_cost(order)`` — the platform cost of allowing a full return/RTO.
* ``action_cost(action_type, amount, order)`` — the cost of a candidate action.

No LLM, no DB: just arithmetic over the order facts and Appendix-B-style constants.
"""

from __future__ import annotations

from typing import Any

from config.settings import settings


def order_value(order: dict[str, Any]) -> float:
    return float(order["price"]) * int(order.get("qty", 1))


def expected_return_cost(order: dict[str, Any]) -> float:
    """C_return ≈ reverse logistics + restocking + lost margin·P(unsellable) [+ RTO forward]."""
    value = order_value(order)
    cost = (
        settings.RETURN_REVERSE_LOGISTICS
        + settings.RETURN_RESTOCKING
        + settings.RETURN_MARGIN_RATE * value * settings.RETURN_P_UNSELLABLE
    )
    if order.get("delivery_status") == "rto":
        cost += settings.RTO_FORWARD_COST
    return round(cost, 2)


def action_cost(action_type: str, amount: float, order: dict[str, Any]) -> float:
    """Expected platform cost of an action (C_a). Full refund of a delivered item = value."""
    value = order_value(order)
    if action_type in ("instant_refund", "store_credit_refund"):
        return round(value, 2)
    if action_type == "partial_refund":
        return round(float(amount), 2)
    if action_type == "retention_coupon":
        return round(float(amount) * settings.COUPON_REDEMPTION_RATE, 2)
    if action_type in ("free_exchange", "exchange_with_size_guide"):
        return round(settings.EXCHANGE_SHIPPING_COST, 2)
    if action_type == "expedited_replacement":
        return round(settings.REPLACEMENT_DELTA_COST, 2)
    if action_type == "goodwill_credit":
        return round(float(amount), 2)
    # deny_with_explanation, provide_information, escalate_to_human cost nothing here.
    return 0.0


def expected_saving(order: dict[str, Any], action_type: str, amount: float) -> float:
    """C_return − C_a, floored at 0 (used by metrics / FR-RPT INR-saved)."""
    return round(max(0.0, expected_return_cost(order) - action_cost(action_type, amount, order)), 2)
