"""Async page fetcher with redirect following + SSRF validation."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jobbot.logging import get_logger
from jobbot.parsing.models import PageFetch
from jobbot.parsing.ssrf import UnsafeURLError, validate_url

log = get_logger(__name__)


class PageFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        allow_private_networks: bool = False,
        company_domains: set[str] | None = None,
    ) -> None:
        self._client = client
        self._allow_private = allow_private_networks
        self._company_domains = frozenset(company_domains or set())

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        reraise=True,
    )
    async def fetch(self, url: str) -> PageFetch | None:
        """Fetch a URL after SSRF validation. Returns None if the URL is unsafe."""
        try:
            validate_url(
                url,
                allow_private_networks=self._allow_private,
                extra_domains=self._company_domains,
            )
        except UnsafeURLError as exc:
            log.warning("ssrf_blocked", url=url, error=str(exc))
            return None

        resp = await self._client.get(url, follow_redirects=True)
        final_url = str(resp.url)

        # Re-validate the final URL — a redirect could point somewhere unsafe.
        try:
            validate_url(
                final_url,
                allow_private_networks=self._allow_private,
                extra_domains=self._company_domains,
            )
        except UnsafeURLError as exc:
            log.warning("ssrf_blocked_redirect", url=final_url, error=str(exc))
            return None

        return PageFetch(
            url=url,
            final_url=final_url,
            status_code=resp.status_code,
            html=resp.text if "text/html" in resp.headers.get("content-type", "") else resp.text,
            ok=resp.status_code < 400,
        )
