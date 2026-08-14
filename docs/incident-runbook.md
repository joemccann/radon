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
- **2026-08-13, 401 is not a wedge.** `/api/service-health` stays Clerk-protected.
  The loopback watchdog sends `Authorization: Bearer $RADON_PROBE_FRESHNESS_TOKEN`.
  HTTP 401/403, a missing token, or any non-200 without a `turso-db` error row
  is `unknown`: no wedge count, no restart, no `nextjs-db-read` error row.
  A real wedge is HTTP 200 with a synthetic `turso-db` error row, or a
  transport failure to :3000.
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
- **Classifier (P1 vs P2):** `/sign-in` HTTP 500 is P1 regardless of CI
  observability. Marker != HEAD is P2 only when CI is a positively observed
  `completed` run and the transition journal is absent (`in_flight` false).
  `ci=None` is unknown, never settled. On the VPS `gh` is absent so CI is
  always None; the journal covers promote-verify only, not the multi-minute
  staged build. Marker mismatch with unobservable CI is not an incident.
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
- **(e) Idle / leftover farm-down false stale (2026-08-13):** zero
  subscriptions is healthy unless the IB socket is down. Leftover
  2103/2105/2108 must not silence the 60s idle heartbeat; drain and
  farm-OK-while-idle clear `lastFarmStateCode`. `evaluateRelayTick` uses the
  same `isStale` open-bell grace as other RTH_ONLY writers
  (`ib-realtime-relay`). A dead process or a latched error row is still
  `fresh=false`.
- **(f) UW daily quota silence (2026-08-14, `skew`):** the 1-minute RTH
  writer raised `UWRateLimitError` ("daily request limit of 40000")
  uncaught, wrote no `service_health` row, and paged stale after the 5m
  open window (14m silent at 15:45Z). Discriminating check: VPS
  `journalctl -u radon-skew` shows the daily-limit message and the
  timer is still firing. Not IB, not Turso. Fix: catch the daily cap,
  write `error` with `next_attempt_at` = 20:00 ET (UW reset), keep the
  last snapshot, persist a local embargo so later minutes do not call
  UW, and refresh the error heartbeat so `_check_stale` cannot re-page
  on `updated_at` age. Other 429s keep a 5-minute embargo.
  Regression: `test_skew.py::TestRunIncremental::test_uw_daily_quota_keeps_last_snapshot_and_embargoes_until_reset`.
- **Detection:** `GET /api/probe/freshness` (bearer `RADON_PROBE_FRESHNESS_TOKEN`,
  always 200) — `all_fresh: false` with the failing `checks` named; it is already
  market-state aware, so `all_fresh: null` off-hours is normal.
- **Registration contract:** a new scheduled writer lands in FOUR places:
  `web/lib/serviceHealthWindows.ts`, `scripts/watchdog/services.py`, the right
  watchdog bucket, and a heartbeat on every run INCLUDING skip paths.

---

## menthorq-dashboard-session-expiry

**A third-party session died and nothing noticed for 11 days.** 2026-08-07, P2.

- **Mechanism:** `/options/net-gex` → `/api/options/exposure` → FastAPI
  `/options/exposure/{symbol}` → `MenthorQDashboardClient`. Its dedicated
  Playwright cookie jar
  (`data/menthorq_dashboard/menthorq_dashboard_storage_state.json`) had a
  `cognito` cookie that expired 2026-07-27 while the `authjs.session-token`
  cookies stayed valid to 08-26 — so the jar *looked* healthy. Every request
  then launched chromium twice (stale-jar read, then re-login) before 503ing
  after ~28 s. Symbol-independent: SPY failed identically.
- **Detection:** UI sits on "SAMPLING OPTIONS EXPOSURE" (that is the pending
  state, not a hang — it flips to MEASUREMENT FAULT after the proxy timeout);
  FastAPI logs `503 "Options exposure authentication is unavailable"`.
- **Why no test caught it:** every exposure test mocks the provider — by
  construction they cannot see an expired live credential. `cta-sync` kept
  working throughout (separate jar, separate login), proving creds/host/WAF
  were fine and isolating it to the dashboard jar.
- **Upstream blocker:** the automated re-login cannot complete — MenthorQ's
  WordPress→Cognito redirect issues `client_id=aws_cognito_client_id`, a
  literal placeholder. Not fixable from this side; the jar needs re-minting
  out of band, and a code workaround is a stand-down (upstream defect).
- **Standing defense:** `menthorq-session` daily writer
  (`scripts/monitor_daemon/handlers/menthorq_session_check.py`) reads the
  jar's own auth-cookie expiries — no browser, no network — and publishes a
  `service_health` row so the daily watchdog bucket pages. Warns at ≤3 days.
  Registered in all four places (TS windows, Python SCHEDULED_SERVICES, daily
  bucket, daemon). `_storage_state_expired()` also short-circuits the client
  so a provably dead jar fails in milliseconds instead of ~28 s.
