"""Application configuration.

All configuration is validated at startup via Pydantic. Missing/invalid values
fail fast with a clear error rather than surfacing as runtime exceptions later.

Guild-level settings (channels, intervals, locations, terms, thresholds) live in
the database and are editable through Discord commands. This module holds only
*process-level* configuration that must exist before the bot connects.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SearchProviderName = Literal["serper", "bing", "brave", "google_pse", "mock"]


class Settings(BaseSettings):
    """Process-level configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # --- Core ---
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False

    # --- Discord ---
    discord_token: str = Field(..., min_length=10)
    discord_guild_ids: list[int] = Field(default_factory=list)
    discord_manager_role_ids: list[int] = Field(default_factory=list)

    # --- Database ---
    # Defaults to a local SQLite file so the bot runs with no external services.
    # For production/multi-process, use Postgres:
    #   postgresql+asyncpg://user:pass@host:5432/jobbot
    database_url: str = "sqlite+aiosqlite:///jobbot.db"

    # --- Search providers ---
    # Ordered list; the first with remaining quota is used, others are fallbacks.
    search_providers: list[SearchProviderName] = Field(default=["serper"])
    serper_api_key: str | None = None
    bing_api_key: str | None = None
    brave_api_key: str | None = None
    google_pse_api_key: str | None = None
    google_pse_cx: str | None = None

    # --- Quotas / budgets ---
    daily_search_budget: int = 1000
    hourly_search_budget: int = 100
    results_per_query: int = 10
    max_queries_per_scan: int = 40

    # --- Scheduling ---
    scan_interval_hours: float = 6.0
    expiration_recheck_hours: float = 24.0
    scan_enabled: bool = True

    # --- HTTP / safety ---
    http_timeout_seconds: float = 15.0
    http_user_agent: str = "jobbot/0.1 (+https://github.com/example/jobbot)"
    allow_private_networks: bool = False  # SSRF guard; keep False in production

    # --- Relevance ---
    default_min_score: float = 0.55
    enable_llm_classification: bool = False
    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-4-5-20251001"

    # --- Health check ---
    health_host: str = "0.0.0.0"
    health_port: int = 8080

    @field_validator("discord_guild_ids", "discord_manager_role_ids", mode="before")
    @classmethod
    def _split_ints(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(",", " ").split() if x.strip()]
        return v

    @field_validator("search_providers", mode="before")
    @classmethod
    def _split_providers(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.replace(",", " ").split() if x.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _check_db_url(cls, v: str) -> str:
        if not (v.startswith("sqlite") or v.startswith("postgresql")):
            raise ValueError(
                "database_url must start with 'sqlite' or 'postgresql' "
                "(e.g. sqlite+aiosqlite:///jobbot.db or postgresql+asyncpg://...)"
            )
        if v.startswith("postgresql://"):
            # Ensure the async driver is used by the app engine.
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_sqlite(self) -> bool:
        return str(self.database_url).startswith("sqlite")

    @model_validator(mode="after")
    def _check_provider_keys(self) -> Settings:
        key_map = {
            "serper": self.serper_api_key,
            "bing": self.bing_api_key,
            "brave": self.brave_api_key,
            "google_pse": self.google_pse_api_key and self.google_pse_cx,
            "mock": True,
        }
        for provider in self.search_providers:
            if not key_map.get(provider):
                raise ValueError(
                    f"search provider '{provider}' is enabled but its API key(s) are not set"
                )
        if self.enable_llm_classification and not self.anthropic_api_key:
            raise ValueError("enable_llm_classification=True requires anthropic_api_key")
        return self

    @property
    def sync_database_url(self) -> str:
        """Sync-driver URL for Alembic migrations."""
        url = str(self.database_url)
        if url.startswith("sqlite"):
            return url.replace("+aiosqlite", "")
        return url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
