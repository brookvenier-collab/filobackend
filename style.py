"""
Filo style read — where a piece sits in the fashion calendar, and how long
you'll actually want it.

THREE CLAIMS, AND THEY ARE NOT EQUALLY SOLID. Keep them separate.

  1. SEASON PLACEMENT ("this is a Fall/Winter piece"). Nearly derivable from
     fabric and cut. A wool coat is not a spring garment. Solid.

  2. WEAR TIMING ("it's August, so you have the full season ahead of you").
     Arithmetic once you know (1) and today's date. Solid, and it is really a
     cost-per-wear argument, which is Filo's core logic.

  3. TREND DIRECTION ("timeless vs trend vs emerging"). Subjective opinion from
     general fashion knowledge, NOT live trend data and NOT a forecast. Always
     shown under "Just our read — not a rule."

The season NAMES are computed here, in code, from the current date — never left
to the model. Left to itself it drifts, and it has no knowledge of shows that
haven't happened yet. Naming a season is precise-sounding, so getting it wrong
is expensive: false precision is exactly what costs trust.

Set ANTHROPIC_API_KEY on Railway to turn it on. No key = returns nothing (safe).
"""
import os
import json
import urllib.request
from datetime import date

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Kept under the catalog's search budget so the style read can never be the
# reason a verdict is slow. main.py runs the two concurrently.
STYLE_TIMEOUT = 8


def season_cycle(today=None):
    """Where we are in the retail calendar. Deterministic, no model involved.

    Retail runs roughly six months ahead of the weather: Spring/Summer stock
    lands January to June, Fall/Winter July to December.
    """
    today = today or date.today()
    y2 = today.year % 100
    m = today.month

    if 1 <= m <= 6:
        current = f"Spring/Summer {y2:02d}"
        nxt = f"Fall/Winter {y2:02d}"
        # early: Jan-Feb, peak: Mar-May, late: Jun
        phase = "early" if m <= 2 else ("peak" if m <= 5 else "late")
        months_of_wear_left = 7 - m          # through June
    else:
        current = f"Fall/Winter {y2:02d}"
        nxt = f"Spring/Summer {(y2 + 1) % 100:02d}"
        # early: Jul-Aug, peak: Sep-Oct, late: Nov-Dec
        phase = "early" if m <= 8 else ("peak" if m <= 10 else "late")
        months_of_wear_left = 13 - m         # through December

    return {
        "current": current,
        "next": nxt,
        "phase": phase,
        "months_left": months_of_wear_left,
        "today": today.isoformat(),
    }


def _prompt(item, cycle):
    name = item.get("name") or ""
    category = item.get("category") or "garment"
    composition = item.get("composition") or ""
    price = item.get("price")

    return (
        "You are Filo — a sharp, honest fashion friend. Give a short STYLE read for "
        "this piece. Judge the STYLE and the season, never the fabric quality "
        "(a separate part of the app scores that).\n\n"
        f"PIECE\n- type: {category}\n- name: {name}\n- fabric: {composition}\n"
        f"- price: {price}\n\n"
        f"TODAY IS {cycle['today']}. These are facts, not things to reconsider:\n"
        f"- The season in stores right now is {cycle['current']} "
        f"({cycle['phase']} in its run, about {cycle['months_left']} months of "
        f"wearing weather left).\n"
        f"- The season coming next is {cycle['next']}.\n\n"
        "DECIDE\n"
        "1. label — is the STYLE a timeless staple, a current trend, or an emerging "
        "one? Use exactly: Timeless, Trend, or Emerging.\n"
        f"2. season — which season this piece BELONGS to. Use exactly one of: "
        f"\"{cycle['current']}\", \"{cycle['next']}\", or \"Year-round\". Choose "
        "Year-round for genuine all-season staples (a white tee, a plain denim "
        "jacket) rather than forcing a season onto something that has none.\n"
        "3. note — ONE plain sentence. If the piece belongs to the current season, "
        "say how much wear is left in it. If it belongs to the next season, say "
        "roughly how long it will sit before it gets worn. If it is year-round, "
        "say why it outlasts a season. Warm, specific, never hypey. Do not use "
        "exclamation marks.\n\n"
        "Reply with ONLY JSON, no other text:\n"
        '{"label": "...", "season": "...", "note": "..."}'
    )


def style_read(item, today=None):
    """Returns {label, season, note} or None. Never raises."""
    if not ANTHROPIC_API_KEY:
        return None

    cycle = season_cycle(today)

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": _prompt(item, cycle)}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=STYLE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        text = data["content"][0]["text"]
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception:            # noqa: BLE001
        return None              # a missing style read is invisible; a slow one isn't

    label = parsed.get("label")
    season = parsed.get("season")
    note = parsed.get("note")

    # Trust the model for the opinion, not for the calendar. If it invented a
    # season name, drop the field rather than show a season Filo cannot stand behind.
    if season not in (cycle["current"], cycle["next"], "Year-round"):
        season = None
    if label not in ("Timeless", "Trend", "Emerging"):
        label = None

    if not any((label, season, note)):
        return None

    return {"label": label, "season": season, "note": note}
