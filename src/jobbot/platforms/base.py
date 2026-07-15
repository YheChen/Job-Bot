"""Platform adapter base class.

An adapter knows how to recognize a platform's URLs, pull the external job id,
parse a fetched page into an ExtractedJob, and recognize platform-specific
"expired" signals. The default implementation relies on JSON-LD + Open Graph +
<title>, which already covers most ATSes; subclasses override only what differs.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from jobbot.parsing.jsonld import extract_jobposting
from jobbot.parsing.models import ExtractedJob, PageFetch
from jobbot.parsing.sanitize import snippet
from jobbot.search.models import SearchResult

# Phrases that indicate a posting is no longer open, checked case-insensitively.
DEFAULT_EXPIRED_PHRASES: tuple[str, ...] = (
    "job no longer available",
    "position filled",
    "position has been filled",
    "applications closed",
    "applications are closed",
    "job has expired",
    "this job is no longer accepting applications",
    "no longer accepting applications",
    "posting is closed",
    "this position is closed",
    "job not found",
)


class PlatformAdapter:
    slug: str = "generic"
    name: str = "Generic"
    domains: tuple[str, ...] = ()
    expired_phrases: tuple[str, ...] = DEFAULT_EXPIRED_PHRASES

    def matches(self, host: str) -> bool:
        host = host.lower()
        return any(host == d or host.endswith("." + d) or host.endswith(d) for d in self.domains)

    # --- id extraction -------------------------------------------------- #
    def extract_job_id(self, url: str) -> str | None:
        """Best-effort external id from the URL; subclasses refine this."""
        m = re.search(r"/(\d{4,})(?:[/?#]|$)", url)
        return m.group(1) if m else None

    # --- parsing -------------------------------------------------------- #
    def parse(self, page: PageFetch, search_result: SearchResult | None = None) -> ExtractedJob:
        tree = HTMLParser(page.html)

        job = extract_jobposting(page.html)
        if job is None:
            job = ExtractedJob(url=page.final_url, source="html")

        job.url = page.final_url
        job.platform_slug = self.slug

        if not job.title:
            job.title = self._og(tree, "og:title") or self._title_tag(tree)
        if not job.company:
            job.company = self._og(tree, "og:site_name") or self._company_from_url(page.final_url)
        if not job.description:
            desc = self._og(tree, "og:description")
            if desc:
                job.description = snippet(desc, 600)
        if not job.external_job_id:
            job.external_job_id = self.extract_job_id(page.final_url)

        # Fall back to search-result data when the page yielded nothing.
        if search_result is not None:
            if not job.title:
                job.title = search_result.title
            if not job.description:
                job.description = snippet(search_result.snippet, 400)

        job.is_expired = job.is_expired or self.detect_expired(page)
        return job

    def detect_expired(self, page: PageFetch) -> bool:
        if page.status_code in (404, 410):
            return True
        lowered = page.html.lower()
        return any(phrase in lowered for phrase in self.expired_phrases)

    # --- helpers -------------------------------------------------------- #
    @staticmethod
    def _og(tree: HTMLParser, prop: str) -> str | None:
        node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(f'meta[name="{prop}"]')
        if node:
            content = node.attributes.get("content")
            if content:
                return content.strip()
        return None

    @staticmethod
    def _title_tag(tree: HTMLParser) -> str | None:
        node = tree.css_first("title")
        return node.text(strip=True) if node else None

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        return None
