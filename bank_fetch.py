"""Polite page fetching for the bank importer.

- Follows each site's robots.txt (cached for a day per site).
- At most one request every few seconds to the same site.
- Normal timeout and a size cap. No logins, no cookies, no paywalls.
- Returns a FetchResult; never raises.

Tests swap FETCHER for a function that serves saved fixture pages.
"""
import hashlib
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("filo.bank_fetch")

USER_AGENT = "FiloBot/1.0 (+https://filoquality.com)"
TIMEOUT = 15
MAX_BYTES = 3_000_000
MIN_GAP_SECONDS = 3.0
ROBOTS_TTL = 24 * 3600


@dataclass
class FetchResult:
    ok: bool
    url: str
    status: Optional[int] = None
    text: str = ""
    content_type: str = ""
    error: Optional[str] = None
    page_hash: Optional[str] = None


def page_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


_lock = threading.Lock()
_last_hit: Dict[str, float] = {}
_robots: Dict[str, Tuple[float, Optional[urllib.robotparser.RobotFileParser]]] = {}


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _wait_turn(host: str):
    with _lock:
        now = time.monotonic()
        gap = MIN_GAP_SECONDS - (now - _last_hit.get(host, 0.0))
        _last_hit[host] = now + max(gap, 0.0)
    if gap > 0:
        time.sleep(gap)


def _raw_get(url: str) -> FetchResult:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-CA,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return FetchResult(False, url, r.status, error="page too large")
            charset = r.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, "replace")
            return FetchResult(True, r.geturl(), r.status, text,
                               r.headers.get("Content-Type", ""), None, page_hash(text))
    except urllib.error.HTTPError as e:
        return FetchResult(False, url, e.code, error=f"HTTP {e.code}")
    except Exception as e:                  # noqa: BLE001
        return FetchResult(False, url, None, error=str(e)[:200])


def _allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    cached = _robots.get(host)
    if cached is None or time.time() - cached[0] > ROBOTS_TTL:
        rp = urllib.robotparser.RobotFileParser()
        got = _raw_get(f"{parts.scheme}://{host}/robots.txt")
        if got.ok:
            rp.parse(got.text.splitlines())
        elif got.status in (401, 403):
            rp = None                       # robots.txt itself is locked: treat as no
        else:
            rp.parse([])                    # no robots.txt: allowed
        _robots[host] = (time.time(), rp)
        cached = _robots[host]
    rp = cached[1]
    return False if rp is None else rp.can_fetch(USER_AGENT, url)


def http_fetch(url: str) -> FetchResult:
    if not url.lower().startswith(("http://", "https://")):
        return FetchResult(False, url, error="not a web address")
    if not _allowed(url):
        return FetchResult(False, url, error="blocked by the site's robots.txt")
    _wait_turn(_host(url))
    return _raw_get(url)


# Swapped out in tests.
FETCHER: Callable[[str], FetchResult] = http_fetch


def fetch(url: str) -> FetchResult:
    try:
        return FETCHER(url)
    except Exception as e:                  # noqa: BLE001
        return FetchResult(False, url, error=str(e)[:200])
