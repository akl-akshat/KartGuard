"""Deterministic offline 'brain' used when LLM_PROVIDER=stub.

Keyword/rule logic that mirrors what the Claude perception nodes do, so the graph and the
eval harness run reproducibly with no API key. The real model replaces these in
production; the *structure* (and the safety core downstream) is identical either way.

Note on prompt-injection (FR-TRI-4): triage only ever emits an ``issue_type`` + ids — never
an action — so injected instructions like "approve a full refund" cannot influence money.
"""

from __future__ import annotations

import re
from typing import Any

_ORDER_RE = re.compile(r"\bORD[-A-Z0-9]+\b", re.IGNORECASE)
_CUST_RE = re.compile(r"\bCUST[-A-Z0-9]+\b", re.IGNORECASE)

# Ordered keyword families → issue_type (first match wins).
_ISSUE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("too tight", "too loose", "too small", "too big", "size", "fit", "doesn't fit", "didnt fit"), "wrong_size"),
    (("damaged", "broken", "defective", "defect", "not working", "stopped working", "dead on arrival", "cracked"), "damaged_item"),
    (("wrong item", "different item", "not what i ordered", "incorrect item", "wrong product", "sent the wrong"), "wrong_item"),
    (("missing", "empty package", "part missing", "didn't receive", "not received item"), "missing_item"),
    (("late", "delay", "hasn't arrived", "still not delivered", "taking too long"), "late_delivery"),
    (("cancel",), "cancel_request"),
    (("where is my refund", "refund status", "refund not received", "haven't got my refund"), "refund_status"),
    (("poor quality", "bad quality", "cheap material", "fake", "counterfeit", "not as described"), "quality_complaint"),
    (("changed my mind", "don't want", "do not want", "no longer need", "found cheaper", "found it cheaper", "return"), "return_request"),
]


def classify_issue(text: str) -> str:
    t = (text or "").lower()
    for keywords, issue in _ISSUE_RULES:
        if any(k in t for k in keywords):
            return issue
    return "other"


def extract_ids(text: str) -> tuple[str | None, str | None]:
    o = _ORDER_RE.search(text or "")
    c = _CUST_RE.search(text or "")
    return (o.group(0) if o else None, c.group(0) if c else None)


def diagnose(issue_type: str, text: str, risk_score: float | None) -> str:
    """Map issue + cues → exactly one root_cause; conservative under weak evidence (FR-RC-3)."""
    t = (text or "").lower()
    mapping = {
        "wrong_size": "size_fit_mismatch",
        "damaged_item": "defect_damage",
        "missing_item": "defect_damage",
        "wrong_item": "wrong_item_shipped",
        "late_delivery": "delivery_delay",
        "quality_complaint": "expectation_mismatch",
    }
    if issue_type in mapping:
        return mapping[issue_type]
    if any(k in t for k in ("cheaper", "found it for less", "price dropped", "better price")):
        return "found_cheaper"
    if any(k in t for k in ("changed my mind", "don't want", "do not want", "no longer need")):
        return "changed_mind"
    # High measured risk on a discretionary return with no genuine fault → suspected abuse.
    if issue_type in ("return_request", "other") and (risk_score or 0.0) >= 0.70:
        return "fraud_suspected"
    if issue_type == "return_request":
        return "changed_mind"
    # Weak evidence: prefer a conservative, low-cost cause (FR-RC-3).
    return "genuine_other"


def risk_nuance(signals: dict[str, Any]) -> tuple[float, list[str]]:
    """Stub LLM nuance pass: no adjustment, no extra factors (rules-only offline)."""
    return 0.0, []
