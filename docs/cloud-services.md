# Cloud Services — Operator Runbook

This document covers Radon's two-mode architecture introduced in Phase 0–6 of the cloud-services migration. Both modes serve identical data to `localhost:3000` (laptop dev) and `app.radon.run` (Hetzner production).

## Architecture (TL;DR)

```
                         Turso Cloud DB (libSQL)
                       radon-joemccann.aws-us-west-2
                                  ▲
                ┌─────────────────┴─────────────────┐
                │   direct-to-cloud, no replica     │
                ▼                                   ▼
         LAPTOP dev process                 HETZNER production (<prod-host>)
         localhost:3000 (Next.js)           app.radon.run (Caddy → radon-nextjs)
         FastAPI 8321                       FastAPI 8321 (radon-api, private)
         IB realtime relay 8765             radon-relay, radon-monitor (host systemd)
         newsfeed scraper (Playwright)      newsfeed scraper (Playwright, optional)
                                            ib-gateway docker (4001)
                                            media.radon.run (Caddy static)
```

- **Database**: Turso (libSQL) — every Radon process talks **directly** to the cloud DB for both reads and writes. Direct-to-cloud is the code default (DUR-07; replica is opt-in only via `RADON_DB_USE_REPLICA=1`), and the prefix drop-in `/etc/systemd/system/radon-.service.d/common.conf` sets the `RADON_DB_NO_REPLICA=1` kill switch on every `radon-*` unit as belt-and-suspenders. The embedded-replica architecture (`data/replica.db`) was retired 2026-05-20 after two same-day incidents: multi-writer WAL checkpoint contention (radon-cloud `741cfc6`) followed by single-writer frame conflicts between the replica owner and direct-cloud writers (radon-cloud `2c46232`). The libsql embedded-replica model only works when ONE host has exactly ONE writer; Radon's split between Node and Python writers can't satisfy that constraint. Reads cost +30–60 ms cloud round-trip, absorbed by SWR caching. See `feedback_libsql_replica_one_writer.md` for the full failure-mode catalog.
- **Media**: Hetzner-hosted Caddy serves `https://media.radon.run`; the laptop's newsfeed scraper rsyncs new images over Tailscale.
- **Schedulers**: laptop launchd plists (local mode) OR Hetzner host systemd services (cloud mode). The `docker/services/` directory in this repo is a containerized alternative we designed but did not deploy — production currently uses host-installed services at `/home/radon/radon-cloud/services/*.service`.
- **Self-contained**: themarketear.com newsfeed scraper is now a headless Playwright flow that runs on either the laptop or Hetzner. No magic-link or Chrome Debug.app dependency.

## Newsfeed (`themarketear.com`) — Self-contained headless flow

The newsfeed used to be the only part of Radon that fundamentally required the laptop. As of `feature/newsfeed-headless`, the scraper drives Playwright's bundled Chromium, logs in with email + password, and persists the FirebaseUI session to `data/newsfeed-storage.json` (gitignored). It can run anywhere Chromium can launch.

**Required env (root `.env` — do NOT commit):**

```
THEMARKETEAR_EMAIL=ops@example.com
THEMARKETEAR_PASSWORD=<…>
# Optional: RADON_NEWSFEED_HEADLESS=0   # to launch a visible browser for debugging
```

**Operating procedure:**

1. **Laptop dev stack** — `npm run dev` keeps including the scraper as the 4th child and polls every 120s. No more "must keep Chrome Debug.app open" requirement.
2. **Standalone (laptop or Hetzner)** — `node scripts/newsfeed/index.js` runs forever; `node scripts/newsfeed/index.js --once` runs a single cycle (use for smoke tests).
3. **Storage state** — first launch authenticates with email + password (full FirebaseUI flow), then saves cookies + localStorage to `data/newsfeed-storage.json`. Subsequent runs reuse the session; the scraper still re-authenticates every ~6h to refresh cookies before they expire.
4. **Failure capture** — any login-flow failure dumps a screenshot to `data/newsfeed-debug-<ts>.png` (gitignored) for postmortem.
5. **Cookie rotation** — themarketear can rotate FirebaseUI cookies on its own; just delete `data/newsfeed-storage.json` and the next cycle will re-authenticate from scratch.

