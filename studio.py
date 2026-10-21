"""Filo Studio: Brooklyn's private page for running the bank.

Open /studio in a browser. The password is the Railway variable STUDIO_PASSWORD,
typed in by Brooklyn. It is never in the code, the repo, a test or a log.
No STUDIO_PASSWORD set means Studio is switched off.

Sign-in rules:
- 5 wrong tries from one address locks that address for 15 minutes.
- 25 wrong tries in 15 minutes from anywhere locks Studio for everyone for 15 minutes,
  so guessing from many addresses doesn't work either.
- A sign-in lasts 12 hours. Changing STUDIO_PASSWORD signs everyone out.

Sections: Products, Brands, Standards, Vocabulary, Backlog, Health, Sources.
Every change Brooklyn makes is written to studio_log.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import bank
import bank_import
import bank_rules
import bank_validate
import events

log = logging.getLogger("filo.studio")

COOKIE = "filo_studio"
SESSION_HOURS = 12
MAX_TRIES = 5
LOCK_SECONDS = 15 * 60
GLOBAL_MAX_TRIES = 25

SHOWABLE_STOCK = ("in_stock", "low_stock", "preorder", "made_to_order")
DECISIONS = ("pass", "conditional", "hold", "insufficient_evidence")
RESEARCH_STATUSES = ("priority_screen", "discovery_lead", "resale_source", "remove")
SLOTS = ("best_overall", "best_under", "best_splurge", "alternate")
LANGUAGES = ("en", "fr", "es", "it", "de", "pt", "nl", "sv", "ja", "ko")

# The ten Filo Standards from first_wave.md, in order.
BASICS = [
    ("white_t_shirt", "White T-shirt"), ("white_shirt", "White shirt"),
    ("straight_leg_jeans", "Straight leg jeans"), ("crewneck_sweater", "Crewneck sweater"),
    ("tailored_trousers", "Tailored trousers"), ("trench_coat", "Trench coat"),
    ("tailored_blazer", "Tailored blazer"), ("black_t_shirt", "Black T-shirt"),
    ("leather_jacket", "Leather jacket"), ("camel_wool_coat", "Camel wool coat"),
]
BASIC_IDS = {b for b, _ in BASICS}


# ------------------------------------------------------------------ sign-in

_lock = threading.Lock()
_fails: Dict[str, List[float]] = {}
_locked_until: Dict[str, float] = {}
_global_fails: List[float] = []
_global_locked_until = [0.0]
_signed_out: Dict[str, float] = {}          # token -> expiry, for sign-out before the 12 hours are up


def _password() -> str:
    return os.environ.get("STUDIO_PASSWORD") or ""


def enabled() -> bool:
    return bool(_password())


def _key() -> bytes:
    # Derived from the password, so changing STUDIO_PASSWORD ends every sign-in.
    return hashlib.sha256(b"filo-studio-v1|" + _password().encode("utf-8")).digest()


def make_token(now: Optional[float] = None) -> str:
    exp = int((now or time.time()) + SESSION_HOURS * 3600)
    nonce = os.urandom(8).hex()
    payload = f"{exp}.{nonce}"
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def token_ok(token: Optional[str], now: Optional[float] = None) -> bool:
    if not token or not enabled():
        return False
    try:
        exp_s, nonce, sig = token.split(".")
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(_key(), f"{exp_s}.{nonce}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        return False
    if exp < (now or time.time()):
        return False
    return token not in _signed_out


def sign_out(token: Optional[str]):
    if token:
        with _lock:
            now = time.time()
            for t, e in list(_signed_out.items()):
                if e < now:
                    del _signed_out[t]
            _signed_out[token] = now + SESSION_HOURS * 3600


def _minutes_left(until: float, now: float) -> int:
    return max(1, int((until - now + 59) // 60))


def login(given: str, address: str, now: Optional[float] = None) -> Tuple[bool, str, Optional[str]]:
    """(ok, message, token). Never logs or stores what was typed."""
    now = now or time.time()
    if not enabled():
        return False, "Studio is off. Add STUDIO_PASSWORD in Railway to switch it on.", None
    with _lock:
        if _global_locked_until[0] > now:
            return False, f"Too many wrong tries. Try again in {_minutes_left(_global_locked_until[0], now)} minutes.", None
        if _locked_until.get(address, 0) > now:
            return False, f"Too many wrong tries. Try again in {_minutes_left(_locked_until[address], now)} minutes.", None
    right = hmac.compare_digest(hashlib.sha256((given or "").encode("utf-8")).digest(),
                                hashlib.sha256(_password().encode("utf-8")).digest())
    with _lock:
        if right:
            _fails.pop(address, None)
            return True, "Signed in.", make_token(now)
        recent = [t for t in _fails.get(address, []) if now - t < LOCK_SECONDS] + [now]
        _fails[address] = recent
        _global_fails[:] = [t for t in _global_fails if now - t < LOCK_SECONDS] + [now]
        if len(_global_fails) >= GLOBAL_MAX_TRIES:
            _global_locked_until[0] = now + LOCK_SECONDS
            _global_fails.clear()
            log.warning("studio: too many wrong passwords overall; locked for 15 minutes")
        if len(recent) >= MAX_TRIES:
            _locked_until[address] = now + LOCK_SECONDS
            _fails.pop(address, None)
            log.warning("studio: too many wrong passwords from one address; locked for 15 minutes")
            return False, "Too many wrong tries. Try again in 15 minutes.", None
        left = MAX_TRIES - len(recent)
    return False, f"That's not it. {left} {'try' if left == 1 else 'tries'} left.", None


def reset_for_tests():
    with _lock:
        _fails.clear()
        _locked_until.clear()
        _global_fails.clear()
        _global_locked_until[0] = 0.0
        _signed_out.clear()


# ------------------------------------------------------------------ database helpers

def _rows(sql: str, args=()) -> List[dict]:
    conn = events._conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            names = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _run(statements: List[Tuple[str, Any]]) -> List[int]:
    conn = events._conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        counts = []
        with conn, conn.cursor() as cur:
            for sql, args in statements:
                cur.execute(sql, args)
                counts.append(cur.rowcount)
        return counts
    finally:
        conn.close()


def _log(action: str, subject: str, detail: Optional[dict] = None):
    try:
        _run([("INSERT INTO studio_log (action, subject, detail) VALUES (%s, %s, %s::jsonb)",
               (action, subject, json.dumps(detail or {}, default=str)))])
    except Exception as exc:                # noqa: BLE001
        log.warning("studio: couldn't write the log (%s)", exc)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if v.__class__.__name__ == "Decimal":
        return float(v)
    return v


class StudioError(ValueError):
    pass


# ------------------------------------------------------------------ brands

BRAND_COLS = """brand_id, name, official_url, url_verified, region, gender_target, categories, research_status,
                evidence_level, style_led, approved, approved_on, approved_note, unique_find, independent_label,
                style_fit, studio_note, flags, research, discovery, studio_touched, updated_at"""


def brands(status: str = "", evidence: str = "", category: str = "", style_led: str = "",
           approved: str = "", q: str = "") -> Dict[str, Any]:
    where, args = [], []
    if status:
        where.append("research_status = %s"); args.append(status)
    if evidence:
        where.append("evidence_level = %s"); args.append(evidence)
    if category:
        where.append("%s = ANY(categories)"); args.append(category)
    if style_led in ("yes", "no"):
        where.append("style_led = %s"); args.append(style_led == "yes")
    if approved in ("yes", "no"):
        where.append("approved = %s"); args.append(approved == "yes")
    if q:
        where.append("(name ILIKE %s OR brand_id ILIKE %s)"); args += [f"%{q}%", f"%{q}%"]
    sql = f"SELECT {BRAND_COLS} FROM bank_brands"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY approved DESC, name LIMIT 700"
    rows = _rows(sql, args)
    facets = _rows("""SELECT
        (SELECT array_agg(DISTINCT evidence_level ORDER BY evidence_level) FROM bank_brands) AS evidence,
        (SELECT array_agg(DISTINCT c ORDER BY c) FROM bank_brands, unnest(categories) c) AS categories,
        (SELECT count(*) FROM bank_brands WHERE approved) AS approved,
        (SELECT count(*) FROM bank_brands) AS total""")[0]
    return _jsonable({"brands": rows, "facets": facets})


def update_brand(brand_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    sets, args = ["studio_touched = TRUE", "updated_at = now()"], []
    if "approved" in changes:
        ok = bool(changes["approved"])
        sets.append("approved = %s"); args.append(ok)
        sets.append("approved_on = %s"); args.append(date.today() if ok else None)
        if ok and "research_status" not in changes:
            sets.append("research_status = CASE WHEN research_status = 'remove' THEN 'priority_screen' ELSE research_status END")
    if "research_status" in changes:
        if changes["research_status"] not in RESEARCH_STATUSES:
            raise StudioError("Unknown research status.")
        sets.append("research_status = %s"); args.append(changes["research_status"])
    if changes.get("reject"):
        sets += ["approved = FALSE", "approved_on = NULL", "research_status = 'remove'"]
    for key in ("unique_find", "independent_label"):
        if key in changes:
            sets.append(f"{key} = %s"); args.append(bool(changes[key]))
    if "style_fit" in changes:
        v = changes["style_fit"]
        if v in (None, ""):
            v = None
        else:
            v = int(v)
            if not 0 <= v <= 4:
                raise StudioError("Style fit is 0 to 4.")
        sets.append("style_fit = %s"); args.append(v)
    for key in ("studio_note", "approved_note"):
        if key in changes:
            sets.append(f"{key} = %s"); args.append((changes[key] or "")[:4000] or None)
    if len(sets) == 2:
        raise StudioError("Nothing to change.")
    n = _run([(f"UPDATE bank_brands SET {', '.join(sets)} WHERE brand_id = %s", args + [brand_id])])[0]
    if not n:
        raise StudioError("No such brand.")
    _log("brand", brand_id, changes)
    return _jsonable(_rows(f"SELECT {BRAND_COLS} FROM bank_brands WHERE brand_id = %s", (brand_id,))[0])


# ------------------------------------------------------------------ products

def products(status: str = "", q: str = "", category: str = "") -> Dict[str, Any]:
    where, args = [], []
    if status == "rules":
        where.append("p.decided_by = 'rules'")
    elif status == "spot":
        where.append("p.studio_edits ? 'spot_check'")
    elif status:
        where.append("p.status = %s"); args.append(status)
    if category:
        where.append("p.category = %s"); args.append(category)
    if q:
        where.append("(p.name ILIKE %s OR b.name ILIKE %s OR p.product_url ILIKE %s)"); args += [f"%{q}%"] * 3
    sql = """SELECT p.product_id, p.name, b.name AS brand, b.approved AS brand_approved, p.category, p.status,
                    p.decision, p.decided_by, p.product_version, p.retailer, p.last_checked,
                    (p.record->'filo'->>'is_unique_find')::boolean AS unique_find,
                    p.studio_edits ? 'spot_check' AS spot_check,
                    (SELECT min(coalesce(v.sale_price, v.price)) FROM bank_variants v WHERE v.product_id = p.product_id) AS price,
                    (SELECT min(v.currency) FROM bank_variants v WHERE v.product_id = p.product_id) AS currency,
                    (SELECT min(v.price_cad) FROM bank_variants v WHERE v.product_id = p.product_id) AS price_cad
             FROM bank_products p JOIN bank_brands b ON b.brand_id = p.brand_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """ ORDER BY CASE p.status WHEN 'in_review' THEN 0 WHEN 'hidden_stale' THEN 1 WHEN 'draft' THEN 2
                                       WHEN 'live' THEN 3 ELSE 4 END, p.last_checked DESC LIMIT 500"""
    rows = _rows(sql, args)
    by_status = dict((r["status"], r["n"]) for r in _rows("SELECT status, count(*) AS n FROM bank_products GROUP BY status"))
    extra = _rows("""SELECT count(*) FILTER (WHERE decided_by = 'rules') AS rules,
                            count(*) FILTER (WHERE studio_edits ? 'spot_check') AS spot FROM bank_products""")[0]
    by_status.update(extra)
    return _jsonable({"products": rows, "by_status": by_status})


