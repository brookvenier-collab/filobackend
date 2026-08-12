"""
Filo catalog — pulls real products + prices from the web (Google Shopping via SerpAPI)
and filters them to the same price range. No key = returns nothing (safe fallback).

Set the environment variable SERPAPI_KEY on your host (Railway → Variables) to turn it on.
Get a key at https://serpapi.com (free tier ~100 searches/month). Serper.dev also works
with small changes if you prefer a cheaper provider.
"""
import os
import json
import urllib.parse
import urllib.request

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


def search_alternatives(query, price=None, limit=4):
    """Return up to `limit` products matching `query`, within ~±40% of `price`."""
    if not SERPAPI_KEY or not query:
        return []

    params = {
        "engine": "google_shopping",
        "q": query,
        "num": "40",
        "api_key": SERPAPI_KEY,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []  # never break a scan because search failed

    results = data.get("shopping_results", []) or []
    lo, hi = (price * 0.6, price * 1.4) if price else (None, None)

    out = []
    for it in results:
        p = it.get("extracted_price")
        if price is not None and p is not None and not (lo <= p <= hi):
            continue
        out.append({
            "name": it.get("title"),
            "brand": it.get("source"),
            "price": p,
            "score": None,  # unknown until we have its composition
            "url": it.get("product_link") or it.get("link"),
            "image_url": it.get("thumbnail"),
        })
        if len(out) >= limit:
            break
    return out
