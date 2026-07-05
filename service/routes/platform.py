"""Central-platform endpoints: universal customers (phone-keyed) and client order DBs.

The platform view of ReturnGuard: client brands maintain their (mock) order databases here,
and customers are identified by phone number across every brand — one identity, one order
history, one credibility score platform-wide.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config.settings import settings
from policies.retrieve import within_return_window
from service import platform_store, policy_store

router = APIRouter()

_EMOJI = {"apparel": "👕", "footwear": "👟", "electronics": "🎧", "home": "🛋️",
          "books": "📚", "beauty": "💄", "innerwear": "🩲", "grocery": "🍱"}
_CATEGORIES = ("apparel", "footwear", "electronics", "home", "books", "beauty", "innerwear", "grocery")


class CreateOrder(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    customer_name: str = Field(default="Customer", max_length=80)
    title: str = Field(..., min_length=2, max_length=120)
    category: str = Field(default="grocery")
    price: float = Field(..., gt=0, le=10_00_000)
    payment_mode: str = Field(default="PREPAID", pattern="^(PREPAID|COD)$")
    window_days: int = Field(default=7, ge=0, le=60)


def _present(o: dict) -> dict:
    rwe = o.get("return_window_end")
    from datetime import date
    within = within_return_window(date.fromisoformat(rwe)) if rwe else False
    return {**o, "emoji": _EMOJI.get(o["category"], "📦"),
            "within_window": within, "returnable": rwe is not None}


@router.get("/api/platform/users")
def list_users() -> list[dict]:
    return platform_store.list_users()


@router.get("/api/platform/users/{user_id}/orders")
def user_orders(user_id: str) -> list[dict]:
    """Every order this customer placed across ALL client brands (phone-matched)."""
    u = platform_store.get_user(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return [_present(o) for o in platform_store.orders_for_phone(u["phone"])]


@router.get("/api/platform/users/{user_id}/refunds-summary")
def user_refunds_summary(user_id: str) -> dict:
    if not platform_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="user not found")
    return platform_store.refunds_summary(user_id)


# --------------------------------------------------------------- client order DB
@router.get("/api/companies/{company_id}/orders")
def company_orders(company_id: str) -> list[dict]:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return [_present(o) for o in platform_store.orders_for_company(company_id)]


@router.post("/api/companies/{company_id}/orders")
def add_company_order(company_id: str, body: CreateOrder) -> dict:
    """A client adds an order to its (mock) order DB. The phone number becomes — or joins —
    the customer's universal platform identity."""
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    if body.category not in _CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {_CATEGORIES}")
    platform_store.upsert_user(body.phone, body.customer_name)
    today = settings.as_of_date
    o = platform_store.add_order(
        company_id, body.phone, body.title, body.category, body.price, body.payment_mode,
        delivery_date=today.isoformat(),
        return_window_end=(today + timedelta(days=body.window_days)).isoformat(),
    )
    return _present(o)


@router.delete("/api/orders/{order_id}")
def delete_order(order_id: str) -> dict:
    if not platform_store.remove_order(order_id):
        raise HTTPException(status_code=404, detail="order not found")
    return {"ok": True}
