# Filo v32: the bank, Stage 1 (20 Oct 2026)

Stage 1 of the fashion intelligence engine: the database the Stylist will search. Nothing shoppers see has changed. No new pages or endpoints; scans work exactly as before.

## What's new

| File | What it does |
| --- | --- |
| `bank.py` | Creates the 18 bank and stylist tables when the server starts, the same safe way the other tables are made. All start with `bank_` or `stylist_`, so nothing clashes with the barcode database (`products`). Also clears raw chat text after 90 days and removes a shopper's chats when they delete their account (wired up in Stage 5). |
| `bank_validate.py` | Checks product and evidence records against the kit's schemas, plus rules a schema can't express: no "confirmed" fact without evidence, nothing about material or origin from a photo, no brand-wide claim confirming a product fact. |
| `bank_seed.py` | Loads the 544 starter brands (23 approved) and the vocabulary. Safe to run every startup: it keeps anything changed in Studio and never duplicates phrases. |
| `bank_data/` | The kit's data: brands, approved first wave, vocabulary and knowledge graph, schemas, quality rubric, ranking rules, stylist copy. |
| `main.py` | Four lines at startup to set up the bank. If the database is down, scans still work. |
| `requirements.txt` | Adds `jsonschema` for the validator. |
| `test_filo.py` | 42 new checks, half of them against a real Postgres. |

The database itself also refuses bad data: a confirmed fact with no evidence, a photo fact that isn't marked inferred, shopper evidence with no sample size, or a price with no currency.

## Changes from the kit

- Price is now optional on a draft product (a page with no price can still be saved), and "has a current price" is part of the rule for showing a product.
- Vibe search (vector embeddings) waits for Phase 1, as agreed. Phase 0 matches on tags and synonyms, then the stylist chooses from a shortlist.
- AI calls will follow the backend's existing direct approach, with no extra Anthropic library.

## Tests

619 checks pass, with and without a database. One existing failure is unrelated: "a big budget can" (leather) depends on the day, because the brand list rotates daily and today the luxury leather makers fall outside the top six. Worth a small separate fix.

## Housekeeping to consider

`Main · PY` and `main 2.py` are identical old copies of `main.py`. Nothing uses them; they can be deleted.

## Next: Stage 2

Product importer (manual link first), evidence, FX rates, refresh jobs, tested on the five products in the kit's `test_products.md`.
