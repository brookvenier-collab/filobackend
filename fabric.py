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
    # Leather's imitations. These are plastic coated onto a fabric backing: they
    # crack and peel rather than age, and can't be repaired. Scored as the
    # plastics they are, whatever the tag calls them.
    "polyurethane": 2.5,
    "pvc": 1.5,
    "bondedleather": 3.0,   # leather scraps glued to a backing — peels like PU
}

SYNTHETICS = {"polyester", "acrylic", "nylon", "polyamide", "acetate",
              "polyurethane", "pvc"}
NATURALS = {"cotton", "linen", "hemp", "wool", "merino", "cashmere", "silk",
            "leather"}

# Tags and listings say "vegan leather" and "lambskin", never "polyurethane" and
# "leather". Rewritten to one canonical word BEFORE parsing, fakes first — so
# "faux leather" can never be read as the word "leather" and scored as the real
# thing. Order matters: longest and most specific phrases first.
_MATERIAL_ALIASES = [
    (r"\bfaux[\s-]+suede\b|\bmicro[\s-]?suede\b|\bvegan[\s-]+suede\b", "polyester"),
    (r"\b(?:faux|vegan|fake|imitation|synthetic|man[\s-]?made|artificial|pu|pvc)"
     r"[\s-]+leather\b|\bleatherette\b|\bpleather\b|\bskai\b", "polyurethane"),
    (r"\b(?:bonded|reconstituted|recycled)[\s-]+leather\b", "bondedleather"),
    (r"\bpolyurethane\b|\bpu\b", "polyurethane"),
    (r"\bpolyvinyl[\s-]+chloride\b|\bvinyl\b", "pvc"),
    (r"\b(?:genuine|real|full[\s-]+grain|top[\s-]+grain|nappa)[\s-]+leather\b", "leather"),
    (r"\b(?:lamb|calf|cow|goat|sheep|deer|buffalo)[\s-]?(?:skin|hide|leather)\b"
     r"|\bsuede\b|\bnubuck\b|\bshearling\b", "leather"),
]


def normalize_materials(text):
    """Rewrite leather and its imitations to canonical fiber words."""
    text = (text or "").lower()
    for pattern, canonical in _MATERIAL_ALIASES:
        text = re.sub(pattern, canonical, text)
    return text


def material_class(matched):
    """'real leather', 'faux leather', or None — from a scored `matched` list.

    Decided by what makes up most of the garment, so a wool coat with leather
    elbow patches is not a leather coat.
    """
    if not matched:
        return None
    top = max(matched, key=lambda m: m[1])[0]
    if top == "leather":
        return "real leather"
    if top in ("polyurethane", "pvc", "bondedleather"):
        return "faux leather"
    return None


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
    text = normalize_materials(text)

    # OCR often splits "100%" into "1 00%" or "10 0%" (a gap in the tag's font).
    # Read as written, that becomes "1% leather". Rejoin digits that only make
    # sense together, so the percentage survives the camera.
    text = re.sub(r"\b(\d)\s+(\d{2})\s*%", r"\1\2%", text)
    text = re.sub(r"\b(\d{2})\s+(\d)\s*%", r"\1\2%", text)

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
        if len(pairs) == 1 and pairs[0][1] < 100:
            # One fiber on the tag is the whole garment. A lone "1%" or "10%"
            # is a misread, not a composition.
            pairs = [(pairs[0][0], 100)]
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


# ======================================================================
# THE SCORE, IN NAMED PARTS (v27, 16 Oct 2026)
#
# Every fibre starts at 7.0: cotton, the reference garment in the EU and French
# durability methods (Ecobalyse). Each part then moves it, and each part names
# its source. The size of each part (the K_ weights) is Filo's editorial
# choice and is labelled as such: the sources give direction and relative
# size between fibres, not a 0-100 score.
#
#   life      how long pieces of this fibre are kept and worn
#             Laitala & Klepp 2020 (53,461 garments), via LIFE_FIBRE below
#   comfort   how much moisture the fibre takes up, i.e. how it breathes
#             ASTM D1909-04 commercial moisture regain
#   pills     whether pills stay on the surface
#             Yates 2002; Schindler & Hauser 2004; pill area kept rising for
#             polyester in Živičnjak et al. 2026. Lab pilling GRADES are mixed
#             across studies, so this part is about pills staying, not forming.
#   wear      abrasion, for fibres measured weaker than cotton
#             Tomljenović, Živičnjak & Skenderi 2024: cotton rib ~65,000
#             Martindale cycles to breakage against viscose 30-40,000 and
#             lyocell 20-25,000 after wet processing. Viscose also keeps only
#             ~45-60% of its strength wet (Zhang et al. 2018).
#
# Leather and its imitations keep their FIBER_QUALITY values for now (no
# matching sources yet). Full write-up: claude/filo-evidence-audit.md.
# ======================================================================
SCORE_REFERENCE = 7.0

