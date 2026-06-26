"""Fraud & Risk Assessor node (FR-RSK-1..4).

Hybrid scoring: deterministic rules over the fraud signals plus a bounded LLM nuance pass
→ ``risk_score`` in [0,1] with enumerated ``risk_factors``. A score at or above the
escalation threshold forces ``requires_human=True`` (FR-RSK-3).
"""

from __future__ import annotations

from agent.deps import get_deps
from agent.llm import compute_risk
from agent.risk_model import score_and_factors
from agent.state import ResolutionState
from config.settings import settings


def risk(state: ResolutionState) -> dict:
    da = get_deps().data_access
    order_id = state.get("order_id")
    customer_id = state.get("customer_id")
    signals = da.get_risk_signals(customer_id, order_id) if (customer_id and order_id) else None

    if not signals:
        return {"risk_score": 0.0, "risk_factors": ["no_signals"],
                "requires_human": state.get("requires_human", False)}

    score, factors = compute_risk(signals)
    # Deterministic escalation FLOOR: the rule score alone forcing escalation cannot be undone
    # by the (bounded) model nuance (NFR-SAF-2, FR-RSK-3).
    rule_score, _ = score_and_factors(signals)
    thr = settings.RISK_ESCALATION_THRESHOLD
    requires_human = state.get("requires_human", False) or score >= thr or rule_score >= thr
    return {"risk_score": score, "risk_factors": factors, "requires_human": requires_human}
