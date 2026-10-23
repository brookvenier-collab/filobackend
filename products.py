"""
Filo product database (v29, 16 Oct 2026). The Yuka model, for clothes.

Every scan that comes with a barcode teaches Filo about that product: its brand,
name, garment type, fabric, where it was made, and the registration number on
its care label. The next shopper who scans the same barcode gets the verdict
straight away, without reading the tag.

WHAT THIS STORES, AND WHAT IT NEVER DOES
Products are facts about a garment, not about a person. This module stores no
account ID, no device ID, no location finer than the country, no price, and no
timestamp finer than a day. To stop one phone voting the same fact up many
times, each vote carries HMAC(today's salt, session) from events.py. The salt
rotates daily, so a vote cannot be linked to the same person's other scans, on
any other day, by anyone. The trade-off, written down on purpose: Filo can't
ban a bad contributor across days. Consensus does that job instead.

HOW A FACT BECOMES TRUSTED (modelled on Yuka and Open Food Facts)
- Every field is voted on separately. The top value is shown.
- "confirmed" needs at least CONFIRM_VOTES independent votes for the top value,
  and at least CONFIRM_SHARE of all votes on that field.
- Below that a product is "unconfirmed": the app can show it, labelled.
- "locked" (set by Filo, e.g. brand-supplied data) can't be changed by votes.
- Checks before a vote counts: a valid GTIN check digit; fabric that parses and
  sums to about 100%; short, clean text.

REGISTRATION NUMBERS (RN in the US, CA in Canada)
Care labels often carry one instead of a company name. Neither the FTC nor the
Competition Bureau offers a bulk download or API, so Filo keeps its own cache
(rn_registrants), filled by hand or after asking them for bulk access. A
registrant is often the importer or manufacturer, not the brand, so the app
says "Registered to", never "Brand".
"""
import re
import hmac
import hashlib
import logging
from datetime import date
from typing import Optional, Dict, Any, List

import events
import fabric

log = logging.getLogger("filo.products")

CONFIRM_VOTES = 2
CONFIRM_SHARE = 2 / 3
# v41: image, url and source, filled from barcode lookups and retailer feeds.
FIELDS = ("brand", "name", "category", "composition", "made_in", "rn", "ca_number",
          "image", "url", "source")
MAX_LEN = {"brand": 80, "name": 120, "category": 60, "composition": 200,
           "made_in": 60, "rn": 8, "ca_number": 8, "image": 500, "url": 500, "source": 40}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    gtin         TEXT PRIMARY KEY,
    locked       BOOLEAN NOT NULL DEFAULT FALSE,
    first_day    DATE NOT NULL DEFAULT CURRENT_DATE,
    last_day     DATE NOT NULL DEFAULT CURRENT_DATE,
    scans        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_facts (
    gtin   TEXT NOT NULL REFERENCES products(gtin) ON DELETE CASCADE,
    field  TEXT NOT NULL,
    value  TEXT NOT NULL,
    votes  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (gtin, field, value)
);
CREATE TABLE IF NOT EXISTS product_votes (
    gtin        TEXT NOT NULL,
    field       TEXT NOT NULL,
    voter_hash  TEXT NOT NULL,
    day         DATE NOT NULL,
    PRIMARY KEY (gtin, field, voter_hash)
);
CREATE INDEX IF NOT EXISTS product_votes_day_idx ON product_votes (day);
CREATE TABLE IF NOT EXISTS rn_registrants (
    kind        TEXT NOT NULL,          -- 'RN' (US FTC) or 'CA' (Canada)
    number      TEXT NOT NULL,
    registrant  TEXT NOT NULL,
    role        TEXT,                   -- manufacturer / importer / retailer, if known
    checked_day DATE NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (kind, number)
);
"""


# ------------------------------------------------------------------ validation

_STORE_CODE = re.compile(r"^C:([A-Z0-9]{6,24})$")


def normalize_gtin(raw) -> Optional[str]:
    """A valid UPC-A / EAN-8 / EAN-13 / GTIN-14 as 14 digits, else None.

    v41: a store's own price-tag code (Code 128), sent by the app as "C:<code>",
    is kept as "C:<CODE>" so Filo's database can still learn it."""
    m = _STORE_CODE.match(str(raw or "").strip().upper())
    if m:
        return "C:" + m.group(1)
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) not in (8, 12, 13, 14):
        return None
    digits = digits.zfill(14)
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    if (10 - total % 10) % 10 != check:
        return None
    if set(digits) == {"0"}:
        return None
    return digits


