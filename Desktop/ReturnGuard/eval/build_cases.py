"""Generate eval/cases.yaml — a labelled dataset (EV-1/2).

Specs pair stable seeded order/customer ids with scenario texts and the SPEC-derived
expected labels (root cause, allowed action set, escalation). Run::

    python -m eval.build_cases
"""

from __future__ import annotations

from pathlib import Path

import yaml

# --- scenario texts (chosen so the classifier maps them to the intended issue) ---
T_SIZE = "The item is too tight and doesn't fit me at all."
T_MIND = "I changed my mind and no longer need this, please take it back."
T_CHEAP = "I found this cheaper on another website, can I return it for a refund."
T_DEFECT = "The product arrived defective and is completely broken."
T_WRONG = "You sent the wrong item — this is not what I ordered."
T_DELAY = "My order is very late and still has not been delivered."
T_EXPECT = "The quality is poor and the product is not as described."
T_GENUINE = "I have an issue with my order and would like some help."
T_RETURN = "I want to return this order for a refund."

# --- allowed-action sets per situation (from §9.3) ---
A_SIZE_IN = ["exchange_with_size_guide", "free_exchange", "partial_refund", "instant_refund"]
A_DEFECT = ["expedited_replacement", "instant_refund"]
A_WRONG = ["expedited_replacement", "instant_refund"]
A_MIND_IN = ["retention_coupon", "instant_refund"]
A_CHEAP_IN = ["retention_coupon", "instant_refund"]
A_DELAY = ["provide_information", "goodwill_credit", "partial_refund"]
A_EXPECT_IN = ["exchange_with_size_guide", "goodwill_credit", "instant_refund", "provide_information"]
A_GENUINE_IN = ["instant_refund", "provide_information", "escalate_to_human"]
A_DENY = ["deny_with_explanation", "provide_information"]
A_ESC = ["escalate_to_human", "instant_refund", "deny_with_explanation"]

