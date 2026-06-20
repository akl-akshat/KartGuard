"""Guardrail Checker node (FR-GRD-1..4, NFR-SAF-1/2).

Thin wrapper over the pure :func:`agent.decision.guardrails.evaluate_guardrails`: it fetches
the one piece of state the pure function needs from the data layer (the customer's recent
auto-refund count for the rate limit) and applies the verdict to state. The pure evaluator
sees no free text, so guardrails cannot be bypassed by request content or model output.
"""

from __future__ import annotations

from datetime import timedelta

from agent.decision.guardrails import evaluate_guardrails
from agent.deps import get_deps
from agent.state import ResolutionState
from config.settings import settings


def guardrails(state: ResolutionState) -> dict:
    proposed = state.get("proposed_action") or {"action_type": "provide_information"}
    order = state.get("order_context") or {}
    customer_id = state.get("customer_id")

    since = settings.as_of_date - timedelta(days=settings.AUTO_REFUND_RATE_WINDOW_DAYS)
    auto_refund_count = (
        get_deps().repo.count_auto_refunds_since(customer_id, since) if customer_id else 0
    )

    result = evaluate_guardrails(proposed, order, state.get("risk_score"), auto_refund_count)
    requires_human = state.get("requires_human", False) or result.requires_human
    return {
        "proposed_action": result.action,
        "guardrail_status": result.status,
        "requires_human": requires_human,
        "rationale": (state.get("rationale") or "") + (
            f" | guardrails: {', '.join(result.notes)}" if result.notes else ""
        ),
    }
