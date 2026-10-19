# Filo v30: the fair price (19 Oct 2026)

Quality and price in one answer. Branch `feat/v30-fair-price`, commit after `7551831`, delivered as `filo-backend-v30.zip`. 439 checks pass. The one failure ("a big budget can", v18) was already failing before this work and is unrelated.

## The equation

**Before:** Scan → Quality score → Better made options. Price was a separate shop comparison (v28).

**Now:**
**Scan + Category + Price → Quality score → Fair price → Value verdict → Better made options ranked by value**

**Fair price = (fabric + making + trims) × a fair markup**

| Part | How it's worked out | Where the numbers come from |
|---|---|---|
| Fabric | garment weight × fibre price for its grade, turned into cloth | Ecobalyse (French government), USDA, Australian Wool Innovation, Egyptian Cotton Association, European flax observatory, cashmere and silk FOB prices |
| Making | sewing minutes × how carefully it's made × wage where it's made | Ecobalyse minutes, factory time studies, CBI (Netherlands government) costing, Insee, Eurostat, BLS, national minimum wages |
| Leather | sq ft of skin × grade of skin + hours of work | Tannery and supplier prices, Tanner Leatherstein teardowns, Hermès and Chanel hours |
| Markup | from a lean shop (H&M, Inditex: 2.15 to 2.4× landed) to a typical full-price shop (CBI: 4 to 8× factory price, Filo uses 6) | Company filings, CBI |
| Duty | Canada 18% (free from the EU and least developed countries); US 16.5% plus 2026 add-ons | CBSA, US HTS, USTR via EY |

Every number in `pricebank.json` says where it came from and whether it is **sourced**, **derived** (arithmetic on sourced numbers, shown) or a **judgement** (direction from a source, size set by Filo).

## What the shopper sees

| Scan (Canada, CAD) | Fair price | Verdict |
|---|---|---|
| 100% cotton t-shirt, no price | $11 to $25 | No price yet |
| 100% cotton t-shirt, $30 | $11 to $25 | Worth it |
| Supima 220gsm tee, made in Portugal, $45 | $45 to $120 | Worth it |
| 100% polyester tee, $180 | $10 to $25 | Overpriced |
| 100% wool sweater, no price | $50 to $115 | No price yet |
| 100% cashmere sweater, $70 | $230 to $520 | Low price, check the grade |
| 100% cashmere sweater, $25 | $230 to $520 | Hard to make at this price |
| Grade A cashmere, made in Italy, $600 | $450 to $1,200 | Worth it |
| 100% wool coat, made in Italy, no price | $320 to $840 | No price yet |
| Faux leather jacket, $400 | $65 to $150 | Overpriced |
| Lambskin bag, Italy, calfskin lining, make unseen, $2,600 | $730 to $1,950 | Good piece, high price |
| Same bag, turned edges and saddle stitching seen, $2,600 | $1,550 to $4,100 | Worth it |
| Lambskin bag, China, raw edges seen, $200 | $240 to $540 | You get what you pay for |

**The verdicts:** A steal · Worth it · Fair for what it is · You get what you pay for · High for what it is · Good piece, high price · Overpriced · Low price, check the grade · Hard to make at this price.

**No price entered:** the scan now says "A fair price for a 100% wool sweater made like this is about $50 to $115. Add the price to see if it's worth it." This works in the live 1.1 app today (it rides on `price_prompt`).

## The Row vs the Etsy copy

Both are real lambskin, so the score can't tell them apart from the tag alone. The fair price can, from what is seen:

- **Grade of skin** (nappa, full grain, aniline; named top tanneries; "genuine leather" and "corrected grain" read as lower).
- **Leather lining** counts as more leather.
- **Make signs** from the listing or a close photo: turned edges, painted edges, saddle stitching (good); raw edges, thin plated hardware (faults). These are new construction tokens. The seam photo now reads leather goods too.
- **Hours:** standard factory 2, careful 3, exceptional 6. Hermès (15 to 24) and Chanel (15 to 20) are the ceiling, not the default.

