# Phase 4 stub — replaced by the real constrained planner in Phase 6.
from agent.state import ResolutionState


def planner(state: ResolutionState) -> dict:
    return {
        "candidate_actions": [],
        "proposed_action": {"action_type": "provide_information", "amount": 0.0, "params": {}},
    }
