# IB Gateway Connection Troubleshooting

Runbook for diagnosing and resolving IB Gateway connection failures in Radon.

## Quick Diagnostic

Run this first to classify the failure:

```bash
# 1. Is the Gateway process running?
~/ibc/bin/status-secure-ibc-service.sh

# 2. Is port 4001 listening?
lsof -iTCP:4001 -sTCP:LISTEN

# 3. Is the port accepting connections?
python3.13 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
start = time.time()
try:
    s.connect(('127.0.0.1', 4001))
    print(f'CONNECTED in {time.time()-start:.1f}s')
except Exception as e:
    print(f'FAILED in {time.time()-start:.1f}s: {e}')
finally:
    s.close()
"

# 4. Is the realtime WS server running?
lsof -iTCP:8765 -sTCP:LISTEN

# 5. What does the web UI report?
curl -s http://localhost:3000/api/ib-status | python3.13 -m json.tool
```

## Failure Classification

Match your diagnostic results to one of these scenarios:

### Scenario A: Process not running

**Symptoms:**
- `status-secure-ibc-service.sh` shows no PID or "could not find service"
- Port 4001 not listening

**Root cause:** Gateway crashed, launchd didn't restart, or service was manually stopped.

**Fix:**
```bash
scripts/ibc_remote_control.sh ibc-start
# Wait 15-30s for Java startup
# Approve 2FA on IBKR Mobile when prompted
```

**If start fails repeatedly:** Check the service log for Java errors:
```bash
tail -50 ~/ibc/logs/ibc-gateway-service.log
```

---

### Scenario B: Process running, port listening, connections refused/timeout

**Symptoms:**
- `status-secure-ibc-service.sh` shows running with PID
- `lsof` shows Java listening on port 4001
- `python3` connect test times out or returns ECONNREFUSED
- Web logs: `TimeoutError(60, "Connect call failed ('127.0.0.1', 4001)")`

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
- WS relay: stale tick recovery requests the lock-aware FastAPI restart endpoint

**Manual fix (if auto-recovery fails):**
```bash
# Lease-gated restart (refuses while another push is in flight)
scripts/ibc_remote_control.sh ibc-restart

# Wait 30-60s for Java startup + IBC login sequence
# Approve 2FA push notification on IBKR Mobile

# Verify recovery
curl -s http://localhost:8321/health | python3.13 -m json.tool
# Check: ib_gateway.port_listening=true, ib_gateway.upstream_dead=false
```

**If restart doesn't help:** The restart script already kills lingering processes, but if needed:
```bash
~/ibc/bin/stop-secure-ibc-service.sh
sleep 5
# Verify no Java process lingering
pgrep -f 'ibgateway|IBC|ibcontroller'
# If still running:
# pkill -9 -f 'ibgateway|IBC|ibcontroller'
scripts/ibc_remote_control.sh ibc-start
```

---

### Scenario C: Gateway connected but scripts fail

**Symptoms:**
- Port 4001 accepts connections (connect test says CONNECTED)
- But `ib_sync.py` or `ib_orders.py` still fails
- Error: `clientId already in use` or `Peer closed connection`

**Root cause:** Client ID collision. Another process holds the client ID that the script needs.

**Client ID registry** (from `scripts/clients/ib_client.py`):

| Script | Client ID | Purpose |
|--------|-----------|---------|
| `ib_sync` | 0 | Portfolio sync |
| `ib_orders` | 11 | Orders sync |
| `ib_place_order` | 26 | Order placement |
| `ib_realtime_server` | 100-102 | Real-time prices (rotates) |
| `ib_reconcile` | 8 | Fill reconciliation |
| `exit_order_service` | 20 | Exit order automation |
| `blotter_service` | 25 | Trade blotter |

**Fix:**
```bash
# Check which client IDs are connected in IB Gateway GUI
# (Configuration > API > Active Connections)

# If a stale script holds the ID, find and kill it:
ps aux | grep ib_sync | grep -v grep
ps aux | grep ib_orders | grep -v grep

# The orphaned connections will auto-disconnect after the script exits
```

---

### Scenario D: Real-time WebSocket server down

**Symptoms:**
- Web UI shows prices as "---" or stale
- `lsof -iTCP:8765` shows nothing listening
- Web logs: `WebSocket connection to 'ws://localhost:8765' failed`

**Root cause:** `ib_realtime_server.js` crashed or wasn't started.

