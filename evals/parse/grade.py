"""Grading rules for the parse eval (design doc: Evals).

Per item, these fields are graded: category, color, size, gender, max_price_inr.
A gold value can be a list of acceptable answers. Size compares normalised tokens
(so "XL" = "xl", "2XL" = "XXL"). Item count, budget_type and total_max_inr are graded
per case. `ambiguous` (True/False) is graded when not null.
Exact match = every graded field in the case is correct.
"""
from __future__ import annotations

from typing import Any

from drapefit.matching import COLOUR_ALIASES, size_tokens

ITEM_FIELDS = {"cat": "category", "col": "color", "size": "size", "g": "gender", "cap": "max_price_inr"}


def _as_list(gold: Any) -> list:
    return gold if isinstance(gold, list) else [gold]


def _norm_colour(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    return COLOUR_ALIASES.get(v, v)


def field_ok(name: str, gold: Any, got: Any) -> bool:
    accepted = _as_list(gold)
    if name == "size":
        return any(
            (g is None and not got) or (g is not None and got and size_tokens(g) == size_tokens(got))
            for g in accepted
        )
    if name == "col":
        return _norm_colour(got) in {_norm_colour(g) for g in accepted}
    return got in accepted


def _item_score(gold: dict, got: dict) -> int:
    return sum(field_ok(k, gold.get(k), got.get(f)) for k, f in ITEM_FIELDS.items())


def _pair_items(gold_items: list[dict], items: list[dict]) -> list[tuple[dict, dict]]:
    """Order-insensitive pairing: the user sees items as A/B either way, so order is not graded.
    Uses the best-scoring permutation (item counts are tiny, max 5)."""
    from itertools import permutations

    n = min(len(gold_items), len(items))
    if n == 0:
        return []
    best = max(
        permutations(range(len(items)), n),
        key=lambda perm: sum(_item_score(gold_items[i], items[j]) for i, j in enumerate(perm)),
    )
    return [(gold_items[i], items[j]) for i, j in enumerate(best)]


def grade_case(case: dict, spec: dict | None) -> dict:
    """Returns {'exact': bool, 'fields': {field: [ok, ...]}, 'notes': [...]}."""
    result: dict = {"fields": {}, "notes": [], "exact": False}
    if spec is None:
        result["notes"].append("parse failed")
        return result
    checks: list[bool] = []

    def record(field: str, ok: bool, note: str) -> None:
        result["fields"].setdefault(field, []).append(ok)
        checks.append(ok)
        if not ok:
            result["notes"].append(note)

    items = spec.get("items", [])
    gold_items = case["items"]
    record("item_count", len(items) == len(gold_items), f"items: got {len(items)}, want {len(gold_items)}")
    for idx, (gold, got) in enumerate(_pair_items(gold_items, items)):
        for key, spec_field in ITEM_FIELDS.items():
            ok = field_ok(key, gold.get(key), got.get(spec_field))
            record(key, ok, f"item {idx} {spec_field}: got {got.get(spec_field)!r}, want {gold.get(key)!r}")
    record("budget_type", spec.get("budget_type") == case["budget_type"], f"budget_type: got {spec.get('budget_type')}")
    record(
        "total_max_inr",
        spec.get("total_max_inr") == case["total_max_inr"],
        f"total: got {spec.get('total_max_inr')}, want {case['total_max_inr']}",
    )
    asked = bool(spec.get("ambiguities"))
    if case["ambiguous"] is not None:
        record("ambiguous", asked == case["ambiguous"], f"ambiguity: asked={asked}, want {case['ambiguous']}")
    result["asked"] = asked
    result["exact"] = all(checks)
    return result
