"""Turn-based support conversation manager — fraud-aware, evidence-gated, credibility-scored.

The chat is a real multi-turn dialogue that is *deliberately hard to abuse*. The design
rules (from the product owner) are:

1. **Refund is never the default.** Deflection — an exchange or a like-for-like replacement —
   is the first and preferred remedy. A refund is one conditional outcome, reached only when it
   is the eligible remedy AND the claim is verified AND the customer is credible AND it is
   within the automatic caps. Otherwise a human decides. Full refunds are rare by construction.
2. **Rejecting a remedy never buys a better one.** There is no "say no until you get a full
   refund" ladder. Declining the applicable remedy re-affirms it once, then routes to a human —
   it never escalates generosity.
3. **One resolution per session.** Once a remedy has executed or a case has gone to a human, the
   session is locked; it can never move money a second time.
4. **A claim must make sense and be proven.** The issue type must be valid for the product's
   category (no "wrong size" on a television), and money-moving claims (damage, defect, spoiled
   food, wrong item, fit) must be backed by evidence that a vision assessor judges as supporting
   the claim before anything is offered. Weak or contradictory evidence goes to a human.
5. **Credibility is learned and it gates.** A persistent per-customer credibility score tightens
   the gates for customers whose past claims were disproven; it never drops for merely asking, or
   for a genuine claim. It is internal and never shown to the customer.

All monetary proposals still pass through the tested decision core (:mod:`agent.decision`) and
the deterministic guardrails; this layer adds the stricter conversational policy on top.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agent import credibility
from agent.decision.cost_model import order_value
from agent.decision.eligibility import eligible_actions
from agent.decision.guardrails import REFUNDISH, evaluate_guardrails
from agent.decision.select import default_amount, select_action
from agent.deps import Deps
from agent.evidence import SUPPORTS, assess_evidence, support_threshold
from agent.risk_model import score_and_factors
from agent.stub_brain import classify_issue, diagnose
from agent.validity import (
    PERISHABLE,
    WEARABLE,
    evidence_kind,
    evidence_required,
    issue_valid_for_category,
    redirect_message,
)
from config.settings import settings
from policies.retrieve import within_return_window
from tools.actions import execute_action, issue_goodwill_credit

# --- lexicons -----------------------------------------------------------------
_GREET = ("hi", "hello", "hey", "yo", "hii", "helo", "good morning", "good evening", "namaste")
_THANKS = ("thanks", "thank you", "thankyou", "thx", "appreciate", "great, thanks", "cheers")
_AFFIRM = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "please do", "do it",
           "proceed", "confirm", "sounds good", "please", "go for it", "that works", "affirmative")
_AFFIRM_TOK = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "proceed",
               "please", "affirmative", "y", "yee"}
_DENY = ("no", "nope", "nah", "dont", "don't", "do not", "cancel", "stop", "not that",
         "something else", "other option", "rather not", "no thanks")
_HEDGE = ("but", "wait", "actually", "hold on", "hold up", "think", "maybe", "not sure",
          "let me", "hmm", "however", "though", "idk", "i dont know", "i don't know")
_RESOLUTION_WORDS = ("refund", "return", "replace", "replacement", "exchange", "money back",
                     "send it back", "take it back", "swap")
_QUESTION_CUES = ("where is", "where's", "how long", "status", "what are my options", "what options",
                  "can i", "what can", "how do i", "when will", "policy", "eligible")
# A message that *leads* with an interrogative and reads like a question is a QUESTION even if
# it mentions issue/remedy words — "what is the policy if my food has an insect?" must be
# answered, not intaken as a damage claim (and never denied+locked).
_INTERROGATIVE_LEAD = {"what", "whats", "how", "when", "where", "wheres", "why", "which", "who",
                       "can", "could", "do", "does", "did", "is", "are", "will", "would", "am"}
_HUMAN_WORDS = ("talk to a human", "speak to a human", "talk to a person", "speak to a person",
                "talk to someone", "speak to someone", "real person", "human agent",
                "customer service", "representative", "speak to a manager", "talk to a manager",
                "human being", "escalate to a human")
# "nothing is wrong — close this": opened by mistake, all good, never mind. Multi-word phrases
# so a genuine complaint like "it is not good" can never match.
_DISMISS = ("by mistake", "opened this by mistake", "it is good", "it's good", "its good",
            "all good", "everything is fine", "everything is good", "everything is ok",
            "no issue", "no issues", "nothing wrong", "nothing is wrong", "issue resolved",
            "problem resolved", "already resolved", "never mind", "nevermind", "no help needed",
            "dont need help", "don't need help", "wrong chat", "accidentally opened",
            "it is fine", "it's fine", "its fine", "all sorted", "no complaint")

_ACTION_LABEL = {
    "exchange_with_size_guide": "a free size exchange for the correct size",
    "free_exchange": "a free exchange",
    "expedited_replacement": "a priority replacement at no cost",
    "instant_refund": "a full refund, credited straight to your KartGuard wallet",
    "partial_refund": "a partial refund, credited to your KartGuard wallet",
    "store_credit_refund": "store credit",
    "retention_coupon": "a discount coupon to keep your order",
    "goodwill_credit": "a goodwill credit",
    "deny_with_explanation": "the applicable policy on this order",
    "provide_information": "the information you need",
}
_MONEY_ACTIONS = ("instant_refund", "partial_refund", "store_credit_refund",
                  "retention_coupon", "goodwill_credit")

# A defect at or above this order value is complex/consequential enough to route to a human
# (with a replacement recommendation + vendor notification), never auto-executed.
_HIGH_VALUE_DEFECT = settings.MAX_AUTO_REFUND_ABS


@dataclass
class TurnResult:
    messages: list[dict[str, Any]] = field(default_factory=list)  # [{text, meta}]
    phase: str = "open"
    status: str = "open"
    order_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    credibility_outcome: str | None = None  # "genuine" | "denied" | "false_claim" — applied by the route

    def say(self, text: str, meta: dict | None = None) -> None:
        self.messages.append({"text": text, "meta": meta or {}})


# --- intent -------------------------------------------------------------------
def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def _is(t: str, words) -> bool:
    return any(w in t for w in words)


def _has_word(t: str, words) -> bool:
    """Whole-word membership: single tokens match on word boundaries (so 'ok' does NOT match
    'broken'), multi-word phrases match as substrings. Critical for the confirm/deny gates —
    a substring 'ok' inside 'broken' must never read as a confirmation (T-red-10)."""
    toks = set(re.findall(r"[a-z']+", t))
    for w in words:
        if " " in w:
            if w in t:
                return True
        elif w in toks:
            return True
    return False


def detect_intent(text: str, phase: str) -> str:
    t = _norm(text)
    words = t.split()
    dismissive = _is(t, _DISMISS) and not _is(t, ("not good", "not fine", "not ok",
                                                  "not resolved", "isn't good", "isnt good"))
    if phase == "confirming" and not dismissive:
        neg = _has_word(t, _DENY)
        hedged = _has_word(t, _HEDGE)
        affirm = _has_word(t, _AFFIRM) and not _is(t, _RESOLUTION_WORDS)
        lead = words[0].strip(",.!?") if words else ""
        # a clean yes: short, affirmative, no negation, no hesitation
        if affirm and not neg and not hedged and len(words) <= 5:
            return "confirm"
        # a rejection, unless it actually leads with an un-hedged yes
        if neg and not (lead in _AFFIRM_TOK and not hedged):
            return "reject"
        # otherwise fall through — understand it as a question / new issue / unclear reply
    if _has_word(t, _HUMAN_WORDS):
        return "human"
    # "nothing's wrong / close this" — must beat the issue check ("issue resolved" contains
    # 'issue') and never fire when the sentence carries a complaint or negation around it.
    if dismissive:
        return "dismiss"
    if len(words) <= 4 and _has_word(t, _GREET) and not _is(t, _RESOLUTION_WORDS) and classify_issue(t) == "other":
        return "greet"
    if _has_word(t, _THANKS):
        return "thanks"
    lead = words[0].strip(",.!?'\"") if words else ""
    if lead in _INTERROGATIVE_LEAD and (t.endswith("?") or _is(t, _QUESTION_CUES)):
        return "question"
    if classify_issue(t) != "other" or _is(t, _RESOLUTION_WORDS):
        return "issue"
    if _is(t, _QUESTION_CUES) or t.endswith("?"):
        return "question"
    return "other"


# --- helpers ------------------------------------------------------------------
def _order(deps: Deps, order_id: str | None) -> dict[str, Any] | None:
    return deps.data_access.get_order(order_id) if order_id else None


def _within_window(order: dict[str, Any]) -> bool:
    rwe = order.get("return_window_end")
    return within_return_window(date.fromisoformat(rwe)) if rwe else False


def _fname(deps: Deps, customer_id: str) -> str:
    c = deps.data_access.get_customer(customer_id)
    name = (c or {}).get("name", "there")
    return name.split(" ")[0] if name else "there"


def _cred_score(session: dict[str, Any]) -> float:
    return float((session.get("credibility") or {}).get("score", credibility.DEFAULT_SCORE))


# --- text generators ----------------------------------------------------------
def greeting(deps: Deps, customer_id: str, order: dict | None, customer_name: str | None = None) -> str:
    who = customer_name.split(" ")[0] if customer_name else _fname(deps, customer_id)
    if order:
        return (f"Hi {who}! 👋 I can see your **{order['title']}** (delivered {order.get('delivery_date','recently')}). "
                "What's the problem — wrong size, damaged, faulty, wrong item, or something else?")
    return f"Hi {who}! 👋 I'm here to help with returns, refunds and exchanges. Which order can I help you with?"


def clarify(text: str, order: dict | None) -> str:
    item = order["title"] if order else "your order"
    return random.choice([
        f"I want to get this right — could you tell me a bit more about what's wrong with your **{item}**? "
        "For example: it doesn't fit, it arrived damaged, it's faulty, the wrong item came, or part of it is missing.",
        f"Happy to help with **{item}**. What exactly went wrong — size/fit, a defect, a wrong or missing item, "
        "a delivery delay, or a change of mind?",
    ])


def is_status_query(text: str) -> bool:
    """A tracking/status ask about the customer's case — cue + topic, not a policy question."""
    t0 = _norm(text)
    cue = any(w in t0 for w in ("where", "status", "when will", "when do", "track", "progress",
                                "update on", "any update", "how long", "not received",
                                "haven't got", "have not got", "didn't get"))
    topic = any(w in t0 for w in ("refund", "money back", "my money", "exchange", "replacement",
                                  "return", "request", "case", "complaint", "order", "issue", "it"))
    return cue and topic


