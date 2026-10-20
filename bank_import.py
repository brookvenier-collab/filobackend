"""Product importer: a product URL in, a draft bank record out.

import_url(url) does, in order:
1. Fetch the page politely (bank_fetch). Shopify stores also give their
   public product data.
2. Read it (bank_extract) and pull out facts with their quotes (bank_facts).
3. Match the brand to the bank. The retailer is kept separately: a stockist
   is never treated as the brand.
4. Build a record that validates against product_schema.json, with one
   evidence entry per fact quoting the page.
5. Run Filo's existing score (fabric.py) and fair price (value.py), unchanged.
6. Check Filo's existing rules (quality floor, under 50% synthetic, no
   blocked or department-store shops, no knock-offs).
7. Suggest a decision. Brooklyn confirms it in Studio. Nothing goes live here.

refresh(product_id) re-reads a saved product: price and stock updates, page
change detection (a changed composition, origin or fit makes a new product
version and sends it back for review), and failed-source handling.
"""
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import bank
import bank_extract
import bank_facts
import bank_fetch
import bank_fx
import bank_seed
import bank_validate
import brands
import catalog
import events
import fabric
import value

log = logging.getLogger("filo.bank_import")

ANIMAL_FIBRES = {"wool", "merino", "lambswool", "cashmere", "alpaca", "mohair", "silk", "angora",
                 "yak", "camel", "leather", "suede", "down", "feather", "shearling", "fur"}
FABRIC_CATEGORY = {   # bank category -> the word fabric.py and value.py understand
    "t_shirt": "t-shirt", "long_sleeve": "long sleeve t-shirt", "tank": "tank top", "shirt": "shirt",
    "knitwear": "sweater", "cardigan": "cardigan", "sweatshirt": "sweatshirt", "denim": "jeans",
    "trousers": "trousers", "skirt": "skirt", "dress": "dress", "outerwear": "jacket",
    "leather_outerwear": "leather jacket",
}
CONFIDENCE = {"verified": 1.0, "brand_reported": 0.7, "inferred": 0.3}
KEY_FIELDS = ("materials.composition", "materials.fabric_weight", "construction.country_of_assembly",
              "fit.fit_tags")
RETRY_HOURS = (1, 6, 24)
CURRENCY_MARKS = [(r"\bC\$|\bCA\$|\bCAD\b", "CAD"), (r"\bUS\$|\bUSD\b", "USD"), (r"£|\bGBP\b", "GBP"),
                  (r"€|\bEUR\b", "EUR"), (r"\bA\$|\bAUD\b", "AUD")]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


# ------------------------------------------------------------------ brands

_brand_cache: Dict[str, dict] = {}


def _brands() -> Dict[str, dict]:
    """brand_id -> row. From the database when there is one (Studio's approvals), else the seed file."""
    conn = events._conn()
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT brand_id, name, official_url, approved FROM bank_brands")
                rows = cur.fetchall()
            if rows:
                return {r[0]: {"brand_id": r[0], "name": r[1], "official_url": r[2], "approved": r[3]} for r in rows}
        except Exception:                   # noqa: BLE001
            pass
        finally:
            conn.close()
    if not _brand_cache:
        for r in bank_seed.brand_rows():
            _brand_cache[r["brand_id"]] = {k: r[k] for k in ("brand_id", "name", "official_url", "approved")}
    return _brand_cache


def match_brand(page, url: str) -> Tuple[Optional[dict], str]:
    """(brand row, how it was matched). Never invents a brand."""
    all_brands = _brands()
    if page.brand:
        s = bank.slug(page.brand)
        for candidate in (s, re.sub(r"-(clothing|apparel|the-label|the-brand|studios?|co|london|nyc|official)$", "", s)):
            b = all_brands.get(candidate)
            if b:
                return b, "page brand"
    host = _host(url)
    for b in all_brands.values():
        if b["official_url"] and _host(b["official_url"]) == host:
            return b, "brand website"
    title = f" {(page.name or '').lower()} {urlsplit(url).path.lower().replace('-', ' ')} "
    best = None
    for b in all_brands.values():
        n = b["name"].lower()
        if len(n) >= 4 and re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(n), title):
            if best is None or len(n) > len(best["name"]):
                best = b
    return (best, "name in title") if best else (None, "not found")


