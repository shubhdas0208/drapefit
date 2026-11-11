"""Sarvam parser: natural language -> validated RequestSpec (D20).

The LLM only translates. Its output is validated with Pydantic; on failure we retry
once, then give up with ParseError. Nothing the LLM writes is shown as a price,
size or stock value.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from . import config
from .models import FASHION_CATEGORIES, FollowUp, RequestSpec

log = logging.getLogger(__name__)

PARSE_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 800  # a 5-item spec is ~600 tokens; fail fast on runaway output

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}

REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "budget_type", "total_max_inr", "ambiguities"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "query_terms", "category", "color", "size", "size_system",
                    "gender", "fit", "quantity", "max_price_inr",
                ],
                "properties": {
                    "query_terms": {"type": "string"},
                    "category": {"type": "string", "enum": [*FASHION_CATEGORIES, "not_fashion"]},
                    "color": _NULLABLE_STR,
                    "size": _NULLABLE_STR,
                    "size_system": {"type": "string", "enum": ["alpha", "chest_in", "waist_in", "unknown"]},
                    "gender": {"type": ["string", "null"], "enum": ["men", "women", "unisex", None]},
                    "fit": _NULLABLE_STR,
                    "quantity": {"type": "integer"},
                    "max_price_inr": _NULLABLE_INT,
                },
            },
        },
        "budget_type": {"type": "string", "enum": ["each", "total"]},
        "total_max_inr": _NULLABLE_INT,
        "ambiguities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item_index", "field", "question"],
                "properties": {
                    "item_index": {"type": "integer"},
                    "field": {"type": "string"},
                    "question": {"type": "string"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You turn an Indian shopper's message (English, Hindi or Hinglish) into JSON for a clothing search.

Rules:
- One entry in items[] per distinct product the user wants. Never merge or drop items.
  "one grey one black hoodie" = two items.
- query_terms: the product type in plain English, 1-4 words, without colour, size, gender or price
  (e.g. "turtleneck", "bell bottom jeans", "kurta", "oversized t-shirt").
- category: top | bottom | dress | ethnic | outerwear | set | activewear | other_fashion | not_fashion.
  not_fashion = anything that is not a garment: footwear (shoes, sneakers, sandals), phone covers, bags,
  jewellery, watches, electronics, food, furniture. other_fashion = a garment that fits no other category (scarf, socks).
- color: the colour in English lowercase ("grey", "blue", "navy"), or null. Translate Hindi colour words:
  kala/kaala=black, safed/safaid=white, neela/neeli/nila=blue, laal/lal=red, hara/hari=green, peela/peeli=yellow,
  gulabi=pink, bhura=brown, sleti=grey, maroon=maroon. A fabric (denim, linen) is not a colour.
- size: as the user said it, uppercase letters or a number ("M", "XL", "32", "FREE SIZE"), or null.
- size_system: alpha for XS..XXL; waist_in for a number on bottoms; chest_in for a number on tops; unknown otherwise.
- gender: men | women | unisex | null. Only set it when the user says it or names the wearer
  ("for my wife/sister/mom" = women, "for husband/brother/dad" = men; "men"/"women" anywhere applies to every item).
  Do not guess gender from the product unless the product is clearly gendered (saree, kurti, lehenga = women).
- max_price_inr: the per-item cap in rupees as an integer ("2k" = 2000, "1.5k" = 1500, "under 2000" = 2000), else null.
- budget_type: "each" by default. Use "total" ONLY when the user says the amount is for everything together
  ("total", "in all", "altogether", "combined"); then total_max_inr = that amount and every max_price_inr = null.
  "under 2000 each", "both under 1500" or one amount for one item = "each" with max_price_inr set.
- quantity: default 1.
- ambiguities: add an entry ONLY when a value would otherwise be a guess. Ask exactly in these cases:
  * a bare number size on a TOP (turtleneck, t-shirt, shirt, hoodie, sweater) that is below 34, e.g. "turtleneck size 30":
    ask "chest 30 inches, or XS?". Check every item, also in multi-item messages.
  Never ask about: a number on bottoms (jeans, chinos, trousers, shorts, pants) - that is the waist;
  letter sizes; "free size"; shirt/blazer sizes 36-48 (those are chest sizes); missing colour, gender or budget.
  The question must be short, friendly and offer concrete choices.
- Never invent prices, brands or stock.

Reply with one JSON object only, matching this JSON schema:
"""


class ParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class FollowUpResult:
    follow_up: FollowUp
    attempts: int
    latency_ms: int
    model: str


@dataclass(frozen=True)
class ParseResult:
    spec: RequestSpec
    attempts: int
    latency_ms: int
    model: str


class SarvamParser:
    def __init__(self, http: httpx.AsyncClient, api_key: str, model: str) -> None:
        self._http = http
        self._key = api_key
        self._model = model

    def _body(self, message: str, context: str | None) -> dict:
        user = message if not context else f"{context}\n\nUser reply: {message}"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + json.dumps(REQUEST_SCHEMA)},
                {"role": "user", "content": user},
            ],
            # json_object, not strict json_schema: strict mode made sarvam-105b-conversations
            # emit runaway whitespace on 4/6 Hinglish inputs (eval 2026-11-11). Pydantic validates.
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if self._model == "sarvam-105b":
            body["reasoning_effort"] = "low"
            body["max_tokens"] = 4000  # reasoning tokens count against the limit
        return body

    async def _content(self, body: dict) -> str:
        resp = await self._http.post(
            config.SARVAM_URL,
            headers={"api-subscription-key": self._key},
            json=body,
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            raise ParseError(f"Sarvam HTTP {resp.status_code}")
        try:
            content = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content")
        except (ValueError, AttributeError, IndexError) as err:
            raise ParseError("Sarvam returned a malformed body") from err
        if not content:
            raise ParseError("Sarvam returned no content")
        return content

    async def _call(self, message: str, context: str | None) -> RequestSpec:
        content = await self._content(self._body(message, context))
        try:
            return RequestSpec.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as err:
            raise ParseError(f"Invalid spec: {str(err)[:200]}") from err

    async def _classify_once(self, message: str, context: str) -> FollowUp:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": FOLLOWUP_PROMPT + json.dumps(FOLLOWUP_SCHEMA)},
                {"role": "user", "content": f"{context}\n\nNew message: {message}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": FOLLOWUP_MAX_TOKENS,
        }
        if self._model == "sarvam-105b":
            body["reasoning_effort"] = "low"
            body["max_tokens"] = 4000
        content = await self._content(body)
        try:
            return FollowUp.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as err:
            raise ParseError(f"Invalid follow-up: {str(err)[:200]}") from err

    async def classify(self, message: str, context: str) -> FollowUpResult:
        """Classify a message sent after results were shown (D47/D48)."""
        start = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            try:
                follow_up = await self._classify_once(message, context)
                return FollowUpResult(follow_up, attempt, int((time.monotonic() - start) * 1000), self._model)
            except (ParseError, httpx.HTTPError) as err:
                log.warning("classify attempt %d failed: %s", attempt, err)
                last_error = err
        raise ParseError(str(last_error))

    async def parse(self, message: str, context: str | None = None) -> ParseResult:
        start = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            try:
                spec = await self._call(message, context)
                return ParseResult(spec, attempt, int((time.monotonic() - start) * 1000), self._model)
            except (ParseError, httpx.HTTPError) as err:
                log.warning("parse attempt %d failed: %s", attempt, err)
                last_error = err
        raise ParseError(str(last_error))


def clarification_context(original_message: str, questions: list[str]) -> str:
    """Prompt context for a follow-up reply to a clarifying question."""
    asked = " ".join(questions)
    return (
        f"Original request: {original_message}\n"
        f"We asked: {asked}\n"
        "Combine the original request with the user's reply below into one complete spec. "
        "Only add an ambiguity if the reply still leaves it unresolved."
    )


FOLLOWUP_MAX_TOKENS = 300

FOLLOWUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "labels", "items", "changes"],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["new_search", "refine", "add", "remove", "show_cart", "checkout", "other"],
        },
        "labels": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {"type": "string"}},
        "changes": {
            "type": "object",
            "additionalProperties": False,
            "required": ["color", "size", "query_terms", "gender", "fit", "max_price_inr", "cheaper"],
            "properties": {
                "color": _NULLABLE_STR,
                "size": _NULLABLE_STR,
                "query_terms": _NULLABLE_STR,
                "gender": {"type": ["string", "null"], "enum": ["men", "women", "unisex", None]},
                "fit": _NULLABLE_STR,
                "max_price_inr": _NULLABLE_INT,
                "cheaper": {"type": "boolean"},
            },
        },
    },
}

