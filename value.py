"""
Filo fair price (v30, 19 Oct 2026). Quality and price in one answer.

The question a shopper actually has is not "is this good?" but "is this worth
the money?". fabric.py answers the first. market.py says what shops charge.
This file says what a piece made like this should cost, from what it is made
of and how and where it was made, then puts the price against that.

    fair price = (fabric + making + trims) x a fair markup

  fabric   garment weight x fibre price (by grade) turned into cloth
  making   sewing minutes x how carefully it's made x the wage where it's made
  markup   from a lean shop (H&M, Inditex) to a typical full-price shop (CBI)

Every number lives in pricebank.json with its source and whether it is
sourced, derived or a judgement. Nothing here reads a brand name. The same
lambskin bag gets a higher fair price only because more is SEEN to have gone
into it: a better grade of skin, turned edges, a leather lining, more hours.

RULES
- Quality never reads price. The score is fabric.py's and is never touched.
- What isn't known widens the range instead of being guessed: no country on
  the tag spans Bangladesh to China; no grade spans the grades it could be.
- Saying a price is high needs it to be well past the top of the range
  (verdict_rules.above_x). A false "overpriced" costs trust (see v28.1).
- Words: short, plain, no dashes, never names the brand or the shop.
"""
import json
import os
import re

import fabric

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "pricebank.json"), encoding="utf-8") as _f:
    BANK = json.load(_f)

FX = {k: v["value"] for k, v in BANK["fx"].items() if not k.startswith("_")}
RULES = BANK["verdict_rules"]

# ------------------------------------------------------------------ levels
STEAL = "steal"
WORTH_IT = "worth_it"
HIGH_PRICE = "good_piece_high_price"
FAIR_FOR_WHAT = "fair_for_what_it_is"
HIGH_FOR_WHAT = "high_for_what_it_is"
GET_WHAT_YOU_PAY = "you_get_what_you_pay_for"
OVERPRICED = "overpriced"
TOO_CHEAP = "hard_to_make_at_this_price"
CHECK_GRADE = "low_price_check_the_grade"

LABELS = {
    STEAL: "A steal",
    WORTH_IT: "Worth it",
    HIGH_PRICE: "Good piece, high price",
    FAIR_FOR_WHAT: "Fair for what it is",
    HIGH_FOR_WHAT: "High for what it is",
    GET_WHAT_YOU_PAY: "You get what you pay for",
    OVERPRICED: "Overpriced",
    TOO_CHEAP: "Hard to make at this price",
    CHECK_GRADE: "Low price, check the grade",
}
# Colour for the app: good / ok / poor. Same three as the breakdown dots.
COLOURS = {STEAL: "good", WORTH_IT: "good", FAIR_FOR_WHAT: "ok",
           HIGH_PRICE: "ok", HIGH_FOR_WHAT: "ok", GET_WHAT_YOU_PAY: "ok",
           OVERPRICED: "poor", TOO_CHEAP: "poor", CHECK_GRADE: "ok"}
# Order used to rank better made options: best value first.
RANK = {STEAL: 0, WORTH_IT: 1, FAIR_FOR_WHAT: 2, GET_WHAT_YOU_PAY: 3,
        HIGH_FOR_WHAT: 4, CHECK_GRADE: 4, HIGH_PRICE: 5, OVERPRICED: 6, TOO_CHEAP: 7,
        None: 8}

# ------------------------------------------------------------------ groups
_BAG = re.compile(r"\b(?:bag|handbag|purse|tote|crossbody|clutch|satchel|"
                  r"shoulder\s+bag|hobo|bucket\s+bag|backpack|pochette)s?\b", re.I)

_PLURAL = {"t-shirt": "t-shirts", "top": "tops", "blouse": "blouses",
           "shirt": "shirts", "sweater": "sweaters", "cardigan": "cardigans",
           "hoodie": "hoodies", "jeans": "jeans", "trousers": "trousers",
           "shorts": "shorts", "leggings": "leggings", "skirt": "skirts",
           "dress": "dresses", "jacket": "jackets", "blazer": "blazers",
           "coat": "coats", "bag": "bags"}


def group_for(category, name=None):
    """Filo garment group, or 'bag'. None when it isn't something we price."""
    text = " ".join(x for x in (category, name) if x)
    if _BAG.search(text or ""):
        return "bag"
    import catalog                       # late: catalog imports a lot
    return catalog.garment_group(category) or catalog.garment_group(name)


