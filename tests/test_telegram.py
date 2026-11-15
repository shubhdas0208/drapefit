import json

import httpx
import pytest
import respx

from drapefit.telegram import TelegramClient, TelegramError

API = "https://api.telegram.org/botTOKEN"


def ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
async def tg():
    async with httpx.AsyncClient() as http:
        yield TelegramClient(http, "TOKEN")


@respx.mock
async def test_send_message_uses_html_and_no_preview(tg):
    route = respx.post(f"{API}/sendMessage").mock(return_value=ok())
    await tg.send_message(1, "<b>hi</b>")
    body = json.loads(route.calls[0].request.content)
    assert body == {"chat_id": 1, "text": "<b>hi</b>", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


@respx.mock
async def test_photos_single_vs_album(tg):
    one = respx.post(f"{API}/sendPhoto").mock(return_value=ok())
    album = respx.post(f"{API}/sendMediaGroup").mock(return_value=ok())
    await tg.send_photos(1, ())
    await tg.send_photos(1, (("https://i/1.jpg", "A1"),))
    await tg.send_photos(1, tuple((f"https://i/{i}.jpg", f"A{i}") for i in range(12)))
    assert one.call_count == 1 and album.call_count == 1
    assert len(json.loads(album.calls[0].request.content)["media"]) == 10


@respx.mock
async def test_error_never_leaks_token(tg):
    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(TelegramError) as err:
        await tg.send_message(1, "x")
    assert "chat not found" in str(err.value) and "TOKEN" not in str(err.value)


@respx.mock
async def test_non_json_error(tg):
    respx.post(f"{API}/sendChatAction").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(TelegramError):
        await tg.send_typing(1)


@respx.mock
async def test_set_webhook_sends_secret_and_message_and_button_updates(tg):
    route = respx.post(f"{API}/setWebhook").mock(return_value=ok())
    await tg.set_webhook("https://x/telegram/webhook", "s3cret")
    body = json.loads(route.calls[0].request.content)
    from drapefit.telegram import webhook_token

    assert body["secret_token"] == webhook_token("s3cret") and body["allowed_updates"] == ["message", "callback_query"]


@respx.mock
async def test_send_message_with_inline_buttons(tg):
    route = respx.post(f"{API}/sendMessage").mock(return_value=ok())
    buttons = [[{"text": "+ A1", "callback_data": "add:1"}]]
    await tg.send_message(1, "x", buttons)
    body = json.loads(route.calls[0].request.content)
    assert body["reply_markup"] == {"inline_keyboard": buttons}


@respx.mock
async def test_answer_callback_with_toast(tg):
    route = respx.post(f"{API}/answerCallbackQuery").mock(return_value=ok())
    await tg.answer_callback("abc", "Added A1")
    await tg.answer_callback("def")
    assert json.loads(route.calls[0].request.content) == {"callback_query_id": "abc", "text": "Added A1"}
    assert json.loads(route.calls[1].request.content) == {"callback_query_id": "def"}


@respx.mock
async def test_send_returns_message_id_and_edits(tg):
    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 77}}))
    edit = respx.post(f"{API}/editMessageText").mock(return_value=ok())
    markup = respx.post(f"{API}/editMessageReplyMarkup").mock(return_value=ok())
    commands = respx.post(f"{API}/setMyCommands").mock(return_value=ok())
    assert await tg.send_message(1, "x") == 77
    await tg.edit_message_text(1, 77, "y", [[{"text": "a", "callback_data": "b"}]])
    await tg.edit_buttons(1, 77, [])
    await tg.set_commands([("cart", "Show my cart")])
    assert json.loads(edit.calls[0].request.content)["message_id"] == 77
    assert json.loads(markup.calls[0].request.content)["reply_markup"] == {"inline_keyboard": []}
    assert json.loads(commands.calls[0].request.content)["commands"] == [{"command": "cart", "description": "Show my cart"}]


@respx.mock
async def test_send_photo_card_and_rate_limit_retry(tg, monkeypatch):
    import asyncio as _asyncio

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)
    route = respx.post(f"{API}/sendPhoto").mock(side_effect=[
        httpx.Response(429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}}),
        httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}),
    ])
    buttons = [[{"text": "+ Add A1", "callback_data": "add:1"}]]
    assert await tg.send_photo(1, "https://i/1.jpg", "<b>A1</b>", buttons) == 9
    body = json.loads(route.calls[1].request.content)
    assert body["parse_mode"] == "HTML" and body["reply_markup"] == {"inline_keyboard": buttons}
    assert slept == [3]


@respx.mock
async def test_long_retry_after_is_not_waited_for(tg, monkeypatch):
    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(
        429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 120}}))
    with pytest.raises(TelegramError):
        await tg.send_message(1, "x")
