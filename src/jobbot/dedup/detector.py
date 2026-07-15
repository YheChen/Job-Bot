"""Duplicate detection.

Layered strategy, cheapest/strongest first:
  1. platform + external job id   (same requisition across company URLs)
  2. canonical URL                (same link, tracking params stripped)
  3. content hash                 (same normalized posting content)
  4. normalized company + title   (retitled/reposted same role)
  5. fuzzy title + description similarity (slightly edited repost)

All functions here are pure; the DB lookup of candidates lives in the service.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from jobbot.parsing.models import ExtractedJob

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|corp|corporation|co|gmbh|technologies|technology|labs|"
    r"software|systems|solutions|group|holdings)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
# Noise tokens stripped from titles before comparison.
_TITLE_NOISE = re.compile(
    r"\b(intern|internship|co-?op|20\d\d|summer|winter|fall|spring|"
    r"remote|hybrid|onsite|full[- ]?time|part[- ]?time)\b",
    re.IGNORECASE,
)


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    text = name.lower()
    text = _COMPANY_SUFFIXES.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    text = title.lower()
    # Drop trailing "at Company" / "- Company" segments.
    text = re.split(r"\s+(?:-|–|—|\||at)\s+", text)[0]
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def title_key(title: str | None) -> str:
    """Aggressively normalized title for equality-style matching."""
    text = _TITLE_NOISE.sub(" ", normalize_title(title))
    return _WS.sub(" ", text).strip()


def content_hash(job: ExtractedJob) -> str:
    basis = "|".join(
        [
            normalize_company(job.company),
            title_key(job.title),
            (job.location or "").lower().strip(),
            (job.description or "").lower().strip()[:2000],
        ]
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def dedup_key(job: ExtractedJob) -> str:
    """Stable identity key for a job."""
    if job.platform_slug and job.external_job_id:
        basis = f"{job.platform_slug}:{job.external_job_id}"
    elif job.canonical_url:
        basis = job.canonical_url
    else:
        basis = f"{normalize_company(job.company)}|{title_key(job.title)}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def _tokens(text: str) -> set[str]:
    return set(t for t in text.split() if t)


def token_set_ratio(a: str, b: str) -> float:
    """Jaccard similarity over token sets (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


@dataclass
class DuplicateMatch:
    is_duplicate: bool
    reason: str | None = None
    existing_key: str | None = None


@dataclass
class ExistingJobLike:
    """Minimal view of a stored job used for comparison."""

    dedup_key: str
    canonical_url: str | None
    platform_slug: str | None
    external_job_id: str | None
    content_hash: str | None
    normalized_company: str | None
    title: str | None
    description: str | None


def find_duplicate(
    job: ExtractedJob,
    candidates: list[ExistingJobLike],
    *,
    title_threshold: float = 0.85,
    desc_threshold: float = 0.9,
) -> DuplicateMatch:
    new_key = dedup_key(job)
    new_chash = content_hash(job)
    new_company = normalize_company(job.company)
    new_title = title_key(job.title)
    new_canon = job.canonical_url

    for c in candidates:
        if c.dedup_key == new_key:
            return DuplicateMatch(True, "dedup_key", c.dedup_key)
        if (
            job.platform_slug
            and job.external_job_id
            and c.platform_slug == job.platform_slug
            and c.external_job_id == job.external_job_id
        ):
            return DuplicateMatch(True, "platform_job_id", c.dedup_key)
        if new_canon and c.canonical_url and new_canon == c.canonical_url:
            return DuplicateMatch(True, "canonical_url", c.dedup_key)
        if c.content_hash and c.content_hash == new_chash:
            return DuplicateMatch(True, "content_hash", c.dedup_key)
        if (
            new_company
            and c.normalized_company == new_company
            and title_key(c.title) == new_title
            and new_title
        ):
            return DuplicateMatch(True, "company_title", c.dedup_key)
        # Fuzzy fallback: same company + very similar title (+ description if present)
        if new_company and c.normalized_company == new_company:
            t_sim = token_set_ratio(new_title, title_key(c.title))
            if t_sim >= title_threshold:
                if job.description and c.description:
                    d_sim = token_set_ratio(
                        job.description.lower(), (c.description or "").lower()
                    )
                    if d_sim >= desc_threshold:
                        return DuplicateMatch(True, "fuzzy_title_desc", c.dedup_key)
                else:
                    return DuplicateMatch(True, "fuzzy_title", c.dedup_key)

    return DuplicateMatch(False)
