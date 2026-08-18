"""
Filo AI backend — the brain the app calls.
Run locally:   uvicorn main:app --reload   →   http://localhost:8000/docs
The one endpoint the app uses is POST /analyze.
"""
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fabric
import catalog
import events
import aggregates

app = FastAPI(title="Filo AI")


@app.on_event("startup")
def _startup():
    # Best-effort. No DATABASE_URL just means analytics is off; scans still work.
    events.init_schema()

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

    # catalog builds its own multi-angle search (fiber, certification, and the
    # names of makers known for cloth) because one generic query only ever
    # returns whoever has the biggest product feed. See brands.py.
    alternatives = catalog.search_alternatives(
        category=item.get("category"),
        name=item.get("name"),
        price=item.get("price"),
        scanned_score=score,
    )

    if alternatives:
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
