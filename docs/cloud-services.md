# Cloud Services — Operator Runbook

This document covers Radon's two-mode architecture introduced in Phase 0–6 of the cloud-services migration. Both modes serve identical data to `localhost:3000` (laptop dev) and `app.radon.run` (Hetzner production).

## Architecture (TL;DR)

```
                         Turso Cloud DB (libSQL)
                       radon-joemccann.aws-us-west-2
                                  ▲
                ┌─────────────────┴─────────────────┐
                │   embedded replica.db sync ~60s   │
                ▼                                   ▼
         LAPTOP dev process                 HETZNER production
         localhost:3000 (Next.js)           app.radon.run (Caddy → Next.js)
         FastAPI 8321                       FastAPI 8321 (private)
         IB realtime relay 8765             radon-services systemd timers
         newsfeed scraper (chrome-cdp)      ib-gateway docker (4001)
                                            media.radon.run (Caddy static)
```

- **Database**: Turso (libSQL) — every Next.js / FastAPI / scheduler process holds a SQLite-fast embedded replica, writes go to cloud and stream back.
- **Media**: Hetzner-hosted Caddy serves `https://media.radon.run`; the laptop's newsfeed scraper rsyncs new images over Tailscale.
- **Schedulers**: laptop launchd plists (local mode) OR Hetzner systemd timers (cloud mode) — same scripts, different host.
- **Browser-bound**: themarketear.com newsfeed scraper always runs on the laptop (magic-link login can't be automated). Everything else can run anywhere.

## Mode switch

| Action | Command |
|--------|---------|
| Switch to **Hetzner mode** | `scripts/cloud.sh` |
| Switch to **Local mode**   | `scripts/local.sh` |
| Inspect current mode       | `scripts/ib mode` |

`RADON_MODE` is persisted to `.env.ib-mode` at the project root (gitignored). All Python and Node entry points read this overlay file after `.env`, so the choice survives shell restarts.

## Deployment

### Initial Hetzner bring-up (one-time)

```bash
# On Hetzner VPS, as radon
mkdir -p /home/radon/radon-cloud/services
cd /home/radon/radon-cloud/services
# Copy docker/services/docker-compose.yml + Dockerfile from this repo,
# plus .env (TURSO_DB_URL, TURSO_AUTH_TOKEN, TWS_USERID, etc.)

docker compose build
docker compose up -d
docker compose ps    # verify radon-services healthy
```

### Day-to-day deploys

The radon-services image is rebuilt and rolled by CI on push to `main`:

```
.github/workflows/deploy.yml → ssh radon@ib-gateway → docker compose pull && up -d
```

**Do not deploy from this branch.** The plan document explicitly forbids automatic prod deploys until rollback paths are exercised.

## Bootstrap & disaster recovery

| Scenario | Recovery |
|----------|----------|
| Cold-start a new laptop | Clone repo, `bun install`, set `TURSO_DB_URL` + `TURSO_AUTH_TOKEN`, run `bun run db:migrate`, then `scripts/cloud.sh`. The replica file rebuilds on first read. |
| Cold-start a new VPS | `docker compose up -d` against `docker/ib-gateway/docker-compose.yml`, `docker/services/docker-compose.yml`. Laptop's `scripts/cloud.sh` flips IB host to the new VPS. |
| Replica corruption | `rm data/replica.db data/replica.db-info` then restart Next.js / FastAPI — first read re-syncs from cloud (~5s). |
| Turso outage | Read paths fall through to JSON files (dual-write retains them). Writes queue locally and replay when cloud returns. |
| Hetzner outage | Switch to `scripts/local.sh`. Laptop becomes self-sufficient against local Docker IB Gateway. |

## Health & observability

```bash
# Laptop
curl http://localhost:8321/health | jq          # FastAPI + IB Gateway
sqlite3 data/replica.db 'SELECT service, state, updated_at FROM service_health'

# Hetzner
ssh radon@ib-gateway 'docker compose -f /home/radon/radon-cloud/services/docker-compose.yml logs --tail 50'
ssh radon@ib-gateway 'docker exec radon-services systemctl list-timers --all'
ssh radon@ib-gateway 'docker exec radon-services journalctl -u radon-cri-scan --since "1 hour ago"'
```

Service health for every dual-writing scheduler lands in the `service_health` table; the dashboard's status strip can render this without scraping logs.

## Rollback

The migration was implemented as dual-write at every step — every prior JSON read path is still valid as a fallback. To revert:

1. Comment out the `getDb()` calls in the relevant route (`web/app/api/<route>/route.ts`).
2. Comment out the `upsert_*` calls in the corresponding scheduler (`scripts/<script>.py`).
3. Restart Next.js + FastAPI.

The `data/*.json` files keep advancing on every cycle, so reverting is a no-data-loss change.

## Replica path & disk pressure

`data/replica.db` grows with every write. Plan a periodic vacuum + retention policy when the file exceeds ~100 MB:

```bash
sqlite3 data/replica.db 'VACUUM; PRAGMA wal_checkpoint(TRUNCATE);'
```

A nightly retention sweep (drop snapshots > 90 days from `cri_snapshots`, `gex_snapshots`, etc.) is a follow-up not yet wired.

## MenthorQ Playwright session refresh

When MenthorQ's session cookie rotates, the headless Playwright run will fail. To re-establish the session:

- **Hetzner mode**: `ssh radon@ib-gateway docker exec -it radon-services python3.13 scripts/cta_sync_service.py --interactive` — Playwright opens a VNC-visible Chrome for one-time MFA approval. Session persists to a named volume.
- **Local mode**: `python3 scripts/cta_sync_service.py --interactive` — opens a visible Chrome window on the laptop for MFA approval.

## Security

- **Turso auth token** — single shared token between laptop and Hetzner. Rotate via `turso db tokens create radon-joemccann`. Update both `.env` files.
- **Caddy admin API** — listens on localhost only. `caddy reload --config ~/radon-cloud/caddy/Caddyfile --adapter caddyfile` works without sudo via the `radon-caddy` sudoers rule (`sudo cp` + `systemctl reload caddy`).
- **media.radon.run** — public reads, no upload endpoint. If you ever gate access, swap the `file_server` block for `auth_request` calling Clerk-issued JWTs.

## Known gaps

| # | Item | Owner |
|---|------|-------|
| 1 | Nightly retention sweep on snapshot tables | Future |
| 2 | restic backup of `radon_media` volume to B2/S3 | Future |
| 3 | systemd timer for `oi_changes` (currently on-demand only) | Future |
| 4 | Vercel Edge replica for a public read-only dashboard | Future |
