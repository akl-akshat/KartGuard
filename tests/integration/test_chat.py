"""Fraud-aware chat: turn-based dialogue, evidence gate, credibility, one-resolution lock.

These lock in the behaviour the chat was rebuilt to guarantee — and the fixes that followed the
adversarial red-team:

* refund is never the default, and rejecting a remedy never buys a bigger one (no ladder);
* a claim must be valid for the product category and be proven by SERVER-assessed evidence
  (the customer cannot self-certify it) before money moves; a pivoted claim is re-verified;
* full refunds are rare and human-gated; consequential/high-value defects go to a human;
* a session can only ever produce ONE resolution (no double refunds);
* credibility is persisted and moves ONLY on a human's adjudication of an assessed claim —
  never for merely asking, declining, or being routed to a human procedurally.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent import conversation, credibility  # noqa: E402
from agent.deps import reset_deps  # noqa: E402
from service import chat_store  # noqa: E402
from service.app import app  # noqa: E402

pytestmark = pytest.mark.integration

# concrete seeded orders (see db/dataset.py)
APPAREL = "ORD-FIT-PREPAID"        # CUST-LOW1 apparel 1299 (in window)
EARBUDS = "ORD-DEFECT-ELEC"        # CUST-LOW1 electronics 1899
DEFECT_ELEC = "EVO-DEFECT-COD"     # CUST-NEW1 electronics 1799 (defect, low value)
GROCERY = "EVO-NONRET-GRO"         # CUST-NEW1 grocery 450 (perishable)
HIVAL_ELEC = "EVO-HIVAL-PRE"       # CUST-VIP1 electronics 4999 (high value)
REMORSE = "EVO-MIND-COD"           # CUST-VIP1 apparel 899 (in window)

# The customer supplies only an opaque evidence ref; the server assessor decides the verdict.
STRONG = {"ref": "demo-clear-1"}
WEAK = {"ref": "demo-blurry-1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    reset_deps()
    with TestClient(app) as c:
        yield c
    reset_deps()


def _new(client, customer, order=None):
    body = {"customer_id": customer}
    if order:
        body["order_id"] = order
    return client.post("/api/sessions", json=body).json()


def _turn(client, sid, text="", evidence=None):
    body = {"text": text}
    if evidence:
        body["evidence"] = evidence
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


def _kinds(msgs):
    return [m["meta"].get("kind") for m in msgs if m.get("meta")]


def _proposal(msgs):
    return next((m["meta"] for m in msgs if m.get("meta", {}).get("kind") == "proposal"), None)


def _cred(cid):
    return (chat_store.get_credibility(cid) or {}).get("score", credibility.DEFAULT_SCORE)


# ----------------------------------------------------------- validity
def test_nonsensical_size_claim_on_electronics_is_redirected(client):
    s = _new(client, "CUST-LOW1", EARBUDS)
    r = _turn(client, s["id"], "the size is too small, it doesn't fit me")
    assert r["phase"] == "open" and r["status"] == "open"
    assert "proposal" not in _kinds(r["messages"])
    assert "doesn't apply" in r["messages"][0]["text"] or "don't quite fit" in r["messages"][0]["text"].lower()


def test_no_size_exchange_offered_on_non_wearable(client):
    # a quality complaint on electronics must never surface a "size exchange" remedy
    s = _new(client, "CUST-LOW1", EARBUDS)
    _turn(client, s["id"], "these are poor quality, not as described")
    r = _turn(client, s["id"], evidence=STRONG)
    prop = _proposal(r["messages"])
    if prop:
        assert prop["action_type"] != "exchange_with_size_guide"


# ----------------------------------------------------------- evidence gate
def test_size_issue_requires_evidence_before_any_offer(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    r = _turn(client, s["id"], "the kurti is too tight, it doesn't fit")
    assert r["phase"] == "awaiting_evidence"
    assert "proposal" not in _kinds(r["messages"])


def test_strong_evidence_proposes_exchange_not_refund_then_executes(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight, doesn't fit")
    r = _turn(client, s["id"], evidence=STRONG)
    prop = _proposal(r["messages"])
    assert prop and prop["action_type"] == "exchange_with_size_guide"
    done = _turn(client, s["id"], "yes go ahead")
    assert done["phase"] == "resolved" and done["status"] == "resolved"
    assert "resolution" in _kinds(done["messages"])


def test_weak_evidence_routes_to_human_no_payout(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")
    r = _turn(client, s["id"], evidence=WEAK)
    assert r["phase"] == "escalated" and r["status"] == "escalated"
    assert "resolution" not in _kinds(r["messages"])


def test_customer_cannot_self_certify_evidence(client):
    # a client-supplied 'hint' is ignored; only the server-assessed ref (blurry) counts
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")
    r = _turn(client, s["id"], evidence={"ref": "demo-blurry-9", "hint": "strong"})
    assert r["status"] == "escalated"           # blurry -> human, despite the fake "strong" hint
    assert "resolution" not in _kinds(r["messages"])


def test_pivoting_the_claim_reasks_for_evidence(client):
    # evidence assessed for a quality claim must NOT wave a later DEFECT claim through the gate
    s = _new(client, "CUST-LOW1", EARBUDS)                   # electronics
    _turn(client, s["id"], "these are poor quality, not as described")   # quality_complaint
    _turn(client, s["id"], evidence=STRONG)                  # supports the QUALITY claim -> proposal
    r = _turn(client, s["id"], "actually they are also cracked and broken")  # pivots to defect_damage
    assert r["phase"] == "awaiting_evidence"                 # new claim -> fresh evidence required
    assert "proposal" not in _kinds(r["messages"])
    assert "resolution" not in _kinds(r["messages"])


# ----------------------------------------------------------- no ladder, refund rarity
def test_rejection_never_ladders_to_a_bigger_remedy(client):
    s = _new(client, "CUST-NEW1", DEFECT_ELEC)
    _turn(client, s["id"], "it arrived damaged and won't turn on")
    r1 = _turn(client, s["id"], evidence=STRONG)
    assert _proposal(r1["messages"])["action_type"] == "expedited_replacement"
    r2 = _turn(client, s["id"], "no")
    assert r2["phase"] == "confirming"
    assert _proposal(r2["messages"])["action_type"] == "expedited_replacement"
    r3 = _turn(client, s["id"], "no")
    assert r3["status"] == "escalated"
    full = client.get(f"/api/sessions/{s['id']}").json()
    types = [m["meta"].get("action_type") for m in full["messages"] if m.get("meta", {}).get("action_type")]
    assert "instant_refund" not in types and "partial_refund" not in types
    assert "resolution" not in _kinds(full["messages"])


def test_changed_mind_deflects_and_refund_insistence_goes_to_human(client):
    s = _new(client, "CUST-VIP1", REMORSE)
    r = _turn(client, s["id"], "I changed my mind, I don't want it")
    prop = _proposal(r["messages"])
    assert prop and prop["action_type"] == "retention_coupon"
    _turn(client, s["id"], "no I want a full refund")
    r3 = _turn(client, s["id"], "no, refund please")
    assert r3["status"] == "escalated"
    full = client.get(f"/api/sessions/{s['id']}").json()
    assert "resolution" not in _kinds(full["messages"])


def test_ambiguous_message_does_not_false_confirm(client):
    # a complaint word containing 'ok' (br-ok-en) must NOT be read as a confirmation
    assert conversation.detect_intent("this is broken", "confirming") not in ("confirm", "reject")
    assert conversation.detect_intent("the box looks smokey", "confirming") != "confirm"
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], evidence=STRONG)                  # -> confirming (exchange)
    r = _turn(client, s["id"], "this is broken")             # must NOT execute the exchange
    assert r["status"] != "resolved"
    assert "resolution" not in _kinds(r["messages"])


# ----------------------------------------------------------- conditional remedies
def test_perishable_defect_with_evidence_gets_a_refund(client):
    s = _new(client, "CUST-NEW1", GROCERY)
    _turn(client, s["id"], "the food arrived spoiled and there was an insect")
    r = _turn(client, s["id"], evidence=STRONG)
    prop = _proposal(r["messages"])
    assert prop and prop["action_type"] == "instant_refund"
    done = _turn(client, s["id"], "yes")
    assert done["status"] == "resolved" and "resolution" in _kinds(done["messages"])


def test_high_value_defect_routes_to_human_with_vendor_notify(client):
    s = _new(client, "CUST-VIP1", HIVAL_ELEC)
    _turn(client, s["id"], "the screen arrived cracked and it won't switch on")
    r = _turn(client, s["id"], evidence=STRONG)
    assert r["status"] == "escalated"
    full = client.get(f"/api/sessions/{s['id']}").json()
    assert full["state"].get("vendor_notify") is True
    assert "resolution" not in _kinds(full["messages"])


def test_high_risk_customer_refund_escalates_without_payout(client):
    orders = client.get("/api/customers/CUST-SERIAL/orders").json()
    s = _new(client, "CUST-SERIAL", orders[0]["id"])
    r = _turn(client, s["id"], "I changed my mind, I want a full refund")
    assert r["status"] == "escalated"
    assert "resolution" not in _kinds(r["messages"])


# ----------------------------------------------------------- one resolution per session
def test_one_resolution_per_session_no_double_refund(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], evidence=STRONG)
    _turn(client, s["id"], "yes")
    r = _turn(client, s["id"], "now I also want a full refund")
    assert r["status"] == "resolved"
    assert "proposal" not in _kinds(r["messages"]) and "resolution" not in _kinds(r["messages"])


# ----------------------------------------------------------- credibility (human-adjudicated only)
def test_human_deny_of_assessed_claim_lowers_credibility_and_persists(client):
    s = _new(client, "CUST-NEW1", APPAREL)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], evidence=WEAK)                    # assessed claim -> escalated
    before = _cred("CUST-NEW1")
    out = client.post(f"/api/sessions/{s['id']}/review",
                      json={"decision": "deny", "reviewer_id": "op-7"}).json()
    assert out["status"] == "denied"
    after = chat_store.get_credibility("CUST-NEW1")["score"]
    assert after < before
    assert chat_store.get_credibility("CUST-NEW1")["score"] == after       # persisted


def test_operator_fraud_flag_applies_the_harsher_penalty(client):
    s = _new(client, "CUST-NEW1", APPAREL)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], evidence=WEAK)
    client.post(f"/api/sessions/{s['id']}/review",
                json={"decision": "deny", "reviewer_id": "op-7", "fraud": True})
    # false_claim (-0.30) is harsher than a plain denial (-0.15)
    assert chat_store.get_credibility("CUST-NEW1")["score"] <= credibility.DEFAULT_SCORE - 0.29


def test_procedural_escalation_deny_does_not_penalise_credibility(client):
    # customer merely asked for a human — a deny must NOT lower their credibility
    s = _new(client, "CUST-NEW1", APPAREL)
    _turn(client, s["id"], "can I talk to a human please")
    before = _cred("CUST-NEW1")
    client.post(f"/api/sessions/{s['id']}/review", json={"decision": "deny", "reviewer_id": "op-7"})
    assert chat_store.get_credibility("CUST-NEW1") is None or \
        chat_store.get_credibility("CUST-NEW1")["score"] == before


def test_approve_without_an_assessed_claim_does_not_farm_credibility(client):
    s = _new(client, "CUST-NEW1", GROCERY)
    _turn(client, s["id"], "can I speak to a human")          # no claim, no evidence
    client.post(f"/api/sessions/{s['id']}/review", json={"decision": "approve", "reviewer_id": "op-7"})
    assert chat_store.get_credibility("CUST-NEW1") is None or \
        chat_store.get_credibility("CUST-NEW1")["score"] == credibility.DEFAULT_SCORE


def test_human_approve_of_supported_claim_marks_genuine(client):
    s = _new(client, "CUST-VIP1", HIVAL_ELEC)
    _turn(client, s["id"], "the screen is cracked, it's damaged")
    _turn(client, s["id"], evidence=STRONG)                  # supported -> escalated (high value)
    out = client.post(f"/api/sessions/{s['id']}/review",
                      json={"decision": "approve", "reviewer_id": "op-7"}).json()
    assert out["status"] == "resolved"
    assert chat_store.get_credibility("CUST-VIP1")["score"] >= credibility.DEFAULT_SCORE


def test_auto_resolution_does_not_change_credibility(client):
    # an auto-resolved, assessor-accepted claim must not farm credibility upward
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")
    _turn(client, s["id"], evidence=STRONG)
    _turn(client, s["id"], "yes")                            # resolved automatically
    assert chat_store.get_credibility("CUST-LOW1") is None or \
        chat_store.get_credibility("CUST-LOW1")["score"] == credibility.DEFAULT_SCORE


def test_review_rejects_non_escalated_session(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    assert client.post(f"/api/sessions/{s['id']}/review",
                       json={"decision": "approve", "reviewer_id": "op-1"}).status_code == 409


# ----------------------------------------------------------- evidence meta-questions
def test_how_do_i_photograph_a_smell_gets_guidance_not_policy_dump(client):
    """Live-reported transcript: 'how can i attach image of smelling food??' used to dredge up an
    irrelevant 'Change of mind' policy quote plus a stale 'tell me what went wrong' re-ask."""
    s = _new(client, "CUST-NEW1", GROCERY)
    _turn(client, s["id"], "its spoilt is smelling")                    # -> awaiting_evidence
    r = _turn(client, s["id"], "how can i attach image of smelling food??")
    msg = r["messages"][0]["text"]
    assert "can't be photographed" in msg or "curdling" in msg          # practical guidance
    assert "Change of mind" not in msg and "change of mind" not in msg  # no irrelevant policy
    assert "Tell me exactly what went wrong" not in msg                 # no context amnesia
    assert "talk to a human" in msg                                     # honest escape hatch
    assert r["phase"] == "awaiting_evidence"                            # gate still holds
    # ...and the verified claim still resolves with wallet-truthful copy
    r = _turn(client, s["id"], evidence=STRONG)
    assert any("KartGuard wallet" in m["text"] for m in r["messages"])
    assert not any("original payment method" in m["text"] for m in r["messages"])


def test_generic_question_mid_evidence_keeps_short_reminder(client):
    s = _new(client, "CUST-NEW1", GROCERY)
    _turn(client, s["id"], "the food arrived spoiled")
    r = _turn(client, s["id"], "what is your refund policy for this?")
    msg = r["messages"][0]["text"]
    assert "Tell me exactly what went wrong" not in msg                 # issue already on file
    assert "attach" in msg.lower()                                      # short evidence reminder
    assert r["phase"] == "awaiting_evidence"


# ----------------------------------------------------------- dismissal ("opened by mistake")
def test_opened_by_mistake_closes_gracefully_and_reopens(client):
    s = _new(client, "CUST-LOW1", EARBUDS)
    r = _turn(client, s["id"], "hi by mistake i opened this it is good")
    assert r["status"] == "closed" and r["phase"] == "closed"
    assert "closed this conversation" in r["messages"][0]["text"]
    assert "proposal" not in _kinds(r["messages"])
    # the same phrases from the report: "issue resolved" keeps it closed, not clarify-looping
    r = _turn(client, s["id"], "issue resolved")
    assert r["status"] == "closed"
    # NOT hard-locked: a real problem later reopens THIS conversation (one chat per order)
    r = _turn(client, s["id"], "actually it arrived damaged and won't turn on")
    assert r["phase"] == "awaiting_evidence"


def test_dismiss_during_evidence_wait_closes_instead_of_escalating(client):
    s = _new(client, "CUST-LOW1", APPAREL)
    _turn(client, s["id"], "too tight")                       # -> awaiting_evidence
    r = _turn(client, s["id"], "never mind, it is fine actually")
    assert r["status"] == "closed"                            # NOT escalated to a human
    assert "resolution" not in _kinds(r["messages"])


def test_dismiss_during_confirmation_discards_the_proposal(client):
    s = _new(client, "CUST-NEW1", DEFECT_ELEC)
    _turn(client, s["id"], "it arrived damaged and won't turn on")
    _turn(client, s["id"], evidence=STRONG)                   # -> confirming (replacement)
    r = _turn(client, s["id"], "issue resolved, no help needed")
    assert r["status"] == "closed"
    assert "resolution" not in _kinds(r["messages"])          # nothing executed
    full = client.get(f"/api/sessions/{s['id']}").json()
    assert not full["state"].get("proposed_action")           # proposal discarded


def test_complaint_wording_never_dismisses(client):
    s = _new(client, "CUST-LOW1", EARBUDS)
    r = _turn(client, s["id"], "it is not good, the sound is damaged")
    assert r["status"] != "closed"                            # treated as an issue, not a dismissal


# ----------------------------------------------------------- intent gate (pure)
def test_detect_intent_dismissal():
    d = lambda t: conversation.detect_intent(t, "open")  # noqa: E731
    assert d("hi by mistake i opened this it is good") == "dismiss"
    assert d("issue resolved") == "dismiss"
    assert d("never mind, all good") == "dismiss"
    assert d("it is not good") != "dismiss"
    assert conversation.detect_intent("it is fine actually", "awaiting_evidence") == "dismiss"


def test_detect_intent_confirmation_gate():
    c = lambda t: conversation.detect_intent(t, "confirming")  # noqa: E731
    assert c("yes go ahead") == "confirm"
    assert c("ok") == "confirm"
    assert c("sure do it") == "confirm"
    assert c("hmm ok let me think") not in ("confirm", "reject")
    assert c("this is broken") not in ("confirm", "reject")     # 'ok' inside 'broken' must not confirm
    assert c("yes but actually no") == "reject"
    assert c("no thanks") == "reject"
    assert conversation.detect_intent("can I talk to a human please", "open") == "human"
