"""Filo's bank: the curated brand and product database the Stylist searches.

Stage 1 of the intelligence engine (see bank_data/ and the build kit). This file
only creates tables. Nothing here is read by /analyze or any shopper-facing
route yet, so scans behave exactly as before.

Rules carried into the tables:
- Every table is prefixed bank_ or stylist_, so nothing clashes with the v29
  barcode database (products, product_facts, ...).
- Products are judged one by one. A brand's reputation never passes to them.
- Every fact keeps its status, source type, scope and evidence IDs. "unknown"
  is a value, never a gap to fill with a guess.
- Raw stylist brief text is kept 90 days; the structured brief is kept.

Like the other modules, setup is best-effort: no DATABASE_URL, or a failed
connection, means the bank is off and scans still work.
"""
import logging
import re
import unicodedata

import events

log = logging.getLogger("filo.bank")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_brands (
    brand_id          TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    official_url      TEXT,
    url_verified      BOOLEAN NOT NULL DEFAULT FALSE,
    region            TEXT,
    gender_target     TEXT,
    categories        TEXT[] NOT NULL DEFAULT '{}',
    research_status   TEXT NOT NULL DEFAULT 'discovery_lead'
                      CHECK (research_status IN ('priority_screen','discovery_lead','resale_source','remove')),
    evidence_level    TEXT NOT NULL DEFAULT 'Not yet screened',
    style_led         BOOLEAN NOT NULL DEFAULT FALSE,
    approved          BOOLEAN NOT NULL DEFAULT FALSE,
    approved_on       DATE,
    approved_note     TEXT,
    unique_find       BOOLEAN NOT NULL DEFAULT FALSE,
    independent_label BOOLEAN NOT NULL DEFAULT FALSE,
    style_fit         SMALLINT CHECK (style_fit BETWEEN 0 AND 4),
    studio_note       TEXT,
    flags             JSONB NOT NULL DEFAULT '[]',
    research          JSONB NOT NULL DEFAULT '[]',
    discovery         JSONB NOT NULL DEFAULT '[]',
    directory         JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bank_brands_approved_idx ON bank_brands (approved);

CREATE TABLE IF NOT EXISTS bank_products (
    product_id        TEXT PRIMARY KEY,
    brand_id          TEXT NOT NULL REFERENCES bank_brands(brand_id),
    name              TEXT NOT NULL,
    style_code        TEXT,
    product_version   INTEGER NOT NULL DEFAULT 1,
    category          TEXT NOT NULL,
    subcategory       TEXT,
    gender_target     TEXT,
    product_url       TEXT NOT NULL,
    retailer          TEXT,
    is_resale_listing BOOLEAN NOT NULL DEFAULT FALSE,
    record            JSONB NOT NULL,          -- the full record, valid against product_schema.json
    decision          TEXT CHECK (decision IN ('pass','conditional','hold','insufficient_evidence')),
    decision_reason   TEXT,
    decided_by        TEXT,
    decided_on        DATE,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','in_review','live','hidden_stale','hidden_out_of_stock','retired')),
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bank_products_brand_idx ON bank_products (brand_id);
CREATE INDEX IF NOT EXISTS bank_products_category_idx ON bank_products (category, status);

CREATE TABLE IF NOT EXISTS bank_variants (
    variant_id    TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL REFERENCES bank_products(product_id) ON DELETE CASCADE,
    sku           TEXT,
    gtin          TEXT,
    colour        TEXT NOT NULL,
    colour_family TEXT,
    size_label    TEXT NOT NULL,
    size_system   TEXT,
    price         NUMERIC(10,2),               -- can be missing on a draft; never shown without it
    currency      CHAR(3),
    sale_price    NUMERIC(10,2),
    price_cad     NUMERIC(10,2),
    price_usd     NUMERIC(10,2),
    fx_rate_id    TEXT,
    availability  TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (availability IN ('in_stock','low_stock','out_of_stock','preorder','made_to_order','unknown')),
    ships_to      TEXT[] NOT NULL DEFAULT '{}',
    last_checked  TIMESTAMPTZ NOT NULL,
    CHECK (price IS NULL OR currency IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS bank_variants_product_idx ON bank_variants (product_id);

CREATE TABLE IF NOT EXISTS bank_size_measurements (
    product_id       TEXT NOT NULL REFERENCES bank_products(product_id) ON DELETE CASCADE,
    product_version  INTEGER NOT NULL,
    size_label       TEXT NOT NULL,
    size_system      TEXT NOT NULL,
    measurement_type TEXT NOT NULL DEFAULT 'garment' CHECK (measurement_type IN ('garment','body')),
    measurements     JSONB NOT NULL,              -- field -> Fact
    PRIMARY KEY (product_id, product_version, size_label, size_system, measurement_type)
);

CREATE TABLE IF NOT EXISTS bank_evidence (
    evidence_id   TEXT PRIMARY KEY,
    claim         TEXT NOT NULL,
    field         TEXT,
    subject_type  TEXT NOT NULL,
    subject_id    TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    source_kind   TEXT,
    source_type   TEXT NOT NULL CHECK (source_type IN ('verified','brand_reported','customer_reported','inferred')),
    quote         TEXT,
    sample_size   INTEGER,
    scope         TEXT NOT NULL,
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    checked_at    TIMESTAMPTZ NOT NULL,
    valid_until   DATE,
    page_hash     TEXT,
    status        TEXT NOT NULL DEFAULT 'current'
                  CHECK (status IN ('current','stale','source_failed','superseded','disputed')),
    superseded_by TEXT,
    retrieval     JSONB,
    CHECK (source_type <> 'customer_reported' OR sample_size IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS bank_evidence_subject_idx ON bank_evidence (subject_type, subject_id);

CREATE TABLE IF NOT EXISTS bank_facts (
    fact_id         BIGSERIAL PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES bank_products(product_id) ON DELETE CASCADE,
    product_version INTEGER NOT NULL,
    variant_id      TEXT NOT NULL DEFAULT '',     -- '' = applies to the style
    field           TEXT NOT NULL,                -- e.g. materials.composition
    value           JSONB,
    unit            TEXT,
    status          TEXT NOT NULL CHECK (status IN ('confirmed','from_photo','unknown','conflicts')),
    source_type     TEXT CHECK (source_type IN ('verified','brand_reported','customer_reported','inferred')),
    scope           TEXT,
    confidence      REAL,
    evidence_ids    TEXT[] NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, product_version, variant_id, field),
    CHECK (status = 'unknown' OR cardinality(evidence_ids) > 0),
    CHECK (status <> 'from_photo' OR source_type = 'inferred')
);

CREATE TABLE IF NOT EXISTS bank_fx_rates (
    fx_rate_id TEXT PRIMARY KEY,               -- e.g. GBP-CAD-2026-10-20
    base       CHAR(3) NOT NULL,
    quote      CHAR(3) NOT NULL,
    rate       NUMERIC(14,6) NOT NULL CHECK (rate > 0),
    as_of      DATE NOT NULL,
    source     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bank_concepts (
    concept_id TEXT PRIMARY KEY,
    dimension  TEXT NOT NULL,
    note       TEXT,
    extra      JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS bank_concept_phrases (
    concept_id TEXT NOT NULL REFERENCES bank_concepts(concept_id) ON DELETE CASCADE,
    language   TEXT NOT NULL,
    phrase     TEXT NOT NULL,
    PRIMARY KEY (concept_id, language, phrase)
);
CREATE INDEX IF NOT EXISTS bank_concept_phrases_phrase_idx ON bank_concept_phrases (lower(phrase));
CREATE TABLE IF NOT EXISTS bank_concept_relations (
    from_concept TEXT NOT NULL,
    rel_type     TEXT NOT NULL,
    to_value     JSONB NOT NULL,
    note         TEXT,
    PRIMARY KEY (from_concept, rel_type, to_value)
);

CREATE TABLE IF NOT EXISTS bank_standards (
    basic       TEXT NOT NULL,                 -- e.g. white_t_shirt
    slot        TEXT NOT NULL CHECK (slot IN ('best_overall','best_under','best_splurge','alternate')),
    rank        SMALLINT NOT NULL DEFAULT 1,
    product_id  TEXT NOT NULL REFERENCES bank_products(product_id),
    note        TEXT,
    price_line  NUMERIC(10,2),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (basic, slot, rank)
);

CREATE TABLE IF NOT EXISTS bank_gaps (
    signature        TEXT PRIMARY KEY,         -- normalized brief, so repeats count up
    brief_structured JSONB NOT NULL,
    count            INTEGER NOT NULL DEFAULT 1,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bank_sources_log (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT NOT NULL,
    product_id  TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    method      TEXT,
    status      TEXT NOT NULL,                 -- ok / failed / changed
    error       TEXT,
    page_hash   TEXT
);
CREATE INDEX IF NOT EXISTS bank_sources_log_url_idx ON bank_sources_log (url, fetched_at DESC);

CREATE TABLE IF NOT EXISTS stylist_hunts (
    hunt_id          TEXT PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id        TEXT NOT NULL,
    user_id          TEXT,
    brief_text       TEXT,                     -- scrubbed; cleared after 90 days
    brief_structured JSONB,
    mode             TEXT,
    language         TEXT,
    model            TEXT,
    nothing_passed   BOOLEAN,
    tool_calls       INTEGER,
    latency_ms       INTEGER
);
CREATE INDEX IF NOT EXISTS stylist_hunts_user_idx ON stylist_hunts (user_id);
CREATE INDEX IF NOT EXISTS stylist_hunts_created_idx ON stylist_hunts (created_at);

CREATE TABLE IF NOT EXISTS stylist_results (
    hunt_id             TEXT NOT NULL REFERENCES stylist_hunts(hunt_id) ON DELETE CASCADE,
    turn                INTEGER NOT NULL DEFAULT 1,
    position            SMALLINT NOT NULL,
    product_id          TEXT NOT NULL,
    slot                TEXT,
    is_pick             BOOLEAN NOT NULL DEFAULT FALSE,
    match_score         REAL,
    quality_score       REAL,
    evidence_confidence REAL,
    price               NUMERIC(10,2),
    matches             JSONB,
    PRIMARY KEY (hunt_id, turn, position)
);

CREATE TABLE IF NOT EXISTS stylist_actions (
    id         BIGSERIAL PRIMARY KEY,
    hunt_id    TEXT NOT NULL REFERENCES stylist_hunts(hunt_id) ON DELETE CASCADE,
    product_id TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('heart','tap','dismiss','compare','buy')),
    reason     TEXT CHECK (reason IS NULL OR reason IN ('price','fit','look','trust')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stylist_usage (
    device_id TEXT NOT NULL,
    day       DATE NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, day)
);

CREATE TABLE IF NOT EXISTS fit_preferences (
    owner_id   TEXT PRIMARY KEY,               -- user_id if signed in, else device_id
    prefs      JSONB NOT NULL,                 -- only what the shopper chose to save
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Stage 3: Studio
ALTER TABLE bank_brands ADD COLUMN IF NOT EXISTS studio_touched BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bank_products ADD COLUMN IF NOT EXISTS studio_edits JSONB NOT NULL DEFAULT '{}';
ALTER TABLE bank_concept_phrases ADD COLUMN IF NOT EXISTS removed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bank_concept_phrases ADD COLUMN IF NOT EXISTS added_in_studio BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS studio_log (
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    action  TEXT NOT NULL,
    subject TEXT,
    detail  JSONB
);
-- Stage 4: catalogue import, automatic sorting, search
ALTER TABLE bank_products ADD COLUMN IF NOT EXISTS auto_reasons JSONB NOT NULL DEFAULT '[]';
ALTER TABLE bank_products ADD COLUMN IF NOT EXISTS search_text TEXT;
CREATE TABLE IF NOT EXISTS bank_catalog_jobs (
    job_id      TEXT PRIMARY KEY,
    brand_id    TEXT NOT NULL REFERENCES bank_brands(brand_id),
    store_url   TEXT NOT NULL,
    method      TEXT,
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','stopped')),
    found       INTEGER NOT NULL DEFAULT 0,
    counts      JSONB NOT NULL DEFAULT '{}',
    error       TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS bank_embeddings (
    product_id      TEXT PRIMARY KEY REFERENCES bank_products(product_id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          REAL[] NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

TABLES = (
    "bank_brands", "bank_products", "bank_variants", "bank_size_measurements",
    "bank_evidence", "bank_facts", "bank_fx_rates", "bank_concepts",
    "bank_concept_phrases", "bank_concept_relations", "bank_standards",
    "bank_gaps", "bank_sources_log", "stylist_hunts", "stylist_results",
    "stylist_actions", "stylist_usage", "fit_preferences", "studio_log",
    "bank_catalog_jobs", "bank_embeddings",
)

BRIEF_TEXT_DAYS = 90


def slug(name: str) -> str:
    """A stable brand ID from a brand name: 'Babaà' -> 'babaa', '&Daughter' -> 'and-daughter'."""
    s = (name or "").replace("&", " and ")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "brand"


def init_schema() -> bool:
    """Create the bank tables if they don't exist. Never raises."""
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("bank: schema init failed (%s)", exc)
        return False
    finally:
        conn.close()


def purge_old_brief_text(days: int = BRIEF_TEXT_DAYS) -> int:
    """Clear raw brief text older than the retention window. Keeps the structured brief."""
    conn = events._conn()
    if conn is None:
        return 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE stylist_hunts SET brief_text = NULL "
                "WHERE brief_text IS NOT NULL AND created_at < now() - make_interval(days => %s)",
                (days,))
            return cur.rowcount
    except Exception as exc:            # noqa: BLE001
        log.warning("bank: brief purge failed (%s)", exc)
        return 0
    finally:
        conn.close()


def delete_for_user(user_id: str) -> int:
    """Account deletion: remove a shopper's hunts (results and actions cascade) and fit preferences."""
    if not user_id:
        return 0
    conn = events._conn()
    if conn is None:
        return 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM stylist_hunts WHERE user_id = %s", (user_id,))
            n = cur.rowcount
            cur.execute("DELETE FROM fit_preferences WHERE owner_id = %s", (user_id,))
            return n
    except Exception as exc:            # noqa: BLE001
        log.warning("bank: user delete failed (%s)", exc)
        return 0
    finally:
        conn.close()


def counts() -> dict:
    """Row counts per bank table, for Studio and tests. Empty dict without a database."""
    conn = events._conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            out = {}
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                out[t] = cur.fetchone()[0]
            return out
    except Exception as exc:            # noqa: BLE001
        log.warning("bank: counts failed (%s)", exc)
        return {}
    finally:
        conn.close()
