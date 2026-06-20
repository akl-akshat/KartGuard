"""Phase 5 checkpoints: triage, context, policy, risk, diagnosis nodes (stub LLM)."""

import pytest

from agent.deps import reset_deps
from agent.nodes import context as context_node
from agent.nodes import diagnosis as diagnosis_node
from agent.nodes import policy as policy_node
from agent.nodes import risk as risk_node
from agent.nodes import triage as triage_node
from config.settings import settings
from tools.data_access import LocalDataAccess

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clock_and_deps():
    settings.AS_OF_DATE = "2026-06-22"  # pin window math to the dataset reference
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


def _state(**kw):
    base = {"raw_request": "", "iteration_count": 0, "requires_human": False}
    base.update(kw)
    return base


def test_triage_classifies_and_extracts():
    out = triage_node.triage(_state(raw_request="The kurti is too tight, I want to return it",
                                    order_id="ORD-FIT-PREPAID"))
    assert out["issue_type"] == "wrong_size"
    assert out["order_id"] == "ORD-FIT-PREPAID"
    assert out["iteration_count"] == 1
    assert out["clarification_needed"] is False


def test_context_hit_populates_and_miss_does_not_fabricate():
    hit = context_node.context(_state(order_id="ORD-FIT-PREPAID", customer_id="CUST-LOW1"))
    assert hit["order_context"]["category"] == "apparel"
    assert "status" not in hit  # proceeds to policy

    miss = context_node.context(_state(order_id="ORD-NOPE", customer_id="CUST-NOPE"))
    assert miss["order_context"] is None and miss["customer_context"] is None
    assert miss["status"] == "not_found"


def test_policy_window_and_citations():
    da = LocalDataAccess()
    in_win = policy_node.policy(_state(order_context=da.get_order("ORD-FIT-PREPAID"),
                                       issue_type="wrong_size"))
    assert in_win["within_return_window"] is True
    assert in_win["policy_snippets"] and in_win["policy_snippets"][0]["source"]

    out_win = policy_node.policy(_state(order_context=da.get_order("ORD-OOW-NONRET"),
                                        issue_type="return_request"))
    assert out_win["within_return_window"] is False


def test_risk_discriminates_and_forces_escalation():
    hi = risk_node.risk(_state(order_id="ORD-HIVAL-COD", customer_id="CUST-SERIAL"))
    lo = risk_node.risk(_state(order_id="ORD-FIT-PREPAID", customer_id="CUST-LOW1"))
    assert hi["risk_score"] >= settings.RISK_ESCALATION_THRESHOLD
    assert hi["requires_human"] is True
    assert hi["risk_factors"] and hi["risk_factors"] != ["nominal_profile"]
    assert 0.0 <= lo["risk_score"] <= 1.0 and lo["risk_score"] < 0.3
    assert lo["requires_human"] is False


@pytest.mark.parametrize("text,issue,expected", [
    ("The kurti is too tight", "wrong_size", "size_fit_mismatch"),
    ("The earbuds arrived defective and dead", "damaged_item", "defect_damage"),
    ("I changed my mind, don't want it", "return_request", "changed_mind"),
    ("hello there", "other", "genuine_other"),
])
def test_diagnosis_cases(text, issue, expected):
    out = diagnosis_node.diagnosis(_state(raw_request=text, issue_type=issue, risk_score=0.1))
    assert out["root_cause"] == expected
