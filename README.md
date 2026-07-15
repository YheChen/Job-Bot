# jobbot — Software Engineering Internship Discovery Bot

A production-ready Discord bot that regularly searches applicant tracking systems
(Ashby, Greenhouse, Lever, Workday, SmartRecruiters, Workable, and more) via a
configurable **search API**, extracts and de-duplicates postings, filters them
down to genuine software-engineering internships, and posts them to Discord as
rich embeds with action buttons.

Built for reliability first: deterministic filtering/scoring/dedup with full unit
tests, SSRF-guarded page fetching, quota-aware provider fallback, advisory-lock
scan mutual-exclusion, migrations, structured logging, health checks, and
graceful shutdown. Advanced features (LLM classification, digests) sit behind
feature flags.

---

## Architecture

```
                         ┌────────────────────────────┐
                         │        SchedulerRunner       │  APScheduler
                         │  (per-guild interval + lock) │
                         └──────────────┬───────────────┘
                                        │ trigger_scan()
                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                              ScanService                               │
│  1. pg advisory lock (only one scan at a time)                         │
│  2. QueryGenerator → prioritized, rotated batch of `site:` queries     │
│  3. SearchManager → provider (Serper/Bing/Brave/PSE) w/ quota fallback │
│  4. JobExtractor → canonicalize URL, SSRF-guard, fetch, JSON-LD parse  │
│  5. DedupDetector → url / job-id / company+title / content-hash / fuzzy│
│  6. RelevanceScorer → weighted deterministic signals (+ optional LLM)  │
│  7. ExpirationChecker → HTTP + phrases + validThrough before posting   │
│  8. persist jobs/sources/versions → poster callback                    │
└──────────────────────────────────────────────────────────────────────┘
                                        │ poster(guild_id, job_ids)
                                        ▼
                         ┌────────────────────────────┐
                         │           JobBot            │  discord.py
                         │  embeds + persistent buttons │
                         └────────────────────────────┘
```

### Key design decisions

- **Protocol-based extension points.** `SearchProvider` (Protocol) and
  `PlatformAdapter` make new providers/ATSes drop-in. The `PlatformRegistry` maps
  hostnames → adapters; unknown hosts fall through to a generic JSON-LD adapter
  or a configured company domain.
- **Deterministic core, LLM optional.** Query generation, URL canonicalization,
  dedup, and relevance scoring are pure functions with no I/O — fully unit
  tested. The LLM step (`ENABLE_LLM_CLASSIFICATION`) only *refines* an
  already-passing result and is off by default.
- **Bounded query generation.** No blind Cartesian product: we build a small set
  of templates per (platform, title-group) and decorate a subset with term or
  location clauses. Each scan `select_batch(...)` reserves the top-priority
  queries and rotates a window through the rest, so coverage spreads over time
  while high-value queries always run. Historic relevant-hit rate feeds back as a
  priority bonus.
- **Idempotent identity.** A job's `dedup_key` is derived from
  platform+requisition-id → canonical URL → normalized company+title. Re-discovery
  updates `last_seen_at` and only snapshots a `job_version` on a material content
  change. Discord posting is gated on `posted_to_discord`.
- **Safety by default.** SSRF allowlist + private-IP resolution check before every
  outbound fetch (and again after redirects), HTML→text sanitization,
  Pydantic-validated env that fails fast, request timeouts, tenacity retries with
  exponential backoff.

---

## Project structure

```
Job Bot/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── .env.example
├── docker/entrypoint.sh
├── migrations/                     # Alembic
│   ├── env.py
│   └── versions/0001_initial.py
├── src/jobbot/
│   ├── config.py                   # Pydantic settings + validation
│   ├── logging.py                  # structlog setup
│   ├── health.py                   # /health, /ready HTTP endpoint
│   ├── main.py                     # entrypoint + graceful shutdown
│   ├── db/                         # models, session, repositories
│   ├── search/                     # provider protocol + serper/bing/brave/pse/mock + manager
│   ├── queries/                    # vocabulary (terms.py) + generator.py
│   ├── platforms/                  # base adapter, registry, per-ATS adapters
│   ├── parsing/                    # url, ssrf, sanitize, jsonld, fetcher, extractor
│   ├── scoring/                    # keywords, relevance, llm (optional)
│   ├── dedup/                      # detector
│   ├── expiration/                 # checker
│   ├── scheduler/                  # APScheduler runner
│   ├── services/                   # scan_service, settings_service, job_service
│   └── bot/                        # client, embeds, views, permissions, cogs/
└── tests/                          # 64 tests + HTML fixtures for each ATS
```

---

## Quick start — no Docker (SQLite)

The bot runs with **zero external services**: it defaults to a local SQLite file,
so all you need is Python 3.12+.