def answer_question(text: str, order: dict | None, policy_ctx: dict | None = None,
                    refund_ctx: dict | None = None, has_issue: bool = False) -> str:
    t0 = _norm(text)
    # Status/tracking questions answer from the customer's ACTUAL history, never boilerplate:
    # a processed resolution reports what was done (refund amount / exchange / replacement) and
    # where it stands; a case with a specialist says so; if nothing is due we say that clearly
    # and ask for the issue. Status intent only — "what is your refund POLICY" still goes to
    # policy grounding.
    if refund_ctx is not None and is_status_query(t0):
        res = refund_ctx.get("resolutions") or []
        pend = refund_ctx.get("pending_review") or []
        item = f" for your **{order['title']}**" if order else ""
        if res:
            r0 = res[0]
            at = r0.get("action_type") or ""
            label = _ACTION_LABEL.get(at, (at or "a resolution").replace("_", " "))
            amt = f" of ₹{r0['amount']:.0f}" if r0.get("amount") else ""
            if at in ("instant_refund", "partial_refund", "store_credit_refund"):
                track = ("Refunds reflect in your ReturnGuard wallet immediately and in your bank "
                         "within 3–5 business days of withdrawal.")
            elif at in ("free_exchange", "exchange_with_size_guide", "expedited_replacement"):
                track = ("It's booked and being shipped — you'll get the courier details by email, "
                         "typically within 24 hours, and delivery in 2–4 days.")
            else:
                track = "It's been processed on our side."
            return (f"Here's the status{item}: **{label}{amt}** is confirmed. {track} "
                    "If you'd like, I can connect you to a **human specialist** for anything beyond this.")
        if pend:
            return (f"I checked{item}: your case is **with our specialist team** right now — no action is "
                    "pending from you, and they'll follow up shortly. I can nudge them with your full context.")
        return (f"I've checked your account{item}: **there's no refund due or in process right now** — "
                "no claim has been approved on this order yet. Tell me what went wrong (damaged, wrong "
                "item, quality, size…) and I'll look into it properly.")

    # RAG-grounded: when the session is bound to a company, answer FROM their uploaded policy —
    # the top retrieved paragraphs are the context (a real LLM composes over the same context
    # when LLM_PROVIDER=anthropic; offline the stub quotes the most relevant excerpt verbatim).
    chunks = (policy_ctx or {}).get("chunks") or []
    if chunks:
        company = policy_ctx.get("company", "the company")
        top = chunks[0]["text"]
        excerpt = top if len(top) <= 420 else top[:417].rsplit(" ", 1)[0] + "…"
        lead = f"Here's what **{company}'s policy** says that applies to your question:\n\n> {excerpt}"
        if order:
            win = "still open" if _within_window(order) else "closed"
            lead += f"\n\nFor your **{order['title']}**, the return window is currently {win}. "
        else:
            lead += "\n\n"
        # never re-ask for the issue when one is already on file — the phase handler owns the next step
        return lead + ("" if has_issue else "Tell me exactly what went wrong and I'll take it from there.")
    t = _norm(text)
    if not order:
        return "Sure — pick the order on the left and I'll pull up your options and its return window."
    win = "still open" if _within_window(order) else "closed"
    if any(w in t for w in ("where", "status", "how long")):
        return (f"For **{order['title']}**, if a refund is due it lands in your **KartGuard wallet** instantly "
                "(withdraw to your bank anytime). Your return window is currently " + win + ".")
    return (f"For **{order['title']}** (₹{order['price']:.0f}, {order['category']}), the right resolution depends on "
            "what's wrong: an exchange for a size/fit issue, a like-for-like replacement for a genuine defect or "
            "wrong item, and a refund only where that's the eligible outcome. Tell me what happened and I'll help.")


