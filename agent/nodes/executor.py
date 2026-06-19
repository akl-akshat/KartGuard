# Phase 4 stub — replaced by the real idempotent executor in Phase 7.
from agent.state import ResolutionState


def executor(state: ResolutionState) -> dict:
    proposed = state.get("proposed_action") or {}
    return {"executed_action": proposed, "outcome": {"status": "resolved"}}
