from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobbot.parsing.models import ExtractedJob
from jobbot.scoring.platform_prefs import (
    DEFAULT_DEPRIORITIZED_PLATFORMS,
    DEFAULT_PREFERRED_PLATFORMS,
    is_direct_ats,
    platform_factor,
)
from jobbot.scoring.relevance import ScoringPrefs, score_job

NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _job(platform: str | None):
    return ExtractedJob(
        url="https://example.com/1",
        title="Software Engineer Intern",
        company="Acme",
        platform_slug=platform,
        posting_date=NOW,
    )


# --- tier helper ---------------------------------------------------------- #
@pytest.mark.parametrize("slug", DEFAULT_PREFERRED_PLATFORMS)
def test_preferred_platforms_score_full(slug):
    assert platform_factor(slug) == 1.0


@pytest.mark.parametrize("slug", DEFAULT_DEPRIORITIZED_PLATFORMS)
def test_deprioritized_platforms_score_zero(slug):
    assert platform_factor(slug) == 0.0


def test_other_known_ats_is_neutral():
    assert 0.0 < platform_factor("jobvite") < 1.0


@pytest.mark.parametrize("slug", [None, "company", "generic"])
def test_non_ats_gets_no_preference(slug):
    assert platform_factor(slug) == 0.0
    assert not is_direct_ats(slug)


def test_registry_split_slugs_are_normalized():
    # PlatformRegistry emits greenhouse_jb / workday_alt for alternate domains.
    assert platform_factor("greenhouse_jb") == platform_factor("greenhouse")
    assert platform_factor("workday_alt") == platform_factor("workday")
    assert platform_factor("smartrecruiters_careers") == platform_factor("smartrecruiters")


def test_custom_lists_override_defaults():
    assert platform_factor("workday", preferred=["workday"]) == 1.0
    assert platform_factor("ashby", deprioritized=["ashby"]) == 0.0


# --- end-to-end scoring --------------------------------------------------- #
def test_ashby_outranks_workday():
    ashby = score_job(_job("ashby"), now=NOW)
    workday = score_job(_job("workday"), now=NOW)
    assert ashby.score > workday.score
    assert ashby.breakdown["platform_pref"] > 0
    assert "platform_pref" not in workday.breakdown  # zero bonus is not recorded


def test_greenhouse_and_lever_also_outrank_workday():
    workday = score_job(_job("workday"), now=NOW).score
    for slug in ("greenhouse", "lever"):
        assert score_job(_job(slug), now=NOW).score > workday


def test_deprioritized_platform_still_qualifies():
    """Preference is ranking, not filtering — Workday jobs must still post."""
    result = score_job(_job("workday"), min_score=0.55, now=NOW)
    assert result.is_relevant
    assert result.breakdown["ats"] > 0  # still credited as a direct ATS link


def test_guild_prefs_can_invert_the_default_ranking():
    prefs = ScoringPrefs(preferred_platforms=["workday"], deprioritized_platforms=["ashby"])
    assert (
        score_job(_job("workday"), prefs=prefs, now=NOW).score
        > score_job(_job("ashby"), prefs=prefs, now=NOW).score
    )


def test_empty_prefs_fall_back_to_defaults():
    assert (
        score_job(_job("ashby"), prefs=ScoringPrefs(), now=NOW).score
        == score_job(_job("ashby"), now=NOW).score
    )


def test_explicit_deprioritization_beats_default_preferred_tier():
    """Regression: deprioritizing a slug that ships in the preferred tier was ignored."""
    assert platform_factor("ashby", deprioritized=["ashby"]) == 0.0
    assert platform_factor("greenhouse_jb", deprioritized=["greenhouse"]) == 0.0


def test_supplying_preferred_replaces_the_default_tier():
    # "prefer only workday" must not leave ashby still top-tier.
    assert platform_factor("ashby", preferred=["workday"]) < 1.0


def test_slug_in_both_lists_is_preferred():
    assert platform_factor("ashby", preferred=["ashby"], deprioritized=["ashby"]) == 1.0
