# Spike 01: 20 catalog queries vs the GO rule (scripted, 2026-11-09)

Script: `src/spike/run_spike.py`. Raw responses: kept locally in `docs/research/spike-01-raw/`, not published (Shopify API terms; D61). Trimmed test copies: `tests/fixtures/search/`. Limit 20 per search.
Every search sent ships_to=IN, available=true, price.max (paise), Target gender, context IN/INR.
"Good" = in stock + INR + price <= cap (re-checked in code) + title has item word AND colour word (auto, human to confirm).

**Strict (ships_from=IN, Indian merchants): 20/20 queries (100%) have >= 3 good hits.**
**Loose (ships to IN from anywhere): 19/20 (95%).** GO threshold: 30%.

## Verdict: GO on the Shopify Global Catalog (curated index NOT needed)

## Findings (checklist a-c from design-doc Next Steps #1)
- **(a) India filters work.** `ships_to=IN`, `ships_from=[IN]`, context `IN/INR` accepted. ships_from changes results (IN-origin vs anywhere top-20 overlap is often only 2-10 of 20) and returns 154 distinct Indian sellers across 20 queries (Urbano, TIGC, Sassafras, Thomas Scott, Cahoot, Wrogn, Saadaa, Raymond, US Polo India, Janasya...). Prices come back in INR.
- **(b) Coverage is strong, incl. the two hard Western cases.** Strict pass 20/20 vs a 30% bar. Weakest: grey turtleneck (5 good IN-origin hits; 0 when origin is open, foreign stores crowd it out) and bell-bottoms (7, mostly "bootcut/flared" naming). => Always send ships_from=IN; never search "anywhere".
- **BUG FOUND: price filter is applied in USD.** API message: "Price filtering was applied on a USD basis". Sending 200000 (meant ₹2,000 in paise) = $2,000, so the filter did nothing. Fix: convert the INR cap to USD cents with headroom (~+10%) for the API filter, and keep the exact INR re-check in code (core logic #5). The scores above already use the code re-check, so they stand.
- **(c) Search returns 1 variant per product; size stock needs `get_product`.** One `get_product` call returns `options[].values[]` with `available` per size (full size x stock matrix) plus per-variant `checkout_url`. Cost: 1 detail call per shown product (5 options x 2 items = 10 calls, ~2 s at 5 req/s). Cache 10 min max (ToS).
- **Size labels need normalising before `selected`.** Selecting "XXL" on a store that uses "2XL" silently returns all variants. Labels seen: S, M, m, 28, "M (38)", "S(36)", "S-(28-30)", 2XL.
- **Relevance:** the auto title check (item word + colour word) is lenient on synonyms (bootcut counted as bell-bottom; high neck as turtleneck). Human spot-check of top 8 per query is listed above.
- `checkout_url` format is `https://{store}/buy/{variant}:1?...` (not `/cart/`). `eligible.native_checkout` was false on the sample (handoff, as designed).

## Not yet done
- (d) Click 5 real `checkout_url`s (multi-item?) on Indian stores and confirm they land on a payable checkout.
- (e) Sarvam `sarvam-105b` JSON validity on 10 requests incl. Hinglish.

## Results

| # | Query | Cap ₹ | Anywhere good / returned (total) | IN-origin good / returned (total) | get_product depth (1st hit) |
|---|---|---|---|---|---|
| 1 | grey turtleneck men | 2000 | 0 / 20 (921) | 5 / 20 (926) | 1 variants, 1 in stock, sizes: S |
| 2 | blue bell bottom jeans men | 2000 | 6 / 20 (776) | 7 / 20 (779) | 1 variants, 1 in stock, sizes: 28 |
| 3 | white linen shirt men | 2500 | 18 / 20 (849) | 19 / 20 (809) | 1 variants, 1 in stock, sizes: M (38) |
| 4 | black hoodie men | 2000 | 10 / 20 (821) | 16 / 20 (876) | 1 variants, 1 in stock, sizes: S |
| 5 | navy chinos men | 2000 | 15 / 20 (819) | 19 / 20 (813) | 1 variants, 1 in stock, sizes: 28 |
| 6 | olive cargo pants men | 2500 | 13 / 20 (756) | 18 / 20 (826) | 1 variants, 1 in stock, sizes: M |
| 7 | maroon kurta men | 1500 | 11 / 20 (815) | 18 / 20 (891) | 1 variants, 1 in stock, sizes: 38 |
| 8 | beige oversized t-shirt men | 1000 | 13 / 20 (864) | 19 / 20 (882) | 1 variants, 1 in stock, sizes: S |
| 9 | black joggers men | 1500 | 10 / 20 (760) | 18 / 20 (817) | 1 variants, 1 in stock, sizes: M |
| 10 | blue denim jacket men | 3000 | 11 / 20 (834) | 20 / 20 (800) | 1 variants, 1 in stock, sizes: M |
| 11 | blue polo t-shirt men | 1200 | 13 / 20 (821) | 16 / 20 (835) | 1 variants, 1 in stock, sizes: m |
| 12 | grey sweatshirt men | 1500 | 17 / 20 (910) | 20 / 20 (819) | 1 variants, 1 in stock, sizes: S |
| 13 | pink co-ord set women | 3000 | 19 / 20 (941) | 20 / 20 (947) | 1 variants, 1 in stock, sizes: XS |
| 14 | black bodycon dress women | 2500 | 17 / 20 (896) | 19 / 20 (927) | 1 variants, 1 in stock, sizes: S |
| 15 | white kurti women | 1500 | 19 / 20 (945) | 18 / 20 (967) | 1 variants, 1 in stock, sizes: S(36) |
| 16 | mustard saree women | 3000 | 19 / 20 (879) | 19 / 20 (941) | 1 variants, 1 in stock, sizes: - |
| 17 | blue straight fit jeans women | 2000 | 16 / 20 (745) | 13 / 20 (835) | 1 variants, 1 in stock, sizes: S-(28-30) |
| 18 | green anarkali kurta women | 3500 | 18 / 20 (923) | 18 / 20 (912) | 1 variants, 1 in stock, sizes: S |
| 19 | red crop top women | 1000 | 14 / 20 (899) | 15 / 20 (891) | 1 variants, 1 in stock, sizes: L |
| 20 | black palazzo pants women | 1200 | 13 / 20 (807) | 15 / 20 (856) | 1 variants, 1 in stock, sizes: S |

## Top hits per query (IN-origin search). Y = auto-relevant, ~ = passes hard filters only, x = fails price/stock/INR

### 1. grey turtleneck men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men’s Light Grey Merino Wool Turtle Neck Sweater – Refined Everyday Kn | 98degreenorth | ₹1498
- [Y] Men’s Light Grey Merino Wool Turtle Neck Sweater – Stretchable Cozy Pu | 98degreenorth | ₹1498
- [~] Men’s Wine Merino Wool Turtle Neck Sweater – Elegant Woolen Pullover f | 98degreenorth | ₹1498
- [~] Solid Turtle Neck Sweater | Cahoot | ₹1299
- [~] Men’s Coffee Merino Wool Turtle Neck Sweater – Warm Classic Knit Pullo | 98degreenorth | ₹1498
- [~] Men’s Black Merino Wool Turtle Neck Sweater – Classic Refined Knit for | 98degreenorth | ₹1498
- [~] Full Sleeve Turtle Neck Sweater | Cahoot | ₹1299
- [Y] Thomas Scott Men Grey Regular Fit Long Sleeves High Neck Solid Knitted | Thomas Scott | ₹1489

### 2. blue bell bottom jeans men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Ben Martin Men’s Bell Bottom Jeans Bootcut Denim Pants Wide Leg Flared | SVB Ventures  | ₹1183
- [Y] Ben Martin Men’s Bell Bottom Jeans || Bootcut Denim Pants || Wide Leg  | SVB Ventures  | ₹1183
- [~] Drift Bootcut Jeans | Offduty India | ₹1690
- [~] Men's Blue Straight Fit Washed Jeans Stretchable | Urbano Fashion | ₹999
- [~] Men's Blue Dad Fit Solid Street Wear Ripped Jeans | TIGC | ₹959
- [Y] Men's Dark Blue Washed Bootcut Jeans Stretchable | Urbano Fashion | ₹1099
- [~] Men's Washed Blue Barrel Loose Fit Solid Street Wear Belted Jeans | TIGC | ₹999
- [Y] Men's Blue Bootcut Fit Solid Casual Jeans | TIGC | ₹1229

### 3. white linen shirt men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Linen White Premium Shirts for Men | Cotton Dakshin | ₹979
- [Y] Men's White LYOLEN™ Linen Shirt | Classy Vouge | ₹1299
- [Y] German Linen Prime White Shirts for Men | Andhra Cotton | ₹990
- [Y] 100% Pure Linen White Shirt For Men | Alaya Cotton | ₹2325
- [Y] Casa Linen White Colour Shirts for Men | Andhra Cotton | ₹995
- [Y] Belgium Linen White Shirts for Men | Cotton Dakshin | ₹975
- [Y] White Color Solid Cotton Linen Shirt For Men | Punekar Cotton | ₹1150
- [Y] Linen Plus Solid White Shirt For Men | Alaya Cotton | ₹999

### 4. black hoodie men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men's Solid Zip-Up Hoodie Sweatshirt  | Black | Wrogn | ₹1799
- [Y] No Fade Black Hoodie | Blakc | ₹1490
- [Y] Men's Jet Black Cotton Solid Oversized Hooded Neck Sweatshirt | Urbano Fashion | ₹999
- [Y] Men's Black Regular Fit Hooded Neck Sweatshirt | Numero Uno | ₹1439
- [~] Oversized Hoodie | Nobero | ₹1499
- [Y] Black Regular Fit Hoodie | beinghuman-clothing | ₹1248
- [Y] Black Solid Regular Hoodie | THE EG STORE | ₹799
- [~] Dual Zip Black Sweatshirt | Maniac | ₹1499

### 5. navy chinos men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men's Navy Blue Regular Fit Solid Casual Chinos | TIGC | ₹1289
- [Y] Men's Navy Slim Fit Chinos | Urbano Fashion | ₹999
- [Y] Men Navy Tailored Stretchable Chinos | FYVA | ₹1399
- [Y] Men Navy Cotton Stretch Chino Trousers | FYVA | ₹1499
- [Y] Men's Navy Blue Slim Fit Solid Casual Chinos | TIGC | ₹1349
- [Y] Navy Smart Regular Fit Mid-Rise Cotton Linen Woven Chinos Trouser For  | Thomas Scott | ₹1039
- [Y] Navy Straight Fit Mid-Rise Solid Chinos Trouser For Men | Thomas Scott | ₹979
- [~] Plus Men's Royal Blue Cotton Regular Fit Casual Chinos Trousers Stretc | Urbano Fashion | ₹1149

### 6. olive cargo pants men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men Olive Carpenter Cargo Pants | FYVA | ₹1299
- [Y] Men's Olive Cargo Pants | Beevee | ₹999
- [Y] Men's Olive Green Regular Fit Solid Cargo Chino Pant with 6 Pockets | Urbano Fashion | ₹1199
- [Y] Men's Olive Loose Baggy Fit Solid Cargo Chino Pant with 6 Pockets Non- | Urbano Fashion | ₹1199
- [~] Men Olive Solid Regular Fit Lower | Monte Carlo | ₹1259
- [Y] Olive Green Men Multi Pocket Solid Cargo Pant | Gritstones | ₹1599
- [Y] Urbano Fashion Men's Olive Loose Baggy Fit Solid Cargo Chino Pant with | Urbano Fashion | ₹1199
- [Y] Rock.it Men Olive Solid Regular Fit Cargo | Monte Carlo | ₹1313

### 7. maroon kurta men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Maroon Khadi Cotton Kurta for Men in Traditional Style | Khadi Sadan | ₹1469
- [Y] Ethnic Bay Men Maroon Cotton Solid Woven Design Straight Kurta MKU1096 | Ethnic Bay | ₹649
- [Y] Men Maroon and Gold Sequins Embroidered Chanderi Silk Straight Kurta W | See Designs | ₹1407
- [Y] Men Maroon Airy Linen Long Kurta |  सादा / SAADAA | ₹1199
- [Y] Bandhan – Maroon Printed Cotton Short Kurta for Men | Slayvana | ₹599
- [Y] Maroon Ombre Printed Kurtas For Men | Jompers | ₹755
- [Y] Men's Premium Maroon Embroidered Cotton Kurta | AkshayaFashion | ₹549
- [Y] Maroon-Blue Cotton  Kurta for Men | Dheu  | ₹630

### 8. beige oversized t-shirt men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men Beige Oversized T-shirt |  सादा / SAADAA | ₹799
- [Y] Beige Men’s Solid Oversized T-Shirt | The Meme House | ₹795
- [Y] Men Beige Solid Oversize T-Shirt | Bene Kleed | ₹699
- [Y] Solid Men Oversized T-Shirt - Beige | pronk.in | ₹559
- [Y] Mens Beige Embroidered Oversized T-Shirt | Bene Kleed | ₹749
- [Y] Men Beige Oversized Embroidered Half Sleeves T-Shirt | Bene Kleed | ₹749
- [Y] Rebelz Beige Oversized Graphic Printed T-shirt | Veirdo | ₹499
- [Y] Men Beige Graphic Oversized Half Sleeves T-Shirt | Bene Kleed | ₹699

### 9. black joggers men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men Black Regular Fit Joggers | FYVA | ₹1099
- [Y] Men Black Cult Leisure Joggers | Cult.fit Limited | ₹1129
- [Y] Men Black Oversized Joggers | SASSAFRAS | ₹974
- [Y] Black Loose Fit Joggers | BonkersCorner | ₹1299
- [Y] Men's casual relaxed cargo fit woven jogger | Black | Wrogn | ₹1199
- [Y] Black Heavyweight Joggers | BonkersCorner | ₹1399
- [Y] Men's Black Relaxed Fit Solid Straight Hem Joggers | TIGC | ₹1359
- [Y] Diminutive Black Wide Leg Joggers | BonkersCorner | ₹1299

### 10. blue denim jacket men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men's Blue Relaxed Fit Full Sleeve Utility Denim Jacket | Urbano Fashion | ₹1599
- [Y] Men's Blue Washed Denim Jacket | Showoffff | ₹2360
- [Y] Men's Dark Blue Relaxed Fit Full Sleeve Harrington Denim Jacket | Urbano Fashion | ₹1599
- [Y] Plus Men's Dark Blue Relaxed Fit Full Sleeve Utility Denim Jacket | Urbano Fashion | ₹1999
- [Y] Plus Men's Light Blue Regular Fit Washed Full Sleeve Denim Jacket | Urbano Fashion | ₹1999
- [Y] Heavily Washed Denim Jacket | Cahoot | ₹1999
- [Y] Plus Men's Black Regular Fit Washed Full Sleeve Denim Jacket | Urbano Fashion | ₹1799
- [Y] Men's Blue Regular Fit Washed Full Sleeve Denim Jacket | Urbano Fashion | ₹1599

### 11. blue polo t-shirt men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men Polo T-shirt Blue | Reprise | ₹1049
- [Y] Men's Light Blue Slim Fit Solid Flat Knit Zip Collar Polo T-Shirt | TIGC | ₹1149
- [Y] Men's Navy Blue Slim Fit Colourblocked Flat Knit Panel Polo T-Shirt | TIGC | ₹1099
- [Y] Men's Blue Regular Fit Solid Flat Knit Polo T-Shirt | TIGC | ₹939
- [Y] Polo T Shirts for Men in Stone Blue | RedTape | ₹651
- [~] Men's Black Slim Fit Solid Flat Knit Johnny Collar Polo T-Shirt | TIGC | ₹1099
- [Y] Men’s Naval Blue Polo T-Shirt | Pavilion Jeans Online Store | ₹974
- [Y] Men's Blue Slim Fit Panel Striped Flat Knit Polo T-Shirt | TIGC | ₹1089

### 12. grey sweatshirt men
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Men's Grey Crew Neck Brushed Fleece Sweatshirt | Numero Uno | ₹1040
- [Y] Men Grey Solid Round Neck Full Sleeve Sweatshirt | Rockit | ₹989
- [Y] Men's Solid Grey Melange Round Neck Sweatshirt | Cantabil | ₹799
- [Y] Men's Graphic Printed Grey Fuzzy Sweatshirt | Showoffff | ₹1260
- [Y] Grey Plain Oversized Sweatshirt | Veirdo | ₹899
- [Y] Men Printed Sweatshirts Grey | Reprise | ₹1099
- [Y] Men Plain Zipper High Neck Casual Sweatshirt / Grey | TRIPR | ₹499
- [Y] Raymond Men Grey Regular Fit Solid Sweatshirt | MyRaymond | ₹1154

### 13. pink co-ord set women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Pink Notch Neck Floral Full Sleeve Co-Ord Set For Women | SanskrutiHomes.in | ₹1199
- [Y] Pink Geometric Print Shirt Collar Pure Cotton Co-Ord Set For Women | SanskrutiHomes.in | ₹899
- [Y] Pink Solid Shirt Collar Short Sleeve Co-Ord Set For Women | SanskrutiHomes.in | ₹1199
- [Y] Pink And White Floral Printed Co-ord Set For Women | Saayra.in | ₹999
- [Y] Pink Dotted Print Co-ord Set for Women | Asnowera | ₹1399
- [Y] Trendy Pink Floral Co-ord Set for Women – Casual & Festive Look | Saayra.in | ₹999
- [Y] Women's Co ord set Pink Orange flower | Pitshirts | ₹440
- [Y] Pink Color Floral Printed Cotton Co-ord Set for Women | Claura  | ₹1580

### 14. black bodycon dress women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Athena Women Black Self Design Bodycon Dress | athenalifestyle.com | ₹499
- [Y] Black Floral Mesh Strap Bodycon Midi Dress with Ruffle Hem for Women | Miray  | ₹1099
- [Y] Women Black Velvet Cowl Neck Bodycon Dress | SASSAFRAS | ₹959
- [Y] Women Black Rib Bodycon Midi Dress | SASSAFRAS | ₹863
- [Y] Women Black Sequence Sleeve Detail Bodycon Dress | SASSAFRAS | ₹1124
- [Y] Women Black Ribbed Bodycon Maxi Dress | SASSAFRAS | ₹989
- [~] TWILA | Roslyn | ₹1850
- [Y] Women Black Rib Side Slit Bodycon Dress | SASSAFRAS | ₹809

### 15. white kurti women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] White Handloom Cotton Front-Slit Short Kurti for Women | SaavStore | ₹699
- [Y] White Chikankari Short Kurti for Women with Straight Fit & Full Sleeve | The House of Indu | ₹499
- [Y] White Cotton Chikankari Lace Kurti for Women | Everything In Chikankari | ₹999
- [Y] Womens Printed White Rayon Kurti | GillKart | ₹638
- [Y] Women's Off-White Fendi Kurti with Lining | Ethnic Wear | RAE Trends | ₹1089
- [Y] Womens Printed White Cotton Slub Kurti | GillKart | ₹629
- [Y] White Chikankari Kurti for Women with Blue Embroidery & Straight Fit | The House of Indu | ₹999
- [~] IRA Cotton Chikankari Kurti for Women | Lightweight Hand Embroidered O | Paridhan Chikan Exclusive | ₹1325

### 16. mustard saree women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Mustard Yellow Printed Cotton Saree – Soft Cotton Saree for Women (BFC | Bagru Fashions | ₹1599
- [Y] Ivory White & Mustard Yellow Mysore silk sarees  with Gold Zari Butti  | Vootbuy | ₹1499
- [Y] Mustard  Mysore Silk Saree with Maroon & Gold Zari Border and Blouse | Vootbuy | ₹1299
- [Y] Royal Mustard Yellow  Satin Satin Silk Saree with Antique Gold Zari We | Vootbuy | ₹1899
- [Y] Royal Mustard Yellow  Satin Satin Silk Saree with Antique Gold Zari We | Mysore Pattu | ₹2297
- [Y] Dark Green Embroidered Silk Saree with Mustard Brocade Border and Embe | Modamecca | ₹899
- [Y] Mustard Yellow Mul Cotton Saree with Midnight Blue Contrast Pallu, Tas | Almaari Fashion | ₹1699
- [Y] Black Mustard Printed Cotton Saree – Soft Cotton Saree for Women(MVCS4 | Bagru Fashions | ₹1599

### 17. blue straight fit jeans women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Women Blue Grey  Straight Fit Soft Denim Jeans | TEMPA | My Store | ₹1199
- [Y] Women Light Blue Straight Fit Soft Denim Jeans | BLUETA | My Store | ₹1199
- [Y] Women Bella Jeans Straight Fit Blue Mid Rise | Spykar Lifestyles | ₹1039
- [Y] Women's Blue High Rise Straight Fit Washed Jeans Non-Stretchable | Urbano Fashion | ₹1299
- [~] High Rise Straight Fit Jeans | U.S. Polo Assn. India | ₹1890
- [Y] Women's Blue High Rise Straight Fit Washed Jeans Non-Stretchable | Urbano Fashion | ₹1249
- [Y] Women Blue High Waisted Staright Fit Jeans | SASSAFRAS | ₹1034
- [Y] Women Blue Seam Detail Wide Leg Jeans | SASSAFRAS | ₹1103

### 18. green anarkali kurta women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Women Green Printed Pure Cotton Anarkali Kurta | Rain & Rainbow | ₹1647
- [Y] Women’s Olive Green Cotton Anarkali Kurta Pant Set with Embroidered Yo | The House of Jaipur | ₹999
- [Y] Women's Green Anarkali Cotton Kurta | Showoffff | ₹1890
- [Y] Women's Emerald Green Embroidered Anarkali Kurta Set with Chiffon Dupa | Vastrakaala  | ₹3099
- [Y] Janasya Women's Dark Green Silk Blend Floral Zari Embroidered Anarkali | Janasya.com | ₹1549
- [Y] Green Silk Embroidered Anarkali Kurta Set With Dupatta For Women | Mokosh.in | ₹1449
- [Y] Olive Green Heavy Embroidered Anarkali Kurta Set with Dupatta | Jaipura Kurta | ₹1299
- [Y] Women Pista Green Cotton Embroidery Kurta Pant and Dupatta set | Sugamdha | ₹1799

### 19. red crop top women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Women's Red Layered Crepe Wrap Crop Top | Showoffff | ₹610
- [Y] Women Red Frilled Crop Top | FYVA | ₹699
- [~] Net Crop Top | Revolte | ₹779
- [Y] Red Solid Off Shoulder Crop Top | Zink London | ₹399
- [Y] Women's Red Sweetheart Neck Bralette Crop Top | Showoffff | ₹715
- [Y] Women's Red Net Crop Top | Showoffff | ₹940
- [~] Crop Top | STREET NINE FASHIONS | ₹322
- [Y] Womens Red and White Striped Crop Top | OWND! | ₹399

### 20. black palazzo pants women
- API messages: ["Price filtering was applied on a USD basis; returned prices are contextualized to the buyer's market and may differ."]
- [Y] Women Black Viscose Palazzo Pant |  सादा / SAADAA | ₹899
- [Y] Women's Black Cotton Palazzo Pants – Relaxed Wide-Leg Fit | Pakhi Fashion | ₹599
- [Y] Stylish Women's Palazzo - Black | GO GAZAB | ₹699
- [Y] Lamora Women’s Black Wide-Leg Drawstring Palazzo Pants | Juniperfashions | ₹495
- [Y] Presta Palazzo Pant - Black | Poomer Clothing Company | ₹379
- [Y] Women's Black Viscose Palazzo Pants | vastramay | ₹799
- [Y] Premium Solid Black Wide Leg Palazzo Pants for Women (XS–10XL) | My Store | ₹1099
- [~] Women's Palazzo Pants - R1101 | Swapna Weddings | ₹249