def _is_brand_site(brand: Optional[dict], url: str) -> bool:
    return bool(brand and brand.get("official_url") and _host(brand["official_url"]) == _host(url))


# ------------------------------------------------------------------ prices

CODES = {"CAD": "CAD", "USD": "USD", "GBP": "GBP", "EUR": "EUR", "AUD": "AUD"}
_AMOUNT = r"(\d[\d,]*(?:\.\d{2})?)"


def _text_price(page) -> Tuple[Optional[float], Optional[str], Optional[str], Optional[float]]:
    """(price, currency, quote, was_price) from the page text, only when the currency is written.
    'C$47.99', '£99.75' and '$73.00 USD' all count; a bare '$73' doesn't, because $ alone could be CAD or USD."""
    for line in (page.page_text or "").splitlines():
        found = None
        for pattern, cur in CURRENCY_MARKS:
            m = re.search(r"(?:%s)\s?%s" % (pattern, _AMOUNT), line)
            if m:
                found = (bank_extract._num(m.group(1)), cur, m.start())
                break
        if not found:
            m = re.search(r"\$?\s?%s\s?(CAD|USD|GBP|EUR|AUD)\b" % _AMOUNT, line)
            if m:
                found = (bank_extract._num(m.group(1)), CODES[m.group(2)], m.start())
        if not found:
            continue
        price, cur, at = found
        was = re.search(r"(?i)(?:was|regular|reg\.?|retail|compare at)\s*(?:C\$|US\$|£|€|\$)?\s?%s" % _AMOUNT, line)
        was_price = bank_extract._num(was.group(1)) if was else None
        return price, cur, line[:200], (was_price if was_price and price and was_price > price else None)
    return None, None, None, None


def build_variants(page, product_id: str, checked: str) -> Tuple[List[dict], List[dict]]:
    """(variants, price facts). Each variant's price is what the page shows for it."""
    variants, facts = [], []
    offers = list(page.offers)
    if not offers:
        price, cur, quote, was = _text_price(page)
        if price is not None:
            offers = [bank_extract.Offer(price=price, currency=cur, sale_from=was, source="text")]
            facts.append({"field": "price", "quote": quote})
    seen = set()
    for i, o in enumerate(offers or [bank_extract.Offer()]):
        colour = o.colour or "As shown"
        size = o.size or "All sizes"
        key = (colour, size)
        if key in seen:
            continue
        seen.add(key)
        v = {"variant_id": f"{product_id}-v{i + 1}", "colour": colour, "size_label": size,
             "availability": o.availability or "unknown", "last_checked": checked}
        if o.sku:
            v["sku"] = str(o.sku)
        if o.gtin:
            v["gtin"] = str(o.gtin)
        if o.price is not None and o.currency:
            regular, current = o.price, o.price
            if o.sale_from and o.sale_from > o.price:
                regular, current = o.sale_from, o.price     # on sale: the regular price and what it costs now
            v["price"] = {"amount": round(regular, 2), "currency": o.currency}
            if current != regular:
                v["sale_price"] = {"amount": round(current, 2), "currency": o.currency}
            conv = bank_fx.convert(current, o.currency)
            if conv["price_cad"] is not None:
                v["price_cad"] = conv["price_cad"]
            if conv["price_usd"] is not None:
                v["price_usd"] = conv["price_usd"]
            if conv["fx_rate_id"]:
                v["fx_rate_id"] = conv["fx_rate_id"]
        variants.append(v)
    return variants, facts


def current_price(variant: dict) -> Optional[float]:
    p = variant.get("sale_price") or variant.get("price")
    return p["amount"] if p else None


# ------------------------------------------------------------------ the record

def _set(record: dict, path: str, value: Any):
    parts = path.split(".")
    node = record
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _fact_obj(f: dict, evidence_id: Optional[str]) -> dict:
    if f["status"] == "unknown":
        obj = {"value": None, "status": "unknown"}
        if f.get("raw"):
            obj["raw"] = f["raw"]
        if evidence_id:
            obj["evidence_ids"] = [evidence_id]
        return obj
    obj = {"value": f["value"], "status": f["status"], "source_type": f["source_type"],
           "evidence_ids": [evidence_id], "scope": f["scope"],
           "confidence": round(CONFIDENCE.get(f["source_type"], 0.5), 2)}
    if f.get("unit"):
        obj["unit"] = f["unit"]
    return obj


