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
import catalog as _catalog
_catalog.STRICT_SOURCES = False   # older tests use made-up shop names; v43 test turns it on
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


def test_v43_trusted_recommendations():
    """Brooklyn, 23 Oct 2026: a $3 '100% organic cotton women's t-shirt 00242' from
    an unknown seller was recommended. Only trusted sources now."""
    print("\n=== v43: recommendations only from brands Filo trusts ===")
    import catalog, brands
    catalog.STRICT_SOURCES = True
    try:
        junk = {"title": "100% Organic Cotton Women's T-Shirt 00242", "extracted_price": 3,
                "source": "Shopxyz Marketplace", "product_link": "https://x.example/1"}
        kotn = {"title": "Kotn Women's Essential Crew Tee 100% Organic Cotton", "extracted_price": 35,
                "source": "Kotn", "product_link": "https://kotn.example/2"}
        catalog._fetch = (lambda real: real)(catalog._fetch)
        real = catalog._fetch
        catalog._fetch = lambda q, num=40, gl=None: [junk, kotn]
        try:
            res = catalog.search_alternatives(category="t-shirt", price=40, scanned_score=6.0)
        finally:
            catalog._fetch = real
        names = [r.get("name") or "" for r in res]
        check("the unknown $3 seller is gone", any("00242" in n for n in names), False)
        check("an approved brand still shows", any("Kotn" in n for n in names), True)
        check("approved brands are recognised", brands.is_approved("Kotn"), True)
    finally:
        catalog.STRICT_SOURCES = False


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
    test_v43_trusted_recommendations()
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
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  -", f)
        raise SystemExit(1)
    print("ALL PASS")
