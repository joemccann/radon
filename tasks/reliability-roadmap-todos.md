# Reliability Roadmap — Execution Todos

Source: `tasks/reliability-report-2026-06-12.html` § 10 (full problem/evidence/proposal per item).

> **⛔ EXECUTION GATE: do not start implementation before market close — 16:00 ET Friday 2026-06-12.**
> Several NOW items touch live prod units (radon-api, watchdog, deploy gate, systemd config) and must not run during RTH.

## NOW — stops active bleeding

- [x] **DUR-01** (M, high) Unfreeze DB-first reads and pull `service_health` heartbeats out of the disabled mirror — Turso snapshots frozen at 2026-06-11 while fresher JSON sits on disk; 7 services' heartbeats dark since `2647c93`.
- [x] **DUR-02** (S, high) Kill the `radon-beta-nextjs` crash loop (156,481 restarts over 10 days, beta 502); add `StartLimit*` flap brakes + journald caps to all radon units, with alerting on flap.
- [x] **DUR-03** (S, high) Enforce the 2FA push lock in the `radon` CLI (radon-cloud repo) — it still bypasses `scripts/utils/ib_2fa_lock.py`; one restart broker for the gateway.
- [x] **DUR-05** (M, high) Make the deploy health gate independent of radon-api (it has blocked deploys carrying its own fix); add retry to `migrate.py`.
- [x] **DUR-06** (M, high) Bring live ops config under git (live `/etc/caddy/Caddyfile`, VPS `deploy.sh`, beta units) + daily drift audit between repo and prod.
- [x] **DUR-07** (S, high) Flip the embedded-replica default to OFF in code (not just `RADON_DB_NO_REPLICA` env); shared systemd env drop-in for unit-level invariants.

## NEXT — structural durability

- [x] **DUR-08** (L, high) ~~Root-cause the nightly JVM wedge~~ DONE 06-12: forensic hook live; ROOT CAUSE = the gateway's own default 23:45 UTC auto-restart wedging during relogin (in-tree AUTO_RESTART_TIME "23:58 ET" was invalid IBC format). **OPERATOR ACTION REMAINING:** apply `radon-cloud pending/dur-08-compose.patch` (moves restart to 09:05 UTC + GC logging) — recreates the container, costs ONE 2FA push; watchdog quiet windows already live on both old + new slots.
- [x] **DUR-09** (L, high) Finish the sync-libsql purge: out-of-process mirror writer (in-script scan writes, not GIL-vulnerable worker threads) + bounded DB client (timeouts + retries everywhere).
- [x] **DUR-10** (M, high) Watchdog second sensor (don't depend solely on FastAPI `/health`) + auto-start cascade victims after gateway recovery.
- [x] **DUR-13** (M, high) Backup/restore for the canonical Turso `journal` table (laptop-pull dumps; restore drill).
- [x] **DUR-11** (M, med) Append-only `service_health_events` history + deploy markers in Turso (so incidents can be correlated after the fact).
- [x] **DUR-12** (M, med) Minimal host/process metrics + off-box log shipping, solo-operator sized (no self-managed Prometheus stack).
- [x] **DUR-14** (M, med) `service_health` writer-contract library (heartbeat-every-cycle, staleness windows, writer-state semantics enforced in one place) + alert escalation channel.
- [x] **DUR-15** (S, med) Perimeter CI guards: public-surface snapshot test (what is reachable unauthenticated) + Edge-runtime smoke test (catches `node:*` imports in the middleware graph).

## LATER — step change

- [ ] **DUR-16** (L, high) Synthetic user-path + data-plane freshness probe (login → dashboard → data fresh) with 3 explicit SLOs.

## Candidates from 2026-06-12 investigations (SPCX short rejection + MU share-card fix)

- [ ] **SPX-01** (S, high) `scripts/ib_place_order.py:226-303` — on terminal-failed status (Inactive/Rejected/Cancelled), grace-wait ~1-2s for the pending IB errorEvent, re-check the error buffer, and fall back to `trade.log` entries with `errorCode != 0`; fold the IB 201 reason into the returned message. (SPCX short returned bare "Order Inactive" because the poll broke before the 201 landed.)
- [ ] **SPX-02** (S, high) `scripts/api/server.py:1411-1441` `/orders/place` — log the failure detail server-side before raising `HTTPException(502)` so rejection reasons survive in journald. (The SPCX reject reason now exists nowhere; gateway logs are `.ibgzenc`-encrypted.)
- [ ] **SPX-03** (M, med) `GET /short-availability/{ticker}` — bounded ib_pool tick-236 probe (tick 46 difficulty + tick 89 shares, streaming-only per `feedback_ib_snapshot_no_generic_ticks`, `asyncio.wait_for` bounds) with UW `get_short_data()` fallback for fee/rebate; 200 + `missing: true` semantics; validate freshness + instrument name (UW served stale rows for the recycled SPCX ticker).
- [ ] **SPX-04** (M, med) OrderTab — LOCATE/FEE chip inside `OrderRiskGate` when action=SELL with no held position: red NO LOCATE / amber HTB+fee / green EASY+shares, with as_of + source.
- [ ] **SPX-05** (S, low) Consider re-enabling the stock branch of `nakedShortGuard` (`web/lib/nakedShortGuard.ts:216-231` `_checkNakedShortRiskImpl`) as warn-not-block for SELL-stock-no-position. (Gate 4 disabled 2026-04-30; warn-only respects that while catching the SPCX case pre-flight.)
- [ ] **SPX-06** (S, low) `docs/ib_tws_api.md` corrections — line 337: tick 236 missing the 1.5/2.5 difficulty bands and tick 89 entirely; line 411: "Inactive" missing the short-sale-rejection case.
- [ ] **JRN-01** (M, high) Journal ingest gap — exec `0002920b.6a26c483.01.01` (2026-06-08 SLD 7 MU C1000 @ $25.00) exists in Turso `executed_orders` but is absent from the `journal` table. Root-cause the ingest path (monitor daemon journal_sync? Flex rehydrate?), backfill the row, and add a reconciliation check (executed_orders ↔ journal) so silent drops surface in service_health.

- [ ] **CTA-01** (S, high) `scripts/clients/journal_basis.py:129,182` use `result.rows`, which does not exist on production libsql-experimental 0.0.55 — raises AttributeError, swallowed per-ticker by `ib_sync.build_journal_basis_lookup`, silently falling back to IB drifting avgCost (may be nullifying lot-matched basis in prod). Fix to `.fetchall()`; correct the stale `.rows` docs in `scripts/db/client.py` docstring; audit for other `.rows` call sites.
- [ ] **CTA-02** (S, med) radon-cloud: add a ~16:10 ET slot to `radon-cta-sync.timer` so the day's MenthorQ report lands at the session roll instead of 17:30 ET (closes the structural 16:00-17:30 ET stale-share window at the source), and raise the unit's 5-min start timeout (Jun 11 18:15Z run was killed mid-fetch).

Deferred, not picked up: relay tick-236 streaming subscription (`scripts/ib_realtime_server.js:841`) — only if SPX-04 needs live data rather than on-demand probes.

## Rejected (already covered)

- DUR-04 Tier-3 off-box prober — already shipped/enabled (`fd4ad67`); residuals folded into DUR-12/DUR-16.
