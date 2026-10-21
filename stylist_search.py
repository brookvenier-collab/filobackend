"""Search and rank the bank for one brief. ranking_rules.json, in code.

Order (CLAUDE_CODE_BUILD stage 4): normalize the brief, apply the show rule and hard filters,
retrieve (structured match, keyword match and meaning search together), score Match, Quality
and Evidence Confidence separately, rank, diversify, fill shortlist slots, verify freshness,
then validate.

Three scores, never blended into one:
  match     0-100  how well it answers this brief
  quality   0-100  Filo's existing score, unchanged
  evidence  0-1    how complete, direct and current the evidence behind the card is

A product that fails a hard filter is removed, never ranked lower. If nothing passes, the
result says which filters removed what and which could flex; nothing is relaxed silently.
"""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import bank_embed
import catalog
import events
import stylist_brief

HERE = os.path.dirname(os.path.abspath(__file__))

SHOWABLE = ("in_stock", "low_stock", "preorder", "made_to_order")
PRICE_FRESH_HOURS = 72
PRICE_HIDE_DAYS = 30
COMPOSITION_FRESH_DAYS = 90
DEFAULT_SHORTLIST = 5
MAX_COUNT = 15
MIN_MATCH = 35                    # a product has to earn a place

WEIGHTS = {"musts_met": 0.45, "nice_to_haves": 0.15, "aesthetic": 0.15, "budget_fit": 0.10,
           "availability": 0.10, "taste": 0.05}
SOURCE_WEIGHT = {"verified": 1.0, "brand_reported": 0.7, "inferred": 0.3}
SOURCE_RANK = {"inferred": 0, "brand_reported": 1, "customer_reported": 2, "verified": 3}
SCOPE_MULT = {"this_variant": 1.0, "this_style": 1.0, "this_style_all_colours": 0.9, "brand_wide": 0.3,
              "facility": 0.8, "certificate_scope": 0.8}

# Requirements that need evidence of a minimum kind before they count. Unknown only in the trade-off slot.
GATED = {"opaque": ("fit.opacity", "customer_reported"),
         "room_through_bust": ("fit.room_through_bust", "customer_reported"),
         "non_itchy": ("materials.feel_itch", "customer_reported"),
         "no_pilling": ("durability_evidence.pilling", "customer_reported"),
         "no_shrink": ("durability_evidence.shrinkage", "brand_reported")}
GATED_LABEL = {"opaque": "not see-through", "room_through_bust": "room through the bust",
               "non_itchy": "not itchy", "no_pilling": "no pilling", "no_shrink": "no shrinking"}

CATEGORY_CHILDREN = {"knitwear": {"knitwear", "cardigan"}, "outerwear": {"outerwear", "leather_outerwear"},
                     "t_shirt": {"t_shirt"}, "long_sleeve": {"long_sleeve"}, "denim": {"denim"},
                     "trousers": {"trousers"}}
FIBRE_PARENT = {"merino": "wool", "lambswool": "wool", "virgin wool": "wool", "recycled wool": "wool",
                "organic cotton": "cotton", "supima cotton": "cotton", "pima cotton": "cotton",
                "recycled cotton": "cotton", "lambskin": "leather", "cowhide": "leather", "calfskin": "leather",
                "spandex": "elastane", "lycra": "elastane", "polyamide": "nylon", "tencel": "lyocell"}
ANIMAL = {"wool", "cashmere", "alpaca", "mohair", "silk", "leather", "suede", "down", "fur", "shearling", "feather",
          "angora", "yak", "camel"}
NATURAL = {"cotton", "linen", "hemp", "wool", "cashmere", "silk", "alpaca", "mohair", "ramie"}
SYNTHETIC = {"polyester", "nylon", "acrylic", "elastane", "polypropylene", "modacrylic", "polyurethane", "pvc"}
WARM = {"wool", "cashmere", "alpaca", "mohair", "down", "shearling"}
BREATHABLE = {"cotton", "linen", "hemp", "silk"}
COLOUR_FAMILY = stylist_brief.COLOURS


def _load_config() -> dict:
    try:
        with open(os.path.join(HERE, "bank_data", "stylist_config.json"), encoding="utf-8") as f:
            return json.load(f).get("stylist", {})
    except (OSError, ValueError):
        return {}


EUROPE = {c.lower() for c in _load_config().get("europe_countries", [])} | {"england", "scotland", "wales", "uk"}


# ------------------------------------------------------------------ helpers

