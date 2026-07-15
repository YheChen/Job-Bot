"""Permission checks for admin/config commands."""

from __future__ import annotations

import discord
from discord import app_commands


def is_manager(interaction: discord.Interaction, manager_role_ids: set[int]) -> bool:
    if interaction.guild is None:
        return False
    member = interaction.user
    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator:
            return True
        if manager_role_ids and any(r.id in manager_role_ids for r in member.roles):
            return True
    return False


def require_manager(manager_role_ids: set[int]):
    """app_commands check factory restricting a command to admins/manager roles."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if is_manager(interaction, manager_role_ids):
            return True
        raise app_commands.CheckFailure(
            "You need the Administrator permission or a bot-manager role to do that."
        )

    return app_commands.check(predicate)
