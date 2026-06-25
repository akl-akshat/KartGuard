"""Simulated, audited action tools (SRS §6.4, TOOL-1/2).

Every action performs its (simulated) effect as **an append-only audit_log insert + an
event emission**, and returns a structured result. No real money moves (CON-2).

Idempotency (FR-EXE-2, AC-5): each tool is keyed by ``(request_id, action_type)``. A
repeat finds the existing audit row and **no-ops** — a redelivered request never produces
a second financial effect. Tools must be invoked only from the Executor node (TOOL-2),
after guardrails and (when required) human approval.
"""

from __future__ import annotations

from typing import Any, Mapping

from config.settings import settings
from db.repository import Repository
from events.emit import emit_event

# Action types that move (simulated) money — the idempotency-critical set.
FINANCIAL_ACTIONS = {
    "instant_refund", "store_credit_refund", "partial_refund",
    "retention_coupon", "goodwill_credit",
}


def _audited(
    repo: Repository,
    request_id: str,
    action_type: str,
    amount: float | None,
    actor: str,
    customer_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Append-only audit + event, with (request_id, action_type) idempotency."""
    existing = repo.get_audit(request_id, action_type)
    if existing:
        return {
            "status": "noop_idempotent",
            "action_type": action_type,
            "amount": amount,
            "audit_id": existing[0].get("id"),
            "request_id": request_id,
        }
    entry = {
        "request_id": request_id,
        "action_type": action_type,
        "amount": amount,
        "actor": actor,
        "customer_id": customer_id,
        "payload": payload,
    }
    repo.append_audit(entry)
    emit_event(settings.TOPIC_AUDIT, request_id, entry)
    audit_id = repo.get_audit(request_id, action_type)[0].get("id")
    return {
        "status": "applied",
        "action_type": action_type,
        "amount": amount,
        "audit_id": audit_id,
        "request_id": request_id,
        "payload": payload,
    }


# --------------------------------------------------------------------- tools
def process_refund(repo, request_id, order, amount, *, action_type="instant_refund",
                   mode="original", actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, action_type, round(float(amount), 2), actor,
                    order["customer_id"], {"order_id": order["id"], "refund_mode": mode})


def issue_store_credit(repo, request_id, order, amount, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "store_credit_refund", round(float(amount), 2), actor,
                    order["customer_id"], {"order_id": order["id"], "instrument": "store_credit"})


def book_exchange(repo, request_id, order, *, size_guide=False, actor="agent") -> dict[str, Any]:
    action_type = "exchange_with_size_guide" if size_guide else "free_exchange"
    return _audited(repo, request_id, action_type, 0.0, actor, order["customer_id"],
                    {"order_id": order["id"], "sku": order["sku"], "size_guide": size_guide})


def issue_retention_coupon(repo, request_id, order, amount, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "retention_coupon", round(float(amount), 2), actor,
                    order["customer_id"], {"order_id": order["id"], "coupon_value": round(float(amount), 2)})


def expedite_replacement(repo, request_id, order, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "expedited_replacement", 0.0, actor, order["customer_id"],
                    {"order_id": order["id"], "sku": order["sku"], "priority": "expedited"})


def issue_goodwill_credit(repo, request_id, order, amount, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "goodwill_credit", round(float(amount), 2), actor,
                    order["customer_id"], {"order_id": order["id"]})


def deny_with_explanation(repo, request_id, order, *, reason="policy", actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "deny_with_explanation", 0.0, actor, order["customer_id"],
                    {"order_id": order["id"], "reason": reason})


def provide_information(repo, request_id, order, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "provide_information", 0.0, actor, order["customer_id"],
                    {"order_id": order["id"]})


def create_ticket(repo, request_id, order, *, reason="escalation", actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "create_ticket", None, actor, order["customer_id"],
                    {"order_id": order["id"], "reason": reason})


def send_customer_message(repo, request_id, order, message, *, actor="agent") -> dict[str, Any]:
    return _audited(repo, request_id, "send_customer_message", None, actor, order["customer_id"],
                    {"order_id": order["id"], "chars": len(message or "")})


# --------------------------------------------------------------- dispatcher
def execute_action(repo: Repository, request_id: str, action: Mapping[str, Any],
                   order: Mapping[str, Any], actor: str = "agent") -> dict[str, Any]:
    """Dispatch an approved action to its tool. Called only from the Executor node."""
    at = action["action_type"]
    amount = action.get("amount") or 0.0
    params = action.get("params") or {}
    if at in ("instant_refund", "partial_refund"):
        return process_refund(repo, request_id, order, amount, action_type=at,
                              mode=params.get("refund_mode", "original"), actor=actor)
    if at == "store_credit_refund":
        return issue_store_credit(repo, request_id, order, amount, actor=actor)
    if at in ("free_exchange", "exchange_with_size_guide"):
        return book_exchange(repo, request_id, order,
                             size_guide=(at == "exchange_with_size_guide"), actor=actor)
    if at == "retention_coupon":
        return issue_retention_coupon(repo, request_id, order, amount, actor=actor)
    if at == "expedited_replacement":
        return expedite_replacement(repo, request_id, order, actor=actor)
    if at == "goodwill_credit":
        return issue_goodwill_credit(repo, request_id, order, amount, actor=actor)
    if at == "deny_with_explanation":
        return deny_with_explanation(repo, request_id, order,
                                     reason=params.get("reason", "policy"), actor=actor)
    if at == "provide_information":
        return provide_information(repo, request_id, order, actor=actor)
    raise ValueError(f"no tool registered for action_type={at!r}")
