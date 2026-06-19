# Phase 4 stub — replaced by the real deterministic guardrail checker in Phase 6.
from agent.state import ResolutionState


def guardrails(state: ResolutionState) -> dict:
    return {"guardrail_status": "pass", "requires_human": state.get("requires_human", False)}