def evidence_ask(kind: str, order: dict) -> str:
    return (f"To process this correctly I just need to see the problem first. Could you attach **{kind}** for your "
            f"**{order['title']}**? It's used only to verify this claim, kept private, and it means I can resolve a "
            "genuine issue right away. You can attach it below.")


# "how do I photograph a smell?" — a fair question about the evidence itself. Answer it with
# practical guidance for THIS claim; never route it through policy RAG (which once dredged up
# an irrelevant 'change of mind' paragraph for exactly this question).
_EVIDENCE_META_WORDS = ("attach", "photo", "image", "picture", "upload", "camera", "screenshot",
                        "smelling", "smell")


def evidence_help(issue_type: str | None, order: dict | None) -> str:
    item = order["title"] if order else "the item"
    perishable = bool(order) and order.get("category") in PERISHABLE
    if perishable and issue_type in ("damaged_item", "quality_issue", None):
        what = ("Fair question — a smell can't be photographed, but spoilage almost always shows. "
                f"Photograph the **{item} itself**: any curdling, discoloration or unusual texture, "
                "plus the container, seal or label if there is one.")
    elif issue_type in ("damaged_item", "defective_item", "quality_issue"):
        what = (f"Photograph the **{item}** so the damage or fault is clearly visible — one wide shot "
                "and one close-up of the problem area works best.")
    elif issue_type == "wrong_item":
        what = ("Photograph **what you actually received**, ideally next to the order label or invoice — "
                "that's enough to show the mismatch.")
    else:
        what = f"A clear photo of the **{item}** showing the problem is all I need."
    return (what + " Use the **attach** button below to send it. And if the problem genuinely can't be "
            "captured in a photo, just say **\"talk to a human\"** — a specialist will review your "
            "description instead.")


