# Operations Runbook

Live-trading operational concerns: IB Gateway connection modes, background services, watchdogs, deploy flow. The authoritative developer runbook is [`CLAUDE.md`](../CLAUDE.md). The cloud-services architecture deep dive is [`docs/cloud-services.md`](cloud-services.md).

## Environment Variables

### Web app (`web/.env`)

```bash
ANTHROPIC_API_KEY=
UW_TOKEN=
EXA_API_KEY=
CEREBRAS_API_KEY=                       # optional, newsfeed text tagger

# Clerk authentication
# MFA is scoped to the operator account (Clerk policy "optional" + operator has TOTP enrolled),
# NOT required instance-wide. Clerk challenges any user with an enrolled factor, so the operator
# is MFA-gated while demo users (same instance, no enrolled factor) stay frictionless.
# Do NOT set the Clerk policy to "required for all users" — it would force MFA on every demo signup.
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
```

### Root `.env`

```bash
MENTHORQ_USER=
MENTHORQ_PASS=

# IB Gateway
IB_GATEWAY_HOST=127.0.0.1               # loopback on Hetzner; ib-gateway only for special topologies
IB_GATEWAY_PORT=4001
IB_GATEWAY_MODE=cloud                  # Hetzner production. Local laptop: docker | launchd
IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud   # monorepo cloud path on Hetzner (not ~/radon-cloud)
RADON_MODE=hetzner                      # Hetzner production. Laptop: local

# Clerk JWT validation (FastAPI + WS relay)
CLERK_JWKS_URL=
CLERK_ISSUER=
ALLOWED_USER_IDS=user_...               # comma-separated allowlist

# Newsfeed scraper
THEMARKETEAR_EMAIL=
THEMARKETEAR_PASSWORD=

# IB Flex Web Service (Hetzner)
IB_FLEX_TOKEN=
IB_FLEX_QUERY_ID=1422766                # blotter
IB_FLEX_NAV_QUERY_ID=1497709            # cash transactions

# Equibles (13F, ATS, COT, filings, short crowding)
EQUIBLES_API_KEY=

# Loopback / off-box probes (not a Clerk session)
RADON_PROBE_FRESHNESS_TOKEN=
```

`scripts/cta_sync_service.py` and `scripts/run_cta_sync.sh` parse `.env` values literally instead of shell-sourcing them, so unquoted secrets containing shell metacharacters (`$`, backticks, etc.) survive the scheduled CTA path.

`.env.ib-mode` overlays `.env` and stores the IB mode toggle from `scripts/ib mode local|cloud`.

## IB Gateway

Three deployment modes selected by `IB_GATEWAY_MODE`:

| Mode | Description |
|------|-------------|
| `docker` (default; local development) | Local `ghcr.io/gnzsnz/ib-gateway` via Docker Compose with `restart: "no"`. Local start/restart paths acquire the shared lease. Reports Docker `container_state` / `container_health`. |
| `cloud` (Hetzner production) | Lifecycle is externally owned by `/usr/local/bin/radon-ib-gateway-control` on the Hetzner VM. FastAPI performs TCP/API health checks only and reports `service_state=reachable` when the port/API path is up; local Compose restart returns 503. |
| `launchd` (legacy) | IBC under macOS launchd. |

**2FA-aware restart.** After every restart, IB Gateway sits at the IBKR Mobile push prompt with the API socket already open, so port probes alone falsely report success. `restart_ib_gateway()` runs an explicit `managedAccounts()` probe; non-empty resets backoff, empty advances it (1m → 2m → 5m → 15m → 30m → 60m capped). `/health` exposes `auth_state` (`authenticated | awaiting_2fa | unreachable | unknown | remote`), `service_state` (`healthy | unhealthy | starting | reachable | unknown`), `upstream_dead`, and `restart_backoff` (attempt count, next attempt in seconds, push lock holder/TTL, last outcome). Schema-v2 `/status` treats nested broker degradation (`awaiting_2fa`, `upstream_dead`, unhealthy service) as aggregate-down even when FastAPI returns HTTP 200, and treats cloud-mode `reachable` as healthy. `POST /ib/reset-backoff` is the operator escape hatch after manually approving 2FA. **Watchdog stuck-2FA self-heal (2026-05-20):** after 3 consecutive `auth_state=awaiting_2fa` cycles with no active push or scheduled retry, the watchdog acquires the cross-process lease and invokes the fixed `radon-ib-gateway-preheld-restart.service` adapter. The adapter consumes that exact lease once and calls `/usr/local/bin/radon-ib-gateway-control`; boot, admin, operator, and laptop cloud starts use the same helper. Never run raw Docker or unmanaged `systemctl restart radon-ib-gateway.service` when the helper is installed.

