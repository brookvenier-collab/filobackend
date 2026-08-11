"""
Filo AI backend — the brain the app calls.

Run locally:   uvicorn main:app --reload
Then open:     http://localhost:8000/docs   (interactive tester)

The one endpoint the app uses is POST /analyze.
"""
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fabric

app = FastAPI(title="Filo AI")

# Allow the app (and the web) to call this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return fabric.analyze(item)
