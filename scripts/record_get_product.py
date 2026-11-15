"""Record real get_product payloads as test fixtures (run once; needs Shopify creds in env)."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "spike"))
from catalog_client import CatalogClient  # noqa: E402

RAW = ROOT / "tests" / "fixtures" / "search"
OUT = ROOT / "tests" / "fixtures"
CTX = {"address_country": "IN", "currency": "INR"}

client = CatalogClient()
for name in ["01-grey-turtleneck-men", "05-navy-chinos-men"]:
    data = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    product = data["products"][0]
    plain = client.call_tool("get_product", {"catalog": {"id": product["id"], "context": CTX}})
    (OUT / f"get_product-{name}.json").write_text(json.dumps(plain, indent=1, ensure_ascii=False), encoding="utf-8")
    sc = plain.get("result", {}).get("structuredContent", {}).get("product", {})
    sizes = next((o for o in sc.get("options", []) if o.get("name", "").lower() == "size"), {})
    print(name, "variants:", len(sc.get("variants", [])), "size values:", json.dumps(sizes.get("values", []))[:300])
    labels = [v.get("label") for v in sizes.get("values", [])]
    if len(labels) > 1:
        sel = client.call_tool(
            "get_product",
            {"catalog": {"id": product["id"], "context": CTX, "selected": [{"name": sizes["name"], "label": labels[1]}]}},
        )
        (OUT / f"get_product-{name}-selected.json").write_text(json.dumps(sel, indent=1, ensure_ascii=False), encoding="utf-8")
        sv = sel.get("result", {}).get("structuredContent", {}).get("product", {}).get("variants", [])
        print("  selected", labels[1], "->", [(o.get("label")) for v in sv for o in v.get("options", [])], [v.get("availability") for v in sv])
