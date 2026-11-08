# Implicit State Machines Audit (2026-11-03)

Scope: every lifecycle in `scripts/`, `web/`, `cloud/`, `site/`, `lib/tools`, `.pi/extensions` that is encoded as
scattered flags, status strings, timestamps, lock/marker files or DB columns instead of one explicit
state type plus a transition function. Read-only audit; no code changed.

Method: five parallel read-only sweeps (API/IB/cloud, daemons/orders, other Python, web/lib+app,
components/site), then direct re-reading of the highest-severity claims. **[V]** = re-verified
line by line during synthesis. Unmarked = reported by a sweep with a code citation, not re-read.
*(inferred)* = consequence not traced end to end.

Count: **98 implicit machines** in §3. 20 HIGH (H or M-H), 45 MEDIUM (M, M-L, L-M), 33 LOW.
Only explicit machines found: `web/lib/offline/offlineStatus.ts` (reducer), `useWhatIfMargin`,
`agent/turnSteps`, `assistant/loop`, `SetupWizard.step`, `ChatStatus`, `scripts/lib/staleDataMachine.js`,
`scripts/research/state.py` (CAS `WHERE status=`), `ai_cycle` store. There is **zero `useReducer`** in `web/`.

---

## 1. Fix-first list (orders, money, auth)

