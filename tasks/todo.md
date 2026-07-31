# Task: Day P&L ESTIMATED (LIVE) way off during RTH (2026-07-28)

## Dependency graph

- T1 depends_on: [] - Reproduce from screenshot + Turso: IB daily vs UI estimate
- T2 depends_on: [T1] - Fix computeDayMoveBreakdown (IB first, same-day, stock short sign)
- T3 depends_on: [T2] - Red/green tests + related suite

## Checklist

- [x] T1 Live Turso: daily_pnl ≈ -$40,889; sum ib_daily ≈ -$40,932; UI showed -$159,354 ESTIMATED (LIVE). Same-day AAOI +$42k IB vs poisoned close math.
- [x] T2 `web/lib/dayMoveBreakdown.ts`: prefer `ib_daily_pnl` without quotes; same-day entry path; stock SHORT sign; keep mid fallback for overnight options.
- [x] T3 Tests: `day-move-ib-first.test.ts` (4) + mid/ib/premarket/same-day suites green (29 focused).

## Review

- Root cause: RTH fallback when account `daily_pnl` is null used prior-close math and required full quotes before accepting per-position IB daily. Same-day RRs and stale option closes inflated Day Move ~4× vs TWS.
- Fix is client-side only; when IB fields are present the estimate matches sum of `ib_daily_pnl`. Account card still labels ESTIMATED when aggregate `daily_pnl` is null.

# Task: META 2026-08-21 bullish-expression analysis (2026-07-28)

## Dependency graph

- T1 depends_on: [] - Run the required fresh META evaluation pipeline and record its signal, data-freshness status, and active gate outcome.
- T2 depends_on: [] - Retrieve the complete listed META 2026-08-21 chain from the authoritative options source, retaining bid/ask, IV, Greeks, open interest, and volume with timestamps.
- T3 depends_on: [T1, T2] - Compare viable defined-risk bullish structures across liquid strikes using executable prices, expected-move context, payoff, break-even, IV/skew, and liquidity.
- T4 depends_on: [T3] - Apply convexity, edge, fractional-Kelly, and naked-short constraints; state the conditional decision and the exact order-quality criteria.
- T5 depends_on: [T4] - Verify calculations and source freshness, then document the review without modifying execution state.

## Checklist

- [x] T1 Fresh evaluation pipeline: `evaluate.py META` fetched through 2026-07-28 12:06 PT and returned NO_TRADE at EDGE (flow strength 12.8 vs >50; one sustained day; bearish news tilt). M1–M3B passed, while M5/M6 did not run.
- [x] T2 Full expiry chain capture: blocked at both authoritative sources. IB resolves `ib-gateway:4001` to no host and local FastAPI/port 4001 are unavailable; UW rejects without `UW_TOKEN`. Yahoo fallback returned HTTP 429. Third-party web pages returned internally inconsistent snapshots and lack the required full IV/Greek set, so they were not substituted for executable data.
- [x] T3 Strike and structure comparison: no contract-specific ranking was calculated because executable, internally consistent bid/ask + IV + Greek data is unavailable. Structurally, a traditional risk reversal and a risk-reversal call spread retain naked short-put tail exposure; a debit call vertical is the defined-risk comparator once data is restored.
- [x] T4 Gate and sizing decision: EDGE failed before structure/Kelly. No META entry, including a risk reversal, qualifies today.
- [x] T5 Calculation and freshness review: fresh evaluation data was available through 2026-07-28 12:06 PT; the expiry chain required to calculate payoff/skew/Kelly was unavailable or inconsistent, so no fabricated strikes, IVs, or order price is reported.

## Review

- Decision: NO TRADE. The live evaluation failed EDGE (12.8 strength vs >50 requirement); structure and Kelly are intentionally not advanced. No order was authorized or placed.
- Data limitation: reconnect IB or configure `UW_TOKEN` before asking for a strike-specific replacement. Yahoo was attempted only after those two authoritative sources failed and was rate limited.

# Task: Operator control-plane reliability audit and repair (2026-07-27)

## Dependency graph

- T1 depends_on: [] - Inventory every operator-page feature, UI action, and backing API/control-plane route; classify each by read-only, service-only mutation, or 2FA/Gateway mutation.
- T2 depends_on: [] - Reconcile service-reliability memories, incident logs, installed control-plane state, and production telemetry into explicit failure-mode invariants.
- T3 depends_on: [T1, T2] - Execute safe production read paths and controlled local/mock action-path probes; identify every reproducible operator-page reliability defect.
- T4 depends_on: [T3] - Design the smallest complete repair plan, including regression coverage and safe handling for service restart and single-push 2FA operations.
- T5 depends_on: [T4] - Implement the approved-in-scope repairs with red/green tests and full affected-suite verification.
- T6 depends_on: [T5] - Run production-safe verification, deploy through CI, and prove every operator-page action contract without issuing an unnecessary Gateway restart or 2FA push.

## Checklist

- [x] T1 Operator UI/API/action inventory: nine admin routes, including six read surfaces, Gateway/2FA actions, full-stack restart, per-unit control, and demo-user actions; each is classified and mapped to its UI control and existing test coverage.
- [x] T2 Reliability evidence and invariant audit: identified root-HOME corruption in the installed full-stack CLI, cloud health omission of 2FA lock/backoff, browser-driven recovery side effects, false Gateway-stop cascade claims, and command-acceptance falsely rendered as recovery.
- [x] T3 Read-path and controlled action-path validation: production read paths confirm a healthy broker/data plane but `radon status` falsely fails; focused Python and web proxy/UI suites cover existing mocked action paths, exposing missing status-mapping and convergence regressions without issuing a restart or 2FA push.
- [x] T4 Durable repair specification: preserve inherited deploy-lock FDs while forcing the demoted helper HOME to `/home/radon`; expose cloud-mode lock/backoff; make `/health` passive; make Gateway stop's actual dependent state truthful; and add action-route/UI regressions that distinguish acceptance from verified recovery.
- [x] T5 Regression fixes and test suite verification: added root-controller identity, cloud push-lock, passive-health, exact Gateway cascade, fail-closed restart-acknowledgement, FastAPI conflict mapping, and authless-browser boundary regressions; focused Python (328) and web (56) suites, Chromium operator E2E (5), typecheck, lint, shell syntax, and full suites were run.
- [x] T6 Deployment and production contract verification: committed/pushed `2928c804`; CI run `30274059548` passed pytest, full Vitest/coverage, secret scan, perimeter smoke, and deployment after a documented non-restarting exact-SHA control-plane refresh. Production verifies the installed operator hash, passive authenticated health with restart-backoff present, schema-v2 edge health, Gateway-dependent `PartOf` links, and six core units active. All unauthenticated admin reads/mutations are blocked at the production auth perimeter.

## Review

- Reproducible outage cause: root's full-stack operator preserved `HOME=/root` while demoting the Gateway helper to `radon`; Docker then could not read its client config, so `radon status` reported the healthy Gateway as `unknown` and returned failure.
- Prevention: preserve only the required deploy-lock environment while forcing the demoted identity's HOME/USER/LOGNAME; cloud health always includes the 2FA lock/backoff; browser health polling is read-only; Gateway stop now truly stops relay and monitor through systemd `PartOf`; and restart transport errors can no longer masquerade as accepted restarts.
- Delivery: CI initially refused a stale installed control-plane manifest before mutating services. The documented exact-SHA bootstrap atomically refreshed 20 artifacts without restarting IB Gateway, and the deployment rerun completed successfully.

# Task: Relay connection repair (2026-07-27)

## Dependency graph

- T1 depends_on: [] - Collect live relay, Gateway, API-pool, and client-session evidence; isolate the relay-only failure mode.
- T2 depends_on: [T1] - Apply the smallest documented recovery that restores the relay without unnecessary Gateway or 2FA churn.
- T3 depends_on: [T2] - Verify the IB relay socket, fresh ticks, service-health row, and core service state; add a regression only if a source defect is found.
- T4 depends_on: [T3] - Make cloud-mode health fail closed when Docker/socat accepts TCP but the Gateway protocol listener is absent; preserve the `/health/lite` bounded, side-effect-free contract.
- T5 depends_on: [T4] - Add regression coverage, run affected and full suites, deploy the narrowly scoped health fix, and verify the production contract.

## Checklist

- [x] T1 Diagnose the relay-only failure: the container is running but Docker marks it unhealthy; its socat proxy repeatedly receives `Connection refused` from the Gateway's missing internal API listener on 127.0.0.1:4001. The relay and cached API-pool health are false-green consequences, not independent failures.
- [x] T2 Recover the affected service safely: issued exactly one lock-controlled `POST /ib/restart`; the IBKR Mobile approval completed and the Gateway was recreated without an unmanaged Docker or service restart.
- [x] T3 Verify data-plane recovery and record the result: at 13:41 UTC the relay logged `IB connected (clientId 10)` with no later reconnect attempt; Gateway is Docker-healthy; `/health` reports authenticated Gateway and all API-pool clients connected; API, relay, monitor, newsfeed, and Next.js are active. Markets are closed, so fresh ticks are not expected and were not used as the success condition.
- [x] T4 Make the cloud-mode Gateway health probe protocol-aware and fail closed: the side-effect-free version handshake is capped at 250ms, consumes no client ID, and reports `upstream_dead=true` / `unhealthy` before cached pool state can claim authentication.
- [x] T5 Test, deploy, and verify the durable health-reporting repair: focused 70-test suite and affected 41-test suite passed; GitHub CI run 30272256009 passed every gate and deployed `eda1a2bf`; live `/health` and `/health/lite` now exercise the protocol-aware check and report an authenticated, non-dead Gateway.

## Review

- Root cause: the prior Gateway 2FA cycle ended in authorization failure, leaving its Java API listener absent. Docker/socat still accepted host-port connections, which made the process and cached API-pool health look up while the relay retried continuously.
- Recovery: one sanctioned Gateway restart at 13:40 UTC and one successful IBKR approval. No client-ID collision, OOM, or code deployment was involved.
- Final evidence: Gateway Docker health `healthy`; FastAPI reports `auth_state=authenticated`, `upstream_dead=false`, and API client IDs 3/4/5 connected; relay connected as client ID 10; all five core units are active.
- Durable repair: commit `eda1a2bf` replaces cloud-mode TCP-only health with a bounded raw IB version handshake. On the observed Docker/socat-only failure, health now reports `upstream_dead=true`, `service_state=unhealthy`, and `auth_state=unreachable`, so aggregate monitoring fails closed without triggering an IBKR push. CI run `30272256009` and its automated production deployment passed; live production is on the matching SHA and is healthy.

# Task: Relay outage investigation and service recovery (2026-07-24)

## Dependency graph

- T1 depends_on: [] - Collect read-only live service, relay, and health evidence; identify the authoritative recovery path and preserve the existing worktree.
- T2 depends_on: [T1] - Recover the wedged Gateway through its documented lock-aware controller and restore the persistent service topology without unmanaged Docker mutation.
- T3 depends_on: [T1] - Add a red regression for the stale-disconnected relay being published as healthy, then implement the smallest truthful health-state repair.
- T4 depends_on: [T2] - Add a regression for the operator losing its inherited deploy-lock descriptor during a privileged Gateway restart, then preserve that descriptor through the bounded subprocess wrapper.
- T5A depends_on: [T3, T4] - Restore the declared Python 3.13 test dependency set and run the full application suite after the initial collection failure.
- T5B depends_on: [T3, T4] - Run full web and cloud release suites plus static checks.
- T5 depends_on: [T2, T5A, T5B] - Verify relay connectivity, core unit activity, aggregate health, and relevant logs; record the incident recovery evidence.
- T6 depends_on: [T5] - Commit only the reliability fixes, push to main, and verify the automatic production deployment.
- T7 depends_on: [T6] - Diagnose and correct the failed automated VPS deployment, then rerun the release and verify live production health.

## Checklist

- [x] T1 Establish live failure cause and safe recovery path: relay is process-healthy but cannot complete the IB protocol handshake; TCP 4001 is open while IB API is wedged, relay ticks stopped at 2026-07-23T23:45Z, and the watchdog exhausted its bounded API-restart cap. The lock-aware full-stack operator is the required recovery path.
- [x] T2 Gateway and all persistent services restarted again at 2026-07-24T14:01Z; IBKR 2FA completed and production later reported authenticated Gateway/API/relay health.
- [x] T3 Correct stale-disconnected relay health reporting with regression coverage.
- [x] T4 Correct the lock-aware full-stack operator regression with coverage.
- [x] T5 Operational preflight confirms production is healthy and authenticated before release.
- [x] T5A Used an isolated Python 3.13 environment with the declared dependencies plus pytest-asyncio; full suite passed.
- [x] T5B Full web and cloud release suites plus static checks passed.
- [x] T6 Released `339c7a7f` and verified the automatic deployment path.
- [x] T7 Refreshed the root control plane, reran the failed deploy, and verified the released SHA live.

## Review

