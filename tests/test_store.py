from datetime import timedelta

import pytest
import sqlalchemy as sa

from drapefit import pipeline, store as store_mod
from drapefit.store import allowed_target, normalise_database_url

from .conftest import load_search


def options(n=2):
    products = load_search("03-white-linen-shirt-men")[:n]
    return [pipeline.make_option(f"A{i + 1}", 0, p, p["variants"][0]) for i, p in enumerate(products)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require",
         "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
    ],
)
def test_database_url_normalisation(raw, expected):
    assert normalise_database_url(raw) == expected


def test_allowed_target_only_on_seller_domain_over_https():
    option = options(1)[0]
    assert allowed_target(option)
    cls = option.__class__
    assert not allowed_target(cls(**{**option.__dict__, "product_url": "https://evil.example/x"}))
    assert not allowed_target(cls(**{**option.__dict__, "product_url": option.product_url.replace("https", "http")}))
    assert not allowed_target(cls(**{**option.__dict__, "product_url": ""}))


async def test_claim_update_dedupes(store):
    assert await store.claim_update(1) is True
    assert await store.claim_update(1) is False
    assert await store.claim_update(2) is True


async def test_results_and_tokens_round_trip(store):
    opts = options(2)
    tokens = await store.save_results(42, "s1", opts)
    assert set(tokens) == {"A1", "A2"}
    assert len(set(tokens.values())) == 2
    row = await store.resolve_token(tokens["A1"])
    assert row["target_url"] == opts[0].product_url and row["telegram_user_id"] == 42 and row["label"] == "A1"
    assert await store.resolve_token("nope-nope-nope") is None


async def test_disallowed_target_gets_no_token(store):
    bad = options(1)[0]
    bad = bad.__class__(**{**bad.__dict__, "product_url": "https://evil.example/x"})
    assert await store.save_results(42, "s1", [bad]) == {}


async def test_session_upsert(store):
    assert await store.get_session(7) is None
    await store.save_session(7, "s1", {"items": []}, "orig", ["q?"])
    await store.save_session(7, "s1", {"items": [1]}, None, None)
    s = await store.get_session(7)
    assert s["last_spec"] == {"items": [1]} and s["pending_message"] is None


async def test_delete_user_removes_everything(store):
    await store.save_session(7, "s1", None, None, None)
    tokens = await store.save_results(7, "s1", options(1))
    await store.log_event(7, "s1", "request_received", {"text": "x"})
    await store.log_event(8, "s2", "request_received", {"text": "other user"})
    await store.delete_user(7)
    assert await store.get_session(7) is None
    assert await store.resolve_token(tokens["A1"]) is None
    assert await store.events_for(7) == []
    assert len(await store.events_for(8)) == 1


async def test_prune_drops_old_rows(store, monkeypatch):
    await store.log_event(7, "s1", "old", {})
    await store.claim_update(99)
    future = store_mod.now() + timedelta(days=100)
    monkeypatch.setattr(store_mod, "now", lambda: future)
    await store.log_event(7, "s1", "new", {})
    await store.prune(90)
    assert [e["type"] for e in await store.events_for(7)] == ["new"]
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(store_mod.processed_updates))).scalar() == 0


async def test_followup_merges_merchants_until_sent(store):
    from drapefit.store import now

    due = now() - timedelta(seconds=1)
    await store.schedule_followup(42, "s1", [{"name": "A", "host": "a.in"}], due)
    await store.schedule_followup(42, "s1", [{"name": "A", "host": "a.in"}, {"name": "B", "host": "b.in"}], due)
    [f] = await store.due_followups()
    assert [m["host"] for m in f["merchants"]] == ["a.in", "b.in"]
    await store.mark_followup_sent(f["id"], None)
    await store.schedule_followup(42, "s1", [{"name": "C", "host": "c.in"}], due)  # already sent: no change
    assert await store.due_followups() == []
    assert [m["host"] for m in (await store.get_followup(42, f["id"]))["merchants"]] == ["a.in", "b.in"]
    assert await store.get_followup(7, f["id"]) is None  # other users can't read it


async def test_cart_is_unique_per_variant(store):
    opts = options(2)
    import dataclasses

    rows = [dataclasses.asdict(o) for o in opts]
    assert await store.add_to_cart(42, "s1", rows[0], 1) == "added"
    assert await store.add_to_cart(42, "s1", rows[0], 1) == "already"
    assert await store.add_to_cart(42, "s1", rows[1], 3) == "added"
    cart = await store.cart(42)
    assert [c["quantity"] for c in cart] == [1, 3]
    assert await store.remove_cart_lines(7, [cart[0]["id"]]) == 0  # not your line
    assert await store.remove_cart_lines(42, [cart[0]["id"]]) == 1


async def test_prune_covers_rung2_tables(store):
    import dataclasses

    from drapefit.store import cart_lines, checkouts, followups, now

    await store.add_to_cart(42, "s1", dataclasses.asdict(options(1)[0]), 1)
    await store.save_checkout(42, "s1", "B", "b.in", "https://b.in/cart/1:1", [], 1)
    await store.schedule_followup(42, "s1", [{"name": "B", "host": "b.in"}], now())
    await store.prune(retention_days=-1)
    async with store._engine.connect() as conn:
        for table in (cart_lines, checkouts, followups):
            assert (await conn.execute(sa.select(table))).first() is None
