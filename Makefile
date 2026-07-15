.PHONY: install migrate run test lint fmt clean

# One-time setup: create a venv and install the package (SQLite, no Docker).
install:
	python3.12 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

# Apply database migrations (creates jobbot.db for SQLite).
migrate:
	. .venv/bin/activate && alembic upgrade head

# Run the bot (reads .env).
run:
	. .venv/bin/activate && jobbot

# Run the test suite.
test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src tests

fmt:
	. .venv/bin/activate && ruff format src tests

clean:
	rm -rf .venv .pytest_cache .ruff_cache jobbot.db
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
