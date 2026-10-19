# Filo fair-price model: garment weights, fabric norms, construction, durability

Researched 2026-10-19. Every figure below comes from a page or file fetched in this session; the URL is on each row. Text is paraphrased. Conversions marked "(calc)" are mine, done from the source's own numbers.

Confidence key:
- **H**: government or official data, peer-reviewed paper, or primary standard.
- **M**: established trade or brand guide that is internally consistent.
- **L**: SEO or shipping blog, forum, or a page with inconsistencies or no cited basis. Use only as a sanity range.

---

## 1. Garment mass and fabric consumption

### 1a. Ecobalyse (French government, ADEME/MTES) product defaults

Source file: https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/public/data/textile/products.json. This is the live repo data behind the official French textile environmental-cost calculator.

| Ecobalyse product | Fabric surface mass (g/m²) | Yarn size (Nm) | Fabric process | Making complexity | Default price (abstract €) | Default repair cost | Days of wear | Wears per wash | Default wash cycles | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|
| Chemise (shirt) | 200 | 40 | weaving | low | 15 | 10 | 40 | 2 | 20 | H |
| Jean | 250 | 40 | weaving | medium | 20 | 14 | 70 | 3 | 23 | H |
| Jupe / Robe (skirt/dress) | 200 | 40 | weaving | low | 15 | 19 | 70 | 3 | 23 | H |
| Manteau / Veste (coat/jacket) | 450 | 30 | weaving | high | 40 | 31 | 100 | 20 | 5 | H |
| Pantalon / Short | 250 | 40 | weaving | medium | 20 | 14 | 70 | 3 | 23 | H |
| Pull (sweater) | 250 | 35 | knitting-mix | low | 20 | 15 | 85 | 5 | 17 | H |
| T-shirt / Polo | 200 | 40 | knitting-mix | low | 10 | 10 | 45 | 1 | 45 | H |

Making waste (pcrWaste) is 0.20 for most products, 0.22 for jeans and 0.15 for t-shirts (same file, H). Ecobalyse treats the price field as an abstract value with no currency attached; see the comment in https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/src/Data/Textile/Economics.elm.

**Making-complexity labour minutes.** Source: https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/src/Data/Textile/MakingComplexity.elm (H). Ecobalyse uses these minutes to model the energy and impact of garment making.

| Complexity | Minutes |
|---|---|
| very-low | 5 |
| low (shirt, skirt/dress, sweater, tee) | 15 |
| medium (jeans, trousers) | 30 |
| high (coat/jacket) | 60 |
| very-high | 120 |

**Ecobalyse reference example masses.** Source: https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/public/data/textile/examples.json (H for "what Ecobalyse uses", M as real-world averages). The "Majorant par défaut" examples are the regulatory worst-case defaults.

| Item | Mass (g) | Example price used | Confidence |
|---|---|---|---|
| T-shirt | 150 | 10 (ultra fast) / 20 (fast) / 30 (traditional or ethical) | H |
| Shirt (chemise) | 250 | 15 | H |
| Skirt (jupe) | 300 | 15 | H |
| Jeans | 450 | 20 | H |
| Trousers | 450 | 20 | H |
| Sweater (pull) | 550 | 15 (ultra fast) / 30 (fast) / 70 (traditional) / 95 (ethical, wool, FR) | H |
| Coat (manteau) | 950 | 40 | H |

### 1b. Finished garment weights from trade and shipping guides

