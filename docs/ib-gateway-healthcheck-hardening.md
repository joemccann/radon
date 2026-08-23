# IB Gateway Healthcheck Hardening — Proposal

**Status**: proposed, not implemented.
**Owner**: Joe.
**Author**: investigation session 2026-05-17 → 2026-05-18.

---

## Problem

On 2026-05-17 → 2026-05-18 (a ~24h window) the Hetzner IB Gateway container
degraded three times in three different ways:

| Time (UTC) | Symptom | Detection latency |
|---|---|---|
| 2026-05-17 23:45 | IBKR daily auto-logoff fired, container exited cleanly, Docker auto-restarted, 2FA dialog opened+closed in <1s for hours (rate-limit) | Joe noticed via dashboard ~12h later |
| 2026-05-18 16:22 | Java app alive, port 4001 accepts TCP, but API thread frozen — pool clients (3/4/5) connected mid-day suddenly went `connected: false`, no shutdown event in logs | Joe noticed banner ~30 min later |
| 2026-05-18 19:42 | Same JVM API-thread hang. Existing pool clients OK, new monitor-daemon connections (clientId 70 / 72) `Connected` at TCP layer then `TimeoutError` on API handshake within 3s | Banner caught it within 1 cycle (~3 min) — but only because the *monitor* tried fresh connections |

The middle and last cases are the harder ones. The container's
`docker inspect` reports `Status: running (healthy)` throughout, and our
own monitoring tools can't distinguish "Java app responsive" from
"Java process exists and accepts TCP."

---

## Why the current healthcheck misses it

`cloud/docker-compose.yml` (production IB Gateway compose; the only
production container) ships a TCP-only healthcheck:

```yaml
healthcheck:
  test:
    - CMD-SHELL
    - >-
      case "$${TRADING_MODE:-paper}" in
        live) port=4001 ;;
        paper) port=4002 ;;
        *) exit 1 ;;
      esac;
      exec bash -c "exec 3<>/dev/tcp/127.0.0.1/$${port}"
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 60s
```

That's a pure TCP open. The OS accepts the SYN as long as the JVM has the
socket bound. Whether the API thread is alive and answering the IB binary
handshake is invisible to bash. Result: when the API thread hangs, the
container stays "healthy" in Docker's eyes and never restarts.

Our `restart_ib_gateway()` in `scripts/api/ib_gateway.py` already has the
right signal (`managedAccounts()` empty after restart → backoff) but it's
only invoked when a request comes in and IB rejects it. There's no
continuous watchdog that proactively detects this drift.

---

## Proposed fix — host-side API-aware watchdog

Add a small systemd timer on the VPS that polls FastAPI's `/health`
every 60s. FastAPI already does the right probe (`ib_pool.connect_all()`
during startup; `service_state` reflects real handshake state). The
watchdog just needs to *act* on a persistent degraded reading.

### Wire

```
radon-ib-watchdog.timer  →  radon-ib-watchdog.service  →  scripts/ib_watchdog.py
```

### Algorithm

> Historical design record: the original pseudocode below predated the shared
> 2FA lease and is not an executable runbook. Production now uses the fixed
> preheld-lease unit and `/usr/local/bin/radon-ib-gateway-control`; direct
> restarts of the main Gateway unit are forbidden.

```
counter = read_state('ib_watchdog_degraded_count', 0)
health  = GET http://localhost:8321/health  (5s timeout)

if health.ib_gateway.service_state == 'healthy':
    write_state('ib_watchdog_degraded_count', 0)
    exit 0

if health.ib_gateway.port_listening and health.ib_gateway.upstream_dead:
    # The hang signature — TCP open but API not responding.
    counter += 1
    write_state('ib_watchdog_degraded_count', counter)
    log_warn(f"IB gateway API-degraded for {counter} cycles")
    if counter >= 3:
        # 3 cycles × 60s = 3 minutes of sustained API hang.
        log_warn("Triggering lease-preheld gateway recovery")
        run("systemctl restart radon-ib-gateway-preheld-restart.service")
        write_state('ib_watchdog_degraded_count', 0)
        # Current production flow: the watchdog already owns the shared
        # 2FA lease. The fixed preheld adapter verifies that exact holder,
        # consumes the lease fingerprint once, and calls the authoritative
        # helper without re-acquiring. Docker uses restart: "no"; an extra
        # push is never considered safe.
    exit 0

# Anything else (auth_state == awaiting_2fa, etc.) is NOT this bug.
# Reset and let the existing 2FA-aware restart logic handle it.
write_state('ib_watchdog_degraded_count', 0)
```

### Update 2026-05-20: stuck-awaiting-2FA branch added

The "let the existing 2FA-aware restart logic handle it" assumption above
turned out to be wrong. The FastAPI `restart_ib_gateway()` backoff is only
consulted when **something else** triggers a restart — there's no proactive
heartbeat that advances it. On a fresh FastAPI start (`attempt_count=0`,
`push_lock=null`), the system would sit at `awaiting_2fa` forever waiting
for an operator to click "Force 2FA Push" on `/admin`. The user
documented this as "the IB-connected-but-banner-degraded contradiction
keeps happening".

So `ib_watchdog` now handles BOTH failure modes (commit 68c6e57):

1. **`is_api_hang()`** — original `upstream_dead && port_listening` signature.
   Unchanged threshold of 3 cycles, restarts via `systemctl restart`.
