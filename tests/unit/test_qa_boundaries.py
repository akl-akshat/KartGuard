"""Adversarial QA — boundary/edge unit tests (rate-limit, eligibility, selection, state)."""

from datetime import timedelta

import pytest

from agent.decision.eligibility import eligible_actions
from agent.decision.select import select_action
from config.settings import settings
from db.dataset import REFERENCE_DATE
from db.repository import InMemoryRepository

pytestmark = pytest.mark.unit


# --------------------------------------------------------- rate-limit edges
def _audit(repo, rid, cust, days_ago):
    created = (REFERENCE_DATE - timedelta(days=days_ago)).isoformat()
    repo.append_audit({"request_id": rid, "action_type": "instant_refund", "amount": 100.0,
                       "actor": "agent", "customer_id": cust, "created_at": created})


def test_rate_limit_window_off_by_one():
    repo = InMemoryRepository()
    for i, age in enumerate((5, 20, 29)):       # 3 within window
        _audit(repo, f"r{i}", "CUST-X", age)
    _audit(repo, "r-old", "CUST-X", 31)          # outside the 30-day window
    since = REFERENCE_DATE - timedelta(days=settings.AUTO_REFUND_RATE_WINDOW_DAYS)
    count = repo.count_auto_refunds_since("CUST-X", since)
    assert count == 3, f"expected 3 in-window refunds, got {count} (31-day refund must not count)"


def test_rate_limit_counts_other_customer_separately():
    repo = InMemoryRepository()
    _audit(repo, "a", "CUST-A", 1)
    _audit(repo, "b", "CUST-B", 1)
    since = REFERENCE_DATE - timedelta(days=30)
    assert repo.count_auto_refunds_since("CUST-A", since) == 1


# ------------------------------------------------- eligibility (exhaustive)
@pytest.mark.parametrize("cause,within,cat,must_have,must_not", [
    ("defect_damage", False, "beauty", {"expedited_replacement", "instant_refund"}, {"deny_with_explanation"}),
    ("wrong_item_shipped", False, "grocery", {"expedited_replacement"}, {"deny_with_explanation"}),
    ("fraud_suspected", True, "apparel", {"escalate_to_human"}, set()),
    ("changed_mind", False, "apparel", {"deny_with_explanation"}, {"instant_refund", "retention_coupon"}),
    ("changed_mind", True, "beauty", {"deny_with_explanation"}, {"instant_refund"}),
    ("size_fit_mismatch", True, "apparel", {"exchange_with_size_guide"}, {"deny_with_explanation"}),
])
def test_eligibility_matrix(cause, within, cat, must_have, must_not):
    s = eligible_actions(cause, cat, within)
    assert must_have <= s, f"{cause}/{cat}/{within}: missing {must_have - s}"
    assert not (must_not & s), f"{cause}/{cat}/{within}: must not contain {must_not & s}"


# ---------------------------------------------------- selection invariants
def test_selection_never_denies_defect_even_to_save_cost():
    order = {"id": "O", "customer_id": "C", "price": 50000.0, "qty": 1, "delivery_status": "delivered"}
    elig = eligible_actions("defect_damage", "electronics", True)
    proposed, _ = select_action("defect_damage", elig, order, True)
    assert proposed["action_type"] != "deny_with_explanation"
    assert proposed["action_type"] in {"expedited_replacement", "instant_refund"}


def test_selection_empty_feasible_set_escalates():
    proposed, _ = select_action("genuine_other", {"escalate_to_human"}, {"price": 100, "qty": 1}, True)
    assert proposed["action_type"] == "escalate_to_human"


def test_selection_is_deterministic():
    order = {"id": "O", "customer_id": "C", "price": 1299.0, "qty": 1, "delivery_status": "delivered"}
    elig = eligible_actions("size_fit_mismatch", "apparel", True)
    a, _ = select_action("size_fit_mismatch", elig, order, True)
    b, _ = select_action("size_fit_mismatch", elig, order, True)
    assert a == b


# --------------------------------------------------------- state contract
def test_messages_channel_uses_append_reducer():
    from langgraph.graph.message import add_messages

    from agent.state import ResolutionState
    ann = ResolutionState.__annotations__["messages"]
    assert "add_messages" in repr(ann), "messages must use the add_messages append reducer"
    merged = add_messages([{"role": "user", "content": "a"}], [{"role": "assistant", "content": "b"}])
    assert len(merged) == 2  # append, not overwrite


def test_nodes_return_only_partial_keys():
    from agent.deps import reset_deps
    from agent.nodes import triage
    reset_deps()
    out = triage.triage({"raw_request": "The kurti is too tight", "order_id": "ORD-FIT-PREPAID",
                         "iteration_count": 0})
    assert set(out).issubset({"issue_type", "order_id", "customer_id", "clarification_needed",
                              "clarification_question", "iteration_count", "requires_human"})
    assert "order_context" not in out and "proposed_action" not in out
