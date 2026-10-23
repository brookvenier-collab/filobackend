"""
Filo regression tests. Run with:  python3 test_filo.py

The integrity tests are the important ones. Filo's single promise is that a
"better-made option" is genuinely better made. If those tests ever go red,
something is recommending an item we cannot vouch for — treat that as broken,
not as a failing test to adjust.
"""
import fabric
import barcodes as _barcodes
_barcodes._fetch_upcitemdb = lambda g: None   # tests stay offline
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
    # v27: values from the sourced, named-part score (fabric.fibre_parts).
    # Were 5.4 / 2.2 / 7.9 / 5.2 under the hand-set table.
    print("\n=== scoring: known inputs (v27 named parts) ===")
    for comp, want in [
        ("60% Cotton, 40% Polyester", 6.0),
        ("100% Polyester", 4.5),
        ("100% Linen", 7.2),   # v36: breathes, but wears at the folds; no lifespan data
        ("Top 55% Cotton 45% Polyester", 5.9),
        ("100% Cotton", 7.0),  # the reference
        ("100% Wool", 7.9),    # 8.4 uncapped, held at the tag-only cap
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
          any(brands.maker_in(q) for q in brands.build_queries("women's jeans")), True)


def test_price_earns_itself():
    """A piece may cost more than what's in their hands only if it is cheaper
    per wear. Both sides now come from fabric.lifespan() (v26), so fibre alone
    rarely justifies a big price jump: the evidence doesn't show linen lasting
    five times longer than a blend."""
    print("\n=== price has to earn itself in cost-per-wear ===")
    scanned = fabric.quality_score("60% Cotton, 40% Polyester")[0]
    scanned_wears = fabric.expected_wears("60% Cotton, 40% Polyester", "t-shirt")

    for label, item, expect_kept in [
        ("cotton $150 tee, 1.9x, dearer per wear", {"title": "Tee 100% Cotton", "extracted_price": 150, "source": "M"}, False),
        ("wool $90 tee, 1.15x, inside the free headroom", {"title": "Tee 100% Merino Wool", "extracted_price": 90, "source": "M"}, True),
        ("cotton $200 — over the 2.5x ceiling", {"title": "Tee 100% Cotton", "extracted_price": 200, "source": "M"}, False),
        ("suspiciously cheap $20",            {"title": "Tee 100% Cotton", "extracted_price": 20, "source": "M"}, False),
    ]:
        got = catalog.evaluate(item, price=78, scanned_score=scanned, category="t-shirt",
                               scanned_wears=scanned_wears) is not None
        check(label, got, expect_kept)

    check("cost per wear is price / wears", fabric.cost_per_wear(90, 45), 2.0)
    check("no wears, no cost per wear", fabric.cost_per_wear(90, None), None)


def test_score_parts():
    """v27. The score is 7.0 (cotton reference) plus named, sourced parts."""
    print("\n=== the score taken apart ===")
    sp = lambda c: fabric.analyze({"composition": c})["score_parts"]
    check("cotton is the reference, nothing added", [p["part"] for p in sp("100% Cotton")], ["reference"])
    parts = {p["part"]: p["points"] for p in sp("100% Polyester")}
    check("polyester: comfort and pills named", ("comfort" in parts, "pills" in parts), (True, True))
    check("parts add up to the score (within rounding)",
          abs(sum(p["points"] for p in sp("60% Cotton, 40% Polyester"))
              - fabric.quality_score("60% Cotton, 40% Polyester")[0]) <= 0.15, True)
    check("wool lasts longer than cotton", fabric.fibre_points("wool") > fabric.fibre_points("cotton"), True)
    check("viscose wears through faster than cotton", fabric.fibre_points("viscose") < fabric.fibre_points("cotton"), True)
    check("no 'built to be replaced'",
          any("built to be replaced" in r for r in fabric.analyze({"composition": "100% Polyester"})["reasons"]), False)
    check("cotton copy no longer overclaims",
          fabric.analyze({"composition": "100% Cotton"})["reasons"][0],
          "100% cotton. Breathable and easy to care for. A heavier weight lasts longer.")


def test_market_price_check():
    """v28. Price verdicts come from live comparable listings only."""
    import market
    print("\n=== price check: from live listings, three levels, never a guess ===")
    poly = fabric.quality_score("100% Polyester")[1]

    def listings(n, shops, price_fn, comp="100% Polyester", title="Women's Crew T-Shirt"):
        names = ["Alba", "Bea", "Cora", "Dina", "Ema", "Fay", "Gia", "Hana", "Ida", "Juno",
                 "Kai", "Lena", "Mara", "Nia", "Ora", "Pia", "Quin", "Rhea", "Sia", "Tia",
                 "Uma", "Vera", "Wren", "Xena", "Yara", "Zoe", "Ada", "Bo", "Cy", "Di"]
        return [{"title": f"{title} {comp} {names[i]}", "source": f"Shop{i % shops}",
                 "extracted_price": price_fn(i), "link": f"l{i}"} for i in range(n)]
    tees = listings(30, 12, lambda i: 15 + i)         # $15 to $44, median ~$30
    fetch = lambda q, num=100: tees
    b = market.benchmark("t-shirt", poly, "women", fetch=fetch)
    check("enough listings -> a benchmark", (b["enough"], b["listings"], b["shops"]), (True, 30, 12))
    check("median is the middle listing", b["median"], 30)
    lvl = lambda p: market.check(p, "t-shirt", poly, "women", fetch=fetch)["level"]
    check("$12 is good value", lvl(12), market.GOOD)
    check("$30 is fair", lvl(30), market.FAIR)
    check("$40, above p75 but under 1.25x median, is still fair", lvl(37), market.FAIR)
    check("$180 polyester tee: you may be overpaying", lvl(180), market.OVER)
    over = market.check(180, "t-shirt", poly, "women", fetch=fetch)
    check("overpaying line shows the multiple and the range",
          over["line"].startswith("$180 is about 6.0 times the usual price for 100% polyester t-shirts"), True)
    check("says what it's based on", over["based_on"].startswith("Based on 30 similar listings at 12 shops"), True)
    check("$55 (under 2x the median) is not called overpaying", lvl(55), market.FAIR)
    check("the listing sample never reaches the app", "_sample" in over["benchmark"], False)
    packs = lambda q, num=100: [dict(x, title=x["title"] + " 3 Pack") for x in tees]
    check("multipacks don't count", market.check(180, "t-shirt", poly, fetch=packs)["level"], None)
    kids = lambda q, num=100: [dict(x, title="Girls " + x["title"]) for x in tees]
    check("kids' sizes don't count", market.check(180, "t-shirt", poly, fetch=kids)["level"], None)
    deep = lambda q, num=100: listings(30, 30, lambda i: 15 + i)[:6] + [dict(x, source="BigShop") for x in listings(30, 30, lambda i: 5)]
    check("one shop counts at most 3 times", market.benchmark("t-shirt", poly, fetch=deep)["listings"], 9)
    check("never names a brand or shop", "Shop" in over["line"] + over["based_on"], False)
    for bad in ("rip", "scam", "fake", "lying"):
        check(f"no '{bad}' in the line", bad in over["line"].lower(), False)

    few = lambda q, num=100: tees[:12]
    check("under 20 listings -> no verdict", market.check(180, "t-shirt", poly, fetch=few)["level"], None)
    one_shop = lambda q, num=100: listings(30, 4, lambda i: 20)
    check("under 5 shops -> no verdict", market.check(180, "t-shirt", poly, fetch=one_shop)["level"], None)

    cotton = lambda q, num=100: listings(30, 12, lambda i: 60, comp="100% Cotton")
    check("cotton listings don't price a polyester tee",
          market.check(180, "t-shirt", poly, fetch=cotton)["level"], None)
    bare = lambda q, num=100: [dict(x, title="Women's Crew T-Shirt") for x in tees]
    check("listings that don't state fibre don't count",
          market.check(180, "t-shirt", poly, fetch=bare)["level"], None)
    dresses = lambda q, num=100: listings(30, 12, lambda i: 20, title="Midi Dress")
    check("other garments don't count", market.check(180, "t-shirt", poly, fetch=dresses)["level"], None)
    resale = lambda q, num=100: [dict(x, source="The RealReal") for x in tees]
    check("pre-owned doesn't count", market.check(180, "t-shirt", poly, fetch=resale)["level"], None)
    sale = [dict(x, extracted_price=5, extracted_old_price=x["extracted_price"]) for x in tees]
    check("uses the regular price, not the sale price",
          market.benchmark("t-shirt", poly, fetch=lambda q, num=100: sale)["median"], 30)
    check("no price -> no check", market.check(None, "t-shirt", poly, fetch=fetch), None)
    seen_gl = []
    catalog._fetch = lambda q, num=40, gl=None: (seen_gl.append(gl), tees)[1]
    ca = market.check(180, "t-shirt", poly)
    check("Canadian scan asks for Canadian listings", market.check(180, "t-shirt", poly, country="CA")["based_on"].endswith("in Canada, " + ca["benchmark"]["as_of"] + ". Prices in CAD."), True)
    check("country goes to the search", "ca" in seen_gl and "us" in seen_gl, True)


def test_products(store_factory=None, label="memory"):
    """v29. The product database: barcode in, verdict back, built by scans."""
    import products, main
    print(f"\n=== product database ({label}) ===")
    P = products
    S = store_factory() if store_factory else P.MemoryStore()
    check("UPC-A with a good check digit", P.normalize_gtin("036000291452"), "00036000291452")
    check("EAN-13 with a good check digit", P.normalize_gtin("4006381333931"), "04006381333931")
    check("bad check digit is refused", P.normalize_gtin("036000291453"), None)
    check("junk is refused", P.normalize_gtin("hello"), None)
    check("fabric is stored the same way however it's written",
          (P.canonical_composition("Polyester 40% Cotton 60%"), P.canonical_composition("60% cotton, 40% polyester")),
          ("60% cotton, 40% polyester", "60% cotton, 40% polyester"))
    check("fabric that doesn't add up is refused", P.canonical_composition("60% cotton 20% polyester"), None)
    check("RN, CA number and country read off a tag",
          P.from_tag("RN 54867 CA 12345 Made in Portugal 100% cotton"),
          {"rn": "54867", "ca_number": "12345", "made_in": "Portugal"})

    g = "036000291452"
    a = P.contribute(g, {"brand": "Aritzia", "composition": "100% Cotton", "category": "T-Shirt"},
                     "session-aaaaaaaa", s=S)
    check("first scan is accepted", sorted(a["accepted"]), ["brand", "category", "composition"])
    one = P.lookup(g, s=S)
    check("one scan: unconfirmed", one["status"], "unconfirmed")
    P.contribute(g, {"brand": "Aritzia", "composition": "100% Cotton"}, "session-aaaaaaaa", s=S)
    check("same phone twice the same day counts once",
          P.lookup(g, s=S)["fields"]["brand"]["votes"], 1)
    P.contribute(g, {"brand": "Aritzia", "composition": "100% cotton"}, "session-bbbbbbbb", s=S)
    two = P.lookup(g, s=S)
    check("two independent scans: confirmed", two["status"], "confirmed")
    check("scans are counted", two["scans"], 3)
    P.contribute(g, {"brand": "Zara"}, "session-cccccccc", s=S)
    check("one dissent doesn't flip the brand", P.lookup(g, s=S)["fields"]["brand"]["value"], "Aritzia")
    P.contribute(g, {"brand": "Zara"}, "session-dddddddd", s=S)
    check("a real split drops back to unconfirmed",
          P.lookup(g, s=S)["fields"]["brand"]["status"], "unconfirmed")
    S.set_locked(P.normalize_gtin(g), "brand", "Aritzia")
    P.contribute(g, {"brand": "Zara"}, "session-eeeeeeee", s=S)
    check("locked fields can't be voted over", P.lookup(g, s=S)["fields"]["brand"],
          {"value": "Aritzia", "status": "locked", "votes": 1000000, "of": 1000000})
    S.set_registrant("RN", "54867", "Example Imports Inc.", "importer")
    P.contribute(g, {"rn": "54867"}, "session-ffffffff", s=S)
    check("RN shows who it's registered to",
          P.lookup(g, s=S)["fields"]["rn"]["registered_to"], "Example Imports Inc.")
    check("bad barcode can't be contributed", P.contribute("123", {"brand": "X"}, "session-x1234567", s=S)["ok"], False)
    check("no session, no vote", P.contribute("4006381333931", {"brand": "X"}, None, s=S)["accepted"], [])
    check("brand summary counts products", P.brand_summary(s=S)[0]["brand"], "Aritzia")
    check("nothing about a person is stored",
          any(isinstance(k, tuple) and "session" in str(k) for k in getattr(S, "votes", [])), False)
    return S


def test_products_api():
    """v29. /analyze with a barcode: learns from the tag, answers from the database."""
    import products, main
    print("\n=== product database through /analyze ===")
    products._memory = products.MemoryStore()
    g = "4006381333931"
    A = lambda **kw: main.analyze(main.AnalyzeRequest(item=main.Item(mode="score", **kw)))
    new = A(gtin=g)
    check("unknown barcode, no tag: asks for the tag", (new["score"], new["product"]["status"]), (None, "new"))
    A(gtin=g, composition="95% Cotton, 5% Elastane", brand="Aritzia", category="t-shirt",
      tag_text="RN 12345 Made in Vietnam 95% Cotton 5% Elastane", session_id="sess-11111111")
    back = A(gtin=g)
    check("next shopper gets the verdict from the barcode alone", back["score"],
          fabric.quality_score("95% Cotton, 5% Elastane")[0])
    check("and is told it's from one scan, unconfirmed", back["product"]["status"], "unconfirmed")
    check("brand came along", back["product"]["fields"]["brand"]["value"], "Aritzia")
    check("country made came off the tag", back["product"]["fields"]["made_in"]["value"], "Vietnam")
    A(gtin=g, composition="95% cotton 5% elastane", session_id="sess-22222222")
    check("second tag scan confirms it", A(gtin=g)["product"]["status"], "confirmed")
    check("a lookup-only scan doesn't vote for itself", A(gtin=g)["product"]["scans"], 2)
    check("bad barcode is said plainly", A(gtin="123", composition="100% Cotton")["product"]["status"], "invalid")


def test_v36_premium_fibres():
    """Brooklyn, 22 Oct 2026: wool, cashmere, silk and leather all showed 79, and
    leather was a typed-in number. Each premium fibre now scores on its own
    sourced parts, under the tag-only cap for its own reasons."""
    print("\n=== v36: premium fibres scored on their own ===")
    q = lambda c: fabric.quality_detail(c)["score"]
    tops = {c: q("100% " + c) for c in ["wool", "cashmere", "silk", "linen", "leather"]}
    check("the five no longer share one score", len(set(tops.values())) >= 4, True)
    check("nothing tag-only passes 79", max(tops.values()) <= 7.9, True)
    check("all five at or above cotton", min(tops.values()) >= q("100% Cotton"), True)
    check("wool tops the tag-only scores", tops["wool"], max(tops.values()))
    check("cashmere pills more than wool", tops["cashmere"] < tops["wool"], True)
    check("leather is no longer hand-set",
          [p["part"] for p in fabric.score_parts(fabric.quality_detail("100% leather")["matched"])
           if p["part"] == "fixed"], [])
    check("leather is tougher than cotton",
          dict((p["part"], p["points"]) for p in
               fabric.score_parts(fabric.quality_detail("100% leather")["matched"])).get("wear", 0) > 0, True)
    check("suede marks down a little", q("100% suede") < q("100% leather"), True)
    check("genuine leather below plain leather", q("Genuine leather") < q("100% Leather"), True)
    check("poly-lined leather is not The real thing",
          fabric.verdict(q("Full-grain leather. Lining: 100% polyester")) != "The real thing", True)
    check("faux leather still far below", q("100% vegan leather") < 4.5, True)
    check("elastane at 10% barely moves cotton", round(q("90% cotton 10% elastane"), 1), 6.9)
    lines = " ".join(fabric.reasons(q("100% silk"), fabric.quality_detail("100% silk")["matched"], None))
    check("silk copy says it's delicate", "delicate" in lines, True)
    lines = " ".join(fabric.reasons(q("100% leather"), fabric.quality_detail("100% leather")["matched"], None))
    check("no 'decades' claim for leather", "decade" in lines, False)
    check("no em dashes in new copy",
          any("\u2014" in l for l in list(fabric.NATURAL_LINES.values()) +
              list(fabric.FIBRE_PART_LINES.values()) + [r[3] for r in fabric._GRADE_RULES]), False)


def test_v37_market_price():
    """Brooklyn, 23 Oct 2026: "Worth it?" must say what similar pieces sell for
    today, never what a piece costs to make, and answer on the fast scan."""
    print("\n=== v37: worth it, from today's listings ===")
    import market, main
    lst = lambda t, p, src: {"title": t, "extracted_price": p, "source": src}
    shops = [f"Shop{i}" for i in range(8)]
    styles = ["Moto", "Biker", "Bomber", "Blazer", "Trucker", "Racer", "Aviator", "Shirt",
              "Cropped", "Longline", "Boxy", "Belted", "Collarless", "Quilted", "Zip", "Field",
              "Western", "Utility", "Oversized", "Fitted", "Suede", "Classic", "Vintage", "Wrap"]
    leather = [lst(f"Women's {w} Leather Jacket 100% Leather", 300 + i * 20, shops[i % 8])
               for i, w in enumerate(styles)]
    faux = [lst(f"Women's {w} Jacket 100% Polyurethane", 60, shops[i % 8]) for i, w in enumerate(styles)]
    fetch = lambda q, num=100: leather + faux
    _, m = fabric.quality_score("100% Leather")
    pc = market.check(100, "jacket", m, "women", fetch=fetch)
    check("real leather is now priced", pc["level"], market.GOOD)
    check("...against real leather only", pc["benchmark"]["median"] >= 300, True)
    check("short line says what pieces sell for", pc["short"].startswith("Similar pieces sell for $"), True)
    check("plain words", (market.LABELS[market.GOOD], market.LABELS[market.OVER]), ("Good value", "Pricey for what it is"))
    _, fm = fabric.quality_score("100% Polyurethane")
    check("faux leather isn't priced", market.benchmark("jacket", fm, fetch=fetch), None)
    row = market.price_rows(pc)[0]
    check("row title is the word", row["title"], "Good value")
    check("no cost-to-make wording", any("to make" in (row[k] or "") for k in ("line", "detail")), False)
    check("not enough listings says so", market.price_rows({"level": None, "line": market.NOT_ENOUGH,
                                                           "pending": False})[0]["line"], market.NOT_ENOUGH)
    check("still waiting: no row", market.price_rows({"pending": True}), [])
    r = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% cotton", price=40, mode="score")))
    check("no type: asks what it is", r["price_check"].get("needs_type"), True)
    check("...as a Worth it? row",
          [x["title"] for x in r["breakdown"] if x["side"] == "price"], ["Worth it?"])
    r = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% cotton", mode="score")))
    check("no price: no price row", [x for x in r["breakdown"] if x["side"] == "price"], [])
    check("price section comes last", main.BREAKDOWN_SECTIONS[-1]["label"], "WORTH IT?")