def canonical_composition(text) -> Optional[str]:
    """'60% cotton, 40% polyester' (largest first), or None if it doesn't
    parse into numbers that add up to about 100%."""
    pairs = fabric.parse_composition(text or "", allow_bare=False)
    if not pairs:
        return None
    total = sum(p for _, p in pairs)
    if not 95 <= total <= 105:
        return None
    pairs = sorted(pairs, key=lambda x: (-x[1], x[0]))
    return ", ".join(f"{p}% {f}" for f, p in pairs)


_RN = re.compile(r"\bRN\s*#?\s*:?\s*(\d{4,6})\b", re.I)
_CA = re.compile(r"\bCA\s*#?\s*:?\s*(\d{5})\b", re.I)
_MADE_IN = re.compile(r"\bmade\s+in\s+([a-z][a-z .'-]{1,38}?[a-z])"
                      r"(?=\s*(?:$|[\d,.;/|\n%()])|\s+(?:rn|ca)\b)", re.I)


def from_tag(tag_text) -> Dict[str, str]:
    """What a care label says beyond the fibre: RN, CA number, country made."""
    t = tag_text or ""
    out = {}
    m = _RN.search(t)
    if m:
        out["rn"] = m.group(1).lstrip("0") or "0"
    m = _CA.search(t)
    if m:
        out["ca_number"] = m.group(1)
    m = _MADE_IN.search(t)
    if m:
        out["made_in"] = m.group(1).strip().title()
    return out


def _clean(field, value) -> Optional[str]:
    if value is None:
        return None
    if field == "composition":
        return canonical_composition(value)
    v = re.sub(r"\s+", " ", str(value)).strip()
    if not v or len(v) > MAX_LEN[field]:
        return None
    if field in ("rn", "ca_number"):
        return v if v.isdigit() else None
    if field in ("image", "url"):
        return v if v.startswith("https://") and " " not in v else None
    if field == "category":
        return v.lower()
    return v


def voter_hash(session_id) -> Optional[str]:
    """HMAC(today's salt, session). Unlinkable across days by design."""
    if not session_id or len(str(session_id)) < 8:
        return None
    return events.hash_session(str(session_id))


# --------------------------------------------------------------------- storage

class MemoryStore:
    """Same behaviour as the Postgres store, in memory. Used when no database
    is configured, and by the tests."""

    def __init__(self):
        self.products, self.facts, self.votes, self.reg = {}, {}, set(), {}

    def ensure(self, gtin):
        p = self.products.setdefault(gtin, {"locked": False, "scans": 0,
                                            "first_day": date.today(),
                                            "last_day": date.today()})
        return p

    def touch(self, gtin):
        p = self.ensure(gtin)
        p["scans"] += 1
        p["last_day"] = date.today()

    def vote(self, gtin, field, value, voter):
        key = (gtin, field, voter)
        if key in self.votes:
            return False
        self.votes.add(key)
        self.facts[(gtin, field, value)] = self.facts.get((gtin, field, value), 0) + 1
        return True

    def get(self, gtin):
        if gtin not in self.products:
            return None
        facts = {}
        for (g, f, v), n in self.facts.items():
            if g == gtin:
                facts.setdefault(f, []).append((v, n))
        return dict(self.products[gtin]), facts

    def set_locked(self, gtin, field, value):
        self.ensure(gtin)["locked"] = True
        for k in [k for k in self.facts if k[0] == gtin and k[1] == field]:
            del self.facts[k]
        self.facts[(gtin, field, value)] = 10 ** 6

    def registrant(self, kind, number):
        return self.reg.get((kind, number))

    def set_registrant(self, kind, number, name, role=None):
        self.reg[(kind, number)] = {"registrant": name, "role": role}

    def brands(self):
        out = {}
        for (g, f, v), n in self.facts.items():
            if f == "brand":
                out.setdefault(g, []).append((v, n))
        return out


