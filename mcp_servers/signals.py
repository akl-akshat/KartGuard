"""Deterministic fraud/risk signal computation (backs the fraud MCP server).

Kept separate from the MCP transport so both the standalone ``fraud-service`` server and
the offline ``LocalDataAccess`` compute identical signals. The Risk Assessor node
(Phase 5) combines these raw signals with an LLM nuance pass into ``risk_score``.
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from db.dataset import HIGH_RTO_PINCODES, Customer, Order

# Per-category abuse propensity (size bracketing / wardrobing tendency by category).
CATEGORY_ABUSE: dict[str, float] = {
    "apparel": 0.60,
    "footwear": 0.45,
    "beauty": 0.50,
    "innerwear": 0.40,
    "electronics": 0.30,
    "home": 0.25,
    "books": 0.20,
    "grocery": 0.15,
}


def compute_risk_signals(customer: Customer, order: Order) -> dict[str, Any]:
    order_value = float(order.price) * order.qty
    cod_refusal_rate = round(customer.cod_refusals / customer.cod_orders, 3) if customer.cod_orders else 0.0
    wardrobing = order.category in ("apparel", "footwear") and customer.return_rate >= 0.45
    return {
        "customer_id": customer.id,
        "order_id": order.id,
        "return_rate": float(customer.return_rate),
        "total_returns": customer.total_returns,
        "total_orders": customer.total_orders,
        "cod_refusal_rate": cod_refusal_rate,
        "cod_refusals": customer.cod_refusals,
        "order_value": order_value,
        "high_value_order": order_value > settings.MAX_AUTO_REFUND_ABS,
        "category": order.category,
        "category_abuse_propensity": CATEGORY_ABUSE.get(order.category, 0.3),
        "region": customer.region,
        "pincode": customer.pincode,
        "region_rto_baseline": 0.40 if customer.pincode in HIGH_RTO_PINCODES else 0.15,
        "risk_flags": list(customer.risk_flags),
        "wardrobing_suspected": wardrobing,
    }
