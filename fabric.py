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


def parse_composition(text):
    """Pull [(fiber, pct)] out of a string like '60% Cotton, 40% Polyester'."""
    pairs = []
    for m in re.finditer(r"(\d{1,3})\s*%\s*([a-zA-Z ]+?)(?=,|\d|$)", text or ""):
        pct = int(m.group(1))
        name = m.group(2).strip().lower()
        pairs.append((name, pct))
    return pairs


def _fiber_weight(name):
    """Map a raw fiber name (e.g. 'organic cotton') to a known fiber + weight."""
    for key, q in FIBER_QUALITY.items():
        if key in name:
            return key, q
    return name, 5.0  # unknown fiber → neutral


def quality_score(composition):
    pairs = parse_composition(composition)
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
