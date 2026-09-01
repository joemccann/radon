# IB Gateway — Recovery State Machine

Detailed derivation of the 2FA-aware restart + push lock + watchdog self-heal. Summary in `scripts/api/CLAUDE.md`; this doc is the long-form reference.

---

## Problem

After a restart, IB Gateway sits at the IBKR Mobile push prompt with the API socket open. Naive health checks (`port_listening == true`) falsely report success. Worse: IBKR's backend cannot reconcile multiple pending push tokens — if a second push request fires while the first is pending, every approval shows "unsuccessful" on the user's phone.

Symptoms before the lock:
- FastAPI restart fires push A. Watchdog observes "still no session, looks dead" 60s later, fires push B. User approves either A or B on the phone — IBKR rejects both because two tokens are in flight.
- Net effect: gateway stuck `awaiting_2fa` for hours, unrecoverable without manual `POST /ib/reset-backoff` + manual approval.

---

## Three Gates

### 1. Cross-process push lock

`scripts/utils/ib_2fa_lock.py` reads/writes `/var/lib/radon/ib-lease/ib-2fa-push-lock.json`. 10-min TTL.

Every restart path that fires a push acquires the lock first. While held, restart requests REJECTED with `reason="2fa_push_in_flight"`.

Required participants:
- Local Docker recovery in `scripts/api/ib_gateway.py` and `scripts/docker_ib_gateway.sh`
- The production `/usr/local/bin/radon-ib-gateway-control` helper installed from `radon-cloud`
- `radon-ib-watchdog`, which acquires the lease and invokes the fixed `radon-ib-gateway-preheld-restart.service` adapter exactly once
- Boot, operator, admin-panel, and laptop cloud starts, all of which delegate to the production helper instead of acquiring independently

This is what defends against stacked-push rejection.

**Two release rules keep the lease from outliving the push it guards** (2026-08-25: an
admin stop landed 14s after a restart, the stop path left the lease held, and every
recovery control - Start Gateway, Restart All Services, `radon restart`, the watchdog -
stayed refused for the remaining 590s with no container on the host):

- `radon-ib-gateway-control stop` releases the lease unconditionally after a converged
  stop. A stopped container has no login session, so no push can still be pending
  against it. An unconverged stop keeps the lease - the container is still up.
- A lease is not honoured once the Gateway is provably down: older than
  `GATEWAY_DOWN_GRACE_SECS` (90s) with nothing accepting on `IB_GATEWAY_HOST:PORT`.
  A real pending push always keeps 4001 listening (`auth_state=awaiting_2fa` +
  `port_listening=true`), so this only ever eats an orphan - a killed control plane, a
  container crash, an out-of-band `docker stop`. The grace covers container boot, where
  the lease exists a beat before the port binds. The probe runs only for a lease past its
  grace, so `/health` polling never opens a socket.

### 2. In-memory backoff ladder

Per-process. `restart_ib_gateway()` runs a `managedAccounts()` probe post-restart:
- Non-empty result → reset backoff to baseline.
- Empty result → advance backoff: **1m → 2m → 5m → 15m → 30m → 60m capped**.

Backoff applies to the next restart attempt by THIS process. Cross-process backoff is the push lock above.

### 3. Watchdog stuck-2FA self-heal (2026-05-20)

`is_stuck_awaiting_2fa()` fires when ALL of:
- `auth_state == "awaiting_2fa"`
- `push_lock_active == false`
- `next_attempt_in_secs <= 0`

After **3 consecutive stuck cycles (~3 min)**, the watchdog acquires the push lock and starts `radon-ib-gateway-preheld-restart.service`. That fixed adapter verifies the exact watchdog holder, consumes that lease fingerprint once, and delegates the Docker cycle to the production helper without acquiring a second lease.

`stuck_2fa_count` freezes during push-in-flight or active backoff. Resets only on `auth_state == "authenticated"`.

Without the self-heal, a 2FA timeout where the user dismisses the push notification leaves the gateway stuck until the next operator interaction. With it, the watchdog retries cleanly after backoff expires.

### 4. Watchdog API-hang self-heal (2026-06-10)

A distinct failure from stuck-2FA: the IB Gateway Java API listener wedges **in place** while the session stays authenticated. Signature: `auth_state == "authenticated"` + `port_listening == true` but `upstream_dead == true` / `service_state == "unhealthy"`; socat floods `Connection reset by peer`; a fresh client gets TCP `Connected` then `API connection failed: TimeoutError`; Docker's TCP healthcheck (`/dev/tcp/127.0.0.1/4001`) times out (accepts then stalls). Docker's `restart` policy never fires (the process does not exit). `is_api_hang()` catches it and, after 3 cycles, restarts the gateway via the push lock.