def _variant_ok(v: dict) -> bool:
    return v.get("availability") in SHOWABLE_STOCK


go_live_problems = bank_rules.go_live_problems


def _stock_note(record: dict) -> Optional[str]:
    av = {v.get("availability") for v in record.get("variants") or []}
    if av and av <= {"unknown"}:
        return "The page doesn't say what's in stock. It can go live, but it won't show in a hunt until stock is known."
    if av and not (av & set(SHOWABLE_STOCK)):
        return "Sold out in every size and colour. It will go live as hidden until it's back."
    return None


def _fit_tag_choices() -> List[dict]:
    dims = _rows("""SELECT DISTINCT dimension FROM bank_concepts WHERE concept_id = ANY(%s)""",
                 ([t for _, t in __import__("bank_facts").FIT_WORDS],))
    dims = [d["dimension"] for d in dims] or ["silhouette"]
    return _rows("SELECT concept_id, dimension FROM bank_concepts WHERE dimension = ANY(%s) ORDER BY dimension, concept_id",
                 (dims,))


def product(product_id: str) -> Dict[str, Any]:
    saved = bank_import.load_product(product_id)
    if not saved:
        raise StudioError("No such product.")
    record = saved["record"]
    brand = _rows("SELECT brand_id, name, approved, official_url FROM bank_brands WHERE brand_id = %s",
                  (record["brand_id"],))
    brand = brand[0] if brand else {"brand_id": record["brand_id"], "name": record["brand_id"], "approved": False}
    facts = _rows("""SELECT field, value, unit, status, source_type, scope, confidence, evidence_ids
                     FROM bank_facts WHERE product_id = %s AND product_version = %s ORDER BY field""",
                  (product_id, saved["product_version"]))
    ev = {e["evidence_id"]: e for e in _rows(
        """SELECT evidence_id, claim, field, source_url, source_kind, source_type, quote, scope, confidence,
                  checked_at, status FROM bank_evidence WHERE subject_id = %s""", (product_id,))}
    for f in facts:
        f["evidence"] = [ev[i] for i in (f.get("evidence_ids") or []) if i in ev]
    variants = _rows("""SELECT variant_id, colour, size_label, price, currency, sale_price, price_cad, price_usd,
                               fx_rate_id, availability, last_checked FROM bank_variants
                        WHERE product_id = %s ORDER BY colour, size_label""", (product_id,))
    standards = _rows("SELECT basic, slot, rank, note, price_line FROM bank_standards WHERE product_id = %s",
                      (product_id,))
    sources = _rows("""SELECT fetched_at, status, error FROM bank_sources_log WHERE product_id = %s
                       ORDER BY fetched_at DESC, id DESC LIMIT 8""", (product_id,))
    return _jsonable({
        "product_id": product_id, "record": record, "status": saved["status"], "decision": saved["decision"],
        "decided_by": saved["decided_by"], "product_version": saved["product_version"],
        "product_url": saved["product_url"], "brand": brand, "facts": facts, "variants": variants,
        "standards": standards, "sources": sources, "studio_edits": saved.get("studio_edits") or {},
        "go_live_problems": go_live_problems(record, bool(brand.get("approved")), saved["decision"]),
        "stock_note": _stock_note(record), "fit_tag_choices": _fit_tag_choices(),
        "auto_reasons": (_rows("SELECT auto_reasons FROM bank_products WHERE product_id = %s", (product_id,)) or [{}])[0].get("auto_reasons") or [],
        "spot_check": bool((saved.get("studio_edits") or {}).get("spot_check")),
    })


