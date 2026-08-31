"""
Filo vision read — what the garment actually looks like.

WHY THIS EXISTS. The scan reads the care LABEL. The label knows the fibre and
nothing else, so a search built from it looks for "sweater merino" and returns
well-made sweaters of every shape on earth. The shopper is holding a cropped
boxy sage crewneck and gets shown a beautifully-made cardigan. Right on fabric,
wrong on taste — which reads as broken.

So: one photo of the piece, one vision call, and the search gains a silhouette.

TWO RULES THIS MODULE FOLLOWS.

  1. CONTROLLED VOCABULARY. The model may only answer from the lists below.
     Free text produces queries like "vibey oversized energy" and quietly
     poisons the search. Anything off-list is dropped, not passed through.

  2. DESCRIPTORS WIDEN, THEY NEVER FILTER. They are added to queries and used to
     RANK what comes back. Nothing is ever rejected for failing to match them.
     The model will sometimes say "cropped" where a listing says "crop", and
     losing a genuinely better-made piece over a word is a bad trade — fabric
     quality is the promise, aesthetics are the assist.

Set ANTHROPIC_API_KEY to turn it on. No key, no image, or any failure at all
returns None, and the scan behaves exactly as it did before this file existed.
"""
import os
import json
import base64
import logging
import urllib.request

log = logging.getLogger("filo.vision")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-haiku-4-5")

# The search can't start until this returns, so it is deliberately tight.
VISION_TIMEOUT = 5

# ~1.5MB of base64 ≈ a 1MP JPEG, which is plenty for judging a silhouette.
# The app should downscale before sending; this is the backstop.
MAX_IMAGE_BYTES = 1_500_000

# --------------------------------------------------------------- vocabulary

SILHOUETTE = {"cropped", "boxy", "oversized", "relaxed", "fitted", "slim",
              "straight", "a-line", "wrap", "longline"}
NECKLINE = {"crewneck", "v-neck", "mock-neck", "turtleneck", "scoop",
            "collared", "henley", "square-neck", "halter", "strapless"}
SLEEVE = {"long-sleeve", "short-sleeve", "sleeveless", "three-quarter",
          "puff-sleeve", "raglan", "cap-sleeve"}
TEXTURE = {"chunky-knit", "fine-knit", "ribbed", "cable-knit", "waffle",
           "jersey", "fleece", "twill", "denim", "poplin", "corduroy",
           "satin", "crepe", "boucle"}
COLOR = {"black", "white", "cream", "ivory", "grey", "charcoal", "navy",
         "blue", "green", "sage", "olive", "forest", "brown", "tan", "beige",
         "camel", "burgundy", "red", "rust", "pink", "blush", "purple",
         "yellow", "mustard", "orange", "striped", "floral", "checked",
         "multicolour"}
FORMALITY = {"casual", "smart-casual", "formal", "athletic", "loungewear"}

_FIELDS = [
    ("silhouette", SILHOUETTE),
    ("neckline", NECKLINE),
    ("sleeve", SLEEVE),
    ("texture", TEXTURE),
    ("color", COLOR),
    ("formality", FORMALITY),
]

_PROMPT = (
    "Look at this garment and describe its SHAPE and LOOK. Ignore fabric quality "
    "entirely — another part of the system scores that from the care label.\n\n"
    "Answer ONLY with words from these lists. If you are unsure of a field, use "
    "null rather than guessing:\n"
    + "\n".join(f"- {name}: {', '.join(sorted(vocab))}" for name, vocab in _FIELDS)
    + "\n\nAlso give search_phrase: 3-6 words a shopper would type to find this "
      "shape, e.g. \"cropped boxy crewneck sweater\". No brand names, no colour "
      "adjectives beyond the list, no marketing words.\n\n"
    "Reply with ONLY JSON:\n"
    '{"silhouette": "...", "neckline": "...", "sleeve": "...", '
    '"texture": "...", "color": "...", "formality": "...", "search_phrase": "..."}'
)


def _media_type(image_b64):
    """Sniff the format from the first bytes so callers don't have to declare it."""
    try:
        head = base64.b64decode(image_b64[:24] + "==", validate=False)[:4]
    except Exception:                       # noqa: BLE001
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/jpeg"


def describe(image_b64):
    """Return {silhouette, neckline, ..., search_phrase} or None. Never raises."""
    if not ANTHROPIC_API_KEY or not image_b64:
        return None
    if len(image_b64) > MAX_IMAGE_BYTES:
        log.info("vision: image too large (%d bytes), skipping", len(image_b64))
        return None

    body = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": _media_type(image_b64),
                                         "data": image_b64}},
            {"type": "text", "text": _PROMPT},
        ]}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={"content-type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=VISION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        text = data["content"][0]["text"]
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as exc:                # noqa: BLE001
        log.info("vision: read failed (%s)", exc)
        return None

    return sanitize(parsed)


def sanitize(parsed):
    """Drop anything outside the controlled vocabulary. Split out so it's testable."""
    if not isinstance(parsed, dict):
        return None

    out = {}
    for name, vocab in _FIELDS:
        value = parsed.get(name)
        if isinstance(value, str) and value.strip().lower() in vocab:
            out[name] = value.strip().lower()

    phrase = parsed.get("search_phrase")
    if isinstance(phrase, str):
        # Keep it short and free of anything that would derail a query.
        words = [w for w in phrase.lower().split() if w.isalpha() or "-" in w][:6]
        if words:
            out["search_phrase"] = " ".join(words)

    return out or None


def descriptors(read):
    """The words worth putting into a search query, most distinctive first."""
    if not read:
        return []
    order = ("silhouette", "neckline", "texture", "sleeve", "color")
    return [read[k] for k in order if read.get(k)]