def test_v40_leather_lining_label():
    """A polyester lining after a leather shell is not a leather lining."""
    print("\n=== v40: leather lining read correctly ===")
    tag = "Shell: 100% Leather Lining: 100% Polyester"
    d = fabric.quality_detail(tag)
    check("no 'lined in leather' from a section label", "leather_lining" in d["construction"], False)
    check("still reads a real leather lining",
          "leather_lining" in fabric.quality_detail("100% lambskin, leather-lined")["construction"], True)
    check("poly-lined leather stays below The real thing", d["score"] < 8.0, True)
    import breakdown, main
    r = main.analyze(main.AnalyzeRequest(item=main.Item(composition=tag, category="Jacket", mode="score")))
    life = [x["line"] for x in r["breakdown"] if x["title"] == "How long it lasts"]
    check("no 'decades' for leather", any("ecade" in l for l in life), False)


def test_v41_barcode_brain():
    """Brooklyn, 23 Oct 2026: barcode -> Filo's database -> outside lookup ->
    verdict, so a shopper doesn't have to scan two things every time."""
    print("\n=== v41: the barcode brain ===")
    import barcodes, products, main
    products._memory.__init__()
    barcodes._cache.clear(); barcodes._misses.clear()
    gtin = "0012345678905"
    found = {"brand": "Kotn", "name": "Essential Crew Tee", "image": "https://img.example/t.jpg",
             "url": "https://kotn.example/tee", "description": "A soft tee.", "source": "upcitemdb"}
    calls = []
    fake = lambda g: (calls.append(g), found)[1]
    k = barcodes.resolve(gtin, fetch=fake)
    check("unknown barcode asks outside once", len(calls), 1)
    check("...and saves the brand", products.value(k, "brand"), "Kotn")
    check("...and the photo", products.value(k, "image"), "https://img.example/t.jpg")
    check("no fabric yet counts as a miss", barcodes.misses()[0]["code"], products.normalize_gtin(gtin))
    check("...noting the outside lookup found it", barcodes.misses()[0]["external_found"], True)
    barcodes.resolve(gtin, fetch=fake)
    check("a known brand is not looked up again", len(calls), 1)
    sid = "test-session-123456"
    r = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% Cotton", gtin=gtin,
                                                        session_id=sid, mode="score")))
    check("the care tag scan fills in the fabric",
          products.value(products.lookup(gtin), "composition"), "100% cotton")
    r2 = main.analyze(main.AnalyzeRequest(item=main.Item(gtin=gtin, mode="score")))
    check("next time the barcode alone gives the score", r2["score"], 7.0)
    check("...with the brand", r2["product"]["fields"]["brand"]["value"], "Kotn")
    check("store price-tag codes are kept", products.normalize_gtin("c:ab12cd34"), "C:AB12CD34")
    check("...and never looked up outside", barcodes.external_lookup("C:AB12CD34", fetch=fake), None)
    check("only https photos", products._clean("image", "http://x.example/a.jpg"), None)
    res = barcodes.import_records([
        {"gtin": "4006381333931", "brand": "Uniqlo", "title": "Supima Cotton Crew Neck T-Shirt",
         "image_url": "https://img.example/u.jpg", "description": "100% Cotton. Machine wash."},
        {"gtin": "123", "brand": "Bad"}], source="feed-test")
    check("feed import adds the good record, skips the bad", (res["added"], res["skipped"]), (1, 1))
    u = products.lookup("4006381333931")
    check("feed facts are locked", u["fields"]["brand"]["status"], "locked")
    check("feed fabric read from the description", products.value(u, "composition"), "100% cotton")
    r3 = main.analyze(main.AnalyzeRequest(item=main.Item(gtin="4006381333931", mode="score")))
    check("a feed product scores from the barcode alone", r3["score"], 7.0)
    barcodes.external_lookup("0099999999999", fetch=lambda g: (_ for _ in ()).throw(TimeoutError()))
    check("an outside failure never breaks a scan", True, True)


def test_v42_usual_price():
    """Brooklyn, 23 Oct 2026: once Filo knows a product, it should know its price too."""
    print("\n=== v42: the usual price for a barcode ===")
    import products, main
    products._mem_prices.clear()
    g = "4006381333931"
    check("no price yet", products.usual_price(g, "CA"), None)
    main.analyze(main.AnalyzeRequest(item=main.Item(gtin=g, composition="100% Cotton", category="T-shirt",
                                                    price=40, country="CA", mode="score")))
    main.analyze(main.AnalyzeRequest(item=main.Item(gtin=g, composition="100% Cotton", category="T-shirt",
                                                    price=50, country="CA", mode="score")))
    u = products.usual_price(g, "CA")
    check("shoppers' prices make the usual price", (u["price"], u["currency"], u["count"]), (45.0, "CAD", 2))
    check("a different country keeps its own", products.usual_price(g, "US"), None)
    r = main.analyze(main.AnalyzeRequest(item=main.Item(gtin=g, country="CA", mode="score")))
    check("the next scan gets the usual price", r["usual_price"]["price"], 45.0)
    main.analyze(main.AnalyzeRequest(item=main.Item(gtin=g, price=45, price_source="usual",
                                                    country="CA", mode="score")))
    check("the usual price sent back isn't counted again", products.usual_price(g, "CA")["count"], 2)
    check("silly prices are refused", products.record_price(g, 0, "CA"), False)
    check("no shopper id is stored with a price", set(products._mem_prices[0]),
          {"gtin", "price", "currency", "country", "source", "day"})


def test_v39_more_listings():
    """Brooklyn, 23 Oct 2026: a 100% cotton tee at $25 in Canada said "Not enough
    similar listings" (5 usable). Shop titles rarely spell out "100%"."""
    print("\n=== v39: plain shop titles count, Canada falls back to the US ===")
    import market
    words = ["Crew", "Boxy", "Classic", "Relaxed", "Fitted", "Pocket", "Ringer", "Baby",
             "Muscle", "Vintage", "Heavyweight", "Essential", "Slub", "Oversized", "Cropped",
             "Longline", "Scoop", "Raglan"]
    bare = [{"title": f"Women's {w} Cotton Tee", "extracted_price": 20 + i * 2,
             "source": f"Shop{i % 6}"} for i, w in enumerate(words)]
    _, m = fabric.quality_score("100% cotton")
    check("a plain 'Cotton Tee' title counts as similar",
          market.match_kind(bare[0], "t-shirt", m, "women"), "similar")
    check("...but not as stated", market.comparable(bare[0], "t-shirt", m, "women"), False)
    other = {"title": "Women's Crew Polyester Tee", "extracted_price": 15, "source": "X"}
    check("a title naming another fibre doesn't", market.match_kind(other, "t-shirt", m, "women"), None)
    blend = {"title": "Women's Cotton Modal Tee", "extracted_price": 15, "source": "X"}
    check("a blend named in words doesn't", market.match_kind(blend, "t-shirt", m, "women"), None)
    pc = market.check(25, "t-shirt", m, "women", fetch=lambda q, num=100: bare)
    check("plain titles now give an answer", pc["level"] is not None, True)
    check("...in plain words", pc["short"].startswith("Similar pieces sell for $"), True)
    calls = []
    def by_country(q, num=100, gl=None):
        return []
    import catalog
    real = catalog._fetch
    try:
        catalog._fetch = lambda q, num=100, gl=None: bare if gl == "us" else []
        b = market.benchmark("t-shirt", m, "women", country="CA")
    finally:
        catalog._fetch = real
    check("Canada short: US listings join", (b["enough"], b["market"]), (True, "Canada and the US"))
    check("...converted to CAD", b["currency"], "CAD")


def test_lifespan_is_sourced():
    """v26. "How long it lasts" must move with the garment, not the score band."""
    print("\n=== how long it lasts is computed, not templated ===")
    L = fabric.lifespan
    tee = lambda c: L(c, "t-shirt")
    check("100% cotton tee is the Ecobalyse reference, 45 washes",
          tee("100% Cotton")["washes"], 45)
    check("wool outlasts cotton (Laitala & Klepp 2020)",
          tee("100% Wool")["wears"] > tee("100% Cotton")["wears"], True)
    # v36: silk's +2.2 years came from rarely worn ties, so it is held at cotton.
    check("silk no longer outlasts wool", tee("100% Silk")["wears"] < tee("100% Wool")["wears"], True)
    check("viscose not credited with the regenerated bonus",
          tee("100% Viscose")["wears"] < tee("100% Lyocell")["wears"], True)
    check("same fibre, two garments: same multiplier, different washes",
          (L("100% Cotton", "t-shirt")["multiplier"] == L("100% Cotton", "sweater")["multiplier"],
           L("100% Cotton", "t-shirt")["washes"] != L("100% Cotton", "sweater")["washes"]),
          (True, True))
    check("sweater talks in wears, tee in washes",
          (L("100% Wool", "sweater")["unit"], tee("100% Wool")["unit"]), ("wears", "washes"))
    check("heavy fabric lasts longer", tee("100% Cotton 240gsm")["wears"] > tee("100% Cotton")["wears"], True)
    check("thin fabric lasts less", tee("100% Cotton 130gsm")["wears"] < tee("100% Cotton")["wears"], True)
    check("stated weight narrows the range",
          (lambda a, b: (a[1] - a[0]) < (b[1] - b[0]))(tee("100% Cotton 240gsm")["washes_range"],
                                                      tee("100% Cotton")["washes_range"]), True)
    check("never outside the Ecobalyse 0.67–1.45 band",
          all(0.67 <= tee(c)["multiplier"] <= 1.45 for c in
              ["100% Silk 30 momme 240gsm", "100% Cotton 100gsm", "100% Polyester"]), True)
    canon = ["100% Cotton", "100% Linen", "100% Polyester", "95% Cotton, 5% Elastane",
             "60% Cotton, 40% Acrylic", "100% Viscose", "100% Wool"]
    lines = [tee(c)["line"] for c in canon]
    print("        " + " | ".join(lines))
    check("the 'Solid' template line is gone",
          any("30–50" in fabric.analyze({"composition": c, "group": "t-shirt"})["wears"] for c in canon), False)
    check("leather keeps its own line", L("100% Leather", "jacket"), None)
    check("no garment type: wears only, no washes", L("100% Cotton")["washes"], None)
    check("cost per wear moves when wears move",
          fabric.cost_per_wear(100, tee("100% Wool")["wears"]) <
          fabric.cost_per_wear(100, tee("100% Cotton")["wears"]), True)


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
        # v26: $105, not $120. Under the sourced model merino doesn't last 8x a
        # blend, so a 1.5x price no longer earns itself per wear. This test is
        # about tier order, so keep the price inside the free headroom.
        {"title": "Merino Crew 100% Merino Wool",
         "extracted_price": 105, "source": "ARMEDANGELS", "link": "f"},
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
    check("real leather scores as leather (v36 parts)", real, 7.5)
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

    # Jackets now have real specialists (the taste tier, v17). The rule that
    # still matters: a category nobody specialises in gets no brand-led query.
    check("no fallback makers where nobody specialises", brands.makers_for("leggings"), [])
    check("jacket makers actually make jackets",
          all("jacket" in brands.QUALITY_MAKERS[m]["good_at"] or "leather" in brands.QUALITY_MAKERS[m]["good_at"]
              for m in brands.makers_for("jacket")), True)
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
    # v22: the in-range coat still leads; the pricey one only fills a spare slot,
    # and is labelled as outside the usual range.
    check("no price: $280 coat first, $760 only as a labelled extra",
          [(a["brand"], a["fit"]) for a in alts], [("A", "better"), ("B", "pricier")])
    check("no price: no made-up cost-per-wear line", alts[0]["value_note"], None)
    check("leather gets a leather-sized range",
          catalog.typical_price("jacket", "faux leather"), catalog.TYPICAL_LEATHER_PRICE)
    priced = catalog.search_alternatives("coat", price=600, scanned_score=7.0)
    check("with a price, the in-band piece leads",
          [(a["brand"], a["fit"]) for a in priced], [("B", "better"), ("A", "pricier")])


def test_v23_taste():
    """Taste reorders. It never lets a piece in, and never changes a score."""
    print("\n=== v23: taste ranks, never filters ===")
    catalog.SERPAPI_KEY = "test"
    asked = []
    tees = [{"title": "Classic Tee 100% Organic Cotton", "extracted_price": 60, "source": "Plainco", "link": "a"},
            {"title": "Cropped Boxy Tee 100% Organic Cotton", "extracted_price": 60, "source": "Shapeco", "link": "b"},
            {"title": "Everyday Tee 100% Organic Cotton", "extracted_price": 60, "source": "Lovedlabel", "link": "c"},
            {"title": "Cropped Boxy Tee 100% Polyester", "extracted_price": 60, "source": "Lovedlabel", "link": "d"}]
    def fetch(q, num=40):
        asked.append(q)
        return tees
    catalog._fetch = fetch
    plain = catalog.search_alternatives("t-shirt", price=60, scanned_score=2.2)
    asked.clear()
    taste = {"words": ["cropped", "boxy", "DROP TABLE", 7], "brands": ["Lovedlabel", "Shein", "x" * 90]}
    mine = catalog.search_alternatives("t-shirt", price=60, scanned_score=2.2, taste=taste)
    check("no taste -> nothing marked for you", any(a["for_you"] for a in plain), False)
    check("same pieces either way", sorted(a["name"] for a in mine), sorted(a["name"] for a in plain))
    check("same scores either way", sorted(a["score"] for a in mine), sorted(a["score"] for a in plain))
    check("their brand and their shape come first", {a["retailer"] for a in mine[:2]}, {"Lovedlabel", "Shapeco"})
    check("the plain one comes last", mine[-1]["retailer"], "Plainco")
    check("polyester in their shape from their brand still never shows",
          any("Polyester" in a["name"] for a in mine), False)
    check("one extra search for the liked brand", "Lovedlabel t-shirt" in asked, True)
    words, liked = catalog.clean_taste(taste)
    check("unknown words are dropped", words, ["cropped", "boxy"])
    check("blocked and junk brands are dropped", liked, ["Lovedlabel"])
    check("junk taste is ignored", catalog.clean_taste("hello"), ([], []))
    check("internal keys are not sent", any(k.startswith("_") for a in mine for k in a), False)


def test_v22_always_an_option():
    """A typed price must not empty the list, and the price can be taken away."""
    print("\n=== v22: price never empties the list ===")
    catalog.SERPAPI_KEY = "test"
    tees = [{"title": "Organic Tee 100% Organic Cotton", "extracted_price": 95, "source": "A", "link": "a"},
            {"title": "Linen Tee 100% Linen", "extracted_price": 180, "source": "B", "link": "b"},
            {"title": "Poly Tee 100% Polyester", "extracted_price": 20, "source": "C", "link": "c"}]
    catalog._fetch = lambda q, num=40: tees
    # $20 scan: both good tees are far above 2.5x. Before v22 this was empty.
    alts = catalog.search_alternatives("t-shirt", price=20, scanned_score=2.2)
    check("price far below the good ones still shows them, nearest first",
          [a["brand"] for a in alts], ["A", "B"])
    check("they are labelled as outside the price", {a["fit"] for a in alts}, {"pricier"})
    check("the app gets a plain line saying so", catalog.note_for(alts), catalog.NOTE_PRICIER)
    check("plain line has no long dash", "\u2014" in catalog.NOTE_PRICIER + catalog.NOTE_AS_GOOD, False)
    check("polyester never gets in, at any level", "C" in [a["brand"] for a in alts], False)
    # Same scan with the price removed: same pieces, nothing hidden.
    free = catalog.search_alternatives("t-shirt", price=None, scanned_score=2.2)
    check("price removed -> options still there", sorted(a["brand"] for a in free), ["A", "B"])
    # In-band picks carry no note.
    ok = catalog.search_alternatives("t-shirt", price=90, scanned_score=2.2)
    check("an in-band pick means no note", catalog.note_for(ok), None)
    # Already holding a good one: equals are offered, and said to be equals.
    top = max(catalog.evaluate(t, scanned_score=0)["score"] for t in tees[:2])
    same = catalog.search_alternatives("t-shirt", price=95, scanned_score=top)
    check("nothing better -> as-good pieces, labelled",
          {a["fit"] for a in same} <= {"as_good"} and len(same) >= 1, True)
    check("as-good note", catalog.note_for(same), catalog.NOTE_AS_GOOD)
    check("a worse piece is never offered", all(a["score"] >= top for a in same), True)
    check("internal sort keys are not sent to the app",
          any(k.startswith("_") for a in alts for k in a), False)


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
    check("fast mode returns a score", fast["score"], 4.5)
    check("fast mode is pending", fast["pending"], True)
    check("no price -> prompt", fast["price_prompt"], fabric.PRICE_PROMPT)
    check("no price -> no value verdict", fast["value_note"], None)
    priced = main.analyze(main.AnalyzeRequest(item=main.Item(composition="100% Polyester",
                                                             price=75, category="t-shirt", mode="score")))
    # v37: the fast path now runs the listings check itself, so it answers.
    check("price -> price check answered on the fast path", priced["price_check"]["pending"], False)
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
    check("tag-only 100% leather, own parts (v36)", a("100% Leather")["score"], 7.5)
    check("tag-only is 'Solid', not 'The real thing'", a("100% Leather")["verdict"], "Solid")
    check("capped scan explains what's missing", a("100% Wool")["ceiling_note"] is not None, True)
    check("poly-lined leather", a("100% Leather / Lining: 100% Polyester")["score"], 7.0)
    check("stated grade can reach The real thing", a("Full-grain leather. Lining: 100% cupro")["score"], 8.2)
    check("genuine leather is marked down", a("Genuine leather")["score"] < 7.5, True)
    h = a("Full-grain leather. Lining: 100% cupro",
          ["french_seams", "hand_finished", "quality_hardware"])
    check("grade + 2 proofs + no faults = Heirloom", h["verdict"], "Heirloom")
    check("a fault rules out Heirloom",
          a("Full-grain leather. Lining: 100% cupro",
            ["french_seams", "hand_finished", "loose_threads"])["score"] <= 8.9, True)
    check("construction can't rescue plastic", a("100% polyester", ["french_seams"])["score"] < 6, True)
    check("faults always cost, even above the cap", a("100% wool", ["overlocked_only"])["score"], 7.6)
    check("unknown tokens ignored", a("100% Wool", ["made_by_angels"])["score"], 7.9)
    check("listing words count as evidence",
          fabric.quality_score("Shirt 100% Supima cotton, French seams, horn buttons, 240gsm",
                               allow_bare=False)[0] > 7.9, True)
    check("durability isn't capped", a("100% Wool")["durability"], 8.1)
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


