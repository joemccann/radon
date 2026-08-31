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
  `web/instrumentation.ts`, `cloud/scripts/nextjs_db_watchdog.py`.
  Watchdog units load `EnvironmentFile=/etc/radon/env`.

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

## deploy-stop-clean-oneshot-signal

**`Type=oneshot` scan units page P1 `Result=signal` when deploy
`stop-clean` SIGTERMs an in-flight run.** Known class:
`feedback_deploy_stop_clean_fails_inflight_scan_oneshots`.

- **Mechanism:** `deploy.sh` stop-clean stops every `radon-*` unit,
  including long `Type=oneshot` writers (`radon-bpi` TimeoutStartSec=6900,
  weekday sweep 35-105 min). systemd records `Result=signal`,
  `NRestarts=0`. The unit recovers on its next timer fire. The
  continuous units watchdog used to page that as a P1 outage.
- **2026-08-05:** kill and `radon-deploy-root stop-clean` in the same
  second. Fix `deba568a`: downgrade `Result=signal` to P3 when the
  kill sits inside a deploy window (20 min before green-marker mtime,
  or while the transition journal exists).
- **2026-08-14 23:30Z:** three deploys stacked (`a173289` 22:51,
  `ba6ec0d` 23:03, `bf3e73c` 23:25, green 23:27). BPI killed at
  22:52:36Z by the first stop-clean. 34 min later the last green
  exceeded the 20-min single-deploy budget; between deploys the
  journal was gone and the previous green (20:25Z) sat *before* the
  kill, so the classifier paged P1 at 23:20/23:25/23:30. Edge and
  `:8321/health/lite` stayed up. Next timer (23:30) restarted the
  scan.
- **2026-08-15 01:35Z:** Friday 23:30 catch-up still running when
  deploy `68f7aac` stop-cleaned BPI at 00:34:41Z (green 00:35:59Z,
  78s later). The oneshot stayed `failed` until Sat 11:00. A
  now-to-kill 60-min cap flipped P3 to P1 at 01:35 (3619s after the
  kill) even though kill-to-marker was 78s. Edge and
  `:8321/health/lite` stayed up. Classifier now measures
  kill-before-green against the marker, not `now`.
- **2026-08-20 02:45Z:** page `b76d4a52`. a231 stop-cleaned BPI at
  00:04:23Z (green 00:05:39Z, kill-to-marker 76s → P3). Four more
  deploys stacked; 0f7d greened at 02:42:18Z and overwrote the
  marker. Kill-to-latest-marker became 158 min, so the 60-min
  kill-before-green bound flipped P3 to P1. Edge and
  `:8321/health/lite` stayed up; BPI recovered on a later timer
  (`Result=success`). Kill-before-green and in_flight now use the
  24h oneshot recovery horizon against any post-kill green.
- **2026-08-25 16:20Z:** page `a70a393e`. BPI stop-cleaned at
  12:41:04Z; green `78a9e353` at 15:14:53Z (kill-to-marker ~2.5h →
  P3). Successor deploy at 16:19 wrote a fresh transition journal;
  `in_flight` short-circuited past kill-before-green so the 60-min
  age cap re-paged the latched oneshot as P1. Edge and
  `:8321/health/lite` stayed up. Classifier now evaluates
  kill-before-green before the in_flight branch.
- **Discriminating check:** `InactiveEnterTimestamp` before a later
  green-marker mtime (within the 24h oneshot horizon) or within
  60 min after the last green (cancelled stack / not-yet-green);
  sibling oneshots often fail the same second; `Result=timeout` /
  `exit-code` is a different class (do not downgrade). A fresh
  successor journal does not override kill-before-green.
- **Remediation:** classifier only, do not restart. Exit-code and
  start-limit-hit stay P1.
- **Regression:** `test_units.py::TestDeployCollateralSignalKill`
  (`test_stacked_deploy_signal_kill_34min_before_green_is_p3`,
  `test_signal_kill_after_last_green_during_cancelled_stack_is_p3`,
  `test_oneshot_still_failed_61min_after_stop_clean_is_p3`,
  `test_stacked_successor_green_158min_after_kill_is_p3`,
  `test_latched_kill_before_green_not_repaged_by_successor_inflight_journal`).
- **Code:** `scripts/watchdog/units.py` (`DEPLOY_COLLATERAL_WINDOW_SECS=3600`,
  `KILL_BEFORE_GREEN_FROZEN_CAP_SECS=86400`).

---

## deploy-restart-window-edge-502

**Every page 502s for a few seconds during a deploy promote.**
Reported 2026-08-25 as "502 on https://app.radon.run/admin".

- **Mechanism:** `restart-managed` stops `radon-nextjs` and `radon-api` before
  the new ones listen. `reverse_proxy` defaults to zero retries, so Caddy
  turned each `dial tcp [::1]:3000: connect: connection refused` into a raw
  502 the instant the dial failed. Deploy #89 promoted at 12:31 UTC and the
  edge served 502s from 12:31:29 to 12:31:36 — `/admin` and its RSC prefetches,
  plus 64 `/api/ib/ws-ticket`, `/api/portfolio`, `/api/orders`. Nothing was
  broken: the release was green and `/admin` answered 307 → `/sign-in` again
  the moment `next start` bound the port.
- **Detection:** transient — a 502 that clears on reload. `journalctl -u caddy`
  shows `connection refused` to `:3000`/`:8321`, `/var/log/caddy/radon.log` has
  a `"status":502` burst confined to one minute, and that minute matches the
  deploy's `[gate] Next.js HTTP responding` line. `curl -sI` after the fact
  returns the normal 307.
- **Discriminating check:** compare the 502 timestamps with
  `systemctl show radon-nextjs -p ActiveEnterTimestamp` and
  `/home/radon/.radon-last-green-deploy`. Inside the restart window it is this
  case. Persisting 502s after the unit is active are a different failure
  (check `.next/BUILD_ID` and `cancelled-deploy-corrupt-next-build`).
- **Fix (`cloud/caddy/Caddyfile`):** `lb_try_duration 15s` /
  `lb_try_interval 250ms` on the `:3000` and `:8321` upstreams. The window
  outlasts the bounded 10 s Next.js SIGTERM drain
  (`web/lib/boundedShutdown.ts`) plus the restart, so requests wait out the gap
  instead of failing. Never add `fail_duration`: marking the single upstream
  down removes the retry loop and restores the instant 502. The edge health
  floor (`:8330`) deliberately keeps zero retries so probes stay fast.
- **Publishing:** the Caddyfile is NOT shipped by the CI deploy. After merge,
  on the VPS as `radon`:
  `sudo -n /usr/local/sbin/radon-deploy-root publish-caddy` (stages, validates,
  installs atomically, bounded reload, rolls back on failure). Until then the
  drift audit flags `/etc/caddy/Caddyfile`.
- **Regression:** `cloud/tests/test_caddyfile.py::TestUpstreamRestartWindow`
  (retry window ≥ drain + start on both app upstreams, none on the health
  floor) and `::TestRestartWindowMechanism`, which runs a real caddy against a
  dead port and asserts the request is served once the upstream returns
  (`RADON_CADDY_BIN=<path>` to run it; skipped when no binary is present).

---

## caddy-health-floor-pages-aggregate-invalid

**Off-box observer pages P1 `aggregate_invalid` while ping and `/sign-in`
stay 200.** Peak: 2026-08-29 23:05Z, page `1b0b049c…`. GitHub probe
failed 23:03:36–23:03:56Z; deploy runner `1b2a82db` materialized 23:05Z
(the first release that ships `cloud/caddy/Caddyfile`).

- **Mechanism:** `/edge-health/ping` is Caddy-static 200. `/edge-health/status`
  reverse-proxies `:8330`. The never-502 floor rewrites healthd 5xx and
  dial-refused (healthd stopped during deploy `stop-clean`) to HTTP 200
  `{"reachable":false,"observer":"caddy"}`. `classify_probes` treated that
  opaque 200 as `aggregate_invalid`. Deploy-window suppression only matched
  `(ping|status)_http_5xx`, so the rewritten 502 paged P1. Local
  `:8330/status` was schema-v2 `up`; `/sign-in` 200. Not IB, not Turso.
- **Detection:** Turso `external_probe.detail=aggregate_invalid` with
  `http_status=200`; GitHub `external-health-probe.yml` FAILURE in the
  same minute; ping 200; serving-path units up. After healthd binds,
  the next off-box cycle writes `edge_ok`.
- **Discriminating check:** the public `/edge-health/status` body (or the
  Caddyfile literal) is `{"reachable":false,"observer":"caddy"}` while
  loopback `:8330/status` is schema-v2, OR the sample sits inside a
  deploy window with serving path up. A real schema contradiction
  (`ok` vs `overall_state`) is still `aggregate_invalid` and still P1.
  `status_http_502` is the pre-floor form of the same restart
  (`deploy-restart-window-edge-502` / page `d98c3364`).
- **Remediation (code):** classify the Caddy observer body as
  `status_unreachable:caddy`. Suppress that reason inside the deploy
  window when the local serving path is up, same arm as 5xx. Real
  healthd-down outside a deploy still pages. Do not local-clear
  perimeter unreachability.
- **Regression:**
  `test_health_probe.py::TestClassifyProbes::test_caddy_never_502_floor_is_status_unreachable_not_aggregate_invalid`,
  `test_external_probe_deadman.py::test_deploy_window_caddy_status_unreachable_is_suppressed`,
  `test_caddy_status_unreachable_outside_deploy_window_pages`,
  `test_aggregate_invalid_inside_deploy_window_still_pages`.