| Item | Value | Unit | Source URL | Confidence |
|---|---|---|---|---|
| T-shirt, Bella+Canvas 3001 (142 g/m²), sizes XS to XL | 99 / 111 / 125 / 136 / 150 | g | https://www.printful.com/blog/t-shirt-weight-guide | M |
| T-shirt, basic cotton | 113–170 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-does-a-shirt-weigh/ | L |
| Dress shirt | 170–230 | g | same parcelpath shirt page | L |
| Hoodie/sweatshirt | 340–450 | g | https://www.printful.com/blog/how-much-does-a-hoodie-weigh | M |
| Hoodie/sweatshirt | 450–680 | g | parcelpath shirt page | L |
| Cashmere sweater (fine gauge) | 250–400 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-does-a-sweater-weigh/ | L |
| Merino sweater | 450–600 | g | same | L (page contradicts itself) |
| Chunky-knit sweater | 567–794 | g | same | L |
| Jeans, lightweight / mid / heavy denim | 360–600 / 650–800 / 900–1500 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-do-jeans-weigh/ | L |
| Jeans examples: 10 oz skinny / 12 oz stretch / 14 oz 501 / 21 oz raw | 450 / 650 / 720 / 950 | g | same | L |
| Dress, casual | 170–340 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-does-a-dress-weigh/ | L |
| Dress, business or lined sheath | 340–454 | g | same | L |
| Lining effect on dress weight | +25–40 | % | same | L |
| Blazer / suit jacket | 340–570 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-does-a-jacket-weigh/ | L |
| Wool peacoat | 910–1360 | g | https://parcelpath.com/shipping-terms-glossary/glossary-weights-and-measurements/how-much-does-a-coat-weigh/ | L |
| Down parka | 680–1130 | g | same | L |
| Leather jacket | 910–1810 (lambskin lighter, motorcycle heavier) | g | same, plus the parcelpath jacket page | L |
| Down fill, lightweight jacket | ~85 (3 oz) | g fill | https://www.switchbacktravel.com/down-fill-insulation-explained | M |
| Down fill, midweight jacket | ~140–170 (5–6 oz, calc) | g fill | same | M |
| Down fill, spec examples | 113 and 227 | g fill | https://www.kathmandu.com.au/blogs/article/fill-power-explained-what-those-numbers-mean-for-your-jacket | M |

### 1c. Fabric consumption per garment (metres)

| Item | Value | Unit / width | Source URL | Confidence |
|---|---|---|---|---|
| Formal shirt, long sleeve (factory, ratio-cut S–XL) | 1.6 | m @ 60 in | https://www.onlineclothingstudy.com/2017/06/how-many-meters-of-cloth-do-i-need-for.html | M |
| Formal shirt, long sleeve | 2.10 | m @ 44 in | same | M |
| Formal shirt, half sleeve | 1.4 / 1.9 | m @ 60 / 44 in | same | M |
| Men's trousers | 1.3–1.5 | m @ 58–60 in | https://www.onlineclothingstudy.com/2018/11/how-many-meters-of-cloth-needed-for.html | M |
| Men's trousers | 2.3 | m @ 36 in | same | M |
| T-shirt (knit) | ~1.25 | m @ 60 in | https://sewguide.com/how-much-fabric-do-you-need/ | L–M (home-sewing) |
| Straight skirt | ~1.15 | m @ 60 in | same | L–M |
| Pleated or gathered long skirt | ~3 | m | same | L–M |
| Long dress | ~3 | m @ 45 in | same | L–M |
| Men's jacket / blazer | ~2 (2.75 if longer, @45 in) | m | same | L–M |
| Women's coat, Gwendoline pattern | 2.8–3.9 main, 1.95–2.65 lining, 1.3–1.5 interfacing | m @ 140–150 cm | https://vikisews.com/vykrojki/outerwear/gwendoline-coat/ | M (one pattern) |
| Straight coat, DP Studio pattern | 2.8 (size 44) to 3.2 (size 58) main; 1.8–2.0 lining | m @ 145 cm | https://dpstudio-fashion.com/en/coats/1374-1333-le-1800-straight-cut-coat-with-a-tailored-collar.html | M (one pattern) |
| Leather jacket | 40–50 | sq ft of hide | https://www.thefedoralounge.com/threads/any-advice-on-sourcing-your-own-hide-and-doing-mtm-jackets.109283/post-2958869 | L (forum, agrees across posters) |
| Extra fabric for large plaid matching | ≥0.5 m, or one pattern repeat per major piece | m | https://minervapatterns.com/blog/print-and-pattern-matching-plaids-stripes-and-repeats | M |
| Extra fabric for checks | at least 25% | % | https://sewguide.com/how-much-fabric-do-you-need/ | L |

