"""Outcome Logger & Audit node (FR-LOG-1/2).

Persists the full resolution record (classification, root cause, risk, citations, proposed
vs executed action, amounts, expected cost/saving, human involvement, rationale, timestamps,
trace id) and emits the resolution event. The audit_log rows for any monetary action were
written by the action tools in the executor; this node guarantees every resolution is
recorded and every monetary action has its matching event (AC-6).
"""

from __future__ import annotations

from datetime import datetime

from agent.deps import get_deps
from agent.state import ResolutionState, ResolutionStatus
from config.settings import settings
from events.emit import emit_event


def logger(state: ResolutionState) -> dict:
    repo = get_deps().repo
    request_id = state["request_id"]
    executed = state.get("executed_action")
    status = state.get("status") or (ResolutionStatus.resolved.value if executed else ResolutionStatus.pending.value)

    amount = None
    if executed:
        amount = executed.get("amount")

    resolution = {
        "request_id": request_id,
        "order_id": state.get("order_id"),
        "customer_id": state.get("customer_id"),
        "issue_type": state.get("issue_type"),
        "root_cause": state.get("root_cause"),
        "risk_score": state.get("risk_score"),
        "risk_factors": state.get("risk_factors", []),
        "proposed_action": state.get("proposed_action"),
        "executed_action": executed,
        "amount": amount,
        "expected_return_cost": state.get("expected_return_cost"),
        "expected_saving": state.get("expected_saving"),
        "requires_human": state.get("requires_human", False),
        "human_decision": state.get("human_decision"),
        "rationale": state.get("rationale"),
        "status": status,
        "trace_id": state.get("trace_id"),
        "customer_message": state.get("customer_message"),
        "resolved_at": datetime.now().isoformat(),
    }
    repo.save_resolution(resolution)

    emit_event(settings.TOPIC_RESOLUTIONS, request_id, {
        "request_id": request_id,
        "order_id": state.get("order_id"),
        "customer_id": state.get("customer_id"),
        "issue_type": state.get("issue_type"),
        "root_cause": state.get("root_cause"),
        "risk_score": state.get("risk_score"),
        "action_type": (executed or {}).get("action_type") or (state.get("proposed_action") or {}).get("action_type"),
        "amount": amount or 0.0,
        "requires_human": state.get("requires_human", False),
        "rationale": state.get("rationale"),
        "expected_saving": state.get("expected_saving") or 0.0,
        "status": status,
    })
    return {"status": status}
