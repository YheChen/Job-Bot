"""Guild-settings mutations invoked by Discord admin commands."""

from __future__ import annotations

from jobbot.db import repositories as repo
from jobbot.db.session import session_scope


def _parse_list(value: str) -> list[str]:
    return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]


async def set_channel(guild_id: int, channel_id: int, digest: bool = False) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        if digest:
            s.digest_channel_id = channel_id
        else:
            s.post_channel_id = channel_id


async def set_interval(guild_id: int, hours: float) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.scan_interval_hours = max(0.5, hours)


async def set_locations(guild_id: int, locations: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.locations = _parse_list(locations)
        return s.locations


async def set_terms(guild_id: int, terms: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.academic_terms = _parse_list(terms)
        return s.academic_terms


async def set_keywords(guild_id: int, keywords: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.extra_keywords = _parse_list(keywords)
        return s.extra_keywords


async def set_negative_keywords(guild_id: int, keywords: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.negative_keywords = _parse_list(keywords)
        return s.negative_keywords


async def set_min_score(guild_id: int, score: float) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.min_score = max(0.0, min(1.0, score))


async def enable_platform(guild_id: int, slug: str) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.disabled_platforms = [p for p in (s.disabled_platforms or []) if p != slug]


async def disable_platform(guild_id: int, slug: str) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        disabled = set(s.disabled_platforms or [])
        disabled.add(slug)
        s.disabled_platforms = sorted(disabled)


async def add_company_domain(guild_id: int, domain: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        domains = set(s.company_domains or [])
        domains.add(domain.strip().lower())
        s.company_domains = sorted(domains)
        return s.company_domains


async def remove_company_domain(guild_id: int, domain: str) -> list[str]:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.company_domains = [d for d in (s.company_domains or []) if d != domain.strip().lower()]
        return s.company_domains


async def set_paused(guild_id: int, paused: bool) -> None:
    async with session_scope() as session:
        s = await repo.get_or_create_settings(session, guild_id)
        s.paused = paused
