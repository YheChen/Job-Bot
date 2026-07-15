"""Data-access helpers. Thin async functions over the ORM used by services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from jobbot.db.models import (
    Guild,
    GuildSettings,
    Job,
    JobStatus,
    ScanRun,
    ScanStatus,
    SearchQuery,
)
from jobbot.db.session import dialect_name

# Advisory-lock key namespace for "only one scan at a time".
SCAN_LOCK_KEY = 0x4A4F4253  # "JOBS"


async def try_acquire_scan_lock(session: AsyncSession) -> bool:
    """Cross-process scan mutex.

    On Postgres this is a session-level advisory lock (works across multiple bot
    instances). On SQLite / other single-process deployments there are no
    advisory locks, so we return True and rely on the scheduler's in-process
    asyncio lock to serialize scans.
    """
    if dialect_name() != "postgresql":
        return True
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": SCAN_LOCK_KEY}
    )
    return bool(result.scalar())


async def release_scan_lock(session: AsyncSession) -> None:
    if dialect_name() != "postgresql":
        return
    await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCAN_LOCK_KEY})


# --- Guild settings ------------------------------------------------------- #
async def get_or_create_settings(session: AsyncSession, guild_id: int) -> GuildSettings:
    settings = await session.get(GuildSettings, guild_id)
    if settings is None:
        if await session.get(Guild, guild_id) is None:
            session.add(Guild(id=guild_id))
            await session.flush()
        settings = GuildSettings(guild_id=guild_id)
        session.add(settings)
        await session.flush()
    return settings


async def all_active_guild_settings(session: AsyncSession) -> list[GuildSettings]:
    rows = await session.execute(
        select(GuildSettings).where(GuildSettings.paused.is_(False))
    )
    return list(rows.scalars())


# --- Queries -------------------------------------------------------------- #
async def upsert_query(
    session: AsyncSession,
    *,
    query_hash: str,
    query_text: str,
    platform_slug: str | None,
    group: str | None,
    priority: float,
) -> SearchQuery:
    existing = await session.execute(
        select(SearchQuery).where(SearchQuery.query_hash == query_hash)
    )
    q = existing.scalar_one_or_none()
    if q is None:
        q = SearchQuery(
            query_hash=query_hash,
            query_text=query_text,
            platform_slug=platform_slug,
            group=group,
            priority=priority,
        )
        session.add(q)
        await session.flush()
    return q


async def query_priority_overrides(session: AsyncSession) -> dict[str, float]:
    """Learned priority bonus per query_hash from historic relevant-hit rate."""
    rows = await session.execute(
        select(SearchQuery.query_hash, SearchQuery.relevant_results, SearchQuery.times_run)
    )
    overrides: dict[str, float] = {}
    for qhash, relevant, runs in rows:
        if runs:
            overrides[qhash] = min(1.0, (relevant or 0) / runs)
    return overrides


async def record_query_run(
    session: AsyncSession,
    query: SearchQuery,
    *,
    result_count: int,
    relevant_count: int,
) -> None:
    query.times_run += 1
    query.total_results += result_count
    query.relevant_results += relevant_count
    query.last_run_at = datetime.now(UTC)


# --- Jobs ----------------------------------------------------------------- #
async def get_job_by_dedup_key(session: AsyncSession, key: str) -> Job | None:
    rows = await session.execute(select(Job).where(Job.dedup_key == key))
    return rows.scalar_one_or_none()


async def candidate_jobs_for_dedup(
    session: AsyncSession,
    *,
    dedup_key: str,
    platform_slug: str | None,
    external_job_id: str | None,
    normalized_company: str | None,
    canonical_url: str | None,
    content_hash: str | None,
) -> list[Job]:
    """Fetch a small set of jobs that could plausibly be duplicates."""
    conditions = [Job.dedup_key == dedup_key]
    if platform_slug and external_job_id:
        conditions.append(
            (Job.platform_slug == platform_slug) & (Job.external_job_id == external_job_id)
        )
    if canonical_url:
        conditions.append(Job.canonical_url == canonical_url)
    if content_hash:
        conditions.append(Job.content_hash == content_hash)
    if normalized_company:
        conditions.append(Job.normalized_company == normalized_company)

    from sqlalchemy import or_

    rows = await session.execute(select(Job).where(or_(*conditions)).limit(50))
    return list(rows.scalars())


async def recent_jobs(session: AsyncSession, hours: int = 24, limit: int = 25) -> list[Job]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await session.execute(
        select(Job)
        .where(Job.first_seen_at >= since)
        .order_by(Job.relevance_score.desc(), Job.first_seen_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def jobs_pending_post(
    session: AsyncSession, min_score: float, limit: int = 25
) -> list[Job]:
    rows = await session.execute(
        select(Job)
        .where(
            Job.posted_to_discord.is_(False),
            Job.status == JobStatus.active,
            Job.relevance_score >= min_score,
        )
        .order_by(Job.relevance_score.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def jobs_to_recheck(session: AsyncSession, older_than_hours: float, limit: int = 50) -> list[Job]:
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    rows = await session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.active,
            (Job.last_checked_at.is_(None)) | (Job.last_checked_at < cutoff),
        )
        .order_by(Job.last_checked_at.asc().nullsfirst())
        .limit(limit)
    )
    return list(rows.scalars())


async def mark_job_status(session: AsyncSession, job_id: int, status: JobStatus) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=status, last_checked_at=datetime.now(UTC))
    )


async def is_company_ignored(
    session: AsyncSession, guild_id: int, normalized_company: str
) -> bool:
    from jobbot.db.models import IgnoredCompany

    rows = await session.execute(
        select(func.count())
        .select_from(IgnoredCompany)
        .where(
            IgnoredCompany.guild_id == guild_id,
            IgnoredCompany.normalized_company == normalized_company,
        )
    )
    return (rows.scalar() or 0) > 0


# --- Scan runs ------------------------------------------------------------ #
async def start_scan_run(
    session: AsyncSession, *, guild_id: int | None, triggered_by: str
) -> ScanRun:
    run = ScanRun(guild_id=guild_id, triggered_by=triggered_by, status=ScanStatus.running)
    session.add(run)
    await session.flush()
    return run


async def finish_scan_run(
    session: AsyncSession,
    run: ScanRun,
    *,
    status: ScanStatus,
    error: str | None = None,
) -> None:
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.error = error