**Hetzner first-time setup:**

1. `scripts/deploy.sh` already runs `npx playwright install chromium` after the npm install (idempotent).
2. System libs (libnspr4, libnss3, libcups2, libxkbcommon0, libgbm1, …) require **one-time** sudo install:
   ```bash
   sudo apt-get update
   sudo npx playwright install-deps chromium    # installs all required apt packages
   ```
   Without these, the headless Chromium binary fails with `error while loading shared libraries: libnspr4.so`.
3. `THEMARKETEAR_EMAIL` + `THEMARKETEAR_PASSWORD` are appended to `/home/radon/radon-cloud/.env`.
4. **Hetzner runs it as `radon-newsfeed.service`** (enabled 2026-05-03 cutover). `Restart=on-failure`, `RestartSec=30`, `EnvironmentFile=/home/radon/radon-cloud/.env`. Steady-state cycle ~4s; first cold cycle does the FirebaseUI auth (~16s) then caches storage state. Tail logs:
   ```bash
   ssh root@ib-gateway "journalctl -u radon-newsfeed -f"
   ```
   To re-install on a fresh VPS or after `radon-newsfeed.service` was removed:
   ```bash
   sudo cp /home/radon/radon/docker/services/services/radon-newsfeed.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now radon-newsfeed.service
   ```
5. **`RADON_MEDIA_REMOTE` is a local fs path on Hetzner** — `/home/radon/radon-cloud/media/` (no `host:` prefix, no SSH). rsync does a local copy directly to the volume Caddy serves. The Tailscale and public-IP variants are laptop-only fallbacks (see below).
6. **Closing the laptop after the cutover does not break `app.radon.run`** — the newsfeed now runs entirely on Hetzner. No chrome-cdp, no Chrome Debug.app, no Tailscale dependency for new posts.

### Tailscale-free media push

The default rsync target (`radon@ib-gateway:/home/radon/radon-cloud/media/`) only resolves when Tailscale is up on the laptop. If the operator has shut Tailscale off (battery, conference WiFi, MagicDNS flake) the newsfeed cycle keeps scraping but logs `[push-media] non-fatal: rsync exit …` until the next cycle.

To bypass Tailscale and push over the Hetzner public IP, export the env override before running the scraper / dev stack:

```bash
export RADON_MEDIA_REMOTE=radon@<prod-host>:/home/radon/radon-cloud/media/
```

The same SSH public key is authorized on both routes — `~/.ssh/authorized_keys` on the VPS is shared between the Tailscale and public-IP entry points, so no key swap is needed. Tailscale remains the secure default; only flip the env when you actively want the public path. If the public route ever needs different SSH options (custom port, identity file, `StrictHostKeyChecking`), surface them via `RADON_MEDIA_RSYNC_SSH_OPTS` (not yet wired — add when you actually need it).

## Mode switch

| Action | Command |
|--------|---------|
| Switch to **Hetzner mode** | `scripts/cloud.sh` |
| Switch to **Local mode**   | `scripts/local.sh` |
| Inspect current mode       | `scripts/ib mode` |

`RADON_MODE` is persisted to `.env.ib-mode` at the project root (gitignored). All Python and Node entry points read this overlay file after `.env`, so the choice survives shell restarts.

`cloud.sh` runs a Step 0 preflight that scans the IPv4 routing table for a non-Tailscale tunnel interface (`utun*`, `ipsec*`, `ppp*`, `tun*`) owning a `default`, `0/1`, or `128.0/1` route — the signature of a third-party VPN (NordVPN, ProtonVPN, WireGuard, Cisco AnyConnect, Cloudflare WARP, OpenVPN, IKEv2, …) hijacking traffic. If found, the script bails before the TCP probe so the failure mode is named, not "Tailscale not running?". Tailscale's own interface is identified by its 100.64/10 IP and excluded, so `--exit-node` users don't false-positive.

## Deployment

### Production layout on Hetzner

