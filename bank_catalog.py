"""Bring in a whole brand catalogue at once.

Studio's "Bring in catalogue" button on an approved brand starts a job here. It reads every
product the brand sells, keeps the women's and unisex clothing, builds a record for each one
the same way a single import does (every fact quoted from the brand's own words), and lets
the rules in bank_rules sort it: live, held, waiting for evidence, or waiting for Brooklyn.

How it reads a store:
1. Shopify stores (most of Filo's brands): the public /products.json list, 250 products per
   request, plus /cart.js for the store's currency. Fast and polite.
2. Anything else: the site's sitemap, then each product page one by one (one page every few
   seconds per site, as bank_fetch already enforces).

Robots.txt is followed for every request (bank_fetch). One job runs at a time.
"""
import json
import logging
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import bank_extract
import bank_facts
import bank_fetch
import bank_import
import bank_rules
import bank_validate
import events

log = logging.getLogger("filo.bank_catalog")

MAX_PRODUCTS = 600              # per job
SHOPIFY_PAGE = 250
SHOPIFY_MAX_PAGES = 12
SITEMAP_MAX_FILES = 6
CLOTHING = {"t_shirt", "long_sleeve", "tank", "shirt", "knitwear", "cardigan", "sweatshirt", "denim",
            "trousers", "outerwear", "leather_outerwear", "dress", "skirt"}
PRODUCT_PATH = re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:products?|p|shop)/[^/?#]+/?$", re.I)

_run_lock = threading.Lock()
_stop: Dict[str, bool] = {}


# ------------------------------------------------------------------ helpers

def store_base(url: str) -> str:
    """https://shop.com/en-ca/anything -> https://shop.com/en-ca (keeps a locale folder, drops the rest)."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    m = re.match(r"^/([a-z]{2}(?:-[a-z]{2})?)(?:/|$)", parts.path, re.I)
    path = "/" + m.group(1) if m else ""
    return urlunsplit((parts.scheme or "https", parts.netloc.lower(), path, "", ""))


def _json(url: str) -> Optional[Any]:
    got = bank_fetch.fetch(url)
    if not got.ok:
        return None
    try:
        return json.loads(got.text)
    except ValueError:
        return None


def gender_ok(title: str, url: str, product_type: str, tags: List[str], brand_gender: Optional[str],
              women_only_brand: bool) -> Tuple[bool, str]:
    """(keep, reason). Women's and unisex only."""
    g = bank_facts.gender_of(title, url, [product_type or ""] + list(tags or []))
    if g in ("women", "unisex"):
        return True, g
    if g == "men":
        return False, "men"
    if women_only_brand or brand_gender in ("women",):
        return True, "women (brand)"
    return False, "unclear"


def same_brand(vendor: str, brand: dict) -> bool:
    """A store can sell other labels too. Keep only this brand's own pieces."""
    import bank
    v = re.sub(r"-(clothing|studio|co|official|store|shop)$", "", bank.slug(vendor or ""))
    b = {bank.slug(brand.get("name") or ""), brand.get("brand_id") or ""}
    return any(x and (v == x or v.startswith(x) or x.startswith(v)) for x in b) if v else True


def shopify_page(product: dict, base: str, currency: Optional[str]) -> bank_extract.Page:
    url = f"{base}/products/{product.get('handle')}"
    page = bank_extract.Page(url=url, name=product.get("title"), brand=product.get("vendor"), is_shopify=True)
    if product.get("product_type"):
        page.breadcrumbs = [product["product_type"]]
    page.offers = [bank_extract.Offer(currency=currency)]
    bank_extract.add_shopify(page, product)
    if "shopify" not in page.sources:
        page.offers = []
    page.page_text = ""
    return page


def _sitemap_urls(base: str, limit: int) -> List[str]:
    parts = urlsplit(base)
    root = f"{parts.scheme}://{parts.netloc}"
    seen, out, queue = set(), [], [root + "/sitemap.xml"]
    files = 0
    while queue and files < SITEMAP_MAX_FILES and len(out) < limit * 3:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        got = bank_fetch.fetch(sm)
        files += 1
        if not got.ok:
            continue
        try:
            tree = ET.fromstring(got.text.encode("utf-8", "ignore"))
        except ET.ParseError:
            continue
        locs = [e.text.strip() for e in tree.iter() if e.tag.endswith("loc") and e.text]
        if tree.tag.endswith("sitemapindex"):
            queue += sorted(locs, key=lambda u: 0 if "product" in u.lower() else 1)
        else:
            out += [u for u in locs if PRODUCT_PATH.search(urlsplit(u).path)]
    prefix = urlsplit(base).path
    if prefix:
        local = [u for u in out if urlsplit(u).path.startswith(prefix + "/")]
        out = local or out
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ jobs

def _job_update(job_id: str, **fields):
    conn = events._conn()
    if conn is None:
        return
    sets, args = [], []
    for k, v in fields.items():
        if k == "counts":
            sets.append("counts = %s::jsonb"); args.append(json.dumps(v))
        elif k == "finished":
            sets.append("finished_at = now()")
        else:
            sets.append(f"{k} = %s"); args.append(v)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"UPDATE bank_catalog_jobs SET {', '.join(sets)} WHERE job_id = %s", args + [job_id])
    finally:
        conn.close()