# ------------------------------------------------------------------ country
def country_made(text):
    """Bank country key from 'Made in Italy', or None."""
    m = re.search(r"\bmade\s+in\s+(?:the\s+)?([a-z][a-z .'-]{1,30})", text or "", re.I)
    if not m:
        return None
    said = m.group(1).lower()
    for key, names in BANK["country_names"].items():
        for n in names:
            if re.match(r"\s*" + re.escape(n) + r"\b", said):
                return key
    return None


def _wage(country):
    """(low, mid, high) USD an hour. A known country is its middle throughout."""
    known = bool(country) and country in BANK["countries"]
    c = BANK["countries"][country] if known else BANK["countries"]["_unknown"]
    lo, hi = c["wage_usd_h"]
    mid = (lo + hi) / 2
    return (mid, mid, mid) if known else (lo, mid, hi)


# ------------------------------------------------------------------ grades
_LONG_STAPLE = re.compile(r"\b(?:supima|pima|egyptian|giza|sea[\s-]+island|"
                          r"(?:extra[\s-]+)?long[\s-]+staple)\b", re.I)
_FINE_WOOL = re.compile(r"\bsuper\s*(?:1[0-9]0|[2-9]00)'?s?\b|\b(?:extra[\s-]*fine|"
                        r"superfine|ultra[\s-]*fine)\b|\b1[5-8](?:\.\d)?\s*(?:micron|µm|mic)\b", re.I)
_MERINO = re.compile(r"\bmerino\b", re.I)
_CASH_A = re.compile(r"\bgrade[\s-]*a\b|\bbaby\s+cashmere\b|\b1[34](?:\.\d)?\s*(?:micron|µm|mic)\b", re.I)
_CASH_PLY = re.compile(r"\b[2-4][\s-]*ply\b", re.I)
_LEATHER_TOP = re.compile(r"\b(?:haas|alran|degermann|walpier|conceria|tuscan|vera\s+pelle|"
                          r"vegetable[\s-]+tanned|veg[\s-]+tan(?:ned)?)\b", re.I)
_LEATHER_GOOD = re.compile(r"\b(?:full[\s-]*grain|aniline|nappa|plong[eé])\b", re.I)
_LEATHER_LOW = re.compile(r"\b(?:genuine[\s-]+leather|corrected[\s-]*grain|split[\s-]*(?:grain|leather)|"
                          r"top[\s-]*grain|embossed)\b", re.I)


def _resolve(key):
    """Bank entry for a fibre, following alias_of. (entry, grade overrides)."""
    entry = BANK["fibres"].get(key)
    if entry is None:
        return None, None
    over = None
    if "alias_of" in entry:
        over = entry
        entry = BANK["fibres"][entry["alias_of"]]
    return entry, over


def fibre_grades(key, text):
    """Which grades of this fibre the piece could be: (grades, stated?)."""
    entry, over = _resolve(key)
    if entry is None:
        return [], False
    t = text or ""
    stated = None
    if key == "cotton" and _LONG_STAPLE.search(t):
        stated = "long_staple"
    elif key in ("wool", "merino"):
        if _FINE_WOOL.search(t):
            stated = "fine_merino"
        elif key == "merino" or _MERINO.search(t):
            stated = "merino"
    elif key == "cashmere":
        if _CASH_A.search(t):
            stated = "grade_a"
        elif _CASH_PLY.search(t):
            stated = "premium"
    if stated and stated in entry["grades"]:
        return [stated], True
    rng = (over or {}).get("range_grades") or entry.get("range_grades") or [entry["default"]]
    return list(rng), False


def leather_grades(text):
    t = text or ""
    if _LEATHER_LOW.search(t):
        return ["commodity"], True
    if _LEATHER_TOP.search(t):
        return ["top"], True
    if _LEATHER_GOOD.search(t):
        return ["standard"], True
    return list(BANK["leather"]["unknown_range"]), False


# ------------------------------------------------------------------ make
# Signs that change what a piece is made of rather than how carefully it was
# sewn. They add material, not hours, so they don't move the make tier.
MATERIAL_TOKENS = {"leather_lining"}
LEATHER_LINING_AREA_X = 1.5   # judgement: a full leather lining is most of the outer again


