"""Adversarial QA — end-to-end decision matrix (deterministic stub LLM)."""

import pytest

from agent.deps import get_deps, reset_deps
from agent.graph import build_graph
from agent.runner import is_paused, run_config
from agent.state import initial_state
from config.settings import settings

pytestmark = pytest.mark.e2e

FORBIDDEN = ("risk", "fraud", "serial", "wardrob", "abuse", "score")

# (id, order, customer, text, allowed_actions, expect_escalation)
MATRIX = [
    ("m-size", "EVO-SIZE-PRE", "CUST-LOW1", "too tight, doesn't fit",
     {"exchange_with_size_guide", "free_exchange", "partial_refund", "instant_refund"}, False),
    ("m-defect", "EVO-DEFECT-COD", "CUST-NEW1", "arrived defective and broken",
     {"expedited_replacement", "instant_refund"}, False),
    ("m-wrong", "EVO-WRONG-PRE", "CUST-LOW1", "you sent the wrong item",
     {"expedited_replacement", "instant_refund"}, False),
    ("m-mind", "EVO-MIND-COD", "CUST-VIP1", "changed my mind, take it back",
     {"retention_coupon", "instant_refund"}, False),
    ("m-cheap", "EVO-CHEAP-PRE", "CUST-VIP1", "found it cheaper elsewhere, return please",
     {"retention_coupon", "instant_refund"}, False),
    ("m-delay", "EVO-DELAY-COD", "CUST-VIP1", "my order is very late and not delivered",
     {"provide_information", "goodwill_credit", "partial_refund"}, False),
    ("m-deny", "ORD-OOW-NONRET", "CUST-VIP1", "changed my mind, return it",
     {"deny_with_explanation", "provide_information"}, False),
    ("m-fraud", "EVO-FRAUD-COD", "CUST-SERIAL", "I want to return this order",
     {"escalate_to_human", "instant_refund", "retention_coupon"}, True),
    ("m-hival", "EVO-HIVAL-PRE", "CUST-VIP1", "I have an issue and want a refund",
     {"instant_refund", "escalate_to_human"}, True),
    ("m-defect-nonret", "EVO-DEFECT-NONRET", "CUST-NEW1", "arrived broken and defective",
     {"expedited_replacement", "instant_refund"}, False),
]


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


@pytest.mark.parametrize("rid,oid,cid,text,allowed,expect_esc", MATRIX)
def test_decision_matrix(rid, oid, cid, text, allowed, expect_esc):
    g = build_graph(checkpointer=_mem())
    final = g.invoke(initial_state(rid, text, order_id=oid, customer_id=cid), run_config(rid))
    requires_human = is_paused(g, run_config(rid)) or final.get("requires_human")
    action = (final.get("executed_action") or final.get("proposed_action") or {}).get("action_type")

    assert requires_human == expect_esc, f"{rid}: escalation {requires_human} != {expect_esc}"
    if expect_esc:
        assert requires_human  # the recommendation may be any; escalation is the invariant
    else:
        assert action in allowed, f"{rid}: action {action} not in {allowed}"
        # auto path must never move money above the order value
        repo = get_deps().repo
        ov = repo.get_order(oid).price
        moved = sum(a.get("amount") or 0 for a in repo.get_audit(rid)
                    if a["action_type"] in ("instant_refund", "partial_refund", "store_credit_refund"))
        assert moved <= ov + 1e-6
    # never leak internals
    assert not any(t in (final.get("customer_message") or "").lower() for t in FORBIDDEN)


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