def propose_text(action: dict, order: dict) -> str:
    label = _ACTION_LABEL.get(action["action_type"], action["action_type"].replace("_", " "))
    amt = action.get("amount") or 0
    money = f" of ₹{amt:.0f}" if action["action_type"] in _MONEY_ACTIONS and amt else ""
    if action["action_type"] == "deny_with_explanation":
        return (f"I've checked the policy for **{order['title']}**. This one isn't eligible for a return "
                "(the window has closed, or it's a non-returnable / final-sale item), so I can't process a refund "
                "here. Is there anything else I can help with?")
    return (f"Based on what you've shared, the right resolution for your **{order['title']}** is **{label}{money}**. "
            "Shall I go ahead and set that up? (yes / no)")


def reaffirm_text(action: dict, order: dict) -> str:
    label = _ACTION_LABEL.get(action["action_type"], action["action_type"].replace("_", " "))
    return (f"I hear you. For this issue, **{label}** is the resolution our policy provides for your "
            f"**{order['title']}** — it's the fair outcome and I can set it up right now. If you'd rather not, "
            "I can pass this to a human specialist to review. Shall I go ahead, or would you prefer a specialist? "
            "(go ahead / talk to a human)")


def human_text(reason: str) -> str:
    base = ("Thanks — I've passed this to a specialist on our team to review, along with everything we've discussed. "
            "They'll follow up shortly. 🙏")
    if reason == "high_value_defect":
        return ("Because this is a higher-value item, I'm arranging a priority replacement and notifying the seller — "
                "a specialist will confirm the details with you shortly. I've shared the full context with them. 🙏")
    if reason in ("weak_evidence", "evidence_contradicts"):
        return ("Thanks for that. I wasn't able to fully verify the issue from what was shared, so I've asked a "
                "specialist to take a closer look. They'll follow up shortly — no action has been taken yet. 🙏")
    return base


def close_text(order: dict | None) -> str:
    item = f" with your **{order['title']}**" if order else ""
    return (f"No problem at all — glad everything's good{item}! 😊 I've closed this conversation. "
            "If anything does come up with this order later, just message here and we'll pick it "
            "right back up.")


def locked_text() -> str:
    return random.choice([
        "This conversation has already been resolved. If you have a **different order or a new issue**, please start "
        "a new conversation from the left and I'll help with that one.",
        "I've already actioned this request (or passed it to our team). For anything on a **different order**, start a "
        "new conversation and I'll pick it up fresh.",
    ])