def import_product(url: str) -> Dict[str, Any]:
    url = (url or "").strip()
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
        raise StudioError("Paste the full product link, starting with https://")
    r = bank_import.import_url(url)
    _log("import", r.get("product_id") or url, {"url": url, "saved": r.get("saved"), "ok": r.get("ok")})
    if not r.get("record"):
        return {"saved": False, "message": f"Couldn't read that page ({r.get('error') or 'no product found'})."}
    rec = r["record"]
    f = rec.get("filo") or {}
    if r.get("saved"):
        msg = f"Saved for review. Suggested: {f.get('decision', '').replace('_', ' ')}. {f.get('decision_reason', '')}"
    elif r.get("brand") is None:
        msg = "Not saved: the brand isn't in Filo's bank yet. Add the brand first, or try the brand's own page."
    elif r.get("problems"):
        msg = "Not saved: the record didn't pass the checks. " + "; ".join(r["problems"][:3])
    else:
        msg = "Not saved: the database didn't accept it. Try again in a minute."
    return {"saved": bool(r.get("saved")), "product_id": r.get("product_id"), "message": msg.strip()}


def _current(product_id: str) -> dict:
    rows = _rows("""SELECT p.record, p.status, p.decision, p.decided_by, p.product_version, p.studio_edits, p.auto_reasons,
                           b.approved AS brand_approved, p.product_url
                    FROM bank_products p JOIN bank_brands b ON b.brand_id = p.brand_id WHERE p.product_id = %s""",
                 (product_id,))
    if not rows:
        raise StudioError("No such product.")
    return rows[0]


def _check_version(row: dict, version) -> None:
    if version is not None and int(version) != int(row["product_version"]):
        raise StudioError("This product changed since you opened it (a new version came in). Reload and look again.")


def _save_record(product_id: str, record: dict, extra_sets: str = "", extra_args=()):
    problems = bank_validate.validate_product(record)
    if problems:
        raise StudioError("That would break the record: " + "; ".join(problems[:3]))
    _run([(f"UPDATE bank_products SET record = %s::jsonb{extra_sets} WHERE product_id = %s",
           (json.dumps(record), *extra_args, product_id))])


