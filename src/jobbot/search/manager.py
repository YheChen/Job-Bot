"""SearchManager — orchestrates providers with quota-aware fallback."""

from __future__ import annotations

import httpx

from jobbot.config import Settings
from jobbot.logging import get_logger
from jobbot.search.base import QuotaTracker, SearchProvider
from jobbot.search.bing import BingProvider
from jobbot.search.brave import BraveProvider
from jobbot.search.google_pse import GooglePSEProvider
from jobbot.search.mock import MockProvider
from jobbot.search.models import ProviderError, QuotaExceeded, SearchResult
from jobbot.search.serper import SerperProvider

log = get_logger(__name__)


class SearchManager:
    """Runs a query against the first available provider, falling back on quota."""

    def __init__(self, providers: list[SearchProvider], results_per_query: int = 10) -> None:
        if not providers:
            raise ValueError("SearchManager requires at least one provider")
        self._providers = providers
        self._results_per_query = results_per_query

    @classmethod
    def from_settings(cls, settings: Settings, client: httpx.AsyncClient) -> SearchManager:
        tracker = QuotaTracker(settings.hourly_search_budget, settings.daily_search_budget)
        providers: list[SearchProvider] = []
        for name in settings.search_providers:
            if name == "serper":
                providers.append(SerperProvider(settings.serper_api_key, client, tracker))
            elif name == "bing":
                providers.append(BingProvider(settings.bing_api_key, client, tracker))
            elif name == "brave":
                providers.append(BraveProvider(settings.brave_api_key, client, tracker))
            elif name == "google_pse":
                providers.append(
                    GooglePSEProvider(
                        settings.google_pse_api_key, settings.google_pse_cx, client, tracker
                    )
                )
            elif name == "mock":
                providers.append(MockProvider())
        return cls(providers, settings.results_per_query)

    @property
    def active_provider_name(self) -> str:
        return self._providers[0].name

    async def search(self, query: str, *, page: int = 1) -> tuple[list[SearchResult], str]:
        """Return (results, provider_name). Tries each provider until one succeeds."""
        last_err: Exception | None = None
        for provider in self._providers:
            try:
                results = await provider.search(
                    query, page=page, results_per_page=self._results_per_query
                )
                return results, provider.name
            except QuotaExceeded as exc:
                log.warning("provider_quota_exceeded", provider=provider.name, error=str(exc))
                last_err = exc
                continue
            except ProviderError as exc:
                log.error("provider_error", provider=provider.name, error=str(exc))
                last_err = exc
                continue
            except Exception as exc:  # noqa: BLE001 - fall back on any provider failure
                log.error("provider_unexpected", provider=provider.name, error=str(exc))
                last_err = exc
                continue
        raise ProviderError(f"all providers failed: {last_err}")
