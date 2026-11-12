"""Checkout handoff (D50): group the cart by merchant, re-check stock and price, build
one Shopify cart permalink per merchant.

    cart lines ─▶ group by storefront host ─▶ re-fetch each variant (get_product)
        ├─ sold out / gone ─▶ dropped, named to the user
        ├─ price changed    ─▶ kept, fresh API price shown and flagged
        └─ ok               ─▶ kept
    ─▶ https://<host>/cart/<variant>:<qty>,<variant>:<qty>   (one per merchant)

Every price in a Checkout comes from the API: the stored Option or the fresh variant.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

from .catalog import CatalogError
from .models import Option
from .pipeline import Catalog
from .store import allowed_target

log = logging.getLogger(__name__)

LineStatus = Literal["ok", "price_changed", "sold_out", "unchecked"]
RECHECK_ERRORS = (CatalogError, httpx.HTTPError, KeyError, ValueError, TypeError)
_VARIANT_NUMBER = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class CartLine:
    line_id: int
    option: Option
    quantity: int


@dataclass(frozen=True)
class CheckedLine:
    line: CartLine
    status: LineStatus
    price_paise: int  # fresh API price when re-checked, else the shown price
    old_price_paise: int


@dataclass(frozen=True)
class MerchantCheckout:
    seller_name: str
    host: str
    lines: tuple[CheckedLine, ...]  # every line, including dropped ones
    permalink: str | None  # None when nothing is buyable

    @property
    def buyable(self) -> tuple[CheckedLine, ...]:
        return tuple(l for l in self.lines if l.status != "sold_out")

    @property
    def total_paise(self) -> int:
        return sum(l.price_paise * l.line.quantity for l in self.buyable)


def storefront_host(option: Option) -> str | None:
    """The merchant's own https host, only if the product URL is on the seller's domain."""
    if not allowed_target(option):
        return None
    return (urlsplit(option.product_url).hostname or "").lower() or None


def variant_number(variant_id: str) -> str | None:
    """gid://shopify/ProductVariant/43297406484615 -> 43297406484615."""
    match = _VARIANT_NUMBER.search(variant_id or "")
    return match.group(1) if match else None


def permalink(host: str, lines: tuple[CheckedLine, ...]) -> str | None:
    parts = []
    for checked in lines:
        number = variant_number(checked.line.option.variant_id)
        if number is None:
            continue
        parts.append(f"{number}:{checked.line.quantity}")
    return f"https://{host}/cart/{','.join(parts)}" if parts else None


def group_by_merchant(lines: list[CartLine]) -> tuple[dict[str, list[CartLine]], list[CartLine]]:
    """host -> lines (cart order kept), plus lines with no safe checkout host."""
    groups: dict[str, list[CartLine]] = {}
    unlinkable: list[CartLine] = []
    for line in lines:
        host = storefront_host(line.option)
        if host is None or variant_number(line.option.variant_id) is None:
            unlinkable.append(line)
            continue
        groups.setdefault(host, []).append(line)
    return groups, unlinkable


def _selected(option: Option) -> list[dict] | None:
    selected = []
    if option.size_label:
        selected.append({"name": "Size", "label": option.size_label})
    if option.color_label:
        selected.append({"name": "Color", "label": option.color_label})
    return selected or None


async def recheck(catalog: Catalog, line: CartLine) -> CheckedLine:
    option = line.option
    try:
        detail = await catalog.get_product(option.product_id, _selected(option), fresh=True)
    except RECHECK_ERRORS as err:
        log.warning("recheck failed for %s: %s", option.variant_id, err)
        return CheckedLine(line, "unchecked", option.price_paise, option.price_paise)
    variant = next((v for v in detail.get("variants") or [] if v.get("id") == option.variant_id), None)
    if variant is None or not (variant.get("availability") or {}).get("available"):
        return CheckedLine(line, "sold_out", option.price_paise, option.price_paise)
    price = variant.get("price") or {}
    if price.get("currency") != option.currency or not isinstance(price.get("amount"), int):
        return CheckedLine(line, "unchecked", option.price_paise, option.price_paise)
    status: LineStatus = "ok" if price["amount"] == option.price_paise else "price_changed"
    return CheckedLine(line, status, price["amount"], option.price_paise)


async def build_checkouts(catalog: Catalog, lines: list[CartLine]) -> tuple[list[MerchantCheckout], list[CartLine]]:
    groups, unlinkable = group_by_merchant(lines)
    checkouts = []
    for host, group in groups.items():
        # concurrent; the catalog client's shared rate limiter still caps req/s
        checked = tuple(await asyncio.gather(*(recheck(catalog, line) for line in group)))
        buyable = tuple(c for c in checked if c.status != "sold_out")
        checkouts.append(
            MerchantCheckout(
                seller_name=group[0].option.seller_name.strip() or host,
                host=host,
                lines=checked,
                permalink=permalink(host, buyable),
            )
        )
    return checkouts, unlinkable
