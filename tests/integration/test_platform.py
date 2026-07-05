"""Central-platform layer: universal phone identity, client order DBs, cross-client
credibility, refund-status awareness, and the client-portal HITL loop.

Locks in the platform guarantees:
* one phone number = one customer across every client brand (orders + credibility follow it);
* "where is my refund" answers from the customer's ACTUAL history (none / with-specialist /
  processed) — never boilerplate;
* clients manage their own (mock) order DB and see only THEIR review queue;
* a client-human's written reply lands in the same customer chat the case came from;
* fraud confirmed at one brand tightens the gates at every other brand.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from service import chat_store, demo_seed  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration

PHONE = "9650440034"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    # policy/platform stores re-init automatically per DB path
    reset_deps()
    with TestClient(app) as c:
        demo_seed.ensure_platform_demo()
        yield c
    reset_deps()


def _user(client, phone=PHONE):
    return next(u for u in client.get("/api/platform/users").json() if u["phone"] == phone)


def _orders(client, uid):
    return client.get(f"/api/platform/users/{uid}/orders").json()


def _by_brand(orders, brand):
    return next(o for o in orders if o["brand"] == brand)


def _sess(client, uid, order_id):
    return client.post("/api/sessions", json={"customer_id": uid, "order_id": order_id}).json()


def _turn(client, sid, text="", evidence=None):
    body = {"text": text}
    if evidence:
        body["evidence"] = evidence
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


def _kinds(msgs):
    return [m["meta"].get("kind") for m in msgs if m.get("meta")]


# ------------------------------------------------------- universal identity (E3)
def test_one_phone_maps_to_orders_across_all_brands(client):
    u = _user(client)
    orders = _orders(client, u["id"])
    assert len(orders) == 5 and len({o["brand"] for o in orders}) == 5


def test_session_on_client_order_auto_binds_that_clients_policy(client):
    u = _user(client)
    amazon = _by_brand(_orders(client, u["id"]), "Amazon")
    s = _sess(client, u["id"], amazon["id"])
    assert s["company_id"] == amazon["company_id"]


def test_order_ownership_enforced(client):
    u2 = _user(client, "9812345678")
    other = _by_brand(_orders(client, _user(client)["id"]), "Amazon")  # belongs to PHONE
    r = client.post("/api/sessions", json={"customer_id": u2["id"], "order_id": other["id"]})
    assert r.status_code == 403


# ------------------------------------------------------- refund-status truth (E1)
def test_refund_question_with_no_history_says_none_due_and_asks_for_issue(client):
    u = _user(client)
    s = _sess(client, u["id"], _by_brand(_orders(client, u["id"]), "Amazon")["id"])
    r = _turn(client, s["id"], "where is my refund")
    txt = r["messages"][0]["text"]
    assert "no refund due or in process" in txt and "Tell me what went wrong" in txt


def test_refund_question_after_resolution_reports_it(client):
    u = _user(client)
    zo = _by_brand(_orders(client, u["id"]), "Zomato (demo)")
    s = _sess(client, u["id"], zo["id"])
    _turn(client, s["id"], "the paneer was rotten and had an insect in it")
    _turn(client, s["id"], evidence={"ref": "demo-clear-1"})
    done = _turn(client, s["id"], "yes go ahead")
    assert done["status"] == "resolved"
    # one chat per order: tracking happens in the SAME (now locked) conversation
    r = _turn(client, s["id"], "where is my refund")
    assert "is confirmed" in r["messages"][0]["text"]
    # and a duplicate chat for the same order is refused with a pointer to this one
    dup = client.post("/api/sessions", json={"customer_id": u["id"], "order_id": zo["id"]})
    assert dup.status_code == 409 and dup.json()["detail"]["session_id"] == s["id"]


def test_refund_question_while_escalated_says_with_specialists(client):
    u = _user(client)
    sw = _by_brand(_orders(client, u["id"]), "Swiggy")
    s = _sess(client, u["id"], sw["id"])
    _turn(client, s["id"], "the sushi arrived spoiled")
    _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})       # -> escalated
    r = _turn(client, s["id"], "where is my refund?")
    assert "specialist team" in r["messages"][0]["text"]


# ------------------------------------------------------- money rules on client orders
def test_high_value_client_order_defect_goes_to_human_with_vendor_notify(client):
    u = _user(client)
    amazon = _by_brand(_orders(client, u["id"]), "Amazon")          # ₹3,499 keyboard
    s = _sess(client, u["id"], amazon["id"])
    _turn(client, s["id"], "the keyboard arrived damaged, keys are broken")
    r = _turn(client, s["id"], evidence={"ref": "demo-clear-1"})
    assert r["status"] == "escalated"
    full = client.get(f"/api/sessions/{s['id']}").json()
    assert full["state"].get("vendor_notify") is True


# ------------------------------------------------------- client portal (E2, E5, E6)
def test_client_scoped_review_queue(client):
    u = _user(client)
    sw = _by_brand(_orders(client, u["id"]), "Swiggy")
    s = _sess(client, u["id"], sw["id"])
    _turn(client, s["id"], "sushi was stale and foul")
    _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})
    cos = {c["name"]: c["id"] for c in client.get("/api/companies").json()}
    swiggy_q = client.get("/api/reviews", params={"company_id": cos["Swiggy"]}).json()
    amazon_q = client.get("/api/reviews", params={"company_id": cos["Amazon"]}).json()
    assert any(x["id"] == s["id"] for x in swiggy_q)
    assert not any(x["id"] == s["id"] for x in amazon_q)


def test_client_human_reply_lands_in_the_same_chat(client):
    u = _user(client)
    sw = _by_brand(_orders(client, u["id"]), "Swiggy")
    s = _sess(client, u["id"], sw["id"])
    _turn(client, s["id"], "sushi arrived spoiled")
    _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})
    ok = client.post(f"/api/sessions/{s['id']}/reply",
                     json={"reviewer_id": "neha", "text": "Checking with the kitchen — update shortly."})
    assert ok.status_code == 200
    msgs = client.get(f"/api/sessions/{s['id']}").json()["messages"]
    assert any(m["meta"].get("kind") == "human_reply" and "kitchen" in m["text"] for m in msgs)
    # reply is only for open specialist cases
    r2 = client.post(f"/api/sessions/{s['id']}/review",
                     json={"decision": "deny", "reviewer_id": "neha"})
    assert r2.status_code == 200
    assert client.post(f"/api/sessions/{s['id']}/reply",
                       json={"reviewer_id": "neha", "text": "late"}).status_code == 409


def test_client_can_add_and_remove_orders(client):
    cos = {c["name"]: c["id"] for c in client.get("/api/companies").json()}
    o = client.post(f"/api/companies/{cos['Blinkit']}/orders",
                    json={"phone": "9812345678", "customer_name": "Priya Nair",
                          "title": "Milk & Eggs", "category": "grocery", "price": 249}).json()
    assert o["id"].startswith("PORD-")
    u2 = _user(client, "9812345678")
    assert any(x["id"] == o["id"] for x in _orders(client, u2["id"]))
    assert client.delete(f"/api/orders/{o['id']}").json()["ok"] is True
    assert not any(x["id"] == o["id"] for x in _orders(client, u2["id"]))


# ------------------------------------------------------- cross-client credibility (E4)
def test_fraud_at_two_brands_blocks_auto_approval_at_a_third(client):
    u = _user(client)
    orders = _orders(client, u["id"])
    for brand in ("Swiggy", "Blinkit"):
        o = _by_brand(orders, brand)
        s = _sess(client, u["id"], o["id"])
        _turn(client, s["id"], "it arrived spoiled and rotten")
        _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})
        client.post(f"/api/sessions/{s['id']}/review",
                    json={"decision": "deny", "reviewer_id": "op", "fraud": True})
    assert chat_store.get_credibility(u["id"])["score"] <= 0.2   # platform-wide, phone-keyed
    fl = _by_brand(orders, "Flipkart")
    s = _sess(client, u["id"], fl["id"])
    _turn(client, s["id"], "the shoes arrived damaged, sole broken")
    r = _turn(client, s["id"], evidence={"ref": "demo-clear-9"})  # crystal-clear evidence
    assert r["status"] == "escalated"                             # still no auto payout anywhere
    assert "resolution" not in _kinds(r["messages"])


def test_refunds_summary_aggregates_per_brand(client):
    u = _user(client)
    zo = _by_brand(_orders(client, u["id"]), "Zomato (demo)")
    s = _sess(client, u["id"], zo["id"])
    _turn(client, s["id"], "paneer was rotten with an insect")
    _turn(client, s["id"], evidence={"ref": "demo-clear-2"})
    _turn(client, s["id"], "yes")
    summ = client.get(f"/api/platform/users/{u['id']}/refunds-summary").json()
    assert summ["count"] >= 1
    assert any(b["brand"].startswith("Zomato") and b["amount"] > 0 for b in summ["brands"])
