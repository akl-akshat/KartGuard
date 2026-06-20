"""Human-in-the-Loop Escalation node (FR-HIL-1..5).

On ``requires_human``: persist an escalation record with the recommendation, emit an
escalation event, and **pause** via LangGraph ``interrupt`` (state checkpointed to Postgres,
so it survives a restart, CON-3). On resume the reviewer decision routes the graph:

* approve → execute the recommended action
* modify  → the modified action is **re-checked against guardrails** before execution (FR-HIL-3)
* reject  → a policy-compliant denial, no monetary action (FR-HIL-4)
"""

from __future__ import annotations

from langgraph.types import interrupt

from agent.decision.guardrails import evaluate_guardrails
from agent.deps import get_deps
from agent.state import ResolutionState
from config.settings import settings
from events.emit import emit_event


def hitl(state: ResolutionState) -> dict:
    repo = get_deps().repo
    request_id = state["request_id"]
    recommendation = {
        "proposed_action": state.get("proposed_action"),
        "root_cause": state.get("root_cause"),
        "risk_score": state.get("risk_score"),
        "risk_factors": state.get("risk_factors"),
        "rationale": state.get("rationale"),
        "order_id": state.get("order_id"),
        "customer_id": state.get("customer_id"),
    }
    # Persist + emit once (idempotent on redelivery / re-entry).
    if not repo.get_escalation(request_id):
        repo.upsert_escalation(request_id, recommendation)
        emit_event(settings.TOPIC_ESCALATIONS, request_id,
                   {"request_id": request_id, "status": "pending", "recommendation": recommendation})

    # Pause until a reviewer decision arrives (Command(resume=...)).
    decision = interrupt(recommendation)

    if isinstance(decision, dict):
        verdict = decision.get("decision", "approve")
        reviewer_id = decision.get("reviewer_id")
        modified = decision.get("modified_action")
    else:
        verdict, reviewer_id, modified = (decision or "approve"), None, None

    repo.set_escalation_decided(request_id, verdict, reviewer_id)
    out: dict = {"human_decision": verdict, "reviewer_id": reviewer_id}

    if verdict == "modify" and modified:
        # Re-validate the human-supplied action; a violation cannot be executed (FR-HIL-3).
        res = evaluate_guardrails(modified, state.get("order_context") or {}, state.get("risk_score"), 0)
        if res.status == "violation":
            out["human_decision"] = "reject"
            out["proposed_action"] = {"action_type": "deny_with_explanation", "amount": 0.0, "params": {}}
            out["status"] = "denied"
        else:
            out["proposed_action"] = res.action
            out["guardrail_status"] = res.status
    elif verdict == "reject":
        out["proposed_action"] = {"action_type": "deny_with_explanation", "amount": 0.0, "params": {}}
        out["status"] = "denied"
    return out
