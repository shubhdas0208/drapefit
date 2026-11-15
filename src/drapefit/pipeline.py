"""Retrieval pipeline for one item. No Telegram, DB or LLM imports.

    ItemSpec
      |
      v
    search_catalog (ships_from=IN, INR cap, gender)        1 call
      |
      v
    hard filters on the search hit: INR, price <= cap, colour     (code, D5)
      |
      v
    seller cap: max 3 per seller, keep catalog relevance order
      |
      v
    for each candidate, in rank order, until 5 options or 8 detail calls:
      size asked?  no  -> use the search hit's variant as-is (already in stock)
                   yes -> store label from hit options (or a plain get_product
                          when the hit lists no sizes) -> get_product(selected)
                          -> verify size label, availability, INR, price <= cap
      |
      v
    Option objects, copied field-by-field from API variant objects (D30)
"""
from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config, matching
from .models import ItemResult, ItemSpec, Option

log = logging.getLogger(__name__)


class Catalog(Protocol):
    async def search(self, query: str, max_inr: int | None, gender: str | None) -> list[dict]: ...

    async def get_product(
        self, product_id: str, selected: list[dict] | None = None, fresh: bool = False
    ) -> dict: ...


EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _no_events(_type: str, _payload: dict[str, Any]) -> None:
    return None


@dataclass
class PipelineStats:
    search_hits: int = 0
    after_hard_filters: int = 0
    after_seller_cap: int = 0
    detail_calls: int = 0
    unmapped_sizes: list[str] = field(default_factory=list)


def build_query(item: ItemSpec) -> str:
    parts = [item.color, item.fit, item.query_terms]
    if item.gender in ("men", "women"):
        parts.append(item.gender)
    return " ".join(p for p in parts if p)


def _option_label(variant: dict, name: str) -> str | None:
    for opt in variant.get("options") or []:
        if opt.get("name", "").strip().lower() == name:
            return opt.get("label")
    return None


def _product_size_labels(product: dict) -> list[str]:
    for opt in product.get("options") or []:
        if opt.get("name", "").strip().lower() == "size":
            return [v.get("label") for v in opt.get("values", []) if v.get("label")]
    return []


def _available_size_labels(product: dict) -> list[str]:
    for opt in product.get("options") or []:
        if opt.get("name", "").strip().lower() == "size":
            return [
                v["label"]
                for v in opt.get("values", [])
                if v.get("label") and v.get("available", True) and v.get("exists", True)
            ]
    return []


def variant_passes(variant: dict, cap_inr: int | None) -> bool:
    """Hard filter: in stock, priced in INR, at or under the cap (exact, in paise)."""
    price = variant.get("price") or {}
    if not (variant.get("availability") or {}).get("available"):
        return False
    if price.get("currency") != "INR" or not isinstance(price.get("amount"), int):
        return False
    return cap_inr is None or price["amount"] <= cap_inr * 100


def hit_passes(product: dict, item: ItemSpec, cap_inr: int | None) -> bool:
    variants = product.get("variants") or []
    if not variants or not variant_passes(variants[0], cap_inr):
        return False
    return matching.colour_matches(
        item.color, _option_label(variants[0], "color"), product.get("title")
    )


def seller_key(product: dict) -> str:
    seller = ((product.get("variants") or [{}])[0]).get("seller") or {}
    return seller.get("url") or seller.get("domain") or seller.get("name") or "?"


def cap_per_seller(products: list[dict], limit: int = config.MAX_PER_SELLER) -> list[dict]:
    counts: dict[str, int] = {}
    kept = []
    for product in products:
        key = seller_key(product)
        if counts.get(key, 0) < limit:
            counts[key] = counts.get(key, 0) + 1
            kept.append(product)
    return kept


def make_option(label: str, item_index: int, product: dict, variant: dict) -> Option:
    seller = variant.get("seller") or {}
    media = (variant.get("media") or product.get("media") or [{}])[0]
    return Option(
        label=label,
        item_index=item_index,
        product_id=product["id"],
        variant_id=variant["id"],
        # Product title first: variant titles are often just option values ("Black / XL").
        title=html.unescape(product.get("title") or variant.get("title") or ""),
        seller_name=seller.get("name") or "?",
        seller_url=seller.get("url") or "",
        seller_domain=seller.get("domain") or "",
        price_paise=variant["price"]["amount"],
        currency=variant["price"]["currency"],
        size_label=_option_label(variant, "size"),
        color_label=_option_label(variant, "color"),
        available=bool((variant.get("availability") or {}).get("available")),
        image_url=media.get("url"),
        product_url=variant.get("url") or "",
        shipping_policy_url=_policy_url(seller, "shipping_policy"),
        refund_policy_url=_policy_url(seller, "refund_policy"),
    )


