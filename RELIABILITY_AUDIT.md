# RELIABILITY_AUDIT.md — radon reliability audit (frozen contract)

Audited: 2026-08-08/09, HEAD `8eeee9b6` (main, includes the merged `test-audit-remediation` wave T-001…T-054).
Method: six parallel code-walk audits (connectivity/sessions, state/persistence, resource exhaustion, error handling, safety mechanisms, observability) + system map. Every claim was verified in code, not inferred from names. Category reports: session scratchpad `audit-*.md`.

---

## 1. Executive summary

Radon's *manual* order perimeter is genuinely disciplined — permId-confirmed placement, no-auto-retry of non-idempotent scripts, poll-confirmed cancels, idempotent journal writes keyed on IB execIds — but the *autonomous* exit-order handler skipped every one of those disciplines: it places live GTC orders with a 1-second sleep instead of an ack, marks the journal PLACED for orders IB may have silently discarded, and its only duplicate-placement guard is an in-memory dict that evaporates on every deploy. There is no kill switch, no cancel-all, and no server-side risk limit anywhere: every gate that matters for money is client-side display, and at least five code paths reach IB `placeOrder` with nothing but `quantity > 0` between them and the margin engine. Position reconciliation cannot detect same-symbol quantity drift, runs only when a browser loads `/journal`, and alerts no one when it finds a problem. The single scenario most likely to cause real damage: **a Turso blip during an exit-order placement followed by a routine deploy restart — the daemon re-places the same live SELL every 5-minute cycle until the DB recovers, and nothing pages**. Blast-radius profile: a fat-finger or runaway loop is bounded only by IB's margin engine; a silent exit-order drop is bounded only by the operator noticing a missing order in TWS.

## 2. System map

*(assembled from the A1 map agent + category walks; every claim cited)*

### 2.1 External dependencies

**IBKR client-ID ledger** (`scripts/clients/ib_client.py:73-113`): FastAPI IBPool sync=3/orders=4/data=5 (per-role asyncio.Lock, staggered 1s connects, 3 attempts — `scripts/api/ib_pool.py:108-162`); WS relay rotates from clientId 10 (`ib_realtime_server.js:104,230-233`); subprocess scripts + daemon handlers auto-allocate 20–49 (`ib_client.py:294-301`); `ib_reconcile.py` fixed 21; scanners 50–69; CLI 90–99. Steady state: 3 pool + 1 relay + transient sessions against one Gateway login at `127.0.0.1:4001`.

**IB Gateway**: Docker on Hetzner; `IB_GATEWAY_MODE=cloud` makes FastAPI observational-only (TCP probe, never starts — `ib_gateway.py:56, 237, 720-734`); lifecycle owned by `radon-ib-gateway-control` under deploy lock + 2FA lease (`cloud/scripts/ib-gateway-control.sh:393-467`); restarts guarded by the 2FA push lock + backoff ladder with auth-state tracking `{authenticated, awaiting_2fa, unreachable}` (`ib_gateway.py:105-115, 161-212`).

**Market data**: IB L1 via relay (L2 behind `RADON_DEPTH_ENABLED`); Unusual Whales with backoff on 429/5xx and `UWRateLimitError` (`uw_client.py:63-86, 178-182`); Yahoo/Cboe/FRED/FINRA fallbacks.

**Turso/libSQL — two transports by design**: bounded Hrana HTTP (4.0s socket timeout, `hrana_http.py:36`) for journal/portfolio/service_health writes (`writer.py:30-41`); unbounded native `libsql_experimental` for everything else — cannot be timeout-wrapped, holds the GIL while blocked (`client.py:18-24`). FastAPI must never import `db.client` (lint-enforced, `client.py:58-60`). Replica retired; `RADON_DB_NO_REPLICA=1` set fleet-wide by drop-in (`cloud/services/radon-.service.d/common.conf:11-12`). Test-pollution guards on both transports.

**Clerk**: Next.js Edge middleware perimeter; FastAPI `verify_clerk_jwt` 401s on invalid, trusted-loopback bypass via `is_trusted_local_request`, hard RuntimeError if JWKS env unset — fails closed (`auth.py:55, 101-110, 137-163`).

**systemd/clocks/locks**: ~35 units + 25 timers in `cloud/services/`; only hard dep is `Requires=docker.service` on the Gateway; market hours via `ZoneInfo("America/New_York")` with a fail-open fixed UTC-5 fallback (`daemon.py:87-95`); 2FA push lock, deploy lock + lease, fsynced deploy transition journal (`deploy.sh:564-586`).

### 2.2 Persistent state stores + invariants