def result_text(action: dict, order: dict) -> str:
    at = action["action_type"]
    amt = action.get("amount") or 0
    m = {
        "exchange_with_size_guide": "✅ Done! I've arranged a **free exchange for the correct size** with a size guide — you'll get shipping details by email.",
        "free_exchange": "✅ Your **free exchange** is booked — details are on the way by email.",
        "expedited_replacement": "✅ Sorted — a **priority replacement** is on its way at no cost. I've added a small goodwill credit for the trouble.",
        "instant_refund": f"✅ Your **refund of ₹{amt:.0f}** is in your **KartGuard wallet** right now — spend it at any brand here, or withdraw to your bank anytime.",
        "partial_refund": f"✅ A **partial refund of ₹{amt:.0f}** just landed in your **KartGuard wallet**.",
        "store_credit_refund": f"✅ **₹{amt:.0f} store credit** has been added to your account.",
        "retention_coupon": f"✅ I've applied a **₹{amt:.0f} coupon** to your account — thanks for keeping your order!",
        "goodwill_credit": f"✅ A **₹{amt:.0f} goodwill credit** has been added, with our apologies.",
        "provide_information": "I've shared the details above — let me know if you'd like me to take any action.",
    }
    return m.get(at, "✅ All done. Is there anything else I can help with?")


def _card(action: dict, pending: bool) -> dict:
    return {"kind": "proposal" if pending else "resolution",
            "action_type": action.get("action_type"), "amount": action.get("amount") or 0,
            "pending": pending}


def _review_card(reason: str) -> dict:
    return {"kind": "review", "reason": reason}


# --- the turn -----------------------------------------------------------------
def handle_turn(deps: Deps, session: dict[str, Any], text: str,
                evidence: dict[str, Any] | None = None,
                policy_ctx: dict[str, Any] | None = None,
                order: dict[str, Any] | None = None,
                refund_ctx: dict[str, Any] | None = None) -> TurnResult:
    st = dict(session.get("state") or {})
    phase = session.get("phase", "greeting")
    order_id = session.get("order_id")
    cust = session["customer_id"]
    order = order or _order(deps, order_id)  # platform (client-DB) orders are injected by the route
    intent = detect_intent(text, phase)
    r = TurnResult(phase=phase, status=session.get("status", "open"), order_id=order_id, state=st)

    # Tenant grounding: remember the top policy paragraphs retrieved for this turn so any
    # escalation carries the policy basis to the human reviewer.
    if policy_ctx and policy_ctx.get("chunks"):
        st["policy_citations"] = [
            {"doc": c["doc_name"], "seq": c["seq"], "score": c["score"],
             "text": c["text"][:300]}
            for c in policy_ctx["chunks"][:3]
        ]
        st["policy_company"] = policy_ctx.get("company")

    # 0) HARD LOCK — this session already resolved once or is with a human. From here the chat
    # is status-and-human only: track the resolution/case, or reach a specialist. Never act again.
    if st.get("locked"):
        if intent == "thanks":
            r.say("You're welcome! 🙌")
        elif intent == "human":
            r.say("Of course — I've asked a **human specialist** to pick this thread up. "
                  "They'll reply right here with your full context. 🙏")
        elif intent == "question" or is_status_query(text):
            # locked chat: never invite a fresh claim ("tell me what went wrong") — the lock would refuse it
            r.say(answer_question(text, order, policy_ctx, refund_ctx, has_issue=True))
        elif intent == "dismiss":
            r.say("Glad everything's sorted! 😊 This conversation stays closed — message here "
                  "anytime if you need it again.")
        else:
            r.say(locked_text())
        return r

    # 0.5) "nothing's wrong — I opened this by mistake / it's all good": close gracefully.
    # Any pending proposal or evidence request is discarded (nothing executes), the session
    # is NOT hard-locked, and a later real issue simply reopens this same conversation.
    if intent == "dismiss":
        for k in ("proposed_action", "evidence", "evidence_kind", "issue_text", "issue_type",
                  "root_cause", "reject_count"):
            st.pop(k, None)
        r.say(close_text(order))
        r.phase, r.status, r.state = "closed", "closed", st
        return r

    # 1) awaiting the customer's evidence for a money-moving claim
    if phase == "awaiting_evidence":
        if evidence is not None:
            return _assess_and_decide(deps, session, st, order, evidence, r)
        if intent == "human":
            return _to_human(deps, session, st, order, r, reason="customer_requested")
        if intent in ("reject",) or _is(_norm(text), _DENY):
            # declines to substantiate → we can't verify → a human reviews it (no auto remedy)
            return _to_human(deps, session, st, order, r, reason="no_evidence")
        if intent == "question" and _has_word(_norm(text), _EVIDENCE_META_WORDS):
            # a question about the evidence itself ("how can I attach a photo of a smell?")
            r.say(evidence_help(st.get("issue_type"), order))
        elif intent == "question":
            r.say(answer_question(text, order, policy_ctx, refund_ctx, has_issue=True)
                  + " Once you attach that photo, I'll take it from there.")
        else:
            r.say(evidence_ask(st.get("evidence_kind", "a photo of the problem"), order))
        r.phase, r.status, r.state = "awaiting_evidence", "awaiting_evidence", st
        return r

    # 2) awaiting confirmation of a proposed remedy
    if phase == "confirming":
        if intent == "confirm":
            return _execute(deps, session, st, order, r)
        if intent == "human":
            return _to_human(deps, session, st, order, r, reason="customer_requested")
        if intent == "reject":
            return _handle_reject(deps, session, st, order, r)
        if intent != "issue":
            # question / greeting / unclear while a proposal is pending — keep it on the table
            pending = st.get("proposed_action") or {}
            lead = (answer_question(text, order, policy_ctx, refund_ctx, has_issue=True) + " ") if intent == "question" else ""
            r.say(lead + _reconfirm(pending, order),
                  meta=_card(pending, pending=True) if pending else None)
            r.phase, r.status, r.state = "confirming", "awaiting_confirmation", st
            return r
        # intent == "issue": a different problem — fall through and re-intake

    # 3) explicit request for a human
    if intent == "human":
        return _to_human(deps, session, st, order, r, reason="customer_requested")

    # 4) social
    if intent == "greet":
        r.say(greeting(deps, cust, order))
        r.phase, r.status = "open", "open"
        return r
    if intent == "thanks":
        r.say(random.choice(["Happy to help! 🙌 Anything else for this order?",
                             "Anytime! Is there anything else I can do?"]))
        return r

    # 5) a direct question / options
    if intent == "question":
        r.say(answer_question(text, order, policy_ctx, refund_ctx,
                              has_issue=bool(st.get("issue_text"))),
              meta={"kind": "policy", "company": policy_ctx.get("company"),
                    "citations": [{"doc": c["doc_name"], "seq": c["seq"]}
                                  for c in policy_ctx["chunks"][:3]]}
              if policy_ctx and policy_ctx.get("chunks") else None)
        r.status = "open"
        return r

    # 6) a resolvable issue
    if intent == "issue" or (phase == "gathering" and order_id):
        if not order_id:
            st["issue_text"] = (st.get("issue_text", "") + " " + text).strip()
            r.say("Sure — which order is this about? Pick it from the list on the left, or paste the order id.")
            r.phase, r.status, r.state = "gathering", "open", st
            return r
        return _intake(deps, session, st, order, r, text)

    # 7) unclear / off-topic → clarify, never act
    r.say(clarify(text, order))
    r.phase, r.status = "open", "open"
    return r


