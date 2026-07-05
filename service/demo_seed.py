"""Demo-tenant seeding for hosted deployments (opt-in via ``RG_SEED_DEMO=1``).

Free-tier hosts give the app an ephemeral disk, so the SQLite chat/tenant store resets on
every deploy. To keep the public demo self-explanatory, startup (when opted in) ensures a
sample company with an uploaded policy document exists — so anyone opening the live link can
bind a session to "Zomato (demo)" and watch the per-tenant RAG ground the agent's answers.

Idempotent: re-uploading the same doc name replaces it, and an existing company is reused.
Never used in tests (the flag is off by default) and never blocks boot (best-effort).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("service.demo_seed")

DEMO_COMPANY = "Zomato (demo)"
DEMO_DOC = "zomato-refund-policy.md"
DEMO_POLICY = """# Zomato Refund & Replacement Policy (Demo)

## Spoiled or contaminated food
If a customer receives food that is spoiled, stale, or contains a foreign object (hair,
insect, plastic), they must report it within 2 hours of delivery with a clear photo. On
verification, Zomato issues a full refund to the original payment source within 24 hours.
No replacement is offered for hygiene incidents.

## Wrong or missing items
If an item is missing or a wrong dish was delivered, the customer may choose a redelivery of
the correct item or a refund of that item's price. A photo of the received order is required.

## Late delivery
Orders delivered more than 20 minutes after the promised time earn a coupon worth 20% of the
order value, capped at Rs 150. No cash refunds for delays.

## Escalation
Any claim above Rs 1000, or a customer with more than 2 refunds in 30 days, must be reviewed
by a support specialist before any payout.
"""


GENERIC_POLICY = """# {brand} Returns & Refund Guidelines

## Damaged, defective or wrong items
A damaged, defective, expired or wrong item must be reported with a clear photo. On
verification, {brand} provides a like-for-like replacement as the first remedy; a refund to
the original payment method applies where a replacement is not possible.

## Change of mind
{brand} offers exchange or store credit for change-of-mind returns inside the return window.
Cash refunds for change of mind are at {brand}'s discretion and require review.

## Escalation
High-value claims and repeat refunders are reviewed by a {brand} support specialist before
any payout.
"""

# Universal customers: the SAME phone number appears across multiple brands, which is what
# lets the platform link their history and carry one credibility score everywhere.
PLATFORM_USERS = [
    ("9650440034", "Akshat Lakhera"),
    ("9812345678", "Priya Nair"),
    ("9776655443", "Rahul Verma"),
]

# (brand, phone, title, category, price, payment_mode, delivered_days_ago, window_days)
CLIENT_ORDERS = [
    ("Zomato (demo)", "9650440034", "Paneer Tikka Meal", "grocery", 349, "PREPAID", 0, 2),
    ("Swiggy", "9650440034", "Sushi Platter", "grocery", 899, "PREPAID", 0, 2),
    ("Blinkit", "9650440034", "Fresh Fruit Basket", "grocery", 450, "COD", 1, 2),
    ("Amazon", "9650440034", "Mechanical Keyboard", "electronics", 3499, "PREPAID", 2, 10),
    ("Flipkart", "9650440034", "Running Shoes", "footwear", 2199, "PREPAID", 3, 7),
    ("Amazon", "9812345678", "Yoga Mat", "home", 999, "PREPAID", 2, 7),
    ("Zomato (demo)", "9812345678", "Veg Thali", "grocery", 249, "COD", 0, 2),
    ("Flipkart", "9812345678", "Denim Jacket", "apparel", 1799, "PREPAID", 1, 7),
    ("Swiggy", "9776655443", "Chicken Biryani", "grocery", 399, "PREPAID", 0, 2),
    ("Amazon", "9776655443", "Bluetooth Speaker", "electronics", 1899, "PREPAID", 4, 10),
]

CLIENT_BRANDS = ["Zomato (demo)", "Swiggy", "Blinkit", "Flipkart", "Amazon"]


def seed_enabled() -> bool:
    return os.environ.get("RG_SEED_DEMO", "").strip() in ("1", "true", "yes")


def ensure_demo_tenant() -> None:
    """Create the demo companies, policies, platform users and client orders (best-effort)."""
    try:
        from service import policy_store

        co = policy_store.get_company_by_name(DEMO_COMPANY) or policy_store.create_company(DEMO_COMPANY)
        if not any(d["doc_name"] == DEMO_DOC for d in policy_store.list_documents(co["id"])):
            out = policy_store.upload_policy(co["id"], DEMO_DOC, DEMO_POLICY)
            log.info("Seeded demo tenant %r with %s chunks", DEMO_COMPANY, out["chunks"])
        ensure_platform_demo()
    except Exception:  # noqa: BLE001 — demo convenience must never block boot
        log.warning("Demo-tenant seeding failed; continuing without it", exc_info=True)


def ensure_platform_demo() -> None:
    """Seed the multi-client platform: brands + policies + universal users + their orders."""
    from datetime import timedelta

    from config.settings import settings
    from service import platform_store, policy_store

    companies: dict[str, dict] = {}
    for brand in CLIENT_BRANDS:
        co = policy_store.get_company_by_name(brand) or policy_store.create_company(brand)
        companies[brand] = co
        if brand != DEMO_COMPANY and not policy_store.list_documents(co["id"]):
            policy_store.upload_policy(co["id"], f"{brand.lower().split(' ')[0]}-guidelines.md",
                                       GENERIC_POLICY.format(brand=brand))

    for phone, name in PLATFORM_USERS:
        platform_store.upsert_user(phone, name)

    if any(platform_store.orders_for_phone(p) for p, _ in PLATFORM_USERS):
        return  # orders already seeded
    today = settings.as_of_date
    for brand, phone, title, category, price, pay, days_ago, window in CLIENT_ORDERS:
        delivered = today - timedelta(days=days_ago)
        platform_store.add_order(
            companies[brand]["id"], phone, title, category, price, pay,
            delivery_date=delivered.isoformat(),
            return_window_end=(delivered + timedelta(days=window)).isoformat(),
        )
    log.info("Seeded platform demo: %s brands, %s users, %s orders",
             len(CLIENT_BRANDS), len(PLATFORM_USERS), len(CLIENT_ORDERS))