def _node(record: dict, path: str) -> dict:
    node = record
    for p in path.split("."):
        node = node.get(p, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _fibre(name: str) -> str:
    n = (name or "").lower().strip()
    return FIBRE_PARENT.get(n, n)


def composition(record: dict) -> Optional[List[dict]]:
    f = _node(record, "materials.composition")
    return f.get("value") if f.get("status") == "confirmed" else None


def all_material_text(record: dict) -> str:
    """Every material fact on the record (shell, lining, fill, trims), as text, for exclusions."""
    out = []
    mats = record.get("materials") or {}
    for k, f in mats.items():
        if isinstance(f, dict):
            v = f.get("value")
            if isinstance(v, list):
                out += [f"{c.get('fibre', '')}" for c in v if isinstance(c, dict)]
            elif isinstance(v, str):
                out.append(v)
    return " ".join(out).lower()


def synthetic_pct(comp: List[dict]) -> float:
    return sum(float(c.get("percent") or 0) for c in comp if _fibre(c.get("fibre")) in SYNTHETIC)


def colour_family(name: str) -> Optional[str]:
    n = (name or "").lower()
    for word in sorted(COLOUR_FAMILY, key=len, reverse=True):
        if re.search(r"\b" + re.escape(word) + r"\b", n):
            return COLOUR_FAMILY[word]
    return None


def _age_hours(ts) -> float:
    if ts is None:
        return 1e9
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return 1e9
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def _price_in(v: dict, currency: str) -> Optional[float]:
    if currency == "USD":
        return v.get("price_usd")
    return v.get("price_cad")


def _current(v: dict) -> Optional[float]:
    return v.get("sale_price") if v.get("sale_price") is not None else v.get("price")


# ------------------------------------------------------------------ data

class DbSource:
    """Reads the bank. Tests pass their own source with the same three methods."""

    def candidates(self, categories: Optional[List[str]], resale_allowed: bool) -> List[dict]:
        conn = events._conn()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                sql = """SELECT p.product_id, p.record, p.search_text, p.decision, p.status, p.is_resale_listing,
                                p.last_checked, b.brand_id, b.name, b.approved, b.independent_label, b.unique_find,
                                b.discovery
                         FROM bank_products p JOIN bank_brands b ON b.brand_id = p.brand_id
                         WHERE p.status = 'live' AND b.approved AND p.decision IN ('pass','conditional')"""
                args: List[Any] = []
                if categories:
                    sql += " AND p.category = ANY(%s)"
                    args.append(list(categories))
                if not resale_allowed:
                    sql += " AND NOT p.is_resale_listing"
                cur.execute(sql, args)
                names = [d[0] for d in cur.description]
                rows = [dict(zip(names, r)) for r in cur.fetchall()]
                if not rows:
                    return []
                cur.execute("""SELECT product_id, variant_id, colour, colour_family, size_label, price, currency,
                                      sale_price, price_cad, price_usd, fx_rate_id, availability, ships_to, last_checked
                               FROM bank_variants WHERE product_id = ANY(%s)""", ([r["product_id"] for r in rows],))
                vnames = [d[0] for d in cur.description]
                by_p: Dict[str, List[dict]] = {}
                for v in cur.fetchall():
                    d = dict(zip(vnames, v))
                    for k in ("price", "sale_price", "price_cad", "price_usd"):
                        if d[k] is not None:
                            d[k] = float(d[k])
                    by_p.setdefault(d["product_id"], []).append(d)
        finally:
            conn.close()
        out = []
        for r in rows:
            out.append({"product_id": r["product_id"], "record": r["record"], "search_text": r["search_text"] or "",
                        "decision": r["decision"], "status": r["status"], "is_resale": r["is_resale_listing"],
                        "brand": {"brand_id": r["brand_id"], "name": r["name"], "approved": r["approved"],
                                  "independent_label": r["independent_label"], "unique_find": r["unique_find"],
                                  "style": bank_embed.brand_style({"discovery": r["discovery"]})},
                        "variants": by_p.get(r["product_id"], [])})
        return out

    def evidence(self, product_ids: List[str]) -> Dict[str, dict]:
        conn = events._conn()
        if conn is None or not product_ids:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT evidence_id, field, source_type, scope, quote, status, checked_at, sample_size,
                                      source_url FROM bank_evidence WHERE subject_id = ANY(%s)""", (product_ids,))
                names = [d[0] for d in cur.description]
                return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
        finally:
            conn.close()

    def standards(self, basic: str) -> List[dict]:
        conn = events._conn()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT slot, rank, product_id, note, price_line FROM bank_standards
                               WHERE basic = %s ORDER BY slot, rank""", (basic,))
                return [dict(zip(("slot", "rank", "product_id", "note", "price_line"), r)) for r in cur.fetchall()]
        finally:
            conn.close()


# ------------------------------------------------------------------ hard filters

def _material_req(req: str) -> Tuple[str, Optional[float], Optional[str]]:
    """'cotton 100' -> ('cotton', 100, None); 'leather: lambskin' -> ('leather', None, 'lambskin')."""
    req = req.strip().lower()
    m = re.match(r"^([a-z ]+?)\s*:\s*([a-z ]+)$", req)
    if m:
        return _fibre(m.group(1)), None, m.group(2).strip()
    m = re.match(r"^([a-z ]+?)\s+(\d{1,3})$", req)
    if m:
        return _fibre(m.group(1)), float(m.group(2)), None
    return _fibre(req), None, None


