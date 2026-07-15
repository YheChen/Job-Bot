"""User-facing /jobs commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from jobbot.bot.embeds import job_embed
from jobbot.bot.permissions import is_manager
from jobbot.db.session import session_scope
from jobbot.logging import get_logger
from jobbot.queries.terms import PLATFORMS
from jobbot.services import job_service, settings_service

log = get_logger(__name__)


class JobsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    jobs = app_commands.Group(name="jobs", description="Internship discovery commands")

    # --- shared admin guard ------------------------------------------- #
    async def _ensure_manager(self, interaction: discord.Interaction) -> bool:
        role_ids = set(getattr(self.bot, "manager_role_ids", set()))
        if is_manager(interaction, role_ids):
            return True
        await interaction.response.send_message(
            "You need the Administrator permission or a bot-manager role.", ephemeral=True
        )
        return False

    @jobs.command(name="recent", description="Show recently discovered jobs")
    @app_commands.describe(hours="Look-back window in hours (default 24)")
    async def recent(self, interaction: discord.Interaction, hours: int = 24) -> None:
        await interaction.response.defer(thinking=True)
        jobs = await job_service.recent(hours=min(hours, 168), limit=10)
        if not jobs:
            await interaction.followup.send("No jobs found in that window.")
            return
        embeds = [job_embed(j) for j in jobs[:10]]
        await interaction.followup.send(embeds=embeds)

    @jobs.command(name="search", description="Search stored jobs by title/location/term")
    @app_commands.describe(title="Title contains", location="Location contains", term="Term")
    async def search(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        location: str | None = None,
        term: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        from sqlalchemy import select

        from jobbot.db.models import Job

        async with session_scope() as session:
            stmt = select(Job).order_by(Job.relevance_score.desc()).limit(10)
            if title:
                stmt = stmt.where(Job.title.ilike(f"%{title}%"))
            if location:
                stmt = stmt.where(Job.location.ilike(f"%{location}%"))
            if term:
                stmt = stmt.where(Job.internship_term.ilike(f"%{term}%"))
            rows = await session.execute(stmt)
            jobs = list(rows.scalars())

        if not jobs:
            await interaction.followup.send("No matching jobs.")
            return
        await interaction.followup.send(embeds=[job_embed(j) for j in jobs])

    @jobs.command(name="stats", description="Show discovery statistics")
    async def stats(self, interaction: discord.Interaction) -> None:
        s = await job_service.stats()
        embed = discord.Embed(title="Job discovery stats", color=0x3498DB)
        embed.add_field(name="Total", value=str(s["total"]))
        embed.add_field(name="Active", value=str(s["active"]))
        embed.add_field(name="Posted", value=str(s["posted"]))
        embed.add_field(name="Expired", value=str(s["expired"]))
        await interaction.response.send_message(embed=embed)

    @jobs.command(name="platforms", description="List supported ATS platforms")
    async def platforms(self, interaction: discord.Interaction) -> None:
        lines = [f"`{slug}` — {name} ({domain})" for slug, (name, domain) in PLATFORMS.items()]
        await interaction.response.send_message("\n".join(lines)[:1900])

    @jobs.command(name="queries", description="Show top search queries by yield")
    async def queries(self, interaction: discord.Interaction) -> None:
        from sqlalchemy import select

        from jobbot.db.models import SearchQuery

        async with session_scope() as session:
            rows = await session.execute(
                select(SearchQuery)
                .order_by(SearchQuery.relevant_results.desc(), SearchQuery.times_run.desc())
                .limit(15)
            )
            qs = list(rows.scalars())
        if not qs:
            await interaction.response.send_message("No queries run yet.", ephemeral=True)
            return
        lines = [
            f"`{q.times_run}x` rel={q.relevant_results} — {q.query_text[:90]}" for q in qs
        ]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @jobs.command(name="companies", description="Show top companies by discovered jobs")
    async def companies(self, interaction: discord.Interaction) -> None:
        from sqlalchemy import func, select

        from jobbot.db.models import Job

        async with session_scope() as session:
            rows = await session.execute(
                select(Job.company, func.count().label("n"))
                .where(Job.company.isnot(None))
                .group_by(Job.company)
                .order_by(func.count().desc())
                .limit(20)
            )
            data = list(rows)
        if not data:
            await interaction.response.send_message("No companies yet.", ephemeral=True)
            return
        lines = [f"`{n}` — {company}" for company, n in data]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @jobs.command(name="saved", description="Show jobs you saved")
    async def saved(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        jobs = await job_service.saved_jobs(interaction.user.id, limit=10)
        if not jobs:
            await interaction.followup.send("You have no saved jobs.", ephemeral=True)
            return
        await interaction.followup.send(
            embeds=[job_embed(j) for j in jobs], ephemeral=True
        )

    @jobs.command(name="status", description="Show bot / scan status")
    async def status(self, interaction: discord.Interaction) -> None:
        scheduler = getattr(self.bot, "scheduler_runner", None)
        running = scheduler.is_scan_running if scheduler else False
        s = await job_service.stats()
        embed = discord.Embed(title="jobbot status", color=0x2ECC71)
        embed.add_field(name="Scan running", value=str(running))
        embed.add_field(name="Jobs tracked", value=str(s["total"]))
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)))
        await interaction.response.send_message(embed=embed)

    @jobs.command(name="scan", description="Manually trigger a scan (admins)")
    @app_commands.describe(platform="Restrict to one platform slug (optional)")
    async def scan(
        self, interaction: discord.Interaction, platform: str | None = None
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        scheduler = getattr(self.bot, "scheduler_runner", None)
        if scheduler is None:
            await interaction.response.send_message("Scanner unavailable.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        report = await scheduler.trigger_scan(
            guild_id=interaction.guild_id,
            triggered_by=f"user:{interaction.user.id}",
            platform_filter=platform,
        )
        if not report.started:
            await interaction.followup.send(f"Scan not started: {report.reason}")
            return
        await interaction.followup.send(
            f"Scan complete — {report.queries_run} queries, "
            f"{report.results_found} results, {report.jobs_new} new, "
            f"{report.jobs_posted} posted."
        )


    # ================================================================== #
    # Admin / configuration commands (Administrator or bot-manager role)
    # ================================================================== #
    @jobs.command(name="set-channel", description="Set the channel jobs are posted to")
    @app_commands.describe(channel="Target channel", digest="Set the digest channel instead")
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        digest: bool = False,
    ) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.set_channel(interaction.guild_id, channel.id, digest=digest)
        kind = "digest" if digest else "post"
        await interaction.response.send_message(
            f"{kind.title()} channel set to {channel.mention}.", ephemeral=True
        )

    @jobs.command(name="set-interval", description="Set scan interval (hours)")
    async def set_interval(self, interaction: discord.Interaction, hours: float) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.set_interval(interaction.guild_id, hours)
        scheduler = getattr(self.bot, "scheduler_runner", None)
        if scheduler:
            scheduler.reschedule(interaction.guild_id, hours)
        await interaction.response.send_message(
            f"Scan interval set to {hours}h.", ephemeral=True
        )

    @jobs.command(name="set-locations", description="Comma-separated preferred locations")
    async def set_locations(self, interaction: discord.Interaction, locations: str) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.set_locations(interaction.guild_id, locations)
        await interaction.response.send_message(
            f"Locations set: {', '.join(vals)}", ephemeral=True
        )

    @jobs.command(name="set-terms", description="Comma-separated academic terms")
    async def set_terms(self, interaction: discord.Interaction, terms: str) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.set_terms(interaction.guild_id, terms)
        await interaction.response.send_message(
            f"Terms set: {', '.join(vals)}", ephemeral=True
        )

    @jobs.command(name="set-keywords", description="Comma-separated extra keywords")
    async def set_keywords(self, interaction: discord.Interaction, keywords: str) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.set_keywords(interaction.guild_id, keywords)
        await interaction.response.send_message(
            f"Extra keywords set: {', '.join(vals)}", ephemeral=True
        )

    @jobs.command(name="set-negative-keywords", description="Comma-separated negative keywords")
    async def set_negative_keywords(
        self, interaction: discord.Interaction, keywords: str
    ) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.set_negative_keywords(interaction.guild_id, keywords)
        await interaction.response.send_message(
            f"Negative keywords set: {', '.join(vals)}", ephemeral=True
        )

    @jobs.command(name="set-min-score", description="Set minimum relevance score (0-1)")
    async def set_min_score(self, interaction: discord.Interaction, score: float) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.set_min_score(interaction.guild_id, score)
        await interaction.response.send_message(
            f"Minimum score set to {max(0.0, min(1.0, score)):.2f}.", ephemeral=True
        )

    @jobs.command(name="enable-platform", description="Enable an ATS platform")
    async def enable_platform(self, interaction: discord.Interaction, slug: str) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.enable_platform(interaction.guild_id, slug)
        await interaction.response.send_message(f"Enabled `{slug}`.", ephemeral=True)

    @jobs.command(name="disable-platform", description="Disable an ATS platform")
    async def disable_platform(self, interaction: discord.Interaction, slug: str) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.disable_platform(interaction.guild_id, slug)
        await interaction.response.send_message(f"Disabled `{slug}`.", ephemeral=True)

    @jobs.command(name="add-company-domain", description="Track a company career domain")
    async def add_company_domain(
        self, interaction: discord.Interaction, domain: str
    ) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.add_company_domain(interaction.guild_id, domain)
        await interaction.response.send_message(
            f"Company domains: {', '.join(vals)}", ephemeral=True
        )

    @jobs.command(name="remove-company-domain", description="Stop tracking a company domain")
    async def remove_company_domain(
        self, interaction: discord.Interaction, domain: str
    ) -> None:
        if not await self._ensure_manager(interaction):
            return
        vals = await settings_service.remove_company_domain(interaction.guild_id, domain)
        await interaction.response.send_message(
            f"Company domains: {', '.join(vals) or '(none)'}", ephemeral=True
        )

    @jobs.command(name="pause", description="Pause scheduled scans for this server")
    async def pause(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.set_paused(interaction.guild_id, True)
        await interaction.response.send_message("Scans paused.", ephemeral=True)

    @jobs.command(name="resume", description="Resume scheduled scans for this server")
    async def resume(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_manager(interaction):
            return
        await settings_service.set_paused(interaction.guild_id, False)
        await interaction.response.send_message("Scans resumed.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JobsCog(bot))
