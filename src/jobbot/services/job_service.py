"""Job-related mutations/queries for Discord interactions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from jobbot.db import repositories as repo
from jobbot.db.models import (
    Feedback,
    FeedbackKind,
    IgnoredCompany,
    Job,
    JobStatus,
    SavedJob,
)
from jobbot.db.session import session_scope


async def mark_posted(job_ids: list[int], message_ids: dict[int, int] | None = None) -> None:
    message_ids = message_ids or {}
    async with session_scope() as session:
        for jid in job_ids:
            job = await session.get(Job, jid)
            if job is None:
                continue
            job.posted_to_discord = True
            job.posted_at_discord = datetime.now(UTC)
            if jid in message_ids:
                job.discord_message_id = message_ids[jid]


async def get_jobs(job_ids: list[int]) -> list[Job]:
    async with session_scope() as session:
        rows = await session.execute(select(Job).where(Job.id.in_(job_ids)))
        return list(rows.scalars())


async def recent(hours: int = 24, limit: int = 25) -> list[Job]:
    async with session_scope() as session:
        return await repo.recent_jobs(session, hours=hours, limit=limit)


async def add_feedback(
    job_id: int, guild_id: int | None, user_id: int, kind: FeedbackKind
) -> None:
    async with session_scope() as session:
        session.add(
            Feedback(job_id=job_id, guild_id=guild_id, user_id=user_id, kind=kind)
        )
        if kind == FeedbackKind.irrelevant:
            job = await session.get(Job, job_id)
            if job:
                job.relevance_score = max(0.0, job.relevance_score - 0.2)
        elif kind == FeedbackKind.duplicate:
            job = await session.get(Job, job_id)
            if job:
                job.status = JobStatus.closed


async def save_job(user_id: int, job_id: int) -> None:
    async with session_scope() as session:
        exists = await session.execute(
            select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id == job_id)
        )
        if exists.scalar_one_or_none() is None:
            session.add(SavedJob(user_id=user_id, job_id=job_id))


async def saved_jobs(user_id: int, limit: int = 25) -> list[Job]:
    async with session_scope() as session:
        rows = await session.execute(
            select(Job)
            .join(SavedJob, SavedJob.job_id == Job.id)
            .where(SavedJob.user_id == user_id)
            .order_by(SavedJob.created_at.desc())
            .limit(limit)
        )
        return list(rows.scalars())


async def hide_company(guild_id: int, job_id: int) -> str | None:
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None or not job.normalized_company:
            return None
        exists = await session.execute(
            select(IgnoredCompany).where(
                IgnoredCompany.guild_id == guild_id,
                IgnoredCompany.normalized_company == job.normalized_company,
            )
        )
        if exists.scalar_one_or_none() is None:
            session.add(
                IgnoredCompany(
                    guild_id=guild_id, normalized_company=job.normalized_company
                )
            )
        return job.company


async def stats() -> dict:
    async with session_scope() as session:
        total = (await session.execute(select(func.count()).select_from(Job))).scalar()
        active = (
            await session.execute(
                select(func.count()).select_from(Job).where(Job.status == JobStatus.active)
            )
        ).scalar()
        posted = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.posted_to_discord.is_(True))
            )
        ).scalar()
        expired = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.status == JobStatus.expired)
            )
        ).scalar()
        return {
            "total": total or 0,
            "active": active or 0,
            "posted": posted or 0,
            "expired": expired or 0,
        }
