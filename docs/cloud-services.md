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
         LAPTOP dev process                 HETZNER production (5.78.148.38)
         localhost:3000 (Next.js)           app.radon.run (Caddy → radon-nextjs)
         FastAPI 8321                       FastAPI 8321 (radon-api, private)
         IB realtime relay 8765             radon-relay, radon-monitor (host systemd)
         newsfeed scraper (chrome-cdp)      ib-gateway docker (4001)
                                            media.radon.run (Caddy static)
```

- **Database**: Turso (libSQL) — every Next.js / FastAPI / scheduler process holds a SQLite-fast embedded replica, writes go to cloud and stream back.
- **Media**: Hetzner-hosted Caddy serves `https://media.radon.run`; the laptop's newsfeed scraper rsyncs new images over Tailscale.
- **Schedulers**: laptop launchd plists (local mode) OR Hetzner host systemd services (cloud mode). The `docker/services/` directory in this repo is a containerized alternative we designed but did not deploy — production currently uses host-installed services at `/home/radon/radon-cloud/services/*.service`.
- **Browser-bound**: themarketear.com newsfeed scraper always runs on the laptop (magic-link login can't be automated). Everything else can run anywhere.

## Newsfeed (`themarketear.com`) — Always-on dependency

The newsfeed is the **only** part of Radon that fundamentally requires the laptop. Every other service runs on Hetzner. Operating procedure:

1. Keep `Chrome Debug.app` running with port 9222 + an authenticated `themarketear.com` tab. (`scripts/cdp.mjs list` should show the tab.)
2. Keep `Tailscale` connected on the laptop so `push_media.js` can rsync new images to `radon@ib-gateway:/home/radon/radon-cloud/media/`.
3. Keep `npm run dev` (or `scripts/cloud.sh` / `scripts/local.sh`) running — the newsfeed scraper is the 4th child and polls every 120s.
4. **If you want the newsfeed to keep updating without the full dev stack**, run *only* the scraper: `node scripts/newsfeed/index.js`. It needs `web/.env` (CEREBRAS_API_KEY + ANTHROPIC_API_KEY) and the root `.env` (TURSO_DB_URL + TURSO_AUTH_TOKEN). Both peers (`localhost:3000` + `app.radon.run`) read newly-arrived posts immediately.
5. Closing the laptop does NOT break `app.radon.run` — the dashboard keeps rendering the last-known posts.json from the DB, just no new arrivals until the laptop is back online.

If the themarketear cookie ever rotates, log in fresh in the Chrome Debug.app tab; the scraper picks up the new session on the next cycle (`fetchCookieHeader` reads cookies via `Network.getCookies`).

### Tailscale-free media push

The default rsync target (`radon@ib-gateway:/home/radon/radon-cloud/media/`) only resolves when Tailscale is up on the laptop. If the operator has shut Tailscale off (battery, conference WiFi, MagicDNS flake) the newsfeed cycle keeps scraping but logs `[push-media] non-fatal: rsync exit …` until the next cycle.

To bypass Tailscale and push over the Hetzner public IP, export the env override before running the scraper / dev stack:

```bash
export RADON_MEDIA_REMOTE=radon@5.78.148.38:/home/radon/radon-cloud/media/
```

The same SSH public key is authorized on both routes — `~/.ssh/authorized_keys` on the VPS is shared between the Tailscale and public-IP entry points, so no key swap is needed. Tailscale remains the secure default; only flip the env when you actively want the public path. If the public route ever needs different SSH options (custom port, identity file, `StrictHostKeyChecking`), surface them via `RADON_MEDIA_RSYNC_SSH_OPTS` (not yet wired — add when you actually need it).

## Mode switch

| Action | Command |
|--------|---------|
| Switch to **Hetzner mode** | `scripts/cloud.sh` |
| Switch to **Local mode**   | `scripts/local.sh` |
| Inspect current mode       | `scripts/ib mode` |

`RADON_MODE` is persisted to `.env.ib-mode` at the project root (gitignored). All Python and Node entry points read this overlay file after `.env`, so the choice survives shell restarts.

## Deployment

### Production layout on Hetzner

```
/home/radon/
├─ radon/                    (git checkout — main branch, fast-forwarded by CI)
│  ├─ web/.next              (Next.js compile-mode build, regenerated each deploy)
│  ├─ scripts/               (Python schedulers, dual-write to Turso)
│  └─ data/replica.db        (libSQL embedded replica)
└─ radon-cloud/
   ├─ .env                   (TURSO_DB_URL, TURSO_AUTH_TOKEN, RADON_MODE=hetzner, …)
   ├─ caddy/Caddyfile        (app.radon.run + media.radon.run)
   ├─ media/                 (rsync target for newsfeed images)
   ├─ scripts/deploy.sh      (health-gated CI deploy)
   ├─ services/*.service     (radon-{nextjs,api,relay,monitor,refresh,ib-gateway})
   └─ docker-compose.yml     (ib-gateway container)
```

Every `radon-*.service` uses `EnvironmentFile=/home/radon/radon-cloud/.env` so a single edit propagates to all schedulers. Restart with `sudo systemctl restart radon-{nextjs,api,relay,monitor}`.

### Day-to-day deploys

`.github/workflows/deploy.yml` runs `bash scripts/deploy.sh` on every push to `main`:

1. `git fetch origin main && git reset --hard origin/main` → applies repo changes.
2. `pip install -r requirements.txt` → picks up new Python deps (e.g. `libsql-experimental`).
3. `npm install && npm run build` → compile-mode build (no prerender, all routes dynamic).
4. `sudo systemctl restart radon-nextjs radon-api radon-relay radon-monitor` → reload services.
5. Health check `curl http://localhost:8321/health` with retries → rolls back to previous commit on failure.

### Build constraint

`web/package.json` runs `next build --experimental-build-mode=compile` because Next.js 16's standard build crashes during prerender of `/_global-error` and `/_not-found` (the root ClerkProvider context isn't materialised in isolated workers — `useContext` returns null). Compile mode skips prerender entirely; every page is `force-dynamic` already so the runtime behavior is unchanged. If a future Next.js patch fixes the underlying issue, drop the flag and the build returns to the standard pipeline.

