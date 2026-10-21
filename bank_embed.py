"""Meaning search: matching vague requests ("quiet luxury", "a bit French") by meaning, not keywords.

Two ways, chosen automatically:
1. Embeddings, when the Railway variable VOYAGE_API_KEY is set. Each product gets a meaning
   fingerprint (a list of numbers) from its name, fabric, cut, description and the brand's
   style notes. A request gets one too, and products are compared by how close the
   fingerprints are. Voyage is the embeddings company Anthropic recommends.
   VOYAGE_MODEL (default voyage-4-lite) and VOYAGE_API_URL can be changed in Railway.
2. Without a key: word matching, helped by a small vocabulary of style words (minimal,
   workwear, romantic...). Rougher, but it works, costs nothing and needs no setup.

Either way this only feeds the "aesthetic" part of the Match score (15%). It never
overrides a hard filter or the evidence rules.
"""
import hashlib
import json
import logging
import math
import os
import re
import urllib.request
from functools import lru_cache
from typing import Callable, Dict, Iterable, List, Optional

import events

log = logging.getLogger("filo.bank_embed")

BATCH = 64

# Style words for the no-key fallback: each aesthetic concept -> words that suggest it.
VIBE_WORDS = {
    "minimal": "minimal minimalist clean simple essential pared back understated plain quiet refined neutral",
    "elevated_basics": "essential everyday basic staple classic timeless refined wardrobe",
    "workwear": "workwear utility chore work canvas drill twill duck pocket sturdy heavyweight combat field",
    "romantic": "romantic soft feminine lace ruffle puff floral delicate gathered",
    "vintage": "vintage archival heritage retro classic washed faded",
    "sculptural": "sculptural architectural structured volume tailored shape",
    "downtown": "downtown black leather oversized edgy",
    "la_cool": "relaxed easy faded washed boxy",
    "editorial": "editorial art statement unexpected",
    "quiet_luxury": "cashmere merino silk tailored refined neutral camel navy grey wool quiet",
    "french": "breton stripe navy knit tailored effortless classic chic",
    "scandi": "minimal clean organic neutral relaxed",
    "cozy": "soft warm chunky knit brushed fleece cosy cozy",
}


def enabled() -> bool:
    return bool(os.environ.get("VOYAGE_API_KEY"))


def _model() -> str:
    return os.environ.get("VOYAGE_MODEL", "voyage-4-lite")


def _voyage(texts: List[str], input_type: str) -> List[List[float]]:
    body = json.dumps({"input": texts, "model": _model(), "input_type": input_type}).encode()
    req = urllib.request.Request(os.environ.get("VOYAGE_API_URL", "https://ai.mongodb.com/v1/embeddings"),
                                 data=body, method="POST", headers={
                                     "Content-Type": "application/json",
                                     "Authorization": f"Bearer {os.environ.get('VOYAGE_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


# Swapped out in tests.
EMBED_CALL: Callable[[List[str], str], List[List[float]]] = _voyage


def product_text(row: dict) -> str:
    """What a product's fingerprint is made from."""
    rec = row.get("record") or {}
    comp = ((rec.get("materials") or {}).get("composition") or {}).get("value") or []
    comp_t = ", ".join(f"{c['percent']}% {c['fibre']}" for c in comp if isinstance(c, dict))
    tags = " ".join(t["tag"].replace("_", " ") for t in ((rec.get("fit") or {}).get("fit_tags") or []))
    bits = [row.get("brand_name") or "", rec.get("name") or "", (rec.get("category") or "").replace("_", " "),
            comp_t, tags, (row.get("search_text") or "")[:1200], row.get("brand_style") or ""]
    return re.sub(r"\s+", " ", ". ".join(b for b in bits if b)).strip()


def text_hash(t: str) -> str:
    return hashlib.sha1((_model() + "|" + t).encode()).hexdigest()


def brand_style(brand_row: dict) -> str:
    """The brand's style notes from the discovery research, if any."""
    out = []
    for d in brand_row.get("discovery") or []:
        if isinstance(d, dict):
            for k in ("style", "aesthetic", "why", "why_it_fits", "vibe", "notes"):
                if isinstance(d.get(k), str):
                    out.append(d[k])
    return " ".join(out)[:600]


def refresh_missing(limit: int = 2000) -> int:
    """Fingerprint products that don't have one yet (or whose words changed). Returns how many."""
    if not enabled():
        return 0
    conn = events._conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.product_id, p.record, p.search_text, b.name, b.discovery, e.text_hash
                           FROM bank_products p JOIN bank_brands b ON b.brand_id = p.brand_id
                           LEFT JOIN bank_embeddings e ON e.product_id = p.product_id
                           WHERE p.status IN ('live','in_review','hidden_out_of_stock') LIMIT %s""", (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()
    todo = []
    for pid, rec, st, bname, disc, old in rows:
        t = product_text({"record": rec, "search_text": st, "brand_name": bname,
                          "brand_style": brand_style({"discovery": disc})})
        h = text_hash(t)
        if h != old:
            todo.append((pid, t, h))
    done = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            vecs = EMBED_CALL([t for _, t, _ in chunk], "document")
        except Exception as exc:            # noqa: BLE001
            log.warning("bank_embed: embedding call failed (%s)", exc)
            break
        conn = events._conn()
        try:
            with conn, conn.cursor() as cur:
                for (pid, _, h), v in zip(chunk, vecs):
                    cur.execute("""INSERT INTO bank_embeddings (product_id, model, text_hash, vector)
                                   VALUES (%s, %s, %s, %s)
                                   ON CONFLICT (product_id) DO UPDATE SET model = EXCLUDED.model,
                                       text_hash = EXCLUDED.text_hash, vector = EXCLUDED.vector, updated_at = now()""",
                                (pid, _model(), h, [float(x) for x in v]))
                    done += 1
        finally:
            conn.close()
    return done


def vectors(product_ids: Iterable[str]) -> Dict[str, List[float]]:
    ids = list(product_ids)
    if not ids or not enabled():
        return {}
    conn = events._conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT product_id, vector FROM bank_embeddings WHERE product_id = ANY(%s) AND model = %s",
                        (ids, _model()))
            return {r[0]: list(r[1]) for r in cur.fetchall()}
    finally:
        conn.close()


@lru_cache(maxsize=512)
def query_vector(text: str) -> Optional[tuple]:
    if not enabled() or not text.strip():
        return None
    try:
        return tuple(EMBED_CALL([text], "query")[0])
    except Exception as exc:                # noqa: BLE001
        log.warning("bank_embed: query embedding failed (%s)", exc)
        return None


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ------------------------------------------------------------------ the no-key fallback

STOP = set("a an the and or for with in of to my i me it its that this is are be some something bit very really "
           "like more less not no but just want need looking".split())


def words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP and len(w) > 2]


def expand(vibe_text: str, aesthetics: Iterable[str] = ()) -> List[str]:
    """The request's style words plus the vocabulary's words for any style it names."""
    out = words(vibe_text)
    joined = " ".join(out)
    for key, ws in VIBE_WORDS.items():
        if key in aesthetics or key.replace("_", " ") in joined or key in joined:
            out += ws.split()
    return list(dict.fromkeys(out))


def lexical(query_words: List[str], text: str) -> float:
    """0..1: how many of the request's style words show up in the product's words."""
    if not query_words:
        return 0.0
    have = set(words(text))
    hits = sum(1 for w in query_words if w in have or (len(w) > 4 and any(h.startswith(w[:5]) for h in have)))
    return min(1.0, hits / max(3.0, len(query_words) * 0.5))
