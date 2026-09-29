"""
Filo AI backend — the brain the app calls.
Run locally:   uvicorn main:app --reload   →   http://localhost:8000/docs
The one endpoint the app uses is POST /analyze.
"""
import os
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
import html as _html
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
import fund

app = FastAPI(title="Filo AI")


@app.on_event("startup")
def _startup():
    # Best-effort. No DATABASE_URL just means analytics is off; scans still work.
    events.init_schema()
    accounts.init_schema()
    fund.init_schema()
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


@app.get("/internal/shelf/coverage")
def shelf_coverage(x_filo_admin: Optional[str] = Header(default=None)):
    """Are we dense enough to sell anything yet?"""
    _require_admin(x_filo_admin)
    return aggregates.coverage()