# ASTM D1909-04 commercial moisture regain, %. Linen uses the linen value
# (8.75), not raw flax (12.0). Modal, lyocell and cupro have no ASTM entry and
# take the rayon value.
MOISTURE_REGAIN = {
    "cotton": 7.0, "linen": 8.75, "hemp": 12.0, "wool": 13.6, "merino": 13.6,
    "cashmere": 13.6, "silk": 11.0, "viscose": 11.0, "rayon": 11.0,
    "bamboo": 11.0, "modal": 11.0, "lyocell": 11.0, "tencel": 11.0,
    "cupro": 11.0, "acetate": 6.5, "polyester": 0.4, "nylon": 4.5,
    "polyamide": 4.5, "acrylic": 1.5, "elastane": 1.3, "spandex": 1.3,
}
PILLS_STAY = {"polyester", "nylon", "polyamide", "acrylic"}
WEAR_WEAKER = {"viscose": 1.0, "rayon": 1.0, "bamboo": 1.0,
               "lyocell": 1.0, "tencel": 1.0,
               # Gun et al. 2014: modal knits lost more mass than cotton; size
               # not given in the text. Half weight.
               "modal": 0.5}

# EDITORIAL weights (Filo's choice, not a source).
K_LIFE = 4.0          # +0.9 for wool's extra 1.2 years
K_COMFORT = 2.0       # polyester's 0.4% regain costs about 1.9
COMFORT_MIN, COMFORT_MAX = -2.0, 0.5
K_PILLS = 1.0
K_WEAR = 1.0


def fibre_parts(key):
    """[(part, points)] for a garment made 100% of this fibre."""
    if key not in MOISTURE_REGAIN:
        return []
    life = K_LIFE * (LIFE_FIBRE.get(key, 1.0) - 1.0)
    regain = MOISTURE_REGAIN[key]
    comfort = max(COMFORT_MIN, min(COMFORT_MAX,
                                   K_COMFORT * (regain - MOISTURE_REGAIN["cotton"])
                                   / MOISTURE_REGAIN["cotton"]))
    pills = -K_PILLS if key in PILLS_STAY else 0.0
    wear = -K_WEAR * WEAR_WEAKER.get(key, 0.0)
    return [("life", life), ("comfort", comfort), ("pills", pills), ("wear", wear)]


def fibre_points(key):
    parts = fibre_parts(key)
    if not parts:
        return FIBER_QUALITY.get(key, 5.0)
    return round(SCORE_REFERENCE + sum(p for _, p in parts), 2)


PART_LINES = {
    ("life", True): "Pieces in this fibre are kept and worn longer than cotton.",
    ("life", False): "Pieces in this fibre don't last as long as cotton.",
    ("comfort", True): "Takes up moisture well, so it breathes.",
    ("comfort", False): "Takes up very little moisture, so it can feel hot and clammy.",
    ("pills", False): "When it pills, the pills stay on. The fibre is too strong to let them break off.",
    ("wear", False): "Wears through faster than cotton in rub tests.",
}


def score_parts(matched):
    """The score taken apart: start at 7.0, then each part, blended by
    percentage. Only parts that move the score by 0.1 or more are listed."""
    total = sum(p for _, p, _ in matched) or 100
    sums = {}
    for key, pct, _ in matched:
        parts = fibre_parts(key)
        if not parts:
            # Leather family or unknown: one block, its fixed value.
            sums["fixed"] = sums.get("fixed", 0.0) + \
                (FIBER_QUALITY.get(key, 5.0) - SCORE_REFERENCE) * pct / total
            continue
        for name, pts in parts:
            sums[name] = sums.get(name, 0.0) + pts * pct / total
    out = [{"part": "reference", "points": SCORE_REFERENCE,
            "line": "Starts at 70, the cotton reference used in EU and French durability rules."}]
    for name in ("life", "comfort", "pills", "wear", "fixed"):
        pts = round(sums.get(name, 0.0), 1)
        if abs(pts) < 0.1:
            continue
        line = PART_LINES.get((name, pts > 0), "")
        out.append({"part": name, "points": pts, "line": line})
    return out


def _fiber_weight(name):
    """Map a raw fiber name (e.g. 'organic cotton') to a known fiber + points."""
    if name in FIBER_QUALITY:
        return name, fibre_points(name)
    for key in FIBER_QUALITY:
        if key in name:
            return key, fibre_points(key)
    return name, 5.0  # unknown fiber → neutral


def synthetic_pct(matched):
    """Share of the garment that is plastic, from a scored `matched` list."""
    return sum(p for k, p, _ in matched if k in SYNTHETICS)


# Tags list more than one part of the garment: "Shell: 100% Leather / Lining:
# 100% Polyester". The shell is the garment — it is what wears, pills or ages.
# The lining, pocketing, trim and fill are supporting parts, and nearly every
# coat and jacket lines in polyester or acetate. Averaging them in scored a real
# leather jacket 4.7. So the main fabric sets the score and supporting parts can
# only nudge it upward when they are better than the norm (a cupro or silk
# lining, down fill). A standard lining costs nothing.
_MAIN_LABELS = r"shell|outer(?:\s+shell)?|outside|exterior|main(?:\s+fabric)?|body|self|face|fabric|upper"
_SUPPORT_LABELS = (r"lining|linings|lined|interlining|sleeve\s+lining|pocket(?:ing|s)?|"
                   r"trim|trims|rib|ribbing|cuffs?|collar|contrast|filling|fill|"
                   r"padding|wadding|insulation|embroidery|binding|facing")
