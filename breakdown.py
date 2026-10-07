"""
Filo breakdown: the verdict, taken apart in plain words (v21, 7 Oct 2026).

The verdict page used to show a few sentences. This returns the same read as a
list of rows the app can draw one by one, the way a food scanner lists what is
in the jar: each fibre, the lining, how it is sewn, and how long it should last.

Each row:
    side    "bad" | "good" | "note"    which list it goes in
    level   "poor" | "ok" | "good" | "unknown"   the colour of its dot
    icon    a short key the app maps to a line icon
    title   a few words                "62% Polyester"
    line    one short plain sentence
    detail  two or three sentences shown when the row is opened

House rules for the words: short, plain, no dashes, no jargon.
Nothing here changes a score. It only explains the one fabric.py already gave.
"""
import fabric

# fibre: (display name, icon, line, detail)
FIBRES = {
    "cotton": ("Cotton", "leaf", "Breathes well and gets softer with washing.",
               "Cotton is a natural fibre. It is strong, easy to wash and kind to skin. "
               "Thin cotton can lose its shape, so feel the weight of the fabric."),
    "linen": ("Linen", "leaf", "Strong, cool and gets better with age.",
              "Linen comes from the flax plant. It is one of the strongest natural fibres "
              "and it softens every time you wash it. It creases easily, which is normal."),
    "hemp": ("Hemp", "leaf", "Very strong and softens over time.",
             "Hemp is a tough natural fibre, a lot like linen. It holds up to years of washing."),
    "wool": ("Wool", "warm", "Warm, springs back into shape and lasts for years.",
             "Wool is a natural fibre that keeps you warm, resists smells and holds its shape. "
             "Wash it gently and it can last decades."),
    "merino": ("Merino wool", "warm", "Soft, warm wool that does not itch.",
               "Merino is a fine wool. It is soft on skin, warm for its weight and does not "
               "hold smells. Wash it gently."),
    "cashmere": ("Cashmere", "warm", "Very soft and warm. Needs gentle care.",
                 "Cashmere is a fine goat hair. Good cashmere lasts for years, but cheap "
                 "cashmere pills quickly. A tag cannot tell you which one it is."),
    "silk": ("Silk", "drop", "Smooth, strong and breathes well.",
             "Silk is a natural fibre with a soft shine. It is strong for how light it is, "
             "but it marks with water and needs gentle washing."),
    "leather": ("Real leather", "shield", "Softens with wear and can last decades.",
                "Real leather ages instead of wearing out. It can be cleaned, conditioned "
                "and repaired, which plastic leather cannot."),
    "lyocell": ("Lyocell", "drop", "Soft and smooth. Made from wood pulp.",
                "Lyocell (also sold as Tencel) is made from wood. It is soft, breathes well "
                "and is stronger than viscose, though not as hard wearing as cotton or linen."),
    "tencel": ("Tencel", "drop", "Soft and smooth. Made from wood pulp.",
               "Tencel is a brand of lyocell, made from wood. It is soft, breathes well and "
               "is stronger than viscose."),
    "modal": ("Modal", "drop", "Soft and stretchy. Fine for basics.",
              "Modal is made from wood pulp. It feels soft and keeps its colour, but it is "
              "thinner and less hard wearing than cotton."),
    "cupro": ("Cupro", "drop", "Silky feel. Wears out faster than silk.",
              "Cupro is made from cotton waste. It feels like silk and breathes well, but "
              "it is delicate and does not last as long."),
    "bamboo": ("Bamboo viscose", "drop", "Soft, but weak when wet.",
               "Fabric sold as bamboo is almost always viscose made from bamboo pulp. "
               "It feels soft but loses shape and wears thin with washing."),
    "viscose": ("Viscose", "drop", "Soft at first. Shrinks and loses shape.",
                "Viscose is made from wood pulp. It drapes nicely when new but it is weak "
                "when wet, so it can shrink, twist or wear thin after a few washes."),
    "rayon": ("Rayon", "drop", "Soft at first. Shrinks and loses shape.",
              "Rayon is another name for viscose. It drapes nicely when new but it is weak "
              "when wet, so it can shrink or wear thin after a few washes."),
    "acetate": ("Acetate", "drop", "Shiny but fragile. Marks easily.",
                "Acetate is a part plastic fibre used for shine. It tears and marks easily "
                "and often has to be dry cleaned."),
    "polyester": ("Polyester", "flask", "A plastic fibre. Traps heat and pills.",
                  "Polyester is plastic made from oil. It is cheap to make, does not breathe, "
                  "holds smells and forms little balls on the surface with wear."),
    "acrylic": ("Acrylic", "flask", "Plastic made to look like wool. Pills fast.",
                "Acrylic is a plastic copy of wool. It is not as warm, it pills quickly "
                "and it tends to look worn after one season."),
    "nylon": ("Nylon", "flask", "A strong plastic fibre. Does not breathe.",
              "Nylon is plastic made from oil. It is strong and dries fast, which suits "
              "jackets and sportswear, but it traps heat next to skin."),
    "polyamide": ("Polyamide", "flask", "A strong plastic fibre. Does not breathe.",
                  "Polyamide is another name for nylon. It is strong and dries fast, but "
                  "it traps heat next to skin."),
    "elastane": ("Elastane", "stretch", "Adds stretch. Wears out before the rest.",
                 "Elastane (also called spandex or Lycra) is what makes fabric stretch. "
                 "It breaks down with heat and time, which is why stretchy clothes go baggy."),
    "spandex": ("Spandex", "stretch", "Adds stretch. Wears out before the rest.",
                "Spandex is what makes fabric stretch. It breaks down with heat and time, "
                "which is why stretchy clothes go baggy."),
    "polyurethane": ("Faux leather", "flask", "Plastic on a fabric backing. Cracks and peels.",
                     "Faux leather is a plastic coating on cloth. It usually cracks and peels "
                     "within a couple of years and it cannot be repaired."),
    "pvc": ("PVC", "flask", "Stiff plastic. Cracks with wear.",
            "PVC is a hard plastic coating. It does not breathe at all and it cracks "
            "where the garment bends."),
    "bondedleather": ("Bonded leather", "flask", "Leather scraps glued together. Peels.",
                      "Bonded leather is leftover leather ground up and glued to a backing. "
                      "It peels like plastic leather and does not age like the real thing."),
}

