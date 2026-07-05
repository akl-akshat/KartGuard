"""Trust layer: credential chain, admin onboarding, dedupe, CSAT ratings, damage caps.

The full Myntra lifecycle in tests: admin registers the company (policy + client credential in
one call) → the client logs in with issued credentials and creates an employee (credential
issued once) → the employee logs in → a customer order flows through chat grounded in
MYNTRA's document → post-resolution the chat is tracking-only → duplicate chats are refused →
the customer's anonymous rating is credibility-weighted → and no single company can take more
than the quarterly cap off a customer's credit score.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from service import chat_store, demo_seed, rating_store  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration

MYNTRA_POLICY = """# Myntra Fashion Returns Policy

## Size and fit
For any size or fit issue on apparel or footwear, Myntra provides a free size exchange as the
first remedy, verified by a photo. Refunds for size issues are store-credit only.

## Damaged or wrong items
Damaged, defective or wrong items are eligible for a full refund to source or a replacement,
verified with a clear photo, within 7 days of delivery.
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    reset_deps()
    with TestClient(app) as c:
        demo_seed.ensure_platform_demo()
        yield c
    reset_deps()


def _login(client, role, id_, password=""):
    return client.post("/api/auth/login", json={"role": role, "id": id_, "password": password})


def _turn(client, sid, text="", ev=None):
    body = {"text": text}
    if ev:
        body["evidence"] = ev
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


def _register_myntra(client):
    assert _login(client, "admin", "admin", "admin123").status_code == 200
    return client.post("/api/admin/companies",
                       json={"name": "Myntra", "doc_name": "myntra-returns.md",
                             "policy_text": MYNTRA_POLICY}).json()


# ------------------------------------------------------------- credential chain
def test_password_logins_and_provisioning_chain(client):
    assert _login(client, "admin", "admin", "wrong").status_code == 401
    reg = _register_myntra(client)
    cred = reg["credentials"]
    assert reg["policy"]["chunks"] >= 2 and cred["password"]
    # unauthenticated registration is refused
    fresh = TestClient(app)
    assert fresh.post("/api/admin/companies",
                      json={"name": "Evil", "policy_text": "x" * 50}).status_code == 401
    # client logs in with issued creds and provisions an employee
    assert _login(client, "client", cred["login_id"], cred["password"]).status_code == 200
    rep = client.post(f"/api/companies/{reg['company']['id']}/reps",
                      json={"name": "Kiara Malhotra"}).json()
    assert rep["credentials"]["password"]
    assert _login(client, "rep", rep["credentials"]["login_id"],
                  rep["credentials"]["password"]).json()["redirect"] == "/rep"


def test_seeded_demo_credentials_work(client):
    assert _login(client, "client", "swiggy", "swiggy123").status_code == 200
    assert _login(client, "rep", "arjun", "rep123").status_code == 200
    assert _login(client, "client", "swiggy", "nope").status_code == 401


# ------------------------------------------------------------- new-client lifecycle
def _myntra_order_session(client):
    reg = _register_myntra(client)
    o = client.post(f"/api/companies/{reg['company']['id']}/orders",
                    json={"phone": "9650440034", "customer_name": "Akshat Lakhera",
                          "title": "Denim Jacket", "category": "apparel", "price": 1999}).json()
    me = next(u for u in client.get("/api/platform/users").json() if u["phone"] == "9650440034")
    s = client.post("/api/sessions", json={"customer_id": me["id"], "order_id": o["id"]}).json()
    return reg, o, me, s


def test_new_company_resolves_per_its_own_policy(client):
    reg, o, me, s = _myntra_order_session(client)
    assert s["company_id"] == reg["company"]["id"]
    q = _turn(client, s["id"], "what is your policy for size issues?")
    assert "Myntra" in q["messages"][0]["text"] and "store-credit" in q["messages"][0]["text"]
    _turn(client, s["id"], "the jacket is too tight, doesn't fit")
    r = _turn(client, s["id"], ev={"ref": "demo-clear-1"})
    acts = [m["meta"].get("action_type") for m in r["messages"] if m.get("meta", {}).get("action_type")]
    assert "exchange_with_size_guide" in acts
    assert _turn(client, s["id"], "yes go ahead")["status"] == "resolved"


