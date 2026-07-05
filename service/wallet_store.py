"""ReturnGuard Wallet — the customer money layer (the platform's revenue engine).

When a client approves a refund, the money lands in the customer's ReturnGuard wallet instead
of bouncing back to a card. From there the customer can:
  * leave it in the wallet and earn **2% p.a.** interest (the reason we want funds to stay — our
    float is the revenue model);
  * **withdraw** it (KYC-gated the first time, frictionless after);
  * convert it to a **brand coupon code** — e.g. a ₹500 Zomato refund redeemed as an Amazon
    coupon; the amount leaves the wallet when the code is revealed;
  * spend ₹ on **daily rewards / spin / micro-lotteries** for coupons funded by our brand clients.

All balances are simulated (offline demo) but the accounting is real and audited: every change is
a ledger transaction, credits are idempotent by reference, and debits can never overdraw.
Durable in the same SQLite file as the rest of the platform.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any

from service import chat_store
from service.chat_store import _LOCK, _conn

INTEREST_APR = 0.02                 # 2% per year on parked funds
_SECONDS_PER_YEAR = 365.25 * 24 * 3600
LOTTERY_COST = 1.0
LOTTERY_ODDS = 30_000              # 1 in 30,000 wins the headline prize
_MIN_ACCRUAL = 0.0001

_INIT_PATH: str | None = None


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallets (
                user_id     TEXT PRIMARY KEY,
                balance     REAL NOT NULL DEFAULT 0,
                kyc_verified INTEGER NOT NULL DEFAULT 0,
                last_accrued REAL NOT NULL,
                created_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_txns (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                kind       TEXT NOT NULL,     -- refund | deposit | withdrawal | interest | coupon | reward | lottery_stake | lottery_win
                amount     REAL NOT NULL,     -- signed: + credit, - debit
                brand      TEXT,
                note       TEXT,
                ref        TEXT,              -- idempotency key (e.g. resolution request_id)
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_txn_user ON wallet_txns(user_id, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_txn_ref ON wallet_txns(ref) WHERE ref IS NOT NULL;
            CREATE TABLE IF NOT EXISTS coupons (
                code       TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                brand      TEXT NOT NULL,
                amount     REAL NOT NULL,
                revealed   INTEGER NOT NULL DEFAULT 0,
                settled    INTEGER NOT NULL DEFAULT 0,   -- brand redeemed it with the platform
                settled_at REAL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rewards_log (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                game       TEXT NOT NULL,     -- spin | daily | lottery
                day        TEXT NOT NULL,     -- YYYY-MM-DD (gates once/day)
                outcome    TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rewards_user ON rewards_log(user_id, day);
            """
        )
        # migration: coupons gained brand-settlement tracking
        import sqlite3
        for ddl in ("ALTER TABLE coupons ADD COLUMN settled INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE coupons ADD COLUMN settled_at REAL"):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
    _INIT_PATH = chat_store.DB_PATH


def _now() -> float:
    return time.time()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ------------------------------------------------------------------ wallet core