| # | Machine | Defect | Where |
|---|---|---|---|
| 1 [V] | Cancel outcome | "Order gone from `openTrades()`" is reported as `finalStatus="Cancelled"`. ib_insync drops **Filled** orders from that list too, so a fill during the cancel window is a "cancel". `/orders/replace` trusts the success path and places the replacement: original fills + replacement = doubled exposure. The documented "Filled is the hard abort" rule cannot fire. | `scripts/ib_order_manage.py:67-83,216-226`; `scripts/api/server.py:3685-3704` |
| 2 [V] | Browser cancel poll | Same flaw client side: absent from `open_orders` = "order cancelled" toast + `cancelledOrders` row. No dedupe for a second cancel on the same permId (duplicate loops). Cancel+modify on one permId share `pollCountsRef` and overwrite each other's timer handle (orphaned loop survives unmount). | `web/lib/OrderActionsContext.tsx:102-196, 171, 278, 356-363` |
| 3 [V] | Browser cancel poll vs rate limit | Each pending order POSTs `/api/orders` every 5s (12/min); the route allows 4/min/user. Most ticks 429, still count toward the 24-tick cap, and burn the user's sync budget. | `OrderActionsContext.tsx:111,209`; `web/app/api/orders/route.ts:57-60` |
| 4 [V] | Place outcome taxonomy (Py/TS drift) | Next treats `initialStatus:"Unknown"` as deterministic rejection and clears the idempotency key. Python returns `status:"ok", initialStatus:"Unknown"` for an acknowledged order (permId != 0, empty status). A retry can duplicate the order. | `web/app/api/orders/place/route.ts:471-476`; `scripts/ib_place_order.py:729,751` |
| 5 [V] | Exit-order lifecycle `journal.payload.exit_orders.{target,stop}.status` | `PLACED` is terminal: never reconciled with IB, so a broker-cancelled/expired GTC leaves the position unprotected with no signal. No production writer of `PENDING` exists (entry transition only by hand). **Stop leg is a `LimitOrder` at the stop price**: a SELL limit below market is marketable and closes the position immediately (latent until a `PENDING` stop row exists). Guard rows keyed on nullable `trade_id` collide and become permanent; guard re-write failure `continue`s silently while heartbeat says `ok`. `_ACTIVE_ORDER_STATUSES` omits `PendingCancel`/`Inactive` (drifts from `bankroll_guard._RESTING_STATUSES`). | `scripts/monitor_daemon/handlers/exit_orders.py:18,146-162,307,386,643,725,840-856` |
| 6 | Legacy exit service `PENDING_MANUAL`→`ACTIVE` | Second vocabulary for the same journal field (`target_price` vs `price`). Only `Cancelled`/`Inactive` count as failure, so `Rejected`/`ApiCancelled`/`PendingSubmit` mark the row `ACTIVE`. CLI path still runnable. | `scripts/exit_order_service.py:132,446-453,461-545` |
| 7 [V] | ModifyOrderModal fill-race guard (R-633) | Guard compares a seed snapshot to the live `order` prop, but both owners pass a frozen `OpenOrder` captured at click time. `fillsAdvanced` is always false; reprompt branch is dead. Quantity modify sends `snapshot.filled + qty` on stale fills; modal stays open on a dead order. | `web/components/ModifyOrderModal.tsx:365-391,667-686`; `WorkspaceSections.tsx:3232,3240,3446`; `OrderTab.tsx:1121,1228` |
| 8 [V] | Chain OrderBuilder confirm step | Auto-mid effect keeps rewriting `limitPrice` while `confirmStep` is armed; Transmit sends click-time mid, not the reviewed price. Leg add/flip/remove and URL prefill do not reset `confirmStep`. Ack reset deps miss action flips/expiry changes with same length, and un-ack every mid tick (unbounded orders cannot be armed in a moving market). Mobile ticket has the same auto-mid drift. | `web/components/ticker-detail/OptionsChainTab.tsx:455-460,548-552,1250,1451-1488`; `MobileOrderTicket.tsx:245-249` |
| 9 | SingleLegOrderTicket Paper/Live | Paper toggle lives in the confirm-step risk gate; flipping to Live does not reset `confirmStep`, next click POSTs live. Parent `leg` refresh resets `quantity` to full size while armed (partial close silently becomes full close). TIF change does not reset. | `SingleLegOrderTicket.tsx:193,207-213,462-471`; `BookTab.tsx:347,428-440`; `InstrumentDetailModal.tsx:40-46`; `PositionTable.tsx:735-738` |
| 10 [V] | ListedContractOrderForm (futures, index options) | No confirm step and no unbounded-loss ack; Enter submits. `action/quantity/limitPrice/tif` survive a contract change (no key/reset): old limit goes out on the new contract. | `web/components/ticker-detail/ListedContractOrderForm.tsx:123-133,155-194` |
| 11 | Position open/close encoded in `journal.payload.action` | 6 independent classifiers (`fill_monitor`, `journal_sync`, `journal_rehydrate`, `ib_reconcile`, `ib_execute`, `expiry_sweep`) + 2 consumers (`journal_basis`, `fromJournal.ts`). Stock has no open/close distinction (short sale renders as close). Flip-through-zero mislabelled. Failed prior-qty lookup returns 0 → close written as `SELL_TO_OPEN` phantom short. Unknown side: one classifier returns `BUY`, the other `None`. `CLOSED` counts `-qty` in one place, 0 in another. | `fill_monitor.py:363-412`; `journal_sync.py:942-944,1070-1094`; `journal_rehydrate.py:207-239`; `journal_basis.py:117-124`; `expiry_sweep.py:73-82`; `web/lib/blotter/fromJournal.ts:158-170` |
| 12 | Order idempotency entry | In-flight entries are never persisted (restart mid-place loses the guard). `MAX_ENTRIES` evicts oldest regardless of state. Host-local file only. | `web/lib/orders/orderIdempotency.ts:67-77,211,244-247,316-376` |
| 13 | IB failure cooldown | Armed on any connection-pattern error **before** the Gateway health check and kept when Gateway is healthy: a slow read script's timeout blocks order **cancel/modify** for 15s. In cloud mode a placement error is rewritten to "IB Gateway is not responding", which matches neither indeterminate marker, so the caller gets a plain 502 (retryable) instead of 504 `ORDER_INDETERMINATE` *(inferred: needs a transmit-then-fail without the flag)*. Three drifting "IB down" pattern lists. | `scripts/api/server.py:3324,3402-3482,6364,6378,6464-6472,6528-6557`; `ib_gateway.py:212` |
| 14 | `ib_execute.monitor_order` | `Inactive`/`Rejected` loop as "Working" until timeout. On disappearance, fills matched by **symbol** sum every same-day fill and journal the wrong qty/price. Journal id is max+1 read (races). | `scripts/ib_execute.py:176-276,291` |
| 15 | Realtime price socket → transmit gate | `onclose` does not reset `ibConnected`/`marketDataDegraded`; after reconnect `feedConnected = connected && ibConnected && !degraded` opens on stale broker state for up to one status frame. Used by 5 order owners. | `web/lib/usePrices.ts:133-157,650-655,676-689` |
| 16 | Flex delivery claim `flex_deliveries.status` | Release (`DELETE`) and `mark_applied` are not bound to the claimant. A run longer than 20 min loses its lease, then deletes or flips the second run's live claim. Re-apply is documented non-idempotent: double-applied cash flows. | `scripts/db/writer.py:639-748`; `scripts/flex_delivery_ingest.py:227-393` |
| 17 | Operator-preference env overlay (order caps) | `_OVERLAY_WRITTEN` mutated outside `_LOCK` by request threads and the refresh worker; interleaving can unwind a tighter `RADON_MAX_ORDER_NOTIONAL`, so `ib_place_order.py` inherits the wider cap until next refresh. | `scripts/app_preferences.py:458-466,729,748,767-798` |
| 18 | Demo trial (Clerk `demoRole` + Turso `demo_users.status`) | Revoke writes Clerk→DB, extend writes DB→Clerk. A failed DB write after revoke lets a `user.created` replay restore the old expiry. Any non-`pending` role string with future expiry = active trial. Swallowed release error leaves user pending forever. | `web/lib/demo/demoContext.ts:50-70`; `provisionTrial.ts:74-120`; `adminActions.ts:40-89`; `app/api/webhooks/clerk/route.ts:82-113` |
| 19 | 2FA push lease (cross-process file) | Nothing releases the helper's own holder `radon-cloud.ib-gateway-control` after a successful login: lease lingers up to 600s, refusing watchdog recovery and the next Force-2FA. Release matches component name only (ABA after orphan revoke + re-acquire). `consume` skips the orphan check. Broker `/status` lease view ignores the orphan rule the helper honours (UI disarms actions the helper would accept). | `scripts/utils/ib_2fa_lock.py:289-477`; `cloud/scripts/ib-gateway-control.sh:45,124-214,562-608`; `ib_gateway_remote/serve.py:426-447` |
| 20 | IB watchdog episode state (16 fields, ~6 overlapping episodes) | Corrupt JSON resets every counter, including the restart-storm caps that bound 2FA pushes. Valid JSON with a bad field type crashes every oneshot run. No cross-episode invariants. | `scripts/ib_watchdog.py:94,550-661,1700-1726,1840-2060` |
| 21 | Login-throttle quiet period | Missing/unreadable watchdog state = "login allowed" (fail-open), opposite of every other auth guard; each extra login re-arms IBKR's failed-login counter. | `scripts/utils/ib_login_throttle.py:28-39`; `ib_gateway_remote/serve.py:401-413` |
| 22 | Flex token 1025 lockout embargo | Local sidecar lapse writes `ok` to the shared Turso rows, erasing the other host's live lockout; next caller fires SendRequest and extends IBKR's 7-day lockout. Also overwrites `cash-flow-sync`'s own row. | `scripts/utils/flex_embargo.py:233-363` |

