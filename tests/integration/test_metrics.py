"""Phase 12 checkpoints: /metrics/summary (FR-RPT-1) + operator dashboard (UI-2)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from config.settings import settings  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    with TestClient(app) as c:
        # seed a few resolutions: two auto + one escalation
        c.post("/resolve", json={"request_id": "m1", "issue_text": "The kurti is too tight",
                                 "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1"})
        c.post("/resolve", json={"request_id": "m2", "issue_text": "changed my mind, return it",
                                 "order_id": "ORD-MIND-PREPAID", "customer_id": "CUST-VIP1"})
        c.post("/resolve", json={"request_id": "m3", "issue_text": "return this tablet",
                                 "order_id": "ORD-HIVAL-COD", "customer_id": "CUST-SERIAL"})
        yield c
    settings.AS_OF_DATE = ""
    reset_deps()


def test_metrics_summary_is_sane(client):
    m = client.get("/metrics/summary").json()
    assert m["total_resolutions"] >= 2
    assert m["guardrail_violation_count"] == 0  # MUST be 0 (FR-RPT-1)
    assert 0.0 <= m["auto_resolution_rate"] <= 1.0
    assert 0.0 <= m["escalation_rate"] <= 1.0
    assert m["action_type_distribution"]
    assert m["avg_resolution_latency_ms"] is not None


def test_dashboard_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "KartGuard" in r.text and "Returns operations" in r.text


def test_customer_portal_and_orders_endpoints(client):
    # landing page at / and the customer chat surface (UI-1) at /chat
    assert "KartGuard" in client.get("/").text
    assert "Returns Assistant" in client.get("/chat").text
    custs = client.get("/api/customers").json()
    assert any(c["id"] == "CUST-LOW1" for c in custs)
    orders = client.get("/api/customers/CUST-LOW1/orders").json()
    assert orders and "within_window" in orders[0]
    assert client.get("/api/customers/NOPE/orders").status_code == 404
