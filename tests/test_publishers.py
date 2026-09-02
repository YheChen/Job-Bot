from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from jobbot.publishers.github_readme import GitHubReadmePublisher
from jobbot.publishers.markdown import (
    SECTION_BEGIN,
    SECTION_END,
    content_hash,
    escape_cell,
    extract_content_hash,
    merge_section,
    render_readme,
    render_section,
    safe_url,
)

NOW = datetime.now(UTC)


@dataclass
class FakeJob:
    company: str | None = "Acme"
    title: str | None = "Software Engineer Intern"
    location: str | None = "Toronto, ON"
    internship_term: str | None = "Summer 2027"
    canonical_url: str | None = "https://jobs.ashbyhq.com/acme/1"
    posted_at: datetime | None = None
    first_seen_at: datetime | None = None


# --- cell escaping (published publicly; input is third-party) ------------- #
def test_pipes_are_escaped_so_rows_cannot_split():
    assert "\\|" in escape_cell("Backend | Frontend")


def test_newlines_are_collapsed():
    assert "\n" not in escape_cell("Software\nEngineer\r\nIntern")


def test_markdown_and_html_specials_are_escaped():
    out = escape_cell("<img src=x onerror=alert(1)> [link](evil) **bold**")
    # Angle brackets are entity-encoded, so no raw tag survives.
    assert "<" not in out and ">" not in out
    assert "&lt;img" in out
    # Markdown link/emphasis syntax is neutralized.
    assert "[link]" not in out
    assert "**bold**" not in out


def test_control_characters_removed():
    assert "\x00" not in escape_cell("Bad\x00Title\x07")


def test_empty_values_render_a_dash():
    assert escape_cell(None) == "—"
    assert escape_cell("   ") == "—"


def test_long_values_are_truncated():
    assert len(escape_cell("x" * 500)) < 200


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,x", "ftp://x/y", None, "", "http://a b"]
)
def test_unsafe_urls_are_rejected(url):
    assert safe_url(url) is None


def test_http_urls_pass():
    assert safe_url("https://jobs.lever.co/a/1") == "https://jobs.lever.co/a/1"


# --- rendering ------------------------------------------------------------ #
def test_render_contains_table_and_link():
    md = render_readme([FakeJob()])
    assert "| Company | Role | Location | Term | Application | Age |" in md
    assert "[Apply](<https://jobs.ashbyhq.com/acme/1>)" in md
    assert "Acme" in md


def test_render_handles_empty_list():
    md = render_readme([])
    assert "No open roles" in md
    assert "0 open roles" in md


def test_job_without_url_still_renders():
    md = render_readme([FakeJob(canonical_url=None)])
    assert "Apply" not in md.split("| --- |")[-1]


def test_age_column():
    md = render_readme([FakeJob(posted_at=NOW - timedelta(days=3))])
    assert "3d" in md
    assert "today" in render_readme([FakeJob(posted_at=NOW)])


def test_row_count_in_header():
    assert "2 open roles" in render_readme([FakeJob(), FakeJob(title="Backend Intern")])
    assert "1 open role ·" in render_readme([FakeJob()])


# --- content hash (change detection) -------------------------------------- #
def test_hash_marker_roundtrips():
    md = render_readme([FakeJob()])
    assert extract_content_hash(md) == content_hash([FakeJob()])


def test_hash_ignores_the_timestamp():
    """Regression guard: without this every scan would produce a commit."""
    early = render_readme([FakeJob()], generated_at=datetime(2026, 1, 1, tzinfo=UTC))
    late = render_readme([FakeJob()], generated_at=datetime(2026, 6, 1, tzinfo=UTC))
    assert early != late  # timestamps differ
    assert extract_content_hash(early) == extract_content_hash(late)


def test_hash_changes_when_jobs_change():
    assert content_hash([FakeJob()]) != content_hash([FakeJob(title="Backend Intern")])


def test_extract_hash_missing_marker():
    assert extract_content_hash("# plain readme") is None
    assert extract_content_hash(None) is None


# --- publisher validation ------------------------------------------------- #
@pytest.mark.parametrize("repo", ["", "noslash", "a/b/c", "../evil", "owner/name;rm"])
def test_invalid_repo_rejected(repo):
    with pytest.raises(ValueError, match="owner/name"):
        GitHubReadmePublisher(httpx.AsyncClient(), token="t", repo=repo)


@pytest.mark.parametrize("path", ["../../etc/passwd", "/abs/path", "a b.md", ""])
def test_invalid_path_rejected(path):
    with pytest.raises(ValueError, match="path"):
        GitHubReadmePublisher(httpx.AsyncClient(), token="t", repo="o/n", path=path)


def test_missing_token_rejected():
    with pytest.raises(ValueError, match="token"):
        GitHubReadmePublisher(httpx.AsyncClient(), token="", repo="o/n")


# --- publisher behaviour -------------------------------------------------- #
def _existing_response(md: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"content": base64.b64encode(md.encode()).decode(), "sha": "abc123"},
    )


