"""Location matching with metro aliases.

Job postings spell the same place many ways: "SF", "San Francisco", "Palo
Alto" and "Sunnyvale" are all the Bay Area; "Toronto, ON, Canada" and "North
York" are both Toronto. Plain substring matching against a city name misses
most of them, so each metro is an alias group.

Deliberately excluded from the alias lists:
  * bare state/province codes ("CA", "WA", "NY", "ON") — "Ontario, CA" is in
    California and "Spokane, WA" is not Seattle
  * bare country names — too broad to mean a metro

Matching is word-boundary based so "SF" does not fire inside another word.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Canonical metro -> spellings that mean it.
LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "bay area": (
        "bay area",
        "san francisco",
        "sf",
        "s.f.",
        "south san francisco",
        "palo alto",
        "mountain view",
        "sunnyvale",
        "santa clara",
        "menlo park",
        "cupertino",
        "redwood city",
        "foster city",
        "san mateo",
        "san jose",
        "oakland",
        "berkeley",
        "emeryville",
        "burlingame",
        "milpitas",
        "fremont",
        "silicon valley",
    ),
    "toronto": (
        "toronto",
        "gta",
        "north york",
        "scarborough",
        "etobicoke",
        "mississauga",
        "markham",
        "vaughan",
        "brampton",
    ),
    "seattle": ("seattle", "bellevue", "kirkland", "puget sound"),
    "nyc": ("nyc", "new york", "new york city", "manhattan", "brooklyn"),
    "redmond": ("redmond",),
    # Opt-in: only matches when "remote" is in the configured list.
    "remote": ("remote", "anywhere", "distributed", "work from home"),
}


@lru_cache(maxsize=256)
def _alias_pattern(group: str) -> re.Pattern[str] | None:
    aliases = LOCATION_ALIASES.get(group.strip().lower())
    if not aliases:
        return None
    joined = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{joined})(?![a-z0-9])", re.IGNORECASE)


def canonical_groups(locations: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Map configured names onto known alias groups, keeping unknown ones.

    An unrecognized entry (e.g. "Austin") still works — it is matched
    literally — so the filter never silently ignores what someone typed.

    A bare string is split on commas rather than iterated. Iterating one
    character-at-a-time silently turns every location into a meaningless
    single letter, which matches nothing — a filter that quietly rejects
    everything is far worse than one that errors.
    """
    if isinstance(locations, str):
        locations = [part for part in locations.split(",")]

    groups: list[str] = []
    for raw in locations or ():
        if not isinstance(raw, str):
            continue
        name = raw.strip().strip("[]\"'").strip().lower()
        # A single character is never a real location; it only arises from a
        # value that was iterated instead of parsed.
        if len(name) < 2:
            continue
        if name and name not in groups:
            groups.append(name)
    return groups


def match_location(text: str | None, locations: list[str] | tuple[str, ...] | None) -> str | None:
    """Return the first configured location the text matches, else None."""
    if not text or not locations:
        return None
    for group in canonical_groups(locations):
        pattern = _alias_pattern(group)
        if pattern is not None:
            if pattern.search(text):
                return group
        elif re.search(rf"(?<![a-z0-9]){re.escape(group)}(?![a-z0-9])", text, re.IGNORECASE):
            # Not a known metro: match the literal string the user configured.
            return group
    return None
