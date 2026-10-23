"""
Filo barcode brain (v41, 23 Oct 2026).

    barcode  ->  Filo's own product database  ->  outside barcode lookup  ->  verdict

1. Filo's database first (products.py). A barcode another shopper or a retailer
   feed has already filled in comes back with its brand, name, photo and fabric,
   and the verdict needs no care tag.
2. A barcode Filo hasn't seen is looked up once in an outside barcode database
   (UPCitemdb: free trial of about 100 a day with no key, or a paid key in
   UPCITEMDB_KEY). Three seconds at most; a scan never waits longer. What it
   returns (brand, name, photo, link) is saved to Filo's database, so the same
   barcode is never looked up twice. Barcode databases almost never carry fabric
   content, so the care tag scan still fills that in, once, for everyone.
3. Every barcode Filo couldn't fill in is counted (barcode_misses), with whether
   the outside lookup found anything. That list says which brands to add next.
4. Retailer and affiliate product feeds (FMTC, Affiliate.com, a brand's own
   catalogue) load through import_records(). Their brand, name, photo and link
   are locked: a feed is the retailer's own data.

Store barcodes that aren't GTINs (Code 128 on many fashion price tags) are kept
as "C:<code>". They can't be looked up outside Filo, but Filo's own database
learns them from the first shopper who scans the code and the care tag.
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

import events
import products

log = logging.getLogger("filo.barcodes")

UPCITEMDB_KEY = os.environ.get("UPCITEMDB_KEY", "").strip()
EXTERNAL_TIMEOUT = 3          # seconds; a scan never waits longer than this
_CACHE_TTL = 30 * 24 * 3600   # an outside answer (or "not found") is kept 30 days
_cache: Dict[str, Any] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS barcode_misses (
    code            TEXT NOT NULL,
    day             DATE NOT NULL DEFAULT CURRENT_DATE,
    external_found  BOOLEAN NOT NULL DEFAULT FALSE,
    brand           TEXT,
    scans           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, day)
);
"""
_misses: Dict[tuple, Dict[str, Any]] = {}     # used when there's no database


def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:                  # noqa: BLE001
        log.warning("barcodes: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


# ------------------------------------------------------------ outside lookup

def _fetch_upcitemdb(gtin: str) -> Optional[Dict[str, Any]]:
    code = gtin.lstrip("0")
    if len(code) < 12:
        code = code.zfill(12)
    if UPCITEMDB_KEY:
        url = "https://api.upcitemdb.com/prod/v1/lookup?" + urllib.parse.urlencode({"upc": code})
        headers = {"user_key": UPCITEMDB_KEY, "key_type": "3scale"}
    else:
        url = "https://api.upcitemdb.com/prod/trial/lookup?" + urllib.parse.urlencode({"upc": code})
        headers = {}
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=EXTERNAL_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    items = data.get("items") or []
    if not items:
        return None
    it = items[0]
    images = [u for u in (it.get("images") or []) if str(u).startswith("https://")]
    offers = [o for o in (it.get("offers") or []) if str(o.get("link", "")).startswith("https://")]
    return {
        "brand": it.get("brand") or None,
        "name": it.get("title") or None,
        "image": images[0] if images else None,
        "url": offers[0]["link"] if offers else None,
        "description": it.get("description") or "",
        "source": "upcitemdb",
    }


def external_lookup(gtin: str, fetch=None) -> Optional[Dict[str, Any]]:
    """Brand, name, photo and link for a GTIN from outside Filo, or None.
    Never raises; never takes more than EXTERNAL_TIMEOUT seconds."""
    if not gtin or gtin.startswith("C:"):
        return None
    hit = _cache.get(gtin)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        found = (fetch or _fetch_upcitemdb)(gtin)
    except Exception as exc:                  # noqa: BLE001  never block a scan
        log.info("barcodes: outside lookup failed for %s (%s)", gtin, exc)
        return None                           # not cached: try again next time
    _cache[gtin] = (time.time() + _CACHE_TTL, found)
    return found


def remember(gtin: str, found: Dict[str, Any]) -> None:
    """Save an outside answer into Filo's database as one unconfirmed vote."""
    facts = {k: found.get(k) for k in ("brand", "name", "image", "url")}
    facts["source"] = found.get("source")
    comp = products.canonical_composition(found.get("description") or "")
    if comp:
        facts["composition"] = comp
    try:
        products.contribute(gtin, facts, f"source:{found.get('source')}:{gtin}")
    except Exception:                         # noqa: BLE001
        pass


def resolve(raw_code: str, fetch=None, count_miss: bool = True) -> Optional[Dict[str, Any]]:
    """What Filo knows about a barcode, after asking outside if it must.
    count_miss: False when the shopper is scanning the care tag too, so the
    fabric is about to be filled in and this isn't a miss."""
    g = products.normalize_gtin(raw_code)
    if not g:
        return None
    known = products.lookup(g)
    has_brand = bool(products.value(known, "brand"))
    if has_brand:
        return known
    found = external_lookup(g, fetch=fetch)
    if found and (found.get("brand") or found.get("name")):
        remember(g, found)
        known = products.lookup(g)
    if count_miss and not products.value(known, "composition"):
        record_miss(g, external_found=bool(found), brand=(found or {}).get("brand"))
    return known


# ------------------------------------------------------------ misses

def record_miss(code: str, external_found: bool, brand: Optional[str] = None) -> None:
    """A barcode Filo couldn't give a verdict for, without a care tag."""
    if events.DATABASE_URL:
        conn = events._conn()
        if conn is None:
            return
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO barcode_misses (code, external_found, brand, scans) "
                    "VALUES (%s, %s, %s, 1) ON CONFLICT (code, day) DO UPDATE SET "
                    "scans = barcode_misses.scans + 1, "
                    "external_found = barcode_misses.external_found OR EXCLUDED.external_found, "
                    "brand = COALESCE(EXCLUDED.brand, barcode_misses.brand)",
                    (code, external_found, (brand or "")[:80] or None))
        except Exception as exc:              # noqa: BLE001
            log.info("barcodes: miss not recorded (%s)", exc)
        finally:
            conn.close()
        return
    key = (code, date.today())
    m = _misses.setdefault(key, {"code": code, "scans": 0, "external_found": False, "brand": None})
    m["scans"] += 1
    m["external_found"] = m["external_found"] or external_found
    m["brand"] = brand or m["brand"]


