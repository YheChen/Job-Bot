"""Discord client — wires cogs, the poster callback, and lifecycle hooks."""

from __future__ import annotations

import discord
from discord.ext import commands

from jobbot.bot.embeds import job_embed
from jobbot.bot.views import JobActionView
from jobbot.config import Settings
from jobbot.db import repositories as repo
from jobbot.db.session import session_scope
from jobbot.logging import get_logger
from jobbot.services import job_service

log = get_logger(__name__)


class JobBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # slash-command only; no message scraping
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.manager_role_ids: set[int] = set(settings.discord_manager_role_ids)
        self.scheduler_runner = None  # set by main after construction

    async def setup_hook(self) -> None:
        await self.load_extension("jobbot.bot.cogs.jobs")
        # Sync commands: per-guild for instant availability in dev, else global.
        if self.settings.discord_guild_ids:
            for gid in self.settings.discord_guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("commands_synced")

    async def on_ready(self) -> None:
        log.info("bot_ready", user=str(self.user), guilds=len(self.guilds))

    # ------------------------------------------------------------------ #
    # Poster callback used by ScanService.
    # ------------------------------------------------------------------ #
    async def post_jobs(self, guild_id: int, job_ids: list[int]) -> None:
        async with session_scope() as session:
            s = await repo.get_or_create_settings(session, guild_id)
            channel_id = s.post_channel_id

        if channel_id is None:
            log.warning("no_post_channel", guild_id=guild_id)
            return

        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if channel is None:
            log.warning("post_channel_missing", channel_id=channel_id)
            return

        jobs = await job_service.get_jobs(job_ids)
        message_ids: dict[int, int] = {}
        for job in jobs:
            try:
                view = JobActionView(job.id, job.canonical_url)
                msg = await channel.send(embed=job_embed(job), view=view)
                message_ids[job.id] = msg.id
            except discord.DiscordException as exc:
                log.error("post_failed", job_id=job.id, error=str(exc))

        await job_service.mark_posted(job_ids, message_ids)
        log.info("jobs_posted", guild_id=guild_id, count=len(message_ids))