_SECTION_RE = re.compile(r"\b(" + _MAIN_LABELS + r"|" + _SUPPORT_LABELS + r")\b\s*[:\-–]",
                         re.IGNORECASE)
_SUPPORT_RE = re.compile(r"^(?:" + _SUPPORT_LABELS + r")$", re.IGNORECASE)

# Supporting fibers that are a genuine step up from the usual polyester/acetate.
_SUPPORT_BONUS = {"cupro": 0.3, "silk": 0.3, "down": 0.3, "wool": 0.2,
                  "cotton": 0.2, "linen": 0.2, "lyocell": 0.1, "modal": 0.1,
                  "viscose": 0.1, "leather": 0.2}
SUPPORT_BONUS_CAP = 0.3


# A plastic lining is the norm, not a disaster — but on a premium shell it is a
# real compromise: it doesn't breathe, it's usually the first part to tear, and
# it's where a maker saves money. It costs a little, never more than the cap.
SUPPORT_PENALTY_MAX = 0.5


def support_adjustment(support_text, allow_bare=True):
    """Small +/- from lining, fill and trim. Never larger than +0.3 / -0.5."""
    pairs = parse_composition(support_text, allow_bare=allow_bare)
    if not pairs:
        return 0.0
    total = sum(p for _, p in pairs) or 100
    bonus, synth = 0.0, 0
    for name, pct in pairs:
        key, _ = _fiber_weight(name)
        bonus = max(bonus, _SUPPORT_BONUS.get(key, 0.0) * min(pct, 100) / 100)
        if key in SYNTHETICS:
            synth += pct
    share = synth / total
    if share >= 0.5:
        return -round(SUPPORT_PENALTY_MAX * share, 1)
    return min(bonus, SUPPORT_BONUS_CAP)


def lining_note(composition):
    """One plain line about the lining when it changes the read, else None."""
    main_text, support_text = split_sections(composition)
    if not support_text or not parse_composition(main_text):
        return None
    adj = support_adjustment(support_text)
    _, main_matched = _score_pairs(parse_composition(main_text))
    if adj < 0 and synthetic_pct(main_matched) >= 50:
        return None          # plastic inside plastic — nothing new to say
    if adj < 0:
        return ("Lined in synthetic fabric — it won't breathe like the outside, "
                "and it's usually the first part to wear through.")
    if adj > 0:
        return "A quality lining too — a sign the maker didn't cut corners inside."
    return None


def split_sections(composition):
    """(main_text, support_text). Unlabelled text counts as main."""
    text = composition or ""
    marks = list(_SECTION_RE.finditer(text))
    if not marks:
        return text, ""
    main, support = [text[:marks[0].start()]], []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[m.end():end]
        (support if _SUPPORT_RE.match(m.group(1).strip()) else main).append(chunk)
    return " ".join(main), " ".join(support)


# ======================================================================
# EVIDENCE — what keeps the top of the scale rare (1 Oct 2026)
#
# A care tag proves the fibre. It does not prove the garment: a $40 linen shirt
# and a hand-finished one both say "100% linen". So a score can only go as high
# as the evidence behind it:
#
#   fibre only (every in-store tag)          → tops out at 7.9  "Solid"
#   + a stated grade or weight               → tops out at 8.4  "The real thing"
#   + proof of construction (seams, etc.)    → tops out at 8.9
#   Heirloom (9.0+) needs a grade AND two construction proofs AND no faults.
#
# Penalties always apply in full. Only the upside is gated on evidence.
# Nothing here reads a brand name or a price — status is earned by the make.
# ======================================================================

CAP_FIBRE_ONLY = 7.9
CAP_GRADE = 8.4
CAP_CONSTRUCTION = 8.9

# (pattern on the raw text, fibre it applies to, adjustment, plain-English line)
_GRADE_RULES = [
    (r"\bfull[\s-]*grain\b", "leather", 0.4,
     "Full-grain leather — the strongest, most durable layer of the hide."),
    (r"\btop[\s-]*grain\b", "leather", 0.1,
     "Top-grain leather — sanded smooth, a step below full-grain."),
    (r"\bgenuine[\s-]+leather\b|\bcorrected[\s-]*grain\b|\bsplit[\s-]*(?:grain|leather)\b",
     "leather", -0.8,
     "\u201cGenuine leather\u201d is a lower layer of the hide — real, but it "
     "won't age like full-grain."),
    (r"\b(?:supima|pima|egyptian|sea[\s-]+island|(?:extra[\s-]+)?long[\s-]+staple)\b",
     "cotton", 0.4, "Long-staple cotton — smoother, stronger, and slower to pill."),
    (r"\bsuper\s*(?:1[0-9]0|[2-9]00)'?s?\b", "wool", 0.3,
     "A stated wool grade — finer fibre, a smoother cloth."),
    (r"\b(?:extra[\s-]*fine|superfine|ultra[\s-]*fine)\s+merino\b", "merino", 0.2,
     "Extra-fine merino — softer and finer than standard."),
    (r"\bgrade[\s-]*a\b|\b[2-4][\s-]*ply\b", "cashmere", 0.3,
     "Graded, multi-ply cashmere — denser and far slower to pill."),
    (r"\bmulberry\b|\b(?:19|2[0-9]|3[0-9])[\s-]*momme\b", "silk", 0.3,
     "Mulberry or heavy-weight silk — the durable kind."),
]