def decide(product_id: str, decision: str, reason: str = "", version=None, publish: bool = False) -> Dict[str, Any]:
    if decision not in DECISIONS:
        raise StudioError("Pick pass, conditional, hold or not enough evidence.")
    row = _current(product_id)
    _check_version(row, version)
    record = row["record"]
    if publish:
        problems = go_live_problems(record, bool(row["brand_approved"]), decision)
        if problems:
            raise StudioError("Can't go live yet: " + " ".join(problems))
        status = "live" if any(_variant_ok(v) for v in record.get("variants") or []) else "hidden_out_of_stock"
        if all(v.get("availability") == "unknown" for v in record.get("variants") or []):
            status = "live"
    elif decision in ("hold", "insufficient_evidence"):
        status = "draft"
    else:
        status = row["status"] if row["status"] != "draft" else "in_review"
    today = date.today().isoformat()
    f = record.setdefault("filo", {})
    f.update(decision=decision, decided_by="brooklyn", decided_on=today)
    if reason.strip():
        f["decision_reason"] = reason.strip()[:600]
    record["status"] = status
    _save_record(product_id, record,
                 ", decision = %s, decision_reason = %s, decided_by = 'brooklyn', decided_on = %s, status = %s,"
                 " studio_edits = studio_edits - 'spot_check'",
                 (decision, f.get("decision_reason"), today, status))
    _log("decide", product_id, {"decision": decision, "publish": publish, "status": status,
                                "version": row["product_version"]})
    return {"status": status, "decision": decision}


def take_down(product_id: str) -> Dict[str, Any]:
    row = _current(product_id)
    record = row["record"]
    record["status"] = "in_review"
    _save_record(product_id, record, ", status = 'in_review'")
    _log("take_down", product_id)
    return {"status": "in_review"}


def retire(product_id: str) -> Dict[str, Any]:
    row = _current(product_id)
    record = row["record"]
    record["status"] = "retired"
    _save_record(product_id, record, ", status = 'retired'")
    _run([("DELETE FROM bank_standards WHERE product_id = %s", (product_id,))])
    _log("retire", product_id)
    return {"status": "retired"}


def set_flags(product_id: str, changes: Dict[str, Any], version=None) -> Dict[str, Any]:
    row = _current(product_id)
    _check_version(row, version)
    record = row["record"]
    f = record.setdefault("filo", {})
    for key in ("is_unique_find", "is_independent_label"):
        if key in changes:
            f[key] = bool(changes[key])
    if "studio_note" in changes:
        note = (changes["studio_note"] or "").strip()[:2000]
        if note:
            f["studio_note"] = note
        else:
            f.pop("studio_note", None)
    _save_record(product_id, record)
    _log("flags", product_id, changes)
    return {k: f.get(k) for k in ("is_unique_find", "is_independent_label", "studio_note")}


def set_fit_tags(product_id: str, tags: List[str], version=None) -> Dict[str, Any]:
    """Brooklyn's fix of the cut words. Tags she adds are her read of the photos: never 'confirmed'."""
    row = _current(product_id)
    _check_version(row, version)
    allowed = {c["concept_id"] for c in _fit_tag_choices()}
    tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
    unknown = [t for t in tags if t not in allowed]
    if unknown:
        raise StudioError("Not a cut word Filo knows: " + ", ".join(unknown))
    record = row["record"]
    fit = record.setdefault("fit", {})
    old = fit.get("fit_tags") or []
    edits = row["studio_edits"] or {}
    if "fit_tags_page" not in edits:
        edits["fit_tags_page"] = old
    by_tag = {t["tag"]: t for t in old}
    version_n = int(row["product_version"])
    checked = bank_import.now_iso()
    new_tags, evidence = [], []
    for i, tag in enumerate(tags):
        if tag in by_tag:
            new_tags.append(by_tag[tag])
            continue
        eid = f"{product_id}:v{version_n}:fit.fit_tags.{tag}:studio{int(time.time())}{i}"
        evidence.append({
            "evidence_id": eid, "claim": f"Brooklyn's read of the photos: {tag.replace('_', ' ')}",
            "field": f"fit.fit_tags.{tag}", "subject_type": "product", "subject_id": product_id,
            "source_url": row["product_url"], "source_kind": "brooklyn_manual", "source_type": "inferred",
            "scope": "this_style", "confidence": 0.6, "checked_at": checked, "status": "current",
            "retrieval": {"method": "manual"},
        })
        new_tags.append({"tag": tag, "fact": {"value": True, "status": "from_photo", "source_type": "inferred",
                                              "evidence_ids": [eid], "scope": "this_style", "confidence": 0.6}})
    fit["fit_tags"] = new_tags
    for e in evidence:
        problems = bank_validate.validate_evidence(e)
        if problems:
            raise StudioError("; ".join(problems))
    problems = bank_validate.validate_product(record)
    if problems:
        raise StudioError("That would break the record: " + "; ".join(problems[:3]))
    record["status"] = row["status"]
    if not bank_import.save_product(record, evidence):
        raise StudioError("The database didn't accept that. Try again.")
    _run([("UPDATE bank_products SET studio_edits = %s::jsonb WHERE product_id = %s",
           (json.dumps(edits), product_id))])
    _log("fit_tags", product_id, {"tags": tags})
    return {"fit_tags": [t["tag"] for t in new_tags]}


def refresh_now(product_id: str) -> Dict[str, Any]:
    r = bank_import.refresh(product_id)
    _log("refresh", product_id, {"ok": r.get("ok"), "changed": r.get("changed")})
    return _jsonable(r)


# ------------------------------------------------------------------ standards

def standards() -> Dict[str, Any]:
    rows = _rows("""SELECT s.basic, s.slot, s.rank, s.note, s.price_line, s.product_id, p.name, p.status,
                           b.name AS brand,
                           (SELECT min(v.price_cad) FROM bank_variants v WHERE v.product_id = s.product_id) AS price_cad
                    FROM bank_standards s JOIN bank_products p ON p.product_id = s.product_id
                    JOIN bank_brands b ON b.brand_id = p.brand_id
                    ORDER BY s.basic, s.slot, s.rank""")
    out = []
    for bid, label in BASICS:
        picks = [r for r in rows if r["basic"] == bid]
        out.append({"basic": bid, "label": label, "picks": picks})
    choices = _rows("""SELECT p.product_id, p.name, b.name AS brand, p.category, p.status FROM bank_products p
                       JOIN bank_brands b ON b.brand_id = p.brand_id
                       WHERE p.decided_by = 'brooklyn' AND p.decision IN ('pass','conditional')
                         AND p.status <> 'retired' ORDER BY b.name, p.name""")
    return _jsonable({"standards": out, "choices": choices})