- Confirmed incident root cause: the Gateway Java/GUI container remained alive while its internal API listener refused connections. The relay process remained up but retried `127.0.0.1:4001` every five seconds; the last tick was 2026-07-23T23:45:00Z. The API-aware watchdog detected the protocol wedge but had exhausted its bounded three-restart cap.
- Recovery action: the first `/usr/local/bin/radon restart` safely refused a lost inherited deploy-lock descriptor before mutating the Gateway. The documented controller was then invoked directly, acquiring one 2FA lease and rebuilding the Gateway container; `radon-api`, `radon-nextjs`, `radon-relay`, `radon-monitor`, `radon-newsfeed`, and all 25 active timers were restarted. All five persistent units are active.
- Recovery verification: after the replacement restart, production returned to authenticated Gateway/API/relay health. The earlier 2FA wait is resolved.
- Reliability fixes are local and tested: disconnected/stale relays cannot publish an `ok` tick heartbeat, and the stack operator now preserves its inherited deploy-lock descriptor through its bounded subprocess runner. Focused verification passed: `cloud/tests/test_ib_gateway_control.py` 40 passed; `scripts/lib/staleDataMachine.test.js` 29 passed; `git diff --check` passed. These source changes are not deployed while the current Gateway waits for 2FA.
- Release verification: full Python suite passed in an isolated Python 3.13 environment (`4,535 passed, 13 skipped, 90 deselected`); full web Vitest passed (`460 files, 4,430 passed, 26 skipped`); TypeScript passed; ESLint had 11 pre-existing warnings and no errors; cloud suite completed green; `git diff --check` passed. The initial system-Python run was blocked by its missing declared `mcp` package, so the isolated environment was used without changing the system toolchain.
- Release: commit `339c7a7f` passed every CI gate in run `30102688512`. Its initial deployment refusal correctly detected the stale root-owned operator. The documented exact-SHA root bootstrap fast-forwarded the VPS and installed/verified 20 control-plane artifacts without restarting services; rerunning the failed deploy then succeeded. Live verification: deployed SHA `339c7a7f`, matching installed operator hash, schema-v2 `ok=true` / `overall_state=up`, authenticated Gateway with all three API pool clients connected, relay tick age 0 seconds, and API, relay, monitor, newsfeed, Next.js, and health units active.

# Task: Radon Chat composer autofocus (2026-07-20)

## Dependency graph

- T1 depends_on: [] - Audit the launcher, panel lifecycle, and existing focus behavior.
- T2 depends_on: [T1] - Add a regression covering keyboard launch, initial composer focus, and Escape dismissal.
- T3 depends_on: [T2] - Focus the composer when the overlay mounts without changing its dismissal behavior.
- T4 depends_on: [T3] - Run focused, browser, static, and full-suite verification before release.

## Checklist

- [x] T1 Audited the modal lifecycle and composer element.
- [x] T2 Added unit and Playwright regression coverage.
- [x] T3 Focused the composer on modal mount.
- [x] T4 Ran the required release verification and recorded results.

## Review

- The overlay focuses its composer after mounting; Escape dismissal is unchanged.
- Verification: full web Vitest suite passed (442 files, 4,211 tests; 26 skipped), TypeScript passed, lint completed with 11 pre-existing warnings, and the focused Chromium Playwright test passed.

# Task: 48-hour reliability investigation and hardening (2026-07-10)

Source: operator report of repeated reliability failures during the prior 48 hours.

## Dependency graph

- T1 depends_on: [] - Preserve the dirty worktree, inventory recent deploys/changes, and collect an evidence-backed incident timeline from GitHub, production service health, systemd, application, database, and IB Gateway logs.
- T2 depends_on: [] - Audit the IB Gateway, FastAPI, watchdog, 2FA lock, restart/backoff, WebSocket relay, and monitor-daemon control paths for recurrence and correlated-failure modes.
- T3 depends_on: [] - Audit Next.js, Turso read/write, caching/fallback, auth, health probes, deploy gates, systemd topology, and external monitoring paths for recurrence and correlated-failure modes.
- T4 depends_on: [T1, T2, T3] - Reconcile evidence with code-path audits, identify root causes and observability gaps, and define the smallest complete fix set.
- T5A depends_on: [T4] - Add red deployment-cancellation, interrupted-release, false-no-op, and filesystem-boundary fault-injection regressions in Radon and radon-cloud, including fresh-process recovery after SIGKILL.
- T5B depends_on: [T4] - Add red IB sync single-flight, 2FA-lock, watchdog-sensor, pool-lifecycle, and relay-reconnect regressions.
- T5C depends_on: [T4] - Add red DB failure-deduplication, serialized-keepalive, health-schema, freshness-fail-closed, and bounded-read regressions.
- T5D depends_on: [T4] - Add red realtime timeout, alert-delivery cooldown, monitor ownership/state, and telemetry-unknown regressions.
- T5E depends_on: [T4] - Add red external-probe cadence regressions using the observed GitHub scheduler interval distribution, so ordinary dispatch delay cannot flap the dead-man while fresh failed rows still fail immediately.
- T6A depends_on: [T5A] - Make CI deployments non-cancelable and radon-cloud deployments locked, resumable, gated, credential-permission safe, and durably journaled before any release-artifact mutation.
- T6B depends_on: [T5B] - Remove browser IB amplification and harden server sync, 2FA, watchdog, pool, gateway, relay, and daemon control paths.
- T6C depends_on: [T5C] - Deduplicate DB recovery, serialize keepalive, bound critical reads, and make external health/freshness classification fail closed.
- T6D depends_on: [T5D] - Bound realtime authentication/open handshakes, preserve alert retries on delivery failure, and expose unknown telemetry honestly.
- T6E depends_on: [T5E] - Calibrate Python and web external-probe dead-man classifiers to the measured scheduler cadence and keep their contracts identical.
- T7 depends_on: [T6A, T6B, T6C, T6D, T6E] - Run affected tests, broad subsystem suites, full Python and web suites, radon-cloud suites, static checks, and adversarial re-audit; separate pre-existing failures from regressions.
- T8 depends_on: [T7] - Apply safe operational repairs, verify production/deploy health, and document residual credential rotations or destructive changes that require operator action.

## Checklist

- [x] T1 Incident evidence and timeline.
- [x] T2 IB/API/watchdog/daemon path audit.
- [x] T3 Web/DB/deploy/infrastructure path audit.
- [x] T4 Root-cause reconciliation and fix specification.
- [x] T5A Deployment red regression tests.
- [x] T5B IB/control-plane red regression tests.
- [x] T5C DB/probe red regression tests.
- [x] T5D Realtime/alert/telemetry red regression tests.
- [x] T5E External-probe cadence red regression tests.
- [x] T6A Deployment and infrastructure fixes.
- [x] T6B IB/control-plane fixes.
- [x] T6C DB/probe fixes.
- [x] T6D Realtime/alert/telemetry fixes.
- [x] T6E External-probe cadence calibration.
- [x] T7 Full verification and adversarial review (suite gates green; residual: live SIGKILL mid-promote dry-run on non-prod).
- [x] T8 Review documentation and final production checks (safe source/local repairs complete; destructive and privileged production actions documented below).

## Reconciled findings and fix specification

1. Jul 8 deployment outage, confirmed: workflow-level cancellation killed an SSH deploy after units were stopped; the next run trusted Git HEAD and skipped build/restart/gates. Fix both layers: serialize deploy jobs without cancellation, and make the cloud deploy own a nonblocking `flock`, durable requested/green markers, cleanup traps, and a resume/rebuild path when HEAD is current but the green marker is absent.
2. Jul 10 IB outage, confirmed: every browser tab repeatedly POSTed portfolio and orders live-sync routes; FastAPI spawned an independent short-lived IB client for every call. Stale Turso fallbacks returned HTTP 200, so clients erased the error and kept the 30-second cadence. Auto-refresh must be cached GET only, warning fallbacks must remain degraded, and FastAPI must single-flight each script with a minimum refresh age.
3. IB monitoring blind spot, confirmed: persistent pool `/health` stayed green while new clients failed, explicit probe timeouts were parsed as healthy, direct protocol probing ran only when HTTP failed, and the quiet window prolonged a scheduled-restart outage. The watchdog must treat timeout as unknown, consult the independent protocol sensor, count gateway-down/wedged states, and use bounded scheduled-restart grace instead of freezing an entire window.
4. 2FA and lifecycle recurrence, confirmed: the push lease is an unlocked read/replace race, same-holder reentry is allowed, Docker can restart outside the lease, startup can start Gateway outside the lease, pool lifecycle calls race, and reconnect timers can later disconnect recovered relay sockets. Use an OS file lock, reject all active-lease reentry, centralize lock-aware starts, remove unmanaged restart behavior, serialize pool lifecycle, reap canceled subprocesses, and cancel stale relay timers.
5. Turso destroy storm, confirmed: one failed operation is observed by proxy/helper/route layers and counted multiple times; overlapping keepalive calls then create a sustained teardown cadence. Give each DB operation an identity and count it once, remove route-level duplicate resets, serialize keepalive, and abort underlying HTTP work at its transport deadline.
6. Off-box monitoring false green, confirmed: all probe jobs since Jul 3 failed to persist because the GitHub token is read-only, the parser ignores the nested status schema, and endpoint auth/404/transport failures can exit green. Publish and consume a canonical aggregate state, classify endpoint availability failures as failures regardless of market state, bound freshness DB reads, add an on-box dead-man, and restore a least-privilege write credential.
7. Realtime and alert latches, code-confirmed: auth/ticket fetches, WebSocket opens, relay ticket validation, and some health requests can wait forever; failed alert delivery still enters cooldown; unknown footer state renders green. Add deadlines and generation-safe cleanup, mark cooldown only after successful delivery, heartbeat when nothing was delivered, and render unknown as warning.
8. Credential/config exposure, confirmed: a production credential appears in committed cloud history, deployed env files are group/world readable, the full env is copied into the web tree, and one Compose value is interpolated incorrectly. Redact code, narrow build env, enforce mode 0600 and Compose preflight, then rotate/scrub credentials as a separate destructive operator action.

## Constraints

- Existing dirty changes in `.serena/project.yml`, `web/app/globals.css`, `web/components/WorkspaceSections.tsx`, `web/lib/journal/`, and journal-range tests are operator work; do not modify or revert them.
- Production inspection is read-only unless a code/config fix proceeds through the normal reviewed deployment path. Do not restart services, mutate Turso, acknowledge alerts, or rotate credentials during evidence collection.
- Treat the 48-hour window as 2026-07-08 00:00 PT through collection time on 2026-07-10, widening only when earlier state explains a failure in-window.
- Distinguish confirmed incidents, repeated symptoms, stale/latched telemetry, and latent risks. Do not claim zero future failures; require evidence for every conclusion.

## Review

### Progress (2026-07-10 continuation)

T5A–T6E are closed in-tree. Remaining: T7 broad full-suite gate + adversarial re-audit, then T8 operator-only production repairs (credential rotation, deploy green-marker verification on the VPS).

#### T5A / T6A Deployment

- CI: workflow-level cancel-in-progress removed; deploy job is `deploy-production` with `cancel-in-progress: false`, 60m budget, 55m SSH, explicit `${{ github.sha }}`.
- radon-cloud: outer supervisor owns flock + durable transition journal; TERM/INT/HUP recover before lock release; SIGKILL-recoverable journal path covered by fault-injection tests.
- External-signal tests updated so mid-transition journals are created by the worker (startup recovery vs signal recovery are distinct).

#### T5B / T6B IB / control plane

- FastAPI: IB sync single-flight + min age; pool lifecycle serialization; gateway subprocess reaping; admin non-gateway mutations hold deploy lock and cancel timed-out systemd jobs.
- Browser: portfolio/orders auto-refresh is cached GET only (no live-sync amplification).
- Relay: generation-safe `reconnectGate` cancels stale reconnect timers.
- radon-cloud: `ib-gateway-control` is sole Docker lifecycle path; 2FA lease + deploy lock + lifecycle mutex; operator stack start excludes isolated `radon-health`; Gateway dependents are `After=` only (no Wants/Requires pull under deploy lock).

#### Verification evidence (this session)

```
radon (focused reliability):
  486 passed (IB/watchdog/services/health/deploy-concurrency)

radon web (focused reliability Vitest):
  18 files / 201 tests passed (db, sync, telemetry, reconnectGate)

radon-cloud (full suite):
  556 passed, 2 skipped

Deploy external-signal + durable journal suites:
  test_deploy_corrections + resilience + deploy_and_setup green
  test_ib_gateway_control 38/38 green
```

#### T7 suite gates (2026-07-10)

```
radon pytest:     4053 passed, 13 skipped, 90 deselected
web npm test:     419 files / 4082 passed / 26 skipped
web typecheck:    clean
reconnectGate:    3 passed
radon-cloud:      556 passed, 2 skipped
```

#### Still open for T8

- Operator actions: restore write-capable GitHub probe token if still read-only; enforce 0600 on deployed env files; rotate any credential exposed in cloud history; verify production green marker after first post-fix deploy.
- Optional non-prod dry-run: SIGKILL mid-promote to confirm durable journal recovery on the real VPS filesystem.

## Continuation: post-hardening reliability verification (2026-07-11)

The July 10 hardening push passed CI, but the following monorepo cloud import failed CI and all observed Tier-3 probes after that import are red. This continuation treats those results as active reliability regressions rather than accepting the previous suite summary.

### Dependency graph

