"""Generic adapter for ATSes without bespoke parsing, and for company domains.

Relies entirely on the base JSON-LD + Open Graph + <title> extraction, which is
sufficient for the long tail of platforms. Give it a slug/name/domains at
construction time.
"""

from __future__ import annotations

from jobbot.platforms.base import PlatformAdapter


class GenericAdapter(PlatformAdapter):
    def __init__(self, slug: str, name: str, domains: tuple[str, ...]) -> None:
        self.slug = slug
        self.name = name
        self.domains = domains