def test_v17_taste_tier_and_department_stores():
    import brands
    print("\n=== v17: cool makers, not department stores ===")
    leather = {"extracted_price": 600, "source": "Macy's"}
    generic = dict(leather, title="INC Women's Leather Moto Jacket 100% Leather")
    schott = dict(leather, title="Schott NYC Women's Leather Moto Jacket 100% Leather")
    kw = dict(price=500, scanned_score=4.0, category="women's jacket", material="real leather")
    check("Macy's house label is dropped", catalog.evaluate(generic, **kw), None)
    kept = catalog.evaluate(schott, **kw)
    check("a listed maker sold at Macy's is kept", kept is not None, True)
    check("...and shown under the maker's name", kept["brand"], "Schott")
    check("...as a known maker", kept["tier"], brands.TIER_MAKER)
    check("maker in the title lifts a boutique listing",
          brands.tier("Nordstrom", "AGOLDE 90s Pinch Waist Jean"), brands.TIER_MAKER)
    check("unknown brand at Nordstrom stays mainstream", brands.tier("Nordstrom", "Nice Jean"), brands.TIER_MAINSTREAM)
    check("a small label Filo doesn't know still gets through",
          catalog.evaluate({"title": "Leather Jacket 100% Lambskin Leather", "extracted_price": 550,
                            "source": "TinyAtelier"}, **kw) is not None, True)
    check("whole-word matching", brands.maker_in("Possession Wrap Dress"), None)
    check("accents and spacing", brands.maker_in("Sézane Gaspard Cardigan"), "sezane")
    check("a maker's name never buys a pass on fabric",
          catalog.evaluate({"title": "Nour Hammour Jacket 100% Polyester", "extracted_price": 500,
                            "source": "Nour Hammour"}, price=500, scanned_score=4.0,
                           category="women's jacket"), None)
    q = brands.build_queries("women's jacket", look=["leather", "burgundy"], material="real leather")
    check("leather pick searches leather jackets", all("leather" in x for x in q), True)
    check("...the way people type it", q[0].startswith("women's leather jacket"), True)
    check("...and names leather makers", sum(1 for x in q if brands.maker_in(x)) >= 1, True)
    check("...keeping the colour", "burgundy" in q[0], True)


def test_v18_budget_knockoffs_cheap_leather():
    import brands
    print("\n=== v18: makers that fit the budget, no knock-offs, no jacket mills ===")
    check("a typical budget doesn't search luxury makers",
          all(brands.PRICE_LEVEL.get(m, 1) <= 3 for m in brands.makers_for("leather")), True)
    check("a big budget can", any(brands.PRICE_LEVEL.get(m, 1) == 4
                                  for m in brands.makers_for("leather", 3.0)), True)
    check("leather searches only name leather makers",
          all("leather" in brands.QUALITY_MAKERS[m]["good_at"] for m in brands.makers_for("leather", 3.0)), True)
    check("jeans always include a maker that states its fibres",
          brands.makers_for("women's jeans")[0] in brands.STATES_FIBRES, True)
    check("knock-off: luxury name, unknown shop",
          brands.looks_like_knockoff("TaylorJon", "Gucci Cruise Black Leather Jacket"), True)
    check("celebrity replica", brands.looks_like_knockoff("JacketHub", "Dua Lipa Leather Jacket"), True)
    check("the house itself is fine", brands.looks_like_knockoff("Gucci", "Gucci Leather Jacket"), False)
    check("an authorised retailer is fine", brands.looks_like_knockoff("SSENSE", "Prada Leather Jacket"), False)
    check("'the Rowan jacket' is not The Row", brands.looks_like_knockoff("Shop", "The Rowan Jacket"), False)
    check("Rossignol is not Ross", brands.is_department_store("Rossignol"), False)
    kw = dict(scanned_score=4.0, category="women's jacket", material="real leather", assumed_price=350)
    mill = {"title": "Women's Biker Jacket 100% Lambskin Leather", "extracted_price": 159, "source": "JacketMill"}
    check("a $159 leather jacket from an unknown shop is dropped", catalog.evaluate(mill, **kw), None)
    check("the same shop at $320 is kept",
          catalog.evaluate(dict(mill, extracted_price=320), **kw) is not None, True)
    check("a listed maker under $250 is kept",
          catalog.evaluate({"title": "Deadwood River Jacket 100% Leather", "extracted_price": 240,
                            "source": "Deadwood"}, **kw) is not None, True)
    check("The RealReal is allowed (v20)", brands.is_blocked("The RealReal"), False)
    check("other resale is still blocked", all(brands.is_blocked(s) for s in
          ("Depop", "Vinted", "Poshmark", "Vestiaire Collective", "eBay", "Grailed")), True)
    jq = brands.build_queries("women's jeans")
    check("jeans search two makers that state their fibres",
          sum(1 for q in jq if brands.maker_in(q) in brands.STATES_FIBRES) >= 2, True)
    check("never more than five searches", len(jq) <= 5, True)
    check("knock-offs never reach the list", catalog.evaluate(
        {"title": "Gucci Cruise Jacket 100% Leather", "extracted_price": 380, "source": "TaylorJon"}, **kw), None)


def test_v20_the_realreal():
    import brands, fund
    print("\n=== v20: The RealReal can be shown, labelled, capped, no points ===")
    kw = dict(price=300, scanned_score=4.0, category="women's sweater")
    trr = {"title": "Loulou Studio Cable Knit Sweater 100% Cashmere", "extracted_price": 132,
           "source": "The RealReal", "link": "https://www.therealreal.com/products/x"}
    got = catalog.evaluate(trr, **kw)
    check("a pre-owned piece that passes is kept, even far below the price floor", got is not None, True)
    check("...labelled pre-owned on the card", got["brand"], "Loulou Studio · Pre-owned")
    check("...and flagged", got["preowned"], True)
    check("pre-owned plastic is still dropped", catalog.evaluate(
        dict(trr, title="Loulou Studio Sweater 100% Acrylic"), **kw), None)
    check("pre-owned with no fibre stated is still dropped", catalog.evaluate(
        dict(trr, title="Loulou Studio Sweater"), **kw), None)
    check("a new piece is not flagged", catalog.evaluate(
        dict(trr, source="Loulou Studio", extracted_price=320), **kw)["preowned"], False)
    many = [dict(trr, title=f"Naadam Sweater {i} 100% Cashmere", link=f"t{i}") for i in range(2)] + \
           [dict(trr, title=f"Lisa Yang Sweater {i} 100% Cashmere", link=f"l{i}") for i in range(2)] + \
           [{"title": "Wool Sweater 100% Merino Wool", "extracted_price": 280, "source": "KnitCo", "link": "k"}]
    catalog._fetch = lambda q, num=40: many
    catalog.SERPAPI_KEY = "test"
    alts = catalog.search_alternatives("women's sweater", price=300, scanned_score=4.0)
    check("never more than two pre-owned", sum(1 for a in alts if a["preowned"]) <= catalog.MAX_PREOWNED, True)
    check("resale links are recognised for points", fund.brands.is_resale("https://www.therealreal.com/products/x"), True)
    check("an authenticated luxury piece at The RealReal is not a knock-off",
          brands.looks_like_knockoff("The RealReal", "Prada Wool Coat"), False)


def test_v21_breakdown():
    import breakdown
    def rows(c, k=None):
        r = fabric.analyze({"composition": c, "construction": k})
        return breakdown.build(c, k, r)
    r = rows("62% polyester 33% viscose 5% elastane")
    check("v21 polyester is a bad row", [x["side"] for x in r if "Polyester" in x["title"]], ["bad"])
    check("v21 small elastane is not held against it",
          [x["side"] for x in r if "Elastane" in x["title"]], ["good"])
    check("v21 stitching note when unchecked", any(x["side"] == "note" for x in r), True)
    r = rows("Shell: 100% leather. Lining: 100% polyester")
    check("v21 leather is good", r[0]["side"], "good")
    check("v21 plastic lining is bad",
          [x["side"] for x in r if "lining" in x["title"]], ["bad"])
    r = rows("100% linen", ["french_seams"])
    check("v21 seam proof shows", any(x["title"] == "French seams" for x in r), True)
    check("v21 no stitching note once checked", any(x["side"] == "note" for x in r), False)
    check("v21 unreadable tag gives no rows", rows("hello"), [])
    allrows = rows("50% wool 50% acrylic") + rows("100% cashmere") + rows("100% pu leather")
    text = " ".join(x["title"] + x["line"] + x["detail"] for x in allrows) + \
        " ".join(" ".join(v[1:]) for v in breakdown.FIBRES.values())
    check("v21 no dashes in the words", ("\u2014" in text) or ("\u2013" in text), False)
    check("v21 every scored fibre has notes",
          sorted(set(fabric.FIBER_QUALITY) - set(breakdown.FIBRES)), [])


def test_v24_colour_changes_the_list_and_hoodies():
    print("\n=== v24: a picked colour changes the list; hoodies and streetwear ===")
    import brands
    check("red is not in tailored", catalog.colour_match(
        {"title": "Tailored Wool Jacket 100% Wool"}, "red"), False)
    check("tan is not in standard", catalog.colour_match(
        {"title": "Standard Fit Jacket 100% Wool"}, "tan"), False)
    check("ivory counts as cream", catalog.colour_match(
        {"title": "Ivory Wool Jacket 100% Wool"}, "cream"), True)
    check("gray counts as grey", catalog.colour_match(
        {"title": "Gray Wool Sweater 100% Merino Wool"}, "grey"), True)

    qs = brands.build_queries("women's sweater", look=["white"], colour="white")
    check("colour is on all but one search", sum("white" in q for q in qs), len(qs) - 1)
    check("still never more than five", len(qs) <= 5, True)
    check("no colour, no change", brands.build_queries("women's sweater"),
          brands.build_queries("women's sweater", colour=None))
    lq = brands.build_queries("women's jacket", look=["cream"], material="real leather",
                              colour="cream")
    check("leather searches carry the colour too", sum("cream" in q for q in lq) >= len(lq) - 1, True)

    listings = [
        {"title": "Black Merino Sweater 100% Merino Wool", "source": "Shop A",
         "extracted_price": 120, "product_link": "https://a.example/1"},
        {"title": "Navy Merino Sweater 100% Merino Wool", "source": "Shop B",
         "extracted_price": 110, "product_link": "https://b.example/1"},
        {"title": "White Merino Sweater 100% Merino Wool", "source": "Shop C",
         "extracted_price": 300, "product_link": "https://c.example/1"},
        {"title": "Ivory Cashmere Sweater 100% Cashmere", "source": "Shop D",
         "extracted_price": 130, "product_link": "https://d.example/1"},
    ]
    real_fetch, real_key = catalog._fetch, catalog.SERPAPI_KEY
    catalog._fetch = lambda q, num=40: listings
    try:
        plain = catalog.search_alternatives(category="women's sweater", price=100,
                                            scanned_score=3.0)
        white = catalog.search_alternatives(category="women's sweater", price=100,
                                            scanned_score=3.0, look=["white"], colour="white")
        cream = catalog.search_alternatives(category="women's sweater", price=100,
                                            scanned_score=3.0, look=["cream"], colour="cream")
        red = catalog.search_alternatives(category="women's sweater", price=100,
                                          scanned_score=3.0, look=["red"], colour="red")
    finally:
        catalog._fetch, catalog.SERPAPI_KEY = real_fetch, real_key
    check("picking white puts the white one first", white[0]["name"].startswith("White"), True)
    check("picking cream puts the ivory one first", cream[0]["name"].startswith("Ivory"), True)
    check("picking a colour changes the order", [a["name"] for a in white] != [a["name"] for a in plain], True)
    check("white picked: only white ones are shown (v25)", [a["name"][:5] for a in white], ["White"])
    check("no red ones: still shows the others", len(red), len(plain))
    check("no red ones: says so", "red" in (catalog.colour_note(red, "red") or ""), True)
    check("white found: no note", catalog.colour_note(white, "white"), None)
    check("no dashes in the note", any(d in catalog.colour_note(red, "red") for d in "\u2014\u2013"), False)

    check("hoodie is its own garment", catalog.garment_group("women's hoodie"), "hoodie")
    check("a hoodie is answered with a hoodie", catalog.same_garment(
        "Stussy Stock Logo Hoodie", "women's hoodie"), True)
    check("...not a crewneck sweater", catalog.same_garment(
        "Merino Crewneck Sweater", "women's hoodie"), False)
    hq = brands.build_queries("women's hoodie")
    check("hoodies get brand searches", any(brands.maker_in(q) for q in hq), True)
    check("Stussy is searched for hoodies", "stussy" in brands.makers_for("women's hoodie")
          or len(brands.makers_for("women's hoodie")) == 6, True)
    check("St\u00fcssy is recognised with the umlaut", brands.maker_in("St\u00fcssy Basic Hoodie"), "stussy")
    check("a polyester Stussy hoodie is still dropped", catalog.evaluate(
        {"title": "Stussy Hoodie 100% Polyester", "source": "Stussy",
         "extracted_price": 120}, price=100, scanned_score=3.0,
        category="women's hoodie"), None)
    kept = catalog.evaluate(
        {"title": "Stussy Hoodie 100% Cotton", "source": "Stussy",
         "extracted_price": 120}, price=100, scanned_score=3.0,
        category="women's hoodie", relax=catalog.RELAX_ANY_PRICE)
    check("a cotton Stussy hoodie can be shown", kept is not None and kept["brand"], "St\u00fcssy")