The api-watchdog is a oneshot fired every minute (`radon-ib-watchdog.timer`). Two unit-level requirements keep it from breaking itself (it nearly never fired because of these):
- **`TimeoutStartSec=60`** — `Type=oneshot` has no default start timeout, so a hung cycle (its own probe, or a slow DB write) runs forever and, since oneshot can't overlap, permanently stalls the timer. The 6h hang on 2026-06-10 was exactly this.
- **No embedded replica** — when `get_db()` still defaulted to the replica, a missing `Environment=RADON_DB_NO_REPLICA=1` resurrected a multi-GB embedded `data/replica.db` and `conn.sync()`'d it every cycle, hanging the oneshot. Structurally fixed by DUR-07: direct-to-cloud is the code default (replica opt-in only via `RADON_DB_USE_REPLICA=1`), and the fleet drop-in `radon-.service.d/common.conf` keeps `RADON_DB_NO_REPLICA=1` as belt-and-suspenders.

See `feedback_gateway_api_hang_and_watchdog_self_hang`. Gateway-side farm-down (gateway authenticated but the relay gets zero ticks) is recovered by a full `radon restart`, not a relay-only restart.

### 5. Pool self-heal — post-2FA stuck pool (2026-06-24, commit 46ba1e1)

A failure *inside radon-api*, not the gateway: after the user approves the 2FA push the gateway authenticates, but this process's three pool clients (sync 3 / orders 4 / data 5) stay `connected=false` with empty `managed_accounts`, so `/health` reports `auth_state=awaiting_2fa` even though a throwaway-clientId probe to `4001` returns the account. The app shows "awaiting 2FA" until someone restarts radon-api.

**Why the earlier (2026-05-19) edge fix never worked:** it called `pool.reconnect_all()` on the `awaiting_2fa → authenticated` edge, but `auth_state` is *derived from the pool* (`_derive_auth_state` → authenticated only when a role is connected WITH accounts). While the pool is wedged every heartbeat derives `awaiting_2fa`, so the edge never fires and `_auth_transition_state` latches at `awaiting_2fa` — an external approval is invisible to the process forever. Circular dependency.

**The fix — `recover_stuck_pool` (`scripts/api/ib_gateway.py`), level-triggered + pool-independent, run from `_ib_recovery_heartbeat_tick` (15s) via `_recover_stuck_pool_guarded` (`server.py`):** healthy pool → no-op; disconnected slot + an independent `_probe_authenticated()` (throwaway clientId 98) that is NOT authenticated → genuine 2FA wait, do nothing (never touches the gateway/lock, ZERO pushes); disconnected slot + probe authenticated → `reconnect_all()` then RE-READ the pool, success iff a role is now connected WITH accounts. Single-flight + 60s cooldown; only a *verified* failure (probe authenticated yet pool still stuck) counts toward a 3-strike ladder, after which it self-restarts **radon-api only** (`os._exit(1)` under systemd `Restart=always`; never the gateway), then resets so it cannot loop. Pool reconnects in ~15-60s with no operator action and no new push. Tests: `scripts/api/tests/test_ib_gateway_pool_recovery.py` + `test_pool_recovery_escalation.py`. Follow-up not done: a `pool_stuck` /health flag to keep the watchdog from restarting the gateway on this signature — skipped because the probe is 8s and breaks the fast-/health 2.5s budget; the 15s heartbeat beats the watchdog's ~3-min threshold anyway.

---

## Status Surface

`GET /health` exposes:

```json
{
  "auth_state": "authenticated" | "awaiting_2fa" | "unauthenticated",
  "service_state": "...",
  "upstream_dead": false,
  "restart_backoff": {
    "push_lock": { "active": false, "expires_at": null },
    "attempt_count": 0,
    "next_attempt_in_secs": 0
  }
}
```

Next.js footer reads via `useIBStatusContext().displayStatus` (polls `/api/admin/health` every 15s). Fixed "footer says CONNECTED while banner says degraded".

---

## Operator Escape Hatches

