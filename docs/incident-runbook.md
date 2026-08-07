# Incident Runbook

Documented production failure modes, backfilled from git history and postmortems.
Incident JSON artifacts are produced by `scripts/incident_watchdog` (see bottom);
each artifact's `case_id` anchors into this file. Triage them with the
`/incident <path>` slash command, which follows
`.claude/skills/incident-response/SKILL.md`.

**Global rules that apply to every case:**

- HTTP 200 is transport truth only. `/api/service-health`, `/api/probe/freshness`,
  and `:8330/status` all return 200 during real outages — judge the BODY.
- An anonymous 401/404 on a protected route is the auth perimeter, not an outage.
  Liveness curls use the public `/sign-in` route only.
- A bounded-probe timeout is `unknown`, never `down`. Off-hours quiet is normal
  for RTH writers.
- `radon restart` cycles the IB Gateway (a 2FA push). App-tier-only work uses
  `radon unit restart <unit>`.

---

## turso-destroy-storm

**undici Agent destroy storm / Turso per-IP socket exhaustion.**
Peak incident: 2026-07-02 21:32–23:37 UTC, P1, every DB-backed Next.js route dark.

- **Mechanism (layered):**
  - L0 (`fad048a7`): the long-lived Next.js process caches one libsql client whose
    undici keep-alive pool goes stale; a reused dead socket hangs every query at
    its 3000 ms deadline simultaneously.
  - L1 (`e640b532`): hung keep-alive connections accumulated (59 at peak) until
    Turso stopped servicing new requests from the VPS IP.
  - L2 (`4fcd835b`, THE storm): the L1 fix made `resetDb()` destroy the shared
    undici Agent on every failure from every caller. `Agent.destroy()` aborts all
    in-flight requests (`TypeError: fetch failed`, cause `UND_ERR_DESTROYED`);
    each aborted request's catch called `resetDb()` again — a self-sustaining loop
    only a process restart could quiesce.
  - L3 (`a1fd22da`): isolated tail-latency timeouts were still tearing the pool
    down; teardown now requires a second reset inside a 10 s cluster window.
- **Detection:** `/api/service-health` returns 200 with a single synthetic
  `turso-db` error row + `warning`; journald bursts of
  `[dbExecute:<label>] fetch failed` / `read timed out after 3000ms` across
  unrelated routes at once; `UND_ERR_DESTROYED` in `err.cause` = self-inflicted,
  `ECONNRESET`/`ETIMEDOUT` = real network. TCP probe on :3000 stays green.
- **Discriminating canary:** a Python Turso read from the same host in the same
  minute. Canary succeeds ⇒ Node-local wedge, `radon unit restart radon-nextjs`
  helps. Both fail ⇒ upstream Turso — do NOT restart-flap; stand down and probe
  from a second network (`feedback_turso_http_pipeline_incident_signature`).
- **2026-08-06 recurrence — feature-detection vs the retired replica.**
  `syncDb()` guarded with `"sync" in db`, but @libsql/client's HTTP client
  DEFINES `sync()` and throws `SYNC_NOT_SUPPORTED`. The guard passed, the call
  threw, and `readOrdersFromDb`'s catch armed a pool teardown on EVERY
  `/orders` read; two arms inside the 10 s cluster window escalated to a real
  `Agent.destroy()` that aborted unrelated in-flight requests — 503s on
  watchlist/profile/portfolio while Turso itself was healthy (Python canary
  76 ms). Fixed by gating `syncDb` on the replica being ENABLED, never on
  method presence. Lesson: after retiring a subsystem, audit its
  feature-detection guards — a method that exists and throws is not absence.
  Diagnosis required instrumentation: the teardown path logged nothing, so
  journald showed only collateral. Every teardown now logs its trigger label.
- **Standing defenses:** 8-conn bounded Agent, 5 s destroy cooldown,
  10 s failure-cluster gate, transport timeout 2750 ms < 3 s caller deadline,
  routes 503 `DB_UNAVAILABLE` (never 500), `radon-nextjs-db-watchdog.timer`
  (K=3 wedge cycles, 10 min cooldown).
