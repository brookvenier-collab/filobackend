"""
Filo event log — the raw material for Shelf Intelligence.

THE PRIVACY ARCHITECTURE IS THE PRODUCT. Read this before changing anything.

Filo sells aggregate patterns to brands. That is only defensible — commercially,
legally, and morally — if individual shoppers are not merely *protected* by policy
but are *impossible to reconstruct* from the schema. So:

  * There is no user ID in this file. There is no column for one. An account
    identifier cannot be joined to a scan because it does not exist here.

  * Session IDs arrive from the app as random UUIDs that never touch the device's
    disk and are regenerated on launch and after 30 idle minutes. We store only
    HMAC(daily_salt, session_id). The salt rotates every day, so two scans by the
    same person on two different days cannot be linked — not by us, not by a
    buyer, not by anyone who compels the database.

  * No IP addresses. No GPS. No street addresses. Location granularity is a
    country code (free from the device locale, no permission prompt) plus
    whatever store the shopper volunteers.

  * Timestamps are truncated to the hour before they are stored. Not rounded at
    query time — truncated at write time, so the precise moment never lands on
    disk.

  * Prices are stored as bands, not values. "$40–60", not "$47".

Anything that cannot be expressed in this schema is a thing Filo has promised not
to know. Adding a column here is a product decision, not a technical one.
"""
import os
import hmac
import hashlib
import logging
import secrets
from datetime import datetime, timezone, date
from typing import List, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("filo.events")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Rotating-salt secret. Set EVENTS_SALT on Railway. If it is missing we generate a
# random per-process salt: sessions then become unlinkable even within a day, which
# is *more* private but makes distinct-device counts useless across restarts.
_SALT_SECRET = os.environ.get("EVENTS_SALT", "")
if not _SALT_SECRET:
    _SALT_SECRET = secrets.token_hex(32)
    log.warning("EVENTS_SALT not set — using an ephemeral per-process salt. "
                "Distinct-device counts will reset on every deploy.")

MAX_BATCH = 50

ALLOWED_EVENTS = {
    "scan_verdict",        # a scan produced a verdict
    "alternative_viewed",  # better-made options were shown
    "alternative_tapped",  # shopper tapped through to an alternative
    "verdict_shared",      # shopper shared the verdict card
    "item_saved",          # shopper saved something to the closet
}

# Price bands. Deliberately coarse — exact prices are re-identifying when combined
# with a store and an hour.
_PRICE_BANDS = [
    (0, 20, "0-20"), (20, 40, "20-40"), (40, 60, "40-60"), (60, 80, "60-80"),
    (80, 120, "80-120"), (120, 200, "120-200"), (200, 400, "200-400"),
]


def price_band(price: Optional[float]) -> Optional[str]:
    if price is None:
        return None
    for lo, hi, label in _PRICE_BANDS:
        if lo <= price < hi:
            return label
    return "400+"


def _daily_salt(when: Optional[date] = None) -> bytes:
    """A salt that changes every calendar day. Yesterday's links die with yesterday."""
    day = (when or datetime.now(timezone.utc).date()).isoformat()
    return hashlib.sha256(f"{_SALT_SECRET}:{day}".encode()).digest()


def hash_session(session_id: str) -> str:
    """One-way, salted, and the salt rotates daily. Not reversible, not linkable."""
    return hmac.new(_daily_salt(), session_id.encode(), hashlib.sha256).hexdigest()


def _truncate_to_hour(dt: Optional[datetime] = None) -> datetime:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- models

class ScanEvent(BaseModel):
    event: str
    session_id: str = Field(min_length=8, max_length=64)
    brand: Optional[str] = Field(default=None, max_length=80)
    store_hint: Optional[str] = Field(default=None, max_length=120)
    category: Optional[str] = Field(default=None, max_length=60)
    country: Optional[str] = Field(default=None, max_length=2)
    composition: Optional[str] = Field(default=None, max_length=200)
    score: Optional[float] = None
    verdict: Optional[str] = Field(default=None, max_length=40)
    price: Optional[float] = None          # converted to a band, never stored raw
    app_version: Optional[str] = Field(default=None, max_length=20)


class EventBatch(BaseModel):
    events: List[ScanEvent]


# ------------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_events (
    id             BIGSERIAL PRIMARY KEY,
    event          TEXT        NOT NULL,
    session_hash   TEXT        NOT NULL,
    occurred_hour  TIMESTAMPTZ NOT NULL,
    brand          TEXT,
    store_hint     TEXT,
    category       TEXT,
    country        TEXT,
    composition    TEXT,
    score          REAL,
    verdict        TEXT,
    price_band     TEXT,
    app_version    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS scan_events_brand_idx  ON scan_events (brand);
CREATE INDEX IF NOT EXISTS scan_events_hour_idx   ON scan_events (occurred_hour);
CREATE INDEX IF NOT EXISTS scan_events_event_idx  ON scan_events (event);
"""


def _conn():
    """Return a live connection, or None if no database is configured.

    A missing DATABASE_URL must never break a scan — analytics is strictly
    best-effort and the verdict is the product.
    """
    if not DATABASE_URL:
        return None
    try:
        import psycopg
        return psycopg.connect(DATABASE_URL, connect_timeout=5)
    except Exception as exc:            # noqa: BLE001
        log.warning("events: no database connection (%s)", exc)
        return None


def init_schema() -> bool:
    conn = _conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("events: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


def record(batch: List[ScanEvent]) -> int:
    """Write a batch. Returns the number stored. Never raises."""
    clean = [e for e in batch[:MAX_BATCH] if e.event in ALLOWED_EVENTS]
    if not clean:
        return 0

    conn = _conn()
    if conn is None:
        return 0

    hour = _truncate_to_hour()
    rows = [
        (
            e.event,
            hash_session(e.session_id),
            hour,
            (e.brand or None),
            (e.store_hint or None),
            (e.category or None),
            (e.country or None),
            (e.composition or None),
            e.score,
            (e.verdict or None),
            price_band(e.price),
            (e.app_version or None),
        )
        for e in clean
    ]

    try:
        with conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO scan_events
                    (event, session_hash, occurred_hour, brand, store_hint,
                     category, country, composition, score, verdict,
                     price_band, app_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )
        return len(rows)
    except Exception as exc:            # noqa: BLE001
        log.warning("events: insert failed (%s)", exc)
        return 0
    finally:
        conn.close()


def purge_older_than(days: int = 400) -> int:
    """Retention. Raw events expire; aggregates are what persist."""
    conn = _conn()
    if conn is None:
        return 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM scan_events WHERE created_at < NOW() - INTERVAL '%s days'"
                % int(days)
            )
            return cur.rowcount or 0
    except Exception as exc:            # noqa: BLE001
        log.warning("events: purge failed (%s)", exc)
        return 0
    finally:
        conn.close()
