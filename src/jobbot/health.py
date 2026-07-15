"""Minimal dependency-free health-check HTTP server.

Exposes GET /health (liveness) and GET /ready (DB connectivity). Runs on the
asyncio event loop alongside the bot so orchestrators (Fly/Render/Railway/ECS)
can probe it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from jobbot.db.session import get_sessionmaker
from jobbot.logging import get_logger

log = get_logger(__name__)


async def _db_ok() -> bool:
    try:
        maker = get_sessionmaker()
        async with maker() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        line = request.decode("latin1", errors="replace")
        path = line.split(" ")[1] if len(line.split(" ")) > 1 else "/"

        if path.startswith("/ready"):
            ok = await _db_ok()
            status = "200 OK" if ok else "503 Service Unavailable"
            body = b'{"status":"ready"}' if ok else b'{"status":"not-ready"}'
        elif path.startswith("/health"):
            status, body = "200 OK", b'{"status":"ok"}'
        else:
            status, body = "404 Not Found", b'{"error":"not found"}'

        response = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode() + body
        writer.write(response)
        await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


class HealthServer:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(_handle, self._host, self._port)
        log.info("health_server_started", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            log.info("health_server_stopped")