```bash
cp .env.example .env
# Edit .env: set DISCORD_TOKEN. Leave DATABASE_URL as the SQLite default.
# Set SEARCH_PROVIDERS=mock to try the whole pipeline with no API key,
# or set a real provider key (e.g. SERPER_API_KEY).

make install     # creates .venv and installs the package
make migrate     # creates jobbot.db and its tables
make run         # starts the bot
```

Or without `make`:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head        # reads DATABASE_URL from .env / environment
jobbot                      # or: python -m jobbot.main
```

The health endpoint is at `http://localhost:8080/health`.

> **SQLite vs Postgres.** SQLite is perfect for a single instance. The only
> Postgres-specific feature is the cross-process scan advisory lock; on SQLite
> the scheduler's in-process lock serializes scans instead (correct for one
> instance). For multiple instances or heavier load, switch `DATABASE_URL` to
> Postgres — no code changes needed.

## Quick start — Docker Compose (Postgres)

Prefer containers / Postgres? Compose is still fully supported:

```bash
cp .env.example .env         # set DISCORD_TOKEN (+ provider key)
docker compose up --build
```

Compose starts Postgres, runs `alembic upgrade head`, then launches the bot.
(The compose file overrides `DATABASE_URL` to point at the Postgres service.)

### Discord setup

1. Create an application at <https://discord.com/developers/applications>.
2. Add a **Bot**, copy its token into `DISCORD_TOKEN`.
3. Under **OAuth2 → URL Generator**, select scopes `bot` + `applications.commands`
   and permissions *Send Messages*, *Embed Links*, *Use Slash Commands*. Invite
   the bot to your server.
4. Put your server id in `DISCORD_GUILD_IDS` for instant slash-command sync during
   development (global sync can take up to an hour).
5. In your server: `/jobs set-channel #internships`, then `/jobs scan` to test.

---

## Tests

Run the suite (no database or network required — the pipeline pieces are pure):

```bash
make test        # or: pytest -q
```

## Code quality

Lint and format with ruff, and enable the git pre-commit hooks so issues are
caught before they reach CI:

```bash
make hooks       # one-time: installs the pre-commit git hook (needs make install first)
make lint        # ruff check
make fmt         # ruff format
```

`pre-commit install` wires up `.pre-commit-config.yaml` (ruff lint + format plus
whitespace/YAML/TOML hygiene). The same checks run in CI
(`.github/workflows/ci.yml`) on every push and PR to `master`: ruff lint, ruff
format check, the pytest suite, and a SQLite `alembic upgrade head` smoke test.

---

## Search providers

Set `SEARCH_PROVIDERS` to an ordered, comma-separated list. The first with
remaining quota is used; the rest are automatic fallbacks.

| Provider     | Env vars                              | Notes                          |
|--------------|---------------------------------------|--------------------------------|
| `serper`     | `SERPER_API_KEY`                      | Google results via serper.dev  |
| `bing`       | `BING_API_KEY`                        | Bing Web Search v7             |
| `brave`      | `BRAVE_API_KEY`                       | Brave Search API               |
| `google_pse` | `GOOGLE_PSE_API_KEY`, `GOOGLE_PSE_CX` | Programmable Search JSON API   |
| `mock`       | –                                     | Canned results for dev/tests   |

Adding a provider: implement the `SearchProvider` protocol (`search(query, *,
page, results_per_page) -> list[SearchResult]`) and register it in
`SearchManager.from_settings`. SerpAPI is a straightforward addition following
the same shape as `serper.py`.

Adding a platform: subclass `PlatformAdapter` (override `domains`,
`extract_job_id`, `_company_from_url`) and add it to `PlatformRegistry.default`.
The base class already handles JSON-LD + Open Graph + `<title>`.

---

## Discord commands

**Everyone:** `/jobs recent`, `/jobs search`, `/jobs stats`, `/jobs platforms`,
`/jobs queries`, `/jobs companies`, `/jobs saved`, `/jobs status`, `/jobs scan`.

**Admins / bot-manager roles only:** `/jobs set-channel`, `/jobs set-interval`,
`/jobs set-locations`, `/jobs set-terms`, `/jobs set-keywords`,
`/jobs set-negative-keywords`, `/jobs set-min-score`, `/jobs enable-platform`,
`/jobs disable-platform`, `/jobs add-company-domain`, `/jobs remove-company-domain`,
`/jobs pause`, `/jobs resume`.

Each job embed carries **Apply** (link), **Irrelevant**, **Duplicate**, **Save**,
and **Hide company** buttons (persistent across restarts).

---

## Feature flags / roadmap

The first version focuses on reliable **search → dedup → filter → alert**. These
are scaffolded and gated for follow-up work:

- `ENABLE_LLM_CLASSIFICATION` — optional Anthropic classification refinement.
- Digests (hourly/daily/weekly) and per-category subscription roles — schema
  (`subscriptions`, `job_categories`) and digest embed builder are in place;
  scheduling them is the next increment.
