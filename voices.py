"""
v31. What Filo shoppers say about pieces, in one tap.

Two things come in:

  * NOTES. Optional chips under the verdict ("Feels thin", "Runs small"). One
    tap, no typing, no account. Sent the moment they're tapped; tapping again
    takes the note back.

  * WEAR CHECKS. 30 and 90 days after someone says they bought a piece (or
    saved one), the phone asks "How's it holding up?" with three answers. The
    reminder is scheduled on the phone; the server only ever sees the answer.

One line goes out, on the verdict: "Shoppers noticed: feels thin." or "Filo
shoppers say pieces like this still look new after a month." Only from a group
of pieces with at least NOTE_FLOOR different shoppers behind it.

Privacy is the same as events.py, on purpose:
  * No user ID, no account. A session ID is stored only as the daily-rotating
    hash from events.hash_session, so two notes on two days can't be linked.
  * No free text. A note is one of NOTE_CHIPS by id, an answer one of WEAR_ANSWERS.
  * Hours, not moments. Brand, kind of piece and main fibre: nothing finer.
  * Shoppers can switch it off with the same "share anonymous data" switch.

Everything here is best effort. No database means no notes, never a broken scan.
"""
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

import events

log = logging.getLogger("filo.voices")

# The chips, in order. Sent to the app on /config, so the list and the words
# change here without an app update. "say" is how the line on the verdict reads.
NOTE_CHIPS = [
    {"id": "thin", "label": "Feels thin", "say": "feels thin", "side": "bad"},
    {"id": "nice_weight", "label": "Nice weight", "say": "has a nice weight", "side": "good"},
    {"id": "soft", "label": "Feels soft", "say": "feels soft", "side": "good"},
    {"id": "see_through", "label": "See-through", "say": "is see-through", "side": "bad"},
    {"id": "rough_seams", "label": "Seams look rough", "say": "has rough seams", "side": "bad"},
    {"id": "runs_small", "label": "Runs small", "say": "runs small", "side": "fit"},
    {"id": "runs_big", "label": "Runs big", "say": "runs big", "side": "fit"},
]
NOTE_IDS = {c["id"] for c in NOTE_CHIPS}

WEAR_ANSWERS = [
    {"id": "like_new", "label": "Still like new"},
    {"id": "some_wear", "label": "Some wear"},
    {"id": "fell_apart", "label": "Fell apart"},
]
WEAR_IDS = {a["id"] for a in WEAR_ANSWERS}
WEAR_DAYS = (30, 90)

# How many different shoppers a group of pieces needs before Filo says anything
# about it. Lower than the 30 used for selling data: this is a hint on a
# verdict, never sold, and it never names anyone. Raise it on Railway any time.
NOTE_FLOOR = int(os.environ.get("NOTE_FLOOR", "5"))
# A note has to come from at least this share of the shoppers in the group.
NOTE_SHARE = 0.4

SCHEMA = """
CREATE TABLE IF NOT EXISTS shopper_voices (
    id             BIGSERIAL PRIMARY KEY,
    kind           TEXT        NOT NULL,      -- 'note' | 'wear'
    answer         TEXT        NOT NULL,      -- a NOTE_CHIPS id or a WEAR_ANSWERS id
    days           INTEGER,                   -- wear checks: 30 or 90
    session_hash   TEXT        NOT NULL,
    occurred_hour  TIMESTAMPTZ NOT NULL,
    brand          TEXT,
    garment        TEXT,                      -- catalog.garment_group
    fibre          TEXT,                      -- the main fibre
    country        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS shopper_voices_key_idx ON shopper_voices (fibre, garment);
"""


class NoteIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    note: str = Field(max_length=40)
    on: bool = True
    composition: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=60)
    brand: Optional[str] = Field(default=None, max_length=80)
    country: Optional[str] = Field(default=None, max_length=2)


class WearIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    answer: str = Field(max_length=40)
    days: int = 30
    composition: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=60)
    brand: Optional[str] = Field(default=None, max_length=80)
    country: Optional[str] = Field(default=None, max_length=2)


# ------------------------------------------------------------------- keys

