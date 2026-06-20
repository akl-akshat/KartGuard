"""Phase 6 — constrained selection unit tests (§9.4)."""

import pytest

from agent.decision.eligibility import eligible_actions
from agent.decision.select import select_action

pytestmark = pytest.mark.unit


def _order(price=1000.0, category="apparel"):
    return {"id": "O", "customer_id": "C", "price": price, "qty": 1,
            "category": category, "payment_mode": "PREPAID", "delivery_status": "delivered"}


def test_size_fit_selects_cheapest_exchange():
    order = _order(price=1299.0)
    elig = eligible_actions("size_fit_mismatch", "apparel", within_window=True)
    proposed, candidates = select_action("size_fit_mismatch", elig, order, True)
    assert proposed["action_type"] == "exchange_with_size_guide"
    assert proposed["estimated_cost"] == 90.0
    assert len(candidates) == len(elig)


def test_defect_selects_replacement_with_goodwill_never_deny():
    order = _order(price=1899.0, category="electronics")
    elig = eligible_actions("defect_damage", "electronics", within_window=True)
    proposed, _ = select_action("defect_damage", elig, order, True)
    assert proposed["action_type"] == "expedited_replacement"
    assert proposed["params"].get("goodwill") == 100.0
    assert proposed["action_type"] != "deny_with_explanation"


def test_min_cost_chosen_on_constructed_set():
    # Construct a two-remedy set; the provably cheaper one must win.
    order = _order(price=1000.0)
    proposed, _ = select_action("genuine_other", {"instant_refund", "partial_refund"}, order, True)
    assert proposed["action_type"] == "partial_refund"   # 300 < 1000
    assert proposed["estimated_cost"] == 300.0


def test_fraud_proposes_escalation():
    order = _order(price=4999.0, category="electronics")
    elig = eligible_actions("fraud_suspected", "electronics", within_window=True)
    proposed, _ = select_action("fraud_suspected", elig, order, True)
    assert proposed["action_type"] == "escalate_to_human"


def test_out_of_window_changed_mind_denies():
    order = _order(price=800.0)
    elig = eligible_actions("changed_mind", "apparel", within_window=False)
    proposed, _ = select_action("changed_mind", elig, order, False)
    assert proposed["action_type"] == "deny_with_explanation"


def test_changed_mind_in_window_prefers_coupon_over_refund():
    order = _order(price=699.0)
    elig = eligible_actions("changed_mind", "apparel", within_window=True)
    proposed, _ = select_action("changed_mind", elig, order, True)
    assert proposed["action_type"] == "retention_coupon"


def test_proposed_carries_cost_and_saving_annotations():
    order = _order(price=1000.0)
    elig = eligible_actions("size_fit_mismatch", "apparel", within_window=True)
    proposed, _ = select_action("size_fit_mismatch", elig, order, True)
    assert proposed["expected_return_cost"] == 195.0
    assert proposed["expected_saving"] >= 0.0
    assert proposed["rationale"]
