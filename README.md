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
│  2a. ListingSources → curated feeds (SimplifyJobs), no quota, no fetch │
│  2b. QueryGenerator → prioritized, rotated batch of `site:` queries    │
│      → SearchManager → provider (Serper/Bing/Brave/PSE) w/ fallback    │
│      → JobExtractor → canonicalize, SSRF-guard, fetch, JSON-LD parse   │
│  ── both paths converge on the shared _ingest tail ──                  │
│  3. DedupDetector → url / job-id / company+title / content-hash / fuzzy│
│  4. RelevanceScorer → weighted deterministic signals (+ optional LLM)  │
│  5. persist jobs/sources/versions                                      │
│  6. ExpirationChecker → HTTP + phrases + validThrough before posting   │
│  7. poster callback → marks only jobs actually delivered               │
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
│   ├── sources/                    # curated listing feeds (SimplifyJobs)
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

## Listing sources

Besides search, the bot ingests **curated feeds** — a second discovery path that
consumes **no search-API quota**. Feeds carry better metadata than we could
scrape back off a job page, so records map straight to a job with no page fetch.

Enabled by default: **[SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships)**
`listings.json` (title, company, locations, terms, `date_posted`, `active`, and a
direct application URL per posting).

```bash
ENABLE_GITHUB_LISTINGS=true
GITHUB_LISTINGS_LOOKBACK_DAYS=30      # 0 = ingest the whole feed
GITHUB_LISTINGS_CATEGORIES=Software;AI/ML/Data    # semicolon-separated
```

- **Cheap.** The feed is ~11 MB, so requests are conditional on the previous
  `ETag`; an unchanged feed costs a 304 and does no work.
- **Same guarantees as search.** Feed jobs go through the identical dedup,
  relevance-scoring, expiration, and posting path — a posting found by both the
  feed and a search query is stored once.
- **Category is only a pre-filter.** The deterministic scorer remains the
  authority on what counts as a software internship.
- **Third-party data is not trusted.** Only `http(s)` URLs are accepted, so a
  hostile entry can't reach a Discord link button.

> **First run posts a backlog.** With a 30-day lookback the feed yields a few
> hundred relevant postings. Delivery is capped per scan (25 by default), so the
> backlog trickles out over several scans rather than flooding the channel. To
> start with only genuinely new postings, set a small
> `GITHUB_LISTINGS_LOOKBACK_DAYS` (e.g. `3`) for the first scan.

Add a source by implementing the `ListingSource` protocol (`fetch() ->
list[ExtractedJob]`) in `jobbot/sources/` and registering it in
`ScanService._build_listing_sources`.

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

Full runbooks live in **[DEPLOY.md](DEPLOY.md)**, including Discord application
setup, verification steps, and troubleshooting. Three supported paths:

| Path | Stack | Best for |
|---|---|---|
| **A. Docker Compose** ([docker-compose.prod.yml](docker-compose.prod.yml)) | containers + Postgres, localhost-only ports, restart policies | A Linux box you own (home server, ThinkPad, VPS) with Docker |
| **B. systemd + SQLite** ([deploy/jobbot.service](deploy/jobbot.service)) | bare metal, no Docker, no DB server | Oracle Cloud free tier, Raspberry Pi, tiny VPS (ARM-friendly) |
| **C. Managed platforms** | the same container image | Fly.io, Railway, Render, AWS ECS |

The bot only makes **outbound** connections — no inbound ports, port
forwarding, or firewall changes are needed on any path. Never commit `.env`.

---

## Testing

`pytest -q` runs 64 tests covering query generation, URL canonicalization,
duplicate detection, job classification, negative-keyword filtering, relevance
scoring, expired-job detection, platform recognition, provider quota/fallback,
extraction, and Discord permission checks. HTML fixtures for Ashby, Greenhouse,
Lever, Workday, SmartRecruiters, and Workable live in `tests/fixtures/`.
