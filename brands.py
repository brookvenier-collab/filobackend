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
    # Resale: one-off listings whose fibre content is whatever the seller typed.
    "depop", "vinted", "thredup", "therealreal", "vestiaire", "grailed", "tradesy",
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
# Mass department stores and off-price chains (Brooklyn, 1 Oct 2026: "better
# made options should be cool brands, not just Macy's"). They sell thousands of
# house-label and licensed pieces that pass a fibre test and feel like nothing.
# A listing from one of these is only shown when the PIECE is by a maker on
# Filo's list (a Schott jacket sold at Macy's is still a Schott jacket).
# Anything else from them is dropped.
# --------------------------------------------------------------------------
DEPARTMENT_STORES = {
    "macy's", "macys", "kohl's", "kohls", "dillard", "jcpenney", "jc penney",
    "belk", "target", "costco", "sam's club", "qvc", "hsn", "zulily",
    "nordstrom rack", "saks off 5th", "tj maxx", "tjmaxx", "marshalls",
    "burlington", "ross", "winners", "lord & taylor", "boscov", "von maur",
    "walmart", "overstock", "shop premium outlets",
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

    # ---- The taste tier (added 1 Oct 2026) -------------------------------
    # Labels people actually want to wear that are also known for the cloth.
    # Being on this list changes WHERE Filo looks and breaks ties. It never
    # changes a score: every piece still has to state its fibres and clear the
    # same quality floor, or it isn't shown. Edit freely — this is Brooklyn's
    # list to own.
    # Leather and outerwear
    "nour hammour":             {"good_at": ["leather", "jacket", "coat"],   "note": "Paris-made leather jackets"},
    "deadwood":                 {"good_at": ["leather", "jacket"],           "note": "recycled real leather"},
    "schott":                   {"good_at": ["leather", "jacket"],           "note": "US-made leather jackets since 1913"},
    "acne studios":             {"good_at": ["leather", "jacket", "jeans", "denim", "knit"], "note": "leather, wool and denim"},
    "allsaints":                {"good_at": ["leather", "jacket"],           "note": "real leather biker jackets"},
    "the frankie shop":         {"good_at": ["jacket", "blazer", "coat", "trousers", "shirt"], "note": "tailoring in wool and cotton"},
    "anine bing":               {"good_at": ["leather", "jacket", "blazer", "knit", "tee"], "note": "leather, wool blazers, cashmere"},
    "toteme":                   {"good_at": ["coat", "jacket", "knit", "shirt", "jeans", "denim"], "note": "wool coats, organic cotton denim"},
    "harris wharf london":      {"good_at": ["coat", "jacket", "blazer"],    "note": "pressed virgin wool coats"},
    "mackage":                  {"good_at": ["coat", "leather", "jacket"],   "note": "Canadian wool and leather outerwear"},
    "soia & kyo":               {"good_at": ["coat", "jacket"],              "note": "Canadian wool coats"},
    "st. agni":                 {"good_at": ["leather", "jacket", "dress", "trousers", "knit"], "note": "leather, linen and wool"},
    "loulou studio":            {"good_at": ["knit", "sweater", "coat", "jacket", "blazer"], "note": "wool and cashmere"},
    # Denim
    "agolde":                   {"good_at": ["jeans", "denim", "leather"],   "note": "organic and regenerative cotton denim"},
    "citizens of humanity":     {"good_at": ["jeans", "denim"],              "note": "LA-made denim"},
    "re/done":                  {"good_at": ["jeans", "denim", "tee"],       "note": "reworked and 100% cotton denim"},
    "still here":               {"good_at": ["jeans", "denim"],              "note": "100% cotton denim, NY"},
    "slvrlake":                 {"good_at": ["jeans", "denim"],              "note": "rigid 100% cotton denim"},
    # Knitwear
    "babaa":                    {"good_at": ["knit", "sweater", "cardigan"], "note": "Spanish wool and cotton knits"},
    "&daughter":                {"good_at": ["knit", "sweater", "cardigan"], "note": "Irish and Scottish wool knits"},
    "lisa yang":                {"good_at": ["knit", "sweater", "cardigan"], "note": "100% cashmere"},
    "naadam":                   {"good_at": ["knit", "sweater", "cardigan"], "note": "Mongolian cashmere"},
    "jenni kayne":              {"good_at": ["knit", "sweater", "cardigan", "coat"], "note": "cashmere, alpaca, wool"},
    "la ligne":                 {"good_at": ["knit", "sweater", "top"],      "note": "wool and cashmere knits"},
    # Shirts, tees, dresses, trousers
    "sunspel":                  {"good_at": ["tee", "t-shirt", "top", "knit"], "note": "long-staple cotton tees"},
    "with nothing underneath":  {"good_at": ["shirt", "blouse"],             "note": "cotton and linen shirts"},
    "doen":                     {"good_at": ["dress", "blouse", "top", "skirt"], "note": "organic cotton and silk"},
    "faithfull the brand":      {"good_at": ["dress", "skirt", "top"],       "note": "linen dresses"},
    "posse":                    {"good_at": ["dress", "skirt", "top", "linen"], "note": "linen"},
    "reformation":              {"good_at": ["dress", "leather", "jeans", "skirt", "top"], "note": "linen, silk, leather"},
    "sezane":                   {"good_at": ["knit", "blouse", "dress", "jacket", "leather"], "note": "wool knits, leather"},
    "nili lotan":               {"good_at": ["trousers", "pants", "shirt", "knit"], "note": "cotton and wool, NY-made"},
    "margaret howell":          {"good_at": ["shirt", "trousers", "knit", "coat"], "note": "British cotton, linen and wool"},
    "cuyana":                   {"good_at": ["leather", "knit", "top", "trousers"], "note": "leather, silk, pima cotton"},
}

