# Decision Log: DrapeFit (chat-to-cart shopping agent)

Newest decisions at the bottom. Each entry gives the decision, the options that were on the table, and why.

## 2026-11-08: Office-hours session (problem selection to approved design)

| # | Decision | Alternatives considered | Why |
|---|---|---|---|
| D1 | Start fresh instead of continuing the Nov 6 WhatsApp RFQ idea (D2C packaging quotes) | Build on the RFQ agent; fresh but finance-only | User's choice. They wanted an open domain. |
| D2 | Reachable user pools: young professionals/colleagues, small business owners, students | Parents/elders | These are the groups where the user can get 5 people this week. |
| D3 | Explore fintech first | Auto-debit auditor, SMB dues collection, bill reminders, group settle | No named pain yet. The user wanted a wider fintech scan. |
| D4 | Rejected tax-proof copilot, SMB dues, auto-debit auditor and GST matching | — | The user wanted investments or shopping, and pointed to Razorpay "Fix My Itch" (couldn't load: the page is JS-rendered and the browser tools were offline). |
| D5 | Liked "Portfolio X-ray" + an NL mutual-fund screener ("MFs with X under Y and Z over A") | Refund chaser, forgotten-money finder, office gift pool | Plays to the user's MF expertise. Flagged: possible Dezerv employment conflict and SEBI advice line. |
| D6 | **Shopping first** (MF screener parked as a pivot) | MF first; both in parallel | User's choice. Shopping is the agentic-commerce focus, and there's no employer conflict. |
| D7 | **Approach B: chat-to-cart with approve-to-buy** | A: discovery-only gift finder; C: team gift pool | The user wants the full loop: NL multi-item request → 10 options per item → add to cart in chat → checkout → pay. |
| D8 | Approved the design doc (built in 3 rungs) | Revise; start over | Every open risk has a go/no-go test. |

## Core logic decisions (in the design doc)

1. **The LLM only translates.** Sarvam parses the request into a validated JSON spec. All prices, sizes and stock come from catalog API objects.
2. **No LLM in the output path.** Option lists are rendered by code templates, so 100% grounding is a regression test.
3. **The agent never pays.** Checkout is a handoff by default (cart permalink per merchant → the merchant's own payment page). Reason: Indian Shopify D2C stores mostly use third-party checkouts (GoKwik, Shiprocket, Razorpay Magic).
4. **Ask, don't guess.** A non-empty `ambiguities[]` triggers a clarifying question (e.g. "size 30" for a top).
5. **Hard filters in code** (size in stock, INR variant price ≤ cap, ships_to=IN) → colour synonym table → size normalisation → rank by relevance, max 3 per brand.
6. **Channel: Telegram** (not WhatsApp, which risks a ban). 5 options per item by default, labelled A1..A5 / B1..B5.
7. **Free stack:** Shopify Global Catalog MCP (API key, no approval), Sarvam `sarvam-105b`, Telegram Bot API, SQLite/Neon, a free host plus an external cron.

## Gating spike (next action, before any code)

20 real queries with ships_to=IN. **GO on the catalog** if at least 30% of queries return 3 or more relevant, in-stock, INR, IN-shipping hits. **Otherwise** use a curated `/products.json` index of 20-30 Indian Shopify D2C stores. Full checklist: `design-doc.md` → Next Steps.

## 2026-11-08: Setup decisions

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D9 | Secrets live in Windows **user environment variables** (`SARVAM_API_KEY` is set). Pi's `models.json` references `"$SARVAM_API_KEY"`. | A `.env` file in Desktop\Building; a literal key in config | Env vars aren't in any file that might get synced or shared. The Building folder already holds other sensitive files. |
| D10 | Run `/plan-eng-review` right after the spike | Plan the architecture now | Architecture depends on spike facts: variant data, coverage, permalinks. |
| D11 | Spike uses Shopify's hosted example agent profile; self-host later | Self-host a profile now | Fewer steps before the first real query. |

## Open questions

- Indian merchant coverage in Shopify Global Catalog (gating)
- Whether search hits include per-variant size and stock, or need N+1 detail calls
- Whether any Indian merchant supports UCP native checkout
- Whether Sarvam JSON-schema output is reliable, and how well it handles Hinglish
- Telegram chat vs a Telegram Mini App for image-heavy browsing

## 2026-11-08: Naming & Shopify onboarding

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D12 | Project renamed **DrapeFit**; folder is now `Desktop\Building\DrapeFit`; Shopify dev org is also named DrapeFit | Keep "chat-to-cart-agent" | User's choice. A product name reads better on a portfolio than a description. |
| D13 | Shopify's dashboard now has an **Agents** page (not "Catalogs"): (1) **Create a catalog** = choose which products the agent can search; (2) **Agent identities → New identity** = API credentials + agent name shown at checkout. Spike catalog scope: broad (all products), with India filters applied per query in code | Narrow catalog to a few stores up front | Spike is measuring India coverage, so pre-filtering in the dashboard would hide the answer. Narrow later if results are noisy. |
| D14 | Catalog "DrapeFit" config: all stores, Shopify only (Off-Shopify beta off); buyer country IN, ships to IN, ships from Anywhere; category Apparel; condition New; in stock only; colour/size/gender/price/rating left open | Off-Shopify on; ships from IN only; bake size/colour into catalog | Off-Shopify items may lack Shopify cart permalinks (handoff breaks). Ships-from left open so the spike can measure the Indian share. Size, colour, gender and price change per request, so code sets them per query. Indian D2C has few reviews, so no rating filter. |

## 2026-11-09: Catalog spike results (docs/research/spike-01-catalog-20-queries.md)

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D15 | **GO on Shopify Global Catalog**; curated `/products.json` index dropped as plan of record | Curated index of 20-30 stores | 20/20 queries had >=3 relevant, in-stock, INR, ships-from-IN hits (bar was 30%). 154 distinct Indian sellers. |
| D16 | Every search sends `ships_from=[IN]` | Ship-to-IN only | Without it foreign stores crowd out results (grey turtleneck: 0 good hits vs 5). |
| D17 | Price cap: send a USD-converted cap (+~10% headroom) to the API, exact INR check in code | Trust API price filter | API applies price filters in USD (stated in result.messages). |
| D18 | Two-step retrieval: `search_catalog` -> `get_product` per shown product for the size/stock matrix | Search only | Search hits carry 1 variant; get_product returns availability per size in one call. Size labels normalised before use (XXL vs 2XL fails silently). |

## 2026-11-09: Spike (d) + (e) results (docs/research/spike-02-checkout-and-sarvam.md)

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D19 | Checkout handoff = one Shopify cart permalink per merchant, `https://<store>/cart/<variant_id>:<qty>,...`. Fallback: the catalog's per-variant `checkout_url` (`/buy/...`), then product deep link `?variant=<id>` | Catalog `checkout_url` as primary; UCP native checkout | Permalink reached checkout on 7/7 Indian stores, and it takes several variants in one link (one "Pay on <brand>" button per merchant). `/buy` also resolved, but it is per-variant. All sampled variants have `native_checkout=false`, so UCP checkout stays out of the MVP. |
| D20 | Sarvam `sarvam-105b` with JSON-schema output is the parser. Code validates against the schema and retries once on failure. No repair layer. | Regex/rule parser; a second LLM as fallback | Spike (e) returned valid JSON every time (user-reported). Validating at the boundary is cheap insurance. More only if real traffic breaks it. |
| D21 | Channel confirmed: **Telegram** bot (BotFather token in `TELEGRAM_BOT_TOKEN`) | WhatsApp; web chat | Re-confirmed at spike close. Same reasoning as before: no ban risk, free, shareable by link. |

Spike complete (a-e). Next: `/plan-eng-review` to lock the architecture.

## 2026-11-09: Admin dashboard (deferred)

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D22 | Admin dashboard (every user request, bot response, funnel step reached) is built **after** the Telegram bot works end to end. But the bot writes an append-only event log from day one (request received, parsed spec, results shown, item added, checkout link sent, link clicked), keyed by telegram_user_id + session. The dashboard is then only a read layer on that log. | Build dashboard alongside the bot; add logging later | Logging added later loses the early sessions, and those are the most useful ones. The schema is cheap now and costly to retrofit. The dashboard UI itself doesn't block the core loop. |

