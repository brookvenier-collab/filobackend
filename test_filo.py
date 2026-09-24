"""
Filo regression tests. Run with:  python3 test_filo.py

The integrity tests are the important ones. Filo's single promise is that a
"better-made option" is genuinely better made. If those tests ever go red,
something is recommending an item we cannot vouch for — treat that as broken,
not as a failing test to adjust.
"""
import fabric
import catalog

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {label:42} -> {got}")
    else:
        print(f"  PASS  {label:42} -> {got}")


def test_parser():
    print("\n=== parser: what real care labels actually look like ===")
    cases = [
        ("60% Cotton, 40% Polyester",            [("cotton", 60), ("polyester", 40)]),
        ("100% Polyester",                       [("polyester", 100)]),
        ("Cotton 60% Polyester 40%",             [("cotton", 60), ("polyester", 40)]),
        ("Cotton: 95%  Elastane: 5%",            [("cotton", 95), ("elastane", 5)]),
        ("80 cotton 20 polyester",               [("cotton", 80), ("polyester", 20)]),
        ("100 cotton",                           [("cotton", 100)]),
        ("cotton",                               [("cotton", 100)]),
        ("organic combed cotton",                [("cotton", 100)]),
        ("Classic Tee 100% Cotton",              [("cotton", 100)]),
        ("Top 55% Cotton 45% Polyester",         [("cotton", 55), ("polyester", 45)]),
        ("70% wool 20% nylon 10% cashmere",      [("wool", 70), ("nylon", 20), ("cashmere", 10)]),
    ]
    for text, want in cases:
        check(repr(text), fabric.parse_composition(text), want)

    # allow_bare=False is what stops us scoring someone else's listing on a guess.
    check("strict mode refuses a bare mention",
          fabric.parse_composition("Cotton Candy Dress", allow_bare=False), [])
    check("strict mode accepts explicit",
          fabric.parse_composition("Tee 100% Cotton", allow_bare=False), [("cotton", 100)])


def test_scoring():
    print("\n=== scoring: unchanged for known inputs ===")
    for comp, want in [
        ("60% Cotton, 40% Polyester", 5.4),
        ("100% Polyester", 2.2),
        ("100% Linen", 8.0),
        ("Top 55% Cotton 45% Polyester", 5.2),
    ]:
        check(comp, fabric.quality_score(comp)[0], want)


def test_integrity():
    """The one that matters. Shopper holds a 60/40 cotton-poly top at $80."""
    print("\n=== integrity: nothing worse or unverifiable may be recommended ===")
    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]

    cases = [
        ("worse — 100% poly",         {"title": "Silky Blouse 100% Polyester", "extracted_price": 75}, False),
        ("worse — 95/5 poly-elastane", {"title": "Stretch Top 95% Polyester 5% Elastane", "extracted_price": 70}, False),
        ("unverifiable — no fiber",   {"title": "Premium Luxe Blouse", "extracted_price": 80}, False),
        ("trap — 'Cotton Candy Dress'", {"title": "Cotton Candy Dress", "extracted_price": 78}, False),
        ("out of price band",         {"title": "Shirt 100% Linen", "extracted_price": 300}, False),
        ("barely better 55/45",       {"title": "Top 55% Cotton 45% Polyester", "extracted_price": 75}, False),
        ("good — 100% cotton",        {"title": "Classic Tee 100% Cotton", "extracted_price": 70}, True),
        ("good — 100% linen",         {"title": "Camp Shirt 100% Linen", "extracted_price": 95}, True),
        ("good — 70% wool",           {"title": "Sweater 70% Wool 30% Cotton", "extracted_price": 100}, True),
    ]
    for label, item, expect_kept in cases:
        got = catalog.evaluate(item, price=80, scanned_score=scanned) is not None
        check(label, got, expect_kept)

    print("\n=== ordering: strongest verified upgrade leads ===")
    catalog._fetch = lambda q, num=40: [c[1] for c in cases]
    catalog.SERPAPI_KEY = "test"
    results = catalog.search_alternatives("top cotton", price=80, scanned_score=scanned)
    for r in results:
        print(f"        {r['score']}  {r['name']}")
    check("sorted best-first", results == sorted(results, key=lambda r: -r["score"]), True)
    check("every result carries a verified score",
          all(r["score"] is not None for r in results), True)


