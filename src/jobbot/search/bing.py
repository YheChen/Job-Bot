"""Bing Web Search API provider."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobbot.search.base import HTTPProviderBase, QuotaTracker
from jobbot.search.models import ProviderError, SearchResult

_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"


class BingProvider(HTTPProviderBase):
    name = "bing"

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
        offset = (page - 1) * results_per_page
        params = {"q": query, "count": results_per_page, "offset": offset, "responseFilter": "Webpages"}
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        resp = await self._client.get(_ENDPOINT, params=params, headers=headers)
        if resp.status_code == 429:
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ProviderError(f"bing {resp.status_code}: {resp.text[:200]}")
        self._tracker.record()

        data = resp.json()
        pages = data.get("webPages", {}).get("value", [])
        return [
            SearchResult(
                url=item.get("url", ""),
                title=item.get("name"),
                snippet=item.get("snippet"),
                rank=offset + i + 1,
                provider=self.name,
                raw=item,
            )
            for i, item in enumerate(pages)
            if item.get("url")
        ]
