"""Rung 2 flows end to end: buttons, cart, follow-up refinements, checkout, did-you-buy.
Fakes for Telegram, Sarvam and the catalog; real SQLite store."""
import copy
from datetime import timedelta

from drapefit import render
from drapefit.bot import Bot

from .conftest import FakeCatalog, FakeParser, FakeTelegram, item, load_search, spec, tg_callback, tg_update

BASE = "https://drapefit.example"


class NumericCatalog(FakeCatalog):
    """Like FakeCatalog, but sized variants get numeric Shopify ids (permalinks need them)."""

    async def get_product(self, product_id, selected=None, fresh=False):
        product = await super().get_product(product_id, selected, fresh)
        for variant in product.get("variants") or []:
            base, _, size = variant["id"].partition("-")
            if size:
                variant["id"] = f"{base}{sum(map(ord, size))}"
        return product


def jeans():
    return item(query_terms="chinos", category="bottom", color="navy", size="32", size_system="waist_in")


def make(store, parser, delay=timedelta(hours=24)):
    catalog = NumericCatalog(load_search("01-grey-turtleneck-men") + load_search("05-navy-chinos-men"))
    tg = FakeTelegram()
    return Bot(store, tg, parser, catalog, BASE, followup_delay=delay), tg, catalog


def add_data(tg, heading):
    rows = tg.buttons_for(heading)
    return [b["callback_data"] for row in rows for b in row]


def tap(update_id, data, message_id=None, keyboard=None, user_id=42):
    update = tg_callback(update_id, data, user_id)
    if message_id is not None:
        update["callback_query"]["message"]["message_id"] = message_id
    if keyboard is not None:
        update["callback_query"]["message"]["reply_markup"] = {"inline_keyboard": keyboard}
    return update


async def search_two(store, parser_extra=(), follow_ups=None, delay=timedelta(hours=24)):
    parser = FakeParser(spec(item(), jeans()), *parser_extra, follow_ups=follow_ups)
    bot, tg, catalog = make(store, parser, delay)
    await bot.handle_update(tg_update(1, "grey turtleneck M and navy chinos 32 under 2k each"))
    return bot, tg, catalog, parser


async def types(store):
    return [e["type"] for e in await store.events_for(42)]


async def test_results_carry_add_buttons_and_footer_shortcuts(store):
    bot, tg, *_ = await search_two(store)
    data = add_data(tg, "<b>A ·")
    assert len(data) == 6 and all(d.startswith("add:") for d in data[:5]) and data[5] == "chp:0"
    assert tg.buttons_for("Tap <b>open</b>") == render.cart_shortcut_buttons()


async def test_tap_add_then_duplicate_tap(store):
    bot, tg, *_ = await search_two(store)
    first = add_data(tg, "<b>A ·")[0]
    await bot.handle_update(tg_callback(10, first))
    await bot.handle_update(tg_callback(11, first))
    assert tg.callback_answers[0] == ("cb10", "Added A1 · cart has 1 item")
    assert tg.callback_answers[1] == ("cb11", "A1 is already in your cart (1)")
    cart = await store.cart(42)
    assert [c["label"] for c in cart] == ["A1"]
    assert (await types(store)).count("cart_add") == 2


async def test_tap_works_while_a_search_is_running(store):
    bot, tg, *_ = await search_two(store)
    first = add_data(tg, "<b>A ·")[0]
    bot._locks[42] = lock = __import__("asyncio").Lock()
    await lock.acquire()  # simulate item B still searching
    await bot.handle_update(tg_callback(12, first))
    lock.release()
    assert tg.callback_answers[-1][1].startswith("Added A1")


async def test_someone_elses_option_cannot_be_added(store):
    bot, tg, *_ = await search_two(store)
    first = add_data(tg, "<b>A ·")[0]
    await bot.handle_update(tg_callback(13, first, user_id=99))
    assert tg.callback_answers[-1] == ("cb13", "That option has expired. Search again.")
    assert await store.cart(99) == []


async def test_text_add_and_remove_without_any_llm_call(store):
    bot, tg, _, parser = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1 B2 and C1"))
    assert parser.classify_calls == [] and len(parser.calls) == 1
    reply = tg.texts[-1]
    assert reply.startswith("Added A1, B2.") and "C1" in reply and "<b>Your cart</b> · 2 items" in reply
    labels = [b["text"] for row in tg.buttons[-1] for b in row]
    assert labels == ["✕ A1", "✕ B2", "💳 Checkout", "Clear cart"]

    await bot.handle_update(tg_update(3, "remove B2"))
    assert [c["label"] for c in await store.cart(42)] == ["A1"]
    await bot.handle_update(tg_update(4, "clear cart"))
    assert await store.cart(42) == [] and tg.texts[-1] == render.CART_CLEARED_TEXT


