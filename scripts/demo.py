"""Acceptance-criteria demonstration (SRS §13.1).

Prints unambiguous evidence for:
  AC-4  a paused escalation persists and resumes correctly,
  AC-5  a redelivered request does not double-execute a financial action,
  AC-6  every monetary action has a matching immutable audit row + emitted event,
and finishes with the live metrics summary.

Runs fully offline (stub LLM, in-memory store). In production the AC-4 durability is
provided by the Postgres checkpointer, so a paused escalation also survives a process
restart — the resume mechanism shown here is exactly the one used after a restart.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AS_OF_DATE", "2026-06-22")

from agent.deps import get_deps, reset_deps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.runner import is_paused, resume, run_config  # noqa: E402
from agent.state import initial_state  # noqa: E402
from events.consumer import handle_message  # noqa: E402
from events.emit import peek_sink  # noqa: E402
from service.metrics import compute_summary  # noqa: E402


def _graph():
    from langgraph.checkpoint.memory import MemorySaver

    return build_graph(checkpointer=MemorySaver())


def hr(title: str) -> None:
    print(f"\n{'='*64}\n{title}\n{'='*64}")


def demo_ac4() -> None:
    hr("AC-4 — paused escalation persists and resumes")
    g = _graph()
    cfg = run_config("demo-ac4")
    g.invoke(initial_state("demo-ac4", "I want to return this tablet for a refund",
                           order_id="ORD-HIVAL-COD", customer_id="CUST-SERIAL"), cfg)
    repo = get_deps().repo
    esc = repo.get_escalation("demo-ac4")
    snap = g.get_state(cfg)
    print(f"  paused at interrupt: {is_paused(g, cfg)}")
    print(f"  escalation persisted: status={esc['status']} action={esc['recommendation']['proposed_action']['action_type']}")
    print(f"  checkpointed state has root_cause={snap.values.get('root_cause')} risk={snap.values.get('risk_score')}")
    final = resume(g, cfg, {"decision": "modify", "reviewer_id": "ops-7",
                            "modified_action": {"action_type": "partial_refund", "amount": 1500.0}})
    audit = repo.get_audit("demo-ac4", "partial_refund")
    print(f"  resumed -> status={final['status']} executed={final['executed_action']['action_type']} "
          f"by {audit[0]['actor']} (re-checked against guardrails)")


def demo_ac5() -> None:
    hr("AC-5 — redelivered request does not double-execute")
    g = _graph()
    repo = get_deps().repo
    msg = {"request_id": "demo-ac5", "issue_text": "I changed my mind, please take it back",
           "order_id": "ORD-MIND-PREPAID", "customer_id": "CUST-VIP1"}
    r1 = handle_message(msg, g, repo)
    r2 = handle_message(msg, g, repo)  # redelivery
    rows = repo.get_audit("demo-ac5", "retention_coupon")
    print(f"  first delivery : {r1['status']} -> {r1.get('action_type')}")
    print(f"  redelivery     : {r2['status']}")
    print(f"  financial audit rows for request: {len(rows)}  (expected: 1)")


def demo_ac6() -> None:
    hr("AC-6 — every monetary action has an immutable audit row + event")
    repo = get_deps().repo
    rows = repo.get_audit("demo-ac5")
    monetary = [a for a in rows if a["action_type"] == "retention_coupon"]
    audit_events = [e for e in peek_sink() if e["topic"].endswith("audit.v1") and e["key"] == "demo-ac5"]
    print(f"  audit row: action={monetary[0]['action_type']} amount={monetary[0]['amount']} actor={monetary[0]['actor']}")
    print(f"  matching audit events emitted: {len(audit_events)}")
    print("  audit_log is append-only at the app layer (no update/delete API exists).")


def demo_metrics() -> None:
    hr("Metrics summary")
    m = compute_summary(get_deps().repo)
    for k, v in m.items():
        print(f"  {k:<38} {v}")
    assert m["guardrail_violation_count"] == 0, "guardrail violations must be 0"


def main() -> int:
    reset_deps()
    demo_ac5()
    demo_ac6()
    demo_ac4()
    demo_metrics()
    hr("All acceptance demonstrations completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
