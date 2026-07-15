from __future__ import annotations

import re
from urllib.parse import urlsplit

from jobbot.platforms.base import PlatformAdapter

# boards.greenhouse.io/{org}/jobs/{id}  |  job-boards.greenhouse.io/{org}/jobs/{id}
_ID_RE = re.compile(r"/jobs/(\d+)")


class GreenhouseAdapter(PlatformAdapter):
    slug = "greenhouse"
    name = "Greenhouse"
    domains = ("boards.greenhouse.io", "job-boards.greenhouse.io")

    def extract_job_id(self, url: str) -> str | None:
        m = _ID_RE.search(url)
        if m:
            return m.group(1)
        # embedded form: ?gh_jid=12345
        split = urlsplit(url)
        for pair in split.query.split("&"):
            if pair.startswith("gh_jid="):
                return pair.split("=", 1)[1]
        return super().extract_job_id(url)

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        return parts[0].replace("-", " ").title() if parts else None
