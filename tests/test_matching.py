import pytest

from drapefit import matching


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("M", {"A:M"}),
        ("m", {"A:M"}),
        ("MEDIUM", {"A:M"}),
        ("Medium (M)", {"A:M"}),
        ("2XL", {"A:XXL"}),
        ("XXL", {"A:XXL"}),
        ("2X", {"A:XXL"}),
        ("Double extra large (XXL)", {"A:XXL"}),
        ("Extra large (XL)", {"A:XL"}),
        ("XXXL", {"A:3XL"}),
        ("M (38)", {"A:M", "N:38"}),
        ("38/M", {"A:M", "N:38"}),
        ("L-40", {"A:L", "N:40"}),
        ("40 - L", {"A:L", "N:40"}),
        ("44 - 2XL", {"A:XXL", "N:44"}),
        ("30", {"N:30"}),
        ("S-(28-30)", {"A:S", "N:28", "N:30"}),
        ("Large (L),38 EU", {"A:L"}),
        ("Free Size", {"A:FREE"}),
        ("", set()),
        (None, set()),
        ("Regular", set()),
    ],
)
def test_size_tokens(label, expected):
    assert matching.size_tokens(label) == frozenset(expected)


def test_xl_inside_2xl_is_not_xl():
    assert not matching.size_matches("XL", "2XL")
    assert matching.size_matches("XXL", "2XL")


@pytest.mark.parametrize(
    ("requested", "label", "ok"),
    [
        ("M", "M (38)", True),
        ("38", "M (38)", True),
        ("32", "32", True),
        ("32", "30", False),
        ("M", "38", False),  # never cross-map alpha and numeric (fail closed)
        ("L", "Large (L),38 EU", True),
        (None, "M", False),
    ],
)
def test_size_matches(requested, label, ok):
    assert matching.size_matches(requested, label) is ok


def test_requested_size_token_rejects_compound_requests():
    assert matching.requested_size_token("M (38)") is None
    assert matching.requested_size_token("M") == "A:M"
    assert matching.requested_size_token("") is None


def test_pick_size_label_returns_store_spelling_in_store_order():
    assert matching.pick_size_label("XXL", ["S", "M", "2XL", "XXL"]) == "2XL"
    assert matching.pick_size_label("M", ["S", "L"]) is None


@pytest.mark.parametrize(
    ("requested", "texts", "ok"),
    [
        ("grey", ("Light Grey",), True),
        ("grey", (None, "Charcoal Melange Sweater"), True),
        ("gray", ("Ash",), True),
        ("blue", ("Navy",), True),
        ("navy", ("Blue",), False),
        ("grey", ("Grey-Blue",), True),
        ("blue", ("Grey-Blue",), True),
        ("red", ("Shredded Jeans",), False),  # word boundary: no 'red' inside 'shredded'
        ("black", ("Black",), True),
        (None, ("anything",), True),
        ("teal", ("Teal",), True),  # unknown colour falls back to itself
        ("olive green", ("Olive",), True),  # unknown phrase -> known words inside it
        ("sky blue", ("Navy Blue",), True),
        ("olive green", ("Black",), False),
        ("neeli", ("Blue",), True),  # Hindi passed through by the parser
    ],
)
def test_colour_matches(requested, texts, ok):
    assert matching.colour_matches(requested, *texts) is ok
