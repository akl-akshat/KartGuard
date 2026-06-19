# Phase 4 stub — replaced by the real hybrid risk assessor in Phase 5.
from agent.state import ResolutionState


def risk(state: ResolutionState) -> dict:
    return {"risk_score": 0.1, "risk_factors": [], "requires_human": state.get("requires_human", False)}
