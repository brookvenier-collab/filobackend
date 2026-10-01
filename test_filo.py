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
        ("100% Linen", 7.9),   # fibre-only tags top out at 7.9 (v13)
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
    check("real leather scores as leather (tag-only cap)", real, 7.9)
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


def test_v11_department_price_fast_accounts():
    print("\n=== v11: department ===")
    for t, want in [("Women's Wool Coat", "women"), ("Mens Leather Jacket", "men"),
                    ("Men's Genuine Leather Biker Jacket", "men"), ("Womens Trench", "women"),
                    ("Wool Overcoat", None), ("Unisex Chore Jacket", None)]:
        check(f"department of {t!r}", catalog.department_of(t), want)
    check("reads department off 1.0 category string",
          catalog.normalize_department(None, "women's black jacket"), "women")
    check("explicit field wins", catalog.normalize_department("Men's", "women's jacket"), "men")
    check("Everyone = no filter", catalog.normalize_department("Everyone", "jacket"), None)

    listings = [
        {"title": "Men's Genuine Leather Biker Jacket 100% Lambskin", "extracted_price": 300, "source": "A", "link": "1"},
        {"title": "Women's Lambskin Moto Jacket 100% Lambskin", "extracted_price": 320, "source": "B", "link": "2"},
        {"title": "Leather Trucker Jacket 100% Cowhide", "extracted_price": 280, "source": "C", "link": "3"},
    ]
    catalog._fetch = lambda q, num=40: listings
    catalog.SERPAPI_KEY = "test"
    alts = catalog.search_alternatives("jacket", price=150, scanned_score=1.7,
                                       material="faux leather", department="women")
    check("women's scan: no menswear", sorted(a["brand"] for a in alts), ["B", "C"])

    print("\n=== v11: price vs make ===")
    poly = fabric.quality_score("100% Polyester")
    note = fabric.price_note(poly[0], poly[1], 75, "t-shirt")
    check("$75 poly tee called out", note.startswith("$75 is a lot for 100% polyester"), True)
    check("$20 poly tee: going rate",
          fabric.price_note(poly[0], poly[1], 20, "t-shirt").startswith("About the going rate"), True)
    wool = fabric.quality_score("100% Wool")
    check("well-made coat, fair price",
          fabric.price_note(wool[0], wool[1], 300, "coat"), "A fair price for something made this well.")
    check("no price -> no note", fabric.price_note(poly[0], poly[1], None, "t-shirt"), None)
    faux = fabric.quality_score("100% polyurethane")
    check("faux leather uses leather guide",
          "faux leather" in fabric.price_note(faux[0], faux[1], 250, "jacket", "faux leather"), True)

    print("\n=== v11: fast score mode + prompts ===")
    import main
    fast = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% Polyester", mode="score")))
    check("fast mode returns a score", fast["score"], 2.2)
    check("fast mode is pending", fast["pending"], True)
    check("no price -> prompt", fast["price_prompt"], fabric.PRICE_PROMPT)
    check("no price -> no value verdict", fast["value_note"], None)
    priced = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% Polyester",
                                                             price=75, category="t-shirt", mode="score")))
    check("price -> price note", (priced["price_note"] or "").startswith("$75"), True)
    check("price -> no prompt", priced["price_prompt"], None)
    # v14 added season fields; the two 1.1 keys must stay so older builds keep working.
    check("/config keeps 1.1 keys", {"home_image_alt", "home_image_url"} <= set(main.home_config()), True)

    print("\n=== v11: account sessions ===")
    import accounts, uuid
    aid = str(uuid.uuid4())
    tok = accounts.issue_session(aid)
    check("session round-trips", accounts.check_session("Bearer " + tok), aid)
    check("tampered session rejected", accounts.check_session(tok[:-2] + "xx"), None)
    check("garbage rejected", accounts.check_session("nope"), None)
    try:
        accounts.verify_apple_token("x" * 40)
        check("bad apple token rejected", False, True)
    except ValueError:
        check("bad apple token rejected", True, True)