class PostgresStore:
    def _run(self, sql, params=(), fetch=False):
        conn = events._conn()
        if conn is None:
            return None
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else cur.rowcount
        except Exception as exc:            # noqa: BLE001
            log.warning("products: query failed (%s)", exc)
            return None
        finally:
            conn.close()

    def touch(self, gtin):
        self._run("INSERT INTO products (gtin, scans) VALUES (%s, 1) "
                  "ON CONFLICT (gtin) DO UPDATE SET scans = products.scans + 1, "
                  "last_day = CURRENT_DATE", (gtin,))

    def vote(self, gtin, field, value, voter):
        conn = events._conn()
        if conn is None:
            return False
        try:
            with conn, conn.cursor() as cur:
                cur.execute("INSERT INTO products (gtin) VALUES (%s) ON CONFLICT DO NOTHING",
                            (gtin,))
                cur.execute("INSERT INTO product_votes (gtin, field, voter_hash, day) "
                            "VALUES (%s, %s, %s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                            (gtin, field, voter))
                if cur.rowcount == 0:
                    return False
                cur.execute("INSERT INTO product_facts (gtin, field, value, votes) "
                            "VALUES (%s, %s, %s, 1) ON CONFLICT (gtin, field, value) "
                            "DO UPDATE SET votes = product_facts.votes + 1",
                            (gtin, field, value))
            return True
        except Exception as exc:            # noqa: BLE001
            log.warning("products: vote failed (%s)", exc)
            return False
        finally:
            conn.close()

    def get(self, gtin):
        rows = self._run("SELECT locked, scans, first_day, last_day FROM products "
                         "WHERE gtin = %s", (gtin,), fetch=True)
        if not rows:
            return None
        locked, scans, first, last = rows[0]
        facts = {}
        for f, v, n in self._run("SELECT field, value, votes FROM product_facts "
                                 "WHERE gtin = %s", (gtin,), fetch=True) or []:
            facts.setdefault(f, []).append((v, n))
        return {"locked": locked, "scans": scans, "first_day": first,
                "last_day": last}, facts

    def set_locked(self, gtin, field, value):
        self._run("INSERT INTO products (gtin, locked) VALUES (%s, TRUE) "
                  "ON CONFLICT (gtin) DO UPDATE SET locked = TRUE", (gtin,))
        self._run("DELETE FROM product_facts WHERE gtin = %s AND field = %s", (gtin, field))
        self._run("INSERT INTO product_facts (gtin, field, value, votes) "
                  "VALUES (%s, %s, %s, 1000000)", (gtin, field, value))

    def registrant(self, kind, number):
        rows = self._run("SELECT registrant, role FROM rn_registrants "
                         "WHERE kind = %s AND number = %s", (kind, number), fetch=True)
        return {"registrant": rows[0][0], "role": rows[0][1]} if rows else None

    def set_registrant(self, kind, number, name, role=None):
        self._run("INSERT INTO rn_registrants (kind, number, registrant, role) "
                  "VALUES (%s, %s, %s, %s) ON CONFLICT (kind, number) DO UPDATE "
                  "SET registrant = EXCLUDED.registrant, role = EXCLUDED.role, "
                  "checked_day = CURRENT_DATE", (kind, number, name, role))

    def brands(self):
        out = {}
        for g, v, n in self._run("SELECT gtin, value, votes FROM product_facts "
                                 "WHERE field = 'brand'", fetch=True) or []:
            out.setdefault(g, []).append((v, n))
        return out


_memory = MemoryStore()


def store():
    return PostgresStore() if events.DATABASE_URL else _memory


def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("products: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


def purge_votes(days: int = 2) -> int:
    """Vote hashes are only needed to stop same-day double votes. Drop them."""
    if not events.DATABASE_URL:
        return 0
    n = PostgresStore()._run("DELETE FROM product_votes WHERE day < CURRENT_DATE - %s",
                             (days,))
    return n or 0


# ---------------------------------------------------------------------- logic

def _decide(values, locked):
    """(value, votes, total, status) for one field's list of (value, votes)."""
    if not values:
        return None
    values = sorted(values, key=lambda x: (-x[1], x[0]))
    top, n = values[0]
    total = sum(v for _, v in values)
    if locked or n >= 10 ** 6:
        status = "locked"
    elif n >= CONFIRM_VOTES and n / total >= CONFIRM_SHARE:
        status = "confirmed"
    else:
        status = "unconfirmed"
    return top, min(n, 10 ** 6), total, status


def contribute(gtin, fields: Dict[str, Any], session_id, s=None) -> Dict[str, Any]:
    """Count one scan's facts. Returns which fields were accepted."""
    s = s or store()
    g = normalize_gtin(gtin)
    if not g:
        return {"ok": False, "reason": "invalid barcode"}
    voter = voter_hash(session_id)
    s.touch(g)
    accepted, rejected = [], []
    current = s.get(g)
    locked = current[0]["locked"] if current else False
    for field in FIELDS:
        if field not in fields or fields[field] in (None, ""):
            continue
        value = _clean(field, fields[field])
        if value is None:
            rejected.append(field)
            continue
        if locked and field in (current[1] if current else {}):
            continue
        if voter and s.vote(g, field, value, voter):
            accepted.append(field)
    return {"ok": True, "gtin": g, "accepted": accepted, "rejected": rejected}


def lookup(gtin, s=None) -> Optional[Dict[str, Any]]:
    """What Filo knows about this barcode, field by field, with its status."""
    s = s or store()
    g = normalize_gtin(gtin)
    if not g:
        return None
    got = s.get(g)
    if not got:
        return None
    meta, facts = got
    out = {"gtin": g, "scans": meta["scans"], "fields": {}}
    for field in FIELDS:
        d = _decide(facts.get(field, []), meta["locked"])
        if d:
            value, votes, total, status = d
            out["fields"][field] = {"value": value, "status": status,
                                    "votes": votes, "of": total}
    statuses = [f["status"] for f in out["fields"].values()]
    comp = out["fields"].get("composition", {}).get("status")
    out["status"] = ("locked" if comp == "locked" else
                     "confirmed" if comp == "confirmed" else
                     "unconfirmed" if statuses else "empty")
    for kind, field in (("RN", "rn"), ("CA", "ca_number")):
        num = out["fields"].get(field, {}).get("value")
        if num:
            reg = s.registrant(kind, num)
            if reg:
                out["fields"][field]["registered_to"] = reg["registrant"]
                out["fields"][field]["role"] = reg["role"]
    return out


def value(product, field):
    return ((product or {}).get("fields", {}).get(field) or {}).get("value")


def brand_summary(min_products: int = 1, s=None) -> List[Dict[str, Any]]:
    """Admin: products per brand, by each product's top brand vote."""
    s = s or store()
    counts = {}
    for gtin, vals in s.brands().items():
        top = sorted(vals, key=lambda x: (-x[1], x[0]))[0][0]
        counts[top] = counts.get(top, 0) + 1
    rows = [{"brand": b, "products": n} for b, n in counts.items() if n >= min_products]
    return sorted(rows, key=lambda r: (-r["products"], r["brand"]))