def piece_key(composition: Optional[str], category: Optional[str],
              brand: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(brand, garment, fibre): the only things a note is filed under."""
    import catalog
    import fabric
    fibre = None
    try:
        _, matched = fabric.quality_score(composition or "")
        if matched:
            fibre = max(matched, key=lambda m: m[1])[0]
    except Exception:                       # noqa: BLE001
        fibre = None
    garment = catalog.garment_group(category) if category else None
    b = (brand or "").strip().lower() or None
    return b, garment, fibre


def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:                # noqa: BLE001
        log.warning("voices: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


def _write(kind: str, answer: str, days: Optional[int], session_id: str,
           composition, category, brand, country, on: bool = True) -> bool:
    b, garment, fibre = piece_key(composition, category, brand)
    if not fibre and not garment:
        return False                        # nothing to file it under
    conn = events._conn()
    if conn is None:
        return False
    sess = events.hash_session(session_id)
    try:
        with conn, conn.cursor() as cur:
            # One of each note per shopper per piece. Tapping again takes it back.
            cur.execute(
                """DELETE FROM shopper_voices WHERE kind=%s AND answer=%s
                   AND session_hash=%s AND COALESCE(brand,'')=%s
                   AND COALESCE(garment,'')=%s AND COALESCE(fibre,'')=%s""",
                (kind, answer, sess, b or "", garment or "", fibre or ""))
            if on:
                cur.execute(
                    """INSERT INTO shopper_voices
                       (kind, answer, days, session_hash, occurred_hour,
                        brand, garment, fibre, country)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (kind, answer, days, sess, events._truncate_to_hour(),
                     b, garment, fibre, (country or "").upper()[:2] or None))
        _CACHE["at"] = 0.0                  # the next verdict reads it fresh
        return True
    except Exception as exc:                # noqa: BLE001
        log.warning("voices: write failed (%s)", exc)
        return False
    finally:
        conn.close()


def record_note(n: NoteIn) -> bool:
    if n.note not in NOTE_IDS:
        return False
    return _write("note", n.note, None, n.session_id, n.composition,
                  n.category, n.brand, n.country, on=n.on)


def record_wear(w: WearIn) -> bool:
    if w.answer not in WEAR_IDS:
        return False
    days = 90 if w.days >= 60 else 30
    return _write("wear", w.answer, days, w.session_id, w.composition,
                  w.category, w.brand, w.country)


# --------------------------------------------------------------- summaries

def _rows() -> List[tuple]:
    conn = events._conn()
    if conn is None:
        return []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """SELECT kind, answer, days, session_hash, brand, garment, fibre
                   FROM shopper_voices
                   WHERE created_at > NOW() - INTERVAL '540 days'""")
            return list(cur.fetchall())
    except Exception as exc:                # noqa: BLE001
        log.warning("voices: read failed (%s)", exc)
        return []
    finally:
        conn.close()


def build_cells(rows: List[tuple]) -> Dict[tuple, Dict[str, Any]]:
    """{cell: {"notes": {id: set(sessions)}, "wear": {days: {id: set}}, "all": set}}

    Each row counts toward three cells, most specific first:
    (brand, garment, fibre), (None, garment, fibre), (None, None, fibre)."""
    cells: Dict[tuple, Dict[str, Any]] = {}
    for kind, answer, days, sess, brand, garment, fibre in rows:
        keys = {(brand, garment, fibre), (None, garment, fibre), (None, None, fibre)}
        for key in keys:
            if key[2] is None and key[1] is None:
                continue
            c = cells.setdefault(key, {"notes": {}, "note_all": set(),
                                       "wear": {30: {}, 90: {}}, "wear_all": {30: set(), 90: set()}})
            if kind == "note":
                c["notes"].setdefault(answer, set()).add(sess)
                c["note_all"].add(sess)
            elif kind == "wear" and days in (30, 90):
                c["wear"][days].setdefault(answer, set()).add(sess)
                c["wear_all"][days].add(sess)
    return cells


_SAY = {c["id"]: c["say"] for c in NOTE_CHIPS}


def _note_line(cell) -> Optional[str]:
    total = len(cell["note_all"])
    if total < NOTE_FLOOR:
        return None
    ranked = sorted(cell["notes"].items(), key=lambda kv: len(kv[1]), reverse=True)
    picked = [nid for nid, who in ranked if len(who) / total >= NOTE_SHARE][:2]
    if not picked:
        return None
    return "Shoppers noticed: it " + " and ".join(_SAY[n] for n in picked) + "."


def _wear_line(cell) -> Optional[str]:
    for days, when in ((90, "three months"), (30, "a month")):
        who = cell["wear_all"][days]
        total = len(who)
        if total < NOTE_FLOOR:
            continue
        share = {a: len(cell["wear"][days].get(a, ())) / total for a in WEAR_IDS}
        if share["fell_apart"] >= 0.3:
            return f"Some Filo shoppers say pieces like this fell apart within {when}."
        if share["like_new"] >= 0.6:
            return f"Filo shoppers say pieces like this still look new after {when}."
        if share["some_wear"] + share["fell_apart"] >= 0.5:
            return f"Filo shoppers say pieces like this show wear within {when}."
        return f"Filo shoppers say pieces like this hold up fine for {when}."
    return None


def lines_for(cells, brand, garment, fibre) -> List[str]:
    """Up to two lines: how it wears, then what shoppers noticed. The most
    specific group over the floor wins for each."""
    order = [(brand, garment, fibre), (None, garment, fibre), (None, None, fibre)]
    wear = note = None
    for key in order:
        cell = cells.get(key)
        if not cell:
            continue
        wear = wear or _wear_line(cell)
        note = note or _note_line(cell)
    return [x for x in (wear, note) if x]


# Read at most once a minute in the background; a verdict never waits on it.
_CACHE: Dict[str, Any] = {"at": 0.0, "cells": {}, "busy": False}
CACHE_TTL_S = 60
_lock = threading.Lock()


