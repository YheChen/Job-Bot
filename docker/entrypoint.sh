#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] running database migrations..."
alembic upgrade head

echo "[entrypoint] starting jobbot..."
exec jobbot