**Hetzner control boundary.** `radon-ib-gateway.service`, the watchdog adapter, admin controls, boot, and operator commands all call the installed monorepo helper at `/usr/local/bin/radon-ib-gateway-control` (sourced from `/home/radon/radon/cloud`). FastAPI runs with `IB_GATEWAY_MODE=cloud` and must not inspect or mutate the production Compose project directly. Set `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud`. Secrets are `/etc/radon/env` (`0600`); `/home/radon/radon-cloud/.env` is a compatibility symlink. Root demotion of the helper must run from a radon-readable cwd (never leave cwd as `/root`).

**ib_insync request bounding.** `ib_insync` has no built-in timeout on its async API calls — `qualifyContractsAsync`, `reqHistoricalDataAsync`, and `reqMktData` will block forever when the gateway is logged in but the user session isn't authenticated (the 2FA-pending state). Any script that imports `ib_insync` directly must wrap each await in `asyncio.wait_for(..., timeout=15)` and pre-check `auth_state == "authenticated"` against FastAPI `/health` before instantiating `IB()`. `cri_scan.py` is the reference implementation.

**Client ID ranges.**

| Range | Usage |
|-------|-------|
| 0–9 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10–19 | WS relay |
| 20–49 | Subprocess scripts AND monitor_daemon handlers — always `client_id="auto"` |
| 50–69 | Scanners |
| 90–99 | CLI |

As of 2026-05-20 monitor_daemon handlers (`fill_monitor`, `exit_orders`, `journal_sync`) use `client_id="auto"` too — the prior 70/71/72 hardcoded daemon range left them one CLOSE_WAIT socket away from "client id already in use" on every transient gateway hiccup. The auto-allocator rotates around in-use IDs.

**Troubleshooting.**

```bash
# Health
curl -s http://localhost:8321/health | python3.13 -m json.tool

# Gateway reachable?
bash -c 'echo > /dev/tcp/ib-gateway/4001' && echo OK || echo FAIL

# Connections on remote host
ssh root@ib-gateway "ss -tnp | grep 4001"

# Fresh client probe
python3.13 -c "from ib_insync import IB; ib=IB(); ib.connect('ib-gateway',4001,clientId=99,timeout=10); print('OK'); ib.disconnect()"
```

**Management commands** (laptop alias → SSH-wrapped; same names on the VPS):

| Command | Action |
|---------|--------|
| `ibstart` | Start container, wait for port 4001 |
| `ibstop` | Stop and remove container |
| `ibrestart` | Restart container |
| `ibstatus` | Container state, port check, active connections |
| `iblogs [N]` | Tail container logs |
| `ibhealth` | Docker healthcheck status |

Deeper troubleshooting and full Docker setup live in [`docs/ib-gateway-docker.md`](ib-gateway-docker.md) and [`docs/ib-connection-troubleshooting.md`](ib-connection-troubleshooting.md).

## Background Services

Hetzner host systemd is the production surface. Laptop dev uses launchd plists in `config/`. Laptop `com.radon.data-refresh` must stay unloaded. VPS `radon-flow-refresh.timer` owns hourly scanner/discover/flow during ET RTH.