def test_no_fast_fashion():
    """Scanning fast fashion must not return more fast fashion. This is the
    complaint that started it: 'I shouldn't see a garage shirt in fast fashion
    when I'm scanning a fast fashion shirt and want something better.'"""
    print("\n=== fast fashion in must not mean fast fashion out ===")
    import brands
    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]

    for label, item, expect_kept in [
        ("H&M 100% cotton tee $30",     {"title": "Basic Tee 100% Cotton", "extracted_price": 30, "source": "H&M"}, False),
        ("Zara 100% linen shirt $60",   {"title": "Linen Shirt 100% Linen", "extracted_price": 60, "source": "Zara"}, False),
        ("Amazon organic tee $40",      {"title": "Organic Tee 100% Cotton", "extracted_price": 40, "source": "Amazon.com"}, False),
        ("Nudie organic cotton $90",    {"title": "Roy Tee 100% Organic Cotton", "extracted_price": 90, "source": "Nudie Jeans"}, True),
        ("small maker linen $95",       {"title": "Camp Shirt 100% Linen", "extracted_price": 95, "source": "SmallMaker"}, True),
    ]:
        got = catalog.evaluate(item, price=78, scanned_score=scanned) is not None
        check(label, got, expect_kept)

    print("\n  queries actually name good makers:")
    for q in brands.build_queries("women's jeans"):
        print(f"        {q}")
    check("brand-led query present",
          any("nudie" in q or "armedangels" in q for q in brands.build_queries("women's jeans")), True)


def test_price_earns_itself():
    """A piece may cost more than what's in their hands only if it is cheaper
    per wear. The ceiling stops absurdity regardless."""
    print("\n=== price has to earn itself in cost-per-wear ===")
    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]   # 5.4 → 25 wears

    for label, item, expect_kept in [
        ("linen $150 — 1.9x but $0.75/wear",  {"title": "Shirt 100% Linen", "extracted_price": 150, "source": "M"}, True),
        ("cotton $140 — 1.8x, $1.17/wear",    {"title": "Tee 100% Cotton", "extracted_price": 140, "source": "M"}, True),
        ("cotton $200 — over the 2.5x ceiling", {"title": "Tee 100% Cotton", "extracted_price": 200, "source": "M"}, False),
        ("suspiciously cheap $20",            {"title": "Tee 100% Cotton", "extracted_price": 20, "source": "M"}, False),
    ]:
        got = catalog.evaluate(item, price=78, scanned_score=scanned) is not None
        check(label, got, expect_kept)

    check("$78 at score 5.4 → 25 wears", fabric.expected_wears(5.4), 25)
    check("$78 / 25 wears = $3.12", fabric.cost_per_wear(78, 5.4), 3.12)
    check("$150 at score 8.0 → 200 wears", fabric.expected_wears(8.0), 200)
    check("$150 / 200 wears = $0.75", fabric.cost_per_wear(150, 8.0), 0.75)


def test_season_calendar():
    """Season names are computed, never guessed. Check the boundaries and the rollover."""
    import style
    from datetime import date
    print("\n=== season calendar ===")
    cases = [
        (date(2026, 6, 30), "Spring/Summer 26", "Fall/Winter 26"),
        (date(2026, 7, 1),  "Fall/Winter 26",   "Spring/Summer 27"),
        (date(2026, 8, 31), "Fall/Winter 26",   "Spring/Summer 27"),
        (date(2026, 12, 31), "Fall/Winter 26",  "Spring/Summer 27"),
        (date(2027, 1, 1),  "Spring/Summer 27", "Fall/Winter 27"),
        (date(2029, 11, 5), "Fall/Winter 29",   "Spring/Summer 30"),
    ]
    for d, cur, nxt in cases:
        c = style.season_cycle(d)
        check(f"{d} -> {cur}", c["current"] == cur and c["next"] == nxt, True)

    # Wear left must fall as the season runs out, never go negative.
    for d in (date(2026, 7, 1), date(2026, 10, 1), date(2026, 12, 1)):
        check(f"{d} months left positive", style.season_cycle(d)["months_left"] > 0, True)

    # No key means no style read, and no exception.
    check("no API key returns None", style.style_read({"category": "coat"}) is None, True)
