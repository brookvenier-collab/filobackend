"""
Filo style read — Claude's opinion on whether an item is a timeless staple,
a current trend, or an emerging one, and whether it's in season.
Subjective opinion from general fashion knowledge — NOT live trend data.
Set ANTHROPIC_API_KEY on Railway to turn it on. No key = returns nothing (safe).
"""
import os
import json
import urllib.request

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")


def style_read(item):
    if not ANTHROPIC_API_KEY:
        return None

    name = item.get("name") or ""
    category = item.get("category") or "garment"
    composition = item.get("composition") or ""
    price = item.get("price")

    prompt = (
        "You are Filo — a sharp, honest fashion friend. Give a short STYLE read for this piece "
        "(judge the STYLE, not the fabric quality):\n"
        f"- type: {category}\n- name: {name}\n- fabric: {composition}\n- price: {price}\n\n"
        "Decide if the style is a timeless staple, a current trend, or an emerging/forecast trend, "
        "and whether it's in season right now. This is your subjective opinion from general fashion "
        "knowledge, not live data.\n"
        "Reply with ONLY JSON, no other text:\n"
        '{"label": "Timeless" or "Trend" or "Emerging", '
        '"season": short phrase like "In season" / "Fall staple" / "Year-round", '
        '"note": one warm, plain-language sentence, confident but never hypey}'
    )

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}],
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        text = data["content"][0]["text"]
        start = text.find("{")
        end = text.rfind("}")
        parsed = json.loads(text[start:end + 1])
        return {
            "label": parsed.get("label"),
            "season": parsed.get("season"),
            "note": parsed.get("note"),
        }
    except Exception:
        return None