# Spellings a listing might use for the same maker.
MAKER_ALIASES = {
    "sézane": "sezane", "dôen": "doen", "babaà": "babaa", "totême": "toteme",
    "st agni": "st. agni", "redone": "re/done", "and daughter": "&daughter",
    "all saints": "allsaints", "schott nyc": "schott",
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


def maker_in(*texts):
    """The maker on Filo's list named in any of these strings (a retailer name,
    a listing title), or None. Whole-word matching, so "posse" never matches
    "possession" and "schott" never matches a longer word."""
    import re
    for text in texts:
        t = (text or "").lower()
        if not t:
            continue
        for alias, canonical in MAKER_ALIASES.items():
            if alias in t:
                t = t.replace(alias, canonical)
        for maker in sorted(QUALITY_MAKERS, key=len, reverse=True):
            if re.search(r"(?<![a-z0-9])" + re.escape(maker) + r"(?![a-z0-9])", t):
                return maker
    return None


def is_known_maker(source, title=None):
    """True if this is a maker Filo already rates — by the shop it's sold in
    or by the brand named in the listing."""
    return maker_in(source, title) is not None


def is_department_store(source):
    """Mass department stores and off-price chains. See DEPARTMENT_STORES."""
    if not source:
        return False
    s = source.lower()
    return any(name in s for name in DEPARTMENT_STORES)


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


def tier(source, title=None):
    """Which ranking tier a listing sits in. The maker named in the title counts:
    an Agolde jean sold at a multi-brand shop is an Agolde jean."""
    if is_blocked(source):
        return TIER_BLOCKED
    if is_known_maker(source, title):
        return TIER_MAKER
    if is_department_store(source):
        return TIER_BLOCKED          # no listed maker in the title -> not shown
    if is_mainstream(source):
        return TIER_MAINSTREAM
    return TIER_UNKNOWN


def display_name(maker):
    """'nour hammour' -> 'Nour Hammour', for showing on the card."""
    special = {"allsaints": "AllSaints", "re/done": "RE/DONE", "&daughter": "&Daughter",
               "slvrlake": "SLVRLAKE", "agolde": "AGOLDE", "st. agni": "St. Agni",
               "q for quinn": "Q for Quinn"}
    return special.get(maker, " ".join(w.capitalize() for w in maker.split()))


def makers_for(category):
    """Brands worth searching by name for this kind of garment."""
    # No fallback to "the first six makers on the list". That fallback is how a
    # jacket scan searched "nudie jeans jacket" and "armedangels jacket" and came
    # back with knitwear: a maker who doesn't make the garment returns whatever
    # they do make. No specialist for a category means no brand-led query.
    if not category:
        return []
    c = category.lower()
    found = [name for name, meta in QUALITY_MAKERS.items()
             if any(tag in c for tag in meta["good_at"])]
    if len(found) > 1:
        # Rotate by the day so every maker on the list gets searched over a week,
        # while the same scan on the same day still returns the same answer.
        import datetime
        k = datetime.date.today().toordinal() % len(found)
        found = found[k:] + found[:k]
    return found[:6]


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
        if "leather" in cat.lower():
            noun = cat
        else:
            # "women's jacket" -> "women's leather jacket", the way people search.
            head, _, rest = cat.partition(" ")
            noun = f"{head} leather {rest}" if rest and head.lower().endswith("'s") \
                else f"leather {cat}"
        shape = " ".join(w for w in look if w != "leather")
        queries = [
            f"{noun} {shape} genuine leather".replace("  ", " ").strip(),
            f"{noun} lambskin",
        ]
        # Two brand-led queries, so the makers known for leather actually show
        # up to be judged instead of losing to whoever has the biggest feed.
        for maker in makers_for(noun)[:max_queries - len(queries)]:
            queries.append(f"{maker} {noun}")
        return queries[:max_queries]

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