# construction token: (title, line)
MAKE = {
    "french_seams": ("French seams", "Every raw edge is folded away inside. A careful finish."),
    "flat_felled": ("Flat felled seams", "Folded flat and sewn twice. The strongest seam there is."),
    "bound_seams": ("Bound seams", "Raw edges are wrapped so they cannot fray."),
    "hand_finished": ("Hand finished", "Parts of it were sewn by hand."),
    "dense_stitching": ("Tight stitching", "Small, close stitches. Seams like this do not open up."),
    "pattern_matched": ("Pattern lines up", "Stripes or checks meet at the seams. That takes care."),
    "quality_hardware": ("Good hardware", "Solid zips or real buttons. A sign of a careful maker."),
    "overlocked_only": ("Basic seams", "Edges are only looped over. Quick and cheap to sew."),
    "sparse_stitching": ("Loose stitching", "Long, wide stitches. Seams like this can pull open."),
    "puckered_seams": ("Puckered seams", "The fabric bunches along the seam. A sign of rushed sewing."),
    "loose_threads": ("Loose threads", "Threads were left untrimmed or are coming undone."),
    "glued_seams": ("Glued seams", "Edges are stuck with glue, not sewn. Glue lets go over time."),
}


def _level(q):
    return "good" if q >= 7 else "ok" if q >= 5.5 else "poor"


def _pct(p):
    return str(int(p)) if float(p) == int(p) else str(round(p, 1))


def _fibre_row(key, pct, q, where=""):
    name, icon, line, detail = FIBRES.get(
        key, (key.capitalize(), "leaf", "Filo does not have notes on this fibre yet.", ""))
    level = _level(q)
    # A little stretch is normal and not a mark against the piece.
    if key in ("elastane", "spandex") and pct <= 8:
        level, line = "ok", "A little stretch for comfort."
    side = "good" if level == "good" or (level == "ok" and q >= 6) else "bad"
    if key in ("elastane", "spandex") and pct <= 8:
        side = "good"
    title = f"{_pct(pct)}% {name}" + (f" {where}" if where else "")
    return {"side": side, "level": level, "icon": icon,
            "title": title, "line": line, "detail": detail}


def build(composition, construction=None, result=None):
    """Rows for the verdict page. [] when there is nothing to explain."""
    result = result or {}
    if result.get("score") is None:
        return []
    main_text, support_text = fabric.split_sections(composition or "")
    pairs = fabric.parse_composition(main_text) if support_text else []
    if not pairs:
        pairs, support_text = fabric.parse_composition(composition or ""), ""
    if not pairs:
        return []
    rows = []
    _, matched = fabric._score_pairs(pairs)
    for key, pct, q in sorted(matched, key=lambda m: -m[1]):
        rows.append(_fibre_row(key, pct, q))

    if support_text:
        lining = fabric.parse_composition(support_text)
        if lining:
            _, lm = fabric._score_pairs(lining)
            for key, pct, q in sorted(lm, key=lambda m: -m[1])[:2]:
                row = _fibre_row(key, pct, q, "lining")
                if row["side"] == "bad":
                    row["line"] = "The lining is plastic. It is often the first part to wear out."
                rows.append(row)

    tokens = ((result.get("evidence") or {}).get("construction")) or []
    for t in tokens:
        if t in MAKE and t in fabric.CONSTRUCTION:
            good = fabric.CONSTRUCTION[t][0] > 0
            title, line = MAKE[t]
            rows.append({"side": "good" if good else "bad",
                         "level": "good" if good else "poor",
                         "icon": "needle", "title": title, "line": line, "detail": ""})
    if not tokens:
        rows.append({"side": "note", "level": "unknown", "icon": "needle",
                     "title": "Stitching not checked",
                     "line": "A tag does not say how a piece is sewn.",
                     "detail": "Turn it inside out. Good signs are small, even stitches and "
                               "edges that are folded away or wrapped. Loose threads and "
                               "bunched seams are bad signs."})

    life = result.get("durability", result.get("score"))
    material = result.get("material")
    if material == "real leather":
        rows.append(_life("good", "good", "Decades with basic care."))
    elif material == "faux leather":
        rows.append(_life("bad", "poor", "Expect cracking within a couple of years."))
    elif life >= 8:
        rows.append(_life("good", "good", "Years of wear. 50 or more washes with care."))
    elif life >= 6:
        rows.append(_life("good", "ok", "Should hold up for about 30 to 50 washes."))
    elif life >= 4.5:
        rows.append(_life("bad", "ok", "About 15 to 30 washes before it shows wear."))
    else:
        rows.append(_life("bad", "poor", "Likely to pill or lose shape within 5 to 10 washes."))
    return rows


def _life(side, level, line):
    return {"side": side, "level": level, "icon": "clock", "title": "How long it lasts",
            "line": line,
            "detail": "This is an estimate from the fabric. How you wash and wear it "
                      "changes the real number."}
