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
- [ ] T8 Review documentation and final production checks.

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
