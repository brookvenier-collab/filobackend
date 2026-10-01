"""
Filo seam read — how the garment is actually sewn.

WHY THIS EXISTS. A care tag lists fibre, care symbols and the country. It never
says how the piece is sewn, and that is most of the difference between a $40
linen shirt and a $400 one. So a tag-only scan tops out at 7.9 (see the
EVIDENCE section of fabric.py). This file is the way past that: the shopper
turns the piece inside out, takes one close photo of a seam, and one vision
call reads what kind of seam it is.

RULES — the same spirit as vision.py, stricter because this one moves a score.

  1. CONTROLLED VOCABULARY. Only tokens from fabric.CONSTRUCTION come back.
     Anything else is dropped.
  2. GOOD NEWS NEEDS HIGH CONFIDENCE. A proof (French seams, flat-felled…)
     only counts when the model is sure. A fault counts at medium confidence
     too: wrongly withholding a bonus costs a shopper little, wrongly awarding
     one costs Filo its credibility.
  3. NOT A SEAM, NO READ. If the photo isn't a close look at an inside seam,
     nothing comes back and the score stays where the tag put it.
  4. NEVER BLOCKS A VERDICT. No key, no image, a timeout or any error at all
     returns [] and the scan carries on exactly as before.

Cost: one Haiku image call, roughly a cent. Set SEAM_MODEL to override.
"""
import os
import json
import logging
import urllib.request

import vision                      # reuses the media-type sniffing
from fabric import CONSTRUCTION

log = logging.getLogger("filo.seams")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SEAM_MODEL = os.environ.get("SEAM_MODEL", os.environ.get("VISION_MODEL", "claude-haiku-4-5"))
SEAM_TIMEOUT = 6
MAX_IMAGE_BYTES = 1_500_000

PROOFS = sorted(t for t, (a, _, _) in CONSTRUCTION.items() if a > 0)
FAULTS = sorted(t for t, (a, _, _) in CONSTRUCTION.items() if a < 0)

_PROMPT = (
    "You are inspecting the INSIDE of a garment to judge how well it is sewn.\n\n"
    "First decide: is this a clear, close photo of an inside seam or the inside "
    "of a garment? If not, set is_seam to false and leave the lists empty.\n\n"
    "Then list what you can SEE, using only these words.\n"
    f"Signs of careful making: {', '.join(PROOFS)}\n"
    f"Faults: {', '.join(FAULTS)}\n\n"
    "Definitions: french_seams = raw edges fully enclosed inside a narrow folded "
    "seam; flat_felled = seam folded flat and stitched down twice, no raw edge "
    "visible; bound_seams = raw edges wrapped in a strip of fabric or tape; "
    "overlocked_only = raw edges finished only with a looped serger/overlock "
    "stitch; dense_stitching = small, tight, even stitches; sparse_stitching = "
    "long, widely spaced stitches; puckered_seams = fabric bunched along the "
    "seam; loose_threads = untrimmed or unravelling threads; glued_seams = "
    "edges bonded with adhesive, no stitches; hand_finished = visible hand "
    "stitching; pattern_matched = stripes or checks line up across the seam; "
    "quality_hardware = metal zip or horn/corozo buttons visible.\n\n"
    "Only list what is clearly visible. Do not guess. Give a confidence for each.\n"
    "Reply with ONLY JSON:\n"
    '{"is_seam": true, "found": [{"token": "french_seams", "confidence": "high"}]}'
)


def sanitize(parsed):
    """Keep only allowed tokens at the required confidence. Split out for tests."""
    if not isinstance(parsed, dict) or parsed.get("is_seam") is not True:
        return []
    out = []
    for f in parsed.get("found") or []:
        if not isinstance(f, dict):
            continue
        token = str(f.get("token", "")).strip().lower()
        conf = str(f.get("confidence", "")).strip().lower()
        if token in PROOFS and conf == "high":
            out.append(token)
        elif token in FAULTS and conf in ("high", "medium"):
            out.append(token)
    return sorted(set(out))


def read(image_b64):
    """Construction tokens seen in a seam photo, or []. Never raises."""
    if not ANTHROPIC_API_KEY or not image_b64:
        return []
    if len(image_b64) > MAX_IMAGE_BYTES:
        log.info("seams: image too large (%d bytes), skipping", len(image_b64))
        return []
    body = json.dumps({
        "model": SEAM_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": vision._media_type(image_b64),
                                         "data": image_b64}},
            {"type": "text", "text": _PROMPT},
        ]}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"content-type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=SEAM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        text = data["content"][0]["text"]
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as exc:                # noqa: BLE001
        log.info("seams: read failed (%s)", exc)
        return []
    return sanitize(parsed)
