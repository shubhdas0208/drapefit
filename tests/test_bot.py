"""Conversation flow, end to end with fakes for Telegram, Sarvam and the catalog
and a real SQLite store."""
import asyncio

import pytest

from drapefit.bot import Bot

from .conftest import FakeCatalog, FakeParser, FakeTelegram, item, load_search, spec, tg_update

BASE = "https://drapefit.example"


def make_bot(store, parser, catalog=None, telegram=None):
    catalog = catalog or FakeCatalog(load_search("01-grey-turtleneck-men") + load_search("05-navy-chinos-men"))
    telegram = telegram or FakeTelegram()
    return Bot(store, telegram, parser, catalog, BASE), telegram, catalog


def event_types(events):
    return [e["type"] for e in events]


async def test_two_item_request_end_to_end(store):
    parser = FakeParser(spec(item(), item(query_terms="chinos", category="bottom", color="navy", size="32",
                                          size_system="waist_in")))
    bot, tg, _ = make_bot(store, parser)
    await bot.handle_update(tg_update(1, "grey turtleneck M and navy chinos 32 under 2k each"))

    texts = tg.texts
    assert texts[0] == "Searching 2 items… A ✓ B ✓" or texts[0] == "Searching 2 items… B ✓ A ✓"
    item_texts = [t for t in texts if t.startswith("<b>A ·") or t.startswith("<b>B ·")]
    assert len(item_texts) == 2
    assert all(f"{BASE}/r/" in t for t in item_texts)
    assert "Tap <b>open</b>" in texts[-1]
    assert len(tg.photos) == 2

    events = event_types(await store.events_for(42))
    assert events[:2] == ["request_received", "parsed"]
    assert events.count("results_shown") == 2 and events.count("item_searched") == 2
    session = await store.get_session(42)
    assert session["last_spec"]["items"][1]["query_terms"] == "chinos" and session["pending_message"] is None


async def test_ambiguous_size_asks_then_uses_reply(store):
    question = "Size 30 for a turtleneck: chest 30 inches, or XS?"
    parser = FakeParser(
        spec(item(size="30", size_system="unknown"), ambiguities=[{"item_index": 0, "field": "size", "question": question}]),
        spec(item(size="M")),
    )
    bot, tg, catalog = make_bot(store, parser)
    await bot.handle_update(tg_update(1, "grey turtleneck size 30"))
    assert tg.texts == [question]
    assert catalog.search_calls == []
    assert (await store.get_session(42))["pending_message"] == "grey turtleneck size 30"

    await bot.handle_update(tg_update(2, "M"))
    message, context = parser.calls[1]
    assert message == "M" and "grey turtleneck size 30" in context and question in context
    assert "Searching 1 item…" in tg.texts
    events = await store.events_for(42)
    assert "clarify_asked" in event_types(events)
    assert len({e["session_id"] for e in events if e["session_id"]}) == 1  # same session across the clarification


async def test_parse_failure_shows_help_and_logs(store):
    bot, tg, catalog = make_bot(store, FakeParser("FAIL"))
    await bot.handle_update(tg_update(1, "asdf"))
    assert "couldn't read that" in tg.texts[-1]
    assert "parse_failed" in event_types(await store.events_for(42))
    assert catalog.search_calls == []


async def test_out_of_scope_item_is_skipped_others_continue(store):
    parser = FakeParser(spec(item(), item(query_terms="phone", category="not_fashion", color=None, size=None)))
    bot, tg, catalog = make_bot(store, parser)
    await bot.handle_update(tg_update(1, "grey turtleneck M and an iphone"))
    assert any("I only do clothes for now, so I skipped: phone" in t for t in tg.texts)
    assert "Searching 1 item…" in tg.texts
    assert len(catalog.search_calls) == 1


async def test_only_out_of_scope_items(store):
    bot, tg, catalog = make_bot(store, FakeParser(spec(item(query_terms="laptop", category="not_fashion"))))
    await bot.handle_update(tg_update(1, "laptop"))
    assert tg.texts == ["I only do clothes for now, so I skipped: laptop."]
    assert catalog.search_calls == []