def material_ok(record: dict, reqs: List[str]) -> bool:
    if not reqs:
        return True
    comp = composition(record)
    if not comp:
        return False
    raw = " ".join(str(c.get("fibre", "")).lower() for c in comp) + " " + (record.get("name") or "").lower()
    for req in reqs:
        fibre, min_pct, sub = _material_req(req)
        if fibre == "natural_fibre":
            if sum(float(c.get("percent") or 0) for c in comp if _fibre(c.get("fibre")) in NATURAL) < 100:
                return False
            continue
        share = sum(float(c.get("percent") or 0) for c in comp if _fibre(c.get("fibre")) == fibre)
        if share <= 0:
            return False
        if min_pct is not None and share < min_pct:
            return False
        if min_pct is None and share < 50:
            return False                     # "a wool coat" means mostly wool
        if sub and sub not in raw:
            return False
    return True


def exclusion_ok(record: dict, excludes: List[str]) -> bool:
    if not excludes:
        return True
    if not composition(record):
        return False                         # unknown composition is removed when an exclusion is set
    text = all_material_text(record)
    fibres = {_fibre(w) for w in re.findall(r"[a-z]+(?: wool| cotton)?", text)}
    for ex in excludes:
        ex = ex.lower()
        bad = ANIMAL if ex in ("animal_material", "animal_free") else {_fibre(ex)}
        if fibres & bad or any(b in text for b in bad):
            return False
    return True


def origin_ok(record: dict, origin: Optional[str]) -> bool:
    if not origin:
        return True
    f = _node(record, "construction.country_of_assembly")
    if f.get("status") != "confirmed" or not f.get("value"):
        return False                         # origin counts only when confirmed for this product
    country = str(f["value"]).lower()
    if origin == "europe":
        return country in EUROPE or country in ("united kingdom",)
    aliases = {"uk": {"united kingdom", "england", "scotland", "wales", "uk"},
               "usa": {"united states", "usa"}, "united states": {"united states", "usa"}}
    return country in aliases.get(origin, {origin})


def ethics_ok(record: dict, ethics: List[str]) -> bool:
    for e in ethics or []:
        if e == "factory_transparency":
            f = _node(record, "construction.factory")
            if f.get("status") != "confirmed" or f.get("scope") == "brand_wide":
                return False
        elif e in ("animal_free",):
            continue                         # handled as an exclusion
        elif e in ("humane_sourcing", "certified", "no_fur"):
            node = record.get("animal_welfare") if e != "certified" else record.get("certifications")
            facts = [v for v in (node or {}).values() if isinstance(v, dict)] if isinstance(node, dict) else \
                    [v for v in (node or []) if isinstance(v, dict)]
            if not any(f.get("status") == "confirmed" and f.get("scope") != "brand_wide" for f in facts):
                return False
    return True


def gated_state(record: dict, need: str) -> str:
    """satisfied / unknown / conflicts for an evidence-gated requirement."""
    path, minimum = GATED[need]
    f = _node(record, path)
    if not f or f.get("status") in (None, "unknown"):
        if need == "room_through_bust" and (record.get("fit") or {}).get("measurements_by_size"):
            return "satisfied"               # garment measurements count for this one
        return "unknown"
    if f.get("status") == "conflicts":
        return "conflicts"
    v = f.get("value")
    negative = v in (False, "sheer", "semi_sheer", "itchy", "pills", "shrinks", "tight")
    if f.get("status") in ("confirmed", "from_photo") and negative:
        return "conflicts"
    if f.get("status") == "confirmed" and SOURCE_RANK.get(f.get("source_type"), -1) >= SOURCE_RANK[minimum]:
        return "satisfied"
    return "unknown"


def pick_variant(cand: dict, brief: dict, currency: str, market: str) -> Tuple[Optional[dict], Optional[str], dict]:
    """The variant to show, or the filter that removed every variant. Also returns a summary."""
    vs = cand["variants"]
    colour = brief.get("colour")
    if colour:
        vs = [v for v in vs if (v.get("colour_family") or colour_family(v.get("colour"))) == colour]
        if not vs:
            return None, "colour", {}
    size = brief.get("size")
    if size:
        vs = [v for v in vs if str(v.get("size_label", "")).strip().upper() == str(size).upper()
              or v.get("size_label") in ("All sizes",)]
        if not vs:
            return None, "size", {}
    shippable = [v for v in vs if not v.get("ships_to") or market in v.get("ships_to")]
    if not shippable:
        return None, "region", {}
    vs = [v for v in shippable if v.get("availability") in SHOWABLE]
    if not vs:
        return None, "stock", {}
    vs = [v for v in vs if _age_hours(v.get("last_checked")) <= PRICE_HIDE_DAYS * 24]
    if not vs:
        return None, "freshness", {}
    priced = [v for v in vs if _price_in(v, currency) is not None]
    if not priced:
        return None, "price", {}
    budget = brief.get("budget") or {}
    if isinstance(budget, dict) and budget.get("amount") and budget.get("hard_max", True):
        priced = [v for v in priced if _price_in(v, currency) <= float(budget["amount"]) + 1e-6]
        if not priced:
            return None, "budget", {}
    best = min(priced, key=lambda v: _price_in(v, currency))
    same_colour = [v for v in cand["variants"] if v.get("colour") == best.get("colour")]
    in_stock = [v for v in same_colour if v.get("availability") in SHOWABLE]
    return best, None, {"sizes_in_stock": sorted({v.get("size_label") for v in in_stock}),
                        "sizes_total": len({v.get("size_label") for v in same_colour}) or 1}


