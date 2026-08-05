"""Academic-term parsing (e.g. "Summer 2027").

Shared by the listing sources and by job persistence so a term is recognized
the same way regardless of where the posting came from.
"""

from __future__ import annotations

import re

_TERM_RE = re.compile(r"\b(summer|winter|fall|autumn|spring)[\s\-_/]*((?:19|20)\d{2})\b", re.I)

_SEASON_CANON = {
    "summer": "Summer",
    "winter": "Winter",
    "fall": "Fall",
    "autumn": "Fall",
    "spring": "Spring",
}


def parse_internship_term(*texts: str | None) -> str | None:
    """Return the first "<Season> <Year>" found across `texts`, normalized.

    Any year is accepted — the caller decides which terms it cares about via
    guild settings, so hardcoding a year here would silently drop postings.
    """
    for text in texts:
        if not text:
            continue
        match = _TERM_RE.search(text)
        if match:
            season = _SEASON_CANON[match.group(1).lower()]
            return f"{season} {match.group(2)}"
    return None