async def test_zero_results_message(store):
    parser = FakeParser(spec(item(color="purple")))
    bot, tg, _ = make_bot(store, parser, catalog=FakeCatalog(load_search("05-navy-chinos-men")))
    await bot.handle_update(tg_update(1, "purple turtleneck"))
    assert any("No purple turtleneck in M under ₹2,000" in t for t in tg.texts)
    assert not any("Tap <b>open</b>" in t for t in tg.texts)
    assert "zero_results" in event_types(await store.events_for(42))


async def test_catalog_error_retries_once_then_succeeds(store):
    catalog = FakeCatalog(load_search("01-grey-turtleneck-men"), fail_times=1)
    bot, tg, _ = make_bot(store, FakeParser(spec(item())), catalog=catalog)
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    assert "Search is slow right now, retrying…" in tg.texts
    assert any(t.startswith("<b>A ·") for t in tg.texts)


async def test_catalog_error_twice_apologises_for_that_item(store):
    catalog = FakeCatalog(load_search("01-grey-turtleneck-men"), fail_times=5)
    bot, tg, _ = make_bot(store, FakeParser(spec(item())), catalog=catalog)
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    assert any("Something went wrong on my side while searching" in t for t in tg.texts)
    assert event_types(await store.events_for(42)).count("catalog_error") == 2


async def test_album_failure_falls_back_to_text(store):
    bot, tg, _ = make_bot(store, FakeParser(spec(item())), telegram=FakeTelegram(fail_photos=True))
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    assert any(t.startswith("<b>A ·") for t in tg.texts)
    assert "album_failed" in event_types(await store.events_for(42))


async def test_second_message_while_busy_is_rejected(store):
    gate = asyncio.Event()

    class SlowParser(FakeParser):
        async def parse(self, message, context=None):
            await gate.wait()
            return await super().parse(message, context)

    bot, tg, _ = make_bot(store, SlowParser(spec(item())))
    first = asyncio.create_task(bot.handle_update(tg_update(1, "grey turtleneck M")))
    await asyncio.sleep(0.05)
    await bot.handle_update(tg_update(2, "and a hoodie"))
    assert tg.texts == ["Still working on your last request, one moment…"]
    gate.set()
    await first
    assert "Searching 1 item…" in tg.texts


@pytest.mark.parametrize("command", ["/start", "/help", "/start@DrapeFitBot"])
async def test_help_commands(store, command):
    bot, tg, _ = make_bot(store, FakeParser())
    await bot.handle_update(tg_update(1, command))
    assert tg.texts[0].startswith("<b>DrapeFit</b>") and "<blockquote>" in tg.texts[0]
    assert [b["callback_data"] for row in tg.buttons[0] for b in row] == ["ex:0", "ex:1", "ex:2"]


async def test_delete_command_wipes_user(store):
    bot, tg, _ = make_bot(store, FakeParser(spec(item())))
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    await bot.handle_update(tg_update(2, "/delete"))
    assert tg.texts[-1].startswith("Done. I deleted")
    assert await store.events_for(42) == []
    assert await store.get_session(42) is None


async def test_non_text_message_gets_help_and_missing_ids_ignored(store):
    bot, tg, _ = make_bot(store, FakeParser())
    await bot.handle_update({"update_id": 1, "message": {"chat": {"id": 42}, "from": {"id": 42}, "sticker": {}}})
    await bot.handle_update({"update_id": 2, "edited_message": {}})
    assert len(tg.texts) == 1 and "Tell me what you want" in tg.texts[0]


async def test_total_budget_footer(store):
    parser = FakeParser(spec(item(max_price_inr=None), budget_type="total", total_max_inr=3000))
    bot, tg, catalog = make_bot(store, parser)
    await bot.handle_update(tg_update(1, "grey turtleneck M, 3000 total"))
    assert catalog.search_calls[0][1] == 3000
    assert "Your total budget is ₹3,000" in tg.texts[-1]


async def test_local_mode_links_straight_to_store(store):
    bot = Bot(store, FakeTelegram(), FakeParser(spec(item())), FakeCatalog(load_search("01-grey-turtleneck-men")), None)
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    text = next(t for t in bot._tg.texts if t.startswith("<b>A ·"))
    assert "/r/" not in text and 'href="https://' in text


# --- regressions from the 2026-11-11 code review -----------------------------------