HARD_ORDER = ("show_rule", "category", "resale", "material", "exclusion", "origin", "ethics", "gated_conflict",
              "colour", "size", "region", "stock", "freshness", "price", "budget")


def hard_filter(cand: dict, brief: dict, currency: str, market: str, skip: Tuple[str, ...] = ()) -> Tuple[Optional[str], Optional[dict], dict]:
    """(first failing filter or None, shown variant, variant summary)."""
    rec = cand["record"]
    if "show_rule" not in skip:
        comp = composition(rec)
        score = (rec.get("filo") or {}).get("filo_score")
        if (cand.get("status") != "live" or not cand["brand"].get("approved")
                or cand.get("decision") not in ("pass", "conditional") or not comp
                or score is None or score < catalog.QUALITY_FLOOR * 10
                or synthetic_pct(comp) >= catalog.MAX_SYNTHETIC):
            return "show_rule", None, {}
    cat = brief.get("category")
    if cat and "category" not in skip and rec.get("category") not in CATEGORY_CHILDREN.get(cat, {cat}):
        return "category", None, {}
    if "resale" not in skip and cand.get("is_resale") and not brief.get("resale_allowed"):
        return "resale", None, {}
    if "material" not in skip and not material_ok(rec, brief.get("material") or []):
        return "material", None, {}
    if "exclusion" not in skip and not exclusion_ok(rec, brief.get("exclude") or []):
        return "exclusion", None, {}
    if "origin" not in skip and not origin_ok(rec, brief.get("origin")):
        return "origin", None, {}
    if "ethics" not in skip and not ethics_ok(rec, brief.get("ethics") or []):
        return "ethics", None, {}
    for need in _gated_needs(brief):
        if gated_state(rec, need) == "conflicts":
            return "gated_conflict", None, {}
    b = dict(brief)
    for k in ("colour", "size", "budget"):
        if k in skip:
            b.pop(k, None)
    v, why, summary = pick_variant(cand, b, currency, market)
    if v is None and why not in skip:
        return why, None, {}
    return None, v, summary


def _gated_needs(brief: dict) -> List[str]:
    wants = list(brief.get("performance") or []) + list(brief.get("fit_need") or [])
    if brief.get("opacity") == "not_sheer":
        wants.append("opaque")
    return list(dict.fromkeys(w for w in wants if w in GATED))


# ------------------------------------------------------------------ scores

def _weight_of(ev: Optional[dict], fact: dict) -> float:
    st = fact.get("source_type")
    if fact.get("status") in (None, "unknown", "conflicts"):
        return 0.0
    if st == "customer_reported":
        n = (ev or {}).get("sample_size") or 0
        w = 0.4 + 0.3 * min(n, 30) / 30
    else:
        w = SOURCE_WEIGHT.get(st, 0.3)
    w *= SCOPE_MULT.get(fact.get("scope") or "this_style", 1.0)
    if ev:
        if ev.get("status") == "source_failed":
            return 0.0
        if ev.get("status") == "stale" or _age_hours(ev.get("checked_at")) > COMPOSITION_FRESH_DAYS * 24:
            w *= 0.5
    return w


def requirement_score(rec: dict, req: str) -> Tuple[float, str, List[str]]:
    """(0..1, status label, evidence ids) for one must or nice-to-have."""
    tags = {t["tag"]: t["fact"] for t in ((rec.get("fit") or {}).get("fit_tags") or [])}
    if req in tags:
        f = tags[req]
        if f.get("status") == "confirmed":
            return 1.0, "confirmed", f.get("evidence_ids") or []
        if f.get("status") == "from_photo":
            return 0.6, "from_photo", f.get("evidence_ids") or []
    if req in GATED:
        state = gated_state(rec, req)
        f = _node(rec, GATED[req][0])
        if state == "satisfied" and not f.get("evidence_ids"):
            return 1.0, "measured", []
        return (1.0 if state == "satisfied" else 0.0), state, f.get("evidence_ids") or []
    comp = composition(rec) or []
    comp_f = _node(rec, "materials.composition")
    main = {_fibre(c["fibre"]): float(c.get("percent") or 0) for c in comp}
    if req == "lightweight":
        w = _node(rec, "materials.fabric_weight")
        if w.get("status") == "confirmed" and isinstance(w.get("value"), (int, float)):
            unit, val = (w.get("unit") or "").lower(), float(w["value"])
            light = (val <= 11 if "oz" in unit else val <= 170)
            return (1.0 if light else 0.0), ("confirmed" if light else "not_met"), w.get("evidence_ids") or []
        return 0.0, "unknown", []
    if req == "stretch":
        s = _node(rec, "fit.stretch_and_recovery")
        if s.get("status") == "confirmed":
            ok = str(s.get("value")) not in ("rigid", "none", "no_stretch")
            return (1.0 if ok else 0.0), ("confirmed" if ok else "not_met"), s.get("evidence_ids") or []
        if main.get("elastane", 0) >= 1:
            return 1.0, "confirmed", comp_f.get("evidence_ids") or []
        return 0.0, "unknown", []
    if req == "warm" and sum(p for f, p in main.items() if f in WARM) >= 50:
        return 0.6, "likely_from_fibre", comp_f.get("evidence_ids") or []
    if req == "breathable" and sum(p for f, p in main.items() if f in BREATHABLE) >= 80:
        return 0.6, "likely_from_fibre", comp_f.get("evidence_ids") or []
    return 0.0, "unknown", []