def _reconfirm(action: dict, order: dict) -> str:
    if not action:
        return "Would you like me to go ahead? (yes / no)"
    label = _ACTION_LABEL.get(action["action_type"], action["action_type"].replace("_", " "))
    return (f"Just to confirm — shall I go ahead with **{label}** for your **{order['title']}**? "
            "(yes / no)")


# --- intake: validity → evidence gate → propose -------------------------------
def _intake(deps: Deps, session: dict, st: dict, order: dict, r: TurnResult, text: str) -> TurnResult:
    st["issue_text"] = (st.get("issue_text", "") + " " + text).strip()
    issue_type = classify_issue(st["issue_text"])
    category = order["category"]

    # (a) the claim must be able to apply to this product's category
    if issue_type != "other" and not issue_valid_for_category(issue_type, category):
        r.say(redirect_message(issue_type, category, order["title"]))
        st["issue_text"] = ""  # don't let the nonsensical claim pollute a later valid one
        st.pop("issue_type", None)
        st.pop("evidence", None)
        st.pop("evidence_kind", None)
        r.phase, r.status, r.state = "open", "open", st
        return r

    signals = deps.data_access.get_risk_signals(session["customer_id"], order["id"]) or {}
    base_risk, _ = score_and_factors(signals) if signals else (0.0, [])
    root_cause = diagnose(issue_type, st["issue_text"], base_risk)

    # Evidence is bound to the claim it was assessed against. If the claim has pivoted to a
    # different issue type, the old verdict no longer applies — drop it so the new claim must
    # be substantiated afresh (prevents reusing a size photo to wave through a defect refund).
    ev = st.get("evidence") or {}
    if ev and ev.get("issue_type") != issue_type:
        st.pop("evidence", None)
        st.pop("evidence_kind", None)
    st.update(issue_type=issue_type, root_cause=root_cause)

    # (b) money-moving claims must be substantiated before we offer anything
    if evidence_required(issue_type, root_cause) and (st.get("evidence") or {}).get("issue_type") != issue_type:
        st["evidence_kind"] = evidence_kind(issue_type, category, root_cause)
        r.say(evidence_ask(st["evidence_kind"], order))
        r.phase, r.status, r.state = "awaiting_evidence", "awaiting_evidence", st
        return r

    return _propose(deps, session, st, order, r)


