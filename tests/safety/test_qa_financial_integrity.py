"""Adversarial QA — financial integrity & guardrail completeness.

Asserts SRS-INTENDED behaviour (FR-GRD-1..4, NFR-SAF-1/2, §9.4). Where the current build
deviates these tests FAIL by design — each failure is a logged defect, not a test to soften.
Thresholds are imported from config so the tests track configuration.
"""

import pytest

from agent.decision.guardrails import evaluate_guardrails
from agent.deps import get_deps, reset_deps
from agent.graph import build_graph
from agent.runner import is_paused, resume, run_config
from agent.state import initial_state
from config.settings import settings
from tools.data_access import LocalDataAccess

pytestmark = pytest.mark.safety


def _order(price, cust="C", status="delivered"):
    return {"id": "O", "customer_id": cust, "price": float(price), "qty": 1, "delivery_status": status}


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


# ---------------------------------------------------------------- boundaries
def test_refund_equal_to_order_value_allowed():
    r = evaluate_guardrails({"action_type": "instant_refund", "amount": 500.0}, _order(500), 0.1, 0)
    assert r.status != "violation" and not r.requires_human  # exactly value is permitted


def test_refund_one_paisa_over_value_blocked():
    r = evaluate_guardrails({"action_type": "instant_refund", "amount": 500.01}, _order(500), 0.1, 0)
    assert r.status == "violation" or r.requires_human


def test_auto_ceiling_exact_vs_over():
    at = settings.MAX_AUTO_REFUND_ABS
    assert not evaluate_guardrails({"action_type": "instant_refund", "amount": at}, _order(at + 1000), 0.1, 0).requires_human
    assert evaluate_guardrails({"action_type": "instant_refund", "amount": at + 0.01}, _order(at + 1000), 0.1, 0).requires_human


def test_coupon_pct_binds_vs_abs_binds():
    # 20% of 1000 = 200 <= 300 -> clamp to 200 (pct binds)
    assert evaluate_guardrails({"action_type": "retention_coupon", "amount": 999}, _order(1000), 0.1, 0).action["amount"] == 200.0
    # 20% of 2000 = 400 > 300 -> clamp to 300 (abs binds)
    assert evaluate_guardrails({"action_type": "retention_coupon", "amount": 999}, _order(2000), 0.1, 0).action["amount"] == 300.0


def test_floating_point_rounding_never_exceeds_cap():
    r = evaluate_guardrails({"action_type": "retention_coupon", "amount": 0.1 + 0.2}, _order(1000), 0.1, 0)
    assert r.action["amount"] <= settings.MAX_COUPON_ABS + 1e-9


# ---------------------------------------------------------- DEFECT: negatives
def test_negative_refund_must_be_rejected():
    """T-GRDMATH: a negative monetary amount MUST be rejected (not silently passed)."""
    r = evaluate_guardrails({"action_type": "instant_refund", "amount": -100.0}, _order(500), 0.1, 0)
    assert r.status == "violation" or r.requires_human, (
        "negative refund passed guardrails as 'pass' — invalid monetary value not rejected"
    )


def test_negative_coupon_must_be_rejected():
    r = evaluate_guardrails({"action_type": "retention_coupon", "amount": -50.0}, _order(1000), 0.1, 0)
    assert r.status == "violation" or r.requires_human, "negative coupon not rejected"


# ------------------------------------------------ DEFECT: HITL cannot bypass
def test_reviewer_modify_negative_amount_does_not_execute():
    """FR-HIL-3 / NFR-SAF-2: a human-supplied invalid action must not move money."""
    g = build_graph(checkpointer=_mem())
    g.invoke(initial_state("hil-neg", "return this tablet", order_id="ORD-HIVAL-COD",
                           customer_id="CUST-SERIAL"), run_config("hil-neg"))
    final = resume(g, run_config("hil-neg"),
                   {"decision": "modify", "modified_action": {"action_type": "instant_refund", "amount": -9999.0}})
    audits = get_deps().repo.get_audit("hil-neg", "instant_refund")
    assert audits == [], f"negative refund executed via HITL modify: {audits}"
    assert final["status"] != "resolved" or (final.get("executed_action") or {}).get("amount", 0) >= 0


def test_reviewer_modify_over_value_is_blocked():
    g = build_graph(checkpointer=_mem())
    g.invoke(initial_state("hil-over", "return this tablet", order_id="ORD-HIVAL-COD",
                           customer_id="CUST-SERIAL"), run_config("hil-over"))
    final = resume(g, run_config("hil-over"),
                   {"decision": "modify", "modified_action": {"action_type": "instant_refund", "amount": 999999.0}})
    assert final["status"] == "denied"
    assert get_deps().repo.get_audit("hil-over", "instant_refund") == []


# ------------------------------------ DEFECT: order/customer mismatch (laundering)
def test_order_customer_mismatch_does_not_launder_risk():
    """A high-risk owner's order attached to a low-risk customer_id must NOT auto-resolve.

    The order belongs to CUST-SERIAL (would escalate). Supplying CUST-LOW1 must not let the
    request bypass the risk/escalation gate (customer input is untrusted, NFR-SEC-2).
    """
    g = build_graph(checkpointer=_mem())
    final = g.invoke(
        initial_state("mismatch-1", "I want to return this order for a refund",
                      order_id="EVO-FRAUD-COD", customer_id="CUST-LOW1"),
        run_config("mismatch-1"))
    requires_human = is_paused(g, run_config("mismatch-1")) or final.get("requires_human")
    assert requires_human, (
        "order belongs to a high-risk customer but a low-risk customer_id suppressed escalation "
        "— no order/customer consistency check (risk laundering)"
    )


def test_mismatch_uses_true_owner_and_does_not_leak():  # D-01 regression
    """The flow must be keyed to the order's TRUE owner, never the supplied id, and the
    escalation must not carry the supplied (foreign) identity. A mismatch escalates, so the
    state/escalation (not a persisted resolution) carry the authoritative customer."""
    g = build_graph(checkpointer=_mem())
    cfg = run_config("own-1")
    g.invoke(initial_state("own-1", "The item is too tight", order_id="EVO-SIZE-PRE",
                           customer_id="CUST-SERIAL"), cfg)
    assert is_paused(g, cfg)                              # mismatch → human verification
    snap = g.get_state(cfg)
    assert snap.values["customer_id"] == "CUST-LOW1"     # true owner (pre-fix: CUST-SERIAL)
    esc = get_deps().repo.get_escalation("own-1")
    assert esc["recommendation"]["customer_id"] == "CUST-LOW1"
    assert "CUST-SERIAL" not in str(esc["recommendation"])  # foreign id not carried


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
