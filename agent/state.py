"""Typed agent state and catalogues (SRS §5.1).

The graph state is a ``TypedDict`` whose ``messages`` channel uses an **append reducer**
(``add_messages``); every other key is owned and written by exactly one node, which
returns *only* the keys it changes (never the whole state) to avoid clobbering (§4.4).

Enums encode the issue-type / root-cause / action-type catalogues so adding a new value is
a catalogue change, not a re-architecture (NFR-MNT-1).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# --------------------------------------------------------------------- enums
class Channel(str, Enum):
    chat = "chat"
    api = "api"
    kafka_event = "kafka_event"


class IssueType(str, Enum):
    return_request = "return_request"
    cancel_request = "cancel_request"
    refund_status = "refund_status"
    damaged_item = "damaged_item"
    wrong_item = "wrong_item"
    wrong_size = "wrong_size"
    late_delivery = "late_delivery"
    missing_item = "missing_item"
    quality_complaint = "quality_complaint"
    rto_predicted = "rto_predicted"
    other = "other"


class RootCause(str, Enum):
    size_fit_mismatch = "size_fit_mismatch"
    defect_damage = "defect_damage"
    changed_mind = "changed_mind"
    found_cheaper = "found_cheaper"
    delivery_delay = "delivery_delay"
    wrong_item_shipped = "wrong_item_shipped"
    expectation_mismatch = "expectation_mismatch"
    fraud_suspected = "fraud_suspected"
    genuine_other = "genuine_other"


class ActionType(str, Enum):
    instant_refund = "instant_refund"
    store_credit_refund = "store_credit_refund"
    partial_refund = "partial_refund"
    free_exchange = "free_exchange"
    exchange_with_size_guide = "exchange_with_size_guide"
    retention_coupon = "retention_coupon"
    expedited_replacement = "expedited_replacement"
    goodwill_credit = "goodwill_credit"
    deny_with_explanation = "deny_with_explanation"
    escalate_to_human = "escalate_to_human"
    provide_information = "provide_information"


class GuardrailStatus(str, Enum):
    pass_ = "pass"
    clamped = "clamped"
    violation = "violation"


class HumanDecision(str, Enum):
    approve = "approve"
    modify = "modify"
    reject = "reject"


class ResolutionStatus(str, Enum):
    pending = "pending"
    resolved = "resolved"
    escalated = "escalated"
    denied = "denied"
    failed = "failed"
    info = "info"
    not_found = "not_found"
    clarification = "clarification"


# ----------------------------------------------------------------- sub-models
class ResolutionAction(BaseModel):
    """A candidate or proposed action with its cost annotation (FR-PLN-1)."""

    action_type: ActionType
    amount: float = 0.0
    params: dict[str, Any] = Field(default_factory=dict)
    eligible: bool = True
    estimated_cost: float = 0.0
    rationale: str = ""


class Outcome(BaseModel):
    status: str
    action_type: str | None = None
    amount: float = 0.0
    expected_return_cost: float = 0.0
    expected_saving: float = 0.0
    requires_human: bool = False
    message: str | None = None


class OrderContext(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    customer_id: str
    category: str
    price: float
    payment_mode: str
    return_window_end: str | None = None
    delivery_status: str | None = None


class CustomerContext(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    segment: str
    return_rate: float
    risk_flags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------- state
class ResolutionState(TypedDict, total=False):
    # conversation / tool messages (append reducer)
    messages: Annotated[list, add_messages]

    # identity & channel
    request_id: str
    channel: str
    raw_request: str
    order_id: str | None
    customer_id: str | None

    # triage
    issue_type: str | None
    clarification_needed: bool
    clarification_question: str | None

    # context
    order_context: dict[str, Any] | None
    customer_context: dict[str, Any] | None

    # policy
    policy_snippets: list[dict[str, Any]]
    within_return_window: bool | None

    # risk
    risk_score: float | None
    risk_factors: list[str]

    # diagnosis
    root_cause: str | None

    # planning
    candidate_actions: list[dict[str, Any]]
    proposed_action: dict[str, Any] | None

    # guardrails / escalation
    guardrail_status: str | None
    requires_human: bool
    human_decision: str | None
    reviewer_id: str | None

    # execution / outcome
    executed_action: dict[str, Any] | None
    customer_message: str | None
    outcome: dict[str, Any] | None

    # cost annotations (for metrics)
    expected_return_cost: float | None
    expected_saving: float | None

    # observability & control
    trace_id: str
    iteration_count: int
    status: str
    rationale: str | None


def initial_state(
    request_id: str,
    raw_request: str,
    channel: str = Channel.api.value,
    order_id: str | None = None,
    customer_id: str | None = None,
    trace_id: str | None = None,
) -> ResolutionState:
    """Construct a fresh state for a new resolution request."""
    return ResolutionState(
        messages=[],
        request_id=request_id,
        channel=channel,
        raw_request=raw_request,
        order_id=order_id,
        customer_id=customer_id,
        clarification_needed=False,
        requires_human=False,
        risk_factors=[],
        policy_snippets=[],
        candidate_actions=[],
        iteration_count=0,
        status=ResolutionStatus.pending.value,
        trace_id=trace_id or request_id,
    )
