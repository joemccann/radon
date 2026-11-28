# IB Gateway Connection Troubleshooting

Runbook for IB connection failures seen from FastAPI on radon-app against the
broker Gateway (10.0.0.4:4001); production recovery is owned by
[Gateway recovery](ib-gateway-recovery.md).

**Prerequisites and blast radius:** the Gateway runs on the broker; diagnose
from radon-app. Read-only process, socket and log checks do not place orders; a
start, restart or process kill can interrupt all IB clients and request 2FA.
**Safe diagnosis, stop conditions and verification:** follow
[readiness verification](ib-gateway-recovery.md#readiness-verification), preserve
operator holds and active leases, and verify authentication rather than just
a listening socket. **Rollback and escalation:** diagnosis changes nothing;
if controlled recovery fails, stop retries and escalate sanitized evidence to
the operator. Do not clear a hold or lease to make a command succeed.

## Quick Diagnostic

Run this first to classify the failure:

```bash
# 1. Broker Gateway status. This is the process check. A local lsof or
# connect to 127.0.0.1:4001 on radon-app does not reach the broker Gateway.
# A refused local port is not Scenario A or B, and it is not a restart.
ssh root@radon-broker radon ib status

# 2. FastAPI health on the app host. Read ib_gateway.auth_state,
# port_listening, upstream_dead, and ib_pool. An open TCP port is not
# authentication. Owner: ib-gateway-recovery.md#readiness-verification
curl -s http://radon-app:8321/health | python3.13 -m json.tool

# 3. Relay bind on the app host (127.0.0.1:8765). This is not the Gateway.
lsof -iTCP:8765 -sTCP:LISTEN
```

## Failure Classification

Match your diagnostic results to one of these scenarios:

### Scenario A: Process not running

**Symptoms:**
- `ssh root@radon-broker radon ib status` shows the Gateway is not running
- Do not classify this from `lsof` or a connect to `127.0.0.1:4001` on radon-app. That port is not the broker Gateway.

**Root cause:** Gateway crashed, the container exited, or the operator hold released it.

**Fix:**
```bash
ssh root@radon-broker radon ib resume
# Wait 15-30s for Java startup
# Approve 2FA on IBKR Mobile when prompted
```

**If start fails repeatedly:** Check the service log for Java errors:
```bash
ssh root@radon-broker sudo -n /usr/local/sbin/radon-docker-gw logs
```

---

### Scenario B: Process running, port listening, connections refused/timeout

**Symptoms:**
- Broker `radon ib status` shows the Gateway running
- `/health` reports `port_listening` true with `upstream_dead` true, or the API handshake times out after TCP accept
- Do not classify this from a connect to `127.0.0.1:4001` on radon-app

**Root cause:** Gateway is in a zombie/CLOSE_WAIT state -- Java process is alive and the socket is bound, but the API layer is not accepting connections. This happens when:
1. **2FA expired** -- IB session timed out, Gateway needs re-authentication
2. **IB server-side disconnect** -- IB's servers terminated the session (maintenance, compliance), leaving CLOSE_WAIT sockets
3. **Session transition failed** -- IB server maintenance or token expiry left Gateway in a bad state
4. **Memory/GC pressure** -- Java process is alive but unresponsive

**This is the most common failure mode.**

**Automated detection and recovery:**
- FastAPI reports container/process, auth, and upstream state without blindly restarting an in-flight 2FA prompt
- `ib_watchdog.py` performs an independent bounded IB protocol handshake on every cycle
- Sustained DEAD/WEDGED verdicts enter the persisted debounce, backoff, hard-cap, and 2FA lease gates
- Health: `GET /health` returns `ib_gateway.upstream_dead: true` when CLOSE_WAIT detected
- WS relay recovery follows the [mode-specific recovery owner](ib-gateway-recovery.md#relay-recovery)

**Manual fix (if auto-recovery fails):**
```bash
# Lease-gated restart (refuses while another push is in flight)
curl -s -X POST http://radon-app:8321/ib/restart

# Wait 30-60s for Java startup + IBC login sequence
# Approve 2FA push notification on IBKR Mobile

# Verify recovery
curl -s http://radon-app:8321/health | python3.13 -m json.tool
# Check: ib_gateway.auth_state=authenticated, ib_gateway.upstream_dead=false
# Also verify the pool roles required by the operation
```

**If restart does not help:** stop further starts and inspect the controlled
restart result and logs. Process cleanup belongs to the secure IBC wrapper;
do not use broad process-name kills. Preserve holds and leases and escalate
the failed recovery to the operator.

---

### Scenario C: Gateway connected but scripts fail

**Symptoms:**
- Port 4001 accepts connections (connect test says CONNECTED)
- But `ib_sync.py` or `ib_orders.py` still fails
- Error: `clientId already in use` or `Peer closed connection`

**Root cause:** Client ID collision. Another process holds the client ID that the script needs.

The [client-ID allocation owner](../scripts/CLAUDE.md#client-id-ranges)
and [`IBClient`](../scripts/clients/ib_client.py) define allocation; a static
script-to-ID table cannot identify the connection currently holding a slot.
The sync and order subprocesses use the automatic allocator.

**Safe diagnosis:** inspect the Gateway's active connections and the failing
script's logs without stopping an order-placement client. The
[order-placement contract](../scripts/CLAUDE.md#order-placement-contract-ib_place_orderpy)
requires waiting for an IB `permId` or the bounded indeterminate outcome before
normal disconnect. Killing a placing client early leaves the broker outcome
unknown. Stop cleanup when ownership or order acknowledgement is uncertain.
Verify through the existing refreshed order view before any new submit, and
escalate unresolved collisions with sanitized connection evidence instead of
killing processes by name.

---

### Scenario D: Real-time WebSocket server down

**Symptoms:**
- Web UI shows prices as "---" or stale
- On the app host, `lsof -iTCP:8765` shows nothing listening (`radon-relay.service` binds `127.0.0.1:8765`)

**Root cause:** `radon-relay.service` is down. A browser does not open `ws://localhost:8765`.

**Fix:** follow [relay recovery](ib-gateway-recovery.md#relay-recovery). Do not start a second `node scripts/ib_realtime_server.js`, and do not kill a process by name to free the port. A Gateway restart can request 2FA; stop if a hold or lease is active.

---

### Scenario E: 2FA not approved

**Symptoms:**
- Gateway just restarted
- Logs show IBC detected the Gateway frame
- API connections timeout (Scenario B pattern)
- IBKR Mobile has a pending push notification

**Root cause:** IBC initiated login but 2FA approval is pending on your phone.

**Fix:**
1. Open IBKR Mobile app
2. Approve the 2FA push notification
3. Wait 10-15s for Gateway to complete login
4. Verify with connect test

**If you missed the notification:** do not force another login while the lease
is active. IBC exits and does not retry, because another login would invalidate
the pending token. Check the lease and logs:
```bash
python3.13 scripts/utils/ib_2fa_lock.py check
```
After the lease expires, the watchdog can acquire a fresh lease and make one
bounded recovery attempt. Operator starts/restarts go through `radon ib resume`
/ the admin panel on the broker, never raw Docker or IBC commands.

---

## Timeout Budget

Understanding where time goes when Gateway is unreachable:

| Layer | Default Timeout | Notes |
|-------|----------------|-------|
| `IBClient.connect()` | **3s** | Default in `scripts/clients/ib_client.py`. Callers may pass a longer timeout. |
| `runScript()` (Node wrapper) | 30s | Python script spawn + execution |
| Portfolio and orders sync wait | 20s | `PRODUCER_SYNC_WAIT_MS` in `web/lib/edgeBudget.ts`, then the latest Turso snapshot. Not a disk JSON read. |

Scripts that need longer timeouts pass them explicitly:
- `ib_place_order.py`: `timeout=10` (order placement must be reliable)
- `evaluate.py`: `timeout=8`

---

## Cached Data Fallback

A failed portfolio or orders sync POST serves the latest Turso snapshot when one exists. `web/lib/syncFallback.ts` sets `X-Sync-Warning`. Portfolio also sets `X-Portfolio-Source: turso-stale`. A wait that elapsed sets `X-Sync-Pending: 1` and `IB sync still running - serving latest Turso snapshot`. A failed sync uses `IB sync failed - serving latest Turso snapshot`. The wait is `PRODUCER_SYNC_WAIT_MS` (20 seconds) in `web/lib/edgeBudget.ts`. These routes do not read `data/*.json`.

No snapshot, or a Turso read error on that POST, is HTTP 502. A corrupt stored portfolio payload is HTTP 500 `SNAPSHOT_CORRUPT`; re-run the portfolio sync. A portfolio GET with no snapshot is HTTP 503. An orders GET that cannot read Turso is HTTP 503. A 200 is the live sync result only when those warning headers are absent. Do not POST `/api/portfolio` as a connectivity probe.

---

## Automated Recovery

These mechanisms handle transient failures without intervention:

| Mechanism | Where | Behavior |
|-----------|-------|----------|
| Direct protocol sensor | `ib_watchdog.py` | Independent bounded IB handshake detects port-down and JVM wedges |
| Persisted recovery ladder | `ib_watchdog.py` | Debounce, backoff, hard cap, auth-aware 2FA stand-down, then lease-gated restart |
| Atomic push lease | `ib_2fa_lock.py` | OS-locked compare/write; all active leases reject reentry, including same holder |
| Process cleanup | Gateway recovery owner | The broker helper owns cleanup. Do not run a local IBC wrapper or kill processes by name. |
| Stale tick recovery | Relay | [Mode-specific escalation and operator gates](ib-gateway-recovery.md#relay-recovery) |
| Reconnect loop | `ib_realtime_server.js` | 5s interval, client ID rotation on collision, subscription restoration |
| `_IBSyncCoordinator` | FastAPI | Coalesces portfolio/orders subprocesses and caches only successful outcomes briefly |
| Cached fallback | portfolio and orders POST | Latest Turso snapshot plus `X-Sync-Warning` when a row exists; 502 when it does not. Not `data/*.json`. |
| IBC daily cycle | Gateway | [Daily-cycle semantics and local installer limitation](ib-gateway-recovery.md#daily-cycle) |
| 2FA timeout | IBC config | Exit with no relogin; watchdog owns the next bounded attempt after lease expiry |

---

## Monitoring Checklist

Add to daily startup routine:

```bash
# Gateway status on the broker. Read-only.
ssh root@radon-broker radon ib status

# App-host FastAPI health. Do not POST /api/portfolio as a probe, and do not
# time a 3 second curl. Sync fallback is the header contract above, after
# PRODUCER_SYNC_WAIT_MS, not a disk file.
curl -s http://radon-app:8321/health | python3.13 -m json.tool
# ib_gateway.auth_state=authenticated and upstream_dead=false before any restart.
```

---

## Architecture Reference

```
                     ┌─────────────────┐
                     │   IBKR Mobile    │
                     │   (2FA approval) │
                     └────────┬────────┘
                              │ push notification
                     ┌────────▼────────┐
                     │   IB Servers    │
                     └────────┬────────┘
                              │ IB API
              ┌───────────────▼───────────────┐
              │     IB Gateway (Java)          │
              │     Port 4001 (Live API)       │
              │  Managed by IBC inside the    │
              │  broker container; lifecycle   │
              └──┬─────────────┬──────────┬───┘
                 │             │          │
        ┌────────▼───┐  ┌─────▼────┐  ┌──▼──────────────┐
        │ ib_sync.py │  │ib_orders │  │ib_realtime_server│
        │ see ranges │  │see ranges │  │ see ranges       │
        └────────┬───┘  └─────┬────┘  └──┬──────────────┘
                 │             │          │ ws://localhost:8765
        ┌────────▼─────────────▼──────────▼───┐
        │          Next.js API Routes          │
        │  POST /api/portfolio (sync+fallback) │
        │  POST /api/orders   (sync+fallback)  │
        │  prices via the relay, not ib-status │
        └─────────────────┬───────────────────┘
                          │ HTTP
                 ┌────────▼────────┐
                 │    Web UI       │
                 │ usePortfolio()  │
                 │ useOrders()     │
                 │ usePrices()     │
                 └─────────────────┘
```

Client IDs are the ranges in [scripts/CLAUDE.md](../scripts/CLAUDE.md#client-id-ranges). Do not select a process from a static ID in this diagram. The relay listens on `127.0.0.1:8765` on the app host. Gateway authentication is FastAPI `/health`, not a Next route.