# Construction. Each has a token (the app or a seam photo can send it directly)
# and, where a listing might say it in words, a pattern to find it in text.
CONSTRUCTION = {
    # token:            (adjust, pattern or None, line)
    "french_seams":     (0.4, r"\bfrench[\s-]+seams?\b",
                         "French seams — every raw edge enclosed. A couture finish."),
    "flat_felled":      (0.4, r"\bflat[\s-]*fell(?:ed)?\b|\bfelled[\s-]+seams?\b",
                         "Flat-felled seams — the strongest seam there is."),
    "bound_seams":      (0.3, r"\b(?:bound|hong[\s-]+kong)[\s-]+seams?\b",
                         "Bound seams — raw edges wrapped and finished."),
    "hand_finished":    (0.3, r"\bhand[\s-]*(?:finished|stitched|sewn|made)\b",
                         "Hand-finished."),
    "dense_stitching":  (0.3, r"\b(?:1[2-9]|2[0-4])\s*(?:spi|stitches\s+per\s+inch)\b",
                         "Dense stitching — seams that won't open up."),
    "pattern_matched":  (0.2, r"\bpattern[\s-]*match(?:ed|ing)\b|\bmatched\s+(?:stripes|checks|plaid)\b",
                         "Pattern matched at the seams — slow, careful cutting."),
    "quality_hardware": (0.2, r"\b(?:ykk|riri|lampo)\b|\b(?:horn|corozo|mother[\s-]+of[\s-]+pearl)\s+buttons?\b",
                         "Quality hardware."),
    # Faults. Always applied in full, and any one of them rules out Heirloom.
    "overlocked_only":  (-0.3, None, "Overlocked seams only — fast, and the first to fray."),
    "sparse_stitching": (-0.3, None, "Loose, widely spaced stitching."),
    "puckered_seams":   (-0.3, None, "Puckered seams — rushed sewing."),
    "loose_threads":    (-0.4, None, "Loose threads at the seams."),
    "glued_seams":      (-0.6, r"\b(?:glued|bonded)\s+seams?\b",
                         "Glued seams — they peel rather than wear."),
}
CONSTRUCTION_BONUS_CAP = 1.0
HEIRLOOM_MIN_PROOFS = 2

_WEIGHT_GSM = re.compile(r"\b(\d{2,3})\s*(?:gsm|g/m2|g/m\u00b2|grams?\s+per\s+square\s+met(?:er|re))\b", re.I)
_WEIGHT_OZ = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*-?\s*oz\b", re.I)


def _grade(raw, main_matched):
    """(adjustment, [lines]) from stated grades and fabric weight."""
    if not main_matched:
        return 0.0, []
    text = (raw or "").lower()
    top = max(main_matched, key=lambda m: m[1])
    top_key, top_pct = top[0], top[1]
    total = sum(m[1] for m in main_matched) or 100
    adj, lines = 0.0, []
    if top_pct / total >= 0.5:
        hits = [(a, line) for pat, fib, a, line in _GRADE_RULES
                if (fib == top_key or (fib == "wool" and top_key == "merino"))
                and re.search(pat, text)]
        if hits:
            # A fault wins over a flattering word next to it.
            a, line = min(hits) if any(h[0] < 0 for h in hits) else max(hits)
            adj += a
            lines.append(line)
    m = _WEIGHT_GSM.search(text)
    if m:
        gsm = int(m.group(1))
        if gsm >= 220:
            adj += 0.3; lines.append(f"{gsm}gsm — a substantial, hard-wearing weight.")
        elif gsm <= 140:
            adj -= 0.4; lines.append(f"{gsm}gsm — thin, and it will show wear sooner.")
    else:
        m = _WEIGHT_OZ.search(text)
        if m and "denim" in text:
            oz = float(m.group(1))
            if oz >= 13:
                adj += 0.3; lines.append(f"{oz:g}oz denim — heavyweight, built to break in.")
            elif oz <= 10:
                adj -= 0.2; lines.append(f"{oz:g}oz denim — lightweight, it wears through faster.")
    return adj, lines


def _construction(raw, tokens):
    """(proofs, faults) as lists of (token, adjust, line), from text and tokens."""
    found = {t for t in (tokens or []) if t in CONSTRUCTION}
    text = (raw or "").lower()
    for token, (_, pat, _) in CONSTRUCTION.items():
        if pat and re.search(pat, text):
            found.add(token)
    proofs, faults = [], []
    for token in sorted(found):
        a, _, line = CONSTRUCTION[token]
        (proofs if a > 0 else faults).append((token, a, line))
    return proofs, faults


