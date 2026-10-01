"""
Filo Closet Fund — points, fabric tiers, orders, referrals, redemptions.

THE RULES (decided by Brooklyn, Sept 2026)
  * 100 points = $1. No cash value; redeemed as gift cards.
  * Welcome: 100 points on first sign-in.
  * Buy a Filo pick from a verdict: 3 points per $1.
  * Buy a piece saved to the Closet, through the Closet link: 5 per $1 (6 at Gold).
  * Invite a friend who then scans on 3 different days within 30 days: 200 points,
    to the person who invited ONLY. The invited friend gets nothing (App Store 3.2.2).
  * Redeem 1,000 = $10, 2,500 = $25, 5,000 = $50.
  * Purchase points are PENDING until the store confirms the sale, and reversed if
    the order is returned or cancelled.
  * Points expire after 12 months with no activity.
  * No points for ratings, reviews or turning on notifications.

FABRIC TIERS (status, not money)
  Bronze    signed in
  Silver    getting-started checklist complete
  Gold      Silver + 25 scans + 3 well-made purchases
Tiers move on habits (scans, checklist) and purchases. Only purchases, the welcome
gift and referrals add points, so what Filo pays out stays tied to real revenue.

WHAT THIS NEVER DOES
  * Touch search, scoring or ranking. catalog.py does not import this module and
    this module does not import catalog.py. A test enforces identical results
    with the fund on or off.
  * Store what was scanned. The fund keeps a scan COUNT per account (for tiers and
    referral qualification) and nothing about the garment.
  * Send the account ID to Sovrn. Each tap gets a random click reference (the
    Sovrn "cuid"); only our `clicks` table maps it back to a person.

RAILWAY VARIABLES
  FUND_PURCHASE_POINTS   "on" once Sovrn approves Filo for loyalty/rewards.
                         Off: purchases still show in Orders and count toward
                         tiers, but earn 0 points.
  SOVRN_SECRET           Sovrn secret key, for the daily transactions sync.
  FUND_AUTO_APPROVE_DAYS pending purchase points become available after this many
                         days if the store hasn't reversed them (default 90).
"""
import os
import re
import time
import string
import secrets
import logging
import threading
import urllib.parse
import urllib.request
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

import events
import affiliate

log = logging.getLogger("filo.fund")

# ------------------------------------------------------------------ settings

def purchase_points_on() -> bool:
    return os.environ.get("FUND_PURCHASE_POINTS", "").strip().lower() in {"on", "1", "true", "yes"}


SOVRN_SECRET = os.environ.get("SOVRN_SECRET", "").strip()
SOVRN_TRANSACTIONS_URL = "https://viglink.io/v1/reports/transactions"
AUTO_APPROVE_DAYS = int(os.environ.get("FUND_AUTO_APPROVE_DAYS", "90") or 90)

WELCOME_POINTS = 100
REFERRAL_POINTS = 200
REFERRAL_SCAN_DAYS = 3          # friend must scan on this many different days…
REFERRAL_WINDOW_DAYS = 30       # …within this many days of joining
REFERRAL_CODE_WINDOW_DAYS = 14  # a new member can enter an invite code this long
# No cap on how many friends someone can invite (Brooklyn, 29 Sep: grow first).
# The app says "invite 3 friends" as a goal, but every friend keeps counting.
# If abuse ever shows up, set FUND_REFERRAL_CAP on Railway (e.g. 50 per year).
MAX_REFERRALS_PER_YEAR = int(os.environ.get("FUND_REFERRAL_CAP", "0") or 0)   # 0 = unlimited
MAX_SCANS_PER_DAY = 40          # counted toward tiers; more than this is ignored
EXPIRY_DAYS = 365
REDEEM_OPTIONS = [(1000, 10), (2500, 25), (5000, 50)]

TIER_ORDER = ["bronze", "silver", "gold"]
TIER_NAMES = {"bronze": "Bronze", "silver": "Silver", "gold": "Gold"}
TIER_REQUIREMENTS = {
    "bronze": "Sign in with Apple",
    "silver": "Finish getting started",
    "gold": "25 scans and 3 well-made purchases",
}
TIER_PERKS = {
    "bronze": ["3 points per $1 on Filo picks", "5 points per $1 on Closet buys"],
    "silver": ["Choose your card", "Early access to new features"],
    "gold": ["6 points per $1 on Closet buys", "Your monthly Filo Edit (coming soon)",
             "Filo Concierge (coming later)"],
}
GOLD_SCANS, GOLD_BUYS = 25, 3
FRIENDS_GOAL = 3

SOURCES = {"v": "verdict", "c": "closet"}


