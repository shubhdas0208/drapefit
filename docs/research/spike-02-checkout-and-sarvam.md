# Spike 02: checkout permalinks (d) + Sarvam JSON (e)

Date: 2026-11-09

## (d) Cart permalinks on real Indian Shopify stores: PASS (7/7)

Method: took one in-stock variant per distinct Indian seller from the spike-01 `india` results, requested
`https://<store>/cart/<variant_id>:1` without following redirects, and checked where it lands.
Also tested the catalog's own `variants[].checkout_url` (`/buy/<variant_id>:1`).

| Store | `/cart/<id>:1` | catalog `/buy/<id>:1` |
|---|---|---|
| offduty.in (manual, in browser by user) | Lands on Shopify checkout, correct variant (Classic Loose Straight Fit Jeans, Pale Blue / 30, ₹1,590) | not tested |
| 98degreenorth.com | 302 → `/checkouts/cn/...` | 303 → `/cart/c/...` |
| cahoot.in | 302 → `/checkouts/cn/...` | 303 → `/cart/c/...` |
| thomasscott.in | 302 → `/checkouts/cn/...` | 303 → `/cart/c/...` |
| tigc.in | 302 → `/account/login?checkout_url=/checkouts/cn/...` (login wall, then checkout) | 303 → `/cart/c/...` |
| crimsouneclub.com | 302 → `/checkouts/cn/...` | 303 → `/cart/c/...` |
| celsiusclothing.com | 302 → `/checkouts/cn/...` | 303 → `/cart/c/...` |

Findings:
- Cart permalinks work server-side on every store tested. One store (TIGC) forces login before checkout.
  That is merchant friction, not a broken link.
- The catalog returns a ready `checkout_url` per variant (with UCP attribution params). It also resolves.
- Every sampled variant has `eligible.native_checkout = false`. That confirms handoff is the only path for now.
- Caveat: third-party checkouts (GoKwik, Shiprocket, Razorpay Magic) hook in through client-side JS on the
  checkout page. A redirect probe can't see them. The manual Off Duty run showed native Shopify checkout.
  Watch for this in real use; the link still opens the merchant's own payment flow either way.
- Not yet tested: a multi-variant permalink (`/cart/a:1,b:2`) in one store. Test it during the build.

## (e) Sarvam `sarvam-105b` JSON-schema output: PASS (user-reported)

The user ran the parse requests and all came back as valid JSON for the schema.
Raw requests/responses were not saved to this folder. If the parser misbehaves later, rerun the 10 cases
(Hinglish included) and save them here as an eval seed.
