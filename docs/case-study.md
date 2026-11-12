# Case study: DrapeFit, chat-to-cart for Indian D2C fashion

*Solo AI PM-builder project. Built Nov 8-12: three days of problem selection, a gating spike and an architecture review, then two days of build. The build days produced a working Telegram bot, 275 tests, two held-out evals, two code reviews and a live end-to-end run against real merchants. Every decision is logged with its alternatives in [`decisions.md`](decisions.md).*

---

## 1. Problem

A man in Bengaluru wants "a grey turtleneck, size M, and blue bell-bottoms, waist 32, under ₹2,000 each." Today he opens Myntra, Amazon and three or four D2C brand sites he saw on Instagram. Every site has its own filters, and size availability only shows up after clicking into each product. Checkout happens separately on each site. One multi-item request with sizes and a price cap per item takes **30-60 minutes across 5+ tabs**.

What breaks today:
- **Size is hidden.** Marketplaces show a product before they show whether your size is in stock.
- **D2C brands are fragmented.** Many Indian Shopify brands aren't on Myntra or Amazon at all.
- **A multi-item request is several separate searches.** No tool takes the whole sentence at once.

## 2. User

**A young professional in an Indian metro, 22-32, who already buys from Instagram D2C brands.** He knows exactly what he wants (item, colour, size, budget) and hates the tab-juggling more than he enjoys browsing. Testers come from a reachable pool of colleagues, friends and students (D2).

He is not a "browse for inspiration" shopper. Approach A, a discovery-only gift finder, was rejected for that reason (D7). Discovery without a cart doesn't finish his job.

## 3. Metric

**North star: confirmed purchases from an agent link, counted per merchant.** With N brands in a cart there are N separate payments, so the bot measures completion per merchant, not per cart. Drop-off after the first payment is expected and should be visible.

