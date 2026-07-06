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

import os
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
            CREATE TABLE IF NOT EXISTS lottery_tickets (
                id            TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL,
                code          TEXT NOT NULL,     -- boarding-pass code shown to the customer
                day           TEXT NOT NULL,
                draw_at       REAL NOT NULL,     -- outcome stays sealed until this moment
                outcome_label TEXT NOT NULL,     -- drawn at purchase, server-side, never leaked early
                outcome_kind  TEXT NOT NULL,     -- grand | voucher
                outcome_value REAL NOT NULL,
                cost          REAL NOT NULL,
                revealed      INTEGER NOT NULL DEFAULT 0,
                voucher_code  TEXT,
                created_at    REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_user ON lottery_tickets(user_id, created_at DESC);
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


# ------------------------------------------------------------------ games (daily-gated, house-safe)
# The wheel is SERVER-authoritative: the weighted draw happens here and the UI merely animates
# the wheel to the drawn segment. Big prizes are rare (jackpot 1 in 100), small wins common —
# random every time, but the expected daily giveaway stays pocket change.
_SPIN_SEGMENTS = [  # (label, kind, value, weight) — weights sum 100; order == wheel face order
    ("₹2 credit", "credit", 2.0, 28),
    ("₹5 voucher", "voucher", 5.0, 22),
    ("₹5 credit", "credit", 5.0, 18),
    ("₹10 voucher", "voucher", 10.0, 12),
    ("₹10 credit", "credit", 10.0, 9),
    ("₹20 voucher", "voucher", 20.0, 6),
    ("₹25 credit", "credit", 25.0, 4),
    ("₹100 JACKPOT", "credit", 100.0, 1),
]


def wheel_config() -> dict[str, Any]:
    """The wheel face (labels only — never the weights) so the UI draws exactly what we land on."""
    return {"segments": [{"label": s[0], "kind": s[1], "value": s[2]} for s in _SPIN_SEGMENTS]}


def _grant_voucher(c, user_id: str, amount: float, brand: str = "KartGuard Rewards") -> str:
    """A prize voucher = a coupon the platform funds. No wallet debit — it lands as a
    scratchable boarding pass in the customer's wallet, brand-redeemable like any coupon."""
    code = "KG-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
    c.execute("INSERT INTO coupons (code, user_id, brand, amount, revealed, created_at) VALUES (?,?,?,?,0,?)",
              (code, user_id, brand, amount, _now()))
    return code


def _already_played(c, user_id: str, game: str) -> bool:
    return bool(c.execute("SELECT 1 FROM rewards_log WHERE user_id=? AND game=? AND day=?",
                          (user_id, game, _today())).fetchone())


def _daily_streak(c, user_id: str) -> int:
    """Consecutive daily check-ins ending today (or yesterday, if today is still unclaimed)."""
    from datetime import date, timedelta
    days = {r["day"] for r in c.execute(
        "SELECT day FROM rewards_log WHERE user_id=? AND game='daily' ORDER BY day DESC LIMIT 60",
        (user_id,))}
    d, streak = date.today(), 0
    if d.isoformat() not in days:
        d -= timedelta(days=1)  # today not claimed yet — count the run up to yesterday
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def games_status(user_id: str) -> dict[str, Any]:
    """Read-only state for the rewards page: what's played, streak, when things reset."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        return {
            "spin_played": _already_played(c, user_id, "spin"),
            "daily_claimed": _already_played(c, user_id, "daily"),
            "streak": _daily_streak(c, user_id),
        }


def spin_wheel(user_id: str) -> dict[str, Any]:
    """One free daily spin. Weighted, server-side; returns the landed segment index."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if _already_played(c, user_id, "spin"):
            return {"ok": False, "reason": "already_played_today"}
        idx = random.choices(range(len(_SPIN_SEGMENTS)), weights=[s[3] for s in _SPIN_SEGMENTS])[0]
        label, kind, val, _w = _SPIN_SEGMENTS[idx]
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "spin", _today(), label, _now()))
        voucher = None
        if kind == "credit":
            _accrue(c, user_id)
            c.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (val, user_id))
            _txn(c, user_id, "reward", val, note=f"Spin reward: {label}")
        elif kind == "voucher":
            voucher = _grant_voucher(c, user_id, val)
    return {"ok": True, "index": idx,
            "prize": {"label": label, "kind": kind, "value": val, "voucher_code": voucher}}


