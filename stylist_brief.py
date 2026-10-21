"""Turns a shopper's words into a structured brief, without calling a model.

"white tee boxy not see thru under 100" ->
  {"category": "t_shirt", "colour": "white", "silhouette": ["boxy"], "opacity": "not_sheer",
   "performance": ["opaque"], "budget": {"amount": 100, "currency": "CAD", "hard_max": True}, ...}

Stage 5 lets Claude write the brief for anything conversational; this parser is the fast,
free first pass and the fallback when the model is unavailable. It reads Filo's vocabulary
(the concepts and phrases Brooklyn edits in Studio), fixes common typos, and lists any words
it couldn't place so they show up in Studio's backlog.

Brief fields (all optional):
  mode            "standard" (a basic, "just tell me") or "discovery"
  category        concept id: t_shirt, long_sleeve, knitwear, denim, trousers, outerwear, ...
  colour          colour family: white, black, navy, ...
  silhouette      [concept ids]   cut words, all musts
  material        ["cotton 100", "wool", "leather: lambskin"]   must contain (min %)
  exclude         [concept ids or fibres]   must not contain anywhere
  performance     [concept ids]   lightweight, opaque, warm...
  fit_need        [concept ids]   room_through_bust, waist_hip_ratio...
  opacity         "not_sheer"
  aesthetic       [concept ids, "reference_brand: Khaite"]
  ethics          [concept ids]
  origin          "europe" or a country name
  budget          {"amount": 100, "currency": "CAD", "hard_max": true}
  resale_allowed  true/false
  count           1..15
  size            "M", "28"...
  nice            [concept ids] nice-to-haves rather than musts
  vibe_text       the words to use for meaning search
  unmatched_phrases  words nothing in the vocabulary matched
  conflicts       [[a, b]] requirements that can't both be true
"""
import difflib
import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import events

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCH_CATEGORIES = ("t_shirt", "long_sleeve", "knitwear", "denim", "trousers", "outerwear")

# Shopper phrasing the seed vocabulary doesn't have yet. Same shape: phrase -> (dimension, concept).
EXTRA_PHRASES = {
    "tee": ("category", "t_shirt"), "tees": ("category", "t_shirt"), "t shirt": ("category", "t_shirt"),
    "jean": ("category", "denim"), "jeans": ("category", "denim"),
    "cardigan": ("category", "knitwear"), "knit": ("category", "knitwear"), "turtleneck": ("category", "knitwear"),
    "roll neck": ("category", "knitwear"), "crewneck": ("category", "knitwear"),
    "trench": ("category", "outerwear"), "blazer": ("category", "outerwear"), "chore jacket": ("category", "outerwear"),
    "chinos": ("category", "trousers"), "pant": ("category", "trousers"),
    "see thru": ("performance", "opaque"), "see through": ("performance", "opaque"),
    "show everything": ("performance", "opaque"), "show my bra": ("performance", "opaque"),
    "for my hips": ("fit_concern", "waist_hip_ratio"), "gape at the back": ("fit_concern", "waist_hip_ratio"),
    "gap at the back": ("fit_concern", "waist_hip_ratio"),
    "room through the bust": ("fit_concern", "room_through_bust"), "room in the bust": ("fit_concern", "room_through_bust"),
    "room for my chest": ("fit_concern", "room_through_bust"), "supply chain": ("ethics", "factory_transparency"),
    "transparent": ("ethics", "factory_transparency"), "humane": ("ethics", "humane_sourcing"), "waist gap": ("fit_concern", "waist_hip_ratio"),
    "big chest": ("fit_concern", "room_through_bust"), "busty": ("fit_concern", "room_through_bust"),
    "loose": ("silhouette", "relaxed"), "a bit loose": ("silhouette", "relaxed"), "baggy": ("silhouette", "oversized"),
    "crop": ("silhouette", "cropped"), "dropped shoulder": ("silhouette", "drop_shoulder"),
    "quiet luxury": ("aesthetic", "minimal"), "clean": ("aesthetic", "minimal"),
    "last": ("performance", "durable"), "lasts": ("performance", "durable"), "for years": ("performance", "durable"),
    "won't bag out": ("performance", "no_baggy_knees"), "wont bag out": ("performance", "no_baggy_knees"),
    "cozy": ("performance", "warm"), "cosy": ("performance", "warm"),
}
# Words that need opposite handling when negated ("not see-through" means opaque, not "not opaque").
NEGATION_MEANS_POSITIVE = {"see thru", "see through", "see-through", "sheer", "show everything", "itchy", "scratchy",
                           "pill", "shrink", "bag out", "stretch out"}

