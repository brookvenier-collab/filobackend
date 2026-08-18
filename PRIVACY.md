# Filo — Data & Privacy Architecture

*The technical record of what Filo collects, what it deliberately cannot collect, and why. This is the document to hand a privacy lawyer, and the document the App Store privacy labels must match.*

Last updated: 18 August 2026.

---

## The principle

Filo's commercial plan includes selling **aggregate** shelf intelligence to brands and retailers. That is only defensible — commercially, legally, and morally — if individual shoppers are not merely *protected by policy* but are *impossible to reconstruct from the schema*.

So the guarantees below are structural. Where a privacy promise could be enforced either by policy or by architecture, we chose architecture. The test applied throughout: **if a future employee were told to build a per-person shopping history, could they?** The answer must be no without a schema migration and a new data collection — which is a decision someone has to make on purpose, not a query someone can write on a Tuesday.

---

## What is collected

One row per meaningful action, written to `scan_events`:

| Field | Example | Notes |
|---|---|---|
| `event` | `scan_verdict` | One of five allow-listed values; anything else is dropped at ingest |
| `session_hash` | `13b93eb4…` | HMAC of an ephemeral session ID under a **daily-rotating** salt |
| `occurred_hour` | `2026-08-18 03:00Z` | **Truncated to the hour at write time** — the precise moment never reaches disk |
| `brand` | `aritzia` | Lowercased. Read from the tag by OCR, **confirmed by the shopper** |
| `store_hint` | `Pacific Centre` | Optional, volunteered |
| `category` | `top` | Optional |
| `country` | `CA` | Two-letter region from device `Locale`. No permission prompt, no GPS |
| `composition` | `100% Polyester` | The fiber string |
| `score` / `verdict` | `3.4` / `Skip it` | Filo's own output |
| `price_band` | `60-80` | **Banded, never exact** — an exact price plus a store plus an hour is re-identifying |
| `app_version` | `0.1` | |

Events currently emitted: `scan_verdict`, `alternative_viewed`, `alternative_tapped`, `verdict_shared`, `item_saved`.

## What is *not* collected — and cannot be

- **No user or account ID.** There is no column for one. Filo's account system is local-only and never reaches this table.
- **No persistent device identifier.** No IDFA, no IDFV, no fingerprint. The app never requests App Tracking Transparency permission because it does not track.
- **No IP addresses.** Not stored, not logged, not hashed.
- **No GPS or precise location.** `CoreLocation` is not linked. Location granularity is a country code.
- **No cross-day linkage.** See below — this is the important one.
- **No third-party sharing of row-level data.** Ever, under any contract. See "The exit door."

## The session model

The app generates a random UUID held **only in memory**. It is never written to `UserDefaults`, the keychain, or a file. It regenerates:

- on every app launch, and
- after 30 minutes of inactivity.

So a "session" is one shopping trip. Quitting the app destroys it irrecoverably.

The server never stores even that. It stores `HMAC(daily_salt, session_id)`, where `daily_salt = SHA256(EVENTS_SALT + UTC date)`. Because the salt changes at midnight UTC, **two scans by the same person on two different days produce unrelated hashes.** Filo cannot join them. Neither can a buyer, an acquirer, or a subpoena.

This is the specific technical reason the cross-retailer "shopping journey" product — brands seeing that one person visited Lululemon, then Aritzia, then Sephora — is not on the roadmap. It is not a policy we could quietly reverse; the data to build it is not retained in a linkable form.

## The exit door

`aggregates.py` is the only module permitted to return scan data, and every function in it enforces `K_ANONYMITY_FLOOR` (default **30 distinct devices**). A cohort below the floor is not rounded, not noised, not caveated — **it is not returned at all**.

Verified behaviour: with 40 devices per brand and the floor set to 50, brand and rejection queries return zero rows while the category query (120 devices) still returns.

No function in that module returns a row, a session, an identifier, or a timestamp finer than an hour. The return type of every public function is a summary.

Raising the floor is cheap. Lowering it requires a written reason in this file.

## Retention

`events.purge_older_than(days=400)` deletes raw events. Aggregates are what persist. 400 days is chosen to allow year-over-year comparison with a margin; shorten it if no product needs the tail.

## Shopper control

Account → **The Quality Record** → "Contribute my scans." Default on, disclosed in plain language on the same screen:

> *Your scans build the record of what brands are actually making — the fabric, the score, the brand name. No account, no location, no device ID, and nothing that ties two scans to the same person. We report on brands. We never report on you.*

When off, `FiloAnalytics.log` returns immediately and no request is made.

## Configuration

| Variable | Purpose | Required |
|---|---|---|
| `DATABASE_URL` | Postgres. **If absent, analytics silently no-ops** — a scan must never fail because telemetry did | For collection |
| `EVENTS_SALT` | Secret feeding the daily rotating salt. If unset, a random per-process salt is used and device counts reset on deploy | Yes, in production |
| `K_ANONYMITY_FLOOR` | Suppression threshold, default 30 | No |
| `ADMIN_TOKEN` | Guards `/internal/shelf/*`. Without it those routes return 404 | Yes, in production |

## Open items before selling anything

1. **Counsel review** against PIPEDA and BC PIPA, plus CPRA once there are US users and GDPR before any EU launch. Aggregate non-identifiable analytics generally sits outside ATT's definition of tracking — but that line is exactly where the revenue is, so get it confirmed in writing.
2. **Publish a real Privacy Policy and Terms of Service.** The Account screen currently has rows for both that don't resolve anywhere.
3. **Declare App Store privacy labels to match this document exactly.** Mismatched labels are a common Guideline 5.1.2 rejection.
4. **Set `EVENTS_SALT` and `ADMIN_TOKEN`** on Railway before the first real user.
5. **Decide the salt-retention question:** we currently derive salts from one long-lived secret, so historical salts are recomputable. If the threat model requires that yesterday's links be *unrecoverable even by us*, move to storing rotating salts in a table and deleting them on expiry.
