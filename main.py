"""
Filo AI backend — the brain the app calls.
Run locally:   uvicorn main:app --reload   →   http://localhost:8000/docs
The one endpoint the app uses is POST /analyze.
"""
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fabric
import catalog

app = FastAPI(title="Filo AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_BETTER_FIBER = "cotton"


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

    query = (item.get("name") or item.get("category") or "").strip()
    score = result.get("score")
    if query and item.get("category") and (score is None or score < 7):
        query = f"{item['category']} {_BETTER_FIBER}"

    alternatives = catalog.search_alternatives(query, price=item.get("price"))
    if alternatives:
        result["alternatives"] = alternatives
        result["alternatives_note"] = None

    return result