- **Code:** `scripts/health_probe/probe.py` (`classify_probes`),
  `scripts/watchdog/external_probe.py` (`_DEPLOY_COLLATERAL_REASON`),
  `cloud/caddy/Caddyfile` (`handle_response` / `handle_errors`).

---

## assistant-turn-edge-504

**A chat turn dies with `Assistant service returned an error.` and DevTools
shows `POST /api/assistant -> 504` after tens of seconds.** Reported
2026-08-29 after the operator pasted a chart and asked how it related to
their TLT position.

- **Mechanism:** `/api/assistant` rode the catch-all `handle` in
  `cloud/caddy/Caddyfile`, whose R-219 guard abandons a request after
  `response_header_timeout 30s`. The route is NON-STREAMING: it runs the whole
  multi-round `runAssistantLoop` and writes its JSON only at the end, so a turn
  emits no response header at all until it has finished, and it declares
  `maxDuration = 300`. Any turn slower than 30 s therefore reached the operator
  as a 504 while radon-nextjs was still working on it. The edge's 504 body is
  not JSON, so `payload.error` in `requestAssistantTurn` was null and the chat
  fell through to the generic string.
- **Detection:** the browser gets a 504 on `POST /api/assistant`, but
  `journalctl -u radon-nextjs | grep '\[assistant\] done'` shows the SAME turn
  with `outcome=answered` and `ms=` above 30000. The answer existed; nobody
  received it. `/var/log/caddy/radon.log` (root-readable only) carries the
  matching `"status":504` with a ~30 s `duration`.
- **Discriminating check:** an `outcome=error` journal line means the loop
  itself failed and this is NOT the case (read the error). An `outcome=answered`
  line whose `ms` exceeds the path's `response_header_timeout`, or no journal
  line at all with the request still counted at the edge, is this case.
- **Fix (`cloud/caddy/Caddyfile`):** a dedicated `handle /api/assistant*` with
  `response_header_timeout 180s` and no `lb_try_duration` (POST-only path with
  no idempotency key: a retry would replay a billed vision turn). Do NOT raise
  the guard globally; R-219 needs the catch-all short so a wedged upstream
  still becomes an observable 5xx.
- **Durable fix (shipped after the bump):** the route STREAMS. It answers
  `text/event-stream` and writes `event: start` before `runAssistantLoop` is
  awaited, then a `heartbeat` every 10 s, a `tool` frame per completed tool
  call, and a terminal `done` (or `error`) frame. The response header therefore
  exists within milliseconds of the request no matter how long the turn takes,
  so no header guard anywhere can abandon a running turn.
  `flush_interval -1` on the assistant handle states that every frame is
  forwarded as written. Two consequences worth knowing at 3am:
  - Once the header is flushed there is no status left to set. A mid-turn
    failure is an `error` FRAME on a **200**, not a 502. Every rejection that
    still carries a status (`requireRouteAccess`, `enforceDemoAiQuota`, the
    empty-turn guard) runs BEFORE the stream opens.
  - A stream that ends with no `done` frame is a failure. The client renders
    "The connection dropped and the turn did not finish." — never an empty
    assistant bubble.
- **Publishing:** as with every edge change, the Caddyfile is NOT shipped by
  the CI deploy. After merge, on the VPS as `radon`:
  `sudo -n /usr/local/sbin/radon-deploy-root publish-caddy`.
- **Regression:** `cloud/tests/test_caddy_edge_timeouts.py::TestTheAssistantTurnOutlivesTheGenericGuard`
  (the assistant handle exists, outlasts the longest observed answered turn,
  stays inside the route's own `maxDuration`, never retries, and the generic
  30 s guard is untouched), `::TestTheAssistantHandleStatesItStreams` and
  `::TestTheAssistantStreamMechanism` (a real caddy carries a streaming turn's
  first byte through in well under the guard, with the site's `encode` in
  front of it), `web/tests/assistant-stream-route.test.ts` (start flushed
  before the loop resolves; heartbeats; error-as-frame; pre-stream statuses
  intact), `web/tests/assistant-stream-client.test.ts` (a truncated stream is
  an error, never an empty bubble) and `web/tests/assistant-timeout-copy.test.ts`
  (a 504 or 408 names the timeout instead of the generic error).

---

## signals-refresh-curl-timeout-pages-p1

**`radon-signals-refresh.service` oneshot pages P1 `Result=exit-code`
(`NRestarts=0`) on the hourly ET timer.** Recurs every RTH hour while
the unit stays failed.

- **Mechanism:** `run_signals_refresh.sh` POSTs `/theta-harvester/scan`
  then `/strength-confirmation/scan`. FastAPI `run_script` budgets are
  420s / 480s. BUG-013 set curl `-m 200` and treated any non-connect
  failure (curl 28, HTTP 502) as `return 1` with no fallback. Curl abort
  disconnects the request; Starlette cancels it; `run_script` SIGKILLs
  the scanner. The wrapper then starts the second scan. `Type=oneshot`
  has no `Restart=`, so `NRestarts=0` and `ActiveState=failed` until
  the next timer. Unit watchdog pages P1. Next hourly fire repeats.
- **Detection:** journal `FastAPI outcome indeterminate (curl=28, http=000)`
  then `Signals refresh finished with N failed scan(s)`;
  `systemctl show radon-signals-refresh.service -p Result,NRestarts`
  → `exit-code` / `0`. Edge and `:8321/health/lite` stay up.
- **Discriminating check:** curl 28 with http 000 (this case). Instant
  HTTP 502 with `Subprocess capacity exhausted` is
  `signals-refresh-capacity-502` (retry, `6093c087`). HTTP 502 with a
  scanner traceback is a real scan failure, still P1. `Result=signal`
  is deploy stop-clean. 09:00 ET skip (`Market closed`) is exit 0.
- **Remediation (code):** the 1050s unit exports `RADON_SIGNALS_SCAN_TIMEOUT=490`
  (curl `-m` >= max FastAPI child). Wrapper default stays 200 so an
  un-upgraded `TimeoutStartSec=450` host does not SIGTERM a live scan.
  After one `bootstrap-control-plane.sh` publishes the
  `sync-scheduled-units` verb, the next green CI deploy (or a same-SHA
  re-run) installs the unit. Then
  `systemctl reset-failed radon-signals-refresh.service`.
  Do not restart-flap; next timer after the sync.
- **Regression:**
  `test_run_signals_refresh_wrapper.py::test_wrapper_curl_deadline_covers_fastapi_scan_children`,
  `test_systemd_services.py::TestSignalsRefresh::test_oneshot_with_timeout`.
- **Code:** `scripts/run_signals_refresh.sh`
  (`RADON_SIGNALS_SCAN_TIMEOUT`, default 200),
  `cloud/services/radon-signals-refresh.service` (`TimeoutStartSec=1050`,
  `Environment=RADON_SIGNALS_SCAN_TIMEOUT=490`).

---

## divyield-yahoo-sweep-timeout

**`radon-divyield.service` oneshot pages P1 `Result=timeout` (`NRestarts=0`)
on the daily 22:40 UTC timer.** Peak: 2026-08-23 23:57Z, page `c52496dd…`.

- **Mechanism:** `fetch_divyield.py` sweeps ~503 S&P 500 Yahoo v8 charts
  with `ThreadPoolExecutor.map` (6 workers, 30s/request). The spec's
  healthy-path measurement was 15.7s, so `TimeoutStartSec=900` looked
  like slack. A slow Yahoo night (~20s/chart) needs ~28 min
  (`503/6*20`). `map()` waited out the tarpit; systemd SIGTERM'd at
  900s (`ExecMainStatus=15`). Nothing persisted. `Type=oneshot` has no
  `Restart=`, so `NRestarts=0` and `ActiveState=failed` until the next
  calendar fire (~22h). Unit watchdog pages P1. IB unused; weekend
  gateway-down is coincidental.
- **Detection:** journal `[div-yield] constituents: 503 tickers via
  github-datasets` then silence until systemd timeout;
  `systemctl show radon-divyield.service -p Result,NRestarts` →
  `timeout` / `0`; ExecMainStart to InactiveEnter is exactly
  `TimeoutStartSec`. Edge and `:8321/health/lite` stay up.
  `service_health.div-yield` may still be `ok` from an earlier
  same-day catch-up.
- **Discriminating check:** `Result=timeout` with a constituents log
  and no `quote sweep 100/503` (or a late one) in the same run. Yahoo
  chart latency on the host in the tens of seconds. `Result=signal` is
  deploy stop-clean (do not raise the budget). Zero constituents is a
  different class. If `/health/lite` is down too → API, stand down.
- **Remediation (code):** wall-clock `SWEEP_BUDGET_S=1800` with
  `wait(..., FIRST_COMPLETED)` so the process exits before systemd
  kills it; unfinished tickers count as `quote_errors` (the 80%
  degenerate guard still refuses a thin sweep). `TimeoutStartSec=2100`
  covers the budget plus one in-flight `FETCH_TIMEOUT_S`. Do not
  restart-flap on the hung code; after the fix deploys, one
  `radon unit restart radon-divyield`. Polkit cannot gain a new
  rerun grant in the same release (control-plane preflight).
- **Regression:**
  `test_divyield.py::TestSweepBudget::test_tarpitted_yahoo_stops_inside_the_wall_clock_budget`,
  `test_systemd_services.py::TestDivyieldScanBudget`,
  `test_page_responder.py::test_timeout_reruns_when_a_fix_deployed_after_the_failure`.
- **Code:** `scripts/fetch_divyield.py` (`SWEEP_BUDGET_S`, `sweep_yields`),
  `cloud/services/radon-divyield.service` (`TimeoutStartSec=2100`).