def quality_detail(composition, allow_bare=True, construction=None):
    """The full read: score plus the evidence behind it and the ceiling it hit.

    Returns None when there is no fibre to score."""
    main_text, support_text = split_sections(composition)
    if support_text and parse_composition(main_text, allow_bare=allow_bare):
        base, matched = _score_pairs(parse_composition(main_text, allow_bare=allow_bare))
        lining = support_adjustment(support_text, allow_bare)
    else:
        pairs = parse_composition(composition, allow_bare=allow_bare)
        if not pairs:
            return None
        base, matched = _score_pairs(pairs)
        lining = 0.0

    grade_adj, grade_lines = _grade(composition, matched)
    proofs, faults = _construction(composition, construction)
    build_adj = min(sum(a for _, a, _ in proofs), CONSTRUCTION_BONUS_CAP) \
        + sum(a for _, a, _ in faults)

    raw = base + lining + grade_adj + build_adj
    has_grade = any(l for l in grade_lines) and grade_adj > 0
    if proofs and not faults and has_grade and len(proofs) >= HEIRLOOM_MIN_PROOFS:
        ceiling = 10.0
    elif proofs:
        ceiling = CAP_CONSTRUCTION
    elif has_grade:
        ceiling = CAP_GRADE
    else:
        ceiling = CAP_FIBRE_ONLY
    # Faults cost in full, after the cap (v27). Before, a fibre that scored
    # above its cap absorbed the fault and showed no change.
    fault_adj = sum(a for _, a, _ in faults)
    score = max(0.0, min(10.0, round(min(ceiling, raw - fault_adj) + fault_adj, 1)))
    return {
        "score": score,
        "matched": matched,
        "uncapped": max(0.0, min(10.0, round(raw, 1))),
        "ceiling": ceiling,
        "capped": round(raw, 1) > ceiling,
        "grade_lines": grade_lines,
        "construction": [t for t, _, _ in proofs + faults],
        "construction_lines": [l for _, _, l in proofs + faults],
    }


def quality_score(composition, allow_bare=True, construction=None):
    d = quality_detail(composition, allow_bare=allow_bare, construction=construction)
    if d is None:
        return None, []
    return d["score"], d["matched"]


def ceiling_note(detail):
    """What would move a capped score higher — said plainly, never as a sales line."""
    if not detail or not detail["capped"]:
        return None
    if detail["ceiling"] == CAP_FIBRE_ONLY:
        return ("The fabric is right, but a tag can't show how it's sewn. "
                "Add a photo of an inside seam to see if it goes higher.")
    if detail["ceiling"] == CAP_GRADE:
        return ("Top-grade material. Show us an inside seam to see if the "
                "making matches it.")
    return ("Close to Heirloom — that takes a stated grade, two signs of careful "
            "making and no faults.")


def _score_pairs(pairs):
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
    # v27: the old -0.8 "mostly plastic" penalty is gone. It had no source; the
    # comfort and pills parts now carry what it stood for, with sources.
    score = max(0.0, min(10.0, round(score, 1)))
    return score, matched


def verdict(score):
    if score >= 9:   return "Heirloom"
    if score >= 8:   return "The real thing"
    if score >= 6:   return "Solid"
    if score >= 4.5: return "It depends"
    return "Skip it"


# ======================================================================
# HOW LONG IT LASTS — sourced model (v26, 16 Oct 2026)
#
# Replaces wear_estimate()/expected_wears(), which were four fixed lines keyed
# off the score band: every "Solid" piece said "~30–50 washes" whatever it was
# made of. Full evidence write-up: claude/filo-evidence-audit.md (Project).
#
# 1. BASE LIFE per garment type, in wears, with wears per wash.
#    Source: Ecobalyse, the French government's textile environmental-cost
#    method (MTES-MCT/ecobalyse, public/data/textile/products.json, read 16 Oct
#    2026), legal basis Arrêté of 6 Sept 2025. These are regulatory reference
#    defaults, not lab measurements. Cross-checks: WRAP benchmark 56 washes for a
#    t-shirt, 30 for jeans (Guo et al. 2025); EU JRC proposes testing t-shirts
#    after 30 washes (Task 4, Nov 2024).
# 2. FIBRE ADJUSTMENT, from the one large study linking fibre to real lifespan:
#    Laitala & Klepp 2020, Sustainability 12:9151 — 53,461 garments in 5
#    countries. Versus cotton: silk +2.2 yr, wool +1.2 yr, regenerated cellulose
#    +9.7 months, synthetics +5.5 months. Converted to a ratio on the study's
#    ~5.2-year average garment life.
# 3. The total adjustment is kept inside Ecobalyse's legal durability band,
#    0.67–1.45 (Arrêté 6 Sept 2025, art. 6).
#
# Anything marked JUDGEMENT below has direction from evidence but no published
# magnitude. Keep that label until a source gives a number.
# ======================================================================

