"""Step 1: auth + tools/list. Saves tool schemas so the search args are exact."""
import json
import pathlib

from catalog_client import CatalogClient

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "research" / "spike-01-tools-list.json"

client = CatalogClient()
tools = client.rpc("tools/list")
OUT.write_text(json.dumps(tools, indent=2), encoding="utf-8")
names = [t.get("name") for t in tools.get("result", {}).get("tools", [])]
print("auth OK, tools:", names)
print("saved", OUT)