2. **`is_stuck_awaiting_2fa()`** — new. Fires when ALL of:
   - `auth_state == "awaiting_2fa"`
   - `restart_backoff.push_lock` is null (nothing in flight)
   - `restart_backoff.next_attempt_in_secs <= 0` (no scheduled retry)

   Threshold = 3 cycles (`DEFAULT_STUCK_2FA_THRESHOLD`). On the third
   cycle the watchdog acquires the cross-process 2FA push lock and
   triggers `radon-ib-gateway-preheld-restart.service`, which verifies and
   consumes that exact lease once before the authoritative helper sends
   a fresh IBKR Mobile push. Counter freezes (does NOT reset) when a
   push lock holder appears or a backoff retry gets scheduled — that
   way the next cycle after the lock clears acts immediately rather
   than warming up from zero again. Counter only resets on
   `auth_state=authenticated`.

The push lock is the structural defence against stacked-push rejection
(IBKR rejects every approval when multiple pushes are pending). See
`feedback_2fa_push_stacking.md`. New `stuck_2fa_count` field on
`WatchdogState`; tests in `test_ib_watchdog_2fa_lock.py` cover (a)
push-lock-held case, (b) backoff-scheduled case, (c) threshold-fire
case, plus the existing api-hang regressions.

State persists in `/var/lib/radon/ib-watchdog-state.json` (the
implementation chose a file over Turso for v1 — zero migration, survives
Turso outages). Per-process state, not Turso row.

### Why 3 cycles × 60s

- Today's incident: timeouts started at 19:42 and continued for ~13 min
  before the operator restarted. A 3-minute trigger would have cut that
  to <4 min total.
- Faster (1 cycle) risks restart on a transient blip — the 16:22
  incident showed `Connection timed out` for ~30s before stabilizing
  into the longer hang; we don't want to restart for a 30s network blip.
- 60s polling × 3 cycles is also short enough that the `service_state`
  signal predates user impact in most cases.

### Files to create / modify

- `radon-cloud/services/radon-ib-watchdog.{service,timer}` — systemd units
- `radon-cloud/setup-vps.sh:SERVICE_FILES` — register the new units so
  `wipe-vps.sh` rebuilds with them
- `scripts/ib_watchdog.py` — the probe + restart logic (~80 LOC)
- `scripts/api/server.py` — add `service_state` to `/health` payload if
  it isn't already exposed at the right granularity (verify; today's
  output included `service_state: 'unhealthy'` so likely fine)
- `web/lib/serviceHealthWindows.ts` — register `ib-watchdog` so its own
  cycles surface in the banner (event-driven, 24h closed window per the
  precedent in `feedback_event_driven_writer_windows.md`)

---

## Open questions for you

1. **Is restart frequency OK?** This will produce extra IB Gateway
   restarts during normal trading sessions when the JVM degrades. Each
   restart costs ~30s of dead time and one 2FA tap. Given how often
   we've seen the degradation, that seems strictly better than a 13+
   minute hang — but worth confirming.
2. **Should the daily auto-logoff at 23:45 UTC also be addressed
   here?** Resolved 2026-05-20: yes. The new `is_stuck_awaiting_2fa()`
   classifier above now fires a fresh IBKR Mobile push after 3 cycles
   of stuck-without-recovery. The user only has to tap Approve once
   per cycle instead of remembering to click "Force 2FA Push" on the
   admin panel. A Pushover alert on entering stuck-2FA could be a
   future enhancement, but the structural fix obsoletes the need to
   wake the operator up — they'll get the push notification on their
   phone via IBKR Mobile directly.
3. **Where should the state live?** Turso `service_health.extra` is
   convenient (already plumbed) but conflates worker state with
   health metadata. A small `daemon_state` table is cleaner. Lean
   toward `service_health.extra` for v1 since it's zero-migration.

---

## Out of scope

- **JVM tuning** (heap size, GC tuning) — possible root cause of
  the hangs but unverifiable without thread dumps. If hangs continue
  with the watchdog in place, next step is to enable
  `-XX:+HeapDumpOnOutOfMemoryError` and capture a `jstack` next hang.
- **Replacing the gnzsnz/ib-gateway image** — possible upstream bug
  in version `10.45.1b`. The 10.x series has had API-thread bugs
  before. Worth a re-check of IBKR's release notes for newer point
  releases, but not part of this proposal.
- **Moving to client portal gateway** — would change the entire
  data path; out of scope.

---

## Estimated effort

~3 hours: 1h for the watchdog script and tests, 1h for systemd units
+ setup-vps.sh wiring + serviceHealthWindows registration, 1h for VPS
deploy / verify / soak.

## Related

- Memory: `feedback_use_radon_restart_not_piecemeal_systemctl.md` —
  why the operator CLI is the only acceptable stop/start invocation
- Memory: `feedback_ib_gateway_2fa_verification.md` — the existing
  2FA-aware restart backoff
- Memory: `feedback_ib_pool_stuck_after_2fa.md` — `systemctl restart
  radon-api.service` recovery for the post-2FA stuck-pool symptom
- `scripts/api/ib_gateway.py:restart_ib_gateway()` — current
  in-process restart with 2FA backoff
- `web/lib/serviceHealthWindows.ts` — staleness window registration
  contract this watchdog plugs into
