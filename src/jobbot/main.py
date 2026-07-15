"""Application entrypoint.

Boots the DB engine, HTTP client, search/scan services, scheduler, health
server, and the Discord bot, then blocks until a shutdown signal is received.
Handles graceful shutdown of every component.
"""

from __future__ import annotations

import asyncio
import signal

import httpx

from jobbot.bot.client import JobBot
from jobbot.config import get_settings
from jobbot.db.session import dispose_engine, init_engine
from jobbot.health import HealthServer
from jobbot.logging import configure_logging, get_logger
from jobbot.scheduler.runner import SchedulerRunner
from jobbot.services.scan_service import ScanService

log = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log.info("starting", environment=settings.environment)

    init_engine(str(settings.database_url))

    client = httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.http_user_agent},
        follow_redirects=True,
    )

    bot = JobBot(settings)
    scan_service = ScanService(settings, client, poster=bot.post_jobs)
    scheduler = SchedulerRunner(scan_service, settings)
    bot.scheduler_runner = scheduler

    health = HealthServer(settings.health_host, settings.health_port)
    await health.start()

    stop_event = asyncio.Event()

    def _signal() -> None:
        log.info("shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    bot_task = asyncio.create_task(bot.start(settings.discord_token))

    # Start the scheduler after the bot has connected so guild jobs exist.
    async def _start_scheduler() -> None:
        await bot.wait_until_ready()
        for guild in bot.guilds:
            scheduler.ensure_guild(guild.id)
        await scheduler.start()

    scheduler_task = asyncio.create_task(_start_scheduler())

    await stop_event.wait()

    # --- graceful shutdown ---
    log.info("shutting_down")
    await scheduler.shutdown()
    scheduler_task.cancel()
    await bot.close()
    bot_task.cancel()
    await health.stop()
    await client.aclose()
    await dispose_engine()
    log.info("shutdown_complete")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
