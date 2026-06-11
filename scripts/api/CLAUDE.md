# Radon FastAPI — CLAUDE.md

FastAPI service on `localhost:8321`. Loaded automatically when cwd is under `scripts/api/`. Project root + `scripts/CLAUDE.md` rules also apply.

---

## IB Gateway — Three Modes

`IB_GATEWAY_MODE` env, persisted to `.env.ib-mode`. Toggle via `scripts/ib mode {local|cloud}`. Switching does NOT auto-reconnect — restart the dev stack.

- **`docker`** (default local; also Hetzner since 2026-05-07): `ghcr.io/gnzsnz/ib-gateway`, `restart: unless-stopped`. `npm run ib:start`. **Hetzner gotcha:** container launched by `radon-ib-gateway.service` from `/home/radon/radon-cloud/`, not in-tree `<repo>/docker/ib-gateway/`. `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon-cloud` is required.
- **`cloud`** (laptop dev default): Hetzner VM `ib-gateway:4001` via Tailscale. TCP probe only — `POST /ib/restart` returns 503. Laptop aliases (SSH-wrapped, `~/.zshrc`): `ibstart/stop/restart/status/logs/health`. **Whole-stack VPS control** via `/usr/local/bin/radon` (or `ssh root@ib-gateway radon <cmd>`): `radon stop|start|restart|status` operates on all `radon-*` units. Source: `radon-cloud/scripts/operator-radon.sh`.
- **`launchd`** (legacy): `~/ibc/bin/`, Mon-Fri auto-lifecycle.

Auto-recovery (docker): port + CLOSE_WAIT detection at startup (poll 45s); subprocess errors trigger health check first — only restart if port not listening or CLOSE_WAIT. Client ID collisions, VOL errors, transient timeouts do NOT trigger restart.

---

## 2FA-Aware Restart (push lock + backoff + watchdog)

After restart, IB Gateway sits at IBKR Mobile push prompt with API socket open — `port_listening:true` falsely reports success. Three gates guard against stacked-push rejection (IBKR's backend can't reconcile multiple pending push tokens — every approval shows "unsuccessful" when pushes pile up):

1. **Cross-process push lock** (`scripts/utils/ib_2fa_lock.py`, `/var/lib/radon/ib-2fa-push-lock.json`, 10-min TTL). Every restart path that fires a push acquires the lock first; while held, requests REJECTED with `reason="2fa_push_in_flight"`.
2. **In-memory backoff ladder** (per-process). `restart_ib_gateway()` runs `managedAccounts()` probe post-restart; non-empty resets backoff, empty advances (1m → 2m → 5m → 15m → 30m → 60m capped).
3. **Watchdog stuck-2FA self-heal (2026-05-20).** `is_stuck_awaiting_2fa()` fires when `auth_state=awaiting_2fa` AND `push_lock_active=false` AND `next_attempt_in_secs<=0`. After 3 consecutive stuck cycles (~3 min), watchdog acquires lock + triggers `systemctl restart radon-ib-gateway.service`. `stuck_2fa_count` freezes during push-in-flight/backoff; resets only on `auth_state=authenticated`.

`/health` exposes `auth_state`, `service_state`, `upstream_dead`, `restart_backoff` (incl. `push_lock`, `attempt_count`, `next_attempt_in_secs`). **`POST /ib/reset-backoff`** clears BOTH in-memory backoff AND push lock — operator escape hatch after manual 2FA approval.

IBC-side relogin on 2FA timeout is **disabled** (`TWOFA_TIMEOUT_ACTION: exit`, `RELOGIN_AFTER_TWOFA_TIMEOUT: "no"` in `docker/ib-gateway/docker-compose.yml`). VPS counterpart uses IBC default (`no`). **Do not re-enable** anywhere; bypasses the push lock.