async def test_creates_file_when_absent():
    puts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        puts.append(json.loads(request.content))
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pub = GitHubReadmePublisher(client, token="t", repo="o/n")
        assert await pub.publish([FakeJob()]) is True

    assert len(puts) == 1
    assert "sha" not in puts[0], "creating a new file must not send a sha"
    assert "Acme" in base64.b64decode(puts[0]["content"]).decode()
    assert puts[0]["branch"] == "main"


async def test_updates_file_with_sha_when_changed():
    puts: list[dict] = []
    stale = render_readme([FakeJob(title="Old Role")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _existing_response(stale)
        puts.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GitHubReadmePublisher(client, token="t", repo="o/n").publish([FakeJob()])

    assert puts[0]["sha"] == "abc123", "updating an existing file requires its sha"


async def test_skips_commit_when_content_unchanged():
    """The whole point of the hash marker — no commit churn every 6 hours."""
    calls: list[str] = []
    current = render_readme([FakeJob()])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return _existing_response(current)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await GitHubReadmePublisher(client, token="t", repo="o/n").publish([FakeJob()]) is False
        )

    assert calls == ["GET"], "must not issue a PUT when nothing changed"


async def test_auth_failure_is_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await GitHubReadmePublisher(client, token="t", repo="o/n").publish([FakeJob()]) is False
        )


async def test_put_rejection_is_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(403, json={"message": "Resource not accessible"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await GitHubReadmePublisher(client, token="t", repo="o/n").publish([FakeJob()]) is False
        )


async def test_network_error_is_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await GitHubReadmePublisher(client, token="t", repo="o/n").publish([FakeJob()]) is False
        )


async def test_token_is_sent_as_bearer_and_not_in_the_url():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GitHubReadmePublisher(client, token="secret-token", repo="o/n").publish([])

    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(seen[0].url)


def test_age_accepts_extracted_job_shape():
    """ExtractedJob uses posting_date; the DB Job uses posted_at."""

    @dataclass
    class Extracted:
        company = "Acme"
        title = "SWE Intern"
        location = None
        internship_term = None
        canonical_url = None
        posting_date: datetime | None = None

    assert "2d" in render_readme([Extracted(posting_date=NOW - timedelta(days=2))])


# --- section markers: listing inside a hand-written README ---------------- #
def _doc(inner: str = "placeholder") -> str:
    return (
        "# My Project\n\nIntro paragraph.\n\n## Openings\n\n"
        f"{SECTION_BEGIN}\n{inner}\n{SECTION_END}\n\n---\n\n## Architecture\n\nDocs.\n"
    )


def test_merge_replaces_only_the_marked_region():
    out = merge_section(_doc(), render_section([FakeJob()], title=None))
    assert "# My Project" in out and "Intro paragraph." in out
    assert "## Architecture" in out and "Docs." in out
    assert "placeholder" not in out
    assert "Acme" in out


def test_merge_is_idempotent():
    once = merge_section(_doc(), render_section([FakeJob()], title=None))
    twice = merge_section(once, render_section([FakeJob()], title=None))
    assert once.count(SECTION_BEGIN) == 1
    assert twice.count(SECTION_BEGIN) == 1


def test_merge_keeps_content_before_and_after_exactly():
    out = merge_section(_doc(), render_section([FakeJob()], title=None))
    before, after = out.split(SECTION_BEGIN)[0], out.split(SECTION_END)[1]
    assert before == "# My Project\n\nIntro paragraph.\n\n## Openings\n\n"
    assert after == "\n\n---\n\n## Architecture\n\nDocs.\n"


def test_merge_without_markers_returns_the_standalone_document():
    """A dedicated LISTINGS.md has no markers and is replaced wholesale."""
    out = merge_section("# old file", render_section([FakeJob()]))
    assert "old file" not in out
    assert "Acme" in out


def test_merge_refuses_to_mangle_out_of_order_markers():
    broken = f"top\n{SECTION_END}\nmiddle\n{SECTION_BEGIN}\nbottom"
    assert merge_section(broken, render_section([FakeJob()], title=None)) == broken


def test_section_omits_the_heading_when_the_host_supplies_one():
    section = render_section([FakeJob()], title=None)
    assert not section.splitlines()[1].startswith("# ")


def test_section_hash_marker_survives_for_change_detection():
    out = merge_section(_doc(), render_section([FakeJob()], title=None))
    assert extract_content_hash(out) == content_hash([FakeJob()])


def test_explicit_empty_footer_is_honoured():
    """`footer or default` would have ignored this and injected the footer."""
    assert "Generated automatically" not in render_readme([FakeJob()], footer="")


async def test_publisher_splices_into_an_existing_readme():
    puts: list[dict] = []
    existing = _doc()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _existing_response(existing)
        puts.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pub = GitHubReadmePublisher(client, token="t", repo="o/n", path="README.md")
        assert await pub.publish([FakeJob()]) is True

    written = base64.b64decode(puts[0]["content"]).decode()
    assert "## Architecture" in written, "publisher must not destroy the rest of the README"
    assert "Acme" in written