def test_look_never_overrides_quality():
    """The photo read may reorder results. It may never let a worse one in.

    This is the guarantee that matters: aesthetics are an assist, fabric quality
    is the promise. A perfect visual match that fails the quality gate must still
    be dropped, and a poor visual match that passes must still be shown.
    """
    import vision
    print("\n=== shape ranks, quality still gates ===")
    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]
    look = ["cropped", "crewneck", "chunky-knit", "sage"]

    # Dead-on visually, but 100% polyester. Must not survive.
    perfect_but_poly = {"title": "Cropped Chunky Knit Crewneck Sage 100% Polyester",
                        "extracted_price": 80, "source": "M"}
    check("perfect match, poly -> dropped",
          catalog.evaluate(perfect_but_poly, price=80, scanned_score=scanned) is None, True)

    # Visually unrelated, but genuinely better made. Must survive.
    mismatch_but_good = {"title": "Oversized V-Neck Cardigan 100% Linen",
                         "extracted_price": 90, "source": "M"}
    check("no match, well made -> kept",
          catalog.evaluate(mismatch_but_good, price=80, scanned_score=scanned) is not None, True)

    # Ordering among things that ALREADY passed: closest shape leads.
    items = [mismatch_but_good,
             {"title": "Cropped Chunky Knit Crewneck Sweater 100% Cotton",
              "extracted_price": 85, "source": "M"}]
    catalog._fetch = lambda q, num=40: items
    catalog.SERPAPI_KEY = "test"
    results = catalog.search_alternatives("sweater", price=80,
                                          scanned_score=scanned, look=look)
    for r in results:
        print(f"        match={r['match']:.2f}  score={r['score']}  {r['name']}")
    check("closest shape leads", "Cropped" in (results[0]["name"] or ""), True)
    check("the mismatch is still offered", len(results), 2)

    # No photo at all -> unchanged behaviour, and no crash.
    plain = catalog.search_alternatives("sweater", price=80, scanned_score=scanned)
    check("no look -> still returns", len(plain), 2)
    check("no look -> match is 0", all(r["match"] == 0.0 for r in plain), True)

    # Vocabulary guard.
    check("off-vocabulary dropped",
          vision.sanitize({"silhouette": "vibey", "color": "sage"}), {"color": "sage"})
    check("no image -> None", vision.describe(None) is None, True)
def test_mall_brands_cannot_flood():
    """Mall chains list tens of thousands of SKUs and were taking every slot on
    feed size alone. A shopper scanning a mall sweater got four more mall
    sweaters — technically better made, and useless as a recommendation."""
    import brands
    print("\n=== the mall tier ===")

    for name, want in [("Abercrombie & Fitch", brands.TIER_MAINSTREAM),
                       ("Gap", brands.TIER_MAINSTREAM),
                       ("Aritzia", brands.TIER_MAINSTREAM),
                       ("Lululemon", brands.TIER_MAINSTREAM),
                       ("Zara", brands.TIER_BLOCKED),
                       ("Amazon.com", brands.TIER_BLOCKED),
                       ("ARMEDANGELS", brands.TIER_MAKER),
                       ("SomeTinyLabel", brands.TIER_UNKNOWN)]:
        check(f"tier of {name}", brands.tier(name), want)

    check("an unknown label outranks a mall chain",
          brands.TIER_UNKNOWN > brands.TIER_MAINSTREAM, True)

    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]
    mall = [
        {"title": "Cotton Crew 100% Cotton", "extracted_price": 90,
         "source": "Abercrombie & Fitch", "link": "a"},
        {"title": "Knit Sweater 100% Cotton", "extracted_price": 85,
         "source": "Gap", "link": "b"},
        {"title": "Wool Sweater 100% Merino Wool", "extracted_price": 110,
         "source": "Banana Republic", "link": "c"},
        {"title": "Cotton Sweater 100% Cotton", "extracted_price": 95,
         "source": "Aritzia", "link": "d"},
    ]
    catalog._fetch = lambda q, num=40: mall
    catalog.SERPAPI_KEY = "test"
    only_mall = catalog.search_alternatives("sweater", price=80, scanned_score=scanned)
    check("four mall brands in -> one out", len(only_mall), 1)

    # A mall brand that genuinely passes is still allowed. Not a ban.
    check("the one kept is a real result", only_mall[0]["score"] >= 7.0, True)

    indie = mall + [
        {"title": "Organic Cotton Sweater 100% Organic Cotton",
         "extracted_price": 100, "source": "Kotn", "link": "e"},
        {"title": "Merino Crew 100% Merino Wool",
         "extracted_price": 120, "source": "ARMEDANGELS", "link": "f"},
    ]
    catalog._fetch = lambda q, num=40: indie
    mixed = catalog.search_alternatives("sweater", price=80, scanned_score=scanned)
    tiers = [r["tier"] for r in mixed]
    check("independents lead the list", tiers[0], brands.TIER_MAKER)
    check("at most one mall brand survives",
          sum(1 for t in tiers if t == brands.TIER_MAINSTREAM) <= catalog.MAX_MAINSTREAM, True)

    # Equal fabric, equal price: the independent wins.
    catalog._fetch = lambda q, num=40: [
        {"title": "Cotton Sweater 100% Cotton", "extracted_price": 90,
         "source": "Abercrombie & Fitch", "link": "a"},
        {"title": "Cotton Sweater 100% Cotton", "extracted_price": 92,
         "source": "Asket", "link": "g"},
    ]
    tie = catalog.search_alternatives("sweater", price=80, scanned_score=scanned)
    check("at equal quality the independent leads", tie[0]["brand"], "Asket")
