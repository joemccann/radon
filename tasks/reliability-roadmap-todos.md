# Reliability Roadmap — Execution Todos

Source: `tasks/reliability-report-2026-06-12.html` § 10 (full problem/evidence/proposal per item).

> **⛔ EXECUTION GATE: do not start implementation before market close — 16:00 ET Friday 2026-06-12.**
> Several NOW items touch live prod units (radon-api, watchdog, deploy gate, systemd config) and must not run during RTH.

## NOW — stops active bleeding

- [ ] **DUR-01** (M, high) Unfreeze DB-first reads and pull `service_health` heartbeats out of the disabled mirror — Turso snapshots frozen at 2026-06-11 while fresher JSON sits on disk; 7 services' heartbeats dark since `2647c93`.
- [ ] **DUR-02** (S, high) Kill the `radon-beta-nextjs` crash loop (156,481 restarts over 10 days, beta 502); add `StartLimit*` flap brakes + journald caps to all radon units, with alerting on flap.
- [ ] **DUR-03** (S, high) Enforce the 2FA push lock in the `radon` CLI (radon-cloud repo) — it still bypasses `scripts/utils/ib_2fa_lock.py`; one restart broker for the gateway.
- [ ] **DUR-05** (M, high) Make the deploy health gate independent of radon-api (it has blocked deploys carrying its own fix); add retry to `migrate.py`.
- [ ] **DUR-06** (M, high) Bring live ops config under git (live `/etc/caddy/Caddyfile`, VPS `deploy.sh`, beta units) + daily drift audit between repo and prod.
- [ ] **DUR-07** (S, high) Flip the embedded-replica default to OFF in code (not just `RADON_DB_NO_REPLICA` env); shared systemd env drop-in for unit-level invariants.

## NEXT — structural durability

- [ ] **DUR-08** (L, high) Root-cause the nightly JVM API-listener wedge (78 hang detections / 26 forced restarts in 7 days, clustering ~19:51–20:09 ET — re-confirm window before building); deploy autoheal sidecar / scheduled-restart mitigation.
- [ ] **DUR-09** (L, high) Finish the sync-libsql purge: out-of-process mirror writer (in-script scan writes, not GIL-vulnerable worker threads) + bounded DB client (timeouts + retries everywhere).
- [ ] **DUR-10** (M, high) Watchdog second sensor (don't depend solely on FastAPI `/health`) + auto-start cascade victims after gateway recovery.
- [ ] **DUR-13** (M, high) Backup/restore for the canonical Turso `journal` table (laptop-pull dumps; restore drill).
- [ ] **DUR-11** (M, med) Append-only `service_health_events` history + deploy markers in Turso (so incidents can be correlated after the fact).
- [ ] **DUR-12** (M, med) Minimal host/process metrics + off-box log shipping, solo-operator sized (no self-managed Prometheus stack).
- [ ] **DUR-14** (M, med) `service_health` writer-contract library (heartbeat-every-cycle, staleness windows, writer-state semantics enforced in one place) + alert escalation channel.
- [ ] **DUR-15** (S, med) Perimeter CI guards: public-surface snapshot test (what is reachable unauthenticated) + Edge-runtime smoke test (catches `node:*` imports in the middleware graph).

## LATER — step change

- [ ] **DUR-16** (L, high) Synthetic user-path + data-plane freshness probe (login → dashboard → data fresh) with 3 explicit SLOs.

## Rejected (already covered)

- DUR-04 Tier-3 off-box prober — already shipped/enabled (`fd4ad67`); residuals folded into DUR-12/DUR-16.
