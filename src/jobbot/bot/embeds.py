"""Discord embed builders."""

from __future__ import annotations

from jobbot.db.models import Job
from jobbot.queries.terms import PLATFORMS

try:  # discord is optional at import time for unit tests
    import discord
except ImportError:  # pragma: no cover
    discord = None  # type: ignore


def _platform_display(slug: str | None) -> str:
    if not slug:
        return "Unknown"
    entry = PLATFORMS.get(slug)
    return entry[0] if entry else slug.title()


def job_embed(job: Job) -> discord.Embed:
    title = job.title or "(untitled)"
    if job.internship_term and job.internship_term not in title:
        title = f"{title}, {job.internship_term}"

    embed = discord.Embed(
        title=title[:256],
        url=job.canonical_url,
        color=0x2ECC71 if job.relevance_score >= 0.75 else 0x3498DB,
    )
    if job.company:
        embed.add_field(name="Company", value=job.company[:1024], inline=True)
    if job.location:
        embed.add_field(name="Location", value=job.location[:1024], inline=True)
    if job.internship_term:
        embed.add_field(name="Term", value=job.internship_term, inline=True)
    embed.add_field(name="Platform", value=_platform_display(job.platform_slug), inline=True)
    if job.remote_status:
        embed.add_field(name="Remote", value=job.remote_status, inline=True)

    if job.posted_at:
        embed.add_field(
            name="Posted",
            value=f"<t:{int(job.posted_at.timestamp())}:D>",
            inline=True,
        )
    if job.first_seen_at:
        embed.add_field(
            name="Discovered",
            value=f"<t:{int(job.first_seen_at.timestamp())}:R>",
            inline=True,
        )

    embed.add_field(
        name="Relevance", value=f"{job.relevance_score:.2f}", inline=True
    )
    if job.matched_keywords:
        embed.add_field(
            name="Match",
            value=", ".join(job.matched_keywords[:8])[:1024],
            inline=False,
        )
    if job.description:
        embed.description = job.description[:500]

    embed.set_footer(text=f"jobbot • id {job.id}")
    return embed


def digest_embed(title: str, jobs: list[Job]) -> discord.Embed:
    embed = discord.Embed(title=title, color=0x9B59B6)
    for job in jobs[:25]:
        name = (job.title or "(untitled)")[:200]
        company = job.company or "?"
        value = f"[{company} — {job.location or 'N/A'}]({job.canonical_url}) • {job.relevance_score:.2f}"
        embed.add_field(name=name, value=value[:1024], inline=False)
    if len(jobs) > 25:
        embed.set_footer(text=f"+{len(jobs) - 25} more")
    return embed