def test_locked_chat_is_tracking_and_human_only(client):
    reg, o, me, s = _myntra_order_session(client)
    _turn(client, s["id"], "the jacket is too tight")
    _turn(client, s["id"], ev={"ref": "demo-clear-1"})
    _turn(client, s["id"], "yes go ahead")
    r = _turn(client, s["id"], "any update on my exchange?")
    assert "is confirmed" in r["messages"][0]["text"]           # tracking answer
    r = _turn(client, s["id"], "i want a refund now too")
    assert "resolution" not in [m["meta"].get("kind") for m in r["messages"] if m.get("meta")]
    r = _turn(client, s["id"], "talk to a human please")
    assert "specialist" in r["messages"][0]["text"].lower()


def test_one_chat_per_order(client):
    reg, o, me, s = _myntra_order_session(client)
    dup = client.post("/api/sessions", json={"customer_id": me["id"], "order_id": o["id"]})
    assert dup.status_code == 409
    assert dup.json()["detail"]["session_id"] == s["id"]


# ------------------------------------------------------------- ratings + caps
def test_rating_is_credibility_weighted(client):
    reg, o, me, s = _myntra_order_session(client)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], ev={"ref": "demo-clear-1"})
    _turn(client, s["id"], "yes")
    r = client.post(f"/api/sessions/{s['id']}/rate", json={"stars": 5}).json()
    assert r["company_rating"]["rating"] == 5.0
    assert client.post(f"/api/sessions/{s['id']}/rate", json={"stars": 1}).status_code == 409
    # a 0.05-credibility troll's 1★ barely dents the weighted rating
    chat_store.save_credibility({"customer_id": "PU-TROLL", "score": 0.05,
                                 "genuine_count": 0, "denied_count": 0, "false_count": 3})
    rating_store.rate_session("sess_troll", "PU-TROLL", reg["company"]["id"], 1)
    assert rating_store.company_rating(reg["company"]["id"])["rating"] >= 4.5


def test_company_quarterly_damage_cap_and_admin_override(client):
    reg, o, me, s = _myntra_order_session(client)
    cid = reg["company"]["id"]
    out1 = rating_store.apply_outcome_capped(me["id"], "false_claim", company_id=cid,
                                             reason="t", actor="review:x")
    out2 = rating_store.apply_outcome_capped(me["id"], "false_claim", company_id=cid,
                                             reason="t", actor="review:x")
    assert abs(out1["applied"]) + abs(out2["applied"]) <= rating_store.QUARTERLY_COMPANY_CAP + 1e-9
    assert out2["capped"] is True
    # positive outcomes pass through uncapped
    up = rating_store.apply_outcome_capped(me["id"], "genuine", company_id=cid,
                                           reason="ok", actor="review:x")
    assert up["applied"] > 0
    # admin manual override (must be admin-authenticated)
    _login(client, "admin", "admin", "admin123")
    r = client.post(f"/api/admin/customers/{me['id']}/score", json={"score": 0.8})
    assert r.status_code == 200
    assert chat_store.get_credibility(me["id"])["score"] == 0.8


def test_client_records_ledger(client):
    reg, o, me, s = _myntra_order_session(client)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], ev={"ref": "demo-clear-1"})
    _turn(client, s["id"], "yes")
    client.post(f"/api/sessions/{s['id']}/rate", json={"stars": 4})
    recs = client.get(f"/api/companies/{reg['company']['id']}/records").json()
    row = next(x for x in recs if x["session_id"] == s["id"])
    assert row["action_type"] == "exchange_with_size_guide" and row["rated"] is True