Home-sewing figures run higher than factory figures because factories nest many sizes into one marker (onlineclothingstudy says this but does not give a percentage).

---

## 2. Fabric weight norms that signal quality

| Item | Value | Unit | Source URL | Confidence |
|---|---|---|---|---|
| Tee lightweight | 120–150 | g/m² | https://www.printful.com/blog/t-shirt-weight-guide | M |
| Tee midweight | 150–180 | g/m² | same | M |
| Tee heavyweight | 180–254 | g/m² | same | M |
| oz/yd² to g/m² factor | 33.91 | × | same; also denimhunters | H (arithmetic) |
| Hoodie light / medium / heavy | ≤280 / 280–350 / ≥350 | g/m² | https://www.printful.com/blog/how-much-does-a-hoodie-weigh | M |
| Denim light / mid / heavy | <12 / 12–16 / >16 | oz/yd² | https://denimhunters.com/denim-wiki/denim-explained/denim-weight/ | M |
| Denim same tiers in metric | <407 / 407–542 / >542 | g/m² | same | M |
| Denim tiers (second source agrees, heavy goes up to 32 oz) | <12 / 12–16 / >16 | oz | https://www.opumo.com/magazine/how-much-do-jeans-weigh/ | M |
| Wool coat cloth: lightweight / medium / med-heavy / heavy | <300 / 300–350 / 350–400 / >400 | g/m² | https://bonnegueule.fr/en/blogs/medias/limportance-de-lepaisseur-dans-le-choix-dun-manteau-en-laine-cdl85 | M |
| Wool coat cloth: practical upper limit | ~500–550 | g/m² | same | M |
| Wool coat cloth: linear-metre conversion | g per linear m ÷ 1.5 (150 cm width) ≈ g/m²; figures ≥600 are probably per linear metre | rule | same | M |
| Knit gauge (Pringle) | 3–12gg for AW/chunky; 12gg classic; 12gg+ fine (SS) | needles/inch | https://pringlescotland.com/pages/all-about-gauge | M |
| Knit gauge (Permanent Style) | Scottish cashmere crew = 21gg; fine-gauge makers 24–30gg; chunky shawl cardigan ~5gg | gauge | https://www.permanentstyle.com/2021/11/the-guide-to-types-of-knitwear-gauge-end-and-ply | M |
| Knit ply | crewneck 1–2 ply; 4 ply heavy jumper; 8/12/16 ply chunky cardigans | ply | same | M |
| Yarn count notation | e.g. 2/28 = two ends of Nm 28 (28 m weighs 1 g) | — | same | M |
| Shirting yarn counts | 50s to 200s; higher means finer and pricier. The "s" number is hanks of 840 yd per lb, not threads per inch | — | https://propercloth.com/reference/thread-count-ply | M |
| Two-ply shirting | generally better than single-ply (qualitative only) | — | same | M |
| Silk momme | 1 mm ≈ 4.34 g/m²; <20 light, 20–28 mid, >28 heavy | momme | https://mulberryparksilks.com/blogs/mps-blog/what-is-momme-in-silk-fabric | M |
| Silk by weave | charmeuse 12–30; crepe de chine 12–16; chiffon 6–8; organza 4–6 | momme | same | M |
| Leather jacket thickness, men's | 1.4–1.6 (heavier model 1.6–1.8) | mm | https://foxcreekleather.com/blog/leather-thickness | M (one maker) |
| Leather jacket thickness, women's | 1.2–1.4 | mm | same | M |
| Down fill power tiers | 400–500 entry; 550–650 everyday; 700–800 higher quality; 850–900 premium | FP | https://www.kathmandu.com.au/blogs/article/fill-power-explained-what-those-numbers-mean-for-your-jacket | M |
| Down fill power | <500 casual, gives up much of down's benefit; 650 budget; 800 mainstream premium; 900+ premium | FP | https://www.switchbacktravel.com/down-fill-insulation-explained | M |
| Poplin / denim / merino "good" ranges | poplin 115–135; denim 200–320; merino 160–190 | g/m² | https://lifetips.alibaba.com/laundry-secrets/do-you-know-how-identify-well-made-clothes | L (no cited studies; denim range conflicts with the oz tiers above) |

