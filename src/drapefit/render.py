"""Telegram message rendering. Pure functions; no LLM in this path (core logic #2).

Every price, size and title shown comes from an Option, and Options come only from
catalog API variant objects. tests/test_render.py enforces this as a build gate.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import ItemResult, ItemSpec, Option

if TYPE_CHECKING:
    from .checkout import MerchantCheckout

TITLE_MAX_CHARS = 60

HELP_TEXT = (
    "Tell me what you want in one message, for example:\n"
    "<i>grey turtleneck M and blue bell-bottom jeans waist 32, men, under ₹2,000 each</i>\n\n"
    "I search Indian D2C brands and show 5 in-stock options per item, in your size. "
    "Prices and stock come straight from the stores.\n\n"
    "Then: tap <b>+</b> under an option (or send <i>add A1 B3</i>), say <i>same but black</i> or "
    "<i>show cheaper</i>, and /checkout when you're ready. You pay on each brand's own site.\n\n"
    "/cart shows your cart. /delete removes everything I've stored about you."
)
OTHER_TEXT = (
    "I can search again (<i>same but black</i>, <i>show cheaper</i>), add options (<i>add A1</i>), "
    "show your /cart or /checkout. Delivery dates and shipping cost for your pincode show on the brand's "
    "checkout after you enter your address; each checkout message links the brand's shipping and returns policy."
)
CART_EMPTY_TEXT = "Your cart is empty. Tap <b>+</b> under an option, or send <i>add A1</i>."
CART_CLEARED_TEXT = "Cart cleared."
CART_FULL_TEXT = "Your cart is full ({max} items). Remove something first, or /checkout."
UNKNOWN_LABELS_TEXT = "I couldn't find {labels} in your recent results. Search again, then tap <b>+</b>."
FOLLOWUP_THANKS = "Thanks, noted!"
BLOCKER_QUESTION = "Got it. What stopped you? (one tap helps me get better)"
BLOCKER_REASONS = (
    ("price", "Too expensive"),
    ("shipping", "Shipping / COD"),
    ("size", "Size doubt"),
    ("style", "Didn't like it"),
    ("other", "Other"),
)
PARSE_FAILED_TEXT = (
    "Sorry, I couldn't read that. Try something like:\n"
    "<i>black hoodie M under 2000</i>"
)
BUSY_TEXT = "Still working on your last request, one moment…"
ERROR_TEXT = "Something went wrong on my side while searching. Please try again in a minute."
SLOW_TEXT = "Search is slow right now, retrying…"
DELETED_TEXT = "Done. I deleted your requests, results and click history."
NOT_FASHION_TEXT = "I only do clothes for now, so I skipped: {items}."


def escape_text(value: str) -> str:
    """Escape for Telegram HTML body text (quotes need no escaping outside attributes)."""
    return html.escape(value, quote=False)


_text = escape_text


def inr(paise: int) -> str:
    """Indian digit grouping: 125000000 paise -> ₹12,50,000."""
    rupees = paise // 100
    digits = str(rupees)
    if len(digits) <= 3:
        return f"₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups) + "," + tail


def short_title(title: str) -> str:
    title = " ".join(title.split())
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def item_heading(item: ItemSpec, cap_inr: int | None) -> str:
    parts = [p for p in (item.color, item.fit, item.query_terms) if p]
    details = []
    if item.size:
        details.append(f"size {item.size}")
    if cap_inr is not None:
        details.append(f"under {inr(cap_inr * 100)}")
    suffix = f" ({', '.join(details)})" if details else ""
    return " ".join(parts) + suffix


def option_line(option: Option, link: str | None) -> str:
    size = f" · size {_text(option.size_label)}" if option.size_label else ""
    anchor = f'<a href="{html.escape(link, quote=True)}">open</a>' if link else "link unavailable"
    return (
        f"<b>{option.label}</b> {_text(short_title(option.title))}\n"
        f"    {_text(option.seller_name.strip())} · <b>{inr(option.price_paise)}</b>{size} · {anchor}"
    )


def zero_results_text(item: ItemSpec, cap_inr: int | None) -> str:
    what = " ".join(p for p in (item.color, item.query_terms) if p)
    size = f" in {item.size}" if item.size else ""
    under = f" under {inr(cap_inr * 100)}" if cap_inr is not None else ""
    hints = []
    if cap_inr is not None:
        hints.append(f"a higher budget like {inr(int(cap_inr * 1.25) * 100)}")
    if item.color:
        hints.append("another colour")
    if item.size:
        hints.append("a different size")
    hint = f" Try {' or '.join(hints)}?" if hints else ""
    return f"No {_text(what)}{_text(size)}{under} from Indian brands right now.{hint}"


@dataclass(frozen=True)
class RenderedItem:
    photos: tuple[tuple[str, str], ...]  # (image_url, caption)
    text: str


def render_item(result: ItemResult, links: dict[str, str]) -> RenderedItem:
    heading = f"<b>{chr(ord('A') + result.item_index)} · {_text(item_heading(result.item, result.cap_inr))}</b>"
    if not result.options:
        body = _text(result.note) if result.note else zero_results_text(result.item, result.cap_inr)
        return RenderedItem(photos=(), text=f"{heading}\n{body}")
    lines = [option_line(o, links.get(o.label)) for o in result.options]
    photos = tuple((o.image_url, o.label) for o in result.options if o.image_url)
    return RenderedItem(photos=photos, text=heading + "\n\n" + "\n\n".join(lines))


def ack_text(item_count: int) -> str:
    noun = "item" if item_count == 1 else "items"
    return f"Searching {item_count} {noun}…"


def footer_text(total_budget_inr: int | None) -> str:
    lines = ["Tap <b>open</b> to see an item on the brand's site and buy it there."]
    if total_budget_inr is not None:
        lines.append(f"Your total budget is {inr(total_budget_inr * 100)}; pick options that add up to it.")
    return "\n".join(lines)




# --- Rung 2: buttons, cart, checkout, follow-up (D49-D51) -------------------------

BUTTONS_PER_ROW = 5
NL = "\n"


def _rows(buttons: list[dict[str, str]], per_row: int = BUTTONS_PER_ROW) -> list[list[dict[str, str]]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def add_buttons(result_ids: dict[str, int]) -> list[list[dict[str, str]]]:
    """One "+ A1" button per shown option. callback_data carries the results.id."""
    buttons = [{"text": f"+ {label}", "callback_data": f"add:{rid}"} for label, rid in result_ids.items()]
    return _rows(buttons)


def cart_shortcut_buttons() -> list[list[dict[str, str]]]:
    return [[{"text": "🛒 View cart", "callback_data": "cart"}, {"text": "Checkout", "callback_data": "co"}]]


def _cart_line(option: Option, quantity: int, price_paise: int | None = None) -> str:
    size = f" · {_text(option.size_label)}" if option.size_label else ""
    qty = f" ×{quantity}" if quantity > 1 else ""
    price = inr(price_paise if price_paise is not None else option.price_paise)
    return f"{option.label} {_text(short_title(option.title))}{size} · {price}{qty}"


def cart_text(lines: list[tuple[int, Option, int]]) -> str:
    """lines: (line_id, option, quantity), in cart order."""
    if not lines:
        return CART_EMPTY_TEXT
    by_seller: dict[str, list[tuple[Option, int]]] = {}
    for _, option, quantity in lines:
        by_seller.setdefault(option.seller_name.strip() or "?", []).append((option, quantity))
    count = sum(q for _, _, q in lines)
    total = sum(o.price_paise * q for _, o, q in lines)
    brands = len(by_seller)
    head = (
        f"<b>Your cart</b> · {count} item{'s' if count != 1 else ''} "
        f"from {brands} brand{'s' if brands != 1 else ''}"
    )
    parts = [head]
    for seller, entries in by_seller.items():
        body = NL.join("  " + _cart_line(o, q) for o, q in entries)
        parts.append(f"<b>{_text(seller)}</b>{NL}{body}")
    parts.append(f"Subtotal {inr(total)} (+ shipping). Prices are re-checked at checkout.")
    return (NL + NL).join(parts)


def cart_buttons(lines: list[tuple[int, Option, int]]) -> list[list[dict[str, str]]]:
    if not lines:
        return []
    removes = [{"text": f"✕ {o.label}", "callback_data": f"rm:{line_id}"} for line_id, o, _ in lines]
    return _rows(removes) + [
        [{"text": "💳 Checkout", "callback_data": "co"}, {"text": "Clear cart", "callback_data": "clr"}]
    ]


def checkout_text(checkout: "MerchantCheckout") -> str:
    lines = []
    for checked in checkout.lines:
        option, qty = checked.line.option, checked.line.quantity
        if checked.status == "sold_out":
            lines.append(f"  <s>{option.label} {_text(short_title(option.title))}</s> · sold out, removed")
            continue
        line = "  " + _cart_line(option, qty, checked.price_paise)
        if checked.status == "price_changed":
            line += f"{NL}    ⚠ price changed from {inr(checked.old_price_paise)}"
        elif checked.status == "unchecked":
            line += f"{NL}    ⚠ couldn't re-check stock and price; the brand's checkout shows the final price"
        lines.append(line)
    head = f"<b>{_text(checkout.seller_name)}</b>"
    if checkout.permalink is None:
        return head + NL + NL.join(lines) + NL + "Nothing left to buy from this brand."
    policies = policy_links(checkout.lines[0].line.option)
    tail = f"{NL}{policies}" if policies else ""
    return f"{head} · {inr(checkout.total_paise)} (+ shipping){NL}" + NL.join(lines) + tail


def policy_links(option: Option) -> str:
    """The brand's own shipping / returns pages, straight from the catalog API."""
    links = []
    if option.shipping_policy_url:
        links.append(f'<a href="{html.escape(option.shipping_policy_url, quote=True)}">shipping policy</a>')
    if option.refund_policy_url:
        links.append(f'<a href="{html.escape(option.refund_policy_url, quote=True)}">returns</a>')
    return "  " + " · ".join(links) if links else ""


