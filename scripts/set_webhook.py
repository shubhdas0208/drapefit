"""Point Telegram at the deployed service. Run once after the first Render deploy
(and again if PUBLIC_BASE_URL or TELEGRAM_WEBHOOK_SECRET changes).

    .venv/Scripts/python scripts/set_webhook.py https://<your-service>.onrender.com
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from drapefit.config import _read_env  # noqa: E402
from drapefit import render  # noqa: E402
from drapefit.telegram import TelegramClient  # noqa: E402


async def main(base_url: str) -> None:
    token, secret = _read_env("TELEGRAM_BOT_TOKEN"), _read_env("TELEGRAM_WEBHOOK_SECRET")
    if not token or not secret:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET (same value as on Render) first.")
    async with httpx.AsyncClient(timeout=20) as http:
        health = await http.get(f"{base_url.rstrip('/')}/healthz")
        if health.status_code != 200:
            raise SystemExit(f"{base_url}/healthz returned {health.status_code}; is the service up?")
        client = TelegramClient(http, token)
        await client.set_webhook(f"{base_url.rstrip('/')}/telegram/webhook", secret)
        await client.set_commands(render.BOT_COMMANDS)  # the / menu in the chat
    print("Webhook set to", f"{base_url.rstrip('/')}/telegram/webhook")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        raise SystemExit("usage: set_webhook.py https://<service>.onrender.com")
    asyncio.run(main(sys.argv[1]))
