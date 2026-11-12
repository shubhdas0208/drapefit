"""Grade one follow-up classification against its expectation.

Rules: action must match. items/labels are compared as sets when the case gives them.
Changes the case names must match (case-insensitive strings); for refine cases every
change the case does NOT name must be empty, so invented changes count as errors.
"""
from __future__ import annotations

CHANGE_FIELDS = ("color", "size", "query_terms", "gender", "fit", "max_price_inr", "cheaper")


def _norm(value):
    if not isinstance(value, str):
        return value
    value = value.strip().lower()
    return value[: -len(" fit")] if value.endswith(" fit") else value  # "slim fit" == "slim"


def grade_case(case: dict, got: dict | None) -> dict:
    expect = case["expect"]
    notes: list[str] = []
    if got is None:
        return {"exact": False, "action_ok": False, "notes": ["classification failed"]}
    action_ok = got["action"] == expect["action"]
    if not action_ok:
        notes.append(f"action {got['action']} != {expect['action']}")
    for key in ("items", "labels"):
        if key in expect and set(got.get(key) or []) != set(expect[key]):
            notes.append(f"{key} {got.get(key)} != {expect[key]}")
    changes = got.get("changes") or {}
    wanted = expect.get("changes", {})
    for field in CHANGE_FIELDS:
        value = changes.get(field)
        if field in wanted:
            if _norm(value) != _norm(wanted[field]):
                notes.append(f"{field} {value!r} != {wanted[field]!r}")
        elif expect["action"] == "refine" and "changes" in expect and value not in (None, False):
            notes.append(f"invented {field}={value!r}")
    return {"exact": not notes, "action_ok": action_ok, "notes": notes}
