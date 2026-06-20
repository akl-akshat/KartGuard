"""Action Executor node (FR-EXE-1..3, NFR-SAF-3).

Invokes exactly the approved action's tool(s) — the only place action tools are called
(TOOL-2) — records ``executed_action``, and (via the tools) writes the audit row. Execution
is idempotent by ``request_id`` (a redelivery produces no second financial effect), and a
tool failure marks the resolution failed with no partial financial effect, routing to a
safe reply.
"""

from __future__ import annotations

from agent.deps import get_deps
from agent.state import ResolutionState
from tools.actions import execute_action, issue_goodwill_credit


def executor(state: ResolutionState) -> dict:
    repo = get_deps().repo
    request_id = state["request_id"]
    order = state.get("order_context") or {}
    action = dict(state.get("proposed_action") or {})
    at = action.get("action_type")

    if at in (None, "escalate_to_human"):
        # Should not reach the executor; never auto-act on an escalation.
        return {"status": "escalated", "requires_human": True}

    # Idempotency (AC-5): a request already executed returns its prior result, no re-effect.
    existing = repo.get_resolution(request_id)
    if existing and existing.get("executed_action"):
        return {"executed_action": existing["executed_action"],
                "outcome": existing.get("outcome") or {"status": "resolved"},
                "status": existing.get("status", "resolved")}

    decision = state.get("human_decision")
    actor = f"human:{state.get('reviewer_id')}" if (decision in ("approve", "modify") and state.get("reviewer_id")) else "agent"

    try:
        result = execute_action(repo, request_id, action, order, actor=actor)
        # Defect sweetener: a replacement may carry a small goodwill credit (A.2).
        goodwill = action.get("params", {}).get("goodwill")
        if goodwill:
            issue_goodwill_credit(repo, request_id, order, goodwill, actor=actor)
        executed = {**action, "result": result}
        status = "denied" if at == "deny_with_explanation" else (
            "info" if at == "provide_information" else "resolved")
        outcome = {
            "status": status,
            "action_type": at,
            "amount": action.get("amount", 0.0),
            "expected_return_cost": state.get("expected_return_cost"),
            "expected_saving": state.get("expected_saving"),
            "requires_human": state.get("requires_human", False),
        }
        return {"executed_action": executed, "outcome": outcome, "status": status}
    except Exception as exc:  # noqa: BLE001 - degrade safely, no partial financial effect
        return {"executed_action": None, "status": "failed", "requires_human": True,
                "outcome": {"status": "failed", "error": str(exc)}}