| Store | Key/invariant | Cite |
|---|---|---|
| `journal` | `trade_id` PK (= IB execId for live ingest), idempotent upsert; canonical trade store for `/journal`, `/orders`, `/blotter` | `0001_init.sql:70-76`; `writer.py:46-53` |
| `open_orders` | `perm_id` PK; no-empty-window replace (upsert-first, delete-last, T-024) | `0004:16-20`; `writer.py:1100-1141` |
| `executed_orders` | `exec_id` PK, append-in-effect | `0004:22-34` |
| `position_execution_facts` | `(account_id, exec_id, revision)` PK; immutable — hash conflict raises, never overwrites | `0037:5-21`; `writer.py:984-988` |
| `portfolio_snapshots` | `taken_at` PK; archive-coordinated deletion only | `writer.py:361-464` |
| `cash_flows` | IB transactionID PK — idempotent Flex re-pull | `0002:13-25` |
| `service_health` / `_events` | `service` PK latest-state; transitions-only event trigger | `0001:103-110`; `0011:27-49` |
| `daemon_state` (Turso) | flattened `last_run/last_status` only — **lossier than the disk file** (no `known_orders`) | `0004:38-44`; `daemon.py:245-253` |
| `data/daemon_state.json` | per-handler `last_run` + `fill_monitor.known_orders` (fill-dedupe set, disk-only); corrupt file → one WARNING, all handlers start blank, no `.corrupt` backup | `daemon.py:169-170, 226, 268-278` |
| `data/*.json` scan caches | dual-write: Turso best-effort + disk unconditional via checksummed `atomic_save` | `scan_mirror.py:83-126`; `atomic_io.py:36-60` |
| `portfolio.json`/`trade_log.json`/`blotter.json`/`watchlist.json` | **no production writer** (lint-enforced demotion) — but `scripts/knowledge/sources/journal.py:16` still ingests the frozen `trade_log.json` | `test_flat_json_source_truth_contract.py:23-34` |

Divergence resolution in web reads is freshest-content-timestamp-wins with degraded-source gating (`dbFirstRead.ts:126-165`); `journal/blotter/portfolio/orders` routes are DB-only, no disk fallback.

### 2.3 Process boundaries + down-behavior

- **Next.js → FastAPI**: `radonFetch` 30s AbortSignal, structured `RadonApiError` unwrap (`radonApi.ts:37-75`). Placement timeout → 504 `UPSTREAM_TIMEOUT_ORDER_INDETERMINATE` with the idempotency key held (`place/route.ts:369-384`). Post-place refresh failure swallowed as non-fatal (`route.ts:390-394`).
- **Browser → relay**: single-use 30s WS tickets minted by FastAPI (`ws_ticket.py:1-30`); client exponential backoff + jitter; relay stale-tick ladder 45s/K=3 → alert + lock-held `/ib/restart` escalation (`staleDataMachine.js:27-41`; `ib_realtime_server.js:636-757`); `WatchdogSec=45` — the only unit with one.
- **FastAPI → Gateway**: health = TCP + pool `managedAccounts()` (connected-but-empty = 2FA pending, `ib_pool.py:256-284`); pool down → IB routes 503, UW routes keep working; 15s recovery heartbeat, single-flight, cap-3 then `os._exit(1)` (`server.py:289-339`).
- **monitor_daemon → IB/Turso**: fresh auto-ID connection per handler cycle; IB failure → `result["error"]`, retried next cycle; journal upsert failures swallowed with Flex rehydrate as stated recovery (`fill_monitor.py:375-378`); dual-write catches `BaseException` (`daemon.py:229-261`).
- **Watchdogs**: four timer buckets + hysteresis + acks; `radon-ib-watchdog` is the only non-operator path allowed to cycle the Gateway (lease + preheld unit); `radon-nextjs-db-watchdog` restarts Next.js after 3 wedge cycles; `radon-health` :8330 stdlib-only, three-valued probe states, zero dependency edges.
- **Newsfeed → Turso**: bounded writes + 2 retries + 60s circuit breaker (`writer.js:17-132`).

### 2.4 Order lifecycle state machine (money path)

**Placement**: UI → Next.js `/api/orders/place` (schema validation → demo blockade → idempotency `runIdempotentOrder` — ⚠ in-process Map, wiped on restart/deploy, `orderIdempotency.ts:34-55` → `radonFetch` 30s) → FastAPI `/orders/place` (25s subprocess) → `ib_place_order.py` (fresh client 20–49, orderRef `radon-<uuid>`, confirm-poll to `permId≠0` or terminal, 12s combo/6s single) → disconnect.

States at placement: `PendingSubmit/ApiPending/Unknown` (limbo, poll) · `Submitted/PreSubmitted` (accepted) · `Filled` (immediate) · `Rejected/Cancelled/ApiCancelled/Inactive` (terminal-failed, 1.5s async-error grace) · `permId==0` past deadline → explicit error because disconnecting there makes IB silently drop the order (`ib_place_order.py:406-526`).

Crash exposure at placement: (1) subprocess killed mid-poll → order possibly live, no JSON; Next.js holds the idempotency key 60s (indeterminate); (2) Next.js restart wipes the idempotency Map; (3) silent-rejection race handled (`OrderRejectedError`, key cleared); (4) post-place refresh swallowed → `open_orders` lags ≤5 min.

**Cancel/modify**: subprocess reconnects as the original clientId (master can cancel, not modify); pool path confirm-polls both ops; crash point: modify transmitted but confirm-poll times out → order IS modified while caller sees error (`pool_order_manage.py:173-179`).

