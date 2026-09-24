"""
Filo catalog — finds genuinely better-made clothes, not the same shelf reshuffled.

TWO PROBLEMS THIS SOLVES

1. FINDING. A generic Google Shopping search returns whoever has the biggest
   product feed, which is the fast-fashion giants. Scanning a fast-fashion shirt
   and being shown another one is the exact failure Filo exists to prevent. So we
   search several ways — by fiber, by certification, and by the names of makers
   known for cloth — and we refuse to return retailers on the blocklist. See
   brands.py.

2. VOUCHING. A result survives only if it states its fiber content, is under 50%
   synthetic, clears the quality floor, and actually beats the scanned score.
   Showing nothing is the right answer when nothing qualifies.

ON PRICE, AND WHY THE BAND IS ASYMMETRIC
"Better made at the same price" is frequently an empty set — that's the whole
problem with fast fashion. So a piece may cost more than what's in the shopper's
hands, but only if it is *cheaper per wear*. A $95 shirt lasting 200 wears beats
a $78 shirt lasting 25, and Filo says so in those words rather than hiding the
difference. Below 1.4× we don't ask; above it, cost-per-wear has to earn it.

Set SERPAPI_KEY on Railway to turn search on. No key = returns nothing (safe).
"""
import os
import re
import json
import time
import logging
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import fabric
import brands

log = logging.getLogger("filo.catalog")

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

QUALITY_FLOOR = 7.0     # nothing below this is ever called "better made"
MAX_SYNTHETIC = 50      # percent
PRICE_FLOOR = 0.60      # never show something suspiciously cheaper
PRICE_CEILING = 2.50    # absolute ceiling, even with great cost-per-wear
FREE_PRICE_HEADROOM = 1.40   # below this, no justification needed

# At most one mall brand in the list. They are not banned — a mall chain that
# genuinely passes the fabric test has earned a place — but they have enormous
# product feeds and were taking every slot on volume alone, which turned "better
# made options" into "four more of the same shop". See brands.MAINSTREAM.
MAX_MAINSTREAM = 1

# At most two slots per brand, so four slots always means at least two makers.
# Without it one label with a deep feed filled the card — including the same coat
# four times over in different sizes.
MAX_PER_BRAND = 2

# ---------------------------------------------------------------- garment type
# The search QUERY names the garment, but nothing checked that what came back
# was that garment. A jacket scan got sweaters. Every listing now has to name the
# same kind of garment in its title, and must not name a different one.
#
# group -> (words that mean this garment, words that mean it is something else)
GARMENT_GROUPS = {
    "jacket":   (["jacket", "bomber", "blouson", "shacket", "moto", "biker",
                  "trucker", "windbreaker", "anorak", "harrington", "overshirt"],
                 ["sweater", "cardigan", "hoodie", "sweatshirt", "vest", "gilet",
                  "blazer", "coat", "dress", "shirt dress", "pants", "skirt"]),
    "coat":     (["coat", "raincoat", "overcoat", "topcoat", "trench", "parka", "peacoat",
                  "pea coat", "duffle", "duffel", "car coat"],
                 ["sweater", "cardigan", "hoodie", "vest", "gilet", "dress",
                  "petticoat", "coated"]),
    "blazer":   (["blazer", "suit jacket", "sport coat", "sportcoat"],
                 ["sweater", "cardigan", "dress", "pants", "trousers"]),
    "sweater":  (["sweater", "jumper", "pullover", "crewneck", "crew neck", "crew",
                  "knit", "cardigan", "turtleneck", "mock neck"],
                 ["dress", "jacket", "coat", "pants", "skirt", "vest", "shorts"]),
    "cardigan": (["cardigan"], ["dress", "coat", "pants", "skirt"]),
    "hoodie":   (["hoodie", "hooded sweatshirt", "sweatshirt"],
                 ["dress", "jacket", "coat", "pants"]),
    "t-shirt":  (["tshirt", "tee"],
                 ["dress", "long sleeve shirt", "button", "guayabera", "jacket",
                  "sweater", "hoodie", "tank"]),
    "shirt":    (["shirt", "button-down", "button down", "button-up", "oxford"],
                 ["tshirt", "tee", "sweatshirt", "dress", "jacket", "shirt jacket"]),
    "blouse":   (["blouse", "top", "shirt"], ["tshirt", "tee", "sweatshirt",
                                              "dress", "jacket"]),
    "top":      (["top", "tee", "tshirt", "blouse", "tank", "cami", "shirt"],
                 ["dress", "jacket", "coat", "pants", "skirt", "sweatshirt"]),
    "dress":    (["dress", "gown", "slip"], ["top", "skirt", "shirt jacket"]),
    "skirt":    (["skirt"], ["dress", "top", "shorts"]),
    "jeans":    (["jeans", "jean", "denim pant", "denim trouser"],
                 ["jacket", "shirt", "skirt", "shorts", "dress"]),
    "trousers": (["trouser", "pants", "pant", "slacks", "chino", "culotte"],
                 ["jeans", "shorts", "jacket", "dress", "skirt", "leggings"]),
    "shorts":   (["shorts", "short"], ["dress", "top", "shirt", "sleeve", "jacket"]),
    "leggings": (["leggings", "legging", "tights"], ["dress", "top", "jacket"]),
}
# What shoppers type -> which group it belongs to. Checked longest first, so
# "leather jacket" and "t-shirt" resolve before "jacket" and "shirt".
_CATEGORY_ALIASES = {
    "leather jacket": "jacket", "denim jacket": "jacket", "puffer": "jacket",
    "t-shirt": "t-shirt", "tshirt": "t-shirt", "tee": "t-shirt",
    "sweatshirt": "hoodie", "jumper": "sweater", "pullover": "sweater",
    "knit": "sweater", "pants": "trousers", "trouser": "trousers",
    "slacks": "trousers", "jean": "jeans", "denim": "jeans",
    "trench": "coat", "parka": "coat", "overcoat": "coat",
}


