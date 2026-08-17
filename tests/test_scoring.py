from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobbot.parsing.models import ExtractedJob
from jobbot.scoring.relevance import ScoringPrefs, score_job

NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _job(
    title, desc="", company="Acme", location=None, platform="ashby", posted=None, expired=False
):
    return ExtractedJob(
        url="https://jobs.ashbyhq.com/acme/1",
        title=title,
        description=desc,
        company=company,
        location=location,
        platform_slug=platform,
        posting_date=posted,
        is_expired=expired,
    )


def test_software_internship_passes_gate():
    r = score_job(_job("Software Engineer Intern", posted=NOW - timedelta(days=1)), now=NOW)
    assert r.is_internship and r.is_software
    assert r.is_relevant
    assert r.score > 0.55


def test_senior_role_is_rejected():
    r = score_job(_job("Senior Software Engineer"), now=NOW)
    assert not r.is_relevant
    assert "senior software engineer" in r.negatives


def test_sales_intern_rejected_no_software():
    r = score_job(_job("Sales Intern"), now=NOW)
    assert not r.is_relevant
    assert not r.is_software


def test_mechanical_intern_without_software_rejected():
    # "engineering" is a listed software indicator, but the "mechanical
    # engineering intern" negative keyword rejects it at the gate regardless.
    r = score_job(_job("Mechanical Engineering Intern"), now=NOW)
    assert not r.is_relevant
    assert "mechanical engineering intern" in r.negatives


def test_pure_mechanical_intern_not_software():
    r = score_job(_job("Mechanical Design Intern", desc="CAD and manufacturing"), now=NOW)
    assert not r.is_software
    assert not r.is_relevant


def test_mechanical_with_software_focus_can_pass_gate():
    r = score_job(
        _job("Mechanical Systems Software Engineering Intern", desc="write embedded software"),
        now=NOW,
    )
    assert r.is_software  # software indicator present alongside mechanical


def test_upper_year_experience_not_rejected():
    r = score_job(
        _job(
            "Software Engineering Intern",
            desc="Requires prior experience; open to upper-year students.",
            posted=NOW,
        ),
        now=NOW,
    )
    assert r.is_relevant


def test_expired_scores_zero():
    r = score_job(_job("Software Engineer Intern", expired=True), now=NOW)
    assert r.score == 0.0
    assert not r.is_relevant


def test_location_and_term_boost_score():
    base = score_job(_job("Software Engineer Intern", posted=NOW), now=NOW)
    boosted = score_job(
        _job("Software Engineer Intern Summer 2027", location="Toronto, ON", posted=NOW),
        preferred_locations=["Toronto"],
        preferred_terms=["Summer 2027"],
        now=NOW,
    )
    assert boosted.score > base.score


def test_freshness_decreases_with_age():
    fresh = score_job(_job("Software Engineer Intern", posted=NOW - timedelta(days=1)), now=NOW)
    stale = score_job(_job("Software Engineer Intern", posted=NOW - timedelta(days=60)), now=NOW)
    assert fresh.score > stale.score


def test_categories_detected():
    r = score_job(_job("Machine Learning Intern", desc="ML engineer role"), now=NOW)
    assert "machine_learning" in r.categories


# --- ScoringPrefs plumbing (regression: guild settings never reached the scorer) --- #
def test_prefs_apply_location_and_term_bonuses():
    """Regression: locations/terms were passed as None, capping every score at 0.82."""
    job = _job("Software Engineer Intern Summer 2027", location="Toronto, ON", posted=NOW)
    without = score_job(job, now=NOW)
    with_prefs = score_job(
        job,
        prefs=ScoringPrefs(locations=["Toronto"], terms=["Summer 2027"]),
        now=NOW,
    )
    assert with_prefs.score > without.score
    # The two dead signals are worth 0.18 combined; without them nothing can top 0.82.
    assert without.score <= 0.82
    assert with_prefs.score > 0.82
    assert "location" in with_prefs.breakdown
    assert "term" in with_prefs.breakdown


def test_prefs_negative_keywords_are_applied():
    """Regression: /jobs set-negative-keywords was stored but never read."""
    job = _job("Business Systems Analyst Intern", desc="software systems", posted=NOW)
    assert score_job(job, now=NOW).is_relevant
    filtered = score_job(
        job, prefs=ScoringPrefs(negative_keywords=["business systems analyst"]), now=NOW
    )
    assert not filtered.is_relevant
    assert "business systems analyst" in filtered.negatives


def test_prefs_extra_keywords_widen_the_software_gate():
    job = _job("Compiler Intern", desc="work on our toolchain", posted=NOW)
    assert not score_job(job, now=NOW).is_software
    widened = score_job(job, prefs=ScoringPrefs(extra_keywords=["compiler"]), now=NOW)
    assert widened.is_software
    assert widened.is_relevant


def test_prefs_min_score_is_used():
    job = _job("Software Engineer Intern", posted=NOW - timedelta(days=60))
    assert score_job(job, prefs=ScoringPrefs(min_score=0.99), now=NOW).is_relevant is False
    assert score_job(job, prefs=ScoringPrefs(min_score=0.1), now=NOW).is_relevant is True


def test_explicit_kwargs_still_override_prefs():
    job = _job("Software Engineer Intern", location="Toronto", posted=NOW)
    result = score_job(
        job,
        prefs=ScoringPrefs(locations=["Vancouver"]),
        preferred_locations=["Toronto"],
        now=NOW,
    )
    assert "location" in result.breakdown  # explicit arg won


def test_empty_prefs_change_nothing():
    job = _job("Software Engineer Intern", posted=NOW)
    assert score_job(job, now=NOW).score == score_job(job, prefs=ScoringPrefs(), now=NOW).score
