"""Wallet economy + representatives + RBAC routing.

Money invariants the platform's revenue model depends on:
* an approved refund credits the customer's wallet exactly once (idempotent by request);
* withdrawals are KYC-gated the first time and can never overdraw;
* converting balance to a brand coupon deducts immediately; scratch only reveals;
* games are daily-gated and lottery stakes actually debit;
* representatives see only their assigned complaints; role logins land on role dashboards.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from service import chat_store, demo_seed, wallet_store  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration

PHONE = "9650440034"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    reset_deps()
    with TestClient(app) as c:
        demo_seed.ensure_platform_demo()
        yield c
    reset_deps()


def _me(client):
    return next(u for u in client.get("/api/platform/users").json() if u["phone"] == PHONE)


def _order(client, uid, brand):
    return next(o for o in client.get(f"/api/platform/users/{uid}/orders").json()
                if o["brand"] == brand)


def _turn(client, sid, text="", evidence=None):
    body = {"text": text}
    if evidence:
        body["evidence"] = evidence
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


def _resolve_refund(client, uid):
    """Run the verified spoiled-food flow to an auto refund (₹349, Zomato)."""
    zo = _order(client, uid, "Zomato (demo)")
    s = client.post("/api/sessions", json={"customer_id": uid, "order_id": zo["id"]}).json()
    _turn(client, s["id"], "the paneer was rotten with an insect in it")
    _turn(client, s["id"], evidence={"ref": "demo-clear-1"})
    out = _turn(client, s["id"], "yes go ahead")
    assert out["status"] == "resolved"
    return s


# ----------------------------------------------------------- refund -> wallet
def test_approved_refund_credits_wallet_once(client):
    u = _me(client)
    _resolve_refund(client, u["id"])
    w = client.get(f"/api/wallet/{u['id']}").json()
    assert abs(w["balance"] - 349) < 1
    refunds = [t for t in w["transactions"] if t["kind"] == "refund"]
    assert len(refunds) == 1 and refunds[0]["brand"].startswith("Zomato")


def test_wallet_credit_is_idempotent_by_ref(client):
    u = _me(client)
    wallet_store.credit(u["id"], 100, "refund", ref="dup-1")
    wallet_store.credit(u["id"], 100, "refund", ref="dup-1")     # replay must no-op
    assert abs(client.get(f"/api/wallet/{u['id']}").json()["balance"] - 100) < 0.01


# ----------------------------------------------------------- withdrawal + KYC
def test_withdraw_requires_kyc_then_works_and_never_overdraws(client):
    u = _me(client)
    _resolve_refund(client, u["id"])
    assert client.post(f"/api/wallet/{u['id']}/withdraw", json={"amount": 100}).status_code == 412
    client.post(f"/api/wallet/{u['id']}/kyc")
    r = client.post(f"/api/wallet/{u['id']}/withdraw", json={"amount": 100})
    assert r.status_code == 200 and abs(r.json()["wallet"]["balance"] - 249) < 1
    assert client.post(f"/api/wallet/{u['id']}/withdraw", json={"amount": 99999}).status_code == 400


# ----------------------------------------------------------- coupons
def test_coupon_deducts_now_and_scratch_reveals(client):
    u = _me(client)
    _resolve_refund(client, u["id"])
    out = client.post(f"/api/wallet/{u['id']}/coupon",
                      json={"brand": "Amazon", "amount": 200}).json()
    w = client.get(f"/api/wallet/{u['id']}").json()
    assert abs(w["balance"] - 149) < 1                       # money left on creation
    assert w["coupons"][0]["masked"] is True
    rv = client.post(f"/api/wallet/{u['id']}/coupon/{out['code']}/reveal").json()
    assert rv["code"] == out["code"] and rv["brand"] == "Amazon"
    assert client.get(f"/api/wallet/{u['id']}").json()["coupons"][0]["masked"] is False


def test_coupon_cannot_exceed_balance(client):
    u = _me(client)
    assert client.post(f"/api/wallet/{u['id']}/coupon",
                       json={"brand": "Amazon", "amount": 5000}).status_code == 400


# ----------------------------------------------------------- games
def test_daily_and_spin_are_once_per_day_and_lottery_debits(client):
    u = _me(client)
    _resolve_refund(client, u["id"])
    assert client.post(f"/api/wallet/{u['id']}/daily").json()["ok"] is True
    assert client.post(f"/api/wallet/{u['id']}/daily").json()["ok"] is False
    assert client.post(f"/api/wallet/{u['id']}/spin").json()["ok"] is True
    assert client.post(f"/api/wallet/{u['id']}/spin").json()["ok"] is False
    b4 = client.get(f"/api/wallet/{u['id']}").json()["balance"]
    out = client.post(f"/api/wallet/{u['id']}/lottery", json={"lottery": "dinner"}).json()
    af = client.get(f"/api/wallet/{u['id']}").json()["balance"]
    assert out["ok"] and abs((b4 - af) - 1.0) < 0.01         # stake taken exactly


# ----------------------------------------------------------- reps + assignment
def test_assignment_scopes_the_rep_queue(client):
    u = _me(client)
    cos = {c["name"]: c["id"] for c in client.get("/api/companies").json()}
    reps = client.get(f"/api/companies/{cos['Swiggy']}/reps").json()
    sw = _order(client, u["id"], "Swiggy")
    s = client.post("/api/sessions", json={"customer_id": u["id"], "order_id": sw["id"]}).json()
    _turn(client, s["id"], "sushi arrived spoiled")
    _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})     # escalates
    assert client.post(f"/api/sessions/{s['id']}/assign",
                       json={"rep_id": reps[0]["id"]}).status_code == 200
    q0 = client.get(f"/api/reps/{reps[0]['id']}/queue").json()
    q1 = client.get(f"/api/reps/{reps[1]['id']}/queue").json()
    assert any(x["id"] == s["id"] for x in q0) and not q1
    # a rep from another company can't be assigned
    other = client.get(f"/api/companies/{cos['Amazon']}/reps").json()[0]
    assert client.post(f"/api/sessions/{s['id']}/assign",
                       json={"rep_id": other["id"]}).status_code == 404


# ----------------------------------------------------------- RBAC routing
def test_role_logins_route_to_role_dashboards(client):
    u = _me(client)
    P = lambda b: client.post("/api/auth/login", json=b)  # noqa: E731
    # customers use the demo phone identity; staff roles authenticate with issued credentials
    assert P({"role": "customer", "id": u["id"]}).json()["redirect"] == "/app"
    assert P({"role": "client", "id": "swiggy", "password": "swiggy123"}).json()["redirect"] == "/client"
    assert P({"role": "rep", "id": "arjun", "password": "rep123"}).json()["redirect"] == "/rep"
    assert P({"role": "admin", "id": "admin", "password": "admin123"}).json()["redirect"] == "/admin"
    assert P({"role": "rep", "id": "REP-NOPE", "password": "x"}).status_code == 401
    assert P({"role": "client", "id": "swiggy", "password": "wrong"}).status_code == 401


def test_role_pages_are_served(client):
    assert "KartGuard" in client.get("/").text                  # login
    assert "My Dashboard" in client.get("/app").text
    assert "Client Console" in client.get("/client").text
    assert "My Complaints" in client.get("/rep").text