def test_v25_colour_from_the_photo():
    print("\n=== v25: the colour comes from the photo; only that colour is shown ===")
    import io, base64, colour
    from PIL import Image, ImageDraw

    def photo(rgb):
        im = Image.new("RGB", (200, 200), (255, 255, 255))
        ImageDraw.Draw(im).rectangle([45, 25, 155, 180], fill=rgb)
        b = io.BytesIO(); im.save(b, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

    shades = {"black": (22, 22, 24), "white": (246, 245, 243), "cream": (238, 228, 205),
              "grey": (140, 140, 142), "navy": (28, 36, 70), "blue": (70, 110, 190),
              "brown": (92, 58, 36), "tan": (196, 160, 115), "green": (85, 107, 60),
              "red": (200, 30, 35), "burgundy": (105, 25, 40), "pink": (236, 170, 190)}
    wrong = []
    for name, rgb in shades.items():
        found = colour.shares(photo(rgb))
        if not colour.is_colour(found, name):
            wrong.append(name + " missed")
        wrong += [f"{name} read as {o}" for o in shades
                  if o != name and colour.is_colour(found, o)]
    check("every chip colour is read off a photo", wrong, [])
    check("an unreadable photo is 'could not tell'", colour.is_colour(colour.read_image(b"nope"), "white"), None)

    def listing(title, shop, rgb, price):
        return {"title": title, "source": shop, "extracted_price": price,
                "product_link": f"https://{shop.lower().replace(' ', '')}.example/p",
                "thumbnail": photo(rgb)}
    # No colour in any title, exactly like real shops.
    listings = [
        listing("Boxy Cardigan Sweater 100% Merino Wool", "Shop A", shades["black"], 120),
        listing("Cable Crewneck Sweater 100% Merino Wool", "Shop B", shades["navy"], 110),
        listing("Relaxed Crewneck Sweater 100% Merino Wool", "Shop C", shades["white"], 300),
        listing("Fine Knit Sweater 100% Cashmere", "Shop D", shades["cream"], 130),
    ]
    real = catalog._fetch
    catalog._fetch = lambda q, num=40: listings
    try:
        ask = lambda c: catalog.search_alternatives(
            category="women's sweater", price=100, scanned_score=3.0,
            look=[c] if c else None, colour=c)
        plain, white, cream, red = ask(None), ask("white"), ask("cream"), ask("red")
    finally:
        catalog._fetch = real
    check("no colour picked: all four", len(plain), 4)
    check("white picked: only the white one", [a["name"] for a in white], ["Relaxed Crewneck Sweater 100% Merino Wool"])
    check("cream picked: only the cream one", [a["name"] for a in cream], ["Fine Knit Sweater 100% Cashmere"])
    check("red picked, none red: the closest, all four", len(red), 4)
    check("...and it says so", "red" in (catalog.colour_note(red, "red") or ""), True)
    check("a colour match still passed the quality rules", all(a["score"] >= catalog.QUALITY_FLOOR for a in white), True)

    # A title that lists other colours must not beat the photo.
    lists = [listing("Hoodie Sweater in black grey or red 100% Merino Wool", "Shop R", shades["red"], 120),
             listing("Plain Sweater 100% Merino Wool", "Shop G", shades["grey"], 120)]
    catalog._fetch = lambda q, num=40: lists
    try:
        grey = catalog.search_alternatives(category="women's sweater", price=100,
                                           scanned_score=3.0, look=["grey"], colour="grey")
    finally:
        catalog._fetch = real
    check("a red piece whose title says grey is not grey", [a["retailer"] for a in grey], ["Shop G"])
    ev = lambda **kw: catalog.evaluate(dict({"source": "Shop Z", "extracted_price": 90}, **kw),
                                       price=100, scanned_score=3.0, category="women's shirt",
                                       department="women", relax=catalog.RELAX_ANY_PRICE)
    check("menswear named only in the description is dropped",
          ev(title="Oxford Shirt 100% Cotton", snippet="A men's classic fit shirt"), None)
    check("a women's description is kept",
          ev(title="Oxford Shirt 100% Cotton", snippet="Women's relaxed fit") is not None, True)
    check("no department anywhere is kept", ev(title="Oxford Shirt 100% Cotton") is not None, True)
    check("a Macy's house label via an aggregator is dropped",
          ev(title="Women's 100% Linen Shirt, Created For Macy's", source="ModeSens"), None)

    check("'Man Sweatshirt' is menswear", catalog.department_of("Colorful Standard Man Sweatshirt"), "men")
    check("wholesale lots are dropped", catalog.evaluate(
        {"title": "6 Bulk Women's Cotton Oversized Hoodie 100% Cotton", "source": "Shop Z",
         "extracted_price": 90}, price=100, scanned_score=3.0, category="women's hoodie",
        relax=catalog.RELAX_ANY_PRICE), None)
    check("a normal hoodie is not", catalog.evaluate(
        {"title": "Women's Oversized Hoodie 100% Cotton", "source": "Shop Z",
         "extracted_price": 90}, price=100, scanned_score=3.0, category="women's hoodie",
        relax=catalog.RELAX_ANY_PRICE) is not None, True)


def test_v30_fair_price():
    """Quality and price in one answer. The fair price is built from what a
    piece is made of and how and where it's made, never from its brand."""
    import json, value, aggregates
    print("\n=== v30: fair price ===")

    def fair(comp, cat, price=None, country="US", tag=None, cons=None):
        d = fabric.quality_detail(comp, construction=cons)
        return value.assess({"composition": comp, "category": cat, "price": price,
                             "country": country, "tag_text": tag}, d, d["score"])

    # The bank: every source id resolves, every number says what kind it is.
    bank = json.load(open("pricebank.json"))
    ids, missing, unkinded = set(bank["sources"]), [], []

    def walk(o, path=""):
        if isinstance(o, dict):
            if "src" in o:
                for x in (o["src"] if isinstance(o["src"], list) else [o["src"]]):
                    missing.extend(y for y in x.split("|") if y not in ids)
                if "kind" not in o:
                    unkinded.append(path)
            for k, v in o.items():
                if k != "sources":
                    walk(v, path + "/" + k)
    walk(bank)
    check("every source id in the bank resolves", missing, [])
    check("every sourced number says sourced/derived/judgement", unkinded, [])

    # Quality never reads price: same piece, same score at any price.
    a, b = fair("100% cashmere", "sweater", 80), fair("100% cashmere", "sweater", 900)
    check("price never changes the score",
          fabric.quality_detail("100% cashmere")["score"], 7.8)
    check("...but it changes the value verdict", a["level"] != b["level"], True)

    # No price: still a range, and the 1.x prompt says it.
    w = fair("100% wool", "sweater", None, "CA")
    check("no price still gives a fair range", w["fair_low"] > 0 and w["fair_high"] > w["fair_low"], True)
    check("...in CAD for Canada", w["currency"], "CAD")
    check("...and no verdict", w["level"], None)
    check("...said in plain words", w["range_line"].startswith("A fair price for a 100% wool sweater"), True)

    # The five outcomes.
    check("$180 polyester tee is overpriced", fair("100% polyester", "t-shirt", 180)["level"], value.OVERPRICED)
    check("$12 cotton tee is worth it", fair("100% cotton", "t-shirt", 12)["level"], value.WORTH_IT)
    check("$5 polyester tee: fair for what it is",
          fair("100% polyester", "t-shirt", 5)["level"], value.FAIR_FOR_WHAT)
    check("$40 faux leather jacket: you get what you pay for",
          fair("100% polyurethane", "jacket", 40)["level"], value.GET_WHAT_YOU_PAY)
    check("$400 faux leather jacket: overpriced",
          fair("100% polyurethane", "jacket", 400)["level"], value.OVERPRICED)
    check("Supima from Portugal at $25 is a steal",
          fair("100% Supima cotton 220gsm", "t-shirt", 25, tag="Made in Portugal")["level"], value.STEAL)
    check("$1,500 cashmere: good piece, high price",
          fair("100% cashmere", "sweater", 1500)["level"], value.HIGH_PRICE)

    # v28.1's lesson: an ordinary $30 cotton tee is not called a rip-off. With the
    # grade and country unknown, "high" is judged against the generous reading.
    check("a $30 cotton tee is not flagged",
          fair("100% cotton", "t-shirt", 30)["level"] in (value.WORTH_IT, value.FAIR_FOR_WHAT), True)
    above = fair("100% wool", "sweater", 120)
    check("above the usual range but within the unknowns says both",
          ("could fairly cost up to" in above["line"], above["level"]), (True, value.FAIR_FOR_WHAT))

    # A low price rules grades out; it never proves the piece is good.
    check("$50 cashmere, grade unknown: check the grade",
          fair("100% cashmere", "sweater", 50)["level"], value.CHECK_GRADE)
    check("$20 cashmere: less than the cashmere itself costs",
          fair("100% cashmere", "sweater", 20)["level"], value.TOO_CHEAP)
    ga = fair("100% cashmere grade A", "sweater", None, tag="Made in Italy")
    gc = fair("100% cashmere", "sweater", None)
    check("grade A made in Italy is priced above unknown cashmere", ga["fair_low"] > gc["fair_low"], True)

    # What's known narrows; what isn't widens.
    known = fair("100% wool", "coat", None, tag="Made in Italy")
    unknown = fair("100% wool", "coat", None)
    check("Italian making raises the fair price", known["fair_low"] > unknown["fair_low"], True)
    check("unknown country is reported", any(u["key"] == "country" for u in unknown["unknowns"]), True)
    check("...and widens the could-be range",
          unknown["could_be_high"] > unknown["fair_high"], True)
    check("a stated weight raises the fair price",
          fair("100% cotton 260gsm", "t-shirt")["fair_high"] > fair("100% cotton", "t-shirt")["fair_high"], True)

    # The Row vs Etsy: both real lambskin. Only what is SEEN separates them.
    row = "Shell: 100% lambskin leather Lining: 100% calfskin"
    edge = fair(row, "shoulder bag", 1700, tag="Made in Italy. Nappa")
    plain = fair(row, "shoulder bag", 1850, tag="Made in Italy. Nappa")
    seen = fair(row, "shoulder bag", 1850, tag="Made in Italy. Nappa",
                cons=["turned_edges", "saddle_stitched"])
    check("Italian lambskin bag at $1,700: at the top of a fair price",
          (edge["level"], edge["line"].startswith("At the top")), (value.WORTH_IT, True))
    etsy = fair("100% lambskin leather", "shoulder bag", 140, tag="Made in China", cons=["raw_edges"])
    check("bags are priced", plain is not None and plain["fair_high"] > 0, True)
    check("...at $1,850, make unseen: some of it is the name",
          plain["level"], value.HIGH_PRICE)
    check("...with turned edges and saddle stitching seen: worth it", seen["level"], value.WORTH_IT)
    check("seen make raises the fair price", seen["fair_low"] > plain["fair_low"], True)
    check("raw-edged $140 copy: you get what you pay for", etsy["level"], value.GET_WHAT_YOU_PAY)
    check("leather lining is counted as leather",
          any("leather lining" in x for x in plain["built_from"]), True)
    check("the brand never appears in a line",
          any("The Row" in (v["line"] or "") for v in (plain, seen)), False)

    # Same make, same fair price, whatever the name on the listing.
    n1 = fair("100% wool", "coat", 400, tag="Saint Laurent. Made in Italy")
    n2 = fair("100% wool", "coat", 400, tag="No-name label. Made in Italy")
    check("the brand name never moves the fair price", (n1["fair_low"], n1["fair_high"]),
          (n2["fair_low"], n2["fair_high"]))

    # House words: no dashes in anything a shopper reads.
    lines = [fair(c, g, p)[k] for c, g, p in (("100% wool", "coat", 900), ("100% polyester", "t-shirt", 180),
                                               ("100% cashmere", "sweater", 50), ("100% linen", "shirt", None))
             for k in ("line", "range_line")]
    check("no dashes in shopper copy", [l for l in lines if l and ("\u2014" in l or "\u2013" in l)], [])

    # Never blocks a verdict.
    check("nothing to price returns None", fair("100% polyester", "umbrella"), None)
    check("a broken input returns None, never raises",
          value.assess({"composition": None}, {"matched": None}, 5), None)

    # Shopper prices: only over the privacy floor, and never the rows themselves.
    rows = [(f"s{i}", "sweater", "100% wool", "CA", 7.9, "80-120") for i in range(aggregates.K_ANONYMITY_FLOOR)]
    cells = aggregates.price_cells(rows=rows)
    check("a full cell is reported", cells.get(("sweater", "wool", "good", "CA"), {}).get("median"), 100)
    small = aggregates.price_cells(rows=rows[:-1])
    check("one shopper under the floor: nothing", small, {})
    check("no session ever leaves", "s0" in json.dumps(list(cells.values())), False)

    # Options: each gets a fair price read, better value first among equals.
    import main
    alts = [{"name": "A", "price": 900, "score": 7.9, "composition": "100% cashmere", "fit": "better"},
            {"name": "B", "price": 300, "score": 7.9, "composition": "100% cashmere", "fit": "better"},
            {"name": "C", "price": 200, "score": 7.9, "composition": "100% cashmere", "fit": "pricier"}]
    main._value_alternatives(alts, {"category": "sweater"}, "US")
    check("options carry a fair price", all("fair_price" in a for a in alts), True)
    check("better value first within the same fit", [a["name"] for a in alts], ["B", "A", "C"])

    # v30.2: the fair price on the verdict page, generic, from the fabric.
    d = fabric.quality_detail("64% polyester 36% cotton")
    rows = value.breakdown_rows({"composition": "64% polyester 36% cotton", "country": "CA"},
                                d, d["score"], None)
    check("no category: one 'what it should cost' row", [r["side"] for r in rows], ["price"])
    check("...naming a few common pieces", all(w in rows[0]["line"] for w in ("a top", "a dress", "a coat")), True)
    check("...and pointing to the price check", "better made" in rows[0]["detail"], True)
    it = {"composition": "100% wool", "category": "sweater", "country": "CA"}
    d = fabric.quality_detail("100% wool")
    rows = value.breakdown_rows(it, d, d["score"], value.assess(it, d, d["score"]))
    check("with a category: the fair price for this piece",
          rows[0]["line"].startswith("About $") and "100% wool sweater" in rows[0]["line"], True)
    # v30.7: once a price is added, the same row becomes the verdict.
    it2 = dict(it, price=90)
    v2 = value.assess(it2, d, d["score"])
    r2 = value.breakdown_rows(it2, d, d["score"], v2)[0]
    check("with a price the row is the verdict", (r2["title"], r2["level"]), (v2["label"], "good"))
    check("...in one short line", r2["line"].startswith("$90") and len(r2["line"]) < 70, True)
    check("...with the fair range one tap down", "A fair price for this make is about" in r2["detail"], True)
    it3 = {"composition": "64% cotton 36% polyester", "price": 40}
    d3 = fabric.quality_detail(it3["composition"])
    r3 = value.breakdown_rows(it3, d3, d3["score"], value.assess(it3, d3, d3["score"]))[0]
    check("a price with no category asks what it is", "Pick what it is to check your $40" in r3["line"], True)
    leather = fabric.quality_detail("100% lambskin leather")
    rows = value.breakdown_rows({"composition": "100% lambskin leather"}, leather, leather["score"], None)
    check("leather names a bag and a jacket", ("a bag" in rows[0]["line"], "a jacket" in rows[0]["line"]), (True, True))
    from fastapi.testclient import TestClient
    r = TestClient(main.app).post("/analyze", json={"item": {"composition": "64% polyester 36% cotton",
                                                             "mode": "score"}}).json()
    # v37: no fabric-cost price row any more; the row comes only with a price.
    check("no price, no price row", any(x["side"] == "price" for x in r["breakdown"]), False)


def test_v30_3_every_fibre_on_the_tag():
    """A blend is read as a blend, whatever language or layout the tag uses.
    One fibre must never take another's share."""
    import value
    print("\n=== v30.3: every fibre on the tag ===")
    want = [("cotton", 64), ("polyester", 36)]
    for tag in ["64% cotton 36% polyester", "64% coton 36% polyester",
                "64% algodón 36% poliéster", "64% BAUMWOLLE 36% POLYESTER",
                "64% cotone 36% poliestere", "64% C0TTON 36% POLYESTER",
                "64%cotton36%polyester", "64%\n36%\nCOTTON\nPOLYESTER", "64% CO 36% PES",
                "COTON 64% POLYESTER 36%",
                "64% COTTON 36% POLYESTER\n64% COTON 36% POLYESTER\n64% ALGODÓN 36% POLIÉSTER"]:
        check(f"reads {tag!r}", fabric.parse_composition(tag), want)
    check("an unknown word never hands its share to the next fibre",
          fabric.parse_composition("64% xyzfibre 36% polyester"), [("polyester", 36)])
    r = fabric.analyze({"composition": "64% xyzfibre 36% polyester"})
    check("...and the unread share is said", r["unread_pct"], 64)
    check("fully read tags have nothing unread", fabric.analyze({"composition": "64% cotton 36% polyester"})["unread_pct"], None)
    check("a lone fibre is still the whole piece", fabric.parse_composition("cotton"), [("cotton", 100)])
    check("a French lining is a lining",
          fabric.split_sections("Extérieur: 100% laine Doublure: 100% polyester")[1].strip(), "100% polyester")
    check("'lining' is not read as linen", fabric.parse_composition("Lining: 100% polyester"), [("polyester", 100)])
    check("a cotton blend scores as a blend",
          fabric.quality_detail("64% coton 36% polyester")["score"],
          fabric.quality_detail("64% cotton 36% polyester")["score"])
    d = fabric.quality_detail("64% cotton 36% polyester")
    row = value.breakdown_rows({"composition": "64% cotton 36% polyester"}, d, d["score"], None)[0]
    check("the fair price line names the whole make", "64% cotton, 36% polyester" in row["line"], True)

    import main
    check("a partial app read is corrected by the whole tag",
          main._best_composition("64% Polyester", "64% COTTON / COTON 36% POLYESTER MADE IN CHINA"),
          "64% cotton, 36% polyester")
    check("a complete app read is kept",
          main._best_composition("64% Cotton, 36% Polyester", "junk 100% wool"), "64% Cotton, 36% Polyester")
    check("the tag's lining comes along",
          main._best_composition("80% Wool", "Shell: 80% wool 20% polyamide Lining: 100% polyester"),
          "80% wool, 20% polyamide Lining: 100% polyester")
    from fastapi.testclient import TestClient
    r = TestClient(main.app).post("/analyze", json={"item": {
        "composition": "64% Polyester", "tag_text": "64% COTTON 36% POLYESTER", "mode": "score"}}).json()
    check("...and the verdict is the blend's", (round(r["score"], 1), r["composition_read"]),
          (6.1, "64% cotton, 36% polyester"))


def test_v30_5_more_types_and_true_colour():
    import catalog, colour, value
    print("\n=== v30.5: sweatpants, long sleeve, tank, bag; black means black ===")
    for typed, group in [("women's sweatpants", "sweatpants"), ("joggers", "sweatpants"),
                         ("women's long sleeve", "long-sleeve"), ("women's tank", "tank"),
                         ("bag", "bag"), ("women's trousers", "trousers"), ("women's t-shirt", "t-shirt")]:
        check(f"{typed!r} is {group}", catalog.garment_group(typed), group)
    check("joggers come back for sweatpants", catalog.same_garment("Organic Cotton Joggers", "sweatpants"), True)
    check("wool trousers don't", catalog.same_garment("Wool Trousers", "sweatpants"), False)
    check("a long sleeve tee is a long sleeve", catalog.same_garment("Long Sleeve Tee", "long sleeve"), True)
    check("a hoodie isn't", catalog.same_garment("Long Sleeve Hoodie", "long sleeve"), False)
    d = fabric.quality_detail("80% cotton 20% polyester")
    for c in ("women's sweatpants", "women's long sleeve", "women's tank"):
        v = value.assess({"composition": "80% cotton 20% polyester", "category": c}, d, d["score"])
        check(f"{c} gets a fair price", v is not None and v["fair_high"] > v["fair_low"], True)
    check("a navy-titled piece is not black", catalog.title_names_other_colour({"name": "Navy Cotton Tee"}, "black"), True)
    check("a black one is", catalog.title_names_other_colour({"name": "Black Cotton Tee"}, "black"), False)
    check("an untitled colour defers to the photo", catalog.title_names_other_colour({"name": "Organic Tee"}, "black"), False)
    check("deep navy pixels read navy", colour.name_pixel(20, 25, 60), "navy")
    import main, vision
    from fastapi.testclient import TestClient
    cfg = TestClient(main.app).get("/config").json()
    check("/config sends the category chips", "Sweatpants" in cfg["categories"], True)
    check("every colour chip is one the search knows",
          [c["name"] for c in cfg["colors"]
           if c["name"].lower() not in vision.COLOR and c["name"].lower() not in main.EXTRA_LOOK_PICKS], [])
    check("every category chip resolves to a type the search knows",
          [c for c in cfg["categories"] if c not in ("Knitwear", "Activewear")
           and catalog.garment_group(c) is None and value.group_for(c) is None], [])
    r = TestClient(main.app).post("/analyze", json={"item": {"composition": "100% wool", "mode": "score"}}).json()
    check("each scan says which sections to show", [x["side"] for x in r["breakdown_sections"]],
          ["bad", "good", "note", "price"])
    check("black pixels read black", colour.name_pixel(15, 15, 18), "black")


def test_v30_8_the_store():
    """A mall-store price is judged against the store as well as the make."""
    import market, value, catalog
    print("\n=== v30.8: what this store usually charges ===")
    styles = ["Ribbed", "Mock Neck", "Fitted", "Boxy", "Scoop", "Wrap", "Henley",
              "Square Neck", "Cropped", "Draped"]
    listings = [{"title": f"Oak + Fort {st} Long Sleeve Top", "source": "Oak + Fort",
                 "extracted_price": p, "product_link": f"https://x/{i}"}
                for i, (st, p) in enumerate(zip(styles, [48, 52, 55, 58, 60, 65, 70, 78, 85, 98]))]
    listings.append({"title": "Random Long Sleeve Top", "source": "Other Shop",
                     "extracted_price": 12, "product_link": "https://y"})
    market._store_cache.clear()
    b = market.store_benchmark("Oak + Fort", "long-sleeve", "women",
                               fetch=lambda q, num=60: listings, country="CA")
    check("the store's own listings only", b["listings"], 10)
    check("...and its usual range", (b["p25"] >= 50, b["p75"] <= 85), (True, True))
    d = fabric.quality_detail("95% polyester 5% elastane")
    it = {"composition": "95% polyester 5% elastane", "category": "women's long sleeve",
          "price": 70, "country": "CA", "brand": "Oak + Fort"}
    v = value.assess(it, d, d["score"], store_check=b)
    check("v30.9: the store never softens the verdict", v["level"] in (value.HIGH_FOR_WHAT, value.OVERPRICED), True)
    check("...it's context, without naming the store", ("store_line" in v, "Oak" in v.get("store_line", "")), (True, False))
    v2 = value.assess(dict(it, price=100), d, d["score"], store_check=b)
    check("$100 polyester long sleeve: overpriced", v2["level"], value.OVERPRICED)
    check("...with the markup as a fact", (v2["markup_x"] >= 8, "times what it likely costs to make" in v2["markup_line"]), (True, True))
    v3 = value.assess(dict(it, price=20), d, d["score"], store_check=b)
    check("a fair price is still fair", v3["level"] in (value.FAIR_FOR_WHAT, value.WORTH_IT), True)
    check("no brand, no store check", market.store_benchmark("", "long-sleeve"), None)
    row = value.breakdown_rows(dict(it, price=100), d, d["score"], v2)[0]
    check("the row is just the markup", (row["title"], row["line"]),
          (value.LABELS[value.OVERPRICED], f"$100 is about {v2['markup_x']} times what it likely costs to make."))
    check("...the store's prices are one tap down", "This store usually charges" in row["detail"], True)
    # Better made options: cheaper than an overpriced piece is the point.
    item = {"title": "Organic Cotton Long Sleeve Tee 100% cotton", "source": "Kotn",
            "extracted_price": 45, "product_link": "https://k"}
    check("a $45 option for a $100 top passes once the floor follows the fair price",
          catalog.evaluate(item, price=100, scanned_score=4.6, category="long sleeve", floor_base=30) is not None, True)
    check("...and was dropped before", catalog.evaluate(item, price=100, scanned_score=4.6, category="long sleeve"), None)
    check("a colour that couldn't be found is said first",
          catalog.colour_note([{"colour_match": False, "fit": "pricier"}], "black").startswith("We could not find a black"), True)


def test_v31_shopper_voices():
    """One-tap notes and wear checks: a floor, plain words, never a broken scan."""
    import voices
    print("\n=== v31: what Filo shoppers say ===")
    key = voices.piece_key("100% Polyester", "women's top", "Oak + Fort")
    check("filed under brand, garment, fibre", key, ("oak + fort", "top", "polyester"))
    check("bare composition still files", voices.piece_key("100% cotton", None, None)[2], "cotton")

    def rows(kind, answer, n, days=None, brand="oak + fort", start=0):
        return [(kind, answer, days, f"s{start + i}", brand, "top", "polyester") for i in range(n)]

    few = voices.build_cells(rows("note", "thin", voices.NOTE_FLOOR - 1))
    check("under the floor says nothing", voices.lines_for(few, "oak + fort", "top", "polyester"), [])

    cells = voices.build_cells(rows("note", "thin", 4) + rows("note", "runs_small", 1, start=4)
                               + rows("note", "soft", 1, start=5))
    check("the note most shoppers chose", voices.lines_for(cells, "oak + fort", "top", "polyester"),
          ["Shoppers noticed: it feels thin."])

    # Another brand's polyester tops count toward "polyester tops" in general.
    other = voices.build_cells(rows("note", "thin", 6, brand="other"))
    check("falls back to the kind of piece", voices.lines_for(other, "new brand", "top", "polyester"),
          ["Shoppers noticed: it feels thin."])

    wear = voices.build_cells(rows("wear", "like_new", 5, days=30) + rows("wear", "some_wear", 1, days=30, start=5))
    check("still new after a month", voices.lines_for(wear, "oak + fort", "top", "polyester"),
          ["Filo shoppers say pieces like this still look new after a month."])
    bad = voices.build_cells(rows("wear", "fell_apart", 2, days=90) + rows("wear", "like_new", 3, days=90, start=2))
    check("fell apart is said plainly", voices.lines_for(bad, "oak + fort", "top", "polyester"),
          ["Some Filo shoppers say pieces like this fell apart within three months."])

    check("unknown note refused", voices.record_note(voices.NoteIn(session_id="abcdefgh", note="ugly")), False)
    check("no database, no crash", voices.shopper_lines("100% polyester", "top", "x"), [])
    lines = " ".join(l for c in (cells, wear, bad) for l in voices.lines_for(c, "oak + fort", "top", "polyester"))
    check("no dashes in what shoppers read", "\u2014" in lines or "\u2013" in lines, False)
    q = voices.quotes_for(wear, "oak + fort", "top", "polyester")
    check("quote with how many said it", (q[0]["quote"], q[0]["who"]),
          ("Still looks new after a month.", "6 Filo shoppers with a piece like this"))
    check("note quote", voices.quotes_for(cells, "oak + fort", "top", "polyester")[0]["quote"], "Feels thin.")
    check("no quotes under the floor", voices.quotes_for(few, "oak + fort", "top", "polyester"), [])
    check("chips go to the app", [c["label"] for c in voices.config()["note_chips"]][:2],
          ["Feels thin", "Nice weight"])


def _sample_bank_product():
    """A draft record shaped like the Colorful Standard tee in the Stage 2 test set."""
    return {
        "product_id": "colorful-standard-organic-oversized-crew-raspberry",
        "brand_id": "colorful-standard",
        "name": "Organic Oversized Crew",
        "category": "t_shirt",
        "gender_target": "women",
        "product_url": "https://colorfulstandard.com/products/organic-oversized-crew-raspberry-pink-female",
        "variants": [{"variant_id": "v1", "colour": "Raspberry Pink", "size_label": "M",
                      "availability": "unknown", "last_checked": "2026-10-20T20:00:00Z"}],
        "materials": {
            "composition": {"value": [{"fibre": "cotton", "percent": 100}], "status": "confirmed",
                            "source_type": "brand_reported", "evidence_ids": ["ev1"], "scope": "this_style"},
            "fabric_weight": {"value": None, "status": "unknown"},
        },
        "construction": {
            "country_of_assembly": {"value": "Portugal", "status": "confirmed", "source_type": "brand_reported",
                                    "evidence_ids": ["ev2"], "scope": "this_style"},
        },
        "fit": {"fit_tags": [{"tag": "oversized", "fact": {"value": True, "status": "confirmed",
                 "source_type": "brand_reported", "evidence_ids": ["ev3"], "scope": "this_style"}}]},
        "status": "draft",
        "first_seen": "2026-10-20T20:00:00Z",
        "last_checked": "2026-10-20T20:00:00Z",
    }


def test_bank_stage1():
    import copy, json, os
    import bank, bank_seed, bank_validate
    print("\n=== bank stage 1: tables, starter data and the validator ===")

    check("brand ids are stable", [bank.slug(n) for n in ("Babaà", "&Daughter", "Jamieson's of Shetland")],
          ["babaa", "and-daughter", "jamieson-s-of-shetland"])
    rows = bank_seed.brand_rows()
    check("544 starter brands", len(rows), 544)
    check("brand ids are unique", len({r["brand_id"] for r in rows}), 544)
    check("exactly 23 approved", sum(r["approved"] for r in rows), 23)
    check("no brand url is marked verified yet", any(r["url_verified"] for r in rows), False)
    check("people tree is marked remove", [r["research_status"] for r in rows if r["name"] == "People Tree"], ["remove"])
    check("the realreal is a resale source", [r["research_status"] for r in rows if r["name"] == "The RealReal"],
          ["resale_source"])
    concepts, phrases, relations = bank_seed.ontology_rows()
    check("ontology concepts load", len(concepts) >= 60, True)
    check("heavyweight does not prove opaque",
          ("heavyweight", "does_not_prove", "opaque") in {(f, t, json.dumps(v).strip('"')) for f, t, v, _ in relations}, True)
    check("French phrases for not see-through",
          ("opaque", "fr", "opaque") in phrases, True)
    check("stylist launches in English only",
          json.load(open(os.path.join(bank_seed.DATA, "stylist_config.json")))["stylist"]["launch_languages"], ["en"])

    check("schemas are valid", bank_validate.schemas_ok(), True)
    good = _sample_bank_product()
    check("a good draft passes", bank_validate.validate_product(good), [])
    check("a draft with no price is still storable", "price" in good["variants"][0], False)
    check("evidence must exist when checked",
          any("evidence not found" in p for p in bank_validate.validate_product(good, ["ev1", "ev2"])), True)
    bad = copy.deepcopy(good); bad["materials"]["composition"]["evidence_ids"] = []
    check("confirmed with no evidence is refused",
          any("needs at least one piece of evidence" in p for p in bank_validate.validate_product(bad)), True)
    bad = copy.deepcopy(good); bad["materials"]["fabric_weight"] = {"value": 220, "status": "unknown"}
    check("unknown with a value is refused", bank_validate.validate_product(bad) != [], True)
    bad = copy.deepcopy(good)
    bad["materials"]["composition"].update(status="from_photo", source_type="inferred")
    check("material from a photo is refused",
          any("can't come from a photo" in p for p in bank_validate.validate_product(bad)), True)
    bad = copy.deepcopy(good); bad["fit"]["fit_tags"][0]["fact"].update(status="from_photo")
    check("photo facts must be inferred",
          any("must be 'inferred'" in p for p in bank_validate.validate_product(bad)), True)
    bad = copy.deepcopy(good); bad["construction"]["country_of_assembly"]["scope"] = "brand_wide"
    check("brand-wide evidence can't confirm a product fact",
          any("brand-wide" in p for p in bank_validate.validate_product(bad)), True)
    ev = {"evidence_id": "ev1", "claim": "Composition is 100% organic cotton", "subject_type": "product",
          "subject_id": good["product_id"], "source_url": good["product_url"], "source_kind": "brand_product_page",
          "source_type": "brand_reported", "quote": "100% Organic Cotton", "scope": "this_style",
          "confidence": 0.7, "checked_at": "2026-10-20T20:00:00Z", "status": "current"}
    check("a good evidence entry passes", bank_validate.validate_evidence(ev), [])
    check("shopper evidence needs a sample size",
          bank_validate.validate_evidence({**ev, "source_type": "customer_reported"}) != [], True)
    check("a product page can't be called verified",
          bank_validate.validate_evidence({**ev, "source_type": "verified"}) != [], True)
    import events
    saved, events.DATABASE_URL = events.DATABASE_URL, ""
    check("no database, no crash", (bank.init_schema(), bank_seed.seed(), bank.counts()), (False, {}, {}))
    events.DATABASE_URL = saved


def test_bank_stage1_postgres():
    import bank, bank_seed, events
    print("\n=== bank stage 1 on a real Postgres ===")
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(bank.TABLES) + " CASCADE")
    conn.close()
    check("tables create", bank.init_schema(), True)
    check("...and creating again is safe", bank.init_schema(), True)
    out = bank_seed.seed()
    check("seed loads brands", out.get("brands"), 544)
    c = bank.counts()
    check("every bank table exists", sorted(c), sorted(bank.TABLES))
    check("brands stored", c["bank_brands"], 544)
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bank_brands WHERE approved")
        check("23 approved in the database", cur.fetchone()[0], 23)
        cur.execute("UPDATE bank_brands SET approved = TRUE, studio_note = 'Brooklyn liked it' WHERE brand_id = 'sezane'")
    conn.close()
    bank_seed.seed()
    conn = events._conn()
    with conn.cursor() as cur:
        cur.execute("SELECT approved, studio_note FROM bank_brands WHERE brand_id = 'sezane'")
        check("reseeding keeps Studio changes", cur.fetchone(), (True, "Brooklyn liked it"))
        cur.execute("SELECT count(*) FROM bank_concept_phrases")
        n = cur.fetchone()[0]
    conn.close()
    bank_seed.seed()
    check("reseeding adds no duplicate phrases", bank.counts()["bank_concept_phrases"], n)

    def refused(sql, args=()):
        conn = events._conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, args)
            return False
        except Exception:
            return True
        finally:
            conn.close()
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO bank_products (product_id, brand_id, name, category, product_url, record)
                       VALUES ('p1', 'colorful-standard', 'Organic Oversized Crew', 't_shirt', 'https://x', '{}')""")
    conn.close()
    check("database refuses a confirmed fact with no evidence", refused(
        "INSERT INTO bank_facts (product_id, product_version, field, value, status, source_type) "
        "VALUES ('p1', 1, 'materials.composition', '\"cotton\"', 'confirmed', 'brand_reported')"), True)
    check("database refuses a photo fact that isn't inferred", refused(
        "INSERT INTO bank_facts (product_id, product_version, field, value, status, source_type, evidence_ids) "
        "VALUES ('p1', 1, 'fit.cropped', 'true', 'from_photo', 'brand_reported', '{ev9}')"), True)
    check("database accepts an unknown fact", refused(
        "INSERT INTO bank_facts (product_id, product_version, field, status) "
        "VALUES ('p1', 1, 'materials.fabric_weight', 'unknown')"), False)
    check("database refuses shopper evidence with no sample size", refused(
        "INSERT INTO bank_evidence (evidence_id, claim, subject_type, subject_id, source_url, source_type, "
        "scope, confidence, checked_at) VALUES ('e1', 'not sheer', 'product', 'p1', 'https://x', "
        "'customer_reported', 'this_style', 0.5, now())"), True)
    check("database refuses a price with no currency", refused(
        "INSERT INTO bank_variants (variant_id, product_id, colour, size_label, price, last_checked) "
        "VALUES ('v1', 'p1', 'Pink', 'M', 50, now())"), True)
    check("database accepts a draft with no price", refused(
        "INSERT INTO bank_variants (variant_id, product_id, colour, size_label, last_checked) "
        "VALUES ('v2', 'p1', 'Pink', 'M', now())"), False)

    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO stylist_hunts (hunt_id, device_id, user_id, brief_text, brief_structured, created_at) "
                    "VALUES ('h-old', 'd1', 'u1', 'boxy white tee', '{\"category\": \"t_shirt\"}', now() - interval '91 days'), "
                    "('h-new', 'd1', 'u2', 'black jeans', '{}', now())")
        cur.execute("INSERT INTO stylist_actions (hunt_id, product_id, action) VALUES ('h-old', 'p1', 'heart')")
        cur.execute("INSERT INTO fit_preferences (owner_id, prefs) VALUES ('u1', '{\"room_through_bust\": true}')")
    conn.close()
    check("old brief text is cleared", bank.purge_old_brief_text(), 1)
    conn = events._conn()
    with conn.cursor() as cur:
        cur.execute("SELECT brief_text, brief_structured FROM stylist_hunts WHERE hunt_id = 'h-old'")
        check("...but the structured brief stays", cur.fetchone(), (None, {"category": "t_shirt"}))
    conn.close()
    check("deleting an account removes its hunts", bank.delete_for_user("u1"), 1)
    c = bank.counts()
    check("...with their actions and fit preferences", (c["stylist_actions"], c["fit_preferences"], c["stylist_hunts"]),
          (0, 0, 1))


BANK_FIXTURES = {
    "https://www.altitude-sports.com/p/colorful-standard-organic-oversized-tshirt-unisex-cfs-cs2056": "tee_altitude.html",
    "https://sirgordonbennett.com/products/ladies-waffle-crew-neck-jumper-skiddaw": "knit_sgb.html",
    "https://sirgordonbennett.com/products/ladies-waffle-crew-neck-jumper-skiddaw.js": "knit_sgb.js",
    "https://www.altitude-sports.com/p/nudie-jeans-clean-eileen-jeans-womens-ndj-114679": "jeans_altitude.html",
    "https://solefeed.com/shop/community-clothing/womens-work-lightweight-trousers-olive-e8d8tk": "trousers_solefeed.html",
    "https://finisterre.com/en-ca/products/womens-recycled-polyester-firecrest-jacket-burnt-umber": "jacket_finisterre.html",
    "https://finisterre.com/en-ca/products/womens-recycled-polyester-firecrest-jacket-burnt-umber.js": "jacket_finisterre.js",
}
BANK_URL = {k: u for k, u in zip(("tee", "knit", "_knitjs", "jeans", "trousers", "jacket", "_jacketjs"), BANK_FIXTURES)}


def _bank_fetcher(overrides=None, fail=()):
    import os, bank_fetch
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures", "bank")

    def fetch(url):
        if url in fail:
            return bank_fetch.FetchResult(False, url, 503, error="HTTP 503")
        if overrides and url in overrides:
            t = overrides[url]
        elif url in BANK_FIXTURES:
            t = open(os.path.join(here, BANK_FIXTURES[url]), encoding="utf-8").read()
        else:
            return bank_fetch.FetchResult(False, url, 404, error="HTTP 404")
        return bank_fetch.FetchResult(True, url, 200, t, "text/html", None, bank_fetch.page_hash(t))
    return fetch


def _bank_setup():
    import bank_fetch, bank_fx, bank_facts
    bank_fetch.FETCHER = _bank_fetcher()
    bank_fx.clear_cache()
    bank_fx.set_rate_for_tests("USD", 1.38, "2026-10-19")
    bank_fx.set_rate_for_tests("GBP", 1.82, "2026-10-19")
    bank_facts.MODEL_CALL = bank_facts._call_model


def test_bank_stage2():
    import json
    import bank_extract, bank_facts, bank_fetch, bank_fx, bank_import, events
    print("\n=== bank stage 2: the importer on the five test products ===")
    saved_db, events.DATABASE_URL = events.DATABASE_URL, ""
    _bank_setup()
    r = {k: bank_import.import_url(BANK_URL[k], save=False) for k in ("tee", "knit", "jeans", "trousers", "jacket")}
    rec = {k: v["record"] for k, v in r.items()}
    check("all five make valid records", {k: v["problems"] for k, v in r.items()},
          {k: [] for k in r})
    pages = {k: bank_import.read_page(BANK_URL[k])[0].all_text for k in r}
    bad_quotes = [e["evidence_id"] for k, v in r.items() for e in v["evidence"]
                  if e.get("quote") and not bank_facts.quote_is_on_page(e["quote"], pages[k])]
    check("every quote is really on the page", bad_quotes, [])
    check("every fact points to evidence", all(
        f["status"] == "unknown" or f.get("evidence_ids")
        for v in rec.values() for _, f in __import__("bank_validate")._walk_facts(v)), True)
    check("nothing goes live from an import", sorted({v["status"] for v in rec.values()}), ["draft", "in_review"])
    check("brands found", {k: (v["brand"] or {}).get("name") for k, v in r.items()},
          {"tee": "Colorful Standard", "knit": "Peregrine", "jeans": "Nudie Jeans",
           "trousers": "Community Clothing", "jacket": "Finisterre"})
    check("the shop is kept apart from the brand", rec["knit"]["retailer"], "sirgordonbennett.com")
    check("garment types", {k: v["category"] for k, v in rec.items()},
          {"tee": "t_shirt", "knit": "knitwear", "jeans": "denim", "trousers": "trousers", "jacket": "outerwear"})

    tee = rec["tee"]
    check("tee: 100% cotton", tee["materials"]["composition"]["value"], [{"fibre": "cotton", "percent": 100}])
    check("tee: 180g kept as written, not called GSM",
          (tee["materials"]["fabric_weight"]["value"], tee["materials"]["fabric_weight"]["unit"]), (180.0, "g (as stated)"))
    check("tee: made in Portugal, as the shop reports it",
          (tee["construction"]["country_of_assembly"]["value"], tee["construction"]["country_of_assembly"]["source_type"]),
          ("Portugal", "brand_reported"))
    check("tee: cut words", sorted(t["tag"] for t in tee["fit"]["fit_tags"]), ["boxy", "cropped", "drop_shoulder", "oversized"])
    check("tee: stock not stated stays unknown", tee["variants"][0]["availability"], "unknown")
    check("tee: suggested pass, waiting for review", (tee["filo"]["decision"], tee["status"]), ("pass", "in_review"))
    check("tee: Filo's score is unchanged", tee["filo"]["filo_score"], 70)
    check("tee: opacity is unknown, never guessed from weight", tee["fit"]["opacity"], {"value": None, "status": "unknown"})

    knit = rec["knit"]
    check("knit: price in pounds", knit["variants"][0]["price"], {"amount": 99.75, "currency": "GBP"})
    check("knit: converted with a dated rate", (knit["variants"][0]["price_cad"], knit["variants"][0]["fx_rate_id"]),
          (181.55, "GBP-CAD-2026-10-19"))
    check("knit: sold out in every size", {v["availability"] for v in knit["variants"]}, {"out_of_stock"})
    check("knit: 'Manchester' isn't turned into a country",
          knit["construction"]["country_of_assembly"], {**knit["construction"]["country_of_assembly"],
                                                         "status": "unknown", "value": None, "raw": "Manchester"})
    check("knit: 'fits large' kept", knit["fit"]["sizing_advice"]["value"], "fits large")
    check("knit: wool means animal welfare is asked", knit["animal_welfare"]["species"]["status"], "unknown")
    check("knit: suggested conditional", knit["filo"]["decision"], "conditional")

    jeans = rec["jeans"]
    check("jeans: 14.9 oz", (jeans["materials"]["fabric_weight"]["value"], jeans["materials"]["fabric_weight"]["unit"]),
          (14.9, "oz"))
    check("jeans: 'TN' stays unknown", (jeans["construction"]["country_of_assembly"]["status"],
                                        jeans["construction"]["country_of_assembly"].get("raw")), ("unknown", "TN"))
    check("jeans: rigid denim", jeans["fit"]["stretch_and_recovery"]["value"], "rigid")
    check("jeans: wide leg, high rise", sorted(t["tag"] for t in jeans["fit"]["fit_tags"]),
          ["high_rise", "relaxed", "wide_leg"])

    tr = rec["trousers"]
    check("trousers: not enough evidence", tr["filo"]["decision"], "insufficient_evidence")
    check("trousers: suggests the brand's own page",
          any("brand's own product page" in m for m in tr["filo"]["missing_evidence"]), True)
    check("trousers: no brand-wide 'made in the UK' as a product fact",
          tr["construction"]["country_of_assembly"], {"value": None, "status": "unknown"})
    check("trousers: sale price kept apart from the regular price",
          (tr["variants"][0]["price"], tr["variants"][0]["sale_price"]),
          ({"amount": 145.0, "currency": "USD"}, {"amount": 73.0, "currency": "USD"}))

    j = rec["jacket"]
    check("jacket: factory named", j["construction"]["factory"]["value"], "Jiangsu Sudey Garment Industrial Co Ltd")
    check("jacket: made in China", j["construction"]["country_of_assembly"]["value"], "China")
    check("jacket: held back for being synthetic", (j["filo"]["decision"], "synthetic" in j["filo"]["decision_reason"]),
          ("hold", True))
    check("jacket: price per colour", sorted({(v["colour"], v["price"]["amount"]) for v in j["variants"]}),
          [("Burnt Umber", 250.0), ("Deep Sea", 260.0)])

    # The AI tagger: only facts whose quotes are on the page, and never a guessed country.
    def fake_model(prompt):
        return json.dumps({"facts": [
            {"field": "materials.fabric_structure", "value": "waffle", "quote": "Ladies Waffle Crew Neck Jumper"},
            {"field": "construction.country_of_assembly", "value": "United Kingdom", "quote": "Made in Manchester"},
            {"field": "fit.neckline", "value": "crew", "quote": "A classic crew neck cut close to the body"},
        ]})
    bank_facts.MODEL_CALL = fake_model
    ai = bank_import.import_url(BANK_URL["knit"], save=False)["record"]
    bank_facts.MODEL_CALL = bank_facts._call_model
    check("AI fact with a real quote is kept", ai["materials"].get("fabric_structure", {}).get("value"), "waffle")
    check("AI can't turn 'Manchester' into a country", ai["construction"]["country_of_assembly"]["status"], "unknown")
    check("AI fact with an invented quote is dropped", "neckline" in ai.get("fit", {}), False)

    page = bank_extract.extract("<body><p>$73.00</p></body>", "https://shop.example/p")
    check("a bare $ price isn't given a currency", bank_import._text_price(page)[:2], (None, None))
    check("exchange rates read from the Bank of Canada format", bank_fx.parse_valet(json.dumps(
        {"observations": [{"d": "2026-10-19", "FXUSDCAD": {"v": "1.3812"}, "FXGBPCAD": {"v": "1.8301"}}]})),
        {"USD": (1.3812, "2026-10-19"), "GBP": (1.8301, "2026-10-19")})
    bank_fx.clear_cache()
    check("no rate, no conversion", bank_fx.convert(10, "GBP")["price_cad"], None)

    bank_fetch.FETCHER = bank_fetch.http_fetch
    real_get = bank_fetch._raw_get
    bank_fetch._robots.clear()
    bank_fetch._raw_get = lambda url: (bank_fetch.FetchResult(True, url, 200, "User-agent: *\nDisallow: /", "", None, "h")
                                       if url.endswith("/robots.txt") else bank_fetch.FetchResult(True, url, 200, "page"))
    check("robots.txt is respected", bank_fetch.fetch("https://no-bots.example/products/x").error,
          "blocked by the site's robots.txt")
    bank_fetch._raw_get = real_get
    bank_fetch._robots.clear()
    _bank_setup()

    import main
    try:
        main.bank_import_url(main.BankImportIn(url=BANK_URL["tee"]), x_filo_admin="wrong")
        locked = False
    except Exception as e:
        locked = getattr(e, "status_code", None) == 404
    check("import needs the admin key", locked, True)
    events.DATABASE_URL = saved_db


def test_bank_stage2_postgres():
    import bank, bank_fetch, bank_import, bank_seed, events
    print("\n=== bank stage 2 on a real Postgres ===")
    _bank_setup()
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(bank.TABLES) + " CASCADE")
    conn.close()
    bank.init_schema()
    bank_seed.seed()
    saved = {k: bank_import.import_url(BANK_URL[k]) for k in ("tee", "knit", "jeans", "trousers", "jacket")}
    check("all five saved", {k: v["saved"] for k, v in saved.items()}, {k: True for k in saved})
    pid = {k: v["product_id"] for k, v in saved.items()}

    def q(sql, args=()):
        conn = events._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()

    def run(sql, args=()):
        conn = events._conn()
        with conn, conn.cursor() as cur:
            cur.execute(sql, args)
        conn.close()
    check("nothing is live after import", q("SELECT count(*) FROM bank_products WHERE status = 'live'")[0][0], 0)
    check("suggested decisions stored", dict(q("SELECT category, decision FROM bank_products")),
          {"t_shirt": "pass", "knitwear": "conditional", "denim": "conditional",
           "trousers": "insufficient_evidence", "outerwear": "hold"})
    check("facts stored with evidence", q("""SELECT count(*) FROM bank_facts
                                             WHERE status <> 'unknown' AND cardinality(evidence_ids) = 0""")[0][0], 0)
    check("evidence stored", q("SELECT count(*) > 20 FROM bank_evidence")[0][0], True)
    check("variants stored with prices", q("SELECT count(*) FROM bank_variants WHERE product_id = %s AND price IS NOT NULL",
                                           (pid["jacket"],))[0][0], 3)

    r = bank_import.refresh(pid["tee"])
    check("same page: no new version", (r["ok"], r["changed"], r["product_version"]), (True, False, 1))

    run("UPDATE bank_products SET status = 'live', decided_by = 'brooklyn' WHERE product_id = %s", (pid["tee"],))
    changed = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)),
                   "test_fixtures", "bank", "tee_altitude.html")).read().replace(
        "100% Organic Cotton, 180g", "95% Organic Cotton, 5% Elastane, 180g")
    bank_fetch.FETCHER = _bank_fetcher({BANK_URL["tee"]: changed})
    r = bank_import.refresh(pid["tee"])
    check("composition changed: new version", (r["changed"], r["product_version"]), (True, 2))
    check("...back to Brooklyn for review", q("SELECT status, decided_by FROM bank_products WHERE product_id = %s",
                                              (pid["tee"],))[0], ("in_review", None))
    check("...the old version's facts are kept", q("""SELECT count(*) > 0 FROM bank_facts
                                                      WHERE product_id = %s AND product_version = 1""", (pid["tee"],))[0][0], True)
    check("...and the new composition is recorded", q("""SELECT value FROM bank_facts WHERE product_id = %s
                     AND product_version = 2 AND field = 'materials.composition'""", (pid["tee"],))[0][0],
          [{"fibre": "cotton", "percent": 95}, {"fibre": "elastane", "percent": 5}])

    run("UPDATE bank_products SET status = 'live', decided_by = 'brooklyn' WHERE product_id = %s", (pid["jacket"],))
    bank_fetch.FETCHER = _bank_fetcher(fail={BANK_URL["jacket"]})
    r1 = bank_import.refresh(pid["jacket"])
    check("source fails: hidden, never shown stale", (r1["ok"], r1["hidden"], r1["retry_in_hours"]), (False, True, 1))
    check("...its evidence is marked failed", q("""SELECT count(*) = count(*) FILTER (WHERE status = 'source_failed')
                                                   FROM bank_evidence WHERE subject_id = %s""", (pid["jacket"],))[0][0], True)
    r2 = bank_import.refresh(pid["jacket"])
    check("...next retry waits longer", r2["retry_in_hours"], 6)
    bank_fetch.FETCHER = _bank_fetcher()
    r3 = bank_import.refresh(pid["jacket"])
    check("source back: shown again", (r3["ok"], q("SELECT status FROM bank_products WHERE product_id = %s",
                                                   (pid["jacket"],))[0][0]), (True, "live"))
    check("...evidence current again", q("""SELECT count(*) FILTER (WHERE status = 'source_failed')
                                            FROM bank_evidence WHERE subject_id = %s""", (pid["jacket"],))[0][0], 0)

    run("UPDATE bank_products SET status = 'live' WHERE product_id = %s", (pid["knit"],))
    bank_import.refresh(pid["knit"])
    check("sold out: hidden until it's back", q("SELECT status FROM bank_products WHERE product_id = %s",
                                                (pid["knit"],))[0][0], "hidden_out_of_stock")
    check("refresh log kept", q("SELECT count(*) >= 8 FROM bank_sources_log")[0][0], True)


def test_bank_stage3():
    import io, logging, os, secrets
    import bank_import, events, studio
    print("\n=== bank stage 3: Studio sign-in and go-live rules ===")
    saved_db, events.DATABASE_URL = events.DATABASE_URL, ""
    saved_pw = os.environ.pop("STUDIO_PASSWORD", None)
    studio.reset_for_tests()
    check("no password set: Studio is off", studio.login("anything", "1.1.1.1")[0:2],
          (False, "Studio is off. Add STUDIO_PASSWORD in Railway to switch it on."))
    pw = "test-" + secrets.token_hex(8)          # made up for this run; never the real one
    os.environ["STUDIO_PASSWORD"] = pw
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging.getLogger("filo.studio").addHandler(handler)
    check("wrong password says how many tries are left", studio.login("nope", "1.1.1.1")[1], "That's not it. 4 tries left.")
    for _ in range(3):
        studio.login("nope", "1.1.1.1")
    check("5th wrong try locks that address", studio.login("nope", "1.1.1.1")[1], "Too many wrong tries. Try again in 15 minutes.")
    check("...even the right password is refused while locked", studio.login(pw, "1.1.1.1")[0], False)
    check("...another address can still sign in", studio.login(pw, "2.2.2.2")[0], True)
    check("...and the lock lifts after 15 minutes", studio.login(pw, "1.1.1.1", now=__import__("time").time() + 15 * 60 + 5)[0], True)
    studio.reset_for_tests()
    for i in range(25):
        studio.login("nope", f"10.0.0.{i}")
    check("25 wrong tries from many addresses locks everyone", studio.login(pw, "3.3.3.3")[0], False)
    studio.reset_for_tests()
    ok, _, token = studio.login(pw, "4.4.4.4")
    check("right password gives a sign-in", (ok, studio.token_ok(token)), (True, True))
    check("a changed sign-in is refused", studio.token_ok(token[:-1] + ("0" if token[-1] != "0" else "1")), False)
    check("a sign-in ends after 12 hours", studio.token_ok(token, now=__import__("time").time() + 12 * 3600 + 5), False)
    studio.sign_out(token)
    check("signing out ends it", studio.token_ok(token), False)
    ok, _, token = studio.login(pw, "4.4.4.4")
    os.environ["STUDIO_PASSWORD"] = pw + "x"
    check("changing the password signs everyone out", studio.token_ok(token), False)
    os.environ["STUDIO_PASSWORD"] = pw
    logging.getLogger("filo.studio").removeHandler(handler)
    check("the password never reaches the log", pw in buf.getvalue() or "nope" in buf.getvalue(), False)
    import studio_page
    check("the password isn't in the page", pw in studio_page.PAGE, False)
    check("the page escapes what it shows", "const esc=" in studio_page.PAGE and "innerHTML=`" in studio_page.PAGE, True)

    _bank_setup()
    rec = {k: bank_import.import_url(BANK_URL[k], save=False)["record"] for k in ("tee", "trousers", "jacket", "knit")}
    check("tee can go live once its brand is approved", studio.go_live_problems(rec["tee"], True, "pass"), [])
    check("...not while its brand isn't", studio.go_live_problems(rec["tee"], False, "pass"),
          ["The brand isn't approved yet. Approve it in Brands first."])
    check("...not on hold", studio.go_live_problems(rec["tee"], True, "hold"),
          ["The decision has to be pass or conditional."])
    check("trousers can't: no composition", "What it's made of isn't confirmed." in
          studio.go_live_problems(rec["trousers"], True, "pass"), True)
    check("jacket can't, even if Brooklyn says pass: Filo's rules still apply",
          any("synthetic" in p for p in studio.go_live_problems(rec["jacket"], True, "pass")), True)
    check("stock not stated: warned it won't show yet", "won't show in a hunt" in (studio._stock_note(rec["tee"]) or ""), True)
    check("sold out everywhere: warned it goes live hidden", "Sold out" in (studio._stock_note(rec["knit"]) or ""), True)
    studio.reset_for_tests()
    if saved_pw is None:
        os.environ.pop("STUDIO_PASSWORD", None)
    else:
        os.environ["STUDIO_PASSWORD"] = saved_pw
    events.DATABASE_URL = saved_db


def test_bank_stage3_postgres():
    import os, secrets
    import bank, bank_fetch, bank_import, bank_seed, events, studio
    print("\n=== bank stage 3: Studio on a real Postgres ===")
    try:
        from fastapi.testclient import TestClient
    except Exception:                       # httpx not installed
        print("  (skipped: pip install httpx to run the Studio web checks)")
        return
    import main
    _bank_setup()
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(bank.TABLES) + " CASCADE")
    conn.close()
    bank.init_schema()
    bank_seed.seed()
    saved_pw = os.environ.get("STUDIO_PASSWORD")
    pw = "test-" + secrets.token_hex(8)
    os.environ["STUDIO_PASSWORD"] = pw
    studio.reset_for_tests()
    c = TestClient(main.app, base_url="https://testserver")
    H = {"X-Studio": "1"}

    def q(sql, args=()):
        conn = events._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()

    check("Studio page loads", c.get("/studio").status_code, 200)
    check("...and isn't cached or framed", (c.get("/studio").headers.get("cache-control"),
                                            c.get("/studio").headers.get("x-frame-options")), ("no-store", "DENY"))
    check("data is locked before sign-in", c.get("/studio/api/products").status_code, 401)
    check("wrong password refused", c.post("/studio/login", json={"password": "nope"}, headers=H).status_code, 401)
    check("sign-in from another site refused", c.post("/studio/login", json={"password": pw}).status_code, 403)
    r = c.post("/studio/login", json={"password": pw}, headers=H)
    check("right password signs in", r.status_code, 200)
    cookie = r.headers.get("set-cookie", "")
    check("sign-in cookie is locked down", all(x in cookie.lower() for x in ("httponly", "secure", "samesite=strict", "path=/studio")), True)
    check("changes need the Studio header", c.post("/studio/api/import", json={"url": BANK_URL["tee"]}).status_code, 403)

    imp = c.post("/studio/api/import", json={"url": BANK_URL["tee"]}, headers=H).json()
    check("import from Studio saves for review", (imp["saved"], imp["message"].startswith("Saved for review. Suggested: pass.")),
          (True, True))
    tee = imp["product_id"]
    bad = c.post("/studio/api/import", json={"url": "not a link"}, headers=H)
    check("a non-link is refused plainly", (bad.status_code, bad.json()["detail"]),
          (400, "Paste the full product link, starting with https://"))
    for k in ("knit", "trousers", "jacket"):
        c.post("/studio/api/import", json={"url": BANK_URL[k]}, headers=H)
    ids = dict(q("SELECT category, product_id FROM bank_products"))
    lst = c.get("/studio/api/products").json()
    check("review list puts 'needs review' first", lst["products"][0]["status"], "in_review")
    d = c.get(f"/studio/api/products/{tee}").json()
    check("product view shows every fact with evidence", all(f["status"] == "unknown" or f["evidence"] for f in d["facts"]), True)
    check("...the composition quote", any(e.get("quote") == "100% Organic Cotton, 180g"
                                          for f in d["facts"] for e in f["evidence"]), True)
    check("...nothing blocking go-live", d["go_live_problems"], [])

    stale = c.post(f"/studio/api/products/{tee}/decide", json={"decision": "pass", "version": 7, "publish": True}, headers=H)
    check("deciding on an old version is refused", stale.status_code, 400)
    r = c.post(f"/studio/api/products/{tee}/decide", json={"decision": "pass", "version": 1, "publish": True,
                                                         "reason": "Checked the page."}, headers=H)
    check("confirm and go live", r.json(), {"status": "live", "decision": "pass"})
    check("...recorded as Brooklyn's", q("SELECT decided_by, decision_reason, status FROM bank_products WHERE product_id = %s",
                                         (tee,))[0], ("brooklyn", "Checked the page.", "live"))
    r = c.post(f"/studio/api/products/{ids['trousers']}/decide", json={"decision": "pass", "version": 1, "publish": True}, headers=H)
    check("trousers can't go live, and Studio says why", (r.status_code, "isn't confirmed" in r.json()["detail"]), (400, True))
    r = c.post(f"/studio/api/products/{ids['outerwear']}/decide", json={"decision": "hold", "version": 1}, headers=H)
    check("hold keeps it out of the shop", r.json()["status"], "draft")

    r = c.post(f"/studio/api/products/{tee}/flags", json={"version": 1, "changes": {"is_unique_find": True, "studio_note": "Great weight."}}, headers=H)
    check("mark a unique find", r.json()["is_unique_find"], True)
    r = c.post(f"/studio/api/products/{tee}/tags", json={"version": 1, "tags": ["oversized", "boxy", "drop_shoulder", "relaxed"]}, headers=H)
    check("fix the cut words", r.json()["fit_tags"], ["oversized", "boxy", "drop_shoulder", "relaxed"])
    d = c.get(f"/studio/api/products/{tee}").json()
    added = [t for t in d["record"]["fit"]["fit_tags"] if t["tag"] == "relaxed"][0]["fact"]
    check("...a word you add is your read of the photos, not confirmed", (added["status"], added["source_type"]),
          ("from_photo", "inferred"))
    check("...with its own evidence", q("SELECT source_kind FROM bank_evidence WHERE evidence_id = %s",
                                        (added["evidence_ids"][0],))[0][0], "brooklyn_manual")
    check("...still live", d["status"], "live")
    bad = c.post(f"/studio/api/products/{tee}/tags", json={"version": 1, "tags": ["sparkly"]}, headers=H)
    check("...an unknown cut word is refused", bad.status_code, 400)

    r = bank_import.refresh(tee)
    check("same page after your fixes: no new version", (r["changed"], r["product_version"]), (False, 1))
    d = c.get(f"/studio/api/products/{tee}").json()
    check("...your cut words and marks survive", (sorted(t["tag"] for t in d["record"]["fit"]["fit_tags"]),
                                                  d["record"]["filo"]["is_unique_find"], d["status"]),
          (["boxy", "drop_shoulder", "oversized", "relaxed"], True, "live"))
    check("...stock not stated stays live, not 'sold out'", d["status"], "live")
    changed = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures", "bank",
                                "tee_altitude.html")).read().replace("100% Organic Cotton, 180g", "95% Organic Cotton, 5% Elastane, 180g")
    bank_fetch.FETCHER = _bank_fetcher({BANK_URL["tee"]: changed})
    r = bank_import.refresh(tee)
    bank_fetch.FETCHER = _bank_fetcher()
    d = c.get(f"/studio/api/products/{tee}").json()
    check("page changed: version 2, back to review", (r["product_version"], d["status"], d["decided_by"]), (2, "in_review", None))
    check("...your unique-find mark carries over", d["record"]["filo"].get("is_unique_find"), True)

    r = c.post("/studio/api/brands/peregrine", json={"reject": True}, headers=H)
    check("reject a brand", (r.json()["approved"], r.json()["research_status"]), (False, "remove"))
    bank_seed.seed()
    check("...a restart doesn't undo it", q("SELECT approved, research_status FROM bank_brands WHERE brand_id = 'peregrine'")[0],
          (False, "remove"))
    r = c.post("/studio/api/brands/peregrine", json={"approved": True, "style_fit": 3, "studio_note": "Back in."}, headers=H)
    check("approve it again with a note", (r.json()["approved"], r.json()["style_fit"], r.json()["research_status"]),
          (True, 3, "priority_screen"))
    check("style fit is 0 to 4", c.post("/studio/api/brands/peregrine", json={"style_fit": 9}, headers=H).status_code, 400)
    b = c.get("/studio/api/brands?approved=yes&q=peregrine").json()
    check("brand filters work", [x["brand_id"] for x in b["brands"]], ["peregrine"])

    jeans_live = c.post(f"/studio/api/products/{ids['knitwear']}/decide", json={"decision": "conditional", "version": 1}, headers=H)
    check("conditional without going live stays in review", jeans_live.json()["status"], "in_review")
    st = c.post("/studio/api/standards/set", json={"basic": "white_t_shirt", "slot": "best_overall", "product_id": ids["trousers"]}, headers=H)
    check("only confirmed products can be a Standard", st.status_code, 400)
    st = c.post("/studio/api/standards/set", json={"basic": "crewneck_sweater", "slot": "best_under", "product_id": ids["knitwear"]}, headers=H)
    check("best under needs a price", st.status_code, 400)
    st = c.post("/studio/api/standards/set", json={"basic": "crewneck_sweater", "slot": "best_under", "product_id": ids["knitwear"],
                                                   "price_line": 200, "note": "Proper wool."}, headers=H).json()
    sw = [s for s in st["standards"] if s["basic"] == "crewneck_sweater"][0]["picks"]
    check("set a Standard", [(p["slot"], p["price_line"], p["note"]) for p in sw], [("best_under", 200.0, "Proper wool.")])
    check("ten basics listed", len(st["standards"]), 10)

    voc = c.get("/studio/api/vocabulary?q=boxy").json()
    phrase = [p for x in voc["concepts"] if x["concept_id"] == "boxy" for p in x["phrases"] if p["language"] == "en"][0]["phrase"]
    c.post("/studio/api/vocabulary/remove_phrase", json={"concept_id": "boxy", "language": "en", "phrase": phrase}, headers=H)
    bank_seed.seed()
    check("a removed phrase stays removed after a restart", q("""SELECT removed FROM bank_concept_phrases
        WHERE concept_id = 'boxy' AND language = 'en' AND phrase = %s""", (phrase,))[0][0], True)
    r = c.post("/studio/api/vocabulary/add_phrase", json={"concept_id": "boxy", "language": "en", "phrase": "square-ish"}, headers=H)
    check("add a phrase", r.status_code, 200)

    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO stylist_hunts (hunt_id, device_id, brief_structured, language, nothing_passed)
                       VALUES ('h1', 'd1', '{"category": "outerwear", "unmatched_phrases": ["gorpcore"], "brands_asked": ["Arc Teryx"], "follow_ups": 1}', 'en', TRUE),
                              ('h2', 'd1', '{"category": "outerwear", "follow_ups": 0}', 'en', FALSE)""")
        cur.execute("UPDATE stylist_hunts SET created_at = now() - interval '5 minutes' WHERE hunt_id = 'h1'")
        cur.execute("""INSERT INTO bank_gaps (signature, brief_structured, count) VALUES ('outerwear|gorpcore', '{"category": "outerwear"}', 3)""")
    conn.close()
    bl = c.get("/studio/api/backlog").json()
    check("backlog: no-result briefs", [g["count"] for g in bl["no_results"]], [3])
    check("backlog: words that matched nothing", [(u["phrase"], u["times"]) for u in bl["unmatched_phrases"]], [("gorpcore", 1)])
    check("backlog: brands asked for but missing", [m["brand"] for m in bl["missing_brands"]], ["arc teryx"])
    check("backlog: reworded hunts", [(r["category"], r["times"]) for r in bl["rewordings"]], [("outerwear", 1)])
    hl = c.get("/studio/api/health").json()
    check("health: recovered after no results", hl["zero_result_recovery"]["pct"], 100.0)
    check("health: follow-ups per hunt", hl["follow_ups_per_hunt"], 0.5)
    check("health: nothing disputed", hl["evidence_accuracy"]["disputed"], 0)

    bank_fetch.FETCHER = _bank_fetcher(fail={BANK_URL["knit"]})
    q("SELECT 1")
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE bank_products SET status = 'live' WHERE product_id = %s", (ids["knitwear"],))
    conn.close()
    bank_import.refresh(ids["knitwear"])
    bank_fetch.FETCHER = _bank_fetcher()
    src = c.get("/studio/api/sources").json()
    check("sources: failing page listed", [(s["product_id"], s["failures_in_a_row"]) for s in src["failing"]],
          [(ids["knitwear"], 1)])
    r = c.post(f"/studio/api/products/{ids['knitwear']}/retire", json={}, headers=H)
    check("retire a failing page", r.json()["status"], "retired")
    check("...it leaves the Standards", q("SELECT count(*) FROM bank_standards WHERE product_id = %s", (ids["knitwear"],))[0][0], 0)
    check("every change is logged", q("SELECT count(*) >= 10 FROM studio_log")[0][0], True)
    c.post("/studio/logout", headers=H)
    check("signed out: locked again", c.get("/studio/api/products").status_code, 401)
    studio.reset_for_tests()
    if saved_pw is None:
        os.environ.pop("STUDIO_PASSWORD", None)
    else:
        os.environ["STUDIO_PASSWORD"] = saved_pw


