"""Mock provider for local development and tests (no network, no API key)."""

from __future__ import annotations

from jobbot.search.models import SearchResult


class MockProvider:
    """Returns canned results derived from the query.

    Useful to exercise the full pipeline (extraction, dedup, scoring, Discord)
    without burning API quota. Enable with SEARCH_PROVIDERS=mock.
    """

    name = "mock"

    def __init__(self, fixtures: list[SearchResult] | None = None) -> None:
        self._fixtures = fixtures or _DEFAULT_FIXTURES

    async def search(
        self, query: str, *, page: int = 1, results_per_page: int = 10
    ) -> list[SearchResult]:
        # Only surface hits whose platform is referenced by the query's site: filter.
        site = _extract_site(query)
        hits = [r for r in self._fixtures if not site or site in r.url]
        start = (page - 1) * results_per_page
        return hits[start : start + results_per_page]


def _extract_site(query: str) -> str | None:
    for token in query.split():
        if token.startswith("site:"):
            return token[len("site:") :]
    return None


_DEFAULT_FIXTURES = [
    SearchResult(
        url="https://jobs.ashbyhq.com/example-tech/1234-software-engineer-intern?utm_source=google",
        title="Software Engineer Intern, Summer 2027 - Example Technologies",
        snippet="Example Technologies is hiring a Software Engineer Intern for Summer 2027 in Toronto, ON.",
        rank=1,
        provider="mock",
    ),
    SearchResult(
        url="https://boards.greenhouse.io/acme/jobs/56789?gh_src=abcd",
        title="Backend Engineer Intern (Summer 2027) at Acme",
        snippet="Join Acme as a Backend Engineering Intern. Remote (US).",
        rank=2,
        provider="mock",
    ),
    SearchResult(
        url="https://jobs.lever.co/globex/9999?lever-source=LinkedIn",
        title="Senior Software Engineer - Globex",
        snippet="We are looking for a Senior Software Engineer with 8+ years experience.",
        rank=3,
        provider="mock",
    ),
]