def pay_button(checkout: "MerchantCheckout", url: str) -> list[list[dict[str, str]]]:
    return [[{"text": f"Pay on {checkout.seller_name[:30]} · {inr(checkout.total_paise)}", "url": url}]]


DELIVERY_NOTE = (
    "Enter your address on the brand's checkout: it shows the delivery date and shipping cost for "
    "your pincode before you pay. I never see your address or payment details."
)


def checkout_footer(merchant_count: int) -> str:
    if merchant_count == 1:
        lead = "You pay on the brand's own checkout."
    else:
        lead = f"{merchant_count} brands means {merchant_count} separate payments, one per button."
    return NL.join((lead, DELIVERY_NOTE, "Your cart here stays until you clear it."))


def unlinkable_text(options: list[Option]) -> str:
    names = ", ".join(o.label for o in options)
    return f"No safe checkout link for {names}. Open them from the results instead."


def followup_text(merchants: list[dict]) -> str:
    names = ", ".join(_text(m["name"]) for m in merchants)
    return f"Quick one: did you buy anything from {names} after our chat?"


def followup_buttons(followup_id: int, merchants: list[dict]) -> list[list[dict[str, str]]]:
    rows = [
        [{"text": f"✅ Bought from {m['name'][:28]}", "callback_data": f"fb:{followup_id}:{i}"}]
        for i, m in enumerate(merchants)
    ]
    rows.append([
        {"text": "❌ Didn't buy", "callback_data": f"fb:{followup_id}:no"},
        {"text": "⏳ Not yet", "callback_data": f"fb:{followup_id}:later"},
    ])
    return rows


