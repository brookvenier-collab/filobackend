# Stage 2: the product importer

Branch: `intelligence-engine` (not main). Nothing here is visible in the app.

## What it does
Give it a product link. It reads the page politely, pulls out the facts with the
exact words they came from, and saves a draft for Brooklyn to review. Nothing
ever goes live from an import.

## New files
- `bank_fetch.py`: polite fetching. Follows robots.txt, waits 3 seconds between
  hits to the same site, 15s timeout, 3 MB cap, no logins. Never crashes.
- `bank_extract.py`: reads structured product data (JSON-LD, Open Graph,
  Shopify's public product data) and the visible page text.
- `bank_facts.py`: pulls out fibre content, fabric weight, where it's made,
  factory, care, cut words, sizing advice, stretch. Optional AI tagger fills
  gaps only when its quote is really on the page, and can never turn a city or
  state ("Manchester", "TN") into a country.
- `bank_fx.py`: daily Bank of Canada exchange rates. Every converted price keeps
  the dated rate it used. No rate means no conversion.
- `bank_import.py`: builds the product record, checks it against the schema,
  suggests a decision (pass / conditional / hold / not enough evidence), saves it,
  and refreshes it every 6 hours.
- `test_fixtures/bank/`: stand-in pages for the five test products (made up for
  tests, not copies of the real pages).

## Rules it follows
- Every fact points to evidence. Unknown stays unknown, never guessed.
- A price only counts if the currency is written (a bare "$" isn't enough).
  Sale and regular prices are kept apart.
- Refresh: same page means only prices and stock update. If fibre, origin,
  weight, lining or cut words change, it becomes a new version and goes back to
  review. Old evidence is kept.
- If the shop's page fails, a live product is hidden (never shown stale) and
  retried after 1, 6, then 24 hours. Sold out everywhere means hidden until back.

## Admin routes (need the admin key, hidden from the API docs)
- `POST /internal/bank/import` with `{"url": "..."}`
- `GET /internal/bank/products/{product_id}`
- `POST /internal/bank/refresh/{product_id}`
- `POST /internal/bank/fx/update`

## Results on the five test products
| Product | Suggested | Notes |
|---|---|---|
| Colorful Standard oversized tee | pass | 100% cotton, 180g as stated, made in Portugal |
| Peregrine waffle jumper | conditional | £99.75 = C$181.55, sold out, "Manchester" left as unknown country |
| Nudie Clean Eileen jeans | conditional | 14.9 oz, rigid, "TN" left unknown |
| Community Clothing work trousers | not enough evidence | shop page has no fibre content; suggests the brand's own page |
| Finisterre Firecrest jacket | hold | synthetic, below the quality floor; factory named |

## Tests
`python3 test_filo.py` covers all of the above without a database, and with
`FILO_TEST_DATABASE_URL` set, also saving, refresh, new versions, failures and
recovery on real Postgres. Everything passes except the older date-based
"a big budget can" check, which was already failing before this work.

## Next: Stage 3
Studio (password from the Railway variable `STUDIO_PASSWORD`), and importing the
five real pages for Brooklyn to review.