# When the shopper doesn't enter a price there was no price band at all, which is
# how a coat scan came back with $570-$760 coats. These are rough mid-market
# prices, used ONLY to keep results in a sensible range — never shown, and never
# used in the cost-per-wear sentence, because the shopper didn't say it.
TYPICAL_PRICE = {
    "t-shirt": 40, "top": 50, "blouse": 70, "shirt": 70, "sweater": 100,
    "cardigan": 100, "hoodie": 80, "jeans": 100, "trousers": 100, "shorts": 50,
    "leggings": 60, "skirt": 70, "dress": 120, "jacket": 160, "blazer": 200,
    "coat": 250,
}
TYPICAL_LEATHER_PRICE = 350   # leather jackets and coats sit well above cloth ones
TYPICAL_FALLBACK = 80


def typical_price(category, material=None):
    """A stand-in price for the band when the shopper gave none."""
    if material in ("real leather", "faux leather"):
        return TYPICAL_LEATHER_PRICE
    return TYPICAL_PRICE.get(garment_group(category), TYPICAL_FALLBACK)


def garment_group(category):
    """Which garment group a scanned category belongs to, or None."""
    c = (category or "").lower().strip()
    if not c:
        return None
    for alias in sorted(_CATEGORY_ALIASES, key=len, reverse=True):
        if alias in c:
            return _CATEGORY_ALIASES[alias]
    for group in sorted(GARMENT_GROUPS, key=len, reverse=True):
        if group in c:
            return group
    return None


def _has_word(text, word):
    return re.search(r"(?<![a-z])" + re.escape(word) + r"s?(?![a-z])", text) is not None


def same_garment(listing_title, category):
    """True if this listing is the kind of garment the shopper scanned.

    Unknown categories fall back to requiring the category word itself, so a
    category we haven't mapped still can't come back as something else entirely.
    """
    if not category:
        return True
    # "T-shirt" is one garment, not a "shirt" with a "t" in front of it.
    title = re.sub(r"\bt[\s-]?shirt|\btee[\s-]shirt", "tshirt",
                   (listing_title or "").lower())
    group = garment_group(category)
    if group is None:
        word = category.lower().strip().split()[-1]
        return _has_word(title, word)
    include, exclude = GARMENT_GROUPS[group]
    # Exclusions are checked on the title with this group's own words removed,
    # so "shirt" doesn't trip on "t-shirt" and "coat" doesn't trip on "raincoat".
    if not any(_has_word(title, w) for w in include):
        return False
    stripped = title
    for w in sorted(include, key=len, reverse=True):
        stripped = re.sub(re.escape(w), " ", stripped)
    return not any(_has_word(stripped, w) for w in exclude)


_SIZE_NOISE = re.compile(
    r"\b(?:size|sz)\s*[\w/.-]+|\b(?:petite|tall|plus|regular|short|long)\b"
    r"|\b(?:xxs|xs|s|m|l|xl|xxl|xxxl|[0-9]{1,2}[xl]?)\b|[()\[\],-]")