**Fills → journal (three overlapping paths)**:
- Path A `orders-sync`: 5-min market-hours loop + on-demand refresh, single-flight, no-empty-window replace + executed_orders + execution facts, heartbeat via `service_cycle` (`server.py:412-460`; `ib_orders.py:252-296`).
- Path B daemon: `fill_monitor` 60s (diffs open orders vs persisted `known_orders`; partial fills → inline journal upsert on synthetic conId+permId key; vanished → COMPLETED); `journal_sync` 300s + 15-min post-close grace (session-scoped `get_fills()`, dedupe on `ib_exec_id`, corrections supersede in place); `exit_orders` 300s (journal-payload scan → GTC SELL placement → row PLACED; T-010 in-memory guard).
- Path C reconciliation: `ib_reconcile` at startup + on `/journal` load (report-only); `journal_reconcile` daily Turso-only gap scan (alert-only); `POST /journal/rehydrate` Flex backfill 365d (manual, the ultimate recovery net).

Fill-side crash points: fills between cycles invisible until next session-scoped `get_fills()`; daemon_state loss blanks `known_orders`; multi-day outage needs manual rehydrate; production journal_sync has **no per-cycle re-attempt of swallowed Turso writes** (`journal_sync.py:154-158` dead-gated by `trade_log_path=None`, `run.py:99-101`); SIGTERM (every deploy) skips `finally: save_state()` (`run.py:243-245` vs `daemon.py:199-201` catching only KeyboardInterrupt).

### 2.5 Startup/shutdown

- **Topology**: no `BindsTo=`; two `PartOf=radon-ib-gateway.service` edges (relay, monitor) — Gateway stop cleanly stops both and `Restart=always` does NOT re-fire on clean stop (DUR-10 cascade-stop trap); `radon-api` is `After=`-only so the control plane survives Gateway stops. `StartLimitIntervalSec=300/Burst=5` fleet-wide; 60s oneshots get Burst=10.
- **Deploy** (`deploy.sh`, 900s bounded, fsynced journal): build staged release before teardown → `wait_for_gateway_ready` (60s, **warn-and-proceed** — the only data-plane gate) → stop timers-then-services (never the Gateway; refuses if a preheld 2FA restart is in flight) → promote + venv rebuild → batch restart core `{nextjs, api, relay, monitor, newsfeed}` → restore snapshot → gate on units + `/health/lite` + :3000/:8765 + 40s restart-counter stability; full `/health` deliberately excluded; fail → journal-driven rollback.
- **FastAPI lifespan**: observational gateway check → backgrounded pool connect (IB routes 503 until connected) → 15s recovery heartbeat → orders-sync loop → one-shot reconcile. Migration `ExecStartPre` accepts rc=124 so a Turso outage can't block startup.
- **monitor_daemon**: registers 11 handlers, loads state (missing = no-op, corrupt = warn + blank), 30s serial loop, no daemon-level per-handler timeout; starts "successfully" with IB and Turso both down.
- **Laptop switches**: `cloud.sh` goes through the control helper; `local.sh:53` bypasses the control plane (raw `docker compose down` over SSH, no lock/lease) and leaves `radon-ib-gateway.service` active so the PartOf cascade never fires.

## 3. Findings table

Severity model: **P0** can lose money or corrupt position/order state · **P1** outage or silent wrong behavior · **P2** operability.

