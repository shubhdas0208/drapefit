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

