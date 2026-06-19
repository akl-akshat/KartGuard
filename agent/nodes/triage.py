"""Intake & Triage node (FR-TRI-1..5, CON-6).

Classifies ``issue_type``, extracts ids, and may ask one clarifying question — all while
treating ``raw_request`` strictly as untrusted DATA. On LLM failure it degrades to
escalation (LLM-2), never to an action.
"""

from __future__ import annotations

from agent.deps import get_deps
from agent.llm import LLMError
from agent.state import ResolutionState


def triage(state: ResolutionState) -> dict:
    n = state.get("iteration_count", 0) + 1
    try:
        out = get_deps().llm.triage(
            state.get("raw_request", ""), state.get("order_id"), state.get("customer_id")
        )
    except LLMError:
        # Parse/validation failure → safe degrade to escalation (no action taken).
        return {"issue_type": "other", "clarification_needed": False,
                "requires_human": True, "iteration_count": n}
    # Ask at most one clarifying question per invocation (FR-TRI-3); the cap bounds the loop.
    need = bool(out.clarification_needed and n <= 1)
    return {
        "issue_type": out.issue_type,
        "order_id": out.order_id,
        "customer_id": out.customer_id,
        "clarification_needed": need,
        "clarification_question": out.clarification_question if need else None,
        "iteration_count": n,
    }