def set_standard(basic: str, slot: str, product_id: str, note: str = "", price_line=None) -> Dict[str, Any]:
    if basic not in BASIC_IDS:
        raise StudioError("Not one of the ten basics.")
    if slot not in SLOTS:
        raise StudioError("Pick best overall, best under, best splurge or alternate.")
    row = _rows("SELECT decision, decided_by, status FROM bank_products WHERE product_id = %s", (product_id,))
    if not row:
        raise StudioError("No such product.")
    if row[0]["decided_by"] != "brooklyn" or row[0]["decision"] not in ("pass", "conditional"):
        raise StudioError("Confirm this product as pass or conditional first.")
    if slot == "best_under":
        try:
            price_line = float(price_line)
        except (TypeError, ValueError):
            raise StudioError("Best under needs the price, in CAD.")
    else:
        price_line = None
    if slot == "alternate":
        rank = (_rows("SELECT coalesce(max(rank), 0) + 1 AS r FROM bank_standards WHERE basic = %s AND slot = 'alternate'",
                      (basic,))[0]["r"])
        _run([("DELETE FROM bank_standards WHERE basic = %s AND slot = 'alternate' AND product_id = %s",
               (basic, product_id))])
    else:
        rank = 1
    _run([("""INSERT INTO bank_standards (basic, slot, rank, product_id, note, price_line)
              VALUES (%s, %s, %s, %s, %s, %s)
              ON CONFLICT (basic, slot, rank) DO UPDATE SET product_id = EXCLUDED.product_id, note = EXCLUDED.note,
                  price_line = EXCLUDED.price_line, updated_at = now()""",
           (basic, slot, rank, product_id, (note or "").strip()[:300] or None, price_line))])
    _log("standard", basic, {"slot": slot, "product_id": product_id})
    return standards()


def remove_standard(basic: str, slot: str, rank: int = 1) -> Dict[str, Any]:
    _run([("DELETE FROM bank_standards WHERE basic = %s AND slot = %s AND rank = %s", (basic, slot, int(rank)))])
    _log("standard_remove", basic, {"slot": slot, "rank": rank})
    return standards()


def move_alternate(basic: str, rank: int, direction: int) -> Dict[str, Any]:
    alts = _rows("SELECT rank FROM bank_standards WHERE basic = %s AND slot = 'alternate' ORDER BY rank", (basic,))
    ranks = [a["rank"] for a in alts]
    if rank not in ranks:
        raise StudioError("No such alternate.")
    i = ranks.index(rank)
    j = i + (1 if direction > 0 else -1)
    if not 0 <= j < len(ranks):
        return standards()
    a, b = ranks[i], ranks[j]
    _run([("UPDATE bank_standards SET rank = -1 WHERE basic = %s AND slot = 'alternate' AND rank = %s", (basic, a)),
          ("UPDATE bank_standards SET rank = %s WHERE basic = %s AND slot = 'alternate' AND rank = %s", (a, basic, b)),
          ("UPDATE bank_standards SET rank = %s WHERE basic = %s AND slot = 'alternate' AND rank = -1", (b, basic))])
    return standards()


# ------------------------------------------------------------------ vocabulary

