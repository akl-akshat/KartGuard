"""Adversarial QA — durability resume (proxy) + eval-harness meta-tests (gate must bite)."""

import pytest

from agent.deps import get_deps, reset_deps
from agent.graph import build_graph
from agent.runner import is_paused, resume, run_config
from agent.state import initial_state
from config.settings import settings
from eval import metrics
from eval.runner import run_eval

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def test_resume_from_a_fresh_graph_object_sharing_the_checkpointer():
    """Proxy for AC-4: a NEW graph object reads the paused state from the checkpointer and
    resumes (true cross-PROCESS restart with Postgres is UNVERIFIED in this offline env)."""
    saver = _mem()
    g1 = build_graph(checkpointer=saver)
    g1.invoke(initial_state("dur-1", "return this tablet", order_id="ORD-HIVAL-COD",
                            customer_id="CUST-SERIAL"), run_config("dur-1"))
    assert is_paused(g1, run_config("dur-1"))
    # discard g1, build a brand-new graph object over the SAME persisted state
    g2 = build_graph(checkpointer=saver)
    snap = g2.get_state(run_config("dur-1"))
    assert snap.values["root_cause"] == "fraud_suspected"
    final = resume(g2, run_config("dur-1"), {"decision": "approve", "reviewer_id": "op1"})
    assert final["status"] in ("resolved", "escalated")


def test_eval_gate_actually_bites_on_a_broken_guardrail():
    """T-EVAL-2: inject a satisfaction-floor bug; the eval HARD gate must go red."""
    import agent.decision.select as sel

    broken_called = {"n": 0}
    orig = sel.select_action

    def broken(root_cause, eligible, order, within_window):
        # Sabotage: deny defects to 'save cost' (violates the satisfaction floor).
        if root_cause in ("defect_damage", "wrong_item_shipped"):
            return ({"action_type": "deny_with_explanation", "amount": 0.0, "params": {},
                     "expected_return_cost": 0.0, "expected_saving": 0.0, "rationale": "sabotage"}, [])
        return orig(root_cause, eligible, order, within_window)

    sel.select_action = broken
    # planner imports select_action by name; patch there too
    import agent.nodes.planner as planner_mod
    planner_orig = planner_mod.select_action
    planner_mod.select_action = broken
    try:
        report = run_eval()["report"]
        failures = metrics.gate_failures(report)
        assert failures, "eval HARD gate did NOT bite on a satisfaction-floor sabotage (gate is a no-op!)"
        assert report["satisfaction_floor_adherence"] < 1.0
    finally:
        sel.select_action = orig
        planner_mod.select_action = planner_orig


def test_eval_metric_math_is_correct():
    """T-EVAL-3: precision/recall computed correctly on a hand-labelled set."""
    results = [
        {"expected_escalation": True, "actual_escalation": True, "root_correct": True,
         "action_appropriate": True, "satisfaction_floor_ok": True, "guardrail_violation": False,
         "expected_root_cause": "fraud_suspected", "deflectable": False, "deflected": False,
         "expected_saving": 0, "latency": 0.01},
        {"expected_escalation": True, "actual_escalation": False, "root_correct": True,  # a miss (FN)
         "action_appropriate": False, "satisfaction_floor_ok": True, "guardrail_violation": False,
         "expected_root_cause": "fraud_suspected", "deflectable": False, "deflected": False,
         "expected_saving": 0, "latency": 0.01},
        {"expected_escalation": False, "actual_escalation": True, "root_correct": True,  # a false alarm (FP)
         "action_appropriate": False, "satisfaction_floor_ok": True, "guardrail_violation": False,
         "expected_root_cause": "changed_mind", "deflectable": False, "deflected": False,
         "expected_saving": 0, "latency": 0.01},
    ]
    rep = metrics.compute(results)
    assert rep["escalation_precision"] == 0.5  # 1 TP / (1 TP + 1 FP)
    assert rep["escalation_recall"] == 0.5     # 1 TP / (1 TP + 1 FN)
    assert rep["guardrail_violation_rate"] == 0.0
    assert rep["root_cause_accuracy"] == 1.0


def test_eval_dataset_meets_coverage_floor():
    import yaml
    from pathlib import Path
    cases = yaml.safe_load(Path("eval/cases.yaml").read_text(encoding="utf-8"))
    assert len(cases) >= 40
    assert sum(1 for c in cases if c["expected_escalation"]) >= 5
    assert len({c["expected_root_cause"] for c in cases}) == 9
