"""Shared extraction models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExtractedJob(BaseModel):
    """Everything we could extract about a single job posting."""

    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str
    canonical_url: str | None = None
    platform_slug: str | None = None
    posting_date: datetime | None = None
    employment_type: str | None = None
    internship_term: str | None = None
    description: str | None = None
    salary: str | None = None
    remote_status: str | None = None
    external_job_id: str | None = None
    expires_at: datetime | None = None
    is_expired: bool = False
    source: str = "html"  # "jsonld" | "html" | "search"
    raw: dict = Field(default_factory=dict)


class PageFetch(BaseModel):
    url: str
    final_url: str
    status_code: int
    html: str
    ok: bool