```
/home/radon/
├─ radon/                    (git checkout — main branch, fast-forwarded by CI)
│  ├─ web/.next              (Next.js compile-mode build, regenerated each deploy)
│  └─ scripts/               (Python schedulers, direct-to-cloud writes via libsql client)
└─ radon-cloud/
   ├─ .env                   (TURSO_DB_URL, TURSO_AUTH_TOKEN, RADON_MODE=hetzner, …)
   ├─ caddy/Caddyfile        (app.radon.run + media.radon.run)
   ├─ media/                 (rsync target for newsfeed images)
   ├─ scripts/deploy.sh      (health-gated CI deploy)
   ├─ services/*.service     (radon-{nextjs,api,relay,monitor,refresh,ib-gateway};
   │                          shared env invariants live in the prefix drop-in
   │                          radon-.service.d/common.conf, e.g. RADON_DB_NO_REPLICA=1)
   └─ docker-compose.yml     (ib-gateway container)
```

`data/replica.db` is intentionally absent — the embedded-replica architecture was retired 2026-05-20. If the file appears on disk (stray from a pre-migration host), it is safe to `rm` — nothing reads from it.

Every `radon-*.service` uses `EnvironmentFile=/home/radon/radon-cloud/.env` so a single edit propagates to all schedulers. Restart with `sudo systemctl restart radon-{nextjs,api,relay,monitor}`.

**Whole-stack kill switch:** `/usr/local/bin/radon` wraps all units (IB Gateway included). Run on the VPS or remotely:

```bash
radon stop      # stop IB + radon-{api,relay,monitor,newsfeed,nextjs} + refresh.timer
radon start     # start them all (IB Gateway first)
radon restart   # stop + start
radon status    # systemctl list-units "radon-*"
```

From the laptop: `ssh root@ib-gateway radon stop`. Useful for off-hours shutdowns from iPhone/Termius without remembering the unit list. Installed manually 2026-05-04 — not in `setup-vps.sh` yet, so a `wipe-vps.sh` rebuild drops it.

### Day-to-day deploys

