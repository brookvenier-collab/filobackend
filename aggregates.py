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