def make_tier(detail):
    """basic / unknown / fine / exceptional, from what was actually seen."""
    tokens = [t for t in ((detail or {}).get("construction") or [])
              if t not in MATERIAL_TOKENS]
    proofs = [t for t in tokens if fabric.CONSTRUCTION.get(t, (0,))[0] > 0]
    faults = [t for t in tokens if fabric.CONSTRUCTION.get(t, (0,))[0] < 0]
    has_grade = any((detail or {}).get("grade_lines") or [])
    if faults:
        return "basic"
    if len(proofs) >= 2:
        return "exceptional"
    if proofs or has_grade:
        return "fine"
    return "unknown"


# ------------------------------------------------------------------ fabric
def _fabric_usd_per_kg(key, grade, process, side):
    """USD per kg of cloth for one fibre at one grade. side: 0 low, 1 high, None mid."""
    entry, _ = _resolve(key)
    if entry is None:
        return None
    g = entry["grades"].get(grade) or entry["grades"][entry["default"]]
    fibre = g["usd_per_kg"] if side is None else g["range"][side]
    conv = BANK["fabric"]["conversion_usd_per_kg"]
    if entry.get("yarn"):
        return fibre + conv["yarn_knit"]["value"]
    y = BANK["fabric"]["yarn_yield"]["value"]
    c = conv.get(process) or conv["woven"]
    return fibre / y + c["value"]


_GSM = re.compile(r"\b(\d{2,3})\s*(?:gsm|g/m2|g/m²)\b", re.I)
_OZ = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*-?\s*oz\b", re.I)


def _mass(group, g, main, text):
    """Garment mass in kg, scaled by a stated fabric weight when there is one."""
    top = max(main, key=lambda m: m[1])[0] if main else None
    mass = g["mass_kg"]
    if g.get("fine_mass_kg") and top in ("cashmere", "silk", "merino"):
        mass = g["fine_mass_kg"]
    m = _GSM.search(text or "")
    gsm = int(m.group(1)) if m else None
    if gsm is None:
        o = _OZ.search(text or "")
        if o and "denim" in (text or "").lower():
            gsm = round(float(o.group(1)) * 33.91)
    if gsm:
        mass *= max(0.5, min(2.0, gsm / g["gsm"]))
    return mass


# ------------------------------------------------------------------ the build
def _landed_x(country, market):
    d = BANK["landed"]["duty"].get(market) or BANK["landed"]["duty"]["US"]
    duty = 0.0 if country in d.get("free_from", []) else d["default"]
    duty += d.get("add", {}).get(country or "", 0.0)
    return 1 + duty + BANK["landed"]["freight"]["value"]


def _cm(minutes, wage):
    mk = BANK["making"]
    return minutes * mk["efficiency"]["value"] * wage / 60 * mk["burden"]["value"] * mk["profit"]["value"]


def _span(values):
    """(low, mid, high) from candidate (low, mid, high) triples."""
    return (min(v[0] for v in values), sum(v[1] for v in values) / len(values),
            max(v[2] for v in values))


