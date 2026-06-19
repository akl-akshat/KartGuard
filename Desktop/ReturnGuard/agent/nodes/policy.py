# Phase 4 stub — replaced by the real RAG policy retriever in Phase 5.
from agent.state import ResolutionState


def policy(state: ResolutionState) -> dict:
    return {"policy_snippets": [], "within_return_window": True}