def _policy_url(seller: dict, kind: str) -> str | None:
    """A seller policy link from the API, https only."""
    for link in seller.get("links") or []:
        url = link.get("url") or ""
        if link.get("type") == kind and url.startswith("https://"):
            return url
    return None


def _same_colour(variant: dict, hit_colour: str | None, item: ItemSpec) -> bool:
    """The returned variant must be the colour the user was shown in search."""
    label = _option_label(variant, "color")
    if hit_colour is not None:
        return label == hit_colour
    return label is None or matching.colour_matches(item.color, label)


async def _sized_variant(
    catalog: Catalog, product: dict, item: ItemSpec, cap_inr: int | None, stats: PipelineStats
) -> dict | None:
    """Exact variant in the requested size, or None. Uses 1-2 get_product calls."""
    labels = _product_size_labels(product)
    if not labels:
        stats.detail_calls += 1
        detail = await catalog.get_product(product["id"])
        labels = _available_size_labels(detail)
    store_label = matching.pick_size_label(item.size, labels)
    if store_label is None:
        stats.unmapped_sizes.extend(l for l in labels if not matching.size_tokens(l))
        return None
    selected = [{"name": "Size", "label": store_label}]
    hit_colour = _option_label((product.get("variants") or [{}])[0], "color")
    if hit_colour:
        selected.append({"name": "Color", "label": hit_colour})
    stats.detail_calls += 1
    detail = await catalog.get_product(product["id"], selected)
    for variant in detail.get("variants") or []:
        # Verify: a wrong `selected` label silently returns other variants (spike-01).
        if (
            _option_label(variant, "size") == store_label
            and variant_passes(variant, cap_inr)
            and _same_colour(variant, hit_colour, item)
        ):
            return variant
    return None


async def run_item(
    catalog: Catalog,
    item_index: int,
    item: ItemSpec,
    cap_inr: int | None,
    on_event: EventSink = _no_events,
) -> ItemResult:
    letter = chr(ord("A") + item_index)
    stats = PipelineStats()
    wants_size = matching.requested_size_token(item.size) is not None
    if item.size and not wants_size:
        # Never show "any size" under a heading that promises the user's size.
        await on_event("size_unreadable", {"item_index": item_index, "size": item.size})
        note = f'I couldn\'t read the size "{item.size}". Send it again as a letter (S, M, L, XL) or a number (32).'
        return ItemResult(item_index=item_index, item=item, options=(), cap_inr=cap_inr, note=note)
    products = await catalog.search(build_query(item), cap_inr, item.gender)
    stats.search_hits = len(products)
    candidates = [p for p in products if hit_passes(p, item, cap_inr)]
    stats.after_hard_filters = len(candidates)
    candidates = cap_per_seller(candidates)
    stats.after_seller_cap = len(candidates)

    options: list[Option] = []
    for product in candidates:
        if len(options) >= config.OPTIONS_PER_ITEM:
            break
        if wants_size and stats.detail_calls >= config.MAX_DETAIL_CALLS_PER_ITEM:
            break
        if wants_size:
            variant = await _sized_variant(catalog, product, item, cap_inr, stats)
        else:
            variant = product["variants"][0]
        if variant is None:
            continue
        label = f"{letter}{len(options) + 1}"
        options.append(make_option(label, item_index, product, variant))

    if stats.unmapped_sizes:
        await on_event("size_unmapped", {"labels": sorted(set(stats.unmapped_sizes))[:20]})
    await on_event(
        "item_searched",
        {
            "item_index": item_index,
            "query": build_query(item),
            "hits": stats.search_hits,
            "after_filters": stats.after_hard_filters,
            "after_seller_cap": stats.after_seller_cap,
            "detail_calls": stats.detail_calls,
            "shown": len(options),
        },
    )
    return ItemResult(item_index=item_index, item=item, options=tuple(options), cap_inr=cap_inr)