- **Lesson:** on-demand third-party integrations need a credential-liveness
  monitor exactly like `flex-token-check`. Mocked unit tests prove code
  behavior; only a live monitor proves the credential still works.

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
- **Weekend clean exit → false "edge unhealthy" (2026-08-09):** IBKR's weekend
  session shutdown exits the gateway container cleanly (`Exited (0)`, "IBC
  returned exit status 0", JTS ShutdownTask in `docker logs`); IBC auto-restart
  is disabled by doctrine and the ib-watchdog freezes restarts off-hours, so
  the gateway stays down until the quiet window lifts or an operator restarts
  it. Discriminating check: `docker ps -a` Exited(0) + market closed + public
  edge serving 2xx fast → NOT an edge outage. The health aggregate now reports
  this as `overall_state="degraded"` (ok=false) instead of "down", and the
  off-box prober writes `ok=1, detail=edge_ok:aggregate_degraded` — the broker
  dependency alerts through on-box paths only. Operational recovery: the unit
  is a RemainAfterExit oneshot, so `systemctl start` is a NO-OP while it shows
  active(exited) — use `systemctl restart radon-ib-gateway.service` (or the
  admin panel), then approve the 2FA push. Regression tests:
  `test_health_service.py::TestStatusResponse::test_gateway_only_down_degrades_instead_of_down`,
  `test_health_probe.py::TestClassifyProbes::test_schema_v2_degraded_keeps_edge_ok`.

---

## service-health-degraded / service-down (generic cases)

`service-health-degraded`: `/api/service-health` body lists failing rows (error,
or stale scheduled). Cross-check `scripts/watchdog` cooldowns before paging —
this class is usually one writer, not an outage. Remember: a row's state is the
WRITER's health, never the content of the last event it dispatched.

Discriminate **registration-gap** vs **writer-down** before restarting a unit.
Python cannot write `state=stale`; `/api/service-health` only coerces an `ok`
row past its freshness window. So:

- **registration-gap:** coerced `stale` + raw DB `state=ok` + `last_error` null
  + `updated_at` matching the last timer fire. The writer ran. The name is
  missing from `SERVICE_FRESHNESS_WINDOWS` (or the window is the 1h default).
  Register the cadence. Do not restart the unit.
- **writer-down:** raw DB `state=error`, non-null `last_error`, or `updated_at`
  never / far past the real cadence. Inspect the unit and logs.

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

## Laptop responder + pending-diagnoses session hook

`scripts/incident_responder.py` (launchd `com.radon.incident-responder`, 10 min)
mirrors the VPS incident dir to `data/incidents_remote/`, runs
`/incident <file> --analyze-only` via headless Claude Code on open incidents
older than 12 min, writes `<incident_id>.diagnosis.md` and
`<incident_id>.incident.html` beside the mirror, and fires a macOS
notification whose body is the incident title (failing services, not a
filename). Click opens the HTML card. Analyze-only by design — shipping a
fix is human-gated.

Do not post via `osascript -e 'display notification'`. That banner is owned
by Script Editor, so a click opens an empty Untitled document. Delivery
order in `scripts/incident_notify.py`: `terminal-notifier -open file://card`
(already on the operator laptop), else a compiled `RadonIncidentNotify.app`
applet (`scripts/macos/RadonIncidentNotify.applescript`), else osascript
with subtitle (banner still has the description; click stays broken).

A persistent Native SDK or Tauri app is the wrong runtime for a 10-minute
launchd job. Native SDK (`vercel-labs/native`) notifications are
`Cmd.showNotification` inside a running Zig/TS desktop app; there is no
documented click-to-open path for a fire-and-forget helper. Tauri's
notification plugin can post a body, but desktop actions are not supported
(actions are mobile-only) and a click only focuses a running Tauri window.
Either would add a signed always-on tray app to solve a sender-identity
bug. Revisit only if Radon grows a real operator desktop shell.

## Grok auto-response on iPhone P1 pages

The same watchdog send that buzzes Pushover on the iPhone writes a
`watchdog_pages` row (sanitized excerpt, one ticket per service/kind/UTC
hour). Laptop `com.radon.grok-page-responder` (`scripts/grok_page_responder.py`,
30s) claims the row and runs headless Grok with the incident-response
playbook: diagnose, stand down when the runbook says so, otherwise TDD
and ship. A normal-priority Pushover follow-up reports the disposition.
The Mac must be awake. Kill switches: `GROK_PAGE_RESPONDER=0`,
`GROK_PAGE_AUTOSHIP=0` (diagnose only), `GROK_PAGE_AUTOPUSH=0`.
Install: `bash scripts/setup_grok_page_responder.sh`.

macOS banners are easy to miss, so `.claude/hooks/pending_diagnoses.py` closes
the loop at the next Claude Code session start in this repo (SessionStart hook,
registered on Claude Code `SessionStart` (`startup|resume|clear`) in
`~/.claude/settings.json` and on Grok `SessionStart` in
`~/.grok/hooks/pending-diagnoses.json`). It
scans `data/incidents_remote/*.diagnosis.md`, pairs each with its
`incident-<id>.json`, and surfaces any whose incident is not `resolved` (open or
missing) as a session-start banner plus model context, so the session opens
ready to address the diagnosis. Resolved incidents never nag. No output = hook
silent.