COLOURS = {
    "black": "black", "white": "white", "optic white": "white", "off white": "cream", "off-white": "cream",
    "cream": "cream", "ivory": "cream", "ecru": "cream", "oat": "cream", "natural": "cream", "bone": "cream",
    "grey": "grey", "gray": "grey", "charcoal": "grey", "heather grey": "grey", "navy": "navy", "blue": "blue",
    "indigo": "blue", "brown": "brown", "chocolate": "brown", "tan": "tan", "camel": "tan", "beige": "tan",
    "khaki": "tan", "sand": "tan", "green": "green", "olive": "green", "sage": "green", "forest": "green",
    "red": "red", "burgundy": "burgundy", "oxblood": "burgundy", "wine": "burgundy", "pink": "pink",
    "purple": "purple", "lilac": "purple", "yellow": "yellow", "mustard": "yellow", "orange": "orange",
    "rust": "orange", "stripe": "stripe", "striped": "stripe",
}
FIBRES = ["cotton", "organic cotton", "wool", "merino", "lambswool", "cashmere", "alpaca", "mohair", "linen", "hemp",
          "silk", "leather", "lambskin", "suede", "polyester", "nylon", "polyamide", "acrylic", "elastane", "spandex",
          "viscose", "lyocell", "tencel", "modal", "down", "fur", "shearling"]
COUNTRIES = ["italy", "portugal", "spain", "france", "england", "uk", "united kingdom", "scotland", "ireland", "wales",
             "japan", "canada", "usa", "united states", "turkey", "germany", "sweden", "denmark", "romania", "lithuania",
             "peru", "india", "china", "vietnam", "tunisia", "morocco", "bulgaria", "poland", "greece"]
NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "a few": 3, "a couple": 2, "a dozen": 12, "fifteen": 15, "twelve": 12, "twenty": 15}
STANDARD_HINTS = ("just need", "just tell me", "the perfect", "a good basic", "best basic", "go-to", "go to",
                  "i just want a good", "need a good", "a good ")
DIM_FIELD = {"silhouette": "silhouette", "performance": "performance", "fit_concern": "fit_need",
             "aesthetic": "aesthetic", "ethics": "ethics"}


def _norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^\w$%'.\- ]+", " ", t)
    t = re.sub(r"\bt[- ]?shirts?\b", "t shirt", t)
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def _ontology() -> dict:
    with open(os.path.join(HERE, "bank_data", "fashion_ontology.json"), encoding="utf-8") as f:
        return json.load(f)