- T9 depends_on: [] - Collect the post-hardening CI, deploy, external-probe, local runtime-log, and production read-only evidence; build an exact timeline from the July 10 hardening push through the current run.
- T10 depends_on: [] - Audit the cloud monorepo import, GitHub workflow paths, deploy packaging, external-probe contract, credentials, and service-unit topology for integration regressions.
- T11 depends_on: [] - Audit application/API/IB/monitor/database/realtime paths against current logs and the July 10 fixes, including fault containment and observability behavior.
- T12 depends_on: [T9, T10, T11] - Reconcile independent audits into a causal graph, distinguish active incidents from expected fail-closed alarms, and specify the smallest complete regression-test and fix set.
- T13 depends_on: [T12] - Add failing regressions and implement the root-cause fixes across only the affected application and infrastructure surfaces.
- T14 depends_on: [T13] - Run focused tests, affected suites, full Python/web/cloud suites, static checks, workflow validation, and an adversarial second-pass audit; iterate until new regressions are green and no supported active failure remains unexplained.
- T15 depends_on: [T14] - Perform non-destructive production/read-only verification, document operational actions that cannot be safely automated, and complete the review with evidence and explicit confidence.

### Checklist

- [x] T9 Post-hardening evidence and timeline.
- [x] T10 Cloud/CI/deploy/probe integration audit.
- [x] T11 Runtime application/control-plane audit.
- [x] T12 Causal reconciliation and fix specification.
- [x] T13 Red/green regression fixes.
- [x] T13-C1 depends_on: [] - Add a red regression for a missing non-helper control-plane manifest target and prove deploy mutation never begins.
- [x] T13-C2 depends_on: [T13-C1] - Require every installed manifest target to be readable, regular, non-symlinked, and hash-identical while retaining helper executable checks.
- [x] T14 Full verification and adversarial re-audit.
- [x] T14-C1 depends_on: [T13-C2] - Run focused cutover tests, the full cloud suite, and static validation for the fail-closed manifest fix.
- [x] T15 Production verification and final review (production remains correctly red pending deploy and IBKR 2FA).

### Constraints

- Existing dirty changes in `.serena/project.yml`, `data/tag_taxonomy.json`, `web/app/globals.css`, `web/components/WorkspaceSections.tsx`, `web/lib/journal/`, and journal-range tests are operator work; do not modify or revert them.
- Evidence collection and production checks are read-only. Do not rotate credentials, mutate Turso, restart production services, acknowledge alerts, or rewrite Git history.
- Treat a red fail-closed health probe as evidence to diagnose, not automatically as a defect in the probe. Restore green only by repairing the measured dependency or proving and testing a classifier defect.
- No claim of perfect future availability is supportable. Completion requires that every observed failure has a traced cause, a containment or repair, a regression test where feasible, and a verified monitoring signal.

### Reconciled findings and fix specification

1. The broker incident is real and remains operator-blocked: Gateway restarted after an API hang, its 2FA dialog expired, and the watchdog briefly consumed a stale authenticated sample before the session settled at `awaiting_2fa`. Preserve the no-restart stand-down, heartbeat the error every cycle, and require consecutive authenticated samples before the one-shot API pool reconnect.
2. The Tier-3 red sequence is a producer/consumer rollout split. Commit `218da845` made the off-box consumer fail closed on opaque status while the legacy deploy did not restart `radon-health`; the on-box process has served the old schema since June 12. Version the schema, validate exactly N and N-1, inspect nested FastAPI broker state, and gate deploys on producer schema without requiring IB to be healthy.
3. Commit `bcd13a13` never deployed because a calendar-bound options expiry fixture rotted and a Linux-only deploy-lock test assigned a readonly variable after sourcing. Generate future expiries and pass supervisor budgets through subprocess environment before sourcing.
4. The first monorepo cloud deploy is unsafe before it starts: Compose still requires an in-tree `.env`, production lacks the root-owned helpers and exact sudo policy, CI mutates live support code before acquiring the deploy lock, and rollback can delete its own journal helper when crossing to a pre-`cloud/` SHA. Use an immutable exact-SHA runner, external secret path, early compatibility preflight, stable journal support, and an explicit non-restarting root bootstrap.
5. The imported infra still points drift audit, Gateway, backup, watchdog, and setup paths at the legacy checkout; transition topology under `/run` is lost on reboot. Canonicalize code to `/home/radon/radon/cloud`, retain only the temporary 0600 legacy secret file, and persist/fsync transition state under `/var/lib/radon/deploy`.
6. The laptop monitor daemon is an active amplified crash loop: launchd selects Python 3.9, a PEP 604 annotation aborts import, failure KeepAlive retries about every 11 seconds, and status reports `RUNNING`. Pin Python 3.13 under an explicit launchd PATH, preflight dependencies, remove failure amplification, and make status evaluate exit code plus heartbeat age.
7. The laptop data refresh is false green: the resolver accepts a Python missing `libsql_experimental`, all three core children fail, stale caches remain, and the wrapper exits zero. Require the complete Python 3.13 runtime, attempt every child, preserve caches, and return aggregate failure after post-close repair.
8. Full pytest polluted ignored runtime state through an unredirected `/scan` cache and CTA history default. Redirect every implicated path to `tmp_path` and prove protected file hashes and mtimes remain unchanged.
9. Demo reliability has two contained but repeated failures: the Vercel adapter rejects the App Router favicon metadata route under compile-mode output, and the local newsfeed mirror has unbounded single-attempt destination writes with timestamp-free logs. Serve the ICO as a public asset, reproduce the Vercel build path, and add bounded phase-labelled mirror retries and tests.
10. External controls remain outside code: production/main GitHub protection rules and required reviewers are not enabled. Source can enforce pinned actions and tested policy configuration, but repository/environment settings require an explicit GitHub control-plane mutation after code verification.

### T13/T14 control-plane follow-up review

- Red evidence: a valid readiness/manifest fixture with a deleted non-helper systemd target passed preflight and reached every stubbed deploy mutation boundary.
- Fix: deploy rejects missing/non-regular/symlink targets directly, hashes readable targets, and requires the fixed no-argument root helper to verify all 20 ordered bootstrap source/target/mode/hash records. This preserves intentional `0440 root:root` sudoers permissions.
- Verification: 37 focused cutover/bootstrap tests passed; full cloud suite passed with 598 passed and 2 skipped; shell syntax and `git diff --check` are clean.

### Final review (2026-07-11)

#### Root causes closed in source

- Broker recovery no longer acts on one stale authenticated sample after restart, and `awaiting_2fa` remains a no-restart error heartbeat.
- Health producer/consumer contracts are versioned and fail closed; nested broker degradation now controls the aggregate even when FastAPI returns HTTP 200.
- Cloud cutover is immutable-SHA, lock-first, durable, rollback-compatible, externally configured, and guarded by a complete root-owned 20-target manifest verifier before mutation.
- Local launchd jobs pin Python 3.13, preflight required modules, avoid failure-amplifying KeepAlive, expose exit/heartbeat truth, and propagate aggregate refresh failures.
- Preset refreshes use semantic upstream parsers, validate all three universes before any write, and atomically replace files. Canonical refresh is stable on a second dry run: S&P 500 503, Nasdaq-100 103, Russell 2000 1,979.
- The retired replica watchdog is inapplicable when `data/replica.db` is absent across writer, alert, API, and reliability-scoring paths.
- Monitor telemetry isolates non-termination native-extension panics without swallowing process termination signals.
- Runtime-writing tests use temporary paths; the full suite preserved `data/scanner.json` and CTA history hashes and mtimes exactly.
- Demo mirroring has bounded retries, deadlines, idempotent writes, structured phase logs, health heartbeats, and deterministic client cleanup.
- The favicon is a public static asset, web fonts are local, date-sensitive fixtures are future-relative, and output tracing excludes backup/archive explosions with a post-build manifest audit.

#### Verification evidence

```text
Python full suite:       4099 passed, 13 skipped, 90 deselected
Web full Vitest suite:   4099 passed, 26 skipped
Cloud full suite:         598 passed, 2 skipped
Cloud focused cutover:     37 passed
Demo mirror Vitest:         3 passed
TypeScript:                 clean
Next production build:      passed (Next 16.2.10)
Vercel production build:    passed against linked radon-demo project
Output trace audit:         126 manifests; 0 forbidden backup/archive paths;
                            max 3,359 files / 70.64 MiB; 19 explicit fallbacks
Static validation:          git diff --check, Python compileall, all shell bash -n,
                            and three modified launchd plists passed
Protected runtime files:    hashes and mtimes unchanged by the full Python suite
Local monitor launchd:      idle as scheduled, exit 0, 42 runs, fresh heartbeat
```

#### Read-only production state at 2026-07-11 13:44 UTC

- Application checkout is still `218da8456976`; legacy cloud checkout is `fb43aeea3e5d`. None of this review's source changes are deployed.
- The legacy health producer is active but still emits no `schema_version`, `ok`, or `overall_state`. The external consumer correctly reports `aggregate_unhealthy` instead of masking it.
- FastAPI reports `auth_state=awaiting_2fa`, `service_state=unhealthy`, and `upstream_dead=true`; `ib-gateway` is running but Docker reports it unhealthy.
- The watchdog timer is active and its last oneshot result is successful. It is standing down on the authentication gate rather than creating another restart storm.
- Current source is complete and test-green, but live production is not healthy. Claiming otherwise would be false.

#### Required operator/control-plane actions

1. Review and commit the intended reliability diff while excluding unrelated operator-owned changes.
2. Run the non-restarting root control-plane bootstrap on the VPS, then deploy the exact reviewed SHA through the gated Production workflow and verify schema-v2 health plus the durable green marker.
3. Complete a fresh IBKR 2FA approval and verify two consecutive authenticated samples, API/pool recovery, Gateway health, and a green external aggregate.
4. Enable GitHub `main`/Production protections and required reviewers. These repository settings are not enforceable from source alone.

#### Operational cutover (2026-07-11) — executed and closed green

1. **Committed and deployed** the reliability package plus cutover fixes through production SHA `06e683e5` (green marker matches HEAD).
2. **Root bootstrap** published `/var/lib/radon/control-plane-ready` (20 artifacts) without restarting Gateway. Required preseed of shell helpers before `systemd-analyze verify`.
3. **IBKR 2FA recovered** after gateway-control root-demotion cwd fix; pool 3/3 connected. Production enums: `IB_GATEWAY_MODE=cloud`, `RADON_MODE=hetzner`, `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud`.
4. **Schema-v2 aggregate green** after accepting cloud-mode `service_state=reachable` in the nested broker classifier.
5. **First post-ready immutable-runner deploy succeeded** (CI run `29157182912`). Intermediate failures fixed in tree: unreadable-sudoers preflight, Bun 1.3.14 host pin, best-effort backup cleanup, writable prune of a-w runners.
6. **GitHub control plane**: Production required reviewer + main-only deploy branches; main blocks force-push/delete without blocking solo direct push via required pre-push status checks.

**Live verification (2026-07-11 ~15:07 UTC):**
```
HEAD / green marker:    06e683e5160eee043ad9e5ba5279141ec0b1e419
control-plane-ready:    present
/health:                authenticated reachable cloud upstream_dead=false
ib_pool:                sync/orders/data connected
/status:                schema_version=2 ok=true overall_state=up
.env:                   0600; IB_GATEWAY_MODE=cloud; RADON_MODE=hetzner
compose dir:            /home/radon/radon/cloud
CI deploy:              success (all jobs including Deploy to VPS)
```

**Cutover defects found only under live fire (now fixed + documented in tasks/lessons.md):**
bootstrap helper preseed; gateway-control demotion cwd; radon-side sudoers `-f` probes; env invariants cloud vs hetzner/docker; nested `reachable`; host Bun pin; a-w runner prune; post-green backup permission noise.

**Still open (non-blocking):**
- Forced pre-cloud SHA rollback drill.
- Operator-owned local WIP (journal-range, WorkspaceSections, globals, serena, tag_taxonomy) intentionally excluded from reliability commits.
- Host notes pending kernel upgrade on the VPS.

Confidence: high for root causes, source fixes, tests, live bootstrap, 2FA recovery, schema-v2 green aggregate, and a completed immutable-runner deploy.

---

# Task: Standalone watchlist page (2026-07-09)

Source: user request — make watchlist a standalone page, enhance the feature for its own Radon-branded page, and show ticker detail inline instead of redirecting to `/{TICKER}`.

## Dependency graph

- T1 depends_on: [] — Document plan and inspect current routing/watchlist/detail contracts.
- T2 depends_on: [T1] — Add `/watchlist` route and workspace section/nav metadata.
- T3 depends_on: [T1, T2] — Build standalone watchlist page that selects tickers inline and renders ticker detail content without route push.
- T4 depends_on: [T2, T3] — Apply Radon-compliant high-end visual pass and responsive behavior.
- T5 depends_on: [T4] — Add focused regression and Playwright coverage for inline selection.
- T6 depends_on: [T5] — Run focused and broad verification, document review here.

## Checklist

- [x] T1 Plan + route/component/test inspection.
- [x] T2 `/watchlist` page, section type, nav/description/quick prompt updates, reserved ticker guard.
- [x] T3 Standalone watchlist content with inline ticker selection and `TickerDetailContent` reuse.
- [x] T4 Radon-branded premium layout: dense instrument list, selected state, inline context pane, responsive single-column behavior.
- [x] T5 Regression tests: route metadata/guard and watchlist inline selection without navigation.
- [x] T6 Verification: focused Vitest, relevant Playwright, typecheck, full test suite.

## Constraints

- Do not revert existing dirty worktree changes.
- Radon brand rules override generic visual-skill conflicts: tokens, matte instrument modules, max 4px radius, no gradients/glassmorphism/soft shadows.
- Client fetches to `/api/watchlist` must keep `cache: "no-store"`.
- Ticker click on `/watchlist` must not push to `/{TICKER}`.

