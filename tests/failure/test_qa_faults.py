"""Adversarial QA — fault injection (NFR-REL-3, FR-EXE-3, FR-EVT-3, LLM-2).

Degradation must always be to escalation / safe reply — never an unguarded action — and a
failed action must leave NO partial financial effect.
"""

import pytest

from agent.deps import Deps, get_deps, reset_deps, set_deps
from agent.graph import build_graph
from agent.llm import LLMError
from agent.runner import is_paused, run_config
from agent.state import initial_state
from config.settings import settings
from db.repository import InMemoryRepository
from events import emit as emitmod
from tools.actions import process_refund
from tools.data_access import LocalDataAccess

pytestmark = pytest.mark.failure


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    emitmod.set_emitter(None)
    reset_deps()


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


class FailingLLM:
    provider = "stub"
    model = "fail"

    def triage(self, *a, **k):
        raise LLMError("simulated LLM timeout/parse failure")

    def diagnose(self, *a, **k):
        raise LLMError("simulated LLM timeout/parse failure")

    def risk_nuance(self, signals):
        return 0.0, []


def test_llm_failure_degrades_to_escalation_not_action():
    """LLM-2 / NFR-REL-3: exhausted/invalid LLM → escalation, never an unguarded action."""
    repo = InMemoryRepository()
    set_deps(Deps(LocalDataAccess(repo), repo, FailingLLM()))
    g = build_graph(checkpointer=_mem())
    final = g.invoke(initial_state("llmfail", "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                                   customer_id="CUST-LOW1"), run_config("llmfail"))
    requires_human = is_paused(g, run_config("llmfail")) or final.get("requires_human")
    assert requires_human, "LLM failure did not route to escalation"
    # no money moved on any refund-type action
    for at in ("instant_refund", "partial_refund", "store_credit_refund"):
        assert repo.get_audit("llmfail", at) == []


def test_tool_exception_no_partial_effect():
    """FR-EXE-3: a tool failure marks failed with NO partial financial effect."""
    repo = InMemoryRepository()
    order = LocalDataAccess(repo).get_order("ORD-FIT-PREPAID")
    orig = repo.record_action

    def boom(*a, **k):
        raise RuntimeError("DB write failed mid-action")

    repo.record_action = boom
    with pytest.raises(Exception):
        process_refund(repo, "toolfail", order, 1299.0)
    repo.record_action = orig
    assert repo.get_audit("toolfail") == []  # nothing partially written


def test_emit_failure_leaves_no_orphan_audit_row():
    """FR-EXE-3 / AC-6 (D-04 invariant): a broker failure must leave EITHER nothing OR a
    committed audit row WITH a pending outbox event that the relay later emits — never an
    orphan audit with a lost event. Pre-fix this raised and orphaned the audit row."""
    repo = InMemoryRepository()
    order = LocalDataAccess(repo).get_order("ORD-FIT-PREPAID")

    def kafka_down(*a, **k):
        raise RuntimeError("kafka unavailable")

    emitmod.set_emitter(kafka_down)
    res = process_refund(repo, "emitfail", order, 1299.0)   # must NOT raise (decoupled emit)
    emitmod.set_emitter(None)

    audits = repo.get_audit("emitfail")
    pending = [o for o in repo.list_pending_outbox() if o["request_id"] == "emitfail"]
    assert res["status"] == "applied"
    # every committed audit row has a guaranteed event waiting in the outbox (no orphan)
    assert len(pending) >= len(audits) >= 1

    # when the broker recovers, the relay publishes the pending event
    emitmod.drain_sink()
    emitmod.relay_outbox(repo)
    assert any(e["key"] == "emitfail" for e in emitmod.drain_sink())
    assert not [o for o in repo.list_pending_outbox() if o["request_id"] == "emitfail"]


def test_malformed_event_dead_lettered_worker_survives():
    """FR-EVT-3: malformed message dead-lettered; a valid message after it still processes."""
    from events.consumer import handle_message
    repo = get_deps().repo
    g = build_graph(checkpointer=_mem())
    bad = handle_message({"request_id": "x"}, g, repo)            # missing issue_text
    assert bad["status"] == "dead_letter"
    ok = handle_message({"request_id": "y", "issue_text": "The kurti is too tight",
                         "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1"}, g, repo)
    assert ok["status"] == "processed"


def test_clarification_loop_cannot_run_forever():
    """NFR-REL-2: a degenerate loop terminates at MAX_ITERATIONS."""
    import agent.nodes.triage as t

    def always_clarify(state):
        return {"clarification_needed": True, "iteration_count": state.get("iteration_count", 0) + 1}

    orig = t.triage
    t.triage = always_clarify
    try:
        g = build_graph(checkpointer=_mem())
        final = g.invoke(initial_state("loop", "??", order_id="ORD-FIT-PREPAID", customer_id="CUST-LOW1"),
                         {"configurable": {"thread_id": "loop"}, "recursion_limit": 100})
        assert final["iteration_count"] == settings.MAX_ITERATIONS
    finally:
        t.triage = orig
