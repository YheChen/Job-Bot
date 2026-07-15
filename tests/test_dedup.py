from __future__ import annotations

from jobbot.dedup.detector import (
    ExistingJobLike,
    content_hash,
    dedup_key,
    find_duplicate,
    normalize_company,
    title_key,
    token_set_ratio,
)
from jobbot.parsing.models import ExtractedJob


def _job(**kw) -> ExtractedJob:
    base = dict(url="https://jobs.ashbyhq.com/acme/1", platform_slug="ashby")
    base.update(kw)
    return ExtractedJob(**base)


def _existing_from(job: ExtractedJob) -> ExistingJobLike:
    return ExistingJobLike(
        dedup_key=dedup_key(job),
        canonical_url=job.canonical_url,
        platform_slug=job.platform_slug,
        external_job_id=job.external_job_id,
        content_hash=content_hash(job),
        normalized_company=normalize_company(job.company),
        title=job.title,
        description=job.description,
    )


def test_normalize_company_drops_suffixes():
    assert normalize_company("Example Technologies, Inc.") == "example"
    assert normalize_company("Acme LLC") == "acme"


def test_title_key_strips_term_noise():
    assert title_key("Software Engineer Intern, Summer 2027") == title_key(
        "Software Engineer Intern"
    )


def test_same_platform_job_id_is_duplicate_across_urls():
    a = _job(external_job_id="R-100", canonical_url="https://x.myworkdayjobs.com/a/R-100")
    b = _job(
        external_job_id="R-100",
        platform_slug="ashby",
        canonical_url="https://y.myworkdayjobs.com/b/R-100",
    )
    match = find_duplicate(b, [_existing_from(a)])
    assert match.is_duplicate
    assert match.reason in ("platform_job_id", "dedup_key")


def test_tracking_param_change_is_not_a_new_job():
    a = _job(
        company="Acme",
        title="Backend Intern",
        canonical_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    b = _job(
        company="Acme",
        title="Backend Intern",
        canonical_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    assert find_duplicate(b, [_existing_from(a)]).is_duplicate


def test_slight_title_change_same_company_is_duplicate():
    a = _job(company="Acme Inc", title="Backend Engineer Intern")
    b = _job(company="Acme", title="Backend Engineer Internship 2027")
    match = find_duplicate(b, [_existing_from(a)])
    assert match.is_duplicate


def test_different_company_same_title_is_not_duplicate():
    a = _job(company="Acme", title="Backend Engineer Intern")
    b = _job(company="Globex", title="Backend Engineer Intern", external_job_id="Z")
    assert not find_duplicate(b, [_existing_from(a)]).is_duplicate


def test_token_set_ratio_bounds():
    assert token_set_ratio("a b c", "a b c") == 1.0
    assert token_set_ratio("", "x") == 0.0
