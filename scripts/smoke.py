"""Live smoke test: real Sarvam + real Shopify Catalog through the real Bot, with a
console stand-in for Telegram and a local SQLite store. Run before each deploy.

    .venv/Scripts/python scripts/smoke.py ["your request" ...]
"""
from __future__ import annotations

import asyncio
import html
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from drapefit.bot import Bot  # noqa: E402
from drapefit.catalog import CatalogClient  # noqa: E402
from drapefit.config import DEFAULT_AGENT_PROFILE, _read_env  # noqa: E402
from drapefit.parser import SarvamParser  # noqa: E402
from drapefit.store import Store  # noqa: E402

DEFAULT_REQUESTS = [
    "grey turtleneck M and blue bell-bottoms waist 32, men, under ₹2,000 each",
    "mujhe ek kala hoodie chahiye XL mein, 1500 tak",
    "white kurti M for my sister under 1200 and an iphone cover",
]


class ConsoleTelegram:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.first_reply_ms: int | None = None
        self.messages: list[str] = []

    def _stamp(self) -> str:
        ms = int((time.monotonic() - self.t0) * 1000)
        if self.first_reply_ms is None:
            self.first_reply_ms = ms
        return f"[{ms:>5} ms]"

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, buttons: list | None = None) -> None:
        print(self._stamp(), f"(edited #{message_id}: {re.sub(r'<[^>]+>', '', text)})")

    async def edit_buttons(self, chat_id: int, message_id: int, buttons: list) -> None:
        return None

    async def send_message(self, chat_id: int, text: str, buttons: list | None = None) -> int:
        self.messages.append(text)
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        print(self._stamp(), plain.replace("\n", "\n            "))
        return len(self.messages)

    async def send_photos(self, chat_id: int, photos: tuple) -> None:
        print(self._stamp(), f"(album: {len(photos)} photos)")

    async def send_typing(self, chat_id: int) -> None:
        return None


async def main(requests: list[str]) -> int:
    secrets = {n: _read_env(n) for n in ("SARVAM_API_KEY", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET")}
    missing = [n for n, v in secrets.items() if not v]
    if missing:
        print("missing env:", missing)
        return 2
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        store = Store.from_url(f"sqlite+aiosqlite:///{pathlib.Path(tmp) / 'smoke.db'}")
        await store.init()
        async with httpx.AsyncClient(timeout=30) as http:
            catalog = CatalogClient(http, secrets["SHOPIFY_CLIENT_ID"], secrets["SHOPIFY_CLIENT_SECRET"],
                                    _read_env("AGENT_PROFILE_URL") or DEFAULT_AGENT_PROFILE)
            parser = SarvamParser(http, secrets["SARVAM_API_KEY"], _read_env("SARVAM_MODEL") or "sarvam-105b-conversations")
            for n, text in enumerate(requests, 1):
                tg = ConsoleTelegram()
                bot = Bot(store, tg, parser, catalog, "https://drapefit.example")
                print(f"\n=== {n}. {text}")
                await bot.handle_update({"update_id": n, "message": {"chat": {"id": 1}, "from": {"id": 1}, "text": text}})
                total = int((time.monotonic() - tg.t0) * 1000)
                shown = sum(len(re.findall(r"<b>[A-Z]\d</b>", m)) for m in tg.messages)
                print(f"--- total {total} ms, options shown {shown}")
                if shown == 0 and "clothes" not in " ".join(tg.messages):
                    failures += 1
            events = await store.events_for(1)
            print("\nevents:", ", ".join(e["type"] for e in events))
        await store.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or DEFAULT_REQUESTS)))