def blocker_buttons(followup_id: int) -> list[list[dict[str, str]]]:
    buttons = [{"text": text, "callback_data": f"why:{followup_id}:{code}"} for code, text in BLOCKER_REASONS]
    return _rows(buttons, per_row=3)


# --- Telegram UX (D57) -------------------------------------------------------------

BOT_COMMANDS = [
    ("start", "How it works + examples"),
    ("cart", "Show my cart"),
    ("checkout", "Get pay links, one per brand"),
    ("clear", "Empty my cart"),
    ("delete", "Delete everything you stored about me"),
]

# Each example was checked live against the catalog (smoke runs, 2026-11-11).
EXAMPLES = (
    "grey turtleneck M and blue jeans waist 32, men, under 2000 each",
    "white kurti M for women under 1200",
    "mujhe ek kala hoodie chahiye XL mein, 1500 tak",
)
EXAMPLE_LABELS = (
    "Men · Grey turtleneck + blue jeans",
    "Women · White kurti under ₹1,200",
    "Hinglish · “kala hoodie XL, 1500 tak”",
)

WELCOME_TEXT = (
    "<b>DrapeFit</b>\n"
    "Your shopping assistant for Indian D2C fashion.\n\n"
    "Tell me what you need in one message, with the size and budget:\n"
    "<blockquote>Grey turtleneck, size M, and blue jeans, waist 32. Under ₹2,000 each.</blockquote>\n"
    "<b>How it works</b>\n"
    "1. I show 5 in-stock options per item, in your size\n"
    "2. Tap <b>Add to cart</b> on what you like, or ask for <i>cheaper</i> or <i>another colour</i>\n"
    "3. Check out on each brand's own website\n\n"
    "<i>Prices and stock come live from the brands. I never see your address or payment details.</i>\n\n"
    "<b>Try an example</b>"
)


def example_buttons() -> list[list[dict[str, str]]]:
    return [[{"text": label, "callback_data": f"ex:{i}"}] for i, label in enumerate(EXAMPLE_LABELS)]


