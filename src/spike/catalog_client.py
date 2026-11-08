"""Minimal Shopify Global Catalog MCP client for the spike. Stdlib only.

Secrets come from environment variables. If the current process was started
before the user set them, fall back to the Windows user environment (HKCU).
Secrets are never printed or written to disk.
"""
import json
import os
import time
import urllib.error
import urllib.request

AUTH_URL = "https://api.shopify.com/auth/access_token"
MCP_URL = "https://catalog.shopify.com/api/ucp/mcp"
AGENT_PROFILE = (
    "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/"
    "valid-with-capabilities.json"
)
MIN_SECONDS_BETWEEN_CALLS = 0.25  # stay under 5 req/s


def get_secret(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            if value:
                return value
    except (ImportError, OSError):
        pass
    raise RuntimeError(f"Missing secret: {name} (not in env or HKCU\\Environment)")


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="replace")[:500]
            if err.code == 429 and attempt < 2:
                time.sleep(float(err.headers.get("Retry-After", "2")))
                continue
            raise RuntimeError(f"HTTP {err.code} from {url}: {body}") from err
    raise RuntimeError("unreachable")


class CatalogClient:
    def __init__(self) -> None:
        self._token = None
        self._token_at = 0.0
        self._last_call = 0.0
        self._next_id = 1

    def _token_header(self) -> dict:
        if not self._token or time.time() - self._token_at > 50 * 60:
            data = _post(
                AUTH_URL,
                {
                    "client_id": get_secret("SHOPIFY_CLIENT_ID"),
                    "client_secret": get_secret("SHOPIFY_CLIENT_SECRET"),
                    "grant_type": "client_credentials",
                },
                {},
            )
            if "access_token" not in data:
                raise RuntimeError(f"Auth failed, keys in response: {list(data)}")
            self._token = data["access_token"]
            self._token_at = time.time()
        return {"Authorization": f"Bearer {self._token}"}

    def rpc(self, method: str, params: dict | None = None) -> dict:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._next_id += 1
        result = _post(MCP_URL, payload, self._token_header())
        self._last_call = time.time()
        return result

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.rpc(
            "tools/call",
            {
                "name": name,
                "arguments": {"meta": {"ucp-agent": {"profile": AGENT_PROFILE}}, **arguments},
            },
        )