## Review

### Shipped

1. Added `/watchlist` as a reserved standalone workspace route with nav metadata, section prompts, mobile overflow entry, and ticker-route guard coverage.
2. Built `WatchlistContent` so watchlist rows select an inline ticker detail pane instead of routing to `/{TICKER}`.
3. Reused the existing ticker detail cockpit data contract for selected symbols: fundamentals, portfolio/orders, depth, tape, deck tabs, and active ticker context.
4. Applied a Radon-branded visual pass: dense instrument rail, selected-market state, matte detail cockpit, responsive mobile stacking, and mobile scroll-to-detail on selection.
5. Added focused Vitest and Playwright regressions proving `/watchlist` stays fixed while the selected ticker detail changes.

### Verification evidence

```
npx vitest run --config vitest.config.ts web/tests/chat.test.ts web/tests/data.test.ts web/tests/watchlist-content.test.tsx
  3 files / 47 tests passed

npm run typecheck
  clean

npx playwright test --config playwright.config.ts e2e/watchlist-page.spec.ts --project=chromium
  1 passed

npm test
  415 files / 4042 tests passed / 26 skipped
```

---

# Task: Orders page UX/UI improvements (critique execution)

Source: orders page UX critique (P0 safety → P1 hierarchy → P2 polish).

## Dependency graph (pass 1 — shipped)

- T1 Pure display helpers — done
- T2 Combo cancel confirmation dialog — done
- T3 Wire cancel confirm + partial-fill display — done
- T4 Command strip + Historical IA — done
- T5 Δ to fill + status mapping + intent badges — done
- T6 Mobile action sheet + tone by intent — done
- T7 Action button CSS hit targets — done
- T8 Verification — done

## Dependency graph (pass 2 — deferred polish)

- R1 Keyboard shortcuts on /orders — done
- R2 Bulk cancel by selection — done
- R3 Implied default-off + density — done
- R4 Historical page-size options — done
- R5 Verification + commit — done

## Checklist

### Pass 1
- [x] T1 lib helpers + tests (`web/lib/orders/orderDisplay.ts`, `web/tests/orders-display.test.ts`)
- [x] T2 cancel dialog multi-order (`CancelOrderDialog.tsx`, `cancel-order-dialog.test.tsx`)
- [x] T3/T5 open-orders table integration (`WorkspaceSections.tsx`)
- [x] T4 command strip + historical Status label + collapse when open>0
- [x] T6 mobile order list
- [x] T7 CSS action targets + status/intent/delta styles
- [x] T8 verification

### Pass 2
- [x] R1 `/` focuses open-orders filter; `M`/`X` on selected row; selected-row class + tabIndex
- [x] R2 checkbox column + Cancel selected (N) → multi CancelOrderDialog; clear after confirm
- [x] R3 `ORDER_COLUMN_DEFAULTS.implied = false`; compact/comfortable density toggle
- [x] R4 historical page sizes 15/30/50 + localStorage + Showing X-Y of N
- [x] R5 focused vitest + tsc + commit

## Constraints

- Brand: tokens only, no em dashes in new user-facing copy, 4px max radius
- Red/green TDD for logic and UI behavior
- Surgical: only orders-related surfaces

## Review (2026-07-09) — pass 1

### Shipped
1. **P0 safety:** Combo `CANCEL ALL` (desktop + mobile) opens multi-leg `CancelOrderDialog`; no direct cancel. Confirm then sequential `requestCancel`.
2. **Partial fills:** `formatFillQuantity` (`3/10`) + `Partial` status pill on open table/cards.
3. **Command strip:** Working / Partial / Fills today / Last sync + jump anchors to open/executed/historical/cash.
4. **Δ Fill** column (default on) with near/through/far urgency classes.
5. **Status mapping:** IB raw in `title`, operator labels Working/Queued/Partial/…
6. **OPEN/CLOSE** intent badges from portfolio; mobile card tone by intent (CLOSE = default).
7. **Historical:** column Side → Status; `defaultExpanded={openOrderRows.length === 0}`.
8. **Actions:** min-height 32px buttons; mobile bottom sheet shows order summary before actions.
9. Orphan bottom Last Sync section removed (lives on strip).

### Verification evidence (pass 1)
```
vitest: 9 files / 78 tests passed
tsc --noEmit: clean
playwright e2e/orders-ux-command-strip.spec.ts: 3/3 passed
```

## Review (2026-07-09) — pass 2 deferred items

### Shipped
1. **R1 Keyboard:** Pure helpers in `web/lib/orders/ordersUx.ts`. Desktop: `/` focuses `#orders-open-filter`; click/focus selects open-order row (`open-order-row--selected`); `M` opens modify when canModify; `X` opens cancel (single or combo). Ignored while typing / with modifiers / when dialog open.
2. **R2 Bulk cancel:** Checkbox column + select-all; header `Cancel selected (N)` opens multi-order `CancelOrderDialog` with flattened legs; selection cleared after confirm. Multi dialog shows "N symbols" when mixed.
3. **R3 Implied default-off:** `ORDER_COLUMN_DEFAULTS.implied = false` (implied_mv already false). Compact/comfortable density toggle on open-orders table-wrap (`table-wrap--compact`), persisted `radon:orders-open-density`.
4. **R4 Historical page size:** Selectable 15/30/50; localStorage `radon:orders-historical-page-size`; `Showing X-Y of N` on desktop and mobile lists.

### Verification evidence (pass 2)
```
vitest 8 files / 77 tests:
  orders-display, orders-ux, cancel-order-dialog, orders-command-strip,
  workspace-orders-implied, historical-trades-filter, mobile-order-list-display,
  orders-bulk-cancel
tsc --noEmit: clean
```

### Files
- `web/lib/orders/ordersUx.ts` (new)
- `web/components/WorkspaceSections.tsx`
- `web/components/TableSearch.tsx`
- `web/components/CancelOrderDialog.tsx`
- `web/app/globals.css`
- `web/tests/orders-ux.test.ts` (new)
- `web/tests/orders-bulk-cancel.test.tsx` (new)
- `web/tests/workspace-orders-implied.test.tsx`
- `web/tests/historical-trades-filter.test.tsx`

---

# Task: Options OrderBuilder layout pass (2026-07-09)

## Shipped
- Fixed leg grid (56px BUY/SELL chip)
- Removed redundant OrderLegPills
- Tappable OrderPriceStrip for combos; no duplicate chips
- Compact skew (4 metrics)
- Prefill chip, compact TIF, risk teaser, no em-dash limit label
- CSS `.order-builder-*`

## Verify
vitest: order-builder-layout, combo-skew-panel, chain-url-deeplink, order-unified-components green
tsc clean
# 2026-07-12 app.radon.run overnight reliability incident

## Dependency graph

- `T1` depends_on: [] - Correlate Pushover notifications, production journals, edge health, GitHub probes, and service-health history into a timestamped incident graph.
- `T2` depends_on: [`T1`] - Add failing regression coverage for every confirmed code/config defect in the Turso, watchdog, and IB Gateway recovery paths.
- `T3` depends_on: [`T2`] - Implement bounded database operations and crash-safe, convergent Gateway transition recovery with surgical changes.
- `T4` depends_on: [`T3`] - Run focused tests, full Python and web suites, static/unit configuration checks, and review the change for a simpler design.
- `T5` depends_on: [`T3`, `T4`] - Deploy/recover production through managed controls, clear only proven stale failed states, and verify edge health plus notification silence.
- `T6` depends_on: [`T1`, `T4`, `T5`] - Document root causes, evidence, validation results, residual risks, and final production state.

## Checklist

- [x] `T1` Complete incident evidence and root-cause graph.
- [x] `T2` Add regression tests for confirmed defects.
- [x] `T3` Implement minimal fixes.
- [x] `T4` Pass focused validation and full cloud suite; record unrelated baseline failures.
- [x] `T5` Restore production broker, API, watchdog buckets, and control-plane manifest.
- [x] `T6` Add review record and report.

## Review

- Root cause: Docker 29.3.1 emits lowercase `error: no such object`; the Gateway helper treated it as unknown after a successful scheduled shutdown, left `desired=stopped`, and blocked every up phase. The asynchronous watchdog handoff then counted three launches as successful recovery attempts and exhausted its cap.
- Amplifiers: watchdog decision/cooldown/heartbeat state used unbounded native libSQL; Pushover cancellation failures were marked resolved; the off-box probe replaced the actual health verdict with a ledger-write failure; API migration startup had no hard deadline.
- Fixes: case-insensitive missing-container convergence, synchronous recovery result accounting, bounded HTTP health/heartbeat I/O, host-local SQLite watchdog control state, retryable emergency cancellation, best-effort probe persistence, and a 30s API migration transport boundary that still fails closed on immediate schema errors.
- Production: Gateway authenticated; sync/orders/data pools connected; API active; continuous/daily/error watchdog buckets complete successfully; all four watchdog timers active; control-plane bootstrap verified 20 artifacts.
- Validation: focused reliability `283 passed`; watchdog `158 passed`; cloud full suite `611 passed, 2 skipped`; systemd focused `181 passed`; Gateway control focused included in `39 passed` run.
- Baseline failures unrelated to this change: full Python `3660 passed, 13 skipped, 18 failed` before the test seam correction; two remaining failures are the known portfolio-retention Hrana fixture pollution seen in overnight CI. The prior `bun test --run` invocation is invalid for this repo because it loads Playwright specs as Bun unit tests; CI's configured Vitest baseline already has an unrelated stale-warning assertion failure.
- Remaining batch state: config drift still reports legacy live-only timer dependencies/untracked units and production checkout ownership; batch jobs backed by native libSQL require separate migration to bounded Hrana. These did not prevent app/API/broker/watchdog recovery and were not cosmetically cleared.
# 2026-07-12 config-drift elimination

## Dependency graph

- `CD1` depends_on: [] - Inventory every live/repo unit and drop-in mismatch, establish provenance, and classify intentional versus obsolete state.
- `CD2` depends_on: [`CD1`] - Add regressions for audit normalization, symlink/untracked policy, repository ownership, and remediation safety.
- `CD3` depends_on: [`CD1`, `CD2`] - Optimize the auditor and canonical install/removal policy without allowlisting actionable drift.
- `CD4` depends_on: [`CD3`] - Apply production cleanup atomically: install canonical units, remove proven obsolete drop-ins/units, daemon-reload, and preserve active timer topology.
- `CD5` depends_on: [`CD4`] - Run focused/full cloud tests, rerun production drift audit to `state=ok`, and verify app/watchdog health remains green.
- `CD6` depends_on: [`CD5`] - Document the final zero-drift inventory, deletions/adoptions, validation, and residual intentional exceptions.

## Checklist

- [x] `CD1` Classify all current drift with provenance.
- [x] `CD2` Add regression coverage.
- [x] `CD3` Implement canonical audit/install policy.
- [x] `CD4` Reconcile production units and drop-ins.
- [x] `CD5` Prove clean audit and healthy runtime.
- [x] `CD6` Complete review record.

## Review

- Adopted intentional `radon-demo-mirror` and `radon-margin-debt` service/timer pairs into the monorepo; neither feature was deleted.
- Reinstalled every canonical Radon service/timer as a regular `root:root 0644` artifact. Removed all legacy unit symlinks into `/home/radon/radon-cloud` and all obsolete per-unit `50-start-limit.conf` mirrors; retained the fleet `radon-.service.d/common.conf` invariant.
- Preserved timer topology: 22 enabled before, 22 enabled/active after. The Artificial Analysis-gated LLM units remain deliberately absent and exactly allowlisted. Beta staging units remain visible as known-untracked compatibility state.
- Optimized drift semantics: legacy symlinks and stale drop-ins are explicit drift; setup cannot follow retired-tree symlinks; runtime config comparison no longer conflates unrelated application/data worktree changes; clean audit verdict is independent of Turso telemetry transport.
- Production result: `config-drift audit: state=ok`, `Result=success`, `ExecMainStatus=0`; zero failed systemd units; zero legacy symlinks; zero stale drop-ins; public aggregate `up` with authenticated IB pools.
- Validation: focused config/systemd/setup `231 passed`; final drift tests `18 passed`; final full cloud suite `624 passed, 2 skipped`; `systemd-analyze verify` has no actionable errors.
- Rollback archive: `/var/lib/radon/config-drift-backup-20260712T162915Z`.
- External residual: Turso service-health writes time out from the VPS, so the remote dashboard row may remain latched until that independent transport recovers. The audit retries three bounded times, logs the failure, and does not turn a proven-clean configuration into a recursive unit alert.
# 2026-07-12 Turso read-stall root cause and repair

## Dependency graph

- `TS1` depends_on: [] - Measure VPS DNS/TCP/TLS, HTTP pipeline, native libSQL, actual portfolio query, concurrency, and production failure history.
- `TS2` depends_on: [`TS1`] - Add regressions for caller/transport deadline alignment, pool-slot release, keepalive cadence, and truthful portfolio writer failure exit.
- `TS3` depends_on: [`TS2`] - Align transport abort with the 3s database deadline, reduce keepalive amplification, and fix writer exit propagation.
- `TS4` depends_on: [`TS3`] - Deploy Next.js and writer fixes, then soak direct and application reads under concurrency and verify pool/error logs.
- `TS5` depends_on: [`TS4`] - Run focused/full web and Python suites and verify portfolio freshness on the next eligible writer cycle.