- **Fix commits:** `fad048a7`, `c105cbef`, `e640b532`, `4fcd835b`, `6593dc30`,
  `a1fd22da`. Regression suites: `web/tests/db-destroy-storm.test.ts`,
  `db-pool-bounded.test.ts`, `db-timeout-self-heal.test.ts`,
  `db-stall-diagnostics.test.ts`, `routes-db-unavailable-503.test.ts`.
- **Code:** `web/lib/db.ts`, `web/lib/dbExecute.ts`, `web/lib/dbKeepAlive.ts`,
  `web/instrumentation.ts`, `radon-cloud/scripts/nextjs_db_watchdog.py`.

---

## host-local-disk-persistence

**Data written to host-local disk instead of Turso — silently lost after deploys
or invisible cross-host.** P2 class; no error surface, just wrong/empty data.

- **Mechanism:** a producer persisted only to `data/*.json` or host-local SQLite
  on whichever host ran it, and the Next.js route read that same host-local file.
  Correct only while producer host == web host. The VPS checkout is rewritten by
  every deploy, so host-local files are ephemeral. Inverted variant
  (`359f8120`): a host-local file source that derives DELETE authority from local
  presence pruned 177 shared knowledge docs on its first VPS run.
- **Detection:** a scanner page shows zero candidates or a stale universe right
  after a successful scan, HTTP 200, no banner; Turso snapshot table frozen while
  the producer's service_health row is `ok`; a `None` entry in
  `scripts/db/scan_mirror.py:SNAPSHOT_UPSERTS` (heartbeat-only = no mirror);
  shared-table row count drops right after a new host's first timer run.
- **Remediation:** add a migration + writer upsert (`scripts/db/writer.py`), wire
  into `scan_mirror`, flip the GET route to Turso-first via
  `web/lib/dbFirstRead.ts`, then verify the row lands in LIVE Turso — green tests
  are not verification (`feedback_verify_against_turso_not_json_fallback`).
  Never give a file-backed source prune authority over a shared store.
- **Fix commits:** `487f2870` (theta), `b0b0f5e2` (strength), `5cbe564d`
  (generic `scan_snapshots`, migration 0026), `359f8120` (prune authority).
- **Codified:** CLAUDE.md §Data Persistence.

---

## cancelled-deploy-corrupt-next-build

**Cancelled in-flight deploy corrupts the Next.js build — all pages 500.**
Incident: 2026-07-08, P1.

- **Mechanism:** workflow-level `cancel-in-progress: true` let a rapid second
  push cancel the in-flight run INCLUDING the SSH deploy job, after production
  units were stopped and while release artifacts (`web/.next`, `node_modules`)
  were mid-promotion → torn build. Compounding: the successor run mistook Git
  HEAD equality for a completed release (the killed deploy had already
  fast-forwarded the checkout) and skipped the rebuild.
- **Detection:** every page 500 while `/sign-in` should be 200; journald ENOENT
  on `.next/server/...` chunks or missing BUILD_ID; `gh run list
  --workflow=ci.yml` shows a cancelled run or a seconds-long "already green"
  no-op; **the definitive tell:** VPS `git rev-parse HEAD` == target SHA but
  `/home/radon/.radon-last-green-deploy` != that SHA. The `service_health`
  `deploy` row stores the last green SHA in `last_error`.
- **Remediation:** re-run the deploy (empty commit or re-run the job) — the
  marker-mismatch resume path forces a full build/restart/gate. Never bypass a
  control-plane mismatch with `RADON_DEPLOY_NO_GATE=1` except for a wedge class
  that kills `/health/lite` itself.
- **Standing defenses (`218da845`, `65213d97`):** workflow-level cancellation
  forbidden (standing comment in ci.yml); deploy job has its own
  `deploy-production` non-canceling lock; green marker required in addition to
  HEAD equality; staged build in a detached worktree; TERM/INT/HUP recovery trap.
- **Codified:** CLAUDE.md §Commit Hygiene — push once, wait for the deploy.

---