# ------------------------------------------------------------------ stage 4: search, ranking, catalogue

def _fake_product(pid, brand="fake-brand-a", cat="t_shirt", comp=(("cotton", 100),), origin="Portugal",
                  tags=(), price=50.0, colour="Optical White", avail="in_stock", score=80, sizes=("S", "M", "L"),
                  opacity=None, indie=False, factory=None, resale=False, sale=None, checked_hours=1, status="live",
                  decision="pass", approved=True, lining=None, weight=None, currency="CAD", search_text="",
                  origin_status="confirmed", brand_name=None):
    """A clearly fake product for the search tests. Never saved anywhere."""
    from datetime import datetime, timedelta, timezone
    checked = (datetime.now(timezone.utc) - timedelta(hours=checked_hours)).isoformat()
    ev = lambda f: [f"{pid}:{f}"]
    rec = {"product_id": pid, "brand_id": brand, "name": f"FAKE TEST PRODUCT {pid}", "category": cat,
           "product_url": f"https://fake.test/{pid}", "images": [], "is_resale_listing": resale,
           "materials": {"composition": {"value": [{"fibre": f, "percent": p} for f, p in comp], "status": "confirmed",
                                         "source_type": "brand_reported", "scope": "this_style",
                                         "evidence_ids": ev("comp")} if comp else {"value": None, "status": "unknown"}},
           "construction": {"country_of_assembly": {"value": origin, "status": origin_status,
                                                    "source_type": "brand_reported", "scope": "this_style",
                                                    "evidence_ids": ev("origin")} if origin else {"value": None, "status": "unknown"}},
           "fit": {"fit_tags": [{"tag": t, "fact": {"value": True, "status": st, "source_type": "brand_reported" if st == "confirmed" else "inferred",
                                                    "scope": "this_style", "evidence_ids": ev("tag." + t)}}
                                for t, st in [(x if isinstance(x, tuple) else (x, "confirmed")) for x in tags]]},
           "filo": {"filo_score": score, "decision": decision, "is_independent_label": indie}}
    if opacity:
        val, st, src = opacity
        rec["fit"]["opacity"] = {"value": val, "status": st, "source_type": src, "scope": "this_style", "evidence_ids": ev("opacity")}
    if factory:
        name, scope = factory
        rec["construction"]["factory"] = {"value": name, "status": "confirmed", "source_type": "brand_reported",
                                          "scope": scope, "evidence_ids": ev("factory")}
    if lining:
        rec["materials"]["lining"] = {"value": [{"fibre": f, "percent": p} for f, p in lining], "status": "confirmed",
                                      "source_type": "brand_reported", "scope": "this_style", "evidence_ids": ev("lining")}
    if weight:
        rec["materials"]["fabric_weight"] = {"value": weight[0], "unit": weight[1], "status": "confirmed",
                                             "source_type": "brand_reported", "scope": "this_style", "evidence_ids": ev("weight")}
    variants = [{"variant_id": f"{pid}-{s}", "colour": colour, "size_label": s, "price": price, "currency": currency,
                 "sale_price": sale, "price_cad": sale if sale is not None else price,
                 "price_usd": round((sale if sale is not None else price) / 1.38, 2),
                 "availability": avail, "ships_to": [], "last_checked": checked} for s in sizes]
    return {"product_id": pid, "record": rec, "search_text": search_text, "decision": decision, "status": status,
            "is_resale": resale, "variants": variants,
            "brand": {"brand_id": brand, "name": brand_name or brand.replace("-", " ").title(), "approved": approved,
                      "independent_label": indie, "unique_find": False, "style": ""}}


