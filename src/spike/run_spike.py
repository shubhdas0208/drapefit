"""Catalog spike: 20 real queries scored against the GO rule (design-doc Next Steps #1).

GO if >= 30% of queries return >= 3 relevant, in-stock, INR, IN-shipping hits.
Relevance here is an automatic title-keyword check; the report lists top titles
so a human can override it.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
from dataclasses import dataclass

from catalog_client import CatalogClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "docs" / "research" / "spike-01-raw"
REPORT = ROOT / "docs" / "research" / "spike-01-catalog-20-queries.md"
LIMIT = 20
MIN_RELEVANT_HITS = 3
GO_SHARE = 0.30

COLOURS = {
    "grey": ["grey", "gray", "charcoal", "heather", "ash", "slate", "melange"],
    "blue": ["blue", "navy", "indigo", "denim", "azure", "cobalt"],
    "navy": ["navy", "dark blue", "midnight"],
    "white": ["white", "off-white", "off white", "ivory", "cream"],
    "black": ["black", "jet", "onyx"],
    "olive": ["olive", "khaki green", "army green", "military"],
    "maroon": ["maroon", "burgundy", "wine", "oxblood"],
    "beige": ["beige", "sand", "khaki", "tan", "stone", "oatmeal", "cream"],
    "pink": ["pink", "blush", "rose", "fuchsia"],
    "mustard": ["mustard", "yellow", "ochre", "turmeric"],
    "green": ["green", "olive", "emerald", "sage", "bottle", "mint"],
    "red": ["red", "crimson", "scarlet", "cherry"],
}


@dataclass(frozen=True)
class Query:
    text: str
    item_words: tuple[str, ...]
    colour: str
    gender: str  # taxonomy value
    max_inr: int


QUERIES = [
    Query("grey turtleneck men", ("turtleneck", "turtle neck", "high neck", "roll neck", "mock neck"), "grey", "Male", 2000),
    Query("blue bell bottom jeans men", ("bell bottom", "bell-bottom", "flare", "bootcut", "boot cut"), "blue", "Male", 2000),
    Query("white linen shirt men", ("shirt",), "white", "Male", 2500),
    Query("black hoodie men", ("hoodie", "hooded"), "black", "Male", 2000),
    Query("navy chinos men", ("chino", "trouser", "pant"), "navy", "Male", 2000),
    Query("olive cargo pants men", ("cargo",), "olive", "Male", 2500),
    Query("maroon kurta men", ("kurta",), "maroon", "Male", 1500),
    Query("beige oversized t-shirt men", ("t-shirt", "tshirt", "tee"), "beige", "Male", 1000),
    Query("black joggers men", ("jogger", "track pant", "trackpant"), "black", "Male", 1500),
    Query("blue denim jacket men", ("jacket", "trucker"), "blue", "Male", 3000),
    Query("blue polo t-shirt men", ("polo",), "blue", "Male", 1200),
    Query("grey sweatshirt men", ("sweatshirt", "crewneck", "pullover"), "grey", "Male", 1500),
    Query("pink co-ord set women", ("co-ord", "coord", "co ord", "set"), "pink", "Female", 3000),
    Query("black bodycon dress women", ("dress",), "black", "Female", 2500),
    Query("white kurti women", ("kurti", "kurta"), "white", "Female", 1500),
    Query("mustard saree women", ("saree", "sari"), "mustard", "Female", 3000),
    Query("blue straight fit jeans women", ("jean", "denim"), "blue", "Female", 2000),
    Query("green anarkali kurta women", ("anarkali", "kurta"), "green", "Female", 3500),
    Query("red crop top women", ("crop", "top"), "red", "Female", 1000),
    Query("black palazzo pants women", ("palazzo",), "black", "Female", 1200),
]


def search_args(q: Query, india_origin: bool) -> dict:
    filters: dict = {
        "ships_to": {"country": "IN"},
        "available": True,
        "price": {"max": q.max_inr * 100},
        "attributes": [{"name": "Target gender", "values": [q.gender]}],
    }
    if india_origin:
        filters["ships_from"] = [{"country": "IN"}]
    return {
        "catalog": {
            "query": q.text,
            "context": {"address_country": "IN", "currency": "INR"},
            "filters": filters,
            "pagination": {"limit": LIMIT},
        }
    }


def is_relevant(title: str, q: Query) -> bool:
    t = html.unescape(title).lower()
    has_item = any(w in t for w in q.item_words)
    has_colour = any(c in t for c in COLOURS[q.colour])
    return has_item and has_colour


def hit_passes_hard_filters(p: dict, q: Query) -> bool:
    for v in p.get("variants", []):
        price = v.get("price", {})
        if (
            v.get("availability", {}).get("available")
            and price.get("currency") == "INR"
            and price.get("amount", 10**12) <= q.max_inr * 100
        ):
            return True
    return False


def summarise(resp: dict, q: Query) -> dict:
    if "error" in resp:
        return {"error": resp["error"], "total": 0, "returned": 0, "good": 0, "hard_ok": 0, "titles": [], "sellers": [], "messages": []}
    sc = resp.get("result", {}).get("structuredContent", {})
    products = sc.get("products", [])
    hard_ok = [p for p in products if hit_passes_hard_filters(p, q)]
    good = [p for p in hard_ok if is_relevant(p.get("title", ""), q)]
    return {
        "total": sc.get("pagination", {}).get("total_count", len(products)),
        "returned": len(products),
        "hard_ok": len(hard_ok),
        "good": len(good),
        "titles": [
            (
                "Y" if p in good else ("~" if p in hard_ok else "x"),
                html.unescape(p.get("title", ""))[:70],
                (p.get("variants") or [{}])[0].get("seller", {}).get("name", "?"),
                (p.get("variants") or [{}])[0].get("price", {}).get("amount", 0) // 100,
            )
            for p in products[:8]
        ],
        "sellers": sorted({(p.get("variants") or [{}])[0].get("seller", {}).get("name", "?") for p in good}),
        "messages": [m.get("content", m) if isinstance(m, dict) else m for m in sc.get("messages", [])],
        "first_id": products[0]["id"] if products else None,
    }


def variant_depth(client: CatalogClient, product_id: str | None) -> str:
    if not product_id:
        return "n/a"
    resp = client.call_tool(
        "get_product",
        {"catalog": {"id": product_id, "context": {"address_country": "IN", "currency": "INR"}}},
    )
    prod = resp.get("result", {}).get("structuredContent", {}).get("product", {})
    variants = prod.get("variants", [])
    sizes = [
        o.get("label")
        for v in variants
        for o in v.get("options", [])
        if o.get("name", "").lower() == "size"
    ]
    in_stock = sum(1 for v in variants if v.get("availability", {}).get("available"))
    return f"{len(variants)} variants, {in_stock} in stock, sizes: {','.join(dict.fromkeys(s for s in sizes if s)) or '-'}"


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = CatalogClient()
    rows = []
    for i, q in enumerate(QUERIES, 1):
        any_resp = client.call_tool("search_catalog", search_args(q, india_origin=False))
        in_resp = client.call_tool("search_catalog", search_args(q, india_origin=True))
        slug = re.sub(r"[^a-z0-9]+", "-", q.text)
        (RAW_DIR / f"{i:02d}-{slug}.json").write_text(
            json.dumps({"anywhere": any_resp, "india": in_resp}, indent=1), encoding="utf-8"
        )
        any_s, in_s = summarise(any_resp, q), summarise(in_resp, q)
        depth = variant_depth(client, in_s.get("first_id") or any_s.get("first_id"))
        rows.append((i, q, any_s, in_s, depth))
        print(f"{i:02d} {q.text:32s} anywhere good={any_s['good']:2d}/{any_s['returned']:2d} (tot {any_s['total']})  "
              f"IN-origin good={in_s['good']:2d}/{in_s['returned']:2d} (tot {in_s['total']})  | {depth}")
    write_report(rows)


def write_report(rows: list) -> None:
    n = len(rows)
    pass_in = sum(1 for r in rows if r[3]["good"] >= MIN_RELEVANT_HITS)
    pass_any = sum(1 for r in rows if r[2]["good"] >= MIN_RELEVANT_HITS)
    lines = [
        "# Spike 01: 20 catalog queries vs the GO rule (scripted, 2026-11-09)",
        "",
        f"Script: `src/spike/run_spike.py`. Raw responses: `docs/research/spike-01-raw/`. Limit {LIMIT} per search.",
        "Every search sent ships_to=IN, available=true, price.max (paise), Target gender, context IN/INR.",
        "\"Good\" = in stock + INR + price <= cap (re-checked in code) + title has item word AND colour word (auto, human to confirm).",
        "",
        f"**Strict (ships_from=IN, Indian merchants): {pass_in}/{n} queries ({pass_in / n:.0%}) have >= {MIN_RELEVANT_HITS} good hits.**",
        f"**Loose (ships to IN from anywhere): {pass_any}/{n} ({pass_any / n:.0%}).** GO threshold: {GO_SHARE:.0%}.",
        "",
        "| # | Query | Cap ₹ | Anywhere good / returned (total) | IN-origin good / returned (total) | get_product depth (1st hit) |",
        "|---|---|---|---|---|---|",
    ]
    for i, q, a, b, depth in rows:
        lines.append(f"| {i} | {q.text} | {q.max_inr} | {a['good']} / {a['returned']} ({a['total']}) | {b['good']} / {b['returned']} ({b['total']}) | {depth} |")
    lines += ["", "## Top hits per query (IN-origin search). Y = auto-relevant, ~ = passes hard filters only, x = fails price/stock/INR", ""]
    for i, q, a, b, _ in rows:
        lines.append(f"### {i}. {q.text}")
        if b.get("error"):
            lines.append(f"- ERROR: {b['error']}")
        if b["messages"]:
            lines.append(f"- API messages: {b['messages']}")
        for mark, title, seller, price in b["titles"]:
            lines.append(f"- [{mark}] {title} | {seller} | ₹{price}")
        if not b["titles"]:
            lines.append("- (no IN-origin results)")
        lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSTRICT pass {pass_in}/{n}, LOOSE pass {pass_any}/{n}. Report: {REPORT}")


if __name__ == "__main__":
    main()