def misses(days: int = 30, limit: int = 100) -> List[Dict[str, Any]]:
    """Most-scanned barcodes Filo couldn't fill in, newest window first."""
    if events.DATABASE_URL:
        conn = events._conn()
        if conn is None:
            return []
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT code, SUM(scans), BOOL_OR(external_found), MAX(brand) "
                    "FROM barcode_misses WHERE day >= CURRENT_DATE - %s "
                    "GROUP BY code ORDER BY SUM(scans) DESC LIMIT %s", (days, limit))
                return [{"code": c, "scans": int(n), "external_found": bool(f), "brand": b}
                        for c, n, f, b in cur.fetchall()]
        except Exception:                     # noqa: BLE001
            return []
        finally:
            conn.close()
    rows: Dict[str, Dict[str, Any]] = {}
    for (code, _), m in _misses.items():
        r = rows.setdefault(code, {"code": code, "scans": 0, "external_found": False, "brand": None})
        r["scans"] += m["scans"]
        r["external_found"] = r["external_found"] or m["external_found"]
        r["brand"] = r["brand"] or m["brand"]
    return sorted(rows.values(), key=lambda r: -r["scans"])[:limit]


# ------------------------------------------------------------ feeds

def import_records(records: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    """Load product records from a retailer or affiliate feed.

    Each record: gtin (required), brand, name, image, url, composition or a
    description to read it from, category. Brand, name, photo and link are
    locked (the retailer's own data). Fabric is locked only when it parses
    into percentages that add up to about 100."""
    s = products.store()
    added, skipped = 0, 0
    src = (source or "feed")[:40]
    for r in records or []:
        g = products.normalize_gtin(r.get("gtin") or r.get("barcode") or r.get("upc") or "")
        if not g:
            skipped += 1
            continue
        fields = {
            "brand": r.get("brand"), "name": r.get("name") or r.get("title"),
            "image": r.get("image") or r.get("image_url"), "url": r.get("url") or r.get("link"),
            "category": r.get("category"), "source": src,
            "composition": r.get("composition") or r.get("description") or "",
        }
        wrote = False
        for field, value in fields.items():
            clean = products._clean(field, value) if value else None
            if clean is None:
                continue
            s.set_locked(g, field, clean)
            wrote = True
        # v42: a feed price joins the usual price for this barcode.
        if r.get("price") is not None:
            products.record_price(g, r.get("price"), r.get("country"), source=f"feed:{src}")
        added += 1 if wrote else 0
        skipped += 0 if wrote else 1
    return {"ok": True, "source": src, "added": added, "skipped": skipped}