def _evidence(product_id: str, f: dict, url: str, checked: str, kind: str, n: int, page_hash: str,
              version: int = 1) -> dict:
    claim_value = f.get("raw") if f["status"] == "unknown" else f.get("value")
    if isinstance(claim_value, list):
        claim_value = ", ".join(f"{x['percent']}% {x['fibre']}" if isinstance(x, dict) else str(x) for x in claim_value)
    status_word = "says" if f["status"] != "unknown" else "says, but doesn't settle"
    return {
        "evidence_id": f"{product_id}:v{version}:{f['field']}:{n}",
        "claim": f"The page {status_word}: {f['field']} = {claim_value}"[:300],
        "field": f["field"],
        "subject_type": "product",
        "subject_id": product_id,
        "source_url": url,
        "source_kind": "ai_tagger" if f.get("by") == "ai" else kind,
        "source_type": "inferred" if f.get("status") == "from_photo" else "brand_reported",
        **({"quote": f["quote"][:240]} if f.get("quote") else {}),
        "scope": f.get("scope") or "this_style",
        "confidence": round(CONFIDENCE["brand_reported"], 2),
        "checked_at": checked,
        "page_hash": page_hash,
        "status": "current",
        "retrieval": {"method": "page_text" if f.get("by") != "ai" else "page_text", "attempts": 1},
    }


def _composition_text(comp: List[dict]) -> str:
    return ", ".join(f"{c['percent']}% {c['fibre']}" for c in comp)


def filo_assessment(composition: Optional[List[dict]], category: Optional[str], price_cad: Optional[float],
                    name: Optional[str], origin: Optional[str]) -> Dict[str, Any]:
    """Filo's existing score and fair price, unchanged, plus the existing rule checks."""
    out = {"filo_score": None, "fair_price": None, "rules_ok": False, "rule_problems": [],
           "synthetic_pct": None}
    if not composition:
        out["rule_problems"].append("No composition stated on the page.")
        return out
    comp = _composition_text(composition)
    cat_word = FABRIC_CATEGORY.get(category or "", category or "")
    item = {"composition": comp, "category": cat_word, "construction": [], "group": catalog.garment_group(cat_word)}
    result = fabric.analyze(item)
    score = result.get("score")
    if score is not None:
        out["filo_score"] = int(round(score * 10))
    detail = fabric.quality_detail(comp, construction=[])
    if detail:
        out["synthetic_pct"] = fabric.synthetic_pct(detail["matched"])
        tag = f"{comp}. Made in {origin}" if origin else comp
        fair = value.assess({"composition": comp, "category": cat_word, "name": name,
                             "price": price_cad, "country": "CA", "tag_text": tag}, detail, score)
        if fair:
            out["fair_price"] = {k: fair.get(k) for k in ("fair_low", "fair_high", "currency", "range_line",
                                                          "label", "level", "line", "confidence")}
    problems = []
    if score is None or score < catalog.QUALITY_FLOOR:
        problems.append(f"Below Filo's quality floor ({catalog.QUALITY_FLOOR * 10:.0f}).")
    if out["synthetic_pct"] is not None and out["synthetic_pct"] >= catalog.MAX_SYNTHETIC:
        problems.append(f"{out['synthetic_pct']:.0f}% synthetic. Filo only shows pieces under {catalog.MAX_SYNTHETIC}%.")
    out["rule_problems"] = problems
    out["rules_ok"] = not problems
    return out


