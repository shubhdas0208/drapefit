"""Shared test fixtures.

Search fixtures are the real spike-01 responses (docs/research/spike-01-raw/).
get_product fixtures are real recorded payloads (tests/fixtures/), plus a fake that
derives a sized variant from the search hit so pipeline tests stay offline.
"""
from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import pytest

from drapefit.models import FollowUp, RequestSpec
from drapefit.parser import FollowUpResult, ParseError, ParseResult
from drapefit.store import Store
from drapefit.telegram import TelegramError

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "docs" / "research" / "spike-01-raw"
FIXTURES = ROOT / "tests" / "fixtures"


def load_search(name: str) -> list[dict]:
    data = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    return data["india"]["result"]["structuredContent"]["products"]


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeCatalog:
    """search -> recorded results; get_product -> sized variant derived from the hit.

    `sold_out` holds (product_id, size_label) pairs that come back unavailable.
    """

    def __init__(self, products: list[dict], sold_out: set[tuple[str, str]] | None = None, fail_times: int = 0):
        self.products = products
        self.by_id = {p["id"]: p for p in products}
        self.sold_out = sold_out or set()
        self.fail_times = fail_times
        self.search_calls: list[tuple] = []
        self.detail_calls: list[tuple] = []
        self.fresh_calls: list[str] = []

    async def search(self, query: str, max_inr: int | None, gender: str | None) -> list[dict]:
        from drapefit.catalog import CatalogError

        self.search_calls.append((query, max_inr, gender))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CatalogError("search_catalog: HTTP 503")
        return copy.deepcopy(self.products)

    async def get_product(self, product_id: str, selected: list[dict] | None = None, fresh: bool = False) -> dict:
        self.detail_calls.append((product_id, selected))
        if fresh:
            self.fresh_calls.append(product_id)
        product = copy.deepcopy(self.by_id[product_id])
        if not selected:
            return product
        size = next((s["label"] for s in selected if s["name"] == "Size"), None)
        variant = product["variants"][0]
        variant["id"] = f"{variant['id']}-{size}"
        variant["options"] = [o for o in variant.get("options", []) if o.get("name") != "Size"] + [
            {"name": "Size", "label": size}
        ]
        variant["availability"] = {"available": (product_id, size) not in self.sold_out}
        product["variants"] = [variant]
        return product


class FakeTelegram:
    def __init__(self, fail_photos: bool = False) -> None:
        self.messages: list[tuple[int, str]] = []
        self.buttons: list[list | None] = []  # parallel to messages
        self.photos: list[tuple[int, tuple]] = []
        self.callback_answers: list[tuple[str, str | None]] = []
        self.edits: list[tuple[int, str | None, list | None]] = []
        self.fail_photos = fail_photos

    async def send_message(self, chat_id: int, text: str, buttons: list | None = None) -> int:
        self.messages.append((chat_id, text))
        self.buttons.append(buttons)
        return len(self.messages)  # message_id = 1-based index into messages

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, buttons: list | None = None) -> None:
        self.edits.append((message_id, text, buttons))
        self.messages[message_id - 1] = (chat_id, text)
        if buttons is not None:
            self.buttons[message_id - 1] = buttons

    async def edit_buttons(self, chat_id: int, message_id: int, buttons: list) -> None:
        self.edits.append((message_id, None, buttons))
        self.buttons[message_id - 1] = buttons

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.callback_answers.append((callback_id, text))

    def buttons_for(self, prefix: str) -> list | None:
        """Keyboard of the latest message starting with prefix."""
        for (_, text), buttons in zip(reversed(self.messages), reversed(self.buttons)):
            if text.startswith(prefix):
                return buttons
        return None

    async def send_photos(self, chat_id: int, photos: tuple) -> None:
        if self.fail_photos:
            raise TelegramError("sendMediaGroup: HTTP 400 wrong file identifier")
        self.photos.append((chat_id, photos))

    async def send_typing(self, chat_id: int) -> None:
        return None

    @property
    def texts(self) -> list[str]:
        return [t for _, t in self.messages]


class FakeParser:
    """Returns queued specs (dicts) or raises ParseError for the string 'FAIL'.

    classify() pops from `follow_ups`; when it is empty, classify fails, so the bot
    treats the message as a new search (the Rung 1 behaviour).
    """

    def __init__(self, *responses: dict | str, follow_ups: list[dict] | None = None) -> None:
        self.responses = list(responses)
        self.follow_ups = list(follow_ups or [])
        self.calls: list[tuple[str, str | None]] = []
        self.classify_calls: list[tuple[str, str]] = []

    async def classify(self, message: str, context: str) -> FollowUpResult:
        self.classify_calls.append((message, context))
        if not self.follow_ups:
            raise ParseError("no follow-up queued")
        return FollowUpResult(FollowUp.model_validate(self.follow_ups.pop(0)), 1, 5, "fake")

    async def parse(self, message: str, context: str | None = None) -> ParseResult:
        self.calls.append((message, context))
        response = self.responses.pop(0)
        if response == "FAIL":
            raise ParseError("invalid twice")
        return ParseResult(RequestSpec.model_validate(response), 1, 5, "fake")


def item(**overrides: Any) -> dict:
    base = {
        "query_terms": "turtleneck",
        "category": "top",
        "color": "grey",
        "size": "M",
        "size_system": "alpha",
        "gender": "men",
        "fit": None,
        "quantity": 1,
        "max_price_inr": 2000,
    }
    return {**base, **overrides}


def spec(*items: dict, **overrides: Any) -> dict:
    return {"items": list(items), "budget_type": "each", "total_max_inr": None, "ambiguities": [], **overrides}


def tg_callback(update_id: int, data: str, user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": user_id},
            "message": {"message_id": 1, "chat": {"id": user_id}},
            "data": data,
        },
    }


def tg_update(update_id: int, text: str, user_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": user_id}, "from": {"id": user_id}, "text": text},
    }


@pytest.fixture
async def store(tmp_path: pathlib.Path):
    s = Store.from_url(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await s.init()
    yield s
    await s.close()
