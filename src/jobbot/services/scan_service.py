"""ScanService — the orchestrator that ties the whole pipeline together.

A scan:
  1. Acquires a Postgres advisory lock (mutual exclusion; only one scan at once).
  2. Selects a prioritized, rotated batch of queries.
  3. Runs each query through the SearchManager (provider fallback + quotas).
  4. Extracts, dedups, scores, and persists each result.
  5. Expiration-checks and posts the fresh, high-scoring jobs to Discord.

Network I/O is done outside DB transactions; the advisory lock is held on a
dedicated connection for the scan's duration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from jobbot.config import Settings
from jobbot.db import repositories as repo
from jobbot.db.models import (
    Job,
    JobStatus,
    JobVersion,
    ScanStatus,
)
from jobbot.db.session import get_sessionmaker
from jobbot.dedup import detector as dd
from jobbot.expiration.checker import ExpirationChecker
from jobbot.logging import get_logger
from jobbot.parsing.extractor import JobExtractor
from jobbot.parsing.fetcher import PageFetcher
from jobbot.parsing.term import parse_internship_term
from jobbot.platforms.registry import PlatformRegistry
from jobbot.publishers.github_readme import GitHubReadmePublisher
from jobbot.queries.generator import QueryGenConfig, build_queries, select_batch
from jobbot.scoring.relevance import ScoringPrefs, score_job
from jobbot.search.manager import SearchManager
from jobbot.services import job_service
from jobbot.sources.github_listings import GitHubListingsSource

log = get_logger(__name__)

# poster(guild_id, job_ids) -> {job_id: discord_message_id} for jobs actually
# delivered. Only those are marked posted, so a failed send is retried next
# scan instead of being silently dropped.
Poster = Callable[[int, list[int]], Awaitable[dict[int, int]]]


class ScanReport(BaseModel):
    started: bool
    reason: str | None = None
    queries_run: int = 0
    results_found: int = 0
    jobs_new: int = 0
    jobs_posted: int = 0
    listing_candidates: int = 0
    published: bool = False


class ScanService:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        poster: Poster | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._poster = poster
        self._scan_counter = 0
        # Cached across scans so listing sources keep their ETags.
        self._listing_sources: list | None = None
        self._publishers: list | None = None

    # ------------------------------------------------------------------ #
    def _build_components(
        self, company_domains: list[str]
    ) -> tuple[PlatformRegistry, JobExtractor, ExpirationChecker, SearchManager]:
        registry = PlatformRegistry.default(company_domains)
        fetcher = PageFetcher(
            self._client,
            allow_private_networks=self._settings.allow_private_networks,
            company_domains=set(company_domains),
        )
        extractor = JobExtractor(registry, fetcher, fetch_pages=True)
        expiration = ExpirationChecker(registry, fetcher)
        manager = SearchManager.from_settings(self._settings, self._client)
        return registry, extractor, expiration, manager

    def _build_listing_sources(self, registry: PlatformRegistry) -> list:
        """Curated feeds. Reuses the cached instances so ETags survive scans."""
        if not self._settings.enable_github_listings:
            return []
        if self._listing_sources is None:
            self._listing_sources = [
                GitHubListingsSource(
                    self._client,
                    url=self._settings.github_listings_url,
                    categories=self._settings.github_listings_categories,
                    lookback_days=self._settings.github_listings_lookback_days,
                    registry=registry,
                )
            ]
        return self._listing_sources

    @staticmethod
    def _scoring_prefs(settings_row) -> ScoringPrefs:
        """Map a guild's saved settings onto the scorer's inputs.

        Everything the /jobs set-* commands persist must appear here, or the
        setting silently does nothing.
        """
        return ScoringPrefs(
            min_score=settings_row.min_score,
            locations=list(settings_row.locations or []),
            terms=list(settings_row.academic_terms or []),
            negative_keywords=list(settings_row.negative_keywords or []),
            extra_keywords=list(settings_row.extra_keywords or []),
            preferred_platforms=list(settings_row.preferred_platforms or []),
            deprioritized_platforms=list(settings_row.deprioritized_platforms or []),
            require_location=bool(settings_row.require_location),
        )

    def _query_config(self, settings_row) -> QueryGenConfig:
        enabled = [
            slug
            for slug in QueryGenConfig().enabled_platforms
            if slug not in (settings_row.disabled_platforms or [])
        ]
        return QueryGenConfig(
            enabled_platforms=enabled,
            academic_terms=settings_row.academic_terms or QueryGenConfig().academic_terms,
            locations=settings_row.locations or QueryGenConfig().locations,
        )

    # ------------------------------------------------------------------ #
    async def run_scan(
        self,
        *,
        guild_id: int,
        triggered_by: str = "scheduler",
        platform_filter: str | None = None,
    ) -> ScanReport:
        maker = get_sessionmaker()
        # Dedicated connection to hold the advisory lock for the whole scan.
        lock_session = maker()
        try:
            if not await repo.try_acquire_scan_lock(lock_session):
                return ScanReport(started=False, reason="another scan is already running")

            return await self._run_locked(guild_id, triggered_by, platform_filter)
        finally:
            try:
                await repo.release_scan_lock(lock_session)
            finally:
                await lock_session.close()

    async def _run_locked(
        self, guild_id: int, triggered_by: str, platform_filter: str | None
    ) -> ScanReport:
        maker = get_sessionmaker()
        self._scan_counter += 1
        report = ScanReport(started=True)

        # Load settings + build query batch.
        async with maker() as session:
            settings_row = await repo.get_or_create_settings(session, guild_id)
            company_domains = list(settings_row.company_domains or [])
            prefs = self._scoring_prefs(settings_row)
            qconfig = self._query_config(settings_row)
            overrides = await repo.query_priority_overrides(session)
            scan_run = await repo.start_scan_run(
                session, guild_id=guild_id, triggered_by=triggered_by
            )
            scan_run_id = scan_run.id
            await session.commit()

        candidates = build_queries(qconfig)
        if platform_filter:
            candidates = [q for q in candidates if q.platform_slug == platform_filter]
        batch = select_batch(
            candidates,
            self._settings.max_queries_per_scan,
            rotation=self._scan_counter,
            priority_overrides=overrides,
        )

        registry, extractor, expiration, manager = self._build_components(company_domains)

        error: str | None = None
        try:
            # Curated feeds first: they cost no search quota and give the
            # highest-quality metadata, so their jobs are already in the DB
            # (and deduped) before search results arrive.
            if not platform_filter:
                seen, new = await self._process_listing_sources(
                    self._build_listing_sources(registry), prefs
                )
                report.results_found += seen
                report.jobs_new += new
                report.listing_candidates = seen

            for gq in batch:
                try:
                    found, new, relevant = await self._process_query(
                        gq, manager, extractor, prefs, guild_id, scan_run_id
                    )
                    report.queries_run += 1
                    report.results_found += found
                    report.jobs_new += new
                except Exception as exc:  # noqa: BLE001 - one bad query shouldn't kill the scan
                    log.error("query_failed", query=gq.text, error=str(exc))

            posted = await self._post_pending(guild_id, prefs.min_score, expiration)
            report.jobs_posted = posted
            report.published = await self._publish(prefs.min_score)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.error("scan_failed", error=error)
        finally:
            async with maker() as session:
                run = await session.get(type(scan_run), scan_run_id)
                if run is not None:
                    run.queries_run = report.queries_run
                    run.results_found = report.results_found
                    run.jobs_new = report.jobs_new
                    run.jobs_posted = report.jobs_posted
                    await repo.finish_scan_run(
                        session,
                        run,
                        status=ScanStatus.failed if error else ScanStatus.completed,
                        error=error,
                    )
                await session.commit()

        log.info("scan_complete", **report.model_dump())
        return report

    # ------------------------------------------------------------------ #
    async def _process_query(
        self, gq, manager, extractor, prefs: ScoringPrefs, guild_id, scan_run_id
    ) -> tuple[int, int, int]:
        maker = get_sessionmaker()
        results, provider = await manager.search(gq.text)

        new_jobs = 0
        relevant = 0
        async with maker() as session:
            query_row = await repo.upsert_query(
                session,
                query_hash=gq.query_hash,
                query_text=gq.text,
                platform_slug=gq.platform_slug,
                group=gq.group,
                priority=gq.priority,
            )
            query_id = query_row.id
            await session.commit()

        for result in results:
            extracted = await extractor.extract(result)
            if extracted is None:
                continue
            was_new, was_relevant = await self._ingest(
                extracted,
                prefs=prefs,
                raw_url=result.url,
                query_id=query_id,
                provider=provider,
            )
            new_jobs += int(was_new)
            relevant += int(was_relevant)

        async with maker() as session:
            qrow = await session.get(type(query_row), query_id)
            if qrow is not None:
                await repo.record_query_run(
                    session, qrow, result_count=len(results), relevant_count=relevant
                )
            await session.commit()

        return len(results), new_jobs, relevant

    # ------------------------------------------------------------------ #
    async def _ingest(
        self,
        extracted,
        *,
        prefs: ScoringPrefs,
        raw_url: str,
        query_id: int | None,
        provider: str | None,
    ) -> tuple[bool, bool]:
        """Dedup, score, and persist one extracted job.

        Shared by the search path and the listing-source path so both get
        identical duplicate handling and relevance gating. Returns
        (inserted_new_job, passed_relevance_threshold).
        """
        maker = get_sessionmaker()
        key = dd.dedup_key(extracted)
        chash = dd.content_hash(extracted)
        ncompany = dd.normalize_company(extracted.company)

        async with maker() as session:
            candidates = await repo.candidate_jobs_for_dedup(
                session,
                dedup_key=key,
                platform_slug=extracted.platform_slug,
                external_job_id=extracted.external_job_id,
                normalized_company=ncompany,
                canonical_url=extracted.canonical_url,
                content_hash=chash,
            )
            existing_like = [
                dd.ExistingJobLike(
                    dedup_key=c.dedup_key,
                    canonical_url=c.canonical_url,
                    platform_slug=c.platform_slug,
                    external_job_id=c.external_job_id,
                    content_hash=c.content_hash,
                    normalized_company=c.normalized_company,
                    title=c.title,
                    description=c.description,
                )
                for c in candidates
            ]
            match = dd.find_duplicate(extracted, existing_like)

            scored = score_job(
                extracted,
                prefs=prefs,
                already_seen=match.is_duplicate,
            )

            inserted = False
            if match.is_duplicate:
                await self._touch_existing(
                    session, match.existing_key, extracted, raw_url, query_id, provider
                )
            elif (
                scored.is_internship
                and scored.is_software
                and not scored.negatives
                and scored.location_ok
            ):
                # Only persist genuine internships that clear every gate,
                # including the optional location filter — otherwise a
                # filtered-out job would still bloat the database.
                await self._insert_job(
                    session,
                    extracted,
                    scored,
                    key,
                    chash,
                    ncompany,
                    raw_url,
                    query_id,
                    provider,
                )
                inserted = True
            await session.commit()

        return inserted, scored.is_relevant

    async def _process_listing_sources(self, sources: list, prefs: ScoringPrefs) -> tuple[int, int]:
        """Ingest curated feeds. Returns (candidates_seen, new_jobs)."""
        seen = 0
        new_jobs = 0
        for source in sources:
            try:
                jobs = await source.fetch()
            except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the scan
                log.error("listing_source_failed", source=source.name, error=str(exc))
                continue
            seen += len(jobs)
            for extracted in jobs:
                try:
                    was_new, _ = await self._ingest(
                        extracted,
                        prefs=prefs,
                        raw_url=extracted.url,
                        query_id=None,
                        provider=source.name,
                    )
                    new_jobs += int(was_new)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "listing_ingest_failed",
                        source=source.name,
                        url=extracted.url,
                        error=str(exc),
                    )
        return seen, new_jobs

    async def _insert_job(
        self, session, extracted, scored, key, chash, ncompany, raw_url, query_id, provider
    ) -> None:
        from jobbot.db.models import JobCategory, JobSource

        term = parse_internship_term(
            extracted.internship_term, extracted.title, extracted.description
        )

        job = Job(
            dedup_key=key,
            canonical_url=extracted.canonical_url or extracted.url,
            platform_slug=extracted.platform_slug,
            external_job_id=extracted.external_job_id,
            content_hash=chash,
            title=extracted.title or "(untitled)",
            company=extracted.company,
            normalized_company=ncompany,
            location=extracted.location,
            remote_status=extracted.remote_status,
            employment_type=extracted.employment_type,
            internship_term=term,
            salary=extracted.salary,
            description=extracted.description,
            posted_at=extracted.posting_date,
            status=JobStatus.expired if extracted.is_expired else JobStatus.active,
            relevance_score=scored.score,
            score_breakdown=scored.breakdown,
            matched_keywords=scored.matched_keywords,
        )
        session.add(job)
        await session.flush()

        session.add(
            JobSource(
                job_id=job.id,
                query_id=query_id,
                raw_url=raw_url,
                provider=provider,
            )
        )
        for cat in scored.categories:
            session.add(JobCategory(job_id=job.id, category=cat))

    async def _touch_existing(
        self, session, dedup_key_val, extracted, raw_url, query_id, provider
    ) -> None:
        from jobbot.db.models import JobSource

        job = await repo.get_job_by_dedup_key(session, dedup_key_val)
        if job is None:
            return
        job.last_seen_at = datetime.now(UTC)

        new_chash = dd.content_hash(extracted)
        if job.content_hash and new_chash != job.content_hash:
            # Material change → snapshot the old version, update current.
            session.add(
                JobVersion(
                    job_id=job.id,
                    content_hash=job.content_hash,
                    title=job.title,
                    description=job.description,
                )
            )
            job.content_hash = new_chash
            if extracted.title:
                job.title = extracted.title
            if extracted.description:
                job.description = extracted.description

        # Record the new discovery source if the URL differs. Query explicitly
        # rather than walking job.sources: the lazy relationship would trigger
        # sync IO under asyncio and raise MissingGreenlet.
        from sqlalchemy import select

        existing_source = await session.execute(
            select(JobSource.id).where(JobSource.job_id == job.id, JobSource.raw_url == raw_url)
        )
        if existing_source.first() is None:
            session.add(
                JobSource(
                    job_id=job.id,
                    query_id=query_id,
                    raw_url=raw_url,
                    provider=provider,
                )
            )

    # ------------------------------------------------------------------ #
    async def _post_pending(self, guild_id, min_score, expiration) -> int:
        maker = get_sessionmaker()
        posted_ids: list[int] = []

        async with maker() as session:
            pending = await repo.jobs_pending_post(session, min_score)
            for job in pending:
                # Expiration re-check right before posting.
                result = await expiration.check(job.canonical_url)
                job.last_checked_at = datetime.now(UTC)
                if result.is_expired:
                    job.status = JobStatus.expired
                    continue
                if job.company and await repo.is_company_ignored(
                    session, guild_id, job.normalized_company or ""
                ):
                    continue
                posted_ids.append(job.id)
            await session.commit()

        if not posted_ids or self._poster is None:
            return 0

        delivered = await self._poster(guild_id, posted_ids)
        # A poster that reports nothing is treated as having delivered nothing,
        # so the jobs stay pending and are retried on the next scan.
        if not delivered:
            return 0

        await job_service.mark_posted(list(delivered.keys()), delivered)
        return len(delivered)

    # ------------------------------------------------------------------ #
    async def recheck_expirations(self, limit: int = 50) -> int:
        """Periodically re-check previously active jobs; mark expired ones."""
        maker = get_sessionmaker()
        _, _, expiration, _ = self._build_components([])
        marked = 0
        async with maker() as session:
            jobs = await repo.jobs_to_recheck(
                session, self._settings.expiration_recheck_hours, limit=limit
            )
            for job in jobs:
                result = await expiration.check(job.canonical_url)
                job.last_checked_at = datetime.now(UTC)
                if result.is_expired:
                    job.status = JobStatus.expired
                    marked += 1
            await session.commit()
        log.info("expiration_recheck", checked=len(jobs), marked=marked)
        return marked

    # ------------------------------------------------------------------ #
    def _build_publishers(self) -> list:
        """External listing destinations. Empty unless explicitly configured."""
        if not self._settings.enable_github_publish:
            return []
        if self._publishers is None:
            try:
                self._publishers = [
                    GitHubReadmePublisher(
                        self._client,
                        token=self._settings.github_publish_token,
                        repo=self._settings.github_publish_repo,
                        path=self._settings.github_publish_path,
                        branch=self._settings.github_publish_branch,
                        title=self._settings.github_publish_title,
                    )
                ]
            except ValueError as exc:
                log.error("publisher_config_invalid", error=str(exc))
                self._publishers = []
        return self._publishers

    async def _publish(self, min_score: float) -> bool:
        """Mirror the open listing to external destinations. Never raises."""
        publishers = self._build_publishers()
        if not publishers:
            return False

        maker = get_sessionmaker()
        async with maker() as session:
            jobs = await repo.jobs_for_publication(
                session, min_score, limit=self._settings.github_publish_limit
            )

        wrote = False
        for publisher in publishers:
            try:
                wrote |= await publisher.publish(jobs)
            except Exception as exc:  # noqa: BLE001 - publishing must not abort a scan
                log.error("publish_error", publisher=publisher.name, error=str(exc))
        return wrote