class _FakeSource:
    def __init__(self, products, standards=None, evidence=None):
        self.products, self._standards, self._evidence = products, standards or {}, evidence or {}

    def candidates(self, categories, resale_allowed):
        return [p for p in self.products if (not categories or p["record"]["category"] in categories)]

    def evidence(self, ids):
        from datetime import datetime, timezone
        out = {}
        for p in self.products:
            if p["product_id"] in ids:
                for path in ("comp", "origin", "opacity", "factory", "lining", "weight") + tuple(
                        "tag." + t["tag"] for t in p["record"]["fit"]["fit_tags"]):
                    eid = f"{p['product_id']}:{path}"
                    out[eid] = {"evidence_id": eid, "source_type": "brand_reported", "status": "current",
                                "checked_at": datetime.now(timezone.utc), "quote": f"FAKE QUOTE {path}",
                                "sample_size": None}
        out.update(self._evidence)
        return out

    def standards(self, basic):
        return self._standards.get(basic, [])


def test_bank_stage4():
    import json
    import os
    import random
    import bank_catalog, bank_embed, bank_rules, events, stylist_brief, stylist_search as ss
    print("\n=== bank stage 4: search and ranking on a fake bank ===")
    saved_db, events.DATABASE_URL = events.DATABASE_URL, ""
    F = _fake_product

    def ids(r):
        return [c["product_id"] for c in r["results"]]

    # Hard filters, one at a time.
    src = _FakeSource([F("tee-ok"), F("knit-1", cat="knitwear", comp=(("wool", 100),)),
                       F("tee-sold", avail="out_of_stock"), F("tee-old", checked_hours=24 * 40),
                       F("tee-syn", comp=(("polyester", 60), ("cotton", 40))), F("tee-low", score=55),
                       F("tee-unapproved", approved=False), F("tee-review", status="in_review"),
                       F("tee-resale", resale=True)])
    r = ss.search({"category": "t_shirt"}, source=src, log_gap=False)
    check("only live, approved, in-stock, fresh tees that pass Filo's rules", sorted(ids(r)), ["tee-ok"])
    check("...and the funnel says why the rest went", r["funnel"],
          {"stock": 1, "freshness": 1, "show_rule": 4, "resale": 1})
    r = ss.search({"category": "t_shirt", "resale_allowed": True}, source=src, log_gap=False)
    check("resale shows only when the shopper allows it", sorted(ids(r)), ["tee-ok", "tee-resale"])
    check("knitwear asked: no tees", ids(ss.search({"category": "knitwear"}, source=src, log_gap=False)), ["knit-1"])

    src = _FakeSource([F("t50", price=50, brand="b1"), F("t99", price=99.99, brand="b2"), F("t101", price=101, brand="b3"),
                       F("tsale", price=130, sale=95, brand="b4")])
    r = ss.search({"category": "t_shirt", "budget": {"amount": 100, "currency": "CAD", "hard_max": True}}, source=src, log_gap=False)
    check("budget: nothing over $100, sale price counts", sorted(ids(r)), ["t50", "t99", "tsale"])
    check("...no card over budget", all(c["price"] <= 100 for c in r["results"]), True)

    src = _FakeSource([F("black", colour="Deep Black"), F("navy", colour="Navy"), F("asshown", colour="As shown")])
    check("black means black, not navy", ids(ss.search({"category": "t_shirt", "colour": "black"}, source=src, log_gap=False)), ["black"])

    src = _FakeSource([F("has-m", sizes=("S", "M")), F("no-m", sizes=("S", "L"))])
    check("size must be in stock", ids(ss.search({"category": "t_shirt", "size": "M"}, source=src, log_gap=False)), ["has-m"])

    src = _FakeSource([F("c100"), F("c95", comp=(("cotton", 95), ("elastane", 5))), F("lin", comp=(("linen", 100),))])
    check("100% cotton means 100%", ids(ss.search({"category": "t_shirt", "material": ["cotton 100"]}, source=src, log_gap=False)), ["c100"])
    check("'cotton' means mostly cotton", sorted(ids(ss.search({"category": "t_shirt", "material": ["cotton"]}, source=src, log_gap=False))),
          ["c100", "c95"])

    src = _FakeSource([F("coat-cot", cat="outerwear", comp=(("cotton", 100),)),
                       F("coat-wool-lining", cat="outerwear", comp=(("cotton", 100),), lining=(("wool", 100),)),
                       F("coat-wool", cat="outerwear", comp=(("wool", 80), ("polyamide", 20)))])
    check("no animal materials: shell, lining and trims all checked",
          ids(ss.search({"category": "outerwear", "exclude": ["animal_material"]}, source=src, log_gap=False)), ["coat-cot"])

    src = _FakeSource([F("pt"), F("cn", origin="China"), F("unk", origin=None), F("raw", origin="TN", origin_status="unknown")])
    check("made in Europe: only confirmed European origin", ids(ss.search({"category": "t_shirt", "origin": "europe"}, source=src, log_gap=False)), ["pt"])

    src = _FakeSource([F("fac", factory=("Fake Factory Lda", "this_style")), F("brandwide", factory=("All our factories", "brand_wide")), F("none")])
    check("supply chain proof must be for the product, not brand-wide",
          ids(ss.search({"category": "t_shirt", "ethics": ["factory_transparency"]}, source=src, log_gap=False)), ["fac"])

    src = _FakeSource([F("sheer", opacity=("sheer", "confirmed", "customer_reported")), F("ok")])
    r = ss.search({"category": "t_shirt", "opacity": "not_sheer", "performance": ["opaque"]}, source=src, log_gap=False)
    check("evidence that it's see-through removes it", "sheer" in ids(r), False)

    # Evidence-gated musts: unknown only in the single trade-off slot.
    src = _FakeSource([F("unk1"), F("unk2"), F("proven", opacity=("opaque", "confirmed", "customer_reported")),
                       F("brand-says", opacity=("opaque", "confirmed", "brand_reported"))])
    r = ss.search({"category": "t_shirt", "opacity": "not_sheer", "performance": ["opaque"]}, source=src, log_gap=False)
    slots = {c["product_id"]: c["slot"] for c in r["results"]}
    check("opacity proven by buyers: the pick", (slots.get("proven"), r["results"][0]["is_pick"]), ("best_overall", True))
    check("opacity only brand-claimed or unknown: one trade-off at most", sum(1 for v in slots.values() if v == "trade_off"), 1)
    check("...which names what's missing", [c.get("trade_off_note") for c in r["results"] if c["slot"] == "trade_off"],
          ["No proof yet for: not see-through."])
    src = _FakeSource([F("pricey-unk", price=200)])
    r = ss.search({"category": "t_shirt", "opacity": "not_sheer", "budget": {"amount": 100, "hard_max": True, "currency": "CAD"}},
                  source=src, log_gap=False)
    check("the trade-off slot never covers a hard filter", r["results"], [])

    # The three scores.
    src = _FakeSource([F("boxy-c", tags=("boxy",)), F("boxy-p", tags=(("boxy", "from_photo"),)), F("plain")])
    r = ss.search({"category": "t_shirt", "silhouette": ["boxy"]}, source=src, log_gap=False)
    m = {c["product_id"]: c["scores"]["match"] for c in r["results"]}
    check("match: confirmed cut beats a photo read, which beats nothing", m["boxy-c"] > m["boxy-p"], True)
    check("...and nothing scores too low to earn a place", "plain" in m, False)
    check("quality is Filo's score, unchanged", {c["product_id"]: c["scores"]["quality"] for c in r["results"]},
          {"boxy-c": 80, "boxy-p": 80})
    src = _FakeSource([F("with-origin"), F("no-origin", origin=None)])
    r = ss.search({"category": "t_shirt"}, source=src, log_gap=False)
    ev = {c["product_id"]: (c["scores"]["evidence"], c["scores"]["evidence_label"]) for c in r["results"]}
    check("evidence: brand-stated fabric and origin is Medium", ev["with-origin"], (0.7, "Medium"))
    check("evidence: unknown origin drags it to Low", ev["no-origin"], (0.35, "Low"))
    check("within 5 match points, better evidence ranks first", ids(r), ["with-origin", "no-origin"])
    check("exactly one Stylist's pick", sum(c["is_pick"] for c in r["results"]), 1)
    check("every claim on a card has evidence", r["problems"], [])

    # Diversity.
    many = [F(f"a{i}", brand="big-brand", price=50 + i) for i in range(6)] + \
           [F(f"b{i}", brand="other-brand", price=80 + i) for i in range(3)] + [F("c0", brand="third-brand", price=120)]
    r = ss.search({"category": "t_shirt", "colour": "white", "count": 5}, source=_FakeSource(many), log_gap=False)
    from collections import Counter
    per = Counter(c["brand_id"] for c in r["results"])
    check("five white tees: at most 2 per brand", max(per.values()), 2)
    check("...at least two price points", len({round(c["price"], -1) for c in r["results"]}) >= 2, True)
    r = ss.search({"category": "t_shirt", "count": 15}, source=_FakeSource(many), log_gap=False)
    check("fifteen: at most 3 per brand", max(Counter(c["brand_id"] for c in r["results"]).values()), 3)
    check("...and says when fewer than asked passed", r["fewer_than_asked"], len(r["results"]))
    src = _FakeSource([F("big1", brand="big-brand", price=60), F("big2", brand="big-brand", price=70),
                       F("indie1", brand="tiny-label", indie=True, price=65)])
    r = ss.search({"category": "t_shirt"}, source=src, log_gap=False)
    check("an independent label within 5 points gets its own slot",
          [c["slot"] for c in r["results"] if c["product_id"] == "indie1"], ["best_independent"])

    # No match.
    src = _FakeSource([F("t40", price=40), F("t60", price=60)])
    r = ss.search({"category": "t_shirt", "colour": "white", "budget": {"amount": 10, "hard_max": True, "currency": "CAD"}},
                  source=src, log_gap=False)
    check("white tee under $10: nothing passes", (r["nothing_passed"], r["results"]), (True, []))
    check("...says budget is what removed them, and the lowest price", r["flex_options"],
          [{"filter": "budget", "would_pass": 2, "lowest_price": 40.0, "currency": "CAD"}])
    r = stylist_search_hunt = ss.hunt("oversized slim-fit jeans", source=src)
    check("contradictory brief: asks instead of guessing", (r.get("needs_question"), r["conflicts"]), (True, [["oversized", "slim"]]))

    # Standards.
    std = {"white_t_shirt": [{"slot": "best_overall", "rank": 1, "product_id": "s1", "note": "The one.", "price_line": None},
                             {"slot": "best_under", "rank": 1, "product_id": "s2", "note": None, "price_line": 60},
                             {"slot": "best_splurge", "rank": 1, "product_id": "s3-sold", "note": None, "price_line": None}]}
    src = _FakeSource([F("s1", price=90), F("s2", price=45), F("s3-sold", price=200, avail="out_of_stock"), F("other")], standards=std)
    r = ss.search(stylist_brief.parse("I just need a good white t-shirt"), source=src, log_gap=False)
    check("standard mode uses Brooklyn's Standards", (r["mode"], ids(r)), ("standard", ["s1", "s2"]))
    check("...a sold-out Standard is skipped", "s3-sold" in ids(r), False)
    check("...her note travels with the pick", r["results"][0].get("stylist_note"), "The one.")

    # Meaning search, without a key: style words.
    src = _FakeSource([F("chore", cat="outerwear", search_text="A sturdy chore jacket in cotton drill with patch pockets."),
                       F("blazer", cat="outerwear", search_text="A soft romantic blazer with puff sleeves.")])
    r = ss.search({"category": "outerwear", "aesthetic": ["workwear"], "vibe_text": "something workwear, utility"}, source=src, log_gap=False)
    check("vibe without a key: workwear finds the chore jacket first", ids(r)[0], "chore")
    # With a key: fingerprints.
    os.environ["VOYAGE_API_KEY"] = "test-not-a-real-key"
    real_call, real_vectors = bank_embed.EMBED_CALL, bank_embed.vectors
    bank_embed.query_vector.cache_clear()
    bank_embed.EMBED_CALL = lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts]
    bank_embed.vectors = lambda pids: {"chore": [0.1, 1.0, 0.0], "blazer": [1.0, 0.05, 0.0]}
    r = ss.search({"category": "outerwear", "vibe_text": "quiet, a bit French"}, source=src, log_gap=False)
    check("vibe with a key: the closest fingerprint wins", ids(r)[0], "blazer")
    bank_embed.EMBED_CALL, bank_embed.vectors = real_call, real_vectors
    bank_embed.query_vector.cache_clear()
    os.environ.pop("VOYAGE_API_KEY", None)

    # Reading requests.
    b = stylist_brief.parse("white tee boxy not see thru under 100")
    check("fragment: white boxy tee, not see-through, under 100",
          (b["category"], b["colour"], b["silhouette"], b["opacity"], b["budget"]["amount"]),
          ("t_shirt", "white", ["boxy"], "not_sheer", 100.0))
    check("typos: jeens for my hipps", (stylist_brief.parse("jeens for my hipps that dont gape at the back")["category"],
                                        stylist_brief.parse("jeens for my hipps that dont gape at the back")["fit_need"]),
          ("denim", ["waist_hip_ratio"]))
    b = stylist_brief.parse("15 lambskin leather jackets, drop shoulder, cropped, made in Europe")
    check("the big brief", (b["count"], b["material"], sorted(b["silhouette"]), b["origin"]),
          (15, ["leather: lambskin"], ["cropped", "drop_shoulder"], "europe"))
    b = stylist_brief.parse("100% cotton t-shirt, lightweight, not see-through, cropped, a bit loose, flattering for a bigger chest")
    check("the bigger-chest tee", (b["material"], sorted(b["performance"]), sorted(b["silhouette"]), b["fit_need"]),
          (["cotton 100"], ["lightweight", "opaque"], ["cropped", "relaxed"], ["room_through_bust"]))
    check("'no animal materials'", stylist_brief.parse("no animal materials, a warm winter coat")["exclude"], ["animal_material"])
    check("winter isn't a colour", "colour" in stylist_brief.parse("no animal materials, a warm winter coat"), False)
    check("'I just need a good white t-shirt' is standard mode",
          (stylist_brief.parse("I just need a good white t-shirt")["mode"], stylist_brief.parse("I just need a good white t-shirt")["basic"]),
          ("standard", "white_t_shirt"))
    check("a sheer tee isn't turned into not-sheer", "opacity" in stylist_brief.parse("a sheer white tee"), False)
    check("words it can't place are listed for the backlog", stylist_brief.parse("a sequin evening gown")["unmatched_phrases"],
          ["sequin", "evening", "gown"])

    # The rules that sort a catalogue.
    good = F("rule-good", comp=(("cotton", 100),))["record"]
    good["variants"] = [{"availability": "in_stock", "price": {"amount": 50.0, "currency": "CAD"}, "price_cad": 50.0}]
    good["materials"]["composition"]["value"] = [{"fibre": "cotton", "percent": 100}]
    class R: 
        def __init__(self, x): self.x = x
        def random(self): return self.x
    a = bank_rules.auto_decide(good, True, R(0.5))
    check("clear case: live, decided by the rules", (a["status"], a["decided_by"], a["spot_check"]), ("live", "rules", False))
    check("...some go to spot checks", bank_rules.auto_decide(good, True, R(0.01))["spot_check"], True)
    check("...brand not approved: held", bank_rules.auto_decide(good, False, R(0.5))["outcome"], "held")
    sold = json.loads(json.dumps(good)); sold["variants"][0]["availability"] = "out_of_stock"
    check("...sold out: live but hidden", bank_rules.auto_decide(sold, True, R(0.5))["status"], "hidden_out_of_stock")
    unk = json.loads(json.dumps(good)); unk["variants"][0]["availability"] = "unknown"
    check("...stock not stated: waits for Brooklyn", bank_rules.auto_decide(unk, True, R(0.5))["outcome"], "review")
    border = json.loads(json.dumps(good)); border["category"] = "knitwear"
    border["materials"]["composition"]["value"] = [{"fibre": "wool", "percent": 60}, {"fibre": "polyamide", "percent": 40}]
    check("...40% synthetic that passes the floor: waits for Brooklyn", bank_rules.auto_decide(border, True, R(0.5))["outcome"], "review")
    ins = json.loads(json.dumps(good)); ins["filo"]["decision"] = "insufficient_evidence"
    check("...not enough on the page: waiting", bank_rules.auto_decide(ins, True, R(0.5))["outcome"], "waiting")
    os.environ["BANK_AUTO_APPROVE"] = "0"
    check("automatic approval switched off: everything waits", bank_rules.auto_decide(good, True, R(0.5))["outcome"], "review")
    os.environ.pop("BANK_AUTO_APPROVE")

    check("store link keeps the Canadian folder", bank_catalog.store_base("https://colorfulstandard.com/en-ca/collections/women"),
          "https://colorfulstandard.com/en-ca")
    check("women's kept", bank_catalog.gender_ok("Women Tee", "", "", [], "women_and_men", False), (True, "women"))
    check("men's skipped", bank_catalog.gender_ok("Heavy Tee", "", "Men's T-Shirts", [], "women_and_men", False), (False, "men"))
    check("unclear skipped unless the brand only makes women's",
          (bank_catalog.gender_ok("Heavy Tee", "", "", [], "women_and_men", False)[0],
           bank_catalog.gender_ok("Heavy Tee", "", "", [], None, True)[0]), (False, True))
    events.DATABASE_URL = saved_db


