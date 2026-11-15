"""Metrics report from the bot's event log (D64). Read-only.

    .venv/Scripts/python scripts/report.py                 # Neon (DATABASE_URL), last 7 days
    .venv/Scripts/python scripts/report.py --days 30
    .venv/Scripts/python scripts/report.py --exclude 123   # leave out your own Telegram id
    .venv/Scripts/python scripts/report.py --local         # the laptop test database
    .venv/Scripts/python scripts/report.py --save          # also writes docs/metrics/report-<date>.md

DATABASE_URL comes from the environment, .env.local or the Windows user env (setx). It is
never printed. REPORT_EXCLUDE_USERS (comma-separated ids) works like --exclude.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drapefit.config import _read_env  # noqa: E402
from drapefit.report import build_report, format_report  # noqa: E402
from drapefit.store import Store  # noqa: E402


def _ids(raw: str | None) -> set[int]:
    return {int(x) for x in (raw or "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--exclude", default="", help="comma-separated Telegram user ids to leave out")
    ap.add_argument("--local", action="store_true", help="read drapefit-local.db instead of Neon")
    ap.add_argument("--save", action="store_true", help="also save to docs/metrics/")
    args = ap.parse_args()

    if args.local:
        url, source = f"sqlite+aiosqlite:///{ROOT / 'drapefit-local.db'}", "local test database"
    else:
        url, source = _read_env("DATABASE_URL"), "Neon"
        if not url:
            raise SystemExit("DATABASE_URL not set. Copy it from the Neon console (Connect) and run:\n"
                             '  setx DATABASE_URL "<connection string>"   (then open a new terminal)\n'
                             "or use --local for the laptop test database.")
    store = Store.from_url(url)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    try:
        events = await store.events_since(since)
    finally:
        await store.close()
    exclude = _ids(args.exclude) | _ids(_read_env("REPORT_EXCLUDE_USERS"))
    report = build_report(events, exclude)
    title = f"DrapeFit metrics: last {args.days} days ({source}, {len(events)} events" + (
        f", excluding {len(exclude)} user(s))" if exclude else ")")
    text = format_report(report, title)
    print(text)
    if args.save:
        out = ROOT / "docs" / "metrics" / f"report-{datetime.now():%Y-%m-%d}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"```\n{text}\n```\n", encoding="utf-8")
        print(f"\nsaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
