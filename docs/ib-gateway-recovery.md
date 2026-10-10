# IB Gateway — Recovery State Machine

Detailed derivation of the 2FA-aware restart + push lock + watchdog self-heal. Summary in `scripts/api/CLAUDE.md`; this doc is the long-form reference.

---

## Readiness verification

**Symptom and prerequisites:** after startup, restart or a daily cycle, the
API socket can listen while login is incomplete. Use the selected mode's
FastAPI `/health` response and the Gateway logs; a TCP connection or Docker
`healthy` result does not prove authentication. The [compose healthcheck](../docker/ib-gateway/docker-compose.yml)
only opens the configured API port. `_derive_auth_state` in
[`ib_gateway.py`](../scripts/api/ib_gateway.py) requires a connected pool
client with non-empty `managed_accounts` to report `auth_state=authenticated`.

**Blast radius and safe diagnosis:** reading health and logs does not place
orders or request another login. Check `ib_gateway.auth_state`,
`ib_gateway.upstream_dead`, `ib_pool`, operator hold and the push lease before
any recovery action. `unknown` or `remote` is not authentication proof.

**Stop conditions and verification:** stop manual recovery while a hold,
active or unreadable lease, login throttle, or pending 2FA prevents it.
Approve the existing prompt instead of generating another. Recovery requires
`auth_state=authenticated`, no upstream failure and healthy pool roles needed
by the operation; fresh relay ticks also matter for live pricing.

**Rollback and escalation:** diagnosis changes nothing to roll back. If a
controlled recovery fails, do not loop starts or clear safety gates. Leave a
held Gateway held, retain sanitized health/log evidence and escalate to the
operator using the applicable recovery procedure below.

## Daily cycle

The [Docker compose configuration](../docker/ib-gateway/docker-compose.yml)
and [production compose configuration](../cloud/docker-compose.yml) own the
IBC daily token-restart setting and timezone. A token restart can avoid a new
login while the token remains valid; expiry can still require 2FA. A blank
restart field leaves the Gateway's stored daily-cycle setting unchanged; it
does not disable that cycle.

**Symptom and prerequisites:** investigate a recurring login interruption
using the selected Gateway mode and its effective configuration.
[`setup_ibc.sh`](../scripts/setup_ibc.sh) writes blank restart fields for
local launchd and reports that the Gateway's stored daily-cycle setting
applies. The installer does not choose a cycle. The launchd definition's
absence of a start schedule also does not disable the Gateway's own cycle.