def daily_reward(user_id: str) -> dict[str, Any]:
    """A small guaranteed daily check-in credit; the streak makes coming back feel like progress."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if _already_played(c, user_id, "daily"):
            return {"ok": False, "reason": "already_claimed_today", "streak": _daily_streak(c, user_id)}
        val = random.choice([1.0, 1.0, 2.0, 3.0, 5.0])
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "daily", _today(), f"₹{val:.0f}", _now()))
        _accrue(c, user_id)
        c.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (val, user_id))
        _txn(c, user_id, "reward", val, note="Daily check-in reward")
        streak = _daily_streak(c, user_id)
    return {"ok": True, "amount": val, "streak": streak}


# --------------------------------------------------------------- ₹1 lottery: sealed tickets
# Every ticket WINS something — but the ladder is steep: the grand prize stays 1-in-30,000 while
# tiny platform-funded vouchers carry the floor, so each draw delights without bleeding money.
_TICKET_PRIZES = [  # (label, kind, value, weight) — weights sum 30,000
    ("5-star family dinner (3 people)", "grand", 1500.0, 1),        # 1 in 30,000
    ("₹100 food voucher", "voucher", 100.0, 150),                   # 0.5%
    ("₹50 food voucher", "voucher", 50.0, 1500),                    # 5%
    ("₹20 food voucher", "voucher", 20.0, 4500),                    # 15%
    ("₹10 discount voucher", "voucher", 10.0, 8849),                # ~29.5%
    ("₹5 discount voucher", "voucher", 5.0, 15000),                 # 50% — the guaranteed floor
]


def ticket_prizes() -> list[dict[str, Any]]:
    """The public prize ladder (with honest odds) shown on the lottery card."""
    total = sum(p[3] for p in _TICKET_PRIZES)
    return [{"label": p[0], "kind": p[1], "value": p[2],
             "odds": f"1 in {total // p[3]:,}" if p[3] < total // 10 else f"{p[3] * 100 // total}%"}
            for p in _TICKET_PRIZES]


def _next_draw() -> float:
    """Tickets draw at 6 PM local. RG_DRAW_DELAY_S shortens the wait for demos/tests."""
    delay = os.environ.get("RG_DRAW_DELAY_S")
    if delay is not None:
        return _now() + max(0, int(delay))
    lt = time.localtime()
    draw = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 18, 0, 0, 0, 0, -1))
    return draw if draw > _now() else draw + 86_400


def buy_ticket(user_id: str) -> dict[str, Any]:
    """₹1 buys a SEALED ticket: the outcome is drawn server-side at purchase and stored, but
    nobody (including the API) can see it before draw time — reveal is where the fun happens."""
    ensure_wallet(user_id)
    with _LOCK, _conn() as c:
        if not _debit(c, user_id, 1.0, "lottery_stake", note="Lottery ticket"):
            return {"ok": False, "reason": "insufficient balance"}
        pick = random.choices(_TICKET_PRIZES, weights=[p[3] for p in _TICKET_PRIZES])[0]
        tid = "tk_" + uuid.uuid4().hex[:10]
        code = "TKT-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
        draw_at = _next_draw()
        c.execute("INSERT INTO lottery_tickets (id, user_id, code, day, draw_at, outcome_label, "
                  "outcome_kind, outcome_value, cost, revealed, created_at) VALUES (?,?,?,?,?,?,?,?,?,0,?)",
                  (tid, user_id, code, _today(), draw_at, pick[0], pick[1], pick[2], 1.0, _now()))
        c.execute("INSERT INTO rewards_log (id, user_id, game, day, outcome, created_at) VALUES (?,?,?,?,?,?)",
                  ("rw_" + uuid.uuid4().hex[:10], user_id, "lottery", _today(), "ticket:" + code, _now()))
    return {"ok": True, "ticket": {"id": tid, "code": code, "draw_at": draw_at,
                                   "cost": 1.0, "status": "sealed"}}


def tickets(user_id: str) -> list[dict[str, Any]]:
    """The customer's tickets, newest first. Outcomes appear ONLY once revealed."""
    now = _now()
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT * FROM lottery_tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
                         (user_id,)).fetchall()
    out = []
    for r in rows:
        t = {"id": r["id"], "code": r["code"], "draw_at": r["draw_at"], "cost": r["cost"],
             "status": "revealed" if r["revealed"] else ("ready" if now >= r["draw_at"] else "sealed")}
        if r["revealed"]:
            t["prize"] = {"label": r["outcome_label"], "kind": r["outcome_kind"],
                          "value": r["outcome_value"], "voucher_code": r["voucher_code"]}
        out.append(t)
    return out


def reveal_ticket(user_id: str, ticket_id: str) -> dict[str, Any]:
    """Reveal after the draw: first call applies the prize (idempotent thereafter)."""
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM lottery_tickets WHERE id=? AND user_id=?",
                      (ticket_id, user_id)).fetchone()
        if not r:
            return {"ok": False, "reason": "not_found"}
        if _now() < r["draw_at"]:
            return {"ok": False, "reason": "draw_pending", "draw_at": r["draw_at"]}
        if r["revealed"]:
            return {"ok": True, "already": True,
                    "prize": {"label": r["outcome_label"], "kind": r["outcome_kind"],
                              "value": r["outcome_value"], "voucher_code": r["voucher_code"]}}
        brand = "KartGuard Dining" if r["outcome_kind"] == "grand" else "KartGuard Rewards"
        voucher = _grant_voucher(c, user_id, r["outcome_value"], brand=brand)
        c.execute("UPDATE lottery_tickets SET revealed=1, voucher_code=? WHERE id=?", (voucher, r["id"]))
    return {"ok": True, "prize": {"label": r["outcome_label"], "kind": r["outcome_kind"],
                                  "value": r["outcome_value"], "voucher_code": voucher}}