The funnel is instrumented from day one in an append-only event log (D22): `request_received → parsed → results_shown → cart_add → checkout_sent → link_clicked → purchase_reported`, plus `purchase_blocker` with a reason (price, shipping, size doubt, didn't like it). A "did you buy?" message 24 h after checkout closes the loop that a click-out alone can't (D51).

Guardrails that block shipping:
| Guardrail | Bar | Result |
|---|---|---|
| Grounding: prices, sizes and stock shown equal the API objects | 100%, enforced by a test | 100% over all 20 spike queries |
| Parse accuracy on held-out requests | ≥ 90% exact match | 14/15 (93%) |
| Reply latency | p95 < 8 s | ~6 s for the canonical 2-item request, live |
| Running cost | ₹0 fixed | ₹0 fixed, ₹0.06-0.12 per session in LLM tokens |

Success bar for the tester round: 5 named users with at least one real request each, and at least 2 confirmed purchases, within 3 weeks.

## 4. MVP: ladders, not a big bang

The design ships in **rungs**, and each one reaches real users before the next starts:

- **Rung 1, search and shortlist.** One sentence in, 5 in-stock, in-size options per item, with links to the store. This tests the riskiest assumption: *can an agent find the right thing, in the right size, from Indian brands, quickly enough?*
- **Rung 2, cart and checkout handoff.** Build a cart in chat, get one checkout button per brand, then the "did you buy?" follow-up.
- **Rung 3 (optional), full in-chat payment** on a test store. Kept behind a gate, because no real Indian merchant supports native checkout today.

Rungs 1 and 2 are built.

### What it does

```
you   grey turtleneck M and blue bell-bottoms waist 32, men, under ₹2,000 each
bot   Searching 2 items… A ✓ B ✓
      A · Grey turtleneck · M      A1-A5  (photos, brand, ₹ price, + button)
      B · Blue bell-bottoms · 32   B1-B5
you   add A1 B3
you   same jeans but in black          ← re-searches only B
you   checkout
bot   [Pay on <brand 1> · ₹X]    [Pay on <brand 2> · ₹Y]
      (stock and price re-checked live; delivery date for your pincode shows on the brand's checkout)
```

## 5. Risk, and how each one was retired

The design doc named every open risk with a go/no-go test. A gating spike ran those tests before any product code was written.

| Risk | What would kill it | Test | Result |
|---|---|---|---|
| Catalog coverage in India | Fewer than 30% of queries get 3+ relevant, in-stock, Indian-shipping hits. Fallback: hand-curate 20-30 stores | 20 real queries, scripted, raw responses saved | **20/20 (100%)** with ships-from-India. GO on Shopify Global Catalog, curated index dropped (D15) |
| Checkout | No way to hand a multi-item cart to a merchant | Cart permalinks on real Indian stores | **7/7 reached Shopify checkout** (D19) |
| Native in-chat payment | Assumed impossible for Indian D2C, because third-party checkouts are common | `native_checkout` flag on every sampled variant | `false` everywhere, so handoff is the plan, not a fallback |
| LLM output reliability | Invalid JSON, Hinglish breaking the parser | 10 requests, then a 50 + 15 case eval | Valid. Held out 14/15 |
| Hallucinated prices or sizes | One wrong ₹ figure destroys trust | Architecture: no LLM in the output path, plus a grounding test | 100%, and any regression fails the build |
| WhatsApp account ban | Bot gets blocked mid-test | Channel choice | Telegram instead (D21) |

## 6. Where the data changed the plan

The decision log is most useful where it shows a plan meeting reality and changing. These changes came from evidence, not opinion:

- **The price filter bug and its reversal.** The spike found that the catalog's price filter "was applied on a USD basis": ₹2,000 sent in paise filtered at $2,000 and did nothing. The plan became "convert to USD with 15% headroom" (D17, D33). During the build, the spike's raw data showed the API converting INR itself (`fx_converted`, ₹2,000 → $21.21). D39 reversed the FX constant. The exact INR check in code stayed, because the worst overshoot seen was ₹3,505 on a ₹3,500 cap.
- **Strict JSON mode lost to plain JSON mode.** Strict `json_schema` output looked safest, but it produced runaway whitespace on 4 of 6 Hinglish inputs. `json_object` plus Pydantic validation was 6/6 (D38).
- **The model was chosen on latency, and on a held-out set.** `sarvam-105b` and `sarvam-105b-conversations` both scored 14/15 held out. One had a p95 of 31 s, the other 577 ms (D37). The 8 s budget made the decision.
- **The "live" check that wasn't live.** The first end-to-end run finished checkout in 31 ms, fast because the cache had answered. Checkout now bypasses the product cache, so the re-check really is fresh (D53).
- **A secret in the logs.** On the first local run, httpx's INFO logs printed Telegram URLs, which contain the bot token. The logger was turned down, and the token was rotated (D46).
- **A silent wrong answer.** Selecting "XXL" on a store that labels it "2XL" silently returns *all* variants. The bot now reads the store's own label, verifies the variant it gets back, and fails closed on a label it can't read (D34, D40).
- **Saying no to a number we can't ground.** Delivery dates per pincode are the obvious next ask. A live probe showed the catalog returns no fulfillment data for Indian merchants (D58). The bot links each brand's shipping policy and says the date appears at checkout. It doesn't invent an estimate.

## 7. Technical choices, with alternatives

| Choice | Picked | Alternatives | Trade-off |
|---|---|---|---|
| Product data | **Integrate** Shopify Global Catalog | Build a curated `/products.json` index; scrape marketplaces | The spike showed coverage. Integrating keeps stock and price live, where an index goes stale overnight |
| Where the LLM sits | **Input only** (parse, classify) | LLM writes the reply | Gives up chattiness, gains provable grounding |
| Message routing | **Cheapest first**: buttons and a strict grammar in code, an LLM only for language (D47) | One LLM call per message | "add A1" is deterministic, so code is free, instant and never wrong |
| Follow-ups | **Separate classifier with its own eval**; code applies the change (D48) | Extend the parse schema; let the LLM rewrite the spec | Keeps the Rung 1 eval stable, and the LLM never computes "cheaper" |
| Checkout | **One permalink per merchant**, re-checked live (D50) | Catalog `checkout_url` (one variant only); UCP sessions (unsupported) | N payments for N brands, measured per merchant |
| Hosting | **Render free + Neon free**, kept warm by cron (D24-D26) | Fly.io + volume (~$2-5/mo); laptop + tunnel | ₹0. Render's disk is wiped on restart, so all state lives in Postgres |

**LLM limits treated as product constraints.** Hallucination is handled by architecture, not by prompting. Latency picked the model. Prompt brittleness is handled with held-out sets and a stop rule: tuning on the follow-up classifier stopped after 3 rounds, because run-to-run variance at temperature 0 was as large as the remaining misses (D52). Cost is handled by the router, and a typical session is ₹0.06-0.12.

## 8. How the work was checked

- **Evals before prompts.** The 50-request parse set was written before the parser prompt existed (D36). Held-out sets were never used for tuning.
- **A grounding gate.** `tests/test_render.py` runs the real pipeline over all 20 recorded catalog responses, renders the Telegram text, parses it back and compares every price, size, seller and link with the raw variant.
- **275 tests, 95% coverage**, offline on recorded fixtures.
- **Two code reviews.** Rung 1: 0 critical and 2 high issues. Rung 2: 0 critical, 0 high, 7 medium and 3 low (D45, D55). Every fix shipped with a regression test: double follow-up pings from overlapping cron runs, a stale "Not yet" tap reopening an answered follow-up, one failing item dropping the others, and more.
- **A live end-to-end run** (`scripts/smoke_rung2.py`) against the real catalog, the real LLM and real stores: search → "add A1 A2 B1" → "same jeans but in black" (only B re-searched) → checkout with 2 merchant links (one carrying 2 variants) → **both landed on a Shopify checkout** → follow-up sent (D54).

## 9. Timeline

| Day | Work | Decisions |
|---|---|---|
| Nov 8 | Problem selection: dropped a WhatsApp RFQ idea and four fintech ideas, parked an MF screener, chose shopping and approach B. Wrote and approved the design doc. Shopify agent onboarding | D1-D14 |
| Nov 9 | Gating spike: 20 catalog queries (20/20), checkout permalinks (7/7), LLM JSON check | D15-D22 |
| Nov 10 | Engineering review: pipeline, data model, failure modes and test plan locked. Parse eval written | D23-D36 |
| **Nov 11** | **Rung 1 built**: parser, catalog client, pipeline, grounding gate, Telegram bot, parse eval 14/15 held out, first code review | D37-D46 |
| **Nov 12** | **Rung 2 built**: cart, checkout handoff, follow-up classifier (12/12 held out), live end-to-end run, second code review, UX pass, free-tier deploy config | D47-D59 |

## 10. What I'd revisit

- **Rung 2 was built ahead of its own gate.** The design said to build the cart only after 3 of 5 testers asked "can I just buy it from here?" It was built before any tester saw Rung 1, so the first tester round now tests both rungs at once. The event log separates the two funnels, which limits the damage, but it's the gate I'd hold next time.
- **Relevance is measured by humans and isn't automated yet.** Precision@5 needs tester ratings, and they are the next data to collect.
- **One known classifier miss.** "Women's version of the turtleneck" sometimes adds a product-type change. It is logged and left unfixed, so the classifier doesn't overfit to that one phrasing.

## 11. Next

Deploy (Render + Neon + cron-job.org, ~30 minutes, [`deploy.md`](deploy.md)), then 5 named testers. The questions their data answers: does the size-aware shortlist beat the tabs, and where does the per-merchant checkout lose people?