Two caveats for the model:
- Bonne Gueule warns that a heavier coat with synthetic content can hold about the same amount of wool as a lighter 100% wool coat (350 g/m² pure wool compared with 430 g/m² at 80/20).
- Permanent Style notes that a dense lambswool can outlast a light, open cashmere.

Weight alone is therefore not quality. Use it together with fibre content.

---

## 3. Construction quality markers

### 3a. Stitch density

| Item | Value | Unit | Source URL | Confidence |
|---|---|---|---|---|
| Seam strength by SPI, lockstitch 301, warp, 270 g/m² cotton (ASTM D1683) | 275 @8; 455 @10; 389 @12; 318 @14 | N | https://epubl.ktu.edu/object/elaba:124950521/124950521.pdf (Islam et al., Textile & Leather Review 2022) | H |
| Seam strength by SPI, chainstitch 401, warp | 293 @8; 389 @10; 389 @12; 365 @14 | N | same | H |
| Optimum SPI for seam strength (wovens) | ~10; strength falls at 14 from puckering and jamming | SPI | same | H |
| Sweatshirt seams (Coats spec: 514 overlock, 406 coverseam, 304 bartack) | 12–14 per 3 cm (≈10–12 SPI, calc) | stitches | https://www.coats.com/en/?p=23007 | H (thread maker spec) |
| Thread consumption, sweatshirt | ~190 | m | same | H |
| Edge-stitched seam finish | 12–14 | SPI | https://publications.mgcafe.uky.edu/sites/publications.ca.uky.edu/files/fcs2304.pdf (Univ. Kentucky Extension, 2014) | H |
| Hairline seam on sheers | 14–16 | SPI | same | H |
| Higher SPI trade-off | more thread and sewing time, so lower output and higher labour cost. At 5,000 spm: 8 SPI ≈ 17.4 yd/min, 14 SPI ≈ 9.9 yd/min (about 43% slower) | — | https://fibre2fashion.com/industry-article/7121/stitches-per-inch | M |
| Estimated seam strength formula, 10 SPI with 4 lb thread | 301 ≈ 60 lb; 401 ≈ 68 lb (SPI × thread strength × 1.5 or 1.7) | lbf | same | M |
| "Good" SPI | wovens 10–12, knits 14–16; below 8 on wovens is poor | SPI | https://lifetips.alibaba.com/laundry-secrets/do-you-know-how-identify-well-made-clothes | L |

ASTM D6193 is the stitch and seam classification standard (stitch classes 301, 401, 504, 514 and so on). I could not fetch its text (store.astm.org is paywalled). It is a classification standard and does not set "budget vs quality" SPI thresholds.

### 3b. Seam types, allowances, finishing

