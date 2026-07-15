from __future__ import annotations

from jobbot.parsing.url import canonicalize_url, strip_tracking_params


def test_strips_utm_and_tracking_params():
    url = "https://jobs.ashbyhq.com/acme/1234?utm_source=google&utm_medium=cpc&ref=x"
    assert canonicalize_url(url) == "https://jobs.ashbyhq.com/acme/1234"


def test_strips_lever_and_gh_src():
    url = "https://jobs.lever.co/acme/uuid?lever-source=LinkedIn&source=abc"
    assert canonicalize_url(url) == "https://jobs.lever.co/acme/uuid"


def test_equivalent_urls_normalize_identically():
    a = "https://boards.greenhouse.io/acme/jobs/56789?gh_src=abcd&utm_campaign=x"
    b = "https://boards.greenhouse.io/acme/jobs/56789/"
    # gh_src stripped, trailing slash removed → same canonical form
    assert canonicalize_url(a) == canonicalize_url(b)


def test_lowercases_scheme_and_host_drops_fragment():
    url = "HTTPS://Jobs.AshbyHQ.com/acme/1234#section"
    assert canonicalize_url(url) == "https://jobs.ashbyhq.com/acme/1234"


def test_significant_query_params_are_sorted_and_kept():
    url = "https://apply.workable.com/acme/j/ABC/?b=2&a=1"
    out = canonicalize_url(url)
    assert out.endswith("?a=1&b=2")


def test_strip_tracking_preserves_gh_jid_on_greenhouse():
    q = strip_tracking_params("gh_jid=999&utm_source=x", host="boards.greenhouse.io")
    assert "gh_jid=999" in q
    assert "utm_source" not in q
