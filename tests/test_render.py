"""Grounding gate (design doc: 'target 100%, anything lower fails the build').

Every price, size, seller and link shown to the user must equal the raw API variant
it came from. We run the real pipeline over all 20 recorded spike queries, render,
then parse the rendered text back and compare with the raw variant objects.
"""
import html
import re

import pytest

from drapefit import pipeline, render
from drapefit.models import ItemResult, ItemSpec

from .conftest import RAW, FakeCatalog, item, load_search

LINE = re.compile(
    r"<b>(?P<label>[A-Z]\d)</b> (?P<title>.+)\n    (?P<seller>.+) · <b>(?P<price>₹[\d,]+)</b>"
    r"(?: · size (?P<size>.+?))? · (?:<a href=\"(?P<link>[^\"]+)\">open</a>|link unavailable)"
)

QUERIES = sorted(p.stem for p in RAW.glob("*.json"))


@pytest.mark.parametrize("name", QUERIES)
async def test_rendered_values_equal_raw_api_variants(name):
    products = load_search(name)
    by_id = {p["id"]: p for p in products}
    words = name.split("-")[1:]
    gender = "women" if words[-1] == "women" else "men"
    it = ItemSpec.model_validate(item(query_terms=" ".join(words[1:-1]), color=words[0], size=None,
                                      gender=gender, max_price_inr=3500, category="top"))
    result = await pipeline.run_item(FakeCatalog(products), 0, it, 3500)
    assert result.options, "gate must check real options, not an empty list"
    links = {o.label: f"https://drapefit.example/r/tok{o.label}" for o in result.options}
    text = render.render_item(result, links).text

    rendered = {m["label"]: m for m in LINE.finditer(text)}
    assert set(rendered) == {o.label for o in result.options}, "every option must render, nothing extra"
    for option in result.options:
        raw = next(v for v in by_id[option.product_id]["variants"] if v["id"] == option.variant_id)
        line = rendered[option.label]
        assert line["price"] == render.inr(raw["price"]["amount"])
        assert raw["price"]["currency"] == "INR" and raw["availability"]["available"] is True
        assert html.unescape(line["seller"]) == raw["seller"]["name"].strip()
        raw_size = next((o["label"] for o in raw.get("options", []) if o["name"].lower() == "size"), None)
        assert (html.unescape(line["size"]) if line["size"] else None) == raw_size
        assert line["link"] == links[option.label]
        assert option.product_url == raw["url"]


@pytest.mark.parametrize(
    ("paise", "text"),
    [(0, "₹0"), (99900, "₹999"), (149800, "₹1,498"), (1299900, "₹12,999"), (12500000, "₹1,25,000"), (1250000000, "₹1,25,00,000")],
)
def test_inr_uses_indian_grouping(paise, text):
    assert render.inr(paise) == text


def test_zero_results_copy_suggests_concrete_fixes():
    it = ItemSpec.model_validate(item())
    text = render.render_item(ItemResult(0, it, (), 2000), {}).text
    assert "No grey turtleneck in M under ₹2,000 from Indian brands" in text
    assert "₹2,500" in text and "another colour" in text


def test_titles_are_escaped_and_truncated():
    products = load_search("01-grey-turtleneck-men")
    option = pipeline.make_option("A1", 0, products[0], products[0]["variants"][0])
    evil = option.__class__(**{**option.__dict__, "title": "<script>x</script> " + "a" * 100})
    line = render.option_line(evil, "https://x/r/t")
    assert "<script>" not in line and "&lt;script&gt;" in line
    assert "…" in line


def test_missing_link_is_shown_as_unavailable():
    products = load_search("01-grey-turtleneck-men")
    option = pipeline.make_option("A1", 0, products[0], products[0]["variants"][0])
    assert "link unavailable" in render.option_line(option, None)


def test_album_has_one_photo_per_option_with_label_caption():
    products = load_search("03-white-linen-shirt-men")
    options = tuple(pipeline.make_option(f"A{i + 1}", 0, p, p["variants"][0]) for i, p in enumerate(products[:5]))
    rendered = render.render_item(ItemResult(0, ItemSpec.model_validate(item()), options, 2000), {})
    assert [c for _, c in rendered.photos] == ["A1", "A2", "A3", "A4", "A5"]


def test_ack_and_footer():
    assert render.ack_text(1) == "Searching 1 item…"
    assert render.ack_text(2) == "Searching 2 items…"
    assert "₹4,000" in render.footer_text(4000)
    assert "budget" not in render.footer_text(None)
