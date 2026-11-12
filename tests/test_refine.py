"""Follow-up changes are applied by code, not the LLM (D48)."""
from drapefit.models import FollowUp, RequestSpec
from drapefit.refine import apply_refinement

from .conftest import item, spec


def two_items() -> RequestSpec:
    return RequestSpec.model_validate(
        spec(item(), item(query_terms="bell bottom jeans", category="bottom", color="blue", size="32"))
    )


def fu(**kwargs) -> FollowUp:
    return FollowUp.model_validate({"action": "refine", **kwargs})


def test_colour_change_on_named_item_only():
    r = apply_refinement(two_items(), fu(items=["B"], changes={"color": "black"}), {})
    assert r.indices == (1,)
    assert r.spec.items[1].color == "black" and r.spec.items[0].color == "grey"
    assert r.message is None


def test_label_names_its_item():
    r = apply_refinement(two_items(), fu(labels=["A3"], changes={"size": "L"}), {})
    assert r.indices == (0,) and r.spec.items[0].size == "L" and r.spec.items[0].size_system == "unknown"


def test_untargeted_colour_change_with_two_items_asks_which():
    r = apply_refinement(two_items(), fu(changes={"color": "black"}), {})
    assert r.spec is None and "Which item" in r.message and "A (turtleneck)" in r.message


def test_untargeted_change_with_one_item_applies_to_it():
    one = RequestSpec.model_validate(spec(item()))
    r = apply_refinement(one, fu(changes={"color": "black"}), {})
    assert r.indices == (0,) and r.spec.items[0].color == "black"


def test_cheaper_uses_cheapest_shown_price_minus_one_rupee():
    r = apply_refinement(two_items(), fu(changes={"cheaper": True}), {0: 1499, 1: 999})
    assert r.indices == (0, 1)  # price-only change applies to every item
    assert r.spec.items[0].max_price_inr == 1498 and r.spec.items[1].max_price_inr == 998


def test_cheaper_without_shown_prices_explains():
    r = apply_refinement(two_items(), fu(items=["B"], changes={"cheaper": True}), {0: 1499})
    assert r.spec is None and "B: no prices were shown" in r.message


def test_explicit_budget_beats_cheaper():
    r = apply_refinement(two_items(), fu(items=["A"], changes={"cheaper": True, "max_price_inr": 1200}), {0: 1499})
    assert r.spec.items[0].max_price_inr == 1200


def test_partial_failure_still_runs_the_rest():
    r = apply_refinement(two_items(), fu(changes={"cheaper": True}), {0: 1499})
    assert r.indices == (0,) and "B:" in r.message


def test_no_changes_means_nothing_to_run():
    r = apply_refinement(two_items(), fu(items=["A"]), {})
    assert r.spec is None and r.indices == () and r.message is None


def test_unknown_letters_are_ignored():
    r = apply_refinement(two_items(), FollowUp.model_validate(
        {"action": "refine", "items": ["D", "z", "B"], "changes": {"color": "red"}}), {})
    assert r.indices == (1,)


def test_followup_model_cleans_labels():
    f = FollowUp.model_validate({"action": "add", "labels": ["a1", " B 3", "Z9", "A11"], "items": ["b", "x"]})
    assert f.labels == ["A1", "B3"] and f.items == ["B"]