async def test_remove_button_updates_cart(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1 A2"))
    remove_a1 = tg.buttons[-1][0][0]["callback_data"]
    await bot.handle_update(tg_callback(20, remove_a1))
    assert [c["label"] for c in await store.cart(42)] == ["A2"]
    assert tg.callback_answers[-1] == ("cb20", "Removed")


async def test_quantity_comes_from_the_request(store):
    parser = FakeParser(spec(item(quantity=2)))
    bot, tg, _ = make(store, parser)
    await bot.handle_update(tg_update(1, "2 grey turtlenecks M"))
    await bot.handle_update(tg_update(2, "add A1"))
    assert (await store.cart(42))[0]["quantity"] == 2
    assert "×2" in tg.texts[-1]


async def test_checkout_sends_one_pay_button_per_merchant_and_schedules_followup(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1 A2 B1"))
    await bot.handle_update(tg_callback(30, "co"))
    pay = [b for row in tg.buttons if row for r in row for b in r if b.get("url")]
    assert pay and all(b["url"].startswith(f"{BASE}/r/") for b in pay)
    assert all(b["text"].startswith("Pay on ") for b in pay)
    events = await store.events_for(42)
    built = [e for e in events if e["type"] == "checkout_built"]
    assert len(built) == len(pay)
    assert all(line["status"] == "ok" for e in built for line in e["payload"]["lines"])
    sent = next(e for e in events if e["type"] == "checkout_sent")
    assert len(sent["payload"]["merchants"]) == len(pay)
    # grounding: every price in a checkout message is an API price of a cart option
    cart_prices = {render.inr(c["option"]["price_paise"]) for c in await store.cart(42)}
    for text in tg.texts:
        if "(+ shipping)" in text and "<b>Your cart</b>" not in text and text.startswith("<b>"):
            body = text.split("\n", 1)[1]
            assert any(p in body for p in cart_prices)
    # follow-up exists, not due yet
    assert await store.due_followups() == []
    _, _, catalog_used = bot, tg, bot._catalog
    assert len(catalog_used.fresh_calls) == 3  # every cart line re-checked live, never from cache


async def test_checkout_with_empty_cart(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "checkout"))
    assert tg.texts[-1] == render.CART_EMPTY_TEXT


async def test_did_you_buy_flow(store):
    bot, tg, *_ = await search_two(store, delay=timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    assert await bot.shop.send_due_followups() == 1
    assert await bot.shop.send_due_followups() == 0  # sent once
    assert tg.texts[-1].startswith("Quick one: did you buy anything from")
    buttons = [b["callback_data"] for row in tg.buttons[-1] for b in row]
    fid = buttons[0].split(":")[1]
    assert buttons == [f"fb:{fid}:0", f"fb:{fid}:no", f"fb:{fid}:later"]

    await bot.handle_update(tg_callback(40, f"fb:{fid}:0"))
    assert tg.callback_answers[-1][1].startswith("Nice! Noted your order from")
    reported = [e for e in await store.events_for(42) if e["type"] == "purchase_reported"]
    assert reported and reported[0]["payload"]["host"]


async def test_didnt_buy_asks_why_and_not_yet_reasks_once(store):
    bot, tg, *_ = await search_two(store, delay=timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    await bot.shop.send_due_followups()
    fid = tg.buttons[-1][0][0]["callback_data"].split(":")[1]

    await bot.handle_update(tg_callback(50, f"fb:{fid}:later"))
    assert tg.callback_answers[-1][1] == "OK, I'll check once more later."
    assert await bot.shop.send_due_followups() == 1  # re-asked (delay is negative in this test)
    await bot.handle_update(tg_callback(51, f"fb:{fid}:later"))
    assert tg.callback_answers[-1][1] == render.FOLLOWUP_THANKS  # max 2 pings
    assert await bot.shop.send_due_followups() == 0

    await bot.handle_update(tg_callback(52, f"fb:{fid}:no"))
    assert tg.texts[-1] == render.BLOCKER_QUESTION
    why = tg.buttons[-1][0][0]["callback_data"]
    await bot.handle_update(tg_callback(53, why))
    blockers = [e for e in await store.events_for(42) if e["type"] == "purchase_blocker"]
    assert blockers[0]["payload"]["reason"] == "price"


async def test_followup_buttons_of_another_user_are_rejected(store):
    bot, tg, *_ = await search_two(store, delay=timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    await bot.shop.send_due_followups()
    fid = tg.buttons[-1][0][0]["callback_data"].split(":")[1]
    await bot.handle_update(tg_callback(60, f"fb:{fid}:0", user_id=99))
    assert tg.callback_answers[-1][1] == "That question has expired."


async def test_refine_same_but_black_reruns_only_that_item(store):
    follow_ups = [{"action": "refine", "items": ["A"], "changes": {"color": "black"}}]
    bot, tg, catalog, parser = await search_two(store, follow_ups=follow_ups)
    searches_before = len(catalog.search_calls)
    await bot.handle_update(tg_update(2, "same but black"))
    message, context = parser.classify_calls[0]
    assert message == "same but black" and "A: grey turtleneck, size M (options: A1" in context
    assert len(catalog.search_calls) == searches_before + 1
    assert "black" in catalog.search_calls[-1][0]
    assert (await store.get_session(42))["last_spec"]["items"][0]["color"] == "black"
    assert "Searching 1 item…" in tg.texts
    assert "follow_up_classified" in await types(store) and "refined" in await types(store)


async def test_refine_cheaper_caps_below_cheapest_shown(store):
    follow_ups = [{"action": "refine", "items": ["B"], "changes": {"cheaper": True}}]
    bot, tg, catalog, _ = await search_two(store, follow_ups=follow_ups)
    shown_b = [r for r in await store.shown_in_session(42, (await store.get_session(42))["session_id"])
               if r["label"].startswith("B")]
    cheapest = min(r["option"]["price_paise"] for r in shown_b) // 100
    await bot.handle_update(tg_update(2, "kuch sasta dikhao for the chinos"))
    assert catalog.search_calls[-1][1] == cheapest - 1


async def test_follow_up_cart_action_and_remove_by_item(store):
    follow_ups = [
        {"action": "add", "labels": ["B1"]},
        {"action": "remove", "items": ["B"]},
    ]
    bot, tg, _, parser = await search_two(store, follow_ups=follow_ups)
    await bot.handle_update(tg_update(2, "I'll take the first chinos"))
    assert [c["label"] for c in await store.cart(42)] == ["B1"]
    await bot.handle_update(tg_update(3, "remove the chinos"))
    assert await store.cart(42) == []


async def test_follow_up_other_and_new_search(store):
    follow_ups = [{"action": "other"}, {"action": "new_search"}]
    bot, tg, catalog, parser = await search_two(store, parser_extra=[spec(item(query_terms="hoodie"))],
                                                follow_ups=follow_ups)
    await bot.handle_update(tg_update(2, "do you deliver to Pune?"))
    assert tg.texts[-1] == render.OTHER_TEXT
    await bot.handle_update(tg_update(3, "also a black hoodie M"))
    assert len(parser.calls) == 2 and parser.calls[1][0] == "also a black hoodie M"


async def test_classifier_failure_falls_back_to_a_normal_search(store):
    bot, tg, _, parser = await search_two(store, parser_extra=[spec(item(query_terms="hoodie"))])
    await bot.handle_update(tg_update(2, "black hoodie M"))
    assert len(parser.calls) == 2
    assert "classify_failed" in await types(store)


async def test_old_results_skip_the_classifier(store, monkeypatch):
    from drapefit import bot as bot_mod

    bot, tg, _, parser = await search_two(store, parser_extra=[spec(item(query_terms="hoodie"))],
                                          follow_ups=[{"action": "other"}])
    monkeypatch.setattr(bot_mod, "FOLLOWUP_TTL", timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "black hoodie M"))
    assert parser.classify_calls == [] and len(parser.calls) == 2


async def test_delete_removes_cart_checkouts_and_followups(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    await bot.handle_update(tg_update(4, "/delete"))
    assert await store.cart(42) == []
    import sqlalchemy as sa

    from drapefit.store import checkouts, followups

    async with store._engine.connect() as conn:
        for table in (checkouts, followups):
            assert (await conn.execute(sa.select(table))).first() is None


async def test_unknown_button_is_answered(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_callback(70, "zzz"))
    assert tg.callback_answers[-1] == ("cb70", "That button has expired.")


async def test_cart_full(store, monkeypatch):
    from drapefit import store as store_mod

    monkeypatch.setattr(store_mod, "MAX_CART_LINES", 1)
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1 A2"))
    assert "Added A1." in tg.texts[-1] and "full" in tg.texts[-1]
    assert len(await store.cart(42)) == 1


# --- regression tests for the Rung 2 code review (D55) -----------------------------

async def test_answered_followup_cannot_be_reopened_by_an_old_not_yet_tap(store):
    bot, tg, *_ = await search_two(store, delay=timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    await bot.shop.send_due_followups()
    fid = tg.buttons[-1][0][0]["callback_data"].split(":")[1]
    await bot.handle_update(tg_callback(80, f"fb:{fid}:0"))  # bought
    await bot.handle_update(tg_callback(81, f"fb:{fid}:later"))  # old message, tapped later
    assert await bot.shop.send_due_followups() == 0


async def test_overlapping_cron_runs_send_each_followup_once(store):
    import asyncio

    bot, tg, *_ = await search_two(store, delay=timedelta(seconds=-1))
    await bot.handle_update(tg_update(2, "add A1"))
    await bot.handle_update(tg_update(3, "checkout"))
    results = await asyncio.gather(bot.shop.send_due_followups(), bot.shop.send_due_followups())
    assert sum(results) == 1
    assert sum(t.startswith("Quick one") for t in tg.texts) == 1


async def test_one_bad_followup_row_does_not_block_the_rest(store, monkeypatch):
    from drapefit.store import now

    bot, tg, *_ = await search_two(store)
    await store.schedule_followup(42, "s-bad", [{"name": "X", "host": "x.in"}], now() - timedelta(minutes=2))
    await store.schedule_followup(43, "s-good", [{"name": "Y", "host": "y.in"}], now() - timedelta(minutes=1))
    real = bot.shop._send_followup

    async def flaky(followup):
        if followup["session_id"] == "s-bad":
            raise RuntimeError("boom")
        return await real(followup)

    monkeypatch.setattr(bot.shop, "_send_followup", flaky)
    assert await bot.shop.send_due_followups() == 1


async def test_session_is_saved_before_searching(store):
    parser = FakeParser(spec(item(quantity=2)))
    bot, tg, catalog = make(store, parser)
    seen = []
    real_search = catalog.search

    async def search(query, max_inr, gender):
        seen.append((await store.get_session(42) or {}).get("last_spec"))
        return await real_search(query, max_inr, gender)

    catalog.search = search
    await bot.handle_update(tg_update(1, "2 grey turtlenecks M"))
    assert seen[0]["items"][0]["quantity"] == 2  # a + tap mid-search gets the right quantity


async def test_text_add_only_sees_options_currently_on_screen(store):
    follow_ups = [{"action": "refine", "items": ["A"], "changes": {"size": "L"}}]
    bot, tg, catalog, _ = await search_two(store, follow_ups=follow_ups)
    catalog.products = catalog.products[:1]  # the re-run of A returns a single option
    await bot.handle_update(tg_update(2, "turtleneck in L"))
    assert [r["label"] for r in await store.shown_in_session(42, (await store.get_session(42))["session_id"])
            if r["label"].startswith("A")] == ["A1"]
    await bot.handle_update(tg_update(3, "add A5"))
    assert await store.cart(42) == [] and "couldn't find A5" in tg.texts[-1]


async def test_remove_by_item_ignores_old_searches_with_the_same_letter(store):
    follow_ups = [{"action": "new_search"}, {"action": "remove", "items": ["A"]}]
    bot, tg, _, parser = await search_two(store, parser_extra=[spec(jeans())], follow_ups=follow_ups)
    await bot.handle_update(tg_update(2, "add A1"))  # yesterday's A: turtleneck
    await bot.handle_update(tg_update(3, "now navy chinos 32"))  # new search, A is chinos now
    await bot.handle_update(tg_update(4, "add A2"))
    await bot.handle_update(tg_update(5, "remove the chinos"))
    remaining = await store.cart(42)
    assert len(remaining) == 1 and "turtle" in remaining[0]["option"]["title"].lower()


async def test_which_item_question_escapes_html():
    from drapefit.models import FollowUp, RequestSpec
    from drapefit.refine import apply_refinement

    two = RequestSpec.model_validate(spec(item(query_terms="tee <3"), item(query_terms="h&m jeans")))
    r = apply_refinement(two, FollowUp.model_validate({"action": "refine", "changes": {"color": "black"}}), {})
    assert "tee &lt;3" in r.message and "h&amp;m" in r.message


async def test_checkout_button_answers_the_tap_before_slow_work(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1"))
    before = len(tg.messages)
    order = []
    real_answer, real_send = tg.answer_callback, tg.send_message

    async def answer(cid, text=None):
        order.append("answer")
        await real_answer(cid, text)

    async def send(chat, text, buttons=None):
        order.append("send")
        await real_send(chat, text, buttons)

    tg.answer_callback, tg.send_message = answer, send
    await bot.handle_update(tg_callback(90, "co"))
    assert order[0] == "answer" and len(tg.messages) > before


# --- Telegram UX (D57) -----------------------------------------------------------

async def test_tapped_plus_turns_into_a_tick_on_the_same_message(store):
    bot, tg, *_ = await search_two(store)
    message_id = next(i for i, (_, t) in enumerate(tg.messages, 1) if t.startswith("<b>A ·"))
    keyboard = tg.buttons[message_id - 1]
    first = keyboard[0][0]["callback_data"]
    await bot.handle_update(tap(100, first, message_id, keyboard))
    assert tg.buttons[message_id - 1][0][0]["text"] == "✓ A1"
    assert tg.buttons[message_id - 1][0][1]["text"] == "+ A2"
    assert keyboard[0][0]["text"] == "+ A1"  # original structure not mutated


async def test_removing_from_cart_edits_the_cart_message_in_place(store):
    bot, tg, *_ = await search_two(store)
    await bot.handle_update(tg_update(2, "add A1 A2"))
    cart_id, count = len(tg.messages), len(tg.messages)
    remove_a1 = tg.buttons[-1][0][0]["callback_data"]
    await bot.handle_update(tap(101, remove_a1, cart_id))
    assert len(tg.messages) == count  # no new message
    assert "1 item" in tg.messages[cart_id - 1][1] and "A2" in tg.messages[cart_id - 1][1]
    await bot.handle_update(tap(102, "clr", cart_id))
    assert tg.messages[cart_id - 1][1] == render.CART_EMPTY_TEXT and await store.cart(42) == []


async def test_cheaper_button_reruns_that_item_below_the_cheapest_shown_without_llm(store):
    bot, tg, catalog, parser = await search_two(store)
    sid = (await store.get_session(42))["session_id"]
    cheapest = min(r["option"]["price_paise"] for r in await store.shown_in_session(42, sid)
                   if r["label"].startswith("A")) // 100
    await bot.handle_update(tap(103, "chp:0"))
    assert parser.classify_calls == [] and len(parser.calls) == 1
    assert catalog.search_calls[-1][1] == cheapest - 1
    assert tg.callback_answers[-1] == ("cb103", None)
    assert "cheaper_tapped" in await types(store)


async def test_cheaper_button_on_expired_search(store):
    bot, tg, _ = make(store, FakeParser())
    await bot.handle_update(tap(104, "chp:0"))
    assert "expired" in tg.texts[-1]


async def test_example_chip_runs_that_search(store):
    parser = FakeParser(spec(item(query_terms="hoodie", color="black", size="XL")))
    bot, tg, catalog = make(store, parser)
    await bot.handle_update(tap(105, "ex:1"))
    assert parser.calls[0][0] == render.EXAMPLES[1]
    assert catalog.search_calls and tg.callback_answers[-1] == ("cb105", None)


async def test_progress_message_ticks_items_as_they_land(store):
    bot, tg, *_ = await search_two(store)
    ticks = [text for _, text, _ in tg.edits if text and text.startswith("Searching 2 items…")]
    assert len(ticks) == 2 and ticks[-1].count("✓") == 2


def test_mark_added_returns_a_new_keyboard():
    kb = [[{"text": "+ A1", "callback_data": "add:1"}, {"text": "+ A2", "callback_data": "add:2"}]]
    out = render.mark_added(kb, "add:2", "A2")
    assert out[0][1]["text"] == "✓ A2" and kb[0][1]["text"] == "+ A2"


async def test_example_chip_echoes_the_request_first(store):
    parser = FakeParser(spec(item(query_terms="kurti", category="ethnic", color="white", gender="women")))
    bot, tg, _ = make(store, parser)
    await bot.handle_update(tap(106, "ex:1"))
    assert tg.texts[0] == f"<blockquote>{render.EXAMPLES[1]}</blockquote>"


def test_welcome_is_valid_telegram_html():
    import re

    text = render.WELCOME_TEXT
    for tag in ("b", "i", "blockquote"):
        assert len(re.findall(f"<{tag}>", text)) == len(re.findall(f"</{tag}>", text))
    assert "/checkout" not in text  # commands live in the / menu, not mid-sentence