# Better-made earns more (Brooklyn, 1 Oct 2026). The bonus follows Filo's own
# score of the piece — never the brand, never the price — so it can only ever
# reward the thing Filo exists for. Kept small on purpose: every point is paid
# out of a commission, and fashion commissions after Sovrn's share are
# roughly 3–7% of the order. Heirloom at Gold Closet = 8 pts/$1 = 8% back is
# the most this can ever cost; check it against real commission rates once
# the first sales come through.
QUALITY_BONUS = {"heirloom": 2, "real": 1, "base": 0}
QUALITY_BAND_NAMES = {"heirloom": "Heirloom", "real": "The real thing", "base": None}


def quality_band(score: Optional[float]) -> str:
    if score is None:
        return "base"
    if score >= 9.0:
        return "heirloom"
    if score >= 8.0:
        return "real"
    return "base"


def rate_for(source: str, tier: str, score: Optional[float] = None) -> int:
    """Points per $1 for a purchase from this source at this tier and quality."""
    if source == "c":
        base = 6 if tier == "gold" else 5
    else:
        base = 3
    return base + QUALITY_BONUS[quality_band(score)]


# The score that sets the bonus comes from the app, so it must be one Filo
# itself produced. /analyze signs each alternative's (url, score); /fund/link
# only honours a score whose signature checks out. Anything else earns the base
# rate — a tampered request can never earn more than an honest one.
def _sign_key() -> bytes:
    import accounts                       # local: avoids an import cycle at startup
    return hashlib.sha256(b"filo-score:" + accounts.SECRET).digest()


def sign_score(url: Optional[str], score: Optional[float]) -> Optional[str]:
    clean = original_url(url or "")
    if not clean or score is None:
        return None
    msg = f"{clean}|{float(score):.1f}".encode()
    return hmac.new(_sign_key(), msg, hashlib.sha256).hexdigest()[:32]


def verified_score(url: Optional[str], score: Optional[float], sig: Optional[str]) -> Optional[float]:
    want = sign_score(url, score)
    if want and sig and hmac.compare_digest(want, sig):
        return float(score)
    return None


# ------------------------------------------------------------------ schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_progress (
    account_id          UUID        PRIMARY KEY,
    invite_code         TEXT        UNIQUE NOT NULL,
    scans               INTEGER     NOT NULL DEFAULT 0,
    scan_days           INTEGER     NOT NULL DEFAULT 0,
    last_scan_day       DATE,
    scans_today         INTEGER     NOT NULL DEFAULT 0,
    prices              INTEGER     NOT NULL DEFAULT 0,
    saves               INTEGER     NOT NULL DEFAULT 0,
    shared              BOOLEAN     NOT NULL DEFAULT FALSE,
    referred_by         UUID,
    referral_awarded    BOOLEAN     NOT NULL DEFAULT FALSE,
    card_finish         TEXT,
    joined_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fund_progress_ref_idx ON fund_progress (referred_by);

