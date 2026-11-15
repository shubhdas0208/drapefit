"""Metrics report (D64): funnel, north star, quality, speed, purchases, problems."""
from datetime import datetime, timedelta, timezone

from drapefit.report import build_report, format_report

T0 = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)


def ev(seconds, user, session, type_, **payload):
    return {"ts": T0 + timedelta(seconds=seconds), "telegram_user_id": user, "session_id": session,
            "type": type_, "payload": payload}


def sample():
    return [
        # search s1 (user 1): results, cart, checkout, fast click, bought
        ev(0, 1, "s1", "request_received", text="grey turtleneck M"),
        ev(1, 1, "s1", "parsed", latency_ms=400),
        ev(2, 1, "s1", "item_searched"),
        ev(5, 1, "s1", "results_shown", labels=["A1"]),
        ev(20, 1, "s1", "cart_add", outcome="added"),
        ev(30, 1, "s1", "checkout_sent", merchants=["a.in"]),
        ev(40, 1, "s1", "checkout_clicked", host="a.in"),
        ev(90000, 1, "s1", "purchase_reported", name="Brand A", host="a.in"),
        # search s2 (user 2): zero results, slow click, didn't buy (price)
        ev(100, 2, "s2", "request_received", text="mustard saree"),
        ev(101, 2, "s2", "parsed", latency_ms=600),
        ev(103, 2, "s2", "item_searched"),
        ev(104, 2, "s2", "zero_results", item={"color": "mustard", "query_terms": "saree", "max_price_inr": 900}),
        ev(105, 2, "s2", "item_searched"),
        ev(112, 2, "s2", "results_shown", labels=["B1"]),
        ev(400, 2, "s2", "link_clicked", label="B1"),
        ev(500, 2, "s2", "follow_up_received", text="cheaper"),
        ev(501, 2, "s2", "follow_up_classified", follow_up={"action": "refine"}),
        ev(502, 2, "s2", "refined"),
        ev(503, 2, "s2", "cheaper_tapped"),
        ev(504, 2, "s2", "size_unmapped", labels=["M/L"]),
        ev(90100, 2, "s2", "purchase_declined"),
        ev(90101, 2, "s2", "purchase_blocker", reason="price"),
        # user 2 comes back the next day: parse fails, clarification reply doesn't count as a search
        ev(100000, 2, "s3", "request_received", text="???"),
        ev(100001, 2, "s3", "parse_failed"),
        ev(100002, 2, "s3", "request_received", text="M", is_clarification_reply=True),
        ev(100003, 2, None, "error"),
        # the owner (user 9) testing
        ev(10, 9, "s9", "request_received", text="test"),
    ]


def test_funnel_and_north_star():
    r = build_report(sample(), exclude_users={9})
    assert r.users == 2 and r.returning_users == 1
    assert r.searches == 3
    funnel = {step: n for step, n, _ in r.funnel}
    assert funnel == {"searched": 3, "results shown": 2, "added to cart": 1, "checkout links sent": 1,
                      "clicked out to a brand": 2, "reported a purchase": 1}
    assert r.north_star_pct == 66.7
    assert r.north_star_fast_pct == 33.3  # s1 clicked 35 s after results; s2 after ~5 min


def test_quality_language_speed_and_purchases():
    r = build_report(sample(), exclude_users={9})
    assert r.items_searched == 3 and r.zero_result_items == 1 and r.zero_result_pct == 33.3
    assert r.top_zero_queries == [("mustard saree <₹900", 1)]
    assert r.unmapped_sizes == [("M/L", 1)]
    assert r.parse_requests == 3 and r.parse_failed == 1
    assert r.follow_up_actions == [("refine", 1)] and r.refined == 1 and r.cheaper_taps == 1
    assert r.first_result_ms_p50 in (5000, 12000) and r.parse_ms_p95 == 600
    assert r.slowest_searches[0] == ("mustard saree", 12000)
    assert r.purchases == 1 and r.purchases_by_brand == [("Brand A", 1)]
    assert r.declined == 1 and r.blockers == [("price", 1)]
    assert dict(r.problems) == {"parse_failed": 1, "error": 1}


def test_excluded_user_is_gone_and_empty_log_is_safe():
    with_owner = build_report(sample())
    assert with_owner.searches == 4 and with_owner.users == 3
    empty = build_report([])
    assert empty.searches == 0 and empty.north_star_pct is None
    text = format_report(empty, "t")
    assert "NORTH STAR: n/a" in text and "Problems\n  none" in text


def test_format_has_every_section():
    text = format_report(build_report(sample(), {9}), "DrapeFit metrics")
    for heading in ("NORTH STAR", "Funnel", "Search quality", "Language understanding", "Speed",
                    "Did they buy?", "Problems"):
        assert heading in text
    assert "why not: price 1" in text and "1x  mustard saree" in text


def test_string_timestamps_from_sqlite_are_accepted():
    events = [dict(e, ts=e["ts"].replace(tzinfo=None).isoformat()) for e in sample()]
    assert build_report(events, {9}).searches == 3
