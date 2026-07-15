"""Brave Search API provider."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobbot.search.base import HTTPProviderBase, QuotaTracker
from jobbot.search.models import ProviderError, SearchResult

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(HTTPProviderBase):
    name = "brave"

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, tracker: QuotaTracker | None = None
    ) -> None:
        super().__init__(client, tracker)
        self._api_key = api_key

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def search(
        self, query: str, *, page: int = 1, results_per_page: int = 10
    ) -> list[SearchResult]:
        self._guard()
        params = {"q": query, "count": results_per_page, "offset": page - 1}
        headers = {"X-Subscription-Token": self._api_key, "Accept": "application/json"}
        resp = await self._client.get(_ENDPOINT, params=params, headers=headers)
        if resp.status_code == 429:
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ProviderError(f"brave {resp.status_code}: {resp.text[:200]}")
        self._tracker.record()

        data = resp.json()
        items = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                url=item.get("url", ""),
                title=item.get("title"),
                snippet=item.get("description"),
                rank=i + 1,
                provider=self.name,
                raw=item,
            )
            for i, item in enumerate(items)
            if item.get("url")
        ]
