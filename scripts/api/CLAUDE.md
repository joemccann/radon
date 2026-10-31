# Radon FastAPI — CLAUDE.md

FastAPI service on `radon-app:8321` (loopback `127.0.0.1:8321` on the host; the laptop reaches it over Tailscale). Loaded automatically when cwd is under `scripts/api/`. Project root + `scripts/CLAUDE.md` rules also apply.

---

## IB Gateway — cloud mode

`IB_GATEWAY_MODE=cloud` is the only deployed mode (production `/etc/radon/env`, cloud/scripts/check-env.py:18). The laptop never runs FastAPI against a Gateway and no launcher sets a mode. The `docker` and `launchd` branches in `ib_gateway.py` are legacy laptop paths retained only as the 2FA-lock / backoff test harness, scheduled for removal.

- **`cloud`** (**Hetzner production**): Lifecycle is owned by `/usr/local/bin/radon-ib-gateway-control` from monorepo `cloud/`. FastAPI does TCP/API health only and reports `service_state=reachable` when the port path is up. `POST /ib/restart` DELEGATES to `systemctl restart radon-ib-gateway.service` (via `admin_services.control_unit` — polkit path, helper owns the 2FA lease + latched-transition state machine) when the host has systemd + the helper; the laptop remote-alias case still gets the 503 refusal (2026-07-26 incident: the operator page's restart button had NO working path while the watchdog stood down at its awaiting-2fa cap). **App/broker split:** `RADON_HOST_ROLE=app` never execs the helper. FastAPI proxies Force 2FA / Stop / Start / reset-lease over mTLS to `https://10.0.0.4:8340` (`scripts/ib_gateway_remote`, `radon-ib-gateway-remote.service`). Loopback Clerk bypass is disabled for those POSTs. **Required VPS env:** `IB_GATEWAY_MODE=cloud`, `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud` (not `~/radon-cloud`), secrets at `/etc/radon/env` (the Gateway container reads only `/etc/radon/ib-gateway/gateway.env`). **Password rotation:** `PUT /credentials/ib_gateway` skips the validator and calls `services.rotate_gateway_password()` once (mTLS `POST /rotate-password`, `ROTATE_TIMEOUT_S=150`); the store is written only when the broker replies `written: true`. On the app role it needs an operator JWT like the lifecycle POSTs; the Next proxy forwards it. `rotate-password` is not in `REMOTE_VERBS`, so `/admin/services` cannot reach it. `/home/radon/radon-cloud/.env` is retired. Laptop remote-gateway alias mode also uses `cloud` over Tailscale. **Whole-stack VPS control** via `/usr/local/bin/radon` (or `ssh root@radon-app radon <cmd>`): `radon stop|start|restart|status` operates on all `radon-*` units. Source: `cloud/scripts/operator-radon.sh`.

---

## 2FA-Aware Restart (push lock + backoff + watchdog)

After restart, IB Gateway sits at IBKR Mobile push prompt with API socket open — `port_listening:true` falsely reports success. Three gates guard against stacked-push rejection (IBKR's backend can't reconcile multiple pending push tokens — every approval shows "unsuccessful" when pushes pile up):

1. **In-memory backoff ladder** (per-process). Checked before lease acquisition so a deferred request cannot create or extend a lease. `restart_ib_gateway()` runs `managedAccounts()` probe post-restart; non-empty resets backoff, empty advances (1m → 2m → 5m → 15m → 30m → 60m capped).
2. **Cross-process push lock** (`scripts/utils/ib_2fa_lock.py`, `/var/lib/radon/ib-lease/ib-2fa-push-lock.json` on Linux and per-user Application Support on macOS, 10-min TTL). Every start path that can fire a push acquires the lock first; any active lease, including the same component holder, rejects a second cycle with `reason="2fa_push_in_flight"`.
3. **Watchdog stuck-2FA self-heal (2026-05-20).** `is_stuck_awaiting_2fa()` fires when `auth_state=awaiting_2fa` AND `push_lock_active=false` AND `next_attempt_in_secs<=0`. After 3 consecutive stuck cycles (~3 min), watchdog acquires the lock and invokes `radon-ib-gateway-preheld-restart.service`; that adapter consumes the exact watchdog lease once before calling the authoritative cloud helper. `stuck_2fa_count` freezes during push-in-flight/backoff; resets only on `auth_state=authenticated`.

`/health` exposes `auth_state`, `service_state`, `upstream_dead`, `restart_backoff` (incl. `push_lock`, `attempt_count`, `next_attempt_in_secs`). **`POST /ib/reset-backoff`** clears BOTH in-memory backoff AND push lock — operator escape hatch after manual 2FA approval.

**IBKR operator hold.** `/health` also carries `ib_gateway.operator_hold`, mirrored from the broker `/status` on the app role and read from the local flag elsewhere. `None` means unknown. `/health/lite` carries `true` / `false` only for a confirmed hold observation and `null` when unknown (REL-303 / R-722); the relay retains its last hold until a confirmed release. While held, `/ib/restart` and admin Gateway `start|restart` return 423 without calling the broker (`services._control_gateway`), and the 15s recovery heartbeat skips pool reconnect and the self-restart ladder. `GET|POST /ib/operator-hold` reads, sets and clears the hold through the broker daemon's `hold`/`unhold` (operator JWT on the app role). Runbook: `docs/ib-gateway-recovery.md`.

IBC-side relogin on 2FA timeout is disabled; do not re-enable it. The
[Gateway recovery owner](../../docs/ib-gateway-recovery.md#daily-cycle) explains
token restart, expiry and why blank fields do not disable the daily cycle.
Compose owns the container schedule; the broker compose is the only IBC
configuration and timezone source.

**4. Watchdog API-hang self-heal (2026-06-10).** Separate from stuck-2FA: `is_api_hang()` fires when the gateway is authenticated + `port_listening` but `upstream_dead` (the Java API listener wedged in place — socat accepts TCP but the API handshake times out, Docker's TCP healthcheck misses it). After 3 cycles it restarts the gateway via the lock. The api-watchdog runs as a oneshot every minute; it MUST have `TimeoutStartSec=60` (set on `radon-ib-watchdog.service`) — without it a hung cycle stalls the timer forever. The companion replica hazard (a 1.36GB replica sync every cycle) is structurally gone since DUR-07: `db.client` is direct-to-cloud by default and the fleet drop-in `radon-.service.d/common.conf` keeps `RADON_DB_NO_REPLICA=1` as belt-and-suspenders (see `feedback_gateway_api_hang_and_watchdog_self_hang`). Production `radon start/restart` and every other operator path MUST acquire the same push lease before touching the Gateway; bypasses are release-blocking defects.

Code: `scripts/api/ib_gateway.py:restart_ib_gateway`, `scripts/ib_watchdog.py:run_cycle`, `scripts/utils/ib_2fa_lock.py`. Full state-machine derivation in `docs/ib-gateway-recovery.md`.

---

## Event-Loop Discipline (never freeze the single uvicorn worker)

uvicorn runs ONE worker (one asyncio event loop). **Never run a synchronous/blocking libsql call (`db.execute`/`db.commit`) or other unbounded blocking I/O anywhere in this process** — a hung direct-cloud Turso write froze the whole API (`/health` + `/health/lite` timed out, every request stalled) while the relay/data plane stayed fine; the recurring trigger was `_maybe_dual_write_to_db` running the mirror inline, and even a bounded background thread didn't help because libsql's `commit()` holds the GIL during a hung write. The mirror was therefore moved OUT of this process entirely: each scan subprocess writes its own snapshot + `service_health` row via `scripts/db/scan_mirror.py` (own GIL — a hung write stalls only that scan). Request-scoped blocking I/O uses `asyncio.to_thread`. Diagnose a wedge with `py-spy dump --pid <uvicorn MainPID>` (MainThread blocked in the offending frame). See `feedback_no_sync_libsql_on_fastapi_event_loop`.

**DB access from this process goes through `scripts/api/db_http.py` (`hrana_execute`) — the libSQL HTTP pipeline over stdlib `http.client` keep-alive (one TCP/TLS socket per worker thread) with a real socket timeout (`http.client` releases the GIL, so `asyncio.to_thread` around it is genuinely bounded).** Tests may still patch `urllib.request.urlopen`. Importing `db.client` / `db.writer` / `libsql*` anywhere under `scripts/api/` fails the AST lint `scripts/tests/test_no_sync_libsql_in_api.py`. The `service_health` upsert SQL is shared with the sync writer via `scripts/db/service_health_sql.py` (no libsql import) so serialization stays single-source.

**`radon-api` survives a gateway stop.** Its unit carries `After=radon-ib-gateway.service` and NOTHING else — no `Wants=`, no `Requires=`, no `PartOf=` — so a deliberate gateway stop (the operator page's "Stop Gateway" control) does not cascade-kill api — the control plane (`/health`, `/admin/services`, the Start action) stays up. Unit file: `cloud/services/radon-api.service`. The operator page's gateway Stop/Start lives in `web/components/admin/Ib2faControls.tsx` (Stop is type-to-confirm cascade-aware for relay+monitor; Start reuses the full-stack `radon restart`).

---

## Authentication

All FastAPI routes JWT-protected by default.

The demo service token cannot access `/model-gateway` inference or status. Its only gateway exception is exact `GET /model-gateway/models` for the demo picker; operator JWT and trusted-local lanes retain their existing access. The metered-client scope guard recognizes the standalone model gateway and pins its generic GET/POST routes as privileged.

- **Local/tailnet bypass:** `is_trusted_local_request(request)` = trusted peer **AND** not forwarded. Trusted peers are loopback and the tailnet peers in `RADON_TRUSTED_TAILNET_PEERS` (IPv4 `/32`s inside `100.64.0.0/10`; wider or non-tailnet entries are ignored). `RADON_TAILNET_TRUST_MODE` unset or `log` (default) still trusts all of `100.64.0.0/10` and logs `tailnet peer <ip> not in RADON_TRUSTED_TAILNET_PEERS: would refuse (log-only)` once per peer per 10 min; `enforce` (or any other value, fail closed) trusts only the list, and an empty list means loopback-only. Runbook: `docs/operations.md` "Tailnet trust narrowing". The Hetzner Cloud Network `radon-private` `10.0.0.0/16` (app `10.0.0.2`, broker `10.0.0.4`) is NOT in this bypass: `is_private_net_probe(request)` scopes it to the one call that net carries, the broker watchdog's `GET /health` (REL-170), so a private-net peer still needs a Clerk JWT or API key for everything else. Not wider: `docker0` `172.17.0.0/16` and every other RFC1918 range stay untrusted (`test_auth.py` pins `172.17.0.1` false). A trusted-peer request that carries `X-Forwarded-For` / `Forwarded` / `X-Real-IP` / `X-Forwarded-Host` is denied the bypass. A request carrying reverse-proxy headers (Caddy sets `X-Forwarded-For`) is NOT trusted even from 127.0.0.1, so the public `handle_path /api/ib/*` route can't inherit the bypass. Both the middleware and `verify_clerk_jwt` use it. `scripts/api/auth.py`. See `feedback_health_endpoint_public_leak_and_trust_chokepoint.md`.
- **Demo service token is SCOPED.** `RADON_SERVICE_TOKEN` (header `X-Radon-Service-Token`) authenticates the demo frontend to the demo VM's FastAPI without a per-user JWT, and `is_trusted_service_request` is consumed inside `verify_clerk_jwt`, so its verdict becomes the global middleware verdict. Because it carries no per-user identity it also skips `ALLOWED_USER_IDS` / `RADON_REQUIRE_OPERATOR_ALLOWLIST` (only `verify_clerk_bearer` applies those). It is therefore scoped like `MDW_API_KEY`: `SERVICE_TOKEN_DENIED_PREFIXES` in `scripts/api/auth.py` refuses the lane on `/orders/`, `/trading/`, `/pi/exec`, `/credentials`, `/admin/`, `/ib/`, `/journal/`, `/portfolio/`, `/preferences/` (the preference WRITES; `GET /preferences` stays reachable), and the metered third-party lanes no demo route proxies: `/uw/`, `/research/tradingview/`, `/options/uw-chain` and `/quote/`. The metered lanes are NOT all under one prefix, so each is listed individually (`/options/` cannot be denied wholesale — the demo proxies `/options/rv-ratio/`). A valid token on one of those paths falls through to `verify_clerk_bearer` and needs an operator JWT. The reachable set is the `backend` endpoints in `web/lib/demo/routePolicy.ts`; adding a privileged route under a new prefix fails `scripts/api/tests/test_service_token_scope.py`, which pins the prefixes against the live route surface by set equality. That pin is hand-reviewed and so cannot see a privileged route nobody added to either side; `scripts/api/tests/test_metered_client_scope.py` closes that blind spot from the handler side, requiring every route that reaches a metered client to be denied or declared as a proxied `backend` endpoint. `cloud/scripts/check-env.py` additionally refuses a production env file that sets the key at all (`FORBIDDEN_PRODUCTION_KEYS`), the same interlock shape as the web side's `RADON_AUTHLESS_TEST` assertion.
- **Auth-exempt unconditionally:** `/health`, `/ws-ticket/validate`, `/demo/trial-expiry`, all `*/share` routes. **`/docs` + `/openapi.json` are NOT exempt** (changed 2026-06-28 security audit): they were leaking the full endpoint map to anonymous callers via Caddy `/api/ib/*`. They now require the trusted-local bypass (loopback/tailnet — open Swagger over an SSH tunnel); a public or private-net caller gets 401. `AUTH_EXEMPT_PATHS` is pinned by `test_health_payload.py` + `test_route_authz_matrix.py` (full set-equality — adding/removing needs both pins updated). **`/health` is trust-scoped:** untrusted (public/proxied) callers get `{"status":"ok"}` and, only when `<repo>/REVISION` is one lowercase 40-hex sha, `revision` set to that sha. They never get account IDs, IB state, or topology, and the handler short-circuits before `check_ib_gateway()`. A host with no such file (production) keeps the status-only body. The demo VM deploy writes the file; the drift check reads `revision` from the public body. Trusted local/tailnet callers and the private-net probe (`is_private_net_probe`) get the full payload, including `revision` when the file is present.
- **`/health/lite`** — side-effect-free, account-free coarse IB state (`auth_state`/`service_state`/`upstream_dead`/`port_listening`) for high-frequency pollers (the standalone health daemon). Calls `check_ib_gateway(pool=None)` so it NEVER triggers `reconnect_all`/heal — the 2FA-recovery heartbeat stays on `/health`. **NOT auth-exempt** (loopback daemon covered by bypass; public → 401); never add it to `AUTH_EXEMPT_PATHS`.
- WebSocket auth via `scripts/api/ws_ticket.py` — 30s TTL.
- **JWKS outage is not an auth verdict (REL-235):** in `scripts/api/auth.py` only PyJWT signature/claim errors populate the negative-kid cache; a timeout/OSError/5xx (including `PyJWKClientConnectionError`, which subclasses `PyJWTError`) during a JWKS fetch yields 503 and leaves the kid re-probeable, same rule as `scripts/mcp_hosted/auth.py` (REL-229). The task done-callback distinguishes the same way.
- **No non-expiring key cache:** the `PyJWKClient` in `scripts/api/auth.py` is built WITHOUT `cache_keys`. That flag rebinds `get_signing_key` to a `functools.lru_cache` with no expiry, so a kid resolved once is never looked up in the published JWK Set again and removing a compromised key from the JWKS has no effect on a long-lived worker. The expiring `jwk_set_cache` is the only key cache; same rule in `scripts/mcp_hosted/auth.py` (`scripts/tests/test_jwks_revocation_cache.py`).
- **Cached kid skips the in-flight bound:** a kid already in the unexpired cached JWKS set resolves in `_cached_signing_key` (`scripts/api/auth.py`) without a `MAX_JWKS_INFLIGHT` slot; only unknown kids take the bounded single-flight refetch, same rule as RC-A3 in `scripts/mcp_hosted/auth.py`.

Don't return 4xx for legitimate empty/pending states; use 200 + payload flag (e.g., `missing: true`). 4xx noise in the browser console + Next.js logs masks real errors. See `feedback_http_status_for_real_errors.md`.

---

## Subprocess Pattern (`run_script`)

`GET /informed-flow/{ticker}` serves `data/informed_flow/{TICKER}.json` when
`scan_time` is younger than 30 minutes (the open-hours window in
`web/lib/serviceHealthWindows.ts`) and only then spawns
`fetch_informed_flow.py`. Congress / insider / 13F do not justify a new
interpreter on every 10-minute panel poll (PERF-023). Stale or unstamped
cache still goes live; a failed live fetch still falls back to disk.

`GET /short-availability/{ticker}` overlaps the IB tick-46/89 probe and the
UW `get_short_data` fee overlay (PERF-024). Merge after both complete;
source priority stays IB then UW.

`scripts/api/subprocess.py` exposes `run_script(name, args, timeout)` and `run_script_raw(...)`:

- `run_script`: spawns `<script>`, expects JSON on stdout, returns parsed dict. `_find_json_start()` accepts both `{` and `[` as JSON starts (fixed for the leap-scanner array case).
- `run_script_raw`: returns `{ok, stdout, stderr, exit_code, timed_out}` without JSON parsing — for scripts that emit report text or write files directly.

Failed `run_script` calls retain a parsed `status=error` dictionary in `data`
while `ok` remains false and `error` keeps its existing text. Cancel/modify
routes return that rejection as a coded 502 detail, preserving broker fields
and order identity for `coerceRadonErrorDetail` (REL-021b / R-024). Plain-text
infrastructure failures keep their existing response contract.

CLAUDE.md project-wide rule: **No `spawn()` from Next.js.** All Python subprocess invocation goes through this layer. Callers from the Next.js side use `radonFetch` against FastAPI routes that wrap `run_script*` internally.

**Slot lanes.** `MAX_CONCURRENT_SUBPROCESSES` (env `RADON_MAX_CONCURRENT_SUBPROCESSES`, default 4) is the hard cap. `RESERVED_ORDER_SLOTS` (1) is claimable only by `_ORDER_LANE_SCRIPTS`. `RESERVED_INTERACTIVE_SLOTS` (env `RADON_RESERVED_INTERACTIVE_SLOTS`, default 1) is unclaimable by any caller whose `timeout` exceeds `LONG_RUNNING_TIMEOUT_S` (60 s): with the defaults, cri/vcg/gex/breadth-class scans share 2 slots, interactive calls (chains, expirations, sync, orders) 3, orders 4. Interactive callers wait up to `SUBPROCESS_ADMISSION_WAIT_S` (env `RADON_SUBPROCESS_ADMISSION_WAIT_S`, default 10 s, never past their own `timeout`) for a slot before "Subprocess capacity exhausted"; long-running callers stay fail-fast because they have caches and the scan-gate backoff. 2026-09-08: the VIX position page 502'd on `/options/expirations` + `/index-options/chain` while three scans plus the 30 s-cadence ib_sync/ib_orders pinned every general slot. Tests: `test_subprocess_interactive_lane.py`, `test_subprocess_order_lane.py`.

---

## Autonomous Timers (Hetzner)

Use the [operations owner](../../docs/operations.md#background-services) for
operator procedures. Exact schedules and commands live in the checked-in
[systemd units](../../cloud/services/), installed by the control-plane bootstrap.
Wrappers use literal env parsing (not `set -a`) to avoid `$VAR` expansion.

---

## Options exposure (MenthorQ dashboard)

`GET /options/exposure/{symbol}` uses `MenthorQDashboardClient` and a dedicated Playwright jar at `data/menthorq_dashboard/menthorq_dashboard_storage_state.json` (not the CTA jar). Next.js `OPTIONS_PROXY_TIMEOUT_MS` is 50s; auth queue/exchange/login share `REQUEST_PATH_AUTH_BUDGET_SECONDS` 40s; data calls consume the remaining 45s request allowance. Auth failures embargo unchanged config/jar for 300s; an atomic remint is detected without an API restart. Durable Auth.js cookie expiry governs before ephemeral Cognito expiry; rejected ambient tokens get one recovery attempt. Browser and storage faults are not credential failures.

A provider cube can have `spot_price: null` while all exposure arrays remain valid. Serve it with `spot: null` and `complete: false`; the table and PNG show spot unavailable and no spot marker. Never fabricate a price or discard the cube for an explicitly absent spot.

Bootstrap: WordPress login, then click OIDC `input[name=authorize]` (page stays on `wp-login.php` with `client_id=aws_cognito_client_id`), then wait for `dashboard.menthorq.io`. Skipping Authorize is the 2026-08-20 remint hole. Probe: `menthorq-login-probe`. Cookie metadata: `menthorq-session`.

---

## Service Health Dual-Write

Each scan subprocess records its own Turso snapshot + `service_health[<service>]=ok` row via `scripts/db/scan_mirror.py:mirror_scan_snapshot` (vcg-scan, scanner, discover, flow-analysis, performance, oi-changes, leap-scan, garch-scan; cri/gex inline the same pattern in `cri_scan.py`/`gex_scan.py`). The FastAPI process never writes the mirror — its synchronous libsql writes starved the event loop (see Event-Loop Discipline above). The row drives the banner staleness gate in `web/lib/serviceHealthWindows.ts`. A failed snapshot upsert records `state=error` with the detail; the `data/*.json` cache written by the route stays the fallback.

A scheduled service writing on every cycle is the only way the banner notices when a writer goes silent — handlers that short-circuit on "nochange" must still heartbeat. See `feedback_service_health_heartbeat.md`.

---

## Pool Recovery After 2FA

After 2FA resolves, the FastAPI `ib_pool` can stay stuck disconnected even though `auth_state=authenticated`. `systemctl restart radon-api.service` flips it; symptom signature is `auth_state=authenticated` + pool clients all disconnected + a direct `ib_insync` probe succeeds. See `feedback_ib_pool_stuck_after_2fa.md`.

The auto-heal handler clears stale `service_health` rows for `requires_ib=true` services on this transition (commit 5aea4ec).

**Recovery heartbeat (commit 9a16f05).** The `awaiting_2fa → authenticated` `pool.reconnect_all()` recovery is driven server-side by a FastAPI lifespan task `_ib_recovery_heartbeat_loop` (15s, calls `check_ib_gateway(pool=ib_pool)`), NOT by a browser poll. This is required because the status consumers moved to the read-only `/edge-health` surface (`/health/lite`, `pool=None`, no side effects) — without this loop, recovery would only fire on the every-minute `radon-ib-watchdog` `/health` curl. Don't reintroduce browser-poll-driven recovery; keep the mutating `check_ib_gateway(pool=...)` on the server-side heartbeat + watchdog. The isolated health daemon (`scripts/health_service/`) consumes `/health/lite`, never the mutating `/health`.

**Level-triggered self-heal (`recover_stuck_pool`).** The edge handler above has a circular-dependency deadlock: `auth_state` is derived FROM the pool, so while the pool is wedged every probe derives `awaiting_2fa`, the `awaiting_2fa → authenticated` edge never fires, and an external 2FA approval is invisible forever (`_auth_transition_state` latches at `awaiting_2fa`). `scripts/api/ib_gateway.py:recover_stuck_pool` breaks it: pool-INDEPENDENT (probes the Gateway directly via `_probe_authenticated`, throwaway clientId 98) and LEVEL-triggered. Decision ladder — healthy pool (no disconnected slot) → no-op without probing; disconnected slot + probe NOT authenticated → genuine 2FA wait, do nothing (never acquires the 2FA push lock, never calls `restart_ib_gateway`); disconnected slot + probe authenticated → `pool.reconnect_all()`, then RE-READ the pool and return True iff ≥1 role is connected WITH managed_accounts (the verified re-read the edge handler lacks). The same heartbeat tick runs it after `check_ib_gateway` via `_recover_stuck_pool_guarded` (server.py): single-flight + 60s cooldown (`POOL_RECOVERY_COOLDOWN_SECS`); only a VERIFIED failure (probe authenticated yet pool still stuck) counts toward the ladder, and after `POOL_RECOVERY_MAX_BEFORE_RESTART` (3) consecutive verified failures it escalates to ONE `os._exit(1)` self-restart (radon-api only under systemd `Restart=always`; never the Gateway), then resets the counter so it can never loop-restart. Short-circuits under `RADON_API_TEST_MODE`. By construction this path fires ZERO 2FA pushes. Tests: `scripts/api/tests/test_ib_gateway_pool_recovery.py`, `scripts/api/tests/test_pool_recovery_escalation.py`. Follow-up (not done): teach `ib_watchdog.is_stuck_awaiting_2fa` to defer to this path via a `pool_stuck` /health flag — skipped because surfacing `pool_stuck` needs a probe in the /health builder, which violates the fast-/health contract (probe is 8s, /health budget 2.5s); the 15s heartbeat already beats the watchdog's ~3-min stuck-2FA threshold.

---

## Pi / Ratings — No spawn() from Next.js

`/api/ticker/ratings` and `/api/pi` route from Next.js to FastAPI via `radonFetch`. FastAPI runs the actual Python via `run_script` / `run_script_raw`. CLAUDE.md rule enforced since commit 67fe5e8.

The PI endpoint validates `script` against a two-tier allowlist (`scripts/api/server.py`). **READ-tier** (`_PI_READ_SCRIPTS`: `scanner.py`, `discover.py`, `evaluate.py`, `leap_scanner_uw.py`) — pure analysis/scans, run on the default path. **MUTATE-tier** (`_PI_MUTATE_SCRIPTS`: `ib_sync.py`) — connects to the live IB account and writes Turso portfolio snapshots; refused 400 unless the request carries `allow_mutating: true`. Only the `sync` chat command opts in (Next.js `runPythonScript(..., allowMutating=true)` in `web/app/api/pi/route.ts:executeSync`). Anything outside both tiers → 400. Never expand either tier, and never move a script READ→MUTATE-capable without a security review — `/pi/exec` is reachable from the Next.js chat surface.

---

## Intraday Scan Admission (`scan_gate.py`)

`/regime/scan`, `/breadth/scan`, `/vcg/scan` and `/gex/scan` share one policy
(`SCAN_GATES` + `_gated_scan` in `server.py`): a completed scan is served from
cache for `SCAN_COOLDOWN_S` (120 s) without a subprocess, and a failed scan
(lane exhaustion, script error, ticker mismatch) arms `SCAN_FAILURE_BACKOFF_S`
(60 s) during which the route answers **429 + `Retry-After`** instead of
re-spawning. Never re-run a scan on the retry path: on 2026-08-24 three
periodic callers plus a 5 s client poll re-fired every 502'd scan on the
2-vCPU host and starved the IB Gateway JVM (84 relay tick stalls vs 0 the
prior Friday). The Next.js side mirrors it with `web/lib/backgroundScan.ts`
(in-flight dedupe + 60 s backoff on any failure). Tests:
`scripts/api/tests/test_scan_gate.py`, `web/tests/background-scan-trigger.test.ts`.

## Legacy order helpers (REL-021b / R-039)

Production cancel/modify routes retain the original-client subprocess path.
Do not import the unused pooled shortcut into the server. Legacy helpers
must refuse unknown or mismatched client ownership before mutation; modify
also respects trading halt, while owned cancellation remains available.

## Passive dependency health (R-028 / REL-316)

Trusted `/health` and protected `/health/lite` include coarse `dependencies`
verdicts for database reachability and market data. One relay heartbeat is read
through bounded API Hrana in a worker thread, concurrently with the existing
observational Gateway probe. Database uncertainty never becomes a healthy feed;
malformed or future timestamps remain unknown, stale ticks and farm faults
remain degraded, and no-subscriber/off-hours observations remain idle. Public
`/health` stays liveness-only aside from `revision` when `<repo>/REVISION` is
one lowercase 40-hex sha. This read never heals, reconnects, writes health
rows or exposes raw provider errors/accounts/topology.

## Placement acknowledgement outcomes

A completed placement subprocess may return `status: "error", indeterminate: true` after transmit. Preserve its broker identity and status, record an `indeterminate` audit event, and return 504 `ORDER_INDETERMINATE` before the rejection branch. Recovery must retain this structured outcome and identity even when it replaces connection-error guidance; cloud/managed recovery and legacy restart success/failure must not erase it. Unknown confirmation is not a definitive rejection and must not trigger an automatic retry. Next.js retains the placement idempotency key for this coded outcome as well as transport aborts.
