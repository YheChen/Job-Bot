from __future__ import annotations

from unittest.mock import MagicMock

import pytest

discord = pytest.importorskip("discord")

from jobbot.bot.permissions import is_manager  # noqa: E402


def _interaction(member) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.user = member
    return interaction


def test_administrator_is_manager():
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = True
    member.roles = []
    assert is_manager(_interaction(member), set())


def test_manager_role_grants_access():
    role = MagicMock()
    role.id = 42
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = False
    member.roles = [role]
    assert is_manager(_interaction(member), {42})


def test_regular_member_denied():
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = False
    member.roles = []
    assert not is_manager(_interaction(member), {42})


def test_no_guild_denied():
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = True
    member.roles = []
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = None
    interaction.user = member
    assert not is_manager(interaction, set())