def test_v12_closet_fund():
    """Closet Fund. The pure rules always run; the database walk-through runs
    when FILO_TEST_DATABASE_URL points at a scratch Postgres."""
    import os, uuid, ast, pathlib
    import affiliate, fund, events
    print("\n=== v12 closet fund ===")

    # The fund can never reach the ranking: neither module imports the other.
    def imports(path):
        tree = ast.parse(pathlib.Path(path).read_text())
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module)
        return names
    check("catalog.py does not import fund", "fund" in imports("catalog.py"), False)
    check("fund.py does not import catalog", "catalog" in imports("fund.py"), False)
    check("fabric.py does not import fund", "fund" in imports("fabric.py"), False)

    # Resale sites are never alternatives.
    import brands
    check("depop blocked", brands.is_blocked("Depop") if hasattr(brands, "is_blocked")
          else any("depop" == b for b in brands.UNVERIFIABLE_SOURCES), True)

    # Rates and tiers.
    check("verdict rate", fund.rate_for("v", "bronze"), 3)
    check("closet rate", fund.rate_for("c", "silver"), 5)
    check("closet rate at gold", fund.rate_for("c", "gold"), 6)
    base = {"scans": 0, "prices": 0, "saves": 0, "shared": False}
    check("new member is bronze", fund.tier_for(base, 0), "bronze")
    check("bronze shows steps left", fund.next_step(base, 0, "bronze")["label"], "4 steps until Silver")
    done = {"scans": 1, "prices": 1, "saves": 3, "shared": True}
    check("checklist done -> silver", fund.tier_for(done, 0), "silver")
    check("25 scans, 2 purchases -> still silver", fund.tier_for(dict(done, scans=25), 2), "silver")
    check("25 scans + 3 purchases -> gold", fund.tier_for(dict(done, scans=25), 3), "gold")
    check("scans alone never reach gold", fund.tier_for(dict(done, scans=999), 0), "silver")

    # Links.
    check("click ref is alphanumeric", fund.new_click_ref("c").isalnum(), True)
    check("click ref marks closet", fund.new_click_ref("c")[:2], "fc")
    wrapped = "https://redirect.viglink.com/?key=k&u=https%3A%2F%2Fshop.example%2Fa&cuid=x"
    check("unwraps our own redirect", fund.original_url(wrapped), "https://shop.example/a")
    check("rejects non-web links", fund.original_url("javascript:alert(1)"), None)
    affiliate.NETWORK, affiliate.SOVRN_KEY = "sovrn", "KEY"
    link = affiliate.wrap("https://shop.example/a", "s54-a80-sweater")
    check("sovrn uses cuid", "&cuid=s54a80sweater" in link, True)
    check("fund cuid wins", "&cuid=fvABC" in affiliate.wrap("https://shop.example/a", "t", cuid="fvABC"), True)

    # Sovrn statuses.
    check("approved -> available", fund._status_from({"status": "APPROVED"}), "available")
    check("negative revenue -> reversed", fund._status_from({"publisherNetRevenue": -2}), "reversed")
    check("unknown -> pending", fund._status_from({}), "pending")

    url = os.environ.get("FILO_TEST_DATABASE_URL")
    if not url:
        print("  (skipping database walk-through: FILO_TEST_DATABASE_URL not set)")
        affiliate.NETWORK = ""
        return
    events.DATABASE_URL = url
    check("schema created", fund.init_schema(), True)

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    s = fund.summary_for(a)
    check("welcome points", s["points_available"], 100)
    check("starts bronze", s["tier"], "bronze")
    check("sign-in already ticked", s["checklist_done"], 1)
    check("welcome only once", fund.summary_for(a)["points_available"], 100)

    # Invite: b enters a's code, scans on 3 different days -> a gets 200, b nothing.
    fund.enter_referral(b, s["invite_code"])
    try:
        fund.enter_referral(a, s["invite_code"])
        check("own code rejected", False, True)
    except ValueError:
        check("own code rejected", True, True)
    for back in (2, 1):
        fund.record_progress(b, fund.ProgressEvent(event="scan"))
        with fund._Tx() as cur:     # pretend the scan was on an earlier day
            cur.execute("UPDATE fund_progress SET last_scan_day=CURRENT_DATE-%s WHERE account_id=%s",
                        (back, b))
    check("not yet after 2 days", fund.summary_for(a)["points_available"], 100)
    fund.record_progress(b, fund.ProgressEvent(event="scan"))
    check("referrer paid after 3 days", fund.summary_for(a)["points_available"], 300)
    check("invited friend gets only welcome", fund.summary_for(b)["points_available"], 100)
    fund.record_progress(b, fund.ProgressEvent(event="scan"))
    check("referral paid once", fund.summary_for(a)["points_available"], 300)

    # Checklist -> Wool.
    for ev in ("scan", "price", "share"):
        fund.record_progress(a, fund.ProgressEvent(event=ev))
    fund.record_progress(a, fund.ProgressEvent(event="save", count=3))
    check("a reaches silver", fund.summary_for(a)["tier"], "silver")

    # Tap -> purchase. Purchase points off: order shows, 0 points.
    os.environ.pop("FUND_PURCHASE_POINTS", None)
    r = fund.make_link(a, fund.LinkRequest(url=wrapped, source="c", title="Wool coat",
                                           store="Maker", price=300))
    check("tap is tracked", r["tracked"], True)
    ref = r["url"].split("cuid=")[1]
    check("cuid is our ref", ref.startswith("fc"), True)
    check("account id never sent to sovrn", a.replace("-", "") in r["url"] or a in r["url"], False)
    check("off: purchase recorded", fund.record_purchase(ref, "C1", 300.0, "pending"), "added")
    o = fund.orders_for(a)["orders"]
    check("order appears", o[0]["title"], "Wool coat")
    check("off: 0 points", o[0]["points"], 0)

    # Points on: 5 per $1 from the Closet.
    os.environ["FUND_PURCHASE_POINTS"] = "on"
    r = fund.make_link(a, fund.LinkRequest(url="https://shop.example/b", source="c", title="Sweater"))
    ref2 = r["url"].split("cuid=")[1]
    fund.record_purchase(ref2, "C2", 120.0, "pending")
    s = fund.summary_for(a)
    check("pending 600", s["points_pending"], 600)
    check("available unchanged while pending", s["points_available"], 300)
    fund.record_purchase(ref2, "C2", 120.0, "available")
    check("approved -> available", fund.summary_for(a)["points_available"], 900)
    fund.record_purchase(ref2, "C2", 120.0, "reversed")
    check("return reverses", fund.summary_for(a)["points_available"], 300)
    check("reversed stays reversed", fund.record_purchase(ref2, "C2", 120.0, "available"), "unchanged")
    check("unknown cuid ignored", fund.record_purchase("fvNOPE", "C3", 50.0, "pending"), "no-click")
    check("verdict-tag cuid ignored", fund.record_purchase("s54a80sweater", "C4", 50.0, "pending"), "not-ours")

    # Redeem.
    try:
        fund.redeem(a, 1000, None)
        check("can't redeem without points", False, True)
    except ValueError:
        check("can't redeem without points", True, True)
    r = fund.make_link(a, fund.LinkRequest(url="https://shop.example/c", source="v"))
    fund.record_purchase(r["url"].split("cuid=")[1], "C5", 250.0, "available")  # 750 pts
    check("balance 1050", fund.summary_for(a)["points_available"], 1050)
    rid = fund.redeem(a, 1000, "x@privaterelay.appleid.com")["redemption_id"]
    check("redeemed", fund.summary_for(a)["points_available"], 50)
    check("admin sees request", len(fund.list_redemptions()), 1)
    fund.resolve_redemption(rid, "cancelled")
    check("cancel refunds", fund.summary_for(a)["points_available"], 1050)

    # Card finish.
    check("silver can pick bronze", fund.set_card(a, "bronze")["card_finish"], "bronze")
    try:
        fund.set_card(a, "gold")
        check("locked finish refused", False, True)
    except ValueError:
        check("locked finish refused", True, True)

    # Delete account: everything goes.
    fund.delete_account(a)
    with fund._Tx() as cur:
        cur.execute("SELECT COUNT(*) FROM fund_ledger WHERE account_id=%s", (a,))
        check("ledger deleted", cur.fetchone()[0], 0)
        cur.execute("SELECT referred_by FROM fund_progress WHERE account_id=%s", (b,))
        check("invite link cleared", cur.fetchone()[0], None)
    check("admin summary works", "members" in fund.admin_summary(), True)
    os.environ.pop("FUND_PURCHASE_POINTS", None)
    affiliate.NETWORK = ""


