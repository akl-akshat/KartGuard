"""Policy Retriever node (FR-POL-1..3, DR-RAG-2/3).

Metadata-filtered RAG over the policy corpus, plus the derived ``within_return_window``
fact. Snippets carry source ids so the reply and audit can cite the governing rule.
"""

from __future__ import annotations

from datetime import date

from agent.state import ResolutionState
from policies.retrieve import retrieve_policy, within_return_window


def policy(state: ResolutionState) -> dict:
    oc = state.get("order_context") or {}
    category = oc.get("category", "*")
    payment_mode = oc.get("payment_mode")
    issue_type = state.get("issue_type")

    snippets = retrieve_policy(category, payment_mode, issue_type)

    rwe = oc.get("return_window_end")
    window_end = date.fromisoformat(rwe) if rwe else None
    return {
        "policy_snippets": [s.model_dump() for s in snippets],
        "within_return_window": within_return_window(window_end),
    }
