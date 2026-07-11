# Radon Health Daemon — CLAUDE.md

Standalone health daemon. Loaded automatically when cwd is under `scripts/health_service/`. Project root + `scripts/CLAUDE.md` rules also apply.

The daemon exists to move the health surface **off** `radon-api` into a zero-shared-fate process, so it keeps reporting precisely when the trading stack is down. Runs as `radon-health.service` on `127.0.0.1:8330`.

---

## ⛔ Stdlib-only isolation contract

`scripts/health_service/` must import **NOTHING** from the trading stack — no `ib_insync`, `uvicorn`, `fastapi`, `libsql`, `scripts.api`, or `scripts.db`. `scripts/tests/test_health_service.py::TestStdlibOnlyIsolation` runs a subprocess that imports the daemon and **fails CI** if any forbidden module is pulled in. The Turso read (`turso_http.py`) talks to libSQL over the **HTTP pipeline API with stdlib `urllib`** for exactly this reason — never `import libsql`.

The whole point is zero shared fate: a bug here, or any trading-stack dependency, must not be able to harm the daemon's independence.

## Routes

- `GET /healthz` — zero-I/O static `200`. The liveness pin; cannot block.
- `GET /status` — **ALWAYS `200`**. Degraded sources are body fields, never error codes (`feedback_http_status_for_real_errors.md`). Sources, each isolated (own timeout + try/except so one failure can't fail the response):
  - **live probes** (`run_probes`, concurrent): `radon-api` via `http://127.0.0.1:8321/health/lite`, relay `:8765` / Next.js `:3000` / IB-gateway `:4001` TCP.
  - **unit states** (`UnitStateCache`): `systemctl show` on a **5s background thread** — NEVER fork on the request hot path (an OOM/disk-full incident is exactly when you can't).
  - **`service_health`** (`turso_http.ServiceHealthCache`): the Turso table over stdlib HTTP, bounded (≤2.5s) + ~5s TTL + lock-serialized; any outage/missing-creds degrades to `state:"unknown"`.

## Conventions

- **Three-valued states everywhere:** `up` / `down` / `unknown`. A bounded-probe timeout is `unknown`, NEVER `down` — a timeout is not proof of death. `unit_coarse_state` maps `ActiveState=active` (incl. `active(exited)` oneshot/docker-wrapper units like `radon-ib-gateway`) → `up`.
- **Side-effect-free reads.** The daemon probes `/health/lite` (`pool=None`, no `reconnect_all`), never the mutating `/health`. It must never perturb the systems it observes.
- **Caches refresh on background threads**, snapshots are O(1) lock-guarded reads. Keep-last-value-on-failure; expose staleness as age, don't reimplement `web/lib/serviceHealthWindows.ts` staleness logic here.
- **Versioned aggregate contract.** `/status` publishes `schema_version`, boolean `ok`, and `overall_state`. Off-box consumers validate the current schema and exactly one explicit predecessor; unknown or opaque HTTP-200 bodies fail closed. A nested FastAPI payload with `awaiting_2fa`, `upstream_dead`, or an unhealthy service state must make the aggregate non-healthy even when the HTTP transport answered `200`. Cloud-mode FastAPI reports `service_state=reachable` (TCP/API only, no Docker health) — treat `reachable` as healthy alongside `up`/`ok`/`healthy` in `_nested_api_state`.

## Edge + isolation (don't regress)

- Fronted by Caddy: `app.radon.run/edge-health/ping` (static `200`, never-502 floor) + `/edge-health/status` (`reverse_proxy :8330`, rewrite → `/status`). Caddy `handle_response` does NOT catch dial failures, so `/status` 502s when the daemon is down — `/ping` is the guaranteed floor.
- The unit (`cloud/services/radon-health.service`) has **no `Requires=`/`After=radon-ib-gateway`**. Never add one — it would re-couple the daemon to the cascade-stop it exists to escape. The operator CLI (`operator-radon.sh`) deliberately **excludes** `radon-health` from `radon stop|start|restart`.
- Run: `python -m scripts.health_service.serve` (WorkingDirectory `/home/radon/radon`). `EnvironmentFile=-/home/radon/radon-cloud/.env` supplies the Turso creds (graceful `unknown` without them).
- Release activation snapshots and restarts `radon-health` with the other code consumers, then validates the aggregate schema. The legacy deployment fallback also restarts it and requires canonical `ok` plus `overall_state` fields. These are rollout compatibility gates only: they validate field types, not a healthy verdict, so an IB outage or pending 2FA cannot block deployment. Do not turn this release coordination into a runtime systemd dependency.

Full background: `project_health_daemon_tier1_tier2` (memory) + `docs/operations.md` (Health monitoring section).
