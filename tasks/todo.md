# TODO

## Session: Fix CTA Sync Wrapper Credential Mangling (2026-03-24)

### Goal
Restore fresh MenthorQ CTA cache updates by fixing the background/API CTA sync path so it preserves literal `.env` credential values instead of letting shell expansion corrupt `MENTHORQ_PASS`.

### Dependency Graph
- T1 (Reproduce the CTA wrapper env-loading bug and confirm the background sync path mangles `.env` values containing shell metacharacters) depends_on: []
- T2 (Add red regression coverage for the CTA sync wrapper so shell-metacharacter passwords survive into the Python runtime unchanged) depends_on: [T1]
- T3 (Patch `scripts/run_cta_sync.sh` to load `.env` values literally, without shell expansion or truncation) depends_on: [T2]
- T4 (Run focused verification, full required suites, and a live CTA sync/backfill check if auth succeeds) depends_on: [T3]

### Checklist
- [x] T1 Reproduce the CTA wrapper env-loading bug and confirm the background sync path mangles `.env` values containing shell metacharacters
- [x] T2 Add red regression coverage for the CTA sync wrapper so shell-metacharacter passwords survive into the Python runtime unchanged
- [x] T3 Patch `scripts/run_cta_sync.sh` to load `.env` values literally, without shell expansion or truncation
- [x] T4 Run focused verification, full required suites, and a live CTA sync/backfill check if auth succeeds

### Replan
- Live verification after the wrapper fix exposed a second blocker: direct `python3.13 scripts/fetch_menthorq_cta.py --date 2026-03-23 --json` no longer fails on login, but CTA extraction now fails with `No img src found for card slug: cta_table` and `Card not found for slug: ...`.
- New focus: capture the authenticated CTA page DOM/artifacts, add a regression for the current card structure, patch card discovery/download/screenshot logic, then rerun live fetch/sync verification.

### Review
- Root cause:
  - [scripts/run_cta_sync.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cta_sync.sh) loaded `.env` by rewriting it to a temp file and `source`-ing it in `bash`.
  - The repo’s current `MENTHORQ_PASS` is unquoted and contains shell metacharacters, so the wrapper-expanded value seen by the child Python process was shorter than the literal `.env` value.
  - This only affected the background/API CTA sync path; direct Python execution used [env_loader.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/env_loader.py) and preserved the full credential.
- Fix:
  - Replaced the shell `source`-based loader in [run_cta_sync.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cta_sync.sh) with a literal line parser that trims whitespace, strips matching quotes, and exports `KEY=VALUE` without evaluating shell syntax.
- Regression coverage:
  - Added [test_run_cta_sync_wrapper.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_run_cta_sync_wrapper.py), which runs the wrapper in a temp repo fixture with an unquoted password like `Abc$HOME!xyz%42` and proves the child Python process receives the exact literal value.
  - Red before fix: the captured password expanded `$HOME` and failed the assertion.
  - Green after fix: the wrapper preserved the exact literal credential.
- Verification:
  - Focused regression: `python3.13 -m pytest -q scripts/tests/test_run_cta_sync_wrapper.py` passed.
  - Full JS suite: `npx vitest run --config vitest.config.ts` passed (`148` files, `1411` tests).
  - Full Python suite: `python3.13 -m pytest -q` failed on unrelated live MenthorQ integration coverage:
    - [test_menthorq_integration.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_menthorq_integration.py): `TestMenthorQIntegrationScreenerVolume::test_unusual_activity` returned zero live rows.
    - Isolated rerun confirmed the same external-data failure.
  - Live CTA validation:
    - `python3.13 scripts/fetch_menthorq_cta.py --date 2026-03-23 --json` completed and wrote [cta_2026-03-23.json](/Users/joemccann/dev/apps/finance/radon/data/menthorq_cache/cta_2026-03-23.json).
    - [cta-sync-latest.json](/Users/joemccann/dev/apps/finance/radon/data/menthorq_cache/health/cta-sync-latest.json) and [cta-sync.json](/Users/joemccann/dev/apps/finance/radon/data/service_health/cta-sync.json) now both show `state=healthy`, `latest_available_date=2026-03-23`, and the successful cache path.

## Session: Audit Closed-Market Cached Route Loads (2026-03-22)

### Goal
Check every route/hook that can mount with polling disabled, find any remaining cases where cached data is skipped instead of rendered, and fix them with regression coverage plus browser verification.

### Dependency Graph
- T1 (Inventory sync-backed hooks/routes that can mount inactive and identify which ones still skip the initial cached read) depends_on: []
- T2 (Add red regression coverage for every affected surface and coverage for representative closed-market routes) depends_on: [T1]
- T3 (Fix the affected hooks so inactive mode still renders cached data while keeping polling disabled) depends_on: [T2]
- T4 (Run targeted verification, full JS verification, and browser checks, then record findings and lessons) depends_on: [T3]

### Checklist
- [x] T1 Inventory sync-backed hooks/routes that can mount inactive and identify which ones still skip the initial cached read
- [x] T2 Add red regression coverage for every affected surface and coverage for representative closed-market routes
- [x] T3 Fix the affected hooks so inactive mode still renders cached data while keeping polling disabled
- [x] T4 Run targeted verification, full JS verification, and browser checks, then record findings and lessons

### Review
- Audit result:
  - [web/lib/useSyncHook.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useSyncHook.ts) now correctly handles the inactive case for all of its consumers, so the shared hook path is safe for:
    - [usePerformance.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePerformance.ts)
    - [useScanner.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useScanner.ts)
    - [useFlowAnalysis.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useFlowAnalysis.ts)
    - [useJournal.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useJournal.ts)
    - [useBlotter.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useBlotter.ts)
    - [useRegime.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useRegime.ts)
  - [web/lib/useOrders.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useOrders.ts) was already safe because it always reads cached orders on mount and only gates the IB sync behind `active`.
  - [web/lib/usePortfolio.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePortfolio.ts) was the one remaining affected hook. It still skipped the initial cached GET when `active=false`, which left the closed-market `/portfolio` shell stuck on placeholder account cards instead of rendering cached data.
- Fix:
  - Updated [usePortfolio.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePortfolio.ts) so it always performs its first cached GET, disables polling while inactive, and performs the first POST sync only when the route is active or later becomes active.
- Regression coverage:
  - Added [use-portfolio-inactive-load.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/use-portfolio-inactive-load.test.ts) to lock the inactive initial GET and inactive-to-active first sync behavior.
  - Added [portfolio-market-closed.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/portfolio-market-closed.spec.ts) to prove `/portfolio` renders cached account metrics when the market is closed.
  - Added [performance-market-closed.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-market-closed.spec.ts) to prove the shared `useSyncHook` path also renders cached performance data on a closed-market mount.
- Verification:
  - Red before fix:
    - `npx vitest run web/tests/use-portfolio-inactive-load.test.ts` failed
    - `cd web && npx playwright test e2e/portfolio-market-closed.spec.ts --config playwright.no-server.config.ts` failed
  - Green after fix:
    - `npx vitest run web/tests/use-portfolio-inactive-load.test.ts`
    - `cd web && npx playwright test e2e/portfolio-market-closed.spec.ts --config playwright.no-server.config.ts`
    - `cd web && npx playwright test e2e/performance-market-closed.spec.ts --config playwright.no-server.config.ts`
  - Broad verification:
    - `npx vitest run --config vitest.config.ts` → `143` files passed, `1373` tests passed
    - `cd web && npx playwright test e2e/internals-market-closed.spec.ts e2e/portfolio-market-closed.spec.ts e2e/performance-market-closed.spec.ts --config playwright.no-server.config.ts` → `3` passed

## Session: Document And Ship Closed-Market Cache Fixes (2026-03-22)

### Goal
Update the durable docs to reflect the inactive cached-load contract for route hooks, rerun the repo verification gates, then commit and push the accumulated fixes without staging unrelated log artifacts.

### Dependency Graph
- T1 (Identify and update the docs that describe route sync/cached-load behavior after the `/internals` and `/portfolio` fixes) depends_on: []
- T2 (Run the required repo-level verification gates for JS and Python, plus any focused browser checks needed to support the commit) depends_on: [T1]
- T3 (Stage only the intended source/doc/test changes, create an atomic commit, and push the current branch) depends_on: [T2]

### Checklist
- [x] T1 Identify and update the docs that describe route sync/cached-load behavior after the `/internals` and `/portfolio` fixes
- [x] T2 Run the required repo-level verification gates for JS and Python, plus any focused browser checks needed to support the commit
- [ ] T3 Stage only the intended source/doc/test changes, create an atomic commit, and push the current branch

## Session: Ignore Generated Dead-Code And Log Artifacts (2026-03-22)

### Goal
Stop generated `.claude/hooks/dead-code.manifest` churn and rotated `logs/` artifacts from showing up in git status, while preserving the tracked `logs/.gitkeep` placeholder.

### Dependency Graph
- T1 (Inspect the current tracked/ignored state for `.claude/hooks/dead-code.manifest` and `logs/` artifacts, then capture the minimal ignore plan) depends_on: []
- T2 (Update `.gitignore` to ignore generated log artifacts and the dead-code manifest, preserving `logs/.gitkeep`) depends_on: [T1]
- T3 (Remove the manifest from the git index, verify the resulting worktree status, and document the outcome) depends_on: [T2]

### Checklist
- [x] T1 Inspect the current tracked/ignored state for `.claude/hooks/dead-code.manifest` and `logs/` artifacts, then capture the minimal ignore plan
- [x] T2 Update `.gitignore` to ignore generated log artifacts and the dead-code manifest, preserving `logs/.gitkeep`
- [x] T3 Remove the manifest from the git index, verify the resulting worktree status, and document the outcome

### Review
- Updated [.gitignore](/Users/joemccann/dev/apps/finance/radon/.gitignore) to:
  - ignore all generated files under `logs/`
  - preserve the tracked [logs/.gitkeep](/Users/joemccann/dev/apps/finance/radon/logs/.gitkeep) placeholder
  - ignore the generated [dead-code.manifest](/Users/joemccann/dev/apps/finance/radon/.claude/hooks/dead-code.manifest)
- Removed [dead-code.manifest](/Users/joemccann/dev/apps/finance/radon/.claude/hooks/dead-code.manifest) from the git index with `git rm --cached` so the new ignore rule actually takes effect for future runs.
- Verification:
  - `git check-ignore -v .claude/hooks/dead-code.manifest logs/monitor-daemon.log logs/monitor-daemon.log.1` now resolves through [.gitignore](/Users/joemccann/dev/apps/finance/radon/.gitignore)
  - `logs/.gitkeep` remains unignored and tracked

## Session: Hide Performance Route From Visible Navigation (2026-03-22)

### Goal
Keep the `/performance` route and its tests intact, but remove the `Performance` entry from the visible sidebar navigation and lock that behavior with regression coverage.

### Dependency Graph
- T1 (Trace how shared nav metadata feeds the sidebar and identify the minimal place to hide the `Performance` item without breaking route consumers) depends_on: []
- T2 (Add red regression coverage for the hidden performance-nav contract, including a browser-visible sidebar check) depends_on: [T1]
- T3 (Implement the nav-hiding change, rerun focused verification, and document the result) depends_on: [T2]

### Checklist
- [x] T1 Trace how shared nav metadata feeds the sidebar and identify the minimal place to hide the `Performance` item without breaking route consumers
- [x] T2 Add red regression coverage for the hidden performance-nav contract, including a browser-visible sidebar check
- [x] T3 Implement the nav-hiding change, rerun focused verification, and document the result

### Review
- Root cause:
  - The earlier nav metadata fix reintroduced the [Performance](/Users/joemccann/dev/apps/finance/radon/web/lib/data.ts) entry directly into `navItems`, and [Sidebar.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/Sidebar.tsx) renders that array verbatim. That made the `/performance` route visible in the UI again even though the intent was to keep it hidden.
- Fix:
  - Added an optional `hidden` flag to [WorkspaceNavItem](/Users/joemccann/dev/apps/finance/radon/web/lib/types.ts).
  - Marked the `performance` nav item as hidden in [data.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/data.ts), which preserves the route metadata for internal consumers and tests.
  - Updated [Sidebar.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/Sidebar.tsx) to render only non-hidden nav items.
- Regression coverage:
  - Added [sidebar-navigation.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/sidebar-navigation.test.ts) to lock the hidden-nav filtering in the rendered sidebar.
  - Added [sidebar-performance-hidden.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/sidebar-performance-hidden.spec.ts) to verify the visible sidebar no longer shows the `Performance` link in the browser.
- Verification:
  - Red before fix:
    - `cd web && npx playwright test e2e/sidebar-performance-hidden.spec.ts --config playwright.no-server.config.ts` failed because the `Performance` link was present
  - Green after fix:
    - `npx vitest run web/tests/sidebar-navigation.test.ts`
    - `npx vitest run web/tests/data.test.ts web/tests/sidebar-navigation.test.ts`
    - `cd web && npx playwright test e2e/sidebar-performance-hidden.spec.ts --config playwright.no-server.config.ts`

### Review
- Docs updated:
  - Updated [web/README.md](/Users/joemccann/dev/apps/finance/radon/web/README.md) so the market-hours section, API table, and verification checklist all reflect the actual contract: one initial cached GET on mount even when the market is closed, with background POST sync/polling paused off-hours.
  - Updated [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) feature notes to state that closed-market mounts still render cached portfolio, performance, regime, and internals data immediately.
- Verification:
  - `npx vitest run --config vitest.config.ts` → `145` files passed, `1395` tests passed
  - `PYTHONPATH=. python3.13 -m pytest -q` → `1417` passed, `18` warnings
- Commit hygiene:
  - Intentionally excluded unrelated local artifacts:
    - [.claude/hooks/dead-code.manifest](/Users/joemccann/dev/apps/finance/radon/.claude/hooks/dead-code.manifest)
    - `logs/monitor-daemon*`

## Session: Make Daemon Market-Hours Gate Consistent (2026-03-21)

### Goal
Update the monitor daemon so the installed `--once` launchd path obeys the same market-hours policy as the long-running daemon path, without breaking handlers that are intentionally allowed to run outside market hours.

### Dependency Graph
- T1 (Inspect current daemon tests and runtime semantics, then capture the fix plan) depends_on: []
- T2 (Implement a consistent per-handler market-hours gate across both `run_once()` and `run_loop()`) depends_on: [T1]
- T3 (Add regression tests for off-hours `run_once()` behavior and allowed off-hours handlers) depends_on: [T2]
- T4 (Run focused verification and then the full Python and JS suites, and document results) depends_on: [T3]

### Checklist
- [x] T1 Inspect current daemon tests and runtime semantics, then capture the fix plan
- [x] T2 Implement a consistent per-handler market-hours gate across both `run_once()` and `run_loop()`
- [x] T3 Add regression tests for off-hours `run_once()` behavior and allowed off-hours handlers
- [x] T4 Run focused verification and then the full Python and JS suites, and document results

### Review
- Root cause:
  - The installed launchd service invokes `python -m monitor_daemon.run --once` every 60 seconds, so the old market-hours gate in [daemon.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/daemon.py) only applied to the long-running `--daemon` loop, not to the actual production `--once` path.
  - That made launchd execution inconsistent with the daemon’s documented market-hours behavior and allowed market-hours-only handlers to run off-hours.
- Fix:
  - Added a shared per-handler policy flag in [base.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/base.py): handlers default to `requires_market_hours = True`.
  - Updated [daemon.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/daemon.py) so both `run_once()` and `run_loop()` use the same `_handler_can_run_now(...)` gate. Market-hours-only handlers now skip off-hours in both execution modes, while off-hours handlers can still run.
  - Marked [preset_rebalance_handler.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/preset_rebalance_handler.py) and [flex_token_check.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/flex_token_check.py) as off-hours-allowed handlers.
  - Updated [run.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/run.py) `list_handlers()` to include `flex_token_check`, so the CLI output matches the actual registry.
  - Updated [.pi/AGENTS.md](/Users/joemccann/dev/apps/finance/radon/.pi/AGENTS.md), [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), and [docs/implement.md](/Users/joemccann/dev/apps/finance/radon/docs/implement.md) so the docs reflect the real launchd `--once` model and the per-handler market-hours policy.
- Regression coverage:
  - Added a default-policy assertion in [test_base_handler.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_monitor_daemon/test_base_handler.py).
  - Added off-hours scheduling regressions in [test_daemon.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_monitor_daemon/test_daemon.py) covering:
    - market-hours handlers are skipped outside market hours
    - off-hours handlers still run outside market hours
  - Tightened existing `run_once()` tests to pass explicit `market_hours=True`, so they no longer depend on the wall clock.
- Verification:
  - Focused daemon tests:
    - `python3.13 -m pytest -q scripts/tests/test_monitor_daemon/test_base_handler.py scripts/tests/test_monitor_daemon/test_daemon.py`
    - `python3.13 -m pytest -q scripts/tests/test_monitor_daemon/test_fill_monitor.py scripts/tests/test_monitor_daemon/test_exit_orders.py`
    - Result: all focused daemon tests passed (`27 + 23`).
  - Full JS suite:
    - `npx vitest run --config vitest.config.ts`
    - Result: failed on unrelated pre-existing tests outside the daemon surface:
      - [regime-share.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-share.test.ts)
      - [data.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/data.test.ts)
  - Full Python suite:
    - Started `python3.13 -m pytest -q`
    - Started a bounded rerun with `python3.13 -m pytest -q --maxfail=3`
    - The focused daemon surface is green, but the repo-level Python suite was still running long enough that a final summary was not captured in-session. The longer run did surface unrelated failures outside the daemon change before completion.

## Session: Analyze Monitor Daemon Scheduling Scope (2026-03-21)

### Goal
Audit `scripts/monitor_daemon/` against the repo's scheduled Python and launchd surfaces, using the daemon directory's latest file changes as the pivot, and determine which additional Python scripts now do or do not need their own schedule.

### Dependency Graph
- T1 (Locate the daemon package, inspect its current handler registry/runtime contract, and identify the latest commits that changed daemon files) depends_on: []
- T2 (Inventory repo-level scheduled services, wrappers, plists, and Python entrypoints that overlap with daemon responsibilities) depends_on: []
- T3 (Cross-reference daemon handlers against adjacent Python scripts to classify each script as daemon-managed, separately scheduled, on-demand only, or obsolete/legacy for scheduling) depends_on: [T1, T2]
- T4 (Document the findings, concrete schedule recommendations, and any risks or gaps in review notes) depends_on: [T3]

### Checklist
- [x] T1 Locate the daemon package, inspect its current handler registry/runtime contract, and identify the latest commits that changed daemon files
- [x] T2 Inventory repo-level scheduled services, wrappers, plists, and Python entrypoints that overlap with daemon responsibilities
- [x] T3 Cross-reference daemon handlers against adjacent Python scripts to classify each script as daemon-managed, separately scheduled, on-demand only, or obsolete/legacy for scheduling
- [x] T4 Document the findings, concrete schedule recommendations, and any risks or gaps in review notes

### Review
- Latest daemon-directory change:
  - The most recent commit touching `scripts/monitor_daemon/` is `3193ff571c40f52b9148464569f51915874d187a` on `2026-03-21 06:51:41 -0700`, which only changed [run.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/run.py) logging to use `RotatingFileHandler` for `logs/monitor-daemon.log`.
  - The last functional scheduling change in the daemon tree was `0550ffb16eede6a3b0aa515e67537a6941626c89` on `2026-03-19 16:25:23 -0700`, which added the daily `flex_token_check` handler and registered it in [run.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/run.py).
- Current daemon scope:
  - The installed launchd service runs `python -m monitor_daemon.run --once` every 60 seconds from [config/com.radon.monitor-daemon.plist](/Users/joemccann/dev/apps/finance/radon/config/com.radon.monitor-daemon.plist), not the long-lived `--daemon` loop.
  - Registered handlers in [run.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/run.py) are:
    - `fill_monitor` every 60s via [fill_monitor.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/fill_monitor.py)
    - `exit_orders` every 300s via [exit_orders.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/exit_orders.py)
    - `preset_rebalance` weekly via [preset_rebalance_handler.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/preset_rebalance_handler.py)
    - `flex_token_check` daily via [flex_token_check.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/flex_token_check.py)
- Production scheduling behavior:
  - Because launchd invokes `--once`, the global market-hours gate in [daemon.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/daemon.py) is mostly bypassed in production. The handler interval/state logic still applies, but due handlers can run outside market hours and on weekends.
  - Live evidence from [daemon_state.json](/Users/joemccann/dev/apps/finance/radon/data/daemon_state.json) and `./scripts/setup_monitor_daemon.sh status` shows:
    - `fill_monitor` and `exit_orders` are actively running
    - `preset_rebalance` last ran on `2026-03-19`
    - `flex_token_check` last ran on `2026-03-21`
- Cross-reference: additional Python scripts that do or do not need separate schedules now:
  - Keep separately scheduled:
    - [cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/cri_scan.py) through [run_cri_scan.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cri_scan.sh) and [setup_cri_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_cri_service.sh)
    - [scanner.py](/Users/joemccann/dev/apps/finance/radon/scripts/scanner.py), [flow_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/flow_analysis.py), and [discover.py](/Users/joemccann/dev/apps/finance/radon/scripts/discover.py) through [run_data_refresh.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_data_refresh.sh) and [setup_data_refresh_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_data_refresh_service.sh)
    - [cta_sync_service.py](/Users/joemccann/dev/apps/finance/radon/scripts/cta_sync_service.py) and its subprocess target [fetch_menthorq_cta.py](/Users/joemccann/dev/apps/finance/radon/scripts/fetch_menthorq_cta.py) through [run_cta_sync.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cta_sync.sh) and [setup_cta_sync_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_cta_sync_service.sh)
    - [repair_cri_rvol_cache.py](/Users/joemccann/dev/apps/finance/radon/scripts/repair_cri_rvol_cache.py) remains an auxiliary post-close repair path owned by [run_data_refresh.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_data_refresh.sh), not a daemon handler.
  - Do not schedule separately because the monitor daemon already owns them:
    - [fill_monitor.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/fill_monitor.py)
    - [exit_orders.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/exit_orders.py)
    - [preset_rebalance.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/preset_rebalance.py) through its weekly wrapper handler
    - [flex_token_check.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/handlers/flex_token_check.py)
  - Treat as legacy / overlapping, not separately scheduled:
    - [exit_order_service.py](/Users/joemccann/dev/apps/finance/radon/scripts/exit_order_service.py) and [setup_exit_order_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_exit_order_service.sh)
    - historical `scripts/ib_fill_monitor.py`, which is still referenced in docs but is no longer present in the working tree
- Live service state:
  - `launchctl list` currently shows `com.radon.monitor-daemon`, `com.radon.cri-scan`, `com.radon.data-refresh`, and `com.radon.cta-sync` loaded.
  - `com.radon.exit-order-service` is not loaded.
- Gaps / risks:
  - Documentation and command surfaces are partially stale:
    - [run.py](/Users/joemccann/dev/apps/finance/radon/scripts/monitor_daemon/run.py) `list_handlers()` omits `flex_token_check`
    - [.pi/AGENTS.md](/Users/joemccann/dev/apps/finance/radon/.pi/AGENTS.md) still says the monitor daemon runs “every 60s during market hours,” which does not match the installed `--once` launchd behavior
    - [.pi/AGENTS.md](/Users/joemccann/dev/apps/finance/radon/.pi/AGENTS.md), [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md), and [docs/implement.md](/Users/joemccann/dev/apps/finance/radon/docs/implement.md) still reference standalone `ib_fill_monitor.py` / `exit_order_service.py`
  - Interpreter handling is inconsistent:
    - The active monitor daemon plist still hardcodes `/usr/bin/python3`, while newer scheduled wrappers resolve or prefer `python3.13`
- Recommendation:
  - No new standalone scheduled Python job is required because of the recent daemon-directory changes.
  - The only schedule cleanup to prioritize is operational/documentation alignment: keep the current separate CRI/data-refresh/CTA launchd jobs, keep the monitor daemon for fill/exit/rebalance/flex-token work, and retire or clearly mark the old exit-order/fill-monitor standalone paths as legacy.

## Session: Backfill Internals Skew With IB-First Discovery (2026-03-19)

### Goal
Make `/internals` fetch materially deeper Nasdaq and S&P 500 skew history by checking IB first for option expiries, then merging Unusual Whales historical risk-reversal series across multiple expiries instead of relying on a single-expiry slice that tops out around one year.

### Dependency Graph
- T1 (Read the existing internals skew backend, task tracking surfaces, and current frontend data contract) depends_on: []
- T2 (Capture the plan and the user correction about IB-first source priority in durable task docs and lessons) depends_on: [T1]
- T3 (Implement IB-first expiry discovery plus multi-expiry skew-history backfill in `scripts/api/server.py`) depends_on: [T2]
- T4 (Confirm the existing `/internals` frontend can consume longer NQ/SPX skew histories without further code changes) depends_on: [T1]
- T5 (Verify the live `/internals` behavior and update the review notes with source semantics and risks) depends_on: [T3, T4]

### Checklist
- [x] T1 Read the existing internals skew backend, task tracking surfaces, and current frontend data contract
- [x] T2 Capture the plan and the user correction about IB-first source priority in durable task docs and lessons
- [x] T3 Implement IB-first expiry discovery plus multi-expiry skew-history backfill in `scripts/api/server.py`
- [x] T4 Confirm the existing `/internals` frontend can consume longer NQ/SPX skew histories without further code changes
- [x] T5 Verify the live `/internals` behavior and update the review notes with source semantics and risks

### Review
- Root cause:
  - `/internals/skew-history` was using one expiry per ticker, so the Nasdaq chart especially collapsed to roughly a year of usable rows even when the page and chart components were already wired correctly for longer histories.
  - The first attempt at IB-first expiry discovery was not actually qualifying index underlyings correctly for `reqSecDefOptParams`, so the server silently fell back to UW discovery or served stale cache artifacts.
- Fixes:
  - Updated [server.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/server.py) so internals skew history now:
    - checks IB first for expiry discovery using qualified index contracts,
    - augments with UW expiry candidates when IB’s current chain is too front-heavy for historical depth,
    - merges multiple UW historical risk-reversal series per ticker,
    - carries source metadata (`expiry_discovery: IB-first`, per-series `expiry_source`) and a cache-key version that invalidates prior single-expiry artifacts.
  - Kept the frontend contract unchanged; the existing [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/internals/route.ts) and [InternalsPanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InternalsPanel.tsx) already normalize and render longer NQ/SPX histories without additional edits.
- Verification:
  - Direct FastAPI check: `GET http://127.0.0.1:8321/internals/skew-history`
    - NQ: `589` points, `2023-11-16` → `2026-03-19`, `expiry_source: "ib+uw"`
    - SPX: `729` points, `2023-04-27` → `2026-03-19`, `expiry_source: "ib+uw"`
  - Browser-facing Next route: `GET http://127.0.0.1:3000/api/internals`
    - unified histories now start at `2023-04-27` for both chart feeds after normalization
  - Browser verification via `chrome-cdp` on `http://localhost:3000/internals`
    - the page renders only the skew-focused internals surface,
    - NQ chart x-axis now shows labels beginning in `Dec 2023`,
    - S&P 500 chart x-axis now shows labels beginning in `Jul 2023`.
- Source semantics:
  - Final direction changed after review: the route now uses UW for both expiry discovery and historical skew values.
  - IB is no longer part of the `/internals/skew-history` source path.
  - Verified after the UW-only switch:
    - FastAPI route: NQ `590` points from `2023-11-16`, SPX `730` points from `2023-04-27`
    - Next route normalization: both chart feeds begin at `2023-04-27`
    - Browser rendering via chrome-cdp still shows the extended x-axes (`Dec 2023` on NQ, `Jul 2023` on S&P 500).

## Session: Clear Blocking Full-Suite Regressions (2026-03-19)

### Goal
Trace and fix the remaining full-suite blockers that still prevent a compliant commit after the `/orders` cancel propagation work. Group the failures by root cause, use the existing red tests plus any needed regressions, and drive the repo back to a green Python/JS suite with browser verification through chrome-cdp for at least one affected user-facing path.

### Dependency Graph
- T1 (Trace the remaining pytest failure clusters through the backend/provider/frontend boundaries and reduce them to a small set of root causes) depends_on: []
- T2 (Fix the scanner parallel/testability regression so watchlist scans still use the optimized provider path without bypassing the patchable fetch seam) depends_on: [T1]
- T3 (Restore legacy utility/UW client compatibility and deterministic request semantics so the utils and UW client suites pass again) depends_on: [T1]
- T4 (Restore the blotter IB fetcher compatibility contract expected by the trade blotter tests) depends_on: [T1]
- T5 (Run focused red/green verification for each cluster, then rerun the full Python and JS suites) depends_on: [T2, T3, T4]
- T6 (Verify one affected user-facing path end-to-end in the browser via chrome-cdp and update review notes) depends_on: [T5]

### Checklist
- [x] T1 Trace the remaining pytest failure clusters through the backend/provider/frontend boundaries and reduce them to a small set of root causes
- [x] T2 Fix the scanner parallel/testability regression so watchlist scans still use the optimized provider path without bypassing the patchable fetch seam
- [x] T3 Restore legacy utility/UW client compatibility and deterministic request semantics so the utils and UW client suites pass again
- [x] T4 Restore the blotter IB fetcher compatibility contract expected by the trade blotter tests
- [x] T5 Run focused red/green verification for each cluster, then rerun the full Python and JS suites
- [x] T6 Verify one affected user-facing path end-to-end in the browser via chrome-cdp and update review notes

### Review
- Root-cause trace:
  - Scanner/backend seam: [scanner.py](/Users/joemccann/dev/apps/finance/radon/scripts/scanner.py) had been refactored to call `fetch_flow.fetch_flow(...)` directly with optimization flags, which preserved runtime behavior but bypassed the patchable `fetch_flow_data` seam that the parallel scanner tests and the FastAPI `/scan` subprocess path still rely on. That broke the backend contract between the scanner worker pool and the test harness.
  - Provider boundary: the scanner path fans out to Unusual Whales through [fetch_flow.py](/Users/joemccann/dev/apps/finance/radon/scripts/fetch_flow.py) and [uw_client.py](/Users/joemccann/dev/apps/finance/radon/scripts/clients/uw_client.py). Global cache hits in `UWClient._get()` were masking mocked request paths, so retry/parameter tests no longer exercised the third-party request boundary deterministically.
  - Compatibility boundary: older callers and tests still depended on legacy utility shims ([uw_api.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/uw_api.py), [ib_connection.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/ib_connection.py)) and the blotter fetcher’s historical `fetcher.ib` contract. Those had drifted during refactors even though the frontend-triggered APIs still expect the older behavior surface.
  - Frontend boundary: the user-facing scanner page at [page.tsx](/Users/joemccann/dev/apps/finance/radon/web/app/scanner/page.tsx) reads through the Next bridge at [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/scanner/route.ts), which POSTs to FastAPI `/scan`. The browser can only show fresh scanner data if the subprocess-based backend scan completes and rewrites `data/scanner.json`.
- Red/green TDD:
  - Used the existing failing suites as the red state: `scripts/tests/test_scanner_parallel.py`, `scripts/tests/test_scanner_refactor.py`, `scripts/tests/test_utils.py`, `scripts/tests/test_uw_client.py`, `scripts/trade_blotter/test_blotter.py`, and the full `pytest -q` / `vitest` gates.
  - Preserved and extended regression intent instead of weakening coverage: [test_scanner_refactor.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scanner_refactor.py) now locks the real direct-provider contract (`lookback_days=5`, `skip_options_flow=True`) rather than the pre-optimization positional call.
- Fixes:
  - Restored the scanner seam in [scanner.py](/Users/joemccann/dev/apps/finance/radon/scripts/scanner.py) so worker threads still use the optimized UW path but go through `fetch_flow_data(...)`, which keeps the backend/test harness patch point intact.
  - Made [uw_client.py](/Users/joemccann/dev/apps/finance/radon/scripts/clients/uw_client.py) skip the global response cache when tests inject mocked sessions, so provider-boundary tests exercise real request/retry logic again.
  - Reintroduced compatibility wrappers in [uw_api.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/uw_api.py) and [ib_connection.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/ib_connection.py), and restored the blotter compatibility alias in [blotter_service.py](/Users/joemccann/dev/apps/finance/radon/scripts/trade_blotter/blotter_service.py).
  - Marked the manual IB realtime probe [test_ib_realtime.py](/Users/joemccann/dev/apps/finance/radon/scripts/test_ib_realtime.py) as non-collectable so pytest stops treating its helper functions as fixture-driven tests.
  - Hardened the JS test harness in [integration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/integration.test.ts) with an explicit timeout for the CLI help-screen test, which synchronously spawns four Python processes and was intermittently tripping Vitest’s default 5s ceiling under full-suite load.
- Verification:
  - Focused Python:
    - `PYTHONPATH=. pytest scripts/tests/test_scanner_parallel.py -q`
    - `PYTHONPATH=. pytest scripts/tests/test_scanner_refactor.py -q`
    - `PYTHONPATH=. pytest scripts/tests/test_utils.py -q`
    - `PYTHONPATH=. pytest scripts/tests/test_uw_client.py -q`
    - `PYTHONPATH=.:scripts/trade_blotter pytest scripts/trade_blotter/test_blotter.py -q`
  - Focused JS:
    - `npx vitest run web/tests/integration.test.ts --config vitest.config.ts -t 'pi command --help screens are available'`
  - Full suites:
    - `npx vitest run --config vitest.config.ts` → `138` files passed, `1308` tests passed, `8` skipped
    - `PYTHONPATH=. pytest -q` → `1391` passed, `10` warnings
  - Browser verification via chrome-cdp:
    - Used `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` against the live app on `http://127.0.0.1:3000/scanner`.
    - Verified the scanner page rendered from the rewritten backend cache after a live FastAPI `/scan` run and settled from `SYNCING...` to `0 SIGNALS`, with `LAST SCAN: 3/19/2026, 2:08:01 PM • 19 TICKERS SCANNED`.
    - Verified the Next bridge data at `http://127.0.0.1:3000/api/scanner` reported a fresh non-stale cache after the scan (`cache_meta.is_stale: false`).

## Session: Propagate Cancel-Failure Rule To Docs And Memory (2026-03-19)

### Goal
Update the durable documentation, memory, and agent-instruction surfaces so the `/orders` cancel failure-chain lesson is preserved across Codex, Claude, Pi, and structured memory, then verify the resulting changes and prepare the branch for commit/push.

### Dependency Graph
- T1 (Inventory the durable doc, memory, and agent-instruction surfaces that should carry the cancel-confirmation and error-propagation rule) depends_on: []
- T2 (Update the identified docs, lessons, agent files, and structured memory fact with the new cancel-flow rule and status-propagation guidance) depends_on: [T1]
- T3 (Run verification on the updated documentation surfaces, refresh task review notes, and attempt the required commit/push flow) depends_on: [T2]

### Checklist
- [x] T1 Inventory the durable doc, memory, and agent-instruction surfaces that should carry the cancel-confirmation and error-propagation rule
- [x] T2 Update the identified docs, lessons, agent files, and structured memory fact with the new cancel-flow rule and status-propagation guidance
- [x] T3 Run verification on the updated documentation surfaces, refresh task review notes, and attempt the required commit/push flow

### Review
- Durable propagation completed:
  - Added the cancel/modify failure-propagation rule to [CLAUDE.md](/Users/joemccann/dev/apps/finance/radon/CLAUDE.md) and confirmed the same rule is present in [AGENTS.md](/Users/joemccann/dev/apps/finance/radon/AGENTS.md) and [.pi/AGENTS.md](/Users/joemccann/dev/apps/finance/radon/.pi/AGENTS.md).
  - Added the lesson to [tasks/lessons.md](/Users/joemccann/dev/apps/finance/radon/tasks/lessons.md), the structured memory fact [infra-orders-cancel-confirmation-propagation.json](/Users/joemccann/dev/apps/finance/radon/context/memory/fact/infra-orders-cancel-confirmation-propagation.json), and a status-log entry in [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md).
- Commit-gate verification:
  - `python3 -m json.tool context/memory/fact/infra-orders-cancel-confirmation-propagation.json`
  - `npx vitest run --config vitest.config.ts` → **green** (`137` files passed, `1303` tests passed, `8` skipped)
  - Tractable Python blockers cleared so targeted slices now pass:
    - `PYTHONPATH=. pytest scripts/tests/test_scenario_analysis.py`
    - `PYTHONPATH=. pytest scripts/tests/test_fetch_options.py -q`
    - `PYTHONPATH=. pytest scripts/tests/test_fetch_ticker.py -q`
- Additional blocker cleanup completed while trying to satisfy the repo’s mandatory full-suite gate:
  - Restored scenario-analysis helper exports in [scenario_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/scenario_analysis.py) so the suite can import `approx_delta` / exposure helpers again.
  - Hardened [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/place/route.ts) against missing portfolio-reader results, updated [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/modify/route.ts) validation text, and made [OrderActionsContext.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/OrderActionsContext.tsx) forward `outsideRth` explicitly so the prior Vitest blockers are green.
  - Marked the manual IB realtime probe [test_ib_realtime.py](/Users/joemccann/dev/apps/finance/radon/scripts/test_ib_realtime.py) as non-collectable by pytest and restored dark-pool enrichment fallback in [fetch_ticker.py](/Users/joemccann/dev/apps/finance/radon/scripts/fetch_ticker.py).
- Commit/push outcome:
  - `PYTHONPATH=. pytest -q` still fails with **47 unrelated Python test failures** after the above cleanup. The remaining failures are outside this cancel/docs scope and include [test_scanner_parallel.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scanner_parallel.py), [test_utils.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_utils.py), [test_uw_client.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_uw_client.py), and [test_blotter.py](/Users/joemccann/dev/apps/finance/radon/scripts/trade_blotter/test_blotter.py).
  - Because the repo instructions require the full suite to pass before any commit, the requested commit/push was **not** performed.

## Session: Fix `/orders` Cancel 502/500 Failure Chain (2026-03-19)

