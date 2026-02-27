"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """AiHass configuration — all values come from env vars or .env file."""

    # Home Assistant connection
    hass_url: str = "http://homeassistant.local:8123"
    hass_token: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://aihass:aihass@localhost:5432/aihass"

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8100

    # MCP server
    mcp_transport: str = "stdio"  # "stdio" or "sse"
    mcp_port: int = 8101

    # Reconnection
    ha_reconnect_max_retries: int = 0  # 0 = infinite
    ha_reconnect_base_delay: float = 1.0
    ha_reconnect_max_delay: float = 60.0

    model_config = {"env_prefix": "AIHASS_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