def test_v13_evidence_and_rarity():
    import fund, seams
    print("\n=== v13: a score only goes as high as its evidence ===")
    a = lambda comp, c=None: fabric.analyze({"composition": comp, "construction": c})
    check("tag-only 100% leather caps at 7.9", a("100% Leather")["score"], 7.9)
    check("tag-only is 'Solid', not 'The real thing'", a("100% Leather")["verdict"], "Solid")
    check("capped scan explains what's missing", a("100% Linen")["ceiling_note"] is not None, True)
    check("poly-lined leather", a("100% Leather / Lining: 100% Polyester")["score"], 7.5)
    check("stated grade lifts the cap to 8.4", a("Full-grain leather. Lining: 100% cupro")["score"], 8.4)
    check("genuine leather is marked down", a("Genuine leather")["score"] < 7.5, True)
    h = a("Full-grain leather. Lining: 100% cupro",
          ["french_seams", "hand_finished", "quality_hardware"])
    check("grade + 2 proofs + no faults = Heirloom", h["verdict"], "Heirloom")
    check("a fault rules out Heirloom",
          a("Full-grain leather. Lining: 100% cupro",
            ["french_seams", "hand_finished", "loose_threads"])["score"] <= 8.9, True)
    check("construction can't rescue plastic", a("100% polyester", ["french_seams"])["verdict"], "Skip it")
    check("faults always cost", a("100% wool", ["overlocked_only"])["score"], 7.7)
    check("unknown tokens ignored", a("100% Wool", ["made_by_angels"])["score"], 7.9)
    check("listing words count as evidence",
          fabric.quality_score("Shirt 100% Supima cotton, French seams, horn buttons, 240gsm",
                               allow_bare=False)[0] > 7.9, True)
    check("durability isn't capped", a("100% Leather")["durability"], 8.0)
    check("Heirloom is rare: no tag-only text can reach it",
          max(a(t)["score"] for t in ["100% cashmere", "100% merino", "full grain leather",
                                      "100% silk mulberry 22 momme", "100% Supima cotton 280gsm"]) < 9, True)

    print("\n=== v13: seam photo read is strict ===")
    check("not a seam -> nothing", seams.sanitize({"is_seam": False,
          "found": [{"token": "french_seams", "confidence": "high"}]}), [])
    check("proof needs high confidence", seams.sanitize({"is_seam": True,
          "found": [{"token": "french_seams", "confidence": "medium"}]}), [])
    check("fault counts at medium", seams.sanitize({"is_seam": True,
          "found": [{"token": "loose_threads", "confidence": "medium"}]}), ["loose_threads"])
    check("off-list words dropped", seams.sanitize({"is_seam": True,
          "found": [{"token": "luxurious", "confidence": "high"}]}), [])

    print("\n=== v13: better made earns more points, and only for real scores ===")
    check("base verdict rate", fund.rate_for("v", "bronze", 7.5), 3)
    check("real thing +1", fund.rate_for("v", "bronze", 8.2), 4)
    check("heirloom +2", fund.rate_for("v", "bronze", 9.3), 5)
    check("gold closet heirloom", fund.rate_for("c", "gold", 9.3), 8)
    url = "https://shop.example.com/coat"
    sig = fund.sign_score(url, 9.3)
    check("signed score verifies", fund.verified_score(url, 9.3, sig), 9.3)
    check("edited score rejected", fund.verified_score(url, 9.9, sig), None)
    check("signature tied to the link", fund.verified_score("https://other.example.com/x", 9.3, sig), None)
    check("no signature, no bonus", fund.verified_score(url, 9.3, None), None)


