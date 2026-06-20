"""Phase 6 — cost model unit tests (§9.1). Pure, deterministic, hand-computed."""

import pytest

from agent.decision.cost_model import action_cost, expected_return_cost, expected_saving, order_value

pytestmark = pytest.mark.unit

ORDER = {"price": 1000.0, "qty": 1, "delivery_status": "delivered"}


def test_order_value():
    assert order_value({"price": 250.0, "qty": 2}) == 500.0


def test_expected_return_cost():
    # 80 reverse + 40 restocking + 0.30*1000*0.25 margin loss = 195
    assert expected_return_cost(ORDER) == 195.0


def test_expected_return_cost_rto_adds_forward_leg():
    rto = {"price": 1000.0, "qty": 1, "delivery_status": "rto"}
    assert expected_return_cost(rto) == 195.0 + 60.0


@pytest.mark.parametrize("action_type,amount,expected", [
    ("instant_refund", 1000.0, 1000.0),
    ("store_credit_refund", 1000.0, 1000.0),
    ("partial_refund", 300.0, 300.0),
    ("retention_coupon", 200.0, 140.0),       # 200 face * 0.70 redemption
    ("free_exchange", 0.0, 90.0),
    ("exchange_with_size_guide", 0.0, 90.0),
    ("expedited_replacement", 0.0, 120.0),
    ("goodwill_credit", 100.0, 100.0),
    ("provide_information", 0.0, 0.0),
    ("deny_with_explanation", 0.0, 0.0),
    ("escalate_to_human", 0.0, 0.0),
])
def test_action_cost(action_type, amount, expected):
    assert action_cost(action_type, amount, ORDER) == expected


def test_expected_saving_floored_at_zero():
    # full refund (1000) costs more than C_return (195) -> saving floored to 0
    assert expected_saving(ORDER, "instant_refund", 1000.0) == 0.0
    # exchange (90) < C_return (195) -> saving 105
    assert expected_saving(ORDER, "exchange_with_size_guide", 0.0) == 105.0