def evidence_confidence(rec: dict, musts: List[str], ev_map: Dict[str, dict]) -> float:
    claims = [_node(rec, "materials.composition"), _node(rec, "construction.country_of_assembly")]
    tags = {t["tag"]: t["fact"] for t in ((rec.get("fit") or {}).get("fit_tags") or [])}
    for m in musts:
        if m in tags:
            claims.append(tags[m])
        elif m in GATED:
            claims.append(_node(rec, GATED[m][0]))
    scores = []
    for f in claims:
        ids = f.get("evidence_ids") or []
        ev = ev_map.get(ids[0]) if ids else None
        scores.append(_weight_of(ev, f))
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def confidence_label(c: float) -> str:
    return "High" if c >= 0.75 else ("Medium" if c >= 0.45 else "Low")


def aesthetic_score(cand: dict, brief: dict, qvec, vecs: Dict[str, List[float]], qwords: List[str]) -> Optional[float]:
    if not (brief.get("aesthetic") or brief.get("vibe_text")):
        return None
    v = vecs.get(cand["product_id"])
    if qvec is not None and v:
        c = bank_embed.cosine(qvec, v)
        return max(0.0, min(1.0, (c - 0.2) / 0.4))
    text = " ".join([cand["record"].get("name") or "", cand.get("search_text") or "", cand["brand"].get("style") or ""])
    return bank_embed.lexical(qwords, text) if qwords else None


def match_score(cand: dict, brief: dict, variant: dict, summary: dict, currency: str,
                aesthetic: Optional[float], set_aside: Tuple[str, ...] = ()) -> Tuple[float, List[dict], List[str]]:
    """set_aside: evidence-gated musts with no proof yet. They decide the trade-off slot, not the score."""
    rec = cand["record"]
    nice = set(brief.get("nice") or [])
    reqs = [r for r in (list(brief.get("silhouette") or []) + list(brief.get("performance") or [])
                        + list(brief.get("fit_need") or [])) if r]
    if brief.get("opacity") == "not_sheer" and "opaque" not in reqs:
        reqs.append("opaque")
    reqs = [r for r in dict.fromkeys(reqs) if r not in set_aside]
    musts = [r for r in reqs if r not in nice]
    nices = [r for r in reqs if r in nice]
    matches, missing = [], []
    parts: Dict[str, float] = {}
    for group, items in (("musts_met", musts), ("nice_to_haves", nices)):
        if not items:
            continue
        vals = []
        for r in items:
            s, status, ids = requirement_score(rec, r)
            vals.append(s)
            if s > 0:
                matches.append({"requirement": r, "status": status, "evidence_ids": ids})
            else:
                missing.append(r)
        parts[group] = sum(vals) / len(vals)
    if aesthetic is not None:
        parts["aesthetic"] = aesthetic
    budget = brief.get("budget") or {}
    price = _price_in(variant, currency)
    if isinstance(budget, dict) and budget.get("amount") and price is not None:
        ratio = price / float(budget["amount"])
        parts["budget_fit"] = 1.0 if ratio >= 0.5 else 0.6 + 0.8 * ratio
    if brief.get("size"):
        parts["availability"] = 1.0
    else:
        parts["availability"] = len(summary.get("sizes_in_stock") or []) / max(1, summary.get("sizes_total") or 1)
    total_w = sum(WEIGHTS[k] for k in parts)
    score = 100.0 * sum(WEIGHTS[k] * v for k, v in parts.items()) / total_w if total_w else 0.0
    return round(score, 1), matches, missing


# ------------------------------------------------------------------ the search

