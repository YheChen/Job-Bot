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

## Quick start (Docker Compose)

```bash
cp .env.example .env
# Edit .env: set DISCORD_TOKEN and one search provider key
# (or set SEARCH_PROVIDERS=mock to run the whole pipeline with no API key).

docker compose up --build
```

Compose starts Postgres, runs `alembic upgrade head`, then launches the bot. The
health endpoint is exposed at `http://localhost:8080/health`.

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

## Local development (without Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Start just Postgres:
docker compose up -d db

export $(grep -v '^#' .env | xargs)   # or use a dotenv loader
alembic upgrade head
jobbot                                 # or: python -m jobbot.main
```

Run the tests (no database or network required — the pipeline pieces are pure):

```bash
pytest -q
```

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

All targets run the same container. Provide the env vars from `.env.example` via
the platform's secret manager — **never commit `.env`**. The container runs
migrations on start via `docker/entrypoint.sh`.

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
