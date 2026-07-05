"""Multi-tenant policy RAG: upload → chunk/embed → semantic search → grounded chat.

Each company's uploaded policy document becomes its own retrieval corpus; sessions bound to a
company are answered FROM that company's paragraphs (top-5 semantic matches), with citations
carried into escalations for the human reviewer. Also covers the credibility-adaptive
evidence bar (good history → smoother; poor history → every claim goes to a human).
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from service import chat_store  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration

ZOMATO = """# Zomato Refund & Replacement Policy

## Spoiled or contaminated food
If a customer receives food that is spoiled, stale, or contains a foreign object (hair, insect,
plastic), they must report it within 2 hours of delivery with a clear photo. On verification,
Zomato issues a full refund to the original payment source within 24 hours.

## Late delivery
Orders delivered more than 20 minutes late earn a coupon worth 20% of the order value,
capped at Rs 150. No cash refunds for delays.
"""

SWIGGY = """# Swiggy Care Guidelines

## Order quality issues
Swiggy offers replacement as the FIRST remedy for any quality complaint. Refunds are issued
only as Swiggy Money (wallet credit), never to source, unless the replacement also fails.
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    # policy/platform stores re-init automatically per DB path
    reset_deps()
    with TestClient(app) as c:
        yield c
    reset_deps()


def _co(client, name, doc, text):
    co = client.post("/api/companies", json={"name": name}).json()
    client.post(f"/api/companies/{co['id']}/policies", json={"doc_name": doc, "text": text})
    return co


def _sess(client, co_id, order="EVO-NONRET-GRO", cust="CUST-NEW1"):
    return client.post("/api/sessions", json={"customer_id": cust, "order_id": order,
                                              "company_id": co_id}).json()


def _turn(client, sid, text="", evidence=None):
    body = {"text": text}
    if evidence:
        body["evidence"] = evidence
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


# ------------------------------------------------------------- store & search
def test_upload_chunks_and_search_ranks_relevant_paragraph_first(client):
    co = _co(client, "Zomato", "refund-policy.md", ZOMATO)
    docs = client.get(f"/api/companies/{co['id']}/policies").json()
    assert docs and docs[0]["chunks"] >= 2
    hits = client.get(f"/api/companies/{co['id']}/search",
                      params={"q": "spoiled food insect refund"}).json()
    assert "spoiled" in hits[0]["text"].lower()          # the right paragraph ranks first
    assert hits[0]["score"] > hits[-1]["score"]


def test_reupload_replaces_document(client):
    co = _co(client, "Zomato", "p.md", ZOMATO)
    client.post(f"/api/companies/{co['id']}/policies",
                json={"doc_name": "p.md", "text": "## Only one rule\nAll sales are final, always."})
    docs = client.get(f"/api/companies/{co['id']}/policies").json()
    assert len(docs) == 1 and docs[0]["chunks"] == 1     # replaced, not appended


def test_session_binding_validates_company(client):
    r = client.post("/api/sessions", json={"customer_id": "CUST-NEW1", "company_id": "co_nope"})
    assert r.status_code == 404


# ------------------------------------------------------------- grounded answers
def test_policy_question_is_answered_from_the_tenant_document(client):
    co = _co(client, "Zomato", "refund-policy.md", ZOMATO)
    s = _sess(client, co["id"])
    r = _turn(client, s["id"], "what is the policy if my food arrives with an insect?")
    txt = r["messages"][0]["text"]
    assert "Zomato" in txt and "foreign object" in txt   # quoted from THEIR document
    meta = r["messages"][0]["meta"]
    assert meta.get("kind") == "policy" and meta.get("citations")
    assert r["status"] == "open"                         # a question never locks or denies


def test_tenant_isolation_same_question_different_grounding(client):
    zo = _co(client, "Zomato", "z.md", ZOMATO)
    sw = _co(client, "Swiggy", "s.md", SWIGGY)
    # one chat per order: bind the two tenants to two different orders
    r1 = _turn(client, _sess(client, zo["id"])["id"], "what is your refund policy for bad food?")
    r2 = _turn(client, _sess(client, sw["id"], order="EVO-DEFECT-COD")["id"],
               "what is your refund policy for bad food?")
    assert "Zomato" in r1["messages"][0]["text"] and "Swiggy" not in r1["messages"][0]["text"]
    assert "Swiggy" in r2["messages"][0]["text"] and "wallet" in r2["messages"][0]["text"].lower()


def test_unbound_session_answers_without_tenant_grounding(client):
    _co(client, "Zomato", "z.md", ZOMATO)                # exists, but session not bound to it
    s = client.post("/api/sessions", json={"customer_id": "CUST-NEW1",
                                           "order_id": "EVO-NONRET-GRO"}).json()
    r = _turn(client, s["id"], "what are my options?")
    assert "Zomato" not in r["messages"][0]["text"]


# ------------------------------------------------------------- citations to the human
def test_escalation_carries_policy_citations_to_review(client):
    co = _co(client, "Zomato", "refund-policy.md", ZOMATO)
    s = _sess(client, co["id"])
    _turn(client, s["id"], "my food arrived spoiled with an insect in it")
    r = _turn(client, s["id"], evidence={"ref": "demo-blurry-1"})     # weak → human
    assert r["status"] == "escalated"
    rev = [x for x in client.get("/api/reviews").json() if x["id"] == s["id"]][0]
    assert rev["policy_company"] == "Zomato"
    assert rev["policy_citations"] and "spoiled" in rev["policy_citations"][0]["text"].lower()


# ------------------------------------------------------------- adaptive scrutiny
def test_high_risk_credibility_never_auto_approves_even_with_clear_evidence(client):
    chat_store.save_credibility({"customer_id": "CUST-NEW1", "score": 0.2,
                                 "genuine_count": 0, "denied_count": 0, "false_count": 3})
    s = client.post("/api/sessions", json={"customer_id": "CUST-NEW1",
                                           "order_id": "EVO-DEFECT-COD"}).json()
    _turn(client, s["id"], "it arrived damaged and won't turn on")
    r = _turn(client, s["id"], evidence={"ref": "demo-clear-1"})      # 0.94 — but bar is unreachable
    assert r["status"] == "escalated"                                  # scrutinized → human


def test_normal_credibility_auto_approves_with_clear_evidence(client):
    s = client.post("/api/sessions", json={"customer_id": "CUST-LOW1",
                                           "order_id": "ORD-FIT-PREPAID"}).json()
    _turn(client, s["id"], "too tight, doesn't fit")
    r = _turn(client, s["id"], evidence={"ref": "demo-clear-1"})
    assert r["phase"] == "confirming"                                  # smooth path for good history