def search(brief: dict, market: str = "CA", source=None, log_gap: bool = True) -> Dict[str, Any]:
    source = source or DbSource()
    brief = dict(brief or {})
    currency = "USD" if market == "US" else "CAD"
    if isinstance(brief.get("budget"), dict) and brief["budget"].get("currency") in ("CAD", "USD"):
        currency = brief["budget"]["currency"]
    count = int(brief.get("count") or DEFAULT_SHORTLIST)
    count = max(1, min(count, MAX_COUNT))
    cat = brief.get("category")
    cats = sorted(CATEGORY_CHILDREN.get(cat, {cat})) if cat else None
    cands = source.candidates(cats, bool(brief.get("resale_allowed")))
    funnel: Dict[str, int] = {}
    passed = []
    for c in cands:
        why, v, summary = hard_filter(c, brief, currency, market)
        if why:
            funnel[why] = funnel.get(why, 0) + 1
            continue
        passed.append((c, v, summary))

    standard = None
    if brief.get("mode") == "standard" and brief.get("basic"):
        standard = _standard(brief, source, cands, currency, market)
        if standard and standard["results"]:
            return _finish(standard["results"], brief, funnel, len(cands), source, mode="standard",
                           note=standard.get("note"))

    if not passed:
        return _no_match(brief, cands, funnel, currency, market, log_gap)

    qwords = bank_embed.expand(brief.get("vibe_text") or "", brief.get("aesthetic") or []) \
        if (brief.get("aesthetic") or brief.get("vibe_text")) else []
    qvec = bank_embed.query_vector(brief.get("vibe_text") or " ".join(brief.get("aesthetic") or [])) \
        if (brief.get("aesthetic") or brief.get("vibe_text")) else None
    vecs = bank_embed.vectors([c["product_id"] for c, _, _ in passed]) if qvec is not None else {}
    gated = _gated_needs(brief)
    scored = []
    ev_map = source.evidence([c["product_id"] for c, _, _ in passed])
    for c, v, summary in passed:
        aest = aesthetic_score(c, brief, qvec, vecs, qwords)
        unknown_gated = [g for g in gated if gated_state(c["record"], g) == "unknown"]
        m, matches, missing = match_score(c, brief, v, summary, currency, aest, tuple(unknown_gated))
        musts = [r for r in (brief.get("silhouette") or []) + _gated_needs(brief)]
        conf = evidence_confidence(c["record"], musts, ev_map)
        scored.append({"cand": c, "variant": v, "summary": summary, "match": m, "matches": matches,
                       "missing": missing, "quality": (c["record"].get("filo") or {}).get("filo_score"),
                       "evidence": conf, "unknown_gated": unknown_gated})
    return _finish(_rank_and_slot(scored, brief, count, currency), brief, funnel, len(cands), source)


def _sort_key(s: dict):
    return (-math.floor(s["match"] / 5.0), -s["evidence"], -(s["quality"] or 0), -s["match"])


def _rank_and_slot(scored: List[dict], brief: dict, count: int, currency: str) -> List[dict]:
    eligible = [s for s in scored if not s["unknown_gated"] and s["match"] >= MIN_MATCH]
    trade = [s for s in scored if s["unknown_gated"] and s["match"] >= MIN_MATCH]
    eligible.sort(key=_sort_key)
    trade.sort(key=_sort_key)
    max_brand = 2 if count <= 5 else 3
    picked: List[dict] = []
    per_brand: Dict[str, int] = {}

    def add(s, slot, label):
        b = s["cand"]["brand"]["brand_id"]
        if s in picked or per_brand.get(b, 0) >= max_brand or len(picked) >= count:
            return False
        s["slot"], s["slot_label"] = slot, label
        picked.append(s)
        per_brand[b] = per_brand.get(b, 0) + 1
        return True

    top = next((s for s in eligible if s["evidence"] >= 0.45), None)
    if top:
        add(top, "best_overall", "Best overall")
        price = _price_in(top["variant"], currency) or 0
        value = next((s for s in eligible if s is not top and (_price_in(s["variant"], currency) or 1e9) < price
                      and (s["quality"] or 0) >= (top["quality"] or 0) - 10), None)
        if value:
            add(value, "best_value", "Best value")
    indie = next((s for s in eligible if s not in picked and (s["cand"]["brand"].get("independent_label")
                                                             or (s["cand"]["record"].get("filo") or {}).get("is_independent_label"))
                  and (not picked or s["match"] >= picked[0]["match"] - 5)), None)
    if indie:
        add(indie, "best_independent", "Best independent label")
    key_req = brief.get("key_requirement")
    if key_req:
        bev = max((s for s in eligible if s not in picked and any(m["requirement"] == key_req for m in s["matches"])),
                  key=lambda s: s["evidence"], default=None)
        if bev and bev["evidence"] >= 0.45:
            add(bev, "best_evidence_for", f"Best evidence for {GATED_LABEL.get(key_req, key_req.replace('_', ' '))}")
    for s in eligible:
        if len(picked) >= count - (1 if trade else 0):
            break
        add(s, "more", "Also good")
    # Two price points when the picks are comparable.
    prices = {round(_price_in(s["variant"], currency) or 0, -1) for s in picked}
    if len(picked) >= 3 and len(prices) < 2:
        alt = next((s for s in eligible if s not in picked
                    and round(_price_in(s["variant"], currency) or 0, -1) not in prices
                    and s["match"] >= picked[-1]["match"] - 10), None)
        if alt:
            gone = picked.pop()
            b = gone["cand"]["brand"]["brand_id"]
            per_brand[b] -= 1
            add(alt, "more", "Also good")
    if trade and len(picked) < count:
        t = trade[0]
        missing = ", ".join(GATED_LABEL.get(g, g) for g in t["unknown_gated"])
        if add(t, "trade_off", "Trade-off"):
            t["trade_off_missing"] = t["unknown_gated"]
            t["note_missing"] = f"No proof yet for: {missing}."
    return picked


