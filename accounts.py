"""
Filo accounts — who has signed up, and nothing more.

WHAT THIS STORES
One row per person who signs in with Apple: a random account ID, Apple's stable
user ID (`sub`), the email Apple hands over (often a private relay address), an
optional display name, an optional age band, and a default department. That is
the whole record.

WHAT THIS DELIBERATELY DOES NOT DO
Scans are not linked to accounts. /analyze and /events never receive an account
ID, so "who signed up" and "what was scanned" live in separate tables with no
key between them. Joining them would be a product decision with privacy-label
consequences, not a line of code, and it is not made here.

HOW SIGN-IN WORKS
1. The app runs Sign in with Apple and gets an identity token (a JWT signed by
   Apple).
2. POST /accounts with that token. We verify the signature against Apple's
   published keys, check it was issued for Filo's bundle ID, and upsert the row.
3. We hand back a Filo session token (HMAC-signed, 180 days). The app keeps it in
   the Keychain and sends it as `Authorization: Bearer ...` to /accounts/me.

RAILWAY VARIABLES
  APPLE_BUNDLE_ID   required — the app's bundle ID, e.g. com.brooklynvenier.filo
  ACCOUNT_SECRET    recommended — any long random string; signs session tokens.
                    Falls back to a value derived from ADMIN_TOKEN if unset.
"""
import os
import hmac
import json
import time
import uuid
import base64
import hashlib
import logging
import urllib.request
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

import events

log = logging.getLogger("filo.accounts")

APPLE_BUNDLE_ID = os.environ.get("APPLE_BUNDLE_ID", "")
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"

_secret_env = os.environ.get("ACCOUNT_SECRET", "")
_admin = os.environ.get("ADMIN_TOKEN", "")
SECRET = (_secret_env or hashlib.sha256(("filo-accounts:" + _admin).encode()).hexdigest()).encode()

SESSION_DAYS = 180

