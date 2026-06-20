"""Constrained action selection (SRS §9.4) — pure.

Given the eligible action set, choose ``proposed_action`` = the **minimum expected-cost
remedy** that is feasible and satisfies the satisfaction floor. Non-remedy actions
(escalate / deny / info) are chosen only when no genuine remedy is eligible — which is why
cost-minimisation never collapses to "deny everything to save money".
"""

from __future__ import annotations

from typing import Any

from agent.decision.cost_model import action_cost, expected_return_cost, expected_saving, order_value
from agent.decision.eligibility import satisfaction_ok
from config.settings import settings

# Actions that genuinely remedy the customer (the cost-minimisation pool).
REMEDY_ACTIONS = {
    "instant_refund", "partial_refund", "store_credit_refund", "free_exchange",
    "exchange_with_size_guide", "retention_coupon", "expedited_replacement", "goodwill_credit",
}

# Deflection-first tiebreak when expected costs are equal (lower rank = preferred).
_TIEBREAK = {
    "exchange_with_size_guide": 0, "free_exchange": 1, "retention_coupon": 2,
    "expedited_replacement": 3, "goodwill_credit": 4, "partial_refund": 5,
    "store_credit_refund": 6, "instant_refund": 7,
}


def default_amount(action_type: str, order: dict[str, Any]) -> float:
    value = order_value(order)
    if action_type in ("instant_refund", "store_credit_refund"):
        return round(value, 2)
    if action_type == "partial_refund":
        return round(value * settings.PARTIAL_REFUND_FRACTION, 2)
    if action_type == "retention_coupon":
        return round(min(settings.MAX_COUPON_PCT * value, settings.MAX_COUPON_ABS), 2)
    if action_type == "goodwill_credit":
        return round(min(settings.MAX_GOODWILL_CREDIT, settings.DEFECT_GOODWILL_DEFAULT), 2)
    return 0.0


def _feasible(action_type: str, amount: float, order: dict[str, Any], root_cause: str) -> bool:
    if not satisfaction_ok(action_type, root_cause):
        return False
    value = order_value(order)
    if action_type in ("instant_refund", "partial_refund", "store_credit_refund", "goodwill_credit"):
        if amount > value + 1e-6:
            return False
    if action_type == "retention_coupon":
        if amount > settings.MAX_COUPON_ABS + 1e-6 or amount > settings.MAX_COUPON_PCT * value + 1e-6:
            return False
    return True


def _candidate(action_type: str, order: dict[str, Any]) -> dict[str, Any]:
    amt = default_amount(action_type, order)
    return {
        "action_type": action_type,
        "amount": amt,
        "params": {},
        "eligible": True,
        "estimated_cost": action_cost(action_type, amt, order),
    }


def select_action(
    root_cause: str,
    eligible: set[str],
    order: dict[str, Any],
    within_window: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = [_candidate(a, order) for a in sorted(eligible)]
    candidates.sort(key=lambda c: (c["estimated_cost"], _TIEBREAK.get(c["action_type"], 9)))

    remedies = [
        c for c in candidates
        if c["action_type"] in REMEDY_ACTIONS
        and _feasible(c["action_type"], c["amount"], order, root_cause)
    ]

    if remedies:
        proposed = dict(min(remedies, key=lambda c: (c["estimated_cost"],
                                                     _TIEBREAK.get(c["action_type"], 9))))
        # Defect sweetener: add a small goodwill credit on top of a replacement (A.2).
        if root_cause == "defect_damage" and proposed["action_type"] == "expedited_replacement":
            proposed["params"]["goodwill"] = round(
                min(settings.MAX_GOODWILL_CREDIT, settings.DEFECT_GOODWILL_DEFAULT), 2
            )
    elif "escalate_to_human" in eligible:
        proposed = {"action_type": "escalate_to_human", "amount": 0.0, "params": {}, "estimated_cost": 0.0}
    elif "deny_with_explanation" in eligible:
        proposed = {"action_type": "deny_with_explanation", "amount": 0.0, "params": {}, "estimated_cost": 0.0}
    else:
        proposed = {"action_type": "provide_information", "amount": 0.0, "params": {}, "estimated_cost": 0.0}

    proposed["eligible"] = True
    proposed["rationale"] = _rationale(proposed, root_cause, order, within_window)
    proposed["expected_return_cost"] = expected_return_cost(order)
    proposed["expected_saving"] = expected_saving(order, proposed["action_type"], proposed.get("amount", 0.0))
    return proposed, candidates


def _rationale(action: dict[str, Any], root_cause: str, order: dict[str, Any], within_window: bool) -> str:
    at = action["action_type"]
    crc = expected_return_cost(order)
    if at in ("exchange_with_size_guide", "free_exchange"):
        return f"Deflect a {root_cause} via exchange (C_a={action['estimated_cost']} < C_return={crc})."
    if at == "retention_coupon":
        return f"Discretionary return; a capped coupon is cheaper than a full return (C_return={crc})."
    if at == "expedited_replacement":
        return f"Genuine fault → expedited replacement (satisfaction floor; C_return={crc})."
    if at == "instant_refund":
        return f"Eligible refund to original method (C_return={crc})."
    if at == "deny_with_explanation":
        return f"Policy disallows (within_window={within_window}); courteous denial, no money."
    if at == "escalate_to_human":
        return "Risk/ambiguity gate tripped — route to a human reviewer with this recommendation."
    return f"Selected {at} for {root_cause}."
