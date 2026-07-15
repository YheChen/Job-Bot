"""Expired-job detection.

Used in two places:
  * before posting a candidate (don't alert on a dead link)
  * during the periodic recheck of previously discovered jobs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from selectolax.parser import HTMLParser

from jobbot.parsing.fetcher import PageFetcher
from jobbot.parsing.jsonld import extract_jobposting
from jobbot.parsing.models import PageFetch
from jobbot.platforms.base import DEFAULT_EXPIRED_PHRASES
from jobbot.platforms.registry import PlatformRegistry


@dataclass
class ExpirationResult:
    is_expired: bool
    reason: str | None = None
    expires_at: datetime | None = None


def detect_expired_in_html(html: str, phrases: tuple[str, ...] = DEFAULT_EXPIRED_PHRASES) -> str | None:
    lowered = html.lower()
    for phrase in phrases:
        if phrase in lowered:
            return f"phrase:{phrase}"
    # Disabled/missing application button heuristic.
    tree = HTMLParser(html)
    apply_nodes = [
        n
        for n in tree.css("button, a")
        if "apply" in (n.text(strip=True) or "").lower()
    ]
    disabled = [
        n
        for n in apply_nodes
        if "disabled" in n.attributes or "aria-disabled" in n.attributes
    ]
    if apply_nodes and len(disabled) == len(apply_nodes):
        return "apply_button_disabled"
    return None


def evaluate_page(page: PageFetch) -> ExpirationResult:
    if page.status_code in (404, 410):
        return ExpirationResult(True, f"http_{page.status_code}")
    if page.status_code >= 500:
        # Transient; don't mark expired on server errors.
        return ExpirationResult(False, f"http_{page.status_code}")

    job = extract_jobposting(page.html)
    if job and job.expires_at:
        exp = job.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp < datetime.now(UTC):
            return ExpirationResult(True, "validThrough_past", exp)

    reason = detect_expired_in_html(page.html)
    if reason:
        return ExpirationResult(True, reason)
    return ExpirationResult(False)


class ExpirationChecker:
    def __init__(self, registry: PlatformRegistry, fetcher: PageFetcher) -> None:
        self._registry = registry
        self._fetcher = fetcher

    async def check(self, url: str) -> ExpirationResult:
        page = await self._fetcher.fetch(url)
        if page is None:
            # Could not fetch (blocked/unsafe/network) — treat as unknown, not expired.
            return ExpirationResult(False, "unfetchable")

        adapter = self._registry.resolve(page.final_url)
        phrases = adapter.expired_phrases if adapter else DEFAULT_EXPIRED_PHRASES
        if page.status_code in (404, 410):
            return ExpirationResult(True, f"http_{page.status_code}")
        result = evaluate_page(page)
        if not result.is_expired:
            reason = detect_expired_in_html(page.html, phrases)
            if reason:
                return ExpirationResult(True, reason)
        return result
