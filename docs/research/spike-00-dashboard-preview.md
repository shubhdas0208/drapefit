# Spike 00: dashboard search preview (manual, 2026-11-08)

Catalog "DrapeFit" (config D14). Query typed in the Shopify dashboard preview: `kurta for men under 1500`.

| # | Brand | Product | Price | Rating |
|---|---|---|---|---|
| 1 | Saadaa (Indian D2C) | Men Maroon Airy Linen Long Kurta | ₹1,199 | 4.9 (4,342) |
| 2 | Kisah (Indian D2C) | Black Embroidered Kurta Trouser Set | ₹8,004 | 5.0 (1) |
| 3 | Saadaa | Men Black Airy Linen Short Kurtas | (cut off) | 4.8 (909) |

## Findings
- **Indian coverage is real, at least for ethnic wear.** The top results are Indian D2C brands, priced in INR, and each has a Checkout button (a cart permalink).
- **The search ignores budgets written in the query text.** "under 1500" still returned ₹8,004. This confirms core logic #1 and #5: Sarvam must parse the budget into `filters.price.max` (in paise), and the code must re-check the variant price before showing anything. Don't rely on keyword search for constraints.
- **Brand clustering.** Saadaa holds 2 of the top 3, so the max-3-per-brand cap matters.
- **Descriptions contain raw HTML entities** (`&nbsp;`), so strip them before rendering in Telegram.

## Still to verify in the scripted spike
Variant-level sizes and stock, Western wear (turtlenecks, bell-bottoms), all 20 queries scored against the GO rule.