`POST /ib/reset-backoff` clears BOTH in-memory backoff AND the cross-process push lock. Use after manually approving 2FA on the phone. On a split-topology **app**-role host with a remote gateway configured it ALSO issues `reset-lease` to the broker daemon over mTLS and reports `remote` / `broker_lease_released` in the payload (`server.py` `ib_reset_backoff`). That release starts the broker's 60s per-verb cooldown (`VERB_COOLDOWN_S`, `scripts/ib_gateway_remote/serve.py`), so a Start or Restart issued in the next minute comes back `409` with the reason in `detail` — a refusal, not a failure. Wait it out; no verb clears it. See [`spof-host-split.md`](spof-host-split.md).

`/usr/local/bin/radon restart` and the admin Gateway controls delegate to `/usr/local/bin/radon-ib-gateway-control`. A healthy start is a no-op with no lease; a stopped/missing start or any restart atomically acquires the lease before touching Docker.

`radon restart` (whole-stack) restarts all `radon-*` units. Use after a sustained outage.

---

## What NOT to Do

- **Do not re-enable IBC-side relogin on 2FA timeout** (`TWOFA_TIMEOUT_ACTION: exit`, `RELOGIN_AFTER_TWOFA_TIMEOUT: "no"` in `docker/ib-gateway/docker-compose.yml`). VPS counterpart uses IBC default (`no`). IBC's relogin bypasses the push lock and reintroduces the stacked-push bug.
- **Do not piecemeal `systemctl stop radon-<one>`** — a clean stop does not `Restart=always` back, so the unit stays down until something starts it. Use `radon restart` instead. (Stopping `radon-ib-gateway` no longer cascade-stops api/relay/monitor: since 44e89e1b they are `After=`-ordered only, never `PartOf=`; a 2FA restart leaves the app plane up. See `docs/spof-host-split.md`.) See `feedback_use_radon_restart_not_piecemeal_systemctl.md`.
- **Do not assume `auth_state=authenticated` means the pool is healthy.** After 2FA resolves, the FastAPI `ib_pool` can stay stuck disconnected. As of 2026-06-24 (46ba1e1) the `recover_stuck_pool` self-heal (Gate 5) reconnects it in ~15-60s with no operator action, so **do not reflexively restart radon-api** — give the heartbeat a minute. `systemctl restart radon-api.service` remains the emergency override if the self-heal genuinely fails. See `feedback_ib_pool_stuck_after_2fa.md`.
- **Do not call `docker compose`, `docker restart`, or `systemctl restart radon-ib-gateway.service` directly.** Those paths bypass real-container inspection or split one logical cycle across multiple control planes. Use the admin Gateway action or `/usr/local/bin/radon restart`; both use the authoritative helper and refuse while any 2FA lease is active. Clear a lease with `POST /ib/reset-backoff` only after verifying no push is actually in flight.
- **Do not run a synchronous libsql write on the FastAPI event loop.** A hung Turso write freezes the whole API (`/health` times out, which also fails `deploy.sh`'s gateway-ready gate). Offload to a thread. See `feedback_no_sync_libsql_on_fastapi_event_loop`.

---

## Code References

- `scripts/api/ib_gateway.py:restart_ib_gateway`
- `scripts/api/ib_gateway.py:recover_stuck_pool` + `_probe_authenticated` (Gate 5, pool self-heal)
- `scripts/api/server.py:_recover_stuck_pool_guarded` + `_ib_recovery_heartbeat_tick` (the 15s driver)
- `scripts/ib_watchdog.py:run_cycle`
- `scripts/utils/ib_2fa_lock.py`
- `radon-cloud/scripts/ib-gateway-control.sh`
- `radon-cloud/services/radon-ib-gateway-preheld-restart.service`
- `scripts/api/auth.py:51-54` (localhost bypass for Next.js → FastAPI)

---

## Related Feedback Memories

- `feedback_2fa_push_stacking` — stacked push rejection
- `feedback_ib_gateway_2fa_verification` — managedAccounts probe
- `feedback_systemd_cascade_stop_no_autorecover` — cascade-stop issue
- `feedback_ib_pool_stuck_after_2fa` — post-2FA pool recovery
- `feedback_ib_insync_no_request_timeouts` — request-bounding pattern needed because ib_insync blocks indefinitely during awaiting_2fa
- `feedback_gateway_api_hang_and_watchdog_self_hang` — API-listener wedge + watchdog self-hang (TimeoutStartSec, no-replica)
- `feedback_radon_restart_stacks_2fa_with_watchdog` — radon restart stacks pushes; recovery recipe
- `feedback_no_sync_libsql_on_fastapi_event_loop` — event-loop freeze from sync DB writes