async def test_album_network_timeout_still_sends_text(store):
    import httpx

    class SlowAlbums(FakeTelegram):
        async def send_photos(self, chat_id, photos):
            raise httpx.ReadTimeout("telegram fetching images")

    bot, tg, _ = make_bot(store, FakeParser(spec(item())), telegram=SlowAlbums())
    await bot.handle_update(tg_update(1, "grey turtleneck M"))
    assert any(t.startswith("<b>A ·") for t in tg.texts)


async def test_one_item_send_failure_does_not_drop_the_other(store):
    parser = FakeParser(spec(item(), item(query_terms="chinos", category="bottom", color="navy", size="32")))
    bot, tg, _ = make_bot(store, parser)
    original = bot._send_item
    calls = {"n": 0}

    async def flaky(chat_id, user_id, session_id, result, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return await original(chat_id, user_id, session_id, result, event)

    bot._send_item = flaky
    await bot.handle_update(tg_update(1, "two items"))
    assert sum(t.startswith(("<b>A ·", "<b>B ·")) for t in tg.texts) == 1
    assert "send_failed" in event_types(await store.events_for(42))


async def test_malformed_catalog_payload_is_retried(store):
    class Broken(FakeCatalog):
        async def search(self, query, max_inr, gender):
            self.search_calls.append(query)
            if len(self.search_calls) == 1:
                return [{"title": "grey turtleneck", "variants": [{"price": {"amount": 1, "currency": "INR"},
                                                                   "availability": {"available": True}}]}]
            return await super().search(query, max_inr, gender)

    bot, tg, _ = make_bot(store, FakeParser(spec(item(size=None))), catalog=Broken(load_search("01-grey-turtleneck-men")))
    await bot.handle_update(tg_update(1, "grey turtleneck"))
    assert "Search is slow right now, retrying…" in tg.texts
    assert any(t.startswith("<b>A ·") for t in tg.texts)


async def test_llm_text_is_html_escaped(store):
    parser = FakeParser(
        spec(item(query_terms="<b>phone", category="not_fashion")),
        spec(item(size="30"), ambiguities=[{"item_index": 0, "field": "size", "question": "30 <chest> or XS?"}]),
    )
    bot, tg, _ = make_bot(store, parser)
    await bot.handle_update(tg_update(1, "x"))
    await bot.handle_update(tg_update(2, "y"))
    assert "&lt;b&gt;phone" in tg.texts[0] and "<b>phone" not in tg.texts[0]
    assert tg.texts[1] == "30 &lt;chest&gt; or XS?"


async def test_unreadable_size_shows_note_not_random_sizes(store):
    bot, tg, catalog = make_bot(store, FakeParser(spec(item(size="M/L"))))
    await bot.handle_update(tg_update(1, "grey turtleneck M/L"))
    assert any('couldn\'t read the size "M/L"' in t for t in tg.texts)
    assert not any("<b>A1</b>" in t for t in tg.texts)
    assert catalog.search_calls == []


async def test_delete_while_busy_is_refused(store):
    gate = asyncio.Event()

    class SlowParser(FakeParser):
        async def parse(self, message, context=None):
            await gate.wait()
            return await super().parse(message, context)

    bot, tg, _ = make_bot(store, SlowParser(spec(item())))
    first = asyncio.create_task(bot.handle_update(tg_update(1, "grey turtleneck M")))
    await asyncio.sleep(0.05)
    await bot.handle_update(tg_update(2, "/delete"))
    assert tg.texts == ["Still working on your last request, one moment…"]
    gate.set()
    await first
    assert bot._locks == {}  # lock entries are cleaned up


async def test_stale_clarification_is_dropped(store, monkeypatch):
    from datetime import timedelta

    from drapefit import bot as bot_mod

    question = "chest 30 or XS?"
    parser = FakeParser(spec(item(size="30"), ambiguities=[{"item_index": 0, "field": "size", "question": question}]),
                        spec(item(query_terms="hoodie", color="black")))
    b, tg, _ = make_bot(store, parser)
    await b.handle_update(tg_update(1, "turtleneck size 30"))
    monkeypatch.setattr(bot_mod, "PENDING_TTL", timedelta(seconds=-1))
    await b.handle_update(tg_update(2, "black hoodie M"))
    assert parser.calls[1][1] is None  # parsed as a fresh request, not merged with the old one