| ID | Sev | Where | Finding |
|---|---|---|---|
| R-001 | P0 | repo-wide (greps: no `reqGlobalCancel`/halt flag; routes `scripts/api/server.py:1381-4627`; admin `web/components/admin/Ib2faControls.tsx:152-193`) | No kill switch and no cancel-all exist. "Stop Gateway" severs the API session while GTC orders keep working at IB, unmonitored. |
| R-002 | P0 | `scripts/api/server.py:2046-2072`; `scripts/ib_place_order.py:204-216`; `web/lib/placeOrderBodySchema.ts:35-36`; `web/lib/order/risk/useOrderRisk.ts:568,681` | Zero server-side risk limits: no max qty, no max notional, no bankroll %, no order-rate cap. All Four-Gates enforcement is client-side display; `okToSubmit` gates only on data having loaded. |
| R-003 | P0 | `scripts/monitor_daemon/handlers/exit_orders.py:326-334, 394`; contract at `scripts/ib_place_order.py:406-490` | Exit orders placed with `sleep(1)` and no permId/ack confirmation; journal marked PLACED, then `finally: disconnect()` — IB silently discards unacknowledged orders on placer disconnect (documented 2026-05-27 MU failure mode). Position left unprotected while journal claims protection. |
| R-004 | P0 | `exit_orders.py:55-60, 348-367`; `handlers/base.py:264-287`; no broker cross-check in `execute()` | Duplicate-placement guard (`_unrecorded_placements`) is memory-only — not persisted via `get_state`/`set_state` (contrast `fill_monitor.py:407-423`). Restart after a journal-write failure re-places the same live order every cycle. A crash *between* place and journal-update arms no guard at all. No IB open-order cross-check before placing. |
| R-005 | P0 | `scripts/ib_reconcile.py:251-302, 309-312`; `scripts/api/server.py:2236-2244`; `web/lib/journalDb.ts:80-92` | Position reconciliation cannot detect quantity drift (`quantity_mismatch` initialized, never populated), runs only on browser load of `/journal`, discards its report (`{"ok": true}`), exits 0 on IB connect failure, has no service_health row, and pages no one on `needs_attention`. |
| R-006 | P1 | `scripts/api/server.py:1743-1756`; `scripts/workflow/nodes.py:97-149` | `/workflow/run` with `confirm_order: true` places one live order per scanner row — no per-run cap, no size validation, no idempotency at this layer. |
| R-007 | P1 | `scripts/api/server.py:3872-3941`; `scripts/api/ib_gateway.py:1189-1279` | Automated gateway restarts (incl. immediately after a failed placement that "may have reached IB") are blind to working orders, in-flight placements, and RTH. Unapproved 2FA push → orders work at IB with fill/journal/exit management all dead. |
| R-008 | P1 | `scripts/api/server.py:3849-3864` | Placement pre-flight checks `port_listening`/`upstream_dead` only; `awaiting_2fa` passes pre-flight, and a relay stale-feed farm-down does not block submission. |
| R-009 | P1 | `scripts/api/server.py:2070-2082, 3786-3788, 3866-3872`; `scripts/api/subprocess.py:215-222`; `scripts/ib_place_order.py:229,362,528-529` | Placement subprocess timeout (25s SIGKILL, possibly post-transmit) and post-transmit exceptions return a generic error with no "outcome indeterminate — check open orders" warning ("Script timed out" matches no classification pattern); `orderRef` is never returned or used to reconcile arrived-vs-never-arrived. |
| R-010 | P1 | `handlers/fill_monitor.py:323-337` vs `handlers/journal_sync.py:836`; `web/lib/blotter/fromJournal.ts:653` | Partial fills are journaled twice under unmergeable keys (`fill-monitor:…` vs IB execId) — doubled qty/cost in blotter and basis math; fill_monitor also prices increments at running `avgFillPrice`. |
| R-011 | P1 | `journal_sync.py:69,75`; `server.py:435`; no rehydrate timer in `cloud/services/` | Fills outside RTH+15min grace (outsideRth GTC, manual TWS, late corrections) reach neither journal_sync (fresh session next day) nor `executed_orders` (orders-sync gated closed) → invisible to journal_reconcile; Flex rehydrate is manual-only. |
| R-012 | P1 | `exit_orders.py:174-221`; `scripts/db/writer.py:46-53`; `web/lib/journalDb.ts:112` | Journal payload updates are read-modify-write wholesale overwrites with no transaction/CAS; concurrent writers (journal_sync corrections, web reconciliation import) can clobber `PLACED` back to `PENDING` → re-place of a live order (compounds R-004). |
| R-013 | P1 | `daemon.py:157, 248`; `scripts/db/writer.py:1145-1166`; `clients/ib_client.py:607-609, 829,847`; `exit_orders.py:62-67`; `journal_sync.py:263`; `cloud/services/radon-monitor.service` | Monitor daemon: no per-handler deadline, no `WatchdogSec`; per-cycle `upsert_daemon_state` uses unbounded native libsql; handlers use unbounded sync IB calls and unpaginated full-table journal reads. One hung call wedges exit-order management alive-but-dead, mid-RTH. |
| R-014 | P1 | `scripts/api/server.py:1084-1099, 1125-1131` (contrast `:4350-4353`) | Index option chain fetch inside `acquire("data")` is the one unwrapped IB await in FastAPI — a wedged gateway holds the data-role lock forever and strands up to 4 executor threads per request. |
| R-015 | P1 | `clients/ib_client.py:196-198, 427-491, 770-780` | `IBClient._on_disconnect` (5-attempt backoff reconnect + resubscribe) is never registered — documented resilience is dead code; tests invoke it directly. `_subscriptions` also never shrinks. |
| R-016 | P1 | `handlers/fill_monitor.py:185-209`; `clients/ib_client.py:710-718` | "Disappeared from open orders" is treated as complete fill: cancels report as fills; a degraded session returning an empty snapshot mass-completes every tracked order and resets fill baselines — partials during the blip never detected. |
| R-017 | P1 | `scripts/api/ib_pool.py:195-203, 294-313` | Pool `is_connected` is TCP-level only; half-open/wedged-listener sessions pass acquire and are handed to callers. |
| R-018 | P1 | `clients/ib_client.py:520-535`; `scripts/ib_sync.py:749-756, 1343-1379` | `reqPositions` failure falls back to a possibly-empty cache and an empty portfolio snapshot is upserted with no zero-collapse guard — flat book propagates to exposure/bankroll/basis. |
| R-019 | P1 | `exit_orders.py:134-137` | `_load_pending_orders` DB failure → log + `return []` → green heartbeat. Turso outage silently disables exit-order placement with the watchdog seeing `ok`. |
| R-020 | P1 | `journal_sync.py:286-303, 154-158`; prod wiring `monitor_daemon/run.py:99` | Journal upsert failures are swallowed (`logger.warning`), `imported` counted anyway, heartbeat stays green; the documented reconcile-retry path is disabled in production (`trade_log_path=None`). |
| R-021 | P1 | `scripts/watchdog/check.py:399-414`; `watchdog/__main__.py:44-47` | Turso outage → `BucketReport(ran=False)` → printed "skipped (off-window)", exit 0, and the units alarm, external-probe check, digest flush, and heartbeat are all skipped — the on-box alerting pipeline goes mute exactly when the DB is down. |
| R-022 | P1 | `scripts/api/server.py:2046-2099, 2145-2220`; `scripts/api/subprocess.py:209-213` | No server-side record or log of successful order submission/ack/cancel/modify — subprocess stdout discarded on rc=0, `orderRef` persisted nowhere; incident reconstruction depends on the order having filled. |
| R-023 | P1 | `scripts/ib_execute.py:332-341, 459-467, 289-293` | CLI fill journaled-write failure → prints, returns False, ignored → `✅ COMPLETE`, exit 0; `next_id` falls back to 1 on read failure (id collision). Backstopped by journal_sync, hence P1. |
| R-024 | P2 | `scripts/api/server.py:2174, 2219` | Cancel/modify routes collapse structured IB error dicts to a string, defeating `coerceRadonErrorDetail`. |
| R-025 | P2 | `scripts/api/server.py:2083-2098` | Rejected manual orders reach journald + the (possibly departed) browser only — no digest/page entry. |
| R-026 | P2 | `scripts/watchdog/services.py:144-148, 273-277` | No dead-man on the watchdog itself; all four timers dead → nothing fires. |
| R-027 | P2 | repo-wide; `scripts/host_metrics_sampler.py` | No counters/rates (orders, fills, journal upserts, DB error rates, Pushover deliveries); slow bleeds invisible until they become outages. |
| R-028 | P2 | `scripts/api/server.py:1381-1483` | `/health` carries no feed freshness or Turso reachability; `ok` while the relay is farm-down and Turso 502s. |
| R-029 | P2 | `exit_orders.py:204,215-216`; `writer.py:73-74`; `fill_monitor.py:337`; `journal_sync.py:802`; `0001_init.sql:9-10` | Mixed timestamp formats (naive local, UTC-Z, date-only, +00:00) break lexicographic-chronological ordering under `ORDER BY COALESCE(filled_at, written_at)`. |
| R-030 | P2 | `handlers/base.py:133`; `daemon.py:55-76, 136-139` | Wall-clock handler scheduling (NTP step stalls all handlers); main market-hours gate ignores the holiday/early-close calendar SoT. |
| R-031 | P2 | `scripts/utils/atomic_io.py:47-51` | `atomic_save` never fsyncs; power loss can persist a renamed-but-empty file (checksummed, so detected not silent). |
| R-032 | P2 | `scripts/db/hrana_http.py:36, 141-160` | Hrana 4s timeout is commit-ambiguous with no transport retry; safety currently rests on every caller being idempotent by convention. |
| R-033 | P2 | `scripts/utils/price_cache.py:106`; sole caller `scripts/portfolio_performance.py:1228` | "Auto-pruned at 500" only true when the shelved performance rebuild runs; every other writer grows the cache unboundedly. |
| R-034 | P2 | `scripts/ib_realtime_server.js:527-558` | Relay `optionCloseCache` grows forever (expired expiries never evicted), in memory and on disk. |
| R-035 | P2 | `scripts/lib/rate-limiter.js:27`; `ib_realtime_server.js:1786-1840` | Relay rate-limiter queue unbounded; cancelled snapshot requests still execute queued IB calls. |
| R-036 | P2 | `ib_realtime_server.js:2133-2221` | No per-client subscription cap — one authed client can exhaust the shared IB market-data line budget. |
| R-037 | P2 | `scripts/api/server.py:606` | `RADON_AUTH_DISABLED=1` opens all routes incl. `/orders/place`; not refused when `RADON_MODE=hetzner`. |
| R-038 | P2 | `scripts/exit_order_service.py:398, 530` | Legacy second exit-order placer still on disk with its own daemon mode; if ever started beside the monitor daemon, both place for overlapping exits. |
| R-039 | P2 | `scripts/api/pool_order_manage.py:1-6, 34-46`; `ib_client.py:89-93` | Dead code with false premise (claims clientId=0 master; pool has none) — silent 10147/103 failures if ever wired. |
| R-040 | P2 | `ib_realtime_server.js:1547-1571, 2561-2576` | Depth desync detected but never healed; rejected ops still `markTick()` so the staleness ladder never fires for a frozen book. |
| R-041 | P2 | `journal_sync.py:10-12` vs `:102-115` | Module docstring describes a long-lived connection model the code doesn't use — invites unsafe interval widening. |
| R-042 | P2 | `handlers/fill_monitor.py:141-176` | Fast 0→full fills between polls produce completion notices with no price and no journal mirror (by design; rationale comment overstates coverage). |
| R-043 | P1 | `web/lib/orderIdempotency.ts:34-55` | Order idempotency store is an in-process Map — wiped on every Next.js restart/deploy, exactly the windows in which client retries happen. A double-click straddling a deploy places twice. |
| R-044 | P2 | `scripts/local.sh:53` | Laptop mode-switch stops the cloud Gateway via raw `docker compose down` over SSH — bypasses the deploy lock/2FA lease/transition journal, and leaves `radon-ib-gateway.service` active (`RemainAfterExit=yes`) so the PartOf cascade never fires; relay/monitor keep running against a dead data plane. |
| R-045 | P2 | `monitor_daemon/daemon.py:268-278`; `run.py:243-245` | `daemon_state.json` corruption collapses to one WARNING and blank state (no `.corrupt` backup, contrast `journal_sync.py:484-496`); SIGTERM (every deploy) skips `finally: save_state()` — only `KeyboardInterrupt` is caught. |
| R-046 | P2 | `monitor_daemon/daemon.py:87-95` | Market-hours gate fails open to a fixed UTC-5 offset if tzdata is missing — DST-wrong half the year. |
| R-047 | P2 | `cloud/scripts/deploy-root-helper.sh:246-250` vs `radon-health.service:3-7`; `scripts/db/migrations/0026_scan_snapshots.sql:11-16` | Deploy stops `radon-health` despite its isolation contract (edge floor goes dark during deploys); migration 0026 never records itself in `schema_migrations`. |

