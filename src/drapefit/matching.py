"""Colour and size matching. Pure functions over data tables (decision D34).

Size labels seen in the wild (spike-01, 371 products): "M", "m", "MEDIUM", "Medium (M)",
"2XL", "XXL", "Double extra large (XXL)", "M (38)", "38/M", "L-40", "40 - L", "28",
"S-(28-30)", "Large (L),38 EU", "Free Size".

A label is normalised to a set of tokens:
    "M (38)"  -> {"A:M", "N:38"}
    "2XL"     -> {"A:XXL"}
    "30"      -> {"N:30"}
    "S-(28-30)" -> {"A:S", "N:28", "N:30"}
A requested size matches when its token is in the label's set. Alpha and numeric
sizes are never cross-mapped (that would be a guess). Unknown labels give an empty
set, which never matches (fail closed).
"""
from __future__ import annotations

import html
import re

ALPHA_ALIASES: dict[str, str] = {
    "XXS": "XXS", "2XS": "XXS",
    "XS": "XS", "EXTRA SMALL": "XS", "X-SMALL": "XS", "XSMALL": "XS",
    "S": "S", "SMALL": "S", "SM": "S",
    "M": "M", "MEDIUM": "M", "MED": "M",
    "L": "L", "LARGE": "L", "LG": "L",
    "XL": "XL", "EXTRA LARGE": "XL", "X-LARGE": "XL", "XLARGE": "XL",
    "XXL": "XXL", "2XL": "XXL", "2X": "XXL", "XXLARGE": "XXL", "DOUBLE EXTRA LARGE": "XXL",
    "XXXL": "3XL", "3XL": "3XL", "3X": "3XL", "TRIPLE EXTRA LARGE": "3XL",
    "4XL": "4XL", "XXXXL": "4XL", "4X": "4XL",
    "5XL": "5XL", "6XL": "6XL", "7XL": "7XL", "8XL": "8XL",
    "FREE SIZE": "FREE", "FREESIZE": "FREE", "FREE": "FREE", "ONE SIZE": "FREE", "OS": "FREE",
}

# Longest alias first so "EXTRA LARGE" wins over "LARGE".
_ALPHA_PATTERN = re.compile(
    r"(?<![A-Z0-9])("
    + "|".join(re.escape(a) for a in sorted(ALPHA_ALIASES, key=len, reverse=True))
    + r")(?![A-Z0-9])"
)
# Numbers followed by a region (EU/US/UK) are a different scale; ignore them.
_NUMBER_PATTERN = re.compile(r"(?<!\d)(\d{2})(?!\d)(?!\s*(?:EU|US|UK)\b)")
_RANGE_PATTERN = re.compile(r"(?<!\d)(\d{2})\s*-\s*(\d{2})(?!\d)")

MIN_BODY_SIZE_IN = 20
MAX_BODY_SIZE_IN = 60


def size_tokens(label: str | None) -> frozenset[str]:
    if not label:
        return frozenset()
    text = html.unescape(label).upper().replace("_", " ")
    tokens: set[str] = set()
    for match in _ALPHA_PATTERN.finditer(text):
        tokens.add("A:" + ALPHA_ALIASES[match.group(1)])
    for match in _NUMBER_PATTERN.finditer(text):
        number = int(match.group(1))
        if MIN_BODY_SIZE_IN <= number <= MAX_BODY_SIZE_IN:
            tokens.add(f"N:{number}")
    for match in _RANGE_PATTERN.finditer(text):
        low, high = int(match.group(1)), int(match.group(2))
        if MIN_BODY_SIZE_IN <= low < high <= MAX_BODY_SIZE_IN:
            tokens.update(f"N:{n}" for n in range(low, high + 1, 2))
    return frozenset(tokens)


def requested_size_token(size: str | None) -> str | None:
    """Token for the user's requested size, or None if no size was asked for."""
    if not size:
        return None
    tokens = size_tokens(size)
    if len(tokens) == 1:
        return next(iter(tokens))
    return None


def size_matches(requested: str | None, label: str | None) -> bool:
    token = requested_size_token(requested)
    if token is None:
        return False
    return token in size_tokens(label)


def pick_size_label(requested: str | None, labels: list[str]) -> str | None:
    """The store's own label for the requested size, first match in store order."""
    for label in labels:
        if size_matches(requested, label):
            return label
    return None


COLOUR_SYNONYMS: dict[str, tuple[str, ...]] = {
    "grey": ("grey", "gray", "charcoal", "heather", "ash", "slate", "melange", "graphite"),
    "blue": ("blue", "navy", "indigo", "denim", "azure", "cobalt", "teal blue", "sky"),
    "navy": ("navy", "dark blue", "midnight"),
    "white": ("white", "off-white", "off white", "ivory", "cream", "snow"),
    "black": ("black", "jet", "onyx", "charcoal black"),
    "olive": ("olive", "khaki green", "army green", "military"),
    "maroon": ("maroon", "burgundy", "wine", "oxblood"),
    "beige": ("beige", "sand", "khaki", "tan", "stone", "oatmeal", "cream", "camel"),
    "brown": ("brown", "coffee", "chocolate", "mocha", "camel", "rust", "tan"),
    "pink": ("pink", "blush", "rose", "fuchsia", "peach"),
    "mustard": ("mustard", "yellow", "ochre", "turmeric"),
    "yellow": ("yellow", "mustard", "lemon"),
    "green": ("green", "olive", "emerald", "sage", "bottle", "mint", "teal"),
    "red": ("red", "crimson", "scarlet", "cherry"),
    "purple": ("purple", "lavender", "lilac", "violet", "mauve", "plum"),
    "orange": ("orange", "rust", "coral", "tangerine"),
}
COLOUR_ALIASES: dict[str, str] = {
    "gray": "grey", "off white": "white", "cream": "white", "navy blue": "navy",
    # Hindi, in case the parser passes them through untranslated
    "kala": "black", "kaala": "black", "safed": "white", "safaid": "white", "neela": "blue",
    "neeli": "blue", "nila": "blue", "laal": "red", "lal": "red", "hara": "green", "hari": "green",
    "peela": "yellow", "peeli": "yellow", "gulabi": "pink", "bhura": "brown", "sleti": "grey",
}


def colour_words(colour: str) -> tuple[str, ...]:
    """Synonyms for a colour. A phrase we don't know ("olive green", "sky blue") falls back
    to the phrase itself plus the synonyms of every known colour word inside it."""
    key = " ".join(colour.strip().lower().split())
    key = COLOUR_ALIASES.get(key, key)
    if key in COLOUR_SYNONYMS:
        return COLOUR_SYNONYMS[key]
    words: list[str] = [key]
    for part in key.split():
        part = COLOUR_ALIASES.get(part, part)
        words.extend(COLOUR_SYNONYMS.get(part, ()))
    return tuple(dict.fromkeys(words))


def colour_matches(requested: str | None, *texts: str | None) -> bool:
    """True if any synonym of the requested colour appears as a word in any text."""
    if not requested:
        return True
    haystack = " ".join(html.unescape(t).lower() for t in texts if t)
    return any(
        re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", haystack)
        for word in colour_words(requested)
    )