---

## bpi-yahoo-sweep-timeout

**`radon-bpi.service` oneshot pages P1 `Result=timeout` (`NRestarts=0`)
on the weekday 21:30 UTC fire.** Peak: 2026-08-24 23:30Z, page `bbaa065b…`.

- **Mechanism:** `bpi_scan.py --index all` spark-batches NDX/SPX/RUT
  member closes (20 symbols/chunk, sequential). Weekday 21:30 is a
  full-universe refetch. 2026-08-24: NDX 8 min, SPX 22 min, then RUT
  1996 members (~100 chunks at ~60s) still running when systemd
  SIGTERM'd at `TimeoutStartSec=6900` (21:31:31Z start → InactiveEnter
  23:26:31Z). `Type=oneshot` has no `Restart=`, so `NRestarts=0`.
  Unit watchdog pages P1. NDX+SPX had already upserted; RUT did not.
  Heartbeat sits outside the kill, so `bpi-scan` stays on the prior
  cycle. R-071 sized 6900 to unstick the 23:30 catch-up; that fire
  did start. Raising `TimeoutStartSec` would swallow it again.
- **Detection:** journal `history: 1996 members, fetching 1996` then
  silence until systemd timeout; `systemctl show radon-bpi.service
  -p Result,NRestarts` → `timeout` / `0`; ExecMainStart to
  InactiveEnter is exactly `TimeoutStartSec`. Edge and
  `:8321/health/lite` stay up. `service_health.bpi-scan` may still
  be `ok` from an earlier catch-up.
- **Discriminating check:** `Result=timeout` after an NDX/SPX persist
  and a RUT `fetching NNNN` line with no `spark wall-clock budget
  spent` / `RUT: Turso history`. `Result=signal` is deploy stop-clean
  (do not raise the budget). If `/health/lite` is down too → API,
  stand down.
- **Remediation (code):** process-wide `SWEEP_BUDGET_S=6600` shared
  across NDX+SPX+RUT. Spark chunks and chart-fallback
  `wait(..., FIRST_COMPLETED)` stop submitting at the deadline so the
  process exits, heartbeats, and leaves unfinished members to the
  23:30 / 11:00 catch-up. `TimeoutStartSec` stays 6900 (R-071).
  Do not restart-flap the hung run; after the fix deploys, one
  `radon unit restart radon-bpi` if the next timer is >12h out.
- **Regression:**
  `test_bpi_scan.py::TestSweepBudget`
  (`test_tarpitted_spark_stops_inside_the_wall_clock_budget`,
  `test_tarpitted_chart_fallback_stops_inside_the_wall_clock_budget`,
  `test_run_scan_passes_one_shared_deadline_to_every_index`,
  `test_sweep_budget_fits_inside_unit_start_timeout`).
- **Code:** `scripts/bpi_scan.py` (`SWEEP_BUDGET_S`,
  `_fetch_members_spark`, `_fetch_members`),
  `cloud/services/radon-bpi.service` (`TimeoutStartSec=6900`).

---

## leap-partial-ticker-exit-pages-p1

**`radon-leap.service` oneshot pages P1 `Result=exit-code` after a successful
largecaps scan.** Peak: 2026-08-20 14:15Z, page `99554c7a…`. Recurred
2026-08-18 and 2026-08-19 at the same 14:00 UTC timer.

- **Mechanism:** `leap_scanner_uw.py` treats `scan_ticker` returning `None`
  (no LEAP contracts, missing vol) and per-ticker exceptions as
  `failed_tickers`. A 518-name `largecaps` run routinely leaves ~170 of
  those. `main()` still wrote `data/leap.json`, mirrored the snapshot, and
  heartbeated `leap-scan` `ok`, then `return 1 if failed_tickers else 0`.
  FastAPI `run_script` mapped exit 1 to HTTP 502. The wrapper logged
  `FastAPI unreachable`, ran the scanner again, and the oneshot failed
  (`NRestarts=0`).
- **Detection:** journal `SCAN COMPLETE` + `Dashboard cache saved` +
  `LEAP fallback refresh FAILED (exit 1)` in the same second; radon-api
  `Script leap_scanner_uw.py failed (code 1)` then `POST /leap/scan` 502;
  `leap.json` has both `results` and `failed_tickers`; `leap-scan` health
  row is `ok`. Edge and `:8321/health/lite` stay up.
- **Discriminating check:** cache `len(results) > 0` and unit
  `Result=exit-code`. Zero results (empty cache preserved) is HB-013, still
  exit 1. `Result=signal` is deploy stop-clean. UW daily-quota text is the
  oi-changes class. If `/health/lite` is down too → API/IB, stand down.
- **Remediation (code):** exit 0 when at least one ticker produced a valid
  result; keep `failed_tickers` on the payload. Do not restart-flap; next
  timer or one `radon unit restart radon-leap` after the fix deploys.
- **Regression:**
  `test_leap_scanner.py::test_partial_ticker_failures_write_cache_and_exit_zero`,
  `test_all_provider_failures_preserve_cache_and_fail_health`.
- **Code:** `scripts/leap_scanner_uw.py` (`main` exit).

---

## leap-capacity-502

**`radon-leap.service` oneshot pages P1 `Result=exit-code` (`NRestarts=0`)
on an instant FastAPI 502 capacity shed at the 10:00 ET timer.** Peak:
2026-08-27 14:00:20Z, page `4e9ebc66…`.

- **Mechanism:** daily timer POSTs `/leap/scan?preset=largecaps`. At the
  top of the hour peer scanners fill the shared `run_script` lanes
  (hard cap 4 / lane cap 3). The POST returned instant HTTP 502 with
  journal `Subprocess capacity exhausted for leap_scanner_uw.py
  (3 active, lane cap 3, hard cap 4)`. `/health/lite` stayed 200 /
  authenticated. The wrapper (R-144) treated any non-exit-7 response as
  indeterminate, refused the direct fallback, and exited 1 once.
  `Type=oneshot` has no `Restart=`, so `NRestarts=0`. Next timer ~24h;
  `leap.json` stayed on the prior day. Same window's GARCH POST at
  14:02:02Z completed OK — the lane cleared within ~2 minutes.
- **Detection:** unit journal
  `LEAP FastAPI outcome indeterminate (curl=0, http=502)` in the same
  second as the POST; radon-api
  `Subprocess capacity exhausted for leap_scanner_uw.py`; ExecMainStart
  equals InactiveEnter (instant fail); `/health/lite` 200.
- **Discriminating check:** instant 502 with the capacity-exhausted
  body (this case). A long run that ends
  `Script leap_scanner_uw.py failed (code 1)` after `SCAN COMPLETE` is
  `leap-partial-ticker-exit-pages-p1`. `Result=signal` is deploy
  stop-clean. If `/health/lite` is down too → API/IB, stand down.
- **Remediation (code):** wait/retry HTTP 502/503 only when the body
  matches `subprocess capacity exhausted` (R-221), charged against
  `RADON_LEAP_SHED_WAIT_SECS` default 240 (fits under
  `TimeoutStartSec=3900` with the 3610s scan curl). Keep the
  no-duplicate rule for every other non-exit-7 outcome. Persistent shed
  after the wait still exits 1 (daily; next slot is tomorrow). After
  deploy, `reset-failed` + start (unit is on `RERUNNABLE_ONESHOT_UNITS`)
  or wait for the next timer.
- **Regression:**
  `test_leap_capacity_shed_retry.py::test_capacity_502_then_ok_retries_without_direct_fallback`,
  `test_script_failed_502_does_not_retry_as_shed`,
  `test_persistent_capacity_shed_no_duplicate_still_fails`.
- **Code:** `scripts/run_leap_refresh.sh`
  (`RADON_LEAP_SHED_WAIT_SECS`, `CAPACITY_SHED_MARKER`).

---

## knowledge-ingest-sqlite-busy

**`radon-knowledge.service` oneshot exits `Result=exit-code` on a single
transient Turso `SQLITE_BUSY` during the newsfeed (or other) source.**
Incident: 2026-08-15 00:24Z, P1 page `34ab3e3c…`.

- **Mechanism:** hourly `Type=oneshot` runs `ingest.py --source all`.
  Newsfeed paginates ~4.7k `posts` rows while concurrent writers
  (newsfeed-scraper, demo-newsfeed-mirror, other knowledge sources'
  upserts) hold Turso locks. One `database is locked` / `SQLITE_BUSY`
  aborted the source; `main()` raised `knowledge ingest failed for:
  newsfeed` and the unit failed with `NRestarts=0`. Other sources in
  the same run often succeeded. Python Turso canary stayed healthy
  (~600 ms) — not a platform outage.
- **Detection:** journald
  `[knowledge-ingest] newsfeed failed: Hrana: … SQLITE_BUSY`;
  `systemctl status radon-knowledge.service` → `Result=exit-code`;
  `service_health.knowledge-ingest` error row with
  `knowledge ingest failed for: newsfeed`. Edge and
  `:8321/health/lite` stay up.
- **Discriminating check:** Turso canary `SELECT 1` succeeds from the
  same host; journal shows only one source failed with lock/busy (not
  every source, not `stream not found` cascade after a dead singleton).
  If the canary fails too → Turso platform; stand down.
- **Remediation (code):** bounded retries per source on transient
  lock/stream markers, with `reset_connection()` so `get_db()` is a
  real fresh Hrana stream (the process singleton made the previous
  per-source `get_db()` a no-op). Do not restart-flap; next timer or
  one `radon unit restart radon-knowledge` after the fix deploys.