def _standard(brief: dict, source, cands: List[dict], currency: str, market: str) -> Optional[dict]:
    rows = source.standards(brief["basic"])
    if not rows:
        return None
    by_id = {c["product_id"]: c for c in cands}
    out = []
    labels = {"best_overall": "Best overall", "best_under": "Best under", "best_splurge": "Best splurge",
              "alternate": "Alternative"}
    alts = 0
    for r in sorted(rows, key=lambda r: (["best_overall", "best_under", "best_splurge", "alternate"].index(r["slot"]), r["rank"])):
        c = by_id.get(r["product_id"])
        if not c:
            continue
        why, v, summary = hard_filter(c, brief, currency, market)
        if why:
            continue
        if r["slot"] == "alternate":
            if alts >= 2:
                continue
            alts += 1
        label = labels[r["slot"]] + (f" C${float(r['price_line']):.0f}" if r["slot"] == "best_under" and r.get("price_line") else "")
        out.append({"cand": c, "variant": v, "summary": summary, "match": 100.0, "matches": [], "missing": [],
                    "quality": (c["record"].get("filo") or {}).get("filo_score"), "evidence": 0.0,
                    "unknown_gated": [], "slot": r["slot"], "slot_label": label, "standard_note": r.get("note")})
    ev_map = source.evidence([s["cand"]["product_id"] for s in out])
    for s in out:
        s["evidence"] = evidence_confidence(s["cand"]["record"], _gated_needs(brief), ev_map)
    return {"results": out}


def _no_match(brief: dict, cands: List[dict], funnel: Dict[str, int], currency: str, market: str,
              log_gap: bool) -> Dict[str, Any]:
    """Nothing passed: say what removed things and what could flex. Never relax silently."""
    flex = []
    for filt in ("budget", "colour", "size", "origin", "material", "exclusion"):
        if not funnel.get(filt) and not (filt == "budget" and funnel.get("price")):
            continue
        would = [c for c in cands if hard_filter(c, brief, currency, market, skip=(filt,))[0] is None]
        if not would:
            continue
        option = {"filter": filt, "would_pass": len(would)}
        if filt == "budget":
            prices = [p for c in would for v in c["variants"] if v.get("availability") in SHOWABLE
                      for p in [_price_in(v, currency)] if p is not None]
            if prices:
                option["lowest_price"] = round(min(prices), 2)
                option["currency"] = currency
        if filt == "colour":
            option["colours"] = sorted({colour_family(v.get("colour")) or v.get("colour") for c in would
                                        for v in c["variants"] if v.get("availability") in SHOWABLE})[:8]
        flex.append(option)
    conflicts = stylist_brief.conflicts_of(brief.get("silhouette") or []) if brief.get("silhouette") else []
    if log_gap:
        _log_gap(brief)
    return {"nothing_passed": True, "results": [], "funnel": funnel, "considered": len(cands),
            "flex_options": flex, "conflicts": conflicts, "mode": brief.get("mode", "discovery"),
            "brief": brief}


def gap_signature(brief: dict) -> str:
    keep = {k: brief.get(k) for k in ("category", "colour", "silhouette", "material", "exclude", "origin",
                                       "performance", "fit_need", "ethics") if brief.get(k)}
    b = brief.get("budget")
    if isinstance(b, dict) and b.get("amount"):
        keep["budget_band"] = int(float(b["amount"]) // 50 * 50)
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16] + "|" + \
        "|".join(str(keep.get(k)) for k in ("category", "colour") if keep.get(k))


