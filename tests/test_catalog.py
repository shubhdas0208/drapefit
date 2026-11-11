import asyncio
import json

import httpx
import pytest
import respx

from drapefit import config
from drapefit.catalog import CatalogClient, CatalogError, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def ok(result: dict) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": result}})


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make(http, clock=None, rps=1000.0):
    return CatalogClient(http, "id", "secret", "https://profile.example/p.json", rps=rps, clock=clock or Clock())


@respx.mock
async def test_search_sends_india_filters_and_inr_cap(http):
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    mcp = respx.post(config.CATALOG_MCP_URL).mock(return_value=ok({"products": [{"id": "p1"}]}))
    products = await make(http).search("grey turtleneck men", 2000, "men")
    assert products == [{"id": "p1"}]
    body = json.loads(mcp.calls[0].request.content)
    args = body["params"]["arguments"]
    assert args["meta"]["ucp-agent"]["profile"] == "https://profile.example/p.json"
    f = args["catalog"]["filters"]
    assert f["ships_to"] == {"country": "IN"} and f["ships_from"] == [{"country": "IN"}]
    assert f["price"] == {"max": 200000}
    assert f["attributes"] == [{"name": "Target gender", "values": ["Male"]}]
    assert args["catalog"]["context"] == {"address_country": "IN", "currency": "INR"}
    assert mcp.calls[0].request.headers["Authorization"] == "Bearer T"


@respx.mock
async def test_unisex_or_no_cap_omit_filters(http):
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    mcp = respx.post(config.CATALOG_MCP_URL).mock(return_value=ok({"products": []}))
    await make(http).search("hoodie", None, "unisex")
    f = json.loads(mcp.calls[0].request.content)["params"]["arguments"]["catalog"]["filters"]
    assert "price" not in f and "attributes" not in f


@respx.mock
async def test_429_waits_retry_after_then_succeeds(http, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("drapefit.catalog.asyncio.sleep", fake_sleep)
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    respx.post(config.CATALOG_MCP_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), ok({"products": []})]
    )
    await make(http).search("x", None, None)
    assert 2.0 in slept


@respx.mock
async def test_persistent_errors_raise(http):
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    respx.post(config.CATALOG_MCP_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(CatalogError):
        await make(http).search("x", None, None)


@respx.mock
async def test_jsonrpc_error_raises(http):
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    respx.post(config.CATALOG_MCP_URL).mock(return_value=httpx.Response(200, json={"error": {"code": -32602}}))
    with pytest.raises(CatalogError):
        await make(http).search("x", None, None)


@respx.mock
async def test_auth_failure_raises(http):
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(CatalogError, match="auth"):
        await make(http).search("x", None, None)


@respx.mock
async def test_token_refreshed_once_for_concurrent_calls_and_after_expiry(http):
    clock = Clock()
    auth = respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    respx.post(config.CATALOG_MCP_URL).mock(return_value=ok({"products": []}))
    client = make(http, clock)
    await asyncio.gather(*(client.search(f"q{i}", None, None) for i in range(5)))
    assert auth.call_count == 1
    clock.t += config.TOKEN_REFRESH_SECONDS + 1
    await client.search("later", None, None)
    assert auth.call_count == 2


@respx.mock
async def test_401_forces_token_refresh(http):
    auth = respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    respx.post(config.CATALOG_MCP_URL).mock(side_effect=[httpx.Response(401), ok({"products": []})])
    await make(http).search("x", None, None)
    assert auth.call_count == 2


@respx.mock
async def test_get_product_cached_for_ten_minutes(http):
    clock = Clock()
    respx.post(config.SHOPIFY_AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "T"}))
    mcp = respx.post(config.CATALOG_MCP_URL).mock(return_value=ok({"product": {"id": "p1"}}))
    client = make(http, clock)
    sel = [{"name": "Size", "label": "M"}]
    assert await client.get_product("p1", sel) == {"id": "p1"}
    await client.get_product("p1", sel)
    assert mcp.call_count == 1
    await client.get_product("p1", [{"name": "Size", "label": "L"}])  # different key
    assert mcp.call_count == 2
    clock.t += config.PRODUCT_CACHE_SECONDS + 1
    await client.get_product("p1", sel)
    assert mcp.call_count == 3
    sent = json.loads(mcp.calls[0].request.content)["params"]["arguments"]["catalog"]
    assert sent["selected"] == sel


async def test_rate_limiter_spaces_calls_across_tasks(monkeypatch):
    clock = Clock()
    slept = []

    async def fake_sleep(s):
        slept.append(round(s, 3))

    monkeypatch.setattr("drapefit.catalog.asyncio.sleep", fake_sleep)
    limiter = RateLimiter(5, clock)
    await asyncio.gather(*(limiter.wait() for _ in range(4)))
    assert sorted(slept) == [0.2, 0.4, 0.6]


async def test_fresh_get_product_bypasses_cache():
    import httpx as _httpx
    import respx as _respx

    from drapefit import config as _config
    from drapefit.catalog import CatalogClient as _Client

    calls = []

    with _respx.mock:
        _respx.post(_config.SHOPIFY_AUTH_URL).mock(
            return_value=_httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))

        def reply(request):
            calls.append(request)
            return _httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {
                "structuredContent": {"product": {"id": "p", "variants": []}}}})

        _respx.post(_config.CATALOG_MCP_URL).mock(side_effect=reply)
        async with _httpx.AsyncClient() as http:
            client = _Client(http, "id", "secret", "https://profile")
            await client.get_product("p")
            await client.get_product("p")  # cached
            await client.get_product("p", fresh=True)  # live
    assert len(calls) == 2
