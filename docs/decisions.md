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

## 2026-11-10: /plan-eng-review, Rung 1 architecture lock (docs/eng-review-rung1.md)

The user picked D23 and D24 and delegated the rest ("take all decisions; the goal is to not spend money").

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D23 | **Lean Rung 1**: fixed 5 options per item, asyncio tasks inside one process; keep `/r/{token}` redirect, D22 event log and clarifying questions | Full doc Rung 1 (More button to 10, job queue); bare loop without redirect/log | Keeps every success metric measurable and drops only what no user has asked for. "More" moves to Rung 2. |
| D24 | **Host: Render free web service + Neon free Postgres** (user's pick; ₹0) | Fly.io + volume (~$2-5/mo); laptop + tunnel | Hard ₹0 constraint. Render's disk is ephemeral, so no state is stored on the box. |
| D25 | Keep-warm: cron-job.org hits `GET /healthz` every 10 min | Accept cold starts; paid instance | Render free sleeps after ~15 min idle (30-60 s wake breaks the 8 s budget). One always-on free service fits the 750 h/month limit. |
| D26 | All state in Neon (sessions, results, link tokens, events, processed updates); this replaces SQLite from the design doc | SQLite on Render disk | Render free wipes the disk on restart and redeploy. |
| D27 | Webhook returns 200 immediately, work runs in an asyncio task, `update_id` dedupe table, `secret_token` header check | Process inline before returning | Telegram retries slow webhooks, which would duplicate searches. The secret blocks forged updates. |
| D28 | One in-flight search per Telegram user; a new message during a search gets "Still working…" | Queue messages; cancel and restart | Simplest correct behaviour. Stops users doubling the catalog load. |
| D29 | `/r/{token}`: random token → stored URL, 404 if unknown, target host must equal the API seller domain | `/r?url=...` | Prevents an open redirect. |
| D30 | `Option` is a frozen dataclass built only from the `get_product` variant; the renderer accepts only `Option`; grounding test is a build gate | Render from search hits | Grounding becomes structural, not a convention. Search hits carry 1 variant (D18). |
| D31 | Catalog client: async httpx port of the spike client, one global rate limiter (5 req/s shared by all users), token refresh behind a lock, 10-min in-memory `get_product` cache | Per-request limiter; no cache | The limit is per API client, not per user. 10 min is the ToS maximum. |
| D32 | `get_product` runs in rank order until 5 options pass the size check, max 8 calls per item; search limit 30 | get_product on all hits; on the top 5 only | Top 5 alone often fails the size check. A cap of 8 keeps the latency budget. |
| D33 | USD cap = INR cap / `INR_PER_USD` × 1.15 (config constant); exact INR check in code | Live FX API | A stale rate only lowers recall slightly; no extra dependency. |
| D34 | Size and colour tables are data; an unmapped size label fails closed and logs `size_unmapped` | Fuzzy match | Showing a wrong size is worse than showing fewer options. The log tells us which labels to add. |
| D35 | Stream results per item; album (images) then a text list; text-only fallback if the album fails | Wait for all items; one combined message | Faster first result; albums can't carry buttons. |
| D36 | Write the 50-request parse eval before the parser prompt (T1); tests run on the spike-01 raw fixtures | Write the eval after the parser | Design doc step 2, plus fixtures already exist. Accepted risk: a Render restart mid-search loses that request (visible in the events table). |

## 2026-11-11: Rung 1 build (code in src/drapefit/, 167 tests, live smoke passing)

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D37 | **Parser model: `sarvam-105b-conversations`** (env `SARVAM_MODEL`) | `sarvam-105b` with reasoning_effort=low (`sarvam-30b` is deprecated) | Held-out eval (15 unseen cases): conversations 14/15 at p50 391 ms / p95 577 ms. sarvam-105b 14/15 at p50 7.2 s / p95 31 s, which breaks the 8 s budget on its own. |
| D38 | Sarvam `response_format: json_object` with the schema in the system prompt; Pydantic validates; max_tokens 800 | Strict `json_schema` mode (D20) | Strict mode produced runaway whitespace (finish_reason=length) on 4/6 Hinglish inputs; json_object was 6/6 valid. The validate-and-retry boundary from D20 stays. |
| D39 | **Correction to D17/D33**: send the INR cap as-is (`price.max` in paise, context currency INR). No FX constant. | Convert to USD in code | Spike data shows the API converts INR to USD itself (₹2,000 → $21.21; `conversion: fx_converted`). Worst overshoot ₹3,505 on a ₹3,500 cap, so the exact INR check in code stays. |
| D40 | Size flow: store's own label taken from the search hit's options (present on 93% of hits) → one `get_product(selected=[Size, Color])` → verify size label, colour, stock, INR price. Hits without size options get a plain `get_product` first (size matrix). Max 8 detail calls per item. | get_product plain for every hit (2 calls each) | 1 call per option in the common case. Verification is needed because a wrong `selected` label silently returns other variants. |
| D41 | A size the matcher can't read ("M/L", "18") returns a note asking the user to resend it, not "any size" | Ignore the size | The heading promises the user's size; showing other sizes would break the grounding promise. |
| D42 | Option title = product title (variant title only as fallback); Telegram text escaped with quote=False | Variant title | Live smoke: a variant title was just "Black". |
| D43 | Parse eval: 50 tuning cases + 15 held-out cases, order-insensitive item matching, size compared by normalised tokens. Tuning 49/50, held-out 14/15. Missed clarifications 0%, needless 0%. | Single set | The prompt was tuned on the 50, so only the held-out number is an honest accuracy estimate. |
| D44 | `scripts/run_local.py`: Telegram long polling + local SQLite, links straight to stores | Deploy before any real chat | Lets the user try the bot with only a BotFather token; zero accounts, zero cost. |
| D45 | Code-review fixes: network errors on album/typing are caught; one item failing doesn't drop the others (tasks cancelled on exit); malformed catalog payloads are retried; all LLM-derived text is HTML-escaped; /delete runs under the user lock; clarifications expire after 15 min; redirect never blocks on a logging failure; secrets compared as bytes; per-user locks are cleaned up | Ship as-is | Each would otherwise leave a user with silence or a broken message. Accepted risk unchanged: a Render restart mid-search loses that request (visible in events). |
| D46 | Set the `httpx` logger to WARNING in `app.py` and `scripts/run_local.py` | A log filter that redacts the token | httpx logs every request URL at INFO, and Telegram Bot API URLs contain the bot token (found on the first `run_local.py` start). The user was told to rotate the token. |

## 2026-11-12: Rung 2 plan (cart edits, checkout links, "did you buy?")

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D47 | **Router, cheapest first**: inline buttons and strict text grammar ("add A1 B3", "remove B2", "cart", "checkout", "clear") are handled in code with no LLM. Free text after a recent search (< 2 h) goes to an LLM follow-up classifier. Everything else goes to the Rung 1 parser. | One LLM call for every message; regex only | Labels are deterministic, so code is faster, free and never wrong. The LLM is only needed for language ("remove the jeans", "same but black", "kuch sasta dikhao"). The grammar only fires when the message has nothing but labels and cart words, so "add a black hoodie" still reaches the parser. |
| D48 | **Follow-up classifier is a separate Sarvam call with its own schema and eval** (`evals/followup/`). It returns action + targets + requested changes; **code applies the changes**. "Cheaper" = new cap at the cheapest shown price minus ₹1, computed in code. | Extend the Rung 1 parse schema; let the LLM rewrite the whole spec | Keeps the Rung 1 parse eval stable, and makes the classifier measurable on its own. The LLM never sees or produces a price shown to the user (core rule). |
| D49 | **Cart**: stored per user (table `cart_lines`), one line per variant, quantity from the request spec, max 10 lines. Cart actions run under a separate per-user cart lock, so tapping + while item B is still searching works. The cart survives checkout until the user taps Clear. | Cart in memory; one shared lock | Render restarts would wipe an in-memory cart. The Rung 1 search lock would reject taps mid-search. |
| D50 | **Checkout**: group lines by the storefront host, re-fetch every variant (stock and price) just before handing off, then send one `https://<host>/cart/<variant>:<qty>,...` permalink per merchant as a URL button, tracked through `/r/{token}` (label `CHECKOUT`). Price changes are flagged with the fresh API price; sold-out lines are dropped and named. A two-variant permalink reached checkout with both items on 98degreenorth (live probe, 2026-11-12). | Catalog `checkout_url` (one variant only); UCP checkout sessions | Spike 02: every variant has `native_checkout=false`, so handoff is the only path. One permalink per merchant = one payment per merchant. |
| D51 | **"Did you buy?"**: one follow-up per session, due 24 h after checkout links are sent or a product link is clicked (`FOLLOWUP_DELAY_HOURS`). Buttons: one "Bought from <brand>" per merchant, "Didn't buy", "Not yet". "Didn't buy" asks why (price, shipping, size doubt, didn't like it, other). "Not yet" re-asks once 24 h later (max 2 pings). Sent by `/tasks/followups` (cron-job.org, hourly) and by an in-process loop in `run_local.py`. | Ask per merchant in separate messages; no reason question | Measures completion per merchant (design doc) in one message. The blocker reason is the most useful product signal for testers. |

## 2026-11-12: Rung 2 build (252 tests, live smoke passing)

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D52 | **Follow-up classifier eval**: 39 tuning + 12 held-out cases (`evals/followup/`), graded on action, targets and every change field (invented changes count as errors). Held-out: action 100%, exact 12/12 on two runs, p50 311 ms. Tuning: 92-95% exact, varying by 1-2 cases between identical runs at temperature 0. Prompt tuning stopped after 3 rounds to avoid overfitting. | Keep tuning until 100% | Run-to-run variance is about the size of the remaining misses. Known miss: "women's version of the turtleneck" sometimes adds a product-type change too. |
| D53 | **Checkout re-check bypasses the 10-min product cache** (`get_product(fresh=True)`) | Use the cache like search does | The live smoke showed checkout in 31 ms, served entirely from cache, so it wasn't a real stock/price check. The cache stays for search. |
| D54 | Live Rung 2 smoke (`scripts/smoke_rung2.py`): search → "add A1 A2 B1" → "same jeans but in black" (re-searched only B) → checkout gave 2 merchant links (98degreenorth with 2 variants, DLAND) → **2/2 permalinks landed on Shopify checkout** → follow-up sent. | Unit tests only | Proves the whole loop against real APIs before testers see it. |
| D55 | **Rung 2 code-review fixes** (0 critical, 0 high, 7 medium, 3 low): an answered follow-up can't be reopened by an old "Not yet" tap; each follow-up is claimed atomically before sending (no double pings from overlapping cron runs) and one bad row can't block the batch; the session is saved before searching so a + tap mid-search keeps the requested quantity; text adds and the classifier see only options from each item's latest run; "remove the jeans" only touches lines from the current search; item names are HTML-escaped in the "which item?" question; lines that couldn't be re-checked are flagged; checkout/cart taps are answered before slow work, and re-checks run concurrently; /delete also takes the cart lock. | Ship as-is | Each fix has a regression test (261 tests). Accepted: a click later in a session whose follow-up was already sent doesn't schedule a second one. |
| D56 | **Delivery address and delivery time stay on the merchant's checkout.** Checkout messages link each brand's shipping and returns policy (`seller.links` from the catalog API) and say that the delivery date and shipping cost for the user's pincode appear on the brand's checkout. The bot never asks for an address. | Collect address/pincode in chat; show an estimated "3-5 days" | The catalog has no delivery-date, shipping-cost or pincode data (only policy links), so any number in chat would be invented, which breaks the grounding rule. Shopify checkout computes real shipping per pincode and Shop Pay autofills the address. Not collecting addresses keeps personal data out of DrapeFit. Revisit only if a merchant exposes UCP checkout (fulfillment options come back per address). |
| D57 | **Telegram UX pass**: / command menu (setMyCommands); /start shows a welcome with 3 tappable example searches; each item gets a one-tap **💸 Cheaper** button (code-only refine, no LLM); a tapped **+ A1** turns into **✓ A1** on the same message; removing/clearing edits the cart message in place instead of sending new ones; "Searching 2 items…" ticks "A ✓ B ✓" as items land. | Telegram Mini App (web UI inside Telegram); per-option photo cards | Biggest friction points for a first-time tester, all cheap. A Mini App is the real UI upgrade (grid, filters, cart page) but needs a hosted web app and a new eval surface; revisit after tester feedback. Per-option cards would mean 5+ messages per item. |
| D58 | **Pincode delivery dates: not possible today.** Probed live: `search_catalog`/`get_product` with `ships_to.postal_code` and context `postal_code` (560001, 110001) returned no `fulfillment` data for Indian merchants, although the schema has a `fulfillment.methods` field. Shopify's storefront cart API (shipping rates per address) was blocked by the permission system: calling a merchant's storefront API without that merchant's authorization isn't sanctioned for an agent. | Scrape storefront shipping-rate endpoints; let the LLM estimate | No grounded data source, so no number is shown (core rule). Paths that would work: (1) Shopify starts populating `fulfillment` for IN merchants, (2) a merchant enables UCP/native checkout, which returns real fulfillment options per address, (3) partner brands share a Storefront API token. Cost/limits written up in `docs/costs-and-limits.md`. |
| D59 | Welcome rewritten as a card: name + one-line value, a quoted example (`<blockquote>`), 3 numbered steps, a trust line, then "Try an example". No commands mid-sentence (they live in the / menu); neutral example buttons ("Men · Grey turtleneck + blue jeans", "Women · White kurti under ₹1,200", "Hinglish · …"); a tapped example is echoed as a quote before results; the cheaper button reads "Show cheaper options for A". | Keep the chatty paragraph | User feedback on the first screenshot: needed to look more professional, better aligned and better formatted. All 3 examples re-checked live. |
| D60 | **Deploy fixes found on the first Render deploy**: (1) `PUBLIC_BASE_URL` falls back to Render's `RENDER_EXTERNAL_URL`, because the URL only exists after the service is created; (2) the Telegram `secret_token` is the SHA-256 hex of `TELEGRAM_WEBHOOK_SECRET` on both sides, because Render's generated secrets contain characters Telegram rejects. Live: https://drapefit.onrender.com, webhook set (message + callback_query), cron-job.org keep-warm every 10 min + hourly follow-ups. | Ask the user to hand-edit both values | Both failures would hit anyone redeploying from the Blueprint; fixing them in code makes the runbook one pass. |
| D61 | **Public-repo cleanup**: raw Shopify catalog responses (`docs/research/spike-01-raw/`, 20 files, 6 MB, plus `spike-01-sample-response.json`) removed from the repo and gitignored; they stay on the laptop. Tests now use trimmed copies in `tests/fixtures/search/` (all 20 queries, only the fields the code reads, URL tracking parameters stripped: 427 KB). The grounding gate still runs over all 20 queries. Local paths containing the owner's full name replaced with neutral wording in `CLAUDE.md` and `docs/deploy.md`. Option lookups now also accept `"options": null`. | Delete the raw data entirely; make the repo private | Shopify's API terms don't allow redistributing catalog data, and the repo is public. Trimmed, field-limited fixtures keep the tests meaningful. Note: older commits on GitHub still contain the raw files and paths until history is rewritten (needs a force push, owner's call). |
| D62 | **One photo card per suggestion** instead of an album plus a text list. Each card is a `sendPhoto` with the label, title, price, size and brand as an HTML caption, and **+ Add A1** / **Open ↗** buttons on that card; an item header above the cards carries "Show cheaper options for A". A card whose image fails (or has none) arrives as the same text card. Tapping + shows "✓ Added A1" on that card. Telegram 429s are retried once or twice when `retry_after` ≤ 10 s. The grounding gate now checks every card caption and button over all 20 queries (301 tests). | Album + list (Rung 1); label the photos by drawing on them (needs image processing) | User testing on a phone: in an album grid you can't tell which photo is A1..A5, because captions only show full-screen. Cards are the standard shopping layout and tie each photo to its price and buttons. Cost: ~6 messages per item instead of 2, within Telegram's per-chat limits. |
