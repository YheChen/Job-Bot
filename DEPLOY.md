# Deploying jobbot

Three supported paths. All of them run the same code; pick by where it will live:

| Path | Stack | Best for |
|---|---|---|
| **A. Docker Compose** | containers + Postgres | A Linux box you own (home server, ThinkPad, VPS) with Docker installed |
| **B. systemd + SQLite** | bare metal, no Docker | Minimal hosts: Oracle Cloud free tier, Raspberry Pi, tiny VPS |
| **C. Managed platforms** | container image | Fly.io, Railway, Render, AWS |

The bot only makes **outbound** connections (Discord gateway, search API, job
pages). No inbound ports, port forwarding, or firewall changes are required on
any path. Never commit `.env` — all secrets go through environment variables.

---

## Part 0 — Discord application (required for every path)

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy it (this is `DISCORD_TOKEN`). Leave all
   **Privileged Gateway Intents off** — the bot is slash-command only and never
   reads message content.
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`;
   permissions **View Channel**, **Send Messages**, **Embed Links**. Open the
   generated URL and invite the bot to your server.
4. Enable Developer Mode (User Settings → Advanced), then right-click your
   server icon → **Copy Server ID** (this is `DISCORD_GUILD_IDS` — setting it
   makes slash commands appear instantly; global sync can take up to an hour).
5. Get a search API key — [serper.dev](https://serper.dev) is the cheapest to
   start. (Or set `SEARCH_PROVIDERS=mock` for a keyless dry run.)

After the bot is online (any path): in Discord run `/jobs set-channel
#your-channel`, then `/jobs scan` to trigger the first scan.

---

## Path A — Docker Compose on your own Linux box (recommended)

Uses [docker-compose.prod.yml](docker-compose.prod.yml): Postgres + bot
containers, ports bound to `127.0.0.1` only, `restart: unless-stopped`,
migrations run automatically on container start.

### Prerequisites

Docker Engine with the compose plugin, daemon enabled at boot:

```bash
docker --version && docker compose version
systemctl is-enabled docker     # should print "enabled"
```

SELinux and firewalld need **no changes**: the stack uses named volumes (no
host bind mounts) and publishes ports only on localhost.

### If the box is a laptop (one-time prep)

A laptop makes a great home server — the battery is a built-in UPS — but the OS
will suspend it unless told otherwise:

```bash
# 1. Don't suspend on lid close
sudo sed -i 's/^#*HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo sed -i 's/^#*HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf

# 2. Never auto-sleep on idle (blocks GNOME/KDE suspend too)
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

sudo systemctl restart systemd-logind
```

On a dual-boot machine, confirm Linux is the GRUB default so an unattended
reboot never lands in Windows (on Fedora: `sudo grub2-set-default 0` if needed).
If your battery firmware supports charge thresholds (ThinkPads do), 20–80%
limits are ideal for an always-plugged machine. Optionally set BIOS "power on
after AC loss".

### Deploy

```bash
git clone https://github.com/YheChen/Job-Bot.git ~/Job-Bot   # or: gh repo clone
cd ~/Job-Bot

cp .env.example .env
vim .env
#   DISCORD_TOKEN=...
#   DISCORD_GUILD_IDS=<your server id>
#   SEARCH_PROVIDERS=serper
#   SERPER_API_KEY=...
#   POSTGRES_PASSWORD=               # REQUIRED — pick a strong one
# (DATABASE_URL is set by the compose file — leave the SQLite default alone.)

docker compose -f docker-compose.prod.yml up -d --build
```

### Verify

```bash
docker compose -f docker-compose.prod.yml ps                # both services Up (db healthy)
docker compose -f docker-compose.prod.yml logs -f bot       # look for: commands_synced, bot_ready
curl -s http://localhost:8080/health                        # {"status":"ok"}
```

Then reboot the box and check the stack comes back on its own:

```bash
sudo reboot
# after it's back:
docker compose -f ~/Job-Bot/docker-compose.prod.yml ps
```

Finish with `/jobs set-channel` + `/jobs scan` in Discord (Part 0, step 6).

### Manage

Tip: `alias jc='docker compose -f ~/Job-Bot/docker-compose.prod.yml'`

| Task | Command |
|---|---|
| Logs | `jc logs -f bot` |
| Restart | `jc restart bot` |
| Stop everything | `jc down` |
| Update | `cd ~/Job-Bot && git pull && jc up -d --build` |
| DB backup | `jc exec db pg_dump -U jobbot jobbot > backup-$(date +%F).sql` |
| psql shell | `jc exec db psql -U jobbot jobbot` |

> **Postgres password note.** Postgres bakes the password into the data volume
> on first init. If you change `POSTGRES_PASSWORD` later, either update the
> user's password with `ALTER USER` in a psql shell, or wipe the volume with
> `docker compose -f docker-compose.prod.yml down -v` (destroys all job data).