- Playwright rendering for JS-only job pages (install the `playwright` extra).

---

## Deployment

### Linux VM with systemd (Oracle Cloud, Raspberry Pi, any VPS) — recommended for SQLite

This is the simplest production setup: one always-on process, the SQLite file on
local disk, restarts handled by systemd. No Docker, no database server. Works on
ARM64 (Oracle Ampere / Raspberry Pi) — the base install has **no compiled
Postgres drivers**.

```bash
# 1. System deps (Ubuntu/Debian on the VM)
sudo apt update && sudo apt install -y python3.12 python3.12-venv git

# 2. Create a service user + app dir
sudo useradd --system --create-home --home-dir /opt/jobbot jobbot
sudo -u jobbot git clone <your-repo-url> /opt/jobbot
cd /opt/jobbot

# 3. Install into a venv (SQLite only — no extras needed)
sudo -u jobbot python3.12 -m venv .venv
sudo -u jobbot .venv/bin/pip install --upgrade pip
sudo -u jobbot .venv/bin/pip install .

# 4. Configure. Use an ABSOLUTE SQLite path (note the four slashes).
sudo -u jobbot cp .env.example .env
sudo -u jobbot nano .env
#   DISCORD_TOKEN=...
#   SEARCH_PROVIDERS=serper
#   SERPER_API_KEY=...
#   DATABASE_URL=sqlite+aiosqlite:////opt/jobbot/jobbot.db

# 5. Create the database
sudo -u jobbot .venv/bin/alembic upgrade head

# 6. Install and start the service
sudo cp deploy/jobbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobbot
sudo journalctl -u jobbot -f          # tail logs
```

To update: `git pull`, `.venv/bin/pip install .`, `.venv/bin/alembic upgrade
head`, `sudo systemctl restart jobbot`.

> **Oracle Cloud note.** The bot only makes *outbound* connections (Discord
> gateway, search API, job pages), so you do **not** need to open any inbound
> ports in the VCN security list or the VM's iptables for it to work. The
> `/health` endpoint binds locally; only expose port 8080 if you want to probe it
> from outside (and add the matching ingress rule if so). Oracle's Ubuntu images
> ship with restrictive iptables by default — that's fine here.

> **Backups.** The entire state is one file. Back it up with a cron job:
> `sqlite3 /opt/jobbot/jobbot.db ".backup /opt/jobbot/backup-$(date +\%F).db"`.

### Container platforms (Docker / Postgres)

The steps below run the same container image and use Postgres. Provide the env
vars from `.env.example` via the platform's secret manager — **never commit
`.env`**. The container runs migrations on start via `docker/entrypoint.sh`.

> For SQLite on Fly/Render, attach a persistent volume and set
> `DATABASE_URL=sqlite+aiosqlite:////data/jobbot.db`. Without a volume the file
> resets on redeploy. On a plain VM (above) this isn't a concern.

### Fly.io

```bash
fly launch --no-deploy                       # generates fly.toml
fly postgres create && fly postgres attach <db-app>   # sets DATABASE_URL
fly secrets set DISCORD_TOKEN=... SERPER_API_KEY=...
# In fly.toml: set internal_port = 8080 and a [[services.http_checks]] path "/health"
fly deploy
```
Use `postgresql+asyncpg://...` for `DATABASE_URL` (rewrite the attached URL).

### Railway

1. New Project → Deploy from repo (Dockerfile detected).
2. Add the **PostgreSQL** plugin; reference `${{Postgres.DATABASE_URL}}` and
   prefix the scheme with `postgresql+asyncpg://`.
3. Add `DISCORD_TOKEN` and provider keys as service variables. Railway builds and
   runs the container; the entrypoint migrates automatically.

### Render

1. **New → Web Service** from the repo (Docker runtime).
2. **New → PostgreSQL**; copy its internal URL into `DATABASE_URL`
   (`postgresql+asyncpg://...`).
3. Set Health Check Path to `/health`, add secrets, deploy.

### AWS (ECS Fargate)

1. Build & push the image to ECR.
2. Create an RDS Postgres instance; store `DATABASE_URL` and `DISCORD_TOKEN` in
   Secrets Manager.
3. Define a Fargate task (1 container) injecting those secrets; open container
   port 8080 and point the ALB/target-group health check at `/health`.
4. Run as a long-lived service (desired count 1 — a single instance owns the
   scan advisory lock).

---

## Testing

`pytest -q` runs 64 tests covering query generation, URL canonicalization,
duplicate detection, job classification, negative-keyword filtering, relevance
scoring, expired-job detection, platform recognition, provider quota/fallback,
extraction, and Discord permission checks. HTML fixtures for Ashby, Greenhouse,
Lever, Workday, SmartRecruiters, and Workable live in `tests/fixtures/`.
