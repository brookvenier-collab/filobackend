"""
Filo Shelf Intelligence — the only way scan data is ever allowed out of the system.

Every function here enforces K_ANONYMITY_FLOOR. A cohort smaller than the floor is
not rounded, not noised, not caveated — it is not returned at all. This module is
the single exit door, and it is deliberately narrow: if a question can only be
answered by looking at a handful of people, Filo does not answer it.

Nothing in here returns a row, an ID, a session, or a timestamp finer than an hour.
The return type of every public function is a summary.
"""
import os
import logging
from typing import List, Dict, Any, Optional

import events

log = logging.getLogger("filo.aggregates")

# The floor. A brand/category cell must be backed by at least this many distinct
# devices before it can be reported. 30 is the starting point; raising it is cheap,
# lowering it is a decision that needs a reason written down.
K_ANONYMITY_FLOOR = int(os.environ.get("K_ANONYMITY_FLOOR", "30"))

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def _query(sql: str, params: tuple = ()) -> List[tuple]:
    conn = events._conn()
    if conn is None:
        return []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as exc:            # noqa: BLE001
        log.warning("aggregates: query failed (%s)", exc)
        return []
    finally:
        conn.close()


def brand_quality(days: int = 90, country: Optional[str] = None) -> List[Dict[str, Any]]:
    """Average fabric-quality score by brand. The core Shelf Intelligence table.

    Suppressed below the k-anonymity floor.
    """
    rows = _query(
        """
        SELECT brand,
               COUNT(*)                        AS scans,
               COUNT(DISTINCT session_hash)    AS devices,
               ROUND(AVG(score)::numeric, 2)   AS avg_score,
               MIN(score)                      AS worst,
               MAX(score)                      AS best
        FROM scan_events
        WHERE event = 'scan_verdict'
          AND brand IS NOT NULL
          AND score IS NOT NULL
          AND occurred_hour > NOW() - INTERVAL '%s days'
          AND (%%s::text IS NULL OR country = %%s::text)
        GROUP BY brand
        HAVING COUNT(DISTINCT session_hash) >= %%s
        ORDER BY avg_score ASC
        """ % int(days),
        (country, country, K_ANONYMITY_FLOOR),
    )
    return [
        {"brand": r[0], "scans": r[1], "devices": r[2],
         "avg_score": float(r[3]) if r[3] is not None else None,
         "worst": r[4], "best": r[5]}
        for r in rows
    ]


def category_benchmark(days: int = 90) -> List[Dict[str, Any]]:
    """Category averages — the number a brand gets compared against."""
    rows = _query(
        """
        SELECT category,
               COUNT(DISTINCT session_hash)  AS devices,
               ROUND(AVG(score)::numeric, 2) AS avg_score
        FROM scan_events
        WHERE event = 'scan_verdict'
          AND category IS NOT NULL
          AND score IS NOT NULL
          AND occurred_hour > NOW() - INTERVAL '%s days'
        GROUP BY category
        HAVING COUNT(DISTINCT session_hash) >= %%s
        ORDER BY avg_score ASC
        """ % int(days),
        (K_ANONYMITY_FLOOR,),
    )
    return [{"category": r[0], "devices": r[1],
             "avg_score": float(r[2]) if r[2] is not None else None} for r in rows]


def rejection_signal(days: int = 90) -> List[Dict[str, Any]]:
    """The number nobody else in retail has.

    Of the shoppers who scanned this brand in-store, what share went on to tap a
    better-made alternative? That is quality-based rejection at the shelf — the
    thing a retailer cannot see in their own sales data, because it is the sale
    that never happened.
    """
    rows = _query(
        """
        WITH scanned AS (
            SELECT brand, session_hash
            FROM scan_events
            WHERE event = 'scan_verdict' AND brand IS NOT NULL
              AND occurred_hour > NOW() - INTERVAL '%s days'
            GROUP BY brand, session_hash
        ),
        rejected AS (
            SELECT brand, session_hash
            FROM scan_events
            WHERE event = 'alternative_tapped' AND brand IS NOT NULL
              AND occurred_hour > NOW() - INTERVAL '%s days'
            GROUP BY brand, session_hash
        )
        SELECT s.brand,
               COUNT(DISTINCT s.session_hash) AS devices,
               ROUND(100.0 * COUNT(DISTINCT r.session_hash)
                     / NULLIF(COUNT(DISTINCT s.session_hash), 0), 1) AS reject_pct
        FROM scanned s
        LEFT JOIN rejected r
               ON r.brand = s.brand AND r.session_hash = s.session_hash
        GROUP BY s.brand
        HAVING COUNT(DISTINCT s.session_hash) >= %%s
        ORDER BY reject_pct DESC
        """ % (int(days), int(days)),
        (K_ANONYMITY_FLOOR,),
    )
    return [{"brand": r[0], "devices": r[1],
             "reject_pct": float(r[2]) if r[2] is not None else None} for r in rows]


