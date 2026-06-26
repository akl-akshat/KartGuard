"""Seed Postgres with the deterministic synthetic dataset (ASM-1).

Idempotent: truncates the domain tables then re-inserts, so re-running keeps counts
stable. Uses parametrised SQL only (no string interpolation of values). Run with::

    python -m db.seed
"""

from __future__ import annotations

import json
import sys

from config.settings import settings
from db.dataset import NON_RETURNABLE, REFERENCE_DATE, build_dataset


def seed(dsn: str | None = None) -> dict[str, int]:
    import psycopg

    ds = build_dataset()
    dsn = dsn or settings.DATABASE_URL
    with psycopg.connect(dsn, autocommit=True) as conn:
        # Truncate domain tables (RESTART IDENTITY resets the audit_log sequence).
        conn.execute(
            "TRUNCATE customers, orders, policies, policy_chunks, resolutions, "
            "audit_log, escalations, eval_cases, outbox RESTART IDENTITY CASCADE"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO customers (id, name, signup_date, segment, ltv, total_orders, "
                "total_returns, return_rate, cod_orders, cod_refusals, risk_flags, region, pincode) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (c.id, c.name, c.signup_date, c.segment, c.ltv, c.total_orders,
                     c.total_returns, c.return_rate, c.cod_orders, c.cod_refusals,
                     json.dumps(c.risk_flags), c.region, c.pincode)
                    for c in ds.customers
                ],
            )
            cur.executemany(
                "INSERT INTO orders (id, customer_id, seller_id, sku, title, category, price, qty, "
                "payment_mode, order_date, dispatch_date, delivery_date, delivery_status, return_window_end) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (o.id, o.customer_id, o.seller_id, o.sku, o.title, o.category, o.price, o.qty,
                     o.payment_mode, o.order_date, o.dispatch_date, o.delivery_date,
                     o.delivery_status, o.return_window_end)
                    for o in ds.orders
                ],
            )
            cur.executemany(
                "INSERT INTO policies (id, category, payment_mode, rule_type, window_days, "
                "refundable, exchange_allowed, refund_mode_default, text) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (p.id, p.category, p.payment_mode, p.rule_type, p.window_days,
                     p.refundable, p.exchange_allowed, p.refund_mode_default, p.text)
                    for p in ds.policies
                ],
            )
    return summary(ds)


def summary(ds=None) -> dict[str, int]:
    ds = ds or build_dataset()
    cod = sum(1 for o in ds.orders if o.payment_mode == "COD")
    return {
        "customers": len(ds.customers),
        "orders": len(ds.orders),
        "policies": len(ds.policies),
        "cod_orders": cod,
        "prepaid_orders": len(ds.orders) - cod,
        "out_of_window": sum(
            1 for o in ds.orders if o.return_window_end and o.return_window_end < REFERENCE_DATE
        ),
        "non_returnable": sum(1 for o in ds.orders if o.category in NON_RETURNABLE),
        "serial_returners": sum(1 for c in ds.customers if "serial_returner" in c.risk_flags),
    }


def _print_summary(s: dict[str, int]) -> None:
    print("Seeded distribution:")
    for k, v in s.items():
        print(f"  {k:<18} {v}")


if __name__ == "__main__":
    try:
        s = seed()
        _print_summary(s)
    except Exception as exc:  # noqa: BLE001 - surface a clear seed failure
        print(f"seed failed: {exc}", file=sys.stderr)
        print("(is Postgres up? `docker compose up -d postgres` then `make schema`)", file=sys.stderr)
        sys.exit(1)
