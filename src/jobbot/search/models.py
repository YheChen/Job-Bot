"""Search domain models (provider-agnostic)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """A single normalized search hit, independent of provider."""

    url: str
    title: str | None = None
    snippet: str | None = None
    rank: int | None = None
    provider: str | None = None
    fetched_at: datetime | None = None
    raw: dict = Field(default_factory=dict)


class QuotaExceeded(Exception):
    """Raised by a provider when its budget/quota is exhausted."""


class ProviderError(Exception):
    """Raised on non-recoverable provider failure."""