---

## 2. Cross-cutting patterns (root causes, not instances)

1. **"Absent from the open list" is treated as a terminal state.** Seen in `ib_order_manage.cancel_order`, `OrderActionsContext` cancel poll, modify poll, `ib_execute.monitor_order`, `pool_order_manage.py:68,132,216`, `fill_monitor` completion branch. Absence means Filled | Cancelled | ApiCancelled | Inactive. Every one of these needs an executions lookup before naming the outcome.
2. **IB order-status vocabulary is re-declared ~8 times** with different members: place route `REJECTED_STATUSES`, `ib_place_order` terminal set, `exit_orders._ACTIVE_ORDER_STATUSES`, `bankroll_guard._RESTING_STATUSES`, `exit_order_service` failure set, `pool_order_manage` terminal set, `ib_execute` elif chain, `fill_monitor` (stored, never read). `PendingCancel`, `Inactive`, `ApiPending`, `Unknown` are the usual omissions.
3. **Confirm/armed steps do not reset when their inputs change.** Chain builder, mobile ticket, SingleLeg ticket, OrderTab TIF, PreferencesSection widen confirm, SetupWizard validation verdict. Root fix: derive "armed" from a hash of the reviewed payload and disarm on mismatch, instead of a boolean.
4. **Owners pass frozen snapshots to components whose guards assume live props** (ModifyOrderModal, MobilePositionList, WorkspaceSections dialogs).
5. **Multi-writer files/rows with read-modify-write and no CAS:** operator hold, trading halt, 2FA lease release, `open_orders` table (last snapshot wins, older book deletes newer inserts), `service_health` healed by non-owners (`journal_reconcile`, `flex_embargo`), watchdog cooldown counters, `watchdog_pages.attempts`, CTA sync status, provider cooldown file.
6. **Fail-open vs fail-closed is inconsistent for the same class of guard.** Fail-closed: `ib_2fa_lock`, `ib_operator_hold`, trading halt (unreadable). Fail-open: login throttle, trading halt (`{}`), NVIDIA trip on corrupt JSON, alert throttle on bad timestamp, `IBStatusContext` `ibConnected` initialised `true`, operator hold `None` on app host.
7. **In-memory counters that bound restart storms reset on the restart they trigger:** pool recovery ladder (`os._exit` every ~3-4 min forever, below systemd `StartLimitBurst`), watchdog corrupt-file reset, Robinhood `_refresh_disabled`, MenthorQ auth embargo, orders-sync shed streak.
8. **The same machine implemented twice:** IB auth/service states (Py `ib_gateway.py` / TS `IBStatusContext`), scan backoff (`scan_gate.py` / `backgroundScan.ts`, plus 4 ad hoc copies in `server.py` and 3 in Next routes), operator hold (Py / bash), CTA sync (2 Python implementations, 2 lock paths, 1 status file), UW quota (3 machines, 3 copies of the 20:00 ET reset), provider health (3 mechanisms), NVIDIA pacing file (3 readers, Py + embedded JS), migrations (`migrate.py` / `migrate.ts`), deploy "what was running" (operator topology + deploy-root-helper snapshot), gateway power state (encoded 5 times).
9. **Status string drift:** `auth_state` `unauthenticated` (docs, health probe) vs never produced; `remote` unmapped; `service_state` `restarting`/`stopped` unhandled in UI; UW governor refusal text never matches `uw_embargo.is_daily_quota`; exit codes 74/75 mean different things in two helpers; `service_health.state` enum exists only in comments (no CHECK).

---

## 3. Full catalog by subsystem

### 3.1 Orders, fills, journal (`scripts/`)