### Audited, clean (silence ≠ clearance — these were walked and verified)

- **Manual placement path** `ib_place_order.py`: permId confirm-poll, refuses success on `permId==0`, async-error grace-wait, partial-fill surfacing, structured errors (`:406-508`).
- **No auto-retry of placement** after gateway recovery (`server.py:3783, 3924-3938`; pinned by test).
- **Cancel/modify**: poll-confirmed, clientId-scoped, never optimistic (`ib_order_manage.py:103-162, 194-253`).
- **Web order route**: idempotency keys held on indeterminate timeouts (504 `UPSTream_TIMEOUT_ORDER_INDETERMINATE`), dup-submit guards, demo blockade fail-closed (`web/app/api/orders/place/route.ts`).
- **Journal idempotency (primary path)**: PK = execId; correction supersede logic; DB-sourced dedupe means failed upserts retry next cycle (`journal_sync.py:556-635`).
- **executed_orders/open_orders**: PK'd, upsert-then-delete replace, revision+sha256 conflict detection (`writer.py:952-1142`).
- **Relay**: full resubscribe-on-reconnect, generation-guarded clients, bounded stale-tick ladder wired to `service_health`, backpressure with coalescing drops + hard terminate, systemd `Type=notify` `WatchdogSec=45` (`ib_realtime_server.js`, `lib/staleDataMachine.js`, `lib/sendBackpressure.js`).
- **Client-ID hygiene**: non-overlapping ranges, auto-allocation rotation on conflict, relay port-probe against duplicate instances (`ib_client.py:83-110, 291-333`).
- **Backoff ladders**: all bounded and error-classified (IB reconnect, gateway restart 2FA ladder + push lock, pool recovery cap-3 then `os._exit(1)`, Turso `_hrana_with_retry`).
- **Watchdog delivery discipline**: cooldown arms only on confirmed 2xx; P1 emergency retry-until-ack; IB-outage grouping; journal gap SLI ≤15min (`notify.py`, `journal_gap_sli.py`).
- **Half-open gateway detection**: CLOSE_WAIT scan / protocol probe / container health → `upstream_dead` consulted pre/post script runs (`ib_gateway.py:246-311, 375-397, 556-563`).
- **Subprocess hygiene**: every entry point `wait_for`-bounded with kill+reap (`subprocess.py:188, 261, 307`).
- **Bounded web DB layer**: 3s `dbExecute`, bounded undici pool, destroy-storm cooldown (`web/lib/db.ts:92-115`).
- **Log/disk**: journald capped 1G, container logs rotated, no unrotated file logs.
- **Timezones**: ET session-date discipline via zoneinfo (`handlers/base.py:50-63`); `time.monotonic()` for API cooldowns.
- **Test-pollution guards** on both DB transports.

