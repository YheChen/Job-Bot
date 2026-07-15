from __future__ import annotations

import pytest

from jobbot.search.base import QuotaTracker
from jobbot.search.manager import SearchManager
from jobbot.search.mock import MockProvider
from jobbot.search.models import ProviderError, QuotaExceeded, SearchResult


class _QuotaProvider:
    name = "quota"

    async def search(self, query, *, page=1, results_per_page=10):
        raise QuotaExceeded("out of quota")


class _OKProvider:
    name = "ok"

    async def search(self, query, *, page=1, results_per_page=10):
        return [SearchResult(url="https://jobs.ashbyhq.com/a/1", title="t", provider=self.name)]


class _BrokenProvider:
    name = "broken"

    async def search(self, query, *, page=1, results_per_page=10):
        raise ProviderError("boom")


async def test_manager_falls_back_on_quota():
    mgr = SearchManager([_QuotaProvider(), _OKProvider()])
    results, provider = await mgr.search("q")
    assert provider == "ok"
    assert results[0].url == "https://jobs.ashbyhq.com/a/1"


async def test_manager_falls_back_on_provider_error():
    mgr = SearchManager([_BrokenProvider(), _OKProvider()])
    _, provider = await mgr.search("q")
    assert provider == "ok"


async def test_manager_raises_when_all_fail():
    mgr = SearchManager([_QuotaProvider(), _BrokenProvider()])
    with pytest.raises(ProviderError):
        await mgr.search("q")


def test_quota_tracker_enforces_budgets():
    tracker = QuotaTracker(hourly_budget=2, daily_budget=5)
    tracker.check()
    tracker.record()
    tracker.record()
    with pytest.raises(QuotaExceeded):
        tracker.check()
    tracker.reset_hour()
    tracker.check()  # ok again


def test_quota_tracker_daily_cap():
    tracker = QuotaTracker(hourly_budget=100, daily_budget=1)
    tracker.record()
    with pytest.raises(QuotaExceeded):
        tracker.check()


async def test_mock_provider_respects_site_filter():
    provider = MockProvider()
    results = await provider.search('site:jobs.ashbyhq.com ("software engineer intern")')
    assert results
    assert all("jobs.ashbyhq.com" in r.url for r in results)
