"""Deterministic relevance scoring.

Produces a 0..1 score plus a breakdown that is stored on the job so we can always
explain *why* a posting was (or wasn't) surfaced. The optional LLM step lives in
`scoring/llm.py` and only refines an already-passing deterministic result.

Gate: a result must contain BOTH an internship indicator AND a software
indicator, and must not trip a strong negative, or it is rejected outright
(score forced below any usable threshold).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from jobbot.parsing.models import ExtractedJob
from jobbot.scoring.keywords import (
    CATEGORY_KEYWORDS,
    INTERNSHIP_INDICATORS,
    NEGATIVE_KEYWORDS,
    NON_SOFTWARE_DISCIPLINES,
    SOFTWARE_INDICATORS,
    contains_any,
)

# Weights sum defines the normalization denominator for positive signals.
_W = {
    "internship": 0.25,
    "software": 0.25,
    "title": 0.15,
    "term": 0.10,
    "location": 0.08,
    "ats": 0.07,
    "freshness": 0.10,
}
_MAX_POSITIVE = sum(_W.values())

_NEG_PENALTY = 0.6
_SEEN_PENALTY = 0.15


class RelevanceResult(BaseModel):
    score: float
    is_relevant: bool
    is_internship: bool
    is_software: bool
    matched_keywords: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


def _detect_categories(text: str) -> list[str]:
    cats: list[str] = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if contains_any(text, kws):
            cats.append(cat)
    return cats or ["software"]


def _freshness_score(posted_at: datetime | None, now: datetime) -> float:
    if posted_at is None:
        return 0.5  # unknown → neutral
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    age_days = (now - posted_at).total_seconds() / 86400
    if age_days <= 3:
        return 1.0
    if age_days <= 7:
        return 0.8
    if age_days <= 14:
        return 0.6
    if age_days <= 30:
        return 0.3
    return 0.1


def score_job(
    job: ExtractedJob,
    *,
    min_score: float = 0.55,
    preferred_locations: list[str] | None = None,
    preferred_terms: list[str] | None = None,
    extra_negative_keywords: list[str] | None = None,
    already_seen: bool = False,
    now: datetime | None = None,
) -> RelevanceResult:
    now = now or datetime.now(UTC)
    preferred_locations = [loc.lower() for loc in (preferred_locations or [])]
    preferred_terms = [t.lower() for t in (preferred_terms or [])]
    negatives_extra = [k.lower() for k in (extra_negative_keywords or [])]

    haystack = " ".join(
        filter(
            None,
            [job.title, job.company, job.location, job.internship_term, job.description],
        )
    )

    matched: list[str] = []
    breakdown: dict[str, float] = {}
    reasons: list[str] = []

    # --- gates ------------------------------------------------------------
    intern_hits = contains_any(haystack, INTERNSHIP_INDICATORS)
    sw_hits = contains_any(haystack, SOFTWARE_INDICATORS)
    is_internship = bool(intern_hits)
    is_software = bool(sw_hits)

    # Non-software discipline without a software indicator → not software.
    non_sw = contains_any(haystack, NON_SOFTWARE_DISCIPLINES)
    if non_sw and not sw_hits:
        is_software = False

    negatives = contains_any(haystack, NEGATIVE_KEYWORDS) + contains_any(haystack, negatives_extra)

    if job.is_expired:
        reasons.append("expired")
        return RelevanceResult(
            score=0.0,
            is_relevant=False,
            is_internship=is_internship,
            is_software=is_software,
            negatives=negatives,
            reasons=reasons,
        )

    # --- positive signals -------------------------------------------------
    score = 0.0
    if is_internship:
        score += _W["internship"]
        matched += intern_hits
        breakdown["internship"] = _W["internship"]
    else:
        reasons.append("no internship indicator")

    if is_software:
        score += _W["software"]
        matched += sw_hits
        breakdown["software"] = _W["software"]
    else:
        reasons.append("no software indicator")

    # Title match — a software title in the *title* field specifically.
    if (
        job.title
        and contains_any(job.title, SOFTWARE_INDICATORS)
        and contains_any(job.title, INTERNSHIP_INDICATORS)
    ):
        score += _W["title"]
        breakdown["title"] = _W["title"]
        reasons.append("software internship in title")

    # Academic term match
    term_source = " ".join(filter(None, [job.internship_term, job.title, job.description]))
    matched_terms = [t for t in preferred_terms if t in term_source.lower()]
    if matched_terms:
        score += _W["term"]
        breakdown["term"] = _W["term"]
        matched += matched_terms

    # Location match
    loc_source = " ".join(filter(None, [job.location, job.title, job.description])).lower()
    matched_locs = [loc for loc in preferred_locations if loc in loc_source]
    if matched_locs:
        score += _W["location"]
        breakdown["location"] = _W["location"]
        matched += matched_locs

    # Direct ATS link (not an aggregator / generic company page)
    if job.platform_slug and job.platform_slug not in ("company", "generic"):
        score += _W["ats"]
        breakdown["ats"] = _W["ats"]

    # Freshness
    fresh = _freshness_score(job.posting_date, now) * _W["freshness"]
    score += fresh
    breakdown["freshness"] = round(fresh, 4)

    # --- normalize + penalties -------------------------------------------
    score = score / _MAX_POSITIVE

    if negatives:
        score -= _NEG_PENALTY
        breakdown["negative_penalty"] = -_NEG_PENALTY
        reasons.append(f"negative keywords: {', '.join(sorted(set(negatives)))}")

    if already_seen:
        score -= _SEEN_PENALTY
        breakdown["seen_penalty"] = -_SEEN_PENALTY

    score = max(0.0, min(1.0, score))

    # Hard gate: must be both an internship and software to ever pass.
    passes_gate = is_internship and is_software and not negatives
    is_relevant = passes_gate and score >= min_score

    categories = _detect_categories(haystack) if passes_gate else []

    return RelevanceResult(
        score=round(score, 4),
        is_relevant=is_relevant,
        is_internship=is_internship,
        is_software=is_software,
        matched_keywords=sorted(set(matched)),
        negatives=sorted(set(negatives)),
        categories=categories,
        breakdown=breakdown,
        reasons=reasons,
    )