## 4. Top 10 failure scenarios (probability × damage)

1. **Silent exit-order drop.** Gateway sluggish post-2FA; exit_orders places a GTC SELL at `exit_orders.py:326`, sleeps 1s, journal → PLACED, `finally: disconnect()` at `:394` tears down the client while the order is still `PendingSubmit` with `permId==0`; IB discards it. The position runs unprotected; the journal says otherwise; nothing reconciles PLACED-vs-IB. Damage: unbounded downside on the underlying move. (R-003)
2. **Exit-order double-place after deploy.** Turso blips during the journal UPDATE at `exit_orders.py:348`; guard arms in RAM; the nightly deploy restarts `radon-monitor`; row still PENDING → a second identical live SELL next cycle, and another every 5 minutes until the DB recovers. Both fill → oversold position/naked short, Gate 4 disabled. (R-004, compounded by R-012)
3. **Fat-finger with no backstop.** `quantity: 5000` instead of 50 passes the TypeBox schema, the route, and `quantity > 0`; risk UI shows a warning chip that gates nothing. $20M notional reaches IB; only its margin engine can refuse. No kill switch exists to unwind the aftermath quickly. (R-001 + R-002)
4. **Timeout → operator re-place → duplicate.** Combo placement exceeds the 25s budget post-transmit; subprocess SIGKILLed; route returns generic "Script timed out after 25s" (matches no classification pattern → no indeterminate warning); operator re-places; both orders live. The `orderRef` that could have answered "arrived or not" died with the subprocess. (R-009)
5. **Position drift invisible.** An assignment or manual TWS partial-close changes IB quantity; `quantity_mismatch` is never populated, reconciliation only runs when a browser opens `/journal`, its report is discarded, and no alert path exists. Basis, exposure, and exit sizing silently computed on wrong quantities indefinitely. (R-005)
6. **Daemon wedge, alive-but-dead.** Gateway TCP-alive but auth-wedged; `qualifyContracts` (no timeout) hangs the exit-orders handler inside the single-threaded loop; `Restart=always` never fires (process alive, no `WatchdogSec`); exit orders unmanaged mid-RTH until a staleness page ~30+ min later. (R-013)
7. **Mass false-completion.** A degraded session returns an empty `openTrades()` snapshot; fill_monitor declares every tracked order COMPLETED, notifies "filled Nx" with stale counts, deletes baselines; partials during the blip are never detected in real time. (R-016)
8. **DB outage mutes the pager.** Turso down during RTH: every handler's heartbeat write fails, and the watchdog that should page about it returns "skipped (off-window)" exit 0 — units alarm, external-probe read, digest all skipped. First signal is a GitHub Actions failure email up to ~80 min later. (R-021, R-019, R-020)
9. **After-hours fill lost.** A GTC with `outsideRth` fills at 16:20 ET; journal_sync's grace ended 16:15; next morning's fresh IB session no longer returns it; orders-sync was gated closed so `executed_orders` never saw it; journal_reconcile has nothing to diff. Invisible until someone manually runs Flex rehydrate. (R-011)
10. **Workflow mass-placement.** A workflow graph wired scanner→order with `confirm_order: true` returns 40 rows → 40 sequential live orders in one request; a retry places 40 more (no idempotency at the FastAPI layer, no per-run cap). (R-006)

