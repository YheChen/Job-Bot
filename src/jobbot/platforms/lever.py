from __future__ import annotations

import re
from urllib.parse import urlsplit

from jobbot.platforms.base import PlatformAdapter

# https://jobs.lever.co/{org}/{uuid}
_ID_RE = re.compile(r"/[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


class LeverAdapter(PlatformAdapter):
    slug = "lever"
    name = "Lever"
    domains = ("jobs.lever.co",)

    def extract_job_id(self, url: str) -> str | None:
        m = _ID_RE.search(url)
        return m.group(1) if m else super().extract_job_id(url)

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        return parts[0].replace("-", " ").title() if parts else None
