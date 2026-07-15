"""Platform registry: resolve a hostname to the right adapter."""

from __future__ import annotations

from jobbot.parsing.url import registrable_host
from jobbot.platforms.ashby import AshbyAdapter
from jobbot.platforms.base import PlatformAdapter
from jobbot.platforms.generic import GenericAdapter
from jobbot.platforms.greenhouse import GreenhouseAdapter
from jobbot.platforms.lever import LeverAdapter
from jobbot.platforms.smartrecruiters import SmartRecruitersAdapter
from jobbot.platforms.workable import WorkableAdapter
from jobbot.platforms.workday import WorkdayAdapter
from jobbot.queries.terms import PLATFORMS

# Platforms handled by generic JSON-LD/OG parsing (no bespoke module yet).
_GENERIC_SLUGS = {
    "jobvite",
    "bamboohr",
    "icims",
    "careerspage",
    "recruitee",
    "personio",
    "rippling",
    "adp",
    "oracle",
    "successfactors",
}


class PlatformRegistry:
    def __init__(self, adapters: list[PlatformAdapter], company_domains: list[str] | None = None):
        self._adapters = adapters
        self._company = GenericAdapter("company", "Company", tuple(company_domains or ()))

    @classmethod
    def default(cls, company_domains: list[str] | None = None) -> PlatformRegistry:
        adapters: list[PlatformAdapter] = [
            AshbyAdapter(),
            GreenhouseAdapter(),
            LeverAdapter(),
            WorkdayAdapter(),
            SmartRecruitersAdapter(),
            WorkableAdapter(),
        ]
        # Add generic adapters for the remaining declared platforms.
        covered_domains = {d for a in adapters for d in a.domains}
        for slug in _GENERIC_SLUGS:
            entry = PLATFORMS.get(slug)
            if not entry:
                continue
            name, domain = entry
            if domain in covered_domains:
                continue
            adapters.append(GenericAdapter(slug, name, (domain,)))
        return cls(adapters, company_domains)

    def resolve(self, url: str) -> PlatformAdapter | None:
        host = registrable_host(url)
        if not host:
            return None
        for adapter in self._adapters:
            if adapter.matches(host):
                return adapter
        if self._company.domains and self._company.matches(host):
            return self._company
        return None

    def platform_slug_for(self, url: str) -> str | None:
        adapter = self.resolve(url)
        return adapter.slug if adapter else None