- **2026-08-21 17:22Z recurrence (page `e3268a38…`):** `_SOURCE_ATTEMPTS=2`
  retried newsfeed once; both attempts SQLITE_BUSY; incidents in the
  same run succeeded 6s later; Turso canary `SELECT 1` ~961 ms. Budget
  raised to 4. Persistent busy after the budget still fails the unit.
- **Regression:** `test_knowledge_pipeline.py::TestTransientDbRetry`
  (`test_source_retries_once_on_sqlite_busy`,
  `test_two_consecutive_sqlite_busy_then_success_exits_zero`,
  `test_exhausted_sqlite_busy_still_fails`,
  `test_sqlite_busy_is_classified_transient`,
  `test_non_transient_source_error_is_not_retried`).
- **Code:** `scripts/knowledge/ingest.py` (`_is_transient_db_error`,
  `_fresh_db`, `_SOURCE_ATTEMPTS=4`).

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
  - **(f2) UW daily quota on a oneshot (`oi-changes`, 2026-08-14 20:00Z):**
    same 40k cap, but `fetch_oi_changes.py --market` printed the error and
    `sys.exit(1)`. `Type=oneshot` + NRestarts=0 → unit watchdog P1.
    Discriminating check: journal `FAILED (exit 1)` in the same second as
    the start line; `logs/oi_changes.err.log` has the daily-limit text.
    14:01Z the same day was OK. Not IB, not Turso. Fix: catch the daily
    cap on the market-wide path, write `error` with `next_attempt_at` =
    20:00 ET, persist `data/oi_changes_uw_embargo.json`, keep the last
    snapshot, exit 0 so the unit does not page. Ticker eval lookups still
    exit 1. Other 429s still fail the oneshot.
    Regression: `test_fetch_oi_changes.py::test_market_uw_daily_quota_exits_zero_and_embargoes_until_reset`.
- **(g) data-refresh soft-fail silence (2026-08-28, `cri-scan`):**
  `radon-refresh.timer` runs `scripts.data_refresh` every 15m. `cri_scan.py`
  only heartbeats `service_health[cri-scan]` at successful completion. When
  the parent kills the child at the budget, no row is written; two consecutive
  kills freeze `updated_at` past the 35m open window and page stale
  (`silent for 43m`, page `441a4316…`, last ok 14:31Z, timeouts 14:45/15:00Z).
  Neighbouring cycles finished in 63-103s against the old 120s ceiling; IB
  pool stayed connected and vcg/gex completed in seconds. Discriminating
  check: journal `cri_scan.py timed out after Ns — keeping existing cri.json`
  with `Data refresh complete (cri: FAIL, …)` while `/health` `ib_pool` is
  connected. Not IB 2FA, not Turso. Fix: raise cri budget to 180s (still
  fits `TimeoutStartSec=480` with vcg/gex at 120s) and parent-heartbeat
  `error` + `next_attempt_at` on every soft-fail so `_check_stale` cannot
  page silence. Regression: `test_data_refresh.py::test_run_scan_timeout_heartbeats_cri_scan_error`,
  `test_cri_scan_budget_exceeds_observed_slow_path`.
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
- **OIDC consent is not a stand-down:** MenthorQ's WordPress→Cognito
  redirect keeps `client_id=aws_cognito_client_id` in the query and stays
  on `wp-login.php` with an **Authorize** submit (`input[name=authorize]`).
  Headless used to wait for `dashboard.menthorq.io` and time out. Click
  Authorize, then wait for landing. Regression:
  `test_menthorq_dashboard_bootstrap.py::test_clicks_oidc_authorize_consent_before_waiting_for_the_dashboard`
  (`d2d595e7`). A headed browser on that consent page is a valid remint,
  not proof the chain is unfixable.
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
- **2026-08-20 recurrence, different operator-visible failure (HTTP 504):**
  `menthorq-session` stayed ok (authjs cookies not expired) while
  `menthorq-login-probe` latched error at 06:03Z after ~65s:
  `Options exposure authentication is unavailable`. The probe's 90s
  read timeout can see FastAPI's 503. Next.js `OPTIONS_PROXY_TIMEOUT_MS`
  is 50s and `DEFAULT_LOGIN_TIMEOUT_SECONDS` was 60s, so `/options/net-gex`
  504'd (MEASUREMENT FAULT) before the 503 arrived. `_storage_state_expired`
  does not fire on a live-looking unspendable jar, so every Retry relaunched
  chromium then the 60s bootstrap. Fix: cap request-path login at 25s
  (`REQUEST_PATH_AUTH_BUDGET_SECONDS` 40s, still under the 50s proxy) and a
  process-wide 300s auth-failure embargo so a new FastAPI client per request
  cannot re-pay the budget. First call still bootstraps (self-heal).
  Discriminating check: probe 503 in ~60s + session ok + UI 504 in 50s.
  Timeout/embargo stop the 504; they do not restore the ladder. Remint is
  the Authorize click (or a headed export of the jar to
  `data/menthorq_dashboard/menthorq_dashboard_storage_state.json` on the
  VPS, then restart `radon-api` to clear the 300s embargo). Regression:
  `test_menthorq_dashboard_client.py::test_request_path_auth_budget_fits_inside_next_proxy`
  and `TestAuthFailureEmbargo`.
- **2026-08-20 remint (headed Chrome):** VPS headless filled WordPress then
  timed out on `wp-login.php?client_id=aws_cognito_client_id`. Chrome Debug
  was already on OIDC consent ("Do you want to log in to Quin By MenthorQ").
  Click Authorize → `dashboard.menthorq.io/en/options/exposure`. Export
  cookies to the dashboard jar (mode 0600), install on the VPS, restart
  `radon-api`. Live check: `GET /options/exposure/GLD?frequency=eod` 200
  with `complete: true`. CTA jar (`data/menthorq_cache/`) is a different
  login; do not copy it over the dashboard jar.

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
  goes through `ib_2fa_lock.py` (`/var/lib/radon/ib-lease/ib-2fa-push-lock.json`). Use the
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
- **Weekend dwell escalation → false `aggregate_down` (2026-08-30, page
  `a45d6410`):** R-382 applied the 900s sidecar dwell to every
  `DEPENDENCY_UNIT`, including `radon-ib-gateway`. Weekend clean-exit
  (`inactive`/`dead`, `Result=success` at 20:28Z) plus a 21:41 deploy
  that reset healthd's in-process dwell: at t+15m the aggregate flipped
  `degraded` → `down` and off-box paged P1 while ping and `/sign-in`
  stayed 200 and api/relay/nextjs stayed `up`. Discriminating check:
  market closed + serving path up + gateway `Result=success` +
  `ConnectionRefusedError` → stay `degraded`. Dwell still escalates
  newsfeed/monitor. Regression:
  `test_rel135_dependency_dwell.py::test_broker_weekend_clean_exit_does_not_escalate_past_dwell`.
- **Weekend vol-cone "outage" banner (2026-08-31, `e7323e4e`):** `/api/vol-cone`
  carried a private 48h `MAX_AGE_MS` while both vol-cone writers are Mon-Fri
  only, so from Sunday ~20:45 UTC Friday's healthy EOD snapshot collapsed to
  `missing:true` and the tab rendered the R-245 outage banner; the watchdog's
  own `vol-cone` window still read the writer as `ok`. Discriminating check:
  newest `scan_snapshots` row for `vol-cone` is Friday 20:45 UTC and
  `service_health` `vol-cone` is `ok` → not an outage. Fix pattern (R-450):
  the route shares `getFreshnessWindowMs("vol-cone", "closed")` (4d) with the
  watchdog catalog; a session-gated writer never gets a route-private budget.
  Regression: `web/tests/vol-cone-api.test.ts` "serves a weekend-aged snapshot
  (60h) instead of collapsing to missing".
- **Weekend deploy → false `status_http_502` P1 (2026-08-29, page
  `d98c3364`):** off-box sampled `/edge-health/status` HTTP 502 at
  16:45Z while user_path + freshness stayed green; deploy runners were
  active 16:42–16:52 (healthd restart collateral). Aggregate was
  `degraded` (IB weekend clean-exit). Deploy-window 5xx suppression
  required `_local_aggregate_is_healthy` (`overall_state=up`), so the
  weekend degraded state re-armed P1. Discriminating check: probe
  history `status_http_502` with sibling user_path/freshness ok +
  deploy-runner mtime inside the sample window + local serving path
  up (api/relay/nextjs) → suppress, do not restart. Fix: suppression
  now uses `_local_aggregate_serving_path_ok` (up OR dependency-only
  degraded). Regression:
  `test_deploy_window_5xx_with_dependency_only_degraded_is_suppressed`.
- **Sidecar unit flap → false "edge unhealthy" (2026-08-29, page
  `0b7726f8`):** `radon-newsfeed.service` Restart=always flaps
  (`NRestarts` in the dozens; `InactiveEnterTimestamp` within the same
  minute as the page) while api/relay/nextjs stay up. Newsfeed and monitor
  were counted as serving-path units, so the aggregate went `down`, the
  off-box row wrote `aggregate_down`, and continuous paged P1 edge
  unhealthy alongside the correct newsfeed unit page. Discriminating
  check: `https://app.radon.run/edge-health/ping` 200 + `/sign-in` 200 +
  `:8330/status` probes for api/relay/nextjs `up` + only
  `radon-newsfeed.service` / `radon-monitor.service` downish → NOT an edge
  outage. Those units are now `DEPENDENCY_UNITS` (degraded, not down);
  on-box continuous still pages the sidecar itself. Regression:
  `test_newsfeed_only_unit_down_degrades_instead_of_down`,
  `test_monitor_only_unit_down_degrades_instead_of_down`,
  `test_newsfeed_unit_down_payload_keeps_edge_ok`.
