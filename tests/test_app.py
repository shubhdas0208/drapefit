"""HTTP layer: webhook auth + dedupe, redirect, health, tasks."""
import asyncio

import httpx
import pytest

from drapefit.app import Services, create_app
from drapefit.bot import Bot
from drapefit.config import Settings
from drapefit.telegram import webhook_token

from .conftest import FakeCatalog, FakeParser, FakeTelegram, item, load_search, spec, tg_update

SECRET = "webhook+secret/123="  # Render-style value with characters Telegram rejects
TASKS = "tasks-secret-456"
HEADER = webhook_token(SECRET)


def settings() -> Settings:
    return Settings(
        telegram_bot_token="t", telegram_webhook_secret=SECRET, tasks_secret=TASKS, sarvam_api_key="s",
        sarvam_model="m", shopify_client_id="c", shopify_client_secret="x", database_url="sqlite://",
        public_base_url="https://drapefit.example", agent_profile_url="https://p",
    )


@pytest.fixture
async def setup(store):
    tg = FakeTelegram()
    parser = FakeParser(*[spec(item())] * 5)
    bot = Bot(store, tg, parser, FakeCatalog(load_search("01-grey-turtleneck-men")), "https://drapefit.example")
    app = create_app(Services(settings=settings(), store=store, bot=bot, http=None))
    APP["app"] = app
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, tg, store, parser


APP = {}


async def drain():
    tasks = list(APP["app"].state.background)
    if tasks:
        await asyncio.gather(*tasks)


