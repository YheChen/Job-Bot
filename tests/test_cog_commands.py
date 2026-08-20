"""Exercise the /jobs command handlers themselves.

These exist because a real production break slipped through: the
settings_service.set_locations signature changed to return a tuple, the cog
was not updated to match, and every existing test called the service directly
— so nothing caught it. The handler crashed formatting its reply and Discord
showed only "The application didn't respond".

Handlers are invoked through `.callback` with a stubbed interaction, against a
real in-memory database. No Discord connection involved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from jobbot.bot.cogs.jobs import JobsCog
from jobbot.db.base import Base
from jobbot.db.session import dispose_engine, init_engine, session_scope


@pytest.fixture
async def db():
    engine = init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture
def cog() -> JobsCog:
    bot = MagicMock()
    bot.manager_role_ids = set()
    return JobsCog(bot)


@pytest.fixture
def interaction() -> MagicMock:
    """An admin invoking a command in a guild."""
    member = MagicMock(spec=discord.Member)
    member.guild_permissions.administrator = True
    member.roles = []
    member.id = 42

    inter = MagicMock(spec=discord.Interaction)
    inter.guild = MagicMock()
    inter.guild_id = 1
    inter.user = member
    inter.command = MagicMock(qualified_name="jobs set-locations")
    inter.response = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.is_done = MagicMock(return_value=False)
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()
    return inter


def _reply(interaction) -> str:
    assert interaction.response.send_message.await_count == 1, "handler must reply exactly once"
    return interaction.response.send_message.await_args.args[0]


# --- set-locations: the command that broke in production ------------------ #
async def test_set_locations_replies_and_persists(db, cog, interaction):
    await JobsCog.set_locations.callback(
        cog, interaction, locations="Bay Area, Toronto, Seattle", required=True
    )

    reply = _reply(interaction)
    assert "Bay Area" in reply and "Toronto" in reply and "Seattle" in reply
    assert "ONLY these locations" in reply

    from jobbot.db import repositories as repo

    async with session_scope() as session:
        settings = await repo.get_or_create_settings(session, 1)
        assert settings.locations == ["Bay Area", "Toronto", "Seattle"]
        assert settings.require_location is True


async def test_set_locations_without_required_is_a_bonus(db, cog, interaction):
    await JobsCog.set_locations.callback(cog, interaction, locations="Toronto")
    assert "ranked higher" in _reply(interaction)

    from jobbot.db import repositories as repo

    async with session_scope() as session:
        assert (await repo.get_or_create_settings(session, 1)).require_location is False


async def test_set_locations_can_be_turned_back_off(db, cog, interaction):
    await JobsCog.set_locations.callback(cog, interaction, locations="Toronto", required=True)
    interaction.response.send_message.reset_mock()
    await JobsCog.set_locations.callback(cog, interaction, locations="Toronto", required=False)
    assert "ranked higher" in _reply(interaction)


async def test_non_admin_is_refused_and_nothing_is_written(db, cog, interaction):
    interaction.user.guild_permissions.administrator = False
    await JobsCog.set_locations.callback(cog, interaction, locations="Toronto", required=True)
    assert "Administrator" in _reply(interaction)

    from jobbot.db import repositories as repo

    async with session_scope() as session:
        assert (await repo.get_or_create_settings(session, 1)).locations == []


# --- the sibling command with the same tuple-return shape ----------------- #
async def test_set_platform_priority_replies_and_persists(db, cog, interaction):
    await JobsCog.set_platform_priority.callback(
        cog, interaction, preferred="ashby,greenhouse", deprioritized="workday"
    )
    reply = _reply(interaction)
    assert "ashby" in reply and "workday" in reply

    from jobbot.db import repositories as repo

    async with session_scope() as session:
        settings = await repo.get_or_create_settings(session, 1)
        assert settings.preferred_platforms == ["ashby", "greenhouse"]
        assert settings.deprioritized_platforms == ["workday"]


# --- every set-* handler must survive a round trip ------------------------ #
@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("set_locations", {"locations": "Toronto"}),
        ("set_terms", {"terms": "Summer 2027"}),
        ("set_keywords", {"keywords": "rust"}),
        ("set_negative_keywords", {"keywords": "sales"}),
        ("set_min_score", {"score": 0.7}),
        ("set_platform_priority", {"preferred": "ashby"}),
        ("add_company_domain", {"domain": "careers.acme.com"}),
        ("enable_platform", {"slug": "ashby"}),
        ("disable_platform", {"slug": "workday"}),
    ],
)
async def test_setting_handlers_reply_without_raising(db, cog, interaction, name, kwargs):
    """Catches service/cog signature drift across the whole family."""
    await getattr(JobsCog, name).callback(cog, interaction, **kwargs)
    assert isinstance(_reply(interaction), str)


# --- error handler -------------------------------------------------------- #
async def test_error_handler_reports_instead_of_timing_out(cog, interaction):
    await cog.cog_app_command_error(interaction, RuntimeError("boom"))
    assert "failed" in _reply(interaction).lower()


async def test_error_handler_uses_followup_when_already_responded(cog, interaction):
    interaction.response.is_done = MagicMock(return_value=True)
    await cog.cog_app_command_error(interaction, RuntimeError("boom"))
    interaction.followup.send.assert_awaited_once()


async def test_error_handler_survives_an_expired_interaction(cog, interaction):
    interaction.response.send_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "unknown interaction")
    )
    await cog.cog_app_command_error(interaction, RuntimeError("boom"))  # must not raise