def jobs(limit: int = 30) -> List[dict]:
    conn = events._conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT j.job_id, j.brand_id, b.name, j.store_url, j.method, j.status, j.found, j.counts,
                                  j.error, j.started_at, j.finished_at
                           FROM bank_catalog_jobs j JOIN bank_brands b ON b.brand_id = j.brand_id
                           ORDER BY j.started_at DESC LIMIT %s""", (limit,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _brand(brand_id: str) -> Optional[dict]:
    conn = events._conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT brand_id, name, official_url, approved, gender_target FROM bank_brands WHERE brand_id = %s",
                        (brand_id,))
            r = cur.fetchone()
            return dict(zip(("brand_id", "name", "official_url", "approved", "gender_target"), r)) if r else None
    finally:
        conn.close()


def start(brand_id: str, store_url: str = "", women_only_brand: bool = False, use_ai: bool = False,
          limit: int = MAX_PRODUCTS, background: bool = True) -> Dict[str, Any]:
    brand = _brand(brand_id)
    if not brand:
        raise ValueError("No such brand.")
    if not brand["approved"]:
        raise ValueError("Approve the brand first. Only approved brands can bring in a catalogue.")
    url = (store_url or brand.get("official_url") or "").strip()
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
        raise ValueError("Add the brand's store link, starting with https://")
    if any(j["status"] in ("queued", "running") for j in jobs(10)):
        raise ValueError("A catalogue is already coming in. Wait for it to finish, or stop it.")
    job_id = uuid.uuid4().hex[:12]
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO bank_catalog_jobs (job_id, brand_id, store_url) VALUES (%s, %s, %s)",
                    (job_id, brand_id, store_base(url)))
    conn.close()
    args = (job_id, brand, store_base(url), women_only_brand, use_ai, max(1, min(int(limit), 2000)))
    if background:
        threading.Thread(target=run, args=args, daemon=True, name=f"catalog-{job_id}").start()
    else:
        run(*args)
    return {"job_id": job_id}


def stop(job_id: str):
    _stop[job_id] = True
    _job_update(job_id, status="stopped", finished=True)


def run(job_id: str, brand: dict, base: str, women_only_brand: bool, use_ai: bool, limit: int):
    counts = {k: 0 for k in ("live", "hidden_out_of_stock", "review", "held", "waiting", "spot_check",
                             "skipped_men", "skipped_unclear_gender", "skipped_not_clothing",
                             "skipped_other_brand", "invalid", "failed", "kept_brooklyn")}
    with _run_lock:
        _job_update(job_id, status="running")
        try:
            data = _json(f"{base}/products.json?limit={SHOPIFY_PAGE}&page=1")
            if isinstance(data, dict) and isinstance(data.get("products"), list):
                _job_update(job_id, method="shopify")
                _run_shopify(job_id, brand, base, data, women_only_brand, use_ai, limit, counts)
            else:
                _job_update(job_id, method="sitemap")
                _run_sitemap(job_id, brand, base, women_only_brand, use_ai, limit, counts)
            if not _stop.get(job_id):
                _job_update(job_id, status="done", counts=counts, finished=True)
        except Exception as exc:            # noqa: BLE001
            log.warning("bank_catalog: job %s failed (%s)", job_id, exc)
            _job_update(job_id, status="failed", error=str(exc)[:300], counts=counts, finished=True)
        finally:
            _stop.pop(job_id, None)
    try:
        import bank_embed
        bank_embed.refresh_missing()
    except Exception as exc:                # noqa: BLE001
        log.warning("bank_catalog: embeddings not updated (%s)", exc)


def _currency(base: str) -> Optional[str]:
    cart = _json(f"{base}/cart.js")
    if isinstance(cart, dict) and isinstance(cart.get("currency"), str) and len(cart["currency"]) == 3:
        return cart["currency"].upper()
    return None


def _run_shopify(job_id, brand, base, first, women_only_brand, use_ai, limit, counts):
    currency = _currency(base)
    page_no, data, seen = 1, first, 0
    while data and data.get("products") and seen < limit and page_no <= SHOPIFY_MAX_PAGES:
        for product in data["products"]:
            if _stop.get(job_id) or seen >= limit:
                return
            seen += 1
            tags = product.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]
            url = f"{base}/products/{product.get('handle')}"
            keep, why = gender_ok(product.get("title"), url, product.get("product_type"), tags,
                                  brand.get("gender_target"), women_only_brand)
            if not keep:
                counts["skipped_men" if why == "men" else "skipped_unclear_gender"] += 1
                continue
            if currency is None:
                currency = _currency_from_page(url)
            page = shopify_page(product, base, currency)
            _one(page, brand, use_ai, counts)
            if seen % 20 == 0:
                _job_update(job_id, found=seen, counts=counts)
        page_no += 1
        data = _json(f"{base}/products.json?limit={SHOPIFY_PAGE}&page={page_no}")
    _job_update(job_id, found=seen, counts=counts)


def _currency_from_page(url: str) -> Optional[str]:
    got = bank_fetch.fetch(url)
    if not got.ok:
        return None
    page = bank_extract.extract(got.text, url)
    return next((o.currency for o in page.offers if o.currency), None)


def _run_sitemap(job_id, brand, base, women_only_brand, use_ai, limit, counts):
    urls = _sitemap_urls(base, limit)
    if not urls:
        raise RuntimeError("Couldn't find the brand's product list (no Shopify list and no product sitemap).")
    seen = 0
    for url in urls:
        if _stop.get(job_id) or seen >= limit:
            return
        page, got = bank_import.read_page(url)
        seen += 1
        if page is None:
            counts["failed"] += 1
            continue
        keep, why = gender_ok(page.name, url, " ".join(page.breadcrumbs), [], brand.get("gender_target"),
                              women_only_brand)
        if not keep:
            counts["skipped_men" if why == "men" else "skipped_unclear_gender"] += 1
            continue
        _one(page, brand, use_ai, counts, got.page_hash)
        if seen % 10 == 0:
            _job_update(job_id, found=seen, counts=counts)
    _job_update(job_id, found=seen, counts=counts)


def _one(page, brand: dict, use_ai: bool, counts: Dict[str, int], page_hash: Optional[str] = None) -> Optional[str]:
    """Build, check, save and sort one product. Returns its product_id when saved."""
    try:
        if page.brand and not same_brand(page.brand, brand):
            counts["skipped_other_brand"] += 1
            return None
        facts = bank_facts.all_facts(page) if use_ai else bank_facts.rule_facts(page)
        checked = bank_import.now_iso()
        built = bank_import.build_record(page.url, page, facts, checked, page_hash or bank_import.text_hash(page))
        record = built["record"]
        if record["category"] not in CLOTHING:
            counts["skipped_not_clothing"] += 1
            return None
        if not built["brand"] or built["brand"]["brand_id"] != brand["brand_id"]:
            counts["skipped_other_brand"] += 1
            return None
        ev_ids = [e["evidence_id"] for e in built["evidence"]]
        problems = bank_validate.validate_product(record, ev_ids)
        problems += [p for e in built["evidence"] for p in bank_validate.validate_evidence(e)]
        if problems:
            counts["invalid"] += 1
            return None
        existing = bank_import.load_product(record["product_id"])
        if existing and existing.get("decided_by") == "brooklyn":
            # Brooklyn already decided this one. The nightly refresh keeps its prices and stock current.
            counts["kept_brooklyn"] += 1
            return record["product_id"]
        if not bank_import.save_product(record, built["evidence"], built.get("search_text")):
            counts["failed"] += 1
            return None
        outcome = bank_rules.auto_decide(record, bool(brand["approved"]))
        bank_rules.apply_auto(record["product_id"], outcome)
        counts[outcome["outcome"]] = counts.get(outcome["outcome"], 0) + 1
        if outcome["spot_check"]:
            counts["spot_check"] += 1
        return record["product_id"]
    except Exception as exc:                # noqa: BLE001
        log.warning("bank_catalog: one product failed (%s)", exc)
        counts["failed"] += 1
        return None
