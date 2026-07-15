from __future__ import annotations

import pytest

from jobbot.parsing.models import PageFetch
from jobbot.platforms.registry import PlatformRegistry


@pytest.fixture
def registry():
    return PlatformRegistry.default()


@pytest.mark.parametrize(
    "url,slug",
    [
        ("https://jobs.ashbyhq.com/acme/1234-abc", "ashby"),
        ("https://boards.greenhouse.io/acme/jobs/56789", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/56789", "greenhouse"),
        ("https://jobs.lever.co/acme/uuid", "lever"),
        ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x_R-100", "workday"),
        ("https://jobs.smartrecruiters.com/Acme/12345-title", "smartrecruiters"),
        ("https://apply.workable.com/acme/j/ABC123/", "workable"),
        ("https://jobs.jobvite.com/acme/job/x", "jobvite"),
        ("https://ats.rippling.com/acme/jobs/1", "rippling"),
    ],
)
def test_platform_recognition(registry, url, slug):
    assert registry.platform_slug_for(url) == slug


def test_unknown_host_returns_none(registry):
    assert registry.resolve("https://example.com/careers/1") is None


def test_company_domain_allowlisted():
    reg = PlatformRegistry.default(company_domains=["careers.acme.com"])
    assert reg.platform_slug_for("https://careers.acme.com/jobs/1") == "company"


def test_workday_requisition_id_extraction(registry):
    adapter = registry.resolve("https://acme.wd1.myworkdayjobs.com/careers/job/x_R-12345")
    assert (
        adapter.extract_job_id("https://acme.wd1.myworkdayjobs.com/careers/job/x_R-12345")
        == "R-12345"
    )


def test_greenhouse_id_extraction(registry):
    adapter = registry.resolve("https://boards.greenhouse.io/acme/jobs/56789")
    assert adapter.extract_job_id("https://boards.greenhouse.io/acme/jobs/56789") == "56789"


def test_ashby_jsonld_parse(registry, fixture_html):
    page = PageFetch(
        url="https://jobs.ashbyhq.com/example-tech/12345678-1234-1234-1234-123456789abc",
        final_url="https://jobs.ashbyhq.com/example-tech/12345678-1234-1234-1234-123456789abc",
        status_code=200,
        html=fixture_html("ashby.html"),
        ok=True,
    )
    adapter = registry.resolve(page.final_url)
    job = adapter.parse(page)
    assert job.title == "Software Engineer Intern, Summer 2027"
    assert job.company == "Example Technologies"
    assert "Toronto" in job.location
    assert job.platform_slug == "ashby"
    assert job.source == "jsonld"


def test_greenhouse_remote_parse(registry, fixture_html):
    page = PageFetch(
        url="https://boards.greenhouse.io/acme/jobs/56789",
        final_url="https://boards.greenhouse.io/acme/jobs/56789",
        status_code=200,
        html=fixture_html("greenhouse.html"),
        ok=True,
    )
    job = registry.resolve(page.final_url).parse(page)
    assert job.remote_status == "Remote"
    assert job.company == "Acme"
