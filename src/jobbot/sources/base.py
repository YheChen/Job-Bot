"""Listing-source protocol.

A *listing source* is a curated feed of postings — as opposed to a
`SearchProvider`, which answers a query. Sources emit fully-formed
`ExtractedJob`s because feeds usually carry better metadata than we could
scrape back off the job page, so no fetch/parse round-trip is needed.

Everything downstream (dedup, relevance scoring, expiration, persistence,
Discord delivery) is shared with the search path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobbot.parsing.models import ExtractedJob


@runtime_checkable
class ListingSource(Protocol):
    name: str

    async def fetch(self) -> list[ExtractedJob]:
        """Return candidate jobs. Should return [] rather than raise on a
        transient failure or an unchanged upstream feed."""
        ...
