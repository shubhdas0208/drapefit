"""Live Rung 2 smoke: real Sarvam + real Shopify Catalog through the real Bot.

    search -> "add A1 A2 B1" -> "same but black" (classifier + re-search) -> checkout
    -> each merchant permalink is fetched and must land on that store's Shopify checkout.

Telegram is a console stand-in; state goes to a temporary SQLite file.

    .venv/Scripts/python scripts/smoke_rung2.py
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import tempfile
import time
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from drapefit.bot import Bot  # noqa: E402
from drapefit.catalog import CatalogClient  # noqa: E402
from drapefit.config import DEFAULT_AGENT_PROFILE, _read_env  # noqa: E402
from drapefit.parser import SarvamParser  # noqa: E402
from drapefit.store import Store  # noqa: E402

USER = 7
STEPS = [
    "grey turtleneck M and blue jeans waist 32, men, under 2500 each",
    "add A1 A2 B1",
    "same jeans but in black",
    "checkout",
]


class ConsoleTelegram:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def edit_message_text(self, chat_id, message_id, text, buttons=None) -> None:
        print(f"  BOT (edit #{message_id}): {re.sub(r'<[^>]+>', '', text)}")

    async def edit_buttons(self, chat_id, message_id, buttons) -> None:
        return None

    async def send_message(self, chat_id, text, buttons=None) -> int:
        plain = re.sub(r"<[^>]+>", "", text)
        print("  BOT:", plain.replace("\n", "\n       "))
        for row in buttons or []:
            for b in row:
                if b.get("url"):
                    self.urls.append(b["url"])
                    print(f"       [button] {b['text']} -> {b['url']}")
        print()
        self.count = getattr(self, "count", 0) + 1
        return self.count

    async def send_photos(self, chat_id, photos) -> None:
        print(f"  BOT: [album of {len(photos)} photos]")

    async def send_typing(self, chat_id) -> None:
        return None

    async def answer_callback(self, callback_id, text=None) -> None:
        print(f"  TOAST: {text}")


async def main() -> None:
    names = ("SARVAM_API_KEY", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET")
    env = {n: _read_env(n) for n in names}
    if missing := [n for n, v in env.items() if not v]:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "smoke.db"
    store = Store.from_url(f"sqlite+aiosqlite:///{tmp}")
    await store.init()
    tg = ConsoleTelegram()
    async with httpx.AsyncClient(timeout=40) as http:
        bot = Bot(
            store=store,
            telegram=tg,
            parser=SarvamParser(http, env["SARVAM_API_KEY"], _read_env("SARVAM_MODEL") or "sarvam-105b-conversations"),
            catalog=CatalogClient(http, env["SHOPIFY_CLIENT_ID"], env["SHOPIFY_CLIENT_SECRET"],
                                  _read_env("AGENT_PROFILE_URL") or DEFAULT_AGENT_PROFILE),
            public_base_url=None,  # buttons carry the raw permalink so we can probe it
            followup_delay=timedelta(seconds=-1),
        )
        for i, text in enumerate(STEPS, start=1):
            print(f"USER: {text}")
            start = time.monotonic()
            await bot.handle_update({"update_id": i, "message": {"message_id": i, "chat": {"id": USER},
                                                                 "from": {"id": USER}, "text": text}})
            print(f"  ({int((time.monotonic() - start) * 1000)} ms)\n")

        print("FOLLOW-UP (delay forced to 0):")
        sent = await bot.shop.send_due_followups()
        print(f"  sent {sent}\n")

        print("PERMALINK PROBES:")
        ok = 0
        for url in tg.urls:
            resp = await http.get(url, follow_redirects=True, headers={"user-agent": "Mozilla/5.0"})
            landed = str(resp.url)
            good = resp.status_code == 200 and ("/checkouts/" in landed or "checkout" in landed)
            ok += good
            print(f"  {'OK ' if good else 'BAD'} {resp.status_code} {url}\n      -> {landed[:110]}")
        types = [e["type"] for e in await store.events_for(USER)]
        print(f"\nevents: {types}")
        print(f"\n{ok}/{len(tg.urls)} checkout links reached a Shopify checkout")
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