def suggest_decision(facts_by_field: Dict[str, dict], variants: List[dict], filo: dict,
                     brand: Optional[dict], retailer_ok: bool, is_brand_site: bool) -> Tuple[str, str, List[str]]:
    """(decision, reason, missing evidence). A suggestion for Brooklyn, never final."""
    missing = []
    comp = facts_by_field.get("materials.composition")
    origin = facts_by_field.get("construction.country_of_assembly")
    if not comp or comp["status"] != "confirmed":
        missing.append("composition")
    if not origin or origin["status"] != "confirmed":
        missing.append("where it's made" + (f" (the page says \"{origin['raw']}\", which doesn't name a country)"
                                            if origin and origin.get("raw") else ""))
    if not any(current_price(v) is not None for v in variants):
        missing.append("a price with its currency")
    if not any(v["availability"] in ("in_stock", "low_stock", "preorder", "made_to_order") for v in variants):
        missing.append("stock")
    w = facts_by_field.get("materials.fabric_weight")
    if not w:
        missing.append("fabric weight")
    if comp and comp["status"] == "confirmed":
        fibres = {c["fibre"] for c in comp["value"]}
        if fibres & ANIMAL_FIBRES:
            missing.append("animal welfare and traceability")
    if "composition" in missing and not is_brand_site:
        missing.append("try the brand's own product page, which usually lists more")

    if "composition" in missing:
        return "insufficient_evidence", "The page doesn't state what it's made of.", missing
    if not retailer_ok:
        return "hold", "The shop is on Filo's blocked list.", missing
    if not filo["rules_ok"]:
        return "hold", " ".join(filo["rule_problems"]), missing
    if brand is None:
        return "hold", "The brand isn't in Filo's bank yet.", missing
    core_gaps = [m for m in missing if m.startswith(("where it's made",))]
    if not core_gaps:
        return "pass", "Composition and origin are stated, and it meets Filo's rules.", missing
    return "conditional", "Meets Filo's rules on what's stated, with gaps listed.", missing


def build_record(url: str, page, facts: List[dict], checked: str, page_hash: str,
                 version: int = 1, product_id: Optional[str] = None) -> Dict[str, Any]:
    brand, matched_by = match_brand(page, url)
    retailer = _host(url)
    brand_site = _is_brand_site(brand, url)
    category = bank_facts.category_of(page.name, page.breadcrumbs)
    name = page.name or "Untitled product"
    brand_id = brand["brand_id"] if brand else "unknown-brand"
    product_id_new = f"{brand_id}-{bank.slug(re.sub(re.escape(brand['name']) if brand else '^$', '', name, flags=re.I))[:60]}" \
                 f"-{hashlib.sha1(url.encode()).hexdigest()[:6]}"
    product_id = product_id or product_id_new
    variants, price_facts = build_variants(page, product_id, checked)

    kind = "brand_product_page" if brand_site else "retailer_page"
    if "json-ld" in page.sources or "shopify" in page.sources:
        kind_struct = "structured_data"
    else:
        kind_struct = kind
    evidence, by_field, n = [], {}, 0
    record = {
        "product_id": product_id, "brand_id": brand_id, "name": name,
        "category": category or "uncategorized", "product_url": url, "retailer": retailer,
        "is_resale_listing": "therealreal" in retailer,
        "images": [{"url": u, "kind": "front" if i == 0 else "detail",
                    "licence_note": "Link only. Not stored by Filo."} for i, u in enumerate(page.images[:6])],
        "variants": variants, "status": "draft", "first_seen": checked, "last_checked": checked,
        "product_version": str(version),
    }
    gender = bank_facts.gender_of(page.name, url, page.breadcrumbs)
    if gender:
        record["gender_target"] = gender
    fit_tags = []
    for f in facts:
        n += 1
        ev = _evidence(product_id, f, url, checked, kind, n, page_hash, version)
        evidence.append(ev)
        if f["field"].startswith("fit.fit_tags."):
            tag = f["field"].rsplit(".", 1)[-1]
            fit_tags.append({"tag": tag, "fact": _fact_obj(f, ev["evidence_id"])})
            continue
        by_field[f["field"]] = f
        _set(record, f["field"], _fact_obj(f, ev["evidence_id"]))
    if fit_tags:
        _set(record, "fit.fit_tags", fit_tags)
        by_field["fit.fit_tags"] = {"status": "confirmed"}
    for pf in price_facts:
        n += 1
        evidence.append(_evidence(product_id, {"field": "price", "value": current_price(variants[0]) if variants else None,
                                               "status": "confirmed", "source_type": "brand_reported",
                                               "scope": "this_variant", "quote": pf["quote"]},
                                  url, checked, kind, n, page_hash, version))
    if kind_struct == "structured_data" and any("price" in v for v in variants):
        n += 1
        evidence.append({**_evidence(product_id, {"field": "price", "value": current_price(variants[0]),
                                                  "status": "confirmed", "source_type": "brand_reported",
                                                  "scope": "this_variant", "quote": ""},
                                     url, checked, "structured_data", n, page_hash, version),
                         "retrieval": {"method": "shopify_json" if "shopify" in page.sources else "schema_org",
                                       "attempts": 1}})

    # Say "unknown" out loud for the fields that matter, so Studio shows the gap.
    for path in KEY_FIELDS[:3] + ("construction.care",):
        if path not in by_field:
            _set(record, path, {"value": None, "status": "unknown"})
    comp_fact = by_field.get("materials.composition")
    comp = comp_fact["value"] if comp_fact and comp_fact["status"] == "confirmed" else None
    if comp and {c["fibre"] for c in comp} & ANIMAL_FIBRES:
        for p in ("species", "origin", "farm_processor_traceability"):
            _set(record, f"animal_welfare.{p}", {"value": None, "status": "unknown"})
    _set(record, "fit.opacity", {"value": None, "status": "unknown"})

    origin_fact = by_field.get("construction.country_of_assembly")
    origin = origin_fact["value"] if origin_fact and origin_fact["status"] == "confirmed" else None
    prices_cad = [v.get("price_cad") for v in variants if v.get("price_cad") is not None]
    filo = filo_assessment(comp, record["category"], min(prices_cad) if prices_cad else None, name, origin)
    retailer_ok = not (brands.is_blocked(retailer) or brands.is_department_store(retailer)
                       or brands.looks_like_knockoff(retailer, name))
    decision, reason, missing = suggest_decision(by_field, variants, filo, brand, retailer_ok, brand_site)
    record["filo"] = {
        "decision": decision, "decision_reason": reason, "missing_evidence": missing,
        "is_unique_find": False, "is_independent_label": False,
    }
    if filo["filo_score"] is not None:
        record["filo"]["filo_score"] = filo["filo_score"]
    if (filo["fair_price"] or {}).get("range_line"):
        record["filo"]["fair_price_verdict"] = filo["fair_price"]["range_line"]
    if not category:
        record["filo"]["missing_evidence"].append("garment type (set it in Studio)")
    record["status"] = "draft" if decision == "insufficient_evidence" else "in_review"
    return {
        "record": record, "evidence": evidence, "brand": brand, "brand_matched_by": matched_by,
        "category_found": category is not None, "filo": filo, "page_sources": page.sources,
    }


