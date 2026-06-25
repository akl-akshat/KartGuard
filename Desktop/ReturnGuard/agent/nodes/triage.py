# Phase 4 stub — replaced by the real LLM triage in Phase 5.
from agent.state import ResolutionState


def triage(state: ResolutionState) -> dict:
    return {
        "issue_type": "return_request",
        "clarification_needed": False,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
