"""Phase 3 checkpoints: data-access seam + fraud signal discrimination."""

import pytest

from tools.data_access import LocalDataAccess

pytestmark = pytest.mark.unit


@pytest.fixture
def data():
    return LocalDataAccess()


def test_known_order_and_customer_resolve(data):
    o = data.get_order("ORD-FIT-PREPAID")
    assert o and o["category"] == "apparel" and o["payment_mode"] == "PREPAID"
    c = data.get_customer("CUST-LOW1")
    assert c and c["segment"] == "loyal"


def test_unknown_ids_return_none_not_fabricated(data):
    assert data.get_order("ORD-NOPE") is None
    assert data.get_customer("CUST-NOPE") is None
    assert data.get_return_history("CUST-NOPE")["found"] is False


def test_order_status_window_fields(data):
    st = data.get_order_status("ORD-FIT-PREPAID")
    assert st["delivery_status"] == "delivered"
    assert st["return_window_end"] is not None


def test_high_vs_low_risk_signals_differ(data):
    hi = data.get_risk_signals("CUST-SERIAL", "ORD-HIVAL-COD")
    lo = data.get_risk_signals("CUST-LOW1", "ORD-FIT-PREPAID")
    assert hi["return_rate"] > lo["return_rate"]
    assert hi["high_value_order"] is True and lo["high_value_order"] is False
    assert hi["wardrobing_suspected"] is False  # electronics, not apparel/footwear
    assert hi["cod_refusal_rate"] > 0
    assert "serial_returner" in hi["risk_flags"]


def test_risk_signals_missing_returns_none(data):
    assert data.get_risk_signals("CUST-NOPE", "ORD-NOPE") is None
