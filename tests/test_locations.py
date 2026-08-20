from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobbot.parsing.models import ExtractedJob
from jobbot.scoring.locations import LOCATION_ALIASES, canonical_groups, match_location
from jobbot.scoring.relevance import ScoringPrefs, score_job

NOW = datetime(2026, 8, 18, tzinfo=UTC)
TARGETS = ["bay area", "toronto", "seattle", "nyc", "redmond"]


def _job(location: str | None, title: str = "Software Engineer Intern"):
    return ExtractedJob(
        url="https://jobs.ashbyhq.com/acme/1",
        title=title,
        company="Acme",
        location=location,
        platform_slug="ashby",
        posting_date=NOW,
    )


# --- alias matching ------------------------------------------------------- #
@pytest.mark.parametrize(
    "location,expected",
    [
        ("SF", "bay area"),
        ("San Francisco, CA", "bay area"),
        ("Palo Alto", "bay area"),
        ("Sunnyvale, CA", "bay area"),
        ("Foster City, CA", "bay area"),
        ("Oakland, CA", "bay area"),
        ("Toronto, ON, Canada", "toronto"),
        ("Mississauga, ON", "toronto"),
        ("Seattle, WA", "seattle"),
        ("Bellevue, WA", "seattle"),
        ("New York, NY", "nyc"),
        ("NYC", "nyc"),
        ("Brooklyn, NY", "nyc"),
        ("Redmond, WA", "redmond"),
    ],
)
def test_target_metros_match(location, expected):
    assert match_location(location, TARGETS) == expected


@pytest.mark.parametrize(
    "location",
    [
        "Dallas, TX",
        "Chicago, IL",
        "Madison, WI",
        "London, UK",
        "Edmonton, AB, Canada",
        "Vancouver, BC",
        "Austin, TX",
        "Remote",
    ],
)
def test_other_locations_do_not_match(location):
    assert match_location(location, TARGETS) is None


@pytest.mark.parametrize(
    "location",
    [
        "Ontario, CA",  # California, not Ontario Canada
        "Spokane, WA",  # Washington, but not Seattle
        "Sanford, FL",  # must not fire the "sf" alias
        "Newark, NJ",  # must not fire "new york"
    ],
)
def test_near_miss_locations_are_rejected(location):
    assert match_location(location, TARGETS) is None


def test_multi_location_string_matches_first_hit():
    assert match_location("SF; NYC", TARGETS) == "bay area"


def test_remote_is_opt_in():
    assert match_location("Remote", TARGETS) is None
    assert match_location("Remote (US)", [*TARGETS, "remote"]) == "remote"


def test_unknown_configured_location_matches_literally():
    """An entry with no alias group still filters on its literal name."""
    assert match_location("Austin, TX", ["austin"]) == "austin"
    assert match_location("Dallas, TX", ["austin"]) is None


def test_empty_inputs():
    assert match_location(None, TARGETS) is None
    assert match_location("Seattle", []) is None
    assert match_location("Seattle", None) is None


def test_alias_groups_avoid_bare_region_codes():
    """Bare state/province codes would over-match (Ontario CA, Spokane WA)."""
    for aliases in LOCATION_ALIASES.values():
        for alias in aliases:
            assert alias not in {"ca", "wa", "ny", "on", "usa", "canada"}


# --- filter behaviour in scoring ------------------------------------------ #
def test_require_location_rejects_other_metros():
    prefs = ScoringPrefs(locations=TARGETS, require_location=True)
    result = score_job(_job("Dallas, TX"), prefs=prefs, now=NOW)
    assert not result.is_relevant
    assert not result.location_ok
    assert any("location not in allowed set" in r for r in result.reasons)


def test_require_location_accepts_target_metros():
    prefs = ScoringPrefs(locations=TARGETS, require_location=True)
    result = score_job(_job("Seattle, WA"), prefs=prefs, now=NOW)
    assert result.is_relevant
    assert result.location_ok
    assert result.matched_location == "seattle"


def test_without_require_location_it_is_only_a_bonus():
    prefs = ScoringPrefs(locations=TARGETS, require_location=False)
    elsewhere = score_job(_job("Dallas, TX"), prefs=prefs, now=NOW)
    target = score_job(_job("Seattle, WA"), prefs=prefs, now=NOW)
    assert elsewhere.is_relevant, "filtering must be opt-in"
    assert target.score > elsewhere.score, "matching locations still rank higher"


def test_alias_earns_the_location_bonus():
    """Configuring 'bay area' must also reward a posting listed as 'SF'."""
    prefs = ScoringPrefs(locations=["bay area"])
    assert "location" in score_job(_job("SF"), prefs=prefs, now=NOW).breakdown


def test_missing_location_falls_back_to_title():
    prefs = ScoringPrefs(locations=TARGETS, require_location=True)
    ok = score_job(_job(None, title="Software Engineer Intern - Toronto"), prefs=prefs, now=NOW)
    assert ok.location_ok and ok.matched_location == "toronto"


def test_missing_location_with_no_hint_is_filtered_out():
    prefs = ScoringPrefs(locations=TARGETS, require_location=True)
    assert not score_job(_job(None), prefs=prefs, now=NOW).location_ok


# --- purge helper semantics ----------------------------------------------- #
def test_purge_predicate_matches_the_live_filter():
    """The purge script must judge a job exactly as _ingest would.

    It reuses match_location with the same (location or title) fallback, so
    this pins that contract rather than a second copy of the rules.
    """
    cases = {
        "SF": True,
        "San Jose, CA": True,
        "Toronto, ON, Canada": True,
        "Seattle, WA": True,
        "Chicago, IL": False,
        "London, UK": False,
        None: False,
    }
    for location, expected in cases.items():
        job = _job(location)
        via_scorer = score_job(
            job, prefs=ScoringPrefs(locations=TARGETS, require_location=True), now=NOW
        ).location_ok
        via_purge = bool(match_location(job.location or job.title, TARGETS))
        assert via_scorer is expected, location
        assert via_purge is expected, location
        assert via_scorer == via_purge, f"purge and ingest disagree on {location!r}"


# --- canonical_groups robustness ------------------------------------------ #
def test_a_bare_string_is_split_not_iterated():
    """Regression: a stringified list was iterated character-by-character,
    resolving to zero locations and silently rejecting every job."""
    assert canonical_groups("Bay Area, Toronto") == ["bay area", "toronto"]
    assert canonical_groups('["Bay Area", "Toronto"]') == ["bay area", "toronto"]


def test_stringified_list_still_matches_correctly():
    stringified = '["Bay Area", "Toronto", "Seattle", "NYC", "Redmond"]'
    assert match_location("San Jose, CA", stringified) == "bay area"
    assert match_location("Redmond, WA", stringified) == "redmond"
    assert match_location("Chicago, IL", stringified) is None


def test_single_character_entries_are_discarded():
    """Characters only arise from an iterated string; they must never become
    filter terms, or a location like 'Austin' matches on a stray 'a'."""
    assert canonical_groups(["a", "[", '"', "S"]) == []


def test_non_string_entries_are_ignored():
    assert canonical_groups(["Toronto", None, 42, {"x": 1}]) == ["toronto"]


def test_quotes_and_brackets_are_stripped_from_entries():
    assert canonical_groups(['"Seattle"', "[Toronto]"]) == ["seattle", "toronto"]
