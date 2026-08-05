from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobbot.parsing.term import parse_internship_term
from jobbot.platforms.registry import PlatformRegistry
from jobbot.scoring.relevance import score_job
from jobbot.sources.github_listings import (
    DEFAULT_CATEGORIES,
    GitHubListingsSource,
    is_candidate,
    listing_to_job,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def records() -> list[dict]:
    return json.loads((FIXTURES / "github_listings.json").read_text())


@pytest.fixture
def registry() -> PlatformRegistry:
    return PlatformRegistry.default()


# --- term parsing --------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Summer 2027", "Summer 2027"),
        ("summer 2027", "Summer 2027"),
        ("Software Engineer Intern, Fall 2026", "Fall 2026"),
        ("Autumn 2027 internship", "Fall 2027"),  # normalized
        ("Winter-2028 co-op", "Winter 2028"),
        ("2027 internship", None),  # year alone is not a term
        ("Software Engineer Intern", None),
        (None, None),
    ],
)
def test_parse_internship_term(text, expected):
    assert parse_internship_term(text) == expected


def test_parse_term_is_not_hardcoded_to_one_year():
    # Regression: the original implementation only recognized 2027.
    assert parse_internship_term("Summer 2026") == "Summer 2026"
    assert parse_internship_term("Spring 2029") == "Spring 2029"


def test_parse_term_prefers_first_non_empty_source():
    assert parse_internship_term(None, "", "Winter 2027 intern") == "Winter 2027"


# --- record filtering ----------------------------------------------------- #
def test_inactive_records_are_filtered(records):
    inactive = [r for r in records if not r["active"]]
    assert inactive, "fixture should contain an inactive record"
    for r in inactive:
        assert not is_candidate(r, categories=DEFAULT_CATEGORIES, cutoff=None)


def test_hardware_category_filtered_out(records):
    hw = [r for r in records if r["category"] == "Hardware"]
    assert hw, "fixture should contain a hardware record"
    for r in hw:
        assert not is_candidate(r, categories=DEFAULT_CATEGORIES, cutoff=None)


def test_software_and_ml_categories_pass(records):
    passing = [r for r in records if is_candidate(r, categories=DEFAULT_CATEGORIES, cutoff=None)]
    assert {r["category"] for r in passing} <= set(DEFAULT_CATEGORIES)
    assert len(passing) == 3  # 2x Software + 1x AI/ML/Data (active+visible)


def test_empty_category_list_disables_category_filter(records):
    software = next(r for r in records if r["category"] == "Hardware")
    assert is_candidate(software, categories=(), cutoff=None)


def test_cutoff_excludes_old_postings(records):
    rec = next(r for r in records if is_candidate(r, categories=DEFAULT_CATEGORIES, cutoff=None))
    future = datetime.now(UTC) + timedelta(days=365)
    assert not is_candidate(rec, categories=DEFAULT_CATEGORIES, cutoff=future)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    assert is_candidate(rec, categories=DEFAULT_CATEGORIES, cutoff=past)


def test_record_without_date_excluded_when_cutoff_set(records):
    rec = dict(records[0])
    rec.pop("date_posted", None)
    rec.pop("date_updated", None)
    cutoff = datetime(2020, 1, 1, tzinfo=UTC)
    assert not is_candidate(rec, categories=DEFAULT_CATEGORIES, cutoff=cutoff)


# --- mapping -------------------------------------------------------------- #
def test_maps_greenhouse_listing(records, registry):
    rec = next(r for r in records if "greenhouse" in r["url"])
    job = listing_to_job(rec, registry)
    assert job is not None
    assert job.company == "Ginkgo Bioworks"
    assert job.title == "Software Intern - Autonomous Lab"
    assert job.location == "Oakland, CA"
    assert job.internship_term == "Winter 2026"
    assert job.platform_slug == "greenhouse"  # resolved from the URL
    assert job.employment_type == "INTERN"
    assert job.source == "github_listings"
    assert job.external_job_id == rec["id"]
    assert not job.is_expired
    assert job.posting_date is not None and job.posting_date.tzinfo is not None


