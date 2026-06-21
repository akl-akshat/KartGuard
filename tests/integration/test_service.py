"""Phase 8 checkpoints: FastAPI endpoints (§6.2, API-1/2)."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import get_deps, reset_deps  # noqa: E402
from config.settings import settings  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    with TestClient(app) as c:
        yield c
    settings.AS_OF_DATE = ""
    reset_deps()


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["graph"] is True


def test_resolve_is_idempotent(client):
    body = {"request_id": "svc-1", "issue_text": "The kurti is too tight",
            "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1"}
    r1 = client.post("/resolve", json=body).json()
    r2 = client.post("/resolve", json=body).json()
    assert r1["action_type"] == "exchange_with_size_guide"
    assert r1["reply"] and "refund" in r1["reply"].lower()
    assert r2 == r1  # identical, no re-run
    assert len(get_deps().repo.get_audit("svc-1")) == 1


def test_validation_rejects_empty(client):
    assert client.post("/resolve", json={"request_id": "", "issue_text": ""}).status_code == 422


def test_escalation_queue_and_decision_flow(client):
    body = {"request_id": "svc-esc", "issue_text": "return this tablet",
            "order_id": "ORD-HIVAL-COD", "customer_id": "CUST-SERIAL"}
    res = client.post("/resolve", json=body).json()
    assert res["paused"] is True and res["status"] == "awaiting_human"

    queue = client.get("/escalations").json()
    assert any(e["request_id"] == "svc-esc" for e in queue)

    decided = client.post("/escalations/svc-esc/decision",
                          json={"decision": "approve", "reviewer_id": "op9"}).json()
    assert decided["paused"] is False

    # API-2: deciding again (no longer paused) is rejected.
    again = client.post("/escalations/svc-esc/decision", json={"decision": "approve"})
    assert again.status_code == 409


def test_resolution_read_endpoints(client):
    client.post("/resolve", json={"request_id": "svc-2", "issue_text": "changed my mind, return it",
                                  "order_id": "ORD-MIND-PREPAID", "customer_id": "CUST-VIP1"})
    detail = client.get("/resolutions/svc-2")
    assert detail.status_code == 200 and detail.json()["rationale"]
    assert client.get("/resolutions/nope").status_code == 404
    assert any(r["request_id"] == "svc-2" for r in client.get("/resolutions").json())


def test_resolve_stream_emits_node_events(client):
    body = {"request_id": "svc-stream", "issue_text": "The kurti is too tight",
            "order_id": "ORD-FIT-PREPAID", "customer_id": "CUST-LOW1"}
    text = client.post("/resolve/stream", json=body).text
    assert "event: node" in text and "triage" in text
    assert "event: done" in text