def _accrue(c, user_id: str) -> None:
    """Add 2% p.a. interest for the time elapsed since the last accrual (simple, on-read)."""
    row = c.execute("SELECT balance, last_accrued FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return
    bal, last = float(row["balance"]), float(row["last_accrued"])
    now = _now()
    interest = bal * INTEREST_APR * ((now - last) / _SECONDS_PER_YEAR)
    if interest >= _MIN_ACCRUAL and bal > 0:
        c.execute("UPDATE wallets SET balance=balance+?, last_accrued=? WHERE user_id=?",
                  (round(interest, 6), now, user_id))
        c.execute("INSERT INTO wallet_txns (id, user_id, kind, amount, note, created_at) VALUES (?,?,?,?,?,?)",
                  ("tx_" + uuid.uuid4().hex[:12], user_id, "interest", round(interest, 6),
                   "2% p.a. on parked balance", now))
    else:
        c.execute("UPDATE wallets SET last_accrued=? WHERE user_id=?", (now, user_id))


def ensure_wallet(user_id: str) -> None:
    init()
    with _LOCK, _conn() as c:
        if not c.execute("SELECT 1 FROM wallets WHERE user_id=?", (user_id,)).fetchone():
            c.execute("INSERT INTO wallets (user_id, balance, last_accrued, created_at) VALUES (?,?,?,?)",
                      (user_id, 0.0, _now(), _now()))


def get_wallet(user_id: str) -> dict[str, Any]:
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        _accrue(c, user_id)
        r = c.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return {"user_id": user_id, "balance": round(r["balance"], 2),
            "kyc_verified": bool(r["kyc_verified"]),
            "interest_apr": INTEREST_APR}


def _txn(c, user_id: str, kind: str, amount: float, *, brand=None, note=None, ref=None) -> None:
    c.execute("INSERT INTO wallet_txns (id, user_id, kind, amount, brand, note, ref, created_at) "
              "VALUES (?,?,?,?,?,?,?,?)",
              ("tx_" + uuid.uuid4().hex[:12], user_id, kind, round(amount, 6), brand, note, ref, _now()))


def credit(user_id: str, amount: float, kind: str, *, brand=None, note=None, ref=None) -> dict[str, Any]:
    """Add money to the wallet. Idempotent when ``ref`` is given (a repeat is a silent no-op)."""
    ensure_wallet(user_id)
    if amount <= 0:
        return {"ok": False, "reason": "amount must be positive"}
    with _LOCK, _conn() as c:
        if ref and c.execute("SELECT 1 FROM wallet_txns WHERE ref=?", (ref,)).fetchone():
            return {"ok": True, "idempotent": True}
        _accrue(c, user_id)
        c.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (round(amount, 2), user_id))
        _txn(c, user_id, kind, amount, brand=brand, note=note, ref=ref)
    return {"ok": True}


def _debit(c, user_id: str, amount: float, kind: str, *, brand=None, note=None) -> bool:
    _accrue(c, user_id)
    bal = float(c.execute("SELECT balance FROM wallets WHERE user_id=?", (user_id,)).fetchone()["balance"])
    if amount <= 0 or bal + 1e-9 < amount:
        return False
    c.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (round(amount, 2), user_id))
    _txn(c, user_id, kind, -amount, brand=brand, note=note)
    return True


def deposit(user_id: str, amount: float) -> dict[str, Any]:
    return credit(user_id, amount, "deposit", note="Top-up")


def withdraw(user_id: str, amount: float) -> dict[str, Any]:
    """KYC-gated the first time; overdraw is impossible."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        w = c.execute("SELECT balance, kyc_verified FROM wallets WHERE user_id=?", (user_id,)).fetchone()
        if not w["kyc_verified"]:
            return {"ok": False, "reason": "kyc_required"}
        if not _debit(c, user_id, amount, "withdrawal", note="Bank withdrawal"):
            return {"ok": False, "reason": "insufficient balance"}
    return {"ok": True}


def set_kyc(user_id: str, verified: bool = True) -> None:
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        c.execute("UPDATE wallets SET kyc_verified=? WHERE user_id=?", (1 if verified else 0, user_id))


def transactions(user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM wallet_txns WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                         (user_id, limit)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "amount": round(r["amount"], 2), "brand": r["brand"],
             "note": r["note"], "created_at": r["created_at"]} for r in rows]


# ------------------------------------------------------------------ brand coupons
def redeem_coupon(user_id: str, brand: str, amount: float) -> dict[str, Any]:
    """Convert wallet balance into a redeemable brand coupon code (money leaves the wallet now).

    The customer withdraws to a specific brand — the brand later validates the code's value with
    us. Reveal ('scratch') just unmasks the code they already paid for."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if not _debit(c, user_id, amount, "coupon", brand=brand, note=f"{brand} coupon"):
            return {"ok": False, "reason": "insufficient balance"}
        code = f"{brand[:3].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
        c.execute("INSERT INTO coupons (code, user_id, brand, amount, revealed, created_at) VALUES (?,?,?,?,?,?)",
                  (code, user_id, brand, round(amount, 2), 0, _now()))
    return {"ok": True, "code": code, "brand": brand, "amount": round(amount, 2)}


def reveal_coupon(user_id: str, code: str) -> dict[str, Any]:
    init()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM coupons WHERE code=? AND user_id=?", (code, user_id)).fetchone()
        if not r:
            return {"ok": False}
        c.execute("UPDATE coupons SET revealed=1 WHERE code=?", (code,))
    return {"ok": True, "code": r["code"], "brand": r["brand"], "amount": round(r["amount"], 2)}