def build(group, matched, raw_text, detail, country=None, market="US"):
    """What it costs to make, and the fair range. None if we can't price it.

    Every cost is a (low, mid, high) triple. The fair range is built from the
    MIDDLE cost and the markup band, so the range a shopper sees is about the
    markup, not about what we don't know. What we don't know is reported as
    'unknowns' and as the wider 'could_be' range."""
    if group is None or not matched:
        return None
    material = fabric.material_class(matched)
    tier = make_tier(detail)
    tier_key = "standard" if tier == "unknown" else tier
    wages = _wage(country)
    unknowns, parts = [], []
    profit = BANK["making"]["profit"]["value"]
    packing = BANK["making"]["packing_usd"]["value"]

    leather_piece = material == "real leather" and group in ("bag", "jacket", "coat", "blazer")
    if group == "bag" and material != "real leather":
        return None                           # cloth bags: not in the bank yet
    top_key = max(matched, key=lambda m: m[1])[0]
    grade_known = True

    if leather_piece:
        lg = BANK["leather_goods"]["bag" if group == "bag" else "jacket"]
        grades, stated = leather_grades(raw_text)
        grade_known = stated and len(grades) == 1
        lp = BANK["leather"]["grades"]
        per_sqft = _span([(lp[x]["range"][0], lp[x]["usd_per_sqft"], lp[x]["range"][1])
                          for x in grades])
        area = lg["sqft"] * lg["yield"]
        lined = "leather_lining" in ((detail or {}).get("construction") or [])
        if lined:
            area *= LEATHER_LINING_AREA_X
        mat = tuple(area * x for x in per_sqft)
        hours = lg["hours"][tier_key]
        trims = lg["trims_usd"][tier_key]
        # Hours are real working time, so the lost-time allowance is not added again.
        minutes = hours * 60 / BANK["making"]["efficiency"]["value"]
        if not stated:
            unknowns.append(("grade", "Grade of the leather",
                             "Look for full grain, aniline or nappa on the tag or listing. "
                             "A plastic-looking, perfectly even surface is usually corrected leather."))
        parts.append(f"About {lg['sqft']} sq ft of leather" +
                     (", plus a leather lining." if lined else "."))
        if stated:
            parts.append(lp[grades[-1]]["line"])
        parts.append(f"About {hours} hour{'s' if hours != 1 else ''} of work for this make.")
        prem = 1.0
    else:
        g = BANK["garments"].get(group)
        if g is None:
            return None
        main_text, support = fabric.split_sections(raw_text)
        total = sum(p for _, p, *_ in matched) or 100
        mass = _mass(group, g, matched, raw_text)
        kg = mass * (1 + g["waste"])
        lo = mid = hi = 0.0
        grade_lines = []
        for key, pct, *_ in matched:
            grades, stated = fibre_grades(key, main_text)
            if not grades:
                continue
            share = kg * pct / total
            trip = _span([(_fabric_usd_per_kg(key, gr, g["process"], 0),
                           _fabric_usd_per_kg(key, gr, g["process"], None),
                           _fabric_usd_per_kg(key, gr, g["process"], 1)) for gr in grades])
            lo += share * trip[0]; mid += share * trip[1]; hi += share * trip[2]
            entry, _ = _resolve(key)
            if stated:
                grade_lines.append(entry["grades"][grades[0]]["line"])
            elif len(grades) > 1 and pct / total >= 0.5:
                grade_known = False
        if hi <= 0:
            return None
        mat = (lo, mid, hi)
        minutes = g["minutes"] * BANK["make_tiers"][tier_key]["minutes_x"]
        trims = g["trims_usd"]
        parts.append(f"About {round(mass * 1000, -1):.0f} g of fabric.")
        parts.extend(grade_lines[:1])
        if not grade_known:
            unknowns.append(("grade", f"Grade of the {top_key}",
                             f"The tag doesn't say which {top_key} this is. "
                             "Grades are priced very differently."))
        prem = 1.0
        if support.strip() and group in ("dress", "skirt", "coat", "jacket", "blazer", "trousers"):
            prem *= BANK["construction_premiums"]["lined"]["price_x"]

    # Market premium measured with brand held fixed (BLS).
    if "hand_finished" in ((detail or {}).get("construction") or []):
        prem *= BANK["construction_premiums"]["hand_finished"]["price_x"]

    cm = tuple(_cm(minutes, w) for w in wages)
    fob = tuple((m + c + trims + packing) * profit for m, c in zip(mat, cm))

    lean = BANK["markup"]["lean"]["value"]
    typical = BANK["markup"]["typical"]["value"]
    landed = _landed_x(country, market)
    fair = (fob[1] * landed * lean * prem, fob[1] * typical * prem)
    could_be = (fob[0] * landed * lean * prem, fob[2] * typical * prem)

    if country is None:
        unknowns.append(("country", "Where it's made",
                         "Check the tag for 'Made in'. Making costs differ a lot by country."))
    else:
        parts.append(f"Made in {country.title() if country != 'usa' else 'the USA'}.")
    if tier == "unknown":
        unknowns.append(("make", "How it's sewn",
                         "Scan a seam inside the piece to check how it's made. "
                         "Careful sewing takes longer and costs more."))
    else:
        parts.append(BANK["make_tiers"][tier]["line"])

    return {
        "group": group,
        "top_fibre": top_key,
        "grade_known": grade_known,
        "material_usd": [round(x, 2) for x in mat],
        "making_usd": [round(x, 2) for x in cm],
        "factory_usd": [round(x, 2) for x in fob],
        "fair_usd": list(fair),
        "could_be_usd": list(could_be),
        "make_tier": tier,
        "country": country,
        "leather": leather_piece,
        "built_from": parts,
        "unknowns": [{"key": k, "title": t, "tip": tip} for k, t, tip in unknowns],
    }