FOLLOWUP_PROMPT = """A shopper already got clothing search results from us. Each item has a letter (A, B, ...)
and each shown option has a label (A1..A5, B1..B5). Classify their NEW message (English, Hindi or Hinglish).

action:
- refine: change an item already searched and search it again: another colour ("same but black", "kala wala"),
  size ("in L instead"), cheaper ("show cheaper", "kuch sasta", "under 1500"), gender, fit or product type
  ("make it a sweatshirt"). Put the item letters in items[] when the user names or describes them
  ("the jeans" = the letter whose product is jeans). If there are 2+ items and the message does not
  name or describe one ("same but black", "show cheaper"), items[] MUST be empty; never guess A.
- items[] rule for refine AND remove: any product word that matches an item ("turtleneck", "jeans", "kurti")
  means that item's letter goes in items[].
- add: put options in the cart ("I'll take the second one" after one item = A2, "B3 wala lelo", "add both blue ones" is NOT
  enough: only use labels you can name exactly). labels[] = option labels.
- remove: take something out of the cart. labels[] for options, items[] when they name a product ("remove the jeans").
- show_cart: they want to see the cart. checkout: they want to pay / buy now / checkout.
- new_search: a different product not in the list ("also need a white shirt", "now show me kurtas").
- other: thanks, greetings, questions about delivery or returns, anything else.

changes (only for refine; otherwise all null and cheaper false):
- color: ALWAYS an English colour word in lowercase, never the Hindi word. Translate: kala/kaala=black,
  safed=white, neela/nila=blue, laal/lal=red, hara=green, sleti=grey, gulabi=pink, peela=yellow, bhura=brown.
  A Hindi colour word before a product ("neela turtleneck") is a colour change for that item.
- size: as said ("L", "32"). query_terms: only when the product TYPE changes ("make it a sweatshirt"),
  1-4 words, never a gender, colour or size ("women's version" is gender, not query_terms).
- max_price_inr: only when the user gives a number ("under 1500", "1.5k tak" = 1500). Never invent one.
- cheaper: true when they ask for cheaper/lower price without a number.
- gender / fit: only when stated. fit is one word: slim, regular, relaxed, oversized, skinny, straight.
Never invent labels or letters that are not in the context.

Reply with one JSON object only, matching this JSON schema:
"""


def followup_context(spec: RequestSpec, shown: dict[int, list[str]], cart_labels: list[str]) -> str:
    """What the classifier needs to resolve "the jeans" or "the second one"."""
    lines = ["Items already shown:"]
    for index, item in enumerate(spec.items):
        letter = chr(ord("A") + index)
        what = " ".join(p for p in (item.color, item.fit, item.query_terms) if p)
        size = f", size {item.size}" if item.size else ""
        labels = ", ".join(shown.get(index, [])) or "no options shown"
        lines.append(f"{letter}: {what}{size} (options: {labels})")
    lines.append("Cart: " + (", ".join(cart_labels) if cart_labels else "empty"))
    return "\n".join(lines)