| Machine | States | Hazards | Sev |
|---|---|---|---|
| Cancel outcome | Submitted/PreSubmitted/Cancelled/ApiCancelled/Filled | Fill reported as Cancelled; replace doubles exposure (§1 #1) | H |
| Exit-order lifecycle | PENDING/PLACED + guard dict | §1 #5 | H |
| Legacy exit service | PENDING_MANUAL/ACTIVE | §1 #6 | H |
| Journal `action` label | BUY/SELL/BUY_OPTION/SELL_OPTION/SELL_TO_OPEN/BUY_TO_CLOSE/CLOSED | §1 #11 | H |
| `ib_execute.monitor_order` | filled/cancelled/unknown/timeout | §1 #14 | M-H |
| Fill-monitor mirror rows (`fill-monitor:con-…:order-{permId|orderId}:…:filled-N`) | provisional → superseded (deleted) | Mirror keyed by orderId when permId was 0 is never superseded (permanent duplicate) *(inferred: permId 0 at detection)*. `known_orders` keyed by orderId across all clients (manual TWS 0/negative ids collide). Final increment not journaled by fill_monitor. Stored `status` never read. `fill_monitor.py:146,212,247-276,445-451`; `journal_sync.py:404-498` | M |
| `open_orders` snapshot table | whole-table replace | Two writers, no lock or snapshot-time ordering; older book deletes newer rows; unacked orders (no permId) dropped; fill_monitor heartbeats `orders-sync` row. `fill_monitor.py:283-339`; `ib_orders.py:301-343`; `db/writer.py:2793` | M |
| Basis carry-forward pin | prior snapshot → sticky basis | Close+reopen at ≤ size same side between syncs keeps old basis *(inferred)*; `basis_source` still says `ib`; `except: pass`. `ib_sync.py:1797-1910` | M |
| Monitor-daemon handler cycle | last_run, _pending_health, _enabled, _inflight | Timed-out handler threads keep running (only cash_flow_sync retires); late exit_orders thread mutates guard during `save_state` *(inferred)*; exit_orders has no corrupt-load seed. `daemon.py:353-377,454-568`; `base.py:207` | M |
| `bankroll_guard` globals | `_BROKER_BOOK` Ellipsis/None/tuple, take-once `_STORE_CLOSE_PROVEN` | Assumes one order per interpreter; long-lived callers set but never consume; not thread-safe. `bankroll_guard.py:194-209,285-293,533-535` | L-M |
| Flex throttle ladder | throttle_count, blocked_until, soft_attempt_* | Corrupt `blocked_until` blocks forever (only reset is success, which cannot run). Docstrings contradict code (1018-only, 1h cap). `handlers/_throttle_backoff.py:150-172` | M-L |
| `pool_order_manage.py` | terminal {Filled,Cancelled,ApiCancelled} | Missing = Cancelled; no Inactive/PendingCancel. Documented unused. `:68,132,216` | L |

### 3.2 IB Gateway, auth, ops (`scripts/api`, `ib_gateway_remote`, `cloud/`)

| Machine | States | Hazards | Sev |
|---|---|---|---|
| IB failure cooldown + place outcome | armed/clear; submitted/rejected/indeterminate/502 | §1 #13. Typo in `IB_GATEWAY_MODE` re-enables auto-restart (`server.py:6420`) | H |
| 2FA push lease | free/held/expired/orphaned/unreadable/guard-timeout/consumed + synthetic `broker:*` holders | §1 #19. Orphan memory per process (`_orphan_reported`, `_orphan_seen_up`); orphan probe sleeps ~0.8s under the exclusive guard | M-H |
| Gate 1 restart backoff ladder `[60…3600]` | attempt_count, next_attempt_after, last_outcome | Cloud mode returns before Gate 1 (`ib_gateway.py:1497-1505`) [V]: ladder never runs in production; `/health.restart_backoff` always 0, so the watchdog's stuck-2FA predicate is always true on that term; docs still describe it as active; `reset_restart_backoff` keeps `last_outcome` | M |
| `radon stop/start` topology file | running / stopped(saved timers) | Second `radon stop` overwrites the saved list with empty [V]; next `start` restores **no** timers (watchdog, backups stay off). `set -e` aborts on gateway refusal before `systemctl start` and `rm`. `cloud/scripts/operator-radon.sh:425,588-629` | M-H |
| IBKR operator hold | absent / `{"held":false…}` / held | No CAS; `clear_hold` ignores hold `id` so an unhold deletes a newer hold. App host treats `None` (unknown) as not held; relay keeps last value. Cache reset races an in-flight probe. `ib_operator_hold.py:43,162-189`; `services.py:788-914`; `radon-docker-gw.sh:106-116` (bash copy) | M |
| Pool recovery ladder + auth transition tracker | in_progress, consecutive_failures, previous_auth_state | `os._exit(1)` after 3 failures wipes the counter: exits every 3-4 min forever under systemd's burst limit. Hold freezes `previous_auth_state` (streaks route serves stale). `unreachable→authenticated` skips heal + lease release. `IBPool._connected` written 6×, never read. `server.py:378-507`; `ib_gateway.py:1193-1205,1613,1630`; `ib_pool.py:111` | M |
| `auth_state` vocabulary + `/health/lite` | unreachable/authenticated/awaiting_2fa/unknown/remote | `unauthenticated` documented and checked, never produced. 0.5s `/health/lite` budget times out on broker mTLS; daemon classifies as `down` not `unknown`. `ib_gateway.py:1350-1420`; `server.py:163,2258-2275`; `health_service/probes.py:337-346` | M |
| Gateway power state | docker running/stopped/missing/unknown → transition journal `desired` → helper exit 0/1/2/74 → broker HTTP 409 → systemd-like `active_state` → TS `gatewayPowerState` | Encoded 5 times; stderr-first `detail` makes any stderr a substring guess; `Type=oneshot RemainAfterExit` reads `active(exited)` while stopped (health = `up`); exit 74/75 collide across helpers; inverted lock order → spurious 74; `stop` skips app-role refusal. `ib-gateway-control.sh:278-629`; `serve.py:351,725-740`; `services.py:426-437,855`; `web/lib/adminFormat.ts:284-295` | M-L |
| Broker verb cooldown + login-throttle gate | `_verb_history` persisted | Every direct helper caller bypasses both gates; `record_verb` only on exit 0 (a failed restart that sent a push does not arm cooldown); check/record not atomic across threads. `ib_gateway_remote/serve.py:86-152,400-413,722,751` | M |
| Deploy transition | prepared/backup_started/backup_complete/promoting/target_unverified/verified/finalization_pending + receipt + green marker + Turso row | Any phase → any phase accepted. Green marker and Turso row written before `commit-transition`; a failed recovery restores the old release while markers name the failed SHA *(inferred crash window)*. `deploy-journal.py:14-77`; `deploy.sh:404-424,1344-1379,1796-1828,2042-2045`; `deploy-root-helper.sh:842-1130` | M-L |
| Control daemon detached actions | `_detached_pending` | Check-then-act deploy-lock race; `{"accepted":true}` mapped to success even if the run later exits 74. `control_service/serve.py:304,424-447`; `services.py:1025-1026` | L-M |
| orders-sync shed streak | warn/error | In memory; gateway-down detected by string match. `server.py:711-730` | L |
| nextjs DB watchdog | consecutive_wedges | Parse error resets counter; `check=True` raises before `save_state`; "restarted" row written before restarting. `cloud/scripts/nextjs_db_watchdog.py:143-227` | L |
| post-deploy hooks | running/done/failed | Killed run leaves `running`, re-executed with no idempotency check. `cloud/scripts/post_deploy.py:446-479` | L |
| Ad hoc scan cooldowns | `_leap_last_scan`, `_theta_last_scan`, `_strength_last_scan`, `_flow_tab_last` | Duplicate `ScanGate`. `server.py:2659,4254,4393,4545` | L |

### 3.3 Watchdogs, alerting, incidents

| Machine | States | Hazards | Sev |
|---|---|---|---|
| `watchdog_cooldowns` (host-local SQLite) | hysteresis `stale`/`error` × failure/success; `severity:P1-3` × notified/resolved; pseudo-services | A success of one kind resolves all severity rows: a writer heartbeating `state=error` re-pages P1 every 1h, defeating the 24h ceiling (no test covers cross-kind). P1 emergency cancelled by the wrong bucket's "healthy". Counter RMW without transaction under concurrent buckets. `cooldown.py:79-133`; `check.py:261-263,378`; `__main__.py:213-243` | M |
| `service_health` healed by non-owner | error → ok | `journal_reconcile.heal_…` flips the row whatever caused the error; non-atomic. `handlers/journal_reconcile.py:398-446` | M |
| `service_health.state` column | ok/syncing/error/paused (comment only) | No CHECK; embargo in `last_error` JSON; COALESCE keeps stale `finished_at`. `db/service_health_sql.py:24-56`; `service_cycle.py:118-120` | L |
| Page-responder ledger `watchdog_pages` | pending/claimed/skipped/free string | `claim_token=None` bypasses ownership guard; `attempts` RMW. `pages.py:59-80,313-366` | L |
| Incident files | open/resolved | Duplicate fingerprint keeps only last; no hysteresis (flap = new file). `incident_watchdog/store.py:72-81` | L |
| Incident/grok responder pid locks | — | Non-atomic check-then-write; "gave up" and "analyzed" share one state. `incident_responder.py:152-161,397-404`; `grok_page_responder.py:306-314` | L |
| Systemd unit alarm state | flap/delta memory | Non-atomic `write_text`; corrupt = empty. `watchdog/units.py:282-307` | L |
| User alert rules | `last_fired_at` | Level-triggered, re-fires hourly; failed mark write = fires every scan; bad timestamp = no throttle. `alerts/evaluate.py:84-90,155-169` | L |
| TradingView alert drain [V] | processed_at, digest_sent_at, duplicate_of | `processed_at` stamped before the Pushover send; failed push leaves rows processed + unsent, reader filters `processed_at IS NULL`: alerts never retried (comment claims "rows stay unstamped"). `scripts/tv_alerts_drain.py:166,192-222` | M |
| Unattended release | claimed/merged/green/rejected + outbox sent/attempted | `record()` accepts any transition, including regressions. `unattended_release.py:233-270,337-431` | L |
| Nightly runner phase status | exit code by string-prefix precedence | CI only. `runner/phase_status.sh:19-44` | L |

### 3.4 Data sources, quotas, credentials

| Machine | States | Hazards | Sev |
|---|---|---|---|
| Flex delivery claim | absent/in_progress/applied/stale-in_progress | §1 #16 | H |
| Flex token embargo | none/active/lapsed/unknown/cleared | §1 #22 | H |
| Trading halt flag [V] | absent/halted/not-halted/unreadable→halted | `{}` or an object without `halted` reads as ALLOWED (`trading_halt.py:57`) despite the fail-closed docstring; no CAS (halt vs resume last-writer-wins); ImportError fallback write non-atomic | M |
| UW quota (3 machines) | `uw_budget.json` count; governor `policy.embargo/provider_count/auth_status/reserved_total`; per-writer `next_attempt_at` | Governor refusal text never matches `is_daily_quota`: writers arm 5-min burst while governor holds to 20:00 ET; wrong `next_attempt_at` heartbeated. 20:00 ET reset implemented 3×. Governor seeds from budget once then diverges; universe-scan block reads one, admission the other. `auth_status` persisted, never consulted. `utils/uw_budget.py:177-241`; `uw_governor.py:103-250`; `uw_embargo.py:40-104`; `uw_client.py:256-258` | M |
| Model-ladder provider health (3 mechanisms) | provider-cooldown file; engine `_dead_until`; NVIDIA `{next_at,cooldown_until,tripped_until,consecutive_429}` + `.tripped` | Shorter cooldown overwrites longer (no max); persisted cooldown never cleared on success; `consecutive_429` survives trip expiry (one 429 re-trips); corrupt JSON = trip lost; `acquire()` ignores `.tripped`; 3 readers of the pacing file (Py + JS). `clients/provider_cooldown.py:160`; `model_gateway/engine.py:1490-1505,1756-1759`; `nvidia_rate_limit.py:169-266`; `cloud/scripts/model_pacing.py:27-95` | M |
| Subscription credential refresh jobs | queued/running/complete; provider live/refreshed/restored/unbootstrapped/expiring/needs_reauth/store_unavailable/error | `start()` returns the active job regardless of requested provider (request dropped). Engine treats async refresh as sync and retries with stale token. Single-provider/lock-skipped pass heartbeats fleet-wide `ok`, masking `needs_reauth`. `model_gateway/lifecycle.py:16-75`; `engine.py:1666-1667`; `subscription_tokens.py:139-148,1834-1846` | M |
| CTA sync (2 implementations) | running/success/error vs healthy/syncing/degraded | Lock-busy writes `syncing` over the shared file (sticks if the active run finished); SIGKILL leaves `syncing`; `mkdir`→`lock.json` window lets a contender rmtree a live lock; pid-only liveness; 3-file non-transactional status. `utils/cta_sync.py:105-270`; `cta_sync_service.py:356-372`; `utils/cta_sync_health.py:60-69` | M |
| Robinhood rung | `_refresh_disabled`, `_breaker_until`, `_failure{class}` + token file | Disabled state not persisted (every oneshot re-hits token endpoint); rotation fallback written to /tmp never read back (spent refresh token stays on disk); env overlay resurrects stale refresh token; `robinhood_configured()` False during breaker conflates unconfigured with closed. `clients/robinhood_client.py:214-246,340-345,454-464,655` | M |
| Newsfeed delivery + auth | dbDirty/mediaDirty; `lastAuthAt=0` sentinel | Pass-1 writes `ok` while `mediaDirty`; nochange branch suppresses it (inconsistent). Failed auth retries Firebase renewal every cycle with no backoff. `newsfeed/deliveryState.js`; `cycle.js:91-276`; `index.js:93-123` | L-M |
| MenthorQ auth embargo | in-process | Lost on restart; non-atomic storage-state write. `menthorq_dashboard_client.py:79-104,389-418`; `menthorq_client.py:338` | L |
| IBClient pacing | `_pacing_retries`, `_failed_contracts` | Counter only logs, never drives retry (CLAUDE.md claims "cap 3"); never pruned. `clients/ib_client.py:305,546-561` | L |
| Data-source ladders per script | IB>RH>UW>Yahoo with skip on `auth_state` | `portfolio_risk.py:447` comment says IB>UW>RH; `fetch_dispersion.py:359-372` is IB>Yahoo (skips RH, UW, breaks rule 7); blackout marker RMW non-atomic. Yield-curve live route is Yahoo-only (`web/app/api/yield-curve/live/route.ts`) | L-M |
| UW research stream | connecting/live/degraded/disconnected/disabled + stale/missing/unavailable/offline-replay | After a mid-connection gap, stays `degraded` until a fresh subscribe ack. `uw_stream/model.py:75-133` | L |
| Migrations (`migrate.py`/`migrate.ts`) | applied set | No lock; relies on "already exists"; two languages kept in step by hand; host-local marker. | L |
| Flex sftp budget counter | consecutive | Single writer. `flex_sftp_pull.py:507-535,955-984` | L |
| Flex token-wide lockout writers | — | `record_lockout` RMW (extend-only, benign). `flex_embargo.py:265-285` | L |

### 3.5 Web data layer (`web/lib`, `web/app`)

| Machine | States | Hazards | Sev |
|---|---|---|---|
| Cancel/modify confirmation polling | requested→polling→confirmed/timed-out per permId | §1 #2, #3. Modify needs `ibOrder` present: a fill during modify yields a sticky "not confirmed" error after 2 min. `pendingModifiesRef` synced in an effect (stale overlay) | H |
| Place-route status taxonomy | — | §1 #4 | H |
| Order idempotency entry | in-flight/succeeded/indeterminate/deleted (nullable fields) | §1 #12 | H |
| Price socket | idle/connecting/open/closed + 5 flags | §1 #15; early return leaves `connecting` (recovered by teardown effect); `RealtimePricesContext` default lacks `marketDataDegraded` | M-H |
| IB status chip | wsConnected, ibConnected (init **true**), disconnectedSince, degraded, authState, serviceState, upstreamDead | `restarting`/`stopped`/`remote`/launchd strings unhandled → fall through to "connected"; legacy `connectionState` can disagree with `displayStatus`; degraded not reset on close; duplicates price-socket lifecycle and Py auth machine. `web/lib/IBStatusContext.tsx:118-137,267-283,337-529` | M-H |
| Demo trial | Clerk role × Turso status | §1 #18 | M-H |
| First-run setup | setup-mode → misconfigured → configured; token unissued/issued/expired/consumed | One Clerk key present disables both gates *(inferred)*; restart resets `consumed`/`issuedAt` (60-min replay, bounded by `isSetupMode`). `lib/setup/setupMode.ts`; `setupToken.ts:29-32` | M |
| Cross-tab auto-sync claim | idle/cooldown/backoff×1-16 | Claim fires `syncNow` captured at claim time; other tab's `consecutive` reset ignored on equal `lastFiredAt`. `lib/autoSyncClaim.ts:47,121` | M |
| `useOrders` poll/backoff | via `errorRef` | `errorRef` also holds sync warnings and edge-pending; early returns leave it set, so successes count as failures and RTH 2s cadence degrades to 60s→5min. `lib/useOrders.ts:51-192` | M |
| `usePortfolio` | ~15 refs | One `error` string for 4 sources; clean GET clears failed manual sync error within 2s. `lib/usePortfolio.ts:50-141` | M |
| `useSyncHook` | SWR + retry ladder | GET and POST share no sequence: older GET overwrites newer POST; `setError` inside `setData` updater (StrictMode double-invokes); retry counter not reset on endpoint change. `lib/useSyncHook.ts:132-320` | M |
| Subscription shrink linger | applied/desired/linger timer | `grown` unions `previousDesired` not `applied`; A→B→C inside 5s drops A immediately (the flap it exists to prevent). `lib/RealtimePricesContext.tsx:160-180` | M |
| WS-ticket backoff singleton | shared block | AbortError and 401/403 raise the shared block for prices and headlines. `lib/wsTicket.ts:15-16,76,86` | M |
| Headlines socket | connecting/live/down | No open timeout, no stale watchdog: stuck CONNECTING or half-open never retries. `lib/useHeadlines.ts:107-199` | M |
| Hand-rolled background-scan flags | — | gamma-rotation/internals reimplement `backgroundScan` without failure backoff; internals and regime routes run separate CRI triggers; cta route clears flag on a 30s timer while child still runs, and uses `spawn()` (banned). `app/api/gamma-rotation/route.ts:153-163`; `internals/route.ts:454,584-601`; `menthorq/cta/route.ts:51,318-358` | M |
| Turso pool circuit | healthy/armed/destroyed/cooldown (timestamps) | Implicit only. `lib/db.ts:86-150` | L |
| Yield-curve live cache | fresh/negative/stale | Negative window serves stale as `HIT` with no flag. `app/api/yield-curve/live/route.ts:46-48` | L |
| `uiPreferences` hydrate/flush | — | Save-before-hydrate short-circuits hydrate for the session. `lib/uiPreferences.ts:26-75` | L |
| `UwResearchContext.refresh` | — | No current-ticker guard (late A replaces B). `:29-35` | L |
| `useBookmarks`, `useRiskFreeRate` | loaded/loadError/inFlight; retry ladder | Implicit, no defect found | L |
| SLM review admin | stage/restoring/serverReady/saveStatus/notice/error | Independent flags. `app/admin/slm-review/slm-review.tsx:114-123` | L |
| DemoPendingRetry | attempt counter | `router.replace` every tick while pending. | L |
| Performance rebuild throttle | `lastBackgroundRebuildAtMs` | Never read: dead throttle. `app/api/performance/route.ts:76-78` | L |

### 3.6 UI components (`web/components`)

| Machine | States | Hazards | Sev |
|---|---|---|---|
| ModifyOrderModal fill-race | fillSnapshot/newQuantity/newPrice/editableLegs/fillRaceNotice + parent loading | §1 #7; reseeds on `permId` only (null-permId orders share a key) | H |
| Chain OrderBuilder | confirmStep/loading/inFlightRef/error/priceManuallySet/limitPrice/riskAcknowledged | §1 #8 | H |
| SingleLegOrderTicket | confirmStep + Paper/Live | §1 #9 | H |
| ListedContractOrderForm | action/quantity/limitPrice/tif/submitting | §1 #10 | H |
| OrderTab one-click cancel | local `actionLoading` | No confirm (unlike CancelOrderDialog elsewhere); duplicates context `pendingCancels`; no try/finally. `OrderTab.tsx:107,140-144,204-209` | M |
| WorkspaceSections open-order dialogs | cancelTarget/cancelCombo/modifyTarget/actionLoading | Modal × and Escape not gated by loading; old `finally` closes a newly opened dialog; mobile path can open cancel and modify at once. `WorkspaceSections.tsx:3183-3576`; `Modal.tsx:56-86` | M |
| MobileOrderTicket | confirmStep/riskAcknowledged/success/error (stays mounted when closed) | Reopen lands on armed confirm + stale success; untracked `setTimeout` clears legs/closes sheet after reopen. `MobileOrderTicket.tsx:165-175,433-548` | M |
| MobilePositionList leg snapshot | — | Stale contracts/direction feed close-out risk input. `:466-470` | M |
| PreferencesSection group save + widen confirm (risk caps) | dirty/saving/confirm | Stops at first widening row; after confirm only that row commits, rest stay dirty silently; Save not disabled while saving (double loop). `:73-75,171-211,390-410` | M |
| DemoUsersTable | single `busyId` | Overlapping actions clear each other's busy flag; revoke has no confirm. `:59,82-104,177-185` | M |
| TradingKillSwitch | status/statusKnown/lock | Halt vs Resume chosen from stale status when unknown; null renders "Halt"; own lock outside `runCommand` (can run during stack restart). `:37-45,116,135-158` | M |
| Admin ConfirmDialog | pending | Escape calls `onCancel` while pending; owners clear confirm state mid-flight. `admin/ConfirmDialog.tsx:60-63,128,202` | M |
| SetupWizard | step enum + validation verdict | Verdict not cleared on draft edit; `complete()` not blocked during validation. `:71-77,318` | M |
| Ib2faControls | 6 `show*` + 4 `pending*` + optimisticPower | Optimistic "stopped" enables Start early; safety timer re-arms on poll change; refused commands close dialog with no log. `:76-243`; AdminWorkspace `~583-591` | L-M |
| AdminWorkspace polling | per-source in-flight refs | Post-action refresh dropped if a poll is in flight; transitional flag re-creates intervals and fires all 6 fetches. `:117-169,264-288,485-490` | L-M |
| Desktop ticket inconsistency | — | OrderTab and PositionTradeTicket lack `inFlightRef` (R-281); TIF does not reset confirm in OrderTab/chain. | L |
| ChatPanel / ApprovalGate | ChatStatus + streamAbortRef + isPlacing | Busy split across 3 sources; ApprovalGate `selected` seeded once, not keyed to proposal (latent). `ChatPanel.tsx:227-458`; `ApprovalGate.tsx:56` | L |
| Scanner flags in WorkspaceSections | 6× scanning/error/lastRequest | Guard reads state, not tied to `mode`. `:1446-1462` | L |
| MetricCards | 20 mutually exclusive modal booleans | Cosmetic. `:623-644` | L |
| `.pi/extensions` StartupTracker | complete() count | Counts calls not distinct names. `startup-protocol.ts:254-310` | L |
| `lib/tools/runner.ts` | exit/timeout | SIGKILL timeout indistinguishable from failure; truncated stdout → "Invalid JSON". `:169-183` | L |
| `site/`, `apps/` | — | Nothing found | — |

---

## 4. Recommended order of work

1. **Order outcomes from absence** (§1 #1, #2, #14, `pool_order_manage`): add an executions check; model `CancelOutcome = Cancelled | Filled | PartiallyFilled | Unknown` once in Python and mirror the literal union in TS. Block `/orders/replace` on anything but `Cancelled`.
2. **One IB order-status module** (Py + generated TS union) replacing the ~8 local sets; fix the `Unknown` drift (§1 #4).
3. **Exit orders**: switch the stop leg to `StopOrder`, add `PLACED → {Filled, Cancelled, Expired}` reconciliation against IB, key the guard on a non-null id.
4. **Order tickets**: replace `confirmStep: boolean` with `armedFor: hash(reviewedPayload)`; disarm on any mismatch (price, legs, TIF, Paper/Live, contract). Add a confirm step to ListedContractOrderForm. Owners re-resolve `modifyTarget` from live orders.
5. **Cross-process leases/flags**: owner-token CAS on release/clear (2FA lease, operator hold, flex claim, trading halt), persist restart-storm counters, make the topology file idempotent on a second stop.
6. **`useReducer` for the order-facing hooks** (`OrderActionsContext`, `usePrices`, `IBStatusContext`) so impossible combos stop being representable.