# group: (base wears, wears per wash). Ecobalyse product → Filo garment group.
LIFE_BASE = {
    "t-shirt":  (45, 1),    # T-shirt / Polo
    "top":      (45, 1),    # T-shirt / Polo
    "shirt":    (40, 2),    # Chemise
    "blouse":   (40, 2),    # Chemise
    "jeans":    (70, 3),    # Jean
    "trousers": (70, 3),    # Pantalon / Short
    "shorts":   (70, 3),    # Pantalon / Short
    "skirt":    (70, 3),    # Jupe / Robe
    "dress":    (70, 3),    # Jupe / Robe
    "sweater":  (85, 5),    # Pull
    "cardigan": (85, 5),    # Pull
    "hoodie":   (85, 5),    # Pull (closest Ecobalyse product)
    "jacket":   (100, 20),  # Manteau / Veste
    "blazer":   (100, 20),  # Manteau / Veste
    "coat":     (100, 20),  # Manteau / Veste
    # leggings: no Ecobalyse product. Laitala 2018 puts sportswear at ~1.5
    # wears per wash; base life borrowed from T-shirt. JUDGEMENT.
    "leggings": (45, 1),
}
# No garment type picked: total wears of the average garment in Laitala &
# Klepp 2020 (~35 worn so far + ~46 expected). Washes can't be given without a type.
LIFE_UNKNOWN_WEARS = 80

_AVG_LIFE_YEARS = 5.2   # Laitala & Klepp 2020, average garment lifespan estimate
LIFE_FIBRE = {
    # Laitala & Klepp 2020, extra years of life versus cotton.
    "silk": 1 + 2.2 / _AVG_LIFE_YEARS,
    "wool": 1 + 1.2 / _AVG_LIFE_YEARS,
    "merino": 1 + 1.2 / _AVG_LIFE_YEARS,
    "cashmere": 1 + 1.2 / _AVG_LIFE_YEARS,
    "lyocell": 1 + 0.81 / _AVG_LIFE_YEARS,
    "tencel": 1 + 0.81 / _AVG_LIFE_YEARS,
    "modal": 1 + 0.81 / _AVG_LIFE_YEARS,
    "cupro": 1 + 0.81 / _AVG_LIFE_YEARS,
    "polyester": 1 + 0.46 / _AVG_LIFE_YEARS,
    "nylon": 1 + 0.46 / _AVG_LIFE_YEARS,
    "polyamide": 1 + 0.46 / _AVG_LIFE_YEARS,
    "acrylic": 1 + 0.46 / _AVG_LIFE_YEARS,
    "elastane": 1 + 0.46 / _AVG_LIFE_YEARS,
    "spandex": 1 + 0.46 / _AVG_LIFE_YEARS,
    # Viscose sits in Laitala's regenerated group, but keeps only ~45–60% of
    # its strength wet against ~85% for lyocell (Zhang et al. 2018). Held at
    # cotton rather than given the group bonus. JUDGEMENT.
    "viscose": 1.0, "rayon": 1.0, "bamboo": 1.0, "acetate": 1.0,
    # Linen and hemp: no lifespan figure found. Held at cotton. GAP.
    "cotton": 1.0, "linen": 1.0, "hemp": 1.0,
}
LIFE_BAND = (0.67, 1.45)    # Ecobalyse legal durability coefficient range
# Fabric weight: heavier 100% cotton tees ranked better after 50 washes
# (Morris et al. 2025, Spearman 0.44); heavier knits pilled less
# (Mikučionienė 2009). Size of effect not published. JUDGEMENT.
LIFE_GSM_HEAVY, LIFE_GSM_THIN = 1.10, 0.90
# Long-staple cotton fibre is ~15–30% stronger (Tang et al. 2025); no
# garment-level test found. Kept small. JUDGEMENT.
LIFE_LONG_STAPLE = 1.05
# How wide the range is. Burst strength across 47 tees after 50 washes ran
# 156–598 N (Morris et al. 2025), so a tag alone can't narrow it much.
LIFE_SPREAD_TAG_ONLY = 0.30
LIFE_SPREAD_WEIGHT_KNOWN = 0.15

_SYNTH_LIFE = {"polyester", "nylon", "polyamide", "acrylic"}


def _round5(n):
    return max(5, int(5 * round(n / 5)))


