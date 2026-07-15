from __future__ import annotations

import re
from urllib.parse import urlsplit

from jobbot.platforms.base import PlatformAdapter

# jobs.smartrecruiters.com/{Company}/{id}-{slug}
_ID_RE = re.compile(r"/[^/]+/(\d{6,})")


class SmartRecruitersAdapter(PlatformAdapter):
    slug = "smartrecruiters"
    name = "SmartRecruiters"
    domains = ("jobs.smartrecruiters.com", "careers.smartrecruiters.com")

    def extract_job_id(self, url: str) -> str | None:
        m = _ID_RE.search(url)
        return m.group(1) if m else super().extract_job_id(url)

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        return parts[0].replace("-", " ").title() if parts else None
