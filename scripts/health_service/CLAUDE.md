# Radon Health Daemon — CLAUDE.md

Standalone health daemon. Loaded automatically when cwd is under `scripts/health_service/`. Project root + `scripts/CLAUDE.md` rules also apply.

The daemon exists to move the health surface **off** `radon-api` into a zero-shared-fate process, so it keeps reporting precisely when the trading stack is down. Runs as `radon-health.service` on `127.0.0.1:8330`.

---

## ⛔ Stdlib-only isolation contract

`scripts/health_service/` must import **NOTHING** from the trading stack — no `ib_insync`, `uvicorn`, `fastapi`, `libsql`, `scripts.api`, or `scripts.db`. `scripts/tests/test_health_service.py::TestStdlibOnlyIsolation` runs a subprocess that imports the daemon and **fails CI** if any forbidden module is pulled in. The Turso read (`turso_http.py`) talks to libSQL over the **HTTP pipeline API with stdlib `urllib`** for exactly this reason — never `import libsql`.

The whole point is zero shared fate: a bug here, or any trading-stack dependency, must not be able to harm the daemon's independence.

## Routes

- `GET /healthz` — zero-I/O static `200`. The liveness pin; cannot block.
- `GET /status` — **ALWAYS `200`**, and **trust-split**. Degraded sources are body fields, never error codes (`feedback_http_status_for_real_errors.md`).

  **Detail gate.** Caddy proxies `app.radon.run/edge-health/status` here with no auth of its own, so the full body was readable by any anonymous internet client: IB `auth_state` (a live "a 2FA push is pending right now" signal), the `radon-*` unit inventory, and `service_health` `last_error` text carrying tickers and IB order ids — the same data the FastAPI perimeter denies untrusted callers. A caller now gets the full body only when it is **unproxied** (no `X-Radon-Public-Edge` / `X-Forwarded-*` / `Forwarded` / `X-Real-Ip` header) **or** presents `Authorization: Bearer $RADON_HEALTH_STATUS_TOKEN`. Everything else gets `public_status_payload()`: `schema_version`, `ok`, `overall_state`, `generated_at`, `health_service` — no `probes`, `units`, `service_health` or `external_probe`. Redaction is never a `401`; the never-502 edge floor requires a fast valid `200`. Proxy markers are treated as untrusted because a proxy can only ADD them, so the gate fails safe even before the Caddy marker rolls out.

  **Consumers.** The watchdogs, the deploy health-gate, CI and the Next.js admin proxy (`web/app/api/admin/edge-health/route.ts`) all read `127.0.0.1:8330/status` directly and keep the full body — do NOT point any of them at the public edge, or the admin unit inventory and `WriterFreshnessTable` silently render empty (and empty reads as green). The browser IB chip (`web/lib/IBStatusContext.tsx`) is unavoidably a public-edge caller, so it consumes the redacted aggregate: `overall_state == "up"` settles "connected", anything else is reported as unhealthy and the rich `/api/admin/health` proxy is asked to attribute the fault. `RADON_HEALTH_STATUS_TOKEN` is optional (documented in the root `.env.example`); unset just means the edge stays redacted.

  Sources, each isolated (own timeout + try/except so one failure can't fail the response):
  - **live probes** (`run_probes`, concurrent): `radon-api` via `http://127.0.0.1:8321/health/lite`, relay `:8765` / Next.js `:3000` / IB-gateway `:4001` TCP, plus a `radon-mcp` HTTP liveness probe registered only when `RADON_MCP_PROBE_URL` is set (`cloud/services/radon-health.service` sets it) — a dependency probe, so a hung-but-alive MCP degrades the aggregate without collapsing the edge to `down`.
  - **unit states** (`UnitStateCache`): `systemctl show` on a **5s background thread** — NEVER fork on the request hot path (an OOM/disk-full incident is exactly when you can't).
  - **`service_health`** (`turso_http.ServiceHealthCache`): the Turso table over stdlib HTTP, bounded (≤2.5s) + ~5s TTL + lock-serialized; any outage/missing-creds degrades to `state:"unknown"`.

  `/status` also carries **`degraded_reasons`** (`probes.degraded_reasons`): the sorted names of the non-`up` dependency probes and units behind a degraded aggregate, always computed and empty when everything dependency-side is up, so "gateway down (suppressed)", "newsfeed flap" and "2FA lock" stop reading as the same word. `_gateway_dwell_suppressed` also holds back the `radon-ib-gateway` dwell escalation when that unit's last `Result` is `success` and the clock is outside the 04:00-20:00 ET weekday session: a clean weekend/overnight exit is the expected state, not a stuck dependency.

  **Host-role applicability.** On `RADON_HOST_ROLE=app` (read from the process env, which `radon-health.service` inherits from `/etc/radon/env` — no repo import, the stdlib contract holds) the local `ib-gateway` probe and `radon-ib-gateway.service` unit are **not applicable**: the 2026-08-30 two-host split moved IB Gateway to the broker host, so the unit file is gone and `:4001` refuses locally. They still appear verbatim under `probes` / `units`, and `/status` lists them under `not_applicable` alongside `host_role`, but they never enter the aggregate, the dwell escalation, or `degraded_reasons` — otherwise a permanently absent unit collapses the public edge to `down` on every market-hours run. The broker is still covered from this host by `radon-api`'s `/health/lite` payload (`auth_state`, `port_listening`, `upstream_dead`, surfaced as `radon-api:broker`) and by the `fill-monitor` `service_health` row. Every other role — `broker`, `combined`, unset, unrecognized — keeps the dwell behavior above unchanged.

  **The app-role suppression is bounded, not permanent (REL-243, NF-10).** `effective_not_applicable()` keeps the gateway exclusion only while the nested `radon-api:broker` probe is observed up; once that positive precondition fails, the gateway names degrade back to counted after the existing 900s `DEPENDENCY_DWELL_LIMIT_SECS`, so a misapplied `RADON_HOST_ROLE=app` on a host that IS running a local gateway cannot stay edge-green forever. On expiry `/status` sets `role_suppression_expired` and empties `not_applicable`. A true app host (broker probe up) is unaffected.

## Conventions

- **Three-valued states everywhere:** `up` / `down` / `unknown`. A bounded-probe timeout is `unknown`, NEVER `down` — a timeout is not proof of death. `unit_coarse_state` maps `ActiveState=active` (incl. `active(exited)` oneshot/docker-wrapper units like `radon-ib-gateway`) → `up`.
- **Side-effect-free reads.** The daemon probes `/health/lite` (`pool=None`, no `reconnect_all`), never the mutating `/health`. It must never perturb the systems it observes.
- **Caches refresh on background threads**, snapshots are O(1) lock-guarded reads. Keep-last-value-on-failure; expose staleness as age, don't reimplement `web/lib/serviceHealthWindows.ts` staleness logic here.
- **Versioned aggregate contract.** `/status` publishes `schema_version` (`probes.py:STATUS_SCHEMA_VERSION` is the source of truth; v3 added `degraded_reasons` to the full body), boolean `ok`, and `overall_state`. `degraded_reasons` is detail-gated, so it is NOT in the public `PUBLIC_STATUS_FIELDS` payload. Off-box consumers validate the current schema and exactly one explicit predecessor (`SUPPORTED_STATUS_SCHEMAS = (3, 2)` in both `health_probe.probe` and `watchdog.external_probe` — bumping the producer without widening both fails every consumer closed); unknown or opaque HTTP-200 bodies fail closed. A nested FastAPI payload with `awaiting_2fa`, `upstream_dead`, or an unhealthy service state must make the aggregate non-healthy even when the HTTP transport answered `200`. Cloud-mode FastAPI reports `service_state=reachable` (TCP/API only, no Docker health) — treat `reachable` as healthy alongside `up`/`ok`/`healthy` in `_nested_api_state`.

## Edge + isolation (don't regress)

- Fronted by Caddy: `app.radon.run/edge-health/ping` (static `200`, never-502 floor) + `/edge-health/status` (`reverse_proxy :8330`, rewrite → `/status`). Caddy rewrites BOTH an upstream 5xx (`handle_response`) and a dial-refused daemon (`handle_errors`) to `200` `{"reachable":false,"observer":"caddy"}` with no `ok` field, so an external monitor must read the body, never the status code; `/ping` is the guaranteed floor.
- The unit (`cloud/services/radon-health.service`) has **no `Requires=`/`After=radon-ib-gateway`**. Never add one — it would re-couple the daemon to the cascade-stop it exists to escape. The operator CLI (`operator-radon.sh`) deliberately **excludes** `radon-health` from `radon stop|start|restart`.
- Run: `python -m scripts.health_service.serve` (WorkingDirectory `/home/radon/radon`). `EnvironmentFile=-/home/radon/radon-cloud/.env` supplies the Turso creds (graceful `unknown` without them).
- Release activation snapshots and restarts `radon-health` with the other code consumers, then validates the aggregate schema. The legacy deployment fallback also restarts it and requires canonical `ok` plus `overall_state` fields. These are rollout compatibility gates only: they validate field types, not a healthy verdict, so an IB outage or pending 2FA cannot block deployment. Do not turn this release coordination into a runtime systemd dependency.

Full background: `project_health_daemon_tier1_tier2` (memory) + `docs/operations.md` (Health monitoring section).