def test_v14_seasonal_home():
    import os, main
    print("\n=== v14: the home card follows the season ===")
    for k in [k for k in os.environ if k.startswith("HOME_")]:
        del os.environ[k]
    check("October is coat season", main.home_config(10)["home_eyebrow"], "COAT SEASON")
    check("fall copy", main.home_config(10)["home_title"], "Check the lining.")
    check("January is winter", main.home_config(1)["season"], "winter")
    check("July is summer", main.home_config(7)["season"], "summer")
    check("no photo set -> app keeps its bundled one", main.home_config(10)["home_image_url"], None)
    os.environ["HOME_IMAGE_URL_FALL"] = "https://x.example/fall.jpg"
    os.environ["HOME_IMAGE_URL_SUMMER"] = "https://x.example/summer.jpg"
    check("fall photo in fall", main.home_config(10)["home_image_url"], "https://x.example/fall.jpg")
    check("summer photo in summer", main.home_config(7)["home_image_url"], "https://x.example/summer.jpg")
    os.environ["HOME_SEASON"] = "summer"
    check("pinned season wins", main.home_config(10)["home_eyebrow"], "LINEN SEASON")
    os.environ["HOME_TITLE"] = "Launch week."
    check("one-off override beats the season", main.home_config(10)["home_title"], "Launch week.")
    for k in [k for k in os.environ if k.startswith("HOME_")]:
        del os.environ[k]


def test_v16_colour_pick():
    import brands, vision
    print("\n=== v16: the shopper's colour joins the search, never filters ===")
    qs = brands.build_queries("women's jacket", look=["black"])
    check("colour lands in a query", any("black" in q for q in qs), True)
    good = {"title": "Wool Jacket 100% Wool", "extracted_price": 200, "source": "Maker"}
    check("a navy pick still shows a better-made piece with no colour in its title",
          catalog.evaluate(good, price=180, scanned_score=5.0, category="jacket") is not None, True)
    check("matching colour ranks higher", catalog.look_match(
        {"title": "Black Wool Jacket 100% Wool"}, ["black"]) > catalog.look_match(good, ["black"]), True)
    import main
    check("leather is an allowed pick", "leather" in main.EXTRA_LOOK_PICKS, True)
    check("leather lands in a query",
          any("leather" in q for q in brands.build_queries("women's jacket", look=["leather"])), True)
    check("app colours are all in the vocabulary",
          {"black","white","cream","grey","navy","blue","brown","tan","green","red","burgundy","pink"} <= vision.COLOR, True)


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
    test_v11_department_price_fast_accounts()
    test_v12_closet_fund()
    test_v13_evidence_and_rarity()
    test_v14_seasonal_home()
    test_v16_colour_pick()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  -", f)
        raise SystemExit(1)
    print("ALL PASS")
