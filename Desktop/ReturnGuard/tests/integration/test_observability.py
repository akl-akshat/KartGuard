"""Phase 10 checkpoints: full-trajectory spans + structured logs (NFR-OBS-1/2)."""

import logging

import pytest

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.state import initial_state  # noqa: E402
from config.settings import settings  # noqa: E402
from observability.tracing import InMemoryTracer, set_tracer  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def env():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    tracer = InMemoryTracer()
    set_tracer(tracer)
    yield tracer
    set_tracer(None)
    settings.AS_OF_DATE = ""
    reset_deps()


def test_full_trajectory_is_spanned(env):
    tracer = env
    g = build_graph(checkpointer=MemorySaver())
    g.invoke(initial_state("obs-1", "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                           customer_id="CUST-LOW1"), {"configurable": {"thread_id": "obs-1"}})
    names = tracer.names()
    # every node on the auto path is spanned
    for node in ("triage", "context", "policy", "risk", "diagnosis", "planner",
                 "guardrails", "executor", "responder", "logger"):
        assert node in names, f"missing span for node {node}"
    # LLM and tool calls are spanned too
    assert "llm.triage" in names and "llm.diagnose" in names
    assert any(n.startswith("tool.") for n in names)
    # spans are correlated by request_id
    assert all(s["request_id"] in ("obs-1", None) for s in tracer.spans)


def test_logs_are_structured_with_request_and_node(env, caplog):
    caplog.set_level(logging.INFO, logger="agent.node")
    g = build_graph(checkpointer=MemorySaver())
    g.invoke(initial_state("obs-2", "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                           customer_id="CUST-LOW1"), {"configurable": {"thread_id": "obs-2"}})
    node_records = [r for r in caplog.records if getattr(r, "node", None)]
    assert node_records, "expected structured node log records"
    sample = next(r for r in node_records if r.node == "triage")
    assert sample.request_id == "obs-2"
    assert hasattr(sample, "outcome")