def test_affiliate_cannot_bend_the_ranking():
    """Wrapping runs last, on a frozen list. It may change where a link points;
    it may never change which links are shown or in what order."""
    import affiliate
    print("\n=== affiliate wrapping ===")

    alts = [
        {"name": "A", "url": "https://maker.example/a", "score": 8.0},
        {"name": "B", "url": "https://maker.example/b", "score": 7.5},
        {"name": "C", "url": "https://maker.example/c", "score": 7.2},
    ]
    before_order = [a["name"] for a in alts]

    # Off by default — nothing configured, nothing changed.
    affiliate.NETWORK = ""
    out = affiliate.decorate([dict(a) for a in alts], scanned_score=5.4, category="sweater")
    check("disabled -> urls untouched",
          [a["url"] for a in out], [a["url"] for a in alts])
    check("disabled -> affiliate flag false", all(not a["affiliate"] for a in out), True)

    # Turned on.
    affiliate.NETWORK = "skimlinks"
    affiliate.SKIMLINKS_ID = "12345"
    out = affiliate.decorate([dict(a) for a in alts], scanned_score=5.4, category="sweater")
    check("enabled -> every link wrapped", all(a["affiliate"] for a in out), True)
    check("wrapped link points at the network",
          out[0]["url"].startswith("https://go.skimresources.com/"), True)
    check("original destination survives inside",
          "maker.example" in out[0]["url"], True)

    # The guarantee.
    check("order unchanged", [a["name"] for a in out], before_order)
    check("nothing added or dropped", len(out), len(alts))

    # SubID carries the verdict, and nothing else.
    tag = affiliate.subid(scanned_score=5.4, alt_score=8.0, category="sweater")
    check("subid encodes both scores", tag, "s54-a80-sweater")
    check("subid is punctuation-free apart from dashes",
          all(c.isalnum() or c == "-" for c in tag), True)

    # A missing URL must not explode.
    odd = affiliate.decorate([{"name": "D", "url": None, "score": 7.0}])
    check("missing url handled", odd[0]["affiliate"], False)

    affiliate.NETWORK = ""      # leave the module as we found it


