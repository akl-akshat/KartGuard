# Phase 4 stub — replaced by the real MCP-backed context gatherer in Phase 5.
from agent.state import ResolutionState


def context(state: ResolutionState) -> dict:
    return {"order_context": {}, "customer_context": {}}