def vocabulary(q: str = "", dimension: str = "") -> Dict[str, Any]:
    where, args = [], []
    if dimension:
        where.append("c.dimension = %s"); args.append(dimension)
    if q:
        where.append("""(c.concept_id ILIKE %s OR EXISTS (SELECT 1 FROM bank_concept_phrases x
                         WHERE x.concept_id = c.concept_id AND x.phrase ILIKE %s))"""); args += [f"%{q}%"] * 2
    sql = """SELECT c.concept_id, c.dimension, c.note,
                    coalesce(json_agg(json_build_object('language', p.language, 'phrase', p.phrase,
                             'removed', p.removed, 'added_in_studio', p.added_in_studio)
                             ORDER BY p.language, p.phrase) FILTER (WHERE p.phrase IS NOT NULL), '[]') AS phrases
             FROM bank_concepts c LEFT JOIN bank_concept_phrases p ON p.concept_id = c.concept_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY c.concept_id ORDER BY c.dimension, c.concept_id LIMIT 400"
    dims = [r["dimension"] for r in _rows("SELECT DISTINCT dimension FROM bank_concepts ORDER BY dimension")]
    return _jsonable({"concepts": _rows(sql, args), "dimensions": dims, "unmatched": unmatched_phrases()})


def add_phrase(concept_id: str, language: str, phrase: str) -> Dict[str, Any]:
    phrase = re.sub(r"\s+", " ", (phrase or "").strip())
    if not phrase or len(phrase) > 80:
        raise StudioError("Type a phrase, up to 80 characters.")
    if language not in LANGUAGES:
        raise StudioError("Unknown language.")
    if not _rows("SELECT 1 FROM bank_concepts WHERE concept_id = %s", (concept_id,)):
        raise StudioError("No such concept.")
    _run([("""INSERT INTO bank_concept_phrases (concept_id, language, phrase, added_in_studio)
              VALUES (%s, %s, %s, TRUE)
              ON CONFLICT (concept_id, language, phrase) DO UPDATE SET removed = FALSE""",
           (concept_id, language, phrase))])
    _log("phrase_add", concept_id, {"language": language, "phrase": phrase})
    return {"ok": True}


def remove_phrase(concept_id: str, language: str, phrase: str) -> Dict[str, Any]:
    # Marked removed, not deleted, so a restart doesn't bring a seeded phrase back.
    n = _run([("UPDATE bank_concept_phrases SET removed = TRUE WHERE concept_id = %s AND language = %s AND phrase = %s",
               (concept_id, language, phrase))])[0]
    if not n:
        raise StudioError("No such phrase.")
    _log("phrase_remove", concept_id, {"language": language, "phrase": phrase})
    return {"ok": True}


def add_concept(concept_id: str, dimension: str, note: str = "") -> Dict[str, Any]:
    cid = re.sub(r"[^a-z0-9_]+", "_", (concept_id or "").strip().lower()).strip("_")
    if not cid or len(cid) > 60:
        raise StudioError("Give the concept a short ID, like wide_leg.")
    if not _rows("SELECT 1 FROM bank_concepts WHERE dimension = %s LIMIT 1", (dimension,)):
        raise StudioError("Pick one of the existing groups.")
    n = _run([("""INSERT INTO bank_concepts (concept_id, dimension, note, extra) VALUES (%s, %s, %s, '{"added_in_studio": true}')
                  ON CONFLICT (concept_id) DO NOTHING""", (cid, dimension, (note or "").strip()[:300] or None))])[0]
    if not n:
        raise StudioError("That concept already exists.")
    _log("concept_add", cid, {"dimension": dimension})
    return {"concept_id": cid}


def unmatched_phrases(days: int = 90) -> List[dict]:
    """Shopper words that matched no concept. Stage 5 writes them to brief_structured.unmatched_phrases."""
    return _jsonable(_rows("""
        SELECT lower(p) AS phrase, count(*) AS times, max(h.created_at) AS last_seen
        FROM stylist_hunts h, jsonb_array_elements_text(coalesce(h.brief_structured->'unmatched_phrases', '[]')) p
        WHERE h.created_at > now() - make_interval(days => %s)
        GROUP BY lower(p) ORDER BY times DESC, last_seen DESC LIMIT 100""", (days,)))


# ------------------------------------------------------------------ backlog

def backlog(days: int = 30) -> Dict[str, Any]:
    """Brooklyn's research to-do list, from what shoppers did. Most frequent first."""
    window = (days,)
    no_results = _rows("""SELECT signature, brief_structured, count, last_seen FROM bank_gaps
                          ORDER BY count DESC, last_seen DESC LIMIT 50""")
    rewordings = _rows("""
        WITH h AS (SELECT device_id, created_at, brief_structured, nothing_passed,
                          lag(created_at) OVER (PARTITION BY device_id ORDER BY created_at) AS prev_at
                   FROM stylist_hunts WHERE created_at > now() - make_interval(days => %s))
        SELECT coalesce(brief_structured->>'category', 'unknown') AS category, count(*) AS times
        FROM h WHERE prev_at IS NOT NULL AND created_at - prev_at < interval '15 minutes'
        GROUP BY 1 ORDER BY times DESC LIMIT 20""", window)
    saved_not_clicked = _rows("""
        SELECT a.product_id, p.name, b.name AS brand, count(*) AS saves
        FROM stylist_actions a LEFT JOIN bank_products p ON p.product_id = a.product_id
        LEFT JOIN bank_brands b ON b.brand_id = p.brand_id
        WHERE a.action = 'heart' AND a.created_at > now() - make_interval(days => %s)
          AND NOT EXISTS (SELECT 1 FROM stylist_actions c WHERE c.hunt_id = a.hunt_id
                          AND c.product_id = a.product_id AND c.action IN ('tap','buy'))
        GROUP BY a.product_id, p.name, b.name ORDER BY saves DESC LIMIT 30""", window)
    reasons = _rows("""SELECT reason, count(*) AS times FROM stylist_actions
                       WHERE action = 'dismiss' AND reason IS NOT NULL AND created_at > now() - make_interval(days => %s)
                       GROUP BY reason ORDER BY times DESC""", window)
    weak_categories = _rows("""
        SELECT coalesce(brief_structured->>'category', 'unknown') AS category, count(*) AS hunts,
               round(100.0 * avg(CASE WHEN nothing_passed THEN 1 ELSE 0 END)) AS pct_nothing
        FROM stylist_hunts WHERE created_at > now() - make_interval(days => %s)
        GROUP BY 1 ORDER BY pct_nothing DESC, hunts DESC LIMIT 20""", window)
    weak_languages = _rows("""
        SELECT coalesce(language, 'unknown') AS language, count(*) AS hunts,
               round(100.0 * avg(CASE WHEN nothing_passed THEN 1 ELSE 0 END)) AS pct_nothing
        FROM stylist_hunts WHERE created_at > now() - make_interval(days => %s)
        GROUP BY 1 ORDER BY pct_nothing DESC, hunts DESC""", window)
    missing_brands = _rows("""
        SELECT lower(x) AS brand, count(*) AS asked
        FROM stylist_hunts h, jsonb_array_elements_text(coalesce(h.brief_structured->'brands_asked', '[]')) x
        WHERE h.created_at > now() - make_interval(days => %s)
          AND NOT EXISTS (SELECT 1 FROM bank_brands b WHERE b.approved AND (lower(b.name) = lower(x) OR b.brand_id = lower(x)))
        GROUP BY lower(x) ORDER BY asked DESC LIMIT 30""", window)
    return _jsonable({"days": days, "no_results": no_results, "rewordings": rewordings,
                      "saved_not_clicked": saved_not_clicked, "rejection_reasons": reasons,
                      "weak_categories": weak_categories, "weak_languages": weak_languages,
                      "missing_brands": missing_brands, "unmatched_phrases": unmatched_phrases(days)})


# ------------------------------------------------------------------ health

