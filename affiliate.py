"""
Filo affiliate wrapping — the last step, deliberately.

THE ORDER OF OPERATIONS IS THE WHOLE ETHIC.

Alternatives are found, scored, filtered and SORTED before this module is ever
called. By the time `decorate()` runs, the list is frozen: this file may change
where a link POINTS, never which links are in the list or what order they are in.
That is what makes "no paid rankings" a true statement rather than a slogan, and
it is why wrapping lives in its own module called at the very end rather than
inside catalog.py where it would be one refactor away from touching the sort.

Two things that would quietly break the promise, neither of which happens here:

  * Filtering candidates by whether they can be monetised. A search that only
    considers merchants in the affiliate network is a paid ranking wearing a
    disguise. `decorate()` never drops anything.
  * Sorting by commission rate. This module does not receive commission rates
    and does not look them up.

INERT UNTIL CONFIGURED. With no AFFILIATE_NETWORK set, wrap() returns the URL
unchanged and `affiliate` comes back False on every row. That is the state on
day one, and shipping it that way is intentional: the SubID plumbing needs to be
live from the first scan because conversion history cannot be backfilled.

THE SUBID CARRIES THE VERDICT, AND NOTHING ELSE.
Networks report earnings broken down by SubID, so encoding the scanned score and
the alternative's score answers the question worth more than the early revenue:
which verdicts actually convert. It contains no session, no device, no user, and
nothing that could identify a person — see PRIVACY.md.

Config:
    AFFILIATE_NETWORK   skimlinks | sovrn | template   (unset = off)
    SKIMLINKS_ID        publisher id, for skimlinks
    SOVRN_KEY           api key, for sovrn
    AFFILIATE_TEMPLATE  for `template`: any URL containing {url} and optionally
                        {subid}; {url} is percent-encoded before substitution
"""
import os
import logging
import urllib.parse

log = logging.getLogger("filo.affiliate")

NETWORK = os.environ.get("AFFILIATE_NETWORK", "").strip().lower()
SKIMLINKS_ID = os.environ.get("SKIMLINKS_ID", "").strip()
SOVRN_KEY = os.environ.get("SOVRN_KEY", "").strip()
AFFILIATE_TEMPLATE = os.environ.get("AFFILIATE_TEMPLATE", "").strip()


def enabled():
    if NETWORK == "skimlinks":
        return bool(SKIMLINKS_ID)
    if NETWORK == "sovrn":
        return bool(SOVRN_KEY)
    if NETWORK == "template":
        return "{url}" in AFFILIATE_TEMPLATE
    return False


def subid(scanned_score=None, alt_score=None, category=None):
    """A compact, non-identifying tag the network reports earnings against.

    Example: `s54-a80-sweater` — scanned 5.4, alternative 8.0, sweater.
    Scores are multiplied by ten so the tag stays alphanumeric; several networks
    reject punctuation in SubIDs.
    """
    parts = []
    if scanned_score is not None:
        parts.append(f"s{int(round(scanned_score * 10))}")
    if alt_score is not None:
        parts.append(f"a{int(round(alt_score * 10))}")
    if category:
        clean = "".join(ch for ch in str(category).lower() if ch.isalnum())[:16]
        if clean:
            parts.append(clean)
    return "-".join(parts) or "filo"


def wrap(url, tag="filo"):
    """Return the tracking URL, or the original if wrapping is off or fails."""
    if not url or not enabled():
        return url
    try:
        quoted = urllib.parse.quote(url, safe="")
        if NETWORK == "skimlinks":
            return (f"https://go.skimresources.com/?id={urllib.parse.quote(SKIMLINKS_ID)}"
                    f"&xs=1&url={quoted}&sref={urllib.parse.quote(tag)}")
        if NETWORK == "sovrn":
            return (f"https://redirect.viglink.com/?key={urllib.parse.quote(SOVRN_KEY)}"
                    f"&u={quoted}&subId={urllib.parse.quote(tag)}")
        if NETWORK == "template":
            return AFFILIATE_TEMPLATE.replace("{url}", quoted).replace("{subid}", urllib.parse.quote(tag))
    except Exception as exc:            # noqa: BLE001
        log.info("affiliate: wrap failed (%s)", exc)
    return url


def decorate(alternatives, scanned_score=None, category=None):
    """Rewrite each link in place. Order and membership are never touched.

    Returns the same list object, same items, same sequence — only `url` changes,
    plus an `affiliate` flag so the app knows whether to show the disclosure.
    """
    for alt in alternatives or []:
        original = alt.get("url")
        if not original:
            alt["affiliate"] = False
            continue
        tag = subid(scanned_score=scanned_score, alt_score=alt.get("score"),
                    category=category)
        wrapped = wrap(original, tag)
        alt["url"] = wrapped
        alt["affiliate"] = wrapped != original
    return alternatives