## stale-market-data-freshness

**Market data stops being fresh while everything looks alive.** Four sub-modes.

- **(a) Relay stale-tick:** IB Gateway control plane alive, data plane dead (all
  `reqMktData` return nan). Ladder in `scripts/lib/staleDataMachine.js`: 45 s
  no-tick during RTH → resubscribe → K=3 reconnects → **alert-only escalate**
  (writes `ib-realtime-relay` error row; a relay-only restart does NOT fix a
  gateway-side farm-down — that needs `radon restart`). Commit `9cdcf3e`.
- **(b) Heartbeat clobbers the error row (2026-06-18):** an `ok` tick heartbeat
  landing after an `error` escalation hid a dead data plane behind a green row
  whose own payload said `tick_age_secs: 195`. `decideHealthWrite()` now decides
  both from one snapshot; the anti-pattern to test for is `state=ok` while the
  row's own detail reports a stale tick age.
- **(c) False-positive staleness windows:** weekend/holiday/open-bell window bugs
  (`cd9f552c`, `75c65196`, `e7c6a538`). Rules: widen closed/extended windows to
  the longest legit quiet period; open-bell grace caps age at seconds-since-open;
  `health=None` (never written) is a deploy artifact, not a window bug; a `×N`
  latched daily handler means N watchdog cycles since latch, not N failures.
- **(d) Delayed, not stale:** the relay defaulted to `reqMarketDataType(4)`;
  prior close matches reference exactly but LAST lags; delayed tick fields 66-76.
  Fresh tick timestamps do NOT prove realtime.
- **Detection:** `GET /api/probe/freshness` (bearer `RADON_PROBE_FRESHNESS_TOKEN`,
  always 200) — `all_fresh: false` with the failing `checks` named; it is already
  market-state aware, so `all_fresh: null` off-hours is normal.
- **Registration contract:** a new scheduled writer lands in FOUR places:
  `web/lib/serviceHealthWindows.ts`, `scripts/watchdog/services.py`, the right
  watchdog bucket, and a heartbeat on every run INCLUDING skip paths.

---

## cri-query-plan-read-stall

**A query-plan regression that impersonated a network outage.** 2026-07-12.

- **Mechanism:** latest-CRI query `ORDER BY taken_at DESC LIMIT 1` had no
  matching index → SQLite scanned 14,924 rows of ~47 KB payloads → 6.8–7.6 s
  reads → the 3 s caller deadlines expired first, reset the shared client, and
  produced collateral failures across unrelated routes.
- **Fix (`ea24c66c`):** `ORDER BY date DESC, taken_at DESC` to use
  `idx_cri_latest`; after: p50 8.3 ms. Also `DB_TRANSPORT_TIMEOUT_MS=2750` and
  keepalive relaxed 3 s → 30 s.
- **Diagnosis order for recurrence:** (1) EXPLAIN QUERY PLAN the exact hot SQL in
  production; (2) measure fresh `SELECT 1` reads from the VPS to separate
  provider latency from query cost; (3) inspect pool running/queued before
  blaming saturation; (4) confirm `nextjs-db-read` refreshes after deploy.
- **Related class:** Turso Hrana I/O bounding (three incidents in one week) —
  signature `upstream forward failed` 502s / `stream not found`. Rules in
  `scripts/CLAUDE.md` §Turso Hrana I/O Bounding.

---

## ib-gateway-wedge

**IB Gateway wedge classes** (full state machine: `docs/ib-gateway-recovery.md`).

- **JVM/API-listener wedge:** `auth_state=authenticated` + `port_listening=true`
  BUT `upstream_dead=true`; socat floods "Connection reset by peer". The 15×
  restart loop happened because the watchdog misread `upstream_dead` as
  awaiting-2FA. Rule (`6af999d`): `upstream_dead=true` is ALWAYS api-hang (one
  bounded restart); stuck-2FA recovery requires `upstream_dead=false`; ≤2
  stuck-2FA restarts/hour.
