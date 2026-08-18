"""
Filo's brand registry — who actually makes cloth, and who assembles plastic.

WHY THIS FILE EXISTS
Google Shopping ranks by feed quality and ad spend, so a generic search for
"women's t-shirt cotton" returns the same handful of fast-fashion giants every
time. Scanning a fast-fashion shirt and being shown another fast-fashion shirt
is not an upgrade — it's the failure mode Filo exists to prevent. Filtering by
fiber alone can't fix it, because the good makers never appear in the results
to be filtered.

So Filo has to go looking for them by name.

THIS LIST IS A SEED, NOT THE TRUTH
It is a starting point assembled from public certification directories and
should be owned, corrected and extended by hand. More importantly it is meant
to be *replaced* by evidence: once enough scans exist, aggregates.brand_quality()
knows which brands actually score well from real care labels, and that data
should progressively take over from this file. See quality_sources_from_data().

A brand earns its place here by what it puts in the cloth, never by paying.
Nobody can buy an entry. If that ever changes, Filo is over.
"""
import logging

log = logging.getLogger("filo.brands")


# --------------------------------------------------------------------------
# Never recommend. These are the shops Filo is helping people walk out of.
# Matching is substring-based on the retailer name Google reports as `source`.
# --------------------------------------------------------------------------
FAST_FASHION = {
    "shein", "temu", "romwe", "zaful", "cider", "fashion nova", "boohoo",
    "prettylittlething", "nasty gal", "forever 21", "forever21", "missguided",
    "h&m", "hm.com", "zara", "bershka", "pull&bear", "pull & bear",
    "stradivarius", "primark", "wish", "aliexpress", "alibaba", "dhgate",
    "old navy", "shien", "urbanic", "yesstyle", "papaya", "rue21",
}

# Marketplaces where the seller is unknown and the listing is unverifiable.
# Not an accusation of quality — we simply cannot stand behind the item.
UNVERIFIABLE_SOURCES = {
    "amazon", "walmart", "ebay", "etsy", "poshmark", "mercari", "wayfair",
}


# --------------------------------------------------------------------------
# Makers worth surfacing, and what they're actually good at. `queries` are the
# search terms most likely to return that brand's better pieces.
# --------------------------------------------------------------------------
QUALITY_MAKERS = {
    "nudie jeans":              {"good_at": ["jeans", "denim"],              "note": "100% organic cotton denim, free repairs for life"},
    "armedangels":              {"good_at": ["jeans", "denim", "knit", "top"], "note": "GOTS organic cotton and wool, Fair Wear"},
    "kuyichi":                  {"good_at": ["jeans", "denim"],              "note": "organic and recycled denim"},
    "naked & famous":           {"good_at": ["jeans", "denim"],              "note": "Japanese selvedge, unusual weaves"},
    "knowledge cotton apparel": {"good_at": ["knit", "top", "shirt"],        "note": "GOTS organic cotton, RWS wool"},
    "hessnatur":                {"good_at": ["knit", "top", "dress", "wool"], "note": "GOTS cotton, wool, hemp, TENCEL"},
    "people tree":              {"good_at": ["dress", "top", "blouse"],      "note": "80%+ GOTS organic cotton, WFTO fair trade"},
    "q for quinn":              {"good_at": ["socks", "underwear", "basics"], "note": "95–100% organic cotton, RWS merino"},
    "organic basics":           {"good_at": ["basics", "top", "underwear"],  "note": "organic cotton and TENCEL basics"},
    "colorful standard":        {"good_at": ["top", "sweatshirt", "knit"],   "note": "heavyweight organic cotton"},
    "asket":                    {"good_at": ["top", "shirt", "knit"],        "note": "traceable supply chain, heavier weights"},
    "pact":                     {"good_at": ["basics", "top"],               "note": "GOTS organic cotton basics"},
    "harvest & mill":           {"good_at": ["top", "basics"],               "note": "US-grown organic cotton"},
    "jungmaven":                {"good_at": ["top", "tee"],                  "note": "hemp and hemp-cotton"},
    "christy dawn":             {"good_at": ["dress"],                       "note": "deadstock and regenerative cotton"},
    "not perfect linen":        {"good_at": ["linen", "dress", "shirt"],     "note": "washed European linen"},
    "son de flor":              {"good_at": ["linen", "dress"],              "note": "linen dresses"},
    "magiclinen":               {"good_at": ["linen", "dress", "shirt"],     "note": "OEKO-TEX linen"},
    "icebreaker":               {"good_at": ["wool", "knit", "base layer"],  "note": "merino wool"},
    "smartwool":                {"good_at": ["wool", "socks", "base layer"], "note": "merino wool"},
}


