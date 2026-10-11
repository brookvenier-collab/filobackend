"""
Filo colour check (v25): what colour a listing's photo actually is.

Shops almost never put the colour in a product title ("Signature Cashmere
Boxy Cardigan"), so reading the words could not tell a white sweater from a
black one, and picking a colour chip changed nothing. This looks at the
listing's own small photo instead and names the colour of the garment in it.

Used ONLY when the shopper picked a colour. It never changes a score and never
lets a piece past the quality rules: it only decides which of the pieces that
already passed are the colour they asked for.

If Pillow is missing or a photo cannot be read, every answer is None ("could
not tell") and the search carries on exactly as before.
"""
import base64
import colorsys
import io
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("filo.colour")

try:
    from PIL import Image
except Exception:                       # noqa: BLE001
    Image = None

FETCH_TIMEOUT = 2.0      # seconds for one photo
CHECK_BUDGET = 3.0       # seconds for all of them together
MAX_CHECKS = 28          # photos looked at per search
MIN_SHARE = 0.38         # how much of the garment has to be that colour

# The chip the shopper tapped -> the colour names that count as it.
ACCEPTS = {
    "black": {"black"}, "white": {"white"}, "cream": {"cream"},
    "ivory": {"cream"}, "grey": {"grey"}, "charcoal": {"grey"},
    "navy": {"navy"}, "blue": {"blue"}, "brown": {"brown"},
    "tan": {"tan"}, "beige": {"tan", "cream"}, "camel": {"tan"},
    "green": {"green"}, "sage": {"green"}, "olive": {"green"},
    "forest": {"green"}, "red": {"red"}, "burgundy": {"burgundy"},
    "pink": {"pink"}, "blush": {"pink"}, "purple": {"purple"},
    "yellow": {"yellow"}, "mustard": {"yellow"}, "orange": {"orange"},
    "rust": {"orange", "brown"},
}

_cache = {}              # photo url -> {name: share} or None


def name_pixel(r, g, b):
    """One pixel -> a plain colour name."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    h *= 360
    if v < 0.16 or (v < 0.30 and s < 0.35):
        return "black"
    if s < 0.075:
        # Near-neutral. The shaded folds of a white piece are still white.
        return "white" if v > 0.82 else "grey"
    if 170 <= h < 265:
        if s < 0.22:
            return "white" if v > 0.90 else "grey"
        return "navy" if v < 0.45 else "blue"
    if 15 <= h < 50:
        if v > 0.75 and s < 0.30:
            return "cream"
        if v < 0.50:
            return "brown"
        if s < 0.62:
            return "tan"
        return "orange" if h < 38 else "yellow"
    if 50 <= h < 70:
        if v > 0.75 and s < 0.30:
            return "cream"
        return "green" if v < 0.60 else "yellow"
    if 70 <= h < 170:
        return "green"
    if 265 <= h < 300:
        return "purple"
    if 300 <= h < 345:
        return "burgundy" if v < 0.45 else "pink"
    # reds: 345-360 and 0-15
    if s < 0.14 and v > 0.85:
        return "white"
    if v < 0.50 and s > 0.35:
        return "burgundy"
    if s < 0.45 and v > 0.70:
        return "pink"
    return "red"


def read_image(data):
    """Photo bytes -> {colour name: share of the garment}, or None.

    Looks at the middle of the photo, where the garment is, and ignores a pure
    white studio background unless the whole middle is white (a white piece).
    """
    if Image is None or not data:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((48, 48))
    except Exception:                   # noqa: BLE001
        return None
    counts, background, total = {}, 0, 0
    for y in range(11, 38):
        for x in range(14, 34):
            r, g, b = im.getpixel((x, y))
            total += 1
            if r > 246 and g > 246 and b > 246:
                background += 1
                continue
            n = name_pixel(r, g, b)
            counts[n] = counts.get(n, 0) + 1
    if not total:
        return None
    if background > total * 0.80:
        return {"white": 1.0}
    seen = sum(counts.values())
    # A white piece photographed on white: its lit parts read as background.
    if background > total * 0.35:
        counts["white"] = counts.get("white", 0) + background
        seen += background
    return {k: n / seen for k, n in counts.items()} if seen else None


def _bytes(url):
    if not url:
        return None
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception:               # noqa: BLE001
            return None
    if not url.startswith("http"):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Filo)"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(400_000)


def shares(url):
    """{colour: share} for the photo at this address. Cached."""
    if url in _cache:
        return _cache[url]
    try:
        out = read_image(_bytes(url))
    except Exception as exc:            # noqa: BLE001
        log.info("colour: photo not read (%s)", exc.__class__.__name__)
        return None                     # not cached: worth another try later
    if len(_cache) > 4000:
        _cache.clear()
    _cache[url] = out
    return out


def is_colour(found, colour):
    """True / False from a shares() answer, or None when it could not tell."""
    wanted = ACCEPTS.get((colour or "").lower().strip())
    if not wanted or not found:
        return None
    return sum(found.get(n, 0) for n in wanted) >= MIN_SHARE


def check_all(urls, colour):
    """{url: True/False/None} for up to MAX_CHECKS photos, inside CHECK_BUDGET."""
    urls = [u for u in dict.fromkeys(urls) if u][:MAX_CHECKS]
    out = {}
    if Image is None or not urls or colour not in ACCEPTS:
        return out
    pool = ThreadPoolExecutor(max_workers=min(12, len(urls)))
    try:
        futures = {pool.submit(shares, u): u for u in urls}
        try:
            for f in as_completed(futures, timeout=CHECK_BUDGET):
                try:
                    out[futures[f]] = is_colour(f.result(), colour)
                except Exception:       # noqa: BLE001
                    out[futures[f]] = None
        except TimeoutError:
            log.info("colour: photo budget spent, %d of %d read", len(out), len(urls))
    finally:
        pool.shutdown(wait=False)
    return out


def describe(url):
    """'white 71, grey 20' for a photo already read, else None. For checking
    the reads against real listings; the app ignores it."""
    found = _cache.get(url)
    if not found:
        return None
    top = sorted(found.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return ", ".join(f"{k} {round(v * 100)}" for k, v in top)
