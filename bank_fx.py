"""Daily exchange rates for the bank, from the Bank of Canada (free, official, no key).

Prices are stored in the currency the shop shows. CAD and USD versions are
added using a dated rate, and the rate's ID is stored with the price, so every
conversion can be traced. No rate means no conversion: never a guess.

Bank of Canada Valet API: https://www.bankofcanada.ca/valet/docs
"""
import json
import logging
from datetime import date
from typing import Dict, Optional, Tuple

import bank_fetch
import events

log = logging.getLogger("filo.bank_fx")

CURRENCIES = ("USD", "EUR", "GBP", "AUD", "JPY", "CHF", "SEK", "NOK", "DKK", "NZD")
VALET = "https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1"

# In-memory copy, also used when there's no database: base -> (rate to CAD, as_of, fx_rate_id)
_rates: Dict[str, Tuple[float, str, str]] = {}


def _rate_id(base: str, as_of: str) -> str:
    return f"{base}-CAD-{as_of}"


def parse_valet(payload: str) -> Dict[str, Tuple[float, str]]:
    """{'USD': (1.3812, '2026-10-19'), ...} from a Valet response."""
    data = json.loads(payload)
    out = {}
    for obs in data.get("observations", []):
        day = obs.get("d")
        for cur in CURRENCIES:
            cell = obs.get(f"FX{cur}CAD")
            if day and isinstance(cell, dict) and cell.get("v"):
                try:
                    out[cur] = (float(cell["v"]), day)
                except ValueError:
                    continue
    return out


def update() -> Dict[str, Tuple[float, str]]:
    """Fetch today's rates and store them. Returns what was stored; {} if the source failed."""
    series = ",".join(f"FX{c}CAD" for c in CURRENCIES)
    got = bank_fetch.fetch(VALET.format(series=series))
    if not got.ok:
        log.warning("bank_fx: Bank of Canada rates unavailable (%s); keeping the last rates", got.error)
        return {}
    try:
        rates = parse_valet(got.text)
    except ValueError:
        log.warning("bank_fx: couldn't read the Bank of Canada response")
        return {}
    for cur, (rate, day) in rates.items():
        _rates[cur] = (rate, day, _rate_id(cur, day))
    conn = events._conn()
    if conn is not None:
        try:
            with conn, conn.cursor() as cur:
                for c, (rate, day) in rates.items():
                    cur.execute("""INSERT INTO bank_fx_rates (fx_rate_id, base, quote, rate, as_of, source)
                                   VALUES (%s, %s, 'CAD', %s, %s, 'Bank of Canada')
                                   ON CONFLICT (fx_rate_id) DO NOTHING""",
                                (_rate_id(c, day), c, rate, day))
        except Exception as exc:            # noqa: BLE001
            log.warning("bank_fx: couldn't store rates (%s)", exc)
        finally:
            conn.close()
    return rates


def _load_latest(base: str) -> Optional[Tuple[float, str, str]]:
    if base in _rates:
        return _rates[base]
    conn = events._conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT rate, as_of, fx_rate_id FROM bank_fx_rates
                           WHERE base = %s AND quote = 'CAD' ORDER BY as_of DESC LIMIT 1""", (base,))
            row = cur.fetchone()
        if row:
            _rates[base] = (float(row[0]), str(row[1]), row[2])
            return _rates[base]
    except Exception:                       # noqa: BLE001
        return None
    finally:
        conn.close()
    return None


def convert(amount: Optional[float], currency: Optional[str]) -> Dict[str, Optional[object]]:
    """{'price_cad', 'price_usd', 'fx_rate_id', 'fx_as_of'} for an amount. None where no rate is known."""
    out = {"price_cad": None, "price_usd": None, "fx_rate_id": None, "fx_as_of": None}
    if amount is None or not currency:
        return out
    currency = currency.upper()
    if currency == "CAD":
        cad, rid, as_of = amount, None, None
    else:
        r = _load_latest(currency)
        if not r:
            return out
        cad, as_of, rid = amount * r[0], r[1], r[2]
    out["price_cad"] = round(cad, 2)
    if currency == "USD":
        out["price_usd"] = round(amount, 2)
    else:
        u = _load_latest("USD")
        if u:
            out["price_usd"] = round(cad / u[0], 2)
            if rid is None:
                rid, as_of = u[2], u[1]
    out["fx_rate_id"], out["fx_as_of"] = rid, as_of
    return out


def set_rate_for_tests(base: str, rate: float, as_of: str = None):
    as_of = as_of or date.today().isoformat()
    _rates[base] = (rate, as_of, _rate_id(base, as_of))


def clear_cache():
    _rates.clear()
