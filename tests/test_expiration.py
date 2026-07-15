from __future__ import annotations

from jobbot.expiration.checker import detect_expired_in_html, evaluate_page
from jobbot.parsing.models import PageFetch


def _page(html: str, status: int = 200) -> PageFetch:
    return PageFetch(url="u", final_url="u", status_code=status, html=html, ok=status < 400)


def test_detects_closed_phrase(fixture_html):
    reason = detect_expired_in_html(fixture_html("workable.html"))
    assert reason is not None
    assert "closed" in reason


def test_http_404_is_expired():
    result = evaluate_page(_page("<html></html>", status=404))
    assert result.is_expired
    assert result.reason == "http_404"


def test_http_500_is_not_expired():
    result = evaluate_page(_page("<html></html>", status=500))
    assert not result.is_expired


def test_validthrough_in_past_is_expired():
    html = """<script type="application/ld+json">
    {"@type":"JobPosting","title":"X","validThrough":"2020-01-01"}
    </script>"""
    result = evaluate_page(_page(html))
    assert result.is_expired
    assert result.reason == "validThrough_past"


def test_active_job_not_expired(fixture_html):
    result = evaluate_page(_page(fixture_html("ashby.html")))
    assert not result.is_expired


def test_disabled_apply_button_detected():
    html = '<html><body><button disabled>Apply</button></body></html>'
    assert detect_expired_in_html(html) == "apply_button_disabled"
