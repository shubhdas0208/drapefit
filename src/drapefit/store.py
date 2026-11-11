"""Persistence (D26). Neon Postgres in production, SQLite for tests and local dev.

Tables
    sessions(telegram_user_id PK) -- current session, last spec, pending clarification
    results                       -- the exact Option shown, for the grounding audit
    link_tokens(token PK)         -- /r/{token} -> merchant URL (D29, no open redirect)
    events                        -- append-only funnel log (D22)
    processed_updates(update_id)  -- Telegram webhook dedupe (D27)
    cart_lines                    -- the user's cart, one row per variant (D49)
    checkouts                     -- one row per merchant permalink sent (D50)
    followups                     -- "did you buy?" pings, one per session (D51)
"""
from __future__ import annotations

import dataclasses
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .models import Option

TOKEN_BYTES = 9  # 12 url-safe chars

metadata = sa.MetaData()

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("telegram_user_id", sa.BigInteger, primary_key=True),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("last_spec", sa.JSON),
    sa.Column("pending_message", sa.Text),
    sa.Column("pending_questions", sa.JSON),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

results = sa.Table(
    "results",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("telegram_user_id", sa.BigInteger, nullable=False, index=True),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("label", sa.String(8), nullable=False),
    sa.Column("option", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

link_tokens = sa.Table(
    "link_tokens",
    metadata,
    sa.Column("token", sa.String(32), primary_key=True),
    sa.Column("telegram_user_id", sa.BigInteger, nullable=False, index=True),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("label", sa.String(8), nullable=False),
    sa.Column("target_url", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("telegram_user_id", sa.BigInteger, index=True),
    sa.Column("session_id", sa.String(32)),
    sa.Column("type", sa.String(40), nullable=False),
    sa.Column("payload", sa.JSON),
)

processed_updates = sa.Table(
    "processed_updates",
    metadata,
    sa.Column("update_id", sa.BigInteger, primary_key=True, autoincrement=False),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
)


cart_lines = sa.Table(
    "cart_lines",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("telegram_user_id", sa.BigInteger, nullable=False, index=True),
    sa.Column("variant_id", sa.String(200), nullable=False),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("label", sa.String(8), nullable=False),
    sa.Column("option", sa.JSON, nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("telegram_user_id", "variant_id", name="uq_cart_user_variant"),
)

checkouts = sa.Table(
    "checkouts",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("telegram_user_id", sa.BigInteger, nullable=False, index=True),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("seller_name", sa.Text, nullable=False),
    sa.Column("host", sa.Text, nullable=False),
    sa.Column("permalink", sa.Text, nullable=False),
    sa.Column("lines", sa.JSON, nullable=False),
    sa.Column("total_paise", sa.BigInteger, nullable=False),
    sa.Column("token", sa.String(32)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

followups = sa.Table(
    "followups",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("telegram_user_id", sa.BigInteger, nullable=False, index=True),
    sa.Column("session_id", sa.String(32), nullable=False),
    sa.Column("merchants", sa.JSON, nullable=False),  # [{"name": ..., "host": ...}]
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("sent_count", sa.Integer, nullable=False, default=0),
    sa.Column("done", sa.Boolean, nullable=False, default=False),  # no more pings
    sa.Column("answered", sa.Boolean, nullable=False, default=False),  # user said bought / didn't buy
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("telegram_user_id", "session_id", name="uq_followup_session"),
)

MAX_CART_LINES = 10
CHECKOUT_LABEL = "CHECKOUT"  # link_tokens.label for a merchant checkout permalink
USER_TABLES = (sessions, results, link_tokens, events, cart_lines, checkouts, followups)


def now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_database_url(url: str) -> str:
    """Neon gives postgresql://...?sslmode=require; asyncpg wants ssl=require."""
    if url.startswith("sqlite"):
        return url if "+aiosqlite" in url else url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and sslmode != "disable":
        query["ssl"] = "require"
    return urlunsplit(parts._replace(query=urlencode(query)))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def allowed_target(option: Option) -> bool:
    """A redirect target must be https on the seller's own domain (from the API)."""
    if urlsplit(option.product_url).scheme != "https":
        return False
    target = _host(option.product_url)
    allowed = {_host(option.seller_url), option.seller_domain.lower().removeprefix("www.")}
    return bool(target) and target in allowed


class Store:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, url: str) -> Store:
        return cls(create_async_engine(normalise_database_url(url), pool_pre_ping=True))

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    async def claim_update(self, update_id: int) -> bool:
        """True the first time an update_id is seen, False for Telegram retries."""
        try:
            async with self._engine.begin() as conn:
                await conn.execute(processed_updates.insert().values(update_id=update_id, ts=now()))
            return True
        except IntegrityError:
            return False

    async def log_event(
        self, user_id: int | None, session_id: str | None, type_: str, payload: dict[str, Any] | None = None
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                events.insert().values(
                    ts=now(), telegram_user_id=user_id, session_id=session_id, type=type_, payload=payload or {}
                )
            )

    async def get_session(self, user_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(sessions).where(sessions.c.telegram_user_id == user_id))).first()
        return dict(row._mapping) if row else None

    async def save_session(
        self,
        user_id: int,
        session_id: str,
        last_spec: dict | None,
        pending_message: str | None,
        pending_questions: list[str] | None,
    ) -> None:
        values = {
            "session_id": session_id,
            "last_spec": last_spec,
            "pending_message": pending_message,
            "pending_questions": pending_questions,
            "updated_at": now(),
        }
        async with self._engine.begin() as conn:
            updated = await conn.execute(
                sessions.update().where(sessions.c.telegram_user_id == user_id).values(**values)
            )
            if updated.rowcount == 0:
                await conn.execute(sessions.insert().values(telegram_user_id=user_id, **values))

    async def save_results(self, user_id: int, session_id: str, options: list[Option]) -> dict[str, str]:
        """Store shown options and mint one redirect token each. Returns label -> token."""
        tokens, _ = await self.save_results_with_ids(user_id, session_id, options)
        return tokens

    async def save_results_with_ids(
        self, user_id: int, session_id: str, options: list[Option]
    ) -> tuple[dict[str, str], dict[str, int]]:
        """Like save_results, plus label -> results.id (used by the inline + buttons)."""
        tokens: dict[str, str] = {}
        ids: dict[str, int] = {}
        ts = now()
        async with self._engine.begin() as conn:
            for option in options:
                inserted = await conn.execute(
                    results.insert().values(
                        telegram_user_id=user_id,
                        session_id=session_id,
                        label=option.label,
                        option=dataclasses.asdict(option),
                        created_at=ts,
                    )
                )
                ids[option.label] = inserted.inserted_primary_key[0]
                if not allowed_target(option):
                    continue
                token = secrets.token_urlsafe(TOKEN_BYTES)
                await conn.execute(
                    link_tokens.insert().values(
                        token=token,
                        telegram_user_id=user_id,
                        session_id=session_id,
                        label=option.label,
                        target_url=option.product_url,
                        created_at=ts,
                    )
                )
                tokens[option.label] = token
        return tokens, ids

    async def resolve_token(self, token: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(link_tokens).where(link_tokens.c.token == token))).first()
        return dict(row._mapping) if row else None

    async def delete_user(self, user_id: int) -> None:
        async with self._engine.begin() as conn:
            for table in USER_TABLES:
                await conn.execute(table.delete().where(table.c.telegram_user_id == user_id))

    async def prune(self, retention_days: int, dedupe_days: int = 7) -> None:
        cutoff = now() - timedelta(days=retention_days)
        async with self._engine.begin() as conn:
            await conn.execute(results.delete().where(results.c.created_at < cutoff))
            await conn.execute(link_tokens.delete().where(link_tokens.c.created_at < cutoff))
            await conn.execute(events.delete().where(events.c.ts < cutoff))
            await conn.execute(sessions.delete().where(sessions.c.updated_at < cutoff))
            await conn.execute(cart_lines.delete().where(cart_lines.c.added_at < cutoff))
            await conn.execute(checkouts.delete().where(checkouts.c.created_at < cutoff))
            await conn.execute(followups.delete().where(followups.c.created_at < cutoff))
            await conn.execute(
                processed_updates.delete().where(processed_updates.c.ts < now() - timedelta(days=dedupe_days))
            )

    async def events_for(self, user_id: int) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                sa.select(events).where(events.c.telegram_user_id == user_id).order_by(events.c.id)
            )
            return [dict(r._mapping) for r in rows]

    # --- results lookup (cart adds) -------------------------------------------------

    async def get_result(self, user_id: int, result_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(results).where(results.c.id == result_id, results.c.telegram_user_id == user_id)
                )
            ).first()
        return dict(row._mapping) if row else None

    async def latest_result_for_label(self, user_id: int, label: str) -> dict | None:
        """The most recently shown option with this label (labels repeat across searches)."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(results)
                    .where(results.c.telegram_user_id == user_id, results.c.label == label)
                    .order_by(results.c.id.desc())
                    .limit(1)
                )
            ).first()
        return dict(row._mapping) if row else None

    async def shown_in_session(self, user_id: int, session_id: str) -> list[dict]:
        """Options currently on screen: for each item, only its latest run.

        A re-run of item A that returns 2 options replaces all of A1..A5, so an old A5
        can't be added by text afterwards.
        """
        async with self._engine.connect() as conn:
            rows = [
                dict(r._mapping)
                for r in await conn.execute(
                    sa.select(results)
                    .where(results.c.telegram_user_id == user_id, results.c.session_id == session_id)
                    .order_by(results.c.id)
                )
            ]
        latest_run: dict[int, object] = {}
        for row in rows:  # one run's rows share created_at; later runs have later ids
            latest_run[row["option"].get("item_index", 0)] = row["created_at"]
        return [r for r in rows if r["created_at"] == latest_run[r["option"].get("item_index", 0)]]

    # --- cart (D49) ---------------------------------------------------------------

    async def cart(self, user_id: int) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                sa.select(cart_lines).where(cart_lines.c.telegram_user_id == user_id).order_by(cart_lines.c.id)
            )
            return [dict(r._mapping) for r in rows]

    async def add_to_cart(
        self, user_id: int, session_id: str, option: dict, quantity: int
    ) -> str:
        """Returns "added", "already" or "full"."""
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                sa.select(cart_lines.c.id).where(
                    cart_lines.c.telegram_user_id == user_id, cart_lines.c.variant_id == option["variant_id"]
                )
            )
            if existing.first():
                return "already"
            count = await conn.scalar(
                sa.select(sa.func.count()).select_from(cart_lines).where(cart_lines.c.telegram_user_id == user_id)
            )
            if count >= MAX_CART_LINES:
                return "full"
            try:
                await conn.execute(
                    cart_lines.insert().values(
                        telegram_user_id=user_id,
                        variant_id=option["variant_id"],
                        session_id=session_id,
                        label=option["label"],
                        option=option,
                        quantity=quantity,
                        added_at=now(),
                    )
                )
            except IntegrityError:
                return "already"
        return "added"

    async def remove_cart_lines(self, user_id: int, line_ids: list[int]) -> int:
        if not line_ids:
            return 0
        async with self._engine.begin() as conn:
            result = await conn.execute(
                cart_lines.delete().where(cart_lines.c.telegram_user_id == user_id, cart_lines.c.id.in_(line_ids))
            )
        return result.rowcount or 0

    async def clear_cart(self, user_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(cart_lines.delete().where(cart_lines.c.telegram_user_id == user_id))

    # --- checkouts (D50) ----------------------------------------------------------

    async def save_checkout(
        self,
        user_id: int,
        session_id: str,
        seller_name: str,
        host: str,
        permalink: str,
        lines: list[dict],
        total_paise: int,
    ) -> str:
        """Store one merchant checkout and mint its /r token. Returns the token."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        ts = now()
        async with self._engine.begin() as conn:
            await conn.execute(
                checkouts.insert().values(
                    telegram_user_id=user_id,
                    session_id=session_id,
                    seller_name=seller_name,
                    host=host,
                    permalink=permalink,
                    lines=lines,
                    total_paise=total_paise,
                    token=token,
                    created_at=ts,
                )
            )
            await conn.execute(
                link_tokens.insert().values(
                    token=token,
                    telegram_user_id=user_id,
                    session_id=session_id,
                    label=CHECKOUT_LABEL,
                    target_url=permalink,
                    created_at=ts,
                )
            )
        return token

    async def checkout_for_token(self, token: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(checkouts).where(checkouts.c.token == token))).first()
        return dict(row._mapping) if row else None

    async def result_for_label(
        self, user_id: int, session_id: str, label: str, shown_at: datetime | None = None
    ) -> dict | None:
        """The option behind a click. shown_at (the token's time) skips later re-runs of the label."""
        conditions = [
            results.c.telegram_user_id == user_id,
            results.c.session_id == session_id,
            results.c.label == label,
        ]
        if shown_at is not None:
            conditions.append(results.c.created_at <= shown_at)
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(results)
                    .where(*conditions)
                    .order_by(results.c.id.desc())
                    .limit(1)
                )
            ).first()
        return dict(row._mapping) if row else None

    # --- follow-ups (D51) ---------------------------------------------------------

    async def schedule_followup(
        self, user_id: int, session_id: str, merchants: list[dict], due_at: datetime
    ) -> None:
        """Create the session's follow-up, or add merchants to the one not sent yet."""
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(followups).where(
                        followups.c.telegram_user_id == user_id, followups.c.session_id == session_id
                    )
                )
            ).first()
            if row is None:
                try:
                    await conn.execute(
                        followups.insert().values(
                            telegram_user_id=user_id,
                            session_id=session_id,
                            merchants=merchants,
                            due_at=due_at,
                            sent_count=0,
                            done=False,
                            answered=False,
                            created_at=now(),
                        )
                    )
                except IntegrityError:
                    pass
                return
            if row.sent_count > 0 or row.done:
                return
            known = {m["host"] for m in row.merchants}
            merged = list(row.merchants) + [m for m in merchants if m["host"] not in known]
            await conn.execute(followups.update().where(followups.c.id == row.id).values(merchants=merged))

    async def due_followups(self, limit: int = 50) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                sa.select(followups)
                .where(followups.c.done.is_(False), followups.c.due_at <= now())
                .order_by(followups.c.due_at)
                .limit(limit)
            )
            return [dict(r._mapping) for r in rows]

    async def claim_followup(self, followup_id: int, sent_count: int) -> bool:
        """Atomically take a due follow-up for sending. False if another run already took it."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                followups.update()
                .where(
                    followups.c.id == followup_id,
                    followups.c.sent_count == sent_count,
                    followups.c.done.is_(False),
                )
                .values(sent_count=sent_count + 1, done=True)
            )
        return (result.rowcount or 0) == 1

    async def mark_followup_sent(self, followup_id: int, next_due: datetime | None) -> None:
        """next_due=None means no more pings for this follow-up."""
        async with self._engine.begin() as conn:
            values: dict[str, Any] = {"sent_count": followups.c.sent_count + 1}
            if next_due is None:
                values["done"] = True
            else:
                values["due_at"] = next_due
            await conn.execute(followups.update().where(followups.c.id == followup_id).values(**values))

    async def get_followup(self, user_id: int, followup_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(followups).where(
                        followups.c.id == followup_id, followups.c.telegram_user_id == user_id
                    )
                )
            ).first()
        return dict(row._mapping) if row else None

    async def snooze_followup(self, followup_id: int, due_at: datetime) -> bool:
        """Re-ask later, unless the user already answered (an old "Not yet" tap can't reopen it)."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                followups.update()
                .where(followups.c.id == followup_id, followups.c.answered.is_(False))
                .values(due_at=due_at, done=False)
            )
        return (result.rowcount or 0) == 1

    async def close_followup(self, followup_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                followups.update().where(followups.c.id == followup_id).values(done=True, answered=True)
            )
