"""Publish the job listing to a file in a GitHub repository.

Uses the Contents API (one GET + one PUT) rather than cloning, so no git
binary, no working copy, and no credentials on disk.

Safety notes:
  * Writing to a repository is an outward-facing, public action, so this is
    OFF unless ENABLE_GITHUB_PUBLISH is set and a repo + token are configured.
  * The repo slug is validated against owner/name before being interpolated
    into the API path.
  * A commit is only made when the rendered rows actually changed, compared
    via a content-hash marker embedded in the file. Without that, an unchanged
    listing would still produce a commit on every scan because of the
    "last updated" line.
  * Failures are logged and swallowed: publishing must never abort a scan.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from jobbot.logging import get_logger
from jobbot.publishers.markdown import (
    SECTION_BEGIN,
    content_hash,
    extract_content_hash,
    merge_section,
    render_readme,
    render_section,
)

log = get_logger(__name__)

_API = "https://api.github.com"
# Each segment must contain an alphanumeric, so "." and ".." cannot slip
# through and walk up the API path.
_REPO_SEGMENT = r"[A-Za-z0-9_.-]*[A-Za-z0-9][A-Za-z0-9_.-]*"
_REPO_RE = re.compile(rf"^{_REPO_SEGMENT}/{_REPO_SEGMENT}$")
# Repo-relative only: no leading slash, no traversal, no spaces.
_PATH_RE = re.compile(rf"^{_REPO_SEGMENT}(?:/{_REPO_SEGMENT})*$")


class GitHubReadmePublisher:
    name = "github_readme"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        repo: str,
        path: str = "README.md",
        branch: str = "main",
        title: str = "Software Engineering Internships",
        commit_message: str = "chore: update internship listings",
    ) -> None:
        if not _REPO_RE.match(repo or ""):
            raise ValueError("github publish repo must look like 'owner/name'")
        if not _PATH_RE.match(path or "") or ".." in path:
            raise ValueError("github publish path must be a simple repo-relative path")
        if not token:
            raise ValueError("github publish token is required")
        self._client = client
        self._token = token
        self._repo = repo
        self._path = path
        self._branch = branch
        self._title = title
        self._commit_message = commit_message

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @property
    def _url(self) -> str:
        return f"{_API}/repos/{self._repo}/contents/{self._path}"

    async def _fetch_existing(self) -> tuple[str | None, str | None]:
        """Return (decoded_content, sha). Both None when the file is absent."""
        resp = await self._client.get(
            self._url, headers=self._headers, params={"ref": self._branch}
        )
        if resp.status_code == 404:
            return None, None
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"github GET {resp.status_code}: {resp.text[:200]}",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        raw = payload.get("content") or ""
        try:
            decoded = base64.b64decode(raw).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            decoded = ""
        return decoded, payload.get("sha")

    async def publish(self, jobs: Sequence) -> bool:
        try:
            existing, sha = await self._fetch_existing()
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            log.error("publish_fetch_failed", publisher=self.name, error=str(exc))
            return False

        desired_hash = content_hash(jobs)
        if extract_content_hash(existing) == desired_hash:
            log.info("publish_unchanged", publisher=self.name, jobs=len(jobs))
            return False

        # If the target already contains the section markers, replace only that
        # region and leave the rest of the document alone. This is what allows
        # the listing to live inside a hand-written README instead of
        # obliterating it — GitHub only renders a file named README, so the
        # table has to go there to appear on the repo landing page.
        now = datetime.now(UTC)
        if existing and SECTION_BEGIN in existing:
            body = merge_section(existing, render_section(jobs, title=None, generated_at=now))
        else:
            body = render_readme(jobs, title=self._title, generated_at=now)
        payload = {
            "message": f"{self._commit_message} ({len(jobs)} roles)",
            "content": base64.b64encode(body.encode()).decode(),
            "branch": self._branch,
        }
        if sha:
            payload["sha"] = sha  # required to update an existing file

        try:
            resp = await self._client.put(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as exc:
            log.error("publish_failed", publisher=self.name, error=str(exc))
            return False

        if resp.status_code >= 400:
            log.error(
                "publish_rejected",
                publisher=self.name,
                status=resp.status_code,
                error=resp.text[:200],
            )
            return False

        log.info("published", publisher=self.name, repo=self._repo, jobs=len(jobs))
        return True