# ------------------------------------------------------------------ import

def _log_source(url, status, error=None, page_hash=None, product_id=None, method="page_text"):
    conn = events._conn()
    if conn is None:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO bank_sources_log (url, product_id, method, status, error, page_hash)
                           VALUES (%s, %s, %s, %s, %s, %s)""", (url, product_id, method, status, error, page_hash))
    except Exception:                       # noqa: BLE001
        pass
    finally:
        conn.close()


def content_hash(page) -> str:
    """A hash of what the page says about the product, not its raw HTML (which changes on every load)."""
    return hashlib.sha256((page.all_text + json.dumps([o.__dict__ for o in page.offers], sort_keys=True,
                                                      default=str)).encode()).hexdigest()


def text_hash(page) -> str:
    """Like content_hash, without prices and stock: if this is unchanged, the facts are too."""
    return hashlib.sha256(page.all_text.encode()).hexdigest()


def read_page(url: str):
    """(page, fetch result). Shopify product data is merged in when the store offers it."""
    got = bank_fetch.fetch(url)
    if not got.ok:
        return None, got
    page = bank_extract.extract(got.text, got.url or url)
    got.page_hash = None
    if page.is_shopify:
        js = bank_extract.shopify_json_url(got.url or url)
        if js:
            sj = bank_fetch.fetch(js)
            if sj.ok:
                try:
                    bank_extract.add_shopify(page, json.loads(sj.text))
                except ValueError:
                    pass
    got.page_hash = text_hash(page)
    return page, got


def import_url(url: str, save: bool = True) -> Dict[str, Any]:
    url = (url or "").strip()
    page, got = read_page(url)
    if page is None:
        _log_source(url, "failed", got.error)
        return {"ok": False, "url": url, "error": got.error or "couldn't read the page"}
    facts = bank_facts.all_facts(page)
    checked = now_iso()
    built = build_record(url, page, facts, checked, got.page_hash)
    ev_ids = [e["evidence_id"] for e in built["evidence"]]
    problems = bank_validate.validate_product(built["record"], ev_ids)
    problems += [f"evidence {e['evidence_id']}: {p}" for e in built["evidence"] for p in bank_validate.validate_evidence(e)]
    saved = False
    if save and not problems and built["brand"] is not None:
        saved = save_product(built["record"], built["evidence"])
    _log_source(url, "ok", None, got.page_hash, built["record"]["product_id"])
    return {"ok": not problems, "url": url, "product_id": built["record"]["product_id"],
            "saved": saved, "problems": problems, **built}


def _facts_rows(record: dict) -> List[Tuple[str, str, dict]]:
    """(variant_id, field path, Fact) for every fact in a record."""
    rows = []
    for path, fact in bank_validate._walk_facts(record):
        if path.startswith("variants"):
            continue
        rows.append(("", path, fact))
    return rows


def save_product(record: dict, evidence: List[dict]) -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            version = int(record.get("product_version") or 1)
            f = record.get("filo", {})
            cur.execute("""
                INSERT INTO bank_products (product_id, brand_id, name, product_version, category, gender_target,
                    product_url, retailer, is_resale_listing, record, decision, decision_reason, status,
                    first_seen, last_checked)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT (product_id) DO UPDATE SET
                    name = EXCLUDED.name, product_version = EXCLUDED.product_version,
                    category = EXCLUDED.category, gender_target = EXCLUDED.gender_target,
                    record = EXCLUDED.record,
                    decision = CASE WHEN bank_products.decided_by IS NOT NULL
                                    AND bank_products.product_version = EXCLUDED.product_version
                                    THEN bank_products.decision ELSE EXCLUDED.decision END,
                    decision_reason = CASE WHEN bank_products.decided_by IS NOT NULL
                                    AND bank_products.product_version = EXCLUDED.product_version
                                    THEN bank_products.decision_reason ELSE EXCLUDED.decision_reason END,
                    decided_by = CASE WHEN bank_products.product_version = EXCLUDED.product_version
                                    THEN bank_products.decided_by ELSE NULL END,
                    status = CASE WHEN bank_products.product_version <> EXCLUDED.product_version
                                  THEN 'in_review'
                                  WHEN bank_products.status IN ('live','hidden_stale','hidden_out_of_stock')
                                  THEN bank_products.status ELSE EXCLUDED.status END,
                    last_checked = EXCLUDED.last_checked
            """, (record["product_id"], record["brand_id"], record["name"], version, record["category"],
                  record.get("gender_target"), record["product_url"], record.get("retailer"),
                  record.get("is_resale_listing", False), json.dumps(record), f.get("decision"),
                  f.get("decision_reason"), record["status"], record["first_seen"], record["last_checked"]))
            for e in evidence:
                cur.execute("""
                    INSERT INTO bank_evidence (evidence_id, claim, field, subject_type, subject_id, source_url,
                        source_kind, source_type, quote, sample_size, scope, confidence, checked_at, page_hash,
                        status, retrieval)
                    VALUES (%(evidence_id)s,%(claim)s,%(field)s,%(subject_type)s,%(subject_id)s,%(source_url)s,
                        %(source_kind)s,%(source_type)s,%(quote)s,%(sample_size)s,%(scope)s,%(confidence)s,
                        %(checked_at)s,%(page_hash)s,%(status)s,%(retrieval)s::jsonb)
                    ON CONFLICT (evidence_id) DO UPDATE SET claim = EXCLUDED.claim, quote = EXCLUDED.quote,
                        checked_at = EXCLUDED.checked_at, page_hash = EXCLUDED.page_hash, status = 'current'
                """, {**e, "quote": e.get("quote"), "confidence": e.get("confidence"),
                      "page_hash": e.get("page_hash"), "sample_size": e.get("sample_size"), "retrieval": json.dumps(e.get("retrieval"))})
            cur.execute("DELETE FROM bank_facts WHERE product_id = %s AND product_version = %s",
                        (record["product_id"], version))
            for variant_id, path, fact in _facts_rows(record):
                cur.execute("""
                    INSERT INTO bank_facts (product_id, product_version, variant_id, field, value, unit, status,
                        source_type, scope, confidence, evidence_ids)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                """, (record["product_id"], version, variant_id, path, json.dumps(fact.get("value")),
                      fact.get("unit"), fact["status"], fact.get("source_type"), fact.get("scope"),
                      fact.get("confidence"), fact.get("evidence_ids") or []))
            cur.execute("DELETE FROM bank_variants WHERE product_id = %s", (record["product_id"],))
            for v in record["variants"]:
                cur.execute("""
                    INSERT INTO bank_variants (variant_id, product_id, sku, gtin, colour, colour_family, size_label,
                        size_system, price, currency, sale_price, price_cad, price_usd, fx_rate_id, availability,
                        ships_to, last_checked)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (v["variant_id"], record["product_id"], v.get("sku"), v.get("gtin"), v["colour"],
                      v.get("colour_family"), v["size_label"], v.get("size_system"),
                      (v.get("price") or {}).get("amount"), (v.get("price") or {}).get("currency"),
                      (v.get("sale_price") or {}).get("amount"), v.get("price_cad"), v.get("price_usd"),
                      v.get("fx_rate_id"), v["availability"], v.get("ships_to") or [], v["last_checked"]))
        return True
    except Exception as exc:                # noqa: BLE001
        log.warning("bank_import: save failed for %s (%s)", record.get("product_id"), exc)
        return False
    finally:
        conn.close()


