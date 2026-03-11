# TODO

## Session: Site Docs Checkpoint And SEO Audit (2026-03-11)

### Dependency Graph
- T1 (Audit the current `/site` worktree, documentation gaps, generated artifact noise, and the unresolved non-site WIP that must stay out of the checkpoint commit) depends_on: []
- T2 (Update docs and ignore rules for the landing-page batch, then create a scoped checkpoint commit for finished `/site` work only) depends_on: [T1]
- T3 (Audit the live `/site` SEO surface, identify crawl/indexation/share gaps, and write failing or objective verification around the new SEO contract where practical) depends_on: [T1]
- T4 (Implement the `/site` SEO remediations, generate and open an HTML audit report with recommendations, and document the workflow) depends_on: [T2, T3]
- T5 (Run targeted verification for metadata/routes/build/report generation, then capture review notes and a second scoped commit for the SEO pass) depends_on: [T4]

### Checklist
- [x] T1 Audit the current `/site` worktree, documentation gaps, generated artifact noise, and the unresolved non-site WIP that must stay out of the checkpoint commit
- [x] T2 Update docs and ignore rules for the landing-page batch, then create a scoped checkpoint commit for finished `/site` work only
- [x] T3 Audit the live `/site` SEO surface, identify crawl/indexation/share gaps, and write failing or objective verification around the new SEO contract where practical
- [x] T4 Implement the `/site` SEO remediations, generate and open an HTML audit report with recommendations, and document the workflow
- [x] T5 Run targeted verification for metadata/routes/build/report generation, then capture review notes and a second scoped commit for the SEO pass

### Review
- Created the checkpoint commit `400c7f1` for the finished landing-page refactor only, leaving the unrelated fullscreen/header WIP and local `.claude` manifest changes out of the site history.
- Added shared metadata and structured-data helpers in [site/lib/seo.ts](/Users/joemccann/dev/apps/finance/radon/site/lib/seo.ts) so the marketing site now ships canonical URLs, Open Graph/Twitter cards, robots directives, app identity fields, and JSON-LD for `WebSite`, `Organization`, and `SoftwareApplication`.
- Added crawl and share endpoints in [site/app/robots.ts](/Users/joemccann/dev/apps/finance/radon/site/app/robots.ts), [site/app/sitemap.ts](/Users/joemccann/dev/apps/finance/radon/site/app/sitemap.ts), [site/app/manifest.ts](/Users/joemccann/dev/apps/finance/radon/site/app/manifest.ts), [site/app/opengraph-image.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/opengraph-image.tsx), [site/app/twitter-image.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/twitter-image.tsx), and [site/app/apple-icon.tsx](/Users/joemccann/dev/apps/finance/radon/site/app/apple-icon.tsx).
- Added [site/scripts/seo_audit_report.py](/Users/joemccann/dev/apps/finance/radon/site/scripts/seo_audit_report.py), which builds the site, starts a local production server, audits the rendered HTML plus discovery/image routes, writes [site-seo-audit-2026-03-11.html](/Users/joemccann/dev/apps/finance/radon/reports/site-seo-audit-2026-03-11.html), and opens it by default.
- Updated [README.md](/Users/joemccann/dev/apps/finance/radon/README.md), [site/README.md](/Users/joemccann/dev/apps/finance/radon/site/README.md), [site/.gitignore](/Users/joemccann/dev/apps/finance/radon/site/.gitignore), and [site/eslint.config.mjs](/Users/joemccann/dev/apps/finance/radon/site/eslint.config.mjs) so the SEO workflow is documented and the dedicated `.next-seo-audit/` build directory does not pollute lint or git status.
- Verified `python3 -m py_compile site/scripts/seo_audit_report.py`, `cd site && npm run lint`, and `python3 site/scripts/seo_audit_report.py --no-open`, with the final audit reporting `16 pass, 0 warn, 0 fail`.

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
