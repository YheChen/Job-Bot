"""JobExtractor — turns a SearchResult into an ExtractedJob.

Pipeline per result:
  1. canonicalize the URL (strip tracking params)
  2. resolve the platform adapter by host
  3. fetch the page (SSRF-guarded, redirects followed)
  4. adapter parses JSON-LD first, HTML/OG fallback
  5. attach canonical URL of the *final* (post-redirect) location

If fetching is disabled or fails, we degrade gracefully to a search-only
extraction so the pipeline can still dedup and score on title/snippet.
"""

from __future__ import annotations

from jobbot.logging import get_logger
from jobbot.parsing.fetcher import PageFetcher
from jobbot.parsing.models import ExtractedJob, PageFetch
from jobbot.parsing.sanitize import snippet
from jobbot.parsing.url import canonicalize_url
from jobbot.platforms.registry import PlatformRegistry
from jobbot.search.models import SearchResult

log = get_logger(__name__)


class JobExtractor:
    def __init__(
        self,
        registry: PlatformRegistry,
        fetcher: PageFetcher | None = None,
        *,
        fetch_pages: bool = True,
    ) -> None:
        self._registry = registry
        self._fetcher = fetcher
        self._fetch_pages = fetch_pages and fetcher is not None

    async def extract(self, result: SearchResult) -> ExtractedJob | None:
        adapter = self._registry.resolve(result.url)
        if adapter is None:
            log.debug("no_adapter", url=result.url)
            return None

        canonical = canonicalize_url(result.url)

        if not self._fetch_pages:
            return self._from_search(result, canonical, adapter.slug)

        page: PageFetch | None = None
        try:
            page = await self._fetcher.fetch(result.url)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_failed", url=result.url, error=str(exc))

        if page is None:
            return self._from_search(result, canonical, adapter.slug)

        job = adapter.parse(page, result)
        job.canonical_url = canonicalize_url(page.final_url)
        if not job.external_job_id:
            job.external_job_id = adapter.extract_job_id(page.final_url)
        return job

    @staticmethod
    def _from_search(result: SearchResult, canonical: str, slug: str) -> ExtractedJob:
        return ExtractedJob(
            url=result.url,
            canonical_url=canonical,
            platform_slug=slug,
            title=result.title,
            description=snippet(result.snippet, 400),
            source="search",
        )