def listing_key(item):
    """One key per product, not per size. Four sizes of the same Hobbs coat are
    one option, not four."""
    title = (item.get("title") or "").lower()
    base = re.sub(r"\s+", " ", _SIZE_NOISE.sub(" ", title)).strip()
    return (str(item.get("source") or "").lower().strip(), base)

# A scan has to feel instant — the Scan screen promises "under 5 seconds".
# Searches run concurrently and the whole search phase is capped, so a slow or
# hanging provider costs a few seconds, never the verdict.
QUERY_TIMEOUT = 7       # seconds per individual search
SEARCH_BUDGET = 9       # seconds for the whole search phase, all queries
CACHE_TTL = 60 * 60 * 6  # repeat searches are free for six hours

_cache = {}             # query -> (expires_at, results)


def _cached(query):
    hit = _cache.get(query)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _fetch(query, num=40):
    """Raw Google Shopping results. Split out so the filter tests run offline.

    Cached, because Filo re-searches the same categories constantly and SerpAPI
    bills per search.
    """
    if not SERPAPI_KEY or not query:
        return []

    hit = _cached(query)
    if hit is not None:
        return hit

    params = {"engine": "google_shopping", "q": query,
              "num": str(num), "api_key": SERPAPI_KEY}
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=QUERY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:            # noqa: BLE001
        log.info("catalog: search failed for %r (%s)", query, exc)
        return []  # never break a scan because search failed

    results = data.get("shopping_results", []) or []
    _cache[query] = (time.time() + CACHE_TTL, results)
    return results


def _describe(item):
    """Everything a listing tells us about what it's made of."""
    return " ".join(str(item.get(k) or "") for k in
                    ("title", "snippet", "description", "extensions"))


def _value_line(alt_price, alt_score, price, scanned_score):
    """The sentence that justifies paying more, in cost-per-wear."""
    a = fabric.cost_per_wear(alt_price, alt_score)
    s = fabric.cost_per_wear(price, scanned_score)
    if a is None or s is None:
        return None
    if alt_price and price and alt_price > price:
        pct = round((alt_price / price - 1) * 100)
        if a < s:
            return (f"{pct}% more upfront, but ${a:.2f} a wear against ${s:.2f} — "
                    f"it works out cheaper the longer you keep it.")
        return f"${a:.2f} a wear."
    return f"Better made and no more expensive — ${a:.2f} a wear."


def look_match(item, look):
    """How much of the scanned garment's shape this listing echoes, 0.0-1.0.

    RANKING ONLY — never a filter. A listing that matches nothing still gets
    shown if it is better made, because fabric quality is the promise and
    aesthetics are the assist. Matching is substring-based on purpose so that
    "crop" catches "cropped" and "rib" catches "ribbed".
    """
    if not look:
        return 0.0
    text = _describe(item).lower()
    hits = 0
    for word in look:
        stem = word.split("-")[0][:4]       # "chunky-knit" -> "chun", "cropped" -> "crop"
        if stem and stem in text:
            hits += 1
    return hits / len(look)


def evaluate(item, price=None, scanned_score=None, category=None,
             material=None, assumed_price=None):
    """Score one search result. Returns a dict to show, or None to drop it.

    Pure function, no network — this is the part worth testing.
    """
    source = item.get("source")

    # 0. Fast fashion and unverifiable marketplaces are never an upgrade.
    if brands.is_blocked(source):
        return None

    # 0b. It has to be the same kind of garment. A jacket is answered with a
    #     jacket, never a sweater, however well made the sweater is.
    if not same_garment(item.get("title"), category):
        return None

    p = item.get("extracted_price")

    # 1. Price. Cheap-and-suspicious is out; expensive has to earn it below.
    if price is not None and p is not None:
        if p < price * PRICE_FLOOR or p > price * PRICE_CEILING:
            return None
    # 1b. No price given: keep to a sensible range for this kind of garment.
    #     Only the ceiling — a cheaper well-made piece is fine when we don't know
    #     what they'd pay.
    elif price is None and assumed_price and p is not None:
        if p > assumed_price * PRICE_CEILING:
            return None

    # 2. Must state its composition. allow_bare=False means the word "cotton"
    #    appearing in a product title is not evidence of anything.
    score, matched = fabric.quality_score(_describe(item), allow_bare=False)
    if score is None:
        return None

    # 3. Not mostly plastic.
    if fabric.synthetic_pct(matched) >= MAX_SYNTHETIC:
        return None

    # 3b. Leather is answered with leather. Real or faux, the shopper is holding
    #     a leather-look piece, so the only honest upgrade is the real thing.
    if material in ("real leather", "faux leather") and \
            fabric.material_class(matched) != "real leather":
        return None

    # 4. Clears the bar in absolute terms.
    if score < QUALITY_FLOOR:
        return None

    # 5. And is genuinely an upgrade on what they're holding.
    if scanned_score is not None and score <= scanned_score:
        return None

    # 6. If it costs meaningfully more, it must be cheaper per wear.
    if price and p and p > price * FREE_PRICE_HEADROOM:
        a = fabric.cost_per_wear(p, score)
        s = fabric.cost_per_wear(price, scanned_score)
        if a is None or s is None or a >= s:
            return None

    return {
        # The shopper picks their own size at the shop; "Size 18" is noise here.
        "name": re.sub(r"\s*\b(?:size|sz)\s*[\w/.-]+", "", item.get("title") or "",
                       flags=re.I).strip(),
        "brand": source,
        "price": p,
        "score": score,
        "url": item.get("product_link") or item.get("link"),
        "image_url": item.get("thumbnail"),
        "known_maker": brands.is_known_maker(source),
        "tier": brands.tier(source),
        "value_note": _value_line(p, score, price, scanned_score),
    }


