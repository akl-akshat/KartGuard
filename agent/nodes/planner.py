"""Resolution Planner node (FR-PLN-1..5).

Generates a ranked candidate list and selects the cost-optimal, eligible, policy-compliant
action under the §9.4 constraints (with the satisfaction floor). The planner **never
executes** anything — no DB writes, no tool calls (FR-PLN-3).
"""

from __future__ import annotations

from agent.decision.eligibility import eligible_actions
from agent.decision.select import select_action
from agent.state import ResolutionState


def planner(state: ResolutionState) -> dict:
    order = state.get("order_context") or {}
    root_cause = state.get("root_cause") or "genuine_other"
    within_window = bool(state.get("within_return_window"))

    if not order:  # defensive: should not reach here (context routes misses away)
        return {"proposed_action": {"action_type": "escalate_to_human", "amount": 0.0, "params": {}},
                "candidate_actions": [], "requires_human": True}

    category = order.get("category", "*")
    payment_mode = order.get("payment_mode")
    eligible = eligible_actions(root_cause, category, within_window, payment_mode)
    proposed, candidates = select_action(root_cause, eligible, order, within_window)

    requires_human = state.get("requires_human", False) or proposed["action_type"] == "escalate_to_human"
    return {
        "candidate_actions": candidates,
        "proposed_action": proposed,
        "requires_human": requires_human,
        "expected_return_cost": proposed.get("expected_return_cost"),
        "expected_saving": proposed.get("expected_saving"),
        "rationale": proposed.get("rationale"),
    }