AGE_BANDS = {"18-24", "25-34", "35-44", "45+"}
DEPARTMENTS = {"Women's", "Men's", "Everyone"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id          UUID        PRIMARY KEY,
    apple_sub           TEXT        UNIQUE NOT NULL,
    email               TEXT,
    display_name        TEXT,
    age_band            TEXT,
    department_default  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS accounts_created_idx ON accounts (created_at);
"""


# ------------------------------------------------------------------ request models

class SignUpRequest(BaseModel):
    identity_token: str = Field(min_length=20, max_length=8000)
    display_name: Optional[str] = Field(default=None, max_length=80)
    age_band: Optional[str] = None
    department: Optional[str] = None


class AccountUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=80)
    age_band: Optional[str] = None
    department: Optional[str] = None


def _clean_band(v):
    return v if v in AGE_BANDS else None


def _clean_dept(v):
    return v if v in DEPARTMENTS else None


# ------------------------------------------------------------------ storage

def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("accounts: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


def _run(sql, params=(), fetch="none"):
    conn = events._conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return cur.rowcount
    finally:
        conn.close()


_COLUMNS = ("account_id", "email", "display_name", "age_band",
            "department_default", "created_at", "last_seen_at")


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(zip(_COLUMNS, row))
    d["account_id"] = str(d["account_id"])
    for k in ("created_at", "last_seen_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def upsert(apple_sub: str, email: Optional[str], req: SignUpRequest) -> Dict[str, Any]:
    """Create on first sign-in; on later sign-ins refresh last_seen and fill in
    anything that was missing. Apple only sends the email on the FIRST sign-in,
    so an existing email is never overwritten with nothing."""
    row = _run(
        """
        INSERT INTO accounts (account_id, apple_sub, email, display_name,
                              age_band, department_default)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (apple_sub) DO UPDATE SET
            email              = COALESCE(accounts.email, EXCLUDED.email),
            display_name       = COALESCE(EXCLUDED.display_name, accounts.display_name),
            age_band           = COALESCE(EXCLUDED.age_band, accounts.age_band),
            department_default = COALESCE(EXCLUDED.department_default, accounts.department_default),
            last_seen_at       = NOW()
        RETURNING account_id, email, display_name, age_band,
                  department_default, created_at, last_seen_at
        """,
        (str(uuid.uuid4()), apple_sub, email,
         (req.display_name or "").strip() or None,
         _clean_band(req.age_band), _clean_dept(req.department)),
        fetch="one",
    )
    return _row_to_dict(row)


def get(account_id: str) -> Optional[Dict[str, Any]]:
    row = _run(
        """
        UPDATE accounts SET last_seen_at = NOW() WHERE account_id = %s
        RETURNING account_id, email, display_name, age_band,
                  department_default, created_at, last_seen_at
        """,
        (account_id,), fetch="one")
    return _row_to_dict(row) if row else None


def update(account_id: str, u: AccountUpdate) -> Optional[Dict[str, Any]]:
    row = _run(
        """
        UPDATE accounts SET
            display_name       = COALESCE(%s, display_name),
            age_band           = COALESCE(%s, age_band),
            department_default = COALESCE(%s, department_default),
            last_seen_at       = NOW()
        WHERE account_id = %s
        RETURNING account_id, email, display_name, age_band,
                  department_default, created_at, last_seen_at
        """,
        ((u.display_name or "").strip() or None, _clean_band(u.age_band),
         _clean_dept(u.department), account_id),
        fetch="one")
    return _row_to_dict(row) if row else None


def delete(account_id: str) -> bool:
    """Hard delete. Apple requires account deletion to actually delete."""
    return (_run("DELETE FROM accounts WHERE account_id = %s", (account_id,)) or 0) > 0


def summary() -> Dict[str, Any]:
    total = _run("SELECT COUNT(*) FROM accounts", fetch="one")[0]
    per_day = _run(
        """SELECT DATE(created_at) AS d, COUNT(*) FROM accounts
           GROUP BY d ORDER BY d DESC LIMIT 60""", fetch="all")
    bands = _run("SELECT COALESCE(age_band, 'not given'), COUNT(*) FROM accounts GROUP BY 1",
                 fetch="all")
    depts = _run("SELECT COALESCE(department_default, 'not given'), COUNT(*) FROM accounts GROUP BY 1",
                 fetch="all")
    return {
        "total": total,
        "signups_per_day": [{"date": d.isoformat(), "count": c} for d, c in per_day],
        "age_bands": {b: c for b, c in bands},
        "departments": {b: c for b, c in depts},
    }


def list_all(limit: int = 500) -> List[Dict[str, Any]]:
    rows = _run(
        """SELECT account_id, email, display_name, age_band, department_default,
                  created_at, last_seen_at
           FROM accounts ORDER BY created_at DESC LIMIT %s""",
        (min(max(limit, 1), 5000),), fetch="all")
    return [_row_to_dict(r) for r in rows]


# ------------------------------------------------------------------ Apple token

_keys_cache = {"at": 0.0, "keys": []}


def _apple_keys():
    if time.time() - _keys_cache["at"] < 6 * 3600 and _keys_cache["keys"]:
        return _keys_cache["keys"]
    with urllib.request.urlopen(APPLE_KEYS_URL, timeout=6) as resp:
        keys = json.loads(resp.read().decode()).get("keys", [])
    _keys_cache.update(at=time.time(), keys=keys)
    return keys


def verify_apple_token(token: str) -> Dict[str, Any]:
    """Return the verified claims, or raise ValueError."""
    if not APPLE_BUNDLE_ID:
        raise ValueError("APPLE_BUNDLE_ID is not configured on the server")
    try:
        import jwt                                   # PyJWT
        from jwt.algorithms import RSAAlgorithm
    except ImportError as exc:                       # pragma: no cover
        raise ValueError("PyJWT is not installed") from exc

    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except Exception as exc:                         # noqa: BLE001
        raise ValueError("malformed identity token") from exc

    jwk = next((k for k in _apple_keys() if k.get("kid") == kid), None)
    if jwk is None:
        _keys_cache["at"] = 0                        # Apple rotated keys; refetch once
        jwk = next((k for k in _apple_keys() if k.get("kid") == kid), None)
    if jwk is None:
        raise ValueError("unknown signing key")

    key = RSAAlgorithm.from_jwk(json.dumps(jwk))
    try:
        claims = jwt.decode(token, key=key, algorithms=["RS256"],
                            audience=APPLE_BUNDLE_ID, issuer=APPLE_ISSUER)
    except Exception as exc:                         # noqa: BLE001
        raise ValueError(f"identity token rejected ({exc.__class__.__name__})") from exc
    if not claims.get("sub"):
        raise ValueError("identity token has no subject")
    return claims


# ------------------------------------------------------------------ Filo session

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def issue_session(account_id: str) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{account_id}.{exp}"
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def check_session(token: Optional[str]) -> Optional[str]:
    """account_id if the token is genuine and unexpired, else None."""
    if not token:
        return None
    if token.lower().startswith("bearer "):
        token = token[7:]
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    account_id, exp, sig = parts
    expected = _b64(hmac.new(SECRET, f"{account_id}.{exp}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        if int(exp) < time.time():
            return None
        uuid.UUID(account_id)
    except ValueError:
        return None
    return account_id