# ------------------------------------------------------------------ money
def _to_market(usd, market):
    return usd * FX["USD_CAD"] if market == "CA" else usd


def _round(x):
    if x < 20:
        return max(1, round(x))
    if x < 200:
        return int(5 * round(x / 5))
    if x < 1000:
        return int(10 * round(x / 10))
    return int(50 * round(x / 50))


def _money(x):
    return f"${x:,.0f}"


# ------------------------------------------------------------------ the verdict
def _quality_band(score):
    if score is None:
        return None
    if score >= RULES["quality_good"]:
        return "good"
    if score >= RULES["quality_low"]:
        return "mid"
    return "low"


def _what(matched, group):
    """'a 100% wool coat', 'a mostly cotton pair of jeans', 'a leather bag'."""
    key = max(matched, key=lambda m: m[1])
    total = sum(p for _, p, *_ in matched) or 100
    pct = round(100 * key[1] / total)
    lead = "leather" if key[0] == "leather" else (
        f"100% {key[0]}" if pct >= 100 else f"mostly {key[0]}")
    noun = {"jeans": "pair of jeans", "trousers": "pair of trousers",
            "shorts": "pair of shorts", "leggings": "pair of leggings"}.get(group, group)
    if noun.startswith("pair"):
        return f"a pair of {lead} {group}"
    return f"a {lead} {noun}"


def assess(item, detail, score, market_check=None):
    """The fair price and, when there is a price, the value verdict.

    item: composition, category, name, price, country (CA/US), tag_text.
    Returns None when the piece can't be priced (no fibre, or a group not in
    the bank). Never raises."""
    try:
        return _assess(item, detail, score, market_check)
    except Exception:                       # noqa: BLE001  never block a verdict
        return None