def coupons(user_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM coupons WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
                         (user_id,)).fetchall()
    # the owner always gets their code; "masked" drives the scratch-to-reveal presentation
    return [{"code": r["code"], "masked": not r["revealed"], "settled": bool(r["settled"]),
             "brand": r["brand"], "amount": round(r["amount"], 2)} for r in rows]


def get_coupon(code: str) -> dict[str, Any] | None:
    """Brand-side lookup: what is this code worth, and has it been used already?"""
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM coupons WHERE code=?", (code.strip().upper(),)).fetchone()
    if not r:
        return None
    return {"code": r["code"], "brand": r["brand"], "amount": round(r["amount"], 2),
            "revealed": bool(r["revealed"]), "settled": bool(r["settled"]),
            "settled_at": r["settled_at"]}


def settle_coupon(code: str) -> dict[str, Any]:
    """The brand redeems the code with the platform after the customer used it at checkout.
    One-shot: the platform then owes the brand exactly the coupon amount (funds were already
    taken from the customer's wallet at creation)."""
    init()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM coupons WHERE code=?", (code.strip().upper(),)).fetchone()
        if not r:
            return {"ok": False, "reason": "not_found"}
        if r["settled"]:
            return {"ok": False, "reason": "already_settled"}
        c.execute("UPDATE coupons SET settled=1, settled_at=? WHERE code=?", (_now(), r["code"]))
    return {"ok": True, "code": r["code"], "brand": r["brand"], "amount": round(r["amount"], 2),
            "payout_note": "platform pays the brand this amount in the settlement cycle"}


# ------------------------------------------------------------------ games (daily-gated, fair)
_SPIN_PRIZES = [
    ("₹5 wallet credit", "credit", 5.0),
    ("₹10 wallet credit", "credit", 10.0),
    ("₹20 wallet credit", "credit", 20.0),
    ("10% Zomato coupon", "coupon:Zomato", 0.0),
    ("Better luck tomorrow", "none", 0.0),
    ("₹2 wallet credit", "credit", 2.0),
]


def _already_played(c, user_id: str, game: str) -> bool:
    return bool(c.execute("SELECT 1 FROM rewards_log WHERE user_id=? AND game=? AND day=?",
                          (user_id, game, _today())).fetchone())


def spin_wheel(user_id: str) -> dict[str, Any]:
    """One free daily spin. Prizes are funded by brand-supplied coupons + small credits."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if _already_played(c, user_id, "spin"):
            return {"ok": False, "reason": "already_played_today"}
        label, kind, val = random.choice(_SPIN_PRIZES)
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "spin", _today(), label, _now()))
        if kind == "credit":
            _accrue(c, user_id)
            c.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (val, user_id))
            _txn(c, user_id, "reward", val, note=f"Spin reward: {label}")
    return {"ok": True, "prize": label}


def daily_reward(user_id: str) -> dict[str, Any]:
    """A small guaranteed daily check-in credit to bring the customer back each day."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if _already_played(c, user_id, "daily"):
            return {"ok": False, "reason": "already_claimed_today"}
        val = random.choice([1.0, 1.0, 2.0, 3.0, 5.0])
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "daily", _today(), f"₹{val:.0f}", _now()))
        _accrue(c, user_id)
        c.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (val, user_id))
        _txn(c, user_id, "reward", val, note="Daily check-in reward")
    return {"ok": True, "amount": val}


_LOTTERIES = {
    "dinner": {"name": "5-star family dinner for 3", "cost": 1.0, "odds": 30_000},
    "gadget": {"name": "Flagship smartphone", "cost": 2.0, "odds": 80_000},
}


def play_lottery(user_id: str, lottery: str = "dinner") -> dict[str, Any]:
    """Stake a tiny amount for a large, rarely-won prize. Expected value favours the house, so we
    win on aggregate while each customer happily plays ('what's ₹1 for a shot at a 5-star dinner?')."""
    cfg = _LOTTERIES.get(lottery)
    if not cfg:
        return {"ok": False, "reason": "unknown lottery"}
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if not _debit(c, user_id, cfg["cost"], "lottery_stake", note=f"Lottery: {cfg['name']}"):
            return {"ok": False, "reason": "insufficient balance"}
        won = random.randint(1, cfg["odds"]) == 1
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "lottery", _today(),
                   ("WON:" + cfg["name"]) if won else "no_win", _now()))
    return {"ok": True, "won": won, "prize": cfg["name"] if won else None,
            "cost": cfg["cost"], "odds": cfg["odds"]}