Result: a $1,700 Italian lambskin bag with a calfskin lining sits at the top of a fair price ("Worth it, at the top"). At $1,850 with the make unseen it's "Good piece, high price". If turned edges and saddle stitching are seen, the fair range moves up and it's "Worth it". A raw-edged $140 to $200 copy is "You get what you pay for".

## Rules that keep it honest

- **Quality never reads price.** The score is untouched; same piece, same score at any price.
- **The brand never moves the fair price.** Tested: the same wool coat under a luxury name and a no-name label gets the same range.
- **What isn't known widens, never guesses.** No "Made in" spans Bangladesh to China. No grade spans the grades it could be. The app gets an `unknowns` list with a tip for each.
- **"High" needs to be well past the top**, judged against the most generous reading of what's unknown. An ordinary $30 cotton tee is not called a rip-off (the v28.1 lesson).
- **A low price rules grades out, it never proves quality.** Cheap cashmere gets "check the grade", not "a steal".
- **Words:** plain, no dashes, never names the brand or the shop.

## How it gets more accurate over time

1. **What shoppers saw.** `scan_events` already stores a price band for every scan with a price. Once a kind of piece (category × fibre × quality × country) has at least 30 different devices, the verdict adds "Filo shoppers saw pieces like this priced around $X". Nothing new is collected; the floor is the same as Shelf Intelligence.
2. **Calibration.** `GET /internal/shelf/fair-price` (admin) puts Filo's fair range next to what shoppers saw, per kind of piece. Where they keep disagreeing, the bank needs a look.
3. **The bank is one file.** Updating a price or a wage is a JSON edit, no code. FX rates should be refreshed monthly.

## What the app needs (next update)

The backend works with the current app: `price_note` now leads with the fair price verdict, and `price_prompt` carries the fair range when there's no price. For the full design:

1. **Show `fair_price`** on the result page, under the score: `label` with `colour` (good/ok/poor), `line`, and the range `fair_low` to `fair_high` in `currency`.
2. **"How we worked it out"** drop-down from `built_from` (fabric, grade, where it's made, make) and `based_on`.
3. **"What we don't know"** from `unknowns`, each with its `tip` (check "Made in", scan a seam). `could_be_line` says how wide the range could go.
4. **A "Bag" category** in the category picker, so bags get priced.
5. **Send `tag_text`** (the whole care label) and `country` (CA/US) on every scan. "Made in" lives on the tag and narrows the range most.
6. **Each better made option** now has `fair_price` (level, label, colour, range). Show the label on the card. Within the same fit, better value comes first.
7. **Seam photo prompt for bags:** "Get close to an edge or the stitching."

## Admin endpoints

- `GET /internal/fair-price?composition=&category=&price=&country=&tag_text=` returns the full working for any scan, to check by hand.
- `GET /internal/shelf/fair-price` returns Filo vs shoppers, per kind of piece.

## Fact check

A separate pass checked 15 load-bearing figures against their sources (`research/verification.md`). Fixed after it:

- **Silk** is now $59.50/kg ($57 to $61).
- **Cashmere grade ranges** are widened to match the source's grade table.
- **US add-ons** are 12.5% for China and Turkey, now cited to the right EY page.
- **Linen** dates are corrected.
- **Woven conversion** arithmetic is corrected.
- **Mansur Gavriel and Senreve** teardown sources are added.

## Weakest numbers (say so if asked)

- **Leather jackets:** sq ft and hours come from forum-grade sources.
- **Top-tannery leather:** no factory price list is public. $6 to $14/sq ft is a judgement between teardowns and small-maker prices.
- **Make tier minutes** (+25%, +60%): direction is sourced, size is a judgement.
- **Canada and Japan sewing wages:** none found yet. US and French figures stand in.
- **Hoodie, cardigan, blazer and jacket weights** rest on low-confidence sources.

Research files with every URL: `research/` in the backend zip.
