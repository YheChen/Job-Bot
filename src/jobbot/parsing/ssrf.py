"""SSRF protection for outbound job-page fetches.

Before opening any result URL we validate that:
  * the scheme is http(s)
  * the host resolves only to public IP addresses (unless explicitly allowed)
  * the host matches the ATS allowlist or a configured company domain

This prevents a crafted search result from pointing the fetcher at internal
metadata endpoints (169.254.169.254), localhost, or private ranges.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from jobbot.queries.terms import PLATFORMS

_ATS_DOMAINS: frozenset[str] = frozenset(domain for _, domain in PLATFORMS.values())


class UnsafeURLError(Exception):
    pass


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def host_is_allowed(host: str, extra_domains: frozenset[str] | set[str] = frozenset()) -> bool:
    host = host.lower()
    allowed = _ATS_DOMAINS | set(extra_domains)
    return any(host == d or host.endswith("." + d) or host.endswith(d) for d in allowed)


def validate_url(
    url: str,
    *,
    allow_private_networks: bool = False,
    extra_domains: frozenset[str] | set[str] = frozenset(),
    resolve: bool = True,
) -> str:
    """Raise UnsafeURLError if the URL is not safe to fetch; else return it."""
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        raise UnsafeURLError(f"disallowed scheme: {split.scheme!r}")
    host = (split.hostname or "").lower()
    if not host:
        raise UnsafeURLError("missing host")

    if not host_is_allowed(host, extra_domains):
        raise UnsafeURLError(f"host not on allowlist: {host}")

    if allow_private_networks or not resolve:
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host}: {exc}") from exc

    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"host {host} resolves to non-public address {ip}")
    return url