def test_leather_and_garment_type():
    """Brooklyn's scans, 24 Sep 2026: a faux leather jacket scanned as "jacket"
    was offered a sweater, and a coat was offered the same Hobbs coat four times
    in four sizes."""
    import brands
    print("\n=== leather, real and fake ===")
    for tag, want in [("100% Polyurethane", "faux leather"),
                      ("Shell: 100% PU", "faux leather"),
                      ("100% vegan leather", "faux leather"),
                      ("Faux leather", "faux leather"),
                      ("100% bonded leather", "faux leather"),
                      ("100% genuine leather", "real leather"),
                      ("100% Lambskin", "real leather"),
                      ("Suede", "real leather"),
                      ("100% Cotton", None),
                      ("80% wool 20% leather", None)]:
        check(f"material of {tag!r}", fabric.material_class(fabric.quality_score(tag)[1]), want)

    fake = fabric.quality_score("100% vegan leather")[0]
    real = fabric.quality_score("100% lambskin leather")[0]
    check("faux leather never scores like leather", fake < 4.5, True)
    check("real leather scores as leather", real, 8.0)
    check("faux suede is polyester, not leather",
          fabric.parse_composition("100% faux suede"), [("polyester", 100)])

    faux_scan = fabric.analyze({"composition": "100% polyurethane", "price": 120})
    check("faux verdict says faux", "Faux leather" in faux_scan["reasons"][0], True)
    check("no 'washes' for leather", "wash" in faux_scan["wears"], False)
    check("100% cotton is not 'mostly'",
          fabric.analyze({"composition": "100% Cotton"})["reasons"][0].startswith("100% cotton"), True)

    print("\n=== a jacket is answered with a jacket ===")
    for title, want in [("Organic Cotton Chore Jacket 100% Cotton", True),
                        ("Merino Wool Sweater 100% Merino Wool", False),
                        ("Wool Knit Sweater Jacket 100% Wool", False),
                        ("Leather Moto 100% Lambskin", True),
                        ("Wool Blazer 100% Wool", False),
                        ("Puffer Vest 100% Cotton", False)]:
        check(f"jacket <- {title[:34]}", catalog.same_garment(title, "jacket"), want)
    for title, cat, want in [("Classic Crew Tee 100% Cotton", "t-shirt", True),
                             ("Linen Guayabera Shirt 100% Linen", "t-shirt", False),
                             ("Wool Raincoat 100% Wool", "coat", True),
                             ("Oxford Shirt 100% Cotton", "shirt", True),
                             ("Heavy Cotton T-Shirt 100% Cotton", "shirt", False)]:
        check(f"{cat} <- {title[:34]}", catalog.same_garment(title, cat), want)

    check("no fallback makers for jackets", brands.makers_for("jacket"), [])
    q = brands.build_queries("jacket", material="faux leather")
    check("faux leather searches real leather", all("leather jacket" in x for x in q), True)

    scan = fabric.analyze({"composition": "100% polyurethane", "category": "jacket", "price": 150})
    results = [
        {"title": "Merino Wool Sweater 100% Merino Wool", "extracted_price": 140, "source": "Knitco", "link": "1"},
        {"title": "Wool Bomber Jacket 100% Wool", "extracted_price": 180, "source": "Woolco", "link": "2"},
        {"title": "Vegan Leather Moto Jacket 100% Polyurethane", "extracted_price": 120, "source": "Fakeco", "link": "3"},
        {"title": "Leather Biker Jacket 100% Lambskin", "extracted_price": 320, "source": "Hideco", "link": "4"},
    ]
    catalog._fetch = lambda q, num=40: results
    catalog.SERPAPI_KEY = "test"
    alts = catalog.search_alternatives("jacket", price=150, scanned_score=scan["score"],
                                       material=scan["material"])
    check("faux leather jacket -> only real leather jackets",
          [a["brand"] for a in alts], ["Hideco"])

    print("\n=== one product is one option, not one per size ===")
    hobbs = [{"title": f"Hobbs Livia Wool Coat Beryl Red Size {n}", "extracted_price": 570,
              "source": "Hobbs", "link": f"h{n}"} for n in (18, 6, 2, 4)]
    hobbs.insert(1, {"title": "Hobbs Petite Livia Wool Coat Beryl Red Size 6",
                     "extracted_price": 570, "source": "Hobbs", "link": "hp"})
    others = [{"title": f"{w} Wool Overcoat 100% Wool", "extracted_price": 500,
               "source": "Coatmaker", "link": f"c{w}"} for w in ("Camel", "Navy", "Grey")]
    for h in hobbs:
        h["title"] += " 100% Wool"
    catalog._fetch = lambda q, num=40: hobbs + others
    alts = catalog.search_alternatives("coat", price=400, scanned_score=7.0)
    names = [a["name"] for a in alts]
    for n in names:
        print("       ", n)
    check("sizes collapse to one Hobbs coat", sum(1 for a in alts if a["brand"] == "Hobbs"), 1)
    check("no brand takes more than two slots",
          max(sum(1 for a in alts if a["brand"] == b) for b in {a["brand"] for a in alts})
          <= catalog.MAX_PER_BRAND, True)


def test_no_price_still_has_a_range():
    """A coat scanned with no price came back with $570-$760 coats."""
    print("\n=== no price entered -> still a sensible range ===")
    coats = [{"title": "Wool Coat 100% Wool", "extracted_price": 280, "source": "A", "link": "a"},
             {"title": "Cashmere Overcoat 100% Cashmere", "extracted_price": 760, "source": "B", "link": "b"}]
    catalog._fetch = lambda q, num=40: coats
    catalog.SERPAPI_KEY = "test"
    alts = catalog.search_alternatives("coat", price=None, scanned_score=7.0)
    check("no price: $760 coat dropped, $280 kept", [a["brand"] for a in alts], ["A"])
    check("no price: no made-up cost-per-wear line", alts[0]["value_note"], None)
    check("leather gets a leather-sized range",
          catalog.typical_price("jacket", "faux leather"), catalog.TYPICAL_LEATHER_PRICE)
    check("with a price, the real band still rules",
          [a["brand"] for a in catalog.search_alternatives("coat", price=600, scanned_score=7.0)], ["B"])


if __name__ == "__main__":
    test_parser()
    test_scoring()
    test_integrity()
    test_no_fast_fashion()
    test_price_earns_itself()
    test_season_calendar()
    test_look_never_overrides_quality()
    test_mall_brands_cannot_flood()
    test_affiliate_cannot_bend_the_ranking()
    test_leather_and_garment_type()
    test_no_price_still_has_a_range()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  -", f)
        raise SystemExit(1)
    print("ALL PASS")
