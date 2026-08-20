#!/usr/bin/env python3
"""Retire stored jobs that fall outside the configured locations.

`/jobs set-locations ... required:True` filters at *ingest*, so it only
affects jobs discovered from that point on. Anything already in the database
from before stays there — and keeps appearing in Discord and in the published
listing. This retires them.

Uses the same `match_location` the live filter uses, so a job is judged here
exactly as it would be during a scan; there is no second copy of the rules to
drift out of sync.

Dry run by default. Nothing is modified unless you pass --apply.

    python scripts/purge_locations.py                 # report only
    python scripts/purge_locations.py --apply         # mark them closed
    python scripts/purge_locations.py --apply --hard-delete
    python scripts/purge_locations.py --locations "Bay Area,Toronto"

Order matters: enable the filter *before* purging, or the next scan will
re-ingest everything you just removed.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys

from sqlalchemy import delete, select

from jobbot.config import get_settings
from jobbot.db import repositories as repo
from jobbot.db.models import Job, JobStatus
from jobbot.db.session import dispose_engine, get_sessionmaker, init_engine
from jobbot.scoring.locations import match_location

BAR = "─" * 74


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


async def _configured_locations() -> tuple[list[str], bool]:
    """Read locations from guild settings, so the script and bot agree."""
    maker = get_sessionmaker()
    async with maker() as session:
        rows = await repo.all_active_guild_settings(session)
    for row in rows:
        if row.locations:
            return list(row.locations), bool(row.require_location)
    return [], False


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locations", help="Override the configured locations (comma-separated)")
    parser.add_argument("--apply", action="store_true", help="Actually modify the database")
    parser.add_argument(
        "--hard-delete",
        action="store_true",
        help="DELETE rows instead of marking them closed (irreversible)",
    )
    parser.add_argument("--limit-preview", type=int, default=15)
    args = parser.parse_args()

    settings = get_settings()
    init_engine(str(settings.database_url))
    try:
        if args.locations:
            locations, required = _csv(args.locations), True
            source = "--locations"
        else:
            locations, required = await _configured_locations()
            source = "guild settings"

        print(BAR)
        print("jobbot location purge" + ("" if args.apply else "  (DRY RUN — nothing modified)"))
        print(BAR)
        print(f"locations ({source}): {', '.join(locations) or '(none)'}")
        print(f"require_location currently: {required}")

        if not locations:
            print("\nNo locations configured. Run /jobs set-locations first, or pass --locations.")
            return 1
        if not required and not args.locations:
            print(
                "\nWARNING: require_location is FALSE, so the bot is not filtering at ingest.\n"
                "         Purged jobs will come straight back on the next scan.\n"
                '         Run: /jobs set-locations locations:"..." required:True'
            )

        maker = get_sessionmaker()
        async with maker() as session:
            jobs = list(
                (await session.execute(select(Job).where(Job.status == JobStatus.active))).scalars()
            )

        keep, purge = [], []
        for job in jobs:
            hint = job.location or job.title
            (keep if match_location(hint, locations) else purge).append(job)

        print(f"\nactive jobs: {len(jobs)}    keep: {len(keep)}    purge: {len(purge)}")
        if not purge:
            print("Nothing to do.")
            return 0

        counts = collections.Counter((j.location or "(no location)") for j in purge)
        print("\nlocations being removed:")
        for loc, n in counts.most_common(args.limit_preview):
            print(f"   {n:4d}  {loc[:58]}")
        if len(counts) > args.limit_preview:
            print(f"   ... and {len(counts) - args.limit_preview} more distinct locations")

        if not args.apply:
            print(f"\n{BAR}\nDry run. Re-run with --apply to act on these {len(purge)} jobs.")
            return 0

        ids = [j.id for j in purge]
        async with maker() as session:
            if args.hard_delete:
                await session.execute(delete(Job).where(Job.id.in_(ids)))
                action = "deleted"
            else:
                # Closed jobs are excluded from both posting and publishing,
                # and re-discovery will not reopen them.
                for job in (await session.execute(select(Job).where(Job.id.in_(ids)))).scalars():
                    job.status = JobStatus.closed
                action = "marked closed"
            await session.commit()

        print(f"\n{BAR}\n{len(ids)} jobs {action}.")
        print("Run /jobs scan to regenerate the published listing.")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
