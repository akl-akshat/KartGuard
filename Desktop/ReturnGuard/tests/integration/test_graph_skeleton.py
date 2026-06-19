"""Phase 4 checkpoints: graph compiles, runs end-to-end, checkpoints, and terminates."""

import pathlib

import pytest

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from agent.graph import build_graph  # noqa: E402
from agent.state import initial_state  # noqa: E402
from config.settings import settings  # noqa: E402

pytestmark = pytest.mark.integration


def _run(thread_id: str, state, recursion_limit: int = 50):
    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}
    return graph, cfg, graph.invoke(state, cfg)


def test_graph_runs_end_to_end_on_seeded_order():
    st = initial_state("t1", "I want to return this", order_id="ORD-FIT-PREPAID",
                       customer_id="CUST-LOW1")
    _, _, final = _run("t1", st)
    assert final["status"] == "resolved"
    assert final["executed_action"] is not None
    assert final["customer_message"]


def test_checkpointer_persists_state():
    st = initial_state("t2", "return please", order_id="ORD-FIT-PREPAID", customer_id="CUST-LOW1")
    graph, cfg, _ = _run("t2", st)
    snap = graph.get_state(cfg)
    assert snap.values["request_id"] == "t2"


def test_clarification_loop_terminates_at_cap(monkeypatch):
    # Force the clarification loop to never resolve; the cap must still terminate it.
    import agent.nodes.triage as triage_mod

    def always_clarify(state):
        return {"clarification_needed": True, "iteration_count": state.get("iteration_count", 0) + 1}

    monkeypatch.setattr(triage_mod, "triage", always_clarify)
    st = initial_state("t3", "??", order_id="ORD-FIT-PREPAID", customer_id="CUST-LOW1")
    _, _, final = _run("t3", st)
    assert final["iteration_count"] == settings.MAX_ITERATIONS  # bounded (CON-4)
    assert final["status"] == "resolved"  # still reached END


def test_no_memorysaver_in_production_agent_package():
    # Pitfall guard: the production graph must not reference an in-memory saver (CON-3).
    agent_dir = pathlib.Path(__file__).resolve().parents[2] / "agent"
    for py in agent_dir.rglob("*.py"):
        assert "memorysaver" not in py.read_text(encoding="utf-8").lower(), py
