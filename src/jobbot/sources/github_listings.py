"""SimplifyJobs internship-listings source.

Reads the structured `listings.json` published by the SimplifyJobs internship
repos (e.g. github.com/SimplifyJobs/Summer2027-Internships). The feed carries
title/company/locations/terms/date_posted/active per posting plus a direct
application URL, which is richer than anything we could scrape back off the
page — so records map straight to `ExtractedJob` with no page fetch.

Cost control: the feed is ~11 MB, so every request is conditional on the
previously seen ETag. An unchanged feed costs a 304 and yields no work.

Trust boundary: the feed is third-party data, not instructions. Only http(s)
URLs are accepted (a `javascript:` or `data:` URL would otherwise reach a
Discord link button), and the deterministic relevance scorer remains the
authority on what is actually a software internship — `category` is only a
cheap pre-filter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from jobbot.logging import get_logger
from jobbot.parsing.models import ExtractedJob
from jobbot.parsing.term import parse_internship_term
from jobbot.parsing.url import canonicalize_url
from jobbot.platforms.registry import PlatformRegistry

log = get_logger(__name__)

DEFAULT_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)

# Feed categories worth ingesting. Hardware/Product/Quant are dropped up front;
# anything that slips through is still gated by the relevance scorer.
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Software",
    "Software Engineering",
    "AI/ML/Data",
    "Data Science, AI & Machine Learning",
)

SOURCE_NAME = "github_listings"


def _location(record: dict) -> str | None:
    locations = record.get("locations")
    if not isinstance(locations, list):
        return None
    clean = [loc for loc in locations if isinstance(loc, str) and loc.strip()]
    if not clean:
        return None
    joined = "; ".join(clean[:3])
    if len(clean) > 3:
        joined += f" (+{len(clean) - 3} more)"
    return joined


def _remote_status(record: dict) -> str | None:
    locations = record.get("locations")
    if isinstance(locations, list) and any(
        isinstance(loc, str) and "remote" in loc.lower() for loc in locations
    ):
        return "Remote"
    return None


def _term(record: dict) -> str | None:
    """First parseable "<Season> <Year>" among the feed's terms.

    Feed records often carry several terms (e.g. Summer/Spring/Fall 2026); we
    keep the first real one and let scoring decide whether it is wanted.
    """
    terms = record.get("terms")
    if isinstance(terms, list):
        for raw in terms:
            term = parse_internship_term(raw if isinstance(raw, str) else None)
            if term:
                return term
    return parse_internship_term(record.get("title"))


def _posted_at(record: dict) -> datetime | None:
    ts = record.get("date_posted") or record.get("date_updated")
    if not isinstance(ts, int | float) or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _http_url(record: dict) -> str | None:
    url = record.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return None
    return url


def listing_to_job(record: dict, registry: PlatformRegistry | None = None) -> ExtractedJob | None:
    """Map one feed record to an ExtractedJob. Returns None if unusable."""
    url = _http_url(record)
    title = record.get("title")
    if not url or not isinstance(title, str) or not title.strip():
        return None

    canonical = canonicalize_url(url)
    # Resolve the ATS when we recognize the host; unknown hosts stay unlabelled
    # rather than being mislabelled, and lose the direct-ATS scoring bonus.
    platform_slug = registry.platform_slug_for(canonical) if registry else None
    company = record.get("company_name")

    return ExtractedJob(
        url=url,
        canonical_url=canonical,
        title=title.strip(),
        company=company.strip() if isinstance(company, str) and company.strip() else None,
        location=_location(record),
        remote_status=_remote_status(record),
        internship_term=_term(record),
        employment_type="INTERN",
        posting_date=_posted_at(record),
        external_job_id=record.get("id") if isinstance(record.get("id"), str) else None,
        platform_slug=platform_slug,
        is_expired=not record.get("active", True),
        source=SOURCE_NAME,
        raw=record,
    )


def is_candidate(
    record: dict,
    *,
    categories: tuple[str, ...] | list[str],
    cutoff: datetime | None,
) -> bool:
    """Cheap pre-filter applied before mapping/scoring."""
    if not record.get("active") or not record.get("is_visible"):
        return False
    if categories and record.get("category") not in categories:
        return False
    if cutoff is not None:
        posted = _posted_at(record)
        if posted is None or posted < cutoff:
            return False
    return True


class GitHubListingsSource:
    """Fetches and filters a SimplifyJobs-style listings.json feed."""

    name = SOURCE_NAME

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        url: str = DEFAULT_LISTINGS_URL,
        categories: tuple[str, ...] | list[str] = DEFAULT_CATEGORIES,
        lookback_days: int = 30,
        registry: PlatformRegistry | None = None,
    ) -> None:
        if urlsplit(url).scheme.lower() != "https":
            raise ValueError("listings URL must be https")
        self._client = client
        self._url = url
        self._categories = tuple(categories)
        self._lookback_days = lookback_days
        self._registry = registry or PlatformRegistry.default()
        self._etag: str | None = None

    async def fetch(self) -> list[ExtractedJob]:
        try:
            headers = {"Accept": "application/json"}
            if self._etag:
                headers["If-None-Match"] = self._etag
            resp = await self._client.get(self._url, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("listings_fetch_failed", source=self.name, error=str(exc))
            return []

        if resp.status_code == 304:
            log.info("listings_unchanged", source=self.name)
            return []
        if resp.status_code >= 400:
            log.warning("listings_http_error", source=self.name, status=resp.status_code)
            return []

        self._etag = resp.headers.get("ETag") or self._etag

        try:
            records = json.loads(resp.content)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("listings_parse_failed", source=self.name, error=str(exc))
            return []
        if not isinstance(records, list):
            log.warning("listings_unexpected_shape", source=self.name)
            return []

        cutoff = None
        if self._lookback_days > 0:
            cutoff = datetime.now(UTC).timestamp() - self._lookback_days * 86400
            cutoff = datetime.fromtimestamp(cutoff, UTC)

        jobs: list[ExtractedJob] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if not is_candidate(record, categories=self._categories, cutoff=cutoff):
                continue
            job = listing_to_job(record, self._registry)
            if job is not None:
                jobs.append(job)

        log.info(
            "listings_fetched",
            source=self.name,
            total=len(records),
            candidates=len(jobs),
            lookback_days=self._lookback_days,
        )
        return jobs
