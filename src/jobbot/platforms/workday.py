from __future__ import annotations

import re
from urllib.parse import urlsplit

from jobbot.platforms.base import PlatformAdapter

# {tenant}.{dc}.myworkdayjobs.com/{site}/job/{location}/{title}_{REQID}
# The same requisition can appear under multiple tenant/site URLs, so the
# requisition id is the strongest dedup signal for Workday.
_REQ_RE = re.compile(r"_((?:R|JR|REQ)[-_]?\d+)", re.IGNORECASE)


class WorkdayAdapter(PlatformAdapter):
    slug = "workday"
    name = "Workday"
    domains = ("myworkdayjobs.com", "workdayjobs.com")

    def extract_job_id(self, url: str) -> str | None:
        m = _REQ_RE.search(url)
        if m:
            return m.group(1).upper().replace("_", "-")
        return super().extract_job_id(url)

    @staticmethod
    def _company_from_url(url: str) -> str | None:
        host = urlsplit(url).hostname or ""
        # tenant is the first label of the host
        tenant = host.split(".")[0]
        return tenant.replace("-", " ").title() if tenant else None