# (id, order_id, customer_id, text, expected_root_cause, allowed_actions, expected_escalation, notes)
SPECS = [
    # ---- size / fit (deflection lever) ----
    ("c01", "EVO-SIZE-PRE", "CUST-LOW1", T_SIZE, "size_fit_mismatch", A_SIZE_IN, False, "apparel prepaid in-window"),
    ("c02", "EVO-SIZE-COD", "CUST-NEW1", T_SIZE, "size_fit_mismatch", A_SIZE_IN, False, "apparel COD in-window"),
    ("c03", "ORD-FIT-PREPAID", "CUST-LOW1", T_SIZE, "size_fit_mismatch", A_SIZE_IN, False, "A.1 fixture"),
    ("c04", "EVO-OOW-APP-PRE", "CUST-VIP1", T_SIZE, "size_fit_mismatch", A_DENY, False, "size, out-of-window -> deny"),
    # ---- changed mind ----
    ("c05", "EVO-MIND-COD", "CUST-VIP1", T_MIND, "changed_mind", A_MIND_IN, False, "COD in-window coupon"),
    ("c06", "ORD-MIND-PREPAID", "CUST-VIP1", T_MIND, "changed_mind", A_MIND_IN, False, "A fixture"),
    ("c07", "EVO-OOW-APP-PRE", "CUST-VIP1", T_MIND, "changed_mind", A_DENY, False, "changed-mind out-of-window -> deny"),
    ("c08", "EVO-NONRET-INNER", "CUST-LOW1", T_MIND, "changed_mind", A_DENY, False, "non-returnable innerwear -> deny"),
    ("c09", "EVO-NONRET-GRO", "CUST-NEW1", T_MIND, "changed_mind", A_DENY, False, "non-returnable grocery -> deny"),
    ("c10", "ORD-OOW-NONRET", "CUST-VIP1", T_MIND, "changed_mind", A_DENY, False, "A.4 out-of-window non-returnable"),
    # ---- found cheaper ----
    ("c11", "EVO-CHEAP-PRE", "CUST-VIP1", T_CHEAP, "found_cheaper", A_CHEAP_IN, False, "price-aware coupon"),
    ("c12", "EVO-SIZE-COD", "CUST-VIP1", T_CHEAP, "found_cheaper", A_CHEAP_IN, False, "found cheaper COD"),
    # ---- defect / damage (satisfaction floor) ----
    ("c13", "EVO-DEFECT-COD", "CUST-NEW1", T_DEFECT, "defect_damage", A_DEFECT, False, "electronics COD defect"),
    ("c14", "ORD-DEFECT-ELEC", "CUST-LOW1", T_DEFECT, "defect_damage", A_DEFECT, False, "A.2 fixture"),
    ("c15", "EVO-DEFECT-NONRET", "CUST-NEW1", T_DEFECT, "defect_damage", A_DEFECT, False, "defect overrides non-returnable"),
    ("c16", "EVO-HIVAL-PRE", "CUST-VIP1", T_DEFECT, "defect_damage", A_DEFECT, False, "high-value defect -> replacement, no escalation"),
    # ---- wrong item shipped ----
    ("c17", "EVO-WRONG-PRE", "CUST-LOW1", T_WRONG, "wrong_item_shipped", A_WRONG, False, "footwear wrong item"),
    ("c18", "ORD-WRONG-COD", "CUST-NEW1", T_WRONG, "wrong_item_shipped", A_WRONG, False, "A fixture wrong item"),
    # ---- delivery delay ----
    ("c19", "EVO-DELAY-COD", "CUST-VIP1", T_DELAY, "delivery_delay", A_DELAY, False, "home COD delay"),
    ("c20", "EVO-DELAY-PRE", "CUST-LOW1", T_DELAY, "delivery_delay", A_DELAY, False, "electronics prepaid delay"),
    ("c21", "ORD-LATE-PREPAID", "CUST-LOW1", T_DELAY, "delivery_delay", A_DELAY, False, "A fixture late"),
    # ---- expectation mismatch ----
    ("c22", "EVO-EXPECT-PRE", "CUST-LOW1", T_EXPECT, "expectation_mismatch", A_EXPECT_IN, False, "quality complaint apparel"),
    ("c23", "EVO-SIZE-PRE", "CUST-LOW1", T_EXPECT, "expectation_mismatch", A_EXPECT_IN, False, "quality complaint reuse"),
    # ---- genuine other ----
    ("c24", "EVO-GENUINE-PRE", "CUST-LOW1", T_GENUINE, "genuine_other", A_GENUINE_IN, False, "books generic issue"),
    ("c25", "EVO-DELAY-PRE", "CUST-NEW1", T_GENUINE, "genuine_other", A_GENUINE_IN, False, "generic issue electronics"),
    # ---- non-returnable / out-of-window denials ----
    ("c26", "EVO-NONRET-INNER", "CUST-LOW1", T_CHEAP, "found_cheaper", A_DENY, False, "non-returnable found cheaper -> deny"),
    ("c27", "EVO-OOW-APP-PRE", "CUST-VIP1", T_CHEAP, "found_cheaper", A_DENY, False, "out-of-window found cheaper -> deny"),
    # ---- defect on non-returnable / out-of-window still remedied (floor) ----
    ("c28", "EVO-OOW-APP-PRE", "CUST-VIP1", T_DEFECT, "defect_damage", A_DEFECT, False, "defect overrides out-of-window"),
    ("c29", "EVO-NONRET-GRO", "CUST-NEW1", T_DEFECT, "defect_damage", A_DEFECT, False, "defect overrides non-returnable grocery"),
    # ---- escalation cases (>=5): high risk (serial) + high value over ceiling ----
    ("c30", "EVO-FRAUD-COD", "CUST-SERIAL", T_RETURN, "fraud_suspected", A_ESC, True, "serial returner -> escalate"),
    ("c31", "ORD-HIVAL-COD", "CUST-SERIAL", T_RETURN, "fraud_suspected", A_ESC, True, "A.3 high-value serial COD"),
    ("c32", "EVO-FRAUD-COD", "CUST-SERIAL", T_GENUINE, "fraud_suspected", A_ESC, True, "serial returner generic -> escalate"),
    ("c33", "EVO-HIVAL-PRE", "CUST-VIP1", T_GENUINE, "genuine_other", A_ESC, True, "high-value refund over ceiling -> escalate"),
    ("c34", "EVO-HIVAL-PRE", "CUST-VIP1", T_MIND, "changed_mind", A_MIND_IN, False, "high-value discretionary -> capped coupon, no big refund"),
    ("c35", "EVO-FRAUD-COD", "CUST-SERIAL", T_CHEAP, "found_cheaper", A_ESC, True, "high-risk found-cheaper -> escalate (risk gate)"),
    # ---- more coverage (both payment modes / categories) ----
    ("c36", "EVO-SIZE-COD", "CUST-NEW1", T_MIND, "changed_mind", A_MIND_IN, False, "apparel COD changed mind"),
    ("c37", "EVO-WRONG-PRE", "CUST-LOW1", T_DEFECT, "defect_damage", A_DEFECT, False, "footwear defect"),
    ("c38", "EVO-CHEAP-PRE", "CUST-VIP1", T_MIND, "changed_mind", A_MIND_IN, False, "apparel changed mind prepaid"),
    ("c39", "EVO-DEFECT-COD", "CUST-NEW1", T_WRONG, "wrong_item_shipped", A_WRONG, False, "electronics wrong item"),
    ("c40", "EVO-DELAY-COD", "CUST-VIP1", T_GENUINE, "genuine_other", A_GENUINE_IN, False, "home generic issue"),
    ("c41", "EVO-GENUINE-PRE", "CUST-LOW1", T_DELAY, "delivery_delay", A_DELAY, False, "books delay"),
    ("c42", "EVO-EXPECT-PRE", "CUST-LOW1", T_SIZE, "size_fit_mismatch", A_SIZE_IN, False, "apparel size reuse"),
    ("c43", "EVO-NONRET-INNER", "CUST-LOW1", T_DEFECT, "defect_damage", A_DEFECT, False, "innerwear defect remedied"),
    ("c44", "ORD-FIT-PREPAID", "CUST-LOW1", T_MIND, "changed_mind", A_MIND_IN, False, "apparel changed mind fixture"),
]


def build() -> list[dict]:
    cases = []
    for cid, oid, cust, text, root, allowed, esc, notes in SPECS:
        cases.append({
            "id": cid,
            "scenario_text": text,
            "seeded_order_id": oid,
            "seeded_customer_id": cust,
            "expected_root_cause": root,
            "expected_action": allowed,
            "expected_escalation": esc,
            "notes": notes,
        })
    return cases


def main() -> None:
    cases = build()
    path = Path(__file__).parent / "cases.yaml"
    path.write_text(yaml.safe_dump(cases, sort_keys=False, width=100), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {path}")


if __name__ == "__main__":
    main()