## 5. Remediation backlog (frozen; PART B contract)

Order: reconciliation spine → remaining P0 → P1 → P2. Every task's acceptance criteria include the fault-injection proof.

| Task | Finding | Scope | Acceptance criteria (incl. verification) |
|---|---|---|---|
| REL-001 | R-005 | Reconciliation spine: populate per-contract `quantity_mismatch` in `find_position_discrepancies`; non-zero exit on IB connect failure; write a `service_health` row (`position-reconcile`); `needs_attention` ⇒ `state=error`. Schedule it (daemon handler or timer) so it runs without a browser. | Fault injection: fake IB positions vs snapshot with same-symbol qty divergence → discrepancy reported + error row written (test drives the reconcile with stubbed IB + scriptable DB seam, red first). Connect-failure injection → exit≠0 + error row. Restart drill: run on daemon start. |
| REL-002 | R-003 | Exit-order ack discipline: errorEvent capture + permId/terminal-state poll (6s, mirroring `ib_place_order.py:428-446`) before disconnect; unconfirmed/rejected ⇒ journal stays PENDING + `result["error"]`. | Fault injection: fake IB client that (a) never assigns permId, (b) emits rejection — journal must remain PENDING and heartbeat go error; red first against current 1s-sleep code. |
| REL-003 | R-004, R-012 | Durable duplicate-placement guard: persist `_unrecorded_placements` via `get_state`/`set_state`; before any placement, cross-check live IB open orders for a matching contract+action; exit-status journal update via CAS (`WHERE json_extract(payload,'$.exit_orders.status')='PENDING'` or `written_at` guard). | Fault injection: place → journal-write-failure → new handler instance (simulated restart) → second cycle must NOT re-place (fake IB open-orders returns the live order). CAS test: concurrent wholesale upsert does not resurrect PENDING. Red first. |
| REL-004 | R-001 | Kill switch: `trading_halt` flag (Turso + file fallback) checked at `/orders/place`, `/orders/modify`, `/workflow/run` emit_order, and exit_orders before placement; `POST /trading/halt` + `POST /orders/cancel-all` (master-scoped global cancel), operator-auth'd. | Fault injection: set halt → each path refuses with explicit error (4 call-site tests). Cancel-all drill against fake IB: N working orders → all cancelled, confirm-polled. |
| REL-005 | R-002, R-006 | Server-side order limits in FastAPI (single chokepoint used by place/modify/workflow/exit paths): max qty/order, max notional/order, max orders/min; workflow per-run cap. Env-tunable, fail-closed. | Fault injection: oversized qty, oversized notional, burst >N/min, workflow 40-row emit — each refused server-side with the UI bypassed (direct HTTP). Red first. |
| REL-006 | R-009 | Indeterminate-outcome discipline on timeout/exception: placement timeouts and post-transmit exceptions return `ORDER_INDETERMINATE` + "check open orders" + `orderRef`; route passes `orderRef` in so it survives subprocess death. | Fault injection: subprocess killed post-transmit (stub) → response carries indeterminate code + orderRef; classification test for "Script timed out". Red first. |
| REL-007 | R-019, R-020 | Heartbeat honesty: `_load_pending_orders` failure and journal-upsert failures set `result["error"]` (soft failure, no `last_run` latch); `imported` counts only successful writes. | Fault injection: DB seam raises on read/write → service_health row `state=error` written; `imported` excludes failures. Red first. |
| REL-008 | R-013 | Bound the daemon: `upsert_daemon_state` → hrana transport; per-handler hard deadline (thread+timeout); paginate the journal reads; add sd_notify + `WatchdogSec` to `radon-monitor.service`. | Fault injection: handler stub sleeps past deadline → daemon logs timeout, next handler still runs, heartbeat error. Unit-file assertion test (installed-units contract). |
| REL-009 | R-016 | fill_monitor session-sanity: empty-snapshot (or empty managedAccounts) ⇒ skip cycle, not mass-completion; completions cross-checked against `fills()` to distinguish cancelled vs filled. | Fault injection: degraded fake session returns [] with 3 tracked orders → no completions emitted, baselines retained; cancel event → reported as cancelled. Red first. |
| REL-010 | R-021 | Watchdog: `snapshot_unavailable` distinct from off-window; units alarm + external-probe + digest still run without the snapshot; N consecutive snapshot failures ⇒ direct Pushover P1 (no DB dependency). | Fault injection: DB seam raises → exit code ≠ "skipped", units alarm executed, P1 fired after N failures (fake Pushover records delivery). Red first. |
| REL-011 | R-010 | Merge fill_monitor/journal_sync journal rows: fill_monitor row keyed so the execId row supersedes it (or fill_monitor stops writing journal rows; increments priced from execution deltas). | Fault injection: partial fill seen by both writers → exactly one surviving row in blotter derivation; basis math unchanged vs single-writer baseline. Red first. |
| REL-012 | R-011 | Scheduled off-hours execution sweep: daily post-close orders-sync/journal_sync pass (or Flex rehydrate timer) so outside-RTH fills land in `executed_orders`/journal without an operator. | Fault injection: seeded after-hours execution missing from journal → sweep imports it; window-relative test dates. |
| REL-013 | R-014 | Wrap the index-chain fetch in `asyncio.wait_for` with role-reconnect on timeout. | Fault injection: stubbed hang → bounded failure, lock released (subsequent acquire succeeds). Red first. |
| REL-014 | R-015 | Decide `_on_disconnect`: register it with an event-loop-safe implementation or delete it and correct `scripts/CLAUDE.md`; fix `_subscriptions` leak. | Test proves registration fires on disconnectedEvent (or dead path removed and docs updated); subscription map shrinks on cancel. |
| REL-015 | R-017 | Pool acquire-time liveness for `orders`/`sync` roles: bounded `managedAccounts`/`reqCurrentTime` probe; reconnect on failure. | Fault injection: fake client TCP-connected but probe-dead → acquire reconnects instead of handing it out. Red first. |
| REL-016 | R-018 | Empty-snapshot guard in ib_sync: refuse zero-position upsert when prior snapshot non-empty (flag for explicit override). | Fault injection: degraded fetch returns [] with prior non-empty snapshot → no upsert, error surfaced. Red first. |
| REL-017 | R-008 | Placement pre-flight: refuse when `auth_state != "authenticated"`; surface relay farm-down as submit-blocking in the order UI. | Fault injection: awaiting_2fa state → fast refusal (no 25s burn); UI state test for stale-feed gate. |
| REL-018 | R-007 | Order-aware restarts: automated (non-operator) gateway restarts during RTH snapshot open orders first, log + alert them; failed-placement restart branch defers when open orders exist. | Fault injection: fake open orders + auto-restart trigger → alert row + logged inventory before restart. |
| REL-019 | R-022 | Order audit trail: INFO log on every successful submit/ack/cancel/modify + `order_events` Turso append keyed by orderRef/permId. | Test: placement success path emits log + row; cancel/modify covered; scrub check (no account numbers). |
| REL-020 | R-023 | `ib_execute` exit discipline: non-zero exit + "FILLED BUT NOT JOURNALED" banner on `log_trade` failure; raise on id-read failure instead of `next_id=1`. | Fault injection: journal write raises post-fill → exit≠0, banner; id-read failure → abort. Red first. |
| REL-021 | R-024–R-042, R-044–R-047 | P2 batch (each atomic within one commit): structured cancel/modify errors; rejection→digest; watchdog dead-man via external probe; `/health/lite` feed+db echo; `_now_iso()` unification; holiday gate; fsync in `atomic_save`; price-cache prune on write; relay cache eviction + queue caps + per-client sub cap; refuse `RADON_AUTH_DISABLED` in hetzner mode; delete/disable `exit_order_service.py` legacy daemon + `pool_order_manage.py` dead code; depth desync heal + markTick ordering; docstring fix; `local.sh` through the control helper; `.corrupt` backup + SIGTERM-safe save_state; tzdata fail-closed; deploy keeps `radon-health` up; 0026 migration footer. | Each with its own red-first unit test where behavior changes; deletions verified by grep + suite green. |
| REL-022 | R-043 | Durable order idempotency: back the Next.js idempotency store with Turso (or an on-disk store) so keys survive restarts/deploys; keep the in-process Map as an L1. | Fault injection: record key → simulate process restart (new store instance over the durable layer) → identical resubmit is deduped. Red first. |

Findings discovered during PART B go to `NEW_FINDINGS` appendix — this backlog is frozen.

## 6. Audit ledger

The weekend loop (`.claude/skills/reliability-weekend/`) reads the last line
here to scope its Saturday delta audit, and appends one line per run.
Delta findings continue the R-### numbering in dated `## Delta audit` sections.

- Audited through: `19135691` on 2026-08-09 — initial full audit (R-001…R-047) + PART B remediation.

## 7. Exit criteria check (A5)

- RELIABILITY_AUDIT.md exists; every finding cites file:line — **yes** (§3).
- Every A2 category walked; clean areas explicitly listed — **yes** (§3 "Audited, clean").
- Every backlog task has a verification method — **yes** (§5, acceptance criteria column).
- Zero source files modified — **yes** (this document and session scratchpad only).