def test_bank_stage4_postgres():
    import json, os, secrets
    import bank, bank_catalog, bank_fetch, bank_import, bank_seed, events, studio, stylist_search
    print("\n=== bank stage 4: catalogue import and search on a real Postgres ===")
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures", "catalog")
    _bank_setup()
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(bank.TABLES) + " CASCADE")
    conn.close()
    bank.init_schema()
    bank_seed.seed()
    base = "https://colorfulstandard.com/en-ca"
    files = {f"{base}/products.json?limit=250&page=1": "products_page1.json",
             f"{base}/products.json?limit=250&page=2": "products_page2.json", f"{base}/cart.js": "cart.json"}
    overrides = {u: open(os.path.join(here, f)).read() for u, f in files.items()}
    bank_fetch.FETCHER = _bank_fetcher(overrides)

    def q(sql, args=()):
        conn = events._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()
    try:
        bank_catalog.start("peregrine-not-a-brand", base, background=False)
        refused = False
    except ValueError:
        refused = True
    check("unknown brand refused", refused, True)
    r = bank_catalog.start("colorful-standard", base + "/collections/women", background=False)
    job = bank_catalog.jobs(1)[0]
    c = job["counts"]
    check("catalogue job finished, read through Shopify", (job["status"], job["method"], job["found"]), ("done", "shopify", 8))
    check("...sorted by the rules", {k: v for k, v in c.items() if v and k != "spot_check"},
          {"live": 2, "review": 1, "held": 1, "waiting": 1, "skipped_men": 1, "skipped_not_clothing": 1,
           "skipped_other_brand": 1})
    rows = dict(q("SELECT name, status || '/' || coalesce(decided_by, '-') FROM bank_products"))
    check("the clear cases went live, decided by the rules",
          (rows["Women Fake Test Organic Tee"], rows["Women Fake Test Merino Sweater"]), ("live/rules", "live/rules"))
    check("40% synthetic knit waits for Brooklyn", rows["Women Fake Test Wool Blend Cardigan"], "in_review/-")
    check("polyester jacket held back", rows["Women Fake Test Rain Jacket"], "draft/-")
    check("no fabric on the page: waiting", rows["Women Fake Test Mystery Tee"], "draft/-")
    check("why it waits is recorded", q("SELECT auto_reasons FROM bank_products WHERE name = 'Women Fake Test Wool Blend Cardigan'")[0][0],
          ["40% synthetic, close to the limit."])
    check("prices in the store's currency", q("""SELECT DISTINCT v.currency FROM bank_variants v JOIN bank_products p
                                                 USING (product_id) WHERE p.name = 'Women Fake Test Organic Tee'""")[0][0], "CAD")
    tee = q("SELECT product_id FROM bank_products WHERE name = 'Women Fake Test Organic Tee'")[0][0]
    knit = q("SELECT product_id FROM bank_products WHERE name = 'Women Fake Test Merino Sweater'")[0][0]

    r = stylist_search.hunt("boxy white tee under 60")
    check("a hunt finds the live tee", [c["product_id"] for c in r["results"]], [tee])
    card = r["results"][0]
    check("...with its quote as proof", [cl["quote"] for cl in card["claims"] if cl["requirement"] == "composition"],
          ["100% organic cotton, 180 gsm."])
    check("...origin confirmed from the page", [cl["value"] for cl in card["claims"] if cl["requirement"] == "origin"], ["Portugal"])
    check("...the cut matched", any(cl["requirement"] == "boxy" for cl in card["claims"]), True)
    check("...nothing wrong with the card", r["problems"], [])
    r = stylist_search.hunt("black boxy tee")
    check("black is sold out: nothing passes, and it says why", (r["nothing_passed"], r["funnel"].get("stock")), (True, 1))
    check("...and the gap is logged for the backlog", q("SELECT count(*) FROM bank_gaps")[0][0] >= 1, True)
    r = stylist_search.hunt("navy knit made in Italy")
    check("made in Italy knit found", [c["product_id"] for c in r["results"]], [knit])

    q("SELECT 1")
    conn = events._conn()
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE bank_products SET decided_by = 'brooklyn' WHERE product_id = %s", (knit,))
    conn.close()
    bank_catalog.start("colorful-standard", base, background=False)
    check("re-running a catalogue never overrides Brooklyn", q("SELECT decided_by FROM bank_products WHERE product_id = %s", (knit,))[0][0],
          "brooklyn")
    check("...and says so", bank_catalog.jobs(1)[0]["counts"]["kept_brooklyn"], 1)

    page = ('<html><body><script src="https://cdn.shopify.com/x.js"></script><h1>Women Fake Test Organic Tee</h1>'
            '<p>FAKE TEST PRODUCT.</p><p>60% cotton, 40% polyester.</p><p>Made in Portugal.</p></body></html>')
    bank_fetch.FETCHER = _bank_fetcher({**overrides, f"{base}/products/women-fake-test-organic-tee": page,
                                        f"{base}/products/women-fake-test-organic-tee.js": json.dumps(
                                            json.load(open(os.path.join(here, "products_page1.json")))["products"][0] |
                                            {"body_html": "<p>FAKE TEST PRODUCT.</p><p>60% cotton, 40% polyester.</p><p>Made in Portugal.</p>"})})
    r = bank_import.refresh(tee)
    check("the rules' own product turns 40% polyester: the rules look again and hold it back", (r["changed"], q(
        "SELECT status, decided_by FROM bank_products WHERE product_id = %s", (tee,))[0]), (True, ("draft", None)))
    bank_fetch.FETCHER = _bank_fetcher()

    pw = "test-" + secrets.token_hex(8)
    saved_pw = os.environ.get("STUDIO_PASSWORD")
    os.environ["STUDIO_PASSWORD"] = pw
    studio.reset_for_tests()
    try:
        from fastapi.testclient import TestClient
        import main
        cl = TestClient(main.app, base_url="https://testserver")
        H = {"X-Studio": "1"}
        cl.post("/studio/login", json={"password": pw}, headers=H)
        r = cl.post("/studio/api/hunt", json={"text": "relaxed navy sweater", "market": "CA"}, headers=H)
        check("Studio test hunt works", (r.status_code, [c["product_id"] for c in r.json()["results"]]), (200, [knit]))
        r = cl.post("/studio/api/catalog/start", json={"brand_id": "patagonia"}, headers=H)
        check("catalogue for an unapproved brand is refused", r.status_code, 400)
        r = cl.get("/studio/api/catalog")
        check("Studio lists catalogue jobs", (r.status_code, len(r.json()["jobs"])), (200, 2))
        r = cl.get("/studio/api/products?status=rules")
        check("Studio filter: approved by the rules", (r.status_code, r.json()["by_status"].get("rules")),
              (200, q("SELECT count(*) FROM bank_products WHERE decided_by = 'rules'")[0][0]))
        check("admin search route is locked", cl.post("/internal/stylist/search", json={"text": "tee"}).status_code, 404)
    except ImportError:
        print("  (skipped: pip install httpx to run the Studio web checks)")
    studio.reset_for_tests()
    if saved_pw is None:
        os.environ.pop("STUDIO_PASSWORD", None)
    else:
        os.environ["STUDIO_PASSWORD"] = saved_pw


