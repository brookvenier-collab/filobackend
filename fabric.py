"""
Filo quality knowledge base — deterministic scoring from fiber composition.

This is the core IP: every number is traceable to a fiber weight or a rule.
No AI, no black box. Tune the weights below with real data over time.
"""
import re

# Quality weight (0–10) per fiber. Higher = better made / longer lasting.
FIBER_QUALITY = {
    "cotton": 7.0,
    "linen": 8.0,
    "hemp": 7.5,
    "wool": 8.0,
    "merino": 8.5,
    "cashmere": 8.0,
    "silk": 8.0,
    "lyocell": 6.5,
    "tencel": 6.5,
    "modal": 6.0,
    "viscose": 4.5,
    "rayon": 4.5,
    "cupro": 5.0,
    "bamboo": 5.0,
    "acetate": 3.5,
    "polyester": 3.0,
    "acrylic": 2.5,
    "nylon": 4.0,
    "polyamide": 4.0,
    "elastane": 5.0,
    "spandex": 5.0,
    "leather": 8.0,
}

SYNTHETICS = {"polyester", "acrylic", "nylon", "polyamide", "acetate"}
NATURALS = {"cotton", "linen", "hemp", "wool", "merino", "cashmere", "silk"}


KNOWN_FIBERS = set(FIBER_QUALITY.keys()) | {"merino"}


def _clean(name):
    """'organic combed cotton' → 'cotton' if we recognise anything in it."""
    name = name.strip().lower()
    for key in KNOWN_FIBERS:
        if key in name:
            return key
    return name


def parse_composition(text, allow_bare=True):
    """Pull [(fiber, pct)] out of whatever the tag or the shopper actually wrote.

    Handles, in order of confidence:
      '60% Cotton, 40% Polyester'   → explicit, both directions
      'Cotton 60%  Polyester 40%'   → fiber-first
      '80 cotton 20 polyester'      → numbers without the % sign (common OCR miss)
      'cotton'                      → single fiber, assume 100%      (allow_bare)
      'cotton polyester'            → unknown split, assume even     (allow_bare)

    allow_bare=False refuses anything without explicit numbers. Used when scoring
    someone *else's* product listing, where guessing would mean recommending an
    item we cannot actually vouch for.
    """
    text = (text or "").lower()

    # Where does each known fiber appear? (spans, so we can pair by proximity)
    fiber_spans = []
    for fiber in KNOWN_FIBERS:
        for m in re.finditer(r"\b" + re.escape(fiber) + r"\b", text):
            fiber_spans.append((m.start(), m.end(), fiber))
    if not fiber_spans:
        return []
    fiber_spans.sort()

    # 1. Explicit percentages, then 2. bare numbers. Pair each number with the
    #    nearest fiber — after it first ("60% cotton"), otherwise before it
    #    ("cotton 60%"). Order-agnostic, and it can't double-count the way two
    #    competing regexes could.
    for pattern in (r"(\d{1,3})\s*%", r"\b(\d{1,3})\b"):
        numbers = list(re.finditer(pattern, text))
        if not numbers:
            continue

        def nearest(m, side):
            """Closest fiber after / before this number, within 25 chars."""
            best, best_dist = None, 26
            for s, e, fiber in fiber_spans:
                if side == "after" and s >= m.end():
                    dist = s - m.end()
                elif side == "before" and e <= m.start():
                    dist = m.start() - e
                else:
                    continue
                if dist < best_dist:
                    best, best_dist = fiber, dist
            return best

        # A tag is written one way throughout: either "60% cotton" or "cotton 60%".
        # Decide which by counting where fibers actually sit, then apply that
        # consistently — otherwise a number with a fiber on both sides silently
        # binds to the wrong one and the whole composition comes out wrong.
        after_hits = sum(1 for m in numbers if nearest(m, "after"))
        before_hits = sum(1 for m in numbers if nearest(m, "before"))
        primary, fallback = ("after", "before") if after_hits >= before_hits else ("before", "after")

        pairs = []
        for m in numbers:
            fiber = nearest(m, primary) or nearest(m, fallback)
            if fiber:
                pairs.append((fiber, int(m.group(1))))
        pairs = _dedupe(pairs)
        if pairs:
            return pairs

    if not allow_bare:
        return []

    # 3. Bare fiber names, no numbers at all. One → 100%, several → even split.
    found = []
    for _, _, fiber in fiber_spans:
        if fiber not in found:
            found.append(fiber)
    share = 100 // len(found)
    return [(f, share) for f in found]


def _dedupe(pairs):
    """First mention of a fiber wins; drop nonsense percentages."""
    seen, out = set(), []
    for name, pct in pairs:
        if not (0 < pct <= 100) or name in seen:
            continue
        seen.add(name)
        out.append((name, pct))
    return out


