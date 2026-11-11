# DrapeFit (chat-to-cart shopping agent): project instructions

AI PM portfolio project. The user sends one natural-language message ("grey turtleneck M and blue bell-bottoms waist 32, men, under ₹2,000 each"). The agent returns in-stock options from Indian Shopify D2C brands, the user builds a cart in chat, and checkout hands off to each merchant's payment page.

## Where things live
- `docs/design-doc.md`: the approved design (source of truth for scope, rungs, evals and success criteria)
- `docs/decisions.md`: decision log. **Append every new decision here** (date, decision, alternatives, why).
- `docs/research/`: spike results, API findings, catalog query logs
- Code: `src/drapefit/` (app, bot, shop, checkout, commands, refine, catalog, parser, pipeline, matching, render, store, telegram, models, config). Tests: `tests/`. Evals: `evals/parse/`, `evals/followup/`. Scripts: `scripts/` (smoke, run_local, set_webhook). `src/spike/` is the old spike code, kept for reference.

## Working rules for this project
- All work for this project happens in this folder: `C:\Users\SHUBH SANKALP DAS\Desktop\Building\DrapeFit`.
- After any meaningful discussion or change, update `docs/decisions.md`. Update `docs/design-doc.md` if scope or logic changed.
- Never put API keys in files. Use environment variables (`SARVAM_API_KEY`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`) and a `.env` that is gitignored.
- The LLM never produces prices, sizes or stock in output. Code templates render from API objects.
- The agent never handles payment credentials.

## Current status
- 2026-11-09: Design approved. Spike COMPLETE (a-e): GO on Global Catalog (20/20); cart permalinks work 7/7; Sarvam JSON valid. Channel: Telegram. Next: `/plan-eng-review`.
- 2026-11-10: `/plan-eng-review` DONE (`docs/eng-review-rung1.md`, D23-D36).
- 2026-11-11: **Rung 1 BUILT** (D37-D45). Run tests: `.venv\Scripts\python -m pytest -q` (167, includes grounding gate). Live check: `scripts\smoke.py`. Parse eval: `evals\parse\run.py --cases holdout.jsonl`. Try on Telegram locally: `scripts\run_local.py` (needs `TELEGRAM_BOT_TOKEN`). Deploy: `docs\deploy.md`. Next: user creates the BotFather token, then 5 named testers; T9 self-hosted agent profile.
- User constraint: **₹0 running cost**, and the user delegates routine decisions (pick the recommended option and log it).
- Secrets: `SARVAM_API_KEY` is a Windows user env var (set 2026-11-08). Shopify credentials will be `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` (see `.env.example`). Shopify API facts: `docs/research/shopify-catalog-access.md`.