def _log_gap(brief: dict):
    conn = events._conn()
    if conn is None:
        return
    keep = {k: v for k, v in brief.items() if k not in ("vibe_text", "unmatched_phrases")}
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO bank_gaps (signature, brief_structured) VALUES (%s, %s::jsonb)
                           ON CONFLICT (signature) DO UPDATE SET count = bank_gaps.count + 1, last_seen = now()""",
                        (gap_signature(brief), json.dumps(keep, default=str)))
    except Exception:                       # noqa: BLE001
        pass
    finally:
        conn.close()


def _card(s: dict, currency: str, ev_map: Dict[str, dict]) -> dict:
    c, v, rec = s["cand"], s["variant"], s["cand"]["record"]
    comp = composition(rec) or []
    comp_f = _node(rec, "materials.composition")
    origin = _node(rec, "construction.country_of_assembly")
    age = _age_hours(v.get("last_checked"))
    claims = [{"requirement": "composition", "status": "confirmed",
               "value": ", ".join(f"{int(x['percent']) if float(x['percent']).is_integer() else x['percent']}% {x['fibre']}" for x in comp),
               "evidence_ids": comp_f.get("evidence_ids") or []}]
    if origin.get("status") == "confirmed":
        claims.append({"requirement": "origin", "status": "confirmed", "value": origin.get("value"),
                       "evidence_ids": origin.get("evidence_ids") or []})
    for m in s["matches"]:
        claims.append(dict(m))
    for cl in claims:
        evs = [ev_map[i] for i in cl.get("evidence_ids") or [] if i in ev_map]
        cl["quote"] = next((e.get("quote") for e in evs if e.get("quote")), None)
        cl["source"] = {"brand_reported": "Brand says", "verified": "Checked", "customer_reported": "Buyers say",
                        "inferred": "Filo's read"}.get(evs[0]["source_type"], "") if evs else ""
    images = rec.get("images") or []
    out = {
        "product_id": c["product_id"], "brand": c["brand"]["name"], "brand_id": c["brand"]["brand_id"],
        "name": rec.get("name"), "url": rec.get("product_url"), "image": images[0]["url"] if images else None,
        "category": rec.get("category"), "slot": s.get("slot"), "slot_label": s.get("slot_label"),
        "colour": v.get("colour"), "price": _price_in(v, currency), "currency": currency,
        "shop_price": {"amount": _current(v), "currency": v.get("currency")},
        "on_sale": v.get("sale_price") is not None, "sizes_in_stock": s["summary"].get("sizes_in_stock"),
        "stock_label": "In stock" if age <= PRICE_FRESH_HOURS else "Last seen in stock",
        "price_note": None if age <= PRICE_FRESH_HOURS else f"Price checked {int(age // 24)} days ago",
        "scores": {"match": s["match"], "quality": s["quality"], "evidence": s["evidence"],
                   "evidence_label": confidence_label(s["evidence"])},
        "claims": claims, "missing": s.get("missing") or [],
        "unique_find": bool((rec.get("filo") or {}).get("is_unique_find") or c["brand"].get("unique_find")),
        "independent_label": bool(c["brand"].get("independent_label") or (rec.get("filo") or {}).get("is_independent_label")),
    }
    if s.get("note_missing"):
        out["trade_off_note"] = s["note_missing"]
    if s.get("standard_note"):
        out["stylist_note"] = s["standard_note"]
    return out


def _finish(picked: List[dict], brief: dict, funnel: Dict[str, int], considered: int, source,
            mode: str = "discovery", note: Optional[str] = None) -> Dict[str, Any]:
    currency = "CAD"
    if isinstance(brief.get("budget"), dict) and brief["budget"].get("currency") == "USD":
        currency = "USD"
    ev_map = source.evidence([s["cand"]["product_id"] for s in picked])
    cards = [_card(s, currency, ev_map) for s in picked]
    pick_i = next((i for i, s in enumerate(picked) if s.get("slot") == "best_overall"), 0 if cards else None)
    for i, c in enumerate(cards):
        c["is_pick"] = i == pick_i
    asked = int(brief.get("count") or 0)
    result = {"nothing_passed": not cards, "results": cards, "funnel": funnel, "considered": considered,
              "mode": mode, "brief": brief,
              "fewer_than_asked": (asked and len(cards) < asked) and len(cards) or None}
    result["problems"] = validate(result, brief)
    return result


def validate(result: Dict[str, Any], brief: dict) -> List[str]:
    """The checks before anything reaches a shopper. Empty means good."""
    probs = []
    cards = result["results"]
    if cards and sum(1 for c in cards if c.get("is_pick")) != 1:
        probs.append("There must be exactly one Stylist's pick.")
    budget = brief.get("budget") or {}
    for c in cards:
        if isinstance(budget, dict) and budget.get("amount") and c["price"] is not None \
                and c["price"] > float(budget["amount"]) + 1e-6:
            probs.append(f"{c['product_id']} is over budget.")
        for cl in c["claims"]:
            if not cl.get("evidence_ids") and cl.get("status") not in ("likely_from_fibre", "measured"):
                probs.append(f"{c['product_id']}: '{cl['requirement']}' has no evidence.")
        for k in ("slot_label",):
            if c.get(k) and "—" in c[k]:
                probs.append("Em dash in shopper text.")
    ids = [c["product_id"] for c in cards]
    if len(ids) != len(set(ids)):
        probs.append("A product is shown twice.")
    return probs


def hunt(text: str, market: str = "CA", source=None) -> Dict[str, Any]:
    """Words in, results out. Uses the rule parser; Stage 5 swaps in Claude's brief."""
    brief = stylist_brief.parse(text, market)
    if brief.get("conflicts"):
        return {"nothing_passed": True, "results": [], "brief": brief, "conflicts": brief["conflicts"],
                "needs_question": True, "funnel": {}, "considered": 0}
    return search(brief, market, source)
