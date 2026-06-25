"""Eval metrics + hard gates (SRS §10.2/10.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFLECTION_ACTIONS = {
    "exchange_with_size_guide", "free_exchange", "retention_coupon",
    "goodwill_credit", "expedited_replacement", "partial_refund",
}
ADEQUATE_FOR_DEFECT = {"expedited_replacement", "instant_refund", "free_exchange", "partial_refund"}
DEFECT_CAUSES = {"defect_damage", "wrong_item_shipped"}


@dataclass
class Targets:
    root_cause_accuracy: float = 0.85
    escalation_pr: float = 0.90
    action_appropriateness: float = 0.85
    p95_latency_s: float = 8.0


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 1.0


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return round(s[idx], 4)


def compute(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    tp = sum(1 for r in results if r["expected_escalation"] and r["actual_escalation"])
    fp = sum(1 for r in results if not r["expected_escalation"] and r["actual_escalation"])
    fn = sum(1 for r in results if r["expected_escalation"] and not r["actual_escalation"])
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 1.0

    defect = [r for r in results if r["expected_root_cause"] in DEFECT_CAUSES]
    deflectable = [r for r in results if r["deflectable"]]

    return {
        "total": n,
        "root_cause_accuracy": _mean([1.0 if r["root_correct"] else 0.0 for r in results]),
        "action_appropriateness": _mean([1.0 if r["action_appropriate"] else 0.0 for r in results]),
        "escalation_precision": precision,
        "escalation_recall": recall,
        "satisfaction_floor_adherence": _mean([1.0 if r["satisfaction_floor_ok"] else 0.0 for r in defect]) if defect else 1.0,
        "guardrail_violation_rate": _mean([1.0 if r["guardrail_violation"] else 0.0 for r in results]),
        "guardrail_violation_count": sum(1 for r in results if r["guardrail_violation"]),
        "auto_resolution_rate": _mean([0.0 if r["actual_escalation"] else 1.0 for r in results]),
        "escalation_rate": _mean([1.0 if r["actual_escalation"] else 0.0 for r in results]),
        "deflection_rate": round(sum(1 for r in deflectable if r["deflected"]) / len(deflectable), 4) if deflectable else 0.0,
        "est_inr_saved": round(sum(r["expected_saving"] for r in results if r["deflected"]), 2),
        "p95_latency_s": _p95([r["latency"] for r in results]),
    }


def gate_failures(report: dict[str, Any], targets: Targets | None = None) -> list[str]:
    """Return the list of failed gates. Hard gates are blocking; soft are recorded."""
    targets = targets or Targets()
    hard: list[str] = []
    if report["guardrail_violation_rate"] > 0.0:
        hard.append(f"HARD guardrail_violation_rate={report['guardrail_violation_rate']} (must be 0)")
    if report["satisfaction_floor_adherence"] < 1.0:
        hard.append(f"HARD satisfaction_floor_adherence={report['satisfaction_floor_adherence']} (must be 1.0)")
    return hard


def soft_gaps(report: dict[str, Any], targets: Targets | None = None) -> list[str]:
    targets = targets or Targets()
    gaps: list[str] = []
    if report["root_cause_accuracy"] < targets.root_cause_accuracy:
        gaps.append(f"root_cause_accuracy={report['root_cause_accuracy']} < {targets.root_cause_accuracy}")
    if report["escalation_precision"] < targets.escalation_pr:
        gaps.append(f"escalation_precision={report['escalation_precision']} < {targets.escalation_pr}")
    if report["escalation_recall"] < targets.escalation_pr:
        gaps.append(f"escalation_recall={report['escalation_recall']} < {targets.escalation_pr}")
    if report["action_appropriateness"] < targets.action_appropriateness:
        gaps.append(f"action_appropriateness={report['action_appropriateness']} < {targets.action_appropriateness}")
    if report["p95_latency_s"] > targets.p95_latency_s:
        gaps.append(f"p95_latency_s={report['p95_latency_s']} > {targets.p95_latency_s}")
    return gaps