def _assess_and_decide(deps: Deps, session: dict, st: dict, order: dict,
                       evidence: dict, r: TurnResult) -> TurnResult:
    issue_type = st.get("issue_type", "other")
    # The verdict is decided server-side by the assessor from the evidence itself — the customer
    # cannot self-certify it (no client-supplied verdict is trusted). It is bound to issue_type,
    # and the auto-accept bar adapts to the customer's credibility tier: a good history is
    # smoother (0.80), a poor one is scrutinized harder (0.92), high-risk always goes to a human.
    bar = support_threshold(credibility.tier(_cred_score(session)))
    a = assess_evidence(evidence.get("ref", ""), issue_type, order["category"], support_min=bar)
    st["evidence"] = {"ref": evidence.get("ref", ""), "verdict": a.verdict,
                      "confidence": a.confidence, "threshold": bar, "issue_type": issue_type}
    if a.verdict == SUPPORTS:
        r.say("Thanks — I've reviewed what you sent and it confirms the issue. Here's what I can do:")
        return _propose(deps, session, st, order, r)
    # inconclusive / contradicts → a human verifies (this is where a doubtful claim is caught)
    reason = "evidence_contradicts" if a.confidence <= 0.45 else "weak_evidence"
    return _to_human(deps, session, st, order, r, reason=reason)


# --- proposal (deflection-first, refund-rare, credibility-aware) ---------------
def _propose(deps: Deps, session: dict, st: dict, order: dict, r: TurnResult) -> TurnResult:
    cust, order_id = session["customer_id"], order["id"]
    root_cause = st.get("root_cause") or diagnose(st.get("issue_type", "other"), st.get("issue_text", ""), 0.0)
    within = _within_window(order)

    signals = deps.data_access.get_risk_signals(cust, order_id) or {}
    base_risk, _ = score_and_factors(signals) if signals else (0.0, [])
    cred_score = _cred_score(session)
    eff_risk = min(1.0, round(base_risk + credibility.risk_penalty(cred_score), 4))
    st["risk_score"] = eff_risk

    ev = st.get("evidence") or {}
    evidence_ok = ev.get("verdict") == SUPPORTS

    eligible = eligible_actions(root_cause, order["category"], within, order["payment_mode"])
    proposed, _candidates = select_action(root_cause, eligible, order, within)

    # A "size exchange" is meaningless on a product with no size dimension. If the cost-optimal
    # remedy is a size-guide exchange but the item isn't a wearable, offer a plain exchange.
    if proposed["action_type"] == "exchange_with_size_guide" and order["category"] not in WEARABLE:
        proposed = {**proposed, "action_type": "free_exchange"}

    # Perishable/food defect: an exchange of spoiled food is not a real remedy — the fair
    # outcome is a refund. When the evidence supports the claim and a refund is eligible,
    # prefer it (the refund-rarity gate below still applies).
    if (order["category"] in PERISHABLE and root_cause in ("defect_damage", "wrong_item_shipped")
            and evidence_ok and "instant_refund" in eligible
            and proposed["action_type"] not in REFUNDISH):
        proposed = {"action_type": "instant_refund", "amount": default_amount("instant_refund", order),
                    "params": {}, "eligible": True}

    since = settings.as_of_date - timedelta(days=settings.AUTO_REFUND_RATE_WINDOW_DAYS)
    count = deps.repo.count_auto_refunds_since(cust, since)
    grd = evaluate_guardrails(proposed, order, eff_risk, count)
    proposed = grd.action
    requires_human = grd.requires_human or eff_risk >= settings.RISK_ESCALATION_THRESHOLD

    # Refund-rarity gate: a refund is only auto-offered when the claim is verified AND the
    # customer is credible. Otherwise it goes to a human — never auto-executed on a whim.
    if proposed["action_type"] in REFUNDISH:
        if not (evidence_ok and credibility.trusts(cred_score)):
            requires_human = True
            st["human_reason"] = "unverified_refund"

    # High-value defect: complex/consequential — recommend a replacement, notify the seller,
    # but let a human confirm rather than auto-executing.
    if root_cause == "defect_damage" and order_value(order) > _HIGH_VALUE_DEFECT:
        requires_human = True
        st["vendor_notify"] = True
        st["human_reason"] = "high_value_defect"

    st.update(proposed_action=proposed, guardrail_status=grd.status)

    if requires_human or proposed["action_type"] == "escalate_to_human":
        return _to_human(deps, session, st, order, r, reason=st.get("human_reason", "risk_gate"))

    # a clean policy denial or pure information — no money, no confirmation, and it closes.
    if proposed["action_type"] in ("deny_with_explanation", "provide_information"):
        r.say(propose_text(proposed, order))
        status = "denied" if proposed["action_type"] == "deny_with_explanation" else "resolved"
        st["locked"] = True
        r.phase, r.status, r.state = "resolved", status, st
        return r

    # otherwise: a single best-fit proposal, awaiting explicit confirmation. NO alternatives ladder.
    r.say(propose_text(proposed, order), meta=_card(proposed, pending=True))
    r.phase, r.status, r.state = "confirming", "awaiting_confirmation", st
    return r


