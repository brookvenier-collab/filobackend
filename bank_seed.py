"""Loads the starter data in bank_data/ into the bank tables.

- 544 brands from seed_brands.json. Only the first-wave brands are approved.
- The fashion ontology: concepts, their phrases in each language, and the
  knowledge graph relations.

Safe to run on every startup. Brands already in the database keep whatever
Brooklyn has changed in Studio (approval, notes, style fit, unique find); only
the research fields are refreshed from the file. Vocabulary from the file is
added, never deleted, so phrases added in Studio stay.
"""
import json
import logging
import os
from typing import Dict, Tuple

import bank
import events

log = logging.getLogger("filo.bank_seed")

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_data")

CATEGORY_MAP = {
    "tees": "tees", "knitwear": "knitwear", "denim": "denim", "trousers": "trousers",
    "outerwear": "outerwear", "overshirts": "outerwear", "resale": "resale",
    "shirts, dresses & trousers": "shirts_dresses_trousers",
    "leather shoes/accessories; limited apparel relevance": "shoes_accessories",
}


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _gender(text: str):
    t = (text or "").lower()
    if not t:
        return None
    if "men" in t and "women" in t:
        return "unisex" if "unisex" in t else "women_and_men"
    if "women" in t:
        return "women"
    if "unisex" in t:
        return "unisex"
    if "men" in t:
        return "men"
    return None


def brand_rows():
    """Turn seed_brands.json into rows for bank_brands. Pure, so tests can call it."""
    seed = _load("seed_brands.json")
    rows, seen = [], {}
    for b in seed["brands"]:
        bid = bank.slug(b["name"])
        if bid in seen:            # two names that slug the same: keep both, distinctly
            seen[bid] += 1
            bid = f"{bid}-{seen[bid]}"
        else:
            seen[bid] = 1
        url = b.get("url") or None
        flags = b.get("flags") or []
        rows.append({
            "brand_id": bid,
            "name": b["name"],
            "official_url": url,
            "url_verified": False,     # nothing is verified until checked
            "region": b.get("region") or None,
            "gender_target": _gender(b.get("for")),
            "categories": sorted({CATEGORY_MAP.get(c, c) for c in b.get("categories", [])}),
            "research_status": b.get("research_status", "discovery_lead"),
            "evidence_level": b.get("evidence_level", "Not yet screened"),
            "style_led": bool(b.get("style_led")),
            "approved": bool(b.get("approved")),
            "approved_on": b.get("approved_on"),
            "approved_note": b.get("approved_note"),
            "flags": flags,
            "research": b.get("research", []),
            "discovery": b.get("discovery", []),
            "directory": b.get("directory"),
        })
    return rows


def ontology_rows():
    """(concepts, phrases, relations) from fashion_ontology.json."""
    o = _load("fashion_ontology.json")
    concepts, phrases = [], []
    for dimension, items in o["concepts"].items():
        for c in items:
            extra = {k: v for k, v in c.items()
                     if k not in ("id", "note") and not (isinstance(v, list) and len(k) == 2)}
            concepts.append((c["id"], dimension, c.get("note"), extra))
            for lang, words in c.items():
                if len(lang) == 2 and isinstance(words, list):
                    for w in words:
                        phrases.append((c["id"], lang, w))
    relations = [(r["from"], r["type"], r["to"], r.get("note")) for r in o["relations"]]
    return concepts, phrases, relations


def seed() -> Dict[str, int]:
    """Load brands and the ontology. Returns how many rows each step touched."""
    conn = events._conn()
    if conn is None:
        return {}
    out = {}
    try:
        with conn, conn.cursor() as cur:
            n = 0
            for r in brand_rows():
                cur.execute("""
                    INSERT INTO bank_brands (brand_id, name, official_url, url_verified, region,
                        gender_target, categories, research_status, evidence_level, style_led,
                        approved, approved_on, approved_note, flags, research, discovery, directory)
                    VALUES (%(brand_id)s, %(name)s, %(official_url)s, %(url_verified)s, %(region)s,
                        %(gender_target)s, %(categories)s, %(research_status)s, %(evidence_level)s,
                        %(style_led)s, %(approved)s, %(approved_on)s, %(approved_note)s,
                        %(flags)s::jsonb, %(research)s::jsonb, %(discovery)s::jsonb, %(directory)s::jsonb)
                    ON CONFLICT (brand_id) DO UPDATE SET
                        categories = EXCLUDED.categories,
                        research_status = CASE WHEN bank_brands.approved OR bank_brands.studio_touched
                                               THEN bank_brands.research_status
                                               ELSE EXCLUDED.research_status END,
                        evidence_level = EXCLUDED.evidence_level,
                        style_led = EXCLUDED.style_led,
                        research = EXCLUDED.research,
                        discovery = EXCLUDED.discovery,
                        directory = EXCLUDED.directory,
                        updated_at = now()
                """, {**r,
                      "flags": json.dumps(r["flags"]),
                      "research": json.dumps(r["research"]),
                      "discovery": json.dumps(r["discovery"]),
                      "directory": json.dumps(r["directory"]) if r["directory"] is not None else None})
                n += 1
            out["brands"] = n

            concepts, phrases, relations = ontology_rows()
            for cid, dim, note, extra in concepts:
                cur.execute("""
                    INSERT INTO bank_concepts (concept_id, dimension, note, extra)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (concept_id) DO UPDATE SET
                        dimension = EXCLUDED.dimension, note = EXCLUDED.note, extra = EXCLUDED.extra
                """, (cid, dim, note, json.dumps(extra)))
            for cid, lang, phrase in phrases:
                cur.execute("""INSERT INTO bank_concept_phrases (concept_id, language, phrase)
                               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", (cid, lang, phrase))
            for frm, typ, to, note in relations:
                cur.execute("""INSERT INTO bank_concept_relations (from_concept, rel_type, to_value, note)
                               VALUES (%s, %s, %s::jsonb, %s)
                               ON CONFLICT (from_concept, rel_type, to_value) DO UPDATE SET note = EXCLUDED.note""",
                            (frm, typ, json.dumps(to), note))
            out.update(concepts=len(concepts), phrases=len(phrases), relations=len(relations))
        return out
    except Exception as exc:            # noqa: BLE001
        log.warning("bank_seed: seed failed (%s)", exc)
        return {}
    finally:
        conn.close()
