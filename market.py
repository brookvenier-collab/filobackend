"""
Filo market price check (v28, 16 Oct 2026).

"Is this a fair price for what it's made of?" answered from live listings, not
from numbers anyone typed in. Replaces fabric.PRICE_GUIDE for every shopper-facing
price verdict.

WHY LIVE LISTINGS
No public source publishes a usual retail price per garment type and fibre in
North America. The US BLS says it does not publish average prices for apparel;
Statistics Canada publishes clothing price indexes, and we found no dollar
averages for clothing (see claude/filo-evidence-audit.md, section on price).
The legal tests that come closest to "the usual price" ask what suppliers
generally charge: Canada's Competition Act s.74.01(2) ("suppliers generally in
the relevant geographic market") and the FTC's 16 CFR 233.2(a) (the price at
which "substantial sales" are being made in the area). A median of many current
listings from many shops is the nearest honest measure of that.

THE RULES (each one is there to stop an overclaim)
- Comparable means: same garment group, states its fibre content in the
  listing (allow_bare=False), same dominant fibre making up at least 70% and
  within 20 points of the scanned piece, same department where known, not
  pre-owned.
- Regular price, not sale price: a listing's old price when it shows one.
- One listing per product (sizes collapsed) and per shop per product.
- At least MIN_LISTINGS comparables from at least MIN_SHOPS different shops,
  or no verdict at all.
- Three levels, and the line always shows what it's based on.
- Talks about the piece and the price, never the brand or the shop.
"""
import re
import statistics
from datetime import date

import fabric
import brands
import catalog

MIN_LISTINGS = 20
MIN_SHOPS = 5
DOMINANT_MIN = 70        # the fibre must be at least this % of the piece
DOMINANT_TOLERANCE = 20  # and within this many points of the scanned piece
# v28.1 (after the first live probe): "You may be overpaying" only past the 90th
# percentile AND at least twice the median. Listings can't be matched on brand
# tier the way BLS's models are, and a $30 cotton tee was being flagged against
# $7 blanks at 1.25x. A false "overpaying" is the error that hurts, so the bar
# is high on purpose.
OVERPAY_PERCENTILE = 0.90
OVERPAY_RATIO = 2.0
MAX_PER_SHOP = 3         # one shop's deep feed can't set the median
_KIDS = re.compile(
    r"\b(kids?|girls?|boys?|toddlers?|baby|babies|infants?|youth|junior|juniors|children'?s?)\b")

NOT_ENOUGH = "Not enough similar listings to compare the price yet."
DISCLAIMER = ("Compared with current prices for similar pieces. "
              "It's not a judgement on the brand or the shop.")

GOOD, FAIR, OVER = "good_value", "fair", "overpaying"
# v37: the words the app shows, short enough for the in-store card.
LABELS = {GOOD: "Good price", FAIR: "Fair price", OVER: "Priced high"}
# v37: how long the fast verdict waits for the listings before it gives up.
FAST_TIMEOUT_S = 7

_GROUP_WORD = {"t-shirt": "t-shirt", "top": "top", "shirt": "shirt",
               "blouse": "blouse", "sweater": "sweater", "cardigan": "cardigan",
               "hoodie": "hoodie", "jeans": "jeans", "trousers": "trousers",
               "shorts": "shorts", "leggings": "leggings", "skirt": "skirt",
               "dress": "dress", "jacket": "jacket", "blazer": "blazer",
               "coat": "coat", "long-sleeve": "long sleeve top", "tank": "tank top",
               "sweatpants": "sweatpants"}


def _dominant(matched):
    """(fibre, pct) making up most of the piece, or (None, 0)."""
    if not matched:
        return None, 0
    total = sum(p for _, p, _ in matched) or 100
    key, pct, _ = max(matched, key=lambda m: m[1])
    return key, round(100 * pct / total)


def describe(matched):
    """'100% polyester' or 'mostly cotton' — what the line calls the make."""
    key, pct = _dominant(matched)
    if key is None:
        return None
    return f"100% {key}" if pct >= 100 else f"mostly {key}"


