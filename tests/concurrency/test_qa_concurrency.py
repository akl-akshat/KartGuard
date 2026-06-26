"""Adversarial QA — idempotency under concurrency (AC-5, FR-EXE-2, NFR-PERF-2).

Sequential idempotency is necessary but NOT sufficient: concurrent identical requests
(two stateless workers, same key) must still produce exactly one financial effect.
"""

import os
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


@pytest.mark.skipif(not os.environ.get("DATABASE_URL_TEST"),
                    reason="real Postgres concurrency proof — set DATABASE_URL_TEST and apply db/schema.sql")
def test_concurrent_identical_request_single_effect_real_db():
    """End-to-end proof against real Postgres: 32 connections race the same request_id via
    INSERT ... ON CONFLICT DO NOTHING → exactly one audit row. Run:
        DATABASE_URL_TEST=postgresql://returnguard:returnguard@localhost:5432/returnguard_test \
            pytest tests/concurrency -m concurrency
    """
    from db.repository import PostgresRepository
    repo = PostgresRepository(os.environ["DATABASE_URL_TEST"])
    order = {"id": "O-RACE", "customer_id": "C-RACE", "price": 1299.0, "qty": 1}
    n = 32
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        process_refund(repo, "race-db-1", order, 1299.0)  # each opens its own connection

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(repo.get_audit("race-db-1", "instant_refund")) == 1


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
