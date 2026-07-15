from __future__ import annotations

from pathlib import Path

from jobbot.parsing.extractor import JobExtractor
from jobbot.parsing.models import PageFetch
from jobbot.platforms.registry import PlatformRegistry
from jobbot.search.models import SearchResult

FIXTURES = Path(__file__).parent / "fixtures"


class _StubFetcher:
    """Returns fixture HTML keyed by host, mimicking PageFetcher.fetch."""

    _MAP = {
        "jobs.ashbyhq.com": "ashby.html",
        "boards.greenhouse.io": "greenhouse.html",
        "jobs.lever.co": "lever.html",
    }

    async def fetch(self, url: str) -> PageFetch | None:
        for host, name in self._MAP.items():
            if host in url:
                html = (FIXTURES / name).read_text()
                return PageFetch(url=url, final_url=url, status_code=200, html=html, ok=True)
        return None


async def test_extractor_canonicalizes_and_parses():
    registry = PlatformRegistry.default()
    extractor = JobExtractor(registry, _StubFetcher(), fetch_pages=True)
    result = SearchResult(
        url="https://jobs.ashbyhq.com/example-tech/12345678-1234-1234-1234-123456789abc?utm_source=x",
        title="fallback",
    )
    job = await extractor.extract(result)
    assert job is not None
    assert job.company == "Example Technologies"
    assert job.platform_slug == "ashby"
    assert "utm_source" not in (job.canonical_url or "")


async def test_extractor_search_only_mode():
    registry = PlatformRegistry.default()
    extractor = JobExtractor(registry, None, fetch_pages=False)
    result = SearchResult(
        url="https://jobs.lever.co/globex/uuid?lever-source=x",
        title="Senior Software Engineer",
        snippet="8+ years",
    )
    job = await extractor.extract(result)
    assert job.source == "search"
    assert job.canonical_url == "https://jobs.lever.co/globex/uuid"