| Item | Value | Source URL | Confidence |
|---|---|---|---|
| French seam | strong and durable, encloses raw edges; for light wovens and sheers. Sewn at 3/8 in, trimmed to 1/8–1/4 in, then a second line at ~1/4 in | https://publications.mgcafe.uky.edu/sites/publications.ca.uky.edu/files/fcs2304.pdf | H |
| Flat-felled seam | for wovens, sportswear, menswear and heavy-duty items; needs no extra finish | same | H |
| Serged (overlock) seam | 3-thread for stretch knits; 4-thread for wovens or stable knits | same | H |
| Bound seam finish | medium to heavy wovens, often on unlined coats and jackets | same | H |
| Default seam allowance (home-sewing convention) | 5/8 in (≈16 mm) | same | H |
| Flat-felled vs overlock in jeans | flat-felled is generally stronger and encloses edges, but needs extra folding and stitching so it is slower and costlier; overlock only slows fraying. Overlock is also period-correct on some vintage 501s and on many Japanese brands, so it is not by itself a budget signal | https://www.heddels.com/?p=24393 | M |
| Seam allowance thresholds | ≥10 mm good; <7 mm said to lose 41% strength | https://lifetips.alibaba.com/laundry-secrets/do-you-know-how-identify-well-made-clothes | L (uncited) |
| Lined garments | seam allowances may not need a finish when lining covers them | UKY PDF above | H |

### 3c. Tailoring: fused, half canvas, full canvas, bespoke

| Item | Value | Unit | Source URL | Confidence |
|---|---|---|---|---|
| Hand pad-stitching a lapel | ~1 | hour per lapel | https://www.gentlemansgazette.com/fused-half-full-canvas-suit-construction/ | M |
| Hand pad-stitching a lapel | 4–5 | hours per lapel | https://westwoodhart.com/blogs/westwood-hart/fused-vs-canvassed-suits-suit-construction-guide-menswear-tailoring | M (conflicts with the row above) |
| Machine pad-stitching a lapel | ≤1 | minute per lapel | both pages above | M |
| Lapels per 8-hour day: machine vs hand | 600–1000 vs 6–10 | lapels | Gentleman's Gazette | M |
| Pad-stitch machine cost | ~100,000 | USD | Gentleman's Gazette | M |
| Half canvas described as | ~2/3 canvas with 1/3 fused | — | Gentleman's Gazette | M |
| Half and full canvas cost when machine pad-stitched | similar; price gaps often reflect positioning rather than production cost | — | Westwood Hart | M |
| Retail price tiers | half canvas ~650; full canvas ~1,500; full canvas with handwork 3,000+ | USD | https://www.mrporter.com/en-au/journal/fashion/men-suit-cost-tailor-half-fully-canvassed-price-24263946 | M |
| Operations in a factory jacket | ~180 | operations | same | M |
| Bespoke suit labour | 60–80 | man-hours | https://dege-skinner.co.uk/bespoke-tailoring/ | M–H (Savile Row house) |
| Bespoke coat (jacket) labour | ~40 | hours | same | M–H |
| Bespoke jacket (forum consensus) | ~60 for the coat; 60–80 for the suit | hours | https://thelondonlounge.net/forum/viewtopic.php?p=53568 | L |
| Fused durability | bonds can loosen and bubble over time; no timeframe published | — | Gentleman's Gazette; Westwood Hart | M (qualitative) |

### 3d. Factory labour minutes (SMV/SAM)

| Item | Value | Unit | Source URL | Confidence |
|---|---|---|---|---|
| Jeans, large Pakistan exporter | 15.14 | SMV min | https://lahoreschoolofeconomics.edu.pk/assets/uploads/lje/Volume21/09_Chaudhry_and_Faran.pdf (Lahore J. Econ. 2016) | H |
| Jeans, medium / small producer | 22.07 / 32.55 | SMV min | same | H |
| Jeans stitching labour cost, large exporter | 13.61 | PKR per garment (≈0.90 PKR/min, calc) | same | H |
| Monthly wages, 2016 | Bangladesh 83; Pakistan 116; India 130; Indonesia 170; China 220–270 | USD/month | same | H (dated) |
| Full-sleeve women's top with facing and plackets | 16.06 (16.34 after line change) | SMV min | https://www2.kuet.ac.bd/JES/images/files/v102/02_JES_225_21-10-2019.pdf (J. Eng. Sci. 2019) | H |
| Same garment: side seam / sleeve join / body hem / button attach | 1.10 / 0.90 / 0.89 / 0.45 | min per operation | same | H |
| Basic shirt (observed) | 16.5 | SAM min | https://www.degruyterbrill.com/document/doi/10.2478/aut-2019-0045/html (AUTEX 2020) | H |
| Men's / women's polo (observed) | 32.0 / 37.1 | SAM min | same | H (one Ethiopian factory, high) |
| V-neck shirt (observed) | 5.4 | SAM min | same | H |
| Ecobalyse modelled making time | tee/shirt/sweater/skirt 15; jeans/trousers 30; coat 60 | min | Ecobalyse MakingComplexity.elm | H (modelling convention) |