| Service | Cadence | Purpose |
|---------|---------|---------|
| `radon-ib-gateway` | always-on | Broker session for live quotes, execution, reports |
| `radon-api` | always-on | FastAPI on `:8321` |
| `radon-relay` | always-on | IB realtime WebSocket relay on `:8765` |
| `radon-nextjs` | always-on | Next.js terminal at `app.radon.run` |
| `radon-newsfeed` | 120s loop | Headless Playwright scraper for The Market Ear |
| `radon-monitor` | 30s loop | Fills, exit orders, journal sync, cash flow handler |
| `radon-health` | always-on | **Isolated** stdlib health daemon on `:8330` (see Health monitoring below). NO dependency on `radon-ib-gateway` — survives the cascade-stop. |
| `radon-refresh.timer` | 60s | Schedules data-refresh sweeps |
| `radon-vcg-refresh.timer` | Mon-Fri 13-21 UTC every 5 min | Autonomous VCG scan |
| `radon-portfolio-sync.timer` | Mon-Fri 13-21 UTC every 60s | Autonomous portfolio sync |
| `radon-cta-sync.timer` | Mon-Fri 18:15 / 19:00 / 21:30 UTC | MenthorQ CTA refresh |
| `radon-bpi.timer` | Mon-Fri 21:30 / 23:30 UTC; Tue-Sat 11:00 UTC | BPI after the close, same-evening Yahoo catch-up, morning catch-up |
| `radon-watchdog-{intraday,continuous,daily,error}.timer` | varies | Service-health alerting (Pushover) |
| `radon-host-metrics.timer` | every 1 min | Host CPU, memory, loop lag. Details: [`cloud-services.md`](cloud-services.md#host-metrics-dur-12) |
| `radon-equibles-{13f,ats,cot,filings,short-crowding}.timer` | daily / weekly | 13F, ATS, COT, filings, short crowding. Spec: [`equibles-api.md`](equibles-api.md) |
| `radon-vol-cone.timer` | Mon-Fri 20:45 UTC | Completed-session cheap-wing cone (16:45 ET, after the close grace). Spec: [`indicators/vol-cone.md`](indicators/vol-cone.md) |
| `radon-vol-cone-intraday.timer` | Mon-Fri 09:00-16:30 ET every 15 min | Live sample ranked against that stored cone, so the tab is tradeable during the session instead of a day stale. Holds without spending a UW request outside market hours or under a nearly-spent daily budget, and a held pass no longer republishes the shared `vol-cone` snapshot. The 16:45 ET slot is deliberately absent: in EDT it is 20:45 UTC, the EOD writer's own minute (R-128). |
| `radon-vixcor.timer` | daily 02:35 UTC | VIX x COR3M 20-session correlation, 15 min behind `radon-cor`. Spec: [`indicators/vixcor.md`](indicators/vixcor.md) |
| `radon-credit-spread.timer` | daily 21:45 UTC | HYG vs SPX credit-equity series. IB first, then UW, then Yahoo. Spec: [`indicators/credit.md`](indicators/credit.md). Units are in `setup-vps.sh`; root install-copy still owed. |
| `radon-incident-watchdog.timer` | every 5 min | Writes `data/incidents/`. Cases: [`incident-runbook.md`](incident-runbook.md) |
| `radon-grok-page-responder.timer` | 30s after last cycle | Headless Grok auto-fix from dedicated clone. Spec: [`grok-page-responder.md`](grok-page-responder.md) |

The autonomous timers retired Radon's previous "data only refreshes when a browser tab is open" failure mode. Some surfaces remain on-demand by design (`scanner`, `discover`, `flow-analysis`, `analyst-ratings`, `gex-scan`, `orders-read-compare`).

**Operator CLI.** `/usr/local/bin/radon` wraps every loaded `radon-*` unit **except `radon-health`**. Auto-enumerates via `systemctl list-units 'radon-*'` (then filters out `radon-health.service`), so new timers don't require script edits. `radon-health` is deliberately excluded so the health daemon keeps reporting while `radon stop|restart` cycles the trading stack — manage it explicitly with `systemctl restart radon-health`.

```bash
radon stop      # stop IB + all radon-* units
radon start     # start them all (IB Gateway first)
radon restart
radon status
```

From the laptop: `ssh root@ib-gateway radon stop`. The operator CLI is installed from the monorepo [`cloud/scripts/operator-radon.sh`](../cloud/scripts/operator-radon.sh) control-plane source.

## Health monitoring (isolated daemon + edge surface)

The health surface is **decoupled from the trading stack** so it keeps reporting precisely when the stack is down. Two layers plus an off-box witness:

- **`radon-health.service`** (`scripts/health_service/`, stdlib-only) — a standalone daemon on `127.0.0.1:8330` with **no `Requires=`/`After=radon-ib-gateway`**, so the cascade-stop (stop `radon-ib-gateway` → clean-stops api/relay/monitor; `Restart=always` does NOT re-fire) cannot take it down. `Restart=always` + `StartLimitIntervalSec=60`/`StartLimitBurst=5` so a crash-loop parks as `failed`, not an invisible hot-loop. Imports **nothing** from the trading stack (enforced by a subprocess isolation test).
  - `GET /healthz` — zero-I/O static `200` (liveness pin).
  - `GET /status` — **always `200`**; concurrent live probes (`radon-api` via `/health/lite`, relay/Next.js/IB-gateway TCP) + cached `systemctl` unit states (`active(exited)` reads `up`) + the Turso `service_health` table (read over stdlib libSQL HTTP — no libsql import; degrades to `unknown` on any failure). Degraded sources are body fields, never error codes.
- **Caddy edge** (`app.radon.run`): `GET /edge-health/ping` — static `respond "ok" 200`, the **never-502 floor** (depends only on Caddy). `GET /edge-health/status` → `reverse_proxy 127.0.0.1:8330`. **Caveat:** Caddy `handle_response` catches upstream 5xx, NOT dial failures, so `/edge-health/status` returns `502` when the daemon process is down — `/edge-health/ping` is the guaranteed floor.
- **Off-box prober (Tier-3):** `.github/workflows/external-health-probe.yml` (GitHub Actions, `*/5`) hits the public edge from off the VPS and UPSERTs to the Turso `external_probe` table (`scripts/health_probe/`), so a whole-box outage is still recorded externally. `reader.py` is the dead-man's-switch (flags stale `external_probe` rows). Needs repo secrets `TURSO_DB_URL`/`TURSO_AUTH_TOKEN`.

**Consumers:** the always-on IB status chip (`web/lib/IBStatusContext.tsx`) reads `/edge-health/status` in prod (falls back to `/api/admin/health` in dev / as a prod safety net). The admin panel stays on `/api/admin/health` (needs `managed_accounts`). The `/health` payload itself is **trust-scoped**: public/proxied callers get `{"status":"ok"}` only; account/state detail is local/tailnet only. See `scripts/api/CLAUDE.md` and `scripts/health_service/CLAUDE.md`.

**Recovery heartbeat:** the `awaiting_2fa → authenticated` pool reconnect (`pool.reconnect_all`) is driven server-side by a FastAPI lifespan task (`_ib_recovery_heartbeat_loop`, 15s) — independent of any browser poll, since the chip is now a read-only consumer. The every-minute `radon-ib-watchdog` `/health` curl is the slower backstop.

## Service Health & Watchdogs

Every dual-write service writes a row to the `service_health` Turso table on every cycle, including no-op short-circuits. The Next.js `<ServiceHealthBanner />` reads the latest row per service and renders a category-aware banner.

| Category | Stale state |
|----------|-------------|
| `scheduled` | Red — banner alerts; treated as outage |
| `on-demand` | Amber — dormant chip; suppressed from alerts |

Staleness windows live in `web/lib/serviceHealthWindows.ts`. Cycle-driven writers (`newsfeed-scraper`, `journal-sync`, `cri-scan`) use tight windows (~cadence × 3). Event-driven writers (`replica-watchdog`, `watchdog-alerts`) use 24h windows because "no event" is the healthy state. Equibles writers are registered there: daily 26h (`equibles-short-crowding`, `equibles-filing-forensics`), weekly 8d (`equibles-13f`, `equibles-ats-venue-share`, `equibles-cot-positioning`). An `ok` row with null `last_error` that still renders stale is a registration gap, not a dead writer.

**Incident artifacts.** `scripts/incident_watchdog` writes `data/incidents/incident-*.json`. Laptop `com.radon.incident-responder` (`scripts/incident_responder.py`, 10 min) mirrors to `data/incidents_remote/` and analyzes open files older than 12 min. Cases: [`incident-runbook.md`](incident-runbook.md). Triage: `/incident <path>`.

**Grok P1 responder (VPS clone).** A delivered watchdog P1 also inserts `watchdog_pages`. `radon-grok-page-responder.timer` runs headless Grok from `/home/radon/radon-page-responder` and, unless the runbook says stand down, TDD-ships. After the live deploy gate, `scripts/deploy_notify.py` sends `radon deploy live` (priority 0). Spec: [`grok-page-responder.md`](grok-page-responder.md).

**Probe bearer.** `/api/service-health` is Clerk-protected. The loopback nextjs-db-watchdog sends `Authorization: Bearer $RADON_PROBE_FRESHNESS_TOKEN`. HTTP 401/403 is unknown (auth perimeter), never a Turso wedge. Do not add the route to `isPublicRoute`.

**Watchdog** (`scripts/watchdog/`) runs in four buckets (`intraday`, `continuous`, `daily`, `error`), each with its own timer. Alerts route to Pushover (P1 only) with per-service cooldown and hysteresis, plus an always-on `watchdog-alerts` row in `service_health` so the dashboard banner reflects fires even without an external channel. A delivered P1 also inserts `watchdog_pages`; laptop `com.radon.grok-page-responder` (30s) runs headless Grok to diagnose and, unless the runbook says stand down, ship a fix. After a release passes the live deploy gate, `scripts/deploy_notify.py` sends a normal-priority Pushover (`radon deploy live`, never P1). Ack with `python -m scripts.watchdog ack <service>`. The `error` bucket explicitly skips `watchdog-alerts` itself to avoid recursive alerting. (Discord support was removed 2026-05-19.)

**Banner humanization.** `service_health.last_error` JSON payloads are rewritten into operator-friendly copy before render (see `web/lib/serviceHealthBanner.ts`).

**Database access pattern (post-2026-05-20):** every Radon process now goes direct-to-cloud — the code default since DUR-07 (replica opt-in only via `RADON_DB_USE_REPLICA=1`), with the `RADON_DB_NO_REPLICA=1` kill switch applied fleet-wide through the `radon-.service.d/common.conf` prefix drop-in. The libsql embedded-replica architecture (`data/replica.db`) was retired after multi-writer WAL contention and then single-writer frame conflicts between the replica owner and direct-cloud writers. Reads cost +30–60 ms per cloud round-trip, absorbed by SWR caching. The `replica_watchdog` handler still exists in `monitor_daemon` as a vestigial safety net (it sits idle in the no-replica world), but `data/replica.db` itself should not exist on any host. See `feedback_libsql_replica_one_writer.md`.

**Market-hours gate.** Handlers tagged `requires_market_hours=True` (`fill_monitor`, `exit_orders`, `journal_sync`) only run during 09:30-16:00 ET. The daemon converts UTC to ET via `zoneinfo.ZoneInfo("America/New_York")` so DST is handled automatically; a fail-open UTC-5 fallback fires only if the host is missing `tzdata`. Never hardcode a fixed offset for ET — it silently shifts the window 1h every DST season.

## Cash Flows

`scripts/cash_flow_sync.py` pulls `CashTransaction` rows from IBKR Flex (`IB_FLEX_NAV_QUERY_ID=1497709`) and upserts into the `cash_flows` Turso table. Surfaces on `/orders` via `web/components/CashFlowsSection.tsx`.

**Cadence:** once per ET trading day at 17:00 ET (1h after the close). Flex publishes once per day, so faster polling buys nothing. Holidays and weekends are skipped via `utils.market_calendar`. Late-fires past 18:00 ET if the daemon was off.

**Throttle backoff.** Flex codes 1001 / 1018 / 1019 raise `FlexThrottleError` on the first hit (no internal retry, no sleep), and the handler advances an exponential breaker (24h → 48h → 72h → 168h capped) persisted across daemon restarts. The breaker composes with the daily window; embargo expiry waits until the next 17:00 ET slot.

## Deployment

`git push origin main` triggers `.github/workflows/ci.yml`. Superseded test jobs
may cancel independently, but the production deploy job uses a non-canceling
`deploy-production` concurrency group. It SSHes to Hetzner as `radon`, extracts
`cloud/` from the tested `${{ github.sha }}` into an immutable support runner at
`/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`, and invokes that runner's
deploy script. The monorepo [`cloud/`](../cloud/) directory is the canonical
source for deploy code, systemd units, Caddy, and the IB Gateway Compose
project; host secrets are `/etc/radon/env` (`0600`), with
`/home/radon/radon-cloud/.env` a compatibility symlink.

The deploy holds its activity lock, validates the external env and target SHA,
then verifies the installed root control plane against its manifest **before**
any dependency build, service stop, or transition journal mutation. It builds
in a detached target-SHA worktree, journals and restores the active topology,
and gates FastAPI `/health/lite`, Next.js HTTP, relay TCP/HTTP, and stable core
service restart counts. IB state remains advisory.

The deploy job is capped at 60 minutes and SSH at 55 minutes. The inner deploy
gets 900 seconds plus a 30-second kill window. Root mutation actions get 180
seconds, verify/commit actions get 30 seconds, and lifecycle-lock contention may
consume 190 seconds once per recovery. The tested double-recovery bound is
2,150 seconds, leaving at least ten minutes inside SSH for file restoration and
gate overhead.

Git HEAD equality alone is not success. Confirm the durable release with
`gh run list --workflow=ci.yml --limit 1`. For the exact privileged bootstrap,
recovery, and rollback sequence, follow [`cloud/CLAUDE.md`](../cloud/CLAUDE.md)
and [`docs/monorepo-cloud-migration.md`](monorepo-cloud-migration.md) rather
than duplicating commands in this runbook.

## Production Build Constraint

Next.js 16 prerender crashes on `/_global-error` and `/_not-found` because the root ClerkProvider context isn't materialised in isolated workers. `web/package.json` build pins `next build --experimental-build-mode=compile`. The error and not-found shells (`app/error.tsx`, `app/[ticker]/not-found.tsx`, `app/global-error.tsx`) use plain `<a>` and pure JSX (no `next/link`, `useEffect`, or `globals.css`) for the same reason.
