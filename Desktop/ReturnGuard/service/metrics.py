"""Business metrics aggregation (FR-RPT-1).

Computes the analytics summary from the resolutions + audit_log: totals, auto-resolution /
escalation / deflection rates, estimated INR saved vs an always-refund baseline, average
latency, action-type distribution, and the guardrail-violation count (MUST be 0).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from db.repository import Repository
from eval.metrics import DEFLECTION_ACTIONS


def _action_of(row: dict[str, Any]) -> str | None:
    action = row.get("executed_action") or row.get("proposed_action") or {}
    return action.get("action_type") if isinstance(action, dict) else None


def compute_summary(repo: Repository) -> dict[str, Any]:
    rows = repo.list_resolutions(limit=100000)
    total = len(rows)

    escalated = [r for r in rows if r.get("requires_human")]
    auto = [r for r in rows if not r.get("requires_human")]
    deflectable = [r for r in auto if _action_of(r) != "deny_with_explanation"]
    deflected = [r for r in deflectable if _action_of(r) in DEFLECTION_ACTIONS]

    inr_saved = round(sum((r.get("expected_saving") or 0.0) for r in deflected), 2)
    action_dist = dict(Counter(_action_of(r) for r in rows if _action_of(r)))
    violations = sum(1 for r in rows if r.get("guardrail_status") == "violation")
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else None

    def rate(part: list, whole: list) -> float:
        return round(len(part) / len(whole), 4) if whole else 0.0

    return {
        "total_resolutions": total,
        "auto_resolution_rate": rate(auto, rows),
        "escalation_rate": rate(escalated, rows),
        "deflection_rate": rate(deflected, deflectable),
        "estimated_inr_saved_vs_always_refund": inr_saved,
        "avg_resolution_latency_ms": avg_latency,
        "action_type_distribution": action_dist,
        "guardrail_violation_count": violations,  # MUST be 0
    }
