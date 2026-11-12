"""Cart, checkout handoff and the "did you buy?" follow-up (Rung 2, D49-D51).

    button tap / "add A1" ─▶ cart lock ─▶ add | remove | show | clear | checkout
    checkout ─▶ re-check stock + price ─▶ one permalink per merchant ─▶ schedule follow-up
    /tasks/followups (cron) ─▶ due follow-ups ─▶ "did you buy?" buttons
    tap ─▶ purchase_reported | purchase_declined (+ why) | purchase_later (re-ask once)

Cart actions take a per-user cart lock, separate from the search lock, so a user can
tap + on item A while item B is still searching.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import render
from .checkout import CartLine, MerchantCheckout, build_checkouts
from .commands import CartCommand
from .models import Option
from .pipeline import Catalog
from .store import CHECKOUT_LABEL, MAX_CART_LINES, Store, now
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

SEND_ERRORS = (TelegramError, httpx.HTTPError)
MAX_FOLLOWUP_PINGS = 2  # the first ask, plus one more after "Not yet"


def _option(row: dict) -> Option:
    return Option(**row["option"])


def _lines(cart: list[dict]) -> list[tuple[int, Option, int]]:
    return [(row["id"], _option(row), row["quantity"]) for row in cart]


class Shop:
    def __init__(
        self,
        store: Store,
        telegram: TelegramClient,
        catalog: Catalog,
        public_base_url: str | None,
        followup_delay: timedelta,
    ) -> None:
        self._store = store
        self._tg = telegram
        self._catalog = catalog
        self._base = public_base_url
        self.followup_delay = followup_delay
        self._locks: dict[int, asyncio.Lock] = {}

    def lock(self, user_id: int) -> asyncio.Lock:
        """The user's cart lock (also taken by /delete so a tap can't re-create data)."""
        return self._lock(user_id)

    def _lock(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def _event(self, user_id: int, session_id: str | None, type_: str, payload: dict[str, Any]) -> None:
        await self._store.log_event(user_id, session_id, type_, payload)

    # --- text commands ------------------------------------------------------------

    async def run_command(
        self, chat_id: int, user_id: int, command: CartCommand, items: tuple[str, ...] = ()
    ) -> None:
        async with self._lock(user_id):
            if command.action == "add":
                await self._add_labels(chat_id, user_id, command.labels)
            elif command.action == "remove":
                await self._remove(chat_id, user_id, command.labels, items)
            elif command.action == "show_cart":
                await self.send_cart(chat_id, user_id)
            elif command.action == "clear":
                await self._store.clear_cart(user_id)
                await self._event(user_id, None, "cart_cleared", {})
                await self._tg.send_message(chat_id, render.CART_CLEARED_TEXT)
            elif command.action == "checkout":
                await self._checkout(chat_id, user_id)

    async def send_cart(self, chat_id: int, user_id: int, prefix: str | None = None) -> None:
        lines = _lines(await self._store.cart(user_id))
        text = render.cart_text(lines)
        if prefix:
            text = f"{prefix}\n\n{text}"
        await self._tg.send_message(chat_id, text, render.cart_buttons(lines) or None)

    async def _quantity(self, user_id: int, result: dict) -> int:
        session = await self._store.get_session(user_id) or {}
        spec = session.get("last_spec") if session.get("session_id") == result["session_id"] else None
        index = result["option"].get("item_index", 0)
        try:
            return int(spec["items"][index]["quantity"]) if spec else 1
        except (KeyError, IndexError, TypeError, ValueError):
            return 1

    async def _add_result(self, user_id: int, result: dict) -> str:
        quantity = await self._quantity(user_id, result)
        outcome = await self._store.add_to_cart(user_id, result["session_id"], result["option"], quantity)
        await self._event(
            user_id, result["session_id"], "cart_add",
            {"label": result["label"], "outcome": outcome, "seller": result["option"].get("seller_name")},
        )
        return outcome

    async def _add_labels(self, chat_id: int, user_id: int, labels: tuple[str, ...]) -> None:
        if not labels:
            await self._tg.send_message(chat_id, "Which one? Send it like <i>add A1</i>.")
            return
        session = await self._store.get_session(user_id) or {}
        on_screen = {}
        if session.get("session_id"):
            on_screen = {r["label"]: r for r in await self._store.shown_in_session(user_id, session["session_id"])}
        added, already, missing, full = [], [], [], False
        for label in labels:
            result = on_screen.get(label)
            if result is None:
                missing.append(label)
                continue
            outcome = await self._add_result(user_id, result)
            if outcome == "full":
                full = True
                break
            (added if outcome == "added" else already).append(label)
        notes = []
        if added:
            notes.append("Added " + ", ".join(added) + ".")
        if already:
            notes.append(", ".join(already) + " already in your cart.")
        if missing:
            notes.append(render.UNKNOWN_LABELS_TEXT.format(labels=", ".join(missing)))
        if full:
            notes.append(render.CART_FULL_TEXT.format(max=MAX_CART_LINES))
        await self.send_cart(chat_id, user_id, prefix=" ".join(notes))

    async def _remove(self, chat_id: int, user_id: int, labels: tuple[str, ...], items: tuple[str, ...]) -> None:
        cart = await self._store.cart(user_id)
        wanted = set(labels)
        letters = set(items)
        session_id = (await self._store.get_session(user_id) or {}).get("session_id")
        indices = {ord(letter) - ord("A") for letter in letters}

        def matches(row: dict) -> bool:
            if row["label"] in wanted:
                return True  # labels are what the user sees in the cart
            # "remove the jeans" names an item of the current search, not every old "A" line
            return row["session_id"] == session_id and row["option"].get("item_index") in indices

        ids = [row["id"] for row in cart if matches(row)]
        removed = await self._store.remove_cart_lines(user_id, ids)
        await self._event(user_id, None, "cart_remove", {"labels": sorted(wanted), "items": sorted(letters), "removed": removed})
        prefix = "Removed." if removed else "That wasn't in your cart."
        await self.send_cart(chat_id, user_id, prefix=prefix)

    # --- checkout (D50) -----------------------------------------------------------

    async def _checkout(self, chat_id: int, user_id: int) -> None:
        cart = await self._store.cart(user_id)
        if not cart:
            await self._tg.send_message(chat_id, render.CART_EMPTY_TEXT)
            return
        lines = [CartLine(row["id"], _option(row), row["quantity"]) for row in cart]
        session = await self._store.get_session(user_id) or {}
        session_id = session.get("session_id") or cart[-1]["session_id"]
        await self._tg.send_message(chat_id, "Checking stock and prices with each brand…")
        checkouts, unlinkable = await build_checkouts(self._catalog, lines)
        sent: list[dict] = []
        for checkout in checkouts:
            if await self._send_checkout(chat_id, user_id, session_id, checkout):
                sent.append({"name": checkout.seller_name, "host": checkout.host})
        if unlinkable:
            await self._tg.send_message(chat_id, render.unlinkable_text([l.option for l in unlinkable]))
        if sent:
            await self._tg.send_message(chat_id, render.checkout_footer(len(sent)))
            await self._store.schedule_followup(user_id, session_id, sent, now() + self.followup_delay)
        await self._event(
            user_id, session_id, "checkout_sent",
            {"merchants": [m["host"] for m in sent], "unlinkable": [l.option.label for l in unlinkable]},
        )

    async def _send_checkout(self, chat_id: int, user_id: int, session_id: str, checkout: MerchantCheckout) -> bool:
        statuses = [
            {"label": c.line.option.label, "status": c.status, "price_paise": c.price_paise,
             "old_price_paise": c.old_price_paise, "quantity": c.line.quantity, "variant_id": c.line.option.variant_id}
            for c in checkout.lines
        ]
        await self._event(
            user_id, session_id, "checkout_built",
            {"host": checkout.host, "lines": statuses, "total_paise": checkout.total_paise,
             "has_link": checkout.permalink is not None},
        )
        if checkout.permalink is None:
            await self._tg.send_message(chat_id, render.checkout_text(checkout))
            return False
        token = await self._store.save_checkout(
            user_id, session_id, checkout.seller_name, checkout.host, checkout.permalink, statuses,
            checkout.total_paise,
        )
        url = f"{self._base}/r/{token}" if self._base else checkout.permalink
        await self._tg.send_message(chat_id, render.checkout_text(checkout), render.pay_button(checkout, url))
        return True

    # --- clicks (called by /r) ----------------------------------------------------

    async def record_click(self, row: dict) -> None:
        """Log a click-out and, for product links, schedule the follow-up for that brand."""
        user_id, session_id, label = row["telegram_user_id"], row["session_id"], row["label"]
        if label == CHECKOUT_LABEL:
            checkout = await self._store.checkout_for_token(row["token"])
            await self._event(user_id, session_id, "checkout_clicked",
                              {"token": row["token"], "host": (checkout or {}).get("host")})
            return
        await self._event(user_id, session_id, "link_clicked", {"label": label, "token": row["token"]})
        result = await self._store.result_for_label(user_id, session_id, label, row.get("created_at"))
        if result is None:
            return
        option = result["option"]
        host = (urlsplit(option.get("product_url") or "").hostname or "").lower()
        if host:
            merchant = {"name": (option.get("seller_name") or host).strip(), "host": host}
            await self._store.schedule_followup(user_id, session_id, [merchant], now() + self.followup_delay)

    # --- follow-ups (D51) ---------------------------------------------------------

    async def send_due_followups(self) -> int:
        """Send due pings. Each row is claimed atomically first, so overlapping cron runs
        can't double-send, and a failure on one row never blocks the rest."""
        sent = 0
        for followup in await self._store.due_followups():
            try:
                if await self._send_followup(followup):
                    sent += 1
            except Exception:  # noqa: BLE001 - one bad row must not starve the batch
                log.exception("follow-up %s failed", followup["id"])
        return sent

    async def _send_followup(self, followup: dict) -> bool:
        if not await self._store.claim_followup(followup["id"], followup["sent_count"]):
            return False  # another run took it
        user_id = followup["telegram_user_id"]  # private chat id == user id
        merchants = followup["merchants"] or []
        if not merchants:
            return False
        try:
            await self._tg.send_message(
                user_id, render.followup_text(merchants), render.followup_buttons(followup["id"], merchants)
            )
        except SEND_ERRORS as err:  # e.g. the user blocked the bot: claimed, so never retried
            log.warning("follow-up %s not delivered: %s", followup["id"], err)
            await self._event(user_id, followup["session_id"], "followup_failed", {"error": str(err)[:200]})
            return False
        await self._event(user_id, followup["session_id"], "followup_sent",
                          {"followup_id": followup["id"], "merchants": [m["host"] for m in merchants],
                           "ping": followup["sent_count"] + 1})
        return True

    # --- button taps --------------------------------------------------------------

    async def handle_callback(self, query: dict[str, Any]) -> None:
        callback_id = query.get("id")
        user_id = (query.get("from") or {}).get("id")
        chat_id = ((query.get("message") or {}).get("chat") or {}).get("id") or user_id
        data = str(query.get("data") or "")
        if callback_id is None or user_id is None:
            return
        message = query.get("message") or {}
        if data in ("cart", "co", "clr"):  # these send messages; stop the spinner first
            await self._answer(callback_id, None)
            await self._route_callback(chat_id, user_id, data, message)
            return
        toast: str | None = None
        try:
            toast = await self._route_callback(chat_id, user_id, data, message)
        finally:
            await self._answer(callback_id, toast)

    async def _answer(self, callback_id: str, toast: str | None) -> None:
        try:
            await self._tg.answer_callback(callback_id, toast)
        except SEND_ERRORS:
            log.warning("answerCallbackQuery failed")

    async def _route_callback(self, chat_id: int, user_id: int, data: str, message: dict) -> str | None:
        kind, _, rest = data.partition(":")
        if kind == "add" and rest.isdigit():
            async with self._lock(user_id):
                toast, label = await self._tap_add(user_id, int(rest))
            if label:
                await self._mark_added(chat_id, message, data, label)
            return toast
        if kind == "rm" and rest.isdigit():
            async with self._lock(user_id):
                await self._remove_line(chat_id, user_id, int(rest), message.get("message_id"))
            return "Removed"
        if kind == "cart":
            await self.run_command(chat_id, user_id, CartCommand("show_cart"))
            return None
        if kind == "co":
            await self.run_command(chat_id, user_id, CartCommand("checkout"))
            return None
        if kind == "clr":
            async with self._lock(user_id):
                await self._store.clear_cart(user_id)
                await self._event(user_id, None, "cart_cleared", {})
                await self.update_cart_message(chat_id, user_id, message.get("message_id"))
            return None
        if kind in ("fb", "why"):
            followup_id, _, answer = rest.partition(":")
            if followup_id.isdigit():
                return await self._answer_followup(chat_id, user_id, kind, int(followup_id), answer)
        await self._event(user_id, None, "callback_unknown", {"data": data[:64]})
        return "That button has expired."

    async def _tap_add(self, user_id: int, result_id: int) -> tuple[str, str | None]:
        """(toast, label to tick on the button or None)."""
        result = await self._store.get_result(user_id, result_id)
        if result is None:
            return "That option has expired. Search again.", None
        outcome = await self._add_result(user_id, result)
        if outcome == "full":
            return f"Cart is full ({MAX_CART_LINES} items)", None
        count = len(await self._store.cart(user_id))
        label = result["label"]
        if outcome == "already":
            return f"{label} is already in your cart ({count})", label
        return f"Added {label} · cart has {count} item{'s' if count != 1 else ''}", label

    async def _mark_added(self, chat_id: int, message: dict, data: str, label: str) -> None:
        """Show "✓ A1" on the tapped button, so the results message reflects the cart."""
        keyboard = (message.get("reply_markup") or {}).get("inline_keyboard")
        if not keyboard or message.get("message_id") is None:
            return
        updated = render.mark_added(keyboard, data, label)
        if updated == keyboard:
            return
        try:
            await self._tg.edit_buttons(chat_id, message["message_id"], updated)
        except SEND_ERRORS:
            log.warning("could not tick the + button")

    async def _remove_line(self, chat_id: int, user_id: int, line_id: int, message_id: int | None) -> None:
        removed = await self._store.remove_cart_lines(user_id, [line_id])
        await self._event(user_id, None, "cart_remove", {"line_id": line_id, "removed": removed})
        await self.update_cart_message(chat_id, user_id, message_id)

    async def update_cart_message(self, chat_id: int, user_id: int, message_id: int | None) -> None:
        """Edit the cart message in place; fall back to a new message."""
        lines = _lines(await self._store.cart(user_id))
        if message_id is not None:
            try:
                await self._tg.edit_message_text(chat_id, message_id, render.cart_text(lines), render.cart_buttons(lines))
                return
            except SEND_ERRORS:
                log.warning("cart edit failed, sending a new message")
        await self.send_cart(chat_id, user_id)

    async def _answer_followup(
        self, chat_id: int, user_id: int, kind: str, followup_id: int, answer: str
    ) -> str:
        followup = await self._store.get_followup(user_id, followup_id)
        if followup is None:
            return "That question has expired."
        session_id = followup["session_id"]
        merchants = followup["merchants"] or []
        if kind == "why":
            await self._event(user_id, session_id, "purchase_blocker", {"followup_id": followup_id, "reason": answer[:20]})
            return render.FOLLOWUP_THANKS
        if answer.isdigit() and int(answer) < len(merchants):
            merchant = merchants[int(answer)]
            await self._store.close_followup(followup_id)
            await self._event(user_id, session_id, "purchase_reported",
                              {"followup_id": followup_id, "host": merchant["host"], "name": merchant["name"]})
            return f"Nice! Noted your order from {merchant['name']}."
        if answer == "no":
            await self._store.close_followup(followup_id)
            await self._event(user_id, session_id, "purchase_declined", {"followup_id": followup_id})
            await self._tg.send_message(chat_id, render.BLOCKER_QUESTION, render.blocker_buttons(followup_id))
            return render.FOLLOWUP_THANKS
        if answer == "later":
            again = followup["sent_count"] < MAX_FOLLOWUP_PINGS and not followup["answered"]
            if again:
                again = await self._store.snooze_followup(followup_id, now() + self.followup_delay)
            await self._event(user_id, session_id, "purchase_later", {"followup_id": followup_id, "will_ask_again": again})
            return "OK, I'll check once more later." if again else render.FOLLOWUP_THANKS
        return "That button has expired."