CREATE TABLE IF NOT EXISTS fund_ledger (
    id          BIGSERIAL   PRIMARY KEY,
    account_id  UUID        NOT NULL,
    event       TEXT        NOT NULL,   -- welcome|purchase|referral|redeem|refund|expire|adjust
    points      INTEGER     NOT NULL,
    status      TEXT        NOT NULL,   -- pending|available|reversed
    ref         TEXT        UNIQUE NOT NULL,
    click_ref   TEXT,
    order_value NUMERIC(10,2),
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fund_ledger_acct_idx ON fund_ledger (account_id, created_at);

CREATE TABLE IF NOT EXISTS fund_clicks (
    ref         TEXT        PRIMARY KEY,   -- the Sovrn cuid
    account_id  UUID        NOT NULL,
    source      TEXT        NOT NULL,      -- v|c
    rate        INTEGER     NOT NULL,
    earns       BOOLEAN     NOT NULL,
    url         TEXT        NOT NULL,
    title       TEXT,
    store       TEXT,
    image       TEXT,
    price       NUMERIC(10,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fund_clicks_acct_idx ON fund_clicks (account_id, created_at);

CREATE TABLE IF NOT EXISTS fund_redemptions (
    id          BIGSERIAL   PRIMARY KEY,
    account_id  UUID        NOT NULL,
    points      INTEGER     NOT NULL,
    dollars     INTEGER     NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'requested',  -- requested|sent|cancelled
    email       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("fund: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


class _Tx:
    """One connection, one transaction. Commits on success, rolls back on error."""

    def __enter__(self):
        self.conn = events._conn()
        if self.conn is None:
            raise RuntimeError("no database")
        self.cur = self.conn.cursor()
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


# ------------------------------------------------------------------ small helpers

_ALNUM = string.ascii_letters + string.digits
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I confusion


def new_click_ref(source: str) -> str:
    """Sovrn cuids must be alphanumeric. 'f' + source + 14 random = unguessable."""
    return "f" + source + "".join(secrets.choice(_ALNUM) for _ in range(14))


def new_invite_code() -> str:
    return "".join(secrets.choice(_CODE_CHARS) for _ in range(6))


def clean_code(code: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())[:12]


def original_url(url: str) -> Optional[str]:
    """Accept either a store URL or one of our own affiliate redirects, and return
    the store URL. Only http(s) links out to the web are allowed."""
    if not url:
        return None
    try:
        p = urllib.parse.urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme not in {"http", "https"} or not p.netloc:
        return None
    host = p.netloc.lower()
    if host.endswith("viglink.com") or host.endswith("skimresources.com"):
        q = urllib.parse.parse_qs(p.query)
        inner = (q.get("u") or q.get("url") or [None])[0]
        return original_url(inner) if inner else None
    return url.strip()


def checklist(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"id": "signin", "title": "Sign in with Apple", "subtitle": None,
         "done": True, "points": WELCOME_POINTS},
        {"id": "scan", "title": "Scan your first care tag", "subtitle": None,
         "done": p["scans"] >= 1, "points": None},
        {"id": "price", "title": "Add a price", "subtitle": "See if it's worth it",
         "done": p["prices"] >= 1, "points": None},
        {"id": "save", "title": "Save 3 pieces to your Closet",
         "subtitle": f"{min(p['saves'], 3)} of 3 saved",
         "done": p["saves"] >= 3, "points": None},
        {"id": "invite", "title": "Invite a friend",
         "subtitle": f"{REFERRAL_POINTS} points when they start scanning",
         "done": bool(p["shared"]), "points": None},
    ]


def tier_for(p: Dict[str, Any], purchases: int) -> str:
    tier = "bronze"
    if all(c["done"] for c in checklist(p)):
        tier = "silver"
        if p["scans"] >= GOLD_SCANS and purchases >= GOLD_BUYS:
            tier = "gold"
    return tier


def next_step(p: Dict[str, Any], purchases: int, tier: str) -> Dict[str, Any]:
    """What the card says under the big number: '2 steps until Silver', and a 0–1 bar."""
    if tier == "bronze":
        items = checklist(p)
        left = sum(1 for c in items if not c["done"])
        return {"next": "silver", "count": left,
                "label": f"{left} step{'s' if left != 1 else ''} until Silver",
                "progress": round((len(items) - left) / len(items), 3)}
    if tier == "silver":
        scans_goal, buys_goal = GOLD_SCANS, GOLD_BUYS
        nxt = "gold"
        scans_left = max(scans_goal - p["scans"], 0)
        buys_left = max(buys_goal - purchases, 0)
        parts = []
        if scans_left:
            parts.append(f"{scans_left} scan{'s' if scans_left != 1 else ''}")
        if buys_left:
            parts.append(f"{buys_left} purchase{'s' if buys_left != 1 else ''}")
        done = min(p["scans"], scans_goal) / scans_goal * 0.5 + \
            min(purchases, buys_goal) / buys_goal * 0.5
        return {"next": nxt, "count": scans_left + buys_left,
                "label": (" and ".join(parts) + f" until {TIER_NAMES[nxt]}") if parts
                else f"Almost {TIER_NAMES[nxt]}",
                "progress": round(done, 3)}
    return {"next": None, "count": 0, "label": "Our highest tier", "progress": 1.0}


# ------------------------------------------------------------------ core reads

_P_COLS = ("account_id", "invite_code", "scans", "scan_days", "last_scan_day",
           "scans_today", "prices", "saves", "shared", "referred_by",
           "referral_awarded", "card_finish", "joined_at", "active_at")


def _ensure(cur, account_id: str) -> Dict[str, Any]:
    """Create the fund row and the welcome gift on first visit. Idempotent."""
    cur.execute("SELECT " + ",".join(_P_COLS) + " FROM fund_progress WHERE account_id=%s FOR UPDATE",
                (account_id,))
    row = cur.fetchone()
    if row is None:
        for _ in range(5):
            code = new_invite_code()
            cur.execute("SELECT 1 FROM fund_progress WHERE invite_code=%s", (code,))
            if cur.fetchone() is None:
                break
        cur.execute("INSERT INTO fund_progress (account_id, invite_code) VALUES (%s,%s) "
                    "ON CONFLICT (account_id) DO NOTHING", (account_id, code))
        cur.execute("INSERT INTO fund_ledger (account_id, event, points, status, ref, note) "
                    "VALUES (%s,'welcome',%s,'available',%s,'Welcome to Filo') "
                    "ON CONFLICT (ref) DO NOTHING",
                    (account_id, WELCOME_POINTS, f"welcome:{account_id}"))
        cur.execute("SELECT " + ",".join(_P_COLS) + " FROM fund_progress WHERE account_id=%s FOR UPDATE",
                    (account_id,))
        row = cur.fetchone()
    p = dict(zip(_P_COLS, row))
    p["account_id"] = str(p["account_id"])
    return p


def _purchases(cur, account_id: str) -> int:
    cur.execute("SELECT COUNT(*) FROM fund_ledger WHERE account_id=%s AND event='purchase' "
                "AND status IN ('pending','available')", (account_id,))
    return cur.fetchone()[0]


def _balances(cur, account_id: str):
    cur.execute("""SELECT
          COALESCE(SUM(points) FILTER (WHERE status='available'),0),
          COALESCE(SUM(points) FILTER (WHERE status='pending' AND points>0),0)
        FROM fund_ledger WHERE account_id=%s""", (account_id,))
    return cur.fetchone()


def _tier(cur, p):
    return tier_for(p, _purchases(cur, p["account_id"]))


def summary_for(account_id: str) -> Dict[str, Any]:
    with _Tx() as cur:
        p = _ensure(cur, account_id)
        purchases = _purchases(cur, account_id)
        available, pending = _balances(cur, account_id)
        tier = tier_for(p, purchases)
        step = next_step(p, purchases, tier)
        items = checklist(p)

        cur.execute("""SELECT COUNT(*) FILTER (WHERE referral_awarded),
                              COUNT(*) FILTER (WHERE NOT referral_awarded)
                       FROM fund_progress WHERE referred_by=%s""", (account_id,))
        ref_done, ref_waiting = cur.fetchone()

        cur.execute("""SELECT event, points, status, note, created_at FROM fund_ledger
                       WHERE account_id=%s ORDER BY created_at DESC, id DESC LIMIT 30""",
                    (account_id,))
        history = [{"event": e, "points": pts, "status": s, "title": n or e.title(),
                    "date": c.date().isoformat()} for e, pts, s, n, c in cur.fetchall()]

    t_idx = TIER_ORDER.index(tier)
    finish = p["card_finish"] if p["card_finish"] in TIER_ORDER[:t_idx + 1] else tier
    joined = p["joined_at"]
    can_enter = (p["referred_by"] is None and
                 datetime.now(timezone.utc) - joined < timedelta(days=REFERRAL_CODE_WINDOW_DAYS))
    return {
        "points_available": int(available),
        "points_pending": int(pending),
        "dollars_available": round(int(available) / 100, 2),
        "tier": tier,
        "tier_name": TIER_NAMES[tier],
        "card_finish": finish,
        "next": step,
        "tiers": [{"id": t, "name": TIER_NAMES[t], "unlocked": i <= t_idx,
                   "requirement": TIER_REQUIREMENTS[t], "perks": TIER_PERKS[t]}
                  for i, t in enumerate(TIER_ORDER)],
        "checklist": items,
        "checklist_done": sum(1 for c in items if c["done"]),
        "rates": {"verdict": rate_for("v", tier), "closet": rate_for("c", tier),
                  "real_thing_bonus": QUALITY_BONUS["real"],
                  "heirloom_bonus": QUALITY_BONUS["heirloom"]},
        "purchase_points_on": purchase_points_on(),
        "redeem_options": [{"points": pts, "dollars": d, "available": available >= pts}
                           for pts, d in REDEEM_OPTIONS],
        "invite_code": p["invite_code"],
        "referrals": {"qualified": ref_done, "waiting": ref_waiting,
                      "points_each": REFERRAL_POINTS},
        "can_enter_code": can_enter,
        "stats": {"scans": p["scans"], "purchases": purchases},
        "goals": {"activity": len(items), "purchases": GOLD_BUYS,
                  "scans": GOLD_SCANS, "friends": FRIENDS_GOAL},
        "history": history,
    }


# ------------------------------------------------------------------ progress events

class ProgressEvent(BaseModel):
    event: str                                   # scan|price|save|share
    count: Optional[int] = Field(default=None, ge=0, le=100000)


def record_progress(account_id: str, ev: ProgressEvent) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    with _Tx() as cur:
        p = _ensure(cur, account_id)
        if ev.event == "scan":
            new_day = p["last_scan_day"] != today
            scans_today = 1 if new_day else p["scans_today"] + 1
            counted = 1 if scans_today <= MAX_SCANS_PER_DAY else 0
            cur.execute("""UPDATE fund_progress SET scans=scans+%s,
                             scan_days=scan_days+%s, last_scan_day=%s, scans_today=%s,
                             active_at=NOW() WHERE account_id=%s""",
                        (counted, 1 if new_day else 0, today, scans_today, account_id))
            if new_day:
                _maybe_award_referral(cur, account_id)
        elif ev.event == "price":
            cur.execute("UPDATE fund_progress SET prices=prices+1, active_at=NOW() WHERE account_id=%s",
                        (account_id,))
        elif ev.event == "save":
            # The app sends how many pieces are in the Closet now.
            n = ev.count if ev.count is not None else p["saves"] + 1
            cur.execute("UPDATE fund_progress SET saves=GREATEST(saves,%s), active_at=NOW() "
                        "WHERE account_id=%s", (n, account_id))
        elif ev.event == "share":
            cur.execute("UPDATE fund_progress SET shared=TRUE, active_at=NOW() WHERE account_id=%s",
                        (account_id,))
        else:
            raise ValueError("unknown event")
    return {"ok": True}


def _maybe_award_referral(cur, invitee_id: str):
    cur.execute("""SELECT referred_by, referral_awarded, scan_days, joined_at
                   FROM fund_progress WHERE account_id=%s""", (invitee_id,))
    row = cur.fetchone()
    if not row:
        return
    referrer, awarded, days, joined = row
    if referrer is None or awarded or days < REFERRAL_SCAN_DAYS:
        return
    if datetime.now(timezone.utc) - joined > timedelta(days=REFERRAL_WINDOW_DAYS):
        return
    if MAX_REFERRALS_PER_YEAR > 0:
        cur.execute("""SELECT COUNT(*) FROM fund_ledger WHERE account_id=%s AND event='referral'
                       AND created_at > NOW() - INTERVAL '365 days'""", (referrer,))
        if cur.fetchone()[0] >= MAX_REFERRALS_PER_YEAR:
            return
    cur.execute("SELECT 1 FROM fund_progress WHERE account_id=%s", (referrer,))
    if cur.fetchone() is None:          # referrer deleted their account
        return
    cur.execute("""INSERT INTO fund_ledger (account_id, event, points, status, ref, note)
                   VALUES (%s,'referral',%s,'available',%s,'A friend joined Filo')
                   ON CONFLICT (ref) DO NOTHING""",
                (str(referrer), REFERRAL_POINTS, f"referral:{invitee_id}"))
    cur.execute("UPDATE fund_progress SET referral_awarded=TRUE WHERE account_id=%s", (invitee_id,))
    cur.execute("UPDATE fund_progress SET active_at=NOW() WHERE account_id=%s", (str(referrer),))


class ReferralRequest(BaseModel):
    code: str = Field(min_length=3, max_length=20)


def enter_referral(account_id: str, code: str) -> Dict[str, Any]:
    code = clean_code(code)
    with _Tx() as cur:
        p = _ensure(cur, account_id)
        if p["referred_by"] is not None:
            raise ValueError("You've already added an invite code.")
        if datetime.now(timezone.utc) - p["joined_at"] > timedelta(days=REFERRAL_CODE_WINDOW_DAYS):
            raise ValueError("Invite codes can only be added in your first two weeks.")
        cur.execute("SELECT account_id FROM fund_progress WHERE invite_code=%s", (code,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("That code doesn't match anyone. Check it and try again.")
        if str(row[0]) == account_id:
            raise ValueError("That's your own code.")
        cur.execute("UPDATE fund_progress SET referred_by=%s WHERE account_id=%s",
                    (str(row[0]), account_id))
        _maybe_award_referral(cur, account_id)
    return {"ok": True}


# ------------------------------------------------------------------ shop links

class LinkRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4000)
    source: str = "v"                         # v = from a verdict, c = from the Closet
    title: Optional[str] = Field(default=None, max_length=200)
    store: Optional[str] = Field(default=None, max_length=120)
    image: Optional[str] = Field(default=None, max_length=2000)
    price: Optional[float] = Field(default=None, ge=0, le=100000)
    # v13: the alternative's Filo score and the signature /analyze gave it.
    # The app stores both with a saved piece so Closet buys earn the bonus too.
    score: Optional[float] = Field(default=None, ge=0, le=10)
    score_sig: Optional[str] = Field(default=None, max_length=64)


def make_link(account_id: Optional[str], req: LinkRequest) -> Dict[str, Any]:
    """Called on a REAL tap only — the app never pre-fetches these. Returns where
    to send the shopper. Signed out, or no database: a plain affiliate link."""
    url = original_url(req.url)
    if not url:
        raise ValueError("Not a shop link.")
    source = req.source if req.source in SOURCES else "v"
    if not account_id or not affiliate.enabled():
        return {"url": affiliate.wrap(url, "filo"), "tracked": False}
    ref = new_click_ref(source)
    try:
        with _Tx() as cur:
            p = _ensure(cur, account_id)
            tier = _tier(cur, p)
            score = verified_score(req.url, req.score, req.score_sig)
            rate = rate_for(source, tier, score)
            cur.execute("""INSERT INTO fund_clicks (ref, account_id, source, rate, earns, url,
                                                    title, store, image, price)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (ref, account_id, source, rate, purchase_points_on(), url,
                         req.title, req.store, req.image, req.price))
            cur.execute("UPDATE fund_progress SET active_at=NOW() WHERE account_id=%s", (account_id,))
    except Exception as exc:            # noqa: BLE001  never block a shopper from the store
        log.warning("fund: click not recorded (%s)", exc)
        return {"url": affiliate.wrap(url, "filo"), "tracked": False}
    band = quality_band(score)
    return {"url": affiliate.wrap(url, "filo", cuid=ref), "tracked": True,
            "points_per_dollar": rate if purchase_points_on() else 0,
            "quality_bonus": QUALITY_BONUS[band] if purchase_points_on() else 0,
            "quality_band": QUALITY_BAND_NAMES[band]}


# ------------------------------------------------------------------ purchases

def _status_from(raw: Dict[str, Any]) -> str:
    s = str(raw.get("status") or raw.get("commissionStatus") or "").lower()
    try:
        revenue = float(raw.get("publisherNetRevenue") or raw.get("commission") or 0)
    except (TypeError, ValueError):
        revenue = 0.0
    if revenue < 0 or any(w in s for w in ("reject", "cancel", "revers", "void", "declin", "return")):
        return "reversed"
    if any(w in s for w in ("approv", "paid", "confirm", "lock", "final")):
        return "available"
    return "pending"


def _cuid_of(raw: Dict[str, Any]) -> Optional[str]:
    for k in ("cuid", "CUID", "clickCuid", "subId", "subid", "trackingId"):
        v = raw.get(k)
        if v:
            return str(v)
    return None


def record_purchase(cuid: str, commission_id: str, order_value: float, status: str) -> str:
    """Insert or update one purchase. Returns what happened, for the sync report.

    A reversed purchase stays reversed. Points = order value x the rate locked in
    at the tap, and 0 if purchase points were off when the shopper tapped."""
    if not cuid or not cuid.startswith("f"):
        return "not-ours"
    ref = f"sovrn:{commission_id}"
    with _Tx() as cur:
        cur.execute("SELECT account_id, rate, earns, title FROM fund_clicks WHERE ref=%s", (cuid,))
        click = cur.fetchone()
        if click is None:
            return "no-click"
        account_id, rate, earns, title = click
        points = int(round(max(order_value, 0) * rate)) if earns else 0
        cur.execute("SELECT status FROM fund_ledger WHERE ref=%s FOR UPDATE", (ref,))
        row = cur.fetchone()
        if row is None:
            cur.execute("""INSERT INTO fund_ledger (account_id, event, points, status, ref,
                                                    click_ref, order_value, note)
                           VALUES (%s,'purchase',%s,%s,%s,%s,%s,%s)""",
                        (str(account_id), points, status, ref, cuid, order_value,
                         title or "Purchase"))
            cur.execute("UPDATE fund_progress SET active_at=NOW() WHERE account_id=%s",
                        (str(account_id),))
            return "added"
        if row[0] == "reversed" or row[0] == status:
            return "unchanged"
        if row[0] == "available" and status == "pending":
            return "unchanged"
        cur.execute("UPDATE fund_ledger SET status=%s, updated_at=NOW() WHERE ref=%s",
                    (status, ref))
        return status


def auto_approve() -> int:
    """Pending purchases the store hasn't reversed within AUTO_APPROVE_DAYS become
    available. Stores confirm most sales within 60 days."""
    with _Tx() as cur:
        cur.execute("""UPDATE fund_ledger SET status='available', updated_at=NOW()
                       WHERE event='purchase' AND status='pending'
                       AND created_at < NOW() - make_interval(days => %s)""",
                    (AUTO_APPROVE_DAYS,))
        return cur.rowcount


def expire_inactive() -> int:
    """Accounts with no activity for 12 months lose their available points."""
    with _Tx() as cur:
        cur.execute("""
          SELECT p.account_id, COALESCE(SUM(l.points) FILTER (WHERE l.status='available'),0)
          FROM fund_progress p LEFT JOIN fund_ledger l ON l.account_id=p.account_id
          WHERE p.active_at < NOW() - make_interval(days => %s)
          GROUP BY p.account_id""", (EXPIRY_DAYS,))
        n = 0
        for account_id, bal in cur.fetchall():
            if bal > 0:
                cur.execute("""INSERT INTO fund_ledger (account_id, event, points, status, ref, note)
                               VALUES (%s,'expire',%s,'available',%s,'Points expired')
                               ON CONFLICT (ref) DO NOTHING""",
                            (str(account_id), -int(bal),
                             f"expire:{account_id}:{date.today().isoformat()}"))
                n += 1
        return n


def sovrn_sync(day: Optional[str] = None) -> Dict[str, Any]:
    """Pull one day of Sovrn transactions (by update date) and apply them.
    Sovrn allows one request a minute, so this asks for one day at a time."""
    if not SOVRN_SECRET:
        return {"ok": False, "reason": "SOVRN_SECRET is not set"}
    day = day or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    url = SOVRN_TRANSACTIONS_URL + "?" + urllib.parse.urlencode({"updateDate": day})
    req = urllib.request.Request(url, headers={"Authorization": f"secret {SOVRN_SECRET}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode() or "[]")
    rows = data
    if isinstance(data, dict):
        rows = next((v for v in data.values() if isinstance(v, list)), [])
    report = {"ok": True, "day": day, "rows": len(rows)}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            value = float(raw.get("orderValue") or raw.get("saleAmount") or 0)
        except (TypeError, ValueError):
            value = 0.0
        cid = raw.get("commissionId") or raw.get("transactionId") or raw.get("id")
        if not cid:
            continue
        outcome = record_purchase(_cuid_of(raw) or "", str(cid), value, _status_from(raw))
        report[outcome] = report.get(outcome, 0) + 1
    return report


def run_daily():
    """Everything the daily job does. Each part is independent."""
    out = {}
    for name, fn in (("sync", sovrn_sync), ("auto_approve", auto_approve),
                     ("expired", expire_inactive)):
        try:
            out[name] = fn()
        except Exception as exc:        # noqa: BLE001
            out[name] = f"failed: {exc.__class__.__name__}"
            log.warning("fund: %s failed (%s)", name, exc)
    return out


def start_daily_thread():
    """Runs the daily job about once a day while the server is up."""
    if not events.DATABASE_URL:
        return

    def loop():
        time.sleep(120)
        while True:
            log.info("fund daily: %s", run_daily())
            time.sleep(24 * 3600)

    threading.Thread(target=loop, name="fund-daily", daemon=True).start()


# ------------------------------------------------------------------ orders

def orders_for(account_id: str) -> Dict[str, Any]:
    with _Tx() as cur:
        cur.execute("""
          SELECT l.status, l.points, l.order_value, l.created_at, l.updated_at,
                 c.title, c.store, c.image, c.source, c.url, c.earns
          FROM fund_ledger l LEFT JOIN fund_clicks c ON c.ref=l.click_ref
          WHERE l.account_id=%s AND l.event='purchase'
          ORDER BY l.created_at DESC LIMIT 50""", (account_id,))
        bought = cur.fetchall()
        cur.execute("""
          SELECT c.title, c.store, c.image, c.source, c.url, c.price, c.created_at
          FROM fund_clicks c
          WHERE c.account_id=%s AND c.created_at > NOW() - INTERVAL '14 days'
            AND NOT EXISTS (SELECT 1 FROM fund_ledger l WHERE l.click_ref=c.ref)
          ORDER BY c.created_at DESC LIMIT 10""", (account_id,))
        tapped = cur.fetchall()

    stage = {"pending": 2, "available": 4, "reversed": 0}
    orders = []
    for status, pts, value, created, updated, title, store, image, source, url, earns in bought:
        orders.append({
            "title": title or "Purchase", "store": store, "image": image,
            "source": SOURCES.get(source or "v", "verdict"), "url": url,
            "points": pts, "earned": bool(earns),
            "status": "returned" if status == "reversed" else status,
            "stage": stage.get(status, 2),
            "order_value": float(value) if value is not None else None,
            "date": created.date().isoformat(),
        })
    for title, store, image, source, url, price, created in tapped:
        orders.append({
            "title": title or "Shop link", "store": store, "image": image,
            "source": SOURCES.get(source, "verdict"), "url": url, "points": None,
            "earned": False, "status": "tapped", "stage": 1,
            "order_value": float(price) if price is not None else None,
            "date": created.date().isoformat(),
        })
    orders.sort(key=lambda o: o["date"], reverse=True)
    return {"orders": orders,
            "note": "Stores confirm purchases in about 60 days. Returns are taken back out."}


# ------------------------------------------------------------------ card finish

class CardRequest(BaseModel):
    finish: str


def set_card(account_id: str, finish: str) -> Dict[str, Any]:
    with _Tx() as cur:
        p = _ensure(cur, account_id)
        tier = _tier(cur, p)
        allowed = TIER_ORDER[:TIER_ORDER.index(tier) + 1]
        if tier == "bronze":
            raise ValueError("Reach Silver to choose your card.")
        if finish not in allowed:
            raise ValueError("That fabric isn't unlocked yet.")
        cur.execute("UPDATE fund_progress SET card_finish=%s WHERE account_id=%s",
                    (finish, account_id))
    return {"ok": True, "card_finish": finish}


# ------------------------------------------------------------------ redemptions

class RedeemRequest(BaseModel):
    points: int


def redeem(account_id: str, points: int, email: Optional[str]) -> Dict[str, Any]:
    options = dict(REDEEM_OPTIONS)
    if points not in options:
        raise ValueError("Choose $10, $25 or $50.")
    with _Tx() as cur:
        _ensure(cur, account_id)               # locks this member's row: no double spend
        available, _ = _balances(cur, account_id)
        if available < points:
            raise ValueError("Not enough points yet.")
        cur.execute("""INSERT INTO fund_redemptions (account_id, points, dollars, email)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (account_id, points, options[points], email))
        rid = cur.fetchone()[0]
        cur.execute("""INSERT INTO fund_ledger (account_id, event, points, status, ref, note)
                       VALUES (%s,'redeem',%s,'available',%s,%s)""",
                    (account_id, -points, f"redeem:{rid}", f"${options[points]} gift card"))
        cur.execute("UPDATE fund_progress SET active_at=NOW() WHERE account_id=%s", (account_id,))
    return {"ok": True, "redemption_id": rid, "dollars": options[points],
            "message": "We'll email your gift card within 5 business days."}


def list_redemptions(status: str = "requested") -> List[Dict[str, Any]]:
    with _Tx() as cur:
        cur.execute("""SELECT id, account_id, points, dollars, status, email, created_at
                       FROM fund_redemptions WHERE status=%s ORDER BY created_at""", (status,))
        return [{"id": i, "account_id": str(a), "points": p, "dollars": d, "status": s,
                 "email": e, "requested": c.isoformat()} for i, a, p, d, s, e, c in cur.fetchall()]


def resolve_redemption(rid: int, status: str) -> Dict[str, Any]:
    if status not in {"sent", "cancelled"}:
        raise ValueError("status must be sent or cancelled")
    with _Tx() as cur:
        cur.execute("SELECT account_id, points, status FROM fund_redemptions WHERE id=%s FOR UPDATE",
                    (rid,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("no such redemption")
        account_id, points, current = row
        if current != "requested":
            return {"ok": True, "status": current}
        cur.execute("UPDATE fund_redemptions SET status=%s, updated_at=NOW() WHERE id=%s",
                    (status, rid))
        if status == "cancelled":
            cur.execute("""INSERT INTO fund_ledger (account_id, event, points, status, ref, note)
                           VALUES (%s,'refund',%s,'available',%s,'Gift card cancelled, points returned')
                           ON CONFLICT (ref) DO NOTHING""",
                        (str(account_id), points, f"refund:{rid}"))
    return {"ok": True, "status": status}


def admin_summary() -> Dict[str, Any]:
    with _Tx() as cur:
        cur.execute("SELECT COUNT(*) FROM fund_progress")
        members = cur.fetchone()[0]
        cur.execute("""SELECT
            COALESCE(SUM(points) FILTER (WHERE status='available'),0),
            COALESCE(SUM(points) FILTER (WHERE status='pending' AND points>0),0),
            COUNT(*) FILTER (WHERE event='purchase' AND status<>'reversed'),
            COALESCE(SUM(order_value) FILTER (WHERE event='purchase' AND status<>'reversed'),0)
          FROM fund_ledger""")
        avail, pend, buys, sales = cur.fetchone()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(dollars),0) FROM fund_redemptions WHERE status='requested'")
        req_n, req_d = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM fund_clicks WHERE created_at > NOW() - INTERVAL '30 days'")
        clicks30 = cur.fetchone()[0]
    return {
        "members": members,
        "points_outstanding": int(avail), "dollars_outstanding": round(int(avail) / 100, 2),
        "points_pending": int(pend),
        "purchases": buys, "sales_value": float(sales),
        "taps_last_30_days": clicks30,
        "gift_cards_to_send": req_n, "gift_card_dollars_to_send": int(req_d),
        "purchase_points_on": purchase_points_on(),
    }


class ManualPurchase(BaseModel):
    cuid: str
    commission_id: str
    order_value: float = Field(ge=0, le=100000)
    status: str = "pending"


# ------------------------------------------------------------------ account deletion

def delete_account(account_id: str) -> None:
    """Called when a member deletes their Filo account. Everything goes."""
    try:
        with _Tx() as cur:
            for table in ("fund_ledger", "fund_clicks", "fund_redemptions", "fund_progress"):
                cur.execute(f"DELETE FROM {table} WHERE account_id=%s", (account_id,))
            cur.execute("UPDATE fund_progress SET referred_by=NULL WHERE referred_by=%s",
                        (account_id,))
    except Exception as exc:            # noqa: BLE001
        log.warning("fund: delete failed (%s)", exc)
