"""Persistent action buttons attached to each job embed.

Uses discord.py persistent views (custom_id encodes the job id) so buttons keep
working across bot restarts.
"""

from __future__ import annotations

import discord

from jobbot.db.models import FeedbackKind
from jobbot.services import job_service


class JobActionView(discord.ui.View):
    def __init__(self, job_id: int, apply_url: str, careers_url: str | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Apply", style=discord.ButtonStyle.link, url=apply_url, emoji="🔗"
            )
        )
        if careers_url:
            self.add_item(
                discord.ui.Button(
                    label="Careers", style=discord.ButtonStyle.link, url=careers_url
                )
            )
        # Stateful buttons need custom_ids that embed the job id.
        self._add_feedback_buttons(job_id)

    def _add_feedback_buttons(self, job_id: int) -> None:
        self.add_item(_FeedbackButton("Irrelevant", "❌", job_id, FeedbackKind.irrelevant))
        self.add_item(_FeedbackButton("Duplicate", "🔁", job_id, FeedbackKind.duplicate))
        self.add_item(_SaveButton(job_id))
        self.add_item(_HideCompanyButton(job_id))


class _FeedbackButton(discord.ui.Button):
    def __init__(self, label: str, emoji: str, job_id: int, kind: FeedbackKind) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"jobfb:{kind.value}:{job_id}",
        )
        self._job_id = job_id
        self._kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        await job_service.add_feedback(
            self._job_id,
            interaction.guild_id,
            interaction.user.id,
            self._kind,
        )
        await interaction.response.send_message(
            f"Marked as {self._kind.value}. Thanks!", ephemeral=True
        )


class _SaveButton(discord.ui.Button):
    def __init__(self, job_id: int) -> None:
        super().__init__(
            label="Save",
            emoji="⭐",
            style=discord.ButtonStyle.primary,
            custom_id=f"jobsave:{job_id}",
        )
        self._job_id = job_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await job_service.save_job(interaction.user.id, self._job_id)
        await interaction.response.send_message("Saved to your list.", ephemeral=True)


class _HideCompanyButton(discord.ui.Button):
    def __init__(self, job_id: int) -> None:
        super().__init__(
            label="Hide company",
            emoji="🚫",
            style=discord.ButtonStyle.secondary,
            custom_id=f"jobhide:{job_id}",
        )
        self._job_id = job_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        company = await job_service.hide_company(interaction.guild_id, self._job_id)
        msg = f"Hiding future postings from **{company}**." if company else "Could not hide."
        await interaction.response.send_message(msg, ephemeral=True)
