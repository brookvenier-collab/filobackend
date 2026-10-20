"""Turns a product page into facts, each with the exact sentence it came from.

Two passes:
1. Rules (always): composition, fabric weight, lining and fill, where made,
   factory, care, cut and fit words, sizing advice, stretch, category, gender.
2. The AI tagger (only with ANTHROPIC_API_KEY): reads the page once and may
   fill fields the rules left empty. Every AI fact must quote the page, and the
   quote is checked against the page text. A fact whose quote isn't on the page
   is thrown away. The AI never overrides a rule-based fact.

Nothing here guesses. "Made in TN" is not Tunisia and not Tennessee; it stays
unknown with the raw words kept for Brooklyn to decide. "180g" is not turned
into GSM. A photo is never used for material or origin.

A fact here is a dict:
  {field, value, unit, status, source_type, scope, quote, raw, by}
bank_import turns them into product records and evidence entries.
"""
import json
import logging
import os
import re
import urllib.request
from typing import Dict, List, Optional

import fabric

log = logging.getLogger("filo.bank_facts")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TAGGER_MODEL = os.environ.get("STYLIST_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5"
TAGGER_TIMEOUT = 20

COUNTRIES = {
    # name or common form -> canonical country
    "portugal": "Portugal", "italy": "Italy", "spain": "Spain", "france": "France",
    "germany": "Germany", "united kingdom": "United Kingdom", "uk": "United Kingdom",
    "great britain": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom",
    "ireland": "Ireland", "netherlands": "Netherlands", "the netherlands": "Netherlands",
    "belgium": "Belgium", "denmark": "Denmark", "sweden": "Sweden", "norway": "Norway",
    "finland": "Finland", "switzerland": "Switzerland", "austria": "Austria", "poland": "Poland",
    "lithuania": "Lithuania", "latvia": "Latvia", "estonia": "Estonia", "romania": "Romania",
    "bulgaria": "Bulgaria", "greece": "Greece", "czech republic": "Czech Republic",
    "czechia": "Czech Republic", "slovakia": "Slovakia", "hungary": "Hungary", "croatia": "Croatia",
    "slovenia": "Slovenia", "turkey": "Turkey", "türkiye": "Turkey", "tunisia": "Tunisia",
    "morocco": "Morocco", "egypt": "Egypt", "china": "China", "india": "India",
    "bangladesh": "Bangladesh", "vietnam": "Vietnam", "viet nam": "Vietnam", "cambodia": "Cambodia",
    "indonesia": "Indonesia", "sri lanka": "Sri Lanka", "pakistan": "Pakistan", "thailand": "Thailand",
    "japan": "Japan", "south korea": "South Korea", "korea": "South Korea", "taiwan": "Taiwan",
    "mongolia": "Mongolia", "peru": "Peru", "mexico": "Mexico", "canada": "Canada",
    "usa": "United States", "u.s.a.": "United States", "united states": "United States",
    "the usa": "United States", "america": "United States", "australia": "Australia",
    "new zealand": "New Zealand", "madagascar": "Madagascar", "philippines": "Philippines",
    "brazil": "Brazil", "argentina": "Argentina", "uruguay": "Uruguay", "ecuador": "Ecuador",
}

# Cut and fit words -> concept IDs in the ontology. Only unambiguous words:
# "short" is left out because "short sleeve" isn't a cropped garment.
FIT_WORDS = [
    (r"\bboxy\b|\bbox cut\b|\bsquare cut\b", "boxy"),
    (r"\boversized?\b|\boversize fit\b", "oversized"),
    (r"\brelaxed\b|\bloose(?:[- ]fit)?\b|\bslouchy\b|\broomy\b", "relaxed"),
    (r"\bslim(?:[- ]fit)?\b|\bfitted\b", "slim"),
    (r"\bstraight[- ]leg\b|\bstraight fit\b", "straight_leg"),
    (r"\bwide[- ]leg\b|\bwide leg opening\b|\bpalazzo\b", "wide_leg"),
    (r"\bhigh[- ](?:rise|waist(?:ed)?)\b", "high_rise"),
    (r"\bcropped\b|\bcrop\b", "cropped"),
    (r"\bdrop(?:ped)?[- ]shoulders?\b", "drop_shoulder"),
    (r"\blongline\b|\blong length\b", "longline"),
    (r"\btapered\b", "tapered"),
    (r"\bbarrel(?:[- ]leg)?\b", "barrel"),
]

CATEGORY_WORDS = [
    (r"\bleather (?:jacket|coat|bomber|blazer)\b|\b(?:lambskin|suede) (?:jacket|coat)\b", "leather_outerwear"),
    (r"\bsweatshirt\b|\bhoodie\b|\bcrewneck sweat", "sweatshirt"),
    (r"\bcardigan\b", "cardigan"),
    (r"\bjumper\b|\bsweater\b|\bpullover\b|\bknit(?:wear)?\b", "knitwear"),
    (r"\blong[- ]sleeve(?:d)? (?:t-?shirt|tee|top)\b|\bls t-?shirt\b", "long_sleeve"),
    (r"\bt-?shirt\b|\btee\b|\btshirt\b", "t_shirt"),
    (r"\btank\b|\bvest top\b|\bcamisole\b", "tank"),
    (r"\bjeans?\b(?! jacket)|\bdenim (?:trousers|pants)\b", "denim"),
    (r"\btrousers?\b|\bpants\b|\bchinos?\b|\bslacks\b", "trousers"),
    (r"\bjacket\b|\bcoat\b|\bparka\b|\bblazer\b|\bbomber\b|\btrench\b|\bovershirt\b|\bgilet\b", "outerwear"),
    (r"\bshirt\b|\bblouse\b", "shirt"),
    (r"\bdress\b", "dress"),
    (r"\bskirt\b", "skirt"),
]


def _lines(text: str) -> List[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def quote_is_on_page(quote: str, page_text: str) -> bool:
    q = _norm(quote)
    return bool(q) and len(q) >= 4 and q in _norm(page_text)


def _short(line: str, at: int = 0, width: int = 220) -> str:
    """A quote of at most `width` characters around position `at`, cut on word breaks."""
    if len(line) <= width:
        return line
    start = max(0, at - width // 3)
    piece = line[start:start + width]
    if start > 0:
        piece = piece.split(" ", 1)[-1]
    return piece.rsplit(" ", 1)[0] if start + width < len(line) else piece


def _fact(field, value, quote, unit=None, status="confirmed", raw=None, scope="this_style", by="rules"):
    f = {"field": field, "value": value if status != "unknown" else None, "unit": unit,
         "status": status, "source_type": "brand_reported", "scope": scope,
         "quote": quote, "by": by}
    if raw is not None:
        f["raw"] = raw
    if status == "unknown":
        f.pop("source_type")
    return f


def _country(words: str) -> Optional[str]:
    w = _norm(words).strip(" .,'")
    if w in COUNTRIES:
        return COUNTRIES[w]
    # "Jiangsu Province, China" / "Long Eaton, England": the last part names the country.
    for part in reversed(re.split(r",|\bin\b", w)):
        p = part.strip(" .")
        if p in COUNTRIES:
            return COUNTRIES[p]
    return None


def composition(lines: List[str]) -> List[dict]:
    """The shell composition, plus lining and fill when the page states them."""
    out = []
    for line in lines:
        if "%" not in line:
            continue
        segments = re.split(r"(?i)\b(lining|fill|filling|insulation|trim|rib|pocket(?:ing)?)\s*:", line)
        shell = segments[0]
        pairs = fabric.parse_composition(shell, allow_bare=False) if shell else []
        total = sum(p for _, p in pairs)
        if pairs and 95 <= total <= 105 and not any(f.get("field") == "materials.composition" for f in out):
            out.append(_fact("materials.composition", [{"fibre": k, "percent": p} for k, p in pairs],
                             _short(line, line.find("%"))))
        for i in range(1, len(segments) - 1, 2):
            label, rest = segments[i].lower(), segments[i + 1]
            field = "materials.lining" if label == "lining" else "materials.fill"
            parts = fabric.parse_composition(rest, allow_bare=False)
            value = [{"fibre": k, "percent": p} for k, p in parts] if parts else rest.strip(" ./")[:120]
            if not any(f["field"] == field for f in out):
                out.append(_fact(field, value, _short(line, line.lower().find(label))))
        if out:
            break
    return out


def fabric_weight(lines: List[str]) -> Optional[dict]:
    for line in lines:
        m = re.search(r"(\d{2,4}(?:[.,]\d)?)\s*(?:gsm|g/m2|g/m²|gm2|g per square met(?:re|er))\b", line, re.I)
        if m:
            return _fact("materials.fabric_weight", float(m.group(1).replace(",", ".")), _short(line, m.start()), unit="gsm")
        m = re.search(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*-?\s*oz\b", line, re.I)
        if m:
            return _fact("materials.fabric_weight", float(m.group(1).replace(",", ".")), _short(line, m.start()), unit="oz")
        m = re.search(r"(?<![\d.])(\d{2,4})\s*g\b(?!/)", line, re.I)
        if m and ("%" in line or re.search(r"(?i)weight|fabric|cotton|jersey", line)):
            # Grams, but the page doesn't say per square metre. Kept exactly as written.
            return _fact("materials.fabric_weight", float(m.group(1)), _short(line, m.start()), unit="g (as stated)")
    return None


def origin_and_factory(lines: List[str]) -> List[dict]:
    out = []
    for line in lines:
        m = re.search(r"(?i)\bmade by ([^.;\n]{3,120}?)(?: for [^.;\n]{3,120}?)? in ([^.;\n]{2,80})", line)
        if m:
            out.append(_fact("construction.factory", m.group(1).strip(), _short(line, m.start())))
            c = _country(m.group(2))
            out.append(_fact("construction.country_of_assembly", c, _short(line, m.start())) if c else
                       _fact("construction.country_of_assembly", None, _short(line, m.start()),
                             status="unknown", raw=m.group(2).strip()))
            return out
        m = re.search(r"(?i)\b(?:made|manufactured|produced|sewn|crafted|knitted) in ([A-Za-z][A-Za-z .,'&-]{1,60})", line)
        if m:
            words = re.split(r"(?i)\s+(?:from|with|using|by|and|since|for)\s+|[.;]", m.group(1))[0].strip(" ,")
            c = _country(words)
            out.append(_fact("construction.country_of_assembly", c, _short(line, m.start())) if c else
                       _fact("construction.country_of_assembly", None, _short(line, m.start()),
                             status="unknown", raw=words))
            return out
    return out


def care(lines: List[str]) -> Optional[dict]:
    for line in lines:
        if re.search(r"(?i)\b(machine wash|hand wash|dry clean|do not tumble|tumble dry|wash cold|wash at \d+)", line):
            return _fact("construction.care", line[:200], _short(line))
    return None


def fit_tags(name: str, lines: List[str]) -> List[dict]:
    out, seen = [], set()
    for line in [name or ""] + lines:
        for pattern, tag in FIT_WORDS:
            if tag in seen:
                continue
            m = re.search(pattern, line, re.I)
            if m:
                seen.add(tag)
                out.append(_fact(f"fit.fit_tags.{tag}", True, _short(line, m.start())))
    return out


def sizing_advice(lines: List[str]) -> Optional[dict]:
    for line in lines:
        m = re.search(r"(?i)\b(fits|runs) (large|small|big|true to size|oversized)\b|\bsize (up|down)\b", line)
        if m:
            return _fact("fit.sizing_advice", m.group(0).lower(), _short(line, m.start()))
    return None


def stretch(lines: List[str]) -> Optional[dict]:
    for line in lines:
        m = re.search(r"(?i)\brigid\b|\bnon[- ]stretch\b|\bno stretch\b", line)
        if m:
            return _fact("fit.stretch_and_recovery", "rigid", _short(line, m.start()))
        m = re.search(r"(?i)\b(comfort )?stretch\b", line)
        if m and re.search(r"(?i)elastane|spandex|lycra|stretch denim|stretch fabric", line):
            return _fact("fit.stretch_and_recovery", "stretch", _short(line, m.start()))
    return None


def category_of(name: str, breadcrumbs: List[str]) -> Optional[str]:
    text = " ".join([name or ""] + list(breadcrumbs or []))
    for pattern, cat in CATEGORY_WORDS:
        if re.search(pattern, name or "", re.I):
            return cat
    for pattern, cat in CATEGORY_WORDS:
        if re.search(pattern, text, re.I):
            return cat
    return None


def gender_of(name: str, url: str, breadcrumbs: List[str]) -> Optional[str]:
    t = " ".join([name or "", url or "", " ".join(breadcrumbs or [])]).lower()
    if "unisex" in t:
        return "unisex"
    if re.search(r"women|ladies|womens|female|\bher\b", t):
        return "women"
    if re.search(r"\bmen\b|\bmens\b|men's|\bmale\b", t):
        return "men"
    return None


def rule_facts(page) -> List[dict]:
    lines = _lines(page.structured_text) + _lines(page.page_text)
    facts = []
    facts += composition(lines)
    for f in (fabric_weight(lines), care(lines), sizing_advice(lines), stretch(lines)):
        if f:
            facts.append(f)
    facts += origin_and_factory(lines)
    facts += fit_tags(page.name, lines)
    return facts


# ------------------------------------------------------------------ AI tagger

AI_FIELDS = {
    "materials.composition": "fibre percentages of the main fabric, as [{fibre, percent}]",
    "materials.fabric_weight": "fabric weight as a number, with unit gsm, oz, or 'g (as stated)'",
    "materials.lining": "lining fabric",
    "materials.fabric_structure": "jersey, rib, interlock, twill, poplin, fine-gauge knit, waffle, etc.",
    "construction.country_of_assembly": "the country the garment was made in, only if the page states it",
    "construction.factory": "the named factory or maker",
    "construction.care": "care instructions",
    "fit.intended_length": "cropped, hip length, mid-thigh, etc.",
    "fit.neckline": "crew, V, scoop, mock, etc.",
    "fit.rise": "low, mid or high rise",
    "fit.model": "model height and size worn",
    "fit.sizing_advice": "runs small, fits large, size up, etc.",
    "fit.stretch_and_recovery": "rigid, some stretch, stretch",
}

_PROMPT = """You read a clothing product page and pull out facts.

Rules:
- Only facts the page states. Never guess, never use outside knowledge.
- Every fact needs "quote": the exact words from the page, copied character for character, 4 to 200 characters.
- If a field isn't stated, leave it out.
- Never infer the country from the brand's home country, the currency or the shop. Only "made in" style statements count.
- Don't convert units. Keep numbers as written.

Fields you may return:
{fields}

Reply with ONLY a JSON object: {{"facts": [{{"field": "...", "value": ..., "unit": "... or null", "quote": "..."}}]}}

The page:
---
{page}
---"""


def _call_model(prompt: str) -> Optional[str]:
    body = json.dumps({
        "model": TAGGER_MODEL,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt},
                     {"role": "assistant", "content": "{"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"content-type": "application/json", "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=TAGGER_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return "{" + data["content"][0]["text"]
    except Exception as exc:                # noqa: BLE001
        log.info("bank_facts: tagger call failed (%s)", exc)
        return None


# Swapped out in tests.
MODEL_CALL = _call_model


def ai_facts(page, have_fields) -> List[dict]:
    """Facts the AI found that the rules didn't, each with a quote proven to be on the page."""
    if not ANTHROPIC_API_KEY and MODEL_CALL is _call_model:
        return []
    wanted = {k: v for k, v in AI_FIELDS.items() if k not in have_fields}
    if not wanted:
        return []
    text = page.all_text[:12000]
    raw = MODEL_CALL(_PROMPT.format(fields="\n".join(f"- {k}: {v}" for k, v in wanted.items()), page=text))
    if not raw:
        return []
    try:
        items = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("facts", [])
    except ValueError:
        return []
    out, rejected = [], 0
    for it in items if isinstance(items, list) else []:
        field, quote = it.get("field"), it.get("quote") or ""
        if field not in wanted or it.get("value") in (None, "", []):
            continue
        if not quote_is_on_page(quote, page.all_text):
            rejected += 1
            continue
        if field == "construction.country_of_assembly":
            c = _country(str(it["value"]))
            named = c and any(re.search(r"\b%s\b" % re.escape(alias), _norm(quote))
                              for alias, canon in COUNTRIES.items() if canon == c)
            # The country itself must be written in the quote: "Made in TN" never becomes Tunisia.
            if not named or not re.search(r"(?i)\b(made|manufactured|produced|sewn|crafted|knitted)\b", quote):
                rejected += 1
                continue
            it["value"] = c
        if field == "materials.composition":
            pairs = fabric.parse_composition(quote, allow_bare=False)
            if not pairs or not 95 <= sum(p for _, p in pairs) <= 105:
                rejected += 1
                continue
            it["value"] = [{"fibre": k, "percent": p} for k, p in pairs]
        out.append(_fact(field, it["value"], quote.strip()[:220], unit=it.get("unit"), by="ai"))
        wanted.pop(field, None)
    if rejected:
        log.info("bank_facts: dropped %d AI facts whose quotes weren't on the page", rejected)
    return out


def all_facts(page) -> List[dict]:
    facts = rule_facts(page)
    have = {f["field"] for f in facts if f["status"] != "unknown"}
    facts += [f for f in ai_facts(page, have) if f["field"] not in have]
    return facts
