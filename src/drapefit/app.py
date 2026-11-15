"""HTTP surface (FastAPI).

    POST /telegram/webhook   secret header -> dedupe update_id -> 200 at once,
                             work continues in a background task (D27)
    GET  /r/{token}          log link_clicked -> 302 to the stored merchant URL (D29)
    GET  /healthz            keep-warm target for cron-job.org (D25)
    POST /tasks/followups    send due "did you buy?" pings + retention prune (cron, hourly)
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config
from .bot import Bot
from .catalog import CatalogClient
from .config import Settings, load_settings
from .parser import SarvamParser
from .store import Store
from .telegram import TelegramClient, webhook_token

log = logging.getLogger(__name__)
# httpx logs full request URLs at INFO; Telegram URLs embed the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

SHUTDOWN_GRACE_SECONDS = 10


def _secret_ok(given: str | None, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
EXPIRED_PAGE = "<html><body style='font-family:sans-serif'><p>This link has expired. Ask the bot again.</p></body></html>"


@dataclass
class Services:
    settings: Settings
    store: Store
    bot: Bot
    http: httpx.AsyncClient


def build_services(settings: Settings, http: httpx.AsyncClient, store: Store | None = None) -> Services:
    store = store or Store.from_url(settings.database_url)
    catalog = CatalogClient(http, settings.shopify_client_id, settings.shopify_client_secret, settings.agent_profile_url)
    bot = Bot(
        store=store,
        telegram=TelegramClient(http, settings.telegram_bot_token),
        parser=SarvamParser(http, settings.sarvam_api_key, settings.sarvam_model),
        catalog=catalog,
        public_base_url=settings.public_base_url,
        followup_delay=timedelta(minutes=config.followup_delay_minutes()),
    )
    return Services(settings=settings, store=store, bot=bot, http=http)


def create_app(services: Services | None = None) -> FastAPI:
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services
        owns_http = svc is None
        if svc is None:
            svc = build_services(load_settings(), httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS))
        await svc.store.init()
        app.state.svc = svc
        yield
        if background:
            _, pending = await asyncio.wait(background, timeout=SHUTDOWN_GRACE_SECONDS)
            for task in pending:  # don't let stragglers use a closed store
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=1)
        await svc.store.close()
        if owns_http:
            await svc.http.aclose()

    app = FastAPI(title="DrapeFit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.background = background  # exposed so tests can await in-flight updates

    async def run_update(svc: Services, update: dict) -> None:
        source = update.get("message") or update.get("callback_query") or {}
        user = (source.get("from") or {}).get("id")
        chat = ((source.get("message") or source).get("chat") or {}).get("id") or user
        try:
            await svc.bot.handle_update(update)
        except Exception as err:  # last-resort guard: tell the user first, then log
            log.exception("update %s failed", update.get("update_id"))
            if chat is not None:
                try:
                    await svc.bot.notify_failure(chat)
                except Exception:
                    log.exception("could not notify user")
            try:
                await svc.store.log_event(user, None, "error", {"error": repr(err)[:300]})
            except Exception:
                log.exception("could not log failure")

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> JSONResponse:
        svc: Services = request.app.state.svc
        expected = webhook_token(svc.settings.telegram_webhook_secret)
        if not _secret_ok(x_telegram_bot_api_secret_token, expected):
            raise HTTPException(status_code=401)
        try:
            update = await request.json()
            update_id = int(update["update_id"])
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"ok": True})  # malformed: ack so Telegram stops retrying
        if not await svc.store.claim_update(update_id):
            return JSONResponse({"ok": True, "duplicate": True})
        task = asyncio.create_task(run_update(svc, update))
        background.add(task)
        task.add_done_callback(background.discard)
        return JSONResponse({"ok": True})

    @app.get("/r/{token}", response_model=None)
    async def redirect(token: str, request: Request) -> RedirectResponse | HTMLResponse:
        svc: Services = request.app.state.svc
        row = await svc.store.resolve_token(token) if TOKEN_PATTERN.match(token) else None
        if row is None:
            return HTMLResponse(EXPIRED_PAGE, status_code=404)
        try:  # a logging failure must never block the user from reaching the store
            await svc.bot.shop.record_click(row)
        except Exception:
            log.exception("click log failed")
        return RedirectResponse(row["target_url"], status_code=302)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/tasks/followups")
    async def followups(request: Request, x_tasks_secret: str | None = Header(default=None)) -> dict:
        svc: Services = request.app.state.svc
        if not _secret_ok(x_tasks_secret, svc.settings.tasks_secret):
            raise HTTPException(status_code=401)
        sent = await svc.bot.shop.send_due_followups()
        await svc.store.prune(config.RETENTION_DAYS)
        return {"ok": True, "pruned": True, "followups_sent": sent}

    return app