def health(days: int = 30) -> Dict[str, Any]:
    window = (days,)
    hunts = _rows("""SELECT count(*) AS hunts,
                            avg(nullif(brief_structured->>'follow_ups', '')::numeric) AS follow_ups,
                            sum(CASE WHEN nothing_passed THEN 1 ELSE 0 END) AS nothing
                     FROM stylist_hunts WHERE created_at > now() - make_interval(days => %s)""", window)[0]
    violations = _rows("""
        SELECT count(*) AS results,
               sum(CASE WHEN jsonb_array_length(coalesce(r.matches->'violations', '[]')) > 0 THEN 1 ELSE 0 END) AS bad
        FROM stylist_results r JOIN stylist_hunts h ON h.hunt_id = r.hunt_id
        WHERE h.created_at > now() - make_interval(days => %s)""", window)[0]
    recovery = _rows("""
        WITH h AS (SELECT device_id, created_at, nothing_passed,
                          lead(created_at) OVER w AS next_at, lead(nothing_passed) OVER w AS next_nothing
                   FROM stylist_hunts WHERE created_at > now() - make_interval(days => %s)
                   WINDOW w AS (PARTITION BY device_id ORDER BY created_at))
        SELECT count(*) AS empty_hunts,
               sum(CASE WHEN next_at - created_at < interval '15 minutes' AND next_nothing IS FALSE THEN 1 ELSE 0 END)
                 AS recovered
        FROM h WHERE nothing_passed""", window)[0]
    evidence = dict((r["status"], r["n"]) for r in _rows("SELECT status, count(*) AS n FROM bank_evidence GROUP BY status"))
    fresh = _rows("""SELECT count(*) AS live,
                            sum(CASE WHEN last_checked > now() - interval '48 hours' THEN 1 ELSE 0 END) AS fresh
                     FROM bank_products WHERE status = 'live'""")[0]
    actions = dict((r["action"], r["n"]) for r in _rows(
        """SELECT action, count(*) AS n FROM stylist_actions
           WHERE created_at > now() - make_interval(days => %s) GROUP BY action""", window))

    def pct(a, b):
        return None if not b else round(100.0 * float(a or 0) / float(b), 1)

    total_ev = sum(evidence.values())
    return _jsonable({
        "days": days,
        "hard_constraint_violations": {"pct": pct(violations["bad"], violations["results"]),
                                       "count": violations["bad"] or 0, "results": violations["results"],
                                       "target": "near zero"},
        "evidence_accuracy": {"pct": pct(total_ev - evidence.get("disputed", 0), total_ev),
                              "disputed": evidence.get("disputed", 0), "by_status": evidence},
        "zero_result_recovery": {"pct": pct(recovery["recovered"], recovery["empty_hunts"]),
                                 "empty_hunts": recovery["empty_hunts"]},
        "follow_ups_per_hunt": None if hunts["follow_ups"] is None else round(float(hunts["follow_ups"]), 2),
        "hunts": hunts["hunts"],
        "freshness": {"pct": pct(fresh["fresh"], fresh["live"]), "live": fresh["live"]},
        "saves_and_clicks": {"saves": actions.get("heart", 0), "taps": actions.get("tap", 0),
                             "buys": actions.get("buy", 0), "dismissed": actions.get("dismiss", 0)},
        "bank": bank.counts(),
    })


# ------------------------------------------------------------------ sources

def sources() -> Dict[str, Any]:
    failing = _rows("""
        SELECT p.product_id, p.name, p.status, p.product_url, b.name AS brand,
               (SELECT l.error FROM bank_sources_log l WHERE l.product_id = p.product_id
                ORDER BY l.fetched_at DESC, l.id DESC LIMIT 1) AS last_error,
               (SELECT max(l.fetched_at) FROM bank_sources_log l WHERE l.product_id = p.product_id
                AND l.status <> 'failed') AS last_ok,
               (SELECT count(*) FROM bank_sources_log l WHERE l.product_id = p.product_id AND l.status = 'failed'
                AND l.fetched_at > coalesce((SELECT max(k.fetched_at) FROM bank_sources_log k
                                             WHERE k.product_id = p.product_id AND k.status <> 'failed'), 'epoch'))
                 AS failures_in_a_row
        FROM bank_products p JOIN bank_brands b ON b.brand_id = p.brand_id
        WHERE p.status = 'hidden_stale'
           OR EXISTS (SELECT 1 FROM bank_evidence e WHERE e.subject_id = p.product_id AND e.status = 'source_failed')
        ORDER BY p.name""")
    failed_imports = _rows("""SELECT url, error, max(fetched_at) AS last_tried, count(*) AS tries
                              FROM bank_sources_log WHERE product_id IS NULL AND status = 'failed'
                                AND fetched_at > now() - interval '14 days'
                              GROUP BY url, error ORDER BY last_tried DESC LIMIT 50""")
    return _jsonable({"failing": failing, "failed_imports": failed_imports})


# ------------------------------------------------------------------ test hunt and catalogues

def test_hunt(text: str, market: str = "CA") -> Dict[str, Any]:
    import stylist_search
    text = (text or "").strip()
    if not text:
        raise StudioError("Type what a shopper might ask.")
    if market not in ("CA", "US"):
        market = "CA"
    r = stylist_search.hunt(text[:500], market)
    _log("test_hunt", None, {"text": text[:200], "results": len(r.get("results") or [])})
    return _jsonable(r)


def catalog_start(brand_id: str, store_url: str = "", women_only: bool = False, use_ai: bool = False,
                  limit: int = 600) -> Dict[str, Any]:
    import bank_catalog
    try:
        r = bank_catalog.start(brand_id, store_url, women_only, use_ai, limit)
    except ValueError as exc:
        raise StudioError(str(exc))
    _log("catalog_start", brand_id, {"store_url": store_url, "women_only": women_only, "use_ai": use_ai})
    return r


def catalog_jobs() -> Dict[str, Any]:
    import bank_catalog
    import bank_rules
    return _jsonable({"jobs": bank_catalog.jobs(), "auto_approve": bank_rules.auto_approve_on()})


def catalog_stop(job_id: str) -> Dict[str, Any]:
    import bank_catalog
    bank_catalog.stop(job_id)
    _log("catalog_stop", job_id)
    return {"ok": True}


# ------------------------------------------------------------------ web routes

router = APIRouter(include_in_schema=False)