def fiber_trend(days: int = 90) -> List[Dict[str, Any]]:
    """Synthetic content drift over time, by month. Feeds the LIES franchise."""
    rows = _query(
        """
        SELECT to_char(date_trunc('month', occurred_hour), 'YYYY-MM') AS month,
               COUNT(DISTINCT session_hash)  AS devices,
               ROUND(AVG(score)::numeric, 2) AS avg_score
        FROM scan_events
        WHERE event = 'scan_verdict' AND score IS NOT NULL
          AND occurred_hour > NOW() - INTERVAL '%s days'
        GROUP BY 1
        HAVING COUNT(DISTINCT session_hash) >= %%s
        ORDER BY 1
        """ % int(days),
        (K_ANONYMITY_FLOOR,),
    )
    return [{"month": r[0], "devices": r[1],
             "avg_score": float(r[2]) if r[2] is not None else None} for r in rows]


def coverage() -> Dict[str, Any]:
    """Am I dense enough to sell yet? Internal readiness check, not a product."""
    rows = _query(
        """
        SELECT COUNT(*),
               COUNT(DISTINCT session_hash),
               COUNT(DISTINCT brand),
               COUNT(DISTINCT store_hint)
        FROM scan_events
        WHERE occurred_hour > NOW() - INTERVAL '30 days'
        """
    )
    if not rows:
        return {"events": 0, "devices": 0, "brands": 0, "stores": 0,
                "floor": K_ANONYMITY_FLOOR, "sellable": False}
    ev, dev, br, st = rows[0]
    return {
        "events": ev, "devices": dev, "brands": br, "stores": st,
        "floor": K_ANONYMITY_FLOOR,
        "sellable": bool(dev and dev >= K_ANONYMITY_FLOOR * 10),
    }


# ------------------------------------------------------------- v30 fair price
# What Filo shoppers actually saw pieces priced at, by kind of piece. Feeds the
# fair price (value.py) as a third, clearly labelled line next to what it costs
# to make and what shops list it for. Same floor as everything else here: a
# cell with fewer than K_ANONYMITY_FLOOR distinct devices does not exist.
#
# Prices are only ever bands (events.price_band), so a cell's "price" is the
# middle of its bands. Session hashes are read to count devices and never leave
# this function.
import time as _time

BAND_MID = {"0-20": 10, "20-40": 30, "40-60": 50, "60-80": 70, "80-120": 100,
            "120-200": 160, "200-400": 300, "400+": 500}
_CELL_CACHE = {"at": 0.0, "cells": {}}
CELL_TTL_S = 3600


def _quality_band(score):
    if score is None:
        return None
    s = float(score)
    s = s / 10 if s > 10 else s            # the app may send 0-100
    return "good" if s >= 6 else "mid" if s >= 4.5 else "low"


def _weighted_pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def price_cells(days: int = 180, rows: Optional[List[tuple]] = None) -> Dict[tuple, Dict[str, Any]]:
    """{(group, fibre, quality, market): summary}, only cells over the floor.

    rows can be passed in for tests: (session_hash, category, composition,
    country, score, price_band)."""
    import fabric
    import value
    if rows is None:
        rows = _query(
            """
            SELECT session_hash, category, composition, country, score, price_band
            FROM scan_events
            WHERE event = 'scan_verdict' AND price_band IS NOT NULL
              AND composition IS NOT NULL AND category IS NOT NULL
              AND occurred_hour > NOW() - INTERVAL '%s days'
            """ % int(days))
    cells: Dict[tuple, Dict[str, Any]] = {}
    for sess, category, composition, country, score, band in rows:
        if band not in BAND_MID:
            continue
        group = value.group_for(category)
        _, matched = fabric.quality_score(composition or "")
        if not group or not matched:
            continue
        fibre = max(matched, key=lambda m: m[1])[0]
        q = _quality_band(score)
        market = "CA" if (country or "").upper() == "CA" else "US"
        c = cells.setdefault((group, fibre, q, market), {"devices": set(), "prices": []})
        c["devices"].add(sess)
        c["prices"].append(BAND_MID[band])
    out = {}
    for key, c in cells.items():
        n = len(c["devices"])
        if n < K_ANONYMITY_FLOOR:
            continue
        prices = sorted(c["prices"])
        out[key] = {"devices": n, "scans": len(prices),
                    "p25": _weighted_pct(prices, 0.25), "median": _weighted_pct(prices, 0.5),
                    "p75": _weighted_pct(prices, 0.75),
                    "currency": "CAD" if key[3] == "CA" else "USD"}
    return out


def price_cell(group, fibre, quality, market):
    """One cell, from an hourly cache. None when it isn't over the floor."""
    now = _time.time()
    if now - _CELL_CACHE["at"] > CELL_TTL_S:
        try:
            _CELL_CACHE["cells"] = price_cells()
        except Exception as exc:          # noqa: BLE001
            log.warning("aggregates: price cells failed (%s)", exc)
            _CELL_CACHE["cells"] = {}
        _CELL_CACHE["at"] = now
    return _CELL_CACHE["cells"].get((group, fibre, quality, market))