- **Sidecar activating → false aggregate_down (2026-08-29, page
  `344f0592`):** same newsfeed Restart=always storm (NRestarts=105,
  InactiveEnter 06:49:29Z) but sampled in `activating` ("starting"),
  not `down`. 35071d85 only reclassified sidecar *down*; the starting
  check still scanned every unit, so the aggregate stayed `starting`
  and the off-box probe mapped that to `aggregate_down` while
  api/relay/nextjs and the public edge (`/edge-health/ping` 200,
  `/sign-in` 200) stayed up. Sidecar-only starting is now `degraded`
  (same as sidecar down). Serving-path starting stays `starting`.
  Local watchdog recovery from a stale off-box `aggregate_down` now
  accepts schema-v2 `degraded` as well as `up`. Regression:
  `test_newsfeed_only_unit_starting_degrades_instead_of_starting`,
  `test_monitor_only_unit_starting_degrades_instead_of_starting`,
  `test_nextjs_unit_starting_stays_starting`,
  `test_newsfeed_unit_starting_payload_keeps_edge_ok`,
  `test_local_degraded_aggregate_clears_fresh_aggregate_down`.

---

## newsfeed-container-playwright-missing

**`radon-newsfeed.service` crash-loops `Result=exit-code` after the
container cutover because Chromium is not at the path Playwright
expects.** Peak: 2026-08-29 08:45Z, page `3e952746…`, NRestarts=21.

- **Mechanism:** the runtime-container drop-in `docker run`s
  `node scripts/newsfeed/index.js` from the node image. Image ENV is
  `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`. `Dockerfile.node` ran
  `bun x playwright install` from WORKDIR `web/`, which is not the
  repo-root `playwright@1.59.1` the scraper imports
  (`scripts/newsfeed/browser.js`). Runtime launch looked up
  `/ms-playwright/chromium_headless_shell-1217/...` and threw
  `Executable doesn't exist`. `runForever` exits after 3 consecutive
  cycle failures; `Restart=on-failure` / `RestartSec=30` loops every
  ~5 min and never hits `StartLimitBurst=5`. Host cache already had
  revision 1217 at `/home/radon/.cache/ms-playwright`. Sibling app
  units stayed `NRestarts=0`. Edge and `:8321/health/lite` stayed up.
- **Detection:** unit `NRestarts` climbing, `SubState=auto-restart` in
  two consecutive watchdog cycles; Turso `newsfeed-scraper` error
  `newsfeed pre-cycle failure: browserType.launch: Executable doesn't
  exist at /ms-playwright/chromium_headless_shell-1217/...`;
  `posts.json` frozen at the last host-runtime cycle.
- **Discriminating check:** health row names `/ms-playwright` and
  `Executable doesn't exist`; host
  `~/.cache/ms-playwright/chromium_headless_shell-1217` is present;
  `/health/lite` authenticated. `Result=signal` is deploy stop-clean.
  A themarketear `page.goto` timeout is a different class (cycle
  error, not launch). If `/health/lite` is down too → API/IB, stand
  down.
- **Remediation (code):** wrapper bind-mounts the host Playwright
  cache onto `/ms-playwright` and overlays `scripts/newsfeed` from the
  live checkout so `--no-sandbox` applies before the next image.
  Dockerfile installs via `./node_modules/.bin/playwright` from
  `/home/radon/radon`. After deploy, `refresh-control-plane` updates
  the wrapper; the unit picks up the mount on the next RestartSec.
- **Regression:**
  `test_app_runtime.py::test_run_newsfeed_mounts_host_playwright_browsers`,
  `test_app_images.py::TestNodeImage::test_playwright_install_uses_repo_root_binary`,
  `web/tests/newsfeed-scraper.test.ts` (`launches chromium without a
  sandbox when PLAYWRIGHT_CHROMIUM_SANDBOX=0`).
- **Code:** `cloud/scripts/radon-app-runtime.sh`,
  `docker/app/Dockerfile.node`, `scripts/newsfeed/browser.js`.

---

## portfolio-sync-unit-502

**`radon-portfolio-sync.service` oneshot pages P1 on a single transient 502
while FastAPI and IB are up.** Peak: 2026-08-14 17:00:00Z, RTH, exit 22.

- **Mechanism:** the every-minute wrapper POSTs `/portfolio/sync`. FastAPI
  caps concurrent `run_script` at 4. Top-of-hour scanners (theta, vcg,
  regime, strength, breadth, gex, cri) claim the slots first. The next
  claim returns `Subprocess capacity exhausted` with no journal line;
  `/portfolio/sync` maps that to HTTP 502 instantly (`/health` stays 200).
  `curl -f` exits 22. The wrapper only retried curl exit 7 (connection
  refused), so the oneshot failed and the unit watchdog paged. NRestarts=0
  is normal for `Type=oneshot`.
- **Discriminating check:** journal `POST /portfolio/sync` 502 in the same
  second as the POST, `/health` 200, no `radon.subprocess WARNING Script
  ib_sync.py failed`, other :00 scans 200 a few seconds later. Curl err
  `The requested URL returned error: 502`. A real IB miss logs a
  subprocess warning and takes ~6–10 s. Deploy `stop-clean` is a different
  class (API inactive, exit 7 after retries).
- **Remediation:** retry HTTP 502/503 with the same bounded budget as exit
  7 (`scripts/run_portfolio_refresh.sh`). Log the slot-cap reject so the
  next page is diagnosable. Persistent 502 still fails the unit.
- **Regression:** `test_run_portfolio_refresh_retry.py::TestDeployWindowRetry::test_http_502_then_ok_retries`,
  `test_http_503_then_ok_retries`, `test_http_400_does_not_retry`,
  `test_persistent_502_still_fails_with_exit_22`;
  `test_route_abuse_controls.py::test_subprocess_budget_logs_exhaustion`.

---

## demo-mirror-scan-snapshots-502

**`radon-demo-mirror.service` oneshot pages P1 `Result=exit-code` on a single
transient Turso HTTP 502 reading `scan_snapshots`.** Peak: 2026-08-21
21:45:00Z, page `ba86fe0a…`.

- **Mechanism:** weekday 21:45 UTC oneshot mirrors market-analytics tables
  prod → demo. The `scan_snapshots` per-service window
  (`ROW_NUMBER() OVER (PARTITION BY service …)`) is the slowest source read
  (~30 s on a healthy host). A single Turso `SERVER_ERROR: … HTTP status 502`
  during that read was logged as `SKIP scan_snapshots` and treated as a
  required-table failure; sibling tables (scanner, gex, cri, breadth, …)
  mirrored fine in the same run. `NRestarts=0` is normal for `Type=oneshot`.
  Edge and `:8321/health/lite` stayed up; Turso canary / other table reads
  succeeded — not a platform outage.
- **Discriminating check:** journal
  `SKIP scan_snapshots (source read failed: SERVER_ERROR: Server returned
  HTTP status 502)` then `FAILED: required table failures: scan_snapshots`;
  other `[mirror] <table>: N row(s)` lines in the same run; canary
  `SELECT count(*) FROM scan_snapshots` succeeds from the same host.
  Deploy stop-clean is `Result=signal`. Full-DB canary failure → Turso
  platform; stand down.
- **Remediation (code):** bounded retries on transient Turso source/dest
  errors (`MIRROR_MAX_ATTEMPTS`, default 3), same class as the newsfeed
  demo mirror. Persistent 502 still fails the unit. Do not restart-flap;
  next timer (Mon–Fri 21:45 UTC) or one `radon unit restart
  radon-demo-mirror` after the fix deploys.
- **Regression:** `scripts/lib/demoMirrorReliability.test.js` market suite
  (`retries a transient scan_snapshots source 502 then mirrors`,
  `still fails the unit when scan_snapshots 502 persists past the budget`,
  `does not retry non-transient SQL errors`);
  `test_demo_seed_guard.py::test_market_mirror_retries_transient_turso_502`.
- **Code:** `scripts/db/mirror_market_snapshots_to_demo.js`
  (`isTransientTursoError`, `runMarketMirror`).

---

## media-backup-b2-connection-closed

**`radon-media-backup.service` oneshot pages P1 `Result=exit-code`
(`NRestarts=0`) on a single B2 `ConnectionClosedError` during PUT.** Peak:
2026-08-29 10:17Z, page `02ccb70e…`.

- **Mechanism:** daily 10:15 UTC oneshot walks `/var/lib/radon/media` and
  uploads size-new files to B2 prefix `media/`. `_s3_client` had no botocore
  timeouts or retries (unlike `db_backup.py` on the same bucket). One
  `ConnectionClosedError` on PUT of a single PNG aborted the loop; remaining
  planned files were never attempted. `Type=oneshot` has no `Restart=`, so
  `NRestarts=0` until the next calendar fire (~24h). Unit watchdog pages P1.
  Same-host `radon-db-backup` had already uploaded 31 dumps to that bucket
  ~1h earlier; credentials exist. Not fail-closed (missing `RADON_ARCHIVE_S3_*`).
- **Detection:** journal
  `media-backup failed: ConnectionClosedError: Connection was closed before
  we received a valid response from endpoint URL:
  "https://s3.us-west-004.backblazeb2.com/radon-archive/media/<file>.png"`;
  `systemctl show … -p Result,NRestarts` → `exit-code` / `0`; ExecMainStart
  to InactiveEnter is a few seconds. Edge and `:8321/health/lite` stay up.
  Prior nights `ok: true` with `uploaded` in the dozens.
