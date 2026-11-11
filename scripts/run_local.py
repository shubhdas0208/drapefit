"""Run the bot on your laptop with Telegram long polling. No Render, no Neon, no public URL.

    setx TELEGRAM_BOT_TOKEN "<token from @BotFather>"   (once, then open a new terminal)
    .venv/Scripts/python scripts/run_local.py

State goes to ./drapefit-local.db (gitignored). Links open the store directly, so clicks
are not tracked in this mode. Stop with Ctrl+C. Deleting the webhook is safe: the deploy
script sets it again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import sys
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from drapefit import config, render  # noqa: E402
from drapefit.bot import Bot  # noqa: E402
from drapefit.catalog import CatalogClient  # noqa: E402
from drapefit.config import DEFAULT_AGENT_PROFILE, _read_env  # noqa: E402
from drapefit.parser import SarvamParser  # noqa: E402
from drapefit.store import Store  # noqa: E402
from drapefit.telegram import UPDATE_TYPES, TelegramClient  # noqa: E402

POLL_TIMEOUT_SECONDS = 30
FOLLOWUP_CHECK_SECONDS = 60
log = logging.getLogger("drapefit.local")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    names = ("TELEGRAM_BOT_TOKEN", "SARVAM_API_KEY", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET")
    env = {n: _read_env(n) for n in names}
    if missing := [n for n, v in env.items() if not v]:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")
    store = Store.from_url(f"sqlite+aiosqlite:///{ROOT / 'drapefit-local.db'}")
    await store.init()
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS + 10) as http:
        api = f"{config.TELEGRAM_API}/bot{env['TELEGRAM_BOT_TOKEN']}"
        await http.post(f"{api}/deleteWebhook")
        bot = Bot(
            store=store,
            telegram=TelegramClient(http, env["TELEGRAM_BOT_TOKEN"]),
            parser=SarvamParser(http, env["SARVAM_API_KEY"], _read_env("SARVAM_MODEL") or "sarvam-105b-conversations"),
            catalog=CatalogClient(http, env["SHOPIFY_CLIENT_ID"], env["SHOPIFY_CLIENT_SECRET"],
                                  _read_env("AGENT_PROFILE_URL") or DEFAULT_AGENT_PROFILE),
            public_base_url=None,
            followup_delay=timedelta(minutes=config.followup_delay_minutes()),
        )
        try:
            await TelegramClient(http, env["TELEGRAM_BOT_TOKEN"]).set_commands(render.BOT_COMMANDS)
        except Exception:  # the menu is a nicety; never block startup on it
            log.warning("could not set the command menu")
        me = (await http.get(f"{api}/getMe")).json().get("result", {})
        log.info("polling as @%s. Message the bot on Telegram. Ctrl+C to stop.", me.get("username"))
        offset = 0
        tasks: set[asyncio.Task] = set()
        followups = asyncio.create_task(_followup_loop(bot))  # stands in for the cron job
        while True:
            try:
                resp = await http.get(f"{api}/getUpdates",
                                      params={"timeout": POLL_TIMEOUT_SECONDS, "offset": offset, "allowed_updates": json.dumps(UPDATE_TYPES)})
                updates = resp.json().get("result", [])
            except httpx.HTTPError as err:
                log.warning("poll failed (%s), retrying", type(err).__name__)
                await asyncio.sleep(2)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                if not await store.claim_update(update["update_id"]):
                    continue
                task = asyncio.create_task(_run(bot, update))
                tasks.add(task)
                task.add_done_callback(tasks.discard)


async def _followup_loop(bot: Bot) -> None:
    while True:
        try:
            sent = await bot.shop.send_due_followups()
            if sent:
                log.info("sent %d follow-up(s)", sent)
        except Exception:
            log.exception("follow-up loop failed")
        await asyncio.sleep(FOLLOWUP_CHECK_SECONDS)


async def _run(bot: Bot, update: dict) -> None:
    try:
        await bot.handle_update(update)
    except Exception:
        log.exception("update failed")
        source = update.get("message") or update.get("callback_query") or {}
        chat = ((source.get("message") or source).get("chat") or {}).get("id") or (source.get("from") or {}).get("id")
        if chat is not None:
            await bot.notify_failure(chat)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
