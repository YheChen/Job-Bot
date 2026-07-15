"""SQLAlchemy ORM models.

The schema is designed to *explain* each job: which query found it, when it was
first/last seen, why it scored the way it did, whether it was posted, and how
users reacted. See docs/architecture in README for the ER overview.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jobbot.db.base import Base, TimestampMixin


class JobStatus(enum.StrEnum):
    active = "active"
    expired = "expired"
    closed = "closed"
    unknown = "unknown"


class FeedbackKind(enum.StrEnum):
    relevant = "relevant"
    irrelevant = "irrelevant"
    duplicate = "duplicate"
    saved = "saved"
    hidden_company = "hidden_company"


class ScanStatus(enum.StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


# --------------------------------------------------------------------------- #
# Guild + settings
# --------------------------------------------------------------------------- #
class Guild(Base, TimestampMixin):
    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord guild id
    name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped[GuildSettings] = relationship(
        back_populates="guild", uselist=False, cascade="all, delete-orphan"
    )


class GuildSettings(Base, TimestampMixin):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.id", ondelete="CASCADE"), primary_key=True
    )
    post_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    digest_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    scan_interval_hours: Mapped[float] = mapped_column(Float, default=6.0)
    min_score: Mapped[float] = mapped_column(Float, default=0.55)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    repost_on_material_update: Mapped[bool] = mapped_column(Boolean, default=False)

    # JSON lists so admins can edit via Discord commands without migrations.
    locations: Mapped[list] = mapped_column(JSON, default=list)
    academic_terms: Mapped[list] = mapped_column(JSON, default=list)
    extra_keywords: Mapped[list] = mapped_column(JSON, default=list)
    negative_keywords: Mapped[list] = mapped_column(JSON, default=list)
    company_domains: Mapped[list] = mapped_column(JSON, default=list)
    disabled_platforms: Mapped[list] = mapped_column(JSON, default=list)

    guild: Mapped[Guild] = relationship(back_populates="settings")


# --------------------------------------------------------------------------- #
# Platforms + queries
# --------------------------------------------------------------------------- #
class Platform(Base, TimestampMixin):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)  # e.g. "ashby"
    name: Mapped[str] = mapped_column(String(128))
    domain: Mapped[str] = mapped_column(String(255))  # e.g. jobs.ashbyhq.com
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SearchQuery(Base, TimestampMixin):
    __tablename__ = "search_queries"
    __table_args__ = (UniqueConstraint("query_hash", name="uq_search_query_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    query_text: Mapped[str] = mapped_column(Text)
    platform_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    group: Mapped[str | None] = mapped_column(String(64), index=True)
    priority: Mapped[float] = mapped_column(Float, default=1.0)

    # Bandit-style stats used to prioritize high-yield queries.
    times_run: Mapped[int] = mapped_column(Integer, default=0)
    total_results: Mapped[int] = mapped_column(Integer, default=0)
    relevant_results: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list[QueryRun]] = relationship(back_populates="query")


class QueryRun(Base):
    __tablename__ = "query_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), index=True
    )
    scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    relevant_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    query: Mapped[SearchQuery] = relationship(back_populates="runs")


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    raw_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    canonical_url: Mapped[str] = mapped_column(Text)
    platform_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    external_job_id: Mapped[str | None] = mapped_column(String(255), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Content
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(String(255), index=True)
    normalized_company: Mapped[str | None] = mapped_column(String(255), index=True)
    location: Mapped[str | None] = mapped_column(String(255))
    remote_status: Mapped[str | None] = mapped_column(String(32))
    employment_type: Mapped[str | None] = mapped_column(String(64))
    internship_term: Mapped[str | None] = mapped_column(String(64))
    salary: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    # Dates
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Scoring / status
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.active, index=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_keywords: Mapped[list] = mapped_column(JSON, default=list)

    # Discord
    posted_to_discord: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    posted_at_discord: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger)

    versions: Mapped[list[JobVersion]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    sources: Mapped[list[JobSource]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    categories: Mapped[list[JobCategory]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobVersion(Base):
    """A snapshot created whenever a job's material content changes."""

    __tablename__ = "job_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="versions")


class JobSource(Base):
    """A distinct URL/query through which a job was discovered."""

    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("job_id", "raw_url", name="uq_job_source_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    query_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_queries.id", ondelete="SET NULL")
    )
    raw_url: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(32))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="sources")


class JobCategory(Base):
    __tablename__ = "job_categories"
    __table_args__ = (UniqueConstraint("job_id", "category", name="uq_job_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)

    job: Mapped[Job] = relationship(back_populates="categories")


# --------------------------------------------------------------------------- #
# User-facing tables
# --------------------------------------------------------------------------- #
class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("guild_id", "user_id", "category", name="uq_subscription"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    category: Mapped[str] = mapped_column(String(64))


class SavedJob(Base, TimestampMixin):
    __tablename__ = "saved_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_saved_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))


class IgnoredCompany(Base, TimestampMixin):
    __tablename__ = "ignored_companies"
    __table_args__ = (
        UniqueConstraint("guild_id", "normalized_company", name="uq_ignored_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    normalized_company: Mapped[str] = mapped_column(String(255), index=True)


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[FeedbackKind] = mapped_column(Enum(FeedbackKind, native_enum=False))


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    triggered_by: Mapped[str] = mapped_column(String(64), default="scheduler")
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False), default=ScanStatus.running
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queries_run: Mapped[int] = mapped_column(Integer, default=0)
    results_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_posted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
