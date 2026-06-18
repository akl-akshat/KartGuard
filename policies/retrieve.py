"""Public policy-retrieval interface (FR-POL-1..3, DR-RAG-2/3).

``retrieve_policy`` returns metadata-filtered, similarity-ranked, **cited** snippets. The
default store is an in-memory store built from the corpus (offline); production injects a
:class:`policies.store.PgPolicyStore`.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from pydantic import BaseModel

from config.settings import settings
from policies.corpus_loader import load_chunks
from policies.store import InMemoryPolicyStore, PolicyStore


class PolicySnippet(BaseModel):
    """A retrieved policy chunk carrying a source id so replies/audit can cite it."""

    policy_id: str
    source: str  # chunk id, e.g. "POL-APPAREL-WINDOW#0"
    text: str
    score: float
    metadata: dict = {}


@lru_cache(maxsize=1)
def get_default_store() -> InMemoryPolicyStore:
    return InMemoryPolicyStore(load_chunks())


def _build_query(category: str, payment_mode: str | None, issue_type: str | None) -> str:
    parts = [category]
    if payment_mode:
        parts.append(payment_mode)
    if issue_type:
        parts.append(issue_type.replace("_", " "))
    parts += ["return", "refund", "exchange", "policy", "window", "eligibility"]
    return " ".join(parts)


def retrieve_policy(
    category: str,
    payment_mode: str | None,
    issue_type: str | None = None,
    k: int | None = None,
    store: PolicyStore | None = None,
) -> list[PolicySnippet]:
    k = k or settings.RAG_TOP_K
    store = store or get_default_store()
    query = _build_query(category, payment_mode, issue_type)
    scored = store.search(category, payment_mode, issue_type, query, k)
    return [
        PolicySnippet(
            policy_id=s.policy_id, source=s.chunk_id, text=s.chunk_text,
            score=round(s.score, 4), metadata=s.metadata,
        )
        for s in scored
    ]


def within_return_window(return_window_end: date | None, as_of: date | None = None) -> bool:
    """Derived fact (FR-POL-3): is the order still inside its return window?"""
    if return_window_end is None:
        return False
    as_of = as_of or settings.as_of_date
    return as_of <= return_window_end
