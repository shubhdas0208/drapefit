"""Domain models.

RequestSpec is what the LLM produces (validated at the boundary).
Option is what the user sees. It is built ONLY from catalog API variant objects,
never from LLM output (core logic #1, #2; decision D30).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SizeSystem = Literal["alpha", "chest_in", "waist_in", "unknown"]
Gender = Literal["men", "women", "unisex"]

FASHION_CATEGORIES = (
    "top",
    "bottom",
    "dress",
    "ethnic",
    "outerwear",
    "set",
    "activewear",
    "other_fashion",
)


class ItemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_terms: str = Field(min_length=1, max_length=80)
    category: str
    color: str | None = None
    size: str | None = None
    size_system: SizeSystem = "unknown"
    gender: Gender | None = None
    fit: str | None = None
    quantity: int = Field(default=1, ge=1, le=10)
    max_price_inr: int | None = Field(default=None, ge=1, le=500_000)

    @field_validator("color", "size", "fit", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def in_scope(self) -> bool:
        return self.category in FASHION_CATEGORIES


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_index: int = Field(ge=0)
    field: str
    question: str = Field(min_length=1, max_length=300)


class RequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ItemSpec] = Field(max_length=5)
    budget_type: Literal["each", "total"] = "each"
    total_max_inr: int | None = Field(default=None, ge=1, le=1_000_000)
    ambiguities: list[Ambiguity] = Field(default_factory=list)

    def item_cap(self, item: ItemSpec) -> int | None:
        """Per-item INR cap: the item's own cap, else the total budget."""
        if item.max_price_inr is not None:
            return item.max_price_inr
        return self.total_max_inr


@dataclass(frozen=True)
class Option:
    """One shown product variant. Every field is copied from an API variant object."""

    label: str  # "A1"
    item_index: int
    product_id: str
    variant_id: str
    title: str
    seller_name: str
    seller_url: str
    seller_domain: str
    price_paise: int
    currency: str
    size_label: str | None
    color_label: str | None
    available: bool
    image_url: str | None
    product_url: str
    # seller policy pages from the API (seller.links); delivery dates/costs are not in the API
    shipping_policy_url: str | None = None
    refund_policy_url: str | None = None

    @property
    def price_inr(self) -> int:
        return self.price_paise // 100


@dataclass(frozen=True)
class ItemResult:
    item_index: int
    item: ItemSpec
    options: tuple[Option, ...]
    cap_inr: int | None
    note: str | None = None  # e.g. why there are zero results


FollowUpAction = Literal["new_search", "refine", "add", "remove", "show_cart", "checkout", "other"]
LABEL_PATTERN = r"^[A-E](10|[1-9])$"
ITEM_LETTER_PATTERN = r"^[A-E]$"


class Changes(BaseModel):
    """What the user wants changed on an earlier item. Code applies these (D48)."""

    model_config = ConfigDict(extra="forbid")

    color: str | None = None
    size: str | None = None
    query_terms: str | None = Field(default=None, max_length=80)
    gender: Gender | None = None
    fit: str | None = None
    max_price_inr: int | None = Field(default=None, ge=1, le=500_000)
    cheaper: bool = False

    @field_validator("color", "size", "query_terms", "fit", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_empty(self) -> bool:
        return not self.cheaper and all(
            v is None for v in (self.color, self.size, self.query_terms, self.gender, self.fit, self.max_price_inr)
        )

    @property
    def price_only(self) -> bool:
        return all(v is None for v in (self.color, self.size, self.query_terms, self.gender, self.fit))


class FollowUp(BaseModel):
    """LLM classification of a message sent after results were shown."""

    model_config = ConfigDict(extra="forbid")

    action: FollowUpAction
    labels: list[str] = Field(default_factory=list, max_length=10)  # option labels, e.g. "A1"
    items: list[str] = Field(default_factory=list, max_length=5)  # item letters, e.g. "B"
    changes: Changes = Field(default_factory=Changes)

    @field_validator("labels", mode="before")
    @classmethod
    def _labels(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        cleaned = [str(v).strip().upper().replace(" ", "") for v in value]
        return [v for v in cleaned if re.match(LABEL_PATTERN, v)]

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        cleaned = [str(v).strip().upper() for v in value]
        return [v for v in cleaned if re.match(ITEM_LETTER_PATTERN, v)]
