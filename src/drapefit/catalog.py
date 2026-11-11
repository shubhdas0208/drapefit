"""Async Shopify Global Catalog MCP client (decision D31).

- One rate limiter per client instance, shared by every user: 5 req/s per API client.
- Token refresh behind a lock so a burst of calls triggers one refresh.
- 429 -> sleep Retry-After and retry (max 2 retries).
- get_product responses cached in memory for 10 minutes (ToS maximum).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)

IN_CONTEXT = {"address_country": "IN", "currency": "INR"}
GENDER_TAXONOMY = {"men": "Male", "women": "Female"}
MAX_RETRIES = 2
MAX_CACHE_ENTRIES = 2000


class CatalogError(RuntimeError):
    pass


class RateLimiter:
    """Spaces calls at least 1/rps apart across all concurrent tasks."""

    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = 1.0 / rps
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


class CatalogClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        agent_profile_url: str,
        rps: float = config.CATALOG_MAX_RPS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._profile = agent_profile_url
        self._limiter = RateLimiter(rps, clock)
        self._clock = clock
        self._token: str | None = None
        self._token_at = 0.0
        self._token_lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._next_id = 0

    async def _token_header(self) -> dict[str, str]:
        async with self._token_lock:
            stale = self._clock() - self._token_at > config.TOKEN_REFRESH_SECONDS
            if self._token is None or stale:
                resp = await self._http.post(
                    config.SHOPIFY_AUTH_URL,
                    json={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "client_credentials",
                    },
                )
                if resp.status_code != 200 or "access_token" not in resp.json():
                    raise CatalogError(f"Shopify auth failed: HTTP {resp.status_code}")
                self._token = resp.json()["access_token"]
                self._token_at = self._clock()
        return {"Authorization": f"Bearer {self._token}"}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": {"meta": {"ucp-agent": {"profile": self._profile}}, **arguments},
            },
        }
        for attempt in range(MAX_RETRIES + 1):
            await self._limiter.wait()
            resp = await self._http.post(
                config.CATALOG_MCP_URL, json=payload, headers=await self._token_header()
            )
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                await asyncio.sleep(float(resp.headers.get("Retry-After", "1")))
                continue
            if resp.status_code == 401 and attempt < MAX_RETRIES:
                self._token = None  # force refresh
                continue
            if resp.status_code != 200:
                raise CatalogError(f"{name}: HTTP {resp.status_code}")
            body = resp.json()
            if "error" in body:
                raise CatalogError(f"{name}: {body['error']}")
            return body.get("result", {}).get("structuredContent", {})
        raise CatalogError(f"{name}: retries exhausted")

    async def search(
        self, query: str, max_inr: int | None, gender: str | None, limit: int = config.SEARCH_LIMIT
    ) -> list[dict]:
        filters: dict[str, Any] = {
            "ships_to": {"country": "IN"},
            "ships_from": [{"country": "IN"}],  # D16
            "available": True,
        }
        if max_inr is not None:
            # The API converts INR to USD itself (spike data: Rs 2000 -> $21.21).
            # The exact INR check still happens in code.
            filters["price"] = {"max": max_inr * 100}
        if gender in GENDER_TAXONOMY:
            filters["attributes"] = [{"name": "Target gender", "values": [GENDER_TAXONOMY[gender]]}]
        result = await self.call_tool(
            "search_catalog",
            {
                "catalog": {
                    "query": query,
                    "context": IN_CONTEXT,
                    "filters": filters,
                    "pagination": {"limit": limit},
                }
            },
        )
        return result.get("products", [])

    async def get_product(self, product_id: str, selected: list[dict] | None = None, fresh: bool = False) -> dict:
        """fresh=True skips the cache (checkout re-check must see live stock and price)."""
        key = json.dumps([product_id, selected], sort_keys=True)
        cached = self._cache.get(key)
        if not fresh and cached and self._clock() - cached[0] < config.PRODUCT_CACHE_SECONDS:
            return cached[1]
        catalog: dict[str, Any] = {"id": product_id, "context": IN_CONTEXT}
        if selected:
            catalog["selected"] = selected
        result = await self.call_tool("get_product", {"catalog": catalog})
        product = result.get("product", {})
        self._prune_cache()
        self._cache[key] = (self._clock(), product)
        return product

    def _prune_cache(self) -> None:
        if len(self._cache) < MAX_CACHE_ENTRIES:
            return
        now = self._clock()
        self._cache = {
            k: v for k, v in self._cache.items() if now - v[0] < config.PRODUCT_CACHE_SECONDS
        }
