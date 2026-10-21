"""The rules that decide what can be shown, shared by Studio, the catalogue import and refresh.

Two kinds of decision:
- Brooklyn's, made in Studio (decided_by = 'brooklyn'). Never overwritten by the rules.
- The rules', for whole-catalogue imports (decided_by = 'rules'). Clear cases go live on
  their own; borderline ones wait for Brooklyn; a small random share of the live ones is
  marked for a spot check so she can see the rules are doing their job.

Switch the automatic part off with the Railway variable BANK_AUTO_APPROVE=0. Then every
catalogue product waits for Brooklyn, like a single import does.
"""
import os
import random
from typing import Dict, List, Optional, Tuple

import bank_import
import catalog

SHOWABLE_STOCK = ("in_stock", "low_stock", "preorder", "made_to_order")
SPOT_CHECK_RATE = 0.05
BORDERLINE_SYNTHETIC = 35            # 35-49% synthetic is allowed but worth a look


def auto_approve_on() -> bool:
    return (os.environ.get("BANK_AUTO_APPROVE", "1").strip().lower() not in ("0", "false", "no", "off"))


def _fact(record: dict, path: str) -> dict:
    node = record
    for p in path.split("."):
        node = node.get(p, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def go_live_problems(record: dict, brand_approved: bool, decision: Optional[str]) -> List[str]:
    """Why this product can't be shown yet. Empty means it can go live."""
    out = []
    if decision not in ("pass", "conditional"):
        out.append("The decision has to be pass or conditional.")
    if not brand_approved:
        out.append("The brand isn't approved yet. Approve it in Brands first.")
    variants = record.get("variants") or []
    if not any(bank_import.current_price(v) is not None for v in variants):
        out.append("No price with its currency was found.")
    comp = _fact(record, "materials.composition")
    if comp.get("status") != "confirmed":
        out.append("What it's made of isn't confirmed.")
    else:
        a = assess(record)
        out += a["rule_problems"]
    return out


def assess(record: dict) -> dict:
    comp = _fact(record, "materials.composition")
    origin = _fact(record, "construction.country_of_assembly").get("value")
    variants = record.get("variants") or []
    return bank_import.filo_assessment(comp.get("value"), record.get("category"),
                                       next((v.get("price_cad") for v in variants if v.get("price_cad")), None),
                                       record.get("name"), origin)


def stock_state(record: dict) -> str:
    """in_stock / sold_out / unknown, across every variant."""
    av = {v.get("availability") for v in record.get("variants") or []}
    if av & set(SHOWABLE_STOCK):
        return "in_stock"
    if av and av <= {"unknown"}:
        return "unknown"
    return "sold_out"


def borderline_reasons(record: dict) -> List[str]:
    """Things the rules shouldn't decide alone."""
    out = []
    a = assess(record)
    # Not a reason: a score at or just above the floor. Plain 100% cotton sits exactly on it (70),
    # and Filo's own floor already decides that.
    syn = a.get("synthetic_pct")
    if syn is not None and BORDERLINE_SYNTHETIC <= syn < catalog.MAX_SYNTHETIC:
        out.append(f"{syn:.0f}% synthetic, close to the limit.")
    comp = _fact(record, "materials.composition")
    if comp.get("source_type") == "inferred":
        out.append("What it's made of came from Filo's reading, not the page's own words.")
    if record.get("category") in (None, "", "uncategorized"):
        out.append("Garment type not found.")
    if stock_state(record) == "unknown":
        out.append("The page doesn't say what's in stock.")
    return out


def auto_decide(record: dict, brand_approved: bool, rng: Optional[random.Random] = None) -> Dict[str, object]:
    """What the rules do with one catalogue product.

    Returns {'status', 'decided_by', 'outcome', 'reasons', 'spot_check'} where outcome is
    live / hidden_out_of_stock / review / held / waiting.
    """
    f = record.get("filo") or {}
    decision = f.get("decision")
    if decision == "insufficient_evidence":
        return {"status": "draft", "decided_by": None, "outcome": "waiting",
                "reasons": f.get("missing_evidence") or [], "spot_check": False}
    if decision == "hold":
        return {"status": "draft", "decided_by": None, "outcome": "held",
                "reasons": [f.get("decision_reason") or ""], "spot_check": False}
    problems = go_live_problems(record, brand_approved, decision)
    if problems:
        return {"status": "draft", "decided_by": None, "outcome": "held", "reasons": problems, "spot_check": False}
    border = borderline_reasons(record)
    if border or not auto_approve_on():
        return {"status": "in_review", "decided_by": None, "outcome": "review",
                "reasons": border or ["Automatic approval is switched off."], "spot_check": False}
    state = stock_state(record)
    rng = rng or random
    return {"status": "live" if state == "in_stock" else "hidden_out_of_stock", "decided_by": "rules",
            "outcome": "live" if state == "in_stock" else "hidden_out_of_stock", "reasons": [],
            "spot_check": rng.random() < SPOT_CHECK_RATE}


def apply_auto(product_id: str, outcome: Dict[str, object]) -> None:
    """Write the rules' outcome to a saved product. Never touches one Brooklyn decided."""
    import json
    import events
    conn = events._conn()
    if conn is None:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE bank_products SET status = %s, decided_by = %s,
                    decided_on = CASE WHEN %s IS NULL THEN decided_on ELSE CURRENT_DATE END,
                    record = jsonb_set(record, '{status}', to_jsonb(%s::text)),
                    studio_edits = CASE WHEN %s THEN studio_edits || '{"spot_check": true}'::jsonb
                                        ELSE studio_edits - 'spot_check' END,
                    auto_reasons = %s::jsonb
                WHERE product_id = %s AND coalesce(decided_by, '') <> 'brooklyn'""",
                        (outcome["status"], outcome["decided_by"], outcome["decided_by"], outcome["status"],
                         bool(outcome["spot_check"]), json.dumps(outcome["reasons"]), product_id))
    finally:
        conn.close()