# Fabric and certification language that pulls better-made items to the surface.
# Appended to searches so the query stops returning generic mall stock.
QUALITY_QUALIFIERS = [
    "100% organic cotton GOTS",
    "heavyweight organic cotton",
    "OEKO-TEX certified",
    "100% linen",
    "merino wool",
    "hemp",
]

# Fiber upgrades by what the shopper is holding — used to steer the search
# toward a materially different (not merely different-branded) option.
FIBER_UPGRADE = {
    "top": "100% organic cotton", "shirt": "100% linen", "blouse": "100% silk",
    "tee": "heavyweight organic cotton", "t-shirt": "heavyweight organic cotton",
    "jeans": "selvedge organic cotton", "denim": "selvedge organic cotton",
    "sweater": "merino wool", "knit": "merino wool", "cardigan": "merino wool",
    "dress": "100% linen", "trousers": "wool", "pants": "wool",
    "jacket": "wool", "coat": "wool",
}


def is_blocked(source):
    """True if this retailer should never appear as a better-made option."""
    if not source:
        return False
    s = source.lower()
    return any(bad in s for bad in FAST_FASHION) or any(bad in s for bad in UNVERIFIABLE_SOURCES)


def is_known_maker(source):
    """True if this is a maker Filo already rates for fabric."""
    if not source:
        return False
    s = source.lower()
    return any(maker in s for maker in QUALITY_MAKERS)


def makers_for(category):
    """Brands worth searching by name for this kind of garment."""
    if not category:
        return list(QUALITY_MAKERS)[:6]
    c = category.lower()
    hits = [name for name, meta in QUALITY_MAKERS.items()
            if any(tag in c for tag in meta["good_at"])]
    return hits[:6] or list(QUALITY_MAKERS)[:6]


def fiber_upgrade_for(category):
    """The material step up from whatever they're holding."""
    c = (category or "").lower()
    for key, upgrade in FIBER_UPGRADE.items():
        if key in c:
            return upgrade
    return "100% organic cotton"


def build_queries(category, max_queries=4):
    """Several angles at the same shelf, because one generic query only ever
    returns the shops with the biggest product feeds.

      1. fiber-led   — "women's t-shirt heavyweight organic cotton"
      2. cert-led    — "women's t-shirt OEKO-TEX certified"
      3/4. brand-led — "nudie jeans women's t-shirt"
    """
    cat = (category or "").strip() or "clothing"
    queries = [
        f"{cat} {fiber_upgrade_for(cat)}",
        f"{cat} {QUALITY_QUALIFIERS[2]}",
    ]
    for maker in makers_for(cat)[:max_queries - len(queries)]:
        queries.append(f"{maker} {cat}")
    return queries[:max_queries]


def quality_sources_from_data(min_score=7.0, limit=40):
    """Brands Filo's own scan data says are good — the eventual replacement for
    the hand-written list above. Returns [] until there's enough evidence, so
    this is safe to call from day one.
    """
    try:
        import aggregates
        rows = aggregates.brand_quality(days=180)
    except Exception as exc:            # noqa: BLE001
        log.debug("brands: no aggregate data yet (%s)", exc)
        return []
    return [r["brand"] for r in rows
            if (r.get("avg_score") or 0) >= min_score][:limit]
