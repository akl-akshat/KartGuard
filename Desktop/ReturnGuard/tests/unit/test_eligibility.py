"""Phase 6 — eligibility unit tests (§9.2/9.3), incl. the satisfaction floor."""

import pytest

from agent.decision.eligibility import eligible_actions, satisfaction_ok

pytestmark = pytest.mark.unit


def test_size_fit_prefers_exchange_set():
    s = eligible_actions("size_fit_mismatch", "apparel", within_window=True)
    assert "exchange_with_size_guide" in s
    assert "deny_with_explanation" not in s


@pytest.mark.parametrize("cause", ["defect_damage", "wrong_item_shipped"])
def test_defect_never_allows_denial(cause):
    s = eligible_actions(cause, "electronics", within_window=False)  # even out of window
    assert "deny_with_explanation" not in s
    assert "expedited_replacement" in s and "instant_refund" in s


def test_fraud_forces_escalation_set():
    s = eligible_actions("fraud_suspected", "apparel", within_window=True)
    assert s == {"escalate_to_human", "deny_with_explanation"}


def test_out_of_window_non_defect_is_deny_only():
    s = eligible_actions("changed_mind", "apparel", within_window=False)
    assert s == {"deny_with_explanation", "provide_information"}


def test_non_returnable_category_non_defect_is_deny_only():
    s = eligible_actions("changed_mind", "beauty", within_window=True)
    assert "instant_refund" not in s
    assert "deny_with_explanation" in s


def test_delivery_delay_allows_info_and_goodwill_regardless_of_window():
    s = eligible_actions("delivery_delay", "home", within_window=False)
    assert "provide_information" in s and "goodwill_credit" in s


def test_satisfaction_floor():
    assert satisfaction_ok("expedited_replacement", "defect_damage") is True
    assert satisfaction_ok("deny_with_explanation", "defect_damage") is False
    assert satisfaction_ok("provide_information", "defect_damage") is False
    assert satisfaction_ok("provide_information", "changed_mind") is True
