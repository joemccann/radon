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
- **Schedulers**: laptop launchd plists (local mode) OR Hetzner host systemd (cloud mode). Production scheduling remains host systemd. Installed per-unit drop-ins run Next.js, FastAPI, relay, monitor, and newsfeed in exact-SHA app containers; timer-owned oneshots remain on the host. Unit sources live in `/home/radon/radon/cloud/services/` and are installed through the reviewed control-plane path. The former `docker/services/` tree was deleted as decoy units in `40cfff2a` and is not a scheduler alternative.
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

1. The reviewed deploy transaction installs the Playwright browser dependency before restarting the newsfeed service (idempotent).
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
   For a missing or changed unit, use the reviewed root control-plane procedure
   from [`cloud/CLAUDE.md`](../cloud/CLAUDE.md); do not copy a unit from an
   obsolete containerized-services path.
5. **`RADON_MEDIA_REMOTE` is a local fs path on Hetzner** — `/var/lib/radon/media/` (no `host:` prefix, no SSH). `/home/radon/radon-cloud/media` is a compatibility symlink. The Tailscale and public-IP variants are laptop-only fallbacks (see below).
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
│  ├─ scripts/               (Python schedulers, direct-to-cloud writes via libsql client)
│  └─ cloud/                 (canonical deploy tooling, Caddy, Compose, and unit sources)
└─ radon-cloud/
   ├─ .env                   (external secrets only: TURSO_DB_URL, TURSO_AUTH_TOKEN, RADON_MODE=hetzner, …)
   └─ media/                 (rsync target for newsfeed images)