def queries(group, matched, department=None):
    key, pct = _dominant(matched)
    word = _GROUP_WORD.get(group)
    if not key or not word:
        return []
    dept = {"women": "women's", "men": "men's"}.get(department or "", "")
    lead = f"100% {key}" if pct >= 100 else key
    out = [f"{dept} {lead} {word}".strip(), f"{dept} {key} {word}".strip()]
    if dept:
        out.append(f"{lead} {word}")
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def _regular_price(item):
    """The non-sale price: a listing's old price when it shows one."""
    for k in ("extracted_old_price", "extracted_price"):
        v = item.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def comparable(item, group, matched, department=None):
    """True if this listing can stand in the comparison."""
    title = item.get("title") or ""
    source = item.get("source")
    if brands.is_resale(source) or brands.is_resale(title):
        return False
    # Multipacks and wholesale lots price per pack, not per piece.
    if catalog._BULK.search(title.lower()):
        return False
    # Children's sizes are a different market.
    if _KIDS.search(title.lower()):
        return False
    # Fast fashion and unverifiable marketplaces are left out. This raises the
    # median, so it makes "overpaying" harder to reach, never easier.
    if brands.is_blocked(source):
        return False
    if not catalog.same_garment(title, group):
        return False
    if department and not catalog.same_department(title, department):
        return False
    detail = fabric.quality_detail(catalog._describe(item), allow_bare=False)
    if detail is None:
        return False
    # v37: real leather only against real leather; never against faux, and
    # a fabric piece never against leather.
    if fabric.material_class(detail["matched"]) != fabric.material_class(matched):
        return False
    key, pct = _dominant(matched)
    lkey, lpct = _dominant(detail["matched"])
    return (lkey == key and lpct >= DOMINANT_MIN
            and abs(lpct - pct) <= DOMINANT_TOLERANCE)


def _percentile(sorted_vals, q):
    """Linear-interpolated percentile on a sorted list, q in 0..1."""
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


MARKETS = {"US": ("us", "USD", "US"), "CA": ("ca", "CAD", "Canada")}


def benchmark(group, matched, department=None, fetch=None, country=None):
    """The usual price for pieces like this, from current listings, or None."""
    key, pct = _dominant(matched)
    if key is None or pct < DOMINANT_MIN or group not in _GROUP_WORD:
        return None
    # v37: real leather is compared with real leather listings. Faux is not
    # priced here (its listings rarely state what it is made of).
    if fabric.material_class(matched) == "faux leather":
        return None
    gl, currency, place = MARKETS.get((country or "US").upper(), MARKETS["US"])
    if fetch is None:
        fetch = lambda q, num=100: catalog._fetch(q, num=num, gl=gl)
    prices, shops, seen, per_shop = [], set(), set(), {}
    sample = []
    # v37: the searches run side by side, so the in-store verdict isn't three
    # searches long. Results are read back in query order, as before.
    qs = queries(group, matched, department)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, len(qs))) as pool:
        batches = list(pool.map(lambda q: fetch(q, num=100) or [], qs))
    for batch in batches:
        for item in batch:
            lk = catalog.listing_key(item)
            if lk in seen:
                continue
            seen.add(lk)
            if not comparable(item, group, matched, department):
                continue
            p = _regular_price(item)
            if p is None:
                continue
            shop = str(item.get("source") or "").lower().strip()
            if per_shop.get(shop, 0) >= MAX_PER_SHOP:
                continue
            per_shop[shop] = per_shop.get(shop, 0) + 1
            prices.append(p)
            shops.add(shop)
            sample.append({"title": item.get("title"), "shop": item.get("source"),
                           "price": p, "link": item.get("product_link") or item.get("link")})
    if len(prices) < MIN_LISTINGS or len(shops) < MIN_SHOPS:
        return {"enough": False, "listings": len(prices), "shops": len(shops),
                "_sample": sample}
    prices.sort()
    return {
        "enough": True,
        "listings": len(prices),
        "shops": len(shops),
        "p25": round(_percentile(prices, 0.25)),
        "median": round(statistics.median(prices)),
        "p75": round(_percentile(prices, 0.75)),
        "p90": round(_percentile(prices, OVERPAY_PERCENTILE)),
        "_sample": sample,
        "as_of": date.today().strftime("%b %Y"),
        "currency": currency,
        "market": place,
    }