- **Discriminating check:** `ConnectionClosedError` / `connection was closed`
  on a PUT URL under `media/` (this case). Fail-closed missing-creds text is
  ops (secret). `Result=signal` is deploy stop-clean. If `/health/lite` is
  down too → API, stand down. B2 platform outage (every object, db-backup
  also red) → stand down.
- **Remediation (code):** botocore `Config` with
  `connect_timeout=30` / `read_timeout=300` / `retries max_attempts=3 mode=standard`
  plus application-level retry on transient transport errors so an injected
  client (and a client whose retries are already spent) still retries the
  file and continues the rest of the plan. Persistent closed-connection
  still exits 1. Do not restart-flap; next timer (10:15 UTC) or one
  `radon unit restart radon-media-backup` after the fix deploys. Unit is
  not on `RERUNNABLE_ONESHOT_UNITS`.
- **Regression:**
  `cloud/tests/test_media_backup.py::TestTransientB2UploadRetry`
  (`test_connection_closed_on_first_png_retries_and_uploads_the_rest`,
  `test_persistent_connection_closed_still_fails_the_unit`,
  `test_non_transient_upload_error_is_not_retried`,
  `test_s3_client_uses_bounded_standard_retries`).
- **Code:** `cloud/scripts/media_backup.py`
  (`_s3_client`, `call_s3_with_retry`, `is_transient_s3_error`, `upload_file`).

---

## db-backup-b2-connection-closed

**`radon-db-backup.service` oneshot pages P1 `Result=exit-code`
(`NRestarts=0`) on a single B2 `ConnectionClosedError` during PUT of a
~576 MB dump.** Peak: 2026-08-29 20:35Z, page `29c8a560…`. Recurred
20:28Z and 20:34Z; a third start at 20:41Z uploaded 3/3.

- **Mechanism:** daily 09:00 UTC oneshot dumps Turso then uploads
  missing `*.sql.gz` to B2 prefix `db_backups/`. `_s3_client` already
  had botocore `Config` retries (`max_attempts=3`). `sync_offbox`
  called `upload_file` once with no application-level retry, so a
  spent-retry `ConnectionClosedError` on a 576 MB multipart PUT aborted
  the off-box leg. Local gzip had already landed. `Type=oneshot` has no
  `Restart=`, so `NRestarts=0` until the next calendar fire (~12h at
  page time). Unit watchdog pages P1. Credentials exist; later same-hour
  run uploaded three dumps. Not fail-closed (missing `RADON_ARCHIVE_S3_*`).
- **Detection:** journal
  `db-backup: dumped 100 tables / …; b2 FAILED: ConnectionClosedError:
  Connection was closed before we received a valid response from
  endpoint URL: "https://s3.us-west-004.backblazeb2.com/radon-archive/db_bac…"`
  then `service_health row written: db-backup = error`;
  `systemctl show … -p Result,NRestarts` → `exit-code` / `0`. Edge and
  `:8321/health/lite` stay up.
- **Discriminating check:** `ConnectionClosedError` / `connection was
  closed` on a PUT URL under `db_backups/` (this case) after a dump line
  with table/row counts. Fail-closed missing-creds text is ops (secret).
  `Result=signal` is deploy stop-clean. If `/health/lite` is down too →
  API, stand down. B2 platform outage (every object, media-backup also
  red) → stand down.
- **Remediation (code):** application-level retry on transient transport
  errors around LIST / PUT / HEAD / DELETE so an injected client (and a
  client whose botocore retries are already spent) still retries the
  dump and continues the rest of the plan. Persistent closed-connection
  still exits 1. Local dump remains the critical path. Do not
  restart-flap; next timer (09:00 UTC) or one
  `radon unit restart radon-db-backup` after the fix deploys. Unit is
  not on `RERUNNABLE_ONESHOT_UNITS`.
- **Regression:**
  `cloud/tests/test_db_backup_offbox.py::TestTransientB2UploadRetry`
  (`test_connection_closed_on_first_dump_retries_and_uploads_the_rest`,
  `test_persistent_connection_closed_still_fails_the_unit`,
  `test_non_transient_upload_error_is_not_retried`).
- **Code:** `cloud/scripts/db_backup.py`
  (`call_s3_with_retry`, `is_transient_s3_error`, `sync_offbox`).

---

## demo-mirror-schema-lag

**`radon-demo-mirror.service` oneshot pages P1 `Result=exit-code` with
`no such table` on a newly mirrored market table.** Peak: 2026-08-26
21:46:08Z, page `01b4bac1…`.

- **Mechanism:** weekday 21:45 UTC oneshot mirrors market-analytics tables
  prod → demo. Prod schema advances via `radon-api` `ExecStartPre=migrate.py`.
  The demo Turso is a separate DB and was never on that path, so it stayed at
  `schema_migrations` max=26 while prod reached 57. Commit `3b2945b7` added
  `equibles_13f_snapshots` + `equibles_filing_forensics` to `PER_KEY`; the
  dest write failed with `SQLITE_UNKNOWN: no such table`, sibling tables
  (scanner, gex, cri, breadth, …) mirrored fine, unit exited 1 /
  `NRestarts=0`. Edge and `:8321/health/lite` stayed up — not a Turso
  platform outage.
- **Discriminating check:** journal
  `FAIL equibles_* (dest write failed: … no such table: equibles_*)` then
  `FAILED: required table failures: equibles_13f_snapshots,
  equibles_filing_forensics`; other `[mirror] <table>: N row(s)` lines in
  the same run; demo canary
  `SELECT MAX(version) FROM schema_migrations` far behind prod / missing
  the new table. Transient Turso 502 is
  `demo-mirror-scan-snapshots-502`. Deploy stop-clean is `Result=signal`.
- **Remediation (code):** `migrate.py --demo` applies
  `scripts/db/migrations` to `TURSO_DEMO_*` (refuses the prod marker;
  requires `radon-demo`). `radon-demo-mirror.service` runs it as
  `ExecStartPre` before the Node mirror. The unit is on
  `RERUNNABLE_ONESHOT_UNITS` so the page responder can
  `reset-failed` + `start` after a green deploy (next timer is ~24h).
  Manual: `python3.13 scripts/db/migrate.py --demo` then start the unit.
- **Regression:** `test_migrate.py::TestMigrateDemoTarget`;
  `test_systemd_services.py::TestDemoMirrorSchemaGate`.
- **Code:** `scripts/db/migrate.py` (`resolve_target`,
  `apply_pending_migrations`, `--demo`);
  `cloud/services/radon-demo-mirror.service`.

---

## signals-refresh-capacity-502

**`radon-signals-refresh.service` oneshot pages P1 `Result=exit-code` when
both Top-candidates scans hit the FastAPI subprocess slot cap.** Peak:
2026-08-21 14:00:00Z, page `11e2b093…`.

- **Mechanism:** hourly ET timer POSTs `/theta-harvester/scan` then
  `/strength-confirmation/scan`. At `:00` many scanners claim the shared
  `run_script` lanes (hard cap 4 / lane cap 3). Both POSTs returned
  instant HTTP 502 with journal
  `Subprocess capacity exhausted … (3 active, lane cap 3, hard cap 4)`.
  `/health/lite` stayed 200 / authenticated. The wrapper treated any
  non-exit-7 response as indeterminate (BUG-013: no duplicate direct
  scan) and exited 1 with `NRestarts=0`. A manual POST ~90s later
  succeeded and refreshed `theta_harvester.json`.
- **Discriminating check:** unit journal
  `FastAPI outcome indeterminate (curl=0, http=502)` for both labels in
  the same second; radon-api
  `Subprocess capacity exhausted for theta_harvester_scanner.py` /
  `strength_confirmation_scanner.py`; `/health/lite` 200. Script-failed
  502 (leap / UW quota) logs `Script … failed (code 1)` and takes
  seconds. Deploy stop-clean is `Result=signal`.
- **Remediation (code):** retry HTTP 502/503 with the same bounded budget
  as portfolio-sync (`RADON_SIGNALS_REFRESH_RETRIES`, default 2,
  `RADON_SIGNALS_REFRESH_RETRY_DELAY_SECS` default 8). Keep the no-duplicate
  rule after the budget. Persistent 502 still fails the unit.
- **Regression:**
  `test_run_signals_refresh_wrapper.py::test_http_502_then_ok_retries_without_direct_fallback`,
  `test_http_503_then_ok_retries`, `test_http_500_does_not_retry`,
  `test_persistent_502_still_fails_without_direct_fallback`.
- **Code:** `scripts/run_signals_refresh.sh`.

---

## flow-refresh-capacity-502

**`radon-flow-refresh.service` oneshot pages P1 `Result=exit-code` when
all three flow-tab POSTs hit the FastAPI subprocess slot cap.** Peak:
2026-08-24 19:00:00Z, page `304b0d7f…`.

- **Mechanism:** hourly ET `:00` timer POSTs `/scan?force=true`,
  `/flow-analysis?force=true`, `/discover?force=true`. At the top of the
  hour peer scanners (breadth, portfolio-sync, vcg, regime) claim the
  shared `run_script` lanes (hard cap 4 / lane cap 3). All three POSTs
  returned instant HTTP 502 with journal
  `Subprocess capacity exhausted … (3 active, lane cap 3, hard cap 4)`.
  `/health/lite` stayed 200 / authenticated. The wrapper treated any
  non-exit-7 response as indeterminate (BUG-013: no duplicate direct
  scan) and exited 1 with `NRestarts=0`. Capacity cleared ~26s later.