def lifespan(composition, group=None):
    """How long it lasts, from what the tag and listing actually say.

    Returns None when there's no fibre to read, or for leather (leather fails by
    cracking, not washing — analyze() keeps its own line for that).
    """
    detail = quality_detail(composition)
    if detail is None:
        return None
    matched = detail["matched"]
    if material_class(matched):
        return None
    total = sum(p for _, p, _ in matched) or 100
    fibre = sum(LIFE_FIBRE.get(k, 1.0) * p for k, p, _ in matched) / total
    mult, notes = fibre, []
    text = (composition or "").lower()

    gsm_m = _WEIGHT_GSM.search(text)
    weight_known = bool(gsm_m)
    if gsm_m:
        g = int(gsm_m.group(1))
        if g >= 220:
            mult *= LIFE_GSM_HEAVY
        elif g <= 140:
            mult *= LIFE_GSM_THIN
    top_key = max(matched, key=lambda m: m[1])[0]
    if top_key == "cotton" and re.search(_GRADE_RULES[3][0], text):
        mult *= LIFE_LONG_STAPLE
    mult = max(LIFE_BAND[0], min(LIFE_BAND[1], mult))

    base, per_wash = LIFE_BASE.get(group, (None, None))
    wears = (base or LIFE_UNKNOWN_WEARS) * mult
    spread = LIFE_SPREAD_WEIGHT_KNOWN if weight_known else LIFE_SPREAD_TAG_ONLY
    out = {
        "wears": round(wears),
        "wears_range": [_round5(wears * (1 - spread)), _round5(wears * (1 + spread))],
        "washes": None, "washes_range": None,
        "unit": "wears",
        "multiplier": round(mult, 2),
        "weight_known": weight_known,
    }
    if per_wash:
        washes = wears / per_wash
        out["washes"] = round(washes)
        out["washes_range"] = [_round5(washes * (1 - spread)), _round5(washes * (1 + spread))]
        if per_wash <= 2:
            out["unit"] = "washes"

    # The headline, in the unit people think in for this garment.
    if out["unit"] == "washes":
        lo, hi = out["washes_range"]
        out["line"] = f"About {_round5(washes)} washes ({lo} to {hi})."
    elif out["washes"]:
        lo, hi = out["wears_range"]
        out["line"] = (f"About {_round5(wears)} wears ({lo} to {hi}), "
                       f"around {_round5(washes)} washes.")
    else:
        lo, hi = out["wears_range"]
        out["line"] = f"About {_round5(wears)} wears ({lo} to {hi}). Pick what it is to see washes."

    # The one-line "because".
    synth = sum(p for k, p, _ in matched if k in _SYNTH_LIFE)
    if fibre >= 1.15 and top_key in ("silk", "wool", "merino", "cashmere"):
        notes.append(f"{top_key.capitalize()} pieces are kept and worn longer than cotton "
                     "in large wardrobe studies, and need washing less often.")
    elif synth >= 50:
        notes.append("Synthetic fabric holds its shape through washing, but it pills "
                     "more than cotton and the pills stay on.")
    elif top_key in ("viscose", "rayon", "bamboo"):
        notes.append("Viscose loses about half its strength when wet, so washing is "
                     "where it wears out.")
    elif base:
        notes.append("This is the usual life for this kind of piece, from the EU and "
                     "French durability standards.")
    else:
        notes.append("This is the average life of a garment in a study of over "
                     "50,000 pieces.")
    if weight_known:
        notes.append("The fabric weight is stated, so the range is narrower.")
    else:
        notes.append("The tag doesn't say how heavy the fabric is, and that changes it "
                     "the most, so the range is wide.")
    out["because"] = " ".join(notes)
    return out


def expected_wears(composition, group=None):
    """Wears used for cost-per-wear. Same model as the shopper sees."""
    life = lifespan(composition, group)
    return life["wears"] if life else None


def cost_per_wear(price, wears):
    """price / wears. `wears` comes from lifespan(); both sides of any comparison
    must come from the same model."""
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
    if fibers & {"elastane", "spandex"}:
        # Chlorine is the stretch fibre's proven weak point (Bayer, US 5,969,008);
        # normal washing showed no clear loss within 20 to 25 washes.
        flags.append("Has stretch fibre. Keep it away from bleach and pool chlorine.")
    return flags


def reasons(score, matched, price):
    out = []
    synth = sum(p for k, p, _ in matched if k in SYNTHETICS)
    natural = sum(p for k, p, _ in matched if k in NATURALS)
    top_key, top_pct = sorted(matched, key=lambda x: x[1], reverse=True)[0][:2]
    material = material_class(matched)

    if material == "faux leather":
        if top_key == "bondedleather":
            out.append("Bonded leather — scraps glued to a backing. It peels "
                       "like faux leather, not ages like the real thing.")
        else:
            out.append("Faux leather — a plastic coating on fabric. It cracks "
                       "and peels, usually within a couple of years, and can't "
                       "be repaired.")
    elif material == "real leather":
        out.append("Real leather — it softens and ages instead of wearing out, "
                   "and a good one lasts decades with care.")
    elif synth >= 80:
        out.append(f"{synth}% synthetic. It holds its shape, but it takes up almost no "
                   "moisture, so it can feel hot, and when it pills the pills stay on.")
    elif top_pct >= 100 and top_key in NATURALS:
        out.append(f"100% {top_key}. Breathable and easy to care for. A heavier weight lasts longer.")
    elif natural >= 80:
        out.append(f"Mostly {top_key}. Breathable and easy to care for. A heavier weight lasts longer.")
    else:
        out.append(f"A {natural}% natural / {synth}% synthetic blend — a trade-off between feel and longevity.")

    if score >= 7:
        out.append("Well-made enough to keep for years.")
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


