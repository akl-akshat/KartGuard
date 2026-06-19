"""Root-Cause Diagnoser node (FR-RC-1..3).

Assigns exactly one ``root_cause``, justified by the request + context, preferring a
conservative cause under weak evidence (FR-RC-3). On LLM failure it degrades to a
conservative cause and flags escalation rather than guessing a costly action.
"""

from __future__ import annotations

from agent.deps import get_deps
from agent.llm import LLMError
from agent.state import ResolutionState


def diagnosis(state: ResolutionState) -> dict:
    try:
        out = get_deps().llm.diagnose(
            state.get("raw_request", ""),
            state.get("issue_type") or "other",
            state.get("order_context"),
            state.get("customer_context"),
            state.get("risk_score"),
        )
        return {"root_cause": out.root_cause, "rationale": out.rationale}
    except LLMError:
        return {"root_cause": "genuine_other", "requires_human": True,
                "rationale": "diagnosis fallback → escalation (LLM-2)"}
