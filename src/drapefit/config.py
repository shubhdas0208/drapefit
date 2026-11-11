"""Settings from environment variables. Fails fast at startup if anything is missing.

On Windows dev machines, secrets set with `setx` may not be in the current process
yet, so we fall back to HKCU\\Environment. Secrets are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

CATALOG_MCP_URL = "https://catalog.shopify.com/api/ucp/mcp"
SHOPIFY_AUTH_URL = "https://api.shopify.com/auth/access_token"
SARVAM_URL = "https://api.sarvam.ai/v1/chat/completions"
TELEGRAM_API = "https://api.telegram.org"
DEFAULT_AGENT_PROFILE = (
    "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json"
)

OPTIONS_PER_ITEM = 5
MAX_PER_SELLER = 3
SEARCH_LIMIT = 30
MAX_DETAIL_CALLS_PER_ITEM = 8
CATALOG_MAX_RPS = 5
PRODUCT_CACHE_SECONDS = 600  # Shopify ToS: price/availability cache max 10 min
TOKEN_REFRESH_SECONDS = 50 * 60
HTTP_TIMEOUT_SECONDS = 20
RETENTION_DAYS = 90
FOLLOWUP_DELAY_HOURS = 24  # "did you buy?" ping after checkout links / a click-out (D51)



def _read_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value or None
    except (ImportError, OSError):
        return None


def followup_delay_minutes() -> float:
    """FOLLOWUP_DELAY_MINUTES overrides the 24 h default (e.g. 2 for a local test)."""
    raw = _read_env("FOLLOWUP_DELAY_MINUTES")
    try:
        return float(raw) if raw else FOLLOWUP_DELAY_HOURS * 60
    except ValueError:
        return FOLLOWUP_DELAY_HOURS * 60


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_webhook_secret: str
    tasks_secret: str
    sarvam_api_key: str
    sarvam_model: str
    shopify_client_id: str
    shopify_client_secret: str
    database_url: str
    public_base_url: str
    agent_profile_url: str


REQUIRED = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "TASKS_SECRET",
    "SARVAM_API_KEY",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "DATABASE_URL",
    "PUBLIC_BASE_URL",
)


def load_settings() -> Settings:
    values = {name: _read_env(name) for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"],
        telegram_webhook_secret=values["TELEGRAM_WEBHOOK_SECRET"],
        tasks_secret=values["TASKS_SECRET"],
        sarvam_api_key=values["SARVAM_API_KEY"],
        sarvam_model=_read_env("SARVAM_MODEL") or "sarvam-105b-conversations",
        shopify_client_id=values["SHOPIFY_CLIENT_ID"],
        shopify_client_secret=values["SHOPIFY_CLIENT_SECRET"],
        database_url=values["DATABASE_URL"],
        public_base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
        agent_profile_url=_read_env("AGENT_PROFILE_URL") or DEFAULT_AGENT_PROFILE,
    )
