"""Extract schema.org JobPosting data from JSON-LD blocks.

Structured data is preferred over HTML selectors: it's stable across ATS
template changes and gives us canonical title/company/location/dates directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from jobbot.parsing.models import ExtractedJob
from jobbot.parsing.sanitize import html_to_text


def _iter_jsonld(tree: HTMLParser) -> list[Any]:
    blocks: list[Any] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text(deep=True, strip=False)
        if not text:
            continue
        try:
            blocks.append(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            continue
    return blocks


def _flatten(obj: Any) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, list):
        for item in obj:
            out.extend(_flatten(item))
    elif isinstance(obj, dict):
        if "@graph" in obj:
            out.extend(_flatten(obj["@graph"]))
        else:
            out.append(obj)
    return out


def _parse_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dateparser.parse(value)
    except (ValueError, OverflowError):
        return None


def _job_type(node: dict) -> str | None:
    t = node.get("@type")
    if isinstance(t, list):
        return "JobPosting" if "JobPosting" in t else None
    return t if t == "JobPosting" else None


def _location(node: dict) -> str | None:
    loc = node.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address", {})
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            parts = [p for p in parts if isinstance(p, str) and p]
            if parts:
                return ", ".join(parts)
    if node.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    return None


def extract_jobposting(html: str) -> ExtractedJob | None:
    """Return an ExtractedJob from the first JobPosting JSON-LD block, or None."""
    tree = HTMLParser(html)
    for block in _iter_jsonld(tree):
        for node in _flatten(block):
            if _job_type(node) != "JobPosting":
                continue
            org = node.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org
            valid_through = _parse_date(node.get("validThrough"))
            return ExtractedJob(
                url="",  # filled by caller
                title=node.get("title"),
                company=company if isinstance(company, str) else None,
                location=_location(node),
                posting_date=_parse_date(node.get("datePosted")),
                employment_type=(
                    node.get("employmentType")
                    if isinstance(node.get("employmentType"), str)
                    else None
                ),
                description=html_to_text(node.get("description")),
                external_job_id=str(node.get("identifier", {}).get("value"))
                if isinstance(node.get("identifier"), dict)
                else None,
                expires_at=valid_through,
                remote_status="Remote" if node.get("jobLocationType") == "TELECOMMUTE" else None,
                source="jsonld",
                raw=node,
            )
    return None
