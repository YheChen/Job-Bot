#!/usr/bin/env python3
"""Dry run: show what jobbot *would* post, touching nothing.

No database writes, no Discord connection, no state changes. Runs the real
listing source and the real relevance scorer, then prints the jobs that would
be delivered plus why the rest were rejected.

Needs no DISCORD_TOKEN and no database. Search queries are skipped by default
so no search-API quota is spent; pass --queries N to actually run N of them
(requires SERPER_API_KEY).

Examples
--------
    python scripts/dry_run.py
    python scripts/dry_run.py --lookback-days 3 --min-score 0.65
    python scripts/dry_run.py --locations "Toronto,Waterloo,Remote" \
                              --terms "Summer 2027,Winter 2027"
    python scripts/dry_run.py --queries 2          # spends 2 search credits
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

import httpx

from jobbot.parsing.models import ExtractedJob
from jobbot.platforms.registry import PlatformRegistry
from jobbot.queries.generator import build_queries, select_batch
from jobbot.scoring.relevance import score_job
from jobbot.sources.github_listings import DEFAULT_CATEGORIES, GitHubListingsSource

BAR = "─" * 78


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _fmt_row(job: ExtractedJob, score: float) -> str:
    title = (job.title or "?")[:46]
    company = (job.company or "?")[:18]
    term = job.internship_term or "-"
    platform = job.platform_slug or "-"
    return f"  {score:.2f}  {title:<46}  {company:<18}  {term:<12}  {platform}"


async def _gather_listings(args) -> list[ExtractedJob]:
    registry = PlatformRegistry.default()
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        source = GitHubListingsSource(
            client,
            categories=DEFAULT_CATEGORIES,
            lookback_days=args.lookback_days,
            registry=registry,
        )
        return await source.fetch()


async def _gather_search(args) -> list[ExtractedJob]:
    """Optionally run a few real search queries (spends quota)."""
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        print("  ! --queries requested but SERPER_API_KEY is unset; skipping search.\n")
        return []

    from jobbot.parsing.extractor import JobExtractor
    from jobbot.parsing.fetcher import PageFetcher
    from jobbot.search.serper import SerperProvider

    registry = PlatformRegistry.default()
    batch = select_batch(build_queries(), args.queries, rotation=0)
    jobs: list[ExtractedJob] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        provider = SerperProvider(api_key, client)
        extractor = JobExtractor(registry, PageFetcher(client), fetch_pages=True)
        for gq in batch:
            print(f"  query: {gq.text}")
            try:
                results = await provider.search(gq.text, results_per_page=10)
            except Exception as exc:  # noqa: BLE001
                print(f"    ! failed: {exc}")
                continue
            for result in results:
                job = await extractor.extract(result)
                if job is not None:
                    jobs.append(job)
    print()
    return jobs


async def main() -> int:
    parser = argparse.ArgumentParser(description="Show what jobbot would post.")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-score", type=float, default=0.55)
    parser.add_argument("--locations", default="Toronto,Waterloo,Vancouver,Remote,Canada")
    parser.add_argument("--terms", default="Summer 2027,Winter 2027,Fall 2026,Summer 2026")
    parser.add_argument("--limit", type=int, default=25, help="rows to print")
    parser.add_argument(
        "--queries",
        type=int,
        default=0,
        help="also run N real search queries (spends API quota)",
    )
    args = parser.parse_args()

    locations = _csv(args.locations)
    terms = _csv(args.terms)

    print(BAR)
    print("jobbot DRY RUN — nothing is written to the database or sent to Discord")
    print(BAR)
    print(f"lookback: {args.lookback_days}d   min-score: {args.min_score}")
    print(f"locations: {', '.join(locations) or '(none)'}")
    print(f"terms:     {', '.join(terms) or '(none)'}\n")

    print("Fetching SimplifyJobs listings feed...")
    candidates = await _gather_listings(args)
    print(f"  {len(candidates)} candidates after active/category/lookback filters\n")

    if args.queries > 0:
        print(f"Running {args.queries} live search quer{'y' if args.queries == 1 else 'ies'}...")
        candidates += await _gather_search(args)

    if not candidates:
        print("No candidates. Try a larger --lookback-days.")
        return 0

    would_post: list[tuple[ExtractedJob, float]] = []
    rejected: Counter[str] = Counter()
    for job in candidates:
        result = score_job(
            job,
            min_score=args.min_score,
            preferred_locations=locations,
            preferred_terms=terms,
        )
        if result.is_relevant:
            would_post.append((job, result.score))
        elif not result.is_internship:
            rejected["no internship indicator"] += 1
        elif not result.is_software:
            rejected["not software-related"] += 1
        elif result.negatives:
            rejected[f"negative keyword ({result.negatives[0]})"] += 1
        else:
            rejected[f"below min-score {args.min_score}"] += 1

    would_post.sort(key=lambda pair: -pair[1])

    print(BAR)
    print(f"WOULD POST: {len(would_post)} of {len(candidates)} candidates")
    print(BAR)
    print(f"  {'score':<5} {'title':<46}  {'company':<18}  {'term':<12}  platform")
    for job, score in would_post[: args.limit]:
        print(_fmt_row(job, score))
    if len(would_post) > args.limit:
        print(f"  ... and {len(would_post) - args.limit} more")

    print(f"\n{BAR}")
    print("FILTERED OUT")
    print(BAR)
    for reason, count in rejected.most_common():
        print(f"  {count:5d}  {reason}")

    # Discord delivery is capped per scan, so a large backlog trickles out.
    per_scan = 25
    if len(would_post) > per_scan:
        scans = -(-len(would_post) // per_scan)
        print(f"\nNote: delivery is capped at {per_scan}/scan → ~{scans} scans to drain.")
        print("      Lower --lookback-days (and GITHUB_LISTINGS_LOOKBACK_DAYS) to start small.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
