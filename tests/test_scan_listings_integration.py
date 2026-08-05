"""End-to-end: listings feed → ingest → dedup → score → post callback.

Runs the real ScanService against an in-memory SQLite database and a mocked
HTTP transport, with search disabled (max_queries_per_scan=0) so only the
listing-source path contributes.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from jobbot.config import Settings
from jobbot.db.base import Base
from jobbot.db.models import Job, JobSource
from jobbot.db.session import dispose_engine, get_sessionmaker, init_engine
from jobbot.services.scan_service import ScanService

FIXTURES = Path(__file__).parent / "fixtures"
LISTINGS = json.loads((FIXTURES / "github_listings.json").read_text())

_JOB_PAGE = b"<html><head><title>Job</title></head><body><a href='/apply'>Apply</a></body></html>"


def _handler(request: httpx.Request) -> httpx.Response:
    if "listings.json" in str(request.url):
        return httpx.Response(200, content=json.dumps(LISTINGS).encode())
    # Any job page fetched during the pre-post expiration check.
    return httpx.Response(200, content=_JOB_PAGE, headers={"content-type": "text/html"})


@pytest.fixture
async def db():
    engine = init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        discord_token="x" * 20,
        database_url="sqlite+aiosqlite:///:memory:",
        search_providers=["mock"],
        max_queries_per_scan=0,  # isolate the listing path
        enable_github_listings=True,
        github_listings_lookback_days=0,  # fixture dates are fixed
        allow_private_networks=True,  # skip DNS in the SSRF guard under test
    )


async def test_listings_flow_into_db_and_get_posted(db, settings):
    posted: list[int] = []

    async def poster(guild_id: int, job_ids: list[int]) -> dict[int, int]:
        posted.extend(job_ids)
        return {jid: 1000 + jid for jid in job_ids}

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        service = ScanService(settings, client, poster=poster)
        report = await service.run_scan(guild_id=1, triggered_by="test")

    assert report.started
    assert report.queries_run == 0  # search disabled
    assert report.listing_candidates == 3  # inactive + hardware filtered upstream
    assert report.jobs_new >= 1

    maker = get_sessionmaker()
    async with maker() as session:
        jobs = list((await session.execute(select(Job))).scalars())

    titles = {j.title for j in jobs}
    assert "Software Intern - Autonomous Lab" in titles
    assert "Full-Stack Engineer Intern" in titles
    # Hardware/inactive records never reach the DB.
    assert not any("Firmware" in t for t in titles)

    ginkgo = next(j for j in jobs if j.company == "Ginkgo Bioworks")
    assert ginkgo.platform_slug == "greenhouse"
    assert ginkgo.internship_term == "Winter 2026"  # non-2027 term preserved
    assert ginkgo.relevance_score > 0
    assert ginkgo.location == "Oakland, CA"

    # Provenance is recorded against the source, not a search query.
    async with maker() as session:
        sources = list((await session.execute(select(JobSource))).scalars())
    assert sources and all(s.provider == "github_listings" for s in sources)
    assert all(s.query_id is None for s in sources)

    assert posted, "high-scoring jobs should reach the poster"
    assert len(posted) == len([j for j in jobs if j.posted_to_discord])


async def test_undelivered_jobs_are_retried_next_scan(db, settings):
    """A poster that fails to deliver must not leave jobs marked as posted."""
    attempts: list[list[int]] = []
    fail_first = True

    async def poster(guild_id: int, job_ids: list[int]) -> dict[int, int]:
        nonlocal fail_first
        attempts.append(list(job_ids))
        if fail_first:
            fail_first = False
            return {}  # e.g. channel unset or Discord rejected every send
        return {jid: 1000 + jid for jid in job_ids}

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        service = ScanService(settings, client, poster=poster)
        first = await service.run_scan(guild_id=1, triggered_by="test")
        second = await service.run_scan(guild_id=1, triggered_by="test")

    assert first.jobs_posted == 0, "nothing delivered → nothing counted as posted"
    assert len(attempts) == 2
    assert attempts[0] == attempts[1], "the same jobs are retried"
    assert second.jobs_posted == len(attempts[1])

    maker = get_sessionmaker()
    async with maker() as session:
        jobs = list((await session.execute(select(Job))).scalars())
    assert all(j.posted_to_discord for j in jobs if j.id in attempts[1])
    assert all(j.discord_message_id == 1000 + j.id for j in jobs if j.id in attempts[1])


async def test_second_scan_does_not_duplicate_or_repost(db, settings):
    posted: list[int] = []

    async def poster(guild_id: int, job_ids: list[int]) -> dict[int, int]:
        posted.extend(job_ids)
        return {jid: 1000 + jid for jid in job_ids}

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        service = ScanService(settings, client, poster=poster)
        first = await service.run_scan(guild_id=1, triggered_by="test")
        posted_after_first = list(posted)
        second = await service.run_scan(guild_id=1, triggered_by="test")

    maker = get_sessionmaker()
    async with maker() as session:
        count = (await session.execute(select(func.count()).select_from(Job))).scalar()

    assert first.jobs_new >= 1
    assert second.jobs_new == 0, "re-seeing the same feed must not create jobs"
    assert count == first.jobs_new, "no duplicate rows"
    assert posted == posted_after_first, "already-posted jobs must not repost"