The deploy job is bound to the **`Production` GitHub Environment** (added
2026-07-03; its manual-approval rule was removed the same week at the
operator's request, so a green push deploys automatically again). The
environment remains as a non-blocking label; re-adding a required-reviewers
rule in repo Settings > Environments re-enables the gate without any yaml
change. On green, CI runs `bash scripts/deploy.sh` (from `radon-cloud`):

1. `git fetch origin main && git reset --hard origin/main` → applies repo changes.
2. `pip install -r requirements.txt` → picks up new Python deps (e.g. `libsql-experimental`).
3. `npm install && npm run build` → compile-mode build (no prerender, all routes dynamic).
4. Pre-teardown: `wait_for_gateway_ready` reads the FULL `:8321/health` (while the old radon-api still serves it) so the relay is never restarted into a mid-restart / awaiting-2FA gateway. Bounded 60s, warn-and-proceed.
5. `sudo systemctl restart radon-{nextjs,api,relay,monitor,newsfeed}` → reload services.
6. **Layered post-deploy gate** (`deploy_gate`) → rolls back to the previous commit on failure.

#### Post-deploy gate (DUR-05, 2026-06-12)

The gate measures only what the deployed code controls — it must never fail (or hang) for IB-side reasons. On 2026-06-11 the old gate curled the full `/health` of the freshly restarted radon-api; a wedged event loop hung the probe, the GitHub Actions step SIGTERMed at ~3min, and the deploy rolled back the fix for the very wedge being measured.

Layers, in order:

1. `systemctl is-active` on every restarted unit (`SERVICES` array) — instant.
2. `:8321/health/lite` — process-up probe, no IB probing. Bounded: 6 attempts × (5s curl timeout + 5s wait) = 60s worst case.
3. `:8330/status` (isolated health daemon) — **advisory log only**, never a rollback trigger: its verdict includes IB-gateway state. `:8330/healthz` is deliberately unused (vacuous zero-I/O 200).

The full `/health` (IB auth state) is used only by the pre-teardown `wait_for_gateway_ready`. Worst-case gate time ≈ 65s — well inside the CI step budget.

#### Escape hatches

| Env var | Effect |
|---|---|
| `RADON_DEPLOY_SKIP_PREFLIGHT=1` | Skips the required-env-var preflight only. |
| `RADON_DEPLOY_NO_GATE=1` | Still **runs** every gate layer and logs results loudly, but skips rollback-on-failure. Use to force-deploy a fix for a wedge class that kills `/health/lite` itself. |

#### Manual server-only deploy (when CI is unusable)

When Actions is down, the gate is wedged, or the fix must land NOW (push the commit to `origin/main` first so the VPS can fetch it):

```bash
ssh root@ib-gateway
sudo -u radon RADON_DEPLOY_NO_GATE=1 bash /home/radon/radon-cloud/scripts/deploy.sh
# then read the loud [gate] log lines and verify by hand:
systemctl status 'radon-*' --no-pager
curl -s http://localhost:8321/health/lite
curl -s http://localhost:8330/status
```

`deploy.sh` is sourceable (`main` is guarded behind a `BASH_SOURCE` check), so the gate can be dry-exercised without deploying: `bash -c 'source /home/radon/radon-cloud/scripts/deploy.sh; deploy_gate'`.

Related: `scripts/db/migrate.py` (radon-api `ExecStartPre`) retries transport-class Turso failures (Hrana / dns / timeout / connection) with 2s/5s/15s backoff before failing startup — a transient DNS blip hard-failed radon-api on 2026-06-12. SQL/schema errors still fail immediately.

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
| Cold-start a new laptop | Clone repo, `bun install`, set `TURSO_DB_URL` + `TURSO_AUTH_TOKEN`, run `bun run db:migrate`, then `scripts/cloud.sh`. No replica file to seed — every process talks directly to the cloud DB. |
| Cold-start a new VPS | Run `radon-cloud/scripts/setup-vps.sh`, configure the production `.env`, then use `/usr/local/bin/radon start`. Setup installs the lease-aware Gateway helper and every `radon-*.service`; no raw Compose start is permitted. Laptop's `scripts/cloud.sh` flips IB host to the new VPS through the same helper. |
| Stale `data/replica.db` from a pre-2026-05-20 host | `rm data/replica.db*` — nothing reads from it anymore. The libsql client opens cloud connections regardless of whether the file exists. |
| Turso outage | Read paths fall through to JSON files (dual-write retains them). Writes queue in the libsql client and replay when cloud returns. |
| Hetzner outage | Switch to `scripts/local.sh`. Laptop becomes self-sufficient against local Docker IB Gateway. |

## Health & observability

```bash
# Laptop
curl http://localhost:8321/health | jq          # FastAPI + IB Gateway

# Query Turso directly (no replica — all reads round-trip)
PYTHONPATH=scripts python3.13 -c "from db.client import get_db; \
    print(get_db().execute('SELECT service, state, updated_at FROM service_health').rows)"

# Hetzner
ssh radon@ib-gateway 'systemctl list-units "radon-*"'
ssh radon@ib-gateway 'journalctl -u radon-api --since "1 hour ago"'
```

Service health for every dual-writing scheduler lands in the `service_health` table; the dashboard's status strip can render this without scraping logs.

### Next.js DB-read deep probe (2026-07-03)

The health daemon's `radon-nextjs` probe is TCP-liveness only, so a process
that is alive but cannot read Turso (the 2026-07-02 destroy-storm class)
looked healthy to every monitoring layer. `radon-nextjs-db-watchdog.timer`
(radon-cloud `services/`, 60s, 24/7) closes the gap: it curls
`localhost:3000/api/service-health` — a real Turso read through the Next.js
process — and judges the **body** (the route returns HTTP 200 with a
synthetic `turso-db` error row when its DB read fails). K=3 consecutive
wedge cycles plus a Python-side canary read (proving Turso itself is fine,
so a restart will actually help) → solo `systemctl restart radon-nextjs`
(no PartOf/BindsTo — no cascade), 10 min restart cooldown. Heartbeats the
`nextjs-db-read` service_health row every clean cycle; state in
`radon-cloud/state/nextjs-db-watchdog.json`. Script:
`radon-cloud/scripts/nextjs_db_watchdog.py`.

### CRI read-stall incident (2026-07-12)

Repeated Next.js Turso timeouts were caused by a query-plan regression, not a
Turso outage or the VPS network path. The latest-CRI query used
`ORDER BY taken_at DESC LIMIT 1`; `cri_snapshots` has no index beginning with
`taken_at`, so SQLite scanned 14,924 rows and built a temporary sort over rows
whose payload averaged roughly 47 KB. Production latency was 6.8-7.6 seconds.
The route's 2.5-3 second caller deadlines expired first, reset the shared
undici/libSQL client, and created collateral portfolio, internals, gamma, and
service-health read failures.

The durable query is:

```sql
SELECT taken_at, payload
FROM cri_snapshots
ORDER BY date DESC, taken_at DESC
LIMIT 1;
```

This uses the existing `idx_cri_latest(date DESC, taken_at DESC)` index. After
deploying `ea24c66c1e337cbd4e2c28bd66142f9c936f8e42`, `EXPLAIN QUERY PLAN`
reported `SCAN cri_snapshots USING INDEX idx_cri_latest`; 20 live reads measured
p50 8.3 ms, p95 28.1 ms, and max 29.7 ms.

Transport containment is intentionally aligned with caller behavior:
`DB_TRANSPORT_TIMEOUT_MS=2750` aborts and releases the socket before the common
3-second route deadline, while the keepalive runs every 30 seconds with a
40-second idle-socket window. Do not set a transport timeout longer than its
caller deadline: a timed-out caller otherwise leaves the pool slot occupied and
turns one slow query into queue amplification. Do not respond to every isolated
timeout by destroying the shared Agent; the cooldown/evidence rules from the
2026-07-02 incident remain mandatory.

Diagnosis order for a recurrence:

1. Measure the exact hot SQL and run `EXPLAIN QUERY PLAN` against production.
2. Measure fresh native-libSQL and Hrana `SELECT 1` reads from the VPS to
   separate provider/network latency from query cost.
3. Inspect pool running/queued counts before attributing the event to pool
   saturation.
4. Confirm `nextjs-db-read` refreshes cleanly after deployment; TCP or HTTP
   liveness alone does not prove the application can read Turso.

The portfolio snapshot warning seen during this incident had a separate writer
failure: IB connection timeout prevented the scheduled refresh. The shell
wrapper also captured `$?` after an `if`, falsely printing `FAILED (exit 0)` and
returning success. `scripts/run_portfolio_refresh.sh` now preserves curl's exit
status and returns 22 for non-2xx responses. A stale snapshot with “live sync was
not requested” means the cache-only GET path deliberately did not contact IB; it
does not mean Turso necessarily failed.

### Host metrics (DUR-12)

`scripts/host_metrics_sampler.py` (main repo, stdlib-only) runs every minute on the VPS via `radon-host-metrics.timer` (radon-cloud) and writes one row per run to the Turso `host_metrics` table (migration 0012): CPU % from a 1s `/proc/stat` delta, memory + swap from `/proc/meminfo`, `load1`, per-`radon-*`-unit ActiveState/NRestarts, and the FastAPI event-loop lag exposed as `loop_lag_ms` on `/health/lite`. Writes ride the bounded hrana path (`scripts/db/hrana_http.py`) with a capped JSONL fallback at `data/host_metrics_fallback.jsonl`; every run heartbeats `service_health[host-metrics]` (10-min freshness window). Retention is 14 days, pruned hourly by the sampler. The `/admin` page renders the latest values + 1h sparkline via `GET /api/admin/host-metrics`.

### Log shipping (DUR-12)

journald on the VPS is on-box only (capped at 1G). A laptop launchd job (`~/Library/LaunchAgents/com.radon.journal-pull.plist`, daily + RunAtLoad) runs `scripts/journal_pull.sh`, which ssh-pulls `journalctl --since yesterday -o export | gzip` into `data/journal_archive/` (gitignored) and prunes local snapshots older than 30 days. Laptop-initiated by design — VPS-push to a sleeping laptop fails silently (media-rsync precedent). Inspect a snapshot with `zcat <file> | journalctl --file=- ...` or `gunzip` + `journalctl --root` import tooling.

### Error tracking — Sentry (not wired; recommended next step)

No Sentry SDK is installed (no DSN exists; an unconfigured SDK is dead-weight). When ready, the free tier (5k errors/mo) is plenty for a solo operator:

1. Create a Sentry org + two projects: `radon-api` (Python) and `radon-web` (Next.js). Copy each DSN.
2. Put DSNs in `/home/radon/radon-cloud/.env` (`SENTRY_DSN_API`) and `web/.env` (`NEXT_PUBLIC_SENTRY_DSN`); never in the repo.
3. FastAPI: `pip install sentry-sdk`, then in `scripts/api/server.py` startup gate on the env var — `sentry_sdk.init(dsn, traces_sample_rate=0)` (errors only; tracing would duplicate what host_metrics already covers). The asyncio + FastAPI integrations are automatic.
4. Next.js: `@sentry/nextjs` via the wizard, but keep `tracesSampleRate: 0` and disable session replay — error capture only. Mind the Edge-runtime middleware constraint (`feedback_middleware_edge_runtime`): do not import Sentry helpers into `web/middleware.ts`.
5. Set both projects' alert rule to "new issue" → the existing Pushover webhook, so paging stays single-channel.

Until then, errors surface via `service_health` rows (the watchdog buckets page on them) and the journald snapshots above.

## Rollback

The migration was implemented as dual-write at every step — every prior JSON read path is still valid as a fallback. To revert:

1. Comment out the `getDb()` calls in the relevant route (`web/app/api/<route>/route.ts`).
2. Comment out the `upsert_*` calls in the corresponding scheduler (`scripts/<script>.py`).
3. Restart Next.js + FastAPI.

The `data/*.json` files keep advancing on every cycle, so reverting is a no-data-loss change.

## MenthorQ Playwright session refresh

When MenthorQ's session cookie rotates, the headless Playwright run will fail. To re-establish the session:

- **Hetzner mode**: `ssh radon@ib-gateway docker exec -it radon-services python3.13 scripts/cta_sync_service.py --interactive` — Playwright opens a VNC-visible Chrome for one-time MFA approval. Session persists to a named volume.
- **Local mode**: `python3 scripts/cta_sync_service.py --interactive` — opens a visible Chrome window on the laptop for MFA approval.

## Security

- **Turso auth token** — single shared token between laptop and Hetzner. Rotate via `turso db tokens create radon-joemccann`. Update both `.env` files.
- **Caddy admin API** — listens on localhost only. `caddy reload --config ~/radon-cloud/caddy/Caddyfile --adapter caddyfile` works without sudo via the `radon-caddy` sudoers rule (`sudo cp` + `systemctl reload caddy`).
- **media.radon.run** — public reads, no upload endpoint. If you ever gate access, swap the `file_server` block for `auth_request` calling Clerk-issued JWTs.

## DB backup & restore (DUR-13)

The Turso `journal` table is the canonical trade store; the JSON mirrors
in `data/` are frequently stale and are NOT a disaster-recovery story.
Nightly full-database dumps are the recovery story.

### Architecture

| Piece | Where | What |
|---|---|---|
| `radon-db-backup.timer` | VPS (`radon-cloud/services/`) | Nightly 07:52 UTC (off-hours, deliberately not :00/:30), `Persistent=true` |
| `radon-db-backup.service` | VPS | Oneshot, `User=radon`, `TimeoutStartSec=3600` (libsql has no client timeouts — the unit bound is the real one) |
| `radon-cloud/scripts/db_backup.py` | VPS | Iterates `sqlite_master` — the ENTIRE DB, no hand-picked table list, so new migration tables are captured automatically. Paged `SELECT`s (500 rows/page; the DB is ~1.4 GB, direct-to-cloud reads run ~1 MB/s ⇒ ~20–25 min). Emits portable SQL (schema + INSERTs, `sqlite3 .dump`-style), gzip'd to `/home/radon/radon-cloud/backups/db/radon-<UTC>.sql.gz`. Prunes dumps older than 30 days in-script. |
| `service_health` heartbeat | row `db-backup` | Written on EVERY run — `ok` with `{size_bytes, duration_secs, tables, rows, pruned}` detail, `error` with the failure summary. 48h freshness window (`web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py` daily bucket), so ONE missed night alerts before the second dump is lost. A backup timer with no liveness signal is the canonical silently-dead backup. |
| `com.radon.db-backup-pull` | laptop launchd (`~/Library/LaunchAgents/`) | `RunAtLoad` + daily 08:37 local (after the 08:23 journal pull); runs `scripts/db_backup_pull.sh` (journal-pull pattern): rsyncs the dump dir over Tailscale (`radon@ib-gateway`, same key as the media push) into `data/db_backups/` (gitignored), fails loudly if no non-empty dump landed, prunes local copies older than 30 days. Deliberately NO `--delete`: a wiped/compromised VPS must not be able to empty the off-box copy on the next pull. |

### Restore runbook

**1. Scratch restore (verify a dump / inspect old data)** — plain sqlite3,
no Turso involved:

```bash
gunzip -c data/db_backups/radon-<stamp>.sql.gz | sqlite3 /tmp/radon_restore.db
sqlite3 /tmp/radon_restore.db "SELECT COUNT(*) FROM journal; SELECT COUNT(*) FROM service_health;"
```

Run this drill after any change to `db_backup.py` and compare counts
against prod (`PYTHONPATH` + `get_db()` per Health & observability above).
Last drill 2026-06-12: 37 tables / 80,171 rows round-tripped exactly
(`PRAGMA integrity_check` ok); `journal`/`executed_orders` matched prod,
remaining deltas were post-dump live drift only (`service_health` +1 =
host-metrics first heartbeat, `posts` +1, `portfolio_snapshots` +58).

**2. Full restore to a NEW Turso DB + URL swap** (DB lost/corrupted):

```bash
turso auth login                                   # CLI: /opt/homebrew/bin/turso (laptop)
turso db create radon-restore-$(date +%Y%m%d)
gunzip -c data/db_backups/radon-<stamp>.sql.gz | turso db shell radon-restore-<date>
turso db tokens create radon-restore-<date>
turso db show radon-restore-<date> --url
```

Then swap `TURSO_DB_URL` + `TURSO_AUTH_TOKEN` in ALL THREE env files —
laptop root `.env`, laptop `web/.env`, VPS `/home/radon/radon-cloud/.env` —
and restart the stack (`ssh root@ib-gateway radon restart`; mind the 2FA
push-lock rules). Do NOT repoint by editing the old DB in place.

**3. Partial-table surgery** (bad rows written to one table — the
2026-05-14 MagicMock incident wrote garbage contracts to the prod
`journal` and recovery was manual row surgery):

```bash
# Restore the last-good dump into a scratch DB (step 1), then diff:
sqlite3 /tmp/radon_restore.db "SELECT ib_exec_id FROM journal" | sort > /tmp/good_ids
# Delete only the poisoned rows from prod via get_db(), keyed on ib_exec_id
# (or INSERT the good rows back). NEVER DROP/replace the prod table wholesale —
# writers are live against it.
```

**4. Platform PITR** — OPEN QUESTION. The `turso` CLI is not
authenticated on the laptop and absent on the VPS, so whether the current
plan includes point-in-time restore is unverified. Operator: `turso auth
login && turso org show` / check the Turso dashboard. Until verified,
treat the nightly dumps as the only restore path.

## Portfolio archive + snapshot retention (R1 / R2)

| Piece | When (UTC) | What |
|---|---|---|
| `radon-portfolio-archive.timer` | 06:52 daily | Cold-archives `portfolio_snapshots` older than 30d to local monthly `jsonl.gz` partitions, then DELETEs from Turso (optional S3 via `RADON_ARCHIVE_S3_*`). Heartbeat: `portfolio-archive`. |
| `radon-db-retention.timer` | 07:22 daily | Keep-latest prune on append-only scan tables (gex/vcg/scanner/…); never touches journal or portfolio. Heartbeat: `db-retention`. |
| `radon-db-backup.timer` | 07:52 daily | Full dump **after** archive + retention so the dump reflects the pruned hot set. |

## Known gaps

| # | Item | Owner |
|---|------|-------|
| 1 | ~~Nightly retention sweep on snapshot tables~~ | **Done** — `radon-portfolio-archive` + `radon-db-retention` (2026-07-11) |
| 2 | restic backup of `radon_media` volume to B2/S3 (DB backups shipped 2026-06-12 — see "DB backup & restore" above; media volume still unbacked) | Future |
| 3 | systemd timer for `oi_changes` (currently on-demand only) | Future |
| 4 | Vercel Edge replica for a public read-only dashboard | Future |
| 5 | Verify Turso plan PITR (see restore runbook §4) | Operator |
| 6 | Configure `RADON_ARCHIVE_S3_*` so portfolio archive uploads off-box (unit currently allows local-verified delete until S3 is set) | Operator |
