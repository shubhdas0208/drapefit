"""Apply a follow-up change ("same but black", "show cheaper") to the last request (D48).

Pure. The LLM only says WHAT should change; this module decides the new spec.
"Cheaper" is computed here from the prices actually shown, never by the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Changes, FollowUp, ItemSpec, RequestSpec
from .render import escape_text


@dataclass(frozen=True)
class Refinement:
    spec: RequestSpec | None  # updated full spec, or None when nothing can run
    indices: tuple[int, ...]  # items to search again
    message: str | None = None  # shown to the user instead of / before searching


def letter(index: int) -> str:
    return chr(ord("A") + index)


def _targets(spec: RequestSpec, follow_up: FollowUp) -> list[int]:
    named = [ord(x) - ord("A") for x in follow_up.items]
    named += [ord(label[0]) - ord("A") for label in follow_up.labels]
    seen: list[int] = []
    for index in named:
        if 0 <= index < len(spec.items) and index not in seen:
            seen.append(index)
    return seen


def _updated(item: ItemSpec, changes: Changes, cheapest_shown_inr: int | None) -> tuple[ItemSpec | None, str | None]:
    update: dict = {}
    for field in ("color", "size", "query_terms", "gender", "fit"):
        value = getattr(changes, field)
        if value is not None:
            update[field] = value
    if "size" in update:
        update["size_system"] = "unknown"
    if changes.max_price_inr is not None:
        update["max_price_inr"] = changes.max_price_inr
    elif changes.cheaper:
        if cheapest_shown_inr is None:
            return None, "no prices were shown for it yet"
        if cheapest_shown_inr <= 1:
            return None, "it is already the cheapest I can find"
        update["max_price_inr"] = cheapest_shown_inr - 1
    if not update:
        return None, None
    return item.model_copy(update=update), None


def apply_refinement(
    spec: RequestSpec, follow_up: FollowUp, cheapest_shown_inr: dict[int, int]
) -> Refinement:
    changes = follow_up.changes
    if changes.is_empty:
        return Refinement(None, (), None)
    targets = _targets(spec, follow_up)
    if not targets:
        if len(spec.items) == 1 or changes.price_only:
            targets = list(range(len(spec.items)))
        else:
            options = " or ".join(f"{letter(i)} ({escape_text(item.query_terms)})" for i, item in enumerate(spec.items))
            return Refinement(None, (), f"Which item should I change: {options}? Reply like: <i>B in black</i>")

    items = list(spec.items)
    run: list[int] = []
    problems: list[str] = []
    for index in targets:
        new_item, problem = _updated(items[index], changes, cheapest_shown_inr.get(index))
        if problem:
            problems.append(f"{letter(index)}: {problem}")
        if new_item is not None:
            items[index] = new_item
            run.append(index)
    message = ("Couldn't change " + "; ".join(problems) + ".") if problems else None
    if not run:
        return Refinement(None, (), message)
    new_spec = spec.model_copy(update={"items": items, "ambiguities": []})
    return Refinement(new_spec, tuple(run), message)
