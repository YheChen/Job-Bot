"""Render discovered jobs as a Markdown listing table.

Output mirrors the SimplifyJobs-style internship READMEs: one row per open
role with a direct application link.

Everything here is pure so it can be unit-tested without network or database.

Cell contents come from third-party feeds and scraped pages, and the result is
published to a public repository — so every value is escaped. Pipes would break
the table, newlines would split rows, and raw brackets/HTML could inject markup
into someone else's README.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime

# Marker used to detect whether the *content* changed, independent of the
# "last updated" line — otherwise every run would produce a commit.
# When these markers are present in the target file, only the region between
# them is replaced. That lets the listing live inside a hand-written README
# (GitHub only renders a file named README, so the table has to go there) while
# leaving every other line under human control.
SECTION_BEGIN = "<!-- jobbot:begin -->"
SECTION_END = "<!-- jobbot:end -->"

_HASH_MARKER = "<!-- jobbot:content-hash="
_HASH_RE = re.compile(re.escape(_HASH_MARKER) + r"([0-9a-f]{8,64})\s*-->")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MD_SPECIALS = re.compile(r"([\\`*_\[\]])")

_MAX_CELL = 120


def escape_cell(value: str | None, limit: int = _MAX_CELL) -> str:
    """Make an arbitrary string safe inside a Markdown table cell."""
    if not value:
        return "—"
    text = _CONTROL_CHARS.sub(" ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "—"
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    # Angle brackets become entities so no raw HTML can reach the rendered
    # page; the remaining Markdown specials are backslash-escaped; pipes last
    # because an unescaped one would split the table row.
    text = _MD_SPECIALS.sub(r"\\\1", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|")


def safe_url(url: str | None) -> str | None:
    """Only http(s) URLs are emitted as links."""
    if not url:
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    if any(ch in url for ch in " \n\r\t<>"):
        return None
    return url


def _age(job) -> str:
    # Accepts either a DB Job (posted_at/first_seen_at) or an ExtractedJob
    # (posting_date), so the renderer works before persistence too.
    stamp = (
        getattr(job, "posted_at", None)
        or getattr(job, "posting_date", None)
        or getattr(job, "first_seen_at", None)
    )
    if stamp is None:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    days = max(0, (datetime.now(UTC) - stamp).days)
    return "today" if days == 0 else f"{days}d"


def _row(job) -> str:
    url = safe_url(getattr(job, "canonical_url", None))
    apply_cell = f"[Apply](<{url}>)" if url else "—"
    return (
        f"| {escape_cell(getattr(job, 'company', None), 60)} "
        f"| {escape_cell(getattr(job, 'title', None))} "
        f"| {escape_cell(getattr(job, 'location', None), 60)} "
        f"| {escape_cell(getattr(job, 'internship_term', None), 30)} "
        f"| {apply_cell} "
        f"| {_age(job)} |"
    )


def content_hash(jobs: Sequence) -> str:
    """Digest of the rendered rows only — excludes the timestamp."""
    basis = "\n".join(_row(job) for job in jobs)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def extract_content_hash(markdown: str | None) -> str | None:
    """Read the marker back out of a previously published document."""
    if not markdown:
        return None
    match = _HASH_RE.search(markdown)
    return match.group(1) if match else None


def render_readme(
    jobs: Sequence,
    *,
    title: str = "Software Engineering Internships",
    generated_at: datetime | None = None,
    footer: str | None = None,
) -> str:
    """Render the full document. Deterministic apart from the timestamp."""
    generated_at = generated_at or datetime.now(UTC)
    digest = content_hash(jobs)

    lines = [
        f"# {title}",
        "",
        f"{_HASH_MARKER}{digest} -->",
        f"_{len(jobs)} open role{'' if len(jobs) == 1 else 's'} · "
        f"last updated {generated_at.strftime('%Y-%m-%d %H:%M')} UTC_",
        "",
    ]

    if not jobs:
        lines.append("_No open roles right now._")
    else:
        lines += [
            "| Company | Role | Location | Term | Application | Age |",
            "| --- | --- | --- | --- | --- | --- |",
            *(_row(job) for job in jobs),
        ]

    # `footer or default` would ignore an explicit "" — a caller embedding this
    # table inside a larger document needs a way to suppress the footer.
    if footer is None:
        footer = "_Generated automatically by [jobbot](https://github.com/YheChen/Job-Bot)._"
    lines += ["", footer, ""] if footer else [""]
    return "\n".join(lines)


def render_section(
    jobs: Sequence,
    *,
    title: str | None = "Software Engineering Internships",
    generated_at: datetime | None = None,
) -> str:
    """Render just the managed block, wrapped in the section markers."""
    body = render_readme(jobs, title=title or "", generated_at=generated_at, footer="")
    if not title:
        # Drop the leading "# " heading when the host document supplies its own.
        body = "\n".join(body.splitlines()[2:])
    return f"{SECTION_BEGIN}\n{body.strip()}\n{SECTION_END}"


def merge_section(existing: str | None, section: str) -> str:
    """Splice `section` into `existing` between the markers.

    Falls back to returning the section alone when the target has no markers,
    which is the standalone-file case (e.g. a dedicated LISTINGS.md).
    """
    if not existing or SECTION_BEGIN not in existing or SECTION_END not in existing:
        return section

    start = existing.index(SECTION_BEGIN)
    end = existing.index(SECTION_END) + len(SECTION_END)
    if end <= start:  # markers out of order; refuse to mangle the file
        return existing
    return existing[:start] + section + existing[end:]