```

`data/replica.db` is intentionally absent — the embedded-replica architecture was retired 2026-05-20. If the file appears on disk (stray from a pre-migration host), it is safe to `rm` — nothing reads from it.

Every `radon-*.service` (except `radon-grok-page-responder`) uses `EnvironmentFile=/etc/radon/env`. `/home/radon/radon-cloud/.env` is a compatibility symlink to that file. Media is `/var/lib/radon/media` (Caddy `media.radon.run`); `/home/radon/radon-cloud/media` is a compatibility symlink. The legacy directory is not a deploy source.

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
2026-07-03; its required-reviewer rule was removed on 2026-07-15 at the
operator's request, so a green push deploys automatically). The environment
remains for deployment history, URL metadata, environment-scoped configuration,
and a custom branch policy that allows only `main`. Re-adding a required-reviewers
rule in repo Settings > Environments would re-enable the gate without any YAML
change. On green, CI extracts `cloud/` from the exact tested SHA into an
immutable runner under `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`.
Before builds, teardown, or transition-journal writes, the runner verifies the
installed root control plane against its manifest. The canonical lifecycle and
recovery procedure is [`cloud/CLAUDE.md`](../cloud/CLAUDE.md); do not invoke a
legacy `radon-cloud/scripts/deploy.sh` path manually.

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

A control-plane edit no longer needs this: the deploy job runs
`radon-deploy-root sync-control-plane` before `deploy.sh` and installs the
GitHub-main-tip bundle itself (R-430). When Actions is down or a deploy
requires privileged recovery, preserve the manifest boundary: compare `/home/radon/radon` to the exact tested SHA,
fast-forward as `radon` if it is behind, run the root bootstrap from that
checkout, then rerun CI. The command sequence and live verification contract
are maintained in [`cloud/CLAUDE.md`](../cloud/CLAUDE.md) and
[`docs/monorepo-cloud-migration.md`](monorepo-cloud-migration.md). Do not use
`RADON_DEPLOY_NO_GATE` or a legacy `radon-cloud/scripts/deploy.sh` invocation
to bypass a control-plane mismatch.

Related: `scripts/db/migrate.py` (radon-api `ExecStartPre`) retries transport-class Turso failures (Hrana / dns / timeout / connection) with 2s/5s/15s backoff before failing startup — a transient DNS blip hard-failed radon-api on 2026-06-12. SQL/schema errors still fail immediately.

### Build constraint

`web/package.json` runs `next build --experimental-build-mode=compile` because Next.js 16's standard build crashes during prerender of `/_global-error` and `/_not-found` (the root ClerkProvider context isn't materialised in isolated workers — `useContext` returns null). Compile mode skips prerender entirely; every page is `force-dynamic` already so the runtime behavior is unchanged. If a future Next.js patch fixes the underlying issue, drop the flag and the build returns to the standard pipeline.

### Runtime planes

Production is three planes: host, broker, and app. IB Gateway runs from `cloud/docker-compose.yml`; the five long-lived app services run from exact-SHA `docker/app` images through installed per-unit `runtime-container.conf` drop-ins; timer-owned oneshots remain host systemd. Main CI gates deploy on both app images, pre-pulls them in parallel with prestage, and the runtime refuses `latest`. Do not install the fleet `radon-.service.d` example because it would override Gateway and health. The former `docker/services/` tree was deleted as decoy units in `40cfff2a` and is not a scheduler alternative.

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
- Gap sensors (alert-only, pure Turso): daily `journal-reconcile`
  (monitor daemon, 26h window) and continuous `journal-gap-sli` (every
  5m; `service_health.last_error` carries structured
  `missing_exec_id_count` + sample `gap_exec_ids` over the same 7d
  rolling window, with a 10m min-age so live fills racing
  `journal_sync` do not false-positive). Repair with
  `scripts/backfill_journal_from_executed_orders.py`.

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
(radon-cloud `services/`, 60s, 24/7) closes the gap: it GETs
`localhost:3000/api/service-health` with
`Authorization: Bearer $RADON_PROBE_FRESHNESS_TOKEN` (the route is not
public) and judges the **body**. HTTP 200 with a synthetic `turso-db`
error row is a wedge. HTTP 401/403 or a missing token is unknown: stand
down, do not restart. K=3 consecutive
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

### Bounded vs process-bound Turso writes (R5 partial)

`libsql_experimental` has **no client timeouts** and holds the GIL while blocked. Prefer `scripts/db/hrana_http.py` (real `urllib` socket timeout) for hang-risk writers; leave bulk/oneshot scan writers on sync libsql under process supervision. Full inventory: module docstring of `scripts/db/client.py`.

| Path | Transport | Why |
|---|---|---|
| `record_service_health` (daemon / service_cycle / scan_mirror) | hrana (`write_service_health_http` + `service_health_sql`) | Long-lived + frequent heartbeats |
| `upsert_journal_entry` (fill_monitor / journal_sync) | hrana | Daemon fill path |
| `upsert_portfolio_snapshot` | hrana | High-volume single-row dual-write |
| portfolio DELETE / retention sweep / host_metrics / ib_watchdog | hrana | Already migrated |
| FastAPI Turso I/O | `api.db_http` only | Sync libsql banned in process |
| Other scan/snapshot upserts, cash_flows, prunes | sync libsql | Process-bound: `run_script` timeout / `TimeoutStartSec` / `RuntimeMaxSec` |

### Log shipping (DUR-12)

journald on the VPS is on-box only (capped at 1G). A laptop launchd job (`~/Library/LaunchAgents/com.radon.journal-pull.plist`, daily + RunAtLoad) runs `scripts/journal_pull.sh`, which ssh-pulls `journalctl --since yesterday -o export | gzip` into `data/journal_archive/` (gitignored) and prunes local snapshots older than 30 days. Laptop-initiated by design — VPS-push to a sleeping laptop fails silently (media-rsync precedent). Inspect a snapshot with `zcat <file> | journalctl --file=- ...` or `gunzip` + `journalctl --root` import tooling.

### Grok P1 responder (dedicated VPS clone)

`radon-grok-page-responder.timer` claims `watchdog_pages` from
`/home/radon/radon-page-responder` with a stripped env
(`/home/radon/radon-page-responder.env`). It must not use
`/home/radon/radon` or `/home/radon/radon-cloud/.env`. Laptop launchd is
off. Spec: [`grok-page-responder.md`](grok-page-responder.md).
Do not install this on `~/radon-weekend/radon` (that clone hard-resets).

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
- **Caddy admin API** — listens on localhost only. The canonical source is `/home/radon/radon/cloud/caddy/Caddyfile`; apply changes through the reviewed control-plane path, not a legacy `~/radon-cloud/caddy` checkout.
- **media.radon.run** — public reads, no upload endpoint. If you ever gate access, swap the `file_server` block for `auth_request` calling Clerk-issued JWTs.

## DB backup & restore (DUR-13)

The Turso `journal` table is the canonical trade store; the JSON mirrors
in `data/` are frequently stale and are NOT a disaster-recovery story.
Nightly full-database dumps are the recovery story for the whole DB.
Portfolio snapshot **history** older than ~30d is additionally cold-archived
to **Backblaze B2** (see Portfolio archive below) so the hot Turso table stays
bounded.

### Architecture

| Piece | Where | What |
|---|---|---|
| `radon-db-backup.timer` | VPS | Nightly **09:00 UTC** (after archive 05:40 + retention 08:10), `Persistent=true` |
| `radon-db-backup.service` | VPS | Oneshot, `User=radon`, `TimeoutStartSec=3600` (libsql has no client timeouts — the unit bound is the real one) |
| `radon-cloud/scripts/db_backup.py` / monorepo `cloud/scripts/db_backup.py` | VPS | Iterates `sqlite_master` — the ENTIRE DB, no hand-picked table list, so new migration tables are captured automatically. Paged `SELECT`s (500 rows/page). Emits portable SQL (schema + INSERTs), gzip'd to `/home/radon/radon-cloud/backups/db/radon-<UTC>.sql.gz`. Prunes dumps older than `RETENTION_DAYS` (7) in-script, and only those present in B2 once the off-box leg has run (R-445). |
| `service_health` heartbeat | row `db-backup` | Written on EVERY run — `ok` with `{size_bytes, duration_secs, tables, rows, pruned}` detail, `error` with the failure summary. 48h freshness window. |
| `com.radon.db-backup-pull` | laptop launchd | Daily rsync of dump dir over Tailscale into `data/db_backups/` (no `--delete`). |

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

**4. Platform PITR (Turso Point-in-Time Recovery)** — **verified 2026-07-13**

```bash
turso auth login          # browser OAuth once
turso plan show           # org plan + quotas (not `turso org show`)
turso db list             # radon / radon-demo
turso db show radon
```

**Recorded org (personal / slug `joemccann`):**

| Field | Value |
|---|---|
| Plan | **Pro** (overages enabled) |
| PITR window | **90 days** ([pricing](https://turso.tech/pricing)) |
| Prod DB | `radon` → `libsql://radon-joemccann.aws-us-west-2.turso.io` |
| Size (2026-07-13) | ~670 MB; group `default` Healthy; delete protection **on** |
| Storage quota | 1.7 GB / 50 GB |

Plan matrix (for reference if the org ever changes plan):

| Plan | PITR window |
|---|---|
| Free | 1 day |
| Developer | 10 days |
| Scaler | 30 days |
| Pro | 90 days |

Restore creates a **new** database (does not rewrite live in place):

```bash
turso db create radon-pitr-$(date +%Y%m%d) \
  --from-db radon \
  --timestamp 2026-07-12T16:00:00Z
turso db tokens create radon-pitr-$(date +%Y%m%d)
turso db show radon-pitr-$(date +%Y%m%d) --url
```

