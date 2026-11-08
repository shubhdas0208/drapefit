# Shopify Global Catalog: access & API facts (read 2026-11-08 from shopify.dev)

Sources: shopify.dev/docs/agents/catalog, /catalog/global-catalog, /get-started/authentication, /get-started/profile, /get-started/search-catalog, /get-started/checkout, /profiles/auth-and-rate-limiting, /apps/build/dev-dashboard/catalogs

## Getting access

> **UI update (2026-11-08, seen in dashboard):** the sidebar now says **Agents**, not Catalogs. Two parts: **Create a catalog** (which products agents can search) and **Agent identities → New identity** (gives the Client ID + secret and sets the agent name buyers see at checkout). Steps below are from the docs and may be worded differently.
1. Sign in to the Dev Dashboard at https://dev.shopify.com/dashboard/ (this needs a Shopify Partner/dev account).
2. In the sidebar, click **Catalogs**, then **Get an API key**, name it, and click **Create**. Copy the **Client ID** and the **Client secret**.
   - In a Partner org, the page may say "Catalog search is locked". Click **Verify to unlock** to start identity verification.
3. Swap the credentials for a token at runtime. The token expires after 60 minutes.
   ```
   POST https://api.shopify.com/auth/access_token
   {"client_id":"…","client_secret":"…","grant_type":"client_credentials"}
   → {"access_token":"…"}   (scope: read_global_api_catalog_search)
   ```
   Send the token as `Authorization: Bearer <access_token>`.
4. You need an **agent profile URL** on every call (`meta.ucp-agent.profile`). For the spike, Shopify's hosted example works: https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json. Later, host your own JSON (GitHub raw is fine) that declares `dev.ucp.shopping.catalog.search`, `dev.ucp.shopping.catalog.lookup` and `dev.shopify.catalog.global`.

## Endpoint & tools
- `POST https://catalog.shopify.com/api/ucp/mcp` uses JSON-RPC 2.0 (`tools/list`, `tools/call`).
- The tools are `search_catalog`, `lookup_catalog` and `get_product`.
- The `search_catalog` arguments sit under `catalog`:
  - `query`
  - `context` (address_country, postal_code, currency)
  - `filters.ships_to {country:"IN"}`
  - `filters.ships_from [{country:"IN"}]`
  - `filters.price {min,max}` in **minor units** (paise)
  - `filters.attributes [{name:"Color"|"Size"|"Target gender", values:[…]}]`
  - `filters.available`
  - `pagination.limit`, which defaults to 10 and goes up to 50
- Results are clustered by product (UPID) and can include offers from several merchants.

## Variant data (good news for size filtering)
Each variant comes back with:
- `id`, `title`
- `price{amount,currency}` in minor units
- `options[{name:"Size",label:"M"}]`
- `availability{available,status}`
- `checkout_url` (a cart permalink such as `/cart/<variant>:1`)
- `eligible.native_checkout`
- `seller{name,domain,url}`

One caveat: the top-level "options" on search results are *inferred* and may be inaccurate, so rely on the variant-level options.

## Limits & terms
- 5 requests per second per API client, shared across all 3 tools. Going over returns a 429 with Retry-After.
- No cost is documented.
- Price and availability may be cached for at most 10 minutes, and metadata for at most 24 hours. Bulk harvesting, training and redistribution are not allowed (Shopify API Terms).
- Whether India-based developers are eligible is not documented, so the spike will tell us.

## Checkout (Rung 2/3)
- The catalog has no cart tools. It returns `checkout_url` for each variant, and that link is the handoff.
- Merchant UCP endpoint: `https://{shop}/api/ucp/mcp` with `create_cart` and `create_checkout`, which returns `continue_url`. That is the handoff flow.
- `complete_checkout` (paying inside the agent) requires a token "granted the required permission". How to get that permission isn't documented, so treat it as gated.
- Token-tier calls need a `Shopify-Buyer-IP` header.