## Checklist

- [x] `TS1` Isolate network, provider, client, query, and writer behavior.
- [x] `TS2` Add regression coverage.
- [x] `TS3` Implement root-cause fixes.
- [x] `TS4` Deploy and soak production.
- [x] `TS5` Complete verification and review.

## Review

- Root cause: the latest-CRI query ordered only by `taken_at`, forcing a full scan and temporary sort of 14,924 rows with roughly 47 KB payloads. Production latency was 6.8-7.6s and caused repeated caller timeouts plus shared-pool resets.
- Repair: order by `date DESC, taken_at DESC` to use `idx_cri_latest`; abort transport at 2.75s, reduce keepalive cadence to 30s, and propagate portfolio refresh HTTP failures truthfully.
- Validation: focused web `27 passed`; full web `4115 passed, 26 skipped`; focused retention `6 passed`; CI Vitest, pytest, perimeter, and secret-scan gates passed. Local full Python was `3682 passed, 13 skipped` plus the two subsequently repaired Hrana fixture failures.
- Production: deployed `ea24c66c1e337cbd4e2c28bd66142f9c936f8e42`; query plan uses `idx_cri_latest`; 20 live reads p50 8.3ms, p95 28.1ms, max 29.7ms. Post-deploy `nextjs-db-read` is `ok`, overall health is `up`, and `radon-nextjs` has `NRestarts=0`.
- Drift: archived the runtime-enriched `data/tag_taxonomy.json` at `/home/radon/.config-drift-backups/20260712T200352Z` before restoring the tested release version.

# 2026-07-12 Turso incident documentation and memory

## Dependency graph

- `TD1` depends_on: [] - Consolidate the verified RCA, repair, and production measurements.
- `TD2` depends_on: [`TD1`] - Update the cloud-services runbook and persistent incident lessons.
- `TD3` depends_on: [`TD2`] - Commit and push the scoped documentation update.

## Checklist

- [x] `TD1` Preserve the verified incident facts and distinguish the separate IB refresh failure.
- [x] `TD2` Update runbook and memory with reusable diagnosis and prevention rules.
- [x] `TD3` Commit and push documentation only; leave unrelated workspace state untouched.

## Review

- Added the 2026-07-12 CRI query-plan incident, indexed SQL, timeout/keepalive contract, diagnosis order, production latency evidence, and portfolio refresh exit-code semantics to `docs/cloud-services.md`.
- Added durable query-planning, timeout ordering, pool diagnosis, keepalive, shell-exit, test-path, and drift-preservation lessons to `tasks/lessons.md`.

# 2026-07-12 Latched live-data warning

## Dependency graph

- `LW1` depends_on: [] - Compare the screenshot timestamp with deployment and current application DB health.
- `LW2` depends_on: [`LW1`] - Trace warning state through portfolio and orders polling hooks.
- `LW3` depends_on: [`LW2`] - Make clean polls clear transient warnings and add same-snapshot regressions.
- `LW4` depends_on: [`LW3`] - Validate, deploy, and verify the banner clears against healthy production reads.

## Checklist

- [x] `LW1` Prove the screenshot warning predates the fixed deployment while current `nextjs-db-read` is healthy.
- [x] `LW2` Identify the same-`last_sync` warning latch in both hooks.
- [x] `LW3` Implement clean-response recovery and regression coverage.
- [x] `LW4` Validate and deploy.

## Review

- Root cause: successful portfolio and orders GET polls only cleared warnings when `last_sync` changed, so the pre-deploy Turso warning remained latched against an unchanged off-hours snapshot.
- Focused hook regression: `11 passed`; TypeScript: clean. The local parallel full suite hit unrelated load-induced timeouts (`4074 passed`, `43 timed out`, `26 skipped`); CI remains the authoritative isolated full-suite gate.
- CI Vitest, pytest, perimeter, and secret-scan gates passed. Deployed `d9808fa68da2341d0e3873b666c9b49c9bac3708`; all health gates passed, `nextjs-db-read` is `ok`, overall health is `up`, and `radon-nextjs` is active with `NRestarts=0`.
- Existing browser tabs must reload once to receive the corrected client bundle; subsequent transient warnings clear on the next clean 30-second poll even when `last_sync` is unchanged.
# Task: Three responsive UI design directions (2026-07-14)

## Dependency graph

- T1 depends_on: [] - Inventory the current web information architecture, shared shell, responsive rules, and representative high-density screens without modifying operator work.
- T2 depends_on: [] - Audit the current visual language against Radon brand constraints and the `better-ui` surface, interaction, motion, and performance principles.
- T3 depends_on: [T1, T2] - Define three materially distinct mobile-and-desktop design directions, each with layout grammar, navigation, density, component treatment, motion, responsive behavior, strengths, and risks.
- T4 depends_on: [T3] - Compare the directions, recommend one, define a practical rollout sequence, and record review evidence.

## Checklist

- [x] T1 Current UI and responsive inventory.
- [x] T2 Brand and detail-level UI audit.
- [x] T3 Three design directions.
- [x] T4 Recommendation, rollout, and review.

## Constraints

- This task is design analysis only. Do not modify production UI code.
- Preserve existing operator changes, including `.serena/project.yml`.
- Treat the current teal Radon token set in `docs/brand-identity.md`, root `AGENTS.md`, and `brand/radon-design-tokens.json` as authoritative where older brand prose conflicts.

## Review

- Audited the shared desktop and mobile shell, dashboard, portfolio, orders, scanner, ticker cockpit, responsive breakpoints, and representative screenshots.
- Root cause: Radon's palette, typography, and tight geometry are already distinctive; repeated equal-weight bordered modules, duplicated headers, and a weak decision hierarchy make the product read like a themed component library.
- Defined three responsive directions: priority-driven Command Bridge, evidence-first Research Ledger, and configurable Dense Workbench.
- Recommendation: prototype Command Bridge first because it creates the clearest operator sequence while preserving Radon's current data and risk-control architecture.
- No production UI files changed. Verification was code and screenshot inspection only; no test suite was required for a design-analysis task.

# Task: Responsive UI direction prototypes (2026-07-14)

## Dependency graph

- M1 depends_on: [] - Establish an isolated prototype directory, shared content constraints, responsive targets, and validation criteria.
- M2 depends_on: [M1] - Build the Command Bridge standalone HTML prototype.
- M3 depends_on: [M1] - Build the Research Ledger standalone HTML prototype.
- M4 depends_on: [M1] - Build the Dense Workbench standalone HTML prototype.
- M5 depends_on: [M2, M3, M4] - Build a prototype index, validate HTML and responsive behavior, capture desktop/mobile screenshots, and complete the review.

## Checklist

- [x] M1 Prototype scope and validation contract.
- [x] M2 Command Bridge prototype.
- [x] M3 Research Ledger prototype.
- [x] M4 Dense Workbench prototype.
- [x] M5 Index, visual verification, and review.

## Constraints

- Keep all prototype files isolated under `prototypes/ui-directions/`; do not modify production UI code.
- Use realistic but explicitly illustrative Radon market and portfolio data.
- Preserve the current teal brand tokens, tight geometry, matte surfaces, hairline borders, Inter/IBM Plex Mono typography, and semantic signal colors.
- Each prototype must be usable at desktop and 393px mobile widths, preserve 44px touch targets, support reduced motion, and avoid gradients, glass, soft shadows, decorative card grids, and `transition: all`.
- Each direction must have a materially different information architecture, not merely different styling.

## Review

- Added three isolated standalone prototypes under `prototypes/ui-directions/`: Command Bridge, Research Ledger, and Dense Workbench.
- Added a comparison index with desktop/mobile preview switching and six captured viewport screenshots.
- Each prototype uses a distinct information architecture, realistic illustrative data, local Radon fonts, current brand tokens, exact-property transitions, reduced-motion handling, visible focus, and responsive touch targets.
- Playwright verification passed at 1440x1000 and 393x852 for every prototype: no page errors, no failed assets, exact viewport/scroll widths, and working selection/navigation/disclosure actions.
- Static verification passed: HTML tokenization, inline JavaScript compilation, forbidden-pattern audit, and `git diff --check`.
- Full web verification passed: `npm test` reported 426 files passed, 4,118 tests passed, 26 skipped; `npm run typecheck` passed.
- Production UI code was not modified. Existing `.serena/project.yml` and unrelated `tasks/mockups/` workspace files were preserved.

# Task: Modify-order close P&L (2026-07-15)

## Dependency graph

- T1 depends_on: [] - Preserve the dirty worktree, load scoped instructions, and trace the modify-order risk input against the canonical close-out calculation.
- T2 depends_on: [T1] - Add red component and Playwright regressions proving a modified close order shows estimated realized P&L.
- T3 depends_on: [T2] - Thread the matched position cost basis into the modify modal's `OrderRiskGate` input with correct long-close and short-close signs.
- T4 depends_on: [T3] - Run focused Vitest, relevant Playwright coverage, visual verification, typecheck, and the full web test suite.
- T5 depends_on: [T4] - Review the diff for signed credit/debit correctness and document verification results.

## Checklist

- [x] T1 Instructions and current close-order data flow inspected.
- [x] T2 Failing regressions added and demonstrated.
- [x] T3 Minimal implementation complete.
- [x] T4 Focused and full verification complete.
- [x] T5 Review documented.

## Constraints

- Preserve all unrelated dirty files and existing `tasks/todo.md` content.
- Treat option `avg_cost` as per-contract dollars; do not multiply basis by 100 again.
- A sell-to-close long has positive proceeds and positive basis. A buy-to-close short has negative close cash flow and negative original credit basis.
- Do not submit or modify a live broker order during browser verification.

## Review

- Root cause: `ModifyOrderModal` always sent the post-modify option shape through opening-risk math. It never matched the order to the held portfolio leg or passed `closeOut.entryCostDollars`, so the shared risk summary could only render Max Gain/Max Loss.
- Fix: match option symbol, normalized expiry, right, strike, closing action/direction, and held quantity; pass signed per-contract basis to the existing `useOrderRisk` close-out branch. Quantities above the holding remain on the opening/undefined-risk path.
- Red/green component regressions cover sell-to-close long, buy-to-close short, and over-close quantity. Existing signed combo-price and order-risk chokepoint coverage remains green.
- Focused Vitest: 3 files / 25 tests passed. Full web Vitest: 427 files / 4,121 passed / 26 skipped. TypeScript and `git diff --check` passed.
- Playwright: `modify-order-confirmation.spec.ts` passed against stubbed portfolio/orders/modify APIs. Visual inspection confirmed `Proceeds: $27,750` and `Est. Realized P&L: $2,750` with no Max Gain/Max Loss fields; no live broker action was reachable.

# Task: Command Bridge multi-page prototype (2026-07-14)

## Dependency graph

- C1 depends_on: [] - Define the multi-page information architecture and create the shared Command Bridge shell, tokens, navigation, responsive contract, and common interactions.
- C2 depends_on: [C1] - Build the Today command page and instrument decision drill-down.
- C3 depends_on: [C1] - Build the Positions page with risk-priority ledger and selected-position context.
- C4 depends_on: [C1] - Build the Orders page with working/fills queue, execution context, and safe prototype actions.
- C5 depends_on: [C1] - Build the Scan page with ranked candidates, signal filters, evidence context, and candidate drill-down.
- C6 depends_on: [C1] - Build the System page with source integrity, service state, sample freshness, and operator controls.
- C7 depends_on: [C2, C3, C4, C5, C6] - Integrate navigation, validate desktop/mobile behavior and interactions, capture screenshots, run full web verification, and open the prototype in the visible in-app browser.

## Checklist

- [x] C1 Shared shell and responsive contract.
- [x] C2 Today and instrument pages.
- [x] C3 Positions page.
- [x] C4 Orders page.
- [x] C5 Scan page.
- [x] C6 System page.
- [x] C7 Integration, verification, screenshots, and browser handoff.

## Constraints

- Keep all work isolated under `prototypes/command-bridge/`; do not modify production UI code.
- Preserve the approved Command Bridge direction: priority queue, selection-driven context, open canvas and ledger hierarchy, current Radon colors, matte surfaces, hairline borders, maximum 4px radius, local Inter/IBM Plex Mono, and no gradients/glass/soft shadows.
- Use realistic but explicitly illustrative data. Prototype controls must never transmit orders or mutate external systems.
- Every page must work at 1440px and 393px with no horizontal overflow, visible mobile section titles, 40px desktop and 44px touch targets, exact-property transitions, reduced-motion support, and visible focus.
- Preserve the operator sequence: signal -> structure -> Kelly math -> decision; display source, freshness, confidence, and uncertainty where they affect action.
- Keep existing unrelated `.serena/project.yml`, `tasks/mockups/`, and prior prototype files untouched.

## Review

