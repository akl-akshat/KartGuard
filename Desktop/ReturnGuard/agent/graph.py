"""Agent orchestration graph (SRS §4.3/4.4).

A single ``StateGraph`` of specialised nodes (D10) with exactly **two branch points**
(§3.4): (i) the clarification self-loop at intake, and (ii) the guardrail→HITL fork
before execution. Everything else is linear and deterministic.

``build_graph`` takes an injected checkpointer so production passes the Postgres saver
(CON-3) while tests pass an in-memory one — no in-memory saver is referenced here.
Termination is guaranteed: the clarification loop is bounded by ``MAX_ITERATIONS``
(CON-4, NFR-REL-2).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    context,
    diagnosis,
    executor,
    guardrails,
    hitl,
    logger,
    planner,
    policy,
    responder,
    risk,
    triage,
)
from agent.state import ResolutionState
from config.settings import settings


# ------------------------------------------------------------------ routers
def route_after_triage(state: ResolutionState) -> str:
    """Branch point (i): bounded single-question clarification loop."""
    if state.get("clarification_needed") and state.get("iteration_count", 0) < settings.MAX_ITERATIONS:
        return "triage"
    return "context"


def route_after_context(state: ResolutionState) -> str:
    """Graceful 'cannot locate order/customer' path (FR-CTX-3).

    ``None`` means not-found (route to a safe reply); an empty/populated dict means the
    lookup succeeded, so we proceed to policy.
    """
    if state.get("order_context") is None or state.get("customer_context") is None:
        return "responder"
    return "policy"


def route_after_guardrail(state: ResolutionState) -> str:
    """Branch point (ii): guardrail → HITL fork before execution."""
    return "hitl" if state.get("requires_human") else "executor"


def route_after_hitl(state: ResolutionState) -> str:
    """Resume routing: approve/modify → execute; reject → denial reply, no money."""
    return "executor" if state.get("human_decision") in ("approve", "modify") else "responder"


def build_graph(checkpointer: Any | None = None, interrupt_before_hitl: bool = False):
    """Compile the resolution graph. ``interrupt_before_hitl`` pauses at HITL (Phase 7)."""
    g = StateGraph(ResolutionState)
    g.add_node("triage", triage.triage)
    g.add_node("context", context.context)
    g.add_node("policy", policy.policy)
    g.add_node("risk", risk.risk)
    g.add_node("diagnosis", diagnosis.diagnosis)
    g.add_node("planner", planner.planner)
    g.add_node("guardrails", guardrails.guardrails)
    g.add_node("hitl", hitl.hitl)
    g.add_node("executor", executor.executor)
    g.add_node("responder", responder.responder)
    g.add_node("logger", logger.logger)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", route_after_triage, {"triage": "triage", "context": "context"})
    g.add_conditional_edges("context", route_after_context, {"responder": "responder", "policy": "policy"})
    g.add_edge("policy", "risk")
    g.add_edge("risk", "diagnosis")
    g.add_edge("diagnosis", "planner")
    g.add_edge("planner", "guardrails")
    g.add_conditional_edges("guardrails", route_after_guardrail, {"hitl": "hitl", "executor": "executor"})
    g.add_conditional_edges("hitl", route_after_hitl, {"executor": "executor", "responder": "responder"})
    g.add_edge("executor", "responder")
    g.add_edge("responder", "logger")
    g.add_edge("logger", END)

    kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    if interrupt_before_hitl:
        kwargs["interrupt_before"] = ["hitl"]
    return g.compile(**kwargs)
