"""URL canonicalization.

Removes tracking parameters and normalizes equivalent URLs so the same job is
never treated as two. Redirect-following is handled by the fetcher; this module
is pure and fully unit-testable.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gh_src",
        "gh_jid",
        "lever-source",
        "lever-origin",
        "source",
        "src",
        "ref",
        "referrer",
        "recruiter",
        "trk",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
    }
)

# Query params that are meaningful to a job's identity and must be preserved.
SIGNIFICANT_PARAMS: frozenset[str] = frozenset({"jobId", "gh_jid", "id"})
# gh_jid is significant for Greenhouse embed URLs but tracking elsewhere; we keep
# it only when the host is greenhouse (handled in canonicalize()).


def strip_tracking_params(query: str, host: str = "") -> str:
    pairs = parse_qsl(query, keep_blank_values=False)
    kept: list[tuple[str, str]] = []
    for key, value in pairs:
        lower = key.lower()
        if lower in TRACKING_PARAMS and not (
            lower == "gh_jid" and "greenhouse" in host
        ):
            continue
        kept.append((key, value))
    kept.sort()
    return urlencode(kept)


def canonicalize_url(url: str) -> str:
    """Return a normalized, tracking-free URL.

    - lowercases scheme + host
    - drops default ports and fragments
    - removes a trailing slash on non-root paths
    - strips known tracking params and sorts the remainder
    """
    if not url:
        return url
    split = urlsplit(url.strip())
    scheme = (split.scheme or "https").lower()
    host = split.hostname.lower() if split.hostname else ""

    netloc = host
    if split.port and not (
        (scheme == "http" and split.port == 80) or (scheme == "https" and split.port == 443)
    ):
        netloc = f"{host}:{split.port}"

    path = split.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = strip_tracking_params(split.query, host)

    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_host(url: str) -> str:
    split = urlsplit(url)
    return (split.hostname or "").lower()