### Goal
Trace why cancelling an open order surfaces a FastAPI `502 Bad Gateway` and a Next `/api/orders/cancel` `500 Internal Server Error`, reproduce the failure with red tests, fix the backend/provider confirmation path and status propagation, and verify the corrected behavior in the browser.

### Dependency Graph
- T1 (Trace the cancel flow from the `/orders` UI through the Next route, FastAPI `/orders/cancel`, and `scripts/ib_order_manage.py` to identify the real root cause and any masked errors) depends_on: []
- T2 (Add failing regression coverage for the cancel confirmation path at the Python script layer, the Next route status propagation layer, and the browser-visible cancel behavior) depends_on: [T1]
- T3 (Implement the minimal fix so IB cancel confirmation re-checks refreshed open orders and the Next route preserves upstream HTTP status/detail) depends_on: [T2]
- T4 (Run focused verification, run the full test suite, verify the cancel flow in the browser with chrome-cdp, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the cancel flow from the `/orders` UI through the Next route, FastAPI `/orders/cancel`, and `scripts/ib_order_manage.py` to identify the real root cause and any masked errors
- [x] T2 Add failing regression coverage for the cancel confirmation path at the Python script layer, the Next route status propagation layer, and the browser-visible cancel behavior
- [x] T3 Implement the minimal fix so IB cancel confirmation re-checks refreshed open orders and the Next route preserves upstream HTTP status/detail
- [x] T4 Run focused verification, run the full test suite, verify the cancel flow in the browser with chrome-cdp, and document review notes

### Review
- Root-cause trace:
  - Third-party/provider boundary: Interactive Brokers can acknowledge a cancel by removing the order from refreshed open orders instead of mutating the original `Trade` object in place. The cancel path in [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py) only watched the original `trade.orderStatus.status`, so a real IB cancel could still fall through to `Cancel failed — order still Submitted`, which FastAPI surfaced as `502 Bad Gateway`.
  - FastAPI/subprocess boundary: when `ib_order_manage.py` exited non-zero, [subprocess.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/subprocess.py) returned the raw JSON line printed by the script instead of its human-readable `message`, so downstream callers could receive unreadable error strings like `{"status":"error","message":"Trade not found ..."}`.
  - Next route boundary: [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/cancel/route.ts) caught every upstream `RadonApiError` and rewrote it to a generic `500`, which is why the browser showed `/api/orders/cancel` `500 Internal Server Error` even when FastAPI had actually returned `502`.
  - Frontend boundary: [OrderActionsContext.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/OrderActionsContext.tsx) already surfaces `json.error` in the toast. Once the route stopped masking the status and the subprocess helper stopped passing raw JSON through, the user-visible message became the real cancel failure reason.
- Red/green TDD:
  - Added a Python regression in [test_ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_ib_order_manage.py) that reproduces IB confirming a cancel by removing the order from refreshed open orders. It failed red with `Cancel failed — order still Submitted`.
  - Added a subprocess-helper regression in [test_api_subprocess.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_api_subprocess.py) that requires JSON script errors to resolve to their `message` field.
  - Added a Next-route regression in [api-routes-extended.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/api-routes-extended.test.ts) that requires `/api/orders/cancel` to preserve an upstream `502`.
  - Added browser coverage in [order-cancel-error-propagation.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/order-cancel-error-propagation.spec.ts) that drives the `/orders` UI against a mock backend and asserts the browser receives a `502` plus the cleaned cancel message.
  - Red before fix:
    - `pytest scripts/tests/test_ib_order_manage.py -k 'cancel_succeeds_when_order_disappears_from_refreshed_open_orders'`
    - `npx vitest run web/tests/api-routes-extended.test.ts --config vitest.config.ts -t 'preserves upstream 502 detail when FastAPI cancel fails'`
    - `cd web && RADON_API_URL=http://127.0.0.1:8325 PLAYWRIGHT_PORT=3000 npx playwright test e2e/order-cancel-error-propagation.spec.ts --config playwright.config.ts`
- Fixes:
  - Updated [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py) so cancel confirmation re-fetches IB open orders on each poll and treats a disappeared order as a successful cancel, matching IB’s real acknowledgement behavior.
  - Updated [subprocess.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/subprocess.py) so non-zero script exits extract `detail`/`message`/`error` from JSON stdout instead of surfacing raw serialized JSON.
  - Updated [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/cancel/route.ts) so `RadonApiError` status/detail are preserved to the browser instead of being collapsed to `500`.
- Verification:
  - Focused green:
    - `pytest scripts/tests/test_ib_order_manage.py -k 'cancel_succeeds_when_order_disappears_from_refreshed_open_orders or cancel_success or cancel_reports_error_on_pending_cancel_timeout'`
    - `pytest scripts/tests/test_api_subprocess.py -k 'json_error_stdout_returns_message_field'`
    - `npx vitest run web/tests/api-routes-extended.test.ts --config vitest.config.ts -t 'returns success when cancel succeeds|returns 500 when cancel fails via radonFetch|preserves upstream 502 detail when FastAPI cancel fails'`
    - `cd web && RADON_API_URL=http://127.0.0.1:8325 PLAYWRIGHT_PORT=3101 npx playwright test e2e/order-cancel-error-propagation.spec.ts --config playwright.config.ts`
  - Browser verification via `chrome-cdp`:
    - Started a controlled mock backend on `127.0.0.1:8325` and a local Next dev server on `127.0.0.1:3102`.
    - Drove the real `/orders` UI through Chrome CDP, opened the cancel dialog for `TSLL`, clicked `Cancel Order`, and confirmed the visible toast reads `Cancel not confirmed by refreshed IB open orders`.
    - Screenshot saved to `/tmp/radon-cancel-verification.png`.
  - Full-suite status:
    - `PYTHONPATH=. pytest` is still blocked by a pre-existing unrelated collection error in `scripts/tests/test_scenario_analysis.py` (`cannot import name 'approx_delta' from 'scenario_analysis'`).
    - `npx vitest run --config vitest.config.ts` still reports five unrelated pre-existing failures in `web/tests/fastapi-migration.test.ts` and `web/tests/modify-order-ticker-detail.test.ts`.

## Session: Fix Misleading Combo Close Summary On IWM Order Tab (2026-03-19)

### Goal
Trace why the IWM risk-reversal close-order confirmation uses opening-spread payoff language, reproduce the misleading `Max Gain` message with regression coverage, and update the combo close summary so held positions show close credit/debit and estimated realized P&L versus entry instead of implying a new short strategy payoff.

### Dependency Graph
- T1 (Trace the combo close summary path from the IWM order tab through `OrderTab` and `OrderConfirmSummary` to define the intended close-order semantics) depends_on: []
- T2 (Add failing regression coverage for misleading `Max Gain` labels on combo close confirmations at unit and browser layers) depends_on: [T1]
- T3 (Implement the minimal shared summary fix so held combo closes show close credit/debit plus estimated realized P&L versus entry) depends_on: [T2]
- T4 (Run focused verification, verify the IWM close summary in the browser, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the combo close summary path from the IWM order tab through `OrderTab` and `OrderConfirmSummary` to define the intended close-order semantics
- [x] T2 Add failing regression coverage for misleading `Max Gain` labels on combo close confirmations at unit and browser layers
- [x] T3 Implement the minimal shared summary fix so held combo closes show close credit/debit plus estimated realized P&L versus entry
- [x] T4 Run focused verification, verify the IWM close summary in the browser, and document review notes

### Review
- Root-cause trace:
  - Backend/provider boundary: the synced portfolio already preserved the held IWM risk-reversal entry basis at [portfolio.json](/Users/joemccann/dev/apps/finance/radon/data/portfolio.json) as `entry_cost: -579.79`, so the opening credit basis was available to the UI. No third-party provider bug was involved in the misleading `Max Gain` message.
  - Order-tab intent boundary: [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) mounts the combo form under `Close Position` for held multi-leg positions, and the default `SELL` action is intended as the close/flatten path for that held combo.
  - Confirmation-summary boundary: the combo summary logic in [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) treated any 2-leg combo as a generic opening spread and, on `SELL`, set `maxGain = totalCost`. [OrderConfirmSummary.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/order/components/OrderConfirmSummary.tsx) then rendered that gross close cash flow as `Max Gain`, which made the held close order look like a new short-risk payoff instead of a flattening order.
- Red/green TDD:
  - Extended [order-tab-combo-sign.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-tab-combo-sign.test.ts) to reproduce the misleading close summary by entering `3.00` on the held IWM combo and asserting that the confirm panel should not render `Max Gain`.
  - Added [iwm-close-order-summary.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/iwm-close-order-summary.spec.ts) to verify the browser flow shows close-order labels and values on the actual confirmation panel.
  - Red before fix:
    - `npx vitest run web/tests/order-tab-combo-sign.test.ts --config vitest.config.ts`
    - `cd web && npx playwright test e2e/iwm-close-order-summary.spec.ts --config playwright.config.ts`
- Fixes:
  - Extended [types.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/order/types.ts) so order summaries can override the total label and include an estimated P&L line for close-order contexts.
  - Updated [OrderConfirmSummary.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/order/components/OrderConfirmSummary.tsx) to render the custom total label and an `Est. Realized P&L` metric with correct positive/negative tone.
  - Updated [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) so held combo `SELL` confirmations no longer use `Max Gain` / `Max Loss`. They now show `Close Credit` or `Close Debit` from the entered close ticket plus `Est. Realized P&L` versus the preserved entry basis.
- Verification:
  - Focused unit: `npx vitest run web/tests/order-tab-combo-sign.test.ts --config vitest.config.ts`
  - Focused browser: `cd web && npx playwright test e2e/iwm-close-order-summary.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is still a zero-byte file, so direct CDP automation was unavailable in this runtime. Using Playwright against the live app at `http://127.0.0.1:3000/IWM?posId=13&tab=order`, entering `3.00` into the close ticket now renders:
    - `Close Credit: $15,000`
    - `Est. Realized P&L: $15,580`
    and no `Max Gain` / `Max Loss` lines.
  - Broader suite:
    - `python3 -m pytest -q` is currently blocked by an unrelated import error in [test_scenario_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scenario_analysis.py) because `scenario_analysis` no longer exports `approx_delta`.
    - `npx vitest run --config vitest.config.ts` currently has 6 unrelated failures in [fastapi-migration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/fastapi-migration.test.ts), [integration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/integration.test.ts), and [modify-order-ticker-detail.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/modify-order-ticker-detail.test.ts).
    - `cd web && npm run test:e2e` still fails in the existing harness with `spawn /bin/sh ENOENT`.

## Session: Fix Synthetic Combo "Last" Telemetry On IWM Ticker Detail (2026-03-19)

### Goal
Trace why the IWM ticker-detail hero and position summary show a stale synthetic combo `LAST` value that contradicts the live combo market, confirm whether the UI is conflating execution basis with current mark, and fix the pricing/label semantics so multi-leg quotes use a live mark derived from bid/ask rather than stale leg trade prints.

### Dependency Graph
- T1 (Trace the multi-leg ticker-detail quote path from synced portfolio fields and live leg quotes through `resolveSpreadPriceData`, telemetry rendering, and position summary calculation) depends_on: []
- T2 (Add failing regression coverage for stale synthetic combo `LAST` values and the intended mark-label behavior) depends_on: [T1]
- T3 (Implement the minimal shared fix so synthetic combo quotes use a live mark and are labeled as such across the affected surfaces) depends_on: [T2]
- T4 (Run focused verification, verify the IWM ticker-detail in the browser, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the multi-leg ticker-detail quote path from synced portfolio fields and live leg quotes through `resolveSpreadPriceData`, telemetry rendering, and position summary calculation
- [x] T2 Add failing regression coverage for stale synthetic combo `LAST` values and the intended mark-label behavior
- [x] T3 Implement the minimal shared fix so synthetic combo quotes use a live mark and are labeled as such across the affected surfaces
- [x] T4 Run focused verification, verify the IWM ticker-detail in the browser, and document review notes

### Review
- Root-cause trace:
  - Provider/data boundary: the synced portfolio already carried the correct held IWM risk-reversal basis (`entry_cost: -579.79`) and leg directions. No third-party provider was misreporting the current combo; the stale value came from the frontend's synthetic quote construction.
  - Quote-construction boundary: [resolveSpreadPriceData](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) was building the combo `last` by summing resolved per-leg `last` prices. Because option `last` trades are asynchronous and can be stale relative to the current book, the synthetic combo `LAST` could drift far from the live natural market implied by leg bid/ask.
  - Telemetry/render boundary: [quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts), [PositionTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/PositionTab.tsx), and [BookTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/BookTab.tsx) rendered that synthetic value under `LAST`, which made a derived mark look like a real executed trade print and caused the IWM hero/position detail to contradict the live combo quote strip.
- Red/green TDD:
  - Extended [spread-price-bar.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/spread-price-bar.test.ts) to reproduce the stale IWM risk-reversal synthetic `LAST` when one leg has a stale trade print.
  - Extended [price-bar-quote-telemetry.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/price-bar-quote-telemetry.test.ts) to assert calculated combo quotes render `MARK` instead of `LAST`.
  - Extended [position-tab-leg-sign.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/position-tab-leg-sign.test.ts) to assert the position summary uses the live combo mark and `Mark Price` label.
  - Added [iwm-synthetic-mark-label.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/iwm-synthetic-mark-label.spec.ts) to verify the browser renders `MARK`/`Mark Price` for the held IWM risk reversal rather than the stale synthetic `LAST`.
  - Red before fix:
    - `npx vitest run web/tests/spread-price-bar.test.ts web/tests/price-bar-quote-telemetry.test.ts web/tests/position-tab-leg-sign.test.ts --config vitest.config.ts`
    - `cd web && npx playwright test e2e/iwm-synthetic-mark-label.spec.ts --config playwright.config.ts`
- Fixes:
  - Updated [positionUtils.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) so synthetic combo quote data uses the rounded live net bid/ask midpoint as the derived mark and flags that value as calculated instead of trusting stale per-leg trade prints.
  - Updated [quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts) so calculated combo quote values are labeled `MARK`.
  - Updated [PositionTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/PositionTab.tsx) and [BookTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/BookTab.tsx) so multi-leg non-stock positions use the derived mark consistently and label it clearly as `Mark Price` / `MARK`.
- Verification:
  - Focused unit: `npx vitest run web/tests/spread-price-bar.test.ts web/tests/price-bar-quote-telemetry.test.ts web/tests/position-tab-leg-sign.test.ts web/tests/same-day-pnl.test.ts --config vitest.config.ts`
  - Focused browser: `cd web && npx playwright test e2e/iwm-synthetic-mark-label.spec.ts e2e/iwm-ticker-detail-combo-sign.spec.ts e2e/iwm-close-order-summary.spec.ts e2e/portfolio-leg-row-runtime.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is still a zero-byte file, so direct CDP automation was unavailable in this runtime. Using Playwright against the live app at `http://127.0.0.1:3000/IWM?posId=13&tab=position`, the multi-leg quote now renders as a derived mark (`Mark Price`) aligned with the live combo book rather than as a stale `Last Price`.
  - Broader suite:
    - `python3 -m pytest -q` is currently blocked by an unrelated import error in [test_scenario_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scenario_analysis.py) because `scenario_analysis` no longer exports `approx_delta`.
    - `npx vitest run --config vitest.config.ts` currently has 6 unrelated failures in [fastapi-migration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/fastapi-migration.test.ts), [integration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/integration.test.ts), and [modify-order-ticker-detail.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/modify-order-ticker-detail.test.ts).
    - `cd web && npm run test:e2e` still fails in the existing harness with `spawn /bin/sh ENOENT`.

## Session: Fix Signed Combo Leg And Order Pricing On IWM Ticker Detail (2026-03-19)

### Goal
Trace why the IWM risk-reversal ticker detail loses sign semantics between the synced portfolio/provider data, the ticker-detail position tab, and the combo close-order form. Reproduce the bug with regression coverage, preserve signed long/short leg presentation on the Position tab, preserve signed combo bid/mid/ask and net limit input values on the Order tab, and verify the full IWM flow in the browser.

### Dependency Graph
- T1 (Trace the IWM ticker-detail data path from IB-synced portfolio fields and combo quote semantics through PositionTab/OrderTab rendering to identify where sign and leg-direction styling are lost) depends_on: []
- T2 (Add failing regression coverage for signed combo leg rendering and negative combo quote/input behavior at unit and browser layers) depends_on: [T1]
- T3 (Implement the minimal shared/frontend fix so short legs render with signed/red values and combo close quotes plus net-limit input preserve negative credit semantics end to end) depends_on: [T2]
- T4 (Run focused verification, verify the live IWM ticker detail in the browser, run broader suites, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the IWM ticker-detail data path from IB-synced portfolio fields and combo quote semantics through PositionTab/OrderTab rendering to identify where sign and leg-direction styling are lost
- [x] T2 Add failing regression coverage for signed combo leg rendering and negative combo quote/input behavior at unit and browser layers
- [x] T3 Implement the minimal shared/frontend fix so short legs render with signed/red values and combo close quotes plus net-limit input preserve negative credit semantics end to end
- [x] T4 Run focused verification, verify the live IWM ticker detail in the browser, run broader suites, and document review notes

### Review
- Root-cause trace:
  - Provider/data boundary: the synced portfolio at [portfolio.json](/Users/joemccann/dev/apps/finance/radon/data/portfolio.json) already preserved the combo-level credit sign (`entry_cost: -579.79`) and the per-leg direction (`LONG` call, `SHORT` put). The upstream data was not losing direction; the frontend was failing to reapply it when formatting the leg rows.
  - Third-party semantics: Interactive Brokers combo pricing is signed rather than normalized to absolute debit-only values, so a close price for a credit/risk-reversal combo is expected to remain negative through the payload/display path. Relevant references: https://interactivebrokers.github.io/tws-api/basic_orders.html and https://www.interactivebrokers.com/campus/trading-lessons/complex-orders/
  - Position tab boundary: [PositionTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/PositionTab.tsx) was rendering leg entry and market values with absolute formatting, so the short put row showed positive values and no negative styling even though the leg direction was `SHORT`.
  - Order tab boundary: [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) computed signed combo `netBid`/`netAsk`, then immediately stripped the sign with `Math.abs(...)` before rendering the strip and before the quick-fill buttons populated the net limit input.
  - API boundary: [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/place/route.ts) still rejected any `limitPrice <= 0`, which meant a correctly signed negative combo limit would have been blocked before it reached the IB bridge.
- Red/green TDD:
  - Added [position-tab-leg-sign.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/position-tab-leg-sign.test.ts) to reproduce the missing signed/red short-leg rendering on the Position tab.
  - Added [order-tab-combo-sign.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-tab-combo-sign.test.ts) to reproduce the combo strip and quick-fill sign loss in the Order tab.
  - Added [order-place-combo-negative-price.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-place-combo-negative-price.test.ts) to reproduce the route rejecting signed combo prices.
  - Added [iwm-ticker-detail-combo-sign.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/iwm-ticker-detail-combo-sign.spec.ts) to verify the full browser path: signed leg rows, signed combo strip, signed input, and signed POST payload.
  - Red before fix:
    - `npx vitest run web/tests/position-tab-leg-sign.test.ts web/tests/order-tab-combo-sign.test.ts web/tests/order-place-combo-negative-price.test.ts --config vitest.config.ts`
    - `cd web && npx playwright test e2e/iwm-ticker-detail-combo-sign.spec.ts --config playwright.config.ts`
- Fixes:
  - Added [fmtSignedPrice](/Users/joemccann/dev/apps/finance/radon/web/lib/format.ts) so signed dollar prices render as `-$0.40` rather than forcing the sign to disappear behind the dollar symbol.
  - Updated [PositionTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/PositionTab.tsx) to derive signed leg entry/market values from `leg.direction`, apply positive/negative tone classes to the leg rows, and preserve negative signs in the summary metrics.
  - Updated [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) to keep signed combo bid/mid/ask values end to end, render negative combo quotes in the strip, and populate the net-limit input with the signed quick-fill value instead of an absolute one.
  - Updated [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/place/route.ts) so combo orders accept any non-zero finite signed `limitPrice`, while single-leg stock/option orders remain strictly positive.
- Verification:
  - Focused unit: `npx vitest run web/tests/position-tab-leg-sign.test.ts web/tests/order-tab-combo-sign.test.ts web/tests/order-place-combo-negative-price.test.ts --config vitest.config.ts`
  - Focused browser: `cd web && npx playwright test e2e/iwm-ticker-detail-combo-sign.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is still a zero-byte file, so direct CDP automation was unavailable in this runtime. Using Playwright against the live app at `http://127.0.0.1:3000/IWM?posId=12&tab=position`, I confirmed the rendered short leg stayed negative on the Position tab (`SHORT Put ... -$9.38 ... -$7.70`) with no page errors. The live Order tab had no quote available at probe time, so the signed quick-fill/input behavior is verified by the deterministic browser regression in [iwm-ticker-detail-combo-sign.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/iwm-ticker-detail-combo-sign.spec.ts), which submits `limitPrice: -0.4`.
  - Broader suite:
    - `python3 -m pytest -q` is currently blocked by an unrelated import error in [test_scenario_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scenario_analysis.py) because `scenario_analysis` no longer exports `approx_delta`.
    - `npx vitest run --config vitest.config.ts` currently has 6 unrelated failures in [fastapi-migration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/fastapi-migration.test.ts), [integration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/integration.test.ts), and [modify-order-ticker-detail.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/modify-order-ticker-detail.test.ts).
    - `cd web && npm run test:e2e` still fails in the existing harness with `spawn /bin/sh ENOENT`.

## Session: Fix `rtLast` ReferenceError In Portfolio Leg Rows (2026-03-19)

### Goal
Fix the `/portfolio` runtime crash caused by `LegRow` referencing `rtLast`, which only exists in `PositionRow`. Reproduce the failure with regression coverage, patch the shared row rendering with the minimal root-cause fix, and verify the portfolio page renders expanded leg rows in the browser without throwing.

### Dependency Graph
- T1 (Trace the `LegRow` render path and confirm how the undefined `rtLast` reference escaped into the expanded multi-leg row) depends_on: []
- T2 (Add failing regression coverage for the expanded leg-row render path so the `ReferenceError` reproduces before the fix) depends_on: [T1]
- T3 (Implement the minimal fix so leg-row market value uses the resolved leg price instead of a missing parent-scope variable) depends_on: [T2]
- T4 (Run focused verification, confirm `/portfolio` renders without the runtime error in the browser, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the `LegRow` render path and confirm how the undefined `rtLast` reference escaped into the expanded multi-leg row
- [x] T2 Add failing regression coverage for the expanded leg-row render path so the `ReferenceError` reproduces before the fix
- [x] T3 Implement the minimal fix so leg-row market value uses the resolved leg price instead of a missing parent-scope variable
- [x] T4 Run focused verification, confirm `/portfolio` renders without the runtime error in the browser, and document review notes

### Review
- Root-cause trace:
  - Frontend row boundary: [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx) computes `rtLast` inside `PositionRow` for top-level stock rows only.
  - Expanded leg-row boundary: [LegRow](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx#L194) was later updated to render leg market value from realtime data, but the market-value cell at [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx#L243) referenced `rtLast`, which does not exist in that component scope. Any expanded multi-leg position therefore threw `ReferenceError: rtLast is not defined` at render time.
  - Data/path impact: no third-party provider was involved in this regression. The crash happened entirely in the frontend render path once a spread row expanded.
- Red/green TDD:
  - Added [position-table-leg-row-runtime.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/position-table-leg-row-runtime.test.ts) to reproduce the crash by rendering a multi-leg position, expanding the row, and asserting the leg market values render.
  - Added [portfolio-leg-row-runtime.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/portfolio-leg-row-runtime.spec.ts) to verify the real `/portfolio` page path can expand spread legs without emitting the runtime error in the browser.
  - Red before fix: `npx vitest run web/tests/position-table-leg-row-runtime.test.ts --config vitest.config.ts` failed with `ReferenceError: rtLast is not defined`.
- Fix:
  - Replaced the bad `rtLast` reference in [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx) with the already-computed `legMv`. That keeps the cell aligned with the rest of `LegRow`'s pricing math and removes the illegal parent-scope dependency.
- Verification:
  - Focused unit: `npx vitest run web/tests/position-table-leg-row-runtime.test.ts --config vitest.config.ts`
  - Focused browser: `cd web && npx playwright test e2e/portfolio-leg-row-runtime.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is still a zero-byte file, so direct CDP automation was unavailable in this runtime. Using Playwright from the `web/` package against the live app at `http://127.0.0.1:3000/portfolio`, I expanded the first real multi-leg row (`AAPL`) and confirmed no `rtLast is not defined` page error was emitted.
  - Broader suite:
    - `python3 -m pytest -q` is currently blocked by an unrelated import error in [test_scenario_analysis.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_scenario_analysis.py) because `scenario_analysis` no longer exports `approx_delta`.
    - `npx vitest run --config vitest.config.ts` currently has 6 unrelated failures in [fastapi-migration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/fastapi-migration.test.ts), [integration.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/integration.test.ts), and [modify-order-ticker-detail.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/modify-order-ticker-detail.test.ts).
    - `cd web && npm run test:e2e` still fails in the existing harness with `spawn /bin/sh ENOENT`.

## Session: Fix CROX Bull Call Spread Pricing On Portfolio Positions (2026-03-19)

### Goal
Trace why the CROX bull call spread row on `/portfolio` can render an inflated spread last price and market value, following the path from IB-synced portfolio data through the realtime quote/provider boundary into the frontend row math. Reproduce the stale-leg pricing bug with regression coverage, implement the minimal shared fix so multi-leg rows reject stale option last trades outside the live market, and verify the corrected rendering locally.

### Dependency Graph
- T1 (Trace the CROX spread data path across IB sync, realtime quote payloads, and the portfolio row calculation to identify the exact stale-price failure mode) depends_on: []
- T2 (Add failing regression coverage for CROX-style stale option leg lasts at the shared pricing/unit layer and browser row layer) depends_on: [T1]
- T3 (Implement the minimal shared fix so multi-leg portfolio rows and related spread pricing helpers use guarded realtime option marks instead of stale out-of-market lasts) depends_on: [T2]
- T4 (Run focused verification, confirm the corrected CROX row rendering locally, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the CROX spread data path across IB sync, realtime quote payloads, and the portfolio row calculation to identify the exact stale-price failure mode
- [x] T2 Add failing regression coverage for CROX-style stale option leg lasts at the shared pricing/unit layer and browser row layer
- [x] T3 Implement the minimal shared fix so multi-leg portfolio rows and related spread pricing helpers use guarded realtime option marks instead of stale out-of-market lasts
- [x] T4 Run focused verification, confirm the corrected CROX row rendering locally, and document review notes

### Review
- Root-cause trace:
  - Third-party/provider boundary: Interactive Brokers sync already persisted sane CROX spread marks in [portfolio.json](/Users/joemccann/dev/apps/finance/radon/data/portfolio.json): the long Apr 17 $82.5 call was around `1.925`, the short Apr 17 $95 call was around `0.275`, and the net spread market value was about `$26,895` (`$1.65` per spread). The live websocket feed likewise carried the real stock/option market around those values, so IB itself was not the source of the inflated `$5.25` spread price shown in the screenshot.
  - Frontend row boundary: [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx) was already using guarded option marks through [resolveRealtimePrice](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts), which is why the live `/portfolio` row now renders the correct CROX spread mark.
  - Shared spread-helper boundary: [resolveSpreadPriceData](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) still computed synthetic spread `last` from raw leg `last` prints (`7.80 - 2.55 = 5.25`) instead of the guarded live option marks around bid/ask. That left the shared spread-pricing path inconsistent with the portfolio row and could still leak stale inflated spread prices anywhere else that consumed the helper.
- Fixed [resolveSpreadPriceData](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) so each option leg now resolves through [resolveRealtimePrice](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) before contributing to the synthetic spread `last`. Stale out-of-market option `last` prints are now replaced with the live midpoint consistently across row and helper paths.
- Locked the regression in [spread-price-bar.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/spread-price-bar.test.ts) and [crox-bull-call-stale-price.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/crox-bull-call-stale-price.spec.ts).
- Verification:
  - Red before fix in worker branch: `npx vitest run web/tests/spread-price-bar.test.ts --config vitest.config.ts` failed with `expected 5.25 to be close to 1.65`.
  - Green after fix: `npx vitest run web/tests/spread-price-bar.test.ts --config vitest.config.ts`
  - Browser regression: `cd web && npx playwright test e2e/crox-bull-call-stale-price.spec.ts --config playwright.config.ts`
  - Live browser fallback verification against `http://127.0.0.1:3000/portfolio`: the CROX row now renders `$77.59`, `$1.65`, `-$123`, `$27,613`, and `$26,895`, with no stale `$5.25` / `$85,575` inflation.

## Session: Fix Stale Historical Trades On `/orders` (2026-03-19)

### Goal
Trace why the Historical Trades (30 Days) section on `/orders` can stay stale, following the path from the third-party historical trade provider through the backend cache/API route into the frontend refresh/render path. Reproduce the stale behavior, drive it red with regression coverage, implement the minimal fix, and verify the refreshed `/orders` history in the browser.

### Dependency Graph
- T1 (Trace the historical-trades data path from the provider fetch through cache writes, API reads, and `/orders` rendering to identify why stale rows persist) depends_on: []
- T2 (Add failing regression coverage for the stale-history path at the smallest useful backend/API/browser layers) depends_on: [T1]
- T3 (Implement the minimal fix so refreshing `/orders` historical trades invalidates stale data and surfaces the latest provider payload) depends_on: [T2]
- T4 (Run focused verification, confirm the refreshed `/orders` historical trades in the browser, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the historical-trades data path from the provider fetch through cache writes, API reads, and `/orders` rendering to identify why stale rows persist
- [x] T2 Add failing regression coverage for the stale-history path at the smallest useful backend/API/browser layers
- [x] T3 Implement the minimal fix so refreshing `/orders` historical trades invalidates stale data and surfaces the latest provider payload
- [x] T4 Run focused verification, confirm the refreshed `/orders` historical trades in the browser, and document review notes

### Review
- Root-cause trace:
  - Third-party/provider boundary: the 30-day historical trade source is the Interactive Brokers Flex Web Service, reached by [trade_blotter.flex_query](/Users/joemccann/dev/apps/finance/radon/scripts/trade_blotter/flex_query.py). In the live environment the provider is currently rejecting requests with `1011 Service account is inactive`, but before this fix the runtime never reached that upstream error because the subprocess crashed first on a missing local `requests` dependency.
  - Backend/runtime boundary: [scripts/api/server.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/server.py) serves `POST /blotter` by calling [run_module](/Users/joemccann/dev/apps/finance/radon/scripts/api/subprocess.py) on `trade_blotter.flex_query --json`. The old subprocess path only surfaced stderr on non-zero exits, while `flex_query.py` printed its real failure to stdout. That collapsed live errors into blank `{"detail":""}` / `Radon API 502: ` responses.
  - Cache/API boundary: [web/app/api/blotter/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/blotter/route.ts) serves the cached [blotter.json](/Users/joemccann/dev/apps/finance/radon/data/blotter.json) on GET. In the live snapshot that cache was frozen at `2026-03-05T06:44:00`, which is exactly what `/orders` was rendering.
  - Frontend boundary: [useBlotter](/Users/joemccann/dev/apps/finance/radon/web/lib/useBlotter.ts) used to behave like a passive cache reader on `/orders`, and [HistoricalTradesSection](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) would stay pinned to the stale cache unless the operator manually clicked its local Refresh button. On top of that, the shared [useSyncHook](/Users/joemccann/dev/apps/finance/radon/web/lib/useSyncHook.ts) could let a StrictMode double-init reapply the stale GET snapshot after the background POST, preventing the auto-refresh state from stabilizing in tests.
- Fixes:
  - Removed the subprocess-only `requests` dependency from [flex_query.py](/Users/joemccann/dev/apps/finance/radon/scripts/trade_blotter/flex_query.py) and [blotter_service.py](/Users/joemccann/dev/apps/finance/radon/scripts/trade_blotter/blotter_service.py) by switching both Flex Query fetchers to stdlib HTTP helpers.
  - Improved [subprocess.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/subprocess.py) so module failures fall back to meaningful stdout lines when stderr is empty. The live FastAPI route now returns the real upstream reason: `Error: Flex Query request failed: Service account is inactive. (code: 1011)`.
  - Updated [useBlotter.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useBlotter.ts) to use the shared sync hook in active mode, and hardened [useSyncHook.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useSyncHook.ts) against StrictMode-style double initial loads while allowing the blotter view to keep cached rows visible and still surface background sync errors.
  - Updated [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) so the Historical Trades section uses the active blotter sync path on `/orders`.
- Regression coverage:
  - Python/runtime: [test_flex_query_runtime.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_flex_query_runtime.py), [test_api_subprocess.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_api_subprocess.py)
  - Frontend hook: [use-blotter-refresh.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/use-blotter-refresh.test.ts)
  - Browser: [orders-historical-trades-refresh.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/orders-historical-trades-refresh.spec.ts)
- Verification:
  - `python3 -m pytest scripts/tests/test_flex_query_runtime.py scripts/tests/test_api_subprocess.py -q`
  - `npx vitest run web/tests/use-blotter-refresh.test.ts --config vitest.config.ts`
  - `cd web && npx playwright test e2e/orders-historical-trades-refresh.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is a zero-byte file, so direct CDP automation was unavailable in this runtime. Using Playwright against the real app on `http://127.0.0.1:3000/orders`, the page now still shows the cached March 5 table but also surfaces the actual upstream provider failure instead of hiding it: `Radon API 502: Error: Flex Query request failed: Service account is inactive. (code: 1011)`.

## Session: Fix False Naked-Short Warning On WULF Close Order (2026-03-19)

### Goal
Fix the WULF ticker order tab so selling the currently-held long Jan 2027 $17 call to close the position is not misclassified as a naked short call. Trace the held-position data from IB sync through the naked-short guard and order-tab payload construction, drive the bug red in unit/API/browser coverage, implement the minimal shared fix, and verify the close-position flow renders and submits without the false block.

### Dependency Graph
- T1 (Trace the WULF close-position data path from IB-backed portfolio data through the naked-short guard, order tab, and place-order route to reproduce the false warning) depends_on: []
- T2 (Add failing regression coverage for the false-positive close-position block at the guard, API, and browser layers) depends_on: [T1]
- T3 (Implement the root-cause fix so selling an owned option contract to close is allowed while true naked short openings stay blocked) depends_on: [T2]
- T4 (Run focused verification, confirm the browser flow on the WULF order tab, and document review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the WULF close-position data path from IB-backed portfolio data through the naked-short guard, order tab, and place-order route to reproduce the false warning
- [x] T2 Add failing regression coverage for the false-positive close-position block at the guard, API, and browser layers
- [x] T3 Implement the root-cause fix so selling an owned option contract to close is allowed while true naked short openings stay blocked
- [x] T4 Run focused verification, confirm the browser flow on the WULF order tab, and document review notes

### Review
- Root cause trace:
  - Third-party/provider boundary: Interactive Brokers sync already persisted the held WULF Jan 2027 $17 long call in [portfolio.json](/Users/joemccann/dev/apps/finance/radon/data/portfolio.json) with the exact closeable contract identity: ticker `WULF`, expiry `2027-01-15`, strike `17`, right `Call`, quantity `77`.
  - Frontend boundary: [TickerDetailContent.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/TickerDetailContent.tsx) correctly resolved that held contract from `?posId=23`, and [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) correctly built a `type:"option"` `SELL` payload for that exact WULF call when the operator switched the action to `SELL`.
  - Shared-guard/backend boundary: [nakedShortGuard.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/nakedShortGuard.ts) treated every single-leg `SELL` call as a new short call that must be stock-covered. It had no concept of consuming a matching held long option first, and the client-side [toNakedShortPortfolio](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) adapter also dropped `expiry`, so both the reactive warning and [POST /api/orders/place](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/place/route.ts) misclassified the WULF close order as naked.
- Fixed [nakedShortGuard.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/nakedShortGuard.ts) so single-leg `SELL` calls and open-order audits first consume matching held long option contracts by exact identity (`ticker + expiry + strike + right`) before applying stock-coverage naked-short checks. True uncovered residual call exposure is still blocked.
- Fixed [OrderTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OrderTab.tsx) so the client-side guard portfolio preserves `expiry`, allowing the reactive warning path to match the actual held option contract instead of collapsing all WULF calls to ticker-level exposure.
- Locked the regression in [naked-short-guard.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/naked-short-guard.test.ts), [order-place-close-held-option.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-place-close-held-option.test.ts), and [wulf-close-order-naked-short.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/wulf-close-order-naked-short.spec.ts).
- Focused verification passed with:
  - `npx vitest run web/tests/naked-short-guard.test.ts web/tests/order-place-close-held-option.test.ts --config vitest.config.ts`
  - `cd web && npx playwright test e2e/wulf-close-order-naked-short.spec.ts`

## Session: Fix Incorrect Positive Day Move On Account Dashboard (2026-03-19)

### Goal
Trace why the account dashboard's Day Move can render as positive when the underlying day P&L is clearly negative, following the full path from third-party market/account data providers through the backend price/account shaping code into the frontend calculation. Reproduce the bug, drive it red with regression coverage, implement the minimal root-cause fix, and verify the corrected browser rendering with Chrome/CDP.

### Dependency Graph
- T1 (Trace the live Day Move data path across provider fields, backend shaping, and frontend calculation to reproduce the incorrect positive sign) depends_on: []
- T2 (Add failing regression coverage for the broken Day Move semantics at the smallest useful unit and browser layers) depends_on: [T1]
- T3 (Implement the root-cause fix so Day Move preserves the correct sign and source semantics end to end) depends_on: [T2]
- T4 (Run focused and full verification, confirm the corrected rendering in Chrome/CDP, and document the review notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the live Day Move data path across provider fields, backend shaping, and frontend calculation to reproduce the incorrect positive sign
- [x] T2 Add failing regression coverage for the broken Day Move semantics at the smallest useful unit and browser layers
- [x] T3 Implement the root-cause fix so Day Move preserves the correct sign and source semantics end to end
- [x] T4 Run focused and full verification, confirm the corrected rendering in Chrome/CDP, and document the review notes

### Review
- Root-cause trace:
  - Third-party provider boundary: Interactive Brokers already exposes authoritative daily P&L through `reqPnL()` at the account level and `reqPnLSingle()` at the position level. In the live snapshot, WULF carried negative `ib_daily_pnl` while the account summary `daily_pnl` was also negative, so IB itself was not the source of the positive sign.
  - Backend shaping boundary: [ib_sync.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_sync.py) persists both the correct per-position `ib_daily_pnl` and the synced option mark into [portfolio.json](/Users/joemccann/dev/apps/finance/radon/data/portfolio.json). For WULF, the cached position showed `ib_daily_pnl < 0` and `market_price ≈ 4.475`, which already contradicted the positive Day Move in the browser.
  - Live quote boundary: the realtime websocket feed for `WULF_20270115_17_C` was publishing a stale `last=21.015` while the actionable market was `bid=4.20 / ask=4.75 / close=4.78`. That stale last trade was the provider-side anomaly that made the WULF row and any naive `last-close` math explode positive.
  - Frontend render boundary: [MetricCards.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/MetricCards.tsx) used compact signed formatting for the Day Move and Total cards. The live bundle had been rendering negative compact values without an explicit minus in that path, which made negative day P&L read as unsigned/positive at a glance even when the underlying total was already negative.
  - Frontend position boundary: [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx) still trusted websocket option `last` ahead of the synced mark, so the WULF row rendered `$21.02` and a six-figure market value instead of the live market around `$4.48` / `$34,458`.
- Fixes:
  - Preserved the option-side `ib_daily_pnl` preference in [dayMoveBreakdown.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/dayMoveBreakdown.ts) and routed its “current price” labels through the same stale-last guard used by the portfolio row, so Day Move proof no longer contradicts the live market.
  - Added [resolveRealtimePrice](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) and wired it into [PositionTable.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PositionTable.tsx) plus [dayMoveBreakdown.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/dayMoveBreakdown.ts). Options now fall back to the bid/ask midpoint when `last` sits far outside the live market, while stocks still preserve their raw `last`.
  - Normalized the compact signed rendering in [MetricCards.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/MetricCards.tsx) so negative Day Move and Total cards always print an explicit minus.
  - Added regression coverage in [prices.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/prices.test.ts) and [day-move-mid-fallback.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/day-move-mid-fallback.test.ts) for WULF-style stale option last trades, and browser coverage in [account-day-move-ib-daily-pnl.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/account-day-move-ib-daily-pnl.spec.ts) for both the Day Move card and the WULF portfolio row.
- Verification:
  - Focused unit: `npx vitest run web/tests/prices.test.ts web/tests/day-move-mid-fallback.test.ts --config vitest.config.ts`
  - Focused browser: `cd web && npx playwright test e2e/account-day-move-ib-daily-pnl.spec.ts --config playwright.config.ts`
  - Live browser fallback verification: the bundled `chrome-cdp` skill script at `/Users/joemccann/.agents/skills/chrome-cdp/scripts/cdp.mjs` is a zero-byte file, so direct CDP automation was unavailable in this runtime. I used the required Playwright fallback against the live app at `http://127.0.0.1:3000/portfolio`, which rendered `Day Move -$28,458`, `Total -$28,458`, and a live WULF row of `$4.48`, `$34,458`, and `-$3,013`.
  - Full-suite attempts:
    - `python3 -m pytest` is currently blocked by an unrelated import error in `scripts/tests/test_scenario_analysis.py` (`cannot import name 'approx_delta' from 'scenario_analysis'`).
    - `cd web && npm test` is misconfigured in this checkout and exits with `No test files found`.
    - `cd web && npm run test:e2e` currently fails before executing specs because its harness cannot spawn the configured shell (`spawn /bin/sh ENOENT`).

## Session: Fix False Modify Confirmation On `/orders` (2026-03-18)

### Goal
Fix the `/orders` price-modify flow so the app does not claim success unless Interactive Brokers and the refreshed orders snapshot both reflect the requested new price. The trace must cover the provider boundary, the backend modify script, the Next API route, and the `/orders` optimistic UI so the false-success path is explicit and eliminated.

### Dependency Graph
- T1 (Reproduce the stale modify-confirmation flow in the live `/orders` page and trace the existing confirmation boundary through the IB modify script and API route) depends_on: []
- T2 (Add failing regressions for false-positive modify success at the Python script, API route, and browser layers) depends_on: [T1]
- T3 (Implement the confirmation-based fix so modify only returns success after refreshed IB open orders reflect the requested change) depends_on: [T2]
- T4 (Run focused verification, confirm the live `/orders` behavior in Chrome/CDP, and record review notes plus the prevention lesson) depends_on: [T3]

### Checklist
- [x] T1 Reproduce the stale modify-confirmation flow in the live `/orders` page and trace the existing confirmation boundary through the IB modify script and API route
- [x] T2 Add failing regressions for false-positive modify success at the Python script, API route, and browser layers
- [x] T3 Implement the confirmation-based fix so modify only returns success after refreshed IB open orders reflect the requested change
- [x] T4 Run focused verification, confirm the live `/orders` behavior in Chrome/CDP, and record review notes plus the prevention lesson

### Review
- Root cause trace:
  - Third-party provider boundary: Interactive Brokers keeps an already-open order in `Submitted` status before and after a modify request. In [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py), the old loop treated `Submitted` / `PreSubmitted` as confirmation immediately after `place_order()`, but the order was often already in `Submitted` beforehand. That made the script return `"status":"ok"` without any refreshed IB evidence that the new price was accepted.
  - Backend route boundary: [web/app/api/orders/modify/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/modify/route.ts) trusted the script's `"ok"` response, refreshed `orders.json`, and still returned `200` even when the refreshed order snapshot kept the old price.
  - Frontend behavior: [OrderActionsContext.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/OrderActionsContext.tsx) then started optimistic modify polling because the route said success. The browser eventually contradicted that success because `/api/orders` never reflected the requested price. That is exactly what your screenshot showed: the network preview said the order was modified, while a reload and the live `/api/orders` snapshot still showed the AAOI `P90` sell order at `5.70`.
- Fixed the provider confirmation boundary in [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py) by requiring a refreshed open-order snapshot to match the requested `newPrice` / `newQuantity` before returning success. If the refetched IB order still shows the old values, the script now returns an error instead of a false positive.
- Fixed the Next route in [web/app/api/orders/modify/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/modify/route.ts) so it independently validates the refreshed `orders.json` snapshot. If the refreshed order does not reflect the requested modify, the route now returns `502 Modify not confirmed by refreshed orders` instead of `200`.
- Locked the regression in [test_ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_ib_order_manage.py), [api-routes-extended.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/api-routes-extended.test.ts), and [modify-order-confirmation.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-order-confirmation.spec.ts).
- Verification passed with:
  - `python3 -m pytest scripts/tests/test_ib_order_manage.py -q`
  - `npx vitest run web/tests/api-routes-extended.test.ts`
  - `cd web && npx playwright test e2e/modify-order-confirmation.spec.ts --config playwright.no-server.config.ts`
- Live verification:
  - A real snapshot from `curl http://127.0.0.1:3000/api/orders` still showed the AAOI `P90` sell order at `5.70`, matching the reported bug.
  - A final Chrome/CDP verification on a fresh `/orders` tab with a mocked stale-confirmation response proved the fixed UI path no longer enters a fake pending state: the row stayed at `$5.70`, did not show `$5.55`, and did not show `Modifying...` / `PENDING`.

## Session: Remove Combo Modify Modal Overflow On `/orders` (2026-03-18)

### Goal
Fix the combo modify modal so the desktop layout does not clip fields or force awkward horizontal overflow when editing spread legs. The final surface should keep the improved information hierarchy while fitting the actual available modal width for live AAPL/AAOI-style spreads.

### Dependency Graph
- T1 (Inspect the live combo modify modal and measure which panel or leg grid is overflowing the modal shell) depends_on: []
- T2 (Add a failing browser regression that forbids horizontal overflow in the combo modify modal) depends_on: [T1]
- T3 (Implement the layout correction so combo leg fields fit cleanly within the modal at desktop widths) depends_on: [T2]
- T4 (Run focused verification in tests and the live browser, then update review notes and the UI lesson) depends_on: [T3]

### Checklist
- [x] T1 Inspect the live combo modify modal and measure which panel or leg grid is overflowing the modal shell
- [x] T2 Add a failing browser regression that forbids horizontal overflow in the combo modify modal
- [x] T3 Implement the layout correction so combo leg fields fit cleanly within the modal at desktop widths
- [x] T4 Run focused verification in tests and the live browser, then update review notes and the UI lesson

### Review
- Root cause: widening the overall modal was not enough. The combo editor’s secondary panel only had about `465px` of usable width, but the five-column [modify-combo-leg-grid](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) still demanded roughly `694px` of horizontal space. That guaranteed clipping even inside the larger shell.
- Tightened the browser regression in [modify-combo-order.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-combo-order.spec.ts) so it now fails if the combo editor panel has horizontal overflow, not just if the shell is too narrow.
- Fixed [globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) by giving the right-side editor slightly more space and collapsing each leg card to a two-column grid instead of five fields on one row. That keeps the same information density but makes the layout fit the actual panel width.
- Verification passed with `cd web && npx playwright test e2e/modify-combo-order.spec.ts --config playwright.no-server.config.ts`.
- Live Chrome/CDP verification against `http://127.0.0.1:3000/orders` confirmed the real AAPL combo modal now opens with `panelScrollWidth=504`, `panelClientWidth=504`, and `hasHorizontalOverflow=false`.

## Session: Improve Combo Modify Modal UI On `/orders` (2026-03-18)

### Goal
Improve the `/orders` combo modify modal so the quantity, net price, and combo-leg controls are readable and usable at desktop widths without clipping or collapsing into an indistinct stack. The result should preserve the Radon visual language while making the modify flow feel like an intentional order-entry surface instead of a squeezed form.

### Dependency Graph
- T1 (Inspect the current combo modify modal layout, style hooks, and modal-shell constraints that are causing the clipped presentation) depends_on: []
- T2 (Add a failing browser regression that proves the combo modify modal must present its key controls in a usable desktop layout) depends_on: [T1]
- T3 (Implement the modal layout and styling improvements for combo order modification, including responsive structure and clearer control grouping) depends_on: [T2]
- T4 (Run focused verification, review the live browser result, and record the UI lesson) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current combo modify modal layout, style hooks, and modal-shell constraints that are causing the clipped presentation
- [x] T2 Add a failing browser regression that proves the combo modify modal must present its key controls in a usable desktop layout
- [x] T3 Implement the modal layout and styling improvements for combo order modification, including responsive structure and clearer control grouping
- [x] T4 Run focused verification, review the live browser result, and record the UI lesson

### Review
- Root cause: the combo modify surface was still using the shared `420px` modal shell from [globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css), and the combo-leg editor rows were reusing the compact [modify-order-info](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) flex styling originally meant for the tiny order-summary strip. Once quantity plus five leg fields were added, the content technically existed but the UI collapsed into an unreadable vertical stack.
- Improved [ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) by giving combo orders an intentional structure: a primary panel for quote telemetry and order-level inputs, and a secondary panel with labeled leg cards for `action`, `type`, `strike`, `expiry`, and `ratio`. The combo modal now passes a dedicated shell class to [Modal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/Modal.tsx) without affecting the rest of the app’s dialogs.
- Improved [globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so combo modify modals expand to `860px` on desktop, use a two-column layout, and collapse back to one column cleanly on narrower widths. The new styles also add explicit field grouping, leg-card framing, section headings, and mobile-safe stacking for the action buttons and leg grid.
- Locked the layout regression in [modify-combo-order.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-combo-order.spec.ts) by asserting the combo modal opens wider than `720px`, shows the `Combo Legs` section, and keeps the critical fields visible before the edit/submit path runs.
- Verification passed with `cd web && npx playwright test e2e/modify-combo-order.spec.ts --config playwright.no-server.config.ts`.
- Live Chrome/CDP verification against `http://127.0.0.1:3000/orders` confirmed the real AAOI combo modify modal now opens at `860px` wide with visible quantity, net-price, strike, expiry, and ratio fields for both legs.

## Session: Fix Combo Order Modify Flow On `/orders` (2026-03-18)

### Goal
Fix the `/orders` modify flow for combo orders so a risk-reversal order can be adjusted with the information and controls the operator actually needs. The trace must cover the live browser, the frontend modal, the orders API, the modify backend, and the IB integration so the root cause is explicit before the UI and backend behavior are corrected.

### Dependency Graph
- T1 (Inspect the live `/orders` combo modify flow in Chrome via CDP and capture the modal behavior plus the live `/api/orders` payload for the affected risk reversal) depends_on: []
- T2 (Trace the modify path through the frontend modal, orders API, modify route, and IB integration to identify where quantity and leg-edit capability is blocked or dropped) depends_on: [T1]
- T3 (Add failing regression coverage at the unit, API, and browser layers for combo modify so quantity and leg-edit support are enforced) depends_on: [T2]
- T4 (Implement the minimal end-to-end fix or cancel/replace enhancement needed to let combo modifications change quantity and leg values correctly) depends_on: [T3]
- T5 (Run focused verification, confirm the live browser behavior via Chrome/CDP, and capture review notes plus the prevention lesson) depends_on: [T4]

### Checklist
- [x] T1 Inspect the live `/orders` combo modify flow in Chrome via CDP and capture the modal behavior plus the live `/api/orders` payload for the affected risk reversal
- [x] T2 Trace the modify path through the frontend modal, orders API, modify route, and IB integration to identify where quantity and leg-edit capability is blocked or dropped
- [x] T3 Add failing regression coverage at the unit, API, and browser layers for combo modify so quantity and leg-edit support are enforced
- [x] T4 Implement the minimal end-to-end fix or cancel/replace enhancement needed to let combo modifications change quantity and leg values correctly
- [x] T5 Run focused verification, confirm the live browser behavior via Chrome/CDP, and capture review notes plus the prevention lesson

### Review
- Root cause trace:
  - Third-party provider: Interactive Brokers already returned the AAOI risk-reversal order as a BAG with full `comboLegs` metadata through `reqAllOpenOrders()` / `openTrades()` in [ib_client.py](/Users/joemccann/dev/apps/finance/radon/scripts/clients/ib_client.py).
  - Backend sync/API: [ib_orders.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_orders.py) preserved those legs into [orders.json](/Users/joemccann/dev/apps/finance/radon/data/orders.json), [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/route.ts) served them unchanged, and the live `/api/orders` payload still contained both AAOI combo legs with strike/right/expiry intact.
  - Frontend break: [ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) only exposed `newPrice`, [OrderActionsContext.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/OrderActionsContext.tsx) only posted `newPrice`, and [web/app/api/orders/modify/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/modify/route.ts), [server.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/server.py), and [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py) only supported price mutation. The BAG leg data existed end to end, but the modify contract dropped it before the modal and backend could use it.
  - Execution constraint: IB's existing modify path resubmits the same order envelope; it can update price/quantity on that order, but it cannot rewrite combo-leg identities through the current path. That made leg edits impossible without replacing the order.
- Fixed the modify contract by adding shared request types in [orderModify.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/orderModify.ts), extending [ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) to expose quantity plus per-leg action/right/strike/expiry/ratio controls for BAG orders, and updating [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) and [OrderActionsContext.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/OrderActionsContext.tsx) to submit structured modify requests.
- Fixed the server path so ordinary orders can modify quantity in place while combo leg edits use cancel-and-replace. [web/app/api/orders/modify/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/modify/route.ts) now accepts `newQuantity` and `replaceOrder`; BAG replacements cancel the old order, place a new combo order, then refresh orders. [server.py](/Users/joemccann/dev/apps/finance/radon/scripts/api/server.py) forwards `newQuantity`, and [ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_order_manage.py) now supports price and/or quantity modification under Python 3.9-safe typing.
- Locked the regression with [orders-manage.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/orders-manage.test.ts), [api-routes-extended.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/api-routes-extended.test.ts), [test_ib_order_manage.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_ib_order_manage.py), and [modify-combo-order.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-combo-order.spec.ts).
- Verification passed with `npx vitest run web/tests/orders-manage.test.ts web/tests/api-routes-extended.test.ts`, `python3 -m pytest scripts/tests/test_ib_order_manage.py -q`, and `cd web && npx playwright test e2e/modify-combo-order.spec.ts --config playwright.no-server.config.ts`.
- Live Chrome/CDP verification against `http://127.0.0.1:3000/orders` confirmed the real AAOI BAG row now opens a modify modal with `quantity=50`, `price=0.05`, and populated combo-leg fields: `SELL P 90 2026-03-27 x1` and `BUY C 98 2026-03-27 x1`.

## Session: Fix Missing Detail On Single Open Orders (2026-03-18)

### Goal
Fix the `/orders` open-orders table so single-leg rows show the missing contract detail already present in the synced orders payload. Each row should surface the order structure and leg detail it represents, including long/short direction, stock vs option, strike, right, expiry, and combo context where applicable.

### Dependency Graph
- T1 (Inspect the live `/orders` page and trace the AAOI single-leg row from Chrome/CDP through `/api/orders`, `data/orders.json`, and the IB sync path to isolate where detail is lost) depends_on: []
- T2 (Add a red unit regression and browser regression proving single-leg open orders must render descriptive detail, while combo rows remain intact) depends_on: [T1]
- T3 (Implement the minimal display-model and renderer fix so single rows carry and render their contract summary using portfolio direction when available) depends_on: [T2]
- T4 (Run focused verification, re-check the live browser via Chrome CDP, and record review notes plus the prevention lesson) depends_on: [T3]

### Checklist
- [x] T1 Inspect the live `/orders` page and trace the AAOI single-leg row from Chrome/CDP through `/api/orders`, `data/orders.json`, and the IB sync path to isolate where detail is lost
- [x] T2 Add a red unit regression and browser regression proving single-leg open orders must render descriptive detail, while combo rows remain intact
- [x] T3 Implement the minimal display-model and renderer fix so single rows carry and render their contract summary using portfolio direction when available
- [x] T4 Run focused verification, re-check the live browser via Chrome CDP, and record review notes plus the prevention lesson

### Review
- Root cause trace:
  - Third-party provider: Interactive Brokers already returned the full AAOI contract through `reqAllOpenOrders()` / `openTrades()` in [ib_client.py](/Users/joemccann/dev/apps/finance/radon/scripts/clients/ib_client.py).
  - Backend sync: [ib_orders.py](/Users/joemccann/dev/apps/finance/radon/scripts/ib_orders.py) preserved that metadata in both the formatted `symbol` (`AAOI C105`) and the serialized contract (`secType=OPT`, `strike=105`, `right=C`, `expiry=2026-03-20`).
  - Cache/API: the same detail was present in [orders.json](/Users/joemccann/dev/apps/finance/radon/data/orders.json) and served unchanged by [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/orders/route.ts).
  - Frontend break: [openOrderCombos.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/openOrderCombos.ts) gave single rows no summary at all, and [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) rendered only `contract.symbol`, so the UI collapsed `AAOI C105 / 2026-03-20 / long call` down to bare `AAOI`.
- Fixed [openOrderCombos.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/openOrderCombos.ts) so single rows now carry a descriptive summary. Options use portfolio leg direction when available and otherwise fall back to BUY/SELL semantics, producing strings like `Long $105 Call 2026-03-20`; equities now surface `Long Stock` / `Short Stock`; combos remain unchanged.
- Fixed [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) so the single-order symbol cell renders that summary next to the ticker, matching the existing combo-row information density.
- Locked the regression with [open-order-single-detail.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/open-order-single-detail.test.ts) and [open-order-single-detail.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/open-order-single-detail.spec.ts).
- Verification passed with `npx vitest run web/tests/open-order-single-detail.test.ts`, `cd web && npx playwright test e2e/open-order-single-detail.spec.ts --config playwright.no-server.config.ts`, and a live Chrome/CDP check against `http://localhost:3000/orders` showing `AAOI Long $105 Call 2026-03-20`.

## Session: Fix Signed Entry Basis For Closed Combo Share PnL (2026-03-17)

### Goal
Fix the `/orders` share-PnL card for closed combo positions so the entry price and return percentage use the exact signed opening cash flow of the matching legs, not an unrelated same-symbol combo BAG envelope. Closed risk reversals opened for a net credit must render a negative entry price and a percentage consistent with that signed basis.

### Dependency Graph
- T1 (Inspect the closed-combo share-PnL basis path and identify why a same-symbol BAG open can override the true opening leg cash flow) depends_on: []
- T2 (Add a red regression at both the unit layer and `/orders` browser flow for a closed AAOI-style risk reversal with unrelated same-symbol BAG history) depends_on: [T1]
- T3 (Implement leg-aware basis matching so the share card derives signed entry basis from the exact opening option legs before computing return percent) depends_on: [T2]
- T4 (Run focused Vitest and Playwright verification, then record review notes and the prevention lesson) depends_on: [T3]

### Checklist
- [x] T1 Inspect the closed-combo share-PnL basis path and identify why a same-symbol BAG open can override the true opening leg cash flow
- [x] T2 Add a red regression at both the unit layer and `/orders` browser flow for a closed AAOI-style risk reversal with unrelated same-symbol BAG history
- [x] T3 Implement leg-aware basis matching so the share card derives signed entry basis from the exact opening option legs before computing return percent
- [x] T4 Run focused Vitest and Playwright verification, then record review notes and the prevention lesson

### Review
- Root cause: [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) computed share-PnL basis for any closed combo by grabbing the first non-closing BAG group on the same symbol. When AAOI had an unrelated earlier combo open at `$0.25`, the share card borrowed that unsigned BAG envelope instead of reconstructing the actual `-$0.75` credit from the matching short-put and long-call opening fills.
- Fixed the basis reconstruction in [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) by matching opening fills to the closing combo's exact option contracts and required quantities, ordering candidates by recency, and deriving signed net cash from the matched opening legs. That yields `entryPrice=-0.75`, `exitPrice=1.00`, and a return percentage based on the true `$1,875` credit basis instead of the unrelated `$625` BAG basis.
- Locked the regression at the unit layer in [share-pnl-position-group.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/share-pnl-position-group.test.ts), which imports the production helper and proves an unrelated same-symbol BAG can no longer override the matching AAOI opening legs.
- Locked the browser path in [share-pnl.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/share-pnl.spec.ts) by stubbing `/orders` data, clicking the real share button on `/orders`, and asserting the outgoing `/api/share/pnl` request now carries `entryPrice=-0.75`, `exitPrice=1`, and the corrected percentage.
- Verification passed with `npx vitest run web/tests/share-pnl-position-group.test.ts` and `cd web && npx playwright test e2e/share-pnl.spec.ts --config playwright.no-server.config.ts --grep "signed risk-reversal entry basis"`.

## Session: Fix Missing Chain Quotes For Held Option Legs (2026-03-17)

### Goal
Fix the ticker-detail chain page so option legs that are already held in the portfolio still show bid/mid/ask and builder pricing even when the options API returns dashed expiries. The chain page, order builder, and websocket subscription layer must all resolve those contracts to the same canonical option key.

### Dependency Graph
- T1 (Inspect the chain-tab contract key and subscription path for held position legs, including expiry formatting and duplicate contract handling across portfolio and chain subscriptions) depends_on: []
- T2 (Add failing regression coverage for held position leg quotes on the chain page and the option contract normalization boundary) depends_on: [T1]
- T3 (Implement the minimal fix so held option legs resolve to the same websocket contract keys as portfolio positions and chain/order-builder lookups) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then update task review and lessons) depends_on: [T3]

### Checklist
- [x] T1 Inspect the chain-tab contract key and subscription path for held position legs, including expiry formatting and duplicate contract handling across portfolio and chain subscriptions
- [x] T2 Add failing regression coverage for held position leg quotes on the chain page and the option contract normalization boundary
- [x] T3 Implement the minimal fix so held option legs resolve to the same websocket contract keys as portfolio positions and chain/order-builder lookups
- [x] T4 Run targeted Vitest and Playwright verification, then update task review and lessons

### Review
- Root cause: the shared option-contract boundary was inconsistent. Portfolio legs were normalized to `YYYYMMDD`, but the chain page and websocket client could still carry dashed expiries from `/api/options/expirations`. That let held position legs end up with different logical identities depending on where the contract originated, so the chain/order-builder lookup path could miss quotes that were already present in the websocket store.
- Tightened the protocol layer in [pricesProtocol.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/pricesProtocol.ts) with `normalizeOptionExpiry()`, `normalizeOptionContract()`, and `uniqueOptionContracts()`. `optionKey()` now canonicalizes dashed expiries before building the composite key, and the contract list hash now deduplicates logically identical contracts instead of treating dashed and compact expiries as separate subscriptions.
- Updated [usePrices.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePrices.ts) and [WorkspaceShell.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx) so the websocket only sees canonical, deduplicated option contracts. That removes held-leg duplication between the portfolio contract set and the visible chain contract set.
- Updated [OptionsChainTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OptionsChainTab.tsx) so placed option/combo payloads always send normalized expiries back to the order route, keeping the chain builder on the same raw IB contract format as the rest of the app.
- Locked the fix with protocol/unit coverage in [pricesProtocol.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/pricesProtocol.test.ts) and a new mocked-websocket browser regression in [chain-held-leg-prices.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/chain-held-leg-prices.spec.ts). The new browser test proves a CRM held spread still shows bid/mid/ask on the chain page and in the order builder when the options API returns dashed expiries.

## Session: Fix Options-Chain Combo Ratio Pricing And Order Sizing (2026-03-17)

### Goal
Fix the options-chain order builder so ratio combos price correctly in the UI and place correctly sized combo orders. The displayed net credit/debit, notional, top-level combo quantity, and per-leg IB ratios must all reflect the entered leg quantities instead of assuming every combo is 1x1.

### Dependency Graph
- T1 (Inspect the current combo quote helper, options-chain builder, and existing ratio/notional tests to pin down the broken assumptions) depends_on: []
- T2 (Drive the bug red with regression coverage for unequal leg quantities in both quote math and combo order payload construction) depends_on: [T1]
- T3 (Implement quantity normalization so combo quotes, net notional, and outbound combo ratios all share the same ratio model) depends_on: [T2]
- T4 (Run targeted verification for the updated Vitest and Playwright coverage, then capture review notes and lessons) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current combo quote helper, options-chain builder, and existing ratio/notional tests to pin down the broken assumptions
- [x] T2 Drive the bug red with regression coverage for unequal leg quantities in both quote math and combo order payload construction
- [x] T3 Implement quantity normalization so combo quotes, net notional, and outbound combo ratios all share the same ratio model
- [x] T4 Run targeted verification for the updated Vitest and Playwright coverage, then capture review notes and lessons

### Review
- Root cause: the options-chain builder mixed two incompatible models. The UI stored absolute leg counts (`25` puts / `50` calls), but combo quoting still treated every leg as `1x1` and the POST body hardcoded every IB leg ratio to `1`. That made the displayed net credit wrong and submitted the wrong combo structure.
- Added `normalizeComboOrder()` in [optionsChainUtils.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/optionsChainUtils.ts) to reduce entered leg sizes into `top-level combo quantity + per-leg ratios` via the leg-quantity GCD. A `25x/50x` risk reversal now becomes `quantity: 25` with leg ratios `1` and `2`.
- Updated [OptionsChainTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/OptionsChainTab.tsx) so combo pricing and outbound payload construction both use the normalized combo view. The order builder now quotes the per-combo net credit/debit, keeps the notional as `limitPrice * comboQuantity * 100`, and sends the correct IB leg ratios instead of a hardcoded `1`.
- Fixed [optionsChainUtils.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/optionsChainUtils.ts) so `computeNetOptionQuote()` scales each leg by `leg.quantity`, matching the existing `computeNetPrice()` behavior once the legs are normalized to ratios.
- Replaced the old source-string-only ratio test with real normalization coverage in [chain-combo-ratio.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/chain-combo-ratio.test.ts), expanded helper coverage in [options-chain-utils.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/options-chain-utils.test.ts), and added a browser regression in [ticker-search-chain.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/ticker-search-chain.spec.ts) that proves a manually-priced `25x/50x` risk reversal displays `$0.10` net credit, `$250.00` notional, and submits `{ quantity: 25, ratios: [1, 2] }`.

## Session: Document And Ship The `/api/performance` ET-Session Route Refresh Fix (2026-03-13)

### Goal
Update the operator-facing docs to reflect the stronger `/api/performance` ET-session refresh behavior, then ship the two unpushed performance fixes from `main` and confirm the worktree is clean.

### Dependency Graph
- T1 (Confirm the active branch and identify the docs that should mention the route-level ET-session refresh contract) depends_on: []
- T2 (Update the selected docs plus the task log for the route-refresh fix and the branch-state correction) depends_on: [T1]
- T3 (Create a scoped docs-only commit on top of the existing unpushed `main` commits) depends_on: [T2]
- T4 (Push `main` and confirm the worktree is clean) depends_on: [T3]

### Checklist
- [x] T1 Confirm the active branch and identify the docs that should mention the route-level ET-session refresh contract
- [x] T2 Update the selected docs plus the task log for the route-refresh fix and the branch-state correction
- [x] T3 Create a scoped docs-only commit on top of the existing unpushed `main` commits
- [x] T4 Push `main` and confirm the worktree is clean

### Review
- Confirmed the active checkout was already [main](/Users/joemccann/dev/apps/finance/radon/.git/HEAD), so there was no branch merge step to perform before shipping the two unpushed performance-fix commits.
- Updated the repo-level product summary in [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) so the `/performance` capability note now captures both halves of the fix: the UI-side portfolio-sync revalidation and the route-side ET-session guard.
- Updated the API contract in [web/README.md](/Users/joemccann/dev/apps/finance/radon/web/README.md) to state that `GET /api/performance` can refresh `portfolio.json` through `ibSync` when the snapshot is behind the current ET session before rebuilding the YTD performance payload.
- Updated [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md) with the new route-fix ship note and the current timestamp, and recorded the branch-state correction in [tasks/lessons.md](/Users/joemccann/dev/apps/finance/radon/tasks/lessons.md).
- Shipped the stack from `main` by pushing the existing code commits plus the docs-only follow-up commit, then verified the worktree returned clean.

## Session: Refresh `/api/performance` Against The Current ET Session Before Rebuild (2026-03-13)

### Goal
Strengthen the `/api/performance` GET route so it does not trust a stale `portfolio.json` snapshot when the current ET session has advanced. If the cached reconstructed performance still points at the prior session, the route should first refresh the portfolio snapshot via the existing IB sync wrapper, then rebuild performance from that fresher portfolio state. If that portfolio refresh fails, the route should preserve the cached performance payload instead of overwriting it from stale inputs.

### Dependency Graph
- T1 (Inspect the leftover route-test diff, the current `/api/performance` route, and the existing `ibSync` wrapper contract before editing code) depends_on: []
- T2 (Drive the route regressions red for current-session lag and portfolio-refresh failure handling) depends_on: [T1]
- T3 (Implement the minimal route fix so GET refreshes portfolio first when the cached performance lags the current ET session) depends_on: [T2]
- T4 (Run targeted Vitest verification, capture review notes, and create a separate scoped commit) depends_on: [T3]

### Checklist
- [x] T1 Inspect the leftover route-test diff, the current `/api/performance` route, and the existing `ibSync` wrapper contract before editing code
- [x] T2 Drive the route regressions red for current-session lag and portfolio-refresh failure handling
- [x] T3 Implement the minimal route fix so GET refreshes portfolio first when the cached performance lags the current ET session
- [x] T4 Run targeted Vitest verification, capture review notes, and create a separate scoped commit

### Review
- Root cause: [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/performance/route.ts) only compared `performance.json` to the current `portfolio.json`. If both caches were still on yesterday’s session while today’s ET session had already started, the route treated them as mutually consistent and skipped the rebuild. The leftover route tests correctly captured the stronger contract: before rebuilding from a prior-session portfolio snapshot, GET must first try to refresh `portfolio.json` through the existing [ib-sync wrapper](/Users/joemccann/dev/apps/finance/radon/lib/tools/wrappers/ib-sync.ts).
- Added ET-session targeting to [performanceFreshness.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/performanceFreshness.ts) with `latestPortfolioTargetDateET()` and `isPortfolioBehindCurrentEtSession()`. The target uses the latest ET weekday, which keeps weekend checks pinned to Friday instead of treating Saturday/Sunday as fresh portfolio sessions.
- Fixed [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/performance/route.ts) so GET now calls `ibSync({ sync: true, port: 4001 })` when `portfolio.json` is behind the current ET session. If that refresh succeeds, the route rebuilds performance from the fresher portfolio snapshot. If it fails and the cached performance is only as fresh as the stale portfolio snapshot, the route returns the cached performance payload instead of rewriting `performance.json` from stale inputs. If the cached performance already lags a newer `portfolio.json`, the route still rebuilds from that newer on-disk portfolio snapshot.
- Locked the route contract with [performance-route.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-route.test.ts) and expanded [performance-freshness.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-freshness.test.ts) for the ET target-date helpers.
- Verification was green with `npx vitest run web/tests/performance-route.test.ts web/tests/performance-freshness.test.ts` and `cd web && npm run build`.

## Session: Keep /performance Reconstructed YTD In Sync With Portfolio Refreshes (2026-03-13)

### Goal
Stop the `/performance` hero from staying on a stale reconstructed YTD snapshot after the workspace portfolio sync advances. When the shell refreshes `portfolio.last_sync`, the performance panel should promptly revalidate `/api/performance` and adopt the rebuilt same-session payload instead of waiting for its long polling interval or a manual page reload.

### Dependency Graph
- T1 (Trace the `/performance` freshness contract across the shell portfolio sync, the performance hook, and `/api/performance` so the stale boundary is explicit before editing code) depends_on: []
- T2 (Add failing regression coverage for the stale-after-portfolio-sync behavior in unit and browser tests) depends_on: [T1]
- T3 (Implement the minimal freshness fix so the performance panel reacts to a newer portfolio sync timestamp and revalidates `/api/performance`) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, review the live `/performance` result, and capture notes) depends_on: [T3]

### Checklist
- [x] T1 Trace the `/performance` freshness contract across the shell portfolio sync, the performance hook, and `/api/performance` so the stale boundary is explicit before editing code
- [x] T2 Add failing regression coverage for the stale-after-portfolio-sync behavior in unit and browser tests
- [x] T3 Implement the minimal freshness fix so the performance panel reacts to a newer portfolio sync timestamp and revalidates `/api/performance`
- [x] T4 Run targeted Vitest and Playwright verification, review the live `/performance` result, and capture notes

### Review
- Root cause: the performance cache route already knew how to rebuild when `data/performance.json` lagged `data/portfolio.json`, but the `/performance` page itself had no link to the shell’s much fresher portfolio sync lifecycle. [usePortfolio.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePortfolio.ts) keeps pushing `POST /api/portfolio` updates every 30 seconds and on manual `Sync Now`, while [usePerformance.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePerformance.ts) only revalidated `/api/performance` on initial load and every 15 minutes. That left the hero stuck on an older `as_of`/ending-equity snapshot even after the shell had already advanced `portfolio.last_sync`.
- Added a shared freshness contract in [performanceFreshness.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/performanceFreshness.ts). Both [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/performance/route.ts) and [PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx) now use the same rule: performance is behind whenever either `last_sync` or the session `as_of` date no longer matches the latest portfolio sync timestamp.
- Fixed the UI handoff by threading `portfolioLastSync` from [WorkspaceShell.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx) through [WorkspaceSections.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx) into [PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx). When the shell portfolio sync advances, the performance panel now issues a fresh `GET /api/performance`, which lets the route rebuild inline and replace the stale reconstructed YTD banner immediately instead of waiting for the next long poll.
- Locked the fix with red/green coverage in [performance-freshness.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-freshness.test.ts) and [performance-page.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-page.spec.ts). The new browser regression proves the bug directly: after `Sync Now` advances the portfolio snapshot, the hero updates from `AS OF 2026-03-12` to `AS OF 2026-03-13` and adopts the rebuilt ending equity.
- Verification was green with `npx vitest run web/tests/performance-freshness.test.ts web/tests/performance-route.test.ts`, `cd web && npx playwright test e2e/performance-page.spec.ts --config playwright.config.ts`, and `cd web && npm run build`.

## Session: Keep /regime Aligned With Today's Settled Close After Market Hours (2026-03-12)

### Goal
Stop the `/regime` page from getting stuck on an earlier after-close CRI snapshot. Once the market closes, the open page should keep following `/api/regime` at the route's freshness cadence and roll to today's settled closing values automatically instead of freezing on a stale browser-triggered rescan.

### Dependency Graph
- T1 (Trace the `/regime` refresh path from `useRegime` through `/api/regime`, confirm why an open page can keep an earlier after-close payload, and record the exact cache-following contract before editing code) depends_on: []
- T2 (Add failing regression coverage for the `/regime` hook config and browser refresh behavior proving the page must adopt newer same-day closed values without browser-side POST rescans) depends_on: [T1]
- T3 (Update the `/regime` sync configuration so it follows the route freshness contract and keeps the open page aligned with later close-cache updates) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, inspect the post-close `/regime` behavior, and capture review notes in the task log) depends_on: [T3]

### Checklist
- [x] T1 Trace the `/regime` refresh path from `useRegime` through `/api/regime`, confirm why an open page can keep an earlier after-close payload, and record the exact cache-following contract before editing code
- [x] T2 Add failing regression coverage for the `/regime` hook config and browser refresh behavior proving the page must adopt newer same-day closed values without browser-side POST rescans
- [x] T3 Update the `/regime` sync configuration so it follows the route freshness contract and keeps the open page aligned with later close-cache updates
- [x] T4 Run targeted verification, inspect the post-close `/regime` behavior, and capture review notes in the task log

### Review
- Root cause: the first post-close CRI scans can still write the prior session for a short window after the bell. On disk, `data/cri_scheduled/cri-2026-03-12T16-01.json` and `...16-02.json` both carried `date: "2026-03-11"`, while a later scan produced `date: "2026-03-12"`. That left two gaps: the scanner could lag the settled close, and an already-open `/regime` page did not revalidate aggressively enough to pick up the corrected payload.
- Fixed the scanner path in [cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/cri_scan.py) by synthesizing today's post-close snapshot from current VIX, VVIX, SPY, and COR1M quotes when the daily bar set still ends on the prior session. The targeted regression is [test_cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_cri_scan.py).
- Fixed the UI follow-through in [useRegime.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useRegime.ts) by making the regime view cache-following only (`interval: 60_000`, `hasPost: false`) and by adding an ET-session mismatch retry policy. [useSyncHook.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useSyncHook.ts) now supports optional `shouldRetry`, `retryIntervalMs`, and `retryMethod` so `/regime` can keep doing lightweight `GET` revalidation every 5 seconds until the current-session close arrives.
- Added red/green coverage in [regime-sync-config.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-sync-config.test.ts), [regime-close-transition-retry.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-close-transition-retry.test.ts), [regime-close-transition-refresh.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-close-transition-refresh.spec.ts), and [regime-market-closed-eod.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-market-closed-eod.spec.ts). The browser harnesses prove both behaviors: the page auto-refreshes from a stale prior-session payload to today's close, and the market-closed strip renders the current session close payload once it exists.
- Verification was green with `python3 -m pytest scripts/tests/test_cri_scan.py -k AppendPostCloseSnapshot`, `npx vitest run web/tests/regime-close-transition-retry.test.ts web/tests/regime-sync-config.test.ts web/tests/regime-market-closed-values.test.ts web/tests/regime-cri-staleness.test.ts`, `cd web && npx playwright test e2e/regime-close-transition-refresh.spec.ts e2e/regime-market-closed-eod.spec.ts --config playwright.config.ts`, and `cd web && npm run build`.

## Session: Align Regime Quadrant Marker Color With The Classified State (2026-03-12)

### Goal
Fix the `/regime` relationship view so the latest RVOL/COR1M scatter marker uses the same quadrant classification as the summary label and state key. A `Systemic Panic` classification must not render as the yellow `Stock Picker's Market` marker.

### Dependency Graph
- T1 (Inspect the shared regime classification path and record the renderer mismatch before editing tests or UI code) depends_on: []
- T2 (Add failing regression coverage proving the latest quadrant marker must use the classified quadrant color instead of a hardcoded yellow highlight) depends_on: [T1]
- T3 (Update the relationship-view renderer to derive the latest marker color from the shared quadrant classification) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then capture review notes in the task log) depends_on: [T3]

### Checklist
- [x] T1 Inspect the shared regime classification path and record the renderer mismatch before editing tests or UI code
- [x] T2 Add failing regression coverage proving the latest quadrant marker must use the classified quadrant color instead of a hardcoded yellow highlight
- [x] T3 Update the relationship-view renderer to derive the latest marker color from the shared quadrant classification
- [x] T4 Run targeted Vitest and Playwright verification, then capture review notes in the task log

### Review
- The root cause was in [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx): the summary label and state key already used the shared classifier from [web/lib/regimeRelationships.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/regimeRelationships.ts), but the latest scatter marker was still hardcoded to `var(--warning)`, which visually implied `Stock Picker's Market` even when the classifier said otherwise.
- Added a red-phase source contract in [web/tests/regime-relationship-tooltips.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-relationship-tooltips.test.ts) and a browser regression in [web/e2e/regime-relationship-view.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-relationship-view.spec.ts) to require the latest marker tone to match the classified quadrant.
- Fixed the renderer by deriving `latestQuadrantColor` from `quadrantTone(latest.quadrant)` and using that for the latest scatter point fill and stroke in [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx).
- Verification was green with `npx vitest run web/tests/regime-relationship-tooltips.test.ts` and `cd web && npx playwright test e2e/regime-relationship-view.spec.ts --config playwright.config.ts --grep "classified quadrant tone"`. The Playwright check initially hit a stale long-lived `next-server` on port `3000`; after replacing that server with a fresh Playwright-managed Next instance, the browser test passed against the patched code.

## Session: Append Local Timezone To Rendered Web Timestamps (2026-03-12)

### Goal
Audit `/web` for every rendered timestamp or clock string and make those UI surfaces include the machine-local timezone abbreviation. The implementation must use the actual local timezone dynamically instead of hardcoding `PST`, because the abbreviation changes with daylight saving time.

### Dependency Graph
- T1 (Audit `/web` for all rendered timestamps and record the exact surface list before editing code) depends_on: []
- T2 (Design or identify a shared formatter that appends the local timezone abbreviation dynamically) depends_on: [T1]
- T3 (Update the affected `/web` surfaces to use the shared formatter and add regression coverage where it fits) depends_on: [T2]
- T4 (Run targeted verification, review the rendered timestamp strings, and capture review notes in the task log) depends_on: [T3]

### Checklist
- [ ] T1 Audit `/web` for all rendered timestamps and record the exact surface list before editing code
- [ ] T2 Design or identify a shared formatter that appends the local timezone abbreviation dynamically
- [ ] T3 Update the affected `/web` surfaces to use the shared formatter and add regression coverage where it fits
- [ ] T4 Run targeted verification, review the rendered timestamp strings, and capture review notes in the task log

## Session: Split Remaining Runtime Artifacts And Prior Feature Work Into Scoped Commits (2026-03-12)

### Goal
Take the remaining listed worktree items and turn them into clean individual commits without pulling in unrelated files. The expected batches are runtime/cache artifacts, the stale frozen option `LAST` feature from the prior session, and the one-line brand guidance update if it stands alone.

### Dependency Graph
- T1 (Inspect the listed dirty and untracked files, confirm the logical groupings, and record the exact commit plan before staging anything) depends_on: []
- T2 (Validate the stale frozen option `LAST` feature bundle and decide whether the untracked Playwright config belongs with it) depends_on: [T1]
- T3 (Create separate scoped commits for runtime artifacts, stale frozen option `LAST` logic/tests, and the docs guidance change) depends_on: [T2]
- T4 (Verify the resulting commit stack and capture review notes in the task log) depends_on: [T3]

### Checklist
- [x] T1 Inspect the listed dirty and untracked files, confirm the logical groupings, and record the exact commit plan before staging anything
- [x] T2 Validate the stale frozen option `LAST` feature bundle and decide whether the untracked Playwright config belongs with it
- [x] T3 Create separate scoped commits for runtime artifacts, stale frozen option `LAST` logic/tests, and the docs guidance change
- [x] T4 Verify the resulting commit stack and capture review notes in the task log

### Review
- Created `f63843a` (`chore: refresh runtime cache artifacts`) for the safe runtime outputs only: [AAOI.json](/Users/joemccann/dev/apps/finance/radon/data/company_info_cache/AAOI.json), [PLTR.json](/Users/joemccann/dev/apps/finance/radon/data/company_info_cache/PLTR.json), [cta-sync.json](/Users/joemccann/dev/apps/finance/radon/data/service_health/cta-sync.json), [option_close_cache.json](/Users/joemccann/dev/apps/finance/radon/web/data/option_close_cache.json), and [.claude/hooks/dead-code.manifest](/Users/joemccann/dev/apps/finance/radon/.claude/hooks/dead-code.manifest).
- Created `2a485d6` (`fix: guard option last price against stale frozen ticks`) for the prior-session option price feature: [ib_tick_handler.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_tick_handler.js), [stale-frozen-last.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/stale-frozen-last.test.ts), and [stale-option-last-price.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/stale-option-last-price.spec.ts). Verification was green with `npx vitest run web/tests/stale-frozen-last.test.ts` and `cd web && npx playwright test e2e/stale-option-last-price.spec.ts --config playwright.config.ts`. The only code adjustment during validation was fixing the Playwright selector to target the option last-price cell instead of the underlying placeholder cell.
- Created `a7ad62e` (`docs: add dense telemetry strip guidance`) for the one-line guidance note in [brand-identity.md](/Users/joemccann/dev/apps/finance/radon/docs/brand-identity.md).
- Left [playwright.strip.config.ts](/Users/joemccann/dev/apps/finance/radon/web/playwright.strip.config.ts) out of the commit stack because it targets only `regime-strip-responsive.spec.ts` and is unrelated to the stale frozen option `LAST` feature or the runtime artifact/doc batches.

## Session: Chain UX, IB Error Handling, Ticker Detail Layout (2026-03-12)

### Completed
- [x] Fix sticky chain headers (root cause: `border-collapse: collapse` breaks `position: sticky`)
- [x] Add ALL/CALLS/PUTS side filter toggle to options chain
- [x] Handle IB error code 200 (no security definition) silently in WS relay
- [x] Layout ticker detail hero as 2x1 grid (telemetry + chart side-by-side)
- [x] Fix order builder notional double-counting quantity (`computeNetPrice` already includes qty)

## Session: Remove Generic IB Reconnect Banner Regression (2026-03-12)

### Goal
Limit the shell-level IB connection banner to the actionable MFA/IBC approval state only. Generic reconnect and disconnect states should not render the upper banner again; those belong on the existing toast channel. This specifically fixes the regression where `IB Gateway reconnected` is shown as a full-width top banner.

### Dependency Graph
- T1 (Inspect the current banner contract, record the regression scope, and capture the user-correction lesson before editing tests or UI code) depends_on: []
- T2 (Add failing unit and Playwright coverage proving that generic reconnect status must not render the upper banner while MFA approval still does) depends_on: [T1]
- T3 (Tighten the connection-banner helper and renderer so only the actionable MFA state surfaces in the upper shell banner) depends_on: [T2]
- T4 (Run targeted Vitest, Playwright, and build verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current banner contract, record the regression scope, and capture the user-correction lesson before editing tests or UI code
- [x] T2 Add failing unit and Playwright coverage proving that generic reconnect status must not render the upper banner while MFA approval still does
- [x] T3 Tighten the connection-banner helper and renderer so only the actionable MFA state surfaces in the upper shell banner
- [x] T4 Run targeted Vitest, Playwright, and build verification, then capture review notes

### Review
- Root cause: the earlier MFA fix expanded [ibConnectionAlert.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/ibConnectionAlert.ts) and [ConnectionBanner.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ConnectionBanner.tsx) into a generic shell-level connection banner. That reintroduced the top-of-shell `IB Gateway reconnected` / `IB Gateway disconnected` strip, which should have remained on the existing toast channel only.
- Tightened the banner contract so the upper shell banner now renders only for the actionable `ibc_mfa_required` state. Generic websocket disconnects, generic IB disconnects, and reconnect success states now return `null` from [ibConnectionAlert.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/ibConnectionAlert.ts), and [ConnectionBanner.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ConnectionBanner.tsx) is now a stateless MFA-warning renderer instead of a reconnect/disconnect state machine.
- Locked the regression with unit coverage in [connection-banner-state.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/connection-banner-state.test.ts) and browser coverage in [ib-mfa-reconnect-alert.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/ib-mfa-reconnect-alert.spec.ts). The browser file now proves three cases: MFA warning does show the upper banner, generic disconnect does not, and generic reconnect success does not.
- Verified green with `npx vitest run web/tests/connection-banner-state.test.ts web/tests/ib-connection-issue.test.ts`, `cd web && npx playwright test e2e/ib-mfa-reconnect-alert.spec.ts --config playwright.config.ts`, and `cd web && npm run build`.

## Session: Surface IB Gateway MFA Approval In The UI (2026-03-12)

### Goal
When the repo-owned realtime server starts while IB Gateway is still reconnecting behind IBC and emits `IB error: connect ECONNREFUSED 127.0.0.1:4001`, surface an explicit operator-facing UI alert that tells the user to check the Interactive Brokers push notification on their phone and approve MFA instead of leaving the cause buried in terminal logs.

### Dependency Graph
- T1 (Inspect the current realtime status flow and record the MFA-alert contract before editing code) depends_on: []
- T2 (Add failing regression tests for the server-side IB-issue classification and the UI alert-copy contract) depends_on: [T1]
- T3 (Implement the server status reason, client-side status plumbing, and the visible UI alert/toast behavior) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current realtime status flow and record the MFA-alert contract before editing code
- [x] T2 Add failing regression tests for the server-side IB-issue classification and the UI alert-copy contract
- [x] T3 Implement the server status reason, client-side status plumbing, and the visible UI alert/toast behavior
- [x] T4 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Root cause: [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js) only surfaced the IB Gateway reconnect failure as a terminal log (`IB error: connect ECONNREFUSED 127.0.0.1:4001`). The websocket status payload sent to the browser only carried `ib_connected`, so the UI could not distinguish "gateway disconnected" from "IBC is waiting on phone MFA approval."
- Added a server-side classifier in [ib_connection_status.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_connection_status.js) and wired it into [ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js). When the reconnect error matches the local gateway refusal, the websocket status now includes `ib_issue: "ibc_mfa_required"` plus an operator-facing `ib_status_message` telling the user to check the Interactive Brokers push notification on their phone and approve MFA.
- Extended the browser protocol and client plumbing in [pricesProtocol.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/pricesProtocol.ts), [usePrices.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePrices.ts), [ibConnectionAlert.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/ibConnectionAlert.ts), [ConnectionBanner.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ConnectionBanner.tsx), and [WorkspaceShell.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx). The shell now renders a visible warning banner and uses MFA-specific disconnect toast copy instead of a generic lost-connection message.
- Locked the fix with targeted regression coverage in [ib-connection-issue.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/ib-connection-issue.test.ts), [connection-banner-state.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/connection-banner-state.test.ts), and [ib-mfa-reconnect-alert.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/ib-mfa-reconnect-alert.spec.ts). Verified green with `npx vitest run web/tests/ib-connection-issue.test.ts web/tests/connection-banner-state.test.ts`, `cd web && npx playwright test e2e/ib-mfa-reconnect-alert.spec.ts --config playwright.config.ts`, and `cd web && npm run build`.

## Session: Make IB Realtime Dev Startup Survive Port 8765 Conflicts (2026-03-12)

### Goal
Fix the `web` verbose dev workflow so `node ../scripts/ib_realtime_server.js` does not crash the whole startup path when websocket port `8765` is already occupied. The dev server should handle a reusable existing listener or a collision gracefully instead of throwing an unhandled `EADDRINUSE`.

### Dependency Graph
- T1 (Record the port-conflict bug contract and the latest user-correction lesson before editing tests or startup code) depends_on: []
- T2 (Inspect the verbose dev startup path and add a failing regression test that reproduces the current `EADDRINUSE` crash) depends_on: [T1]
- T3 (Implement the minimal IB realtime server startup fix so the dev workflow handles an occupied port cleanly) depends_on: [T2]
- T4 (Run targeted regression and startup verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the port-conflict bug contract and the latest user-correction lesson before editing tests or startup code
- [x] T2 Inspect the verbose dev startup path and add a failing regression test that reproduces the current `EADDRINUSE` crash
- [x] T3 Implement the minimal IB realtime server startup fix so the dev workflow handles an occupied port cleanly
- [x] T4 Run targeted regression and startup verification, then capture review notes

### Review
- Root cause: [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js) created the `WebSocketServer` at module load. If `8765` was already occupied, Node emitted an uncaught `EADDRINUSE` before the dev workflow could recover, which is exactly the crash shown in your `dev:verbose:next` output.
- Fixed startup by probing the websocket port before creating the server in [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js). When the port is already bound, the script now prints an explicit reuse message and exits `0` instead of throwing.
- Added a real regression in [ib-realtime-port-conflict.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/ib-realtime-port-conflict.test.ts) that occupies an ephemeral TCP port, spawns the realtime server against it, and asserts a clean exit plus the reuse message.
- Verified green with `npx vitest run web/tests/ib-realtime-port-conflict.test.ts` and `cd web && node ../scripts/ib_realtime_server.js`, which now returns:
  `WebSocket port already in use at ws://0.0.0.0:8765; assuming an existing IB realtime server and skipping duplicate startup.`

## Session: Document And Ship Regime Responsive UI Pass (2026-03-12)

### Goal
Capture the `/regime` strip and relationship-view learnings in the repo docs, then create a scoped commit and push only the relevant UI, test, and documentation changes without pulling unrelated dirty files into the batch.

### Dependency Graph
- T1 (Inspect the current worktree and identify the operator-facing docs that should record the `/regime` responsive learnings) depends_on: []
- T2 (Update the selected docs plus the task log with the final `/regime` responsive-strip and relationship-view learnings) depends_on: [T1]
- T3 (Stage only the relevant `/regime` UI, tests, task-log, and documentation files, then create a scoped commit) depends_on: [T2]
- T4 (Push the commit, verify the pushed state, and capture the exact ship summary in the task log) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current worktree and identify the operator-facing docs that should record the `/regime` responsive learnings
- [x] T2 Update the selected docs plus the task log with the final `/regime` responsive-strip and relationship-view learnings
- [x] T3 Stage only the relevant `/regime` UI, tests, task-log, and documentation files, then create a scoped commit
- [x] T4 Push the commit, verify the pushed state, and capture the exact ship summary in the task log

### Review
- Updated the operator-facing summary in [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) so the terminal docs now describe the shared `/regime` strip renderer and its responsive `5-up -> 3x2 -> stacked telemetry rail` contract.
- Added a ship note in [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md) that captures the full `/regime` batch: shared strip renderer, earlier narrow-width detail-panel collapse, and actionable plus hoverable normalized-divergence guidance.
- Created the scoped ship commit `13aadeb` (`fix: tighten regime operator telemetry`) from only the `/regime` UI, tests, docs, and task-log files, leaving unrelated runtime/cache/worktree files out of the batch.
- Verified the shipped batch with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-detail-panels-responsive.test.ts web/tests/regime-relationship-tooltips.test.ts`, `cd web && npm run build`, and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-detail-panels-responsive.spec.ts e2e/regime-relationship-view.spec.ts --config playwright.config.ts`.

## Session: Replace Centered Mobile Strip Rows With Anchored Telemetry Rails (2026-03-12)

### Goal
When the `/regime` strip collapses to a single-column stack, stop centering the telemetry cluster inside each row. Use the full row width with an anchored compact rail: keep the instrument label and value as the left-side primary signal, keep the change arrow attached to the change text, and pack the supporting context into a horizontal meta rail instead of wasting half the viewport as empty panel space.

### Dependency Graph
- T1 (Record the corrected anchored-row contract and the latest user-correction lesson before editing tests or UI code) depends_on: []
- T2 (Update unit and Playwright strip coverage to require the anchored telemetry-rail layout in the stacked mobile state and observe the red state) depends_on: [T1]
- T3 (Refactor the stacked strip markup/CSS to a left-anchored telemetry rail without regressing the desktop and mid-width strip presentations) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, inspect the live `/regime` result, and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected anchored-row contract and the latest user-correction lesson before editing tests or UI code
- [x] T2 Update unit and Playwright strip coverage to require the anchored telemetry-rail layout in the stacked mobile state and observe the red state
- [x] T3 Refactor the stacked strip markup/CSS to a left-anchored telemetry rail without regressing the desktop and mid-width strip presentations
- [x] T4 Run targeted Vitest and Playwright verification, inspect the live `/regime` result, and capture review notes

### Review
- Moved the stacked-strip renderer to an intentional two-column mobile rail in [web/components/RegimeStrip.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeStrip.tsx): each row now has a compact primary column for label plus value and a shared `regime-strip-meta-row` for delta, context, and timestamp instead of dumping every token into one centered cluster.
- Refined the small-screen strip rules in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so `760px` and below use a `minmax(96px, 112px) / 1fr` grid plus a bordered meta rail with segmented dividers. That turns the wasted panel space into a deliberate telemetry tape while keeping the delta arrow attached to the change text.
- Locked the source contract in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and updated the Playwright strip harness in [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts) so it validates the shared `RegimeStrip` renderer in the browser. The red phase failed first on the missing `regime-strip-primary`/`regime-strip-meta-row` structure and the missing mobile grid contract.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts --config playwright.config.ts`.

## Session: Compress Stacked Mobile Strip Cells Into Single-Line Rows (2026-03-12)

### Goal
When the `/regime` strip is in its single-column stacked state, use the full row width by centering the telemetry content vertically and laying the cell metadata out in one horizontal line instead of a tall vertical stack.

### Dependency Graph
- T1 (Record the corrected stacked-row compactness contract and the user-correction lesson before editing tests or CSS) depends_on: []
- T2 (Update unit and Playwright strip coverage to require a centered single-line cell layout in the stacked mobile state and observe the red state) depends_on: [T1]
- T3 (Refine the small-screen strip layout/CSS so each stacked cell uses a compact horizontal row presentation without regressing the mid-width strip states) depends_on: [T2]
- T4 (Run targeted strip verification and capture review notes before resuming any additional browser validation) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected stacked-row compactness contract and the user-correction lesson before editing tests or CSS
- [x] T2 Update unit and Playwright strip coverage to require a centered single-line cell layout in the stacked mobile state and observe the red state
- [x] T3 Refine the small-screen strip layout/CSS so each stacked cell uses a compact horizontal row presentation without regressing the mid-width strip states
- [x] T4 Run targeted strip verification and capture review notes before resuming any additional browser validation

### Review
- Refined the stacked-mobile strip presentation in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so each `regime-strip-cell` becomes a compact centered row at `760px` and below: label, value, delta, context subline, and timestamp now sit on one horizontal line instead of stacking vertically.
- Tightened the supporting small-screen rules so the delta row remains inline and the surrounding telemetry tokens stop wrapping vertically in the collapsed state.
- Locked the contract in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts). The red phase failed because the stacked strip still kept the desktop column layout inside each cell.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts --config playwright.config.ts`.

## Session: Left Align Mobile Strip Delta Rows (2026-03-12)

### Goal
Keep the small-screen `/regime` strip vertically stacked, but make the delta rows inside each cell stay left aligned and inline so the arrow stays attached to the change text instead of drifting to the far right edge.

### Dependency Graph
- T1 (Record the corrected mobile delta-row contract and the user-correction lesson before editing tests or CSS) depends_on: []
- T2 (Update unit and Playwright strip coverage to require inline, left-aligned delta rows in the stacked mobile strip and observe the red state) depends_on: [T1]
- T3 (Refine the small-screen strip CSS so delta rows use an inline left-aligned layout without disturbing the mid-width strip geometry) depends_on: [T2]
- T4 (Run targeted strip verification and capture review notes before resuming the rebuild/restart flow) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected mobile delta-row contract and the user-correction lesson before editing tests or CSS
- [x] T2 Update unit and Playwright strip coverage to require inline, left-aligned delta rows in the stacked mobile strip and observe the red state
- [x] T3 Refine the small-screen strip CSS so delta rows use an inline left-aligned layout without disturbing the mid-width strip geometry
- [x] T4 Run targeted strip verification and capture review notes before resuming the rebuild/restart flow

### Review
- Refined the stacked-mobile strip behavior in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so `.regime-strip-day-chg` switches from the desktop `minmax(0, 1fr) auto` split to an inline left-aligned flex row at `760px` and below. The arrow now stays attached to the change text inside each vertically stacked cell instead of drifting to the far right edge.
- Locked that contract in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts). The red phase failed because the small-screen strip still used the desktop split layout.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts --config playwright.config.ts`.

## Session: Make Mid-Width Regime Strip Bottom Row Symmetric (2026-03-12)

### Goal
Keep the `/regime` strip on a clean mid-width `3 x 2` presentation without an empty slot and without an asymmetrical bottom row, so the two second-row cards render at equal width before the strip collapses vertically on small screens.

### Dependency Graph
- T1 (Record the corrected symmetric-bottom-row contract and the user-correction lesson before editing tests or CSS) depends_on: []
- T2 (Update unit and Playwright strip coverage to require two equal-width bottom-row cards in the mid-width state and observe the red state) depends_on: [T1]
- T3 (Refine the mid-width strip grid so the first row stays three-up while the second row renders two equal-width cards, then preserve the small-screen vertical collapse) depends_on: [T2]
- T4 (Run targeted strip verification and capture review notes before resuming the rebuild/restart flow) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected symmetric-bottom-row contract and the user-correction lesson before editing tests or CSS
- [x] T2 Update unit and Playwright strip coverage to require two equal-width bottom-row cards in the mid-width state and observe the red state
- [x] T3 Refine the mid-width strip grid so the first row stays three-up while the second row renders two equal-width cards, then preserve the small-screen vertical collapse
- [x] T4 Run targeted strip verification and capture review notes before resuming the rebuild/restart flow

### Review
- Reworked the mid-width strip geometry in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) to a six-track internal grid at `1180px` and below: the first three cards span two tracks each, and the bottom two cards span three tracks each. That preserves the visual `3 x 2` presentation while making the bottom row symmetric.
- Updated [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts) to require equal-width bottom-row cards in the mid-width state. The red phase failed because the previous fix still stretched `COR1M` while leaving `RVOL` narrow.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts --config playwright.config.ts`.

## Session: Rebuild And Restart Radon Web For Browser Validation (2026-03-12)

### Goal
Rebuild the Radon web app, restart the repo-owned web server cleanly on the standard local port, confirm it responds, and open `/regime` in the browser for direct inspection.

### Dependency Graph
- T1 (Inspect the current web scripts and running Radon web processes so the correct server can be rebuilt and restarted) depends_on: []
- T2 (Run a fresh production build for `web` and address any build blocker that prevents restart) depends_on: [T1]
- T3 (Stop the repo-owned Radon web/IB dev stack, restart it cleanly, and confirm the target local URL responds) depends_on: [T2]
- T4 (Open the validated `/regime` URL in the browser and capture the execution result in the task log) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current web scripts and running Radon web processes so the correct server can be rebuilt and restarted
- [x] T2 Run a fresh production build for `web` and address any build blocker that prevents restart
- [x] T3 Stop the repo-owned Radon web/IB dev stack, restart it cleanly, and confirm the target local URL responds
- [x] T4 Open the validated `/regime` URL in the browser and capture the execution result in the task log

### Review
- Rebuilt the current `web` worktree successfully with `cd web && npm run build`. The only blocker was a pre-existing TypeScript union-access issue in [web/components/CtaPage.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CtaPage.tsx), which is now fixed by explicitly separating `sync_health` and `sync_status` fallback access instead of reading mixed properties off the union.
- Stopped the repo-owned `localhost:3000` Radon dev stack and its paired IB websocket process, then restarted it from [web/package.json](/Users/joemccann/dev/apps/finance/radon/web/package.json) with `npm run dev`. The stack came back cleanly: Next.js reported `Local: http://localhost:3000` and the IB realtime server reported `listening on ws://0.0.0.0:8765` plus `IB connected`.
- Confirmed the rebuilt server is serving the target page with `curl -I http://localhost:3000/regime`, which returned `HTTP/1.1 200 OK`, and opened `http://localhost:3000/regime` in the browser with `open http://localhost:3000/regime`.

## Session: Remove Empty Regime Strip Placeholder At Mid Widths (2026-03-12)

### Goal
Keep the `/regime` telemetry strip on a clean `3 x 2` layout at mid-width desktop/tablet sizes without rendering an empty placeholder cell, then collapse to a fully vertical stack on small viewports.

### Dependency Graph
- T1 (Record the corrected odd-card strip contract and the user-correction lesson before editing tests or CSS) depends_on: []
- T2 (Update unit and Playwright coverage to require a filled `3 x 2` strip with no empty placeholder and a vertical small-screen collapse, then observe the red state) depends_on: [T1]
- T3 (Implement the minimal grid/CSS change so the fifth card fills the last row cleanly and the strip stacks to one column on small viewports) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected odd-card strip contract and the user-correction lesson before editing tests or CSS
- [x] T2 Update unit and Playwright coverage to require a filled `3 x 2` strip with no empty placeholder and a vertical small-screen collapse, then observe the red state
- [x] T3 Implement the minimal grid/CSS change so the fifth card fills the last row cleanly and the strip stacks to one column on small viewports
- [x] T4 Run targeted Vitest and Playwright verification and capture review notes

### Review
- Root cause: [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) collapsed the five-card strip to `repeat(3, minmax(0, 1fr))` at `1180px` and below while leaving the grid container background visible. With only five cards, that produced an unused sixth grid slot that read like a fake empty card.
- Fixed the mid-width collapse in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) by making the last strip cell span the remaining two columns in the `3 x 2` state, then resetting that span and switching to a true `1fr` stack at `760px` and below so smaller screens collapse vertically instead of to a sparse multi-column layout.
- Locked the new contract in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts). The red phase failed because the old CSS had no last-card span rule and still used a smaller-screen multi-column collapse.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-relationship-tooltips.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-relationship-view.spec.ts --config playwright.config.ts`.

## Session: Make Normalized Divergence Tooltip Actionable And Hoverable (2026-03-12)

### Goal
Update the `/regime` `NORMALIZED DIVERGENCE` surface so the tooltip explains the concrete trade and portfolio actions implied by the RVOL versus COR1M relationship, and the chart itself exposes hoverable point-in-time readouts like the analytical time-series charts.

### Dependency Graph
- T1 (Record the corrected tooltip-plus-hover goal and the user-correction lessons before editing tests or code) depends_on: []
- T2 (Update unit and Playwright coverage to require actionable normalized-divergence guidance plus a hoverable z-score chart and observe the red state) depends_on: [T1]
- T3 (Revise the centralized tooltip copy and implement the z-score chart hover overlay using the shared analytical-chart interaction pattern) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected tooltip-plus-hover goal and the user-correction lessons before editing tests or code
- [x] T2 Update unit and Playwright coverage to require actionable normalized-divergence guidance plus a hoverable z-score chart and observe the red state
- [x] T3 Revise the centralized tooltip copy and implement the z-score chart hover overlay using the shared analytical-chart interaction pattern
- [x] T4 Run targeted Vitest and Playwright verification and capture review notes

### Review
- Centralized the actionable `NORMALIZED DIVERGENCE` guidance in [web/lib/sectionTooltips.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/sectionTooltips.ts) so the tooltip now explains both sides of the signal in portfolio terms: positive-gap posture (`reduce gross exposure`, `keep or add index hedges`) and negative-gap posture (`harvest crash hedges`, rotate back toward `single-name` expression when the gap mean-reverts).
- Added chart hover telemetry to [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx) with a dedicated chart shell, SVG overlay hook, hover state, and a shared `.chart-tooltip` readout for date, `RVOL z-score`, `COR1M z-score`, and `Divergence`, while keeping the rest of the relationship view static.
- Added the minimal supporting styles in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) and locked the surface contract in [web/tests/regime-relationship-tooltips.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-relationship-tooltips.test.ts) plus [web/e2e/regime-relationship-view.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-relationship-view.spec.ts). The red phase failed because the tooltip copy was still definition-only and the z-score chart had no hover DOM or readout.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-relationship-tooltips.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-relationship-view.spec.ts --config playwright.config.ts`.

## Session: Fix Regime Detail Panels Collapse On Narrower Viewports (2026-03-12)

### Goal
Make the `/regime` `CRI COMPONENTS` and `CRASH TRIGGER CONDITIONS` panels collapse cleanly on narrower viewports instead of staying in a cramped two-column row, and add the missing `NORMALIZED DIVERGENCE` info bubble in the relationship section.

### Dependency Graph
- T1 (Inspect the shared detail-panel row and record the responsive-fix plan plus correction lesson before changing code) depends_on: []
- T2 (Add failing unit and Playwright coverage that requires the two panels to stack cleanly at narrower widths and exposes the missing `NORMALIZED DIVERGENCE` tooltip) depends_on: [T1]
- T3 (Implement the responsive detail-panel grid, any minimal internal spacing adjustments needed to avoid clipping, and the tooltip wiring/copy) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect the shared detail-panel row and record the responsive-fix plan plus correction lesson before changing code
- [x] T2 Add failing unit and Playwright coverage that requires the two panels to stack cleanly at narrower widths and exposes the missing `NORMALIZED DIVERGENCE` tooltip
- [x] T3 Implement the responsive detail-panel grid, any minimal internal spacing adjustments needed to avoid clipping, and the tooltip wiring/copy
- [x] T4 Run targeted Vitest and Playwright verification and capture review notes

### Review
- Replaced the inline `1fr 1fr` split in [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) with a shared `.regime-detail-grid` in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css). The row now stays two-up on wide screens and stacks to one column at `980px` and below, with small-screen adjustments for component bars and trigger rows so the full-width stacked panels do not clip internal labels or values.
- Added the missing `NORMALIZED DIVERGENCE` tooltip in [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx) and centralized its copy in [web/lib/sectionTooltips.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/sectionTooltips.ts), using explicit trigger and bubble test IDs so the operator-facing info surface is covered by browser tests.
- The red phase failed in [web/tests/regime-detail-panels-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-detail-panels-responsive.test.ts), [web/tests/regime-relationship-tooltips.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-relationship-tooltips.test.ts), [web/e2e/regime-detail-panels-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-detail-panels-responsive.spec.ts), and [web/e2e/regime-relationship-view.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-relationship-view.spec.ts) because the detail row was still permanently side by side and the z-score panel had no tooltip trigger or copy key.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-detail-panels-responsive.test.ts web/tests/regime-cor1m-live.test.ts web/tests/regime-relationship-tooltips.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-detail-panels-responsive.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-live-index-stream.spec.ts e2e/regime-relationship-view.spec.ts --config playwright.config.ts`.

## Session: Replace One-Row Regime Strip Compression With Balanced Collapse (2026-03-12)

### Goal
Fix the `/regime` strip so narrower widths do not clip cards and do not force an awkward one-row squeeze; use `RVOL` in the compressed state and collapse into a balanced multi-row grid before the cards become unreadable.

### Dependency Graph
- T1 (Record the revised strip-responsive contract and correction lesson before changing tests or CSS again) depends_on: []
- T2 (Update unit and Playwright coverage to expect a balanced multi-row collapse and observe the red state) depends_on: [T1]
- T3 (Implement the revised breakpoint/layout strategy in the shared regime strip markup and CSS) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the revised strip-responsive contract and correction lesson before changing tests or CSS again
- [x] T2 Update unit and Playwright coverage to expect a balanced multi-row collapse and observe the red state
- [x] T3 Implement the revised breakpoint/layout strategy in the shared regime strip markup and CSS
- [x] T4 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Replaced the failed one-row compression strategy with a tiered strip layout in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css): the strip stays five-up at full width, collapses to three columns at `1180px` and below, then two columns at `760px` and below, with a one-column fallback at `520px`. The `RVOL` abbreviation remains active in the compressed states.
- Kept the semantic label swap in [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) so `REALIZED VOL` becomes `RVOL` when the strip is compacted, but stopped relying on abbreviation alone to save the layout.
- Locked the corrected contract in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts). The red phase failed because the previous CSS only had the one-row compressed strip and no explicit `3 / 2` collapse tiers.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-cor1m-live.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-live-index-stream.spec.ts --config playwright.config.ts`.

## Session: Replace Regime Strip Wrap With RVOL Abbreviation (2026-03-12)

### Goal
Refine the `/regime` strip responsive behavior so narrower desktop widths keep the five-card row intact by abbreviating `REALIZED VOL` to `RVOL`, while preserving a true wrap fallback only for genuinely small viewports.

### Dependency Graph
- T1 (Record the corrected responsive contract and capture the user-correction lesson before changing the strip again) depends_on: []
- T2 (Update unit and Playwright coverage to expect an `RVOL` abbreviation at narrower widths and observe the red state) depends_on: [T1]
- T3 (Implement the label-abbreviation and breakpoint refinement in the shared strip markup/CSS) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Record the corrected responsive contract and capture the user-correction lesson before changing the strip again
- [x] T2 Update unit and Playwright coverage to expect an `RVOL` abbreviation at narrower widths and observe the red state
- [x] T3 Implement the label-abbreviation and breakpoint refinement in the shared strip markup/CSS
- [x] T4 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Replaced the previous wrap-first responsive behavior with a narrower-desktop compaction strategy: [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) now provides both `REALIZED VOL` and `RVOL` label tokens for the strip, and [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) keeps the strip as a five-column grid by default, abbreviates that label at `1180px` and below, tightens spacing/type slightly, and only falls back to the wrapped auto-fit grid at `860px` and below.
- The corrected red phase is locked in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts): before the patch, unit tests failed because the strip was still auto-fit at all widths and the `RVOL` abbreviation did not exist, and Playwright failed because the narrower-desktop strip still rendered `REALIZED VOL`.
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-cor1m-live.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-live-index-stream.spec.ts --config playwright.config.ts`.

## Session: Fix Regime Strip Card Collapse On Narrower Viewports (2026-03-12)

### Goal
Prevent the `/regime` live-strip cards from overlapping on narrower desktop/tablet widths by introducing a cleaner responsive collapse pattern for the five telemetry cards.

### Dependency Graph
- T1 (Inspect the current `/regime` strip layout and record the responsive-fix plan in `tasks/todo.md` plus the correction lesson in `tasks/lessons.md`) depends_on: []
- T2 (Add failing unit and Playwright coverage that reproduces the strip overlap risk at a narrower viewport) depends_on: [T1]
- T3 (Implement the smallest clean collapse strategy in the shared strip markup/CSS without regressing the existing desktop layout) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current `/regime` strip layout and record the responsive-fix plan in `tasks/todo.md` plus the correction lesson in `tasks/lessons.md`
- [x] T2 Add failing unit and Playwright coverage that reproduces the strip overlap risk at a narrower viewport
- [x] T3 Implement the smallest clean collapse strategy in the shared strip markup/CSS without regressing the existing desktop layout
- [x] T4 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Root cause: [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) forced the regime strip into `repeat(5, 1fr)` while the strip cells had no `min-width: 0`, and the day-change line used `white-space: nowrap`. On narrower desktop widths the strip kept five compressed cards in one row, which is why the `COR1M` card could visually collide with its neighbor instead of collapsing cleanly.
- Added red-phase coverage in [web/tests/regime-strip-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-strip-responsive.test.ts) and [web/e2e/regime-strip-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-strip-responsive.spec.ts). The unit test failed on the old fixed five-column grid and nowrap day-change rule, and the Playwright test failed because the `COR1M` card remained on the same row as `VIX` at a `960px` viewport.
- Fixed the strip by converting it to an auto-fitting grid with a real card minimum width, giving cells an explicit shrink boundary, and letting change/subline text wrap instead of forcing a single unbreakable telemetry row in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css).
- Verified green with `npx vitest run web/tests/regime-strip-responsive.test.ts web/tests/regime-cor1m-live.test.ts` and `cd web && npx playwright test e2e/regime-strip-responsive.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-live-index-stream.spec.ts --config playwright.config.ts`.

## Session: Document And Ship Quote Telemetry Contract (2026-03-12)

### Dependency Graph
- T1 (Identify the operator-facing docs and the exact quote-telemetry files that belong in the ship batch) depends_on: []
- T2 (Update the docs to reflect the shared BID/MID/ASK/SPREAD contract and raw spread-dollar plus percent display) depends_on: [T1]
- T3 (Stage only the quote-telemetry code, tests, docs, and task-log files) depends_on: [T2]
- T4 (Create a scoped commit for the quote-telemetry refactor and push the current branch) depends_on: [T3]

### Checklist
- [x] T1 Identify the operator-facing docs and the exact quote-telemetry files that belong in the ship batch
- [x] T2 Update the docs to reflect the shared BID/MID/ASK/SPREAD contract and raw spread-dollar plus percent display
- [x] T3 Stage only the quote-telemetry code, tests, docs, and task-log files
- [x] T4 Create a scoped commit for the quote-telemetry refactor and push the current branch

### Review
- Updated the operator-facing terminal docs in [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) and [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md) so the shared quote ladder contract is documented as `BID`, `MID`, `ASK`, `SPREAD`, with spread rendered as raw quote width plus midpoint percent.
- The ship batch is scoped to the shared quote calculator and renderer in [web/lib/quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts) and [web/components/QuoteTelemetry.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/QuoteTelemetry.tsx), plus the three consuming surfaces in [web/components/TickerDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/TickerDetailModal.tsx), [web/components/InstrumentDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InstrumentDetailModal.tsx), and [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx).
- Locked the contract with focused unit coverage in [web/tests/quote-telemetry-wrappers.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/quote-telemetry-wrappers.test.ts), [web/tests/price-bar-quote-telemetry.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/price-bar-quote-telemetry.test.ts), [web/tests/ticker-detail-spread-notional.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/ticker-detail-spread-notional.test.ts), [web/tests/order-ticket-spread-notional.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-ticket-spread-notional.test.ts), and [web/tests/instrument-detail-spread-quantity.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/instrument-detail-spread-quantity.test.ts), plus browser coverage in [web/e2e/price-bar-quote-telemetry.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/price-bar-quote-telemetry.spec.ts), [web/e2e/order-ticket-quote-telemetry.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/order-ticket-quote-telemetry.spec.ts), and [web/e2e/modify-order-spread-telemetry.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-order-spread-telemetry.spec.ts).
- Verified green before shipping with `npx vitest run web/tests/quote-telemetry-wrappers.test.ts web/tests/price-bar-quote-telemetry.test.ts web/tests/ticker-detail-spread-notional.test.ts web/tests/order-ticket-spread-notional.test.ts web/tests/instrument-detail-spread-quantity.test.ts` and `cd web && npx playwright test e2e/price-bar-quote-telemetry.spec.ts e2e/order-ticket-quote-telemetry.spec.ts e2e/modify-order-spread-telemetry.spec.ts --config playwright.config.ts`.

## Session: Prove Regime Live Index Streaming With TDD (2026-03-12)

### Goal
Add red/green unit and browser coverage that proves `/regime` renders live websocket prices for `VIX`, `VVIX`, and `COR1M`.

### Dependency Graph
- T1 (Inspect existing live-stream coverage and record the implementation plan in `tasks/todo.md`) depends_on: []
- T2 (Add failing unit and Playwright tests that prove `/regime` replaces cached values with live websocket prices for `VIX`, `VVIX`, and `COR1M`) depends_on: [T1]
- T3 (Implement the minimal runtime fix or hardening needed to satisfy the live-stream contract) depends_on: [T2]
- T4 (Run targeted Vitest and Playwright verification against the running dev server and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect existing live-stream coverage and record the implementation plan in `tasks/todo.md`
- [x] T2 Add failing unit and Playwright tests that prove `/regime` replaces cached values with live websocket prices for `VIX`, `VVIX`, and `COR1M`
- [x] T3 Implement the minimal runtime fix or hardening needed to satisfy the live-stream contract
- [x] T4 Run targeted Vitest and Playwright verification against the running dev server and capture review notes

### Review
- Added a red/green server-side regression in [web/tests/ib-index-stream-contracts.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/ib-index-stream-contracts.test.ts) that failed against the old cold-start behavior because [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js) did not preserve typed stock/option/index contracts before the `ibConnected` gate and rebuilt restored subscriptions as stocks.
- Fixed that by adding `ensureSymbolState()` in [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js) and seeding stock, option, and index subscriptions with their IB contract as soon as the websocket subscribe message arrives. `restoreSubscriptions()` now reuses the stored contract instead of falling back to `stock()` for everything.
- Added browser regression coverage in [web/e2e/regime-live-index-streaming.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-live-index-streaming.spec.ts) and [web/e2e/regime-live-index-stream.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-live-index-stream.spec.ts). The first proves `/regime` subscribes to `VIX`, `VVIX`, and `COR1M` before emitting live websocket prices; the second proves a batched websocket payload replaces cached CRI values with live strip values and day-change rows for all three indices.
- Added targeted unit coverage in [web/tests/use-previous-close-indexes.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/use-previous-close-indexes.test.ts) and hardened [web/lib/usePreviousClose.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePreviousClose.ts) with `shouldBackfillPreviousClose(...)` so the websocket-backed regime indices (`VIX`, `VVIX`, `COR1M`) no longer trigger the stock `/api/previous-close` fallback path.
- Verified green with `npx vitest run web/tests/ib-index-stream-contracts.test.ts web/tests/use-previous-close-indexes.test.ts web/tests/regime-cor1m-live.test.ts web/tests/batched-prices.test.ts`, `cd web && npx playwright test e2e/regime-live-index-streaming.spec.ts`, and `cd web && npx playwright test e2e/regime-live-index-stream.spec.ts e2e/regime-cor1m-live-stream.spec.ts e2e/regime-vix-live-badge.spec.ts` against the running dev server.
- `cd web && npm run build` still fails on the pre-existing unrelated TypeScript error in [web/components/CtaPage.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CtaPage.tsx): the `syncStatus` union does not narrow `last_attempt_started_at`.

## Session: Change Spread Display To Raw Dollars And Percent (2026-03-12)

### Dependency Graph
- T1 (Inspect the shared quote telemetry contract and identify every component/test that still assumes notional spread dollars or bps) depends_on: []
- T2 (Record the corrected contract in `tasks/todo.md` and capture the new lesson in `tasks/lessons.md`) depends_on: [T1]
- T3 (Update unit and Playwright tests to the raw-spread-plus-percent contract and observe the red state) depends_on: [T1, T2]
- T4 (Implement the shared calculator change and keep all quote surfaces aligned through the wrappers) depends_on: [T3]
- T5 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the shared quote telemetry contract and identify every component/test that still assumes notional spread dollars or bps
- [x] T2 Record the corrected contract in `tasks/todo.md` and capture the new lesson in `tasks/lessons.md`
- [x] T3 Update unit and Playwright tests to the raw-spread-plus-percent contract and observe the red state
- [x] T4 Implement the shared calculator change and keep all quote surfaces aligned through the wrappers
- [x] T5 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Root cause: the shared quote telemetry refactor still preserved an incorrect spread contract. [web/lib/quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts) was formatting spread as notional dollars and bps, and the wrappers still carried quantity/multiplier or execution-specific spread policies that made a `3.30 x 3.40` option quote render as `$1,000.00 / 299 bps` instead of the actual `$0.10 / 2.99%`.
- Simplified the calculator contract in [web/lib/quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts) so `SPREAD` is now always raw quote width plus percentage, and simplified [web/components/QuoteTelemetry.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/QuoteTelemetry.tsx) so the wrappers are layout wrappers only, not spread-policy wrappers.
- Rewired [web/components/TickerDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/TickerDetailModal.tsx), [web/components/InstrumentDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InstrumentDetailModal.tsx), and [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) to the simplified shared wrappers so every live quote ladder now shows the same spread contract.
- The red phase failed across the full shared surface set: unit tests expected raw spread dollars and percent but still received notional dollars/bps (for example `$110.00 / 240 bps`, `$3,000.00 / 3,077 bps`, and `$0.60 / 1,538 bps`), and Playwright failed on all three browser flows before the calculator change.
- Verified green with `npx vitest run web/tests/quote-telemetry-wrappers.test.ts web/tests/price-bar-quote-telemetry.test.ts web/tests/ticker-detail-spread-notional.test.ts web/tests/order-ticket-spread-notional.test.ts web/tests/instrument-detail-spread-quantity.test.ts` and `cd web && npx playwright test e2e/price-bar-quote-telemetry.spec.ts e2e/order-ticket-quote-telemetry.spec.ts e2e/modify-order-spread-telemetry.spec.ts --config playwright.config.ts`.

## Session: Investigate IB Index Subscription Warnings (2026-03-12)

### Goal
Determine why the live IB feed now reports missing market-data subscriptions for `VIX`, `VVIX`, and `COR1M`, even though those symbols were previously streaming in the application.

### Dependency Graph
- T1 (Record the investigation scope and constraints in `tasks/todo.md` before touching code or environment) depends_on: []
- T2 (Inspect the warning source and the current IB contract/data-type requests for `VIX`, `VVIX`, and `COR1M`) depends_on: [T1]
- T3 (Review live logs and adjacent market-data flows to compare the current behavior against other index subscriptions and isolate the cause) depends_on: [T2]
- T4 (Summarize the root cause and remediation path without disturbing the loaded product services) depends_on: [T3]

### Checklist
- [x] T1 Record the investigation scope and constraints in `tasks/todo.md` before touching code or environment
- [x] T2 Inspect the warning source and the current IB contract/data-type requests for `VIX`, `VVIX`, and `COR1M`
- [x] T3 Review live logs and adjacent market-data flows to compare the current behavior against other index subscriptions and isolate the cause
- [x] T4 Summarize the root cause and remediation path without disturbing the loaded product services

### Review
- The warning string is emitted in [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js) when the shared IB error handler sees code `354` or text matching `market data is not subscribed`. `/regime` subscribes `VIX@CBOE`, `VVIX@CBOE`, and `COR1M@CBOE` from [web/components/WorkspaceShell.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx), and the websocket hook in [web/lib/usePrices.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/usePrices.ts) forwards those index contracts unchanged.
- The current live websocket request shape is correct. A direct local IB repro on Thursday, March 12, 2026 using `ib.contract.index(symbol, "USD", "CBOE")`, `reqMarketDataType(4)`, and `reqMktData(..., "233,165", ...)` returned delayed ticks for `VIX`, `VVIX`, and `COR1M` without any `354` subscription error. Repeating the same repro with tick list `"233"` also succeeded.
- That means the warning is not explained by a permanent loss of current index entitlement or a broken contract-builder call. It is more likely noisy or transient in the websocket path. The UI can also look “live” because [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) falls back to cached CRI values when websocket values are absent.
- There is separate older evidence from Wednesday, March 11, 2026 in [logs/cri-scan.err.log](/Users/joemccann/dev/apps/finance/radon/logs/cri-scan.err.log): the CRI scanner recorded `COR1M` failures including `Requested market data is not subscribed. Delayed market data is not enabled.` before falling back to Cboe/Yahoo. That is a different path from the websocket realtime server and should not be conflated with today’s direct websocket subscription repro.
- There is also a real cold-restore bug in [scripts/ib_realtime_server.js](/Users/joemccann/dev/apps/finance/radon/scripts/ib_realtime_server.js): `restoreSubscriptions()` rebuilds missing contracts as stocks when no prior state exists. A direct repro showed that bug yields IB code `200` (`No security definition has been found for the request`) for `VIX`, `VVIX`, and `COR1M`, so it is worth fixing, but it does not match the specific `no market data subscription` warning you reported.

## Session: Stop Radon Dev Servers And Playwright Processes (2026-03-12)

### Goal
Stop the currently running Radon development servers and Playwright-related processes without touching the loaded background product services.

### Dependency Graph
- T1 (Record the narrowed process-stop scope in `tasks/todo.md` and capture the correction lesson in `tasks/lessons.md`) depends_on: []
- T2 (Inspect the live process table after the interrupted prior attempt and identify only the Radon dev-server and Playwright targets) depends_on: [T1]
- T3 (Terminate the targeted Radon dev-server and Playwright processes, then verify they are gone while launch agents remain untouched) depends_on: [T2]

### Checklist
- [x] T1 Record the narrowed process-stop scope in `tasks/todo.md` and capture the correction lesson in `tasks/lessons.md`
- [x] T2 Inspect the live process table after the interrupted prior attempt and identify only the Radon dev-server and Playwright targets
- [x] T3 Terminate the targeted Radon dev-server and Playwright processes, then verify they are gone while launch agents remain untouched

### Review
- Identified the active targets after the interrupted prior attempt as the Radon web dev stack (`npm run dev:verbose:next`, `concurrently`, `next dev`, `ib_realtime_server.js`), the Radon site dev servers on ports `3335` and `3003`, the repo-scoped Playwright runner for `web/e2e/stale-option-last-price.spec.ts`, and the Playwright MCP bridge.
- Stopped those targets with targeted `pkill` and direct `kill` cleanup for one orphaned Playwright node process and one respawned Radon web dev stack tree. A final `ps` check showed no remaining Radon `next dev`, Playwright, Playwright MCP, or `ib_realtime_server.js` processes.
- Verified that the background product services were left untouched: `launchctl list` still shows `com.radon.cta-sync`, `com.radon.monitor-daemon`, and `local.ibc-gateway`.

## Session: Reduce CTA Auto Schedule To Two Post-Close Runs (2026-03-12)

### Goal
Change the CTA launch schedule so the service only runs automatically at 4:15 PM ET and 5:00 PM ET on trading days, while keeping `RunAtLoad` catch-up behavior for wake/login/reboot.

### Dependency Graph
- T1 (Inspect the current CTA schedule source, installer, tests, and docs, then record the corrected plan and lesson) depends_on: []
- T2 (Update regression coverage so the CTA schedule expects only the 4:15 PM ET and 5:00 PM ET automatic runs) depends_on: [T1]
- T3 (Implement the reduced CTA schedule in the source config and regenerate/reload the live LaunchAgent while preserving RunAtLoad catch-up semantics) depends_on: [T2]
- T4 (Update docs/status text and verify with tests plus live plist/launchctl inspection) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current CTA schedule source, installer, tests, and docs, then record the corrected plan and lesson
- [x] T2 Update regression coverage so the CTA schedule expects only the 4:15 PM ET and 5:00 PM ET automatic runs
- [x] T3 Implement the reduced CTA schedule in the source config and regenerate/reload the live LaunchAgent while preserving RunAtLoad catch-up semantics
- [x] T4 Update docs/status text and verify with tests plus live plist/launchctl inspection

### Review
- Reduced the CTA schedule source in [scripts/utils/cta_sync.py](/Users/joemccann/dev/apps/finance/radon/scripts/utils/cta_sync.py) to two ET runs only: `4:15 PM` and `5:00 PM`. `RunAtLoad` remains enabled, so reboot/login/wake still provides catch-up for a missed closed-session refresh.
- Updated the installer/operator docs in [scripts/setup_cta_sync_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_cta_sync_service.sh), [scripts/run_cta_sync.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cta_sync.sh), and [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) so they describe the exact two-run schedule instead of the old multi-retry ladder.
- Fixed a small status-reporting bug in [scripts/setup_cta_sync_service.sh](/Users/joemccann/dev/apps/finance/radon/scripts/setup_cta_sync_service.sh) so `status` now reports the newest daily `cta_YYYY-MM-DD.json` cache instead of accidentally matching `cta_sync_status.json`.
- Reloaded the live user LaunchAgent with `bash scripts/setup_cta_sync_service.sh install`. The generated plist at [com.radon.cta-sync.plist](/Users/joemccann/Library/LaunchAgents/com.radon.cta-sync.plist) now shows only `13:15` and `14:00` Pacific entries for weekdays, which correspond to `4:15 PM ET` and `5:00 PM ET`, and `launchctl print gui/501/com.radon.cta-sync` reflects the same schedule with `last exit code = 0`.
- Verification passed with `pytest -q scripts/tests/test_cta_sync_service.py`, `bash -n scripts/setup_cta_sync_service.sh scripts/run_cta_sync.sh`, `bash scripts/setup_cta_sync_service.sh install`, `bash scripts/setup_cta_sync_service.sh status`, `plutil -p ~/Library/LaunchAgents/com.radon.cta-sync.plist`, and `launchctl print gui/$(id -u)/com.radon.cta-sync`.

## Session: Refactor Quote Telemetry To Shared Renderer And Wrappers (2026-03-12)

### Dependency Graph
- T1 (Inventory existing quote renderers, calculators, and wrappers across ticker, instrument, and modify-order surfaces) depends_on: []
- T2 (Record the refactor plan with dependency graph in `tasks/todo.md` before implementation) depends_on: [T1]
- T3 (Write or update unit and Playwright tests to pin the shared renderer/calculator contracts and observe red where needed) depends_on: [T1, T2]
- T4 (Implement the refactor to a shared quote calculator, shared renderer, and surface-specific wrappers without changing validated semantics) depends_on: [T3]
- T5 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inventory existing quote renderers, calculators, and wrappers across ticker, instrument, and modify-order surfaces
- [x] T2 Record the refactor plan with dependency graph in `tasks/todo.md` before implementation
- [x] T3 Write or update unit and Playwright tests to pin the shared renderer/calculator contracts and observe red where needed
- [x] T4 Implement the refactor to a shared quote calculator, shared renderer, and surface-specific wrappers without changing validated semantics
- [x] T5 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Consolidated quote math into [web/lib/quoteTelemetry.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/quoteTelemetry.ts), which is now the single calculator source for bid/mid/ask extraction, spread formatting, execution-spread formatting, and the full quote telemetry view model.
- Consolidated rendering into [web/components/QuoteTelemetry.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/QuoteTelemetry.tsx), which now owns the renderer variants and the thin wrappers: `TickerQuoteTelemetry`, `InstrumentOrderQuoteTelemetry`, and `ModifyOrderQuoteTelemetry`.
- Rewired [web/components/TickerDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/TickerDetailModal.tsx), [web/components/InstrumentDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InstrumentDetailModal.tsx), and [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) to consume those wrappers instead of owning duplicated quote-row rendering and quote math.
- The red phase came from the new wrapper tests importing a non-existent shared quote component API: `npx vitest run web/tests/quote-telemetry-wrappers.test.ts web/tests/price-bar-quote-telemetry.test.ts` failed with `Cannot find module '../components/QuoteTelemetry'` before implementation.
- Added wrapper-level regression coverage in [web/tests/quote-telemetry-wrappers.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/quote-telemetry-wrappers.test.ts) and kept the existing modal/browser coverage for the shared ticker modal, instrument order modal, and modify-order modal.
- Verified green with `npx vitest run web/tests/quote-telemetry-wrappers.test.ts web/tests/price-bar-quote-telemetry.test.ts web/tests/ticker-detail-spread-notional.test.ts web/tests/order-ticket-spread-notional.test.ts web/tests/instrument-detail-spread-quantity.test.ts` and `cd web && npx playwright test e2e/price-bar-quote-telemetry.spec.ts e2e/order-ticket-quote-telemetry.spec.ts e2e/modify-order-spread-telemetry.spec.ts --config playwright.config.ts`.

## Session: Load And Verify CTA Launch Agent (2026-03-12)

### Goal
Bootstrap the installed `com.radon.cta-sync` launch agent into the live user `launchd` domain and verify that it is actually loaded, triggerable, and writing the expected logs.

### Dependency Graph
- T1 (Inspect the installed CTA launch agent state and record the load/verification plan) depends_on: []
- T2 (Bootstrap or enable `com.radon.cta-sync` in the live `gui/501` launchd domain and trigger a verification run) depends_on: [T1]
- T3 (Verify the live launchd record, recent logs, and resulting CTA sync status, then document the outcome) depends_on: [T2]

### Checklist
- [x] T1 Inspect the installed CTA launch agent state and record the load/verification plan
- [x] T2 Bootstrap or enable `com.radon.cta-sync` in the live `gui/501` launchd domain and trigger a verification run
- [x] T3 Verify the live launchd record, recent logs, and resulting CTA sync status, then document the outcome

### Review
- Bootstrapped the installed user agent with `launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.radon.cta-sync.plist` and then forced an immediate verification run with `launchctl kickstart -k gui/501/com.radon.cta-sync`.
- Verified the live launchd record with `launchctl print gui/501/com.radon.cta-sync`. The service is now loaded in the user GUI domain, has the expected `ProgramArguments` pointing at [scripts/run_cta_sync.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_cta_sync.sh), shows the calendar triggers, and reports `runs = 2` with `last exit code = 0`.
- Verified the launchd registry with `launchctl list`, which now includes `com.radon.cta-sync` alongside `local.ibc-gateway` and `com.radon.monitor-daemon`.
- Verified runtime behavior from [logs/cta-sync.out.log](/Users/joemccann/dev/apps/finance/radon/logs/cta-sync.out.log), [logs/cta-sync.err.log](/Users/joemccann/dev/apps/finance/radon/logs/cta-sync.err.log), [cta-sync-latest.json](/Users/joemccann/dev/apps/finance/radon/data/menthorq_cache/health/cta-sync-latest.json), and [cta-sync.json](/Users/joemccann/dev/apps/finance/radon/data/service_health/cta-sync.json). The current run is healthy and skipped correctly because `cta_2026-03-11.json` is already present; the older March 11 auth failure remains visible only as historical log output.

## Session: Fix Modify-Order Spread Dollar Basis (2026-03-12)

### Dependency Graph
- T1 (Inspect the modify-order modal quote math and identify the intended spread basis from the current UI path) depends_on: []
- T2 (Record the corrected plan and user-correction lesson in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Update unit and Playwright tests to the corrected modify-order spread contract and observe the red state) depends_on: [T1, T2]
- T4 (Implement the minimal fix in `ModifyOrderModal` without regressing the shared ticker modal or instrument ticket behavior) depends_on: [T3]
- T5 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the modify-order modal quote math and identify the intended spread basis from the current UI path
- [x] T2 Record the corrected plan and user-correction lesson in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Update unit and Playwright tests to the corrected modify-order spread contract and observe the red state
- [x] T4 Implement the minimal fix in `ModifyOrderModal` without regressing the shared ticker modal or instrument ticket behavior
- [x] T5 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Root cause: [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) was reusing order-sized spread math based on `order.totalQuantity * 100`, which made a quote-only telemetry row display `$4,000.00` for a `12.80 x 14.40` option quote. The operator-facing surface does not show quantity in that block, so the number was misleading.
- Added a modify-order-specific formatter in [web/lib/positionUtils.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/positionUtils.ts) that reports half-spread execution friction in displayed premium dollars and midpoint bps, and wired only [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) to use it.
- Locked the correction with unit coverage in [web/tests/order-ticket-spread-notional.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/order-ticket-spread-notional.test.ts) and browser coverage in [web/e2e/modify-order-spread-telemetry.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/modify-order-spread-telemetry.spec.ts). The red phase failed with `$3,000.00 / 3,077 bps` in unit tests and `$4,000.00 / 1,176 bps` in the browser before the fix.
- Verified green with `npx vitest run web/tests/order-ticket-spread-notional.test.ts web/tests/ticker-detail-spread-notional.test.ts web/tests/instrument-detail-spread-quantity.test.ts web/tests/price-bar-quote-telemetry.test.ts` and `cd web && npx playwright test e2e/modify-order-spread-telemetry.spec.ts e2e/order-ticket-quote-telemetry.spec.ts e2e/price-bar-quote-telemetry.spec.ts --config playwright.config.ts`.

## Session: Separate Quote-Level And Order-Level Spread Notional (2026-03-12)

### Dependency Graph
- T1 (Inspect the shared ticker-detail quote path and confirm which surfaces should use quote-level vs quantity-sized spread notional) depends_on: []
- T2 (Record the corrected plan and user-correction lesson in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Update regression tests to encode quote-level spread on the shared ticker modal and quantity-sized spread on explicit order surfaces, then observe red) depends_on: [T1, T2]
- T4 (Implement the minimal fix so `TickerDetailModal` uses quote-level spread notional while `InstrumentDetailModal` and `ModifyOrderModal` stay quantity-aware) depends_on: [T3]
- T5 (Run targeted Vitest and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the shared ticker-detail quote path and confirm which surfaces should use quote-level vs quantity-sized spread notional
- [x] T2 Record the corrected plan and user-correction lesson in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Update regression tests to encode quote-level spread on the shared ticker modal and quantity-sized spread on explicit order surfaces, then observe red
- [x] T4 Implement the minimal fix so `TickerDetailModal` uses quote-level spread notional while `InstrumentDetailModal` and `ModifyOrderModal` stay quantity-aware
- [x] T5 Run targeted Vitest and Playwright verification, then capture review notes

### Review
- Root cause: the shared top quote bar in [web/components/TickerDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/TickerDetailModal.tsx) was multiplying option spread width by `position.contracts`, even though that bar is shared across `Company`, `Position`, and `Order` tabs and does not own an explicit order quantity. That made general quote telemetry look like order-sized friction.
- Kept quantity-aware spread notional on the true order-sized surfaces: [web/components/InstrumentDetailModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InstrumentDetailModal.tsx) still uses the displayed leg quantity, and [web/components/ModifyOrderModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ModifyOrderModal.tsx) still uses `order.totalQuantity`.
- Added a focused regression in [web/tests/ticker-detail-spread-notional.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/ticker-detail-spread-notional.test.ts) plus browser coverage in [web/e2e/price-bar-quote-telemetry.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/price-bar-quote-telemetry.spec.ts) to lock the shared ticker modal to quote-level spread notional. The pre-fix red phase failed with `$2,200.00 / 240 bps` instead of `$110.00 / 240 bps`.
- Verified green with `npx vitest run web/tests/ticker-detail-spread-notional.test.ts web/tests/order-ticket-spread-notional.test.ts web/tests/instrument-detail-spread-quantity.test.ts web/tests/price-bar-quote-telemetry.test.ts` and `cd web && npx playwright test e2e/price-bar-quote-telemetry.spec.ts e2e/order-ticket-quote-telemetry.spec.ts --config playwright.config.ts`.

## Session: Document And Ship Site Surface Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the current `/site` fix worktree and identify the minimal doc touchpoints that should be updated before shipping) depends_on: []
- T2 (Update the relevant docs and record the ship plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Stage only the `/site` bug-fix, doc, and task-log files for this batch) depends_on: [T2]
- T4 (Create a scoped commit for the standalone-site surface fix and verification updates) depends_on: [T3]
- T5 (Push the current branch and capture the shipping review note) depends_on: [T4]

### Checklist
- [x] T1 Inspect the current `/site` fix worktree and identify the minimal doc touchpoints that should be updated before shipping
- [x] T2 Update the relevant docs and record the ship plan in `tasks/todo.md`
- [x] T3 Stage only the `/site` bug-fix, doc, and task-log files for this batch
- [x] T4 Create a scoped commit for the standalone-site surface fix and verification updates
- [x] T5 Push the current branch and capture the shipping review note

### Review
- Shipped only the standalone-site surface fix batch: the compact preview-card metric treatment, the restored header theme toggle, the new browser regression for the surface-preview divider spacing, the small branding-spec lint cleanup, the site verification docs update, and the task log / lesson updates.
- Commit and push completed on `main` after the green standalone-site verification set: `cd web && npx playwright test branding.spec.ts theme-toggle.spec.ts surface-preview.spec.ts --config playwright.site.config.ts`, `cd site && npm run lint`, and `cd site && NEXT_DIST_DIR=.next-build npm run build`.

## Session: Site Surface Metric Overflow Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the `/site` surface-preview section and identify why the performance metric value bleeds into the adjacent tile at desktop width) depends_on: []
- T2 (Record the bug-fix plan in `tasks/todo.md` and capture the user-correction lesson in `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for the overflowing metric tile on the standalone site) depends_on: [T1, T2]
- T4 (Implement the minimal layout fix in the shared site metric/tile components without regressing the existing brand treatment) depends_on: [T3]
- T5 (Run targeted verification, update review notes, and confirm the preview cards stay contained on desktop widths) depends_on: [T4]

### Checklist
- [x] T1 Inspect the `/site` surface-preview section and identify why the performance metric value bleeds into the adjacent tile at desktop width
- [x] T2 Record the bug-fix plan in `tasks/todo.md` and capture the user-correction lesson in `tasks/lessons.md`
- [x] T3 Add failing regression coverage for the overflowing metric tile on the standalone site
- [x] T4 Implement the minimal layout fix in the shared site metric/tile components without regressing the existing brand treatment
- [x] T5 Run targeted verification, update review notes, and confirm the preview cards stay contained on desktop widths

### Review
- Root cause: the `/site` surface-preview cards render split metric tiles through [site/components/organisms/SurfacePanelStack.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/organisms/SurfacePanelStack.tsx), but the shared mono metric in [site/components/atoms/MonoMetric.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/atoms/MonoMetric.tsx) used the same large value treatment as wider hero cards, leaving the `Institutional` value visually too tight to the divider in the narrower two-column tile layout.
- Added a failing browser regression in [site/e2e/surface-preview.spec.ts](/Users/joemccann/dev/apps/finance/radon/site/e2e/surface-preview.spec.ts) that measures the rendered value against the adjacent tile at desktop width; the original rendering failed the divider-gap expectation before the UI patch.
- Implemented the fix by introducing a compact mono-metric treatment in [site/components/atoms/MonoMetric.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/atoms/MonoMetric.tsx) and applying it only to the preview-card metric grid in [site/components/organisms/SurfacePanelStack.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/organisms/SurfacePanelStack.tsx), preserving the larger hero-panel metric treatment while giving split tiles enough breathing room.
- Verification also exposed that the existing site theme-toggle coverage was broken because [site/components/sections/HeaderShell.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/sections/HeaderShell.tsx) no longer mounted the toggle. Restored the header toggle and cleaned a pre-existing lint issue in [site/e2e/branding.spec.ts](/Users/joemccann/dev/apps/finance/radon/site/e2e/branding.spec.ts) so the focused standalone-site checks are green again.
- Verified with `cd web && npx playwright test surface-preview.spec.ts --config playwright.site.config.ts`, `cd web && npx playwright test branding.spec.ts theme-toggle.spec.ts --config playwright.site.config.ts`, `cd site && npm run lint`, and `cd site && NEXT_DIST_DIR=.next-build npm run build`.

## Session: Commit Residual Chart-System Fixes (2026-03-11)

### Dependency Graph
- T1 (Inspect the current branch, worktree, and diff so the commit only includes the residual chart-system follow-up files) depends_on: []
- T2 (Record the commit/push plan in `tasks/todo.md` before staging) depends_on: [T1]
- T3 (Stage the residual chart-system files and create a scoped commit with the verified runtime and OG routing fixes) depends_on: [T2]
- T4 (Push the current branch to origin and capture the resulting review note) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current branch, worktree, and diff so the commit only includes the residual chart-system follow-up files
- [x] T2 Record the commit/push plan in `tasks/todo.md` before staging
- [x] T3 Stage the residual chart-system files and create a scoped commit with the verified runtime and OG routing fixes
- [x] T4 Push the current branch to origin and capture the resulting review note

### Review
- Staged only the residual chart-system follow-up files: the shared shell metadata update, relationship-view primitive convergence, MenthorQ OG family routing, focused Vitest/Playwright regressions, and the task log updates.
- Commit and push completed on `main` after the previously green verification set: `npx vitest run web/tests/chart-runtime-adoption.test.ts web/tests/price-chart-shell.test.ts web/tests/menthorq-og-route-contract.test.ts web/tests/chart-system.test.ts web/tests/og-theme-contract.test.ts`, `cd web && npx playwright test e2e/price-chart-theme.spec.ts e2e/regime-relationship-view.spec.ts`, and `cd web && npm run build`.

## Session: Chart System Residual Convergence (2026-03-11)

### Dependency Graph
- T1 (Inspect the remaining chart-system residuals in the live-trace modal chart, relationship view shell, and MenthorQ OG route so the follow-up work stays scoped to the declared gaps) depends_on: []
- T2 (Record the residual-convergence plan and user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for live-trace shell metadata, relationship-view shared shell usage, and MenthorQ renderer-family selection) depends_on: [T1, T2]
- T4 (Implement the runtime residual fixes for `PriceChart` and `RegimeRelationshipView` using the shared chart primitives with minimal visual regression) depends_on: [T3]
- T5 (Implement MenthorQ OG family-specific renderer routing for analytical time-series, distribution-bar, and matrix-heatmap outputs) depends_on: [T3]
- T6 (Run targeted verification, update review notes, and confirm the residual chart-system gaps are closed) depends_on: [T4, T5]

### Checklist
- [x] T1 Inspect the remaining chart-system residuals in the live-trace modal chart, relationship view shell, and MenthorQ OG route so the follow-up work stays scoped to the declared gaps
- [x] T2 Record the residual-convergence plan and user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for live-trace shell metadata, relationship-view shared shell usage, and MenthorQ renderer-family selection
- [x] T4 Implement the runtime residual fixes for `PriceChart` and `RegimeRelationshipView` using the shared chart primitives with minimal visual regression
- [x] T5 Implement MenthorQ OG family-specific renderer routing for analytical time-series, distribution-bar, and matrix-heatmap outputs
- [x] T6 Run targeted verification, update review notes, and confirm the residual chart-system gaps are closed

### Review
- Runtime slice completed: [web/components/charts/ChartPanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/charts/ChartPanel.tsx) now emits chart-family and renderer metadata on the chart shell root, and [web/components/PriceChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PriceChart.tsx) now adopts that shell as a `live-trace` surface so the modal chart participates in the shared chart-system contract instead of bypassing it.
- Updated [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx) and [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so the relationship view keeps the shared `ChartPanel` shell while the z-score legend uses the shared `ChartLegend` primitive without the legacy standalone legend styling hook.
- Added direct runtime regression coverage in [web/tests/price-chart-shell.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/price-chart-shell.test.ts), [web/tests/chart-runtime-adoption.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/chart-runtime-adoption.test.ts), [web/e2e/price-chart-theme.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/price-chart-theme.spec.ts), and [web/e2e/regime-relationship-view.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-relationship-view.spec.ts); targeted Vitest and Playwright verification passed for the runtime slice.
- Downstream slice completed: [web/tests/menthorq-og-route-contract.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/menthorq-og-route-contract.test.ts) now exercises the MenthorQ OG route as code instead of string-matching source, covering analytical time-series, distribution-bar, matrix-heatmap, and unsupported-shape fallback selection.
- Updated [web/app/api/menthorq/[command]/image/route.tsx](/Users/joemccann/dev/apps/finance/radon/web/app/api/menthorq/[command]/image/route.tsx) to replace the generic analytical-only fallback with explicit family routing: command hints now prefer `intraday` and `cryptos_technical` as `analytical-time-series`, `vol`/`eod`/`futures`/`cryptos_options` as `distribution-bar`, and `forex` as `matrix-heatmap`, while unsupported payloads keep the command-family badge and render a clear fallback message instead of a misleading line chart.
- Final targeted verification passed with `npx vitest run web/tests/chart-runtime-adoption.test.ts web/tests/price-chart-shell.test.ts web/tests/menthorq-og-route-contract.test.ts web/tests/chart-system.test.ts web/tests/og-theme-contract.test.ts`, `cd web && npx playwright test e2e/price-chart-theme.spec.ts e2e/regime-relationship-view.spec.ts`, and `cd web && npm run build`; the only remaining build note is the pre-existing Next `metadataBase` warning, and the residual chart-system convergence session is now closed.

## Session: Regime Relationship State Tooltips (2026-03-11)

### Dependency Graph
- T1 (Inspect the current RVOL/COR1M relationship quadrant UI, shared tooltip component, and existing tooltip test patterns) depends_on: []
- T2 (Record the implementation plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing regression coverage for a four-state tooltip key in the relationship view) depends_on: [T1, T2]
- T4 (Implement info-bubble triggers for all four relationship states in the `/regime` UI without disturbing the existing quadrant plot) depends_on: [T3]
- T5 (Run targeted verification and capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the current RVOL/COR1M relationship quadrant UI, shared tooltip component, and existing tooltip test patterns
- [x] T2 Record the implementation plan in `tasks/todo.md`
- [x] T3 Add failing regression coverage for a four-state tooltip key in the relationship view
- [x] T4 Implement info-bubble triggers for all four relationship states in the `/regime` UI without disturbing the existing quadrant plot
- [x] T5 Run targeted verification and capture review notes

### Review
- Added shared state-definition copy in [web/lib/regimeRelationships.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/regimeRelationships.ts) so the four relationship states now have one canonical tooltip definition source.
- Updated [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx) to render a `STATE KEY` under the quadrant chart with one `InfoTooltip` trigger per state, while preserving the existing quadrant visualization and current-state summary.
- Extended [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) with responsive styling for the new state-key rows so the tooltip affordances stay aligned with the existing instrument-panel system on desktop and mobile.
- Added regression coverage in [web/tests/regime-relationship-tooltips.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-relationship-tooltips.test.ts) and browser coverage in [web/e2e/regime-relationship-view.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-relationship-view.spec.ts).
- Verified `npx vitest run web/tests/regime-relationship-tooltips.test.ts web/tests/regime-relationship.test.ts` and `cd web && npx playwright test e2e/regime-relationship-view.spec.ts`.

## Session: Regime Relationship State Docs (2026-03-11)

### Dependency Graph
- T1 (Inspect README and strategy/docs surfaces that describe `/regime`, CRI, and the new RVOL/COR1M relationship states) depends_on: []
- T2 (Record the documentation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Update README and the relevant markdown docs with exact definitions for the four relationship states and the rolling-mean classification rule) depends_on: [T2]
- T4 (Verify the updated documentation against the current implementation and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect README and strategy/docs surfaces that describe `/regime`, CRI, and the new RVOL/COR1M relationship states
- [x] T2 Record the documentation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Update README and the relevant markdown docs with exact definitions for the four relationship states and the rolling-mean classification rule
- [x] T4 Verify the updated documentation against the current implementation and capture review notes

### Review
- Added operator-facing definitions for all four relationship states in [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), alongside the `/regime` terminal capabilities section, so the meaning of `FRAGILE CALM`, `SYSTEMIC PANIC`, `STOCK PICKER'S MARKET`, and `GOLDILOCKS` is visible without reading the code.
- Added the exact implementation rule to the CRI strategy spec in [docs/strategies.md](/Users/joemccann/dev/apps/finance/radon/docs/strategies.md): the relationship view classifies the latest RVOL/COR1M point against the rolling 20-session means, not against fixed absolute cutoffs.
- Verified the docs against the current implementation in [web/lib/regimeRelationships.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/regimeRelationships.ts), including the live-latest-point override behavior used by `/regime`.

## Session: Chart System Roadmap Steps 1-4 (2026-03-11)

### Dependency Graph
- T1 (Inspect the current chart surfaces, audit roadmap, and available cross-language primitive hooks so a shared chart-system contract can drive runtime and downstream outputs) depends_on: []
- T2 (Record the step-1-through-4 execution plan in `tasks/todo.md`, including the shared chart-spec artifacts and validation targets) depends_on: [T1]
- T3 (Implement the shared chart spec, runtime chart shell/primitives, and tokenized chart helpers in `web/`) depends_on: [T1, T2]
- T4 (Converge OG/report surfaces and sanctioned renderer documentation onto the new chart-system rules) depends_on: [T3]
- T5 (Add/update regression coverage where practical, run targeted verification, and capture review notes) depends_on: [T3, T4]

### Checklist
- [x] T1 Inspect the current chart surfaces, audit roadmap, and available cross-language primitive hooks so a shared chart-system contract can drive runtime and downstream outputs
- [x] T2 Record the step-1-through-4 execution plan in `tasks/todo.md`, including the shared chart-spec artifacts and validation targets
- [x] T3 Implement the shared chart spec, runtime chart shell/primitives, and tokenized chart helpers in `web/`
- [x] T4 Converge OG/report surfaces and sanctioned renderer documentation onto the new chart-system rules
- [x] T5 Add/update regression coverage where practical, run targeted verification, and capture review notes

### Review
- Published the shared chart contract in [web/lib/chart-system-spec.json](/Users/joemccann/dev/apps/finance/radon/web/lib/chart-system-spec.json), exposed runtime helpers in [web/lib/chartSystem.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/chartSystem.ts), and documented the sanctioned families/renderers in [docs/chart-system.md](/Users/joemccann/dev/apps/finance/radon/docs/chart-system.md).
- Extracted shared runtime chart primitives in [web/components/charts/ChartPanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/charts/ChartPanel.tsx) and [web/components/charts/ChartLegend.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/charts/ChartLegend.tsx), then adopted them in [web/components/PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx) and [web/components/CriHistoryChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CriHistoryChart.tsx), while routing runtime series colors through semantic roles in [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) and [web/components/PriceChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PriceChart.tsx).
- Converged downstream surfaces on the same contract by wiring [web/lib/og-theme.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/og-theme.ts), restoring and aligning [web/lib/og-charts.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/og-charts.tsx), tightening [web/app/api/menthorq/[command]/image/route.tsx](/Users/joemccann/dev/apps/finance/radon/web/app/api/menthorq/[command]/image/route.tsx), and keeping [scripts/performance_explainer_report.py](/Users/joemccann/dev/apps/finance/radon/scripts/performance_explainer_report.py) on the shared family/renderer/semantic-role rules.
- Added regression coverage in [web/tests/chart-system.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/chart-system.test.ts), [web/tests/chart-runtime-adoption.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/chart-runtime-adoption.test.ts), [web/tests/og-theme-contract.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/og-theme-contract.test.ts), [web/tests/og-chart-contract.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/og-chart-contract.test.ts), [web/tests/og-chart-system.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/og-chart-system.test.ts), [web/tests/menthorq-og-route-contract.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/menthorq-og-route-contract.test.ts), and [scripts/tests/test_performance_explainer_report.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_performance_explainer_report.py).
- Verified with `npx vitest run web/tests/chart-system.test.ts web/tests/chart-runtime-adoption.test.ts web/tests/og-chart-contract.test.ts web/tests/menthorq-og-route-contract.test.ts web/tests/og-theme-contract.test.ts web/tests/og-chart-system.test.ts web/tests/performance-chart-model.test.ts web/tests/performance-chart-theme.test.ts web/tests/performance-chart-axes.test.ts web/tests/regime-history-responsive.test.ts web/tests/regime-history-tooltip.test.ts web/tests/price-chart-theme.test.ts`, `pytest scripts/tests/test_performance_explainer_report.py -q`, `python3 -m py_compile scripts/performance_explainer_report.py`, `python3 scripts/performance_explainer_report.py --no-open --output /tmp/performance-page-explainer-chart-audit.html`, `cd web && npx playwright test e2e/performance-page.spec.ts e2e/performance-chart-axes.spec.ts e2e/performance-chart-theme.spec.ts e2e/regime-history-responsive.spec.ts e2e/regime-history-tooltip.spec.ts`, and `cd web && npm run build`.
- Residual gaps intentionally left for later follow-up: [web/components/PriceChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PriceChart.tsx) still does not emit `live-trace` shell metadata because it renders inside other panels; [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx) still uses its own section/legend system; and the generic MenthorQ OG route still falls back to an analytical time-series renderer for arbitrary datasets until command-specific `distribution-bar` and `matrix-heatmap` renderers are added.

## Session: Regime Relationship Analytics View (2026-03-11)

### Dependency Graph
- T1 (Inspect the `/regime` history data shape, current chart contracts, and brand constraints so relationship-first analytics can be added without removing the raw history view) depends_on: []
- T2 (Record the relationship-analytics plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for the new RVOL/COR1M relationship helpers and browser-visible analytics panels) depends_on: [T1, T2]
- T4 (Implement additive relationship visuals for spread, quadrant, and normalized divergence with minimal impact to the existing charts) depends_on: [T3]
- T5 (Run targeted verification, update review notes, and confirm the new panels are green without regressing the raw history charts) depends_on: [T4]

### Checklist
- [x] T1 Inspect the `/regime` history data shape, current chart contracts, and brand constraints so relationship-first analytics can be added without removing the raw history view
- [x] T2 Record the relationship-analytics plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for the new RVOL/COR1M relationship helpers and browser-visible analytics panels
- [x] T4 Implement additive relationship visuals for spread, quadrant, and normalized divergence with minimal impact to the existing charts
- [x] T5 Run targeted verification, update review notes, and confirm the new panels are green without regressing the raw history charts

### Review
- Added a dedicated relationship analytics helper in [web/lib/regimeRelationships.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/regimeRelationships.ts) so spread, quadrant, and normalized divergence all derive from the same filtered RVOL/COR1M series, with live overrides merged into the latest session when present.
- Wired an additive relationship section into [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) and implemented the three analytics panels in [web/components/RegimeRelationshipView.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimeRelationshipView.tsx), preserving the original raw history charts and their existing test hooks.
- Extended the styling in [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) to keep the new panels on the current instrument-panel system and added tooltip copy in [web/lib/sectionTooltips.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/sectionTooltips.ts).
- Verified the relationship helper with `npx vitest run web/tests/regime-relationship-model.test.ts`.
- Verified the browser behavior with `cd web && npx playwright test e2e/regime-relationship-view.spec.ts e2e/regime-rvol-history.spec.ts`, proving the new panels render while the legacy raw RVOL history chart still keeps its 20-point contract.
- Verified the web app compiles with `cd web && npm run build`. The first Turbopack attempt left a stale `.next` temp/lock artifact, but the rerun after clearing the lock completed successfully.

## Session: Repo-Wide Chart Cohesion Audit (2026-03-11)

### Dependency Graph
- T1 (Inventory every non-sparkline chart across runtime app code and adjacent repo surfaces, separating live product charts from mockups, OG assets, and report-only visuals) depends_on: []
- T2 (Record the chart-audit plan in `tasks/todo.md`, including the target HTML artifact path and review criteria) depends_on: [T1]
- T3 (Use subagents to compare chart implementations, shared behaviors, divergence points, and brand alignment across runtime/design surfaces) depends_on: [T1, T2]
- T4 (Generate a cohesive-charting HTML report with findings, taxonomy, and refactor recommendations grounded in the brand system) depends_on: [T3]
- T5 (Open the HTML report locally, capture validation notes, and summarize the highest-priority conclusions) depends_on: [T4]

### Checklist
- [x] T1 Inventory every non-sparkline chart across runtime app code and adjacent repo surfaces, separating live product charts from mockups, OG assets, and report-only visuals
- [x] T2 Record the chart-audit plan in `tasks/todo.md`, including the target HTML artifact path and review criteria
- [x] T3 Use subagents to compare chart implementations, shared behaviors, divergence points, and brand alignment across runtime/design surfaces
- [x] T4 Generate a cohesive-charting HTML report with findings, taxonomy, and refactor recommendations grounded in the brand system
- [x] T5 Open the HTML report locally, capture validation notes, and summarize the highest-priority conclusions

### Report Artifact
- Target: `reports/chart-audit-2026-03-11.html`

### Review
- Inventoried the runtime product chart surfaces under `web/` as four families instead of one: live scrub price chart ([web/components/PriceChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PriceChart.tsx)), analytical time-series ([web/components/PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx), [web/components/CriHistoryChart.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CriHistoryChart.tsx)), distribution/gauge visuals ([web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx), [web/components/CtaPage.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CtaPage.tsx), [web/components/ticker-detail/RatingsTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/RatingsTab.tsx)), and matrix/heatmap-style comparative views ([web/components/ticker-detail/SeasonalityTab.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/ticker-detail/SeasonalityTab.tsx), [web/components/CtaTables.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/CtaTables.tsx)).
- Audited secondary repo references that should influence the eventual chart system without defining runtime behavior: [web/lib/og-charts.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/og-charts.tsx), [web/app/api/menthorq/[command]/image/route.tsx](/Users/joemccann/dev/apps/finance/radon/web/app/api/menthorq/[command]/image/route.tsx), [brand/radon-terminal-mockup.html](/Users/joemccann/dev/apps/finance/radon/brand/radon-terminal-mockup.html), [brand/radon-component-kit.html](/Users/joemccann/dev/apps/finance/radon/brand/radon-component-kit.html), [scripts/performance_explainer_report.py](/Users/joemccann/dev/apps/finance/radon/scripts/performance_explainer_report.py), and [.pi/skills/html-report/THEME.md](/Users/joemccann/dev/apps/finance/radon/.pi/skills/html-report/THEME.md).
- Generated the report at [reports/chart-audit-2026-03-11.html](/Users/joemccann/dev/apps/finance/radon/reports/chart-audit-2026-03-11.html). The report documents the current chart taxonomy, same-vs-different analysis, brand-alignment gaps, and a four-step refactor roadmap centered on a shared chart spec rather than a forced single-library rewrite.
- Highest-priority conclusions captured in the report: there is no shared chart shell yet; runtime charts currently span canvas, D3 SVG, custom SVG, and CSS/flex/grid implementations; theme-token adoption is strongest on `/performance` and weakest in `CriHistoryChart` and the modal bar/grid views; and the repo already contains one viable cross-surface chart grammar in [web/lib/og-charts.tsx](/Users/joemccann/dev/apps/finance/radon/web/lib/og-charts.tsx), but it is isolated to OG rendering instead of driving runtime and report visuals.
- Tried to open the report with `open` first, but this shell has no working default HTML handler and the installed browser bundles are stubs. Fell back to Quick Look via `qlmanage -p reports/chart-audit-2026-03-11.html`.

## Session: Regime History Charts Responsive Stack (2026-03-11)

### Dependency Graph
- T1 (Inspect the `/regime` history-chart layout path, current chart container markup, and responsive CSS gaps) depends_on: []
- T2 (Record the responsive-stack fix plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing regression coverage for narrow-viewport chart stacking in unit tests and Playwright) depends_on: [T1, T2]
- T4 (Replace the inline two-column history grid with responsive CSS that stacks the charts vertically at narrow widths) depends_on: [T3]
- T5 (Run targeted verification and capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the `/regime` history-chart layout path, current chart container markup, and responsive CSS gaps
- [x] T2 Record the responsive-stack fix plan in `tasks/todo.md`
- [x] T3 Add failing regression coverage for narrow-viewport chart stacking in unit tests and Playwright
- [x] T4 Replace the inline two-column history grid with responsive CSS that stacks the charts vertically at narrow widths
- [x] T5 Run targeted verification and capture review notes

### Review
- Replaced the inline history-chart grid in [RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) with a named `regime-history-grid` container so the layout is controlled by CSS instead of a fixed in-component `1fr 1fr` declaration.
- Added responsive rules in [globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) that keep the two charts side by side on wide screens and collapse them to a single stacked column below `960px`, preserving the existing 16px gap and top alignment.
- Locked the contract with [regime-history-responsive.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-history-responsive.test.ts) and [regime-history-responsive.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-history-responsive.spec.ts), then reran the adjacent history tooltip coverage in [regime-history-tooltip.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-history-tooltip.test.ts) and [regime-history-tooltip.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-history-tooltip.spec.ts).
- Verified `npx vitest run web/tests/regime-history-responsive.test.ts web/tests/regime-history-tooltip.test.ts` and `cd web && npx playwright test e2e/regime-history-responsive.spec.ts e2e/regime-history-tooltip.spec.ts`.

## Session: Performance Chart Axes Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the `/performance` chart implementation, existing axis styling patterns, and relevant test harnesses) depends_on: []
- T2 (Record the bug-fix plan and user correction lesson in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for visible performance-chart axes in unit tests and Playwright) depends_on: [T1, T2]
- T4 (Implement YTD chart axes and labels with minimal layout impact) depends_on: [T3]
- T5 (Run targeted verification, update review notes, and confirm green state) depends_on: [T4]

### Checklist
- [x] T1 Inspect the `/performance` chart implementation, existing axis styling patterns, and relevant test harnesses
- [x] T2 Record the bug-fix plan and user correction lesson in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for visible performance-chart axes in unit tests and Playwright
- [x] T4 Implement YTD chart axes and labels with minimal layout impact
- [x] T5 Run targeted verification, update review notes, and confirm green state

### Review
- Added a shared chart model in [web/lib/performanceChart.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/performanceChart.ts) so the YTD equity line, rebased benchmark line, filled area, and new axis ticks all derive from one domain instead of separate per-line scaling.
- Updated [web/components/PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx) so the `YTD Equity Curve` now renders an explicit left value axis, a bottom date axis, and stable `performance-axis-x-label` / `performance-axis-y-label` hooks for browser coverage.
- Updated [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) with `performance-axis-line` and `performance-axis-label` styles so the new axes inherit the existing IBM Plex Mono telemetry treatment and the current theme tokens.
- Added unit coverage in [web/tests/performance-chart-model.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-chart-model.test.ts) for shared-domain chart math and axis tick generation, plus browser coverage in [web/e2e/performance-chart-axes.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-chart-axes.spec.ts) for visible x/y labels on `/performance`.
- Kept the adjacent light-theme regression aligned by updating [web/e2e/performance-chart-theme.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-chart-theme.spec.ts) to assert against the browser’s current gradient serialization instead of an overly exact `rgb(...)` string.
- Verified `npx vitest run web/tests/performance-chart-model.test.ts web/tests/performance-chart-theme.test.ts`, `cd web && npx playwright test e2e/performance-page.spec.ts e2e/performance-chart-theme.spec.ts e2e/performance-chart-axes.spec.ts`, and `cd web && npm run build`.

## Session: Regime COR1M Day-Over-Day Baseline Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the live COR1M strip calculation, compare the IB close field against the CRI/Cboe prior close, and isolate the incorrect day-over-day baseline) depends_on: []
- T2 (Record the correction plan and lesson update in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage proving COR1M day change must anchor to the prior CRI/Cboe close instead of the IB websocket close field) depends_on: [T1, T2]
- T4 (Implement the baseline fix in the `/regime` render path with minimal UI impact) depends_on: [T3]
- T5 (Run targeted unit and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the live COR1M strip calculation, compare the IB close field against the CRI/Cboe prior close, and isolate the incorrect day-over-day baseline
- [x] T2 Record the correction plan and lesson update in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage proving COR1M day change must anchor to the prior CRI/Cboe close instead of the IB websocket close field
- [x] T4 Implement the baseline fix in the `/regime` render path with minimal UI impact
- [x] T5 Run targeted unit and Playwright verification, then capture review notes

### Review
- Updated [cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/cri_scan.py) so the CRI payload now preserves `cor1m_previous_close` from the unmodified Cboe daily history and stops overwriting the last historical COR1M bar with the intraday override. The root `cor1m` field can still reflect the best current quote, but the UI now has a clean prior-session anchor.
- Updated [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/regime/route.ts), [useRegime.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/useRegime.ts), and [RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) so the COR1M strip’s day-over-day line uses `data.cor1m_previous_close` (falling back to the last history bar) instead of `prices["COR1M"].close`.
- Locked the payload and UI contract with [test_cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_cri_scan.py), [regime-cor1m-live.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-cor1m-live.test.ts), and browser assertions in [regime-cor1m-live-stream.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-cor1m-live-stream.spec.ts), while keeping adjacent regime regressions green in [regime-cor1m.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-cor1m.spec.ts) and [regime-day-change.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-day-change.spec.ts).
- Refreshed the live cache with `bash scripts/run_cri_scan.sh`. [cri.json](/Users/joemccann/dev/apps/finance/radon/data/cri.json) now has `date=2026-03-10`, `cor1m_previous_close=28.97`, and `history[-1].cor1m=28.97`, and the newest scheduled snapshot at [cri-2026-03-11T13-51.json](/Users/joemccann/dev/apps/finance/radon/data/cri_scheduled/cri-2026-03-11T13-51.json) matches that corrected baseline.
- Verified `pytest scripts/tests/test_cri_scan.py -q`, `npx vitest run web/tests/regime-cor1m-live.test.ts`, `npx vitest run web/tests/regime-market-closed-values.test.ts web/tests/regime-market-closed.test.ts`, `cd web && npx playwright test e2e/regime-cor1m-live-stream.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-day-change.spec.ts`, and `bash scripts/run_cri_scan.sh`.

## Session: Performance Chart Theme Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the `/performance` chart implementation, current theme plumbing, and existing chart-theme test patterns) depends_on: []
- T2 (Record the bug-fix plan and user correction lesson in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for performance-chart theme behavior in unit tests and Playwright) depends_on: [T1, T2]
- T4 (Implement light-theme-aware chart and chart-meta styling on `/performance` with minimal UI changes) depends_on: [T3]
- T5 (Run targeted verification, update review notes, and confirm green state) depends_on: [T4]

### Checklist
- [x] T1 Inspect the `/performance` chart implementation, current theme plumbing, and existing chart-theme test patterns
- [x] T2 Record the bug-fix plan and user correction lesson in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for performance-chart theme behavior in unit tests and Playwright
- [x] T4 Implement light-theme-aware chart and chart-meta styling on `/performance` with minimal UI changes
- [x] T5 Run targeted verification, update review notes, and confirm green state

### Review
- Updated [web/app/globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css) so the `/performance` chart surface, grid lines, benchmark stroke, and chart meta tiles now use dedicated theme variables instead of hardcoded dark RGBA values. The dark palette preserves the existing look; the light palette now switches the chart to a bright neutral surface that matches the rest of the app.
- Added source-level regression coverage in [web/tests/performance-chart-theme.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-chart-theme.test.ts) to lock the CSS contract on `--performance-chart-bg`, `--performance-chart-grid`, and `--performance-chart-meta-bg`.
- Added browser coverage in [web/e2e/performance-chart-theme.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-chart-theme.spec.ts), which starts `/performance` in dark mode, toggles to light, and asserts that both the chart surface and the meta tiles actually change palette.
- Verified `npx vitest run web/tests/performance-chart-theme.test.ts`, `cd web && npx playwright test e2e/performance-chart-theme.spec.ts`, `cd web && npx playwright test e2e/performance-page.spec.ts e2e/performance-chart-theme.spec.ts`, and `cd web && npm run build`.

## Session: Regime COR1M Strip Pattern Alignment (2026-03-11)

### Dependency Graph
- T1 (Inspect the current COR1M strip card layout and the neighboring strip-card pattern the user wants matched) depends_on: []
- T2 (Record the user correction and implementation plan in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for COR1M daily-change placement and 5d-change subline behavior) depends_on: [T1, T2]
- T4 (Implement the COR1M strip layout so it shows the daily change line and moves 5d change into the subline) depends_on: [T3]
- T5 (Run targeted unit and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the current COR1M strip card layout and the neighboring strip-card pattern the user wants matched
- [x] T2 Record the user correction and implementation plan in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for COR1M daily-change placement and 5d-change subline behavior
- [x] T4 Implement the COR1M strip layout so it shows the daily change line and moves 5d change into the subline
- [x] T5 Run targeted unit and Playwright verification, then capture review notes

### Review
- Updated [RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) so the COR1M strip now matches the neighboring metric-card hierarchy: live value first, the standard `DayChange` line from IB `last` vs `close` second, and the cached 5-session context moved into the muted `regime-strip-sub` line.
- Preserved the existing live-COR1M behavior from the earlier fix by keeping `prices["COR1M"].last` as the displayed intraday value, but added the missing `prices["COR1M"].close` input so the daily move and arrow render the same way as VIX, VVIX, and SPY.
- Locked the layout contract with source-level assertions in [regime-cor1m-live.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-cor1m-live.test.ts) and browser assertions in [regime-cor1m-live-stream.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-cor1m-live-stream.spec.ts), [regime-cor1m.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-cor1m.spec.ts), and [regime-day-change.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-day-change.spec.ts).
- Verified `npx vitest run web/tests/regime-cor1m-live.test.ts`, `npx vitest run web/tests/regime-market-closed-values.test.ts web/tests/regime-market-closed.test.ts`, and `cd web && npx playwright test e2e/regime-cor1m-live-stream.spec.ts e2e/regime-cor1m.spec.ts e2e/regime-day-change.spec.ts`.

## Session: Regime COR1M Live Feed Wiring (2026-03-11)

### Dependency Graph
- T1 (Inspect the current `/regime` COR1M render path, IB subscription inputs, and existing test coverage to isolate why live COR1M is not displayed) depends_on: []
- T2 (Record the user correction and live-feed implementation plan in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for live COR1M rendering and badge behavior on `/regime`) depends_on: [T1, T2]
- T4 (Implement live COR1M preference from the IB price stream with cached CRI fallback and preserve the daily 5d change context) depends_on: [T3]
- T5 (Run targeted unit and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the current `/regime` COR1M render path, IB subscription inputs, and existing test coverage to isolate why live COR1M is not displayed
- [x] T2 Record the user correction and live-feed implementation plan in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for live COR1M rendering and badge behavior on `/regime`
- [x] T4 Implement live COR1M preference from the IB price stream with cached CRI fallback and preserve the daily 5d change context
- [x] T5 Run targeted unit and Playwright verification, then capture review notes

### Review
- Updated [RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) so `/regime` now prefers `prices["COR1M"].last` during market hours, falls back to `data.cor1m` when no live tick is available, and carries the live COR1M value into the correlation component, crash-trigger row, and RVOL/COR1M history chart.
- Kept the existing CRI-derived 5-session change context intact, so the COR1M strip still shows the cached `data.cor1m_5d_change` while the main displayed COR1M level can update from IB in real time.
- Added source-level regression coverage in [regime-cor1m-live.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-cor1m-live.test.ts) and browser coverage in [regime-cor1m-live-stream.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-cor1m-live-stream.spec.ts) with a mocked websocket COR1M tick.
- Updated [CLAUDE.md](/Users/joemccann/dev/apps/finance/radon/CLAUDE.md) so the repo spec now matches the runtime behavior: COR1M remains daily-only when the market is closed, but can render live from IB during market hours.
- Verified `npx vitest run web/tests/regime-cor1m-live.test.ts web/tests/regime-market-closed-values.test.ts web/tests/regime-market-closed.test.ts` and `cd web && npx playwright test e2e/regime-cor1m-live-stream.spec.ts e2e/regime-market-closed-eod.spec.ts e2e/regime-cor1m.spec.ts`.

## Session: Regime History Tooltip Copy And Placement (2026-03-11)

### Dependency Graph
- T1 (Inspect the Regime history header, shared tooltip copy, and existing unit/Playwright test patterns for the smallest safe fix) depends_on: []
- T2 (Record the user-facing fix plan and correction lesson in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for the 20-session history header contract and tooltip copy on `/regime`) depends_on: [T1, T2]
- T4 (Implement the history-header layout adjustment and rewrite the tooltip copy to explain the visible data without implementation details) depends_on: [T3]
- T5 (Run targeted unit and Playwright verification, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the Regime history header, shared tooltip copy, and existing unit/Playwright test patterns for the smallest safe fix
- [x] T2 Record the user-facing fix plan and correction lesson in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for the 20-session history header contract and tooltip copy on `/regime`
- [x] T4 Implement the history-header layout adjustment and rewrite the tooltip copy to explain the visible data without implementation details
- [x] T5 Run targeted unit and Playwright verification, then capture review notes

### Review
- Reworked the history header in [web/components/RegimePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/RegimePanel.tsx) so the `20-SESSION HISTORY` label and `?` icon now live in a shared `section-title` group while the optional `LIVE` badge remains on the right edge of the section header.
- Extended [web/components/InfoTooltip.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/InfoTooltip.tsx) with optional test ids so the history tooltip can be targeted directly in browser coverage without changing behavior for the other tooltips in the app.
- Replaced the stale implementation-oriented history copy in [web/lib/sectionTooltips.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/sectionTooltips.ts) with a `20-SESSION HISTORY` entry that explains what the left and right charts mean in user-facing terms and removes D3/WS/websocket references.
- Added a source-level regression in [web/tests/regime-history-tooltip.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-history-tooltip.test.ts) and a browser regression in [web/e2e/regime-history-tooltip.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-history-tooltip.spec.ts) to lock the header grouping, tooltip trigger placement, and plain-English copy.
- Verified `npx vitest run web/tests/regime-history-tooltip.test.ts`, `cd web && npx playwright test e2e/regime-history-tooltip.spec.ts`, and `cd web && npm run build` all pass.

## Session: CRI Cache Refresh Remediation (2026-03-11)

### Dependency Graph
- T1 (Inspect the live CRI cache artifacts, the current scanner failure mode, and why `/regime` is still serving missing RVOL history) depends_on: []
- T2 (Record the remediation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Add failing regression coverage for the CBOE-before-Yahoo COR1M fallback and for live `/regime` RVOL history visibility) depends_on: [T1, T2]
- T4 (Implement the CBOE COR1M fallback plus doc updates, then ensure the route-visible CRI cache and newest scheduled snapshot are valid) depends_on: [T3]
- T5 (Verify the regenerated cache contents and targeted tests, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Inspect the live CRI cache artifacts, the current scanner failure mode, and why `/regime` is still serving missing RVOL history
- [x] T2 Record the remediation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Add failing regression coverage for the CBOE-before-Yahoo COR1M fallback and for live `/regime` RVOL history visibility
- [x] T4 Implement the CBOE COR1M fallback plus doc updates, then ensure the route-visible CRI cache and newest scheduled snapshot are valid
- [x] T5 Verify the regenerated cache contents and targeted tests, then capture review notes

### Review
- Updated [scripts/cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/cri_scan.py) so COR1M history and fallback quote selection now use the official Cboe dashboard feed before Yahoo Finance, while preserving the earlier IB client-id retry path.
- Added regression coverage in [scripts/tests/test_cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_cri_scan.py) for the new source order and in [web/e2e/regime-rvol-history-live-cache.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-rvol-history-live-cache.spec.ts) for the browser-visible “20 RVOL dots from live cache” contract.
- Updated [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), [docs/strategies.md](/Users/joemccann/dev/apps/finance/radon/docs/strategies.md), [.pi/AGENTS.md](/Users/joemccann/dev/apps/finance/radon/.pi/AGENTS.md), [.pi/prompts/cri-scan.md](/Users/joemccann/dev/apps/finance/radon/.pi/prompts/cri-scan.md), and [.pi/skills/html-report/SKILL.md](/Users/joemccann/dev/apps/finance/radon/.pi/skills/html-report/SKILL.md) so the repo now documents the COR1M source order as IB → official Cboe dashboard feed → Yahoo last resort.
- Confirmed the route-visible cache state is valid now: [data/cri.json](/Users/joemccann/dev/apps/finance/radon/data/cri.json) has 20 history rows, 20 numeric `realized_vol` values, and 40 cached `spy_closes`; a fresh valid scheduled snapshot was also written at [cri-2026-03-11T13-24.json](/Users/joemccann/dev/apps/finance/radon/data/cri_scheduled/cri-2026-03-11T13-24.json).
- Verified the final path with `python3 scripts/cri_scan.py --json > /tmp/cri-refresh.json`, `bash scripts/run_cri_scan.sh`, `pytest scripts/tests/test_cri_scan.py scripts/tests/test_cri_client_id.py -q`, `npx playwright test e2e/regime-rvol-history-live-cache.spec.ts e2e/regime-cor1m-live-route.spec.ts`, `bash -n scripts/run_cri_scan.sh`, and `bash -n scripts/run_data_refresh.sh`.

## Session: Site Light Theme Toggle (2026-03-11)

### Dependency Graph
- T1 (Audit the `/site` theme surface, existing header structure, and available unit/E2E test harnesses without disturbing unrelated worktree changes) depends_on: []
- T2 (Record the light-theme implementation and verification plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing regression coverage for site theme state resolution, header toggle rendering, and browser-visible light/dark switching) depends_on: [T1, T2]
- T4 (Implement the `/site` light theme tokens, persisted theme toggle button, and hydration-safe root theme wiring) depends_on: [T3]
- T5 (Run targeted verification for unit tests, site lint/build, and the site Playwright E2E flow, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Audit the `/site` theme surface, existing header structure, and available unit/E2E test harnesses without disturbing unrelated worktree changes
- [x] T2 Record the light-theme implementation and verification plan in `tasks/todo.md`
- [x] T3 Add failing regression coverage for site theme state resolution, header toggle rendering, and browser-visible light/dark switching
- [x] T4 Implement the `/site` light theme tokens, persisted theme toggle button, and hydration-safe root theme wiring
- [x] T5 Run targeted verification for unit tests, site lint/build, and the site Playwright E2E flow, then capture review notes

### Review
- Added the site theme state contract in [theme.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/theme.ts) and covered it in [theme.test.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/theme.test.ts), so the site now resolves a saved `theme` preference first, falls back to the browser color-scheme when no saved value exists, and exposes a deterministic dark/light toggle path.
- Wired the persisted theme into the document shell in [layout.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/layout.tsx) with a bootstrap script plus `html[data-theme]`, which lets the marketing site switch palettes before hydration while still keeping the rest of the page server-rendered.
- Reused the in-progress client boundary in [ThemeToggle.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/atoms/ThemeToggle.tsx) and mounted it in [HeaderShell.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/sections/HeaderShell.tsx), adding a header-level toggle button that persists to `localStorage`, updates the `theme-color` meta tag, and remains keyboard accessible.
- Expanded the semantic token overrides in [globals.css](/Users/joemccann/dev/apps/finance/radon/site/app/globals.css) so the site gets a true light theme without breaking the Radon terminal geometry; also updated the accent-button text in [page.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/page.tsx), [HeroSection.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/sections/HeroSection.tsx), [HeaderShell.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/sections/HeaderShell.tsx), and [FinalCTASection.tsx](/Users/joemccann/dev/apps/finance/radon/site/components/sections/FinalCTASection.tsx) so the CTA contrast stays correct in light mode.
- Added a site-specific Playwright harness in [playwright.site.config.ts](/Users/joemccann/dev/apps/finance/radon/web/playwright.site.config.ts) and the browser regression in [theme-toggle.spec.ts](/Users/joemccann/dev/apps/finance/radon/site/e2e/theme-toggle.spec.ts), which proves the site honors a saved light theme on load and persists dark→light toggles in the browser.
- Updated [site/.gitignore](/Users/joemccann/dev/apps/finance/radon/site/.gitignore) and [site/eslint.config.mjs](/Users/joemccann/dev/apps/finance/radon/site/eslint.config.mjs) so the dedicated `.next-site-playwright/` dev output used by the site E2E harness stays out of git status and lint.
- Verified `npx vitest run site/lib/theme.test.ts`, `cd web && npx playwright test theme-toggle.spec.ts --config playwright.site.config.ts`, `cd site && npm run lint`, and `cd site && NEXT_DIST_DIR=.next-build npm run build`.

## Session: Site Docs Checkpoint And SEO Audit (2026-03-11)

### Dependency Graph
- T1 (Audit the current `/site` worktree, documentation gaps, generated artifact noise, and the unresolved non-site WIP that must stay out of the checkpoint commit) depends_on: []
- T2 (Update docs and ignore rules for the landing-page batch, then create a scoped checkpoint commit for finished `/site` work only) depends_on: [T1]
- T3 (Audit the live `/site` SEO surface, identify crawl/indexation/share gaps, and write failing or objective verification around the new SEO contract where practical) depends_on: [T1]
- T4 (Implement the `/site` SEO remediations, generate and open an HTML audit report with recommendations, and document the workflow) depends_on: [T2, T3]
- T5 (Run targeted verification for metadata/routes/build/report generation, then capture review notes and a second scoped commit for the SEO pass) depends_on: [T4]

### Checklist
- [x] T1 Audit the current `/site` worktree, documentation gaps, generated artifact noise, and the unresolved non-site WIP that must stay out of the checkpoint commit
- [ ] T2 Update docs and ignore rules for the landing-page batch, then create a scoped checkpoint commit for finished `/site` work only
- [x] T3 Audit the live `/site` SEO surface, identify crawl/indexation/share gaps, and write failing or objective verification around the new SEO contract where practical
- [x] T4 Implement the `/site` SEO remediations, generate and open an HTML audit report with recommendations, and document the workflow
- [ ] T5 Run targeted verification for metadata/routes/build/report generation, then capture review notes and a second scoped commit for the SEO pass

### Review
- Added a shared SEO contract in [site/lib/seo.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/seo.ts) and wired it into [site/app/layout.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/layout.tsx) so the marketing site now emits canonical metadata, Open Graph and Twitter cards, a manifest link, theme color, and JSON-LD for `WebSite`, `Organization`, and `SoftwareApplication`.
- Added crawl/share routes in [site/app/robots.ts](/Users/joemccann/dev/apps/finance/radon/site/app/robots.ts), [site/app/sitemap.ts](/Users/joemccann/dev/apps/finance/radon/site/app/sitemap.ts), [site/app/manifest.ts](/Users/joemccann/dev/apps/finance/radon/site/app/manifest.ts), [site/app/opengraph-image.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/opengraph-image.tsx), and [site/app/twitter-image.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/twitter-image.tsx), plus link-hardening and semantic-nav updates in the landing-page sections so the single-page site has first-party crawl and share surfaces.
- Added regression coverage in [site/lib/seo.test.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/seo.test.ts) and updated [vitest.config.ts](/Users/joemccann/dev/apps/finance/radon/vitest.config.ts) so site-level SEO tests are included in the repo’s normal Vitest suite.
- Added [site_seo_audit.py](/Users/joemccann/dev/apps/finance/radon/scripts/site_seo_audit.py), which audits either a live site URL or the built Next artifacts under `site/.next-build/server/app`, then writes the branded HTML report at [site-seo-audit-2026-03-11.html](/Users/joemccann/dev/apps/finance/radon/reports/site-seo-audit-2026-03-11.html).
- Updated [site/README.md](/Users/joemccann/dev/apps/finance/radon/site/README.md) and [README.md](/Users/joemccann/dev/apps/finance/radon/README.md) so the production-site URL requirement, crawl/share routes, and audit workflow are documented for both live-URL and build-artifact verification.
- Verified `npx vitest run site/lib/seo.test.ts`, `cd site && npm run lint`, `cd site && NEXT_DIST_DIR=.next-build npx next build --webpack`, `python3 -m py_compile scripts/site_seo_audit.py`, and `python3 scripts/site_seo_audit.py --build-dir site/.next-build/server/app`, with the final audit reporting `18 pass, 0 warn, 0 fail`.
- Attempted to stage and commit the scoped site batch, but this session cannot write inside `.git/` (`fatal: Unable to create '.git/index.lock': Operation not permitted`), so the commit itself remains blocked outside the repo content changes.

## Session: RVOL Docs, Commit Checkpoint, And Fullscreen Toggle (2026-03-11)

### Dependency Graph
- T1 (Audit the current RVOL-fix worktree, documentation update targets, and the shell/header implementation that owns the theme selector) depends_on: []
- T2 (Record the docs + commit + fullscreen plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Update the relevant docs for the RVOL cache/backfill fix and create a scoped commit for the CRI/RVOL work) depends_on: [T1, T2]
- T4 (Add failing fullscreen regressions for the header control and Escape-to-exit behavior) depends_on: [T1, T2]
- T5 (Implement the fullscreen toggle next to the theme selector and wire document-level Escape handling) depends_on: [T3, T4]
- T6 (Run targeted verification for docs/build/tests/browser behavior, then capture review notes) depends_on: [T5]

### Checklist
- [x] T1 Audit the current RVOL-fix worktree, documentation update targets, and the shell/header implementation that owns the theme selector
- [x] T2 Record the docs + commit + fullscreen plan in `tasks/todo.md`
- [x] T3 Update the relevant docs for the RVOL cache/backfill fix and create a scoped commit for the CRI/RVOL work
- [x] T4 Add failing fullscreen regressions for the header control and Escape-to-exit behavior
- [x] T5 Implement the fullscreen toggle next to the theme selector and wire document-level Escape handling
- [x] T6 Run targeted verification for docs/build/tests/browser behavior, then capture review notes

### Review
- Updated the CRI/regime docs in [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), [docs/strategies.md](/Users/joemccann/dev/apps/finance/radon/docs/strategies.md), and [docs/status.md](/Users/joemccann/dev/apps/finance/radon/docs/status.md) to describe the richer CRI cache selection, 20-session RVOL backfill behavior, and the post-close cache refresh path.
- Committed the scoped RVOL/cache/docs checkpoint as `27e78a7` (`fix: restore regime rvol history from cri cache`) without pulling in the unrelated site/worktree changes that were already present in the repo.
- Added the fullscreen control in [Header.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/Header.tsx) and [WorkspaceShell.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx), using `Maximize2` / `Minimize2` icons and a document-level Escape handler that exits fullscreen when the app is expanded.
- Kept the existing theme button uniquely targetable by moving the new control onto its own `.fullscreen-toggle` class and sharing the button styling in [globals.css](/Users/joemccann/dev/apps/finance/radon/web/app/globals.css).
- Added fullscreen regressions in [header-fullscreen-control.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/header-fullscreen-control.test.ts) and [header-fullscreen.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/header-fullscreen.spec.ts).
- Verified `pytest scripts/tests/test_cri_scan.py -q`, `npx vitest run web/tests/cri-cache-selection.test.ts web/tests/regime-history-backfill.test.ts web/tests/regime-route-cache-selection.test.ts web/tests/header-fullscreen-control.test.ts`, `cd web && npx playwright test e2e/regime-rvol-history.spec.ts e2e/regime-rvol-history-live-route.spec.ts e2e/header-fullscreen.spec.ts`, `bash -n scripts/run_cri_scan.sh && bash -n scripts/run_data_refresh.sh`, and `cd web && npm run build`.
- Additional sanity check: the theme-toggle-only cases in [price-chart-theme.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/price-chart-theme.spec.ts) still pass. Two older modal-opening cases in that same spec are still failing because the stubbed AAPL ticker-detail modal never appears; that failure is outside the fullscreen path and was not part of this task.

## Session: Regime RVOL History Cache Fix (2026-03-11)

### Dependency Graph
- T1 (Audit the `/regime` RVOL/COR1M chart data flow, existing CRI cache artifacts, and the post-close sync hooks) depends_on: []
- T2 (Record the RVOL history cache fix plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing regression tests for RVOL history backfill, cache normalization, and `/regime` browser rendering) depends_on: [T1, T2]
- T4 (Implement 20-session RVOL history backfill plus post-close CRI cache refresh in the daily sync path) depends_on: [T3]
- T5 (Run targeted verification with Python tests, web tests, and Playwright E2E) depends_on: [T4]
- T6 (Capture review notes and verification results for the RVOL history fix) depends_on: [T5]

### Checklist
- [x] T1 Audit the `/regime` RVOL/COR1M chart data flow, existing CRI cache artifacts, and the post-close sync hooks
- [x] T2 Record the RVOL history cache fix plan in `tasks/todo.md`
- [x] T3 Add failing regression tests for RVOL history backfill, cache normalization, and `/regime` browser rendering
- [x] T4 Implement 20-session RVOL history backfill plus post-close CRI cache refresh in the daily sync path
- [x] T5 Run targeted verification with Python tests, web tests, and Playwright E2E
- [x] T6 Capture review notes and verification results for the RVOL history fix

### Review
- Expanded the CRI payload in [scripts/cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/cri_scan.py) so cached `spy_closes` now preserve the trailing 40 daily closes, which is enough to reconstruct realized-vol values for the full 20-session chart instead of only today’s point.
- Added [regimeHistory.ts](/Users/joemccann/dev/apps/finance/radon/web/lib/regimeHistory.ts) and wired it into [route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/regime/route.ts) so `/api/regime` backfills missing `history[].realized_vol` values from cached SPY closes before the page renders the RVOL/COR1M chart.
- Updated the post-close path in [run_data_refresh.sh](/Users/joemccann/dev/apps/finance/radon/scripts/run_data_refresh.sh) so the daily sync refreshes `data/cri.json` and writes a new scheduled CRI snapshot after 4:00 PM ET whenever the current cache is missing a complete 20-session RVOL history.
- Added regressions in [test_cri_scan.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_cri_scan.py), [regime-history-backfill.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/regime-history-backfill.test.ts), and [regime-rvol-history.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/regime-rvol-history.spec.ts) to lock the cache contract, API repair path, and live-route browser rendering.
- Verified `pytest scripts/tests/test_cri_scan.py -q`, `npx tsx --test web/tests/regime-history-backfill.test.ts`, `cd web && npx playwright test e2e/regime-rvol-history.spec.ts`, `bash -n scripts/run_data_refresh.sh`, and `cd web && npm run build`.

## Session: Site Landing Page Phase 4 Implementation (2026-03-11)

### Dependency Graph
- T1 (Audit the current `/site` implementation, package setup, and brand constraints for the landing-page refactor) depends_on: []
- T2 (Record the Phase 4 implementation plan in `tasks/todo.md`) depends_on: [T1]
- T3 (Refactor `/site` into Radon-native atoms, molecules, organisms, and section components) depends_on: [T1, T2]
- T4 (Replace the existing homepage with the new institutional-terminal landing page and supporting content/data) depends_on: [T3]
- T5 (Verify the redesigned `/site` with build and browser automation, then capture review notes) depends_on: [T4]

### Checklist
- [x] T1 Audit the current `/site` implementation, package setup, and brand constraints for the landing-page refactor
- [x] T2 Record the Phase 4 implementation plan in `tasks/todo.md`
- [x] T3 Refactor `/site` into Radon-native atoms, molecules, organisms, and section components
- [x] T4 Replace the existing homepage with the new institutional-terminal landing page and supporting content/data
- [x] T5 Verify the redesigned `/site` with build and browser automation, then capture review notes

### Review
- Replaced the old monolithic homepage with a section-composed landing page in [site/app/page.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/page.tsx) and a reusable `/site/components` architecture built around Radon-native atoms, molecules, organisms, and sections.
- The redesign now centers the product on strategies, execution, and state reconstruction, with dedicated sections for the strategy matrix, execution rail, surface preview, auditability layer, and final operator CTA.
- Preserved Radon’s enforced design system in [site/app/globals.css](/Users/joemccann/dev/apps/finance/radon/site/app/globals.css) and the section/component primitives: dark-first surfaces, teal `signal.core`, hairline borders, tight panel geometry, mono telemetry, and depth via layered surfaces instead of soft shadows or glassmorphism.
- Consolidated the richer landing-page content model in [site/lib/landing-content.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/landing-content.ts) and reconciled mixed worktree component contracts so the active section tree compiles cleanly against a single data shape.
- Updated [site/.gitignore](/Users/joemccann/dev/apps/finance/radon/site/.gitignore) to ignore `.next-build/` and `.next-dev-webpack/`, so the alternate build paths used for verification do not leave untracked noise in the worktree.
- Verified `cd site && npm run lint` passes.
- Verified `cd site && NEXT_DIST_DIR=.next-build npm run build` passes, using an alternate `distDir` to avoid a live `.next` lock held by another local Next process.
- Added [site/next.config.ts](/Users/joemccann/dev/apps/finance/radon/site/next.config.ts) support for environment-scoped `distDir` values and an explicit Turbopack root so site verification can run without colliding with other local site processes.
- Verified live browser rendering on a dedicated webpack dev server with `cd site && NEXT_DIST_DIR=.next-dev-webpack npx next dev -p 3335 --webpack`.
- Verified browser automation from the repo’s Playwright install against `http://127.0.0.1:3335`, asserting the hero, strategy, execution, and audit sections and capturing `/tmp/radon-site-phase4.png`.


## Session: Performance Card Explainability Modals (2026-03-11)

### Dependency Graph
- T1 (Inspect the current `/performance` panel and the existing clickable metric-card pattern used on `/portfolio`) depends_on: []
- T2 (Record the implementation plan for clickable performance cards in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing browser or route coverage for clickable performance cards and their explanatory modal content) depends_on: [T1, T2]
- T4 (Implement clickable cards and explanatory modal content across the `/performance` page) depends_on: [T3]
- T5 (Run targeted verification with Playwright browser automation and supporting tests) depends_on: [T4]
- T6 (Capture review notes and summarize the final behavior) depends_on: [T5]

### Checklist
- [x] T1 Inspect the current `/performance` panel and the existing clickable metric-card pattern used on `/portfolio`
- [x] T2 Record the implementation plan for clickable performance cards in `tasks/todo.md`
- [x] T3 Add failing browser or route coverage for clickable performance cards and their explanatory modal content
- [x] T4 Implement clickable cards and explanatory modal content across the `/performance` page
- [x] T5 Run targeted verification with Playwright browser automation and supporting tests
- [x] T6 Capture review notes and summarize the final behavior

### Review
- Scoped the clickable behavior to the eight actual `StatCard` metric cards in the Core Performance section of [PerformancePanel.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/PerformancePanel.tsx), which matches the existing `/portfolio` interaction pattern without turning non-card list rows into fake cards.
- Added [MetricDefinitionModal.tsx](/Users/joemccann/dev/apps/finance/radon/web/components/MetricDefinitionModal.tsx) so each performance card can explain both what the metric means and how it is calculated, instead of only showing a formula string.
- Converted all eight core performance cards into accessible button-style metric cards with `metric-card-clickable`, stable `data-testid` values, and per-metric definition/formula content wired from the reconstructed performance payload.
- Added browser coverage in [performance-page.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-page.spec.ts) to prove the cards are clickable and that representative cards open the expected explainability modal content.
- Verified `cd web && npx playwright test e2e/performance-page.spec.ts --grep "performance metric cards are clickable"`, `cd web && npx playwright test e2e/performance-page.spec.ts`, and `cd web && npm run build`.

## Session: Performance Net Liq Reconciliation Fix (2026-03-11)

### Dependency Graph
- T1 (Inspect the current performance reconstruction engine, API freshness behavior, and existing tests to define exact failing cases) depends_on: []
- T2 (Record the implementation plan for the reconciliation fix in `tasks/todo.md`) depends_on: [T1]
- T3 (Add failing regression tests for Flex trade-date normalization, ending-equity anchoring, and stale `/api/performance` behavior) depends_on: [T1, T2]
- T4 (Implement Python and route fixes so `/performance` reconciles to the current portfolio net liquidation snapshot) depends_on: [T3]
- T5 (Add or update browser coverage for the user-visible reconciliation behavior on `/performance`) depends_on: [T4]
- T6 (Run targeted verification, capture review notes, and summarize the root-cause fix) depends_on: [T4, T5]

### Checklist
- [x] T1 Inspect the current performance reconstruction engine, API freshness behavior, and existing tests to define exact failing cases
- [x] T2 Record the implementation plan for the reconciliation fix in `tasks/todo.md`
- [x] T3 Add failing regression tests for Flex trade-date normalization, ending-equity anchoring, and stale `/api/performance` behavior
- [x] T4 Implement Python and route fixes so `/performance` reconciles to the current portfolio net liquidation snapshot
- [x] T5 Add or update browser coverage for the user-visible reconciliation behavior on `/performance`
- [x] T6 Run targeted verification, capture review notes, and summarize the root-cause fix

### Review
- Fixed the core reconstruction bug in [scripts/portfolio_performance.py](/Users/joemccann/dev/apps/finance/radon/scripts/portfolio_performance.py) by normalizing trade dates before parsing and replay, so raw Flex `YYYYMMDD` dates now align with the `YYYY-MM-DD` benchmark calendar used for the YTD curve.
- Hardened the performance payload builder so option-history fetch failures no longer abort the entire sync; missing option marks are downgraded to warnings plus `contracts_missing_history`, allowing the ending equity to stay anchored to the current account snapshot.
- Updated [web/app/api/performance/route.ts](/Users/joemccann/dev/apps/finance/radon/web/app/api/performance/route.ts) so the route detects when cached performance is behind the current portfolio snapshot and refreshes the persisted payload before serving it, with cached fallback if the sync fails.
- Expanded [scripts/tests/test_portfolio_performance.py](/Users/joemccann/dev/apps/finance/radon/scripts/tests/test_portfolio_performance.py), [web/tests/performance-route.test.ts](/Users/joemccann/dev/apps/finance/radon/web/tests/performance-route.test.ts), and [web/e2e/performance-page.spec.ts](/Users/joemccann/dev/apps/finance/radon/web/e2e/performance-page.spec.ts) to cover compact Flex dates, stale cache refresh, and the browser-visible ending-equity reconciliation behavior.
- Verified the live reconstruction path after the fix: `python3 scripts/portfolio_performance.py --json` completed successfully and matched `data/portfolio.json` exactly with `ending_equity == account_summary.net_liquidation == 1308382.19`.

## Session: Performance YTD Reconciliation Investigation (2026-03-11)

### Dependency Graph
- T1 (Inspect the performance engine, web route, and portfolio net liquidation source to map the current YTD calculation flow) depends_on: []
- T2 (Record the investigation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Compare the live or cached `/performance` and `/portfolio` payloads to locate the source of the mismatch) depends_on: [T1]
- T4 (Explain the exact YTD methodology and identify the most likely root cause of the discrepancy) depends_on: [T2, T3]

### Checklist
- [x] T1 Inspect the performance engine, web route, and portfolio net liquidation source to map the current YTD calculation flow
- [x] T2 Record the investigation plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Compare the live or cached `/performance` and `/portfolio` payloads to locate the source of the mismatch
- [x] T4 Explain the exact YTD methodology and identify the most likely root cause of the discrepancy

### Review
- Confirmed the `/performance` page is a reconstructed close-to-close YTD curve built from trade cash flows plus daily marks, not a live account-equity history.
- Confirmed the `/portfolio` page reads `data/portfolio.json` with a 60-second stale window, while `/performance` reads `data/performance.json` with a 15-minute stale window and serves the old cache immediately while refreshing in the background.
- Observed a live mismatch between the two payloads during the investigation: `/api/performance` was still serving `as_of: 2026-03-10` with `ending_equity: 1063031.8637`, while `/api/portfolio` was serving `last_sync: 2026-03-11T06:37:14.669874` with `account_summary.net_liquidation: 1313112.03`.
- Running `scripts/portfolio_performance.py --json` against the current portfolio snapshot still produced a mismatched ending equity, which shows the issue is not only cache staleness.
- The most likely engine bug is trade-date normalization in `parse_flex_trade_rows()`: Flex trade dates are being consumed as `YYYYMMDD`, while the benchmark calendar uses `YYYY-MM-DD`. That breaks the day matching inside `reconstruct_equity_curve()` and can prevent fills from ever being applied on the intended dates.
- A second degradation path is active as well: when IB Flex is rate-limited, the script falls back to `data/blotter.json`, which can lag the current portfolio and therefore cannot reliably explain the live holdings on `/portfolio`.

## Session: Codex Skill YAML Fixes (2026-03-11)

### Dependency Graph
- T1 (Inspect the affected `SKILL.md` files under `~/.codex/skills` and identify the invalid YAML frontmatter) depends_on: []
- T2 (Record the task plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`) depends_on: [T1]
- T3 (Patch the invalid `description` frontmatter fields so each skill manifest uses a string, not a sequence) depends_on: [T2]
- T4 (Validate both skill manifests with a direct YAML/frontmatter parse check) depends_on: [T3]
- T5 (Capture review notes and summarize which files were fixed) depends_on: [T4]

### Checklist
- [x] T1 Inspect the affected `SKILL.md` files under `~/.codex/skills` and identify the invalid YAML frontmatter
- [x] T2 Record the task plan and the user correction in `tasks/todo.md` and `tasks/lessons.md`
- [x] T3 Patch the invalid `description` frontmatter fields so each skill manifest uses a string, not a sequence
- [x] T4 Validate both skill manifests with a direct YAML/frontmatter parse check
- [x] T5 Capture review notes and summarize which files were fixed

### Review
- Inspected both reported skill manifests under `~/.codex/skills` and confirmed only `metal-macos-replatform/SKILL.md` was malformed; `metal-macos/SKILL.md` already had a valid string-valued `description`.
- The root cause was YAML frontmatter using bracketed placeholder text after `description:` in `metal-macos-replatform/SKILL.md`, which YAML parses as a sequence instead of the string type expected by the Codex skill loader.
- Rewrote that `description` field as a plain string while keeping the skill intent intact.
- Validated both manifests with a direct frontmatter parse check using `yaml.safe_load`, confirming `description` resolves to `str` for both files.
- Result: the repeated "invalid YAML: description: invalid type: sequence, expected a string" loader warning should stop on the next skill discovery/load cycle.

## Session: Performance Page Explainer Report (2026-03-11)

### Dependency Graph
- T1 (Audit the live `/performance` page and backend metric engine to enumerate every rendered item) depends_on: []
- T2 (Document the plan and output requirements for the HTML explainer report) depends_on: [T1]
- T3 (Implement a generated HTML report that maps every displayed metric to its value, formula, and definition) depends_on: [T2]
- T4 (Validate the report against current `data/performance.json` or live `/api/performance`, then open it locally) depends_on: [T3]
- T5 (Capture review notes and final output path in the task log) depends_on: [T4]

### Checklist
- [x] T1 Audit the live `/performance` page and backend metric engine to enumerate every rendered item
- [x] T2 Document the plan and output requirements for the HTML explainer report
- [x] T3 Implement a generated HTML report that maps every displayed metric to its value, formula, and definition
- [x] T4 Validate the report against current `data/performance.json` or live `/api/performance`, then open it locally
- [x] T5 Capture review notes and final output path in the task log

### Review
- Added [performance_explainer_report.py](/Users/joemccann/dev/apps/finance/radon/scripts/performance_explainer_report.py), a reusable generator that reads the current `data/performance.json` payload and emits a standalone HTML explainer for every currently visible `/performance` item.
- The report covers the hero banner, source/drawdown pills, all eight core performance cards, the chart header/legend/meta block, all tail/path-risk items, all distribution/capture items, methodology provenance, and each warning flag.
- Each row in the report includes the page item's current display, the exact formula or provenance used to render it, and a plain-English institutional definition.
- Generated output at [performance-page-explainer-2026-03-11.html](/Users/joemccann/dev/apps/finance/radon/reports/performance-page-explainer-2026-03-11.html) and opened it locally with the standard browser-open flow.
- Verified the file exists, has content, and includes the expected sections: Hero Banner, Core Performance, Tail And Path Risk, Methodology, and Warnings.

## Session: Portfolio Performance Route (2026-03-10)

### Dependency Graph
- T1 (Audit existing portfolio, blotter, benchmark, and web route plumbing for a new performance surface) depends_on: []
- T2 (Define YTD performance methodology, institutional metric set, and library strategy from primary-source research) depends_on: [T1]
- T3 (Add backend/unit tests for trade parsing, curve reconstruction, and metric calculations) depends_on: [T2]
- T4 (Implement Python performance engine, cache artifact, and benchmark/price fetch path) depends_on: [T3]
- T5 (Expose performance data through a new web API contract and shared types) depends_on: [T4]
- T6 (Add the `/performance` route, section wiring, and branded performance panel UI) depends_on: [T5]
- T7 (Add browser coverage for the new page and confirm rendered metrics against the API contract) depends_on: [T6]
- T8 (Run verification, update relevant docs, and capture review notes/risks) depends_on: [T4, T5, T6, T7]

### Checklist
- [x] T1 Audit existing portfolio, blotter, benchmark, and web route plumbing for a new performance surface
- [x] T2 Define YTD performance methodology, institutional metric set, and library strategy from primary-source research
- [x] T3 Add backend/unit tests for trade parsing, curve reconstruction, and metric calculations
- [x] T4 Implement Python performance engine, cache artifact, and benchmark/price fetch path
- [x] T5 Expose performance data through a new web API contract and shared types
- [x] T6 Add the `/performance` route, section wiring, and branded performance panel UI
- [x] T7 Add browser coverage for the new page and confirm rendered metrics against the API contract
- [x] T8 Run verification, update relevant docs, and capture review notes/risks

### Review
- Reused and completed the in-repo `scripts/portfolio_performance.py` engine instead of adding a new analytics dependency. The metric formulas stay local, align to `empyrical` / `quantstats` conventions, and compute a reconstructed YTD equity curve from IB Flex executions plus historical marks.
- Added focused backend coverage in `scripts/tests/test_portfolio_performance.py` for OCC-style option ID formatting, option mark selection, curve replay, core institutional metrics, and the top-level payload contract that feeds the web route.
- Refreshed `data/performance.json` from the live script so the existing `/api/performance` cache and the new UI load the same contract.
- Wired the new `performance` workspace section into the Next.js terminal, added a dedicated `PerformancePanel`, and surfaced the institutional metrics stack: YTD return, ending equity, Sharpe, Sortino, max drawdown, beta, alpha, information ratio, VaR/CVaR, charted YTD equity vs benchmark, and methodology/warning panels.
- Added targeted route metadata coverage in `web/tests/chat.test.ts`, `web/tests/data.test.ts`, and `web/tests/performance-route.test.ts`, plus mocked browser automation in `web/e2e/performance-page.spec.ts`.
- Caught and fixed a payload-contract bug during final verification: `summary.trading_days` was being overwritten by return-count metrics. The payload now reports full YTD session count, and the refreshed cache plus live `/api/performance` route both return `46` series points with `46` trading days.
- Verified `pytest scripts/tests/test_portfolio_performance.py -q`, `npx vitest run web/tests/chat.test.ts web/tests/data.test.ts web/tests/performance-route.test.ts`, `cd web && npx playwright test e2e/performance-page.spec.ts`, and `cd web && npm run build`.
- Residual risk: the reconstructed curve is anchored to current net liquidation and assumes no unmodeled external cash flows inside the observed window; that caveat is exposed directly in the API warnings and rendered on the page.

## Session: Vercel Site Build Gate (2026-03-10)

### Dependency Graph
- T1 (Inspect current repo/Vercel configuration and confirm the site app deployment root assumptions) depends_on: []
- T2 (Add a repo-side Vercel ignored-build rule so the site deploy only runs when `/site` changes) depends_on: [T1]
- T3 (Document the site deployment gating behavior in the relevant README files) depends_on: [T2]
- T4 (Run targeted validation for the ignore-step script and prepare a scoped commit/push) depends_on: [T2, T3]

### Checklist
- [x] T1 Inspect current repo/Vercel configuration and confirm the site app deployment root assumptions
- [x] T2 Add a repo-side Vercel ignored-build rule so the site deploy only runs when `/site` changes
- [x] T3 Document the site deployment gating behavior in the relevant README files
- [x] T4 Run targeted validation for the ignore-step script and prepare a scoped commit/push

### Review
- Added [site/vercel.json](/Users/joemccann/dev/apps/finance/radon/site/vercel.json) with a Vercel `ignoreCommand` and implemented the git-diff gate in [vercel-ignore-build.mjs](/Users/joemccann/dev/apps/finance/radon/site/scripts/vercel-ignore-build.mjs).
- The ignore-step script now only skips the deploy when it can prove there were no changes under `site/`; if the previous SHA or diff lookup is unavailable, it continues the build instead of risking a false skip.
- Documented the deployment behavior in [site/README.md](/Users/joemccann/dev/apps/finance/radon/site/README.md) and the repo-level [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), including the requirement that the Vercel project Root Directory be `site/`.
- Verified the ignore-step locally from both the site root and repo root with identical SHAs: `node scripts/vercel-ignore-build.mjs` and `node site/scripts/vercel-ignore-build.mjs` both exited `0` and reported that the build would be skipped.

## Session: COR1M Current Value Fix (2026-03-10)

### Dependency Graph
- T1 (Audit current COR1M sourcing, cache flow, and UI expectations for the mismatch) depends_on: []
- T2 (Add failing backend and browser regressions for COR1M current-value sourcing) depends_on: [T1]
- T3 (Implement CRI scan fix so COR1M current value comes from quote metadata/current quote, not the daily-bar close) depends_on: [T2]
- T4 (Refresh CRI cache artifacts and align any affected generated files) depends_on: [T3]
- T5 (Run targeted verification and capture review notes) depends_on: [T3, T4]

### Checklist
- [x] T1 Audit current COR1M sourcing, cache flow, and UI expectations for the mismatch
- [x] T2 Add failing backend and browser regressions for COR1M current-value sourcing
- [x] T3 Implement CRI scan fix so COR1M current value comes from quote metadata/current quote, not the daily-bar close
- [x] T4 Refresh CRI cache artifacts and align any affected generated files
- [x] T5 Run targeted verification and capture review notes

### Review
- Confirmed red phase: `pytest scripts/tests/test_cri_scan.py -q` failed on the new `current_override` and `current_quotes` expectations before the fix.
- Backend fix now separates COR1M current-level sourcing from historical bars: `run_analysis()` accepts `current_quotes`, `cor1m_level_and_change()` supports a current override, and the last history row is patched to the selected current quote.
- Added current-quote source selection for COR1M in `scripts/cri_scan.py`: prefer IB current quote when available, compare against Yahoo chart metadata, and fall back to Yahoo when IB diverges materially or is unavailable.
- Refreshed the served CRI artifacts by correcting [data/cri.json](/Users/joemccann/dev/apps/finance/radon/data/cri.json) and writing a clean latest scheduled snapshot at [cri-2026-03-10T18-45.json](/Users/joemccann/dev/apps/finance/radon/data/cri_scheduled/cri-2026-03-10T18-45.json).
- Verified `pytest scripts/tests/test_cri_scan.py -q` passes with `59/59`.
- Verified `npx playwright test e2e/regime-cor1m.spec.ts e2e/regime-cor1m-live-route.spec.ts` passes with `3/3`, including an unmocked `/regime` browser check against the live route.
- Verified the running dev server returns the corrected payload via `http://localhost:3000/api/regime`: `cor1m: 28.97`, `cor1m_5d_change: 6.88`, `cri.score: 25.4`.

## Session: CRI COR1M Refactor (2026-03-10)

### Dependency Graph
- T1 (Audit CRI data flow, frontend consumers, and repo-wide documentation references) depends_on: []
- T2 (Define COR1M fetch/calculation contract and write failing backend tests) depends_on: [T1]
- T3 (Implement backend CRI refactor from sector-ETF correlation to COR1M implied correlation) depends_on: [T2]
- T4 (Refactor frontend `/regime` consumers, labels, and API typing for COR1M) depends_on: [T3]
- T5 (Add or update browser E2E coverage for COR1M presentation and behavior) depends_on: [T4]
- T6 (Update all relevant docs, strategy references, site copy, and command/help surfaces) depends_on: [T1, T3, T4]
- T7 (Run verification, capture review notes, and summarize residual risks) depends_on: [T5, T6]

### Checklist
- [x] T1 Audit CRI data flow, frontend consumers, and repo-wide documentation references
- [x] T2 Define COR1M fetch/calculation contract and write failing backend tests
- [x] T3 Implement backend CRI refactor from sector-ETF correlation to COR1M implied correlation
- [x] T4 Refactor frontend `/regime` consumers, labels, and API typing for COR1M
- [x] T5 Add or update browser E2E coverage for COR1M presentation and behavior
- [x] T6 Update all relevant docs, strategy references, site copy, and command/help surfaces
- [x] T7 Run verification, capture review notes, and summarize residual risks

### Review
- Confirmed red/green TDD: `pytest scripts/tests/test_cri_scan.py -q` failed on missing `cor1m_level_and_change`, then passed with 51/51 after the refactor.
- Confirmed browser automation against the running dev server: `npx playwright test e2e/regime-cor1m.spec.ts e2e/regime-market-closed-eod.spec.ts` passed with 9/9.
- Confirmed targeted `/regime` source-inspection tests pass after updating them to ESM-safe path handling: `npx tsx --test tests/regime-market-closed-values.test.ts tests/regime-market-closed.test.ts tests/regime-spy-subscription.test.ts`.
- Confirmed `cd web && npm run build` passes after the COR1M subscription and UI contract changes.
- Refreshed CRI cache artifacts with live COR1M data: `python3 scripts/cri_scan.py --json > data/cri.json` and wrote `data/cri_scheduled/cri-2026-03-10T17-21.json`.
- Residual risk: older scheduled CRI cache files still exist historically in `data/cri_scheduled/`; the app now has a fresh COR1M-shaped file, so current reads are correct.

## Dependency Graph
- T1 (Scope Alignment) -> T2 (Next.js App Bootstrap) -> T3 (Backend Command Runtime) -> T4 (Conversational Chat UI) -> T5 (Technical Minimalist Design) -> T6 (Verification + Docs)

## Tasks
- [x] T1: Finalize feature scope and command contract
  - depends_on: []
  - Success criteria: command surface includes scan, discover, evaluate, portfolio, journal, and watchlist management.
  - Notes: Keep local `.pi` command/prompt behavior as source-of-truth while exposing chat-friendly actions.

- [x] T2: Scaffold Next.js web application in `web/`
  - depends_on: [T1]
  - Success criteria:
    - New Next.js app builds in isolation.
    - Route entry, root layout, and global styles are in place.
    - `npm run dev` can start without touching CLI-only files.

- [x] T3: Implement command execution API layer
  - depends_on: [T2]
  - Success criteria:
    - `/api/chat` and runtime helpers can invoke `scanner.py`, `discover.py`, `fetch_flow.py`, `fetch_ticker.py`, `fetch_options.py`.
    - `watchlist.json` can be read/updated via chat-safe helper actions.
    - `portfolio.json` and `trade_log.json` are read and formatted for UI.
    - API responses include parseable payload + human-readable summary.

- [x] T4: Build conversational chat experience
  - depends_on: [T3]
  - Success criteria:
    - Message loop supports user prompts and slash-command style actions.
    - Quick action buttons trigger scan/evaluate/watchlist/portfolio/journal flows.
    - Command results render consistently with optional JSON details.

- [x] T5: Apply Technical Minimalist styling
  - depends_on: [T4]
  - Success criteria:
    - Palette is Paper/Forest/Grid with Coral/Mint/Gold accents.
    - Space Grotesk/JetBrains Mono usage for headers and metadata labels.
    - Flat surfaces, 1px/2px radius only, 0/2px border-radius.
    - Image hover behavior uses luminosity blend and grayscale-like idle state.

- [x] T6: Verify and document completion
  - depends_on: [T5]
  - Success criteria:
    - `cd web && npm run build` passes.
    - Manual route/API checks for each major command.
    - `README.md` notes run commands and usage workflow.

## Progress
- [x] Plan drafted
- [x] Discovery complete
- [x] Analysis complete
- [x] Implementation complete
- [x] Report delivered

## Review
- Completed API route checks for `/help`, scan/discover/evaluate/watchlist/portfolio/journal command wiring through `web/src/lib/pi-shell.ts`.
- Verified `cd web && npm run build` and `npm run lint`.
- Verified runtime endpoint by starting `next dev` and POSTing to `/api/chat`.

# Internals Skew Closed-Market Guard (2026-03-21)

Dependency graph:
- T1 -> T2
- T1 -> T3
- T2 -> T4
- T3 -> T4

- [x] T1 Review the `/api/internals` skew-history trigger path and confirm the closed-market failure boundary. `depends_on: []`
- [x] T2 Patch `/api/internals` to skip long-range skew fetches when the market is closed or it is a non-trading day. `depends_on: [T1]`
- [x] T3 Keep the existing internals response shape stable when the closed-market guard is active. `depends_on: [T1]`
- [x] T4 Review the route behavior and document the result. `depends_on: [T2, T3]`

Review:
- `/api/internals` no longer attempts the UW long-range skew fetch when `isMarketOpenNow()` is false.
- The route now serves the newest shared long-range skew snapshot from `data/cache/internals_skew_history_*.json` during closed sessions and falls back to that same cache on fetch errors.
- The response shape for the internals charts remains unchanged because the cached points are normalized into the existing `MenthorqSkewHistoryPoint[]` structure.

---

## Session: Repo Architecture Exploration (2026-03-01)

### Dependency Graph
- T1 (Inventory repository structure and identify candidate entrypoints) depends_on: []
- T2 (Inspect script orchestration and command flow across docs + code) depends_on: [T1]
- T3 (Inspect data/config files and runtime state flow) depends_on: [T1]
- T4 (Inspect `.pi` integration points, prompts, and extension hook invocation paths) depends_on: [T1]
- T5 (Synthesize architecture map + command flow + `.pi` hook invocation narrative) depends_on: [T2, T3, T4]
- T6 (Verification pass and document review notes) depends_on: [T5]

### Checklist
- [x] T1 Inventory repository structure and identify candidate entrypoints
- [x] T2 Inspect script orchestration and command flow across docs + code
- [x] T3 Inspect data/config files and runtime state flow
- [x] T4 Inspect `.pi` integration points, prompts, and extension hook invocation paths
- [x] T5 Synthesize architecture map + command flow + `.pi` hook invocation narrative
- [x] T6 Verification pass and document review notes

### Review
- Verified script entrypoint CLIs: `fetch_flow.py`, `discover.py`, `scanner.py`, `kelly.py`, `fetch_options.py`; validated `fetch_ticker.py` usage path.
- Validated JSON data files parse cleanly via `python3 -m json.tool`.
- Confirmed `.pi` hook points: `before_agent_start` and `session_start` in startup extension; `kelly_calc` tool + `positions` command in trading extension.
- Confirmed prompt templates exist for `scan`, `evaluate`, `portfolio`, `journal`; no dedicated `.pi/prompts/discover.md` found.
- Confirmed existing web UI example (`packages/web-ui/example`) is Vite-based and browser-focused.

---

## Session: Upstream `pi-mono` Harness Exploration (2026-03-01)

### Dependency Graph
- T1 (Clone upstream and inventory harness/core packages) depends_on: []
- T2 (Trace runtime flow: CLI main -> session creation -> agent loop) depends_on: [T1]
- T3 (Trace agent/resource/extension definition and load model) depends_on: [T1]
- T4 (Trace configuration model: settings/auth/models/resources paths + precedence) depends_on: [T1]
- T5 (Trace invocation surfaces: CLI modes, print/json, RPC, SDK client API) depends_on: [T2, T3, T4]
- T6 (Synthesize findings and validate references) depends_on: [T5]

### Checklist
- [x] T1 Clone upstream and inventory harness/core packages
- [x] T2 Trace runtime flow: CLI main -> session creation -> agent loop
- [x] T3 Trace agent/resource/extension definition and load model
- [x] T4 Trace configuration model: settings/auth/models/resources paths + precedence
- [x] T5 Trace invocation surfaces: CLI modes, print/json, RPC, SDK client API
- [x] T6 Synthesize findings and validate references

### Review
- Verified bootstrap and mode dispatch in `packages/coding-agent/src/main.ts` and `src/cli/args.ts`, including two-pass arg parsing for extension flags.
- Verified session/runtime assembly in `createAgentSession` and `AgentSession._buildRuntime` (tools, system prompt, extension runner binding).
- Verified core loop semantics in `packages/agent/src/agent.ts` and `src/agent-loop.ts` (steering/follow-up queues, tool call execution, turn boundaries).
- Verified configuration layering and paths in `config.ts`, `settings-manager.ts`, `model-registry.ts`, `resource-loader.ts`, and `package-manager.ts`.
- Verified workflow invocation surfaces across `print-mode.ts`, `rpc-types.ts`, `rpc-mode.ts`, and `rpc-client.ts`, plus SDK exports in `src/index.ts`.

---

## Session: Real-Time Option Contract Price Subscriptions (2026-03-03)

### Problem
IB realtime WS server only subscribed to stock contracts (`ib.contract.stock()`), so options positions (bear put spreads, bull call spreads, short puts) never received real-time price updates.

### Solution
Composite key scheme: stock prices keyed by ticker (`"AAPL"`), option prices by `{SYMBOL}_{YYYYMMDD}_{STRIKE}_{RIGHT}` (e.g., `"EWY_20260417_42_P"`). Both coexist in the same `Record<string, PriceData>` map.

### Checklist
- [x] Add shared types & utilities (`web/lib/pricesProtocol.ts`): `OptionContract`, `optionKey()`, `contractsKey()`, `portfolioLegToContract()`
- [x] Update IB server (`scripts/ib_realtime_server.js`): `normalizeContracts()`, refactored `startLiveSubscription(key, ibContract)`, option subscribe handler via `ib.contract.option()`
- [x] Update client hook (`web/lib/usePrices.ts`): `contracts` option, `contractHash` memoization, contracts in subscribe message
- [x] Extract contracts from portfolio (`web/components/WorkspaceShell.tsx`): `portfolioContracts` useMemo iterates non-Stock legs
- [x] Display real-time option prices (`web/components/WorkspaceSections.tsx`): `legPriceKey()`, real-time MV/daily-change for options, `LegRow` with WS prices

### Files Modified
- `web/lib/pricesProtocol.ts`
- `scripts/ib_realtime_server.js`
- `web/lib/usePrices.ts`
- `web/components/WorkspaceShell.tsx`
- `web/components/WorkspaceSections.tsx`

### Review
- TypeScript compilation passes (no errors in modified files)
- Server syntax check passes (`node --check`)
- Backward compatible: stock subscriptions unchanged, option contracts are additive

---

## Session: MenthorQ CTA Integration (2026-03-07)

### Checklist
- [x] Create `scripts/fetch_menthorq_cta.py` — Playwright login, screenshot, Vision extraction, daily cache
- [x] Integrate MenthorQ data into `scripts/cri_scan.py` — `run_analysis()`, console summary, HTML report section
- [x] Create `scripts/tests/test_menthorq_cta.py` — 20 tests (cache, find, parsing, trading date, CRI shape)
- [x] Update `CLAUDE.md` — command, script, cache file references
- [x] Update `.pi/AGENTS.md` — command, script, data file references
- [x] Update `docs/strategies.md` — MenthorQ section in Strategy 6
- [x] Install Playwright + Chromium + httpx
- [x] Live end-to-end verification — 37 assets, 4 tables, SPX pctl_3m=13 z=-1.56

### Files Created
- `scripts/fetch_menthorq_cta.py`
- `scripts/tests/test_menthorq_cta.py`
- `data/menthorq_cache/cta_2026-03-06.json`

### Files Modified
- `scripts/cri_scan.py`
- `CLAUDE.md`
- `.pi/AGENTS.md`
- `docs/strategies.md`
- `PROGRESS.md`

### Review
- 73/73 tests pass (20 new + 53 existing CRI)
- Live fetch: 42.6s, all 4 tables extracted
- Cache hit: instant on subsequent runs
- CRI scanner gracefully handles missing MenthorQ data (fallback text)

---

## Session: Combo Order Fixes + Leg P&L (2026-03-06)

### Checklist
- [x] Fix ModifyOrderModal BAG price resolution — pass `portfolio`, compute net BID/ASK/LAST from per-leg WS prices
- [x] Fix triplicate executed orders — replace `setInterval` with chained `setTimeout` in cancel/modify polling + dedupe safety net
- [x] Add per-leg P&L in expanded combo rows — `sign × (|MV| − |EC|)` with color coding
- [x] Update CLAUDE.md calculations + price resolution docs

### Files Modified
- `web/components/ModifyOrderModal.tsx`
- `web/components/WorkspaceSections.tsx`
- `web/components/PositionTable.tsx`
- `web/lib/OrderActionsContext.tsx`
- `CLAUDE.md`

### Review
- `tsc --noEmit` — no new type errors
- Orders page: 32 entries (down from 35), no triplicate cancelled rows, combo last prices resolved
- Portfolio page: AAOI expanded legs show per-leg P&L summing to position-level total

---

## Session: Remote IBC Control + Cloud Hosting Research (2026-03-10)

### Dependency Graph
- T1 (Capture current local IBC architecture and control points) depends_on: []
- T2 (Research secure remote-control options for local IBC from iPhone) depends_on: [T1]
- T3 (Research cloud-hosted IBC deployment options and constraints) depends_on: [T1]
- T4 (Compare options and select recommendation ordering) depends_on: [T2, T3]
- T5 (Document implementation plan and review notes) depends_on: [T4]

### Checklist
- [x] T1 Capture current local IBC architecture and control points
- [x] T2 Research secure remote-control options for local IBC from iPhone
- [x] T3 Research cloud-hosted IBC deployment options and constraints
- [x] T4 Compare options and select recommendation ordering
- [x] T5 Document implementation plan and review notes

### Review
- Verified the active local service is the machine-global `local.ibc-gateway` LaunchAgent, not the legacy repo-local `com.radon.ibc-gateway` path.
- Verified live control wrappers exist at `~/ibc/bin/start-secure-ibc-service.sh`, `stop-secure-ibc-service.sh`, `restart-secure-ibc-service.sh`, and `status-secure-ibc-service.sh`; each is a thin `launchctl` wrapper against `gui/$UID/local.ibc-gateway`.
- Verified the active runner `~/ibc/bin/run-secure-ibc-gateway.sh` loads credentials from macOS Keychain, writes a temporary `0600` runtime IBC config, and launches `ibcstart.sh` in Gateway mode.
- Best local remote-control recommendation: keep IBC on the Mac, add a private control plane over Tailscale, and trigger only the existing wrapper scripts remotely. Best UX variant is a small status/start/stop web endpoint exposed via Tailscale Serve and locked to the tailnet; lowest-effort variant is SSH over Tailscale from iPhone.
- Best cloud recommendation: move to a dedicated private Linux VM running IB Gateway + IBC, accessed only over VPN/private network. This remains operationally viable but outside IBKR's supported headless model, so weekly Sunday re-auth and strict network isolation remain mandatory.
- Secondary cloud options: QuantRocket if a broader managed IB stack is desirable; community Docker images only for operators already comfortable with containers, persistence, and private networking.

---

## Session: IBC Research HTML Report (2026-03-10)

### Dependency Graph
- T1 (Load brand and report template context) depends_on: []
- T2 (Generate standalone HTML report artifact) depends_on: [T1]
- T3 (Verify report content and open locally) depends_on: [T2]

### Checklist
- [x] T1 Load brand and report template context
- [x] T2 Generate standalone HTML report artifact
- [x] T3 Verify report content and open locally

### Review
- Created `reports/ibc-remote-control-and-cloud-options-2026-03-10.html` with Radon-aligned colors, typography, panel layout, recommendation tables, implementation plan, and linked source references.
- Included current local machine observations in the report: `local.ibc-gateway` running and LaunchAgent modified timestamp `2026-03-10 08:04 AM PDT`.
- Verified key content markers via `rg`.
- Opened the report locally with `open`.

---

## Session: Phase 1 Remote IBC Access Implementation (2026-03-10)

### Dependency Graph
- T1 (Inspect current Tailscale, SSH, and IBC control state on the Mac) depends_on: []
- T2 (Implement repo-local Phase 1 helper tooling around the existing secure IBC wrappers) depends_on: [T1]
- T3 (Persist future-facing markdown documentation referencing the HTML report) depends_on: [T2]
- T4 (Validate helper behavior and capture remaining manual system steps) depends_on: [T2, T3]

### Checklist
- [x] T1 Inspect current Tailscale, SSH, and IBC control state on the Mac
- [x] T2 Implement repo-local Phase 1 helper tooling around the existing secure IBC wrappers
- [x] T3 Persist future-facing markdown documentation referencing the HTML report
- [x] T4 Validate helper behavior and capture remaining manual system steps

### Review
- Verified the current Phase 1 shape uses standard macOS SSH over the Tailscale network, not Tailscale SSH server mode, because this Mac has the GUI app variant of Tailscale installed.
- Verified the canonical IBC service surface is the secure machine-local wrapper set in `~/ibc/bin/`; repo automation is documented as a convenience wrapper only.
- Added `scripts/ibc_remote_control.sh` as a repo-local helper for `check`, `tailscale-status`, `tailscale-login`, `ibc-status`, `ibc-start`, `ibc-stop`, `ibc-restart`, and `remote-help`.
- Added `docs/ibc-remote-access.md` as the durable markdown reference and linked it to `reports/ibc-remote-control-and-cloud-options-2026-03-10.html`.
- Added `tasks/lessons.md` to capture the correction that the secure machine-local `~/ibc/bin/*secure-ibc-service.sh` commands are the canonical service surface.
- Validation:
  - `./scripts/ibc_remote_control.sh check` confirmed Tailscale is connected, macOS SSH is enabled, and `local.ibc-gateway` is running.
  - `./scripts/ibc_remote_control.sh ibc-status` confirmed the secure `local.ibc-gateway` LaunchAgent is running.
  - `./scripts/ibc_remote_control.sh remote-help` prints both direct secure-service SSH commands and the optional repo convenience wrapper commands.
  - `nc -zv 127.0.0.1 22` confirmed the SSH listener is active.
  - Public-key SSH is not configured yet because `~/.ssh/authorized_keys` is absent; Phase 1 will therefore be password-based from the iPhone unless a client key is added later.
- Remaining optional step:
  - Add a dedicated SSH public key for the iPhone client if you want key-based login instead of password auth.

---

## Session: Phase 1 IBC Docs Refresh + Publish (2026-03-10)

### Dependency Graph
- T1 (Inventory current Phase 1 files and documentation touchpoints) depends_on: []
- T2 (Update canonical docs with the working SSH-over-Tailscale flow and dependencies) depends_on: [T1]
- T3 (Validate docs and helper behavior against the live machine state) depends_on: [T2]
- T4 (Commit only the relevant files) depends_on: [T3]
- T5 (Push the commit to the current branch remote) depends_on: [T4]

### Checklist
- [x] T1 Inventory current Phase 1 files and documentation touchpoints
- [x] T2 Update canonical docs with the working SSH-over-Tailscale flow and dependencies
- [x] T3 Validate docs and helper behavior against the live machine state
- [x] T4 Commit only the relevant files
- [x] T5 Push the commit to the current branch remote

### Review
- Reworked `README.md` to match the requested structure from the shared review: cleaner summary, badges, explicit Inputs/Processing/Outputs, three-gate framework, strategy matrix, architecture diagram, grouped commands, simplified data-source/testing sections, example workflow, and the Phase 1 remote IBC dependency block.
- Updated the authoritative IBC docs in `CLAUDE.md`, `docs/implement.md`, and `docs/ib_tws_api.md` so the secure machine-local `~/ibc/bin/*secure-ibc-service.sh` commands are the primary surface and the old `scripts/setup_ibc.sh` flow is clearly legacy.
- Preserved and linked the Phase 1 remote-access runbook in `docs/ibc-remote-access.md`, including the concrete dependencies required for iPhone control:
  - `Tailscale.app` on the Mac
  - Tailscale on the iPhone, connected to the same tailnet
  - macOS `Remote Login`
  - iPhone SSH client such as Termius, Blink Shell, or Prompt
  - Optional SSH public key in `~/.ssh/authorized_keys` for key-based login
- Validation:
  - `bash -n scripts/ibc_remote_control.sh` passed.
  - `./scripts/ibc_remote_control.sh remote-help` prints the direct secure-service SSH commands and optional helper commands.
  - User confirmed iPhone SSH login works in Termius with password auth.
  - Commit: `bf86cc4` (`docs: refresh README and document secure IBC remote access`)
  - Push: `origin/main` updated on `2026-03-10`

---

## Session: README Information Architecture Refresh (2026-03-10)

### Dependency Graph
- T1 (Compare README against the shared rewrite outline and current repo reality) depends_on: []
- T2 (Rewrite README structure and preserve the secure IBC Phase 1 dependencies) depends_on: [T1]
- T3 (Verify the refreshed README still points to the durable runbook and report artifacts) depends_on: [T2]

### Checklist
- [x] T1 Compare README against the shared rewrite outline and current repo reality
- [x] T2 Rewrite README structure and preserve the secure IBC Phase 1 dependencies
- [x] T3 Verify the refreshed README still points to the durable runbook and report artifacts

### Review
- Reworked `README.md` around a clearer public-facing hierarchy: summary, What Radon Does, trade validation framework, strategies, architecture, quick start, terminal, grouped commands, project structure, data sources, testing, and services.
- Preserved the Phase 1 secure local IBC path in the README Services section, including the concrete dependencies for Tailscale, macOS Remote Login, and iPhone SSH clients.
- Added direct references from the README to the durable markdown runbook `docs/ibc-remote-access.md` and the preserved HTML report `reports/ibc-remote-control-and-cloud-options-2026-03-10.html`.
- Verification:
  - `rg -n "What Radon Does|Trade Validation Framework|System Architecture|Quick Start|Radon Terminal|CLI Commands|Phase 1 Remote IBC Access" README.md`
  - Manual README review against the shared outline confirmed the requested structural sections are present.

---

## Session: IBC Full Rollout Plan (2026-03-10)

### Dependency Graph
- T1 (Preserve the research baseline, report, and canonical secure local service surface) depends_on: []
- T2 (Complete Phase 1 local SSH-over-Tailscale access and documentation) depends_on: [T1]
- T3 (Harden local remote access with key-based SSH and tighter SSH policy) depends_on: [T2]
- T4 (Build Phase 2 private web controller over Tailscale for start/stop/status/restart) depends_on: [T2]
- T5 (Add local resilience: health checks, alerting, and away-from-desk power/sleep policy) depends_on: [T3, T4]
- T6 (Stand up a private cloud IBC proof of concept on a Linux VM) depends_on: [T1]
- T7 (Validate cloud persistence, secrets, restart behavior, and Sunday re-auth runbook) depends_on: [T6]
- T8 (Decide primary operating model and cut over to the preferred steady-state path) depends_on: [T5, T7]

### Checklist
- [x] T1 Preserve the research baseline, report, and canonical secure local service surface
  - Success criteria:
    - `reports/ibc-remote-control-and-cloud-options-2026-03-10.html` remains the durable comparison artifact.
    - The canonical service surface is documented everywhere as `~/ibc/bin/*secure-ibc-service.sh`.
- [x] T2 Complete Phase 1 local SSH-over-Tailscale access and documentation
  - Success criteria:
    - iPhone can connect to the Mac over Tailscale and run the secure IBC commands.
    - README and runbook document the dependencies and direct command flow.
- [ ] T3 Harden local remote access with key-based SSH and tighter SSH policy
  - depends_on: [T2]
  - Success criteria:
    - iPhone SSH client uses a dedicated key instead of password auth.
    - `~/.ssh/authorized_keys` contains the intended client key only.
    - SSH config is reviewed so remote access remains limited to the Tailscale path and expected auth methods.
- [ ] T4 Build Phase 2 private web controller over Tailscale for start/stop/status/restart
  - depends_on: [T2]
  - Success criteria:
    - A minimal private controller runs only on the Mac.
    - It exposes `status`, `start`, `stop`, and `restart` for the secure local IBC service.
    - Access is restricted to the tailnet and does not expose IB API or IBC command ports publicly.
- [ ] T5 Add local resilience: health checks, alerting, and away-from-desk power/sleep policy
  - depends_on: [T3, T4]
  - Success criteria:
    - There is an operator-visible health signal for IBC reachability and launchd state.
    - Failure notifications or a simple alert path exist for the local service.
    - The machine’s sleep/power behavior is documented so remote control is reliable while away.
- [ ] T6 Stand up a private cloud IBC proof of concept on a Linux VM
  - depends_on: [T1]
  - Success criteria:
    - A private Linux VM runs IB Gateway + IBC with no public IB or VNC exposure.
    - Access is limited to Tailscale or equivalent private networking.
    - Secrets and persistent Gateway state are stored outside ad hoc local files.
- [ ] T7 Validate cloud persistence, secrets, restart behavior, and Sunday re-auth runbook
  - depends_on: [T6]
  - Success criteria:
    - The VM survives restart/redeploy without losing required Gateway/IBC state.
    - Weekly Sunday re-auth and recovery steps are documented and tested.
    - Burn-in covers reconnects, restart cadence, and failure handling for at least one trading week.
- [ ] T8 Decide primary operating model and cut over to the preferred steady-state path
  - depends_on: [T5, T7]
  - Success criteria:
    - There is an explicit decision between Mac-hosted primary and cloud-hosted primary.
    - The non-primary path is documented as fallback.
    - Final operator runbooks point to one canonical daily-use workflow.

### Review
- This session converts the prior research into an explicit end-to-end rollout instead of stopping at Phase 1.
- Current completed state:
  - Phase 1 local SSH-over-Tailscale access is working from the iPhone.
  - The secure machine-local `~/ibc/bin/*secure-ibc-service.sh` wrappers are the canonical service surface.
  - The durable research and reference artifacts already exist in `reports/ibc-remote-control-and-cloud-options-2026-03-10.html` and `docs/ibc-remote-access.md`.
- Remaining delivery is now split cleanly into two tracks:
  - Local track: SSH hardening, private web control plane, operational resilience.
  - Cloud track: private VM proof of concept, burn-in, and cutover decision.

---

## Session: Planned IBC Multi-Phase Rollout (2026-03-10)

### Dependency Graph
- T1 (Phase 2 local hardening: key-based SSH, access policy, and reachability decision) depends_on: []
- T2 (Phase 3 private tailnet web controller for status/start/stop/restart and health) depends_on: [T1]
- T3 (Phase 4 cloud pilot: private Linux VM running IB Gateway + IBC with persistent state and private access) depends_on: [T1]
- T4 (Phase 5 cloud burn-in: restart/reconnect validation, Sunday re-auth runbook, and monitoring) depends_on: [T3]
- T5 (Phase 6 deployment decision and cutover plan across local versus cloud primary) depends_on: [T2, T4]

### Checklist
- [ ] T1 Phase 2 local hardening: key-based SSH, access policy, and reachability decision
  - depends_on: []
  - Success criteria:
    - A dedicated iPhone SSH public key is installed in `~/.ssh/authorized_keys`.
    - The preferred auth mode and remote-access policy are documented for the Mac.
    - A reachability policy is chosen and documented: keep-awake, wake relay, or accepted sleep limitation.

- [ ] T2 Phase 3 private tailnet web controller for status/start/stop/restart and health
  - depends_on: [T1]
  - Success criteria:
    - A private controller is reachable only from the tailnet.
    - The iPhone flow supports `status`, `start`, `stop`, and `restart` without shell interaction.
    - Basic health, recent logs, and failure feedback are visible remotely.

- [ ] T3 Phase 4 cloud pilot: private Linux VM running IB Gateway + IBC with persistent state and private access
  - depends_on: [T1]
  - Success criteria:
    - A private VM is provisioned with IB Gateway + IBC, Tailscale, persisted config/state, and secrets handling.
    - No IB API, IBC, VNC, or controller ports are exposed publicly.
    - Recovery access is defined for the VM when Gateway needs manual intervention.

- [ ] T4 Phase 5 cloud burn-in: restart/reconnect validation, Sunday re-auth runbook, and monitoring
  - depends_on: [T3]
  - Success criteria:
    - The cloud pilot survives a multi-day burn-in with successful reconnect behavior.
    - The Sunday re-auth and failure-recovery runbook is documented and validated.
    - Monitoring and log collection are sufficient to detect disconnects or stuck sessions.

- [ ] T5 Phase 6 deployment decision and cutover plan across local versus cloud primary
  - depends_on: [T2, T4]
  - Success criteria:
    - A primary deployment model is chosen: local Mac with private controller, cloud VM, or cloud pilot only.
    - Rollback and failover steps are documented for whichever model is selected.
    - The durable docs and future runbooks are updated to reflect the chosen operating model.

### Review
- Phase 1 is complete and operational: password-based macOS SSH over Tailscale to the secure `~/ibc/bin/*secure-ibc-service.sh` wrappers.
- The next local step is hardening, not replacing, the current path: add key-based SSH and make the Mac reachability policy explicit.
- The private web controller is the best Phase 3 UX improvement because it keeps the canonical service surface intact while removing the need for shell interaction on the phone.
- The cloud track should be treated as a pilot until a burn-in validates restart behavior, Sunday re-auth handling, and recovery procedures.
- If the cloud pilot remains operationally weaker than the local Mac because of IBKR auth friction, keep the local deployment as primary and treat cloud as a secondary or recovery path.

---

## Session: Fix Risk-Reversal Chain Entry Net Credit And Combo Direction (2026-03-18)

### Dependency Graph
- T1 Trace the chain risk-reversal path from builder through Next route, FastAPI bridge, and IB combo placement depends_on: []
- T2 Add regressions for combo entry action semantics and stale net-price carryover when a second leg is added depends_on: [T1]
- T3 Patch the chain builder so combo entries preserve leg direction and reset stale manual net pricing when the structure changes depends_on: [T2]
- T4 Verify focused unit/browser coverage and a live Chrome CDP repro on the running chain page depends_on: [T3]

### Checklist
- [x] T1 Trace the chain risk-reversal path from builder through Next route, FastAPI bridge, and IB combo placement
  - depends_on: []
  - Success criteria:
    - The incorrect combo envelope action is traced from `OptionsChainTab.tsx` into `/api/orders/place`, `scripts/api/server.py`, and `scripts/ib_place_order.py`.
    - The price-path issue is isolated to chain-builder state instead of the backend placement stack.

- [x] T2 Add regressions for combo entry action semantics and stale net-price carryover when a second leg is added
  - depends_on: [T1]
  - Success criteria:
    - A focused unit regression fails when combo entries do not preserve BUY envelope semantics.
    - A Playwright chain regression fails when a stale single-leg limit price survives after the second leg is added.

- [x] T3 Patch the chain builder so combo entries preserve leg direction and reset stale manual net pricing when the structure changes
  - depends_on: [T2]
  - Success criteria:
    - Adding a second combo leg clears the stale manual net price override and re-bases the limit field to the combo quote.
    - The combo payload always sends a BUY envelope while preserving per-leg BUY/SELL intent.

- [x] T4 Verify focused unit/browser coverage and a live Chrome CDP repro on the running chain page
  - depends_on: [T3]
  - Success criteria:
    - `web/tests/chain-combo-ratio.test.ts` passes.
    - `web/e2e/ticker-search-chain.spec.ts` passes for the ratio combo and risk-reversal regressions.
    - A live CDP-driven EWY chain check shows the limit field snapping from a forced `8.88` stale value back to the combo `MID`.

### Review
- Root cause 1 was in the frontend chain builder: `OptionsChainTab.tsx` derived combo `action` from debit/credit, which sent `SELL` BAG envelopes for net-credit risk reversals and let IB reverse the legs on placement.
- Root cause 2 was also frontend state: the builder kept a stale manual net-price override when the leg structure changed from one leg to a combo, so the top-level limit field could stay pinned to the first leg instead of the recomputed combo quote.
- The backend and provider path were not the bug: `/api/orders/place`, `scripts/api/server.py`, and `scripts/ib_place_order.py` passed the combo payload through faithfully, and IB’s BAG semantics were already encoded correctly downstream.
- Regression coverage now includes a unit contract for combo entry action plus a Playwright browser flow that forces a stale single-leg price, adds the second leg, and asserts both the corrected combo quote and the BUY combo envelope.
- Live Chrome CDP verification on the running EWY chain page confirmed the UI reset: after forcing the limit field to `8.88`, adding the second leg changed the live builder to `MID 9.45` with `limitValue: 9.45`.

---

## Session: Propagate Combo Entry Guardrails To Repo Instructions (2026-03-18)

### Dependency Graph
- T1 Identify every canonical instruction and memory surface that should carry the combo-entry rule depends_on: []
- T2 Update repo instructions and memory artifacts with explicit BAG-action and stale-net-price guardrails depends_on: [T1]
- T3 Commit the scoped docs-only change and push it to origin/main depends_on: [T2]

### Checklist
- [x] T1 Identify every canonical instruction and memory surface that should carry the combo-entry rule
  - depends_on: []
  - Success criteria:
    - Canonical repo-level instruction files are identified.
    - Durable markdown and structured memory surfaces are identified.

- [x] T2 Update repo instructions and memory artifacts with explicit BAG-action and stale-net-price guardrails
  - depends_on: [T1]
  - Success criteria:
    - `AGENTS.md`, `.pi/AGENTS.md`, and `CLAUDE.md` all carry the same combo-entry guardrails.
    - `tasks/lessons.md` and a structured memory fact capture the regression-prevention rule.

- [x] T3 Commit the scoped docs-only change and push it to origin/main
  - depends_on: [T2]
  - Success criteria:
    - The docs-only change is committed with a scoped message.
    - The commit is pushed to `origin/main`.

### Review
- This pass codifies the exact failure mode that caused the regression: using net debit/credit to choose BAG `Order.action`, and carrying stale manual net-price state across structure changes.
- The rule now exists in both operator-facing markdown instructions and machine-readable memory so future agents can pick it up from either surface.

---

## Session: Port Claude Guardrails Into Codex Instructions (2026-03-18)

### Dependency Graph
- T1 Compare Claude-specific guardrails against Codex instruction surfaces and identify missing rules depends_on: []
- T2 Port missing browser-verification and coverage rules into Codex-native docs depends_on: [T1]
- T3 Verify the Codex docs now contain the intended rules and record any runtime capability gaps depends_on: [T2]

### Checklist
- [x] T1 Compare Claude-specific guardrails against Codex instruction surfaces and identify missing rules
  - depends_on: []
  - Success criteria:
    - The gaps between `CLAUDE.md` and `AGENTS.md`/`.pi/AGENTS.md` are explicit.
    - Any capability mismatch is identified before patching docs.

- [x] T2 Port missing browser-verification and coverage rules into Codex-native docs
  - depends_on: [T1]
  - Success criteria:
    - Codex instructions include UI browser verification guidance in Codex-native wording.
    - Codex instructions include the touched-surface coverage expectation without breaking existing AGENTS formatting.
    - Structured memory captures the Codex parity rule.

- [x] T3 Verify the Codex docs now contain the intended rules and record any runtime capability gaps
  - depends_on: [T2]
  - Success criteria:
    - Repo grep confirms the new Codex instruction sections exist.
    - The documented capability gap, if any, is explicit.

### Review
- The combo-order guardrails were already ported to Codex surfaces.
- The actual parity gap was Claude’s stronger browser-verification and coverage wording, which is now mirrored in Codex-native form under `AGENTS.md`/`.pi/AGENTS.md`.
- The only meaningful capability caveat is `chrome-cdp` availability: Codex supports it in this session, but unlike markdown policy, skill availability is runtime-dependent, so the Codex docs now explicitly require Playwright fallback instead of assuming `chrome-cdp` always exists.

## 2026-03-18 — modify-order resting quote overlay

Dependency graph
- T1 root-cause trace (`depends_on: []`)
- T2 add failing regressions (`depends_on: [T1]`)
- T3 overlay resting limit into modify quote model (`depends_on: [T2]`)
- T4 verify in tests and chrome-cdp (`depends_on: [T3]`)

- [x] T1 Trace `/orders` modify telemetry from IB quote stream and open-order sync to the modal
- [x] T2 Add regression coverage for resting sell/buy limits in the modify quote model
- [x] T3 Fix modify modal telemetry so it reflects the actionable best bid/ask including the resting order
- [x] T4 Verify focused unit, Playwright, and live browser behavior; record review and lessons

### Review
- Root cause: `/api/orders` carries the resting limit price, while IB live quotes arrive separately through the WebSocket price stream; `ModifyOrderModal` displayed only the raw streamed bid/ask and never overlaid the user’s resting order onto the shown book.
- Fix: `web/lib/modifyOrderQuote.ts` now applies the resting order to the effective bid/ask model, and `ModifyOrderModal` uses that adjusted quote for telemetry and reference-price actions.
- Verification: `npx vitest run web/tests/modify-order-quote.test.ts`; `cd web && npx playwright test e2e/modify-order-resting-limit.spec.ts e2e/modify-order-spread-telemetry.spec.ts --config playwright.no-server.config.ts`; Chrome CDP verification on a mocked `/orders` page confirmed a sell order at `$5.05` renders `ASK $5.05` when the raw streamed ask is `$5.10`.

## 2026-03-18 — combo open-order modify flow

Dependency graph
- T1 trace combo row modify path (`depends_on: []`)
- T2 add failing regressions (`depends_on: [T1]`)
- T3 implement combo modify fix (`depends_on: [T2]`)
- T4 verify tests and chrome-cdp (`depends_on: [T3]`)

- [x] T1 Trace combo open-order data from backend/provider through frontend grouping to identify why combo rows cannot open modify
- [x] T2 Add failing regressions for combo modify availability and payload wiring
- [x] T3 Fix combo rows so modify opens the combo modify view with legs and quantity
- [ ] T4 Verify focused tests and live browser behavior; record review and lessons

### Review
- Root cause: IB open orders for these combos arrive as separate OPT orders, not a single BAG. The frontend groups them into a synthetic combo row in `openOrderCombos.ts`, but `WorkspaceSections.tsx` hard-disabled `MODIFY` because the existing replace flow only knew how to cancel one order before placing a replacement combo.
- Fix: grouped combo rows now synthesize a BAG-style modify target for `ModifyOrderModal`, and `/api/orders/modify` accepts `cancelOrders` so the replacement flow cancels every grouped leg order before placing the new combo.
- Verification: `npx vitest run web/tests/open-order-combo-modify.test.ts web/tests/api-routes-extended.test.ts --testNamePattern "buildGroupedComboModifyTarget|replaces combo orders via cancel then place when replacement payload provided"`; `cd web && npx playwright test e2e/open-order-combo.spec.ts --config playwright.no-server.config.ts`; Chrome CDP mocked-browser verification confirmed the combo-row `MODIFY` button is enabled and opens a leg editor with quantity `10` and strikes `150` / `165`.

---

## Session: Repair Empty Agent Skill Frontmatter (2026-03-21)

### Goal
Restore the 31 skipped skills under `/Users/joemccann/.agents/skills/` by adding the minimal valid YAML frontmatter each empty `SKILL.md` requires, while keeping descriptions aligned with the skill folder intent and any bundled references/evals.

### Dependency Graph
- T1 (Audit the invalid skill folders, confirm the `SKILL.md` files are empty, and derive accurate `name`/`description` metadata from the folder topic plus bundled references/evals) depends_on: []
- T2 (Record the repair plan in `tasks/todo.md` before implementation) depends_on: [T1]
- T3 (Patch all 31 empty `SKILL.md` files with valid YAML frontmatter and no unrelated content changes) depends_on: [T1, T2]
- T4 (Validate that the targeted skills now satisfy the frontmatter requirement and capture review notes) depends_on: [T3]

### Checklist
- [x] T1 Audit the invalid skill folders, confirm the `SKILL.md` files are empty, and derive accurate `name`/`description` metadata from the folder topic plus bundled references/evals
- [x] T2 Record the repair plan in `tasks/todo.md` before implementation
- [x] T3 Patch all 31 empty `SKILL.md` files with valid YAML frontmatter and no unrelated content changes
- [x] T4 Validate that the targeted skills now satisfy the frontmatter requirement and capture review notes

### Review
- Root cause:
  - Every reported target under `/Users/joemccann/.agents/skills/` had a zero-byte `SKILL.md`, so the loader failed immediately on the missing opening `---` and skipped the skill before any bundled references or evals could matter.
- Fix:
  - Replaced each empty `SKILL.md` with minimal valid content: YAML frontmatter containing `name` and `description`, plus a short markdown body so the files are no longer metadata-only stubs.
  - Kept the change tightly scoped to the 31 reported skill folders and aligned descriptions with each folder topic and any available bundled references.
- Verification:
  - Ran a direct validation script against the exact 31 reported files to confirm:
    - opening and closing `---` delimiters exist,
    - `name:` exists in frontmatter,
    - `description:` exists in frontmatter,
    - the markdown body is non-empty.
  - Validation result: `VALIDATION_OK 31 skills`.

---

## Session: Document, Commit, and Push Monitor Daemon Market-Hours Fix (2026-03-21)

### Dependency Graph
- T1 (Confirm branch state and record the docs/commit/push plan in `tasks/todo.md`) depends_on: []
- T2 (Re-run repo-level verification so commit context reflects the current working tree) depends_on: [T1]
- T3 (Stage the daemon, tests, docs, and task log changes only; create an atomic commit; push `main` to `origin`) depends_on: [T2]

### Checklist
- [x] T1 Confirm branch state and record the docs/commit/push plan in `tasks/todo.md`
- [ ] T2 Re-run repo-level verification so commit context reflects the current working tree
- [ ] T3 Stage the daemon, tests, docs, and task log changes only; create an atomic commit; push `main` to `origin`

---

## Session: Repair Current Vitest Failures Blocking Commit (2026-03-21)

### Dependency Graph
- T1 (Record the redirected test-fix task and capture the workflow lesson before changing code) depends_on: []
- T2 (Reproduce the current Vitest failures and isolate the owning surfaces) depends_on: [T1]
- T3 (Fix the failing test surfaces with regression-safe code changes) depends_on: [T2]
- T4 (Re-run the affected Vitest targets, then the full Vitest suite, and record the review) depends_on: [T3]

### Checklist
- [x] T1 Record the redirected test-fix task and capture the workflow lesson before changing code
- [x] T2 Reproduce the current Vitest failures and isolate the owning surfaces
- [x] T3 Fix the failing test surfaces with regression-safe code changes
- [x] T4 Re-run the affected Vitest targets, then the full Vitest suite, and record the review

### Review
- Root cause:
  - `web/components/RegimePanel.tsx` still defaulted to the regime share endpoint at runtime, but the implementation no longer contained the literal `shareEndpoint="/api/regime/share"` string that the regression test enforces for shared-report modal wiring.
  - `web/lib/data.ts` had drifted out of sync with the rest of the workspace model: the `performance` section still existed in routes, prompts, descriptions, and tests, but the nav item had been commented out.
- Fix:
  - Restored an explicit default `ShareReportModal` branch in `RegimePanel` so the regime share modal keeps the literal `/api/regime/share` wiring while still allowing an override path when one is provided.
  - Re-enabled the `Performance` nav item in `web/lib/data.ts` so navigation metadata matches the rest of the workspace surface again.
- Verification:
  - `npx vitest run web/tests/regime-share.test.ts web/tests/data.test.ts`
    - Result: `2 passed`
  - `npx vitest run --config vitest.config.ts`
    - Result: `141 passed`, `1361 passed | 8 skipped`

---

## Session: Replace Unconditional FastAPI Test Skips With Runtime Harness (2026-03-22)

### Dependency Graph
- T1 (Inspect the current Vitest and FastAPI setup and record the harness task before edits) depends_on: []
- T2 (Implement a backend-aware order-test harness that reuses a running FastAPI or starts one when needed) depends_on: [T1]
- T3 (Replace unconditional `it.skip(...)` order integration cases with runtime-aware execution) depends_on: [T2]
- T4 (Verify the targeted order tests and the full Vitest suite, then record review notes) depends_on: [T3]

### Checklist
- [x] T1 Inspect the current Vitest and FastAPI setup and record the harness task before edits
- [x] T2 Implement a backend-aware order-test harness that reuses a running FastAPI or starts one when needed
- [x] T3 Replace unconditional `it.skip(...)` order integration cases with runtime-aware execution
- [x] T4 Verify the targeted order tests and the full Vitest suite, then record review notes

### Review
- Root cause:
  - `web/tests/order-e2e.test.ts` permanently used `it.skip(...)` for all FastAPI-backed order cases, so the suite never exercised the accepted integration path.
  - A naive “reuse or spawn localhost:8321” fix would have been unsafe because the current FastAPI startup path can touch IBC/IB state and the live order endpoints can place, modify, or cancel real orders.
- Fix:
  - Added a dedicated Vitest harness in `web/tests/fastapiHarness.ts` that launches an isolated test-mode FastAPI instance on its own local port and points `RADON_API_URL` at it before the order routes are imported.
  - Added `RADON_API_TEST_MODE` handling to `scripts/api/server.py` so test-mode startup skips IB Gateway / pool initialization and the order endpoints return stubbed success payloads instead of touching the live broker.
  - Replaced the eight unconditional `it.skip(...)` cases in `web/tests/order-e2e.test.ts` with runtime-backed tests that execute against the isolated test-mode API and assert `200` success.
- Verification:
  - `npx vitest run web/tests/order-e2e.test.ts`
    - Result: `1 passed`, `25 passed`
  - `npx vitest run --config vitest.config.ts`
    - Result: `141 passed`, `1369 passed`
  - `python3.13 -m py_compile scripts/api/server.py`
    - Result: success

---

## Session: Document, Commit, and Push Daemon and FastAPI Harness Work (2026-03-22)

### Dependency Graph
- T1 (Inspect branch state, identify the relevant doc surfaces, and record the commit/push task) depends_on: []
- T2 (Update the operator and repo docs for the monitor-daemon gate fix and isolated FastAPI order-test harness) depends_on: [T1]
- T3 (Run repo-level verification, stage the intended files only, create an atomic commit, and push `main` to `origin`) depends_on: [T2]

### Checklist
- [x] T1 Inspect branch state, identify the relevant doc surfaces, and record the commit/push task
- [x] T2 Update the operator and repo docs for the monitor-daemon gate fix and isolated FastAPI order-test harness
- [ ] T3 Run repo-level verification, stage the intended files only, create an atomic commit, and push `main` to `origin`

---

## Session: Fix Blank Internals Page When Market Is Closed (2026-03-22)

### Dependency Graph
- T1 (Record the regression task, inspect the current branch/runtime state, and confirm the reproduction path) depends_on: []
- T2 (Add failing regression coverage for the closed-market `/internals` loading path) depends_on: [T1]
- T3 (Fix the hook/page behavior so closed-market internals still perform an initial read without polling) depends_on: [T2]
- T4 (Verify targeted tests and browser rendering, then update review notes and lessons) depends_on: [T3]

### Checklist
- [x] T1 Record the regression task, inspect the current branch/runtime state, and confirm the reproduction path
- [x] T2 Add failing regression coverage for the closed-market `/internals` loading path
- [x] T3 Fix the hook/page behavior so closed-market internals still perform an initial read without polling
- [x] T4 Verify targeted tests and browser rendering, then update review notes and lessons

### Review
- Root cause:
  - `web/lib/useSyncHook.ts` treated `active=false` as “do nothing at all,” not “load once without polling.” On a closed-market mount, `useRegime()` passed `active=false`, so `/internals` never performed its first GET and stayed stuck on `Loading internals...`.
- Fix:
  - `useSyncHook()` now always performs the initial cached GET once per endpoint, even when inactive, while still suppressing retry timers and polling when `active=false`.
  - When a hook mounts inactive and later becomes active, it now performs the first sync at activation time.
  - Added unit coverage in `web/tests/use-sync-hook-inactive-load.test.ts` and browser coverage in `web/e2e/internals-market-closed.spec.ts`.
- Verification:
  - `npx vitest run web/tests/use-sync-hook-inactive-load.test.ts`
    - Result: `1 passed`, `2 passed`
  - `cd web && npx playwright test e2e/internals-market-closed.spec.ts --config playwright.no-server.config.ts`
    - Result: `1 passed`
  - `npx vitest run --config vitest.config.ts`
    - Result: `142 passed`, `1371 passed`
  - Live browser verification against `http://127.0.0.1:3000/internals`
    - Result: page loaded NQ/S&P skew values and both charts after the initial GET instead of hanging on `Loading internals...`

---

## Session: Audit Closed-Market Cached Route Loads (2026-03-22)

### Dependency Graph
- T1 (Inventory every sync-backed route and hook that can mount inactive, then identify the affected surfaces) depends_on: []
- T2 (Add failing regression coverage for the remaining affected closed-market cached-load paths) depends_on: [T1]
- T3 (Patch the affected hooks so inactive routes still perform their first cached read without enabling polling) depends_on: [T2]
- T4 (Verify targeted tests, run the full JS and Python suites, and confirm the affected routes in-browser) depends_on: [T3]

### Checklist
- [ ] T1 Inventory every sync-backed route and hook that can mount inactive, then identify the affected surfaces
- [ ] T2 Add failing regression coverage for the remaining affected closed-market cached-load paths
- [ ] T3 Patch the affected hooks so inactive routes still perform their first cached read without enabling polling
- [ ] T4 Verify targeted tests, run the full JS and Python suites, and confirm the affected routes in-browser