---

## 4. Durability evidence

| Finding | Value | Source URL | Confidence |
|---|---|---|---|
| Leeds/WRAP: 50 garments (17 jeans, 33 tees) priced £2–£140; price "cannot be used to predict" durability | — | https://eprints.whiterose.ac.uk/id/eprint/225641/1/1-s2.0-S2666784324000780-main.pdf (Guo, Morris, Sumner, Taylor, *Cleaner and Responsible Consumption* 16, 2025) | H |
| Same study, jeans seam strength | 73–550 N; threshold 290 N women's / 320 N men's | same | H |
| Same study, jeans abrasion | 15,000–105,000 rubs; threshold 60k women's / 55k men's | same | H |
| Same study, jeans weft tensile | 120–1,300 N; threshold 430 N women's / 530 N men's | same | H |
| Same study, tee bursting strength | 187–437 N | same | H |
| Same study, tee spirality | 0% to 15.4% | same | H |
| Same study, wash protocol | jeans to 30 washes; tees to 56 washes; 30 °C | same | H |
| Same study, dimensional tolerance | jeans ±3%; tees ±5% length, ±7% width | same | H |
| Leeds PLATE 2025: 47 tees priced £4–£395; no price correlation; most expensive ranked 28/47; the best cost £28; 6 of the top 10 cost under £15 | — | https://resourcemedia.eco/article/t-shirt-price-fails-predict-garment-durability and abstract at https://eprints.whiterose.ac.uk/id/eprint/229192 | H (abstract) / M (press) |
| Same study: synthetic blends beat pure cotton (64% of synthetic-containing tees ranked in the top half); heavier pure-cotton tees beat lighter ones (no GSM given); pilling weighted highest | — | same | M |
| Leeds/Hubbub/Primark "Worn Out": 65 garments, <£5 to ~£150; price not reliable; only men's hoodies improved with price | — | https://fashionunited.uk/news/fashion/durability-of-clothing-not-linked-to-a-higher-price/2023072070653 ; https://ahc.leeds.ac.uk/media/news/article/2802/budget-jeans-more-durable-than-luxury-alternatives-ahc-researchers-find | M |
| WRAP 2026 report: 200+ garments, 10,000+ specimens, 27 partners; price, brand and fibre composition do not predict durability | — | https://www.wrap.ngo/resources/report/durability-fashions-next-competitive-advantage | H (summary page; full report is gated) |
| WRAP 2015: 9 extra months of active use cuts carbon, water and waste footprints by ~20–30% | 20–30% | https://www.letsrecycle.com/news/wrap-publishes-clothing-sustainability-guide/ | M (press coverage of WRAP) |
| Which? 2021: 20 jeans at £10–£145; durability not tied to price; £145 Diesel scored worst for wear | — | https://www.which.co.uk/news/article/made-to-last-popular-jeans-put-to-the-test-aH0LX2N4HXuw | M–H |
| Stiftung Warentest 2011: 15 men's jeans at €10–€249; €26 Zara best; €249 7FAM wore through fast; several lost points for sloppy workmanship | — | https://www.test.de/presse/pressemitteilungen/Jeans-fuer-Maenner-Die-beste-Hose-ist-eine-der-billigsten-4282722-0/ | H (dated) |
| Ecobalyse durability coefficient | 0.67 min, 1.00 standard, 1.45 max. Holistic value = min(physical, non-physical) | https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/src/Data/Unit.elm | H |
| Ecobalyse non-physical formula | 0.67 + 0.78 × (0.5 × references index + 0.5 × repair index) | https://raw.githubusercontent.com/MTES-MCT/ecobalyse/master/src/Data/Textile/Economics.elm | H |
| Ecobalyse references index | ≤1,000 refs → 1; 1,000–7,000 → falls linearly 1 to 0.5; 7,000–16,000 → 0.5 to 0; >16,000 → 0 | same | H |
| Ecobalyse repair index | repair/price < 0.33 → 1; > 1 → 0; interpolated between. Large firm with repair service: ×0.67 + 0.33; large firm without: ×0.67; SME: unscaled | same | H |
| French arrêté of 6 Sept 2025 (in force 1 Oct 2025) confirms the 0.67–1.45 range with 50% range width and 50% repair incentive | — | https://aida.ineris.fr/reglementation/arrete-060925-relatif-a-signaletique-a-methodologie-calcul-cout-environnemental | H |
| Ecobalyse default repair costs | shirt/tee 10; trousers/jeans 14; sweater 15; skirt/dress 19; coat 31 | products.json (above); also https://projetcelsius.com/en/blog/ecobalyse-environmental-cost-calculator/ | H |

