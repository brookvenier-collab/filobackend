"""
Filo AI backend — the brain the app calls.
Run locally:   uvicorn main:app --reload   →   http://localhost:8000/docs
The one endpoint the app uses is POST /analyze.
"""
import os
import re
from datetime import datetime, timezone
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
import html as _html
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fabric
import catalog
import market
import products
import events
import aggregates
import style
import vision
import affiliate
import accounts
import fund
import seams
import homecard
import breakdown
import value

app = FastAPI(title="Filo AI")


@app.on_event("startup")
def _startup():
    # Best-effort. No DATABASE_URL just means analytics is off; scans still work.
    events.init_schema()
    accounts.init_schema()
    fund.init_schema()
    homecard.init_schema()
    products.init_schema()
    products.purge_votes()
    fund.start_daily_thread()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# (query building now lives in brands.build_queries)


class Item(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    # v29: optional when a barcode is sent and Filo already knows the product.
    composition: Optional[str] = ""
    # Optional base64 JPEG of the garment itself (not the care label). Lets the
    # search look for the right SHAPE, not just the right fibre. Omit it and the
    # scan behaves exactly as it did before vision existed.
    image: Optional[str] = None
    # v11. All optional, so the 1.0 app keeps working unchanged.
    department: Optional[str] = None        # "Women's" | "Men's" | "Everyone"
    age_band: Optional[str] = None          # accepted; not used for anything yet
    price_source: Optional[str] = None      # "entered" | "scanned" | "none"
    # "score" returns the verdict alone in well under a second (no search, no
    # AI calls) so the app can show it the moment the tag is read. "full" (the
    # default) is everything, as before.
    mode: Optional[str] = "full"
    # v13. What the seam photo (or the shopper) established about how it's made,
    # as tokens from fabric.CONSTRUCTION, e.g. ["french_seams", "quality_hardware"].
    # Unknown tokens are ignored. Without it, a tag-only scan tops out at 7.9.
    construction: Optional[List[str]] = None
    # v13. Optional base64 JPEG of an INSIDE seam. Read once by seams.py into
    # construction tokens, then discarded like the garment photo.
    seam_image: Optional[str] = None
    # v16. The colour the shopper picked on the verdict screen ("black", "navy").
    # One of vision.COLOR; anything else is ignored. It goes on the searches,
    # and (v25) each piece's photo is checked: when any piece is that colour,
    # only those are shown; when none is, the closest are shown with a note.
    # It never changes a score or lets a piece past the quality rules.
    color: Optional[str] = None
    # v23. What this shopper is drawn to, worked out on their phone:
    # {"words": ["cropped", "linen"], "brands": ["Sezane"]}. Used only to ORDER
    # the better made options and add one brand search. Cleaned by
    # catalog.clean_taste; never stored, never tied to an account.
    taste: Optional[dict] = None
    # v28. "CA" or "US": which country's listings the price is compared with,
    # and in which currency. Without it the comparison uses US listings in USD
    # and says so.
    country: Optional[str] = None
    # v29. The product database (products.py). The barcode from the price tag,
    # the full text read off the care label (for the RN/CA number and country
    # made), and the app's random per-launch session ID, which is only ever
    # stored as a daily-rotating hash to stop double votes.
    gtin: Optional[str] = None
    tag_text: Optional[str] = None
    session_id: Optional[str] = None


EXTRA_LOOK_PICKS = {"leather"}


class AnalyzeRequest(BaseModel):
    item: Item


def _product_step(req, known, scanned_composition, from_db, score):
    """Teach the product database from this scan, then say what it knows."""
    if not req.item.gtin:
        return None
    if products.normalize_gtin(req.item.gtin) is None:
        return {"status": "invalid", "line": "That barcode didn't read. Try again, or scan the care tag."}
    if scanned_composition and score is not None:
        facts = {"brand": req.item.brand, "name": req.item.name,
                 "category": req.item.category, "composition": scanned_composition}
        facts.update(products.from_tag(req.item.tag_text or scanned_composition))
        try:
            products.contribute(req.item.gtin, facts, req.item.session_id)
        except Exception:                   # noqa: BLE001  never block a verdict
            pass
        known = products.lookup(req.item.gtin)
    if not known:
        return {"status": "new",
                "line": "First scan of this one. Scan the care tag to add it to Filo."}
    known = dict(known)
    known["source"] = "filo_database" if from_db else "your_scan"
    if from_db:
        known["line"] = ("From Filo's database." if known["status"] in ("confirmed", "locked")
                         else "From one other scan, not confirmed yet. Check the care tag.")
    return known


@app.get("/products/{gtin}")
def product_lookup(gtin: str):
    """v29. What Filo knows about a barcode, with each field's status."""
    p = products.lookup(gtin)
    if not p:
        return {"gtin": products.normalize_gtin(gtin), "status": "new"}
    return p


@app.get("/")
def root():
    return {"ok": True, "service": "Filo AI"}


def _composition_total(text):
    """Sum of the percentages actually written in the main fabric. A lone
    "64% polyester" is 64 here, even though it would score as the whole piece."""
    main, _ = fabric.split_sections(text or "")
    t = fabric.translate_tag(main)
    return sum(int(x) for x in re.findall(r"(\d{1,3})\s*%", t) if 0 < int(x) <= 100)


def _best_composition(composition, tag_text):
    """v30.3. The app sends the fibres it read, and (1.2+) the whole tag. When
    the app's read doesn't add up to the whole piece but the tag does, the tag
    wins, so a partial camera read can never become the verdict."""
    if not tag_text or 95 <= _composition_total(composition) <= 105:
        return composition
    main, support = fabric.split_sections(tag_text)
    canon = products.canonical_composition(main)
    if not canon:
        return composition
    lining = products.canonical_composition(support) if support.strip() else None
    return canon + (f" Lining: {lining}" if lining else "")


def _apply_fair(result, fair, market_check=None):
    """Put the fair price on the verdict, and on the fields the 1.x app reads."""
    result["fair_price"] = value.public(fair)
    if not fair:
        return
    if fair.get("level"):
        # The fair price leads. The shop comparison follows when it has one.
        shops = None
        if market_check and market_check.get("level"):
            shops = market_check.get("line")
        result["price_note"] = " ".join(x for x in (
            fair["label"] + ".", fair["line"], shops) if x)
    elif result.get("price_prompt"):
        # No price yet: say what it should cost, then ask.
        result["price_prompt"] = f"{fair['range_line']} {fabric.PRICE_PROMPT}"


def _value_alternatives(alternatives, item, country):
    """Fair price on each better made option; better value first within a fit."""
    for i, alt in enumerate(alternatives):
        comp = alt.get("composition") or ""
        detail = fabric.quality_detail(comp, allow_bare=False) if comp else None
        v = value.assess({"composition": comp, "category": item.get("category"),
                          "name": alt.get("name"), "price": alt.get("price"),
                          "country": country}, detail, alt.get("score")) if detail else None
        alt["fair_price"] = value.compact(v)
        alt["_order"] = i
    fit_rank = {"better": 0, "pricier": 1, "as_good": 2}
    alternatives.sort(key=lambda a: (fit_rank.get(a.get("fit"), 3),
                                     value.RANK.get((a.get("fair_price") or {}).get("level"), 8)
                                     if a.get("fit") == "better" else 0,
                                     a["_order"]))
    for a in alternatives:
        a.pop("_order", None)


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    # v29: a barcode Filo has seen before fills in what the shopper didn't send.
    known = products.lookup(req.item.gtin) if req.item.gtin else None
    scanned_composition = _best_composition((req.item.composition or "").strip(),
                                            req.item.tag_text)
    from_db = not scanned_composition and bool(products.value(known, "composition"))
    item = {
        "name": req.item.name or products.value(known, "name"),
        "brand": req.item.brand or products.value(known, "brand"),
        "category": req.item.category or products.value(known, "category"),
        "price": req.item.price,
        "composition": scanned_composition or products.value(known, "composition") or "",
        "construction": (req.item.construction or [])[:12],
    }
    if req.item.category is None and item["category"]:
        req.item.category = item["category"]
    # v26: the garment type sets the base life and wears per wash.
    item["group"] = catalog.garment_group(req.item.category)

    seam_tokens = []
    if req.item.seam_image:
        seam_tokens = seams.read(req.item.seam_image)
        item["construction"] = sorted(set(item["construction"]) | set(seam_tokens))

    result = fabric.analyze(item)
    # v30.3: what Filo actually scored, so the app can show the full make.
    result["composition_read"] = item["composition"] or None
    result["product"] = _product_step(req, known, scanned_composition, from_db,
                                      result.get("score"))
    if req.item.seam_image:
        # What the seam photo showed, so the app can say so — even when it was
        # nothing ("we couldn't make out the seam — try closer, in good light").
        result["seam_read"] = {"tokens": seam_tokens, "read": bool(seam_tokens)}

    # v21: the verdict taken apart row by row, in plain words (breakdown.py).
    try:
        result["breakdown"] = breakdown.build(item["composition"], item["construction"], result)
    except Exception:                       # noqa: BLE001  never block a verdict
        result["breakdown"] = []

    score = result.get("score")

    # Price vs what similar pieces sell for (v28, market.py). Only ever on a
    # price the shopper actually gave, never an estimate, and only from live
    # listings: the old hand-set price ranges are no longer used for verdicts.
    _, matched = fabric.quality_score(item["composition"])
    group = catalog.garment_group(item.get("category"))
    result["value_note"] = None
    if item.get("price") is not None and score is not None:
        result["price_prompt"] = None
        if group is None:
            result["price_check"] = {"level": None, "label": None, "pending": False,
                                     "line": "Tell us what it is to compare the price."}
        else:
            result["price_check"] = {"level": None, "label": None, "pending": True,
                                     "line": "Checking similar listings."}
        result["price_note"] = None
    else:
        result["price_check"] = None
        result["price_note"] = None
        result["price_prompt"] = fabric.PRICE_PROMPT if score is not None else None

    department = catalog.normalize_department(req.item.department, item.get("category"))

    # v30. The fair price: what a piece made like this should cost, from what
    # it's made of and how and where it's made (value.py, pricebank.json). On
    # the fast path it has no shop comparison yet; the full path redoes it once
    # the listings are in, so its wording can take them into account.
    value_item = {"composition": item["composition"], "category": item.get("category"),
                  "name": item.get("name"), "price": item.get("price"),
                  "country": req.item.country, "tag_text": req.item.tag_text}
    value_detail = fabric.quality_detail(item["composition"],
                                         construction=item["construction"]) if score is not None else None
    fair = value.assess(value_item, value_detail, score)
    _apply_fair(result, fair)
    # v30.2: the generic fair price on the verdict page itself, as a breakdown
    # row the app shows under "WHAT IT SHOULD COST". From the fibre (and the
    # garment once picked), never from the shopper's price.
    result["breakdown"] = (result.get("breakdown") or []) + \
        value.breakdown_rows(value_item, value_detail, score, fair)
    result["breakdown_sections"] = BREAKDOWN_SECTIONS

    if (req.item.mode or "full") == "score" or score is None:
        # The fast path: verdict only. The app asks again with mode="full" (or
        # with a category) for the alternatives.
        result["alternatives"] = []
        result["alternatives_note"] = None
        result["pending"] = score is not None
        return result
    result["pending"] = False

    if not item.get("category") and not item.get("name"):
        # Nothing to search on. Say what to do instead of an empty shrug.
        result["alternatives"] = []
        result["alternatives_note"] = "Tell us what it is to see better-made options."
        return result

    # Three calls to other people's services. The style read is independent, so
    # it runs alongside everything. The vision read is NOT — the search needs the
    # silhouette before it can look for it — so it gates the search and is kept
    # on a deliberately short leash.
    #
    # With no image (the default) nothing waits on vision and the timing is
    # identical to before this existed.
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        style_future = pool.submit(style.style_read, item)
        price_future = None
        if (result.get("price_check") or {}).get("pending"):
            price_future = pool.submit(market.check, item["price"], group, matched,
                                       department, None, req.item.country)

        look = []
        if req.item.image:
            try:
                seen = pool.submit(vision.describe, req.item.image) \
                           .result(timeout=vision.VISION_TIMEOUT + 1)
            except Exception:               # noqa: BLE001
                seen = None
            if seen:
                result["look"] = seen
                look = vision.descriptors(seen)

        # The shopper's own colour pick beats the photo's guess, and goes first
        # so it always makes it into the search (queries use the first 3 words).
        # "leather" isn't a colour, but shoppers pick it like one ("a leather
        # jacket"), so the colour row offers it and it joins the search the same way.
        chosen = (req.item.color or "").strip().lower()
        if chosen in vision.COLOR or chosen in EXTRA_LOOK_PICKS:
            look = [chosen] + [w for w in look if w not in vision.COLOR]
            result["color"] = chosen

        # Picking "Leather" means "show me leather": the options are searched as
        # leather and only real leather is shown, exactly as if the tag said so.
        search_material = result.get("material")
        if chosen == "leather" and not search_material:
            search_material = "real leather"

        # catalog builds its own multi-angle search (fiber, certification, and the
        # names of makers known for cloth) because one generic query only ever
        # returns whoever has the biggest product feed. See brands.py.
        # v30: with no price, the options are kept near what this make should
        # fairly cost instead of a flat typical price for the garment.
        assumed_override = None
        if item.get("price") is None and fair and fair.get("fair_high"):
            assumed_override = fair["fair_high"]
        alt_future = pool.submit(
            catalog.search_alternatives,
            category=item.get("category"),
            name=item.get("name"),
            price=item.get("price"),
            scanned_score=score,
            scanned_durability=result.get("durability"),
            scanned_wears=(result.get("lifespan") or {}).get("wears"),
            look=look,
            material=search_material,
            department=department,
            taste=req.item.taste,
            colour=chosen if chosen in vision.COLOR else None,
            assumed_price=assumed_override,
        )

        try:
            alternatives = alt_future.result(timeout=catalog.SEARCH_BUDGET + 2)
        except Exception:                   # noqa: BLE001
            alternatives = []

        if price_future is not None:
            try:
                pc = price_future.result(timeout=catalog.SEARCH_BUDGET + 2)
            except Exception:               # noqa: BLE001
                pc = None
            pc = pc or {"level": None, "label": None, "line": market.NOT_ENOUGH}
            pc["pending"] = False
            result["price_check"] = pc
            # The 1.x app shows price_note as one line, so it carries the whole
            # verdict and what it's based on.
            result["price_note"] = " ".join(x for x in (
                (pc.get("label") + ".") if pc.get("label") else None,
                pc.get("line"), pc.get("based_on")) if x)
            # v30: the fair price leads; what shops charge follows it.
            fair = value.assess(value_item, value_detail, score, market_check=pc) or fair
            _apply_fair(result, fair, pc)

        try:
            # The style read is garnish. It gets 4 seconds past the search and
            # is dropped if it isn't back — it must never hold up a verdict.
            read = style_future.result(timeout=4)
        except Exception:                   # noqa: BLE001
            read = None
    finally:
        # Never wait on a hung provider at exit — the verdict is the product.
        pool.shutdown(wait=False)

    if read:
        result["style_read"] = read

    if alternatives:
        # v30: each option gets its own fair price read, and among the options
        # that passed the same rules, better value comes first.
        _value_alternatives(alternatives, item, req.item.country)
        # LAST. The list is already found, filtered, scored and sorted — this
        # only rewrites where each link points. Nothing is added, dropped or
        # reordered here, which is what keeps "no paid rankings" literally true.
        # See affiliate.py. Inert until AFFILIATE_NETWORK is configured.
        # Sign each (url, score) BEFORE the links are rewritten, so a Closet Fund
        # purchase bonus can only ever follow a score Filo actually gave.
        for alt in alternatives:
            alt["score_sig"] = fund.sign_score(alt.get("url"), alt.get("score"))
        affiliate.decorate(alternatives,
                           scanned_score=score,
                           category=item.get("category"))
        result["alternatives"] = alternatives
        # v22: set only when every pick needed the price rules loosened.
        result["alternatives_note"] = (
            catalog.note_for(alternatives)
            or catalog.colour_note(alternatives,
                                   chosen if chosen in vision.COLOR else None))
    elif not catalog.SERPAPI_KEY:
        result["alternatives_note"] = (
            "Better-made options turn on once product search is connected."
        )
    else:
        # Searched and found nothing we could verify. Say so plainly rather than
        # padding the list with items whose fabric we can't read.
        result["alternatives_note"] = (
            "We could not find one we would vouch for this time. We only show a "
            "piece when the shop says what it is made of. Try another colour or "
            "pick a different type."
        )

    return result


# ----------------------------------------------------------------- app config
# Things Brooklyn changes without shipping an app update.
#
# THE SEASON (v14). The home card follows the season on its own: Filo works out
# which season it is from the date and sends that season's photo and copy.
# Set the four photos once and it rotates for the rest of the year.
#
#   HOME_IMAGE_URL_FALL / _WINTER / _SPRING / _SUMMER   public https photo links
#   HOME_IMAGE_ALT_FALL / _WINTER / _SPRING / _SUMMER   one line each, for VoiceOver
#   HOME_EYEBROW_<SEASON>, HOME_TITLE_<SEASON>          optional: replace the copy
#
# Overrides for a one-off (a launch, a holiday) — these beat the season:
#   HOME_IMAGE_URL, HOME_IMAGE_ALT, HOME_EYEBROW, HOME_TITLE
#   HOME_SEASON = fall | winter | spring | summer   pin a season by hand
#
# Seasons are northern-hemisphere retail months: Fall Sep–Nov, Winter Dec–Feb,
# Spring Mar–May, Summer Jun–Aug. Changing a variable redeploys in about a
# minute; phones pick it up the next time Filo is opened.

SEASON_COPY = {
    "fall":   ("COAT SEASON",  "Check the lining."),
    "winter": ("KNIT SEASON",  "Wool, not acrylic."),
    "spring": ("DENIM SEASON", "Heavier is better."),
    "summer": ("LINEN SEASON", "Let it breathe."),
}


def season_for(month: int) -> str:
    if month in (9, 10, 11):
        return "fall"
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    return "summer"


def home_config(month: Optional[int] = None, base_url: str = "") -> dict:
    """What the home card shows right now. Priority, highest first:
         1. the "Now" slot on /admin/home           (a launch, a holiday)
         2. HOME_IMAGE_URL / HOME_EYEBROW / HOME_TITLE on Railway
         3. this season's slot on /admin/home
         4. HOME_IMAGE_URL_<SEASON> etc. on Railway
         5. the season's default line; no photo → the app keeps its bundled one
    """
    env = os.environ.get
    pinned = (env("HOME_SEASON") or "").strip().lower()
    season = pinned if pinned in SEASON_COPY else \
        season_for(month or datetime.now(timezone.utc).month)
    S = season.upper()
    eyebrow, title = SEASON_COPY[season]
    now_card = homecard.meta("now")
    season_card = homecard.meta(season)

    def card_image(slot, card):
        if card and card["has_image"]:
            return f"{base_url}/home-image/{slot}?v={card['version']}"
        return None

    def pick(field, env_now, env_season, default=None):
        return ((now_card or {}).get(field) or env(env_now)
                or (season_card or {}).get(field) or env(env_season) or default)

    image_url = (card_image("now", now_card) or env("HOME_IMAGE_URL")
                 or card_image(season, season_card) or env(f"HOME_IMAGE_URL_{S}") or None)
    return {
        "season": season,
        "home_image_url": image_url,
        "home_image_alt": pick("alt", "HOME_IMAGE_ALT", f"HOME_IMAGE_ALT_{S}"),
        "home_eyebrow": pick("eyebrow", "HOME_EYEBROW", f"HOME_EYEBROW_{S}", eyebrow),
        "home_title": pick("title", "HOME_TITLE", f"HOME_TITLE_{S}", title),
    }


def _public_base(request: Request) -> str:
    """Absolute https origin for links the app will open. Railway terminates TLS
    in front of us, so the request itself looks like plain http."""
    fixed = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if fixed:
        return fixed
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return f"https://{host}" if host else ""


# v30.6. What the app shows that the server decides, so changing it never
# needs an App Store update. The 1.2 app reads these; older apps ignore them.
# Add a category here and it appears as a chip the next time the app opens
# (the backend still needs to know the type in catalog.GARMENT_GROUPS to search
# it well, and pricebank.json to price it).
APP_CATEGORIES = [
    "T-shirt", "Long sleeve", "Tank", "Top", "Shirt", "Sweater", "Cardigan",
    "Hoodie", "Knitwear", "Dress", "Skirt", "Jeans", "Trousers", "Sweatpants",
    "Shorts", "Leggings", "Jacket", "Blazer", "Coat", "Activewear", "Bag",
]
# Names must be in vision.COLOR (lowercased) or EXTRA_LOOK_PICKS.
APP_COLORS = [
    {"name": "Black", "hex": "1A1A1A"}, {"name": "White", "hex": "FFFFFF"},
    {"name": "Cream", "hex": "F2E8D1"}, {"name": "Grey", "hex": "999999"},
    {"name": "Navy", "hex": "1F2B54"}, {"name": "Blue", "hex": "5C80B8"},
    {"name": "Brown", "hex": "664229"}, {"name": "Tan", "hex": "C7A378"},
    {"name": "Green", "hex": "54704F"}, {"name": "Red", "hex": "BF2929"},
    {"name": "Burgundy", "hex": "6E1F24"}, {"name": "Pink", "hex": "EDB8BF"},
    {"name": "Leather", "hex": "3D261C"},
]
# The result page's sections, in order. A row's "side" picks its section.
BREAKDOWN_SECTIONS = [
    {"side": "bad", "label": "HOLDING IT BACK"},
    {"side": "price", "label": "WHAT IT SHOULD COST"},
    {"side": "good", "label": "WHAT IS GOOD"},
    {"side": "note", "label": "NOT CHECKED"},
]


@app.get("/config")
def config(request: Request):
    out = home_config(base_url=_public_base(request))
    out["categories"] = APP_CATEGORIES
    out["colors"] = APP_COLORS
    out["breakdown_sections"] = BREAKDOWN_SECTIONS
    return out


# ----------------------------------------------------------------- home card admin
# Open /admin/home in any browser, unlock with ADMIN_TOKEN, tap a card, pick a
# photo, Save. See homecard.py.

@app.get("/admin/home", response_class=HTMLResponse, include_in_schema=False)
def admin_home_page():
    return HTMLResponse(homecard.ADMIN_PAGE, headers={"Cache-Control": "no-store"})


@app.get("/admin/home/state", include_in_schema=False)
def admin_home_state(request: Request, x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    live_cfg = home_config(base_url=_public_base(request))
    live = "now" if homecard.meta("now") else live_cfg["season"]
    return {"slots": homecard.all_meta(), "live": live,
            "defaults": {s: {"eyebrow": e, "title": t} for s, (e, t) in SEASON_COPY.items()}}


@app.post("/admin/home/{slot}", include_in_schema=False)
def admin_home_save(slot: str, card: homecard.CardIn,
                    x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    try:
        return homecard.save(slot, card)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError:
        raise HTTPException(status_code=503, detail="The database isn't reachable right now.")


@app.delete("/admin/home/{slot}", include_in_schema=False)
def admin_home_clear(slot: str, x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    try:
        return homecard.clear(slot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError:
        raise HTTPException(status_code=503, detail="The database isn't reachable right now.")


@app.get("/home-image/{slot}", include_in_schema=False)
def home_image(slot: str):
    found = homecard.image(slot)
    if not found:
        raise HTTPException(status_code=404, detail="No image")
    data, ctype = found
    # Each save changes the ?v= in the link, so a long cache is safe.
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ----------------------------------------------------------------- accounts
# Sign in with Apple -> one row per person. Scans are NOT linked. See accounts.py.

def _account_id(authorization: Optional[str]) -> str:
    account_id = accounts.check_session(authorization)
    if not account_id:
        raise HTTPException(status_code=401, detail="Sign in again.")
    return account_id


@app.post("/accounts")
def create_account(req: accounts.SignUpRequest):
    try:
        claims = accounts.verify_apple_token(req.identity_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    try:
        acct = accounts.upsert(claims["sub"], claims.get("email"), req)
    except Exception:                       # noqa: BLE001
        raise HTTPException(status_code=503, detail="Accounts are unavailable right now.")
    return {"account": acct, "session_token": accounts.issue_session(acct["account_id"])}


@app.get("/accounts/me")
def get_account(authorization: Optional[str] = Header(default=None)):
    acct = accounts.get(_account_id(authorization))
    if not acct:
        raise HTTPException(status_code=404, detail="No account.")
    return {"account": acct}


@app.patch("/accounts/me")
def patch_account(update: accounts.AccountUpdate,
                  authorization: Optional[str] = Header(default=None)):
    acct = accounts.update(_account_id(authorization), update)
    if not acct:
        raise HTTPException(status_code=404, detail="No account.")
    return {"account": acct}


@app.delete("/accounts/me")
def delete_account(authorization: Optional[str] = Header(default=None)):
    account_id = _account_id(authorization)
    fund.delete_account(account_id)
    accounts.delete(account_id)
    # Deleting something already gone is still a success from the shopper's side.
    return {"ok": True}


@app.get("/internal/accounts/summary")
def accounts_summary(x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return accounts.summary()


@app.get("/internal/accounts")
def accounts_list(limit: int = 500, x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return {"accounts": accounts.list_all(limit)}


# ----------------------------------------------------------------- Closet Fund
# Points, fabric tiers, orders. See fund.py for the rules. Nothing here is ever
# called by /analyze — the fund cannot touch a verdict or a ranking.

def _fund_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError:
        raise HTTPException(status_code=503, detail="The Closet Fund is unavailable right now.")


@app.get("/fund")
def fund_summary(authorization: Optional[str] = Header(default=None)):
    return _fund_call(fund.summary_for, _account_id(authorization))


@app.get("/fund/orders")
def fund_orders(authorization: Optional[str] = Header(default=None)):
    return _fund_call(fund.orders_for, _account_id(authorization))


@app.post("/fund/progress")
def fund_progress(ev: fund.ProgressEvent, authorization: Optional[str] = Header(default=None)):
    return _fund_call(fund.record_progress, _account_id(authorization), ev)


@app.post("/fund/link")
def fund_link(req: fund.LinkRequest, authorization: Optional[str] = Header(default=None)):
    """The app calls this on a real tap of a shop link and opens what comes back.
    Signed out is fine: you get a normal link and no points."""
    account_id = accounts.check_session(authorization)
    return _fund_call(fund.make_link, account_id, req)


@app.post("/fund/referral")
def fund_referral(req: fund.ReferralRequest, authorization: Optional[str] = Header(default=None)):
    return _fund_call(fund.enter_referral, _account_id(authorization), req.code)


@app.post("/fund/card")
def fund_card(req: fund.CardRequest, authorization: Optional[str] = Header(default=None)):
    return _fund_call(fund.set_card, _account_id(authorization), req.finish)


@app.post("/fund/redeem")
def fund_redeem(req: fund.RedeemRequest, authorization: Optional[str] = Header(default=None)):
    account_id = _account_id(authorization)
    acct = accounts.get(account_id) or {}
    return _fund_call(fund.redeem, account_id, req.points, acct.get("email"))


@app.get("/internal/fund/summary")
def fund_admin_summary(x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return _fund_call(fund.admin_summary)


@app.get("/internal/fund/redemptions")
def fund_admin_redemptions(status: str = "requested",
                           x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return {"redemptions": _fund_call(fund.list_redemptions, status)}


@app.post("/internal/fund/redemptions/{rid}/{status}")
def fund_admin_resolve(rid: int, status: str,
                       x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return _fund_call(fund.resolve_redemption, rid, status)


@app.post("/internal/fund/purchase")
def fund_admin_purchase(p: fund.ManualPurchase,
                        x_filo_admin: Optional[str] = Header(default=None)):
    """Add a sale by hand from the Sovrn dashboard, if the automatic sync misses one."""
    _require_admin(x_filo_admin)
    return {"result": _fund_call(fund.record_purchase, p.cuid, p.commission_id,
                                 p.order_value, p.status)}


@app.post("/internal/fund/sync")
def fund_admin_sync(day: Optional[str] = None,
                    x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    if day:
        return {"sync": _fund_call(fund.sovrn_sync, day)}
    return _fund_call(fund.run_daily)


# ----------------------------------------------------------------- invite links
# The link in "have you seen this app??" texts. A small Filo page with an App
# Store button. Tapping it copies this invite link; on Filo's first launch the
# app spots it (with iOS's paste permission) and credits the sender once the new
# member signs in. No code is ever shown or typed.
# Nothing about the visitor is stored. INVITE_IMAGE_URL (optional, Railway) is
# the picture Messages shows in the link preview, e.g. the Filo icon as a PNG.

APP_STORE_URL = "https://apps.apple.com/app/id6800848113"

_INVITE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Filo</title>
<meta property="og:title" content="Filo">
<meta property="og:description" content="Scan the tag. See if it's actually worth it.">
<meta property="og:site_name" content="Filo">
{og_image}
<meta name="apple-itunes-app" content="app-id=6800848113">
<style>
body{{margin:0;background:#FBFAF7;color:#1A1714;font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}}
main{{max-width:340px;padding:32px 24px}}
.w{{font-weight:700;letter-spacing:.3em;font-size:15px}}
h1{{font-family:ui-serif,"New York",Georgia,serif;font-weight:400;font-size:32px;line-height:1.15;margin:28px 0 10px}}
p{{color:#6E6659;font-size:15px;line-height:1.5;margin:0 0 28px}}
a.b{{display:block;background:#1A1714;color:#FBFAF7;text-decoration:none;border-radius:10px;padding:16px;
font-size:13px;font-weight:700;letter-spacing:.2em}}
.c{{margin-top:22px;font-size:12px;color:#B7AD9C}}
.c b{{color:#6E1F2A;font-family:ui-monospace,Menlo,monospace;letter-spacing:.15em}}
</style></head><body><main>
<div class="w">FILO</div>
<h1>A friend thinks you'd like Filo.</h1>
<p>Scan the tag on any piece of clothing and see if it's actually worth it.</p>
<a class="b" id="get" href="{store}">GET FILO</a>
<div class="c">Open Filo after it downloads and sign in. Your friend gets the credit.</div>
</main>
<script>
document.getElementById('get').addEventListener('click', function () {{
  try {{ navigator.clipboard && navigator.clipboard.writeText(location.origin + '/i/{code}'); }} catch (e) {{}}
}});
</script></body></html>"""


@app.get("/i/{code}", response_class=HTMLResponse)
def invite_page(code: str):
    clean = fund.clean_code(code) or "FILO"
    image = os.environ.get("INVITE_IMAGE_URL", "").strip()
    og_image = (f'<meta property="og:image" content="{_html.escape(image, quote=True)}">'
                if image.startswith("https://") else "")
    return HTMLResponse(_INVITE_PAGE.format(code=_html.escape(clean), store=APP_STORE_URL,
                                            og_image=og_image))


# ----------------------------------------------------------------- Shelf Intelligence

@app.post("/events")
def ingest_events(batch: events.EventBatch):
    """Anonymous scan telemetry. See events.py for what this deliberately cannot store.

    Always returns ok — a failed write must never surface to a shopper mid-scan.
    """
    stored = events.record(batch.events)
    return {"ok": True, "stored": stored}


def _require_admin(token: Optional[str]):
    if not aggregates.ADMIN_TOKEN or token != aggregates.ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/internal/shelf/brand-quality")
def shelf_brand_quality(days: int = 90, country: Optional[str] = None,
                        x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return {"floor": aggregates.K_ANONYMITY_FLOOR,
            "rows": aggregates.brand_quality(days=days, country=country)}


@app.get("/internal/shelf/category-benchmark")
def shelf_category_benchmark(days: int = 90,
                             x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return {"floor": aggregates.K_ANONYMITY_FLOOR,
            "rows": aggregates.category_benchmark(days=days)}


@app.get("/internal/shelf/rejection")
def shelf_rejection(days: int = 90,
                    x_filo_admin: Optional[str] = Header(default=None)):
    _require_admin(x_filo_admin)
    return {"floor": aggregates.K_ANONYMITY_FLOOR,
            "rows": aggregates.rejection_signal(days=days)}


@app.get("/internal/price-sample")
def price_sample(composition: str, category: str, department: Optional[str] = None,
                 country: Optional[str] = None,
                 x_filo_admin: Optional[str] = Header(default=None)):
    """v28.1. Every listing a price check would count, so a verdict can be
    audited by hand. Admin only."""
    _require_admin(x_filo_admin)
    _, matched = fabric.quality_score(composition)
    group = catalog.garment_group(category)
    dept = catalog.normalize_department(department, category)
    return market.benchmark(group, matched, dept, country=country) or {}


@app.get("/internal/shelf/fair-price")
def shelf_fair_price(days: int = 180, x_filo_admin: Optional[str] = Header(default=None)):
    """v30. Filo's fair price next to what shoppers actually saw, per kind of
    piece, for cells over the privacy floor. Where the two keep disagreeing,
    the bank needs a look. Admin only."""
    _require_admin(x_filo_admin)
    rows = []
    for (group, fibre, q, mkt), cell in sorted(aggregates.price_cells(days=days).items()):
        comp = f"100% {fibre}"
        detail = fabric.quality_detail(comp)
        v = value.assess({"composition": comp, "category": group, "country": mkt},
                         detail, detail["score"] if detail else None)
        rows.append({"group": group, "fibre": fibre, "quality": q, "market": mkt,
                     "shoppers": cell,
                     "fair_low": (v or {}).get("fair_low"), "fair_high": (v or {}).get("fair_high"),
                     "could_be_low": (v or {}).get("could_be_low"),
                     "could_be_high": (v or {}).get("could_be_high")})
    return {"floor": aggregates.K_ANONYMITY_FLOOR, "rows": rows}


@app.get("/internal/fair-price")
def fair_price_sample(composition: str, category: str, price: Optional[float] = None,
                      country: Optional[str] = None, tag_text: Optional[str] = None,
                      x_filo_admin: Optional[str] = Header(default=None)):
    """v30. The fair price and every step behind it, for checking by hand."""
    _require_admin(x_filo_admin)
    detail = fabric.quality_detail(composition)
    v = value.assess({"composition": composition, "category": category, "price": price,
                      "country": country, "tag_text": tag_text},
                     detail, detail["score"] if detail else None)
    if not v:
        return {}
    out = value.public(v)
    out["working"] = v["_cost"]
    return out


class LockRequest(BaseModel):
    fields: dict


@app.post("/internal/products/{gtin}/lock")
def product_lock(gtin: str, req: LockRequest,
                 x_filo_admin: Optional[str] = Header(default=None)):
    """v29. Set fields from a trusted source (e.g. brand-supplied). Locked
    fields can't be changed by shopper votes."""
    _require_admin(x_filo_admin)
    g = products.normalize_gtin(gtin)
    if not g:
        raise HTTPException(status_code=400, detail="invalid barcode")
    s = products.store()
    done = []
    for field, raw in (req.fields or {}).items():
        if field in products.FIELDS:
            v = products._clean(field, raw)
            if v:
                s.set_locked(g, field, v)
                done.append(field)
    return {"gtin": g, "locked": done}


class RegistrantRequest(BaseModel):
    kind: str
    number: str
    registrant: str
    role: Optional[str] = None


@app.post("/internal/registrants")
def registrant_set(req: RegistrantRequest,
                   x_filo_admin: Optional[str] = Header(default=None)):
    """v29. Record who an RN (FTC) or CA (Canada) number is registered to,
    looked up by hand on the public search pages."""
    _require_admin(x_filo_admin)
    kind = req.kind.upper()
    if kind not in ("RN", "CA") or not req.number.strip().isdigit():
        raise HTTPException(status_code=400, detail="kind must be RN or CA, number digits")
    num = req.number.strip().lstrip("0") if kind == "RN" else req.number.strip()
    products.store().set_registrant(kind, num, req.registrant.strip()[:120], req.role)
    return {"ok": True, "kind": kind, "number": num}


@app.get("/internal/products/brands")
def product_brands(x_filo_admin: Optional[str] = Header(default=None)):
    """v29. How many distinct products Filo knows per brand."""
    _require_admin(x_filo_admin)
    return {"rows": products.brand_summary()}


@app.get("/internal/shelf/coverage")
def shelf_coverage(x_filo_admin: Optional[str] = Header(default=None)):
    """Are we dense enough to sell anything yet?"""
    _require_admin(x_filo_admin)
    return aggregates.coverage()