def load_product(product_id: str) -> Optional[dict]:
    conn = events._conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT record, status, decision, decided_by, product_version, product_url
                           FROM bank_products WHERE product_id = %s""", (product_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("""SELECT evidence_id, field, source_type, quote, status, checked_at, page_hash
                           FROM bank_evidence WHERE subject_id = %s ORDER BY evidence_id""", (product_id,))
            full = [dict(zip(("evidence_id", "field", "source_type", "quote", "status", "checked_at", "page_hash"), r))
                    for r in cur.fetchall()]
        ev = [{k: v for k, v in e.items() if k != "page_hash"} for e in full]
        return {"record": row[0], "status": row[1], "decision": row[2], "decided_by": row[3],
                "product_version": row[4], "product_url": row[5], "evidence": ev, "evidence_full": full}
    finally:
        conn.close()


# ------------------------------------------------------------------ refresh

def _key_facts(record: dict) -> Dict[str, Any]:
    """The facts that, if they change, need Brooklyn to look again."""
    out = {}
    for path in ("materials.composition", "construction.country_of_assembly", "materials.fabric_weight",
                 "materials.lining"):
        node = record
        for p in path.split("."):
            node = node.get(p, {}) if isinstance(node, dict) else {}
        out[path] = (node.get("status"), json.dumps(node.get("value"), sort_keys=True)) if node else None
    tags = (record.get("fit") or {}).get("fit_tags") or []
    out["fit.fit_tags"] = sorted(t["tag"] for t in tags)
    return out


def _failures(url: str) -> int:
    """How many reads of this URL have failed in a row."""
    conn = events._conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM bank_sources_log WHERE url = %s ORDER BY fetched_at DESC, id DESC LIMIT 5",
                        (url,))
            n = 0
            for (s,) in cur.fetchall():
                if s != "failed":
                    break
                n += 1
            return n
    finally:
        conn.close()


def mark_source_failed(product_id: str, url: str, error: str) -> dict:
    """Never reuse old information as current: evidence goes stale-failed and the product is hidden."""
    _log_source(url, "failed", error, product_id=product_id)
    conn = events._conn()
    if conn is None:
        return {"hidden": False}
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE bank_evidence SET status = 'source_failed' WHERE subject_id = %s", (product_id,))
            cur.execute("""UPDATE bank_products SET status = 'hidden_stale'
                           WHERE product_id = %s AND status = 'live'""", (product_id,))
            hidden = cur.rowcount > 0
        return {"hidden": hidden}
    finally:
        conn.close()


def refresh(product_id: str) -> Dict[str, Any]:
    saved = load_product(product_id)
    if not saved:
        return {"ok": False, "error": "no such product"}
    url = saved["product_url"]
    page, got = read_page(url)
    if page is None:
        failed = mark_source_failed(product_id, url, got.error or "failed")
        n = _failures(url)
        retry_in = RETRY_HOURS[min(max(n, 1), len(RETRY_HOURS)) - 1]
        return {"ok": False, "error": got.error, "hidden": failed["hidden"], "failures_in_a_row": n,
                "retry_in_hours": retry_in}
    old = saved["record"]
    old_hashes = {e.get("page_hash") for e in saved.get("evidence_full", [])}
    same_text = got.page_hash in old_hashes
    # Same words on the page: only price and stock can have moved, so no AI call and no new version.
    facts = bank_facts.rule_facts(page) if same_text else bank_facts.all_facts(page)
    next_version = int(saved["product_version"]) + 1
    built = build_record(url, page, facts, now_iso(), got.page_hash, next_version, product_id)
    new = built["record"]
    new["first_seen"] = old.get("first_seen", new["first_seen"])
    if same_text:
        kept = dict(old)
        kept["variants"] = [{**v, "variant_id": f"{product_id}-v{i + 1}"} for i, v in enumerate(new["variants"])]
        kept["last_checked"] = new["last_checked"]
        new, changed = kept, False
        evidence = []
    else:
        changed = _key_facts(old) != _key_facts(new)
        evidence = built["evidence"]
        if not changed:
            # Wording moved but the key facts didn't: keep the reviewed record and its evidence.
            new = {**old, "variants": new["variants"], "last_checked": new["last_checked"]}
            evidence = []
    version = next_version if changed else int(saved["product_version"])
    new["product_version"] = str(version)
    ok_variants = [v for v in new["variants"] if v["availability"] in ("in_stock", "low_stock", "preorder", "made_to_order")]
    status_was = saved["status"]
    save_product(new, evidence)
    conn = events._conn()
    if conn is not None:
        try:
            with conn, conn.cursor() as cur:
                cur.execute("UPDATE bank_evidence SET status = 'current' WHERE subject_id = %s AND status = 'source_failed'",
                            (product_id,))
                if changed:
                    cur.execute("UPDATE bank_products SET status = 'in_review', decided_by = NULL WHERE product_id = %s",
                                (product_id,))
                elif status_was in ("live", "hidden_stale", "hidden_out_of_stock"):
                    target = "live" if ok_variants else "hidden_out_of_stock"
                    cur.execute("UPDATE bank_products SET status = %s WHERE product_id = %s", (target, product_id))
        finally:
            conn.close()
    _log_source(url, "changed" if changed else "ok", None, got.page_hash, product_id)
    return {"ok": True, "product_id": product_id, "changed": changed, "product_version": version,
            "in_stock": bool(ok_variants)}


def due_for_refresh(limit: int = 200) -> List[str]:
    """Products to re-read: shown or recently hidden, last checked over a day ago, or failing and due a retry."""
    conn = events._conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT product_id FROM bank_products
                           WHERE status IN ('live','hidden_stale','hidden_out_of_stock','in_review')
                             AND last_checked < now() - interval '20 hours'
                           ORDER BY last_checked LIMIT %s""", (limit,))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def run_refresh() -> Dict[str, int]:
    bank_fx.update()
    done = failed = 0
    for pid in due_for_refresh():
        r = refresh(pid)
        done += 1
        failed += 0 if r.get("ok") else 1
    return {"refreshed": done, "failed": failed}


def start_refresh_thread():
    """Nightly-ish: exchange rates and product re-reads, about every 6 hours while the server is up."""
    if not events.DATABASE_URL:
        return

    def loop():
        time.sleep(300)
        while True:
            try:
                log.info("bank refresh: %s", run_refresh())
            except Exception as exc:        # noqa: BLE001
                log.warning("bank refresh failed (%s)", exc)
            time.sleep(6 * 3600)

    threading.Thread(target=loop, name="bank-refresh", daemon=True).start()
