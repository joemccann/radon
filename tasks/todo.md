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