def _assess(item, detail, score, market_check):
    if not detail:
        return None
    raw = item.get("composition") or ""
    tag = " ".join(x for x in (item.get("tag_text"), raw, item.get("name")) if x)
    market = "CA" if (item.get("country") or "").upper() == "CA" else "US"
    group = group_for(item.get("category"), item.get("name"))
    country = country_made(tag)
    b = build(group, detail["matched"], tag, detail, country=country, market=market)
    if b is None:
        return None

    def money_range(pair):
        a, z = (_round(_to_market(x, market)) for x in pair)
        return (a, z) if z > a else (a, a + max(1, _round(a * 0.2)))

    lo, hi = money_range(b["fair_usd"])
    clo, chi = money_range(b["could_be_usd"])
    currency = "CAD" if market == "CA" else "USD"
    what = _what(detail["matched"], group)
    range_line = f"A fair price for {what} made like this is about {_money(lo)} to {_money(hi)}."
    unknown_words = {"country": "where it's made", "grade": "the grade", "make": "how it's sewn"}
    missing = [unknown_words[u["key"]] for u in b["unknowns"] if u["key"] in ("country", "grade")]
    could_line = None
    if missing and (clo < lo or chi > hi):
        could_line = (f"Depending on {' and '.join(missing)}, it could fairly be "
                      f"{_money(clo)} to {_money(chi)}.")

    out = {
        "fair_low": lo,
        "fair_high": hi,
        "could_be_low": clo,
        "could_be_high": chi,
        "currency": currency,
        "range_line": range_line,
        "could_be_line": could_line,
        "make_tier": b["make_tier"],
        "built_from": b["built_from"],
        "unknowns": b["unknowns"],
        # The make is almost never known from a tag, so only the grade and the
        # country count against confidence.
        "confidence": ("high" if not missing else "medium" if len(missing) == 1 else "low"),
        "level": None, "label": None, "colour": None, "line": None,
        "based_on": ("Built from what it's made of and how and where it's made, "
                     "with a fair shop markup. Not a judgement on the brand or the shop."),
        "_cost": b,
    }

    # What Filo shoppers saw pieces like this priced at (aggregates.py, over the
    # privacy floor only). A third, separate line: it never moves the range.
    try:
        import aggregates
        cell = aggregates.price_cell(group, b["top_fibre"], _quality_band(score), market)
    except Exception:                       # noqa: BLE001
        cell = None
    if cell:
        out["shoppers"] = {
            "median": cell["median"], "p25": cell["p25"], "p75": cell["p75"],
            "devices": cell["devices"], "currency": cell["currency"],
            "line": (f"Filo shoppers saw pieces like this priced around {_money(cell['median'])} "
                     f"({cell['devices']} shoppers)."),
        }

    price = item.get("price")
    if price is None or score is None:
        out["line"] = range_line
        return out

    q = _quality_band(score)
    # What we don't know can't count against the price: with the country or the
    # grade unknown, "high" is judged against the most generous reading of them.
    # Thresholds use the unrounded numbers, so rounding for display can't flip a verdict.
    exact_lo = _to_market(b["fair_usd"][0], market)
    exact_hi = _to_market(b["could_be_usd"][1] if missing else b["fair_usd"][1], market)
    judge_hi = chi if missing else hi
    above = exact_hi * RULES["above_x"]
    way_above = exact_hi * RULES["way_above_x"]
    mlevel = (market_check or {}).get("level")
    shops_agree = mlevel in ("fair", "good_value")
    p = _money(price)
    rng = f"{_money(lo)} to {_money(hi)}"
    top_key = b["top_fibre"]
    premium = top_key in RULES["floor_premium_fibres"]
    material_lo = _to_market(b["material_usd"][0], market)

    if premium and price < material_lo:
        # Floor: less than the cheapest grade of the material alone costs.
        level = TOO_CHEAP
        line = (f"{p} is less than the {top_key} alone should cost "
                f"(about {_money(_round(material_lo))}). It may be a lower grade, a blend, or very light.")
    elif price < min(exact_lo, lo):
        if b["make_tier"] == "basic":
            level = GET_WHAT_YOU_PAY
            line = (f"Low for {what}, and the make shows it: faults were seen. "
                    f"Expect it to wear out sooner. {range_line}")
        elif q == "good" and premium and not b["grade_known"]:
            # A low price rules the top grades out; it never proves the piece is good.
            level = CHECK_GRADE
            line = (f"Low for {what}. At {p} it's most likely a lower grade. "
                    f"Check the weight and feel, and look closely at the seams.")
        elif q == "good":
            level = STEAL
            line = f"Below the fair price for {what} made like this ({rng})."
        elif q == "mid":
            level = FAIR_FOR_WHAT
            line = f"A low price, and a fair one for what it is. {range_line}"
        else:
            level = GET_WHAT_YOU_PAY
            line = f"Cheap, and made like it. Expect it to wear out sooner. {range_line}"
    elif price <= above:
        if q == "good" and price > exact_hi:
            level, line = WORTH_IT, f"At the top of a fair price for this make. {range_line}"
        elif q == "good":
            level, line = WORTH_IT, f"The price matches the make. {range_line}"
        elif q == "mid":
            level, line = FAIR_FOR_WHAT, f"About right for what it is. {range_line}"
        else:
            level = GET_WHAT_YOU_PAY
            line = f"The price is fair, but it won't last as long as better fabric. {range_line}"
    else:
        times = price / exact_hi
        strong = price > way_above or mlevel == "overpaying"
        if q == "good":
            level = HIGH_PRICE
            if price > way_above:
                top = _money(judge_hi)
                line = (f"Made well, but {p} is about {times:.1f} times the most a piece made like "
                        f"this can fairly cost ({top}). Much of what you pay is the name.")
            elif shops_agree:
                line = (f"Shops often charge this, but a piece made like this can sell fairly "
                        f"for {rng}. Some of the price is the name.")
            else:
                line = (f"Made well, but priced above what this make costs to sell fairly "
                        f"({rng}). Some of the price is the name.")
        elif q == "mid":
            level = OVERPRICED if strong else HIGH_FOR_WHAT
            line = f"{p} is a lot for {what} made like this. A fair price is about {rng}."
        else:
            level = OVERPRICED
            line = f"Priced like quality, made like it isn't. {range_line}"

    out.update(level=level, label=LABELS[level], colour=COLOURS[level], line=line)
    return out


def public(v):
    """What the app gets: everything except the working."""
    if not v:
        return None
    return {k: x for k, x in v.items() if not k.startswith("_")}


def compact(v):
    """The short form used on each better made option."""
    if not v:
        return None
    return {"level": v.get("level"), "label": v.get("label"), "colour": v.get("colour"),
            "fair_low": v.get("fair_low"), "fair_high": v.get("fair_high"),
            "currency": v.get("currency")}