- **Discriminating check:** unit journal
  `FastAPI outcome indeterminate (curl=0, http=502)` for scanner /
  flow-analysis / discover in the same second; radon-api
  `Subprocess capacity exhausted for scanner.py` /
  `flow_analysis.py` / `discover.py`; `/health/lite` 200. Script-failed
  502 logs `Script … failed (code 1)` and takes seconds. Deploy
  stop-clean is `Result=signal`. Market-closed skip is exit 0.
- **Remediation (code):** same class as `signals-refresh-capacity-502`
  / R-170. Retry HTTP 502/503
  (`RADON_FLOW_REFRESH_RETRIES` default 2,
  `RADON_FLOW_REFRESH_RETRY_DELAY_SECS` default 8) charged against the
  per-scan wall budget. Persistent shed exits 0 (`SHED_EXIT=75`) so the
  unit watchdog does not page P1 hourly for a full lane; the next slot
  retries. Keep the no-duplicate rule. Real failures still exit 1.
  `SuccessExitStatus=75` on the oneshot makes a leaked 75 inactive, not
  failed (R-067). The unit watchdog pages P1 once per
  `InactiveEnterTimestamp` for Type=oneshot `Result=exit-code`; the same
  timestamp is P3 digest (next timer retries). Type=simple failed stays P1.
- **Regression:**
  `test_run_flow_refresh_wrapper.py::test_http_502_then_ok_retries_without_direct_fallback`,
  `test_http_503_then_ok_retries`, `test_http_500_does_not_retry`,
  `test_persistent_502_sheds_without_direct_fallback`,
  `test_flow_refresh_oneshot_contract.py`,
  `test_watchdog/test_units.py::TestOneshotExitCodeLatch`.
- **Code:** `scripts/run_flow_refresh.sh`,
  `cloud/services/radon-flow-refresh.service`, `scripts/watchdog/units.py`.
- **Host:** CI deploy `install-units` (hash bumped). No control-plane
  bootstrap. `reset-failed` is not required after this SHA: a shed no
  longer enters failed, and a leftover latch is digest-only.

---

## orders-sync-capacity-shed-stale

**Autonomous `orders-sync` loop pages P1 `kind=stale` during RTH when
`ib_orders.py` is refused by the general subprocess lane and never
heartbeats.** Peak: 2026-08-24 19:30Z, page `60096761…`, 19m silent
(window 10m) while market open.

- **Mechanism:** FastAPI's 5-min `_orders_sync_loop` spawns
  `ib_orders.py --sync` (general lane, cap 3). Scan storms (gex, vcg,
  cri, breadth, portfolio-sync) pin all 3 slots. The tick logs
  `Subprocess capacity exhausted` and returns; `service_cycle` lives
  inside the unspawned script, so `updated_at` freezes. Two consecutive
  sheds trip the 10-min stale window. `/health/lite` stays 200 /
  authenticated. `ib_orders.py` is not on the reserved order lane
  (R-048 is kill-switch / place / manage / cancel only).
- **Discriminating check:** radon-api journal
  `orders-sync loop: running ib_orders.py --sync` then
  `Subprocess capacity exhausted for ib_orders.py (3 active, lane cap 3,
  hard cap 4)` at 5-min cadence; `service_health.orders-sync` `state=ok`
  with `updated_at` older than 10m; `/health/lite` authenticated;
  fill-monitor / portfolio-sync / relay still fresh. Grouped IB 2FA /
  unreachable is a different class. Deploy stop-clean is
  `Result=signal` on units, not this loop.
- **Remediation (code):** retry capacity shed
  (`ORDERS_SYNC_SHED_RETRIES=2`, `ORDERS_SYNC_SHED_RETRY_DELAY_SECS=8`).
  Persistent shed heartbeats `ok` over `api.db_http` (R-170: lane full
  is not a writer fault) so `_check_stale` cannot page; the next 5-min
  tick retries the sync. Real IB/script misses still do not stamp ok.
  Do not put `ib_orders.py` on the reserved order lane.
- **Regression:**
  `test_orders_sync_loop.py::test_orders_sync_tick_retries_capacity_shed_then_succeeds`,
  `test_orders_sync_tick_persistent_capacity_shed_heartbeats_ok`,
  `test_orders_sync_tick_real_failure_does_not_skip_heartbeat`.
- **Code:** `scripts/api/server.py` (`_orders_sync_tick`,
  `_heartbeat_orders_sync_skip`).

---

## flow-report-ticker-capacity-502

**Operator `/flow-analysis/{TICKER}` ANALYZE returns instant HTTP 502
`Subprocess capacity exhausted` while the hero stays on ANALYZING.** Peak:
2026-08-27, JOBY.

- **Mechanism:** `POST /flow-analysis/{ticker}` runs `flow_report.py` on the
  general `run_script` lane (hard cap 4 / lane cap 3). Hourly scans and the
  sibling `GET /informed-flow/{ticker}` spawn pin the lane. `_claim_subprocess_slot`
  is fail-fast, so ANALYZE 502s in milliseconds. `/health/lite` stays 200.
  The ticker hook then sets `status=error` with no cached report; SignalBadge
  treated error-without-verdict as ANALYZING and rendered the raw
  `Radon API 502: …` string.
- **Discriminating check:** UI `Radon API 502: Subprocess capacity exhausted`
  under an ANALYZING hero; radon-api
  `Subprocess capacity exhausted for flow_report.py (3 active, lane cap 3,
  hard cap 4)`; informed-flow panel still populated; `/health/lite` 200.
  Script-failed 502 logs `Script flow_report.py failed (code 1)` and takes
  seconds. Portfolio-tab `POST /flow-analysis` cooldown path is
  `flow-refresh-capacity-502`.
- **Remediation (code):** retry capacity shed on the ticker POST
  (`FLOW_REPORT_SHED_RETRIES` default 2,
  `FLOW_REPORT_SHED_RETRY_DELAY_SECS` default 8), same budget as
  orders-sync. Persistent shed still 502. UI maps the capacity string to
  operator copy and shows `Scan failed` instead of ANALYZING.
- **Regression:**
  `test_flow_report_capacity_shed.py::test_flow_report_retries_capacity_shed_then_succeeds`,
  `test_flow_report_persistent_capacity_shed_still_502`,
  `test_flow_report_real_script_failure_does_not_retry`,
  `web/tests/flow-report-capacity-error.test.tsx`,
  `web/e2e/flow-analysis-ticker.spec.ts` (`capacity 502 shows scan failed`).
- **Code:** `scripts/api/server.py` (`_run_script_retrying_capacity`,
  `run_flow_report`); `web/lib/flowReportError.ts`;
  `web/components/flow-analysis/TickerFlowReport.tsx`.

---

## mktnews-upstream-ws-refused

**Symptom:** the headline tape keeps printing while the `service_health` row
`mktnews-hub` is `error` (banner, `/api/service-health`). Not a contradiction:
`radon-mktnews.service` (`scripts/mktnews/hub.js`) polls the vendor's flash
REST lane every `FLASH_POLL_MS` (60s) while the upstream WebSocket is down,
and keeps the row `error` on purpose because the WS outage still needs an
operator. 2026-08-30: the VPS dial was refused for 16+ minutes at the vendor's
Cloudflare edge while other networks connected fine.

**Discriminate** before touching the unit:
`journalctl -u radon-mktnews.service --since -30min | grep -E 'close|reconnect attempt|error|flash poll|idle'`

- `close <code>` + `reconnect attempt=N` repeating and `flash poll fed N
  print(s)` present → WS lane refused, REST lane alive. Vendor edge or VPS
  egress-IP problem, not the hub.
- No `flash poll` lines and no frames → both lanes down. Compare reachability
  of the vendor host from the VPS and from the laptop.
- `idle: no upstream frame for` → the socket was accepted but went silent;
  the hub terminates and redials by itself.

**Action:** a restart does not fix a refused dial (same egress IP, same edge).
Confirm the vendor is reachable off-net. If only the VPS is blocked the fix is
on the vendor-edge or egress-IP side, which is operator-only (external
console); nothing in the repo changes it. The row clears itself on
`upstream open`. Do not raise the `mktnews-hub` window to hide the state.