---

## Path B — systemd + SQLite, no Docker (Oracle Cloud VM, Raspberry Pi)

One always-on process, the whole database in one SQLite file, restarts handled
by systemd. Works on ARM64 — the base install has **no compiled Postgres
drivers**. Needs Python 3.12+ (Ubuntu 24.04 ships it; on Fedora use
`sudo dnf install python3.12`).

```bash
# 1. System deps (Ubuntu/Debian shown)
sudo apt update && sudo apt install -y python3.12 python3.12-venv git

# 2. Service user + app dir
sudo useradd --system --create-home --home-dir /opt/jobbot jobbot
sudo -u jobbot git clone https://github.com/YheChen/Job-Bot.git /opt/jobbot
cd /opt/jobbot

# 3. Install into a venv (SQLite only — no extras needed)
sudo -u jobbot python3.12 -m venv .venv
sudo -u jobbot .venv/bin/pip install --upgrade pip
sudo -u jobbot .venv/bin/pip install .

# 4. Configure. Use an ABSOLUTE SQLite path (note the four slashes).
sudo -u jobbot cp .env.example .env
sudo -u jobbot nano .env
#   DISCORD_TOKEN=...
#   DISCORD_GUILD_IDS=...
#   SEARCH_PROVIDERS=serper
#   SERPER_API_KEY=...
#   DATABASE_URL=sqlite+aiosqlite:////opt/jobbot/jobbot.db

# 5. Create the database
sudo -u jobbot .venv/bin/alembic upgrade head

# 6. Install and start the service
sudo cp deploy/jobbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobbot
sudo journalctl -u jobbot -f          # look for: commands_synced, bot_ready
```

To update: `git pull`, `.venv/bin/pip install .`, `.venv/bin/alembic upgrade
head`, `sudo systemctl restart jobbot`.

> **Oracle Cloud note.** No ingress rules needed in the VCN security list or
> the VM's iptables — the bot is outbound-only. The `/health` endpoint binds
> locally; only expose port 8080 if you want to probe it from outside. Oracle's
> Ubuntu images ship with restrictive iptables by default — that's fine here.

> **Backups.** The entire state is one file:
> `sqlite3 /opt/jobbot/jobbot.db ".backup /opt/jobbot/backup-$(date +\%F).db"`
> (add to cron).

> **Laptop as the host?** Apply the lid/sleep prep from Path A first.

---

## Path C — Managed container platforms

All platforms run the same image; the entrypoint migrates on start. Provide the
env vars from `.env.example` via the platform's secret manager.

> For SQLite on Fly/Render, attach a persistent volume and set
> `DATABASE_URL=sqlite+aiosqlite:////data/jobbot.db`. Without a volume the file
> resets on redeploy.

### Fly.io

```bash
fly launch --no-deploy                       # generates fly.toml
fly postgres create && fly postgres attach <db-app>   # sets DATABASE_URL
fly secrets set DISCORD_TOKEN=<token> SERPER_API_KEY=<key>
# In fly.toml: set internal_port = 8080 and a [[services.http_checks]] path "/health"
fly deploy
```
Use `postgresql+asyncpg://...` for `DATABASE_URL` (rewrite the attached URL).

### Railway

1. New Project → Deploy from repo (Dockerfile detected).
2. Add the **PostgreSQL** plugin; reference `${{Postgres.DATABASE_URL}}` and
   prefix the scheme with `postgresql+asyncpg://`.
3. Add `DISCORD_TOKEN` and provider keys as service variables.

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

## Remote management

For a home server, SSH in from your LAN, or install
[Tailscale](https://tailscale.com) on the server and your other machines for a
stable address that works from anywhere — no port forwarding, plays fine with
firewalld.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Slash commands don't appear | Set `DISCORD_GUILD_IDS` in `.env` and restart — per-guild sync is instant, global sync can take up to 1 h. |
| Bot online but never posts | Run `/jobs set-channel` first; check the bot has View Channel / Send Messages / Embed Links in that channel; check logs for `no_post_channel` or provider errors. |
| Config validation error at startup | `.env` is missing `DISCORD_TOKEN` or the API key for an enabled provider. For a keyless test set `SEARCH_PROVIDERS=mock`. |
| `port is already allocated` (Path A) | Something on the host already uses 5432/8080 — change the published port in `docker-compose.prod.yml` (e.g. `127.0.0.1:5433:5432`). |
| `python3.12: command not found` (Path B) | Ubuntu 22.04 ships 3.10 — use 24.04, the deadsnakes PPA, or `dnf install python3.12` on Fedora. |
| Machine sleeps and the bot goes offline | Apply the laptop prep in Path A (lid switch + mask sleep targets). |
