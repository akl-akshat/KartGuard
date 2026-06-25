"""Phase 11 checkpoints: dataset coverage (EV-1/2) + hard gates (AC-1..3)."""

from pathlib import Path

import pytest
import yaml

pytest.importorskip("langgraph")

from db.dataset import NON_RETURNABLE, REFERENCE_DATE  # noqa: E402
from eval.metrics import Targets, gate_failures, soft_gaps  # noqa: E402
from eval.runner import run_eval  # noqa: E402
from tools.data_access import LocalDataAccess  # noqa: E402

pytestmark = pytest.mark.integration

CASES = yaml.safe_load((Path("eval/cases.yaml")).read_text(encoding="utf-8"))


def test_dataset_coverage_matrix():
    da = LocalDataAccess()
    assert len(CASES) >= 40
    roots = {c["expected_root_cause"] for c in CASES}
    assert roots == {
        "size_fit_mismatch", "defect_damage", "changed_mind", "found_cheaper",
        "delivery_delay", "wrong_item_shipped", "expectation_mismatch",
        "fraud_suspected", "genuine_other",
    }
    assert sum(1 for c in CASES if c["expected_escalation"]) >= 5

    pmodes, returnable_flags, window_flags = set(), set(), set()
    for c in CASES:
        o = da.get_order(c["seeded_order_id"])
        assert o is not None, c["seeded_order_id"]
        pmodes.add(o["payment_mode"])
        returnable_flags.add(o["category"] not in NON_RETURNABLE)
        rwe = o["return_window_end"]
        window_flags.add(rwe is not None and rwe >= REFERENCE_DATE.isoformat())
    assert pmodes == {"COD", "PREPAID"}
    assert returnable_flags == {True, False}      # returnable + non-returnable
    assert window_flags == {True, False}          # in-window + out-of-window


def test_hard_gates_pass_and_soft_targets_met():
    out = run_eval()
    report = out["report"]
    assert gate_failures(report) == [], "hard gates must pass"
    assert report["guardrail_violation_rate"] == 0.0
    assert report["satisfaction_floor_adherence"] == 1.0
    # soft targets are also met on the deterministic stub baseline
    assert soft_gaps(report, Targets()) == [], report
    assert report["root_cause_accuracy"] >= 0.85
    assert report["p95_latency_s"] <= 8.0
