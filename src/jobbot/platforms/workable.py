from __future__ import annotations

import re
from urllib.parse import urlsplit

from jobbot.platforms.base import PlatformAdapter

# apply.workable.com/{company}/j/{TOKEN}/
_ID_RE = re.compile(r"/j/([0-9A-F]{6,})", re.IGNORECASE)


class WorkableAdapter(PlatformAdapter):
    slug = "workable"
    name = "Workable"
    domains = ("apply.workable.com",)

    def extract_job_id(self, url: str) -> str | None:
        m = _ID_RE.search(url)
        return m.group(1).upper() if m else super().extract_job_id(url)

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        return parts[0].replace("-", " ").title() if parts else None
