"""Safety: the customer reply must never reveal risk/fraud reasoning (FR-RES-2, NFR-CMP-2)."""

import pytest

from agent.reply import compose_reply, scrub

pytestmark = [pytest.mark.safety, pytest.mark.unit]

_FORBIDDEN = ("risk", "fraud", "serial", "wardrob", "abuse", "score")


def _reply(**state):
    return compose_reply(state)


@pytest.mark.parametrize("state", [
    {"status": "resolved", "executed_action": {"action_type": "exchange_with_size_guide"}},
    {"status": "resolved", "executed_action": {"action_type": "instant_refund", "amount": 1299.0}},
    {"status": "resolved", "executed_action": {"action_type": "retention_coupon", "amount": 120.0}},
    {"status": "denied", "human_decision": "reject", "within_return_window": False},
    {"requires_human": True, "risk_score": 0.92, "risk_factors": ["serial_returner", "high_order_value"]},
])
def test_reply_has_no_risk_vocabulary(state):
    msg = _reply(**state).lower()
    assert not any(term in msg for term in _FORBIDDEN)


def test_deflection_states_refund_right():
    # NFR-CMP-1: a deflection offer must surface the standard-refund right.
    msg = _reply(status="resolved", executed_action={"action_type": "exchange_with_size_guide"})
    assert "refund" in msg.lower()


def test_scrub_backstops_accidental_leak():
    leaked = "Your fraud risk score was 0.9 so we denied it."
    assert "fraud" not in scrub(leaked).lower()
