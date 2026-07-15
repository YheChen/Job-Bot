"""Serper.dev Google Search API provider."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobbot.search.base import HTTPProviderBase, QuotaTracker
from jobbot.search.models import ProviderError, SearchResult

_ENDPOINT = "https://google.serper.dev/search"


class SerperProvider(HTTPProviderBase):
    name = "serper"

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
        payload = {"q": query, "page": page, "num": results_per_page}
        headers = {"X-API-KEY": self._api_key, "Content-Type": "application/json"}
        resp = await self._client.post(_ENDPOINT, json=payload, headers=headers)
        if resp.status_code == 429:
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ProviderError(f"serper {resp.status_code}: {resp.text[:200]}")
        self._tracker.record()

        data = resp.json()
        results: list[SearchResult] = []
        for i, item in enumerate(data.get("organic", [])):
            results.append(
                SearchResult(
                    url=item.get("link", ""),
                    title=item.get("title"),
                    snippet=item.get("snippet"),
                    rank=item.get("position", i + 1),
                    provider=self.name,
                    raw=item,
                )
            )
        return [r for r in results if r.url]