**Regression:** `scripts/mktnews/upstream-down-fallback.test.js` ("flash poll
fallback while the upstream WS is down", "clients admitted during an outage
are told the upstream is down"); `scripts/mktnews/rel155-upstream-liveness.test.js`.

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
`systemctl status radon-api` and journald; remember: a cleanly stopped unit
will NOT `Restart=always` back (`radon restart` respects the 2FA lock). A
Gateway stop or 2FA cycle does not take radon-api down (no `PartOf=` since
44e89e1b, `After=` ordering only), so api connection-refused after a Gateway
cycle is its own fault, not a cascade.

---

## flex-1025-lockout

**IBKR Flex code 1025 is a token lockout.** Routine ingest is sFTP
(`docs/flex-sftp-setup.md`). Do not SendRequest to recover. Use
`--from-file` or wait for `radon-flex-pull`. A persisted `class=permanent`
row plus a missing sidecar used to SendRequest at the next 08:00 ET window.

- **Mechanism:** 1025 is undocumented ("Too many failed attempts"), earned by
  retrying 1001. `75ded753` classified new 1025s as lockout (exit 15, 7-day
  sidecar). The live 2026-08-21 13:58Z row predates that commit
  (`class=permanent`, `next_attempt_at=2026-08-24T12:00:00Z`).
  `flex_embargo.active_until` used to read only `data/flex_token_embargo.json`,
  which a deploy wipes and which `record_lockout` never wrote for that row.
  `CashFlowSyncHandler.is_due` honored `daemon_state` `blocked_until` (the
  Monday 08:00 window). `/orders` POST `/api/blotter` → `journal_rehydrate`
  shares the token. Looking at the lozenge was itself a SendRequest.
- **Detection:** `service_health.cash-flow-sync` error with `code 1025` or
  "too many failed attempts"; lozenge `Do not retry`; `last_synced_at` days
  old; sidecar file absent on the host.
- **Discriminating check:** do NOT SendRequest. Read the row and the sidecar.

```
python3.13 -c "from utils.flex_embargo import active_until, is_blocked; print(is_blocked(), active_until())"
```

  Blocked through `last_attempt_finished_at + 7d` (2026-08-28T13:58:28Z for
  the 2026-08-21 row) even if `next_attempt_at` is the next 08:00 ET window.
  A 1012/config permanent row must not reconstruct as a 7-day lockout.
- **Remediation:** reconstruct the sidecar from Turso; do not probe Flex.
  Recover with `--from-file` or the sFTP inbox. Portal Run on `1442520` only.
  Do not set `IB_FLEX_FLOWS_QUERY_ID`.
- **Fix commits:** `75ded753` (new 1025s), plus the reconstruction commit
  that made a missing sidecar + `class=permanent` 1025 fail closed through
  `last_attempt+7d`.
- **Regression:** `scripts/tests/test_flex_token_embargo.py`
  (`test_missing_sidecar_reconstructs_live_1025_permanent_from_turso`),
  `scripts/tests/test_monitor_daemon/test_cash_flow_sync_exit_codes.py`
  (`TestLockoutReconstructedFromTurso`),
  `scripts/tests/test_cash_flows_route_last_synced.py`
  (`test_live_permanent_1025_next_attempt_is_lockout_deadline_not_monday`),
  `web/tests/cash-flows-sync-lozenge.test.tsx` (no `retry tomorrow` on lockout).

---

## watchdog-probe-dead

**The watchdog's own freshness probe answered definitively wrong** —
`RADON_PROBE_FRESHNESS_TOKEN` unset or rotated, a 401/403, or the
`/api/probe/freshness` route removed. Unlike a probe timeout (transient,
indeterminate, never an incident) this cannot self-heal, and while it persists
the watchdog is blind to market-data staleness.

**Fix:** restore the bearer token in the watchdog host's environment (or the
route), then confirm the next `--once` cycle reports `"freshness": "up"` —
the incident auto-resolves on that cycle. Incident resolution is scoped
per-incident to the probes that bear on it (`probes` in the incident JSON),
so open incidents observed by OTHER probes keep resolving while this one is
open (R-065: a dead probe must not latch the whole incident directory).

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

The `--once` (timer) path wraps its cycle in `service_cycle("incident-watchdog",
market_hours_class="continuous")`, so the prober itself is staleness-checked on
the `continuous` bucket and in `web/lib/serviceHealthWindows.ts` (15 min open
and closed, matching the 5-minute timer). It previously wrote no
`service_health` row at all and sat in neither catalog, so a wedged prober was
silent by nature (R-325). The heartbeat closes `ok` BEFORE the exit code is
set: an open P1 is a FINDING, not a watchdog failure, and recording `error`
whenever the tool did its job would make the row useless.

`__main__.py` prepends `scripts/` to `sys.path` (same as
`scripts/watchdog/__main__.py`) so the systemd `-m` invocation can resolve
`from db.service_cycle`. Without that, R-325 parks the oneshot
`Result=exit-code` every 5 minutes (`incident-watchdog-db-import-exit`).

It intentionally does NOT restart anything and does NOT page — remediation
stays with the dedicated units (`feedback_ib_auto_recovery_conservative`,
`feedback_watchdog_works_dont_deploy_autoheal`), and paging stays with
`scripts/watchdog`. This tool's job is evidence: a structured artifact a human
or `/incident` can act on.

---

## incident-watchdog-db-import-exit

**`radon-incident-watchdog.service` oneshot pages P1 `Result=exit-code`
(`NRestarts=0`) on the 5-minute timer after R-325.** Peak: 2026-08-28
16:05Z, page `05511a4f…`.

- **Mechanism:** R-325 wrapped `--once` in
  `from db.service_cycle import service_cycle`. systemd ExecStart is
  `python -m scripts.incident_watchdog --once` with
  `WorkingDirectory` = repo root and no `PYTHONPATH`. That puts the repo
  root on `sys.path`, not `scripts/`, so `db` is not a top-level package.
  `Type=oneshot` has no `Restart=`, so `NRestarts=0`. The 16:00 cycle
  still ran the pre-R-325 body (exit 0); the checkout fast-forwarded
  02133f8b at 16:02Z; 16:05 crashed. `scripts/watchdog/__main__.py`
  already documents this exact `-m` topology.
- **Detection:** journal
  `ModuleNotFoundError: No module named 'db'` at
  `scripts/incident_watchdog/__main__.py` `from db.service_cycle`;
  `systemctl show … -p Result,NRestarts` → `exit-code` / `0`. Edge and
  `:8321/health/lite` stay up. Prior cycle 5 min earlier was OK JSON.
- **Discriminating check:** traceback is ImportError on `db` at process
  start (ExecMainStart equals InactiveEnter). A probe/classify exception
  after a JSON summary is a different class. `Result=signal` is deploy
  stop-clean. If `/health/lite` is down too → API, stand down.
- **Remediation (code):** prepend `scripts/` to `sys.path` in
  `__main__.py` before the `db.service_cycle` import, same as
  `scripts/watchdog/__main__.py`. Do not add a unit `PYTHONPATH` as the
  only fix — the `-m` entry point must be self-contained. After deploy,
  next timer (5 min) recovers; `reset-failed` is not required once the
  next fire exits 0.
- **Regression:**
  `test_incident_watchdog.py::TestSystemdDashMImport::test_dash_m_once_resolves_db_without_pythonpath`.
- **Code:** `scripts/incident_watchdog/__main__.py`.

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

## preferences-operator-403

**Operator Save on `/preferences` returns 403 `Operator authorization required`.**
Peak incident: 2026-08-15, operator signed in as joemccann on app.radon.run,
`PUT /api/preferences` (`RADON_MAX_ORDER_NOTIONAL` → 1_000_000) 403.

- **Mechanism:** PUT/DELETE called the demo-trial admin helper
  (`DEMO_ADMIN_USER_IDS`). That env is unset on the operator deployment and
  default-denies everyone, including the `ALLOWED_USER_IDS` operator. GET
  still worked (signed-in check only), so the page rendered and Save failed.
- **Detection:** DevTools `preferences` request 403
  `{error: "Operator authorization required", code: "FORBIDDEN"}`. UI banner
  "Operator authorization required" under the edited row.
- **Discriminating check:** source of `web/app/api/preferences/route.ts`
  must contain `requireRouteAccess` + `operatorOnly: true` and must not
  import the demo-trial admin helper. Operator is in `ALLOWED_USER_IDS`.
- **Fix:** gate mutations on `ALLOWED_USER_IDS` via
  `requireRouteAccess({ operatorOnly: true })`. Demo-user management stays
  on the demo-trial admin helper.
- **Regression:** `web/tests/preferences-api.test.ts` production topology
  (allowlisted operator, blank demo-admin env), `web/tests/admin-page-gate.test.ts`,
  `web/tests/route-local-authz-matrix.test.ts`.

## performance-twr-payload-length

**`/performance` route error boundary: `TypeError: Cannot read properties of undefined (reading 'length')`.**
2026-08-15, app.radon.run/performance. TWR builder snapshots in Turso omit reconstruction-only arrays.

- **Mechanism:** `perf_twr_builder.py` writes `performance_snapshots` without `contracts_missing_history` (and often without `trades_source` / `price_sources`). Next.js GET serves that row. `PerformancePanel` / `MobilePerformancePanel` read `data.contracts_missing_history.length` after `warnings` is empty, then `trades_source.toUpperCase()`. Route error boundary.
- **Detection:** console `[radon] route error boundary: TypeError ... 'length'`; workspace "RUNTIME ERROR" on `/performance` only.
- **Discriminating check:** latest Turso `performance_snapshots.payload` has `methodology.curve_type == twr_modified_dietz_daily` and no `contracts_missing_history`. Reconstruction fixtures with that array still render.
- **Fix:** `normalizePerformanceData` fills missing arrays and derives NAV equity before either panel reads the payload. Builder now emits the same keys on new writes. Existing Turso rows stay valid through the adapter.
- **Regression:** `web/tests/performance-panel-twr-payload.test.tsx`, `web/tests/performance-twr.test.ts` normalize case, `web/e2e/performance-twr-payload.spec.ts`, `tests/test_portfolio_performance.py`.

## Grok auto-response on iPhone P1 pages

Canonical: [`grok-page-responder.md`](grok-page-responder.md).

The same watchdog send that buzzes Pushover on the iPhone writes a
`watchdog_pages` row (sanitized excerpt, one ticket per service/kind/UTC
hour). VPS `radon-grok-page-responder.timer` (`scripts/grok_page_responder.py`,
dedicated clone `/home/radon/radon-page-responder`) claims the row and
runs headless Grok with this playbook: diagnose, stand down when this
runbook says so, otherwise TDD and ship. A normal-priority `radon grok:`
follow-up reports the disposition. After `git push origin main` and a
green live gate, `deploy_notify.py` sends `radon deploy live`. Neither
follow-up is P1.

Laptop launchd is off. Kill switches: `GROK_PAGE_RESPONDER=0`,
`GROK_PAGE_AUTOSHIP=0` (diagnose only), `GROK_PAGE_AUTOPUSH=0`.
Install: `docs/grok-page-responder.md`.

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
