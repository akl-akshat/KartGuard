"""Adversarial QA — idempotency under concurrency (AC-5, FR-EXE-2, NFR-PERF-2).

Sequential idempotency is necessary but NOT sufficient: concurrent identical requests
(two stateless workers, same key) must still produce exactly one financial effect.
"""

import threading
from pathlib import Path

import pytest

from db.repository import InMemoryRepository
from tools.actions import process_refund
from tools.data_access import LocalDataAccess

pytestmark = pytest.mark.concurrency


def _order():
    return LocalDataAccess().get_order("ORD-FIT-PREPAID")


def test_concurrent_identical_request_single_financial_effect():
    """T-CONC-1: 64 barrier-synchronised identical refunds → exactly one audit row."""
    repo = InMemoryRepository()
    order = _order()
    n = 64
    barrier = threading.Barrier(n)
    errors = []

    def worker():
        try:
            barrier.wait()
            process_refund(repo, "race-1", order, 1299.0)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = repo.get_audit("race-1", "instant_refund")
    assert not errors, errors
    assert len(rows) == 1, f"concurrent redelivery produced {len(rows)} financial effects (race)"


def test_audit_log_has_atomic_idempotency_guard():
    """Structural: the schema MUST enforce idempotency atomically (unique constraint),
    otherwise concurrent workers can double-insert regardless of the read-then-write check."""
    schema = (Path(__file__).resolve().parents[2] / "db" / "schema.sql").read_text(encoding="utf-8").lower()
    assert "unique" in schema and "request_id" in schema and "action_type" in schema, (
        "audit_log has no UNIQUE(request_id, action_type) — app-layer read-then-write is "
        "not concurrency-safe; two workers can double-execute (AC-5/FR-EXE-2 race)"
    )


def test_distinct_concurrent_requests_no_state_bleed():
    """T-CONC-2: many distinct concurrent refunds keep their own audit rows (no bleed)."""
    repo = InMemoryRepository()
    order = _order()
    n = 50
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        process_refund(repo, f"dist-{i}", order, 100.0 + i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(n):
        rows = repo.get_audit(f"dist-{i}", "instant_refund")
        assert len(rows) == 1 and rows[0]["amount"] == 100.0 + i
