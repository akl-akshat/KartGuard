"""Phase 1 checkpoints as tests: dataset distributions + repository invariants."""

import pytest

from db.dataset import NON_RETURNABLE, REFERENCE_DATE, build_dataset
from db.repository import InMemoryRepository

pytestmark = pytest.mark.unit


def test_dataset_is_deterministic():
    a = build_dataset().as_dicts()
    b = build_dataset().as_dicts()
    assert a == b


def test_required_distributions_present():
    ds = build_dataset()
    cod = [o for o in ds.orders if o.payment_mode == "COD"]
    prepaid = [o for o in ds.orders if o.payment_mode == "PREPAID"]
    oow = [o for o in ds.orders if o.return_window_end and o.return_window_end < REFERENCE_DATE]
    in_window = [o for o in ds.orders if o.return_window_end and o.return_window_end >= REFERENCE_DATE]
    non_ret = [o for o in ds.orders if o.category in NON_RETURNABLE]
    assert len(ds.customers) >= 50
    assert len(ds.orders) >= 200
    assert cod and prepaid, "both payment modes must be present"
    assert oow and in_window, "both in- and out-of-window orders must exist"
    assert non_ret, "at least one non-returnable-category order"
    assert any("serial_returner" in c.risk_flags for c in ds.customers)


def test_named_fixtures_exist():
    repo = InMemoryRepository()
    for oid in ("ORD-FIT-PREPAID", "ORD-DEFECT-ELEC", "ORD-HIVAL-COD", "ORD-OOW-NONRET"):
        assert repo.get_order(oid) is not None, oid
    hi = repo.get_order("ORD-HIVAL-COD")
    assert hi.price > 2000 and hi.payment_mode == "COD"  # escalation-warranting value
    oow = repo.get_order("ORD-OOW-NONRET")
    assert oow.category in NON_RETURNABLE and oow.return_window_end is None


def test_repository_unknown_ids_return_none():
    repo = InMemoryRepository()
    assert repo.get_order("NOPE") is None
    assert repo.get_customer("NOPE") is None
    assert repo.get_return_history("NOPE")["found"] is False


def test_audit_is_append_only_and_idempotency_queryable():
    repo = InMemoryRepository()
    repo.append_audit({"request_id": "r1", "action_type": "instant_refund",
                       "amount": 500, "actor": "agent", "customer_id": "CUST-LOW1"})
    assert len(repo.get_audit("r1", "instant_refund")) == 1
    # no update/delete API exists -> append-only is structural
    assert not hasattr(repo, "update_audit") and not hasattr(repo, "delete_audit")


def test_serial_returner_named_fixture_has_high_rate():
    repo = InMemoryRepository()
    c = repo.get_customer("CUST-SERIAL")
    assert c.return_rate >= 0.45
    assert "serial_returner" in c.risk_flags
