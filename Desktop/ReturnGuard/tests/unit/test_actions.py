"""Phase 3 checkpoints: simulated action tools — audit, idempotency, events."""

import pytest

from db.repository import InMemoryRepository
from events.emit import drain_sink
from tools.actions import execute_action, process_refund
from tools.data_access import LocalDataAccess

pytestmark = [pytest.mark.unit, pytest.mark.safety]


@pytest.fixture
def setup():
    repo = InMemoryRepository()
    data = LocalDataAccess(repo)
    order = data.get_order("ORD-FIT-PREPAID")
    drain_sink()
    return repo, order


def test_refund_is_idempotent_single_audit_row(setup):
    repo, order = setup
    r1 = process_refund(repo, "req-1", order, 1299.0)
    r2 = process_refund(repo, "req-1", order, 1299.0)  # redelivery
    assert r1["status"] == "applied"
    assert r2["status"] == "noop_idempotent"
    rows = repo.get_audit("req-1", "instant_refund")
    assert len(rows) == 1, "redelivered request must not double-write audit"


def test_audit_records_amount_and_actor(setup):
    repo, order = setup
    process_refund(repo, "req-2", order, 500.0, actor="human:op42")
    row = repo.get_audit("req-2")[0]
    assert row["amount"] == 500.0
    assert row["actor"] == "human:op42"
    assert row["action_type"] == "instant_refund"


def test_audit_event_emitted_once(setup):
    repo, order = setup
    process_refund(repo, "req-3", order, 700.0)
    process_refund(repo, "req-3", order, 700.0)  # idempotent no-op
    events = [e for e in drain_sink() if e["payload"].get("request_id") == "req-3"]
    assert len(events) == 1


def test_execute_action_dispatch(setup):
    repo, order = setup
    cases = [
        {"action_type": "exchange_with_size_guide"},
        {"action_type": "retention_coupon", "amount": 120.0},
        {"action_type": "expedited_replacement"},
        {"action_type": "goodwill_credit", "amount": 100.0},
        {"action_type": "deny_with_explanation", "params": {"reason": "out_of_window"}},
        {"action_type": "provide_information"},
    ]
    for i, action in enumerate(cases):
        res = execute_action(repo, f"disp-{i}", action, order)
        assert res["status"] == "applied"
        assert res["action_type"] == action["action_type"]


def test_unknown_action_type_raises(setup):
    repo, order = setup
    with pytest.raises(ValueError):
        execute_action(repo, "x", {"action_type": "mint_money"}, order)
