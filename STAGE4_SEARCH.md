# Stage 4: Search, ranking and whole catalogues

Branch: `intelligence-engine` (not main). Nothing here is visible in the app yet.

## What's new

### 1. Bring in a whole catalogue (`bank_catalog.py`)
In Studio, open an approved brand and press **Bring in catalogue**. Filo reads every product
the brand sells and keeps the women's and unisex clothing. Each one gets the same record a
single import makes, with every fact quoted from the brand's own words.
- **Shopify stores** (most of Filo's brands): reads the store's public product list, 250 at
  a time, plus the store's currency. Fast and polite.
- **Other stores:** reads the site's product sitemap, then each page, one every few seconds.
- Follows robots.txt. One catalogue at a time, up to 600 products per run. It can be
  stopped from Studio.
- Skips men's pieces, pieces that aren't clearly women's (unless Brooklyn ticks "this brand
  only makes women's clothes"), non-clothing and other labels the store sells.
- **Never overrides a product Brooklyn has already decided.**

### 2. The rules sort them (`bank_rules.py`)
- **Live:** brand approved, fabric stated on the page, passes Filo's rules (quality floor,
  under 50% synthetic), price found, in stock. Marked "Approved by the rules".
- **Live but hidden:** the same, but sold out everywhere.
- **For Brooklyn to review:** 35-49% synthetic, stock not stated, garment type unclear, or
  the fabric came from Claude's reading rather than the page.
- **Held back:** fails a rule, like a 100% polyester jacket.
- **Waiting:** the page doesn't say what it's made of.
- About 1 in 20 of the live ones is marked **Spot check**, so Brooklyn can see the rules are
  doing their job. Confirming or taking one down clears the mark.
- When a rules-approved product's page changes, the rules look at it again. Brooklyn's own
  decisions still go back to her.
- Set `BANK_AUTO_APPROVE=0` in Railway to turn the automatic part off. Everything then waits
  for Brooklyn.

### 3. Reading a request (`stylist_brief.py`)
This is a fast, free reader with no AI. It uses Filo's vocabulary (editable in Studio) and
fixes typos ("jeens for my hipps"). It handles budgets, fibre percentages, exclusions ("no
wool", "no animal materials"), where it's made, sizes, counts, vintage and "like Khaite".
Words it can't place go to Studio's backlog. Stage 5 lets Claude write the brief for
conversations, with this as the fallback.

### 4. Search and ranking (`stylist_search.py`), following `ranking_rules.json`
- **Hard filters remove, never rank lower:**
  - **Garment and fabric:** category, material (100% means 100), excluded materials
    (shell, lining and trims, and unknown fabric is removed when anything is excluded).
  - **Origin and ethics:** origin, counted only when confirmed for the product. Supply
    chain proof must be about the product, not the brand.
  - **The piece shown:** colour (black is not navy), size in stock, budget (the sale price
    counts), in stock, and checked within 30 days.
  - **Evidence:** anything with evidence of the opposite (proof it's see-through) is removed.
- **Three separate scores:**
  - **Match** (0-100): musts, nice-to-haves, style, budget fit and sizes in stock.
  - **Quality:** Filo's existing score, unchanged.
  - **Evidence** (High, Medium or Low): how direct and current the proof is.
- **Ranking and spread:**
  - Ranked by Match. Within 5 points, better evidence wins.
  - At most 2 per brand in 5, and 3 in 15.
  - At least two price points.
  - An independent label within 5 points gets its own slot.
- **Slots:** Best overall (the Stylist's pick), Best value, Best independent label, and at
  most one **Trade-off**. The trade-off is a piece that matches the cut but has no proof yet
  for something like "not see-through", and it says exactly what's missing. It never
  covers a hard filter.
- **Standard mode:** "I just need a good white tee" uses Brooklyn's Standards first.
- **Nothing passes:** it says what removed them ("2 over budget, lowest C$40"), never
  relaxes a rule quietly, and logs the gap to the Backlog.
- **Checks before anything is shown:**
  - Exactly one pick.
  - No card over budget.
  - Every claim has a quote behind it.
  - No em dashes.

### 5. Meaning search (`bank_embed.py`)
Vague requests ("quiet luxury", "a bit French", "workwear") are matched by meaning.
- **Without a key** (the default): style-word matching using the product's description and
  the brand's style notes. It's free and works today.
- **With a key:** add `VOYAGE_API_KEY` in Railway. Voyage is the embeddings company
  Anthropic recommends, and it gives every product a meaning fingerprint. The default
  model is `voyage-4-lite`, which can be changed with `VOYAGE_MODEL`. Fingerprints update
  after every catalogue run and every 6 hours.

Either way, style is only 15% of Match and never overrides a rule.

### 6. Studio
- **Test hunt** tab: type a request and see what Filo understood, what it removed and why,
  and each card with its scores and quotes.
- **Brands:** Bring in catalogue, with live progress and a Stop button.
- **Products:** "Approved by the rules" and "Spot checks" filters, plus the rules' reasons
  shown on each product.

### Admin route for Stage 5
`POST /internal/stylist/search` with `{"text": "..."}` or `{"brief": {...}}` (admin key).

## Tests
`python3 test_filo.py`: 841 checks pass, with and without a database. The search tests use a
bank of clearly labelled fake products (`FAKE TEST PRODUCT`). They cover every hard filter,
the three scores, the spread rules, every slot, the trade-off rule and the no-match case.
The catalogue test uses a made-up Shopify store (`test_fixtures/catalog/`).

## Worth knowing
- Filo's floor is 70 and plain 100% cotton scores exactly 70, so "close to the floor" is not
  treated as borderline. Otherwise every cotton tee would wait for review.
- Some blends fail the floor. For example, Kotn's trousers at 50% wool, 34% polyester,
  15% viscose and 1% elastane score 68, so those three from the first 50 list will be
  held back on import.
