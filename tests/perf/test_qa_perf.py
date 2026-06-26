"""Adversarial QA — latency (NFR-PERF-1). NOTE: stub LLM + in-memory; real-infra p95 is
UNVERIFIED here. This bounds the orchestration overhead, not real model/DB latency."""

import time

import pytest

from agent.deps import reset_deps
from agent.graph import build_graph
from agent.runner import run_config
from agent.state import initial_state
from config.settings import settings

pytestmark = [pytest.mark.perf, pytest.mark.slow]


def _p(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def test_automated_path_p95_under_budget():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    g = build_graph(checkpointer=_mem())
    lat = []
    for i in range(50):
        rid = f"perf-{i}"
        t0 = time.perf_counter()
        g.invoke(initial_state(rid, "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                               customer_id="CUST-LOW1"), run_config(rid))
        lat.append(time.perf_counter() - t0)
    settings.AS_OF_DATE = ""
    reset_deps()
    p50, p95, mx = _p(lat, 0.5), _p(lat, 0.95), max(lat)
    print(f"\n[perf] orchestration latency p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms max={mx*1000:.1f}ms")
    assert p95 <= 8.0, f"p95 {p95:.3f}s exceeds 8s budget (orchestration only)"


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