def _fiber_weight(name):
    """Map a raw fiber name (e.g. 'organic cotton') to a known fiber + weight."""
    for key, q in FIBER_QUALITY.items():
        if key in name:
            return key, q
    return name, 5.0  # unknown fiber → neutral


def synthetic_pct(matched):
    """Share of the garment that is plastic, from a scored `matched` list."""
    return sum(p for k, p, _ in matched if k in SYNTHETICS)


def quality_score(composition, allow_bare=True):
    pairs = parse_composition(composition, allow_bare=allow_bare)
    if not pairs:
        return None, []
    total = sum(p for _, p in pairs) or 100
    weighted = 0.0
    matched = []
    synth_pct = 0
    for name, pct in pairs:
        key, q = _fiber_weight(name)
        weighted += q * pct
        matched.append((key, pct, q))
        if key in SYNTHETICS:
            synth_pct += pct
    score = weighted / total
    if synth_pct >= 80:      # mostly plastic → real-world penalty
        score -= 0.8
    score = max(0.0, min(10.0, round(score, 1)))
    return score, matched


def verdict(score):
    if score >= 8:   return "The real thing"
    if score >= 6:   return "Solid"
    if score >= 4.5: return "It depends"
    return "Skip it"


def wear_estimate(score):
    if score >= 8:   return "Built to last — years of wear, 50+ washes with care."
    if score >= 6:   return "Solid — should hold up for ~30–50 washes."
    if score >= 4.5: return "Middling — expect ~15–30 washes before it shows wear."
    return "Low — likely to pill or lose shape within ~5–10 washes."


def expected_wears(score):
    """Roughly how many wears before it looks tired. The denominator in
    cost-per-wear, which is how Filo justifies a higher price honestly."""
    if score is None:  return None
    if score >= 8:     return 200
    if score >= 7:     return 120
    if score >= 6:     return 60
    if score >= 4.5:   return 25
    return 8


def cost_per_wear(price, score):
    wears = expected_wears(score)
    if not price or not wears:
        return None
    return round(price / wears, 2)


def care_flags(matched):
    fibers = {k for k, _, _ in matched}
    flags = []
    if fibers & {"wool", "merino", "cashmere", "silk"}:
        flags.append("Delicate — hand wash or dry clean, lay flat to dry.")
    if fibers & {"viscose", "rayon"}:
        flags.append("Can shrink or warp when wet — wash cold and reshape.")
    if fibers & {"polyester", "acrylic"}:
        flags.append("Easy to wash, but prone to pilling and holding odor.")
    if "linen" in fibers:
        flags.append("Wrinkles easily — part of the charm, but expect creasing.")
    return flags


def reasons(score, matched, price):
    out = []
    synth = sum(p for k, p, _ in matched if k in SYNTHETICS)
    natural = sum(p for k, p, _ in matched if k in NATURALS)
    top_key = sorted(matched, key=lambda x: x[1], reverse=True)[0][0]

    if synth >= 80:
        out.append(f"{synth}% synthetic — it'll trap heat, pill quickly, and won't age well.")
    elif natural >= 80:
        out.append(f"Mostly {top_key} — breathable, durable, and it only gets better with time.")
    else:
        out.append(f"A {natural}% natural / {synth}% synthetic blend — a trade-off between feel and longevity.")

    if score >= 7:
        out.append("Well-made enough to keep for years.")
    elif score < 4.5:
        out.append("This is built to be replaced, not kept.")
    return out


def value_note(score, price):
    if price is None:
        return None
    if score < 4.5 and price >= 40:
        return "Priced like quality, made like fast fashion — you're paying for the name, not the make."
    if score >= 7 and price <= 40:
        return "Genuinely well-made for the price — a real find."
    if score >= 7:
        return "The price reflects real quality here."
    return None


def analyze(item):
    """The one function the app calls (via /analyze). Returns the full verdict."""
    composition = item.get("composition", "")
    price = item.get("price")
    score, matched = quality_score(composition)

    if score is None:
        return {
            "score": None,
            "verdict": "Need the tag",
            "reasons": ["Couldn't read the fiber content — try entering it by hand."],
            "wears": None,
            "care_flags": [],
            "value_note": None,
            "alternatives": [],
            "alternatives_note": "Better-made alternatives turn on once product search is connected.",
        }

    return {
        "score": score,
        "verdict": verdict(score),
        "reasons": reasons(score, matched, price),
        "wears": wear_estimate(score),
        "care_flags": care_flags(matched),
        "value_note": value_note(score, price),
        "alternatives": [],  # populated later via SerpAPI + look-matching
        "alternatives_note": "Better-made alternatives turn on once product search is connected.",
    }
