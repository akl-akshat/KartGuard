"""Phase 9 checkpoints: event schemas, idempotent consume (AC-5), dead-letter (FR-EVT-3)."""

import pytest

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from agent.deps import get_deps, reset_deps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from config.settings import settings  # noqa: E402
from events.consumer import handle_message  # noqa: E402
from events.schemas import RequestEvent  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def env():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    graph = build_graph(checkpointer=MemorySaver())
    yield graph, get_deps().repo
    settings.AS_OF_DATE = ""
    reset_deps()


def test_request_event_validation():
    ev = RequestEvent(request_id="r", issue_text="hi", order_id="ORD1")
    assert ev.request_id == "r"
    with pytest.raises(ValidationError):
        RequestEvent(request_id="r")  # missing issue_text


def test_handle_message_processes_valid(env):
    graph, repo = env
    payload = {"request_id": "k1", "issue_text": "The kurti is too tight",
               "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1", "source": "kafka"}
    res = handle_message(payload, graph, repo)
    assert res["status"] == "processed"
    assert repo.get_resolution("k1") is not None


def test_redelivery_does_not_double_execute(env):
    graph, repo = env
    payload = {"request_id": "k2", "issue_text": "changed my mind, return it",
               "order_id": "ORD-MIND-PREPAID", "customer_id": "CUST-VIP1"}
    first = handle_message(payload, graph, repo)
    second = handle_message(payload, graph, repo)  # redelivery
    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert len(repo.get_audit("k2", "retention_coupon")) == 1  # exactly one financial effect


def test_malformed_message_is_dead_lettered_without_crash(env):
    graph, repo = env
    bad = {"request_id": "k3"}  # missing issue_text
    res = handle_message(bad, graph, repo)
    assert res["status"] == "dead_letter" and res["reason"]
    # the worker keeps going: a valid message still processes afterwards
    ok = handle_message({"request_id": "k4", "issue_text": "The kurti is too tight",
                         "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1"}, graph, repo)
    assert ok["status"] == "processed"