**Blast radius and safe diagnosis:** inspect configuration and logs without
changing the broker session. Do not copy container clock settings into local
IBC: local configuration and timezone must be checked independently.
**Stop conditions and verification:** stop on an active hold, lease or login
throttle; follow [readiness verification](#readiness-verification) after a
cycle instead of relying on its elapsed time or open port.
**Rollback and escalation:** diagnosis has no rollback. Do not change restart
settings as an incident experiment; escalate a wanted local cycle change for a
separately tested installer change.

## Relay recovery

**Symptom and prerequisites:** prices stop updating during market hours with
outstanding subscriptions. The [decision core](../scripts/lib/staleDataMachine.js)
owns stale thresholds and retry bounds. A silent subject on an otherwise live
plane is resubscribed; a stale whole plane enters bounded resubscribe/socket
reconnect recovery before escalation.

`shouldRequestGatewayRestart` permits the [relay](../scripts/ib_realtime_server.js)
to request `POST /ib/restart` after escalation in `docker` and `cloud` modes;
`launchd` stays alert-only. The request delegates to FastAPI's push lease and
backoff gates rather than restarting Docker directly. An operator hold
suppresses recovery and a hold refusal makes the relay stand down.

**Blast radius and safe diagnosis:** a Gateway restart can interrupt all IB
clients and require 2FA. Read relay health/logs, Gateway health and existing
hold/lease state first. Do not start another manual login just because ticks
are stale; automatic recovery may already own the prompt.
**Stop conditions and verification:** stop manual retries on an operator hold,
pending 2FA, lease refusal, backoff or throttle. Recovery verification requires
fresh ticks and [authenticated health](#readiness-verification).
**Rollback and escalation:** diagnosis has no rollback; preserve holds and
leases if recovery fails. Escalate sustained stale pricing to the operator
with sanitized relay and Gateway evidence instead of bypassing those gates.

## Problem

After a restart, IB Gateway sits at the IBKR Mobile push prompt with the API socket open. Naive health checks (`port_listening == true`) falsely report success. Worse: IBKR's backend cannot reconcile multiple pending push tokens — if a second push request fires while the first is pending, every approval shows "unsuccessful" on the user's phone.

Symptoms before the lock:
- FastAPI restart fires push A. Watchdog observes "still no session, looks dead" 60s later, fires push B. User approves either A or B on the phone — IBKR rejects both because two tokens are in flight.
- Net effect: gateway stuck `awaiting_2fa` for hours, unrecoverable without manual `POST /ib/reset-backoff` + manual approval.

---

## Gateway logs

**Symptom:** login or recovery output disappears after a container recreate.
**Prerequisites:** identify local Docker versus the production broker and retain
read-only access to that host. The [production compose source](../cloud/docker-compose.yml)
owns retained logging; the [local compose source](../docker/ib-gateway/docker-compose.yml)
does not configure that driver. Production runs an installed root-owned compose
artifact, so a source merge alone does not prove its logging configuration changed.
**Blast radius:** the following diagnosis reads logs and does not change the
Gateway, authentication, holds or leases.
**Diagnosis:** locally, use `scripts/docker_ib_gateway.sh logs`. On the broker,
`sudo -n /usr/local/sbin/radon-docker-gw logs` reads the live container's bounded
recent output. For retained production output, as root use
`journalctl CONTAINER_TAG=ib-gateway --since "<UTC time>"` with the incident's
time. Retention is bounded by the host journal; it is not an off-host backup.
**Stop:** an empty result is not proof of a healthy login or no prior failure.
Do not recreate the container or bypass a hold to obtain diagnostic output.
**Verification:** match the host and incident time to the recovered entries;
use [readiness verification](#readiness-verification) to establish authentication.
**Rollback:** read-only diagnosis needs no rollback. Installed compose changes
belong to the [cloud control-plane owner](../cloud/CLAUDE.md#runtime-planes).
**Escalation:** retain sanitized timestamps and missing-log evidence for the
operator when the installed configuration or journal retention cannot be confirmed.

## Three Gates

### 1. Cross-process push lock

`scripts/utils/ib_2fa_lock.py` reads/writes `/var/lib/radon/ib-lease/ib-2fa-push-lock.json`. 10-min TTL.

Every restart path that fires a push acquires the lock first. While held, restart requests REJECTED with `reason="2fa_push_in_flight"`.

Required participants:
- Local Docker recovery in `scripts/api/ib_gateway.py` and `scripts/docker_ib_gateway.sh`
- The production `/usr/local/bin/radon-ib-gateway-control` helper installed from `cloud/`
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

### 6. Watchdog login-throttle hold (2026-09-26)

**Symptom:** you force a Gateway restart and no IBKR Mobile push arrives. `/health` shows `auth_state=unreachable`, `port_listening=true`, `upstream_dead=true`. The Gateway log (`sudo -n /usr/local/sbin/radon-docker-gw logs` on the broker) ends with `IBC: Too many failed login attempts. Please wait N seconds before attempting to re-login again.` IBKR refused the login before the 2FA step, so no push was ever sent. IBC never retries from that dialog, and the API port stays open with no handshake, which reads as `wedged`.

**Why restarting makes it worse:** every restart is one more login attempt, and IBKR counts each one. On 2026-09-26, the api-hang ladder (Gate 4) restarted three times and the operator forced three more within an hour, and every login was throttled.

**Behaviour:** when the probe is `wedged`/`dead`, `auth_state` is neither `authenticated` nor `awaiting_2fa`, and the latest `Login attempt:` in the container's last 5 minutes of log is followed by the throttle line, `_handle_login_throttled` (`scripts/ib_watchdog.py`) takes over:
- It never touches the api-hang counter or cap, and it holds all restarts.
- It records the error in `service_health` once per episode with the time of its retry: "do not restart the Gateway".
- After 15 minutes with no new throttled attempt (doubling to a 60-minute cap), it makes **one** fresh login under the push lock, so you get a push to approve.
- Any new throttled attempt, including your own, restarts the quiet period.
- Outside market-data hours it never retries. The app path still refuses `start` and `restart` until the quiet period below. After the UTC time that refusal names, restart once from the app admin control and approve the push.
- The episode lives in the watchdog state file, so it survives the 5-minute log window. It ends on `authenticated`, on `awaiting_2fa`, or on a login that got past the throttle.

**Operator action while that state file has `login_throttle_since` set.** Prerequisite: split topology, watchdog state at `/var/lib/radon/ib-watchdog-state.json`. Blast radius: one login attempt is another IBKR failed login and restarts the quiet period. It does not send an order. Safe diagnosis: read the `ib-watchdog` `service_health` error and, from the app, the admin Start or Restart error. Do not run `docker compose`, `docker restart`, or broker-local `radon restart` to probe it.

The app admin Start/Restart path is the broker daemon (`scripts/ib_gateway_remote/serve.py`). `start` and `restart` return HTTP 409 until `login_throttle_since` plus the quiet period. That period is 900 seconds, doubled for each `login_throttle_retries` already spent, capped at 3600 seconds (`scripts/utils/ib_login_throttle.py`). The `detail` names the UTC time a login is allowed. A 409 did not log in. `stop` and `reset-lease` are not gated.

During market-data hours the watchdog makes the one retry. Do not restart it yourself. Outside those hours the watchdog does not retry, so after the UTC time in the 409, restart once from the app admin control and approve the push. The off-hours `service_health` sentence still says a flat "15+ minutes" (`scripts/ib_watchdog.py`). Follow the 409 time when they differ.

Stop when another 409 comes back, or the Gateway log still ends in `Too many failed login attempts`. Verification: after the allowed login, `/health` moves to `awaiting_2fa` or `authenticated` and the watchdog clears `login_throttle_since`. Rollback: nothing to undo. An early login restarts the quiet period; wait for the new time. Escalation: one login after the 409 time, then stop.

Broker-local `radon restart` and a broker-host `POST /ib/restart` call `/usr/local/bin/radon-ib-gateway-control` directly (`cloud/scripts/operator-radon.sh` `gateway_control`; `scripts/api/services.py` `_control_gateway` when the host role is not `app`). They do not read the throttle file.

Tests: `scripts/tests/test_ib_watchdog_login_throttle.py`, `scripts/tests/test_ib_gateway_remote_login_throttle.py`.

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

**IBKR operator hold (log in to IBKR yourself).** The Gateway shares the operator's IBKR username, and IBKR allows one session per username (2026-09-25: with `ExistingSessionDetectedAction=primary` the Gateway kicked every web login). The hold keeps the Gateway logged out, and every automatic login path stands down until someone clears it. It lives on the broker (`/var/lib/radon/ib-operator-hold.json`), so it works with the app host down.

Set and clear it from any of these:

- Broker, as root: `radon ib release [--reason TEXT] [--expires-at ISO]` writes the hold, then stops the Gateway: helper `stop`, falling back to the root shim's `compose-down`, then `kill`, never waiting on the deploy lock. It prints `RELEASED` only once the container is gone. `radon ib resume` clears it, makes sure `radon-ib-watchdog.timer` runs, and starts the Gateway once. `radon ib status` prints the hold, the Gateway and the timer. Console and sudo sessions without SSH metadata retain the operator name with a local origin in the hold audit (REL-301 / R-720).
- Release confirmation requires Docker to report the container stopped or explicitly absent. A timeout, permission failure or malformed inspection leaves the hold set and returns failure; it never prints `RELEASED` (REL-298 / R-717).
- Admin panel (app up): Gateway controls, IBKR operator hold card. Hold needs a reason and a typed `HOLD`. Resume confirms once. This is `POST /api/admin/ib/operator-hold` to FastAPI `POST /ib/operator-hold` (operator JWT), then the broker daemon's mTLS `POST /hold` or `/unhold`. `/hold` writes the hold, then runs the helper `stop`. `/unhold` removes the hold, then runs one `start`, unless the 60s verb cooldown or an IBKR login throttle refuses it. In that case the hold is still cleared and the watchdog takes over.
- Automatic: with `ExistingSessionDetectedAction=primaryoverride` (pinned in `cloud/docker-compose.yml`), IBC hands the session to your login and exits. The Gateway log shows `Other session may be primary, so end this session ... (scenario 6)`, or `scenario 4` when a fresh Gateway login loses to your session. When that is the latest login event and the Gateway is down, the watchdog sets the hold with actor `auto:ib-watchdog` instead of restarting it.

While held:

| Path | Behaviour |
|---|---|
| Root shim `compose-up`, helper `start\|restart\|restart-preheld` | exit 73 (`radon-ib-gateway.service` treats 73 as success) |
| `radon-ib-watchdog` (timer keeps running) | outcome `operator_hold`, no probe, no restart, no lease. `service_health[ib-watchdog]` is `ok` and names who, why and when. Pushover sends ONE normal-priority `IB Gateway HELD` page per hold. Clearing sends one `hold cleared` page |
| Broker daemon `start\|restart` | `423 OPERATOR_HOLD` with the hold in the body |
| FastAPI `/ib/restart`, admin Gateway `start\|restart` | `423`, and no request leaves the app host. `stop` still works |
| FastAPI 15s recovery heartbeat (pool reconnect, radon-api self-restart ladder) | skipped |
| Relay stale-tick ladder | no `disconnected` error row, no reconnect ladder, no `/ib/restart` escalation. It writes an `ok` row with `reason: operator_hold` |
| Watchdog IB-outage grouping | IB-dependent failures are absorbed without a page. No `radon restart` advice |
| IBC 2FA relogin | already off (`TWOFA_TIMEOUT_ACTION=exit`, `RELOGIN_AFTER_TWOFA_TIMEOUT=no`), so no push spam. The daily `AUTO_RESTART_TIME=11:45 PM` token restart only runs while the Gateway is up, which a hold rules out |

Any flag that is unreadable, malformed, symlinked or not root-owned counts as held, and its reason and actor still show. Invalid UTF-8 in diagnostic fields is replaced for display without changing the canonical clear-prefix decision or rewriting the flag (REL-299 / R-718). Holds set by the admin panel and the watchdog are written by `radon`. A non-root clear removes the flag, because absent means not held. An `--expires-at` that has passed reads `expired` but stays held. An expiring hold would log back in and kick you. From the laptop when broker Tailscale SSH is down: `ssh -J radon@5.78.148.38 root@10.0.0.4 radon ib release`. Design: [`ibkr-session-release.md`](ibkr-session-release.md).

### Runbook: flatten from IBKR Mobile while the app is down

**Symptom:** the app host is down and you need to manage orders through IBKR Mobile or the web portal.

**Prerequisites:** broker root access and your IBKR login. If the broker is unreachable, the fallback below depends on its installed `primaryoverride` Compose body; a repository setting alone does not prove rollout.

**Blast radius:** Radon has no IB data and cannot place or manage orders until you resume. Release does not set a trading halt or stop local Gateways. Resting orders at IBKR stay live, and you manage them yourself.

**Diagnosis:** `radon ib status` reports the hold and Gateway state without changing them. A hold alone does not prove the Gateway stopped.

**Stop:** if release fails, reports `HOLD NOT WRITTEN`, or reports `state unknown`, do not treat logout as confirmed and do not clear an existing hold to retry. Follow escalation below.

1. If you can reach the broker, run `radon ib release --reason "flatten from mobile"` as root first (`ssh root@radon-broker`, or the jump host line above). Require exit status 0 and `RELEASED`, then confirm `radon ib status` shows `"held": true` and the Gateway stopped or missing. Only after confirmation, wait about 30 seconds before logging in to IBKR.
2. If you cannot reach it, log in to IBKR Mobile or the web portal anyway. Once the broker runs the `primaryoverride` compose, the Gateway yields, and within about a minute the watchdog sets the hold (`auto:ib-watchdog`) and pages `IB Gateway HELD` once. Before that rollout, IBC takes the session back and kicks you; use step 1.
3. Flatten. Nothing on the broker logs the Gateway back in while held. An `IB Gateway HELD` page is expected. Do not run `radon restart`, Start Gateway or `docker compose` to "fix" it.
4. When done, log out of IBKR Mobile and the web portal. With `primaryoverride` a fresh Gateway login still takes the session and would end yours.
5. Clear the hold: `radon ib resume` on the broker, or Resume Gateway in /admin once the app is back. Approve the one IBKR Mobile push. The watchdog pages `hold cleared`.
6. **Verification:** `radon ib status` shows `"held": false` and `gateway: running`, `/health` reaches `auth_state=authenticated`, and the pool self-heal reconnects within 15 to 60 seconds.

**Rollback:** after logging out of IBKR Mobile and the web portal, use `radon ib resume` and the verification above.

**Escalation:** if shutdown cannot be confirmed or the hold cannot be written, escalate to the operator with broker console access. Emergency broker power-off ends that Gateway session. Verify the durable hold before reboot; a failed hold write does not protect the next boot.

---

## Upgrading the Gateway image

**Symptom:** a Gateway image change fails login, or a rollback would put an unsupported build back on the broker.
**Prerequisites:** the digest comes from the image line in [production compose](../cloud/docker-compose.yml) and [local compose](../docker/ib-gateway/docker-compose.yml). Do not copy that digest into this page. Both comments say the same support floor: drops support for builds below 10.50.1 on 2026-12-15.
**Blast radius:** one lease-held restart is one 2FA push and drops every IB client until login completes. A digest bump that skips the volume copy below fails login because IBC looks for a version directory the old volume hides.
**Diagnosis:** read `IB_GATEWAY_VERSION` from the new image. Confirm `/seed/ibgateway/$VER` is missing before the copy. Confirm the installed body at `/etc/radon/ib-gateway-compose.yml` is the one `radon-docker-gw config-check` renders.
**Stop:** stop if a hold, an unreadable lease, or a login throttle is active. Do not restart during the cash session. Do not roll back onto a build the compose comment says IBKR has dropped.
**Verification:** after the restart, [readiness verification](#readiness-verification) reaches `auth_state=authenticated`. The running container's image digest matches the compose image line.
**Rollback:** the previous compose body and one more lease-held restart, only while that body is still on a supported build. The old image stays local because disk cleanup never prunes `ghcr.io/gnzsnz/ib-gateway`.
**Escalation:** if the version directory cannot be copied or login does not reach authenticated, leave the previous supported body installed and escalate with the digest and the sanitized login error.

The `cloud_ib-config` named volume mounts over `/home/ibgateway/Jts`, the same directory the image installs the Gateway into (`Jts/ibgateway/<version>`). Docker seeds a named volume from the image only when the volume is created, so the live volume holds only the version it was born with. A digest bump alone starts IBC against a version directory the volume hides, and the login fails.

Before the cutover restart, copy the new version directory into the volume from the new image. The copy is additive and leaves the running version's directory alone:

```bash
NEW=sha256:<new index digest>
docker pull "ghcr.io/gnzsnz/ib-gateway@$NEW"
VER=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "ghcr.io/gnzsnz/ib-gateway@$NEW" | sed -n 's/^IB_GATEWAY_VERSION=//p')
docker run --rm --network none --entrypoint /bin/bash -v cloud_ib-config:/seed \
  "ghcr.io/gnzsnz/ib-gateway@$NEW" \
  -c "test -e /seed/ibgateway/$VER || cp -a /home/ibgateway/Jts/ibgateway/$VER /seed/ibgateway/"
```

Then install the new compose body at `/etc/radon/ib-gateway-compose.yml` by hand (the broker gets no control-plane install from CI), confirm `radon-docker-gw config-check`, and run one lease-held `radon-ib-gateway-control restart` outside market hours. That is one 2FA push.


---

## What NOT to Do

- **Do not keep forcing restarts when no push arrives.** Check the Gateway log for `Too many failed login attempts` first. Each login is another failed attempt. Wait for the UTC time the app-path 409 names (Gate 6), then restart once from that path. Do not use broker-local `radon restart` during the quiet period.

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
- [`cloud/scripts/ib-gateway-control.sh`](../cloud/scripts/ib-gateway-control.sh)
- [`cloud/services/radon-ib-gateway-preheld-restart.service`](../cloud/services/radon-ib-gateway-preheld-restart.service)
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
