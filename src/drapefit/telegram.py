"""Minimal Telegram Bot API client (raw HTTP, no framework)."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx

from . import config

MAX_ALBUM = 10
UPDATE_TYPES = ["message", "callback_query"]
MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 10

# A button row is a list of {"text": ..., "callback_data": ...} or {"text": ..., "url": ...}.
Buttons = list[list[dict[str, str]]]


def webhook_token(secret: str) -> str:
    """Telegram's secret_token allows only A-Z a-z 0-9 _ - (max 256). Render's generated
    secrets can contain other characters, so both sides use the SHA-256 hex of the secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._base = f"{config.TELEGRAM_API}/bot{token}"

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            resp = await self._http.post(f"{self._base}/{method}", json=payload, timeout=config.HTTP_TIMEOUT_SECONDS)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            retry_after = (body.get("parameters") or {}).get("retry_after")
            # A search now sends ~6 messages per item; Telegram may ask us to slow down (429).
            should_wait = (
                resp.status_code == 429
                and attempt < MAX_RATE_LIMIT_RETRIES
                and isinstance(retry_after, (int, float))
                and retry_after <= MAX_RETRY_AFTER_SECONDS
            )
            if should_wait:
                await asyncio.sleep(retry_after)
                continue
            break
        if resp.status_code != 200 or not body.get("ok"):
            # Never include the URL: it contains the bot token.
            raise TelegramError(f"{method}: HTTP {resp.status_code} {str(body.get('description', ''))[:200]}")
        return body.get("result")

    async def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> int | None:
        """Returns the sent message_id (used to edit it in place later)."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        result = await self._call("sendMessage", payload)
        return result.get("message_id") if isinstance(result, dict) else None

    async def send_photo(
        self, chat_id: int, photo_url: str, caption: str, buttons: Buttons | None = None
    ) -> int | None:
        """One product card: photo + HTML caption + buttons. Returns the message_id."""
        payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        result = await self._call("sendPhoto", payload)
        return result.get("message_id") if isinstance(result, dict) else None

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        """Stops the button's loading spinner; `text` shows as a small toast."""
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        await self._call("answerCallbackQuery", payload)

    async def send_photos(self, chat_id: int, photos: tuple[tuple[str, str], ...]) -> None:
        """One photo -> sendPhoto; 2-10 -> sendMediaGroup (albums can't carry buttons)."""
        if not photos:
            return
        if len(photos) == 1:
            url, caption = photos[0]
            await self._call("sendPhoto", {"chat_id": chat_id, "photo": url, "caption": caption})
            return
        media = [{"type": "photo", "media": url, "caption": caption} for url, caption in photos[:MAX_ALBUM]]
        await self._call("sendMediaGroup", {"chat_id": chat_id, "media": media})

    async def send_typing(self, chat_id: int) -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def set_webhook(self, url: str, secret: str) -> None:
        await self._call(
            "setWebhook",
            {"url": url, "secret_token": webhook_token(secret), "allowed_updates": UPDATE_TYPES,
             "drop_pending_updates": True},
        )

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons is not None:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        await self._call("editMessageText", payload)

    async def edit_buttons(self, chat_id: int, message_id: int, buttons: Buttons) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": buttons}},
        )

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """The persistent / menu in the chat."""
        await self._call(
            "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]}
        )