- **2FA push stacking:** stacked pushes reject all approvals; every restart path
  goes through `ib_2fa_lock.py` (`/var/lib/radon/ib-2fa-push-lock.json`). Use the
  lock-held `POST /ib/restart`, never a raw `radon restart` alongside the
  watchdog.
- **False awaiting-2FA after deploy:** fresh radon-api reports `awaiting_2fa` for
  ~10-20 s during pool warmup; suppressed when `ActiveEnterTimestamp` < 180 s.
- **Stuck pool after 2FA:** pool clients `connected=false` with empty
  managed_accounts while a throwaway-clientId probe succeeds → level-triggered
  `recover_stuck_pool` restarts radon-api only (`46ba1e1`).
- **Pager hygiene:** tag Pushover emergencies + `cancel_by_tag` on recovery
  (`660cda91`); one page per condition-transition (`0286a396`).

---

## service-health-degraded / service-down (generic cases)

`service-health-degraded`: `/api/service-health` body lists failing rows (error,
or stale scheduled). Cross-check `scripts/watchdog` cooldowns before paging —
this class is usually one writer, not an outage. Remember: a row's state is the
WRITER's health, never the content of the last event it dispatched.

The classifier suppresses EXPECTED states (2026-08-04 false-P2): `stale` rows
while the market is closed (RTH-only writers quiet off-hours), and `error` rows
whose own `last_error.next_attempt_at` circuit-breaker embargo is still in the
future (e.g. the cash-flow-sync 24h Flex embargo). Suppressed rows travel in
the incident evidence as `suppressed_expected`. Errors without embargo metadata
classify at any hour; an expired embargo classifies again.
Related guard: `cash_flow_sync` caps soft-failure retries at 3 SendRequests per
ET day (`MAX_SOFT_ATTEMPTS_PER_ET_DAY`) so a timing-out Flex service cannot
burn the sliding-window budget all evening.

The PAGING watchdog (`scripts/watchdog/check.py:_check_error`) applies the same
embargo rule independently of the incident classifier: an `error` row whose
`last_error.next_attempt_at` is in the future fires through hysteresis (so the
operator learns about it once) and then stays quiet until the deadline passes.
Without this, a 24h Flex breaker produced one digest entry per hourly cycle —
`cash-flow-sync ×21` on 2026-08-06 — because `notify._enqueue_digest` batches
every fired outcome while `cooldown_allows_fire` only gates Pushover. A `×N`
count on a latched daily handler is cycles-since-latch, never N incidents.

`service-down`: `:8321/health/lite` connection-refused. Check
`systemctl status radon-api` and journald; remember the cascade-stop rule — a
cleanly stopped unit will NOT `Restart=always` back (`radon restart` respects
the 2FA lock).

---

## Incident watchdog operation

```
python3.13 -m scripts.incident_watchdog --once            # timer mode
python3.13 -m scripts.incident_watchdog --interval 300    # loop mode (dev)
```

Probes (all bounded, three-valued): `/sign-in` liveness, `/api/service-health`
body, `:8321/health/lite`, `:8330/status`, `/api/probe/freshness` (bearer), and
deploy status (`gh run list` + green-marker vs HEAD where the marker exists).
Findings classify into the cases above (`scripts/incident_watchdog/classify.py`)
and land as `data/incidents/incident-<ts>-<case>.json` — deduped by fingerprint
while open, auto-resolved on a clean cycle. Exit code 2 while a P1 is open (for
a systemd `OnFailure=` hook). Env: `INCIDENT_WATCHDOG_{NEXTJS,API,HEALTH}_BASE`,
`INCIDENT_WATCHDOG_DIR`, `INCIDENT_WATCHDOG_GREEN_MARKER`,
`INCIDENT_WATCHDOG_REPO_DIR`, `RADON_PROBE_FRESHNESS_TOKEN`.

It intentionally does NOT restart anything and does NOT page — remediation
stays with the dedicated units (`feedback_ib_auto_recovery_conservative`,
`feedback_watchdog_works_dont_deploy_autoheal`), and paging stays with
`scripts/watchdog`. This tool's job is evidence: a structured artifact a human
or `/incident` can act on.