def phrases(language: str = "en") -> Dict[str, Tuple[str, str]]:
    """phrase -> (dimension, concept). Studio's edits in the database win over the seed file."""
    out: Dict[str, Tuple[str, str]] = {}
    for dim, cs in _ontology()["concepts"].items():
        for c in cs:
            for p in c.get(language, []):
                if "[" not in p and "$X" not in p:
                    out[p.lower()] = (dim, c["id"])
    conn = events._conn()
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT p.phrase, c.dimension, c.concept_id, p.removed FROM bank_concept_phrases p
                               JOIN bank_concepts c ON c.concept_id = p.concept_id WHERE p.language = %s""", (language,))
                for phrase, dim, cid, removed in cur.fetchall():
                    if removed:
                        out.pop(phrase.lower(), None)
                    else:
                        out[phrase.lower()] = (dim, cid)
        except Exception:                   # noqa: BLE001
            pass
        finally:
            conn.close()
    for p, v in EXTRA_PHRASES.items():
        out.setdefault(p, v)
    return out


def conflicts_of(concepts: List[str]) -> List[List[str]]:
    s = set(concepts)
    out = []
    for r in _ontology()["relations"]:
        if r["type"] == "conflicts_with" and r["from"] in s and r["to"] in s:
            out.append([r["from"], r["to"]])
    return out


TYPO_TARGETS = {"jeans", "denim", "trousers", "pants", "sweater", "jumper", "cardigan", "knit", "jacket", "coat",
                "blazer", "trench", "shirt", "tees", "boxy", "oversized", "cropped", "relaxed", "slim", "baggy",
                "hips", "waist", "bust", "chest", "shoulders", "linen", "cotton", "wool", "merino", "cashmere",
                "leather", "suede", "black", "white", "cream", "navy", "beige", "olive", "under", "through"}


def _fix_typos(text: str, vocab: List[str]) -> str:
    """Fix near-misses of clothing words only ("jeens" -> "jeans"), never ordinary words."""
    out = []
    for w in text.split():
        if len(w) >= 4 and w.isalpha() and w not in TYPO_TARGETS and w not in _COMMON:
            cands = [c for c in difflib.get_close_matches(w, TYPO_TARGETS, n=3, cutoff=0.8)
                     if c[0] == w[0] and abs(len(c) - len(w)) <= 1
                     and not (c.startswith(w) or w.startswith(c))]
            out.append(cands[0] if cands else w)
        else:
            out.append(w)
    return " ".join(out)


def parse(text: str, market: str = "CA") -> dict:
    vocab = phrases()
    raw = text or ""
    t = _fix_typos(_norm(raw), list(vocab))
    brief: Dict[str, object] = {"mode": "discovery", "silhouette": [], "material": [], "exclude": [],
                                "performance": [], "fit_need": [], "aesthetic": [], "ethics": [], "nice": []}
    used = []                                   # character spans already understood

    def take(m):
        used.append(m.span())

    # Budget: "under 100", "less than $150 CAD", "$80 max".
    m = re.search(r"(?:under|less than|below|max(?:imum)?|up to|no more than|<)\s*(?:c\$|us\$|\$)?\s*(\d{2,5})(?:\s*(cad|usd|dollars))?", t) \
        or re.search(r"(?:c\$|us\$|\$)\s*(\d{2,5})\s*(?:max|or less)(?:\s*(cad|usd))?", t)
    if m:
        named = (m.group(2) or "").upper()
        if "us$" in m.group(0) or named == "USD":
            cur = "USD"
        elif "c$" in m.group(0) or named == "CAD":
            cur = "CAD"
        else:
            cur = "USD" if market == "US" else "CAD"
        brief["budget"] = {"amount": float(m.group(1)), "currency": cur, "hard_max": True}
        take(m)
    # Fibre percentages: "100% cotton".
    for m in re.finditer(r"(\d{2,3})\s*%\s*(organic cotton|cotton|wool|merino|cashmere|linen|silk|hemp)", t):
        fibre = "cotton" if "cotton" in m.group(2) else m.group(2)
        brief["material"].append(f"{fibre} {m.group(1)}")
        take(m)
    # Exclusions: "no wool", "not polyester", "without animal materials", "wool-free".
    for m in re.finditer(r"\b(?:no|not|without|zero|avoid|never)\s+((?:animal materials?|animal products|"
                         + "|".join(FIBRES) + r"))\b", t):
        word = m.group(1)
        brief["exclude"].append("animal_material" if word.startswith("animal") else word)
        take(m)
    for m in re.finditer(r"\b(" + "|".join(FIBRES) + r")[- ]free\b", t):
        brief["exclude"].append(m.group(1))
        take(m)
    if re.search(r"\b(vegan|animal[- ]free)\b", t):
        brief["exclude"].append("animal_material")
    # Origin.
    m = re.search(r"\b(?:made in|from|produced in|sewn in)\s+(europe|the eu|eu|" + "|".join(COUNTRIES) + r")\b", t) \
        or re.search(r"\b(european)[- ]made\b|\bmade in (europe)\b", t)
    if m:
        place = next(g for g in m.groups() if g)
        brief["origin"] = "europe" if place in ("europe", "the eu", "eu", "european") else place
        take(m)
    # Size.
    m = re.search(r"\bsize\s+([0-9]{1,2}|xxs|xs|s|m|l|xl|xxl|small|medium|large)\b", t)
    if m:
        brief["size"] = {"small": "S", "medium": "M", "large": "L"}.get(m.group(1), m.group(1).upper())
        take(m)
    # Count: "15 lambskin jackets", "five white tees".
    m = re.search(r"(?:^|\b(?:me|find|show|give|want|need|get|see)\s+)(\d{1,2})(?=\s+(?!%|oz|gsm|cm|in\b)[a-z])", t) or \
        re.search(r"\b(" + "|".join(NUMBERS) + r")(?=\s+[a-z])", t)
    if m:
        g = m.group(1)
        n = int(g) if g.isdigit() else NUMBERS.get(g, 0)
        if 2 <= n <= 30:
            brief["count"] = min(n, 15)
            take(m)
    # Resale.
    if re.search(r"\b(vintage|second[- ]hand|preloved|pre-loved|thrift(?:ed)?|resale|used is (?:fine|ok))\b", t):
        brief["resale_allowed"] = True
    # Reference brand: "like Khaite but cheaper".
    m = re.search(r"\blike ([A-Z][\w&'.]*(?: [A-Z][\w&'.]*)*)", raw)
    if m and m.group(1).lower() not in ("i", "a", "the"):
        brief["aesthetic"].append(f"reference_brand: {m.group(1)}")
    # Colour (first named).
    for word in sorted(COLOURS, key=len, reverse=True):
        mm = re.search(r"\b" + re.escape(word) + r"\b", t)
        if mm and not any(a <= mm.start() < b for a, b in used):
            brief["colour"] = COLOURS[word]
            take(mm)
            break
    # Vocabulary phrases, longest first, with "not ..." handled.
    for p in sorted(vocab, key=len, reverse=True):
        for mm in re.finditer(r"(?<![\w])" + re.escape(p) + r"(?![\w])", t):
            if any(a <= mm.start() < b for a, b in used):
                continue
            dim, cid = vocab[p]
            before = t[max(0, mm.start() - 12):mm.start()]
            negated = bool(re.search(r"\b(?:not|no|isn't|isnt|doesn't|doesnt|won't|wont|never|without)\s+(?:\w+\s+)?$", before))
            take(mm)
            if dim == "category":
                brief.setdefault("category", cid)
            elif dim == "material":
                if negated:
                    brief["exclude"].append(cid)
                elif cid in ("animal_material",):
                    continue
                elif cid == "leather" and "lambskin" in p:
                    brief["material"].append("leather: lambskin")
                else:
                    brief["material"].append(cid)
            elif dim == "budget":
                if cid == "resale_ok":
                    brief["resale_allowed"] = True
            elif dim in DIM_FIELD:
                if negated and p not in NEGATION_MEANS_POSITIVE and cid not in ("opaque", "non_itchy", "no_pilling",
                                                                                "no_shrink", "no_baggy_knees"):
                    continue
                brief[DIM_FIELD[dim]].append(cid)
                if dim == "ethics" and cid == "animal_free":
                    brief["exclude"].append("animal_material")
    if "opaque" in brief["performance"]:
        brief["opacity"] = "not_sheer"
    if re.search(r"\b(sheer|see[- ]?thr(?:ough|u))\b", t) and not re.search(r"\b(not|isn't|isnt|no|doesn't|doesnt|non)[- ]\w*\s*(sheer|see)", t):
        if "opaque" in brief["performance"] and not re.search(r"\b(not|isn't|isnt|doesn't|doesnt)\b", t):
            brief["performance"].remove("opaque")
            brief.pop("opacity", None)
    # Clean up.
    for k in ("silhouette", "material", "exclude", "performance", "fit_need", "aesthetic", "ethics"):
        brief[k] = list(dict.fromkeys(brief[k]))
    if any(a.startswith("reference_brand:") for a in brief["aesthetic"]):
        brief["aesthetic"] = [a for a in brief["aesthetic"] if a != "reference_brand"]
    if "leather: lambskin" in brief["material"] and "leather" in brief["material"]:
        brief["material"].remove("leather")
    brief["conflicts"] = conflicts_of(brief["silhouette"])
    covered = "".join(" " if any(a <= i < b for a, b in used) else ch for i, ch in enumerate(t))
    leftover = [w for w in re.findall(r"[a-z']+", covered)
                if len(w) > 3 and w not in _COMMON and not w.isdigit()]
    brief["unmatched_phrases"] = list(dict.fromkeys(leftover))[:12]
    brief["vibe_text"] = raw.strip()
    # Standard mode: a basic, asked for plainly.
    basics = basic_for(brief)
    plain = not (brief["silhouette"] or brief["fit_need"] or brief.get("count") or brief.get("origin")
                 or brief["aesthetic"] or brief["ethics"] or brief["exclude"]
                 or (brief["material"] and basics != "leather_jacket"))
    if basics and (any(h in t for h in STANDARD_HINTS) or plain):
        brief["mode"] = "standard"
        brief["basic"] = basics
    return {k: v for k, v in brief.items() if v not in ([], None, "")}


_COMMON = set("""that this with want need looking something some just really very good nice item piece pieces
find show give would like love wear wearing what which have from into your mine they them were been will
also more less much than then them dont doesnt isnt wont cant there here when where about over under
buy buying shop shopping please thanks thank make made makes best perfect great well kind sort
doesn't isn't i'll won't can't don't i'm it's that's still actually brand winter summer fall spring back bake lack gape gaping""".split())


def basic_for(brief: dict) -> Optional[str]:
    """Which of the ten Filo Standards a brief is asking for, if any."""
    cat, col, sil = brief.get("category"), brief.get("colour"), set(brief.get("silhouette") or [])
    mats = " ".join(brief.get("material") or [])
    if cat == "t_shirt" and col == "white":
        return "white_t_shirt"
    if cat == "t_shirt" and col == "black":
        return "black_t_shirt"
    if cat == "denim" and (not sil or sil <= {"straight_leg"}):
        return "straight_leg_jeans"
    if cat == "knitwear" and not sil:
        return "crewneck_sweater"
    if cat == "trousers" and not sil:
        return "tailored_trousers"
    if cat == "outerwear":
        if "leather" in mats:
            return "leather_jacket"
        if col in ("tan",) or "camel" in (brief.get("vibe_text") or "").lower():
            return "camel_wool_coat"
        v = (brief.get("vibe_text") or "").lower()
        if "trench" in v:
            return "trench_coat"
        if "blazer" in v:
            return "tailored_blazer"
    if cat == "shirt" and col == "white":
        return "white_shirt"
    return None