if __name__ == "__main__":
    test_parser()
    test_scoring()
    test_integrity()
    test_no_fast_fashion()
    test_price_earns_itself()
    test_lifespan_is_sourced()
    test_v36_premium_fibres()
    test_v37_market_price()
    test_v39_more_listings()
    test_v41_barcode_brain()
    test_v42_usual_price()
    test_v40_leather_lining_label()
    test_score_parts()
    test_market_price_check()
    test_products()
    test_products_api()
    if __import__("os").environ.get("FILO_TEST_DATABASE_URL"):
        import events, products as _p
        events.DATABASE_URL = __import__("os").environ["FILO_TEST_DATABASE_URL"]
        _p.init_schema()
        test_products(lambda: _p.PostgresStore(), "postgres")
        events.DATABASE_URL = ""
    test_season_calendar()
    test_look_never_overrides_quality()
    test_mall_brands_cannot_flood()
    test_affiliate_cannot_bend_the_ranking()
    test_leather_and_garment_type()
    test_no_price_still_has_a_range()
    test_v22_always_an_option()
    test_v23_taste()
    test_v11_department_price_fast_accounts()
    test_v12_closet_fund()
    test_v13_evidence_and_rarity()
    test_v14_seasonal_home()
    test_v16_colour_pick()
    test_v17_taste_tier_and_department_stores()
    test_v18_budget_knockoffs_cheap_leather()
    test_v20_the_realreal()
    test_v21_breakdown()
    test_v24_colour_changes_the_list_and_hoodies()
    test_v25_colour_from_the_photo()
    test_v30_fair_price()
    test_v30_3_every_fibre_on_the_tag()
    test_v30_5_more_types_and_true_colour()
    test_v30_8_the_store()
    test_v31_shopper_voices()
    test_bank_stage1()
    test_bank_stage2()
    test_bank_stage3()
    test_bank_stage4()
    if __import__("os").environ.get("FILO_TEST_DATABASE_URL"):
        import events
        events.DATABASE_URL = __import__("os").environ["FILO_TEST_DATABASE_URL"]
        test_bank_stage1_postgres()
        test_bank_stage2_postgres()
        test_bank_stage3_postgres()
        test_bank_stage4_postgres()
        events.DATABASE_URL = ""
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  -", f)
        raise SystemExit(1)
    print("ALL PASS")
