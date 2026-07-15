"""Google Programmable Search Engine (Custom Search JSON API) provider."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobbot.search.base import HTTPProviderBase, QuotaTracker
from jobbot.search.models import ProviderError, SearchResult

_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


class GooglePSEProvider(HTTPProviderBase):
    name = "google_pse"

    def __init__(
        self,
        api_key: str,
        cx: str,
        client: httpx.AsyncClient,
        tracker: QuotaTracker | None = None,
    ) -> None:
        super().__init__(client, tracker)
        self._api_key = api_key
        self._cx = cx

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
        start = (page - 1) * results_per_page + 1
        params = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "num": min(results_per_page, 10),
            "start": start,
        }
        resp = await self._client.get(_ENDPOINT, params=params)
        if resp.status_code == 429:
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ProviderError(f"google_pse {resp.status_code}: {resp.text[:200]}")
        self._tracker.record()

        data = resp.json()
        items = data.get("items", [])
        return [
            SearchResult(
                url=item.get("link", ""),
                title=item.get("title"),
                snippet=item.get("snippet"),
                rank=start + i,
                provider=self.name,
                raw=item,
            )
            for i, item in enumerate(items)
            if item.get("link")
        ]