def _address(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def _need_session(request: Request, write: bool = False):
    if not token_ok(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Sign in again.")
    # A custom header can't be sent cross-site without permission, so no other site can make changes.
    if write and request.headers.get("x-studio") != "1":
        raise HTTPException(status_code=403, detail="Not allowed.")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        if str(exc) == "no database":
            raise HTTPException(status_code=503, detail="The database isn't connected.")
        raise


NO_STORE = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff"}


@router.get("/studio", response_class=HTMLResponse)
def studio_page():
    from studio_page import PAGE
    return HTMLResponse(PAGE, headers=NO_STORE)


@router.get("/studio/session")
def studio_session(request: Request):
    return JSONResponse({"enabled": enabled(), "signed_in": token_ok(request.cookies.get(COOKIE))},
                        headers=NO_STORE)


@router.post("/studio/login")
def studio_login(request: Request, payload: dict = Body(...)):
    if request.headers.get("x-studio") != "1":
        raise HTTPException(status_code=403, detail="Not allowed.")
    ok, message, token = login(str(payload.get("password") or ""), _address(request))
    resp = JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 401, headers=NO_STORE)
    if ok:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, secure=secure,
                        samesite="strict", path="/studio")
    return resp


@router.post("/studio/logout")
def studio_logout(request: Request):
    sign_out(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True}, headers=NO_STORE)
    resp.delete_cookie(COOKIE, path="/studio")
    return resp


@router.get("/studio/api/products")
def api_products(request: Request, status: str = "", q: str = "", category: str = ""):
    _need_session(request)
    return _call(products, status, q, category)


@router.get("/studio/api/products/{product_id}")
def api_product(request: Request, product_id: str):
    _need_session(request)
    return _call(product, product_id)


@router.post("/studio/api/import")
def api_import(request: Request, payload: dict = Body(...)):
    _need_session(request, write=True)
    return _call(import_product, str(payload.get("url") or ""))


@router.post("/studio/api/products/{product_id}/{action}")
def api_product_action(request: Request, product_id: str, action: str, payload: dict = Body(default={})):
    _need_session(request, write=True)
    v = payload.get("version")
    if action == "decide":
        return _call(decide, product_id, str(payload.get("decision") or ""), str(payload.get("reason") or ""),
                     v, bool(payload.get("publish")))
    if action == "tags":
        return _call(set_fit_tags, product_id, list(payload.get("tags") or []), v)
    if action == "flags":
        return _call(set_flags, product_id, payload.get("changes") or {}, v)
    if action == "refresh":
        return _call(refresh_now, product_id)
    if action == "take_down":
        return _call(take_down, product_id)
    if action == "retire":
        return _call(retire, product_id)
    raise HTTPException(status_code=404, detail="Not found")


@router.get("/studio/api/brands")
def api_brands(request: Request, status: str = "", evidence: str = "", category: str = "",
               style_led: str = "", approved: str = "", q: str = ""):
    _need_session(request)
    return _call(brands, status, evidence, category, style_led, approved, q)


@router.post("/studio/api/brands/{brand_id}")
def api_brand(request: Request, brand_id: str, payload: dict = Body(...)):
    _need_session(request, write=True)
    return _call(update_brand, brand_id, payload)


@router.get("/studio/api/standards")
def api_standards(request: Request):
    _need_session(request)
    return _call(standards)


@router.post("/studio/api/standards/{action}")
def api_standards_action(request: Request, action: str, payload: dict = Body(...)):
    _need_session(request, write=True)
    if action == "set":
        return _call(set_standard, str(payload.get("basic")), str(payload.get("slot")), str(payload.get("product_id")),
                     str(payload.get("note") or ""), payload.get("price_line"))
    if action == "remove":
        return _call(remove_standard, str(payload.get("basic")), str(payload.get("slot")), int(payload.get("rank") or 1))
    if action == "move":
        return _call(move_alternate, str(payload.get("basic")), int(payload.get("rank") or 0),
                     int(payload.get("direction") or 0))
    raise HTTPException(status_code=404, detail="Not found")


@router.get("/studio/api/vocabulary")
def api_vocabulary(request: Request, q: str = "", dimension: str = ""):
    _need_session(request)
    return _call(vocabulary, q, dimension)


@router.post("/studio/api/vocabulary/{action}")
def api_vocabulary_action(request: Request, action: str, payload: dict = Body(...)):
    _need_session(request, write=True)
    if action == "add_phrase":
        return _call(add_phrase, str(payload.get("concept_id")), str(payload.get("language") or "en"),
                     str(payload.get("phrase") or ""))
    if action == "remove_phrase":
        return _call(remove_phrase, str(payload.get("concept_id")), str(payload.get("language")),
                     str(payload.get("phrase")))
    if action == "add_concept":
        return _call(add_concept, str(payload.get("concept_id") or ""), str(payload.get("dimension") or ""),
                     str(payload.get("note") or ""))
    raise HTTPException(status_code=404, detail="Not found")


@router.get("/studio/api/backlog")
def api_backlog(request: Request, days: int = 30):
    _need_session(request)
    return _call(backlog, max(1, min(days, 365)))


@router.get("/studio/api/health")
def api_health(request: Request, days: int = 30):
    _need_session(request)
    return _call(health, max(1, min(days, 365)))


@router.get("/studio/api/sources")
def api_sources(request: Request):
    _need_session(request)
    return _call(sources)


@router.post("/studio/api/hunt")
def api_hunt(request: Request, payload: dict = Body(...)):
    _need_session(request, write=True)
    return _call(test_hunt, str(payload.get("text") or ""), str(payload.get("market") or "CA"))


@router.get("/studio/api/catalog")
def api_catalog(request: Request):
    _need_session(request)
    return _call(catalog_jobs)


@router.post("/studio/api/catalog/{action}")
def api_catalog_action(request: Request, action: str, payload: dict = Body(...)):
    _need_session(request, write=True)
    if action == "start":
        return _call(catalog_start, str(payload.get("brand_id") or ""), str(payload.get("store_url") or ""),
                     bool(payload.get("women_only")), bool(payload.get("use_ai")), int(payload.get("limit") or 600))
    if action == "stop":
        return _call(catalog_stop, str(payload.get("job_id") or ""))
    raise HTTPException(status_code=404, detail="Not found")