**Fix:**
```bash
# The dev server auto-starts it. If running standalone:
node scripts/ib_realtime_server.js

# If port conflict:
lsof -iTCP:8765 -sTCP:LISTEN
# Kill the conflicting process, then restart
```

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
tail -20 ~/ibc/logs/ibc-gateway-service.log
```
After the lease expires, the watchdog can acquire a fresh lease and make one
bounded recovery attempt. Operator starts/restarts must use
`scripts/docker_ib_gateway.sh` or `scripts/ibc_remote_control.sh`, never raw
Docker/IBC commands.

---

## Timeout Budget

Understanding where time goes when Gateway is unreachable:

| Layer | Default Timeout | Notes |
|-------|----------------|-------|
| `IBClient.connect()` | **3s** | `ib_insync` TCP + handshake timeout |
| `runScript()` (Node wrapper) | 30s | Python script spawn + execution |
| `syncMutex` (API route) | Coalesced | Multiple requests share one in-flight sync |
| Cached fallback read | <50ms | Read JSON from disk |
| **Total worst case (unreachable)** | **~3.5s** | Sync fails at 3s, fallback serves cached data |

Scripts that need longer timeouts pass them explicitly:
- `ib_place_order.py`: `timeout=10` (order placement must be reliable)
- `portfolio_performance.py`: `timeout=5`
- `evaluate.py`: `timeout=8`

---

## Cached Data Fallback

When sync fails, API routes serve cached data instead of returning 502:

| Route | Fallback File | Signal |
|-------|--------------|--------|
| `POST /api/portfolio` | `data/portfolio.json` | `X-Sync-Warning` header |
| `POST /api/orders` | `data/orders.json` | `X-Sync-Warning` header |

**502 only occurs when both sync AND cache file are missing/empty.** This is by design -- the UI should always render something if data has ever been synced.

---

## Automated Recovery

These mechanisms handle transient failures without intervention:

| Mechanism | Where | Behavior |
|-----------|-------|----------|
| Direct protocol sensor | `ib_watchdog.py` | Independent bounded IB handshake detects port-down and JVM wedges |
| Persisted recovery ladder | `ib_watchdog.py` | Debounce, backoff, hard cap, auth-aware 2FA stand-down, then lease-gated restart |
| Atomic push lease | `ib_2fa_lock.py` | OS-locked compare/write; all active leases reject reentry, including same holder |
| Process cleanup | `restart-secure-ibc-service.sh` | Snapshots pre-existing PIDs, force-kills only survivors after SIGTERM |
| `_on_disconnect()` | `IBClient` (Python) | 5 attempts, exponential backoff (2^n, cap 30s), restores subscriptions |
| Stale tick detection | `ib_realtime_server.js` | No ticks for 45s during market hours → restart Gateway (120s cooldown) |
| Reconnect loop | `ib_realtime_server.js` | 5s interval, client ID rotation on collision, subscription restoration |
| `_IBSyncCoordinator` | FastAPI | Coalesces portfolio/orders subprocesses and caches only successful outcomes briefly |
| Cached fallback | API routes | Serves `data/*.json` when sync fails, returns 200 not 502 |
| IBC schedules | launchd + IBC | Disabled: no RunAtLoad/calendar, AutoRestartTime, or ColdRestartTime |
| 2FA timeout | IBC config | Exit with no relogin; watchdog owns the next bounded attempt after lease expiry |

---

## Monitoring Checklist

Add to daily startup routine:

```bash
# Gateway health
~/ibc/bin/status-secure-ibc-service.sh | grep -E "state|pid"

# API connectivity
curl -s -o /dev/null -w "%{http_code} %{time_total}s" -X POST http://localhost:3000/api/portfolio
# Expected: "200 0.XXXs" (sub-second when Gateway connected)
# Degraded: "200 3.XXXs" (sync failed, serving cache)
# Broken:   "502 3.XXXs" (no cache available)

# WebSocket server
curl -s http://localhost:3000/api/ib-status | python3.13 -m json.tool
# Expected: {"connected": true}
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
                              │ FIX protocol
              ┌───────────────▼───────────────┐
              │     IB Gateway (Java)          │
              │     Port 4001 (Live API)       │
              │     Managed by IBC + launchd   │
              │     PID tracked by launchd     │
              └──┬─────────────┬──────────┬───┘
                 │             │          │
        ┌────────▼───┐  ┌─────▼────┐  ┌──▼──────────────┐
        │ ib_sync.py │  │ib_orders │  │ib_realtime_server│
        │ clientId=0 │  │clientId=11│  │ clientId=100-102 │
        └────────┬───┘  └─────┬────┘  └──┬──────────────┘
                 │             │          │ ws://localhost:8765
        ┌────────▼─────────────▼──────────▼───┐
        │          Next.js API Routes          │
        │  POST /api/portfolio (sync+fallback) │
        │  POST /api/orders   (sync+fallback)  │
        │  GET  /api/ib-status (WS relay)      │
        └─────────────────┬───────────────────┘
                          │ HTTP
                 ┌────────▼────────┐
                 │    Web UI       │
                 │ usePortfolio()  │
                 │ useOrders()     │
                 │ usePrices()     │
                 └─────────────────┘
```