async def test_webhook_rejects_missing_or_wrong_secret(setup):
    client, tg, _, _ = setup
    assert (await client.post("/telegram/webhook", json=tg_update(1, "hi"))).status_code == 401
    r = await client.post("/telegram/webhook", json=tg_update(1, "hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "no"})
    assert r.status_code == 401
    assert tg.texts == []


async def test_webhook_processes_once_and_dedupes_retries(setup):
    client, tg, store, parser = setup
    headers = {"X-Telegram-Bot-Api-Secret-Token": HEADER}
    r1 = await client.post("/telegram/webhook", json=tg_update(10, "grey turtleneck M"), headers=headers)
    r2 = await client.post("/telegram/webhook", json=tg_update(10, "grey turtleneck M"), headers=headers)
    assert r1.json() == {"ok": True} and r2.json()["duplicate"] is True
    await drain()
    assert len(parser.calls) == 1
    assert any(t.startswith("<b>A ·") for t in tg.texts)


async def test_malformed_update_is_acked(setup):
    client, *_ = setup
    r = await client.post("/telegram/webhook", content=b"not json", headers={"X-Telegram-Bot-Api-Secret-Token": HEADER})
    assert r.status_code == 200


async def test_click_redirect_logs_and_302s(setup):
    client, tg, store, _ = setup
    await client.post("/telegram/webhook", json=tg_update(11, "grey turtleneck M"),
                      headers={"X-Telegram-Bot-Api-Secret-Token": HEADER})
    await drain()
    item_text = next(t for t in tg.texts if t.startswith("<b>A ·"))
    token = item_text.split("/r/")[1].split('"')[0]
    r = await client.get(f"/r/{token}")
    assert r.status_code == 302 and r.headers["location"].startswith("https://")
    clicks = [e for e in await store.events_for(42) if e["type"] == "link_clicked"]
    assert clicks and clicks[0]["payload"]["label"] == "A1"


@pytest.mark.parametrize("token", ["unknown-token-123", "x", "bad$token!", "a" * 40])
async def test_unknown_or_malformed_token_404s(setup, token):
    client, *_ = setup
    r = await client.get(f"/r/{token}")
    assert r.status_code == 404 and "expired" in r.text


async def test_healthz(setup):
    client, *_ = setup
    assert (await client.get("/healthz")).json() == {"ok": True}


async def test_tasks_requires_secret(setup):
    client, *_ = setup
    assert (await client.post("/tasks/followups")).status_code == 401
    r = await client.post("/tasks/followups", headers={"X-Tasks-Secret": TASKS})
    assert r.json()["pruned"] is True


async def test_unexpected_bot_error_is_reported_not_crashed(setup):
    client, tg, store, parser = setup
    parser.responses = [{"items": "not-a-list"}]  # makes FakeParser raise a pydantic error
    await client.post("/telegram/webhook", json=tg_update(12, "boom"), headers={"X-Telegram-Bot-Api-Secret-Token": HEADER})
    await drain()
    assert tg.texts[-1].startswith("Something went wrong on my side")
    assert "error" in [e["type"] for e in await store.events_for(42)]


def test_settings_fail_fast(monkeypatch):
    from drapefit import config

    monkeypatch.setattr(config, "_read_env", lambda name: None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        config.load_settings()


async def test_redirect_survives_event_log_failure(setup, monkeypatch):
    client, tg, store, _ = setup
    await client.post("/telegram/webhook", json=tg_update(13, "grey turtleneck M"),
                      headers={"X-Telegram-Bot-Api-Secret-Token": HEADER})
    await drain()
    token = next(t for t in tg.texts if t.startswith("<b>A ·")).split("/r/")[1].split('"')[0]

    async def boom(*a, **k):
        raise RuntimeError("neon down")

    monkeypatch.setattr(store, "log_event", boom)
    assert (await client.get(f"/r/{token}")).status_code == 302


async def test_non_ascii_secret_is_401_not_500(setup):
    client, *_ = setup
    r = await client.post("/telegram/webhook", json=tg_update(14, "x"),
                          headers={"X-Telegram-Bot-Api-Secret-Token": "sécret".encode("latin-1")})
    assert r.status_code == 401


async def test_button_tap_through_webhook_adds_to_cart(setup):
    from .conftest import tg_callback

    client, tg, store, _ = setup
    headers = {"X-Telegram-Bot-Api-Secret-Token": HEADER}
    await client.post("/telegram/webhook", json=tg_update(20, "grey turtleneck M"), headers=headers)
    await drain()
    add = tg.buttons_for("<b>A ·")[0][0]["callback_data"]
    await client.post("/telegram/webhook", json=tg_callback(21, add), headers=headers)
    await drain()
    assert [c["label"] for c in await store.cart(42)] == ["A1"]
    assert tg.callback_answers[-1][1].startswith("Added A1")


async def test_product_click_schedules_followup_and_cron_sends_it(setup):
    from datetime import timedelta

    client, tg, store, _ = setup
    app = APP["app"]
    app.state.svc.bot.shop.followup_delay = timedelta(seconds=-1)
    await client.post("/telegram/webhook", json=tg_update(22, "grey turtleneck M"),
                      headers={"X-Telegram-Bot-Api-Secret-Token": HEADER})
    await drain()
    token = next(t for t in tg.texts if t.startswith("<b>A ·")).split("/r/")[1].split('"')[0]
    assert (await client.get(f"/r/{token}")).status_code == 302
    r = await client.post("/tasks/followups", headers={"X-Tasks-Secret": TASKS})
    assert r.json()["followups_sent"] == 1
    assert tg.texts[-1].startswith("Quick one: did you buy anything from")
    assert (await client.post("/tasks/followups", headers={"X-Tasks-Secret": TASKS})).json()["followups_sent"] == 0


async def test_checkout_link_click_is_logged_as_checkout_clicked(setup):
    client, tg, store, _ = setup
    headers = {"X-Telegram-Bot-Api-Secret-Token": HEADER}
    await client.post("/telegram/webhook", json=tg_update(23, "grey turtleneck M"), headers=headers)
    await drain()
    await client.post("/telegram/webhook", json=tg_update(24, "add A1"), headers=headers)
    await drain()
    await client.post("/telegram/webhook", json=tg_update(25, "checkout"), headers=headers)
    await drain()
    # FakeCatalog variant ids aren't numeric, so build a checkout row directly for the redirect
    token = await store.save_checkout(42, "s1", "Brand", "brand.example", "https://brand.example/cart/1:1", [], 100)
    r = await client.get(f"/r/{token}")
    assert r.status_code == 302 and r.headers["location"] == "https://brand.example/cart/1:1"
    clicked = [e for e in await store.events_for(42) if e["type"] == "checkout_clicked"]
    assert clicked and clicked[0]["payload"]["host"] == "brand.example"


def test_env_local_file_is_read(tmp_path):
    from drapefit import config

    env = tmp_path / ".env.local"
    env.write_text('# neon\nexport DATABASE_URL="postgresql://u:p@h/db"\nOTHER=1\nEMPTY=\n', encoding="utf-8")
    assert config._read_env_file("DATABASE_URL", str(env)) == "postgresql://u:p@h/db"
    assert config._read_env_file("EMPTY", str(env)) is None
    assert config._read_env_file("MISSING", str(env)) is None
    assert config._read_env_file("X", str(tmp_path / "nope")) is None


def test_public_url_falls_back_to_render_external_url(monkeypatch):
    from drapefit import config

    env = {n: "x" for n in config.REQUIRED if n != "PUBLIC_BASE_URL"}
    env["RENDER_EXTERNAL_URL"] = "https://drapefit-abc.onrender.com/"
    monkeypatch.setattr(config, "_read_env", lambda name: env.get(name))
    assert config.load_settings().public_base_url == "https://drapefit-abc.onrender.com"


async def test_raw_secret_is_not_accepted_only_its_token(setup):
    client, *_ = setup
    r = await client.post("/telegram/webhook", json=tg_update(30, "hi"), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 401
    import re

    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", HEADER)