Then inspect read-only or swap `TURSO_DB_URL` / `TURSO_AUTH_TOKEN` like full-restore step 2. Docs: [PITR](https://docs.turso.tech/features/point-in-time-recovery).

**Radon RPO (defense in depth):**

| Layer | RPO (worst case) | Covers |
|---|---|---|
| Turso platform PITR | **90 days** (Pro) | Cloud DB to a commit timestamp |
| Nightly SQL dump + laptop pull | ~24h (+ pull lag) | Full logical DB if Turso account is gone |
| B2 portfolio cold-archive | Continuous for pruned months | `portfolio_snapshots` history |
| B2 media backup | Nightly | `media.radon.run` tree |

Re-check with `turso plan show` after any plan change. Nightly dumps remain mandatory.

## Portfolio archive + snapshot retention (R1 / R2)

### Nightly schedule (do not overlap on Turso writes)

| Piece | When (UTC) | What |
|---|---|---|
| `radon-portfolio-archive.timer` | **05:40** | Cold-archive `portfolio_snapshots` older than 30d → local monthly `jsonl.gz` → **Backblaze B2** upload → DELETE from Turso. Heartbeat: `portfolio-archive`. `TimeoutStartSec=7200`. |
| `radon-db-retention.timer` | **08:10** | Keep-latest prune on append-only scan tables (gex/vcg/scanner/…); never touches journal or portfolio. Heartbeat: `db-retention`. |
| `radon-db-backup.timer` | **09:00** | Full Turso dump after archive + retention. |
| `radon-media-backup.timer` | **10:15** | Mirror `media.radon.run` tree (`/home/radon/radon-cloud/media`) → B2 prefix `media/`. Heartbeat: `media-backup`. `TimeoutStartSec=3600`. |

## Disk cleanup (weekly)

2026-08-27: the watchdog `root-disk-usage` check paged P1 at 98% of the 75G
root filesystem (1.7 GiB free). Nothing on the box pruned the per-SHA
`ghcr.io/joemccann/radon-{node,python}` image pairs (~5.8G per deploy), and
`deploy.sh`'s best-effort `cleanup_staged_release_path` had leaked seven
`stage.`/`backup.` worktrees under `/home/radon/.radon-releases` going back to
Jul 11. `radon-disk-cleanup` automates the manual reclaim.

| Piece | Where | What |
|---|---|---|
| `radon-disk-cleanup.timer` | VPS | Weekly **Sun 03:20 UTC**, `RandomizedDelaySec=300`, `Persistent=true`. Clear of portfolio-archive (05:40), db-retention (08:10) and db-backup (09:00). |
| `radon-disk-cleanup.service` | VPS | Oneshot, `User=root` (root-only docker socket + journald vacuum), `TimeoutStartSec=900`. Runs the control-plane copy at `/usr/local/lib/radon/disk_cleanup.py` under `python3 -I`, never the radon-writable checkout. |
| `cloud/scripts/disk_cleanup.py` | VPS | Five categories: stale app images, dangling layers, leaked release worktrees, npm/pip caches, journald. |
| `service_health` heartbeat | row `disk-cleanup` | Written on EVERY run — `ok` with per-category bytes plus free-space before/after, `error` naming the failing categories. 8-day freshness window. |

Retention constants (`cloud/scripts/disk_cleanup.py`):

- `KEEP_IMAGE_PAIRS = 2` — keeps the `radon-node` / `radon-python` pair matching
  the live checkout's HEAD **plus** the two newest pairs. An unresolvable HEAD
  prunes nothing.
- `RELEASE_MAX_AGE_DAYS = 3` — removes `stage.<sha>.XXXXXX` / `backup.<sha>.XXXXXX`
  worktrees older than that, then drops their stale `.git/worktrees/<name>`
  records by filesystem op (root never runs `git` inside a repo the radon
  account can configure).
- `JOURNAL_VACUUM_SIZE = "200M"`.

Never touched: the running `ghcr.io/gnzsnz/ib-gateway` and
`willfarrell/autoheal` images, `/home/radon/radon` (the live tree),
`~/.cache/{ms-playwright,huggingface,fastembed,radon-wheels}`, and
`/home/radon/radon-cloud/backups` — DB backup retention is `db_backup.py`'s
`RETENTION_DAYS = 7` (operator call 2026-08-29; was 30) and changing it is an operator policy call.

A category that finds nothing is a normal run. A category that RAISES flips
the heartbeat to `error` but does not fail the unit: a wedged docker daemon
must not also park the timer.

Both units are **control-plane**, so `install-units` skips them by design and
the root `bootstrap-control-plane.sh` run is what installs them and clears
their `config/installed-units.sha256` entries.

### CREDIT spread (`radon-credit-spread.timer`)

Daily `21:45 UTC` (`RandomizedDelaySec=300`), oneshot
`scripts/fetch_credit_spread.py`. IB daily closes for HYG + SPX, then UW, then
Robinhood (HYG only, when configured), then Yahoo. Both units share IB client IDs 56/69 and therefore serialize on `flock -w <peer budget> -E 75 /run/lock/radon-ib-history-5669.lock`: the 21:45/21:55 gap is not a mutex once `RandomizedDelaySec=300` applies to both. The lock loser exits 75 (`SuccessExitStatus=75`) and defers to its next slot instead of entering `failed` (R-127).
Heartbeat `credit-spread`. Units are listed in `setup-vps.sh`
`SERVICE_FILES`; root install-copy is still owed (`not-installed` allowlist
expires 2026-12-31). Spec: [`indicators/credit.md`](indicators/credit.md).

### IEI/HYG ratio (`radon-iei-hyg.timer`)

Daily `21:55 UTC` (`RandomizedDelaySec=300`), oneshot
`scripts/fetch_iei_hyg.py`. IB daily closes for IEI + HYG (SMART) and the ICE
dollar index (`DX`, NYBOT), then UW (IEI/HYG, regular-session rows only), then
Robinhood (IEI/HYG only, when configured), then Yahoo (`DX-Y.NYB` for DXY).
Serialized against `radon-credit-spread` on the
shared IB client IDs (see above). Heartbeat `iei-hyg`. Installed by the deploy's
`install-units` verb from `installed-units.sha256`. Spec:
[`indicators/iei-hyg.md`](indicators/iei-hyg.md).

### TRIN (`radon-trin.timer`)

Every 5 minutes `Mon..Fri 13:02-21:57 UTC` (2 min offset from
`radon-breadth` so the two IB snapshot jobs never collide), oneshot
`scripts/fetch_trin.py`, `TimeoutStartSec=240`. During RTH (ET, calendar-gated
at runtime, `/health` auth-gated) it snapshots IB `TRIN-NYSE` plus `AD-NYSE` /
`VOL-NYSE` into Turso `trin_samples`; hourly bars and MA(10) are derived at
read time. The StockCharts `$TRIN` daily series (`trin_daily`) is scraped
only when the last closed session is missing from the cache (R-121: it used to
scrape on all 108 cycles a day for at most one new row); every run heartbeats,
so off-hours runs are heartbeat-only.
Heartbeat `trin`. Installed by the deploy's `install-units` verb from
`installed-units.sha256`. Spec: [`indicators/trin.md`](indicators/trin.md).

### DIV YIELD (`radon-divyield.timer`)

Daily `22:40 UTC` (`RandomizedDelaySec=300`, offset from `radon-yield-curve`'s
22:30 pass so the day's `y10` row lands first), oneshot
`scripts/fetch_divyield.py`, `TimeoutStartSec=2100`. Percent of S&P 500
constituents whose trailing-12M dividend yield exceeds the 10Y Treasury yield:
constituents from the github-datasets CSV (Wikipedia parse API, then the
`index_constituents` cache/seed chain as fallbacks), per-ticker trailing yields
from Yahoo v8 charts (6 workers), `y10` read from Turso `yield_curve_history`.
Rows in `divyield_history` (monthly pre-cutover rows are a survivorship-biased
approximation, `approximate=1`). Weekend runs are unchanged-day heartbeats.
Heartbeat `div-yield`. Installed by the deploy's `install-units` verb from
`installed-units.sha256`. Spec: [`indicators/divyield.md`](indicators/divyield.md).

### HY AD (`radon-hyad.timer`)

`Tue..Sat 11:00 UTC` (`RandomizedDelaySec=300`), oneshot `scripts/fetch_hyad.py`,
`TimeoutStartSec=300`. High yield corporate bond cumulative advance-decline line
from FINRA TRACE end-of-day market breadth (public dynarep reporting endpoint,
UA `radon/2.0`, self-minted double-submit XSRF pair): HY column summed over
CORP + CORP_144A, rolling 10-day window each run (self-healing over bond-market
holidays), cumulative line and 21/50-day MAs derived at payload build, SPX
overlay joined from `credit_spread_history`. Rows in `hyad_history` (2018+).
Heartbeat `hy-ad`. Installed by the deploy's `install-units` verb from
`installed-units.sha256`. Spec: [`indicators/hyad.md`](indicators/hyad.md).

### HH LEV (`radon-hhlev.timer`)

Daily `13:20 UTC` (`RandomizedDelaySec=300`, offset from `radon-margin-debt`'s
13:10 pass), oneshot `scripts/fetch_hhlev.py`, `TimeoutStartSec=300`. US
household leverage: Z.1 household liabilities as a percent of net worth
(`TLBSHNO`/`TNWBSHNO`) from the keyless FRED fredgraph CSV (UA must be
`radon/2.0 (+https://radon.run)`; the bare token is reset by FRED's edge),
keyed FRED API fallback via `FRED_API_KEY`. Quarterly source (~10 week lag,
releases Mar/Jun/Sep/Dec); the daily run re-upserts the full revised series
into `hhlev_history` and heartbeats `hhlev` regardless. Installed by the
deploy's `install-units` verb from `installed-units.sha256`. Spec:
[`indicators/hhlev.md`](indicators/hhlev.md).

### VIX TS (`radon-vixts.timer`)

Daily `02:45 UTC` (`RandomizedDelaySec=120`), oneshot `scripts/fetch_vixts.py`,
`TimeoutStartSec=300`. VIX / VIX3M term-structure ratio: below 1.00 the curve
is in contango, above it is in backwardation. Three Cboe CDN files
(`VIX_History.csv`, `VIX3M_History.csv`, `SPX_History.csv`) pulled through the
shared `CboeClient` with per-file `If-Modified-Since`; when all three return
304 the run reuses the cached payload and refreshes only the snapshot and
heartbeat. 02:45 clears both the EDT and EST publication windows and sits
after straddle 02:15, cor 02:20 and vixcor 02:35 so the CDN hits stay
staggered. Runs every calendar day; weekend and holiday runs are 304
heartbeats that keep `vixts` inside its 26h window. Single-source, so a
plausibility guard raises rather than latching `ok` over a truncated or
implausible series. Installed by the deploy's `install-units` verb from
`installed-units.sha256`. Spec: [`indicators/vixts.md`](indicators/vixts.md).

### DISPERSION (`radon-dispersion.timer`)

Daily `22:20 UTC` (`RandomizedDelaySec=120`), oneshot `scripts/fetch_dispersion.py`,
`TimeoutStartSec=900`. VIX close, the 95th-minus-5th percentile spread of daily
single-stock returns across the S&P 500 seed, and the same spread across the 11
Select Sector SPDRs, each rolled to a 60-session mean and z-scored over the full
sample since 2017. IB daily `TRADES` bars for every symbol (`IBClient` auto client
id, `asyncio.gather` under 8 slots, `1 M` incremental / `10 Y` `--backfill`), then
Yahoo for whatever IB left empty (spark batches of 20 incrementally, per-symbol
chart on backfill); UW is skipped so the 515-symbol sweep never spends the shared
daily cap. Only raw per-session rows land in `dispersion_history`; the means and
z-scores are rebuilt from every stored row each run. 22:20 clears the EST close
and sits between ivrank 22:10 and yield-curve 22:30. Runs every calendar day;
weekend and holiday runs find no new completed session, make no IB or Yahoo
requests, and refresh only the snapshot + heartbeat that keep `dispersion` inside
its 26h window. An empty VIX or a thin cross-section re-serves the stored series
as `stale_source` with an `error` heartbeat and exits non-zero; a gap wider than
the incremental window raises and asks for `--backfill`. Installed by the deploy's
`install-units` verb from `installed-units.sha256`. Spec:
[`indicators/dispersion.md`](indicators/dispersion.md).

### Model catalog (`radon-model-catalog.timer`)

Daily `03:10 UTC` (`RandomizedDelaySec=300`), oneshot
`scripts/refresh_model_catalog.py`, `TimeoutStartSec=300`. Picks ONE frontier
chat model per LLM provider whose API key is present in the unit env
(`ANTHROPIC_API_KEY` today; `XAI_API_KEY` / `GROK_API_KEY` and `OPENAI_API_KEY`
light up automatically when added to `/etc/radon/env`) by listing that
provider's own models endpoint and applying a deterministic filter, sort, head:
dated snapshots lose to the undated alias they pin, cheap and preview tiers and
non-chat modalities are dropped, and versions are compared as floats so
`grok-4.20` reads as 4.2 rather than beating `grok-4.6`. A provider with no key
is skipped silently, so the chat model picker lists exactly what this
deployment can call. A provider that errors, times out or rate-limits keeps its
existing row (seeded from Turso, JSON cache second); a run that resolves no
provider at all writes only the heartbeat, so a bad poll never blanks a good
catalog. Any carried-forward keyed provider makes that heartbeat `error`
(`class: provider_carry_forward`, naming the providers), a crash before the
write leaves an `error` row (`class: cycle_failed`) and exit 1, each provider
poll has its own 60s wall-clock budget and the Anthropic cursor walk is capped
at 10 pages, so the 26h alarm reports content staleness rather than an `ok`
with today's `finished_at` (R-455/R-456/R-458). Per-provider operator overrides
(`ANTHROPIC_MODEL`, `OPENAI_MODEL`, `XAI_MODEL` / `GROK_MODEL` — the same
variables `web/lib/llm/provider.ts` reads) win over discovery, so a bad
heuristic is recoverable without a deploy. Rows in `llm_model_catalog`, payload
in `scan_snapshots`, heartbeat `model-catalog`, JSON fallback
`data/llm_models.json`. A key added to `/etc/radon/env` only becomes visible to
the picker after `systemctl restart radon-nextjs` — `write_web_env()` rewrites
`web/.env` on every deploy with `NEXT_PUBLIC_*` only, so provider keys must
live in the unit env, never in `web/.env` on the host. Installed by the
deploy's `install-units` verb from `installed-units.sha256`.

### IV RANK (`radon-ivrank.timer`)

Daily `22:10 UTC` (`RandomizedDelaySec=120`), oneshot
`scripts/fetch_ivrank.py`. SPY 30-day implied vol from IB
(`OPTION_IMPLIED_VOLATILITY` daily bars, health-gated), UW iv-rank fallback,
ranked over the trailing 252 sessions. Heartbeat `ivrank`. Units are listed in
`setup-vps.sh` `SERVICE_FILES`; root install-copy is still owed
(`not-installed` allowlist expires 2026-12-31). Spec:
[`indicators/ivrank.md`](indicators/ivrank.md).

### Flex sFTP pull (`radon-flex-pull.timer`)

Install dependency: IBKR-hosted sFTP, not Flex Web Service. Full recipe:
[`flex-sftp-setup.md`](flex-sftp-setup.md).

`Tue..Sat 07:30 ET` plus `08:30 ET` empty-dir retry. Oneshot
`scripts/flex_sftp_pull.py`. Heartbeat `flex-pull`. Stripped env
`/var/lib/radon/flex-secrets/env` (no `TWS_PASSWORD`). Units on
`auto-sync-units.txt`.

Queries: `1442520` SOD (NAV + cash + transfers), `1422766` EOD (trades).
Period Last Business Day, XML, PGP. Empty `outgoing` through 2026-08-31 is
ok skip; from 2026-09-01 empty is error. Miss does not SendRequest.

Same-day blotter is Gateway `journal_sync`. This timer is T+1 recon.

### TWR performance builder (`radon-perf-twr.timer`)

`Tue..Sat 07:30 ET` (`RandomizedDelaySec=300`), oneshot
`scripts/perf_twr_builder.py`, heartbeat via the `performance_snapshots`
mirror. Rebuilds the `/performance` payload: NAV + external flows → TWR.

**Run the morning AFTER a session, never the same evening.** IBKR Flex serves
through its last *finalized* statement date. Measured 2026-08-17: a live Flex
pull at 20:57 ET Monday still returned NAV through Friday 08-14, and a
statement generated Sunday 08-16 came back with `toDate="20260814"`. The unit
originally ran `Mon..Fri 20:45 ET` and could only ever republish the previous
session. `Tue..Sat` covers all five sessions — Tuesday picks up Monday,
Saturday picks up Friday and carries the correct NAV across the weekend.

`/performance` is therefore always **T+1**: on Monday it shows Friday. One
session behind is inside `NAV_STALENESS_BUDGET_SESSIONS: 2`, so the page
correctly renders no amber warning.

**Both units must be in `cloud/config/installed-units.sha256`** or the install
step silently skips them. They were absent until 2026-08-17, so the timer was
never installed on the VPS (`systemctl is-enabled` → `not-found`) and nothing
rebuilt the payload on a schedule at all; the only refreshes came from the
page's own SWR trigger.

**A Flex outage must not blank the page.** NAV degrades `flex → disk_cache →
turso`; flows now degrade `flex → turso external_flows` via
`load_flows_from_turso()`. Before that fallback existed, one `fetch_flex_xml`
exception produced `FlowSet.failed`, which suppresses TWR, Max DD, Sharpe and
the equity curve — so the page flipped between +90.81% and `--` depending on
whether the last run happened to reach Flex. Flows for closed sessions are
settled facts and are safe to reserve from the mirror. An **empty** mirror is
still `FAILED`: absence of evidence is not a verified zero, and inventing a
zero flow set is what produced the +951% TWR.

Flex codes seen on this path: `1001` (statement not generatable right now),
`1018` (the only real rate limit), `1019` (generation in progress),
`1025` (too many failed attempts — a lockout earned by repeated failures).

### Backblaze B2 dependency (production required)

Off-box store for cold portfolio history. **Not** Cloudflare R2 (account billing/R2 enablement blocked). Uses S3-compatible API via `boto3`.

| | |
|---|---|
| Provider | [Backblaze B2](https://secure.backblaze.com) |
| Bucket | `radon-archive` (private) |
| Object prefix | `portfolio_snapshots/` (monthly `YYYY-MM.jsonl.gz`) |
| Local mirror | `/home/radon/radon-cloud/archive/portfolio_snapshots/` |
| Script | `scripts/archive_portfolio_snapshots.py` |
| Unit | `radon-portfolio-archive.service` — **fails closed** if B2 env is unset |
| Env contract | root `.env.example`, `cloud/.env.example`, `cloud/config/required-env.txt` |
| VPS secrets | `/etc/radon/env` (`EnvironmentFile=` on the unit; `~/radon-cloud/.env` is a compatibility symlink) |

Required vars (all five keys + region; endpoint must include `https://`):

```bash
RADON_ARCHIVE_S3_ENDPOINT=https://s3.us-west-004.backblazeb2.com
RADON_ARCHIVE_S3_BUCKET=radon-archive
RADON_ARCHIVE_S3_ACCESS_KEY_ID=...
RADON_ARCHIVE_S3_SECRET_ACCESS_KEY=...
RADON_ARCHIVE_S3_REGION=us-west-004
RADON_ARCHIVE_S3_PREFIX=portfolio_snapshots/
```

Smoke: `python3 scripts/archive_portfolio_snapshots.py --dry-run` must print `"s3_configured": true` when env is loaded.

Export streams keyset pages into monthly partitions (bounded `batch_size` in-flight
payloads — never materializes the full fat table). Crash-safe order is always
archive + verify + B2 upload, then `delete_portfolio_snapshots_before`.

**Catch-up DELETE only** (archive already on disk + B2 — e.g. unit OOM’d during
the Turso prune phase):

```bash
python3 scripts/archive_portfolio_snapshots.py --delete-only
```

Skips re-export/upload; only runs the batched payload-free DELETE for rows older
than the retention cutoff. Do not use unless partitions for those months are
already verified off-box.

### Recovering cold portfolio history from B2

```bash
# list (needs AWS CLI or B2 S3-compatible client with the same endpoint/keys)
aws --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" s3 ls "s3://${RADON_ARCHIVE_S3_BUCKET}/portfolio_snapshots/"
# download a month
aws --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" s3 cp \
  "s3://${RADON_ARCHIVE_S3_BUCKET}/portfolio_snapshots/2026-06.jsonl.gz" /tmp/
gunzip -c /tmp/2026-06.jsonl.gz | head
```

## Media backup (media.radon.run → B2)

Nightly off-box mirror of the Caddy static tree for `media.radon.run`.

| | |
|---|---|
| Local root | `/var/lib/radon/media` (Caddy `file_server` root; `~/radon-cloud/media` is a compatibility symlink) |
| Object store | Same Backblaze B2 bucket as portfolio archive (`radon-archive`) |
| Object prefix | `media/` (override with `RADON_MEDIA_BACKUP_PREFIX`) |
| Script | `cloud/scripts/media_backup.py` |
| Unit | `radon-media-backup.service` + `.timer` (**10:15 UTC**, after db-backup) |
| Heartbeat | `service_health` row `media-backup` (48h freshness window) |
| Credentials | Reuses `RADON_ARCHIVE_S3_*`; optional full override via `RADON_MEDIA_BACKUP_S3_*` |
| Fail-closed | Missing credentials → unit exit 1 + `error` heartbeat (never silent green) |

```bash
# smoke (no upload): credentials must still be present in env
python3 cloud/scripts/media_backup.py --dry-run
# list remote
aws --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" s3 ls "s3://${RADON_ARCHIVE_S3_BUCKET}/media/"
# restore one file
aws --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" s3 cp \
  "s3://${RADON_ARCHIVE_S3_BUCKET}/media/<file>.png" /tmp/
# full restore into a staging dir
aws --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" s3 sync \
  "s3://${RADON_ARCHIVE_S3_BUCKET}/media/" /tmp/media-restore/
```

Enable on VPS. The CI deploy never installs unit files -- copy them as root
first (and bump `cloud/config/installed-units.sha256` in the matching commit):

```bash
sudo install -m 0644 /home/radon/radon/cloud/services/radon-media-backup.service \
  /home/radon/radon/cloud/services/radon-media-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now radon-media-backup.timer
sudo systemctl start radon-media-backup.service   # optional immediate run
journalctl -u radon-media-backup -n 50 --no-pager
```

## Equibles (`EQUIBLES_API_KEY` — production required)

Five oneshot timers pull the Equibles REST surface. All of them construct
`EquiblesClient` (`scripts/clients/equibles_client.py`), which raises
`EquiblesAuthError` at construction when `EQUIBLES_API_KEY` is unset or
rejected. No IB dependency; the Equibles API is the only source.

| Unit | When (UTC) | Script | Heartbeat | Freshness window |
|---|---|---|---|---|
| `radon-equibles-short-crowding.timer` | daily **09:30** | `scripts/fetch_equibles_short_crowding.py` | `equibles-short-crowding` | 26h |
| `radon-equibles-13f.timer` | **Mon 09:45** | `scripts/fetch_equibles_smart_money_13f.py` | `equibles-13f` | 8d |
| `radon-equibles-filings.timer` | daily **10:00** | `scripts/fetch_equibles_filing_forensics.py` | `equibles-filing-forensics` | 26h |
| `radon-equibles-ats.timer` | **Tue 09:15** | `scripts/fetch_equibles_ats_venue_share.py` | `equibles-ats-venue-share` | 8d |
| `radon-equibles-cot.timer` | **Sat 01:00** | `scripts/fetch_equibles_cot_positioning.py` | `equibles-cot-positioning` | 8d |

Windows are registered in `scripts/watchdog/services.py` and
`web/lib/serviceHealthWindows.ts`. All five are in the watchdog's
`freshness` + `error` alert buckets.

| | |
|---|---|
| Provider | [Equibles](https://api.equibles.com/v1) — Bearer key |
| Allowance | Pro plan 100,000 requests/day, shared REST + MCP, resets 00:00 UTC. Every paginated page bills separately, so `max_pages` is bounded in the client. |
| Env contract | root `.env.example`, `cloud/.env.example`, `cloud/config/required-env.txt` |
| VPS secrets | `/etc/radon/env` (`EnvironmentFile=` on every unit) |
| Ticker scope | `fetch_equibles_smart_money_13f.py` and `fetch_equibles_filing_forensics.py` read the Turso `watchlist` table. A ticker off the watchlist has no row, and both API routes serve `missing: true` for it. |
| Tables | `equibles_13f_snapshots` (the route's only read), `equibles_13f_holders` (write-only depth), `equibles_filing_forensics`, `equibles_short_interest` + `equibles_squeeze_scores`, `equibles_ats_venue_share`, `cot_positioning` |
| Demo mirror | `equibles_13f_snapshots` + `equibles_filing_forensics` are mirrored per ticker by `scripts/db/mirror_market_snapshots_to_demo.js`. `equibles_13f_holders` is not — nothing reads it. |

**Fail-closed:** `EQUIBLES_API_KEY` is in
`cloud/config/required-env.txt`, so `cloud/scripts/check-env.py` gates the
deploy preflight on it and a host without the key fails the deploy instead of
shipping five units that die on every fire. The key was added to that contract
on 2026-08-25, taken back out on 2026-08-26, and restored the same day by
PR #104. A second guard sits per producer. All five build their Equibles client
inside the block that owns health reporting, so a construction failure, a
rejected key, an exhausted allowance, and an empty watchlist each leave an
`error` heartbeat before the process exits. The preflight catches an unset key
at deploy time; that heartbeat catches a key the API rejects later. Until
2026-08-25 the construction sat outside that block and the oneshots died
writing no `service_health` row at all.

Read-only operator checks on the VPS:

```bash
# is the key present (name only, never the value)
sudo grep -c '^EQUIBLES_API_KEY=' /etc/radon/env
# has any Equibles timer ever succeeded
systemctl list-timers 'radon-equibles-*' --all --no-pager
for u in short-crowding 13f filings ats cot; do
  systemctl show "radon-equibles-$u.service" \
    -p Result -p ExecMainStatus -p ActiveEnterTimestamp --no-pager
done
journalctl -u radon-equibles-13f -u radon-equibles-filings -n 100 --no-pager
```

Heartbeat rows (a row that never appears is the failure mode above):

```sql
SELECT service, state, finished_at FROM service_health
WHERE service LIKE 'equibles-%' ORDER BY finished_at DESC;
```

## Retired: beta.radon.run (2026-08-20)

The staging clone was never finished. Repo copies of `deploy/beta/`, the
Caddy `beta.radon.run` site, and `cloud/config/sudoers.d/radon-beta` are
gone. Deploy still skips leftover `radon-beta-*` units so a host that still
has them is not torn down as production.

On the VPS, when convenient (not a deploy blocker):

```bash
sudo systemctl disable --now radon-beta-nextjs radon-beta-api radon-beta-health
sudo rm -f /etc/systemd/system/radon-beta-*.service /etc/sudoers.d/radon-beta
sudo systemctl daemon-reload
# drop DNS A for beta.radon.run, Clerk satellite, Turso DB radon-beta, /home/radon/radon-beta
```

## Known gaps

| # | Item | Owner |
|---|------|-------|
| 1 | ~~Nightly retention sweep on snapshot tables~~ | **Done** — `radon-portfolio-archive` + `radon-db-retention` |
| 2 | ~~restic backup of `radon_media` volume to B2~~ | **Done** — `radon-media-backup` (boto3/S3 to B2 prefix `media/`, 2026-07-13); restic not required at solo-operator scale |
| 3 | ~~systemd timer for `oi_changes` (currently on-demand only)~~ | **Done** — `radon-oi-changes.timer` 3x/RTH day (14/17/20 UTC Mon-Fri) via `run_oi_changes_refresh.sh` |
| 4 | ~~Vercel Edge replica for a public read-only dashboard~~ | **Resolved** — public product surface is `demo.radon.run` (synthetic data, separate Turso); marketing site `radon.run` on Vercel. No Edge replica of **prod** Turso (PII / account figures). See `docs/demo-environment.md`. |
| 5 | ~~Verify Turso plan PITR~~ | **Verified 2026-07-13** — org Pro, **90d PITR**; `turso plan show` (not `org show`) |
| 6 | ~~Off-box portfolio archive (`RADON_ARCHIVE_S3_*`)~~ | **Done** — Backblaze B2 `radon-archive` (2026-07-13) |
| 7 | Continuous journal-gap SLI | **Done** — `journal-gap-sli` monitor handler (5m) |

## DB backup off-box copy (B2 `db_backups/`)

Closes the single-copy risk in "DB backup & restore" above: until 2026-08-27
the only copy of the nightly Turso dumps lived on the VPS root filesystem
(`/home/radon/radon-cloud/backups/db`, 13G and growing ~55%/month at
`RETENTION_DAYS = 30`), on the same 75G disk that hit 98% that night.
`scripts/db_backup_pull.sh` is a manual laptop-initiated rsync pull, not an
automated off-box push, and is not a backup strategy.

| Piece | Value |
|---|---|
| Owner | `cloud/scripts/db_backup.py` — the SAME oneshot that writes the dump. No new systemd unit, no new `service_health` row. |
| Unit | Unchanged `radon-db-backup.service` + `.timer` (09:00 UTC). `TimeoutStartSec=19500` already covers dump + upload. |
| Target | S3-compatible API to the existing B2 bucket `radon-archive`, prefix **`db_backups/`** (never collides with `portfolio_snapshots/` or `media/`). |
| Credentials | `RADON_ARCHIVE_S3_*` from `/etc/radon/env` (already required-env). Optional per-field overrides `RADON_DB_BACKUP_S3_{ENDPOINT,BUCKET,ACCESS_KEY_ID,SECRET_ACCESS_KEY,REGION}` and `RADON_DB_BACKUP_PREFIX`, same shape as `RADON_MEDIA_BACKUP_S3_*`. |
| Local retention | `RETENTION_DAYS = 7` since 2026-08-29 (was 30 at cutover), operator policy; B2 is the archive. The off-box leg runs BEFORE the local prune and a dump past the window is unlinked only when it is present in B2 (already listed, or uploaded and size-confirmed tonight); a failed or budget-deferred upload keeps the local copy, so a red `db-backup` row is also a disk-growth signal. Without any B2 config the prune is age-only. |
| Remote retention | `REMOTE_RETENTION_DAYS = 365`. Off-boxing only the local window would buy nothing; a year of nightly dumps is ~190 GB in B2 at current sizes. |
| Transport bound | Multipart at 64 MB chunks, 4 threads, botocore `connect_timeout=30` / `read_timeout=300` / 3 attempts, plus a `UPLOAD_BUDGET_SECS = 3600` wall-clock ceiling. |
| Heartbeat | Existing `db-backup` row, extended detail: `offbox_bucket`, `offbox_prefix`, `offbox_uploaded`, `offbox_bytes_uploaded`, `offbox_deferred`, `offbox_remote_pruned`, and `offbox_error`. Summary gains `; b2 <uploaded>/<planned> (<bytes> B), deferred N, remote pruned N`. |

### Fail-degraded, deliberately

`media_backup.py` fails **closed** — no credentials, unit fails, nothing
written. `db_backup.py` is the opposite by design and says so in its
docstring: the local gzip is the critical path and the upload is not. A
wedged, misconfigured, or credential-less B2 never deletes, truncates, or
skips the on-box dump. The run still writes the file, then reports the
upload failure to the journal and flips the `db-backup` heartbeat to
`error` (exit 1). A red `db-backup` row therefore means "check whether the
dump or only the upload failed" — `offbox_error` in the detail distinguishes
them, and `size_bytes` is still populated when the dump itself was fine.

### Idempotent, resumable, and the first-run backfill

Each run lists `db_backups/` once and uploads only dumps that are absent
remotely or present at a different size. Uploads run **newest first**, so
tonight's dump always goes before any backfill of the older window.

The first run after deploy therefore backfills the whole local retention
window (~30 dumps, ~13 GB) rather than only that night's file. If the
`UPLOAD_BUDGET_SECS` ceiling is reached the remainder is reported as
`offbox_deferred` and the next night resumes exactly where this one
stopped — no re-upload of what already landed. Expect the backlog to clear
over the first one to three nights; steady state is one ~530 MB object per
night.

### Verify

```bash
journalctl -u radon-db-backup -n 50 --no-pager | grep ' b2 '
# object listing (from a host with the B2 credentials)
aws s3 ls "s3://radon-archive/db_backups/" --endpoint-url "$RADON_ARCHIVE_S3_ENDPOINT" | tail
```

Restore is unchanged: pull the object, then follow the "Restore runbook"
above against the downloaded `radon-<stamp>.sql.gz`.