def _refresh():
    try:
        _CACHE["cells"] = build_cells(_rows())
    except Exception as exc:                # noqa: BLE001
        log.warning("voices: refresh failed (%s)", exc)
    finally:
        _CACHE["at"] = time.time()
        _CACHE["busy"] = False


def shopper_lines(composition, category, brand) -> List[str]:
    if time.time() - _CACHE["at"] > CACHE_TTL_S:
        with _lock:
            if not _CACHE["busy"]:
                _CACHE["busy"] = True
                threading.Thread(target=_refresh, daemon=True).start()
    b, garment, fibre = piece_key(composition, category, brand)
    if not fibre and not garment:
        return []
    try:
        return lines_for(_CACHE["cells"], b, garment, fibre)
    except Exception:                       # noqa: BLE001
        return []


def admin_summary(rows: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """For Brooklyn: how much has come in, and what each group of pieces is
    saying, with how far it is from the floor. Counts only, never a session."""
    rows = _rows() if rows is None else rows
    cells = build_cells(rows)
    totals = {"notes": 0, "wear_checks": 0}
    for kind, *_ in rows:
        totals["notes" if kind == "note" else "wear_checks"] += 1
    groups = []
    for (brand, garment, fibre), c in cells.items():
        shoppers = len(c["note_all"] | c["wear_all"][30] | c["wear_all"][90])
        groups.append({
            "brand": brand, "garment": garment, "fibre": fibre,
            "shoppers": shoppers,
            "notes": {k: len(v) for k, v in sorted(c["notes"].items(), key=lambda kv: -len(kv[1]))},
            "wear_30": {k: len(v) for k, v in c["wear"][30].items()},
            "wear_90": {k: len(v) for k, v in c["wear"][90].items()},
            "showing": lines_for({(brand, garment, fibre): c}, brand, garment, fibre),
            "needs_for_a_line": max(0, NOTE_FLOOR - shoppers),
        })
    groups.sort(key=lambda g: -g["shoppers"])
    return {"floor": NOTE_FLOOR, "totals": totals, "groups": groups[:200]}


# ---- v31.2: the same reads as short quotes with how many shoppers said them,
# for the shopper block on the verdict ("Still looks new after a month." /
# "14 Filo shoppers with a piece like this").

def _wear_quote(cell):
    for days, when in ((90, "three months"), (30, "a month")):
        total = len(cell["wear_all"][days])
        if total < NOTE_FLOOR:
            continue
        share = {a: len(cell["wear"][days].get(a, ())) / total for a in WEAR_IDS}
        if share["fell_apart"] >= 0.3:
            q = f"Fell apart within {when}."
        elif share["like_new"] >= 0.6:
            q = f"Still looks new after {when}."
        elif share["some_wear"] + share["fell_apart"] >= 0.5:
            q = f"Shows wear within {when}."
        else:
            q = f"Holds up fine for {when}."
        return {"kind": "wear", "quote": q, "count": total,
                "who": f"{total} Filo shoppers with a piece like this"}
    return None


def _note_quote(cell):
    total = len(cell["note_all"])
    if total < NOTE_FLOOR:
        return None
    ranked = sorted(cell["notes"].items(), key=lambda kv: len(kv[1]), reverse=True)
    picked = [nid for nid, who in ranked if len(who) / total >= NOTE_SHARE][:2]
    if not picked:
        return None
    labels = {c["id"]: c["label"] for c in NOTE_CHIPS}
    words = [labels[picked[0]]] + [labels[p].lower() for p in picked[1:]]
    count = len(set().union(*(cell["notes"][p] for p in picked)))
    return {"kind": "note", "quote": ", ".join(words) + ".", "count": count,
            "who": f"{count} Filo shoppers noticed"}


def quotes_for(cells, brand, garment, fibre):
    order = [(brand, garment, fibre), (None, garment, fibre), (None, None, fibre)]
    wear = note = None
    for key in order:
        cell = cells.get(key)
        if not cell:
            continue
        wear = wear or _wear_quote(cell)
        note = note or _note_quote(cell)
    return [q for q in (wear, note) if q]


def shopper_quotes(composition, category, brand):
    shopper_lines(composition, category, brand)      # keeps the cache fresh
    b, garment, fibre = piece_key(composition, category, brand)
    if not fibre and not garment:
        return []
    try:
        return quotes_for(_CACHE["cells"], b, garment, fibre)
    except Exception:                       # noqa: BLE001
        return []


def config() -> Dict[str, Any]:
    """What the app needs to show the chips and the check-in."""
    return {
        "note_chips": [{"id": c["id"], "label": c["label"]} for c in NOTE_CHIPS],
        "note_title": "Noticed anything?",
        "note_thanks": "Thanks. The next shopper sees this.",
        "wear_answers": WEAR_ANSWERS,
        "wear_title": "How's it holding up?",
        "wear_days": list(WEAR_DAYS),
    }