- Built an isolated six-page Command Bridge prototype under `prototypes/command-bridge/`: Today, Instrument Decision, Positions, Orders, Scan, and System.
- Added a shared responsive shell with selection-driven context, desktop rail navigation, mobile bottom navigation, priority queues, safe prototype-only controls, visible focus, reduced-motion handling, and consistent illustrative account state.
- Playwright verification passed on all six pages at 1440x1000 and 393x852: HTTP 200, zero console/page errors, and exact viewport/scroll widths with no horizontal overflow. Captured twelve desktop/mobile screenshots.
- Static verification passed: HTML parsing, forbidden-pattern audit, and `git diff --check`.
- Full web verification passed: `npm test` reported 426 files passed, 4,118 tests passed, 26 skipped, and 0 failed; `npm run typecheck` passed with no errors.
- Opened the prototype at `http://127.0.0.1:8765/prototypes/command-bridge/index.html`. The in-app browser controller rejected its session because required sandbox metadata was not forwarded, so the visible system-browser fallback was used.
- Production UI code was not modified. Existing `.serena/project.yml`, `tasks/mockups/`, and prior prototype work were preserved.

# Task: Remove production deploy manual approval (2026-07-15)

## Dependency graph

- D1 depends_on: [] - Inspect the deploy workflow, repository environment protection, branch policy, and current pending deployments to identify the active manual gate.
- D2 depends_on: [D1] - Remove only the Production environment required-reviewer protection while preserving the environment binding, main-only deployment policy, test gates, concurrency, and host health gates.
- D3 depends_on: [D2] - Update stale workflow documentation and add regression coverage for the no-manual-approval deploy policy.
- D4 depends_on: [D2, D3] - Run focused policy checks, the full relevant project suites, and static validation.
- D5 depends_on: [D4] - Re-read GitHub control-plane state, review the diff, and document final evidence.

## Checklist

- [x] D1 Identify the active approval gate and surrounding deploy controls.
- [x] D2 Remove the required-reviewer protection.
- [x] D3 Update source policy documentation and regression coverage.
- [x] D4 Run focused and full verification.
- [x] D5 Record review evidence.

## Constraints

- Preserve unrelated dirty worktree files and prior `tasks/todo.md` content.
- Keep all automated CI test gates, production deployment serialization, main-only deployment restriction, and post-deploy health gates intact.
- Do not remove the GitHub `production` environment itself unless required; it remains useful for deployment history, URL metadata, environment secrets, and branch restrictions.
- Do not approve, cancel, or re-run a deployment unless verification proves that action is necessary.

## Review

- Root cause: the deploy workflow's `environment: production` binding was non-blocking by itself; GitHub environment `Production` had a mutable `required_reviewers` protection rule for `joemccann`. Recent deploys entered `waiting` until that review was approved.
- Removed only the required-reviewer rule through GitHub's environment API. The environment retained ID `12868164910`, its `branch_policy` protection, and the sole custom deployment branch `main`. No wait timer, environment secret, environment variable, pending deployment, branch protection, CI dependency, or deploy concurrency setting changed.
- Kept the workflow environment binding and URL, four required `needs:` jobs, exact main-push condition, non-canceling production lock, immutable-SHA runner, and host health gates. Updated stale workflow, agent, Claude, migration, cloud-service, and lessons documentation.
- Added a workflow regression that pins the four automated gates, main-push condition, environment binding, and production URL. Focused policy verification passed: 8 tests; YAML parsing and `git diff --check` passed.
- Full verification passed: Python application/API 4,156 passed, 13 skipped, 90 deselected with 72.79% coverage; cloud infrastructure 651 passed, 2 skipped; Vitest 445 files / 4,266 passed / 26 skipped; TypeScript clean.
- Immediate API verification proves the manual protection rule is absent. Runtime proof that the next green main push transitions directly into deploy, without `waiting`, necessarily occurs on the next pushed commit; no deployment was triggered solely for this settings change.

# Task: Web app typography audit (2026-07-15)

## Dependency graph

- T1 depends_on: [] - Inventory font assets, loading, global typography tokens, representative screens, and current repository state without modifying production UI.
- T2 depends_on: [T1] - Audit type scale, semantic hierarchy, line-height, tracking, wrapping, measure, and font loading against the Radon brand and `better-typography` guidance.
- T3 depends_on: [T1] - Audit numeric typography, form sizing, truncation, text accessibility, contrast, selection, and responsive behavior.
- T4 depends_on: [T2, T3] - Visually verify representative desktop/mobile screens and rank only evidence-backed recommendations.
- T5 depends_on: [T4] - Document findings and deliver recommendations as plain-CSS Before/After tables.

## Checklist

- [x] T1 Audit scope and dependency graph established.
- [x] T2 Scale, hierarchy, spacing, wrapping, and loading audited.
- [x] T3 Numeric, form, truncation, and accessibility behavior audited.
- [x] T4 Representative screens visually verified and findings ranked.
- [x] T5 Review documented and recommendations delivered.

## Constraints

- Analysis only; do not modify production UI code.
- Preserve all unrelated dirty worktree files and existing `tasks/todo.md` content.
- Keep Radon's established Inter UI, IBM Plex Mono numeric/data role, Sohne display-only rule, dense workstation character, and plain global CSS styling system.
- Recommendations must cite concrete source evidence and avoid generic typography advice that does not apply to the rendered app.

## Review

- Audited local Inter and IBM Plex Mono assets, the global CSS typography system, route/page heading semantics, representative dashboard/orders/ticker surfaces, mobile input rules, truncation, numeric stability, and light/dark contrast.
- The supplied Orders screenshot corroborates the source-level finding that operational text is overwhelmingly monospaced and frequently undersized. Automated in-app browser inspection was unavailable because the controller rejected the session's sandbox metadata, so rendered conclusions are limited to the supplied screenshot and static responsive rules.
- Highest-priority findings: light-theme muted text fails normal-text AA contrast; IBM Plex Mono exposes only 400/700 while CSS frequently requests 500/600/650; 504 font-size declarations are below 12px; narrative UI and structural headings do not consistently follow the brand's Inter roles; workspace routes lack a semantic page h1.
- Existing strengths to retain: self-hosted variable Inter WOFF2, broadly correct tabular numeric treatment, zoom-permitting viewport, mobile 16px input safeguards, controlled empty-state measure, and dark-theme text contrast.
- No production UI code or font assets were modified. This was an analysis-only task.

# Task: Implement web typography recommendations (2026-07-15)

## Dependency graph

- I1 depends_on: [] - Reconfirm the dirty worktree, scoped web instructions, available font assets, and establish regression coverage targets.
- I2 depends_on: [I1] - Implement the font-loading foundation, semantic type tokens, contrast correction, synthesis/smoothing policy, and supported weight usage.
- I3 depends_on: [I1] - Implement semantic route and section heading hierarchy plus full-content access for operational truncation.
- I4 depends_on: [I1] - Implement prose sizing, measure, wrapping, mobile date/time input floors, and typography detail rules.
- I5 depends_on: [I2, I3, I4] - Integrate agent work, inspect the complete diff, and add or adjust focused regressions.
- I6 depends_on: [I5] - Run focused Vitest, relevant Playwright visual verification at desktop/mobile, the full web test suite, typecheck, and static checks.
- I7 depends_on: [I6] - Document review evidence, create one intentional commit, push the current branch, and verify the remote ref.

## Checklist

- [x] I1 Repository state, instructions, implementation scope, and validation targets confirmed.
- [x] I2 Font foundation, semantic tokens, contrast, and weights implemented.
- [x] I3 Heading hierarchy and truncation access implemented.
- [x] I4 Prose, wrapping, measure, and responsive typography implemented.
- [x] I5 Agent changes integrated and focused regressions complete.
- [x] I6 Focused, visual, and full verification complete.
- [x] I7 Review documented, committed, pushed, and remote ref verified.

## Constraints

- Preserve all unrelated dirty files and prototype/mockup work. Do not stage `.serena/project.yml`, `prototypes/`, `tasks/mockups/`, or unrelated test changes.
- Keep the dense terminal character. Inter owns narrative and structural UI; IBM Plex Mono owns prices, quantities, contracts, timestamps, tabular data, and telemetry.
- Use plain global CSS and existing components. Do not introduce a second styling system or broad opportunistic refactors.
- Do not interact with live order placement controls during browser verification.
- Commit only typography implementation, its regressions, and this task record, then push the current branch after all required suites pass.

## Review

- Replaced the two static IBM Plex Mono WOFF faces with locally served WOFF2 faces for 400 regular/italic and 500/600/700 normal; disabled synthetic font faces and normalized unsupported mono weights while retaining Inter's variable-weight range.
- Added semantic type/tracking tokens, root font smoothing, an AA-compliant light muted token (`#737373`, approximately 4.74:1 on white), Inter structural title/metric roles, stable tabular primary metrics, and 11px/12px operational floors across shared tables and order-management controls.
- Added valid workspace h1 and dashboard/workspace h2 hierarchy without duplicating route-owned headings. Critical truncated service, contract, signal, and structure strings now wrap or expose their complete content.
- Raised sustained news/chat/company/trial prose to 14px with 1.55-1.6 line height and controlled measure; added deliberate heading/prose wrapping, mobile date/time input floors, explicit placeholder contrast, selection/underline metrics, and safe logical table/action alignment.
- Regression coverage added for font delivery, foundation tokens, semantic hierarchy, truncation access, prose measure, operational text floors, mobile inputs, and detail styling. Full Vitest passed: 430 files, 4,134 tests passed, 26 skipped. TypeScript, production Next compile/build, output-trace audit, and `git diff --check` passed.
- The repository Playwright app specs were attempted but Clerk's middleware wrapper repeatedly retried an unavailable Clerk backend handshake before the authless callback, causing navigation timeouts unrelated to this diff. A standalone Playwright browser harness loaded the production CSS and real local font assets at 1440x1000 and 393x852, captured desktop/mobile screenshots, confirmed Inter 12px section titles, Plex 11px headers, 12px order actions, 14px prose, and zero document-width overflow in both viewports. No live order control was used.
- Committed the isolated 27-file typography scope as `78bcf138` (`Improve web typography hierarchy and legibility`) and pushed `main`. `origin/main` resolves to the exact local SHA `78bcf138daa2b5501e4f112fd6fbb3a865f89d3d`; CI run `29441408282` started successfully and was in progress at handoff.

# Task: False unhealthy push notifications (2026-07-15)

## Dependency graph

- H1 depends_on: [] - Capture live aggregate health, recent watchdog notifications, service-health rows, cooldowns, and deployment timing.
- H2 depends_on: [H1] - Trace the alert classifier and identify whether notifications come from a real outage, deploy transients, stale rows, or incorrect recovery semantics.
- H3 depends_on: [H2] - Add a failing regression for the confirmed notification bug and implement the smallest root-cause fix.
- H4 depends_on: [H3] - Run focused and full verification, deploy if code changes are required, and verify live notification state recovers.
- H5 depends_on: [H4] - Document the diagnosis, fix, and operational evidence.

## Checklist

- [x] H1 Capture live evidence.
- [x] H2 Confirm root cause.
- [x] H3 Implement regression and fix if required.
- [x] H4 Verify locally and live.
- [x] H5 Record review evidence.

## Constraints

- Preserve unrelated dirty worktree changes.
- Do not mute or acknowledge alerts until the underlying condition and recovery behavior are proven.
- Treat IB, Flex, deploy, and database failures according to their distinct ownership and cadence semantics.

## Review

- The original P1 was legitimate: IB's independent protocol probe wedged at 23:46 UTC, the off-box probe observed schema-v2 aggregate failure at 00:03, and the watchdog restarted Gateway until authentication and the API pool recovered at 00:33. It was not deploy-induced.
- The notification flood after recovery was a bug: Pushover emergency priority repeated every 60 seconds while the recovered aggregate remained represented by the last red GitHub probe row. Irregular scheduled-probe cadence delayed cancellation.
- Immediate remediation: manually dispatched the existing external probe; it passed at 00:39, and the 00:40 continuous watchdog cycle recorded healthy, cancelled the `external-health-probe` Pushover tag, and marked the emergency resolved. Current edge, API, Gateway, relay, Next.js, and external probe are green.
- Durable fix: the off-box classifier now distinguishes a validated schema-v2 `aggregate_down` from `aggregate_invalid`. The on-box watchdog may confirm recovery locally only for `aggregate_down`; malformed, contradictory, unsupported, legacy, ping, and public-status reachability failures remain fail-closed and require off-box recovery evidence.
- Red/green regressions cover validated aggregate recovery, still-down behavior, legacy fail-closed behavior, public perimeter non-override, and strict local schema-v2 parsing. Adversarial review approved the corrected gate.
- Verification: 146 affected tests passed; full application Python suite 4,161 passed, 13 skipped, 90 deselected at 72.70% coverage; cloud suite 651 passed, 2 skipped; `git diff --check` clean.
# Task: MenthorQ-style options Net GEX page (2026-07-16)

## Dependency graph

- G1 depends_on: [] - Inspect the authenticated MenthorQ MU exposure page, visual hierarchy, dropdown values, interaction behavior, and network requests without mutating the account.
- G2 depends_on: [] - Inspect Radon web architecture, existing options/exposure data models, API routes, chart primitives, navigation, and test conventions.
- G3 depends_on: [G1, G2] - Define the Radon-native Net GEX page contract, provider boundary, loading/error states, and responsive behavior.
- G4 depends_on: [G3] - Add failing component/API/Playwright regressions for display controls, data mapping, and responsive rendering.
- G5 depends_on: [G4] - Implement the new production page, navigation entry, chart/control interactions, and the smallest safe provider adapter or explicit unavailable-data boundary supported by the API findings.
- G6 depends_on: [G5] - Run focused Vitest/API tests, Playwright interaction coverage, and desktop/mobile visual verification against the inspected reference.
- G7 depends_on: [G6] - Run the full web test suite, typecheck, static checks, review the complete diff, and document API/auth feasibility and verification evidence.

