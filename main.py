"""
Filo AI backend — the brain the app calls.
Run locally:   uvicorn main:app --reload   →   http://localhost:8000/docs
The one endpoint the app uses is POST /analyze.
"""
import os
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fabric
import catalog
import events
import aggregates
import style
import vision
import affiliate
import accounts

app = FastAPI(title="Filo AI")


@app.on_event("startup")
def _startup():
    # Best-effort. No DATABASE_URL just means analytics is off; scans still work.
    events.init_schema()
    accounts.init_schema()

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
    composition: str
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


class AnalyzeRequest(BaseModel):
    item: Item


@app.get("/")
def root():
    return {"ok": True, "service": "Filo AI"}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    item = {
        "name": req.item.name,
        "brand": req.item.brand,
        "category": req.item.category,
        "price": req.item.price,
        "composition": req.item.composition,
    }

    result = fabric.analyze(item)

    score = result.get("score")

    # Price vs how it's made. Only ever on a price the shopper actually gave —
    # never on an estimate.
    _, matched = fabric.quality_score(item["composition"])
    group = catalog.garment_group(item.get("category"))
    if item.get("price") is not None and score is not None:
        result["price_note"] = fabric.price_note(score, matched, item["price"], group,
                                                 result.get("material"))
        result["price_prompt"] = None
    else:
        result["price_note"] = None
        # The fabric-only value line ("paying for the name") needs a price too.
        result["value_note"] = None
        result["price_prompt"] = fabric.PRICE_PROMPT if score is not None else None

    department = catalog.normalize_department(req.item.department, item.get("category"))

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
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        style_future = pool.submit(style.style_read, item)

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

        # catalog builds its own multi-angle search (fiber, certification, and the
        # names of makers known for cloth) because one generic query only ever
        # returns whoever has the biggest product feed. See brands.py.
        alt_future = pool.submit(
            catalog.search_alternatives,
            category=item.get("category"),
            name=item.get("name"),
            price=item.get("price"),
            scanned_score=score,
            look=look,
            material=result.get("material"),
            department=department,
        )

        try:
            alternatives = alt_future.result(timeout=catalog.SEARCH_BUDGET + 2)
        except Exception:                   # noqa: BLE001
            alternatives = []

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
        # LAST. The list is already found, filtered, scored and sorted — this
        # only rewrites where each link points. Nothing is added, dropped or
        # reordered here, which is what keeps "no paid rankings" literally true.
        # See affiliate.py. Inert until AFFILIATE_NETWORK is configured.
        affiliate.decorate(alternatives,
                           scanned_score=score,
                           category=item.get("category"))
        result["alternatives"] = alternatives
        result["alternatives_note"] = None
    elif not catalog.SERPAPI_KEY:
        result["alternatives_note"] = (
            "Better-made options turn on once product search is connected."
        )
    else:
        # Searched and found nothing we could verify. Say so plainly rather than
        # padding the list with items whose fabric we can't read.
        result["alternatives_note"] = (
            "Nothing here we'd vouch for. We only show an alternative when the "
            "listing states its fiber content and it genuinely scores better than "
            "what you're holding."
        )

    return result


# ----------------------------------------------------------------- app config
# Things Brooklyn changes without shipping an app update. Set on Railway:
#   HOME_IMAGE_URL   public https link to the home screen photo
#   HOME_IMAGE_ALT   one short line describing it (for VoiceOver)
# Changing a variable redeploys in about a minute; the app picks up the new image
# the next time it's opened.

@app.get("/config")
def config():
    return {
        "home_image_url": os.environ.get("HOME_IMAGE_URL") or None,
        "home_image_alt": os.environ.get("HOME_IMAGE_ALT") or None,
    }


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
    accounts.delete(_account_id(authorization))
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


@app.get("/internal/shelf/coverage")
def shelf_coverage(x_filo_admin: Optional[str] = Header(default=None)):
    """Are we dense enough to sell anything yet?"""
    _require_admin(x_filo_admin)
    return aggregates.coverage()