**4. Watchdog API-hang self-heal (2026-06-10).** Separate from stuck-2FA: `is_api_hang()` fires when the gateway is authenticated + `port_listening` but `upstream_dead` (the Java API listener wedged in place — socat accepts TCP but the API handshake times out, Docker's TCP healthcheck misses it). After 3 cycles it restarts the gateway via the lock. The api-watchdog runs as a oneshot every minute; it MUST have `TimeoutStartSec=60` (set on `radon-ib-watchdog.service`) and force `RADON_DB_NO_REPLICA=1` (in `scripts/ib_watchdog.py` + the unit) — without those it hangs forever on its own probe / a 1.36GB replica sync and permanently stalls (see `feedback_gateway_api_hang_and_watchdog_self_hang`). A `radon restart` does NOT hold the push lock and can stack pushes with the watchdog (see `feedback_radon_restart_stacks_2fa_with_watchdog`).

Code: `scripts/api/ib_gateway.py:restart_ib_gateway`, `scripts/ib_watchdog.py:run_cycle`, `scripts/utils/ib_2fa_lock.py`. Full state-machine derivation in `docs/ib-gateway-recovery.md`.

---

## Event-Loop Discipline (never freeze the single uvicorn worker)

uvicorn runs ONE worker (one asyncio event loop). **Never run a synchronous/blocking libsql call (`db.execute`/`db.commit`) or other unbounded blocking I/O directly in an async handler** — a hung direct-cloud Turso write froze the whole API (`/health` + `/health/lite` timed out, every request stalled) while the relay/data plane stayed fine; the recurring trigger was `_maybe_dual_write_to_db` running the mirror inline. Fire-and-forget DB side-effects (the dual-write mirror + `service_health` heartbeat) now go through a dedicated bounded background thread (`_db_mirror_worker` in `server.py`, `queue.Queue(maxsize=256)`); request-scoped blocking I/O uses `asyncio.to_thread`. Diagnose a wedge with `py-spy dump --pid <uvicorn MainPID>` (MainThread blocked in the offending frame). See `feedback_no_sync_libsql_on_fastapi_event_loop`.

**`radon-api` survives a gateway stop.** Its unit uses `Wants=` (NOT `Requires=`) `radon-ib-gateway.service`, so a deliberate gateway stop (the operator page's "Stop Gateway" control) does not cascade-kill api — the control plane (`/health`, `/admin/services`, the Start action) stays up. radon-cloud `radon-api.service`. The operator page's gateway Stop/Start lives in `web/components/admin/Ib2faControls.tsx` (Stop is type-to-confirm cascade-aware for relay+monitor; Start reuses the full-stack `radon restart`).

---

## Authentication

All FastAPI routes JWT-protected by default.

- **Local/tailnet bypass:** `is_trusted_local_request(request)` = loopback/tailnet peer **AND** not forwarded. A request carrying reverse-proxy headers (Caddy sets `X-Forwarded-For`) is NOT trusted even from 127.0.0.1, so the public `handle_path /api/ib/*` route can't inherit the bypass. Both the middleware and `verify_clerk_jwt` use it. `scripts/api/auth.py`. See `feedback_health_endpoint_public_leak_and_trust_chokepoint.md`.
- **Auth-exempt unconditionally:** `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`, all `*/share` routes. **`/health` is trust-scoped:** untrusted (public/proxied) callers get `{"status":"ok"}` only — never account IDs / IB state / topology — and short-circuit before `check_ib_gateway()`. Trusted local/tailnet callers get the full payload.
- **`/health/lite`** — side-effect-free, account-free coarse IB state (`auth_state`/`service_state`/`upstream_dead`/`port_listening`) for high-frequency pollers (the standalone health daemon). Calls `check_ib_gateway(pool=None)` so it NEVER triggers `reconnect_all`/heal — the 2FA-recovery heartbeat stays on `/health`. **NOT auth-exempt** (loopback daemon covered by bypass; public → 401); never add it to `AUTH_EXEMPT_PATHS`.
- WebSocket auth via `scripts/api/ws_ticket.py` — 30s TTL.

Don't return 4xx for legitimate empty/pending states; use 200 + payload flag (e.g., `missing: true`). 4xx noise in the browser console + Next.js logs masks real errors. See `feedback_http_status_for_real_errors.md`.

---

## Subprocess Pattern (`run_script`)

`scripts/api/subprocess.py` exposes `run_script(name, args, timeout)` and `run_script_raw(...)`:

- `run_script`: spawns `<script>`, expects JSON on stdout, returns parsed dict. `_find_json_start()` accepts both `{` and `[` as JSON starts (fixed for the leap-scanner array case).
- `run_script_raw`: returns `{ok, stdout, stderr, exit_code, timed_out}` without JSON parsing — for scripts that emit report text or write files directly.

CLAUDE.md project-wide rule: **No `spawn()` from Next.js.** All Python subprocess invocation goes through this layer. Callers from the Next.js side use `radonFetch` against FastAPI routes that wrap `run_script*` internally.

---

## Autonomous Timers (Hetzner)

| Timer | Cadence | Endpoint |
|---|---|---|
| `radon-refresh.timer` | Mon–Fri */15min | direct `scripts/data_refresh.py` |
| `radon-vcg-refresh.timer` | Mon–Fri 13–21 UTC */5min | `POST /vcg/scan` |
| `radon-portfolio-sync.timer` | Mon–Fri 13–21 UTC */60s | `POST /portfolio/sync` |
| `radon-cta-sync.timer` | Mon–Fri 18:15, 19:00, 21:30 UTC | `POST /menthorq/cta` |
| `radon-leap.timer` | Mon–Fri 14:00 UTC | `POST /leap/scan` |
| `radon-llm-index.timer` | Daily 06:30 UTC | direct `scripts/llm_token_index.py --record` |
| `radon-watchdog-{intraday,continuous,daily,error}.timer` | see `scripts/watchdog/CLAUDE.md` | reads `service_health` |

Unit files in `radon-cloud/services/`; enumerated by `setup-vps.sh SERVICE_FILES`. Wrappers use literal env parser (not `set -a`) to avoid `$VAR` expansion (see `feedback_env_file_shell_expansion.md`).

---

## Service Health Dual-Write

`_maybe_dual_write_to_db()` in `scripts/api/server.py` records `service_health[<name>]=ok` for every recognised JSON cache (`vcg.json`, `gex.json`, `cri.json`, `discover.json`, `scanner.json`, `flow_analysis.json`, `performance.json`, `leap.json`, etc.). The row drives the banner staleness gate in `web/lib/serviceHealthWindows.ts`. Failures record `state=error` with `last_error.message`.

A scheduled service writing on every cycle is the only way the banner notices when a writer goes silent — handlers that short-circuit on "nochange" must still heartbeat. See `feedback_service_health_heartbeat.md`.

---

## Pool Recovery After 2FA

After 2FA resolves, the FastAPI `ib_pool` can stay stuck disconnected even though `auth_state=authenticated`. `systemctl restart radon-api.service` flips it; symptom signature is `auth_state=authenticated` + pool clients all disconnected + a direct `ib_insync` probe succeeds. See `feedback_ib_pool_stuck_after_2fa.md`.

The auto-heal handler clears stale `service_health` rows for `requires_ib=true` services on this transition (commit 5aea4ec).

**Recovery heartbeat (commit 9a16f05).** The `awaiting_2fa → authenticated` `pool.reconnect_all()` recovery is driven server-side by a FastAPI lifespan task `_ib_recovery_heartbeat_loop` (15s, calls `check_ib_gateway(pool=ib_pool)`), NOT by a browser poll. This is required because the status consumers moved to the read-only `/edge-health` surface (`/health/lite`, `pool=None`, no side effects) — without this loop, recovery would only fire on the every-minute `radon-ib-watchdog` `/health` curl. Don't reintroduce browser-poll-driven recovery; keep the mutating `check_ib_gateway(pool=...)` on the server-side heartbeat + watchdog. The isolated health daemon (`scripts/health_service/`) consumes `/health/lite`, never the mutating `/health`.

---

## Pi / Ratings — No spawn() from Next.js

`/api/ticker/ratings` and `/api/pi` route from Next.js to FastAPI via `radonFetch`. FastAPI runs the actual Python via `run_script` / `run_script_raw`. CLAUDE.md rule enforced since commit 67fe5e8.

The PI endpoint validates `script` against an allowlist (`scanner.py`, `discover.py`, `evaluate.py`, `ib_sync.py`, `leap_scanner_uw.py`). Never expand the allowlist without a security review — `/pi/exec` is reachable from the Next.js chat surface.
