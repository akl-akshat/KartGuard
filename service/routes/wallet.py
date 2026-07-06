"""Customer wallet + rewards API — refunds, interest, withdrawals, coupons, games."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from service import platform_store, policy_store, wallet_store
from service.routes.auth import require_role

router = APIRouter()


class Amount(BaseModel):
    amount: float = Field(..., gt=0, le=10_00_000)


class CouponReq(BaseModel):
    brand: str = Field(..., min_length=1, max_length=60)
    amount: float = Field(..., gt=0, le=10_00_000)


class LotteryReq(BaseModel):
    lottery: Literal["dinner", "gadget"] = "dinner"


def _require_user(user_id: str) -> None:
    if not platform_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="customer not found")


@router.get("/api/wallet/{user_id}")
def wallet(user_id: str) -> dict:
    _require_user(user_id)
    w = wallet_store.get_wallet(user_id)
    return {**w, "transactions": wallet_store.transactions(user_id),
            "coupons": wallet_store.coupons(user_id)}


@router.post("/api/wallet/{user_id}/deposit")
def deposit(user_id: str, body: Amount) -> dict:
    _require_user(user_id)
    return {**wallet_store.deposit(user_id, body.amount), "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/kyc")
def kyc(user_id: str) -> dict:
    _require_user(user_id)
    wallet_store.set_kyc(user_id, True)
    return {"ok": True, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/withdraw")
def withdraw(user_id: str, body: Amount) -> dict:
    _require_user(user_id)
    out = wallet_store.withdraw(user_id, body.amount)
    if not out["ok"] and out.get("reason") == "kyc_required":
        raise HTTPException(status_code=412, detail="kyc_required")
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "withdrawal failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/coupon")
def redeem_coupon(user_id: str, body: CouponReq) -> dict:
    _require_user(user_id)
    out = wallet_store.redeem_coupon(user_id, body.brand, body.amount)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "redeem failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/coupon/{code}/reveal")
def reveal_coupon(user_id: str, code: str) -> dict:
    _require_user(user_id)
    out = wallet_store.reveal_coupon(user_id, code)
    if not out["ok"]:
        raise HTTPException(status_code=404, detail="coupon not found")
    return out


# --------------------------------------------------------------- brand-side coupon desk
def _own_brand_or_admin(who: dict, brand_name: str) -> None:
    """A client may only operate on codes issued against THEIR brand; admin sees all."""
    if who["role"] == "admin":
        return
    co = policy_store.get_company(who["id"])
    if not co or co["name"] != brand_name:
        raise HTTPException(status_code=403, detail="this code belongs to a different brand")


@router.get("/api/coupons/{code}")
def check_coupon(code: str, rg_session: str | None = Cookie(default=None)) -> dict:
    """Brand desk step 1: check what a customer-presented code is worth (and if it's used)."""
    who = require_role(rg_session, "client", "admin")
    c = wallet_store.get_coupon(code)
    if not c:
        raise HTTPException(status_code=404, detail="unknown code")
    _own_brand_or_admin(who, c["brand"])
    return c


@router.post("/api/coupons/{code}/settle")
def settle_coupon(code: str, rg_session: str | None = Cookie(default=None)) -> dict:
    """Brand desk step 2: after the customer used the code at checkout, the brand submits it —
    one-shot — and the platform pays the brand the coupon amount."""
    who = require_role(rg_session, "client", "admin")
    c = wallet_store.get_coupon(code)
    if not c:
        raise HTTPException(status_code=404, detail="unknown code")
    _own_brand_or_admin(who, c["brand"])
    out = wallet_store.settle_coupon(code)
    if not out["ok"]:
        raise HTTPException(status_code=409, detail=out["reason"])
    return out


@router.post("/api/wallet/{user_id}/spin")
def spin(user_id: str) -> dict:
    _require_user(user_id)
    out = wallet_store.spin_wheel(user_id)
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/daily")
def daily(user_id: str) -> dict:
    _require_user(user_id)
    out = wallet_store.daily_reward(user_id)
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.get("/api/rewards/wheel")
def rewards_wheel() -> dict:
    """The wheel face (labels only — weights stay server-side)."""
    return wallet_store.wheel_config()


@router.get("/api/rewards/prizes")
def rewards_prizes() -> dict:
    """The lottery prize ladder with honest odds."""
    return {"prizes": wallet_store.ticket_prizes()}


@router.get("/api/wallet/{user_id}/rewards/status")
def rewards_status(user_id: str) -> dict:
    _require_user(user_id)
    return wallet_store.games_status(user_id)


@router.post("/api/wallet/{user_id}/lottery")
def lottery(user_id: str, body: LotteryReq | None = None) -> dict:
    """₹1 buys a sealed boarding-pass ticket; the outcome stays hidden until draw time."""
    _require_user(user_id)
    out = wallet_store.buy_ticket(user_id)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "play failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.get("/api/wallet/{user_id}/tickets")
def list_tickets(user_id: str) -> list[dict]:
    _require_user(user_id)
    return wallet_store.tickets(user_id)


@router.post("/api/wallet/{user_id}/tickets/{ticket_id}/reveal")
def reveal_ticket(user_id: str, ticket_id: str) -> dict:
    _require_user(user_id)
    out = wallet_store.reveal_ticket(user_id, ticket_id)
    if not out["ok"]:
        code = 404 if out["reason"] == "not_found" else 409
        raise HTTPException(status_code=code, detail=out)
    return {**out, "wallet": wallet_store.get_wallet(user_id)}