### Containerized scheduler alternative (not currently deployed)

The repo also includes `docker/services/Dockerfile` + `docker/services/docker-compose.yml` describing a single Python+Node+Playwright container with systemd timers. This is the design from Phase 4 of the migration plan — kept as committed config in case the host-systemd setup is ever replaced with a containerized one. Production today uses host systemd.

**Do not deploy from this branch.** The plan document explicitly forbids automatic prod deploys until rollback paths are exercised.

## Trades — single source of truth

The Turso `journal` table is the canonical store for executed trades. Both
the `/journal` and `/orders` pages derive their view from the same rows:

- `web/app/api/journal/route.ts` reads `journal` directly (one row per
  execution-grouped action) and returns it as `{ trades: [...] }`.
- `web/app/api/blotter/route.ts` reads the same rows AND `data/blotter.json`,
  then unions them through `web/lib/blotter/fromJournal.ts:journalRowsToBlotter()`
  into the historical-trades shape that
  `WorkspaceSections.HistoricalTradesSection` consumes (closed/open arrays,
  executions, cost basis, proceeds). The union prefers explicit P&L fields
  on journal rows when present (post-`bbc776e` rehydrate), and falls back
  to `data/blotter.json` for `realized_pnl` / `cost_basis` / `proceeds`
  when the journal row is from before the lot-matched fields existed.
  Trades present only in legacy are spliced into the output. As soon as
  the next IB Flex re-rehydrate runs, journal rows take precedence and the
  fallback retires per-row.
- Writers: `scripts/journal_rehydrate.py` (Flex Query 1442520, working,
  ≤365d backfill) and `scripts/monitor_daemon/handlers/journal_sync.py`
  (live IB session fills via `client.get_fills()`). Both dual-write to
  `data/trade_log.json` and the `journal` table; both use `ib_exec_id`
  for idempotent dedupe.

`data/blotter.json` (Flex Query 1422766, broken IB-side at 2026-03-26)
and `data/trade_log.json` are **file mirrors / fallbacks** — the
`/orders` route reads them alongside the journal so historical P&L is
preserved while the journal table is being re-rehydrated. The
standalone Flex Query 1422766 path lives on as the `POST /api/blotter`
refresh hook for backwards compatibility but
`scripts/trade_blotter/flex_query.py` and `blotter_service.py` are
marked deprecated. Don't extend them.

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
