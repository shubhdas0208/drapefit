# Costs and rate limits (2026-11-12)

## What you pay

| Service | Plan | Cost |
|---|---|---|
| Telegram Bot API | free | ₹0 |
| Shopify Global Catalog | free (no cost documented) | ₹0 |
| Render web service | free | ₹0 |
| Neon Postgres | free (0.5 GB) | ₹0 |
| cron-job.org | free | ₹0 |
| **Sarvam LLM** | pay per token, from your existing credits | **the only variable cost** |

### Sarvam, measured

Official Sarvam 105B chat pricing (docs.sarvam.ai/api/pricing): ₹29.28 per 1M input tokens, ₹10.98 per 1M
cached input tokens, ₹73.20 per 1M output tokens. Assumption: `sarvam-105b-conversations` is billed like
Sarvam 105B. Check the dashboard after the first tester week.

| Call | Tokens (measured 2026-11-12) | Cost per call |
|---|---|---|
| Parse a new request | 1,254 in (1,248 cacheable system prompt) + ~224 out | ₹0.053 first call, ~₹0.030 when the prompt is cached |
| Follow-up classifier ("same but black") | ~1,000 in + ~80 out (estimate, same prompt shape) | ~₹0.035, ~₹0.017 cached |
| Cart buttons, "add A1", 💸 Cheaper button, checkout, follow-ups | no LLM call | ₹0 |

A typical session (1 search + 2 follow-ups) costs **about ₹0.06 to ₹0.12**.
30 testers × 10 sessions each ≈ 300 sessions ≈ **₹20 to ₹40 in total**.

`parsed` and `follow_up_classified` events record model and latency for every call, so the real cost
can be checked from the event log.

## Rate limits that matter

| Limit | Value | What it means for DrapeFit |
|---|---|---|
| Shopify Catalog | 5 requests/s per client, shared by all users | **The real bottleneck.** A 2-item search ≈ 2 searches + up to 16 product checks (~2.5-3.5 s of budget). About 2 people can search at the same moment at full speed; more just get slower replies (the limiter queues, 429s are retried). Checkout uses 1 call per cart line. |
| Telegram, per chat | about 1 message/s sustained, short bursts allowed | A 2-item search sends ~7 messages + 2 edits over ~5 s: fine. |
| Telegram, global | about 30 messages/s across all chats | Fine until hundreds of simultaneous users. |
| Telegram, callback answers | must be answered within ~15 s | Slow buttons (checkout) are answered first, then the work runs (D55). |
| Sarvam | per-plan limits, shown on dashboard → Rate Limits | Not published as numbers; check before inviting more than ~20 testers. |
| Render free | 750 instance-hours/month, sleeps after 15 min idle, may restart | One service always on fits. The keep-warm ping stops sleeping. |
| Neon free | 0.5 GB storage, compute sleeps when idle | ~1 s extra on the first query after idle. Events are pruned after 90 days. |

## When costs would start

- More than ~2 concurrent searchers regularly: ask Shopify for a higher catalog limit (no paid tier documented).
- Render restarts losing in-flight searches becomes visible in events: Render Starter ($7/month) or a worker queue.
- Sarvam spend: at ~₹0.1 per session, 10,000 sessions/month ≈ ₹1,000.
