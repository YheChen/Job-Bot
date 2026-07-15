"""Search provider protocol + a base class with shared quota accounting."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from jobbot.search.models import QuotaExceeded, SearchResult


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        results_per_page: int = 10,
    ) -> list[SearchResult]:
        ...


class QuotaTracker:
    """In-memory hourly/daily counters.

    Persistent budgets are enforced by the scheduler/scan service; this is a
    fast local guard so a single provider never blows past its allotment within
    a process lifetime.
    """

    def __init__(self, hourly_budget: int, daily_budget: int) -> None:
        self.hourly_budget = hourly_budget
        self.daily_budget = daily_budget
        self._hour_count = 0
        self._day_count = 0

    def check(self) -> None:
        if self._hour_count >= self.hourly_budget:
            raise QuotaExceeded("hourly budget exhausted")
        if self._day_count >= self.daily_budget:
            raise QuotaExceeded("daily budget exhausted")

    def record(self, n: int = 1) -> None:
        self._hour_count += n
        self._day_count += n

    def reset_hour(self) -> None:
        self._hour_count = 0

    def reset_day(self) -> None:
        self._hour_count = 0
        self._day_count = 0


class HTTPProviderBase:
    """Common HTTP plumbing for API-backed providers."""

    name: str = "base"

    def __init__(self, client: httpx.AsyncClient, tracker: QuotaTracker | None = None) -> None:
        self._client = client
        self._tracker = tracker or QuotaTracker(10_000, 100_000)

    def _guard(self) -> None:
        self._tracker.check()