def search_alternatives(category=None, name=None, price=None,
                        scanned_score=None, limit=4, look=None, material=None):
    """Search several angles, keep only what we can vouch for, best first.

    The queries run CONCURRENTLY and under a total time budget. Sequentially
    they'd stack up behind each other and a scan could take the better part of a
    minute — unacceptable on a screen that promises a verdict in five seconds.

    Returns [] often, and that is correct behaviour rather than a bug.
    """
    subject = category or name
    if not subject:
        # Nothing to search on. A query like "clothing organic cotton" returns
        # noise, and it would cost a SerpAPI credit to find that out.
        return []

    queries = brands.build_queries(subject, look=look, material=material)
    deadline = time.time() + SEARCH_BUDGET

    raw = []
    # Deliberately not a `with` block: exiting one waits for every worker, so a
    # hung provider would block the response even after the budget expired.
    pool = ThreadPoolExecutor(max_workers=len(queries))
    try:
        futures = {pool.submit(_fetch, q): q for q in queries}
        try:
            for future in as_completed(futures, timeout=SEARCH_BUDGET):
                try:
                    raw.extend(future.result())
                except Exception as exc:        # noqa: BLE001
                    log.info("catalog: query %r failed (%s)", futures[future], exc)
                if time.time() > deadline:
                    break
        except TimeoutError:
            # Budget spent. Keep whatever came back and move on — a shopper
            # waiting on the verdict matters more than a complete result set.
            log.info("catalog: search budget exhausted, returning partial results")
    finally:
        pool.shutdown(wait=False)

    assumed = None if price else typical_price(subject, material)

    seen, kept = set(), []
    for item in raw:
        link = (item.get("product_link") or item.get("link") or "").lower()
        key = listing_key(item)
        if not key[1] or key in seen or (link and link in seen):
            continue
        seen.add(key)
        if link:
            seen.add(link)
        result = evaluate(item, price=price, scanned_score=scanned_score,
                          category=subject, material=material,
                          assumed_price=assumed)
        if result:
            result["match"] = look_match(item, look)
            kept.append(result)

    # Everything still standing has already cleared the quality gate inside
    # evaluate(), so ordering among survivors can serve taste and discovery
    # without weakening the promise. Closest in shape first (bucketed, so noise
    # doesn't reshuffle near-ties), then tier, then raw score.
    kept.sort(key=lambda r: (round(r["match"], 1), r["tier"], r["score"]),
              reverse=True)

    # Then cap the mall brands, so one chain with a huge feed can't own the list.
    out, mainstream_used, per_brand = [], 0, {}
    for r in kept:
        b = (r.get("brand") or "").lower().strip()
        if per_brand.get(b, 0) >= MAX_PER_BRAND:
            continue
        if r["tier"] == brands.TIER_MAINSTREAM:
            if mainstream_used >= MAX_MAINSTREAM:
                continue
            mainstream_used += 1
        per_brand[b] = per_brand.get(b, 0) + 1
        out.append(r)
        if len(out) == limit:
            break

    # Deliberately NOT backfilled. If the cap leaves two results instead of four,
    # two is the honest answer — the same reasoning that already returns an empty
    # list rather than padding it with items whose fabric we can't read. Filling
    # the shelf with mall brands is exactly the failure this tier exists to stop.
    return out
