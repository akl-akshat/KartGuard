"""Adversarial QA — property-based & metamorphic invariants (find what cases miss)."""

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

from agent.decision.guardrails import evaluate_guardrails
from agent.risk_model import score_and_factors
from config.settings import settings

pytestmark = pytest.mark.property

MONEY = {"instant_refund", "partial_refund", "store_credit_refund", "retention_coupon", "goodwill_credit"}
REFUNDISH = {"instant_refund", "partial_refund", "store_credit_refund"}


@hyp_settings(max_examples=400, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    action_type=st.sampled_from(sorted(MONEY)),
    amount=st.floats(min_value=-5000, max_value=50000, allow_nan=False, allow_infinity=False),
    price=st.floats(min_value=1, max_value=200000, allow_nan=False, allow_infinity=False),
    risk=st.floats(min_value=0, max_value=1),
    rate=st.integers(min_value=0, max_value=10),
)
def test_guardrail_invariants_hold_for_any_input(action_type, amount, price, risk, rate):
    """For ANY input, an auto-executed (pass, no-human) monetary action must satisfy:
    0 <= amount <= order_value, amount <= auto-ceiling, coupon <= both caps."""
    order = {"id": "O", "customer_id": "C", "price": price, "qty": 1, "delivery_status": "delivered"}
    r = evaluate_guardrails({"action_type": action_type, "amount": amount}, order, risk, rate)
    if r.status == "pass" and not r.requires_human:
        amt = r.action.get("amount", 0.0)
        value = price
        assert amt >= 0, f"auto-executed NEGATIVE amount {amt}"
        if action_type in REFUNDISH:
            assert amt <= value + 1e-6, f"auto refund {amt} > order value {value}"
            assert amt <= settings.MAX_AUTO_REFUND_ABS + 1e-6, f"auto refund {amt} > ceiling"
        if action_type == "retention_coupon":
            assert amt <= settings.MAX_COUPON_ABS + 1e-6
            assert amt <= settings.MAX_COUPON_PCT * value + 1e-6


@hyp_settings(max_examples=300)
@given(
    return_rate=st.floats(min_value=0, max_value=1),
    cod=st.floats(min_value=0, max_value=1),
    rto=st.floats(min_value=0, max_value=1),
    cap=st.floats(min_value=0, max_value=1),
    high=st.booleans(),
    flags=st.lists(st.sampled_from(["serial_returner", "cod_refuser"]), max_size=2),
)
def test_risk_score_always_bounded(return_rate, cod, rto, cap, high, flags):
    signals = {"return_rate": return_rate, "cod_refusal_rate": cod, "region_rto_baseline": rto,
               "category_abuse_propensity": cap, "high_value_order": high, "risk_flags": flags,
               "category": "apparel"}
    score, factors = score_and_factors(signals)
    assert 0.0 <= score <= 1.0
    assert factors  # always names at least one factor


@hyp_settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=st.text(min_size=0, max_size=500))
def test_fuzz_raw_request_never_crashes_classifier(text):
    """T-FUZZ: arbitrary text must classify without crashing and never emit an action."""
    from agent.stub_brain import classify_issue, diagnose, extract_ids
    issue = classify_issue(text)
    assert issue in {
        "return_request", "cancel_request", "refund_status", "damaged_item", "wrong_item",
        "wrong_size", "late_delivery", "missing_item", "quality_complaint", "rto_predicted", "other",
    }
    extract_ids(text)
    diagnose(issue, text, 0.1)  # must not raise


def test_metamorphic_higher_risk_never_reduces_escalation():
    """T-META: raising risk past the threshold must not DECREASE escalation likelihood."""
    order = {"id": "O", "customer_id": "C", "price": 1000, "qty": 1, "delivery_status": "delivered"}
    action = {"action_type": "instant_refund", "amount": 1000}
    low = evaluate_guardrails(action, order, 0.1, 0).requires_human
    high = evaluate_guardrails(action, order, 0.95, 0).requires_human
    assert high or not low  # high-risk must escalate at least as often as low-risk


def test_metamorphic_value_past_ceiling_forces_escalation():
    below = {"id": "O", "customer_id": "C", "price": settings.MAX_AUTO_REFUND_ABS - 1, "qty": 1}
    above = {"id": "O", "customer_id": "C", "price": settings.MAX_AUTO_REFUND_ABS + 1000, "qty": 1}
    a_below = {"action_type": "instant_refund", "amount": settings.MAX_AUTO_REFUND_ABS - 1}
    a_above = {"action_type": "instant_refund", "amount": settings.MAX_AUTO_REFUND_ABS + 500}
    assert not evaluate_guardrails(a_below, below, 0.1, 0).requires_human
    assert evaluate_guardrails(a_above, above, 0.1, 0).requires_human
