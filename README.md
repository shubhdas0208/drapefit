# DrapeFit

**One message in, a cart out.** DrapeFit is a Telegram shopping agent for Indian D2C fashion. You send it one sentence:

> grey turtleneck M and blue bell-bottoms waist 32, men, under ₹2,000 each

It replies with in-stock options in your size from Indian Shopify brands. You build a cart by tapping buttons or by typing "add A1 B3" or "same jeans but in black". Checkout hands you off to each brand's own Shopify checkout page. The agent never handles payment details, and the language model never writes a price, size or stock value.

Code, tests, evals, two code reviews and a live end-to-end run against real merchants took **two days of build** (Nov 11 and 12). Problem selection, a gating spike and an architecture review came first (Nov 8 to 10). Every call along the way is written down in [`docs/decisions.md`](docs/decisions.md).

→ **Read the case study: [`docs/case-study.md`](docs/case-study.md)**

---

## What was shipped

| | |
|---|---|
| **Working bot** | Telegram bot: search, cart, change a search in plain language, per-brand checkout handoff, "did you buy?" follow-up 24 h later |
| **59 logged decisions** | [`docs/decisions.md`](docs/decisions.md): each one lists the alternatives considered and why it was chosen. Some reverse earlier ones after live data disagreed (D38, D39). |
| **Held-out evals** | Parser **14/15 (93%)** on unseen cases, target ≥ 90%. Follow-up classifier **12/12** on unseen cases, two runs. Both prompts were tuned on separate sets. |
| **Grounding gate** | A test that fails the build if any price, size or stock value shown differs from the raw catalog object. Runs over all 20 spike queries. Result: 100%. |
| **275 tests, 95% coverage** | Unit, integration and grounding tests, offline, on recorded catalog fixtures |
| **Two code reviews** | Rung 1: 0 critical, 2 high, both fixed with regression tests. Rung 2: 0 critical, 0 high, 7 medium, 3 low, all fixed with regression tests. |
| **Live end-to-end test** | Real catalog, real LLM, real stores: search → "add A1 A2 B1" → "same jeans but in black" → checkout → **2/2 cart permalinks reached Shopify checkout** → follow-up sent |
| **₹0 to run** | Telegram, Shopify Global Catalog, Render free, Neon free, cron-job.org. The only variable cost is the LLM, measured at **₹0.06 to ₹0.12 per session**. |

## How it works

1. **Parse** (the only LLM step on the request path). Sarvam `sarvam-105b-conversations` turns the message into a typed `RequestSpec`: items with category, colour, size, gender and price cap. Pydantic validates it, with one retry. If a size is ambiguous ("size 30" for a top), the bot asks instead of guessing. p95 under 0.6 s.
2. **Search.** One Shopify Global Catalog search per item, in parallel: ships to India, ships from India, in stock, INR price cap.
3. **Filter in code.** Exact INR price check, a colour synonym table, at most 3 options per brand.
4. **Verify the size.** `get_product` with the store's own size label, in rank order, until 5 options pass. If the bot can't read a size label, it drops that option rather than show a wrong size.
5. **Render from API objects only.** An `Option` is a frozen dataclass built from the catalog variant, and the renderer accepts nothing else. The LLM never sees this path, which is why grounding can be a test and not a hope.
6. **Refine.** Buttons and a strict grammar ("add A1 B3", "remove B2", "checkout") run in code, with no LLM. Free text after a search ("same but black", "kuch sasta dikhao") goes to a small follow-up classifier (~0.3 s). Code applies the change and re-searches only the item that changed.
7. **Checkout.** The cart is grouped by brand, and every line is re-checked live (no cache) just before handoff. You get one **Pay on <brand> · ₹X** button per brand, linking to a Shopify cart permalink. Price changes are flagged, and sold-out lines are removed and named.
8. **Measure.** The bot writes every step to an append-only event log: parsed, results shown, cart add, checkout sent, link clicked, purchase reported, and the reason when someone didn't buy.

## Evals

| Eval | Set | Result | Target | Files |
|---|---|---|---|---|
| Request parsing | 50 tuning + **15 held-out** | 49/50 tuning, **14/15 held out**, missed clarifications 0% | ≥ 90% exact | [`evals/parse/`](evals/parse/) |
| Follow-up classifier | 39 tuning + **12 held-out** | 92-95% tuning, **12/12 held out** (two runs), p50 311 ms | n/a | [`evals/followup/`](evals/followup/) |
| Grounding | 20 real catalog queries | **100%**: anything lower fails the build | 100% | [`tests/test_render.py`](tests/test_render.py) |

The parse eval set was written **before** the parser prompt (D36). Only the held-out numbers count as accuracy estimates. The saved held-out file for the chosen model shows 13/15, because it was graded before item matching became order-insensitive (D43). Re-grading the same saved outputs with the current grader gives 14/15.

The parser model was chosen on the held-out set. `sarvam-105b` also scored 14/15, but at p95 31 s against 577 ms (D37), and 31 s alone breaks the 8 s reply budget.

## Run it

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m pytest -q           # 275 tests, offline
```

To talk to the bot on your laptop you need a BotFather token, a Sarvam key and Shopify agent credentials (names in [`.env.example`](.env.example)):

```bash
.venv/Scripts/python scripts/run_local.py   # Telegram long polling + local SQLite
.venv/Scripts/python scripts/smoke.py       # live search against the real catalog
.venv/Scripts/python scripts/smoke_rung2.py # live cart → checkout → follow-up loop
.venv/Scripts/python evals/parse/run.py --cases holdout.jsonl
```

To deploy on free tiers (Render + Neon + cron-job.org), see [`docs/deploy.md`](docs/deploy.md). For costs and rate limits, see [`docs/costs-and-limits.md`](docs/costs-and-limits.md).

## Repo map

| Path | What's there |
|---|---|
| [`docs/case-study.md`](docs/case-study.md) | The story: problem, user, metric, MVP, risks, what the data changed |
| [`docs/design-doc.md`](docs/design-doc.md) | The approved design: rungs, evals, success criteria |
| [`docs/decisions.md`](docs/decisions.md) | D1-D59, each with alternatives and reasons |
| [`docs/eng-review-rung1.md`](docs/eng-review-rung1.md) | Architecture lock: pipeline, data model, failure modes, test plan |
| [`docs/research/`](docs/research/) | Spike write-ups, API findings, 20 raw catalog responses |
| [`src/drapefit/`](src/drapefit/) | The bot: `app` (FastAPI webhook), `bot`, `parser`, `pipeline`, `catalog`, `matching`, `render`, `shop`, `checkout`, `refine`, `commands`, `store`, `telegram`, `models`, `config` |
| [`tests/`](tests/) | 275 tests and recorded `get_product` fixtures |
| [`evals/`](evals/) | Parse and follow-up evals: cases, held-out cases, graders, runners, saved results |
| [`scripts/`](scripts/) | Local runner, live smoke tests, webhook setup, fixture recorder |

## Status and what's next

- **Built:** Rung 1 (search and shortlist) and Rung 2 (cart in chat, checkout handoff, purchase follow-up).
- **Next:** deploy to Render + Neon, then put it in front of 5 named testers. The success bar: 5 real users with at least one real request each, and at least 2 confirmed purchases from an agent link.
- **Not built, on purpose:** in-chat payment. No Indian merchant in the sample supports Shopify native checkout (`native_checkout=false` on every variant checked), so handoff is the only honest path. Delivery dates per pincode aren't shown, because the catalog doesn't provide them, and an estimated number would break the grounding rule (D58).