def test_maps_ashby_listing(records, registry):
    rec = next(r for r in records if "ashbyhq" in r["url"])
    job = listing_to_job(rec, registry)
    assert job.platform_slug == "ashby"
    assert job.internship_term == "Summer 2026"


def test_non_ats_url_has_no_platform_slug(records, registry):
    rec = next(r for r in records if "amazon.jobs" in r["url"])
    job = listing_to_job(rec, registry)
    assert job is not None
    assert job.platform_slug is None  # unknown host: unlabelled, not mislabelled
    assert job.canonical_url.startswith("https://amazon.jobs/")


def test_multiple_terms_picks_first_parseable(registry):
    rec = {
        "url": "https://jobs.lever.co/acme/1",
        "title": "SWE Intern",
        "terms": ["N/A", "Summer 2027", "Fall 2027"],
        "active": True,
    }
    assert listing_to_job(rec, registry).internship_term == "Summer 2027"


def test_many_locations_are_truncated(registry):
    rec = {
        "url": "https://jobs.lever.co/acme/1",
        "title": "SWE Intern",
        "locations": ["A", "B", "C", "D", "E"],
        "active": True,
    }
    loc = listing_to_job(rec, registry).location
    assert loc == "A; B; C (+2 more)"


def test_remote_location_sets_remote_status(registry):
    rec = {
        "url": "https://jobs.lever.co/acme/1",
        "title": "SWE Intern",
        "locations": ["Remote, US"],
        "active": True,
    }
    assert listing_to_job(rec, registry).remote_status == "Remote"


def test_inactive_record_maps_to_expired(registry):
    rec = {"url": "https://jobs.lever.co/acme/1", "title": "SWE Intern", "active": False}
    assert listing_to_job(rec, registry).is_expired


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/job",
        "",
        None,
    ],
)
def test_non_http_urls_are_rejected(url, registry):
    rec = {"url": url, "title": "SWE Intern", "active": True}
    assert listing_to_job(rec, registry) is None


def test_missing_title_rejected(registry):
    rec = {"url": "https://jobs.lever.co/acme/1", "title": "", "active": True}
    assert listing_to_job(rec, registry) is None


def test_mapped_job_survives_relevance_scoring(records, registry):
    """End-to-end sanity: a real software listing should pass the gate."""
    rec = next(r for r in records if "greenhouse" in r["url"])
    job = listing_to_job(rec, registry)
    result = score_job(job, min_score=0.0)
    assert result.is_internship
    assert result.is_software
    assert result.is_relevant


# --- HTTP behaviour ------------------------------------------------------- #
async def test_fetch_filters_and_maps(records):
    payload = json.dumps(records).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"ETag": 'W/"abc"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = GitHubListingsSource(client, lookback_days=0)
        jobs = await source.fetch()

    assert len(jobs) == 3  # inactive + hardware dropped
    assert all(j.source == "github_listings" for j in jobs)


async def test_fetch_sends_etag_and_handles_304(records):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        if request.headers.get("if-none-match") == 'W/"abc"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=json.dumps(records).encode(), headers={"ETag": 'W/"abc"'}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = GitHubListingsSource(client, lookback_days=0)
        first = await source.fetch()
        second = await source.fetch()

    assert len(first) == 3
    assert second == []  # 304 → no work, no re-download
    assert "if-none-match" not in calls[0]
    assert calls[1]["if-none-match"] == 'W/"abc"'


async def test_fetch_returns_empty_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GitHubListingsSource(client).fetch() == []


async def test_fetch_returns_empty_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GitHubListingsSource(client).fetch() == []


async def test_fetch_returns_empty_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GitHubListingsSource(client).fetch() == []


async def test_fetch_tolerates_non_dict_entries():
    payload = json.dumps(["nope", 42, None]).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GitHubListingsSource(client).fetch() == []


def test_non_https_feed_url_rejected():
    with pytest.raises(ValueError, match="https"):
        GitHubListingsSource(httpx.AsyncClient(), url="http://example.com/listings.json")
