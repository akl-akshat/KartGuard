"""Phase 2 checkpoints: metadata-filtered retrieval, citations, window math, coverage."""

from datetime import date

import pytest

from policies.corpus_loader import load_chunks
from policies.retrieve import retrieve_policy, within_return_window

pytestmark = pytest.mark.unit


def test_size_fit_probe_returns_exchange_rule():
    snips = retrieve_policy("apparel", "PREPAID", "wrong_size")
    assert snips, "expected snippets"
    joined = " ".join(s.text.lower() for s in snips)
    assert any(s.policy_id == "POL-APPAREL-WINDOW" for s in snips)
    assert "exchange" in joined and "size guide" in joined
    # top result should be the exchange/size-guide lever for a fit issue
    assert "exchange" in snips[0].text.lower()


def test_non_returnable_probe_returns_nonret_rule():
    snips = retrieve_policy("beauty", "PREPAID", "return_request")
    assert any(s.policy_id == "POL-BEAUTY-NONRET" for s in snips)
    assert any("non-returnable" in s.text.lower() for s in snips)


def test_metadata_filter_excludes_wrong_payment_mode():
    # A PREPAID query must NOT surface the COD-only refund-mode rule.
    snips = retrieve_policy("apparel", "PREPAID", "refund_status")
    assert all(s.policy_id != "POL-COD-REFUND-MODE" for s in snips)
    # ...and a COD query must NOT surface the PREPAID-only rule.
    cod = retrieve_policy("apparel", "COD", "refund_status")
    assert all(s.policy_id != "POL-PREPAID-REFUND-MODE" for s in cod)


def test_snippets_carry_citations():
    snips = retrieve_policy("electronics", "PREPAID", "damaged_item")
    assert all(s.source and "#" in s.source for s in snips)


def test_within_return_window_math():
    assert within_return_window(date(2026, 12, 31), as_of=date(2026, 6, 22)) is True
    assert within_return_window(date(2026, 1, 1), as_of=date(2026, 6, 22)) is False
    assert within_return_window(None, as_of=date(2026, 6, 22)) is False


def test_chunk_ids_are_globally_unique():
    # Regression: a policy_id spanning multiple corpus files must not collide ids.
    chunks = load_chunks()
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk ids would corrupt the embedding map"


def test_corpus_covers_all_dr_rag_4_topics():
    chunks = load_chunks()
    rule_types = {c.metadata.get("rule_type") for c in chunks}
    for required in ("window", "non_returnable", "defect", "refund_mode", "exchange"):
        assert required in rule_types, f"missing corpus topic: {required}"
    text = " ".join(c.chunk_text.lower() for c in chunks)
    # original-method default + store-credit-as-incentive principle (NFR-SAF-4 / DR-RAG-4)
    assert "original payment method" in text
    assert "store credit" in text or "store-credit" in text
    # both refund modes present
    pms = {c.metadata.get("payment_mode") for c in chunks}
    assert "COD" in pms and "PREPAID" in pms
