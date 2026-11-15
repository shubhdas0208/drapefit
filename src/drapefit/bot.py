"""Conversation flow for one Telegram update.

    update ─▶ button tap? ──yes──▶ Shop (cart lock): + / ✕ / cart / checkout / did-you-buy
                ▼
           command? ─(/start,/help)─▶ help
                │    ├─(/cart,/checkout,/clear, "add A1 B3", "remove B2")─▶ Shop, no LLM (D47)
                │    └─(/delete)────▶ delete user data
                ▼
           user busy? ──yes──▶ "Still working…"
                ▼
           pending clarification? ──yes──▶ parse(reply + original)
                ▼
           results shown < 2 h ago? ──yes──▶ classify follow-up (LLM, D48)
                │      refine ─▶ code applies the change ─▶ re-search only those items
                │      add/remove/cart/checkout ─▶ Shop;  other ─▶ help;  new_search ─▼
                ▼
           parse(message)
           ambiguities? ──yes──▶ ask, save pending, stop
                ▼
           not-fashion items ─▶ note; in-scope items ─▶ ack "Searching N items…"
                ▼
           run items concurrently; send each item as soon as it is ready
           (catalog error ─▶ "retrying…" ─▶ one retry ─▶ apology for that item)
                ▼
           footer, save last_spec, events at every step (D22)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import config, render
from .catalog import CatalogError
from .commands import CartCommand, parse_cart_command
from .models import FollowUp, ItemResult, ItemSpec, RequestSpec
from .parser import ParseError, SarvamParser, clarification_context, followup_context
from .pipeline import Catalog, run_item
from .refine import apply_refinement
from .shop import Shop
from .store import Store
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
ITEM_ATTEMPTS = 2
PENDING_TTL = timedelta(minutes=15)  # a clarification older than this is dropped
FOLLOWUP_TTL = timedelta(hours=2)  # after this, a message is a fresh search, not "same but black"
SEND_ERRORS = (TelegramError, httpx.HTTPError)
# A malformed catalog payload (missing keys, bad JSON) is treated like a catalog outage.
ITEM_ERRORS = (CatalogError, httpx.HTTPError, KeyError, ValueError, TypeError)


class Bot:
    def __init__(
        self,
        store: Store,
        telegram: TelegramClient,
        parser: SarvamParser,
        catalog: Catalog,
        public_base_url: str | None,
        followup_delay: timedelta = timedelta(hours=config.FOLLOWUP_DELAY_HOURS),
    ) -> None:
        self._store = store
        self._tg = telegram
        self._parser = parser
        self._catalog = catalog
        self._base = public_base_url
        self._locks: dict[int, asyncio.Lock] = {}
        self.shop = Shop(store, telegram, catalog, public_base_url, followup_delay)

    async def notify_failure(self, chat_id: int) -> None:
        await self._tg.send_message(chat_id, render.ERROR_TEXT)

    async def handle_update(self, update: dict[str, Any]) -> None:
        query = update.get("callback_query")
        if query:
            data = str(query.get("data") or "")
            if data.startswith(("ex:", "chp:")):
                await self._search_button(query, data)
            else:
                await self.shop.handle_callback(query)
            return
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (message.get("from") or {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or user_id is None:
            return
        if not text:
            await self._tg.send_message(chat_id, render.HELP_TEXT)
            return
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            await self._tg.send_message(chat_id, render.WELCOME_TEXT, render.example_buttons())
            await self._store.log_event(user_id, None, "help_shown", {"command": command})
            return
        cart_command = parse_cart_command(text)
        if cart_command is not None:  # deterministic, no LLM, no search lock (D47, D49)
            await self._store.log_event(user_id, None, "cart_command", {"action": cart_command.action,
                                                                         "labels": list(cart_command.labels)})
            await self.shop.run_command(chat_id, user_id, cart_command)
            return
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            await self._tg.send_message(chat_id, render.BUSY_TEXT)
            await self._store.log_event(user_id, None, "busy_rejected", {})
            return
        try:
            async with lock:
                if command == "/delete":  # inside both locks so a search or a tap can't re-create data
                    async with self.shop.lock(user_id):
                        await self._store.delete_user(user_id)
                    await self._tg.send_message(chat_id, render.DELETED_TEXT)
                    return
                await self._handle_request(chat_id, user_id, text[:MAX_MESSAGE_CHARS])
        finally:
            if not lock.locked():
                self._locks.pop(user_id, None)

    async def _search_button(self, query: dict[str, Any], data: str) -> None:
        """Example chips on /start and the one-tap "Cheaper" under each item (D57)."""
        user_id = (query.get("from") or {}).get("id")
        chat_id = ((query.get("message") or {}).get("chat") or {}).get("id") or user_id
        callback_id = query.get("id")
        if user_id is None or callback_id is None:
            return
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            await self.shop._answer(callback_id, "Still working on your last request…")
            return
        await self.shop._answer(callback_id, None)
        kind, _, rest = data.partition(":")
        try:
            async with lock:
                if kind == "ex" and rest.isdigit() and int(rest) < len(render.EXAMPLES):
                    await self._tg.send_message(chat_id, render.example_echo(int(rest)))
                    await self._handle_request(chat_id, user_id, render.EXAMPLES[int(rest)], fresh=True)
                elif kind == "chp" and rest.isdigit():
                    await self._cheaper(chat_id, user_id, int(rest))
        finally:
            if not lock.locked():
                self._locks.pop(user_id, None)

    async def _cheaper(self, chat_id: int, user_id: int, item_index: int) -> None:
        session = await self._store.get_session(user_id) or {}
        if not session.get("last_spec"):
            await self._tg.send_message(chat_id, "That search has expired. Send it again and I'll find cheaper ones.")
            return
        letter = chr(ord("A") + item_index)
        follow_up = FollowUp(action="refine", items=[letter], changes={"cheaper": True})
        await self._store.log_event(user_id, session["session_id"], "cheaper_tapped", {"item_index": item_index})
        await self._apply_follow_up(chat_id, user_id, session, follow_up)

    async def _handle_request(self, chat_id: int, user_id: int, text: str, fresh: bool = False) -> None:
        session = await self._store.get_session(user_id) or {}
        pending = session.get("pending_message") if _fresh(session.get("updated_at")) else None
        recent = (
            not fresh and not pending and session.get("last_spec")
            and _fresh(session.get("updated_at"), FOLLOWUP_TTL)
        )
        try:
            await self._tg.send_typing(chat_id)
        except SEND_ERRORS:
            log.warning("typing action failed")
        if recent and await self._follow_up(chat_id, user_id, session, text):
            return

        session_id = session.get("session_id") if pending else uuid.uuid4().hex
        context = clarification_context(pending, session.get("pending_questions") or []) if pending else None

        async def event(type_: str, payload: dict[str, Any]) -> None:
            await self._store.log_event(user_id, session_id, type_, payload)

        await event("request_received", {"text": text, "is_clarification_reply": bool(pending)})

        try:
            parsed = await self._parser.parse(text, context)
        except ParseError as err:
            await event("parse_failed", {"error": str(err)[:300]})
            await self._tg.send_message(chat_id, render.PARSE_FAILED_TEXT)
            await self._store.save_session(user_id, session_id, session.get("last_spec"), None, None)
            return
        spec = parsed.spec
        await event(
            "parsed",
            {"spec": spec.model_dump(), "attempts": parsed.attempts, "latency_ms": parsed.latency_ms, "model": parsed.model},
        )

        original = f"{pending}\n{text}" if pending else text
        if spec.ambiguities:
            questions = [a.question for a in spec.ambiguities]
            await self._tg.send_message(chat_id, "\n".join(render.escape_text(q) for q in questions))
            await self._store.save_session(user_id, session_id, spec.model_dump(), original, questions)
            await event("clarify_asked", {"questions": questions})
            return

        # saved before searching too, so a + tap on item A while B still searches sees this spec
        await self._store.save_session(user_id, session_id, spec.model_dump(), None, None)
        await self._search_and_reply(chat_id, user_id, session_id, spec, event)
        await self._store.save_session(user_id, session_id, spec.model_dump(), None, None)

    async def _follow_up(self, chat_id: int, user_id: int, session: dict, text: str) -> bool:
        """Handle a message about the last results. False = treat it as a new search."""
        session_id = session["session_id"]

        async def event(type_: str, payload: dict[str, Any]) -> None:
            await self._store.log_event(user_id, session_id, type_, payload)

        try:
            spec = RequestSpec.model_validate(session["last_spec"])
        except ValueError:
            return False
        shown, _ = await self._shown(user_id, session_id)
        cart = await self._store.cart(user_id)
        context = followup_context(spec, shown, [row["label"] for row in cart])
        await event("follow_up_received", {"text": text})
        try:
            result = await self._parser.classify(text, context)
        except ParseError as err:
            await event("classify_failed", {"error": str(err)[:300]})
            return False
        follow_up = result.follow_up
        await event("follow_up_classified", {"follow_up": follow_up.model_dump(), "attempts": result.attempts,
                                             "latency_ms": result.latency_ms, "model": result.model})
        if follow_up.action == "new_search":
            return False
        await self._apply_follow_up(chat_id, user_id, session, follow_up)
        return True

    async def _shown(self, user_id: int, session_id: str) -> tuple[dict[int, list[str]], dict[int, int]]:
        """Labels on screen per item, and the cheapest shown price (₹) per item."""
        shown: dict[int, list[str]] = {}
        cheapest: dict[int, int] = {}
        for row in await self._store.shown_in_session(user_id, session_id):
            index = row["option"]["item_index"]
            shown.setdefault(index, []).append(row["label"])
            price = row["option"]["price_paise"] // 100
            cheapest[index] = min(price, cheapest.get(index, price))
        return shown, cheapest

    async def _apply_follow_up(self, chat_id: int, user_id: int, session: dict, follow_up: FollowUp) -> None:
        session_id = session["session_id"]

        async def event(type_: str, payload: dict[str, Any]) -> None:
            await self._store.log_event(user_id, session_id, type_, payload)

        spec = RequestSpec.model_validate(session["last_spec"])
        action = follow_up.action
        if action in ("add", "remove", "show_cart", "checkout"):
            command = CartCommand(action, tuple(follow_up.labels))
            await self.shop.run_command(chat_id, user_id, command, tuple(follow_up.items))
            return
        if action == "refine":
            _, cheapest = await self._shown(user_id, session_id)
            refinement = apply_refinement(spec, follow_up, cheapest)
            if refinement.message:
                await self._tg.send_message(chat_id, refinement.message)
            if refinement.spec is None:
                if not refinement.message:
                    await self._tg.send_message(chat_id, render.OTHER_TEXT)
                return
            await event("refined", {"indices": list(refinement.indices), "spec": refinement.spec.model_dump()})
            await self._search_and_reply(chat_id, user_id, session_id, refinement.spec, event,
                                         only=set(refinement.indices))
            await self._store.save_session(user_id, session_id, refinement.spec.model_dump(), None, None)
            return
        await self._tg.send_message(chat_id, render.OTHER_TEXT)

    async def _search_and_reply(
        self, chat_id: int, user_id: int, session_id: str, spec: RequestSpec, event, only: set[int] | None = None
    ) -> None:
        chosen = [(i, item) for i, item in enumerate(spec.items) if only is None or i in only]
        in_scope = [(i, item) for i, item in chosen if item.in_scope]
        skipped = [item.query_terms for _, item in chosen if not item.in_scope]
        if skipped:
            await self._tg.send_message(chat_id, render.NOT_FASHION_TEXT.format(items=render.escape_text(", ".join(skipped))))
            await event("out_of_scope", {"items": skipped})
        if not in_scope:
            if not skipped:
                await self._tg.send_message(chat_id, render.PARSE_FAILED_TEXT)
                await event("no_items", {})
            return

        ack_id = await self._tg.send_message(chat_id, render.ack_text(len(in_scope)))
        done: list[str] = []
        tasks = [
            asyncio.create_task(self._run_item_with_retry(chat_id, idx, item, spec.item_cap(item), event))
            for idx, item in in_scope
        ]
        shown_any = False
        try:
            for finished in asyncio.as_completed(tasks):
                result = await finished
                if result is None:
                    continue
                try:
                    shown_any = await self._send_item(chat_id, user_id, session_id, result, event) or shown_any
                    done.append(chr(ord("A") + result.item_index))
                    await self._tick(chat_id, ack_id, len(in_scope), done)
                except Exception as err:  # one item failing must not drop the others
                    log.exception("sending item %s failed", result.item_index)
                    await event("send_failed", {"item_index": result.item_index, "error": repr(err)[:300]})
                    await self._tg.send_message(chat_id, render.ERROR_TEXT)
        finally:
            for task in tasks:
                task.cancel()
        if shown_any:
            await self._tg.send_message(
                chat_id,
                render.footer_text(spec.total_max_inr if spec.budget_type == "total" else None),
                render.cart_shortcut_buttons(),
            )

    async def _tick(self, chat_id: int, ack_id: int | None, count: int, done: list[str]) -> None:
        """Edit "Searching 2 items…" to "Searching 2 items… A ✓" as each item lands."""
        if ack_id is None or count < 2:
            return
        try:
            await self._tg.edit_message_text(chat_id, ack_id, render.progress_text(count, done))
        except SEND_ERRORS:
            log.warning("progress edit failed")

    async def _run_item_with_retry(
        self, chat_id: int, idx: int, item: ItemSpec, cap: int | None, event
    ) -> ItemResult | None:
        for attempt in range(1, ITEM_ATTEMPTS + 1):
            try:
                return await run_item(self._catalog, idx, item, cap, on_event=event)
            except ITEM_ERRORS as err:
                await event("catalog_error", {"item_index": idx, "attempt": attempt, "error": str(err)[:300]})
                if attempt < ITEM_ATTEMPTS:
                    await self._tg.send_message(chat_id, render.SLOW_TEXT)
        heading = render.item_heading(item, cap)
        await self._tg.send_message(chat_id, f"{render.ERROR_TEXT}\n(<i>{render.escape_text(heading)}</i>)")
        return None

    async def _send_item(self, chat_id: int, user_id: int, session_id: str, result: ItemResult, event) -> bool:
        tokens, result_ids = await self._store.save_results_with_ids(user_id, session_id, list(result.options))
        if self._base:
            links = {label: f"{self._base}/r/{token}" for label, token in tokens.items()}
        else:  # local polling mode: no public server, so link straight to the store (clicks untracked)
            by_label = {o.label: o.product_url for o in result.options}
            links = {label: by_label[label] for label in tokens}
        await self._tg.send_message(
            chat_id, render.item_header(result), render.cheaper_buttons(result.item_index) if result.options else None
        )
        for option in result.options:  # one photo card per suggestion (D62)
            caption = render.option_caption(option)
            buttons = render.card_buttons(option, links.get(option.label), result_ids.get(option.label))
            if option.image_url:
                try:
                    await self._tg.send_photo(chat_id, option.image_url, caption, buttons)
                    continue
                except SEND_ERRORS as err:  # bad image URL: the same card as text, nothing lost
                    await event("photo_failed", {"label": option.label, "error": str(err)[:200]})
            await self._tg.send_message(chat_id, caption, buttons)
        if result.options:
            await event(
                "results_shown",
                {
                    "item_index": result.item_index,
                    "labels": [o.label for o in result.options],
                    "linked": sorted(links),
                    "sellers": [o.seller_name for o in result.options],
                },
            )
            return True
        await event("zero_results", {"item_index": result.item_index, "item": result.item.model_dump()})
        return False


def _fresh(updated_at: datetime | None, ttl: timedelta | None = None) -> bool:
    ttl = PENDING_TTL if ttl is None else ttl
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:  # SQLite returns naive datetimes; we always store UTC
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at < ttl


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS)