def example_echo(index: int) -> str:
    """Show the tapped example as if the user had typed it, so the results make sense."""
    return f"<blockquote>{escape_text(EXAMPLES[index])}</blockquote>"


def item_buttons(result_ids: dict[str, int], item_index: int) -> list[list[dict[str, str]]]:
    """+ A1 … + A5, then a one-tap "cheaper" for this item (no LLM, D57)."""
    letter = chr(ord("A") + item_index)
    return add_buttons(result_ids) + [[{"text": f"Show cheaper options for {letter}", "callback_data": f"chp:{item_index}"}]]


def mark_added(buttons: list[list[dict[str, str]]], callback_data: str, label: str) -> list[list[dict[str, str]]]:
    """Same keyboard, with the tapped "+ A1" shown as "✓ A1". Returns a new structure."""
    return [
        [{**b, "text": "✓ In cart"} if b.get("callback_data") == callback_data else dict(b) for b in row]
        for row in buttons
    ]


def progress_text(item_count: int, done: list[str]) -> str:
    base = ack_text(item_count)
    return base if not done else f"{base} {' '.join(f'{d} ✓' for d in done)}"


# --- Photo cards: one message per suggestion (D62) ---------------------------------

CARD_TITLE_MAX_CHARS = 42
_TITLE_TAIL = re.compile(r"\s+[–—|]\s+.*$|\s+-\s+.*$")  # " – Refined Everyday Knit for Work…"
_TITLE_AUDIENCE = re.compile(r"\s+(for|For)\s+(men|women|Men|Women|boys|girls|kids|unisex|Unisex)\b.*$")


_TITLE_LEAD = re.compile(r"^(men|women|mens|womens|men's|women's|men’s|women’s|unisex|boys|girls)\s+", re.IGNORECASE)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def card_title(title: str, seller: str) -> str:
    """A short, readable product name (D63).

    Drops a leading brand, gender word, bracketed notes, marketing tails ("– Refined…")
    and "for Men"; if still long, drops words from the middle so the product type at the
    end survives: "Vintage Blue Tailored Relaxed Fit Stretch Jeans" -> "... Relaxed Fit Jeans".
    """
    original = " ".join(title.split())
    text = original
    brand = seller.strip()
    if brand and text.lower().startswith(brand.lower() + " "):
        text = text[len(brand) + 1:]
    text = _BRACKETS.sub("", text)
    text = _TITLE_TAIL.sub("", text)
    text = _TITLE_AUDIENCE.sub("", text)
    text = _TITLE_LEAD.sub("", text).strip(" ,-") or original
    if len(text) <= CARD_TITLE_MAX_CHARS:
        return text
    words = text.split()
    last = words[-1]
    kept: list[str] = []
    for word in words[:-1]:
        if len(" ".join([*kept, word, last])) > CARD_TITLE_MAX_CHARS:
            break
        kept.append(word)
    return " ".join([*kept, last]) if kept else text[: CARD_TITLE_MAX_CHARS - 1].rstrip() + "…"


def option_caption(option: Option) -> str:
    """Card caption under the product photo (D63). Every value comes from the Option (API data).

        <b>Grey Regular Fit Round Neck Sweater</b>
        Thomas Scott
        <b>₹999</b> · Size M · A5
    """
    size = f" · Size {_text(option.size_label)}" if option.size_label else ""
    return (
        f"<b>{_text(card_title(option.title, option.seller_name))}</b>{NL}"
        f"{_text(option.seller_name.strip())}{NL}"
        f"<b>{inr(option.price_paise)}</b>{size} · {option.label}"
    )


def card_buttons(option: Option, link: str | None, result_id: int | None) -> list[list[dict[str, str]]]:
    row = []
    if result_id is not None:
        row.append({"text": "🛒 Add to cart", "callback_data": f"add:{result_id}"})
    if link:
        row.append({"text": "Open ↗", "url": link})
    return [row] if row else []


def item_header(result: ItemResult) -> str:
    """The line above an item's cards (or the zero-results message)."""
    heading = f"<b>{chr(ord('A') + result.item_index)} · {_text(item_heading(result.item, result.cap_inr))}</b>"
    if not result.options:
        body = _text(result.note) if result.note else zero_results_text(result.item, result.cap_inr)
        return f"{heading}{NL}{body}"
    count = len(result.options)
    return f"{heading}{NL}<i>{count} in-stock option{'s' if count != 1 else ''} in your size</i>"


def cheaper_buttons(item_index: int) -> list[list[dict[str, str]]]:
    letter = chr(ord("A") + item_index)
    return [[{"text": f"Show cheaper options for {letter}", "callback_data": f"chp:{item_index}"}]]
