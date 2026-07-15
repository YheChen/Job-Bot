"""APScheduler-based scan scheduler.

Responsibilities:
  * Schedule a recurring scan per guild at its configured interval.
  * Schedule a periodic expiration recheck.
  * Serialize scans with an asyncio lock (belt-and-suspenders on top of the
    Postgres advisory lock in ScanService).
  * Expose manual trigger + reschedule for Discord commands.
  * Exponential backoff on repeated failures.
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from jobbot.config import Settings
from jobbot.db import repositories as repo
from jobbot.db.session import session_scope
from jobbot.logging import get_logger
from jobbot.services.scan_service import ScanReport, ScanService

log = get_logger(__name__)


class SchedulerRunner:
    def __init__(self, scan_service: ScanService, settings: Settings) -> None:
        self._scan = scan_service
        self._settings = settings
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._lock = asyncio.Lock()
        self._running = False
        self._failures: dict[int, int] = {}

    @property
    def is_scan_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if not self._settings.scan_enabled:
            log.info("scheduler_disabled")
            return

        async with session_scope() as session:
            guild_settings = await repo.all_active_guild_settings(session)

        for gs in guild_settings:
            self._add_guild_job(gs.guild_id, gs.scan_interval_hours)

        # Expiration recheck.
        self._scheduler.add_job(
            self._recheck_job,
            IntervalTrigger(hours=self._settings.expiration_recheck_hours),
            id="expiration_recheck",
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("scheduler_started", guilds=len(guild_settings))

    def _add_guild_job(self, guild_id: int, hours: float) -> None:
        self._scheduler.add_job(
            self._scan_job,
            IntervalTrigger(hours=hours or self._settings.scan_interval_hours),
            id=f"scan:{guild_id}",
            args=[guild_id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def ensure_guild(self, guild_id: int) -> None:
        if self._settings.scan_enabled and not self._scheduler.get_job(f"scan:{guild_id}"):
            self._add_guild_job(guild_id, self._settings.scan_interval_hours)

    def reschedule(self, guild_id: int, hours: float) -> None:
        self._add_guild_job(guild_id, hours)
        log.info("rescheduled", guild_id=guild_id, hours=hours)

    async def _scan_job(self, guild_id: int) -> None:
        await self.trigger_scan(guild_id=guild_id, triggered_by="scheduler")

    async def _recheck_job(self) -> None:
        try:
            await self._scan.recheck_expirations()
        except Exception as exc:  # noqa: BLE001
            log.error("recheck_job_failed", error=str(exc))

    async def trigger_scan(
        self,
        *,
        guild_id: int,
        triggered_by: str = "manual",
        platform_filter: str | None = None,
    ) -> ScanReport:
        if self._lock.locked():
            return ScanReport(started=False, reason="another scan is already running")
        async with self._lock:
            self._running = True
            try:
                report = await self._scan.run_scan(
                    guild_id=guild_id,
                    triggered_by=triggered_by,
                    platform_filter=platform_filter,
                )
                self._failures[guild_id] = 0
                return report
            except Exception as exc:  # noqa: BLE001
                self._handle_failure(guild_id)
                log.error("scan_trigger_failed", guild_id=guild_id, error=str(exc))
                return ScanReport(started=False, reason=str(exc))
            finally:
                self._running = False

    def _handle_failure(self, guild_id: int) -> None:
        """Exponential backoff: after repeated failures, widen the interval."""
        self._failures[guild_id] = self._failures.get(guild_id, 0) + 1
        n = self._failures[guild_id]
        if n >= 3:
            backoff_hours = min(24.0, self._settings.scan_interval_hours * (2 ** (n - 2)))
            self._add_guild_job(guild_id, backoff_hours)
            log.warning("scan_backoff", guild_id=guild_id, hours=backoff_hours)

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
