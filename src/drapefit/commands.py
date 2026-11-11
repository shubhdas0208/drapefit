"""Strict text grammar for cart actions, handled in code with no LLM (D47).

A message counts as a cart command only if every word is a label (A1..E10), a cart
verb or filler. "add A1 and B3 please" is a command; "add a black hoodie" is not,
so it still reaches the parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CartAction = Literal["add", "remove", "show_cart", "checkout", "clear"]

LABEL = re.compile(r"^([a-e])(10|[1-9])$", re.IGNORECASE)
ADD_WORDS = {"add", "+", "buy", "take", "want", "le", "lo", "lena", "daal", "dalo", "daalo", "chahiye", "plus"}
REMOVE_WORDS = {"remove", "delete", "drop", "hatao", "hata", "hatado", "nikalo", "nikal", "minus", "-", "x", "rm"}
CART_WORDS = {"cart", "basket", "bag"}
CHECKOUT_WORDS = {"checkout", "pay"}
CLEAR_WORDS = {"clear", "empty", "reset"}
FILLER = {
    "and", "&", "please", "pls", "plz", "to", "the", "my", "in", "me", "from", "of", "karo", "kar", "do", "de",
    "dena", "mein", "main", "ko", "bhi", "show", "see", "view", "open", "items", "item", "both", "also", "too", "it", "now", "check", "out", "ok",
}


@dataclass(frozen=True)
class CartCommand:
    action: CartAction
    labels: tuple[str, ...] = ()


def _words(text: str) -> list[str]:
    spaced = re.sub(r"[,;.!?/]+", " ", text.strip().lower())
    spaced = re.sub(r"(?<=\s)([+-])(?=[a-e]\d)", r"\1 ", f" {spaced}")
    return spaced.split()


def parse_cart_command(text: str) -> CartCommand | None:
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        return {
            "/cart": CartCommand("show_cart"),
            "/checkout": CartCommand("checkout"),
            "/clear": CartCommand("clear"),
        }.get(command)
    words = _words(text)
    if not words or len(words) > 20:
        return None
    labels: list[str] = []
    verbs: set[str] = set()
    for word in words:
        match = LABEL.match(word)
        if match:
            label = f"{match.group(1).upper()}{match.group(2)}"
            if label not in labels:
                labels.append(label)
        elif word in ADD_WORDS:
            verbs.add("add")
        elif word in REMOVE_WORDS:
            verbs.add("remove")
        elif word in CHECKOUT_WORDS:
            verbs.add("checkout")
        elif word in CLEAR_WORDS:
            verbs.add("clear")
        elif word in CART_WORDS:
            verbs.add("cart")
        elif word not in FILLER:
            return None  # any other word means this is language, not a command
    if labels:
        if "remove" in verbs and "add" in verbs:
            return None
        return CartCommand("remove" if "remove" in verbs else "add", tuple(labels))
    if "checkout" in verbs or words == ["check", "out"]:
        return CartCommand("checkout")
    if "clear" in verbs:
        return CartCommand("clear")
    if verbs == {"cart"}:
        return CartCommand("show_cart")
    return None