**What this means for Filo.** Independent lab work agrees that retail price is a poor predictor of physical durability: Leeds 2025 papers, WRAP 2026, Which?, Stiftung Warentest. For Filo's fair-price model:
- Price should be built up from inputs (fabric mass × fibre cost, labour minutes, construction premiums) rather than trusted as a quality proxy.
- Durability should come from fibre or blend and fabric weight, not from price. These are the only predictors with some support: synthetic blends and heavier cotton tees did better in the Leeds tee study.

---

## Gaps

1. **Peer-reviewed garment mass table.** Ecobalyse examples cover tee, shirt, skirt, jeans, trousers, sweater and coat. Nothing government-grade covers hoodie, cardigan, blazer, puffer, leather jacket, dress or blouse, so those rows rely on shipping blogs (L). The PEFCR Apparel & Footwear v3.1 has representative-product BOMs and duration-of-service tables (Table 6, Annex IV), but the PDF host was blocked by the proxy.
2. **Fine-gauge vs chunky sweater grams, and cardigans.** Only the L-grade parcelpath ranges were found.
3. **Factory fabric consumption** for knits in kg, jeans, dresses, blazers and coats. The coat figures come from home-sewing patterns. The onlineclothingstudy SAM table by garment is an image I could not read.
4. **Labour-minute premiums for specific features** (French seam vs overlock, hand-finishing, pattern matching, lining) are not published as numbers. Only qualitative "slower/costlier" claims, plus the SPI speed ratio from fibre2fashion.
5. **Canvas vs fused factory cost difference.** Only hand-vs-machine lapel times (which conflict: 1 h vs 4–5 h) and retail price tiers. No ex-factory cost premium.
6. **ASTM D6193 text** is paywalled. No authoritative "budget vs quality" SPI table was found. The 10–12 woven and 14–16 knit figures come from an L-grade page; Coats and Kentucky Extension corroborate roughly 10–14.
7. **Stiftung Warentest** data is from 2011. No recent T-shirt or coat test was fetched.
8. **Leeds results do not report GSM, SPI or seam type**, so no study links construction specifics to measured lifespan. Construction-to-durability links in this file are expert or qualitative.
9. **Ecobalyse Gitbook methodology pages and the PEFCR PDF** were blocked by the proxy. Figures were taken from the Ecobalyse source code instead, which is authoritative for the calculator.
10. **Shirting thread count and ply**: no numeric durability evidence for 2-ply over 1-ply.