# ---------------------------------------------------------------- price vs make
# Rough price ranges, per garment group, for pieces made two ways: cheaply
# (mostly synthetic, score under 4.5) and well (score 7+). HAND-SET STARTING
# ESTIMATES in CAD/USD for mid-market retail — tune them, and replace them with
# aggregates.category_benchmark() once the scan data clears its k-anonymity floor.
PRICE_GUIDE = {
    #            cheaply made   well made
    "t-shirt":  ((10, 30),     (35, 80)),
    "top":      ((15, 40),     (45, 110)),
    "blouse":   ((20, 50),     (60, 160)),
    "shirt":    ((20, 50),     (60, 150)),
    "sweater":  ((25, 60),     (80, 220)),
    "cardigan": ((25, 60),     (80, 220)),
    "hoodie":   ((25, 55),     (70, 160)),
    "jeans":    ((30, 70),     (90, 220)),
    "trousers": ((25, 60),     (80, 200)),
    "shorts":   ((15, 35),     (40, 100)),
    "leggings": ((20, 50),     (50, 120)),
    "skirt":    ((20, 50),     (60, 160)),
    "dress":    ((30, 70),     (90, 250)),
    "jacket":   ((50, 120),    (150, 400)),
    "blazer":   ((50, 120),    (180, 450)),
    "coat":     ((70, 160),    (220, 600)),
    "leather":  ((60, 150),    (300, 800)),   # leather-look jackets and coats
}


def _describe_make(matched):
    material = material_class(matched)
    if material:
        return material
    top_key, top_pct = sorted(matched, key=lambda x: x[1], reverse=True)[0][:2]
    if top_key == "bondedleather":
        return "bonded leather"
    return f"{top_pct}% {top_key}"


def price_note(score, matched, price, group, material=None):
    """One plain sentence comparing the price to what pieces made like this
    usually cost. None when there's nothing useful to say."""
    if price is None or score is None or not matched:
        return None
    key = "leather" if material in ("real leather", "faux leather") else group
    guide = PRICE_GUIDE.get(key)
    if not guide:
        return None
    (cheap_lo, cheap_hi), (good_lo, good_hi) = guide
    make = _describe_make(matched)
    p = f"${price:,.0f}"
    # v27: "cheaply made" is the guide's mostly-synthetic band. The rebuilt score
    # puts 100% polyester at 4.5, so the band is matched on what it describes.
    if score < 4.5 or synthetic_pct(matched) >= 50:
        if price > cheap_hi * 1.25:
            return (f"{p} is a lot for {make}. Pieces made like this usually "
                    f"sell for ${cheap_lo}–{cheap_hi}.")
        return f"About the going rate for {make} — just don't expect it to last."
    if score >= 7:
        if price <= good_hi:
            return "A fair price for something made this well."
        if price > good_hi * 1.5:
            return (f"Well made, but priced above most pieces of this quality "
                    f"(usually ${good_lo}–{good_hi}). You may be paying for the name.")
    return None


def _with_evidence(out, detail):
    """Grade and construction lines go straight after the fibre line."""
    extra = (detail or {}).get("grade_lines", []) + (detail or {}).get("construction_lines", [])
    if not extra:
        return out
    out = list(out)
    return out[:1] + extra[:3] + out[1:]


def _with_lining(out, composition):
    note = lining_note(composition)
    if note:
        out = list(out)
        out.insert(1, note)
    return out


PRICE_PROMPT = "Add the price to see if it's worth it."


def analyze(item):
    """The one function the app calls (via /analyze). Returns the full verdict."""
    composition = item.get("composition", "")
    price = item.get("price")
    detail = quality_detail(composition, construction=item.get("construction"))
    score, matched = (detail["score"], detail["matched"]) if detail else (None, [])

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

    material = material_class(matched)
    durability = detail["uncapped"]
    group = item.get("group")
    life = lifespan(composition, group)
    wears = life["line"] if life else None
    flags = care_flags(matched)
    # Washes are the wrong unit for leather. It fails by cracking, not pilling.
    if material == "faux leather":
        wears = "Low — expect cracking and peeling within a couple of years of regular wear."
    elif material == "real leather":
        wears = "Built to last — decades of wear with basic care."
    if material:
        flags = ["Don't machine wash — spot clean, and keep it away from direct heat."] + flags

    return {
        "score": score,
        "material": material,
        "verdict": verdict(score),
        "reasons": _with_evidence(_with_lining(reasons(score, matched, price), composition),
                                  detail),
        "ceiling_note": ceiling_note(detail),
        # v27: the score taken apart, each part with its source (see fibre_parts).
        "score_parts": score_parts(matched),
        # How long it lasts, from the fibre and make alone — never capped by
        # evidence. Used for wears and cost-per-wear; the headline uses "score".
        "durability": durability,
        "evidence": {
            "ceiling": detail["ceiling"],
            "capped": detail["capped"],
            "grade": detail["grade_lines"],
            "construction": detail["construction"],
        },
        "wears": wears,
        # v26: the sourced life estimate behind "wears" (None for leather).
        "lifespan": life,
        "care_flags": flags,
        "value_note": value_note(durability, price),
        "alternatives": [],  # populated later via SerpAPI + look-matching
        "alternatives_note": "Better-made alternatives turn on once product search is connected.",
    }
