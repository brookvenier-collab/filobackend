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
import json
import urllib.parse
import urllib.request

import fabric
import brands

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

QUALITY_FLOOR = 7.0     # nothing below this is ever called "better made"
MAX_SYNTHETIC = 50      # percent
PRICE_FLOOR = 0.60      # never show something suspiciously cheaper
PRICE_CEILING = 2.50    # absolute ceiling, even with great cost-per-wear
FREE_PRICE_HEADROOM = 1.40   # below this, no justification needed


def _fetch(query, num=40):
    """Raw Google Shopping results. Split out so the filter tests run offline."""
    if not SERPAPI_KEY or not query:
        return []
    params = {"engine": "google_shopping", "q": query,
              "num": str(num), "api_key": SERPAPI_KEY}
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []  # never break a scan because search failed
    return data.get("shopping_results", []) or []


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


def evaluate(item, price=None, scanned_score=None):
    """Score one search result. Returns a dict to show, or None to drop it.

    Pure function, no network — this is the part worth testing.
    """
    source = item.get("source")

    # 0. Fast fashion and unverifiable marketplaces are never an upgrade.
    if brands.is_blocked(source):
        return None

    p = item.get("extracted_price")

    # 1. Price. Cheap-and-suspicious is out; expensive has to earn it below.
    if price is not None and p is not None:
        if p < price * PRICE_FLOOR or p > price * PRICE_CEILING:
            return None

    # 2. Must state its composition. allow_bare=False means the word "cotton"
    #    appearing in a product title is not evidence of anything.
    score, matched = fabric.quality_score(_describe(item), allow_bare=False)
    if score is None:
        return None

    # 3. Not mostly plastic.
    if fabric.synthetic_pct(matched) >= MAX_SYNTHETIC:
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
        "name": item.get("title"),
        "brand": source,
        "price": p,
        "score": score,
        "url": item.get("product_link") or item.get("link"),
        "image_url": item.get("thumbnail"),
        "known_maker": brands.is_known_maker(source),
        "value_note": _value_line(p, score, price, scanned_score),
    }


def search_alternatives(category=None, name=None, price=None,
                        scanned_score=None, limit=4):
    """Search several angles, keep only what we can vouch for, best first.

    Returns [] often, and that is correct behaviour rather than a bug.
    """
    queries = brands.build_queries(category or name)

    seen, kept = set(), []
    for query in queries:
        for item in _fetch(query):
            key = (item.get("product_link") or item.get("link")
                   or item.get("title") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result = evaluate(item, price=price, scanned_score=scanned_score)
            if result:
                kept.append(result)
        if len(kept) >= limit * 3:      # enough to rank well; stop burning API calls
            break

    # Known makers first, then by score. A brand Filo already rates for cloth
    # beats an unknown one at the same score.
    kept.sort(key=lambda r: (r["known_maker"], r["score"]), reverse=True)
    return kept[:limit]