def _handle_reject(deps: Deps, session: dict, st: dict, order: dict, r: TurnResult) -> TurnResult:
    st["reject_count"] = int(st.get("reject_count", 0)) + 1
    action = st.get("proposed_action") or {}
    if st["reject_count"] >= 2:
        # a second decline never buys a bigger remedy — a human takes it from here.
        return _to_human(deps, session, st, order, r, reason="customer_declined")
    r.say(reaffirm_text(action, order), meta=_card(action, pending=True))
    r.phase, r.status, r.state = "confirming", "awaiting_confirmation", st
    return r


def _to_human(deps: Deps, session: dict, st: dict, order: dict, r: TurnResult, reason: str) -> TurnResult:
    rec = {
        "proposed_action": st.get("proposed_action"),
        "root_cause": st.get("root_cause"),
        "issue_type": st.get("issue_type"),
        "risk_score": st.get("risk_score"),
        "evidence": st.get("evidence"),
        "vendor_notify": bool(st.get("vendor_notify")),
        "reason": reason,
        "policy_citations": st.get("policy_citations"),   # the tenant-policy basis, for the reviewer
        "policy_company": st.get("policy_company"),
        "order_id": order["id"] if order else session.get("order_id"),
        "customer_id": session["customer_id"],
    }
    try:
        deps.repo.upsert_escalation(session["id"], rec)
    except Exception:  # noqa: BLE001 — recording is best-effort; the lock still protects money
        pass
    st["locked"] = True
    st["escalation_reason"] = reason
    r.say(human_text(reason), meta=_review_card(reason))
    r.phase, r.status, r.state = "escalated", "escalated", st
    return r


def _execute(deps: Deps, session: dict, st: dict, order: dict, r: TurnResult) -> TurnResult:
    if st.get("locked"):  # defence-in-depth: never execute twice in one session
        r.say(locked_text())
        return r
    action = st.get("proposed_action") or {}
    seq = int(st.get("resolve_seq", 0)) + 1
    st["resolve_seq"] = seq
    request_id = f"{session['id']}:{seq}"
    try:
        execute_action(deps.repo, request_id, action, order, actor="agent")
        goodwill = (action.get("params") or {}).get("goodwill")
        if goodwill:
            issue_goodwill_credit(deps.repo, request_id, order, goodwill, actor="agent")
        _persist_resolution(deps, request_id, session, st, action, order)
        r.say(result_text(action, order), meta=_card(action, pending=False))
        st["resolution"] = {"action_type": action["action_type"], "amount": action.get("amount") or 0,
                            "request_id": request_id}
        st["locked"] = True
        # NOTE: credibility is only ever adjusted by a *human* review decision (approve/deny),
        # never automatically here — an auto-resolved claim whose evidence the stub assessor
        # accepted must not be able to farm credibility upward (T-red-9).
        r.phase, r.status, r.state = "resolved", "resolved", st
    except Exception:  # noqa: BLE001 — safe degrade, no partial effect
        r.say("Sorry — something went wrong while processing that. I haven't made any changes; "
              "let me route this to our team to sort out.")
        st["locked"] = True
        r.phase, r.status, r.state = "escalated", "escalated", st
    return r


def _persist_resolution(deps: Deps, request_id: str, session: dict, st: dict, action: dict, order: dict) -> None:
    deps.repo.save_resolution({
        "request_id": request_id, "order_id": order["id"], "customer_id": session["customer_id"],
        "issue_type": st.get("issue_type"), "root_cause": st.get("root_cause"),
        "risk_score": st.get("risk_score"), "proposed_action": st.get("proposed_action"),
        "executed_action": action, "amount": action.get("amount") or 0,
        "requires_human": False, "status": "resolved", "guardrail_status": st.get("guardrail_status"),
        "rationale": action.get("rationale"), "customer_message": None,
    })
