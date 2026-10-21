# Stage 3: Filo Studio

Branch: `intelligence-engine` (not main). Nothing here is visible in the app.

## What it is
Brooklyn's private page at `/studio`. It's where brands get approved,
products get imported and reviewed, and the Filo Standards get picked.

## Signing in
- The password is the Railway variable `STUDIO_PASSWORD`. Brooklyn types it into
  Railway herself. It's not in the code, the repo, a test or a log.
- No `STUDIO_PASSWORD` means Studio is off.
- 5 wrong tries from one address locks that address for 15 minutes. 25 wrong tries
  from anywhere in 15 minutes locks Studio for everyone for 15 minutes.
- A sign-in lasts 12 hours. Changing the password signs everyone out.
- The sign-in cookie can't be read by scripts, only works on https and only on
  `/studio`. Changes need a header other sites can't send. The page can't be
  framed or cached.

## Sections
- **Products:** paste a link to import. Review every fact with its quote, source
  and date. Save a decision, or confirm and go live. Fix cut words (words Brooklyn
  adds count as her read of the photos, never as confirmed). Mark unique find or
  independent label. Check the page now, take down, retire.
- **Brands:** filter by approved, research status, evidence level, category,
  style-led. Approve, reject, note, unique find, independent label, style fit 0-4.
  Research and discovery notes beside the controls.
- **Standards:** the ten basics. Best overall, best under (with a CAD price), best
  splurge, plus ordered alternates. Only products Brooklyn confirmed can be picked.
- **Vocabulary:** search concepts and phrases, add or remove phrases, add concepts.
  Shopper words that matched nothing.
- **Backlog:** no-result briefs, rewordings, saved but not tapped, why shoppers
  passed, weakest categories and languages, brands asked for but missing.
- **Health:** hard-constraint violations, evidence accuracy, recovery after no
  results, follow-ups per hunt, freshness, saves and clicks, bank size.
- **Sources:** failing product pages (try now or retire) and imports that couldn't
  read the page.

Backlog and Health show "nothing yet" until the Stylist runs (Stage 5).

## Rules that can't be skipped
A product can only go live when the brand is approved, the decision is pass or
conditional, there's a price with its currency, the composition is confirmed, and
it passes Filo's existing rules (quality floor, under 50% synthetic). Brooklyn
saying "pass" doesn't override Filo's rules. Deciding on an old version is refused.
Stock not stated: it can go live, but won't show in a hunt until stock is known.

## Changes to earlier stages
- New columns: `bank_brands.studio_touched`, `bank_products.studio_edits`,
  `bank_concept_phrases.removed` and `added_in_studio`. New table `studio_log`
  (every Studio change).
- A restart no longer undoes a brand Brooklyn rejected, or brings back a phrase
  she removed.
- Refresh compares the page with what the page said before, not with Brooklyn's
  cut-word fixes, and her marks carry over to new versions.
- A live product whose page doesn't state stock stays live on refresh instead of
  being hidden as sold out.

## For Stage 5 to fill in
`stylist_hunts.brief_structured` keys Studio reads: `category`,
`unmatched_phrases` (list), `brands_asked` (list), `follow_ups` (number).
`stylist_results.matches.violations` (list) for hard-constraint violations.

## Tests
`python3 test_filo.py`. The Studio web checks need `httpx`, which is only for
tests (`pip install httpx`) and isn't needed on Railway. Everything passes except
the older date-based "a big budget can" check.

## Not done here
- Seeing a product appear in a test hunt: that needs retrieval, which is Stage 4.
- Importing the five real pages: the build workspace can't reach shop websites,
  so Brooklyn does it in Studio once it's running.
