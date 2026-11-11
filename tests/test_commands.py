"""Cart text grammar (D47): strict, so normal shopping requests never trigger it."""
import pytest

from drapefit.commands import CartCommand, parse_cart_command


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("add A1", CartCommand("add", ("A1",))),
        ("A1", CartCommand("add", ("A1",))),
        ("add a1 and b3 please", CartCommand("add", ("A1", "B3"))),
        ("A1, B3", CartCommand("add", ("A1", "B3"))),
        ("+A2", CartCommand("add", ("A2",))),
        ("B3 lelo", None),  # unknown word "lelo" -> language, goes to the classifier
        ("B3 le lo", CartCommand("add", ("B3",))),
        ("A1 cart mein daal do", CartCommand("add", ("A1",))),
        ("add A1 A1", CartCommand("add", ("A1",))),
        ("e10", CartCommand("add", ("E10",))),
        ("remove B2", CartCommand("remove", ("B2",))),
        ("B2 hatao", CartCommand("remove", ("B2",))),
        ("delete a1 and a2", CartCommand("remove", ("A1", "A2"))),
        ("cart", CartCommand("show_cart")),
        ("show my cart", CartCommand("show_cart")),
        ("/cart", CartCommand("show_cart")),
        ("checkout", CartCommand("checkout")),
        ("check out", CartCommand("checkout")),
        ("/checkout@DrapeFitBot", CartCommand("checkout")),
        ("pay", CartCommand("checkout")),
        ("clear cart", CartCommand("clear")),
        ("/clear", CartCommand("clear")),
    ],
)
def test_cart_grammar(text, expected):
    assert parse_cart_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "add a black hoodie",  # language, not labels
        "grey turtleneck M under 2000",
        "same but black",
        "show cheaper",
        "F1",  # only A-E exist
        "A11",
        "add A1 and remove B2",  # mixed intent: let the classifier decide
        "ok",
        "",
        "/delete",
        "/start",
        "remove",
        "size 32 jeans",
    ],
)
def test_not_a_cart_command(text):
    assert parse_cart_command(text) is None
