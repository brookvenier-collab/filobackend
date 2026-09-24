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
# The mall tier. NOT blocked — these shops do sometimes make a decent cotton
# tee, and blocking something that legitimately passes the fabric test would be
# dishonest.
#
# But they were winning slots on feed size alone. A mall chain lists tens of
# thousands of SKUs and prints "100% cotton" on plenty of them, while the small
# makers Filo exists to surface have a few hundred products and lose on volume
# every single time. The result was a shopper scanning a mall sweater and being
# shown four more mall sweaters — technically better made, and useless.
#
# So they rank BELOW independent makers at equal quality, and take at most one
# of the four slots. See MAX_MAINSTREAM in catalog.py.
#
# Note on COS, Arket, & Other Stories (H&M Group) and Massimo Dutti (Inditex):
# their parents are blocked outright, but these lines genuinely use better cloth,
# so they sit here and get judged on the garment like everyone else.
# --------------------------------------------------------------------------
MAINSTREAM = {
    "abercrombie", "aerie", "american eagle", "hollister", "gap", "banana republic",
    "j.crew", "j crew", "madewell", "anthropologie", "urban outfitters",
    "free people", "express", "ann taylor", "loft", "talbots", "chico's",
    "uniqlo", "everlane", "quince", "mango", "massimo dutti", "cos ",
    "arket", "& other stories", "club monaco", "reiss", "ted baker",
    "lululemon", "athleta", "gymshark", "alo yoga", "vuori", "fabletics",
    "aritzia", "garage", "dynamite", "reitmans", "simons", "roots",
    "nordstrom", "macy's", "bloomingdale", "dillard", "kohl's", "target",
    "asos", "revolve", "shopbop", "boden", "white house black market",
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


def is_mainstream(source):
    """True for mall and mass-market chains. Allowed, but never favoured."""
    if not source:
        return False
    return any(name in source.lower() for name in MAINSTREAM)


# Ranking tiers. Higher wins. The gap that matters is UNKNOWN above MAINSTREAM:
# a small label Filo has never heard of, which has already proved its fibre
# content and cleared the quality floor, is exactly the discovery this product
# exists to make. A mall chain that cleared the same bar is not a discovery.
TIER_MAKER = 3        # Filo already rates them for cloth
TIER_UNKNOWN = 2      # unrecognised, passed every test — the interesting case
TIER_MAINSTREAM = 1   # mall and mass-market
TIER_BLOCKED = 0      # never returned at all


def tier(source):
    """Which ranking tier a retailer sits in."""
    if is_blocked(source):
        return TIER_BLOCKED
    if is_known_maker(source):
        return TIER_MAKER
    if is_mainstream(source):
        return TIER_MAINSTREAM
    return TIER_UNKNOWN


def makers_for(category):
    """Brands worth searching by name for this kind of garment."""
    # No fallback to "the first six makers on the list". That fallback is how a
    # jacket scan searched "nudie jeans jacket" and "armedangels jacket" and came
    # back with knitwear: a maker who doesn't make the garment returns whatever
    # they do make. No specialist for a category means no brand-led query.
    if not category:
        return []
    c = category.lower()
    return [name for name, meta in QUALITY_MAKERS.items()
            if any(tag in c for tag in meta["good_at"])][:6]


def fiber_upgrade_for(category):
    """The material step up from whatever they're holding."""
    c = (category or "").lower()
    for key, upgrade in FIBER_UPGRADE.items():
        if key in c:
            return upgrade
    return "100% organic cotton"


def build_queries(category, max_queries=4, look=None, material=None):
    """Several angles at the same shelf, because one generic query only ever
    returns the shops with the biggest product feeds.

      1. fiber-led   — "women's t-shirt heavyweight organic cotton"
      2. cert-led    — "women's t-shirt OEKO-TEX certified"
      3/4. brand-led — "nudie jeans women's t-shirt"

    `look` is the shape read off a photo of the garment (see vision.py) — e.g.
    ["cropped", "crewneck", "chunky-knit", "sage"]. Without it a scan of a
    cropped boxy sweater searches "sweater merino" and returns every well-made
    sweater ever cut, which is right on fabric and wrong on taste.

    The shape words are ADDED to queries, never used to exclude anything. A
    listing that says "crop" where the model said "cropped" still comes back and
    still gets judged on its fibre content, which is the actual promise.
    """
    cat = (category or "").strip() or "clothing"
    look = [w for w in (look or []) if w][:3]
    shape = " ".join(look)

    # Leather, real or fake, is only ever answered with real leather. A faux
    # leather jacket's upgrade is a leather jacket — not a wool one, and never a
    # sweater. Searched by material, with no brand-led queries, because none of
    # the makers on the list work in leather.
    if material in ("real leather", "faux leather"):
        noun = cat if "leather" in cat.lower() else f"leather {cat}"
        return [
            f"{noun} {shape} genuine leather".replace("  ", " ").strip(),
            f"{noun} full grain leather",
            f"{noun} lambskin",
        ][:max_queries]

    # The shape goes on the fibre-led query (the one that returns the most) and
    # on one brand-led query. Leaving a cert-led query un-narrowed keeps a wide
    # net in play, so a wrong silhouette read can't sink the whole search.
    queries = [
        f"{cat} {shape} {fiber_upgrade_for(cat)}".replace("  ", " ").strip()
        if shape else f"{cat} {fiber_upgrade_for(cat)}",
        f"{cat} {QUALITY_QUALIFIERS[2]}",
    ]

    makers = makers_for(cat)
    for i, maker in enumerate(makers[:max_queries - len(queries)]):
        if shape and i == 0:
            queries.append(f"{maker} {cat} {shape}")
        else:
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
