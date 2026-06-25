"""Deterministic synthetic dataset (ASM-1).

A single seeded generator produces customers, orders, and structured policy rows. It is
the source of truth for BOTH the Postgres seed (:mod:`db.seed`) and the offline
in-memory repository (:mod:`db.repository`), so the graph and the eval harness behave
identically whether or not Postgres is up.

No real PII or payment data is used (CON-2 / NFR-SEC-3). All money is INR.

The generator also guarantees a set of **named fixtures** (stable IDs) that the SRS
Appendix-A worked scenarios and the eval dataset reference directly.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

# A fixed clock anchor keeps return-window math reproducible regardless of wall time.
REFERENCE_DATE = date(2026, 6, 22)
SEED = 20260622

# category -> (returnable, exchange_allowed, return-window days)
CATEGORIES: dict[str, dict[str, Any]] = {
    "apparel": {"returnable": True, "exchange": True, "window": 7, "lo": 400, "hi": 2500},
    "footwear": {"returnable": True, "exchange": True, "window": 7, "lo": 600, "hi": 4000},
    "electronics": {"returnable": True, "exchange": True, "window": 10, "lo": 800, "hi": 15000},
    "home": {"returnable": True, "exchange": True, "window": 7, "lo": 300, "hi": 3500},
    "books": {"returnable": True, "exchange": False, "window": 5, "lo": 150, "hi": 1200},
    "beauty": {"returnable": False, "exchange": False, "window": 0, "lo": 200, "hi": 1800},
    "innerwear": {"returnable": False, "exchange": False, "window": 0, "lo": 200, "hi": 900},
    "grocery": {"returnable": False, "exchange": False, "window": 0, "lo": 100, "hi": 1500},
}
NON_RETURNABLE = {c for c, m in CATEGORIES.items() if not m["returnable"]}

REGIONS = [
    ("North", "110001"), ("West", "400001"), ("South", "560001"),
    ("East", "700001"), ("Central", "462001"), ("North-East", "781001"),
]
# Pincodes with a historically high RTO baseline (used by the fraud service).
HIGH_RTO_PINCODES = {"781001", "462001"}
SEGMENTS = ["new", "regular", "loyal", "vip"]


@dataclass
class Customer:
    id: str
    name: str
    signup_date: date
    segment: str
    ltv: float
    total_orders: int
    total_returns: int
    return_rate: float
    cod_orders: int
    cod_refusals: int
    risk_flags: list[str]
    region: str
    pincode: str


@dataclass
class Order:
    id: str
    customer_id: str
    seller_id: str
    sku: str
    title: str
    category: str
    price: float
    qty: int
    payment_mode: str
    order_date: date
    dispatch_date: date | None
    delivery_date: date | None
    delivery_status: str
    return_window_end: date | None


@dataclass
class Policy:
    id: str
    category: str
    payment_mode: str | None
    rule_type: str
    window_days: int | None
    refundable: bool
    exchange_allowed: bool
    refund_mode_default: str
    text: str


@dataclass
class Dataset:
    customers: list[Customer] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)

    def as_dicts(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "customers": [asdict(c) for c in self.customers],
            "orders": [asdict(o) for o in self.orders],
            "policies": [asdict(p) for p in self.policies],
        }


def _return_rate(total_orders: int, total_returns: int) -> float:
    return round(total_returns / total_orders, 4) if total_orders else 0.0


def _build_policies() -> list[Policy]:
    policies: list[Policy] = []
    for cat, meta in CATEGORIES.items():
        if meta["returnable"]:
            policies.append(
                Policy(
                    id=f"POL-{cat.upper()}-WINDOW",
                    category=cat,
                    payment_mode=None,
                    rule_type="window",
                    window_days=meta["window"],
                    refundable=True,
                    exchange_allowed=meta["exchange"],
                    refund_mode_default="original",
                    text=(
                        f"{cat.title()} items may be returned within {meta['window']} days "
                        f"of delivery. Refunds are issued to the original payment method by "
                        f"default. Exchange is {'available' if meta['exchange'] else 'not available'} "
                        f"for this category."
                    ),
                )
            )
        else:
            policies.append(
                Policy(
                    id=f"POL-{cat.upper()}-NONRET",
                    category=cat,
                    payment_mode=None,
                    rule_type="non_returnable",
                    window_days=0,
                    refundable=False,
                    exchange_allowed=False,
                    refund_mode_default="original",
                    text=(
                        f"{cat.title()} is a non-returnable category for hygiene/safety reasons, "
                        f"except where the item is delivered defective, damaged, or incorrect."
                    ),
                )
            )
    # Cross-cutting rules.
    policies.append(
        Policy(
            id="POL-DEFECT-EXCEPTION",
            category="*",
            payment_mode=None,
            rule_type="defect",
            window_days=None,
            refundable=True,
            exchange_allowed=True,
            refund_mode_default="original",
            text=(
                "A defective, damaged, or wrong item is always eligible for a free replacement "
                "or full refund to the original method, regardless of category or return window. "
                "A denial is never appropriate for a genuine defect or wrong-item case."
            ),
        )
    )
    policies.append(
        Policy(
            id="POL-COD-REFUND-MODE",
            category="*",
            payment_mode="COD",
            rule_type="refund_mode",
            window_days=None,
            refundable=True,
            exchange_allowed=True,
            refund_mode_default="original",
            text=(
                "For COD orders, an eligible refund is paid to the customer's bank account "
                "(original method equivalent). Store credit may be offered only as an optional, "
                "incentivised alternative — never as the sole option for a genuine refund."
            ),
        )
    )
    policies.append(
        Policy(
            id="POL-PREPAID-REFUND-MODE",
            category="*",
            payment_mode="PREPAID",
            rule_type="refund_mode",
            window_days=None,
            refundable=True,
            exchange_allowed=True,
            refund_mode_default="original",
            text=(
                "For prepaid orders, an eligible refund is returned to the original payment "
                "instrument. Store credit is only an optional incentive, never mandatory."
            ),
        )
    )
    return policies


def _make_customer(rng: random.Random, idx: int) -> Customer:
    cid = f"CUST{idx:04d}"
    segment = rng.choices(SEGMENTS, weights=[3, 5, 3, 1])[0]
    total_orders = rng.randint(1, 60)
    # Most customers low return-rate; a minority elevated.
    if rng.random() < 0.18:
        total_returns = int(total_orders * rng.uniform(0.45, 0.8))
    else:
        total_returns = int(total_orders * rng.uniform(0.0, 0.25))
    cod_orders = rng.randint(0, total_orders)
    cod_refusals = int(cod_orders * (rng.uniform(0.3, 0.7) if rng.random() < 0.12 else rng.uniform(0, 0.1)))
    region, pincode = rng.choice(REGIONS)
    flags: list[str] = []
    rr = _return_rate(total_orders, total_returns)
    if rr >= 0.45:
        flags.append("serial_returner")
    if cod_orders and cod_refusals / max(cod_orders, 1) >= 0.3:
        flags.append("cod_refuser")
    return Customer(
        id=cid,
        name=f"Customer {idx:04d}",
        signup_date=REFERENCE_DATE - timedelta(days=rng.randint(30, 1500)),
        segment=segment,
        ltv=round(total_orders * rng.uniform(300, 1200), 2),
        total_orders=total_orders,
        total_returns=total_returns,
        return_rate=rr,
        cod_orders=cod_orders,
        cod_refusals=cod_refusals,
        risk_flags=flags,
        region=region,
        pincode=pincode,
    )


def _make_order(rng: random.Random, idx: int, customer: Customer) -> Order:
    cat = rng.choice(list(CATEGORIES))
    meta = CATEGORIES[cat]
    price = round(rng.uniform(meta["lo"], meta["hi"]), 2)
    payment = "COD" if (customer.cod_orders and rng.random() < 0.5) else "PREPAID"
    in_window = rng.random() < 0.6
    if in_window:
        delivery = REFERENCE_DATE - timedelta(days=rng.randint(0, max(meta["window"] - 1, 1)))
        status = "delivered"
    else:
        delivery = REFERENCE_DATE - timedelta(days=rng.randint(meta["window"] + 5, 40))
        status = "delivered"
    order_date = delivery - timedelta(days=rng.randint(2, 6))
    dispatch = order_date + timedelta(days=1)
    window_end = (
        delivery + timedelta(days=meta["window"]) if meta["returnable"] else None
    )
    return Order(
        id=f"ORD{idx:05d}",
        customer_id=customer.id,
        seller_id="SELLER001",
        sku=f"SKU-{cat[:3].upper()}-{idx:05d}",
        title=f"{cat.title()} item {idx:05d}",
        category=cat,
        price=price,
        qty=1,
        payment_mode=payment,
        order_date=order_date,
        dispatch_date=dispatch,
        delivery_date=delivery,
        delivery_status=status,
        return_window_end=window_end,
    )


def _named_fixtures() -> tuple[list[Customer], list[Order]]:
    """Stable fixtures referenced by Appendix-A scenarios and the eval dataset."""
    customers = [
        Customer("CUST-LOW1", "Low Risk Loyal", REFERENCE_DATE - timedelta(days=900),
                 "loyal", 24000.0, 30, 3, 0.10, 5, 0, [], "West", "400001"),
        Customer("CUST-SERIAL", "Serial Returner", REFERENCE_DATE - timedelta(days=200),
                 "regular", 6000.0, 20, 13, 0.65, 14, 6, ["serial_returner", "cod_refuser"],
                 "North-East", "781001"),
        Customer("CUST-NEW1", "Brand New", REFERENCE_DATE - timedelta(days=12),
                 "new", 0.0, 1, 0, 0.0, 1, 0, [], "South", "560001"),
        Customer("CUST-VIP1", "VIP High LTV", REFERENCE_DATE - timedelta(days=1200),
                 "vip", 90000.0, 75, 6, 0.08, 2, 0, [], "North", "110001"),
    ]
    orders = [
        # A.1 size mismatch, prepaid apparel, in-window, low-risk
        Order("ORD-FIT-PREPAID", "CUST-LOW1", "SELLER001", "SKU-APP-KURTI", "Cotton Kurti",
              "apparel", 1299.0, 1, "PREPAID", REFERENCE_DATE - timedelta(days=4),
              REFERENCE_DATE - timedelta(days=3), REFERENCE_DATE - timedelta(days=2),
              "delivered", REFERENCE_DATE + timedelta(days=5)),
        # A.2 defective electronics, prepaid, in-window
        Order("ORD-DEFECT-ELEC", "CUST-LOW1", "SELLER001", "SKU-ELE-EARBUD", "Wireless Earbuds",
              "electronics", 1899.0, 1, "PREPAID", REFERENCE_DATE - timedelta(days=5),
              REFERENCE_DATE - timedelta(days=4), REFERENCE_DATE - timedelta(days=2),
              "delivered", REFERENCE_DATE + timedelta(days=8)),
        # A.3 high-value, COD, serial returner -> escalation (value above auto ceiling)
        Order("ORD-HIVAL-COD", "CUST-SERIAL", "SELLER001", "SKU-ELE-TABLET", "Android Tablet",
              "electronics", 4999.0, 1, "COD", REFERENCE_DATE - timedelta(days=6),
              REFERENCE_DATE - timedelta(days=5), REFERENCE_DATE - timedelta(days=3),
              "delivered", REFERENCE_DATE + timedelta(days=7)),
        # A.4 out-of-window, non-returnable category (beauty), prepaid
        Order("ORD-OOW-NONRET", "CUST-VIP1", "SELLER001", "SKU-BEA-SERUM", "Face Serum",
              "beauty", 799.0, 1, "PREPAID", REFERENCE_DATE - timedelta(days=30),
              REFERENCE_DATE - timedelta(days=29), REFERENCE_DATE - timedelta(days=27),
              "delivered", None),
        # changed-mind, prepaid apparel in-window (low value -> coupon vs refund)
        Order("ORD-MIND-PREPAID", "CUST-VIP1", "SELLER001", "SKU-APP-TSHIRT", "Graphic T-Shirt",
              "apparel", 699.0, 1, "PREPAID", REFERENCE_DATE - timedelta(days=3),
              REFERENCE_DATE - timedelta(days=2), REFERENCE_DATE - timedelta(days=1),
              "delivered", REFERENCE_DATE + timedelta(days=6)),
        # wrong item shipped, COD, in-window
        Order("ORD-WRONG-COD", "CUST-NEW1", "SELLER001", "SKU-FOO-SHOE", "Running Shoes",
              "footwear", 2499.0, 1, "COD", REFERENCE_DATE - timedelta(days=4),
              REFERENCE_DATE - timedelta(days=3), REFERENCE_DATE - timedelta(days=1),
              "delivered", REFERENCE_DATE + timedelta(days=6)),
        # late delivery, prepaid home
        Order("ORD-LATE-PREPAID", "CUST-LOW1", "SELLER001", "SKU-HOM-LAMP", "Table Lamp",
              "home", 1150.0, 1, "PREPAID", REFERENCE_DATE - timedelta(days=12),
              REFERENCE_DATE - timedelta(days=11), REFERENCE_DATE - timedelta(days=2),
              "delivered", REFERENCE_DATE + timedelta(days=5)),
    ]
    return customers, orders


def _eval_fixtures() -> list[Order]:
    """Curated orders (stable ids) the eval dataset pairs with scenario texts.

    Values are kept under MAX_AUTO_REFUND_ABS except the designated high-value cases, so
    escalation is driven by the labelled dimension (risk / value) rather than incidentally.
    """
    inw = REFERENCE_DATE + timedelta(days=4)
    out = REFERENCE_DATE - timedelta(days=12)
    deliv_in = REFERENCE_DATE - timedelta(days=2)
    deliv_out = REFERENCE_DATE - timedelta(days=20)

    def mk(oid, cust, cat, price, pm, window_end, deliv):
        order_date = deliv - timedelta(days=3)
        return Order(oid, cust, "SELLER001", f"SKU-{cat[:3].upper()}-{oid}", f"{cat.title()} item",
                     cat, price, 1, pm, order_date, order_date + timedelta(days=1), deliv,
                     "delivered", window_end)

    return [
        mk("EVO-SIZE-PRE", "CUST-LOW1", "apparel", 1299.0, "PREPAID", inw, deliv_in),
        mk("EVO-SIZE-COD", "CUST-NEW1", "apparel", 999.0, "COD", inw, deliv_in),
        mk("EVO-MIND-COD", "CUST-VIP1", "apparel", 899.0, "COD", inw, deliv_in),
        mk("EVO-CHEAP-PRE", "CUST-VIP1", "apparel", 1199.0, "PREPAID", inw, deliv_in),
        mk("EVO-DEFECT-COD", "CUST-NEW1", "electronics", 1799.0, "COD", inw, deliv_in),
        mk("EVO-WRONG-PRE", "CUST-LOW1", "footwear", 1499.0, "PREPAID", inw, deliv_in),
        mk("EVO-DELAY-COD", "CUST-VIP1", "home", 1150.0, "COD", inw, deliv_in),
        mk("EVO-EXPECT-PRE", "CUST-LOW1", "apparel", 1099.0, "PREPAID", inw, deliv_in),
        mk("EVO-OOW-APP-PRE", "CUST-VIP1", "apparel", 1299.0, "PREPAID", out, deliv_out),
        mk("EVO-NONRET-INNER", "CUST-LOW1", "innerwear", 599.0, "PREPAID", None, deliv_in),
        mk("EVO-NONRET-GRO", "CUST-NEW1", "grocery", 450.0, "COD", None, deliv_in),
        mk("EVO-GENUINE-PRE", "CUST-LOW1", "books", 699.0, "PREPAID", inw, deliv_in),
        mk("EVO-HIVAL-PRE", "CUST-VIP1", "electronics", 4999.0, "PREPAID", inw, deliv_in),
        mk("EVO-FRAUD-COD", "CUST-SERIAL", "apparel", 1299.0, "COD", inw, deliv_in),
        mk("EVO-DEFECT-NONRET", "CUST-NEW1", "beauty", 1499.0, "PREPAID", None, deliv_in),
        mk("EVO-DELAY-PRE", "CUST-LOW1", "electronics", 1299.0, "PREPAID", inw, deliv_in),
    ]


def build_dataset(seed: int = SEED, n_customers: int = 50, n_orders: int = 200) -> Dataset:
    """Build the full deterministic dataset (named fixtures + eval fixtures + random)."""
    rng = random.Random(seed)
    fixtures_c, fixtures_o = _named_fixtures()
    customers = list(fixtures_c)
    for i in range(1, n_customers + 1):
        customers.append(_make_customer(rng, i))

    orders = list(fixtures_o) + _eval_fixtures()
    random_customers = customers[len(fixtures_c):]
    for i in range(1, n_orders + 1):
        cust = rng.choice(random_customers)
        orders.append(_make_order(rng, i, cust))

    return Dataset(customers=customers, orders=orders, policies=_build_policies())


if __name__ == "__main__":  # quick distribution summary
    ds = build_dataset()
    cod = sum(1 for o in ds.orders if o.payment_mode == "COD")
    oow = sum(1 for o in ds.orders if o.return_window_end and o.return_window_end < REFERENCE_DATE)
    nonret = sum(1 for o in ds.orders if o.category in NON_RETURNABLE)
    serial = sum(1 for c in ds.customers if "serial_returner" in c.risk_flags)
    print(f"customers={len(ds.customers)} orders={len(ds.orders)} policies={len(ds.policies)}")
    print(f"COD={cod} PREPAID={len(ds.orders) - cod} out_of_window={oow} non_returnable={nonret}")
    print(f"serial_returners={serial}")