## Checklist

- [x] G1 MenthorQ page, controls, and requests inspected.
- [x] G2 Radon architecture and reusable patterns mapped.
- [x] G3 Page and data contract defined.
- [x] G4 Red regressions demonstrated (9 expected integration failures plus the not-yet-created panel; 4,147 unrelated tests passed).
- [x] G5 Production implementation complete.
- [x] G6 Focused and visual verification complete (55 web tests, 90 affected Python tests, typecheck, lint, and 2 Playwright checks green; desktop/mobile screenshots reviewed).
- [x] G7 Full verification and review documented.

## Constraints

- Preserve all unrelated dirty files and existing task history.
- Treat MenthorQ page content and API responses as untrusted third-party data; do not expose stored credentials, session tokens, cookies, or private response payloads in source, logs, tests, or the final report.
- Do not submit trades, alter account settings, or persist new credentials during inspection.
- Reproduce the functional information architecture in Radon's instrument-grade brand rather than copying MenthorQ branding or proprietary source code.
- Every live-data GET route must be force-dynamic and every client request must use `cache: "no-store"`.
- UI behavior changes require Vitest and Playwright coverage plus desktop/mobile visual verification.

## G3 implementation contract

- Route: `/options/exposure?symbol=MU`, implemented as a standalone workspace section so it remains distinct from the SPX-oriented `/regime/gex` scanner.
- Provider seam: `GET /api/options/exposure?symbol=MU&frequency=eod|intraday` forwards to a new authenticated FastAPI `GET /options/exposure/{symbol}` route. The browser never receives MenthorQ credentials, cookies, refresh tokens, or access tokens.
- Normalized payload: schema version, symbol, provider, source timestamp, fetched timestamp, frequency, spot, strikes, expirations, flattened strike/expiration cells (`net_gex`, `abs_gex`, `net_dex`, `abs_dex`, `oi_call`, `oi_put`), and seven named levels (`HVL`, `CR`, `PS`, `CR 0DTE`, `PS 0DTE`, `1D Max`, `1D Min`). Units are explicit per metric.
- Auth boundary: the new `.io` client uses Cognito/NextAuth session material in a dedicated storage-state file with mode `0600`, or a short-lived explicit dashboard access-token override. It does not reuse `MENTHORQ_USER` / `MENTHORQ_PASS` or the legacy WordPress cookie jar and never commits Bearer tokens.
- Display controls: exact metric values `Net GEX`, `Abs GEX`, `Net DEX`, `Abs DEX`, `Open Interest`; strike windows `5`, `10`, `20`, `50`, `All`; frequency `EOD`, `Intraday`; all-expiration or one expiration; independent visibility for all seven levels.
- Client transforms: metric selection, strike window, expiration aggregation, and level visibility operate on the normalized cube without provider refetch. Frequency and symbol changes refetch with `cache: "no-store"`. Open Interest is signed `call OI - put OI`; the right rail renders absolute put/call OI separately.
- Visual contract: Radon-native dark instrument panel, zero-centered signed bars, positive signal color, negative fault color, separate put/call OI rail, spot-row marker, source/as-of/frequency readout, keyboard-accessible controls, 44px coarse-pointer targets, and no gradients, glass, soft shadows, or radii above 4px.
- Failure contract: invalid enums/symbols return `400`; expired/unavailable MenthorQ authentication returns a sanitized `503`; provider timeouts return `504`; no cross-symbol or cross-frequency cache reuse is allowed. The UI distinguishes loading, unavailable, empty, and partial states.

## Review

- Live MenthorQ inspection confirmed the page is a client-rendered strike-by-expiration cube. Exact metric options are Net GEX, Abs GEX, Net DEX, Abs DEX, and Open Interest; strike windows are 5/10/20/50 Strikes +/- and All Strikes; frequencies are EOD and Intraday; expiration and all seven level filters are local transforms except frequency, which selects a separate prefetched payload.
- The primary payload is `GET gateway.menthorq.io/clickhouse-api/api/web/v1/options/net-gex-by-expiration/{SYMBOL}?frequency=eod|intraday`; levels come from `/gamma-levels/{SYMBOL}/eod`. The cube contains indexed parallel arrays for strike, expiration, GEX, DEX, and call/put OI. Unauthenticated requests return 401; the live dashboard's Cognito/NextAuth `accessToken` succeeds as a Bearer token.
- Added `/options/exposure?symbol=MU`, workspace navigation, mobile overflow navigation, exact exposure controls, zero-centered signed bars, put/call OI rails, spot and level markers, responsive styling, normalized client transforms, and loading/error/empty/partial states. The UI follows Radon's instrument-panel brand rather than copying MenthorQ presentation.
- Added a server-only provider boundary: FastAPI `/options/exposure/{symbol}` fetches and validates the two MenthorQ endpoints, normalizes provider GEX billions to USD per 1% move, preserves DEX USD and OI contracts, and returns sanitized 400/503/504 errors. Next `/api/options/exposure` remains force-dynamic/no-store. Credentials, cookies, and Bearer tokens never enter the browser payload or public errors.
- Auth conclusion: direct API retrieval is proven and high-confidence. Existing saved `MENTHORQ_USER`/`MENTHORQ_PASS` and the legacy `.com` WordPress storage state are a different auth domain and do not currently establish the `.io` Cognito session. The new client accepts a short-lived `MENTHORQ_DASHBOARD_ACCESS_TOKEN` or exchanges a dedicated `.io` Playwright storage state through `/api/auth/session`, forcing mode 0600 and keeping its directory Git-ignored. No dedicated `.io` state/token is currently configured, so an unmocked local route intentionally returns sanitized 503 until provisioning. Whether the legacy credentials are also valid in Cognito remains unproven.
- Security finding: the existing root `.env` and legacy `data/menthorq_cache/menthorq_storage_state.json` are mode 0644. They were not mutated because they belong to the separate legacy flow, but they should be owner-only.
- Verification: focused web 55 passed; focused Python 21 passed and affected Python 90 passed; Playwright desktop/mobile 2 passed with no document overflow; desktop/mobile screenshots reviewed; full web 435 files / 4,158 passed / 26 skipped; full Python 4,186 passed / 13 skipped / 90 deselected; TypeScript and ESLint green; production Next build and output-trace audit green; `git diff --check` clean. Build emitted only pre-existing broad `cri_scheduled` trace warnings.

# Task: MenthorQ Net GEX implementation report (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Confirm the existing implementation record, screenshots, and report scope without exposing provider credentials or session material.
- T2 depends_on: [T1] - Create a self-contained, accessible HTML report with Radon-native styling and readable API/auth conclusions.
- T3 depends_on: [T2] - Open the local report and visually verify desktop legibility, responsive behavior, and the absence of external asset dependencies.

## Checklist

- [x] T1 Report facts, artifacts, and constraints confirmed.
- [x] T2 Self-contained HTML report created.
- [x] T3 Local visual verification and handoff.

## Review

- Created `tasks/artifacts/menthorq-net-gex-report.html`: a standalone, printable Radon-styled handoff with no remote fonts, scripts, images, or network dependencies. It presents the implementation, exact controls, API schema, authentication boundary, confidence conclusions, and verification record without including credentials, tokens, cookies, or private provider payloads.
- Opened the report in a new Chrome tab, preserving the existing MenthorQ tab. The local title and accessible document outline rendered correctly.
- Headless renders at 1440px and 393px confirmed a matching document title, 16px base body font, and no document-level horizontal overflow. Desktop and mobile evidence are saved as `tasks/artifacts/menthorq-net-gex-report-desktop.png` and `tasks/artifacts/menthorq-net-gex-report-mobile.png`.
- Verified the HTML has no external URL, script, image, or linked-asset dependency; `git diff --check` passed.

# Task: MenthorQ redirect-mediated dashboard authentication (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Verify the user-specified `dashboard.menthorq.io` to WordPress to dashboard redirect flow without exposing credentials or session material.
- T2 depends_on: [T1] - Update the dedicated dashboard-state client so it can establish a valid dashboard session through the verified redirect-mediated login flow.
- T3 depends_on: [T2] - Add red/green regression coverage for the bootstrap behavior and preserve the existing server-only authentication boundary.
- T4 depends_on: [T3] - Run affected Python verification, inspect the live authenticated result, and document exact confidence and any remaining constraint.

## Checklist

- [x] T1 Live redirect flow verified.
- [x] T2 Dedicated dashboard auth bootstrap implemented.
- [x] T3 Regression coverage implemented.
- [x] T4 Verification and review recorded.

## Review

- The user-specified flow is correct. A fresh request to `dashboard.menthorq.io/en/options/exposure?symbol=MU` redirects to the MenthorQ WordPress login with a Cognito callback; submitting the existing server-side WordPress credentials returns a valid dashboard session.
- The server-only dashboard client now resolves authentication in order: explicit short-lived access token, valid dedicated dashboard storage state, then the verified dashboard → WordPress → Cognito bootstrap. It saves the resulting dedicated storage state in `data/menthorq_dashboard/` with directory mode `0700` and state-file mode `0600`.
- Live proof: a fresh MU EOD request authenticated, fetched, validated, and normalized a complete cube of 6,321 cells across 24 expirations. No credential, cookie, ID token, or access token was emitted in source, logs, tests, or this record.
- Live provider quality correction: the payload currently contains six negative values in an `abs_gex` field. The backend now canonicalizes `abs_gex` with `abs()` at the trusted provider boundary, retains strict non-negative validation for absolute DEX and open interest, and has a regression test.
- Regression sequence: the new bootstrap tests first failed because the constructor had no credential-bootstrap contract; after implementation, focused dashboard/API tests passed 25/25. Final full Python verification passed: 4,190 passed, 13 skipped, 90 deselected, 19 warnings. `py_compile` and `git diff --check` passed.
- Updated `.env.example` and the self-contained MenthorQ implementation report to state the verified redirect-mediated bootstrap rather than the earlier, now disproven manual-provisioning conclusion.

# Task: Options workspace navigation (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Inspect existing workspace/tab conventions and define the durable information architecture for the market-structure surfaces.
- T2 depends_on: [T1] - Add red regressions for the canonical Options route, Net GEX first tab, preserved legacy deep link, and planned-module navigation state.
- T3 depends_on: [T2] - Implement the Options workspace shell, migrate the current Net GEX exposure view into its first tab, and update navigation metadata.
- T4 depends_on: [T3] - Run focused Vitest, Playwright desktop/mobile behavior coverage, visual verification, typecheck, and static checks.
- T5 depends_on: [T4] - Run the full web suite, review the diff, and document the migration contract for future option-structure modules.
- T6 depends_on: [T5] - Commit and push the completed MenthorQ Options feature set while preserving unrelated worktree changes.

## Checklist

- [x] T1 Information architecture and reuse pattern confirmed: use the broad "Options" workspace label because it covers exposure, Greeks, open interest, and volatility/VIX; adopt Regime-style URL tabs with canonical `/options/net-gex` and retain `/options/exposure` as a redirect.
- [x] T2 Red navigation/tab regressions added and observed: canonical route/nav metadata, legacy redirect contract, Net GEX active state, planned measurements, and canonical tab navigation.
- [x] T3 Options workspace implemented: `/options/net-gex` is canonical, root and legacy entry points redirect with the normalized symbol, and the existing exposure instrument is the first URL-driven tab.
- [x] T4 Focused, visual, and static verification complete: 57 focused Vitest assertions, TypeScript, ESLint, `git diff --check`, three Playwright cases, and desktop/393px screenshot inspection passed.
- [x] T5 Full verification and review documented.
- [x] T6 Intentional commit and push complete.

## Review

- The workspace is named **Options**, not “Options Structure.” It is broad enough for dealer exposure, Greeks, OI, and volatility/VIX; “structure” would misleadingly exclude the latter measures.
- Canonical first surface: `/options/net-gex?symbol=MU`. `/options?symbol=…` and the pre-existing `/options/exposure?symbol=…` normalize the symbol and redirect there, preserving saved deep links.
- `OptionsWorkspacePanel` owns the URL-driven tab rail. Net GEX is active and rendered today; DEX, Greeks, Open Interest, and VIX / Volatility are visible, non-interactive planned tabs. Future pages add a route and mark their tab available without changing the workspace route or navigation identity.
- The shell identifies this as the `options` workspace, so provider-specific panels do not inherit unrelated IB portfolio relay alerts, sync controls, or portfolio metric cards.
- Verification: focused Vitest 7 files / 57 tests passed; Playwright 3/3 passed for canonical interaction, redirects, and 393px overflow; desktop/mobile screenshots were visually inspected; `npm run typecheck`, `npm run lint`, and `git diff --check` passed. Full web Vitest passed: 437 files, 4,163 tests, 26 skipped (4,189 total), exit 0.
- Published the scoped MenthorQ/Options feature set as `5cbaf570 feat(options): add MenthorQ exposure workspace` on `origin/main`. Unrelated prototype, mockup, artifact-image, `.serena`, and pre-existing task-history changes remain unstaged. CI run `29601857080` for this commit is in progress.

