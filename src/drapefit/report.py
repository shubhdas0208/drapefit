"""Metrics from the append-only event log (D22, D64). Pure functions over event dicts.

A "search" is one session started by a new request (not a clarification reply). The
funnel counts searches that reached each step at least once:

    searched -> results shown -> added to cart -> checkout links sent -> clicked out -> bought

North star: the share of searches with a click-out to a brand's site (product link or
checkout link). Also reported within 60 s of the first results, the design-doc target.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

CLICK_TYPES = ("link_clicked", "checkout_clicked")
FAST_CLICK_SECONDS = 60
TOP_N = 8


def _ts(event: dict) -> datetime:
    ts = event["ts"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


@dataclass
class Report:
    users: int = 0
    returning_users: int = 0
    searches: int = 0
    funnel: list[tuple[str, int, float | None]] = field(default_factory=list)
    north_star_pct: float | None = None
    north_star_fast_pct: float | None = None
    items_searched: int = 0
    zero_result_items: int = 0
    zero_result_pct: float | None = None
    top_zero_queries: list[tuple[str, int]] = field(default_factory=list)
    parse_requests: int = 0
    parse_failed: int = 0
    clarify_asked: int = 0
    follow_ups: int = 0
    classify_failed: int = 0
    follow_up_actions: list[tuple[str, int]] = field(default_factory=list)
    refined: int = 0
    cheaper_taps: int = 0
    first_result_ms_p50: float | None = None
    first_result_ms_p95: float | None = None
    parse_ms_p50: float | None = None
    parse_ms_p95: float | None = None
    slowest_searches: list[tuple[str, int]] = field(default_factory=list)
    purchases: int = 0
    purchases_by_brand: list[tuple[str, int]] = field(default_factory=list)
    declined: int = 0
    later: int = 0
    blockers: list[tuple[str, int]] = field(default_factory=list)
    problems: list[tuple[str, int]] = field(default_factory=list)
    unmapped_sizes: list[tuple[str, int]] = field(default_factory=list)


def _search_sessions(events: list[dict]) -> dict[str, dict[str, Any]]:
    """session_id -> {"start": ts, "text": request text} for new searches."""
    sessions: dict[str, dict[str, Any]] = {}
    for e in events:
        payload = e.get("payload") or {}
        if e["type"] == "request_received" and not payload.get("is_clarification_reply") and e.get("session_id"):
            sessions.setdefault(e["session_id"], {"start": _ts(e), "text": str(payload.get("text", ""))[:80]})
    return sessions


def _first(events: list[dict], session_ids: set[str], types: tuple[str, ...]) -> dict[str, datetime]:
    first: dict[str, datetime] = {}
    for e in events:
        sid = e.get("session_id")
        if sid in session_ids and e["type"] in types and sid not in first:
            first[sid] = _ts(e)
    return first


def build_report(events: list[dict], exclude_users: set[int] | None = None) -> Report:
    exclude = exclude_users or set()
    events = sorted((e for e in events if e.get("telegram_user_id") not in exclude), key=_ts)
    r = Report()
    r.users = len({e["telegram_user_id"] for e in events if e.get("telegram_user_id") is not None})
    # Returning = searched again on another day. Answering the follow-up the next day doesn't count.
    search_days: dict[int, set] = {}
    for e in events:
        if e["type"] == "request_received" and e.get("telegram_user_id") is not None:
            search_days.setdefault(e["telegram_user_id"], set()).add(_ts(e).date())
    r.returning_users = sum(1 for days in search_days.values() if len(days) > 1)

    sessions = _search_sessions(events)
    ids = set(sessions)
    r.searches = len(ids)

    def reached(*types: str, where=None) -> set[str]:
        return {
            e["session_id"] for e in events
            if e.get("session_id") in ids and e["type"] in types and (where is None or where(e.get("payload") or {}))
        }

    shown = reached("results_shown")
    carted = reached("cart_add", where=lambda p: p.get("outcome") == "added")
    checkout = reached("checkout_sent", where=lambda p: bool(p.get("merchants")))
    clicked = reached(*CLICK_TYPES)
    bought = reached("purchase_reported")
    r.funnel = [
        (step, n, _pct(n, r.searches))
        for step, n in (
            ("searched", r.searches), ("results shown", len(shown)), ("added to cart", len(carted)),
            ("checkout links sent", len(checkout)), ("clicked out to a brand", len(clicked)),
            ("reported a purchase", len(bought)),
        )
    ]
    r.north_star_pct = _pct(len(clicked), r.searches)
    first_shown = _first(events, ids, ("results_shown",))
    first_click = _first(events, ids, CLICK_TYPES)
    fast = sum(
        1 for sid, t in first_click.items()
        if sid in first_shown and 0 <= (t - first_shown[sid]).total_seconds() <= FAST_CLICK_SECONDS
    )
    r.north_star_fast_pct = _pct(fast, r.searches)

    _items(r, events)
    _language(r, events)
    _latency(r, events, sessions, first_shown)
    _purchases(r, events)
    _problems(r, events)
    return r


def _items(r: Report, events: list[dict]) -> None:
    r.items_searched = sum(1 for e in events if e["type"] == "item_searched")
    zeros = [e for e in events if e["type"] == "zero_results"]
    r.zero_result_items = len(zeros)
    r.zero_result_pct = _pct(len(zeros), r.items_searched)
    names = Counter()
    for e in zeros:
        item = (e.get("payload") or {}).get("item") or {}
        names[" ".join(str(v) for v in (item.get("color"), item.get("query_terms"),
                                         f"size {item['size']}" if item.get("size") else None,
                                         f"<₹{item['max_price_inr']}" if item.get("max_price_inr") else None) if v)] += 1
    r.top_zero_queries = names.most_common(TOP_N)


def _language(r: Report, events: list[dict]) -> None:
    count = Counter(e["type"] for e in events)
    r.parse_requests = count["parsed"] + count["parse_failed"]
    r.parse_failed = count["parse_failed"]
    r.clarify_asked = count["clarify_asked"]
    r.follow_ups = count["follow_up_received"]
    r.classify_failed = count["classify_failed"]
    r.refined = count["refined"]
    r.cheaper_taps = count["cheaper_tapped"]
    actions = Counter(
        ((e.get("payload") or {}).get("follow_up") or {}).get("action", "?")
        for e in events if e["type"] == "follow_up_classified"
    )
    r.follow_up_actions = actions.most_common()


def _latency(r: Report, events: list[dict], sessions: dict, first_shown: dict[str, datetime]) -> None:
    waits = {sid: (first_shown[sid] - s["start"]).total_seconds() * 1000 for sid, s in sessions.items() if sid in first_shown}
    r.first_result_ms_p50 = _percentile(list(waits.values()), 0.5)
    r.first_result_ms_p95 = _percentile(list(waits.values()), 0.95)
    parse = [float((e.get("payload") or {}).get("latency_ms")) for e in events
             if e["type"] == "parsed" and isinstance((e.get("payload") or {}).get("latency_ms"), (int, float))]
    r.parse_ms_p50 = _percentile(parse, 0.5)
    r.parse_ms_p95 = _percentile(parse, 0.95)
    slow = sorted(waits.items(), key=lambda kv: kv[1], reverse=True)[:3]
    r.slowest_searches = [(sessions[sid]["text"], int(ms)) for sid, ms in slow]


def _purchases(r: Report, events: list[dict]) -> None:
    reported = [e for e in events if e["type"] == "purchase_reported"]
    r.purchases = len(reported)
    r.purchases_by_brand = Counter((e.get("payload") or {}).get("name", "?") for e in reported).most_common(TOP_N)
    r.declined = sum(1 for e in events if e["type"] == "purchase_declined")
    r.later = sum(1 for e in events if e["type"] == "purchase_later")
    r.blockers = Counter((e.get("payload") or {}).get("reason", "?") for e in events
                         if e["type"] == "purchase_blocker").most_common()


PROBLEM_TYPES = ("error", "send_failed", "photo_failed", "catalog_error", "parse_failed", "classify_failed",
                 "size_unreadable", "busy_rejected", "followup_failed", "callback_unknown")


def _problems(r: Report, events: list[dict]) -> None:
    count = Counter(e["type"] for e in events if e["type"] in PROBLEM_TYPES)
    r.problems = count.most_common()
    labels = Counter(label for e in events if e["type"] == "size_unmapped"
                     for label in (e.get("payload") or {}).get("labels", []))
    r.unmapped_sizes = labels.most_common(TOP_N)


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f} s"


def format_report(r: Report, title: str) -> str:
    out = [title, "=" * len(title), ""]
    out.append(f"Users: {r.users} ({r.returning_users} came back on another day)")
    out.append(f"NORTH STAR: {_fmt_pct(r.north_star_pct)} of searches led to a click to a brand's site "
               f"({_fmt_pct(r.north_star_fast_pct)} within {FAST_CLICK_SECONDS} s of the first results)")
    out += ["", "Funnel (searches reaching each step)"]
    widest = max((len(step) for step, _, _ in r.funnel), default=0)
    out += [f"  {step:<{widest}}  {n:>4}  {_fmt_pct(p):>6}" for step, n, p in r.funnel]
    out += ["", "Search quality",
            f"  items searched: {r.items_searched}, zero results: {r.zero_result_items} ({_fmt_pct(r.zero_result_pct)})"]
    out += [f"    {n}x  {q}" for q, n in r.top_zero_queries]
    if r.unmapped_sizes:
        out.append("  size labels the matcher can't read: " + ", ".join(f"{s} ({n})" for s, n in r.unmapped_sizes))
    out += ["", "Language understanding",
            f"  new requests parsed: {r.parse_requests}, failed: {r.parse_failed} "
            f"({_fmt_pct(_pct(r.parse_failed, r.parse_requests))}), clarifying questions: {r.clarify_asked}",
            f"  follow-up messages: {r.follow_ups}, classifier failed: {r.classify_failed}, re-searches: {r.refined}, "
            f"'cheaper' taps: {r.cheaper_taps}"]
    if r.follow_up_actions:
        out.append("  follow-up actions: " + ", ".join(f"{a} {n}" for a, n in r.follow_up_actions))
    out += ["", "Speed",
            f"  first results: p50 {_fmt_ms(r.first_result_ms_p50)}, p95 {_fmt_ms(r.first_result_ms_p95)} (target p95 < 8 s)",
            f"  parser: p50 {_fmt_ms(r.parse_ms_p50)}, p95 {_fmt_ms(r.parse_ms_p95)}"]
    out += [f"    slow: {ms / 1000:.1f} s  {text!r}" for text, ms in r.slowest_searches]
    out += ["", "Did they buy? (24 h follow-up)",
            f"  bought: {r.purchases}, didn't buy: {r.declined}, not yet: {r.later}"]
    if r.purchases_by_brand:
        out.append("  by brand: " + ", ".join(f"{b} {n}" for b, n in r.purchases_by_brand))
    if r.blockers:
        out.append("  why not: " + ", ".join(f"{b} {n}" for b, n in r.blockers))
    out += ["", "Problems"]
    out += [f"  {t}: {n}" for t, n in r.problems] or ["  none"]
    return "\n".join(out)