def check(price, group, matched, department=None, fetch=None, country=None):
    """The price check the app shows. None when there's no price to check."""
    if price is None or not matched:
        return None
    make = describe(matched)
    bench = benchmark(group, matched, department, fetch=fetch, country=country)
    if not bench or not bench.get("enough"):
        public = {k: v for k, v in (bench or {}).items() if not k.startswith("_")} or None
        return {"level": None, "label": None, "line": NOT_ENOUGH,
                "based_on": None, "benchmark": public, "disclaimer": None}
    p25, med, p75 = bench["p25"], bench["median"], bench["p75"]
    word = _GROUP_WORD[group]
    plural = word if word.endswith("s") else word + "s"
    p = f"${price:,.0f}"
    rng = f"${p25:,}" + (f" to ${p75:,}" if p75 != p25 else "")
    if price < p25:
        level = GOOD
        line = f"Below the usual price for {make} {plural}. Similar pieces sell for {rng}."
    elif price > bench["p90"] and price >= OVERPAY_RATIO * med:
        level = OVER
        ratio = price / med if med else 0
        how = f"about {ratio:.1f} times"
        line = (f"{p} is {how} the usual price for {make} {plural}. "
                f"Similar pieces sell for {rng}.")
    else:
        level = FAIR
        line = f"About the usual price for {make} {plural}. Similar pieces sell for {rng}."
    based_on = (f"Based on {bench['listings']} similar listings at {bench['shops']} shops "
                f"in {'the US' if bench['market'] == 'US' else bench['market']}, {bench['as_of']}. Prices in {bench['currency']}.")
    public = {k: v for k, v in bench.items() if not k.startswith("_")}
    # v37: one short line for the in-store card: what similar pieces sell for.
    short = f"Similar pieces sell for {rng} right now."
    return {"level": level, "label": LABELS[level], "line": line, "short": short,
            "based_on": based_on, "benchmark": public, "disclaimer": DISCLAIMER}


_ROW_LEVEL = {GOOD: "good", FAIR: "ok", OVER: "poor"}


def price_rows(pc):
    """v37. The "Worth it?" row for the result page, from today's listings.

    Replaces the "What it should cost" row built from fabric costs: shoppers
    are told what similar pieces sell for now, never what a piece costs to make."""
    if not pc:
        return []
    if pc.get("level"):
        return [{"side": "price", "level": _ROW_LEVEL.get(pc["level"], "unknown"),
                 "icon": "tag", "title": pc["label"],
                 "line": pc.get("short") or pc.get("line") or "",
                 "detail": " ".join(x for x in (pc.get("line"), pc.get("based_on"),
                                                pc.get("disclaimer")) if x) or None}]
    if pc.get("pending"):
        return []
    return [{"side": "price", "level": "unknown", "icon": "tag",
             "title": "Worth it?", "line": pc.get("line") or NOT_ENOUGH, "detail": None}]


# ------------------------------------------------------------- v30.8 the store
# "Nothing at this store is under $50." A fair price built from the make is the
# honest number, but a shopper standing in a store also needs to know whether
# THIS price is normal THERE. So: what does this brand list this kind of piece
# for, from its own current listings? One search per brand and garment type,
# cached for two weeks. It never moves the score or the fair price; it only
# changes how the verdict is worded (value.py).
import time as _time

STORE_MIN_LISTINGS = 6
STORE_TTL_S = 14 * 24 * 3600
_store_cache = {}


def _norm_brand(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def store_benchmark(brand, group, department=None, fetch=None, country=None):
    """{'p25','median','p75','listings','currency'} for this brand's own pieces of
    this type, or None. Listings must be sold by the brand or name it in the title."""
    b = _norm_brand(brand)
    word = _GROUP_WORD.get(group) or group
    if len(b) < 3 or not word:
        return None
    gl, currency, place = MARKETS.get((country or "US").upper(), MARKETS["US"])
    key = (b, group, department or "", gl)
    hit = _store_cache.get(key)
    if hit and hit[0] > _time.time():
        return hit[1]
    if fetch is None:
        fetch = lambda q, num=60: catalog._fetch(q, num=num, gl=gl)
    dept = {"women": "women's", "men": "men's"}.get(department or "", "")
    q = f"{brand} {dept} {word}".replace("  ", " ").strip()
    prices, seen = [], set()
    for item in fetch(q, num=60) or []:
        title = item.get("title") or ""
        source = item.get("source") or ""
        if b not in _norm_brand(source) and b not in _norm_brand(title):
            continue
        if brands.is_resale(source) or not catalog.same_garment(title, group):
            continue
        if catalog._BULK.search(title.lower()) or _KIDS.search(title.lower()):
            continue
        lk = catalog.listing_key(item)
        if lk in seen:
            continue
        seen.add(lk)
        p = _regular_price(item)
        if p:
            prices.append(p)
    out = None
    if len(prices) >= STORE_MIN_LISTINGS:
        prices.sort()
        out = {"p25": round(_percentile(prices, 0.25)), "median": round(statistics.median(prices)),
               "p75": round(_percentile(prices, 0.75)), "listings": len(prices),
               "currency": currency, "as_of": date.today().strftime("%b %Y")}
    _store_cache[key] = (_time.time() + STORE_TTL_S, out)
    return out