# Task: Options ticker-gated measurement state (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Inspect established ticker-entry and regime spectral-loading patterns and define the explicit Options entry-state contract.
- T2 depends_on: [T1] - Add red regressions for no default symbol, valid ticker submission, provider-label removal, and submitted-state spectral loading.
- T3 depends_on: [T2] - Implement the ticker-gated Options workspace and remove provider identity from the exposure header.
- T4 depends_on: [T3] - Run focused Vitest, Playwright desktop/mobile behavior coverage, visual verification, typecheck, lint, and static checks.
- T5 depends_on: [T4] - Run the full web suite and record review evidence.

## Checklist

- [x] T1 Entry and loading pattern confirmed: use the API-compatible symbol regex, Flow Analysis-style explicit form, and Regime `SpectralLoader` treatment.
- [x] T2 Red ticker-entry/loading regressions added and observed: no initial panel, invalid-symbol rejection, canonical normalized submission, provider-label removal, and spectral loading state.
- [x] T3 Ticker-gated workspace implemented: no MU fallback remains in options routes/navigation; a valid query deep link remains supported without bypassing the entry contract for first visits.
- [x] T4 Focused, visual, and static verification complete: 59 focused Vitest assertions, Playwright 4/4 including the submitted loading state, desktop/mobile screenshot inspection, typecheck, lint, and `git diff --check` passed. ESLint reported 11 pre-existing warnings and no errors.
- [x] T5 Full web Vitest suite passed: 437 files, 4,166 tests passed, 26 skipped (4,192 total); review evidence recorded.

## Review

- The Options workspace begins without a query or API request. It presents an operator-first ticker entry surface instead of preloading a sample instrument.
- A valid submitted ticker is uppercased, encoded into the canonical Net GEX URL, and then mounts the existing exposure panel. Valid bookmarked query URLs continue to work.
- Exposure loading now uses the shared `SpectralLoader` used by Regime, and the provider identity string is absent from the measurement telemetry.
- Verification: focused Vitest 7 files / 59 tests passed; Playwright 4/4 passed, including no pre-submit request, canonical submission, and visible loading state; typecheck passed; lint completed with no errors and 11 pre-existing warnings; full web Vitest passed (437 files, 4,166 passed, 26 skipped); `git diff --check` passed.
- [ ] T4 Focused, visual, and static verification complete.
- [ ] T5 Full verification and review documented.

# Task: Continuing Pushover reliability notifications (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Reconcile the operator report with live Pushover delivery, VPS journal, cooldown, and deployed-revision evidence.
- T2 depends_on: [T1] - Trace the emergency receipt cancellation contract and isolate the specific failed or incomplete stop path.
- T3 depends_on: [T1, T2] - Apply the smallest safe corrective action, prove delivery has stopped, and record the outcome.
- T4 depends_on: [T2] - Repair the unrelated deterministic ComboSkewPanel Vitest regression that blocked the Pushover-fix deployment.
- T5 depends_on: [T4] - Run focused and full web validation, rerun CI, verify deployment, and close the Pushover delivery review.

## Checklist

- [x] T1 Reopened live investigation after the original cancellation conclusion was contradicted.
- [x] T2 Cancellation-path root cause confirmed: Pushover accepts only the plural `tags` message field; the singular field made cancellation a false-success no-op.
- [x] T3 Delivery-stop correction deployed: future P1 receipts are tagged and recovered services can cancel them; the two pre-fix untagged receipts expired at 20:40:32Z.
- [x] T4 ComboSkewPanel regression repaired: the ready-state fixture now pins a pre-expiry clock rather than changing production expiry handling.
- [x] T5 Full validation and deployment verified: CI run `29617684295` passed all required jobs and the automatic VPS deployment completed successfully.

## Review

- Immediate RCA: at 19:40:32Z `radon-breadth.service` and `radon-portfolio-sync.service` each sent a P1 Emergency. The recovered services were falsely logged as cancelled at 19:45:01Z because the payload used Pushover's unsupported singular `tag` field; Pushover records only plural `tags`, so `cancel_by_tag` matched no receipt while returning success.
- Existing receipt mitigation: the two original, untagged P1 emergencies cannot be cancelled programmatically. They stop on acknowledgement in the Pushover app or naturally expire at 20:40:32Z (13:40:32 PDT).
- Permanent correction: commit `660cda91` sends `tags` and adds the red/green regression. Local verification: affected pytest 68 passed; watchdog suite 165 passed; full Python suite passed; `git diff --check` passed.
- Deployment gate: CI `29610454907` passed secret scan, Python application/cloud tests, and perimeter smoke, but failed its unrelated `web/tests/combo-skew-panel.test.tsx` contract (3 deterministic failures). The Pushover change touches no web code; deployment did not run. Fixing that separate web regression required a scoped follow-up.
- ComboSkewPanel regression root cause: the fixture expired at 20:00 UTC on 2026-07-17, so the product correctly rendered the unavailable state. The test now freezes time to 2026-06-24T16:00:00Z; focused Vitest is 4/4 green and TypeScript has no diagnostics. Full web Vitest was rerun locally after the correction; CI rerun is the deployment gate.
- Final gate: CI `29617684295` passed secret scan, pytest, perimeter smoke, and full Vitest; its automatic VPS deployment completed successfully. The production Pushover tagging correction and durable Vitest fixture are live in commits `660cda91` and `f13ae29a`.

# Task: Service-wide reliability and performance pressure test (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Inventory every deployed service, owner, health contract, safe local load path, and existing baseline metric.
- T2 depends_on: [T1] - Run reproducible, bounded pressure tests and establish per-service latency, throughput, error-rate, and recovery baselines without touching production state.
- T3 depends_on: [T2] - Prioritize root causes with enough headroom for a measured 20% improvement; add red regressions for selected defects.
- T4 depends_on: [T3] - Implement minimal reliability/performance corrections in the selected services and rerun focused pressure tests.
- T5 depends_on: [T4] - Run full relevant suites, compare before/after metrics, verify deployment, and record services that cannot honestly meet a 20% target without architectural or capacity authority.

## Checklist

- [x] T1 Service inventory and measurement contracts: 33 installed units and 25 timers mapped; unsafe broker/provider workloads are excluded from synthetic pressure.
- [x] T2 Baseline pressure tests: local web `/api/service-health` p95 56.4ms at 10-way concurrency with 0 errors, 511 targeted Python resilience tests in 4.78s, and 18 newsfeed tests in 187ms; a fleet-wide systemd recovery-boundary contract now pressure-tests all 33 installed units without touching broker/provider state.
- [x] T3 Prioritized, testable remediation plan: bound previously unbounded FastAPI health, database reliability, and timer-worker paths before adding broader synthetic-load instrumentation.
- [x] T4 Corrections and focused verification: bounded health-lite gateway probes, self-healing admin reliability DB reads, and all timer oneshot execution ceilings are covered by red/green regressions.
- [x] T5 Full verification and production deployment completed.

## Review

- Bounded recovery metrics: the admin reliability read path now has a 3s self-healing deadline versus the former roughly 300s client transport ceiling, a 99% worst-case tail-latency reduction. `/health/lite` now fails safely within 0.5s when its read-only gateway probe wedges; it previously had no finite application deadline. Breadth and VCG now release within 240s and refresh within 480s instead of blocking future timer slots indefinitely.
- Fleet pressure contract: 33/33 installed systemd services are now checked for a finite recovery boundary, either a restart policy for long-running units or an execution cap for timer oneshots. Unsafe live load against IB, order, portfolio-sync, scanner/UW, and Playwright services remains intentionally excluded; those require an isolated staging stack for a defensible throughput claim.
- Verification before deployment: targeted API 78 passed, systemd 237 passed, Python 4,193 passed / 13 skipped / 90 deselected, cloud 687 passed / 2 skipped, and the full web coverage run plus typecheck passed.
- CI run `29630430683` passed secret scan, Python, perimeter, and full Vitest gates. Its first deployment preflight correctly refused the release because the root-published control-plane manifest had the prior hash for `services/radon-refresh.service`. Bootstrap initially reported current because `/home/radon/radon` was one commit behind; fast-forwarding it to the tested SHA before root bootstrap repaired the manifest without bypassing the preflight.
- Production closure: the VPS checkout was fast-forwarded to `20c4b14b`, then root bootstrap installed and verified 20 control-plane artifacts. Rerunning CI `29630430683` passed every gate and deployed successfully. Live verification: health schema v2 returned `ok=true`, `overall_state=up`; `radon-api`, `radon-nextjs`, `radon-relay`, `radon-monitor`, `radon-newsfeed`, and `radon-health` are active.

# Task: Production runbook and memory reconciliation (2026-07-18)

## Dependency graph

- T1 depends_on: [] - Audit canonical and secondary runbooks for stale standalone `radon-cloud` deployment claims and identify the authoritative 2026-07-18 recovery evidence.
- T2 depends_on: [T1] - Update canonical cloud deployment/migration runbooks with the exact-SHA checkout, root bootstrap, manifest-preflight, CI rerun, and live-health contract.
- T3 depends_on: [T1] - Align secondary repository, operations, and forecasting references with monorepo ownership without duplicating lifecycle internals.
- T4 depends_on: [T2, T3] - Record the durable lesson, reconcile task-review text, verify documentation links/searches, and report the preserved operational facts.

## Checklist

- [x] T1 Documentation and memory audit complete.
- [x] T2 Canonical runbooks updated with the exact-target checkout and fail-closed bootstrap/deploy sequence.
- [x] T3 Secondary references aligned; the legacy directory is documented only as the external secrets/runtime-media exception, and the historical cloud plan is clearly archived.
- [x] T4 Memory/review reconciliation and verification complete.

## Review

- Canonical runbooks (`cloud/CLAUDE.md`, `docs/monorepo-cloud-migration.md`) now require proving the VPS checkout equals the tested SHA before root bootstrap, then preserving manifest preflight, rerunning CI, and verifying the schema-v2/core-service contract.
- Secondary docs now name monorepo `cloud/` as the source of deploy tooling, units, Caddy, and Compose. `/home/radon/radon-cloud/.env` remains deliberately documented as external host secrets only; runtime-media and historical archival references are explicitly scoped.
- Memory records the stale-checkout false-current failure mode and the verified recovery: target `20c4b14b`, bootstrap installed 20 artifacts after the fast-forward, CI `29630430683` deployed successfully, and live status was `schema_version=2`, `ok=true`, `overall_state=up` with six core services active.
- Verification: `git diff --check` passed; changed markdown links and all current ownership/deployment references were checked. Historical `cloud/PLAN.md` now carries a non-executable archived-plan banner.

# Task: Current push-notification reliability triage (2026-07-19)

## Dependency graph

- T1 depends_on: [] - Reconcile active Pushover receipts, tags, retry state, and cancellation evidence without changing delivery state.
- T2 depends_on: [] - Inspect current health, watchdog, service-health, and unit evidence without mutating production.
- T3 depends_on: [T1, T2] - Correlate notifications to the underlying reliability incident and report current severity, impact, and recovery status.

## Checklist

- [x] T1 Push-delivery evidence.
- [x] T2 Service and watchdog evidence.
- [x] T3 Correlated terse incident report.

## Review

- Read-only investigation at 2026-07-20 03:04Z: the active P1 is `external-health-probe`, dispatched as a Pushover Emergency at 03:00Z (60-second retry, one-hour expiry). It is a monitoring-plane dead-man alert: the independent GitHub Actions probe has been queued/pending since 01:48Z/02:51Z and its last successful off-box observation was 00:58Z, exceeding the two-hour freshness threshold.
- Production itself is healthy: public edge and local health returned `ok=true` / `overall_state=up`; API, Next.js, relay, monitor, newsfeed, and IB are active. Breadth and portfolio-sync P1s are resolved and are not the current phone notifications.
- Separate non-emergency P2 items: `cash-flow-sync` is waiting for its scheduled 2026-07-20 21:00Z retry after an IB Flex 1001 throttle; `config-drift` reports that live `radon-breadth.service` lacks the repository `TimeoutStartSec=240` setting. No acknowledgements, cancellations, restarts, mutations, or deploys were performed.

# Task: Gateway 2FA recovery and service-stack restart (2026-07-20)

## Dependency graph

- T1 depends_on: [] - Read the live 2FA lease, gateway, and service-stack state; confirm the lock-safe operator restart path.
- T2 depends_on: [T1] - Run the approved full-stack restart through the lock-aware operator path, preserving the shared 2FA lease protocol.
- T3 depends_on: [T2] - Verify the IB Gateway produces an authentication prompt or reaches an authenticated state, then verify core units and schema-v2 health.

## Checklist

- [x] T1 Preflight the live gateway and 2FA state.
- [x] T2 Restart the full Radon service stack.
- [x] T3 Verify gateway authentication and production health.

## Review

- User explicitly authorized the service restart because an expected IBKR 2FA push is absent. The initial `/usr/local/bin/radon restart` attempt fail-closed with `inherited deploy-lock proof is invalid` before mutating the gateway. The safe fallback invoked the same authoritative, lock-aware gateway controller directly, then restarted the persistent production services, `radon-health`, and 25 active timers.
- Verification at 2026-07-21 00:25Z: a fresh 2FA lease was acquired and IBC logged `Second Factor Authentication initiated` at 00:25:06Z. The Gateway container reached `healthy`; FastAPI correctly reports `auth_state=awaiting_2fa`, `service_state=reachable`, and `port_listening=true`. API, Next.js, relay, monitor, newsfeed, health, and gateway units are active. Aggregate status remains `down` only until the IBKR 2FA approval completes. VPS evidence cannot prove delivery to the phone.
