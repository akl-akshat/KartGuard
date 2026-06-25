"""Evaluation runner (AC-1..3).

Runs every labelled case through the real graph (stub LLM, deterministic), scores the
§10.2 metrics, and enforces the hard gates: guardrail-violation rate 0% and
satisfaction-floor adherence 100% → non-zero exit on failure.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import yaml

from agent.decision.cost_model import order_value
from agent.deps import get_deps, reset_deps
from agent.runner import is_paused, run_config
from agent.state import initial_state
from config.settings import settings
from db.dataset import REFERENCE_DATE
from eval import metrics

CASES_PATH = Path(__file__).parent / "cases.yaml"
_DEFLECTION = metrics.DEFLECTION_ACTIONS
_DEFECT = metrics.DEFECT_CAUSES


def _build_graph():
    from langgraph.checkpoint.memory import MemorySaver  # eval harness (not agent/)

    from agent.graph import build_graph

    return build_graph(checkpointer=MemorySaver())


def _score_case(case: dict[str, Any]) -> dict[str, Any]:
    reset_deps()  # isolate each case (no cross-case rate-limit contamination)
    graph = _build_graph()
    repo = get_deps().repo
    cfg = run_config(case["id"])

    state = initial_state(case["id"], case["scenario_text"], "api",
                          case["seeded_order_id"], case["seeded_customer_id"])
    t0 = time.perf_counter()
    final = graph.invoke(state, cfg)
    latency = time.perf_counter() - t0

    paused = is_paused(graph, cfg)
    requires_human = bool(paused or final.get("requires_human"))
    action = (final.get("executed_action") or final.get("proposed_action") or {})
    action_type = action.get("action_type")
    order = final.get("order_context") or {}
    grd = final.get("guardrail_status")

    # guardrail violation: a violation status, or an executed refund exceeding order value.
    violation = grd == "violation"
    if action_type in ("instant_refund", "partial_refund", "store_credit_refund") and order:
        if (action.get("amount") or 0) > order_value(order) + 1e-6 and not requires_human:
            violation = True

    root = final.get("root_cause")
    expected_esc = case["expected_escalation"]
    if expected_esc:
        action_appropriate = requires_human
    else:
        action_appropriate = (action_type in case["expected_action"]) and not requires_human

    sat_ok = True
    if case["expected_root_cause"] in _DEFECT:
        sat_ok = action_type in metrics.ADEQUATE_FOR_DEFECT or requires_human

    deflectable = (not requires_human) and action_type != "deny_with_explanation"
    deflected = action_type in _DEFLECTION

    return {
        "id": case["id"],
        "expected_root_cause": case["expected_root_cause"],
        "actual_root_cause": root,
        "root_correct": root == case["expected_root_cause"],
        "expected_escalation": expected_esc,
        "actual_escalation": requires_human,
        "escalation_correct": requires_human == expected_esc,
        "action_type": action_type,
        "action_appropriate": action_appropriate,
        "satisfaction_floor_ok": sat_ok,
        "guardrail_violation": violation,
        "guardrail_status": grd,
        "deflectable": deflectable,
        "deflected": deflected,
        "expected_saving": final.get("expected_saving") or 0.0,
        "latency": latency,
    }


def run_eval(cases_path: Path | None = None) -> dict[str, Any]:
    settings.AS_OF_DATE = REFERENCE_DATE.isoformat()  # pin window math
    cases = yaml.safe_load((cases_path or CASES_PATH).read_text(encoding="utf-8"))
    results = [_score_case(c) for c in cases]
    report = metrics.compute(results)
    return {"report": report, "results": results}


def _print_report(report: dict[str, Any]) -> None:
    print("\n===== ReturnGuard Evaluation Report =====")
    for k, v in report.items():
        print(f"  {k:<28} {v}")


def main() -> int:
    out = run_eval()
    report, results = out["report"], out["results"]
    _print_report(report)

    hard = metrics.gate_failures(report)
    soft = metrics.soft_gaps(report)
    if soft:
        print("\n-- soft-metric gaps (recorded, non-blocking) --")
        for g in soft:
            print(f"  - {g}")
    wrong = [r for r in results if not r["root_correct"] or not r["action_appropriate"] or not r["escalation_correct"]]
    if wrong:
        print("\n-- mismatched cases --")
        for r in wrong[:20]:
            print(f"  {r['id']}: root {r['actual_root_cause']} (exp {r['expected_root_cause']}), "
                  f"action {r['action_type']}, esc {r['actual_escalation']} (exp {r['expected_escalation']})")

    if hard:
        print("\nHARD GATES FAILED:")
        for h in hard:
            print(f"  [X] {h}")
        return 1
    print("\n[OK] HARD GATES PASSED (guardrail-violation 0%, satisfaction-floor 100%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
