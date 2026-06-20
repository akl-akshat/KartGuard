"""Eligibility predicates (SRS §9.2/9.3) — pure.

``eligible_actions`` maps a root cause (gated by policy: returnable category + return
window, with the defect/wrong-item exception) to the set of *selectable* primary actions.
The two safety-critical invariants encoded here:

* defect_damage / wrong_item_shipped **never** include ``deny_with_explanation`` (the
  satisfaction floor, FR-PLN-4) — the defect exception overrides window/non-returnable.
* a non-defect, out-of-window or non-returnable case yields only deny/info (no money).
"""

from __future__ import annotations

from db.dataset import CATEGORIES

DEFECT_CAUSES = {"defect_damage", "wrong_item_shipped"}


def _category_meta(category: str) -> dict:
    return CATEGORIES.get(category, {"returnable": True, "exchange": True})


def eligible_actions(
    root_cause: str,
    category: str,
    within_window: bool,
    payment_mode: str | None = None,
) -> set[str]:
    meta = _category_meta(category)
    returnable = bool(meta["returnable"])
    exchange = bool(meta["exchange"])

    # Forced escalation cause.
    if root_cause == "fraud_suspected":
        return {"escalate_to_human", "deny_with_explanation"}

    # Defect / wrong-item: exception overrides window and non-returnable; never deny.
    if root_cause in DEFECT_CAUSES:
        actions = {"expedited_replacement", "instant_refund"}
        return actions

    # Delivery delay: information + small goodwill regardless of window.
    if root_cause == "delivery_delay":
        return {"provide_information", "goodwill_credit", "partial_refund"}

    # All remaining causes are discretionary → require an open window AND a returnable item.
    if not returnable or not within_window:
        return {"deny_with_explanation", "provide_information"}

    if root_cause == "size_fit_mismatch":
        actions = {"partial_refund", "instant_refund"}
        if exchange:
            actions |= {"exchange_with_size_guide", "free_exchange"}
        return actions
    if root_cause in ("changed_mind", "found_cheaper"):
        return {"retention_coupon", "instant_refund"}
    if root_cause == "expectation_mismatch":
        actions = {"provide_information", "goodwill_credit", "instant_refund"}
        if exchange:
            actions |= {"exchange_with_size_guide"}
        return actions
    # genuine_other
    return {"instant_refund", "provide_information", "escalate_to_human"}


def satisfaction_ok(action_type: str, root_cause: str) -> bool:
    """Satisfaction floor (FR-PLN-4): defect/wrong-item must get an adequate remedy."""
    if root_cause in DEFECT_CAUSES:
        return action_type in {
            "expedited_replacement", "instant_refund", "free_exchange", "partial_refund",
        }
    return True
