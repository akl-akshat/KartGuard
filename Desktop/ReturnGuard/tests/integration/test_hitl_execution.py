"""Phase 7 checkpoints: HITL pause/resume, idempotent execution, audit (AC-5/AC-6)."""

import pytest

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from agent.deps import get_deps, reset_deps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.runner import is_paused, resume, run_config  # noqa: E402
from agent.state import initial_state  # noqa: E402
from config.settings import settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


def _graph():
    return build_graph(checkpointer=MemorySaver())


def _start(graph, rid, msg, oid, cid):
    return graph.invoke(initial_state(rid, msg, order_id=oid, customer_id=cid), run_config(rid))


def test_high_risk_pauses_with_pending_escalation():
    g = _graph()
    _start(g, "esc-1", "return this tablet", "ORD-HIVAL-COD", "CUST-SERIAL")
    cfg = run_config("esc-1")
    assert is_paused(g, cfg)
    esc = get_deps().repo.get_escalation("esc-1")
    assert esc and esc["status"] == "pending" and esc["recommendation"]["proposed_action"]


def test_resume_approve_executes():
    g = _graph()
    _start(g, "esc-2", "return this tablet", "ORD-HIVAL-COD", "CUST-SERIAL")
    final = resume(g, run_config("esc-2"), {"decision": "approve", "reviewer_id": "op7"})
    assert final["status"] in ("resolved", "escalated")
    assert get_deps().repo.get_escalation("esc-2")["status"] == "decided"


def test_resume_modify_revalidates_guardrails():
    g = _graph()
    _start(g, "esc-3", "return this tablet", "ORD-HIVAL-COD", "CUST-SERIAL")
    # Reviewer modifies to a partial refund within order value -> must pass + execute.
    final = resume(g, run_config("esc-3"),
                   {"decision": "modify", "reviewer_id": "op7",
                    "modified_action": {"action_type": "partial_refund", "amount": 1500.0, "params": {}}})
    assert final["executed_action"]["action_type"] == "partial_refund"
    audit = get_deps().repo.get_audit("esc-3", "partial_refund")
    assert len(audit) == 1 and audit[0]["actor"] == "human:op7"


def test_resume_modify_violation_becomes_denial_no_money():
    g = _graph()
    _start(g, "esc-4", "return this tablet", "ORD-HIVAL-COD", "CUST-SERIAL")
    # Modify to a refund exceeding order value -> violation -> denial, no money.
    final = resume(g, run_config("esc-4"),
                   {"decision": "modify", "modified_action": {"action_type": "instant_refund", "amount": 999999.0}})
    assert final["status"] == "denied"
    assert get_deps().repo.get_audit("esc-4", "instant_refund") == []


def test_resume_reject_denies_without_money():
    g = _graph()
    _start(g, "esc-5", "return this tablet", "ORD-HIVAL-COD", "CUST-SERIAL")
    final = resume(g, run_config("esc-5"), {"decision": "reject", "reviewer_id": "op7"})
    assert final["status"] == "denied"
    assert final["customer_message"]
    # no monetary audit row of any refund type
    assert get_deps().repo.get_audit("esc-5", "instant_refund") == []


def test_executor_idempotent_on_replay():
    g = _graph()
    _start(g, "idem-1", "The kurti is too tight", "ORD-FIT-PREPAID", "CUST-LOW1")
    g2 = build_graph(checkpointer=MemorySaver())  # fresh thread state, same repo (deps)
    g2.invoke(initial_state("idem-1", "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                            customer_id="CUST-LOW1"), run_config("idem-1"))
    audit = get_deps().repo.get_audit("idem-1")
    assert len(audit) <= 1, "a replayed request_id must not double-write audit"


def test_logger_persists_resolution_and_audit_for_monetary():
    g = _graph()
    final = _start(g, "log-1", "I changed my mind, return it", "ORD-MIND-PREPAID", "CUST-VIP1")
    repo = get_deps().repo
    res = repo.get_resolution("log-1")
    assert res and res["status"] in ("resolved", "denied", "info")
    assert res["root_cause"] and res["customer_message"]
    # A monetary action (coupon) has exactly one matching audit row (AC-6).
    if final["executed_action"] and final["executed_action"]["action_type"] == "retention_coupon":
        assert len(repo.get_audit("log-1", "retention_coupon")) == 1
