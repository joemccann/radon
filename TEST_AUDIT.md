# TEST_AUDIT.md — Radon test-suite audit (PART A, frozen)

**Audit date:** 2026-08-07 (evening ET) · **Auditor:** Claude (max-effort two-part workflow)
**Tree state:** branch `feat/realtime-skew`, HEAD `2a75496a` at audit close. The working tree was DIRTY throughout (uncommitted WIP) and a concurrent interactive session (`radon-49`) was actively editing it during the audit — HEAD advanced from `af255787` to `2a75496a` mid-run and several audited files (`scripts/ib_place_order.py`, `scripts/monitor_daemon/handlers/journal_sync.py`, `web/tests/position-pnl-pct-*.test.ts`, `web/CLAUDE.md`) changed under us. Where run results trace to that WIP rather than committed code, it is called out explicitly. PART B executes in an isolated worktree off `2a75496a` and never touches the main tree.
**Zero source or test files were modified by this audit.** The only environment change: `pip install mcp==1.28.1` (pinned in `requirements.txt:83`, was missing locally and broke collection of `scripts/tests/test_knowledge_mcp.py:25`).
**Method:** every one of the 956 test files was read in full and classified by a fleet of 41 reader agents against a fixed rubric (one retry round; 1 file classified manually), cross-checked by 6 senior gap-hunting agents over the critical-path SOURCE files, 3 full runs of each CI-gated suite, 1 run of the non-gated extras, and 1 live run of the curated P0-financial Playwright subset. Every claim cites `file:line`.

---

## 1 · Executive summary

The unit and API layers are genuinely strong: ~10,900 tests across pytest and vitest ran **three consecutive times each with zero nondeterministic failures**, are protected by real boundary fakes (in-memory sqlite behind the Hrana seam, `fakeDemoDb`, a spawn-real-FastAPI harness) and by two non-regressing coverage ratchets, and in places reach genuinely elite quality (a `fast-check` property suite over the order-risk seam that already caught a real money bug, `web/tests/fuzz/order-risk.fuzz.test.ts:1`; AST-level negative-dependency contracts, `scripts/api/tests/test_orders_place_safety_contract.py:82`). The pyramid is inverted at the top: **all 124 Playwright specs gate nothing** (deferred from CI, `.github/workflows/ci.yml:236-268`), the full e2e suite is unrunnable today because one legacy file self-spawns a dev server and crashes the runner in 7 seconds (`web/e2e/prices-performance.test.js:1`, collected by Playwright's default testMatch), and when the curated P0-financial subset was actually executed, **23 of 43 tests failed** — the browser layer manufactures confidence instead of providing it. The deepest holes are on the money path itself: the order-placement confirm loop's three exit paths, duplicate-order protection (FastAPI recovery re-runs `ib_place_order.py` after a gateway restart, `scripts/api/server.py:3959`; a Next-side timeout deletes the idempotency key that would block the retry, `web/lib/orders/orderIdempotency.ts:82`; exit-orders re-places a live SELL every 5 minutes while a journal write fails, `scripts/monitor_daemon/handlers/exit_orders.py:198`), and fill→journal identity (colliding synthetic `trade_id`s that destructively overwrite journal rows, `scripts/monitor_daemon/handlers/fill_monitor.py:299`; unsorted fill labelling, `journal_sync.py:496`; UTC-vs-ET session dates, `journal_sync.py:624`) are all untested — and several are live defects, one of them **pinned as correct by its own test** (ratio combos flattened to 1:1, `web/tests/position-trade.test.ts:76-79` asserting the bug at `web/lib/order/positionTrade.ts:94`). Forty-nine files are net-negative (self-asserting literals, copy-pasted logic mirrors, source-string grepping) and twenty-six are fragile by mechanism (sleep-based sync, `waitForTimeout`, nth-child selectors), but the single biggest risk is unambiguous: **a duplicate live order — three independent, individually-unguarded paths can each double a real position while every gate stays green.**

**Pyramid shape:** ~4,944 pytest + 725 cloud pytest + 5,205 vitest tests execute in ~4.5 minutes of runner time; 124 e2e specs (~25k lines) execute never. A wide, healthy base under a dead canopy.

**Single biggest risk:** double-submission of a real-money order (backlog T-010/T-011/T-021), followed closely by silent journal corruption (T-013/T-014/T-023/T-024).

---

## 2 · Inventory

### 2.1 Runners and configs

| Layer | Runner / config | Where it runs |
|---|---|---|
| Python unit/API | pytest 9.0.2 · `pyproject.toml:12-39` (pythonpath pins, `-m 'not integration'`, broad collection, `norecursedirs`) | CI gate: `python -m pytest scripts/tests scripts/api/tests scripts/trade_blotter --cov --cov-fail-under=64` (`ci.yml:141-144`) |
| Cloud infra | pytest · `cloud/tests` (own conftest) | CI gate (`ci.yml:145-146`) |
| Web/site/tools unit | vitest 4 · root `vitest.config.ts:14-75` (NODE_ENV pinned, `vitest.setup.ts` cleanup, coverage ratchet 75/78/65) | CI gate (`ci.yml:103-107`, excludes 3 python-spawning files) |
| Perimeter smoke | `next start` + curl assertions | CI gate (`ci.yml:148-233`) |
| Browser e2e (app) | Playwright 1.58 · `web/playwright.config.ts` (spawns `next dev`, RADON_AUTHLESS_TEST=1) + `web/playwright.no-server.config.ts` | **NOT in CI** — deferred with a documented loopback blocker (`ci.yml:236-268`) |
| Browser e2e (site) | Playwright · `site/e2e/*` | **NOT in CI**, no config found wired to a gate |
| Secret scan | gitleaks pinned binary | CI gate (`ci.yml:24-61`) |

### 2.2 What is NOT gated by CI (exists but protects nothing)

- `web/e2e/` — 124 specs / 25,071 lines. Deferred from CI; full suite currently **unrunnable** (see §3).
- `tests/test_portfolio_performance.py` — 355 lines / 20 TWR money-math cases; passes locally (`runs/pytest-extras-r1`, rc=0) but absent from the CI pytest invocation (`ci.yml:142` lists only `scripts/tests scripts/api/tests scripts/trade_blotter`). Backlog T-051.
- `tests/test_position_return_capital.py` — untracked concurrent-session WIP; inventoried, not judged as shipped work.
- `site/e2e/` — 3 specs, ungated.
- `.pi/tests/startup-protocol.test.ts` — matched by no runner config at all.
- `web/e2e/prices-performance.test.js` — worse than orphaned: Playwright's default testMatch DOES collect it and it kills the run (T-001).
- `scripts/test_ib_realtime.py` — manual live-connectivity harness; pytest collects **0 tests** from it (runner exit 5, `runs/ib-realtime-collect`), contradicting the `pyproject.toml:16-17` comment that broad collection protects it (T-054).
- `cloud/scripts/tests/test_preflight.sh` — shell self-test, wired to no gate.

### 2.3 Critical-path coverage map (pytest r1, `--cov-branch`; statements missed / total)

| Critical path | Module | Miss/Stmts | Verdict |
|---|---|---|---|
| Order placement | `scripts/ib_place_order.py` | 110/223 (~49% covered) | **Weak** — confirm-loop exits untested (§5) |
| Order cancel/modify | `scripts/ib_order_manage.py` | 55/195 | Moderate |
| Kill path | `scripts/exit_order_service.py` | 247/335 (~26%) | **Weak** |
| Exit orders (daemon) | `handlers/exit_orders.py` | 28/157 | Good stmt coverage, wrong scenarios (§5) |
| Fills → journal | `handlers/fill_monitor.py` 25/184 · `handlers/journal_sync.py` 81/460 · `journal_reconcile.py` 25/189 | Strong stmt coverage — but the **production wiring is never instantiated** (§5) |
| Basis | `clients/journal_basis.py` 17/160 · `journal_rehydrate.py` 39/302 | Strong |
| Position sync | `scripts/ib_sync.py` | 433/881 (~51%) | **Weak** |
| Persistence | `scripts/db/writer.py` 215/494 · `scripts/db/readers.py` 98/216 | Weak; TWR readers 0-tested (`readers.py:348-469`) |
| API surface | `scripts/api/server.py` | 880/2263 | Weak in absolute terms |
| Blotter | `trade_blotter/flex_query.py` 157/228 · `blotter_service.py` 44/156 | Weak |
| **pytest total** | `--cov=scripts --cov=api --cov-branch` | **73.01%** (gate: ≥64, `ci.yml:144`) | Note: test files themselves are counted (e.g. `trade_blotter/test_blotter.py` rows in the report) — inflates the ratchet (T-050) |
| **vitest** | ratchet lines 75 / funcs 78 / branches 65 (`vitest.config.ts:41-45`) | thresholds ENFORCED and passing in all 3 runs (rc=0 with `--coverage` in r2/r3; ~79% lines per `vitest.config.ts:38` comment) | Healthy; excludes hooks/PI/spawn routes by design (`vitest.config.ts:53-74`) |

### 2.4 Web-side critical paths

Order seam (`web/lib/order/risk/*`, `positionTrade.ts`, `orderIdempotency.ts`, place/modify/cancel routes), blotter derivation (`web/lib/blotter/fromJournal.ts`), market-data client (`usePrices.ts`, `pricesProtocol.ts`), and middleware perimeter are all unit-covered — the gaps are specific scenarios (§5), not absent files. The relay itself (`scripts/ib_realtime_server.js`, ~2k lines) has **no behavioral tests at all**: its two "contract" test files assert source substrings, not behavior (`web/tests/ib-depth-stream-contracts.test.ts`, `web/tests/ib-index-stream-contracts.test.ts` — REFACTOR bucket).

---

## 3 · Suite stability — 3 consecutive full runs per gated layer

All logs + junit XML: session scratchpad `runs/` (paths in §10). Runs were sequential on an otherwise-quiet machine, `caffeinate`-pinned.

| Run | Result | Time | Failures |
|---|---|---|---|
| pytest-gate r1 | rc=1 · 4930 pass / 13 skip / **1 fail** / 90 deselected | 97s | `test_performance_explainer_report.py::test_build_html_mentions_shared_chart_contract` |
| pytest-gate r2 | rc=1 · identical to r1 | 97s | same test |
| pytest-gate r3 | rc=1 · identical to r1 | 97s | same test |
| cloud r1 | rc=1 · 723 pass / 2 skip / **2 fail** | 110s | `test_deploy_corrections.py::test_external_signal_status_is_preserved_after_recovery[int,hup]` |
| cloud r2 | rc=1 · identical | 120s | same 2 |
| cloud r3 | rc=1 · identical | 147s | same 2 |
| vitest r1 | rc=1 · 5175 pass / 26 skip / **4 fail** | 71s | 4 tests in `web/tests/position-pnl-pct-entry-margin.test.ts` |
| vitest r2 | rc=0 · 5179 pass / 26 skip | 61s | — |
| vitest r3 | rc=0 · 5179 pass / 26 skip | 72s | — |
| pytest extras (root `tests/`) | rc=0 | 1s | — |
| Playwright full suite | rc=1 in **7s** | — | crashed before running any spec |
| Playwright P0-financial subset (10 specs) | **23 fail / 18 pass / 2 skip** | 4.7m | see below |

**Conclusions, with proof:**

1. **Zero true flake detected.** Every pytest/cloud failure is byte-identical across all three runs. The only run-to-run delta in vitest (r1's 4 failures + 82 tests whose identity changed between runs, all in `web/tests/position-pnl-pct-structures-catalog.test.ts` and `web/tests/position-pnl-pct-entry-margin.test.ts`) is fully attributed to the concurrent session editing exactly those files mid-protocol: `position-pnl-pct-entry-margin.test.ts` mtime `21:54:07` falls between r1 start (`21:53:44`) and r2 start (`21:54:56`). These two files are NOT flagged fragile; they are in-flight WIP.
2. **`test_performance_explainer_report.py` is FRAGILE by mechanism and a CI-coverage hole.** It asserts `build_html` against the live, gitignored `data/performance.json` (`scripts/tests/test_performance_explainer_report.py:29-36`): red on any dev box whose cache predates the `period_label` key (KeyError in `scripts/performance_explainer_report.py:build_sections`), auto-skipped in CI where the file is absent — so the report builder has **zero CI coverage** while looking tested. Backlog T-003.
3. **The two cloud failures are platform-coupled, not product bugs**: macOS bash signal propagation hangs the supervised worker until the test SIGKILLs it (`cloud/tests/test_deploy_corrections.py:469`, `communicate(timeout=5)` → returncode -9). Green on the linux CI runners that matter; deterministic red on darwin. Backlog T-002 (darwin skip, linux coverage preserved).
4. **The Playwright suite cannot run at all**: `web/e2e/prices-performance.test.js` is collected by the default testMatch, self-spawns `npm run dev` and throws an unhandled `spawn /bin/sh ENOENT` that kills the runner 7s in (`runs/playwright-r1.log`). Backlog T-001.
5. **The e2e layer has rotted while ungated.** With the killer file excluded, the 10 highest-value financial specs produced 23 failures — dominated by `.metric-card` CSS-class locators timing out across all 9 `account-metric-cards.spec.ts` cases, plus failures in `order-combo` (4), `realized-pnl` (3), `share-pnl` (3), `wulf-close-order-naked-short`, `portfolio-same-day-combo-pnl`, `orders-realized-pnl-portfolio-fallback`, and `day-move-ib-daily-pnl` (`runs/playwright-subset-r1.log`). Specs that gate nothing drift from the app they describe; these now document a UI that no longer exists in that shape.

---


---

## 4 · Scorecard — every test file, classified

Fleet of 41 readers + 1 manual classification; every file read in full. Bucket totals over 956 files:

| Bucket | Count | Share |
|---|---|---|
| GOOD AS-IS | 810 | 84% |
| NEEDS IMPROVEMENT | 71 | 7% |
| FRAGILE | 26 | 2% |
| REFACTOR COMPLETELY | 49 | 5% |

Severity of the defect among the 146 non-GOOD files: **P0 8 · P1 46 · P2 92** (P0 = money-losing gap, P1 = correctness gap, P2 = maintainability). A GOOD rating scores the file's own quality only — missing coverage in its subject area is tracked in §5, not here.

<details><summary><b>Full 956-file scorecard (click to expand)</b></summary>

#### e2e-site (3 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `site/e2e/branding.spec.ts` | GOOD | NONE | Locates by semantic attribute selectors (rel/sizes/name) and fetches the real manifest JSON to assert icon sizes (site/e2e/branding.spec.ts:48-63); no structural coupling. |
| `site/e2e/surface-preview.spec.ts` | FRAGILE | P2 | Locates tiles via index-based `:scope > div` nth(0)/nth(1) and `div.grid` class chain (site/e2e/surface-preview.spec.ts:13-18), so a behavior-preserving layout reorder breaks it without any real regression. |
| `site/e2e/theme-toggle.spec.ts` | GOOD | NONE | Uses data-testid selector and web-first assertions on data-theme attribute and localStorage persistence (site/e2e/theme-toggle.spec.ts:11,28,31-32). |

#### e2e-web (124 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `web/e2e/aaoi-risk-reversal-max-loss.spec.ts` | GOOD | NONE | Full regression for a real P0 max-loss bug: drives the UI, asserts the corrected six-figure max loss range and Gate 1 warning text (web/e2e/aaoi-risk-reversal-max-loss.spec.ts:296-306), all backend calls stubbed via page.route. |
| `web/e2e/account-day-move-ib-daily-pnl.spec.ts` | GOOD | NONE | Pins IB daily P&L preference over stale last-trade pricing with exact dollar assertions and a negative assertion against the old wrong value (web/e2e/account-day-move-ib-daily-pnl.spec.ts:216,224,241). |
| `web/e2e/account-metric-cards.spec.ts` | IMPROVE | P2 | 13 tests each re-run identical setupMocks+goto boilerplate instead of test.beforeEach (web/e2e/account-metric-cards.spec.ts:101-129), otherwise solid class + text-content assertions on modal formulas. |
| `web/e2e/admin-panel.spec.ts` | GOOD | NONE | Testid-based happy path + confirm-gate for /api/admin/ib/restart, verified POST only fires after explicit confirm (web/e2e/admin-panel.spec.ts:212-218). |
| `web/e2e/admin-visual-snapshot.spec.ts` | REFACTOR | P2 | File's own docstring says 'NOT a regression test' (web/e2e/admin-visual-snapshot.spec.ts:3-4); only assertion is admin-page visible (line 108), duplicating admin-panel.spec.ts render coverage with zero added signal. |
| `web/e2e/cash-flows-section.spec.ts` | GOOD | NONE | Testid-driven collapse/expand + filter isolation + sign-convention assertions on real dollar values (web/e2e/cash-flows-section.spec.ts:169-173). |
| `web/e2e/cash-flows-withdrawal-may8.spec.ts` | GOOD | NONE | Production-incident regression pinning exact sign + amount for a real withdrawal via testid + semantic class (web/e2e/cash-flows-withdrawal-may8.spec.ts:109-118). |
| `web/e2e/catalyst-card-weekend.spec.ts` | GOOD | NONE | page.clock.install pins deterministic time; covers weekend-fossil past/future/exact-time boundary cases with testid/role-scoped assertions (web/e2e/catalyst-card-weekend.spec.ts:155-213). |
| `web/e2e/chain-held-leg-prices.spec.ts` | GOOD | NONE | Regresses a real bid/mid/ask sign bug via role-scoped row lookup + button-name assertions (web/e2e/chain-held-leg-prices.spec.ts:279-291). |
| `web/e2e/chain-sticky-header.spec.ts` | GOOD | NONE | Asserts both the CSS mechanism (position:sticky/z-index) and the actual scrolled pixel outcome (headerTop within 2px of wrapperTop), web/e2e/chain-sticky-header.spec.ts:104-129 — real behavior, not just implementation. |
| `web/e2e/chain-strikes-selector.spec.ts` | FRAGILE | P2 | setStrikesPerSide uses a hardcoded page.waitForTimeout(100) as the sync mechanism for the React re-render (web/e2e/chain-strikes-selector.spec.ts:82-87) instead of a web-first row-count assertion. |
| `web/e2e/chat-launcher-focus.spec.ts` | FRAGILE | P2 | Opens the dialog via a documented retry-until-green toPass loop dispatching synthetic keydown events because headless Chromium drops the real shortcut (web/e2e/chat-launcher-focus.spec.ts:39-48). |
| `web/e2e/crcl-spread-builder.spec.ts` | IMPROVE | P1 | Second test silently skips its own click-and-verify step if the clickable cell isn't found (`if (await clickable.count())`, web/e2e/crcl-spread-builder.spec.ts:328-330), letting the test pass without exercising the sign-flip regression it targets. |
| `web/e2e/crox-bull-call-stale-price.spec.ts` | FRAGILE | P1 | Portfolio row assertions are keyed by raw column index (`cells.nth(4)`, `.nth(6)`, `.nth(9)`, `.nth(10)`, web/e2e/crox-bull-call-stale-price.spec.ts:280-283) instead of testid/role/header text — any column reorder silently mis-attributes values. |
| `web/e2e/cta-page.spec.ts` | GOOD | NONE | Testid/aria-sort based coverage of sortable CTA tables, stale banner, and currency-decimal formatting (web/e2e/cta-page.spec.ts:225-280). |
| `web/e2e/cta-stale-banner.spec.ts` | GOOD | NONE | Testid-scoped stale-banner content assertions with a distinct sync_health shape from the primary cta-page spec (web/e2e/cta-stale-banner.spec.ts:82-89). |
| `web/e2e/curve-tab.spec.ts` | GOOD | NONE | Verifies dual-axis chart, summary strip, and that missing:true never surfaces a 4xx to the client by listening on page 'response' events (web/e2e/curve-tab.spec.ts:129-139). |
| `web/e2e/day-move-ib-daily-pnl.spec.ts` | GOOD | NONE | Pins IB daily-P&L-over-mark-to-close precedence with exact dollar figures in both the summary row and the reqPnLSingle breakdown modal (web/e2e/day-move-ib-daily-pnl.spec.ts:218-231). |
| `web/e2e/dollar-delta-leverage.spec.ts` | GOOD | NONE | Exercises long/short/combo leverage sign math end to end via testid subtitle+modal assertions with worked-out fixture math (web/e2e/dollar-delta-leverage.spec.ts:379-462). |
| `web/e2e/fill-toast.spec.ts` | FRAGILE | P1 | Second test waits out the real 30s orders-poll interval with test.setTimeout(120_000) rather than triggering/advancing it deterministically (web/e2e/fill-toast.spec.ts:195-204), and the first test uses a bare waitForTimeout(1_000) to let a priming effect 'settle'. |
| `web/e2e/flow-analysis-ticker.spec.ts` | GOOD | NONE | Covers routing, stale-scan/analyzing state, fresh-cache badge, and mobile P/C-ratio copy with testid/role assertions (web/e2e/flow-analysis-ticker.spec.ts:210-218). |
| `web/e2e/flow-surprise.spec.ts` | FRAGILE | P2 | Only /api/flow-surprise is stubbed before navigating to '/' (web/e2e/flow-surprise.spec.ts:60-61); WorkspaceShell's other required routes (portfolio/orders/regime/blotter/ib-status) are left unstubbed and hit whatever the dev server actually returns. |
| `web/e2e/header-fullscreen.spec.ts` | GOOD | NONE | Uses expect.poll on document.fullscreenElement and aria-label selectors for toggle/Escape (web/e2e/header-fullscreen.spec.ts:57-67); no sleeps. |
| `web/e2e/historical-trades-filter.spec.ts` | GOOD | NONE | Filters blotter rows by text and asserts both surviving/removed rows plus the count indicator (web/e2e/historical-trades-filter.spec.ts:190-196). |
| `web/e2e/ib-mfa-reconnect-alert.spec.ts` | FRAGILE | P1 | The 'generic disconnect/reconnect' tests wait a real waitForTimeout tied to the mock's own internal setTimeout delay (2300ms mock delay vs 2300/2800ms test wait, web/e2e/ib-mfa-reconnect-alert.spec.ts:152-162 vs 268/276) — any timing skew races the assertion. |
| `web/e2e/internals-market-closed.spec.ts` | GOOD | NONE | Deterministic Date override (documented MockDate class) plus testid-scoped skew-chart assertions (web/e2e/internals-market-closed.spec.ts:56-77, 133-137). |
| `web/e2e/iwm-close-order-summary.spec.ts` | GOOD | NONE | Verifies combo close-order confirmation shows Close Credit / Est. Realized P&L (not Max Gain/Loss) with exact dollar figures on a P0 order-confirm surface (web/e2e/iwm-close-order-summary.spec.ts:284-290). |
| `web/e2e/iwm-synthetic-mark-label.spec.ts` | GOOD | NONE | Realistic combo/WS fixtures, asserts MARK label + signed value AND negation of stale value (web/e2e/iwm-synthetic-mark-label.spec.ts:280-286). |
| `web/e2e/iwm-ticker-detail-combo-sign.spec.ts` | GOOD | NONE | Exercises full combo order flow to submitted payload, asserting signed limitPrice -0.4 against CLAUDE.md sign convention (web/e2e/iwm-ticker-detail-combo-sign.spec.ts:322-323). |
| `web/e2e/margin-debt-tab.spec.ts` | GOOD | NONE | Deterministic synthetic FINRA series, testid-scoped assertions incl. CSS color threshold and missing:true empty state (web/e2e/margin-debt-tab.spec.ts:94,157). |
| `web/e2e/margin-warning-toast.spec.ts` | FRAGILE | P1 | Uses waitForTimeout(500) to assert absence of a toast and waitForTimeout(6500) to prove persistence, instead of web-first no-toast/visible assertions (web/e2e/margin-warning-toast.spec.ts:146,195). |
| `web/e2e/mobile-a11y-pwa.spec.ts` | GOOD | NONE | Web-first assertions, testid/role selectors, real touch-target and manifest/SW contract checks across routes (web/e2e/mobile-a11y-pwa.spec.ts:173-183). |
| `web/e2e/mobile-blotter.spec.ts` | GOOD | NONE | Static realistic blotter fixture, testid-scoped assertions incl. desktop table absence (web/e2e/mobile-blotter.spec.ts:83-100). |
| `web/e2e/mobile-chain-ladder.spec.ts` | GOOD | NONE | Uses waitForFunction (not sleep) to settle animation before paint-order check; testid selectors throughout (web/e2e/mobile-chain-ladder.spec.ts:121-136). |
| `web/e2e/mobile-charts.spec.ts` | IMPROVE | P2 | Two assertions are conditionally skipped via `if (await el.count())`, so the check silently passes when the element never renders (web/e2e/mobile-charts.spec.ts:106,129). |
| `web/e2e/mobile-cockpit-book-layout.spec.ts` | GOOD | NONE | Regression-driven geometry + sign-convention assertions (credit combo avg entry negative) with real numeric tolerances (web/e2e/mobile-cockpit-book-layout.spec.ts:213-222). |
| `web/e2e/mobile-combo-instrument-switcher.spec.ts` | GOOD | NONE | Window-relative expiry dates per repo convention with an explicit rot-regression comment (web/e2e/mobile-combo-instrument-switcher.spec.ts:5-11); asserts aria-pressed + displayed price on toggle. |
| `web/e2e/mobile-executed-journal.spec.ts` | GOOD | NONE | Realistic fill/journal fixtures, testid-scoped assertions of realized P&L and desktop-table absence (web/e2e/mobile-executed-journal.spec.ts:146-150). |
| `web/e2e/mobile-order-ticket.spec.ts` | IMPROVE | P2 | Order-placement payload assertions (BUY/SELL, combo legs, envelope action) are thorough, but the long-press test uses raw waitForTimeout(400)/(600) as dwell-timer synchronization (web/e2e/mobile-order-ticket.spec.ts:285,288). |
| `web/e2e/mobile-orders.spec.ts` | GOOD | NONE | testid-scoped order card assertions incl. action sheet and Escape dismissal (web/e2e/mobile-orders.spec.ts:83-128). |
| `web/e2e/mobile-p2-polish.spec.ts` | GOOD | NONE | Real drag interaction verified via bounding-box delta and viewport-overflow check, not a mocked event (web/e2e/mobile-p2-polish.spec.ts:138-151). |
| `web/e2e/mobile-positions.spec.ts` | GOOD | NONE | Derives expected return % from the same numeric fixture entries and checks against rendered text; expand/collapse and modal navigation covered (web/e2e/mobile-positions.spec.ts:140-141). |
| `web/e2e/mobile-shell.spec.ts` | FRAGILE | P2 | No page.route API stubbing anywhere in the file (unlike every sibling mobile-*.spec.ts), so tests hit live/unstubbed backend endpoints and inherit whatever real disk/DB state the dev server has. |
| `web/e2e/mobile-short-strangle-skew.spec.ts` | GOOD | NONE | Deterministic mocked chain/WS fixtures drive skew-panel telemetry assertions for both strangle and risk-reversal structures (web/e2e/mobile-short-strangle-skew.spec.ts:139-146,162-170). |
| `web/e2e/mobile-ticker-search.spec.ts` | GOOD | NONE | Web-first testid assertions incl. body-scroll-lock open/close cycle (web/e2e/mobile-ticker-search.spec.ts:69-84). |
| `web/e2e/modify-combo-order.spec.ts` | GOOD | NONE | P0 combo-modify flow: asserts full replacement payload incl. per-leg actions, signed negative limit price, and layout regressions via expect.poll (web/e2e/modify-combo-order.spec.ts:404-420,481-497). |
| `web/e2e/modify-order-confirmation.spec.ts` | GOOD | NONE | P0 failure-path test: a 502 from /api/orders/modify must not leave a fake pending/optimistic UI state (web/e2e/modify-order-confirmation.spec.ts:111-117,170-172). |
| `web/e2e/modify-order-resting-limit.spec.ts` | GOOD | NONE | Verifies the modal shows the resting order's own limit as ASK rather than a stale live quote, with an explicit negative assertion (web/e2e/modify-order-resting-limit.spec.ts:176-177). |
| `web/e2e/modify-order-spread-telemetry.spec.ts` | FRAGILE | P1 | Dispatches a `ws-price` CustomEvent that no app code listens to (repo-wide grep outside e2e/ finds zero matches) and the file never mocks the real WebSocket, so displayed bid/ask depend on an unmocked live relay connection; the assertion then recomputes 'expected' spread from that same rendered bid/ask (web/e2e/modify-order-spread-telemetry.spec.ts:215-227,277-283). |
| `web/e2e/newsfeed-lightbox-image-fit.spec.ts` | GOOD | NONE | Regression test with a deliberately tall SVG fixture and precise geometry assertions capturing the previous clip bug (web/e2e/newsfeed-lightbox-image-fit.spec.ts:63-71). |
| `web/e2e/open-order-combo.spec.ts` | GOOD | NONE | Verifies two single-leg open orders are correctly merged into one risk-reversal combo row and that its modify modal reflects both legs (web/e2e/open-order-combo.spec.ts:148-168). |
| `web/e2e/open-order-single-detail.spec.ts` | GOOD | NONE | Verifies single-option and combo row descriptions render correctly from mocked orders/portfolio; deterministic route stubs (web/e2e/open-order-single-detail.spec.ts:205-211). |
| `web/e2e/options-exposure.spec.ts` | GOOD | NONE | Covers loader gating, canonical route redirects, sticky header math, mobile overflow; strong role/testid selectors (web/e2e/options-exposure.spec.ts:99-119,177-192). |
| `web/e2e/order-cancel-error-propagation.spec.ts` | GOOD | NONE | Real HTTP server returns FastAPI-shaped 502 detail; asserts status code and toast text propagate end to end (web/e2e/order-cancel-error-propagation.spec.ts:76,169-176). |
| `web/e2e/order-combo.spec.ts` | IMPROVE | P2 | Solid red/green coverage of IB rejection paths but relies on CSS class selectors (.order-error, .toast-success, .modify-price-input) rather than role/testid (web/e2e/order-combo.spec.ts:176,227,233). |
| `web/e2e/order-margin-impact-unavailable.spec.ts` | GOOD | NONE | Deterministic mock WebSocket + API stubs; asserts specific UNAVAILABLE margin-impact copy and data attribute (web/e2e/order-margin-impact-unavailable.spec.ts:243-247). |
| `web/e2e/order-ticket-quote-telemetry.spec.ts` | GOOD | NONE | Derives expected spread/mid from raw bid/ask read from DOM, and asserts exact stock-close order payload (web/e2e/order-ticket-quote-telemetry.spec.ts:397-403,451-459). |
| `web/e2e/orders-empty-state.spec.ts` | GOOD | NONE | Asserts testid-scoped empty-state copy across four sections and confirms legacy bare-text markup is gone (web/e2e/orders-empty-state.spec.ts:82-86). |
| `web/e2e/orders-historical-trades-refresh.spec.ts` | GOOD | NONE | GET/POST branch on /api/blotter mock verifies auto-refresh from stale to fresh blotter counts (web/e2e/orders-historical-trades-refresh.spec.ts:136-151,162-163). |
| `web/e2e/orders-historical-trades-today.spec.ts` | GOOD | NONE | Targeted date-off-by-one regression test with forced America/Los_Angeles timezone and locale-agnostic day assertion (web/e2e/orders-historical-trades-today.spec.ts:16,127-128). |
| `web/e2e/orders-partial-realized-pnl.spec.ts` | GOOD | NONE | Asserts exact realized-P&L string derived from mock cost basis/proceeds for a partially closed position (web/e2e/orders-partial-realized-pnl.spec.ts:111-113). |
| `web/e2e/orders-realized-pnl-portfolio-fallback.spec.ts` | GOOD | NONE | Documents and reproduces a real bug (null realizedPNL from IB commission-report lag) and asserts fallback math to the dollar (web/e2e/orders-realized-pnl-portfolio-fallback.spec.ts:100,149-150). |
| `web/e2e/orders-section-layout.spec.ts` | GOOD | NONE | DOM-position assertion via compareDocumentPosition plus toggle/aria-expanded collapse behavior, including a non-toggle-interaction regression check (web/e2e/orders-section-layout.spec.ts:107-113,140-159). |
| `web/e2e/orders-ux-command-strip.spec.ts` | GOOD | NONE | Confirms cancel-all is gated behind confirm dialog with zero POSTs pre-confirm, then polls for the POST after confirm (web/e2e/orders-ux-command-strip.spec.ts:178-182). |
| `web/e2e/performance-chart-axes.spec.ts` | GOOD | NONE | Asserts axis label counts and content via testids off a full performance payload (web/e2e/performance-chart-axes.spec.ts:138-141). |
| `web/e2e/performance-chart-theme.spec.ts` | GOOD | NONE | Toggles theme and asserts concrete rgb background-image/color differences before/after (web/e2e/performance-chart-theme.spec.ts:155-158). |
| `web/e2e/performance-market-closed.spec.ts` | GOOD | NONE | Freezes Date via subclassed constructor (not real sleeps) to a deterministic weekend and asserts cached data renders without an infinite loading state (web/e2e/performance-market-closed.spec.ts:107-129,186-188). |
| `web/e2e/performance-page.spec.ts` | GOOD | NONE | Covers metric cards, explainability modal formulas, and three distinct revalidation triggers (poll, sync button, route refresh) with call-count assertions (web/e2e/performance-page.spec.ts:230-232,375-381). |
| `web/e2e/pltr-chain-position-focus.spec.ts` | GOOD | NONE | Deep-link regression asserting the chain view resolves to the position's own expiry and renders correct bid/last/ask for the nearby strike (web/e2e/pltr-chain-position-focus.spec.ts:338-343). |
| `web/e2e/portfolio-leg-row-runtime.spec.ts` | GOOD | NONE | Catches a real runtime ReferenceError (rtLast) via pageerror listener and separately verifies close-debit/realized P&L math to the dollar (web/e2e/portfolio-leg-row-runtime.spec.ts:208-223,247-250). |
| `web/e2e/portfolio-market-closed.spec.ts` | GOOD | NONE | Deterministic Date freeze for closed-weekend rendering, asserts exact cached account metrics and absence of stuck AWAITING SYNC state (web/e2e/portfolio-market-closed.spec.ts:38-60,109-112). |
| `web/e2e/portfolio-ratio-risk-reversal-label.spec.ts` | GOOD | NONE | Asserts raw long/short leg counts render in the label instead of a normalized ratio, with a negative assertion against the wrong format (web/e2e/portfolio-ratio-risk-reversal-label.spec.ts:122-125). |
| `web/e2e/portfolio-return-capital.spec.ts` | GOOD | NONE | Verifies isolated observed return-capital % renders and suppresses the incorrect premium-based percent, plus a defined-risk max-risk return check (web/e2e/portfolio-return-capital.spec.ts:143-148). |
| `web/e2e/portfolio-same-day-combo-pnl.spec.ts` | GOOD | NONE | Deterministic Date freeze for same-day entry, asserts entry-cost-based today P&L and a negative assertion against the wrong (close-based) number (web/e2e/portfolio-same-day-combo-pnl.spec.ts:84-105,213-215). |
| `web/e2e/price-chart-theme.spec.ts` | IMPROVE | P2 | File's own docstring admits it cannot inspect canvas colors and only checks html[data-theme] + canvas presence, not that the theme prop actually reaches Liveline (web/e2e/price-chart-theme.spec.ts:14-23). |
| `web/e2e/prices-performance.test.js` | REFACTOR | P2 | Standalone script (not a Playwright test file) spawning a real dev server, hardcoded 10s sleep, real network navigate, and a wall-clock >15000ms pass/fail threshold instead of assertions (web/e2e/prices-performance.test.js:7,11,20,34-38). |
| `web/e2e/realized-pnl.spec.ts` | GOOD | NONE | RED/GREEN regression proving Realized P&L is fills-derived not IB account summary, with exact-sum assertion (web/e2e/realized-pnl.spec.ts:130-132,144,148). |
| `web/e2e/regime-close-transition-refresh.spec.ts` | GOOD | NONE | Deterministic mocked GET/POST route asserts strip flips stale->settled and polls request count at web/e2e/regime-close-transition-refresh.spec.ts:141. |
| `web/e2e/regime-closed-refresh.spec.ts` | REFACTOR | P1 | Reimplements fetch/render/sync logic inline in page.setContent script (web/e2e/regime-closed-refresh.spec.ts:29-92) instead of exercising real app code; only imports a config constant. |
| `web/e2e/regime-cor1m-live-route.spec.ts` | FRAGILE | P1 | Reads live production data/cri.json / cri_scheduled with no fixture control (web/e2e/regime-cor1m-live-route.spec.ts:42-58,103); pass/fail depends on whatever is on disk at run time. |
| `web/e2e/regime-cor1m-live-stream.spec.ts` | GOOD | NONE | Deterministic mocked WS batch/status frames and API routes assert COR1M day-chg uses prior close not IB close field, web/e2e/regime-cor1m-live-stream.spec.ts:127-130. |
| `web/e2e/regime-cor1m.spec.ts` | GOOD | NONE | Mocked routes, deterministic assertions on COR1M value/subline and crash-trigger copy, web/e2e/regime-cor1m.spec.ts:100-110. |
| `web/e2e/regime-cta-share-pattern.spec.ts` | GOOD | NONE | Mocked routes, deterministic modal-parity assertions across /regime and /cta with Escape/close interactions, web/e2e/regime-cta-share-pattern.spec.ts:142-162. |
| `web/e2e/regime-day-change.spec.ts` | IMPROVE | P2 | Test 1's own comment admits WS is aborted so day-change arrows never render (web/e2e/regime-day-change.spec.ts:74-83), yet the test name claims to verify arrows; assertion only checks visibility. |
| `web/e2e/regime-detail-panels-responsive.spec.ts` | GOOD | NONE | Deterministic bounding-box layout assertions at two fixed viewport sizes with mocked data, web/e2e/regime-detail-panels-responsive.spec.ts:120-157. |
| `web/e2e/regime-history-responsive.spec.ts` | GOOD | NONE | Deterministic bounding-box assertions for chart stack at two viewports, mocked data, web/e2e/regime-history-responsive.spec.ts:119-155. |
| `web/e2e/regime-history-tooltip.spec.ts` | GOOD | NONE | Deterministic mocked data, verifies tooltip copy content and icon position, web/e2e/regime-history-tooltip.spec.ts:135-145. |
| `web/e2e/regime-live-index-stream.spec.ts` | GOOD | NONE | Mocked WS batch update deterministically asserts LIVE badges and day-chg math for VIX/VVIX/COR1M, web/e2e/regime-live-index-stream.spec.ts:219-231. |
| `web/e2e/regime-live-index-streaming.spec.ts` | GOOD | NONE | Mocked WS subscribe/price flow deterministically asserts live values and day-chg math, web/e2e/regime-live-index-streaming.spec.ts:203-213. |
| `web/e2e/regime-live-stream-values.spec.ts` | GOOD | NONE | Mocked WS batch, deterministic assertions on rendered values/badges/day-chg, web/e2e/regime-live-stream-values.spec.ts:202-212. |
| `web/e2e/regime-market-closed-eod.spec.ts` | GOOD | NONE | Mocked routes, seven deterministic assertions that closed-market EOD values win over live WS and timestamps show '---', web/e2e/regime-market-closed-eod.spec.ts:118-192. |
| `web/e2e/regime-relationship-view.spec.ts` | GOOD | NONE | Deterministic mocked history data drives exact numeric spread/quadrant/z-score assertions and hover tooltip content, web/e2e/regime-relationship-view.spec.ts:145-150,223-234. |
| `web/e2e/regime-rvol-history-live-cache.spec.ts` | FRAGILE | P1 | Does not mock /api/regime; asserts 20 RVOL dots and non-'---' strip value against whatever live cache is on disk at run time, web/e2e/regime-rvol-history-live-cache.spec.ts:60-72. |
| `web/e2e/regime-rvol-history-live-route.spec.ts` | FRAGILE | P2 | Writes/overwrites shared repo files under data/cri.json and data/cri_scheduled/ that other concurrently running specs' disk-backed /regime route also reads, despite backup/restore, web/e2e/regime-rvol-history-live-route.spec.ts:140-148. |
| `web/e2e/regime-rvol-history.spec.ts` | FRAGILE | P2 | Writes a fixture file into the real data/cri_scheduled/ dir consumed by the disk-backed /regime route with no isolation from parallel workers, web/e2e/regime-rvol-history.spec.ts:85-120. |
| `web/e2e/regime-stale-market-open.spec.ts` | GOOD | NONE | Mocked routes, deterministic regression test for the market_open override on stale data, web/e2e/regime-stale-market-open.spec.ts:89-98. |
| `web/e2e/regime-strip-responsive.spec.ts` | GOOD | NONE | Mocked WS+routes, deterministic bounding-box layout assertions across three viewport scenarios, web/e2e/regime-strip-responsive.spec.ts:272-382. |
| `web/e2e/regime-vcg-edr-badge.spec.ts` | IMPROVE | P2 | Assertion only checks backgroundColor isn't fully transparent rather than the actual warning/amber token value, web/e2e/regime-vcg-edr-badge.spec.ts:137-138. |
| `web/e2e/regime-vix-live-badge.spec.ts` | GOOD | NONE | Mocked routes, deterministic checks that badge/timestamp elements exist and show '---' without live WS data, web/e2e/regime-vix-live-badge.spec.ts:114-172. |
| `web/e2e/risk-reversal-midprice.spec.ts` | FRAGILE | P1 | Core positive-case assertion (MIDPRICE badge appears) is disabled via test.fixme (web/e2e/risk-reversal-midprice.spec.ts:226), and the remaining test uses a real waitForTimeout(500) as synchronization (line 309). |
| `web/e2e/risk-reversal-skew.spec.ts` | GOOD | NONE | Mocked relay-scoped WebSocket + API routes, deterministic click-through assertions on skew/delta telemetry values, web/e2e/risk-reversal-skew.spec.ts:152-164. |
| `web/e2e/rv-ratio.spec.ts` | GOOD | NONE | Deterministic gated-promise loader states (no real sleeps) plus fixture-driven numeric stat assertions, web/e2e/rv-ratio.spec.ts:99-143,160-176. |
| `web/e2e/scanner-discover.spec.ts` | GOOD | NONE | Mocked API surface, deterministic redirect and mobile-overflow assertions, web/e2e/scanner-discover.spec.ts:88-113. |
| `web/e2e/scanner-leap-garch-tabs.spec.ts` | GOOD | NONE | Role/testid assertions on real payload fields (web/e2e/scanner-leap-garch-tabs.spec.ts:70) pin LEAP/GARCH panel contract without flaky waits. |
| `web/e2e/scanner-tab-gaps.spec.ts` | GOOD | NONE | Deterministic pixel-gap regression via getBoundingClientRect (web/e2e/scanner-tab-gaps.spec.ts:96-104), no timers/sleeps, catches real CSS regressions. |
| `web/e2e/scanner-ticker-scan.spec.ts` | GOOD | NONE | Asserts exact forwarded POST payload shape (web/e2e/scanner-ticker-scan.spec.ts:200,218,235) and inline validation rejection without posting. |
| `web/e2e/share-pnl-entry-exit.spec.ts` | IMPROVE | P2 | File header claims it verifies entry/exit PST time + commission omission but only checks PNG magic bytes and status (web/e2e/share-pnl-entry-exit.spec.ts:26-31). |
| `web/e2e/share-pnl.spec.ts` | GOOD | NONE | Signed risk-reversal basis test asserts exact entryPrice=-0.75, exitPrice=1, pnlPct≈231.35 from realistic multi-tranche fills (web/e2e/share-pnl.spec.ts:360-362). |
| `web/e2e/short-strangle-skew.spec.ts` | GOOD | NONE | WS mock only intercepts the relay socket, asserts concrete skew/delta values from clicked chain legs (web/e2e/short-strangle-skew.spec.ts:154-159). |
| `web/e2e/sidebar-performance-hidden.spec.ts` | GOOD | NONE | Simple, deterministic role-based nav assertion (web/e2e/sidebar-performance-hidden.spec.ts:65-68) with no timing dependence. |
| `web/e2e/skew-tab.spec.ts` | GOOD | NONE | Window-relative synthetic series plus exact data-testid value assertions (web/e2e/skew-tab.spec.ts:96-104) and a dedicated missing:true empty-state case. |
| `web/e2e/spread-price-bar.spec.ts` | IMPROVE | P1 | Primary positive scenario is test.fixme and never runs (web/e2e/spread-price-bar.spec.ts:162), leaving only the fallback-to-underlying path exercised for combo pricing. |
| `web/e2e/straddle-tab.spec.ts` | GOOD | NONE | Window-relative series + exact data-testid strip values and dual-axis chart assertions (web/e2e/straddle-tab.spec.ts:97-103), plus missing-state case. |
| `web/e2e/strength-confirmation-scanner.spec.ts` | GOOD | NONE | Asserts exact scan POST bodies (web/e2e/strength-confirmation-scanner.spec.ts:163,168), tooltip content, and mobile no-overflow geometry via computed styles. |
| `web/e2e/sync-fallback.spec.ts` | FRAGILE | P1 | Uses page.waitForTimeout(2_000) as the synchronization/assertion window for absence of errors (web/e2e/sync-fallback.spec.ts:227,246) instead of a web-first wait. |
| `web/e2e/theta-harvester-prefill.spec.ts` | GOOD | NONE | Asserts exact prefilled legs query string SELL:1x520P,SELL:1x625C and rendered strikes/expiry (web/e2e/theta-harvester-prefill.spec.ts:202-210). |
| `web/e2e/theta-harvester-scanner.spec.ts` | GOOD | NONE | Asserts exact scan POST bodies (web/e2e/theta-harvester-scanner.spec.ts:170,175) and mobile no-overflow geometry via computed styles. |
| `web/e2e/ticker-page.spec.ts` | GOOD | NONE | Covers direct nav, case redirect, tab URL sync, and back-nav with web-first waitForURL assertions (web/e2e/ticker-page.spec.ts:163,180,217), no arbitrary sleeps. |
| `web/e2e/ticker-search-chain.spec.ts` | GOOD | NONE | Verifies real placed-order payload shape including combo leg ratios and actions (web/e2e/ticker-search-chain.spec.ts:352-354,404-408) and IB error-message rewriting (line 447-451). |
| `web/e2e/ticker-search-live.spec.ts` | FRAGILE | P2 | Relies on page.waitForTimeout(500) three times to await debounced WS search results (web/e2e/ticker-search-live.spec.ts:179,270,364) instead of a web-first wait on the dropdown. |
| `web/e2e/vxn-index-routing.spec.ts` | GOOD | NONE | Asserts exact WS subscribe payload shape for CBOE index routing (web/e2e/vxn-index-routing.spec.ts:179-180) using waitForFunction, not sleeps. |
| `web/e2e/watchlist-page.spec.ts` | GOOD | NONE | Web-first assertions on testids and click-through navigation (web/e2e/watchlist-page.spec.ts:160-170), no arbitrary waits. |
| `web/e2e/ws-connection-stability.spec.ts` | FRAGILE | P1 | All four tests gate assertions behind fixed page.waitForTimeout (500/1000/2000/1500ms) instead of web-first waits (web/e2e/ws-connection-stability.spec.ts:362,381,415,438). |
| `web/e2e/wulf-close-order-naked-short.spec.ts` | GOOD | NONE | Direct regression for a real false-positive naked-short bug on a closing SELL, asserts absence of the warning and a specific success toast (web/e2e/wulf-close-order-naked-short.spec.ts:251,261-262). |

#### manual (1 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `scripts/test_ib_realtime.py` | FRAGILE | P1 | Real IB/WS network calls with sleep-based sync (scripts/test_ib_realtime.py:134,151,175) and hardcoded avg_latency<100ms threshold (scripts/test_ib_realtime.py:460); explicitly opts out of pytest via __test__=False. |

#### orphan (1 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `.pi/tests/startup-protocol.test.ts` | REFACTOR | P2 | Tests at .pi/tests/startup-protocol.test.ts:50-56 and :58-165 build a literal string then assert.ok(ui.hasMessage(same literal)) inline, never calling any startup-protocol.ts code; they should be deleted since :171-350 already exercise the real StartupTracker/summarizeFreeTradeError exports. |

#### unit-blotter (2 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `scripts/trade_blotter/test_blotter.py` | GOOD | NONE | Decimal-precise P&L math hand-derived in comments and asserted exactly (scripts/trade_blotter/test_blotter.py:29,150,226); IB fill parsing uses Mock objects only as boundary fakes for IB's own API shape. |
| `scripts/trade_blotter/test_integration.py` | REFACTOR | P0 | Every assertion is wrapped in try/except that swallows AssertionError and returns False/prints instead of failing pytest (scripts/trade_blotter/test_integration.py:141-146), so a broken P&L calc collected by pytest never fails the run. |

#### unit-cloud (17 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `cloud/scripts/tests/test_preflight.sh` | GOOD | NONE | Sources real preflight_env from deploy.sh (cloud/scripts/tests/test_preflight.sh:26) and exercises pass/fail/bypass/dollar-quoting/missing-file paths against real fixtures. |
| `cloud/tests/test_bootstrap_control_plane.py` | GOOD | NONE | Exercises the real bash bootstrap via subprocess with isolated rootfs, asserts exact byte/mode/hash/manifest contracts and no-op idempotency; cloud/tests/test_bootstrap_control_plane.py:375-410,459-487 |
| `cloud/tests/test_caddyfile.py` | IMPROVE | P2 | Mostly substring/regex presence checks on static Caddyfile text (cloud/tests/test_caddyfile.py:32-44) rather than executing Caddy; would miss a syntactically-broken but string-matching config. |
| `cloud/tests/test_db_backup.py` | GOOD | NONE | Real sqlite3 round-trip via _FakeDb adapter (cloud/tests/test_db_backup.py:30-38) exercises actual dump_database SQL generation, paging (cloud/tests/test_db_backup.py:164-174), and both .rows/.fetchall() cursor shapes (cloud/tests/test_db_backup.py:176-187); deterministic, no sleeps/network, assertions check real restored row/table counts. |
| `cloud/tests/test_deploy_and_setup.py` | GOOD | NONE | Static regex guards pin 3 real prod incidents (newsfeed omission, replica corruption, sudoers) w/ deterministic bash -n checks; cloud/tests/test_deploy_and_setup.py:47-54,187-224,320-333 |
| `cloud/tests/test_deploy_corrections.py` | GOOD | NONE | Sources real deploy.sh/deploy-root-helper.sh into subshells and drives real subprocess/signal/flock/fsync behavior against tmp_path fixtures; e.g. injected-failure parametrization at cloud/tests/test_deploy_corrections.py:618-663 and exact-order assertions at :532-537 would catch real rollback regressions. |
| `cloud/tests/test_deploy_resilience.py` | GOOD | NONE | Pins the 2026-07-08 incident: real subprocess execution of deploy.sh functions with fake flock/timeout binaries and tmp_path isolation, e.g. cloud/tests/test_deploy_resilience.py:110-124 asserts handle_deploy_signal actually invokes recovery, cloud/tests/test_deploy_resilience.py:277-289 round-trips the green marker on disk and checks 0600 perms. |
| `cloud/tests/test_docker_compose.py` | GOOD | NONE | Deterministic YAML-config assertions matching real prod contract: no 0.0.0.0 binds (cloud/tests/test_docker_compose.py:82-88), pinned digest matches docker-compose.yml:3 exactly (cloud/tests/test_docker_compose.py:41-44), restart=no + IBC self-restart disabled (cloud/tests/test_docker_compose.py:46-47,137-140). |
| `cloud/tests/test_drift_audit.py` | GOOD | NONE | Exercises real drift_audit.py functions (parse_unit_text, unit_counter_diff, gather) with monkeypatched internals only where system access would occur (cloud/tests/test_drift_audit.py:128-138). |
| `cloud/tests/test_env_example.py` | GOOD | NONE | Deterministic contract test on real .env.example via root fixture; specific assertions incl. exact defaults cloud/tests/test_env_example.py:140-158 and secret-placeholder checks cloud/tests/test_env_example.py:122-136. |
| `cloud/tests/test_gitleaks_policy.py` | GOOD | NONE | Loads real .gitleaks.toml and CI yaml, compiles the actual regex rules and asserts both positive and negative matches (cloud/tests/test_gitleaks_policy.py:77-93). |
| `cloud/tests/test_ib_gateway_control.py` | GOOD | NONE | Real subprocess exec of ib-gateway-control.sh/operator-radon.sh against fake docker/systemctl in tmp_path; race tests use marker-file polling (cloud/tests/test_ib_gateway_control.py:190-194) not blind sleep for sync, e.g. 213,250,633. |
| `cloud/tests/test_integration.py` | GOOD | NONE | Real cross-file consistency checks over actual cloud/ config (ports, paths, deps, security) via conftest.py:7-27 root fixture; e.g. cloud/tests/test_integration.py:63-67 cross-checks Caddyfile vs radon-api.service port; deterministic, no mocks/sleeps/network. |
| `cloud/tests/test_media_backup.py` | GOOD | NONE | Pure-helper unit tests with real tmp_path filesystem fixtures (cloud/tests/test_media_backup.py:125-141) and monkeypatch-isolated env/fail-closed path (cloud/tests/test_media_backup.py:184-196); no network, no sleeps, deterministic. |
| `cloud/tests/test_monorepo_cutover.py` | GOOD | NONE | Executes deploy.sh functions via bash subprocess with real file hashing, manifest verification, and journal replay across rollback scenarios (cloud/tests/test_monorepo_cutover.py:277-335, 371-438). |
| `cloud/tests/test_scripts.py` | IMPROVE | P2 | Almost entirely `assert X in script_text` / regex-presence checks on shell scripts (cloud/tests/test_scripts.py:100-120, 267-296) rather than executing them; catches text removal but not logic bugs. |
| `cloud/tests/test_systemd_services.py` | GOOD | NONE | Parses real committed unit files (configparser, no mocks) and asserts real ordering/restart/timeout contracts, e.g. cloud/tests/test_systemd_services.py:356-358 Wants/Requires exclusion and :211-213 restart-policy check; deterministic, catches real regressions. |

#### unit-jslib (5 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `scripts/lib/demoMirrorReliability.test.js` | GOOD | NONE | Retry/bounded-failure/abort-signal-merge behavior asserted with injected sleep and fetch; scripts/lib/demoMirrorReliability.test.js:79-94 asserts rejection after maxAttempts. |
| `scripts/lib/marketCalendar.test.js` | GOOD | NONE | Pure resolveMarketState boundary tests plus one I/O test against the real static holidays file for a fixed historical date; scripts/lib/marketCalendar.test.js:16-21 covers the Juneteenth regression. |
| `scripts/lib/reconnectGate.test.js` | GOOD | NONE | setTimer/clearTimer injected, no real timers; verifies invalidate-then-reschedule and delay override; scripts/lib/reconnectGate.test.js:11-26. |
| `scripts/lib/staleDataMachine.test.js` | GOOD | NONE | Exhaustive escalation-ladder, farm-code, and heartbeat-vs-error-latch matrix on a pure decision function with fixed NOW; scripts/lib/staleDataMachine.test.js:243-257 covers the 2026-06-18 clobber bug. |
| `scripts/lib/wsTrust.test.js` | GOOD | NONE | Covers the proxied-loopback attack case explicitly; scripts/lib/wsTrust.test.js:31-37 pins the production incident. |

#### unit-py (249 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `scripts/tests/test_alerts_evaluate.py` | GOOD | NONE | Operator boundary tests plus throttle-window edge cases on both sides, and dispatch mocked only at the notify boundary; scripts/tests/test_alerts_evaluate.py:96-106. |
| `scripts/tests/test_all_long_combo.py` | IMPROVE | P1 | test_mixed_long_short_still_complex accepts any of three outcomes, asserting nothing concrete about the actual risk classification; scripts/tests/test_all_long_combo.py:83-93. |
| `scripts/tests/test_api_db_http.py` | GOOD | NONE | Covers hrana arg encoding, statement/transport error wrapping, and verifies timeout + auth header reach urlopen; scripts/tests/test_api_db_http.py:106-124. |
| `scripts/tests/test_api_flow_cache.py` | GOOD | NONE | Covers both the aggregate-zero and per-day-blank cache-poisoning regressions plus a weekend-exemption negative case; scripts/tests/test_api_flow_cache.py:212-243. |
| `scripts/tests/test_api_subprocess.py` | GOOD | NONE | Reuses the real production _extract_json_payload extractor deliberately (comment explains a stale embedded copy hid the EWY bug); scripts/tests/test_api_subprocess.py:300-306; real bounded subprocess timeout test at scripts/tests/test_api_subprocess.py:198-207. |
| `scripts/tests/test_archive_portfolio_snapshots.py` | GOOD | NONE | Covers idempotent dedup re-run, streaming-buffer peak-size guarantee, delete-only mode, and refuse-without-upload; scripts/tests/test_archive_portfolio_snapshots.py:284-329. |
| `scripts/tests/test_atomic_io.py` | GOOD | NONE | Covers checksum determinism, tamper detection, legacy-file fallback, and no-partial-write-on-error; scripts/tests/test_atomic_io.py:91-106. |
| `scripts/tests/test_backfill_flow_history.py` | GOOD | NONE | Window-relative dates via _date(days_ago) (repo-standard, not fragile); covers per-pair error swallowing and empty-cache skip; scripts/tests/test_backfill_flow_history.py:159-177. |
| `scripts/tests/test_backfill_journal_from_executed_orders.py` | GOOD | NONE | Real sqlite migrations, exercises dry-run/execute/skip/window-scan/retroactive-time-bound (scripts/tests/test_backfill_journal_from_executed_orders.py:460) journal reconstruction paths. |
| `scripts/tests/test_backtest_engine.py` | GOOD | NONE | Deterministic no-look-ahead proofs with a tripwire NaN-poisoned future point (scripts/tests/test_backtest_engine.py:66-88); pure logic, no mocks. |
| `scripts/tests/test_backtest_metrics.py` | GOOD | NONE | Every expected metric value is hand-derived in comments and checked via pytest.approx (scripts/tests/test_backtest_metrics.py:58-69 sharpe derivation). |
| `scripts/tests/test_backtest_strategies.py` | GOOD | NONE | Synthetic CRI history proves the exact crash-trigger gating and short-return inversion math (scripts/tests/test_backtest_strategies.py:67-84). |
| `scripts/tests/test_backtest_strategies_wired.py` | GOOD | NONE | Covers vcg/dark-pool-flow entry rules, no-look-ahead streak counting, and backfill dedup/last-write-wins (scripts/tests/test_backtest_strategies_wired.py:209-235). |
| `scripts/tests/test_batched_relay.py` | FRAGILE | P1 | Uses real asyncio.sleep(0.03) against a 10ms flush interval as the synchronization mechanism (scripts/tests/test_batched_relay.py:44-47), racy under CI load. |
| `scripts/tests/test_bootstrap_journal.py` | GOOD | NONE | Tiny deterministic check that the retired entry point returns 0 and prints 'retired' (scripts/tests/test_bootstrap_journal.py:13-17). |
| `scripts/tests/test_bounded_hrana_writers.py` | GOOD | NONE | Pins that hang-risk writers route through bounded hrana and asserts get_db() raises if touched (scripts/tests/test_bounded_hrana_writers.py:42-46), proving the migration off sync libsql. |
| `scripts/tests/test_bpi_scan.py` | GOOD | NONE | Covers aggregation math, carry-forward gaps, Yahoo spark/chart null-close recovery, staleness, persistence gating and chunked writer idempotency (scripts/tests/test_bpi_scan.py:416-425). |
| `scripts/tests/test_breadth_scan.py` | GOOD | NONE | Thorough coverage of divergence classification, degraded-scan carry-forward, and mirror upsert arity (scripts/tests/test_breadth_scan.py:265-292, 459-492). |
| `scripts/tests/test_build_account_summary.py` | GOOD | NONE | Real IB account field mapping verified against ground-truth values and fallback/default-zero paths (scripts/tests/test_build_account_summary.py:114-129). |
| `scripts/tests/test_calibration_report.py` | GOOD | NONE | Aggregate engine-selection and persistence tested with fakes plus a real sqlite writer round-trip and replace-on-conflict check (scripts/tests/test_calibration_report.py:268-283). |
| `scripts/tests/test_card_screenshot.py` | GOOD | NONE | Mocks subprocess at the OS boundary and checks exact command/cwd plus failure paths (missing/empty png, non-zero rc, timeout, missing node) (scripts/tests/test_card_screenshot.py:29-35, 55-86). |
| `scripts/tests/test_cash_flow_sync.py` | GOOD | NONE | Pure classifier/date-normalizer unit tests including combined-label sign disambiguation and garbage passthrough (scripts/tests/test_cash_flow_sync.py:24-27, 72-74). |
| `scripts/tests/test_cash_flow_sync_flex_errors.py` | GOOD | NONE | Verifies throttle codes (1001/1018/1019) raise immediately with zero retry/sleep and exact urlopen call counts for network-blip vs throttle paths (scripts/tests/test_cash_flow_sync_flex_errors.py:156-166, 212-227). |
| `scripts/tests/test_cash_flows_route_last_synced.py` | GOOD | NONE | Real FastAPI TestClient against in-memory sqlite verifies last_synced_at max-aggregation, type-filter scoping, and throttle-vs-generic sync_status classification (scripts/tests/test_cash_flows_route_last_synced.py:110-125, 204-231). |
| `scripts/tests/test_check_demo_isolation.py` | GOOD | NONE | Straightforward, deterministic CI-guard assertions covering prod-marker, missing-var, and shared-DB violations (scripts/tests/test_check_demo_isolation.py:21-42). |
| `scripts/tests/test_checkpoint.py` | GOOD | NONE | Includes a real SIGKILL-mid-run subprocess drill proving exactly-once resume with no duplicate findings (scripts/tests/test_checkpoint.py:127-153). |
| `scripts/tests/test_chronos_engine.py` | GOOD | NONE | Non-crossing quantile enforcement tested against an intentionally reversed matrix, plus full validation-error surface (scripts/tests/test_chronos_engine.py:87-97, 111-135). |
| `scripts/tests/test_chronos_forecast_cli.py` | GOOD | NONE | Three status paths (ok/insufficient_history/engine_unavailable) verified against monkeypatched series+engine surfaces (scripts/tests/test_chronos_forecast_cli.py:52-98). |
| `scripts/tests/test_ci_deploy_concurrency.py` | GOOD | NONE | Parses the actual ci.yml and pins the 2026-07-08 outage fix: deploy concurrency group/cancel-in-progress and explicit SHA passthrough (scripts/tests/test_ci_deploy_concurrency.py:44-48, 70-84). |
| `scripts/tests/test_client_id_allocation.py` | GOOD | NONE | Covers range non-overlap, conflict rotation/wraparound/exhaustion, explicit-ID no-rotation, and randomized-start distribution (scripts/tests/test_client_id_allocation.py:117-159, 240-258). |
| `scripts/tests/test_code_quality.py` | REFACTOR | P2 | TestSafeValue re-implements a hand-copied '_safe_value' mirror instead of importing IBRealtimeServer's real code (scripts/tests/test_code_quality.py:24-34); asserts nothing about production behavior and silently stops catching drift. |
| `scripts/tests/test_combo_entry_date.py` | GOOD | NONE | Reproduces a real production regression (older-blotter-date leak into new AMD RR position) and pins the fallback-to-today invariant against ib_sync.convert_to_portfolio_format (scripts/tests/test_combo_entry_date.py:244-336). |
| `scripts/tests/test_contract_resolver_futures.py` | GOOD | NONE | Pins the exact SPX/NDX/RUT/VIX → futures-root/exchange/multiplier mapping and case-insensitive lookup (scripts/tests/test_contract_resolver_futures.py:17-32). |
| `scripts/tests/test_costs.py` | GOOD | NONE | Pure-function commission/slippage/combo-penalty model tested with derived expected values, magnitude/sign, and floor/fallback edges; scripts/tests/test_costs.py:70 |
| `scripts/tests/test_cover_basis_carry_forward.py` | GOOD | NONE | Covers sticky-basis-carry-forward P0 journal/basis invariant: partial cover, unchanged size, add/grow, no-prior, direction-flip, no-drift; scripts/tests/test_cover_basis_carry_forward.py:47 |
| `scripts/tests/test_covered_call_detection.py` | GOOD | NONE | Behavior-level tests for covered-call grouping across ib_sync and portfolio_report with defined/undefined risk assertions, positive and negative cases; scripts/tests/test_covered_call_detection.py:34 |
| `scripts/tests/test_cri_client_id.py` | GOOD | NONE | Small but meaningful contract test: client-id pools unique, non-zero, disjoint from known script IDs; scripts/tests/test_cri_client_id.py:17 |
| `scripts/tests/test_cri_scan.py` | GOOD | NONE | Extensive coverage of CRI scanner: COR1M source precedence, official Cboe close windows, score components with boundary checks, crash trigger AND-logic, and post-close snapshot synthesis; scripts/tests/test_cri_scan.py:229 |
| `scripts/tests/test_cta_share_history.py` | GOOD | NONE | Regression suite pinning that CTA share copy differs by measured content (digit-stripped prose comparison), regime persistence counted not asserted, no-fabrication guards; scripts/tests/test_cta_share_history.py:95 |
| `scripts/tests/test_cta_share_llm_copy.py` | GOOD | NONE | LLM numeric-firewall tested via injected fake callers (no real network), fabricated figure rejection, graceful degradation on timeout/API error/missing key; scripts/tests/test_cta_share_llm_copy.py:88 |
| `scripts/tests/test_cta_share_narrative.py` | GOOD | NONE | Direct regression for a real published-copy incident: direction-aware assessment, card/tweet agreement, no-fabrication and no-em-dash checks; scripts/tests/test_cta_share_narrative.py:117 |
| `scripts/tests/test_cta_sync_health.py` | GOOD | NONE | Health ledger classify/validate/lock functions tested with real tmp_path files and both stale-PID and live-PID lock contention; scripts/tests/test_cta_sync_health.py:149 |
| `scripts/tests/test_cta_sync_runtime.py` | GOOD | NONE | run_cta_sync retry/backoff/health-record behavior tested via injected fake runner and sleep_fn, verifying attempt counts and persisted JSON health; scripts/tests/test_cta_sync_runtime.py:91 |
| `scripts/tests/test_cta_sync_service.py` | GOOD | NONE | Pure scheduling math for trading-day close cutoff and ET->local launchd calendar conversion, deterministic fixed datetimes; scripts/tests/test_cta_sync_service.py:9 |
| `scripts/tests/test_daemon_state_dual_write.py` | GOOD | NONE | Dual-write daemon_state coverage including DB-failure fallback to disk, BaseException driver panic isolation per-handler, and operator-termination signals not swallowed; scripts/tests/test_daemon_state_dual_write.py:102 |
| `scripts/tests/test_darkpool_cache.py` | GOOD | NONE | Pins the P0 UW-load-reduction contract: prior days cached/immutable, today never cached, empty payloads not cached, and an integration test proving fetch_flow re-fetch avoidance; scripts/tests/test_darkpool_cache.py:109 |
| `scripts/tests/test_data_refresh.py` | GOOD | NONE | Regression pinning that soft scan failures do not fail the systemd unit, plus gex --no-mq wiring check; scripts/tests/test_data_refresh.py:28 |
| `scripts/tests/test_db_client_pytest_guard.py` | GOOD | NONE | Regression for the pytest-poisoning production-write incident; verifies guard blocks real connections under PYTEST_CURRENT_TEST unless explicit override; scripts/tests/test_db_client_pytest_guard.py:43 |
| `scripts/tests/test_db_readers.py` | GOOD | NONE | Real in-memory sqlite with actual migrations exercises canonical reader SQL, including an EXPLAIN QUERY PLAN check that idx_journal_effective_at is used instead of a temp B-tree sort; scripts/tests/test_db_readers.py:198 |
| `scripts/tests/test_db_replica_opt_in.py` | GOOD | NONE | DUR-07 replica-opt-in default fully pinned: clean env, explicit opt-in with warning, legacy kill switch precedence, and pytest never taking the replica branch even with opt-in; scripts/tests/test_db_replica_opt_in.py:121 |
| `scripts/tests/test_db_retention.py` | GOOD | NONE | Retention policy SQL-shape and run_retention_sweep behavior tested including rowcount edge cases and per-table HTTP-sweep failure collection; scripts/tests/test_db_retention.py:124 |
| `scripts/tests/test_db_writers_sql_shape.py` | GOOD | NONE | 8 high-frequency writers exercised against a real in-memory sqlite schema from actual migrations, covering upsert-vs-insert, retention pruning, batched delete-before, and scan_snapshot routing; scripts/tests/test_db_writers_sql_shape.py:428 |
| `scripts/tests/test_demo_seed_guard.py` | GOOD | NONE | Prod-URL guard (SystemExit) and synthetic demo dataset reconciliation (MV/basis/unrealized-pnl arithmetic) both verified; scripts/tests/test_demo_seed_guard.py:51 |
| `scripts/tests/test_demo_trial.py` | GOOD | NONE | Trial-expiry trading-day math tested across weekends, Juneteenth, and July 4th observed holiday, plus exact boundary (16:00 ET) active/inactive checks; scripts/tests/test_demo_trial.py:34 |
| `scripts/tests/test_discover.py` | GOOD | NONE | Darkpool-day scoring and market-open calendar covered with real edge cases (canceled trades, capped strength), plus a targeted regression that discovery_time is tz-aware for correct client-side parsing; scripts/tests/test_discover.py:176 |
| `scripts/tests/test_discover_parallel.py` | IMPROVE | P2 | Two tests infer real ThreadPoolExecutor parallelism from distinct thread idents plus a real time.sleep(0.02) as informal synchronization; scripts/tests/test_discover_parallel.py:64 |
| `scripts/tests/test_earnings_dates.py` | GOOD | NONE | Earnings-window service covered thoroughly: ET day-boundary correctness under a fixed injected now, same-day AMC vs already-reported skip, fraction-vs-percent normalization, disk cache success/skip-on-error; scripts/tests/test_earnings_dates.py:203 |
| `scripts/tests/test_env_loading.py` | GOOD | NONE | Env-load-order contract for IB_GATEWAY_HOST/PORT covered including .env vs .env.ib-mode override precedence and missing-overlay fallback, via isolated module reloads; scripts/tests/test_env_loading.py:63 |
| `scripts/tests/test_evaluate.py` | GOOD | NONE | Deterministic fixture-driven milestone tests with mocked I/O (scripts/tests/test_evaluate.py:425-452); assertions target real gate outcomes (evaluate.py:317-332). |
| `scripts/tests/test_exit_order_service.py` | GOOD | NONE | In-memory sqlite journal fixture (scripts/tests/test_exit_order_service.py:10-28) verifies real payload mutation, not mocks of the module under test. |
| `scripts/tests/test_fetch_analyst_ratings.py` | GOOD | NONE | Signal calc tested with concrete threshold fixtures (scripts/tests/test_fetch_analyst_ratings.py:20-70); cache TTL tested with real datetime deltas, not frozen clock hacks. |
| `scripts/tests/test_fetch_catalysts.py` | GOOD | NONE | Fixed injected 'now' (scripts/tests/test_fetch_catalysts.py:26) makes days_until deterministic; covers pagination stop and partial-source-failure tolerance. |
| `scripts/tests/test_fetch_event_odds.py` | GOOD | NONE | Pure math + mocked client/fetch_options with injected now (scripts/tests/test_fetch_event_odds.py:24,114-136); covers failure tolerance and empty-cache gating. |
| `scripts/tests/test_fetch_flow.py` | GOOD | NONE | Precise numeric assertions on flow_strength/dp_buy_ratio (scripts/tests/test_fetch_flow.py:118-145) and explicit UW error-class retry policy coverage (scripts/tests/test_fetch_flow.py:332-439). |
| `scripts/tests/test_fetch_informed_flow.py` | GOOD | NONE | Normalizes three UW surfaces with fixed now (scripts/tests/test_fetch_informed_flow.py:25,100-103); covers partial failure and don't-cache-empty (scripts/tests/test_fetch_informed_flow.py:168-207). |
| `scripts/tests/test_fetch_market_calendar.py` | GOOD | NONE | Small pure-function test of merge_days precedence and sort order (scripts/tests/test_fetch_market_calendar.py:10-26). |
| `scripts/tests/test_fetch_options.py` | GOOD | NONE | Covers bias thresholds, IV normalization branch (decimal vs percent) (scripts/tests/test_fetch_options.py:88-127), and confidence blending via boundary mocks. |
| `scripts/tests/test_fetch_ticker.py` | GOOD | NONE | Cache roundtrip on tmp_path, liquidity threshold branches asserted with concrete data (scripts/tests/test_fetch_ticker.py:118-142). |
| `scripts/tests/test_fetch_x_watchlist.py` | GOOD | NONE | Sentiment/ticker-extraction covered with concrete phrase fixtures and confidence bucket assertions (scripts/tests/test_fetch_x_watchlist.py:63-121); writer args checked precisely (scripts/tests/test_fetch_x_watchlist.py:136-141). |
| `scripts/tests/test_fetcher_timers.py` | GOOD | NONE | Static launchd plist contract validated via plistlib parse + required keys (scripts/tests/test_fetcher_timers.py:51-81); catches config drift, not implementation detail. |
| `scripts/tests/test_flat_json_source_truth_contract.py` | GOOD | NONE | Static grep contract enforcing Turso-only writes across trading scripts with explicit documented exceptions (scripts/tests/test_flat_json_source_truth_contract.py:21-71); guards a real P0 architectural rule. |
| `scripts/tests/test_flex_query_runtime.py` | IMPROVE | P2 | Test name claims 'imports without requests dependency' but only asserts hasattr, never checking sys.modules for 'requests' absence (scripts/tests/test_flex_query_runtime.py:4-11). |
| `scripts/tests/test_flow_history.py` | GOOD | NONE | Real migrations loaded into in-memory sqlite (scripts/tests/test_flow_history.py:22-57); asserts writer error is swallowed and logged via capsys, not just not.toThrow (scripts/tests/test_flow_history.py:106-120). |
| `scripts/tests/test_flow_surprise.py` | GOOD | NONE | Deterministic baseline forecaster forced via monkeypatch (scripts/tests/test_flow_surprise.py:16-21); numeric PIT thresholds and ranked-extremity ordering both asserted (scripts/tests/test_flow_surprise.py:114-118). |
| `scripts/tests/test_forecast_backtest.py` | GOOD | NONE | Pure math tests with hand-computed expected values (scripts/tests/test_forecast_backtest.py:28-39,83-92) plus a perfect-forecaster harness proving zero pinball loss end to end. |
| `scripts/tests/test_forecast_scan_scoring.py` | GOOD | NONE | Scoring math compared relatively and by exact band values (scripts/tests/test_forecast_scan_scoring.py:37-64); graceful-degradation path asserts no exception and no forecast key on chronos failure (scripts/tests/test_forecast_scan_scoring.py:138-153). |
| `scripts/tests/test_forecast_writers.py` | GOOD | NONE | Real migrations against in-memory sqlite verify upsert-replace semantics and lookback filtering with exact row values (scripts/tests/test_forecast_writers.py:89-124). |
| `scripts/tests/test_forecasting_provisioning.py` | GOOD | NONE | Static regex/text contract ensuring scipy is pinned and reachable by the provisioner (scripts/tests/test_forecasting_provisioning.py:20-49); guards a real prior incident. |
| `scripts/tests/test_free_trade_analyzer.py` | GOOD | NONE | Hand-verified P&L/cost math with worked comments (scripts/tests/test_free_trade_analyzer.py:37-107); portfolio load tests assert fail-closed on IB sync failure (scripts/tests/test_free_trade_analyzer.py:302-314). |
| `scripts/tests/test_gamma_rotation_gap.py` | GOOD | NONE | Synthetic diverging series drives a real state-classification assertion (scripts/tests/test_gamma_rotation_gap.py:42-61); writer upsert-replace verified against real sqlite rows (scripts/tests/test_gamma_rotation_gap.py:87-92). |
| `scripts/tests/test_garch_convergence_scanner.py` | GOOD | NONE | Small, focused pure-function tests on pairing and envelope shape (scripts/tests/test_garch_convergence_scanner.py:7-33). |
| `scripts/tests/test_generate_cta_share.py` | GOOD | NONE | Regression-driven DB-vs-disk freshness precedence with a fake cursor mirroring the real libsql-experimental API (scripts/tests/test_generate_cta_share.py:56-72); asserts visible STALE warning text (scripts/tests/test_generate_cta_share.py:211-218). |
| `scripts/tests/test_gex_scan.py` | GOOD | NONE | Thorough pure-function coverage of GEX flip/levels/bias math with real edge cases and a persist-then-enrich pipeline test; scripts/tests/test_gex_scan.py:134-146 verifies last-crossing logic precisely. |
| `scripts/tests/test_health_lite_loop_lag.py` | GOOD | NONE | Small focused test of loop_lag_ms field/measurement with real async roundtrip; scripts/tests/test_health_lite_loop_lag.py:42-45 measures actual coroutine scheduling latency. |
| `scripts/tests/test_health_probe.py` | GOOD | NONE | Exhaustive contract testing of probe classification incl. fail-closed malformed payloads; scripts/tests/test_health_probe.py:611-654 parametrizes malformed freshness checks to prove fail-closed behavior. |
| `scripts/tests/test_health_service.py` | GOOD | NONE | Uses real localhost sockets/HTTP servers instead of mocks for genuine transport coverage; scripts/tests/test_health_service.py:70-87 binds real sockets for probe_tcp up/down states. |
| `scripts/tests/test_host_metrics_sampler.py` | GOOD | NONE | Pure parser tests from fixture text plus Turso write-path mocked at module seam per repo convention; scripts/tests/test_host_metrics_sampler.py:105-137 verifies fallback-to-jsonl on Hrana failure. |
| `scripts/tests/test_ib_2fa_lock.py` | GOOD | NONE | Filesystem-backed cross-process lock tested incl. real thread-barrier concurrency race and CLI subprocess invocation; scripts/tests/test_ib_2fa_lock.py:92-125 uses a threading.Barrier to force the actual race, not a mocked one. |
| `scripts/tests/test_ib_chain_retry.py` | GOOD | NONE | Deterministic fake IBClient/IB objects (not over-mocked) drive farm-flap retry/backoff behavior; scripts/tests/test_ib_chain_retry.py:37-51 models real retry semantics via a hand-written fake. |
| `scripts/tests/test_ib_client.py` | IMPROVE | P2 | Several TestErrorHandling/disconnect tests call code with no assertion beyond 'should not raise'; scripts/tests/test_ib_client.py:282-288,1014-1024,1027-1037,1039-1049,1052-1063 assert nothing. |
| `scripts/tests/test_ib_daily_pnl.py` | GOOD | NONE | Covers single/multi-leg aggregation, None-propagation, zero-vs-None, and DBL_MAX sentinel filtering for a P&L-critical path; scripts/tests/test_ib_daily_pnl.py:117-130 pins the exact IB sentinel filter. |
| `scripts/tests/test_ib_error_handling.py` | GOOD | NONE | Pacing/invalid-contract/error-code behavior asserted precisely against real counters and sets; scripts/tests/test_ib_error_handling.py:65-79 checks the pacing-retry cap is enforced, not just 'no throw'. |
| `scripts/tests/test_ib_gateway_no_unmanaged_restart.py` | GOOD | NONE | Real subprocess execution of the docker/ibc wrapper scripts with fake docker binary proves the 2FA-lease reentrancy guard end to end; scripts/tests/test_ib_gateway_no_unmanaged_restart.py:93-127 runs the actual shell wrapper twice and asserts the second call is refused. |
| `scripts/tests/test_ib_helpers.py` | GOOD | NONE | Pure-function table tests for structure detection/pricing, e.g. scripts/tests/test_ib_helpers.py:201-209 exercises real edge-case combo shapes with exact assertions. |
| `scripts/tests/test_ib_insync_bounded.py` | GOOD | P0 | AST-based repo-wide lint enforcing asyncio.wait_for on every unbounded ib_insync await; scripts/tests/test_ib_insync_bounded.py:217-227 fails CI on any violation found anywhere in scripts/. |
| `scripts/tests/test_ib_option_chain.py` | GOOD | P1 | Regression tests for adjusted-trading-class chain selection using real SimpleNamespace fixtures; scripts/tests/test_ib_option_chain.py:25-36 verifies canonical-class union-strikes logic. |
| `scripts/tests/test_ib_order_manage.py` | GOOD | P0 | Covers cancel/modify reconnect-as-original-clientId, reqAutoOpenOrders binding, BAG NonGuaranteed, outsideRth flag; scripts/tests/test_ib_order_manage.py:122-150 pins the manual-TWS-order cancel fix. |
| `scripts/tests/test_ib_orders_dual_write.py` | GOOD | P0 | Verifies dual-write to Turso skips permId-less orders and records error state on writer exception; scripts/tests/test_ib_orders_dual_write.py:118-131 asserts error service_health row on writer RuntimeError. |
| `scripts/tests/test_ib_place_order_after_hours.py` | GOOD | P0 | Small but precise: pins non-blocking IB codes (399/2109) vs real rejection codes (201/202/10147/10148) that must still error; scripts/tests/test_ib_place_order_after_hours.py:22-25. |
| `scripts/tests/test_ib_reconcile.py` | GOOD | P0 | Regression for the EWY collar merge bug; scripts/tests/test_ib_reconcile.py:45-61 proves distinct-contract grouping keeps net_quantity nonzero instead of falsely CLOSED. |
| `scripts/tests/test_ib_reconcile_dual_write.py` | GOOD | P1 | Verifies reconciliation report persists to Turso with timestamp fallback and swallows writer failures; scripts/tests/test_ib_reconcile_dual_write.py:49-57. |
| `scripts/tests/test_ib_resilient.py` | GOOD | P1 | Subscription tracking + exponential backoff reconnect with capped delay and restore-on-reconnect; scripts/tests/test_ib_resilient.py:260-278 pins the 30s cap. |
| `scripts/tests/test_ib_sync_basis_guard.py` | GOOD | P0 | Guards journal-vs-IB basis mismatch (the MU 1050C -128% P&L bug) and abs()-negative-basis regressions with first-principles arithmetic; scripts/tests/test_ib_sync_basis_guard.py:224-273. |
| `scripts/tests/test_ib_watchdog.py` | GOOD | P0 | Deterministic run_cycle driver over mocked /health payloads pins hang-classifier and 3-cycle restart threshold; scripts/tests/test_ib_watchdog.py:196-204. |
| `scripts/tests/test_ib_watchdog_2fa_lock.py` | GOOD | P0 | Pins the 2FA push-stacking fix via a real file-backed lock (utils.ib_2fa_lock), verifying watchdog never advances another holder's lease; scripts/tests/test_ib_watchdog_2fa_lock.py:106-121. |
| `scripts/tests/test_ib_watchdog_2fa_storm_2026_07_05.py` | GOOD | P0 | 48-cycle simulated incident replay with injected clock proves zero restarts and bounded backoff/cap; scripts/tests/test_ib_watchdog_2fa_storm_2026_07_05.py:140-174 and 298-318. |
| `scripts/tests/test_ib_watchdog_dur10.py` | GOOD | P0 | Full decision table for API-up/down x gateway alive/dead/wedged/unknown plus quiet-window edges and bounded hrana write timeout; scripts/tests/test_ib_watchdog_dur10.py:434-445 proves a hanging transport is bounded to <5s via injected timeout. |
| `scripts/tests/test_ib_watchdog_loop_2026_06_15.py` | GOOD | P0 | Replays the 15x restart-loop incident with injected clock, proving zero restarts under stand-down and <=2/hour cap under genuine stuck-2FA; scripts/tests/test_ib_watchdog_loop_2026_06_15.py:264-283. |
| `scripts/tests/test_ib_watchdog_market_gate.py` | GOOD | P1 | Pure data_plane_window_active tests over explicit UTC datetimes plus fail-open-on-calendar-error case; scripts/tests/test_ib_watchdog_market_gate.py:68-77. |
| `scripts/tests/test_ib_watchdog_unhangable.py` | GOOD | P0 | Proves real SIGALRM abort + bounded sub-steps abandon genuinely blocking calls (real time.sleep(30) in a thread) within seconds; scripts/tests/test_ib_watchdog_unhangable.py:75-82,162-189. |
| `scripts/tests/test_ib_whatif_margin.py` | GOOD | P0 | Covers what-if margin parsing incl. IB sentinel values, error-360 preview isolation from later live placement, and that live place never returns projected margin; scripts/tests/test_ib_whatif_margin.py:159-176. |
| `scripts/tests/test_incident_responder.py` | GOOD | P1 | Selection/state/command-building tests with fixed injected NOW; covers transient-window gating and rsync exclude flags protecting dedup state; scripts/tests/test_incident_responder.py:106-115. |
| `scripts/tests/test_incident_watchdog.py` | GOOD | P1 | Pure classify()/parse()/record_cycle() tests with fixed NOW datetime, covering P1 destroy-storm, P2 degraded, and in-flight-deploy suppression; scripts/tests/test_incident_watchdog.py:104-127. |
| `scripts/tests/test_incident_watchdog_offhours_suppression.py` | GOOD | NONE | Pure classify() with injected now; window-relative trading-day derivation (scripts/tests/test_incident_watchdog_offhours_suppression.py:62-73) not hardcoded dates; RED/GREEN suppression pairs both directions (scripts/tests/test_incident_watchdog_offhours_suppression.py:236,274). |
| `scripts/tests/test_incremental_sync.py` | GOOD | NONE | MagicMock used only as an IB position boundary fake (scripts/tests/test_incremental_sync.py:22-30); asserts real return values, covers add/remove/qty-change/empty/STK N-A-expiry branches. |
| `scripts/tests/test_index_constituents.py` | GOOD | NONE | No network; _fetch_csv monkeypatched at the boundary (scripts/tests/test_index_constituents.py:99); covers fresh/fallback/stale/implausible/seed/error fallback chain fully. |
| `scripts/tests/test_index_symbols.py` | GOOD | NONE | Pure table/lookup tests plus real ib_insync contract construction (scripts/tests/test_index_symbols.py:73-84), gracefully skipped if ib_insync absent. |
| `scripts/tests/test_journal_basis.py` | GOOD | NONE | P0 lot-matched basis: driver-faithful tuple-row fakes mirroring real libsql cursor shape (scripts/tests/test_journal_basis.py:23-44), exact pinned dollar arithmetic (scripts/tests/test_journal_basis.py:182-189,320-334). |
| `scripts/tests/test_journal_basis_accessors.py` | GOOD | NONE | Exhaustive branch coverage of journal_basis internals with exact-arithmetic pins on open-basis lot matching (scripts/tests/test_journal_basis_accessors.py:643-825), including tuple/dict/attr row parity. |
| `scripts/tests/test_journal_expiry_normalization.py` | GOOD | NONE | Real sqlite3 in-memory DB through the actual writer chokepoint (scripts/tests/test_journal_expiry_normalization.py:47-84,88-105), not a mock of the module under test. |
| `scripts/tests/test_journal_rehydrate.py` | GOOD | NONE | P0 journal import path: idempotency, dedupe, assignment/exercise regression (scripts/tests/test_journal_rehydrate.py:801-856), exact stock/option round-trip P&L pins (scripts/tests/test_journal_rehydrate.py:495-499). |
| `scripts/tests/test_jvm_forensics.py` | GOOD | NONE | FakeRunner/FakeClock injected doubles for subprocess+time (scripts/tests/test_jvm_forensics.py:36-87), verifies every subprocess call is bounded (scripts/tests/test_jvm_forensics.py:121-126) and watchdog hook fire-once/rearm semantics. |
| `scripts/tests/test_kelly_extended.py` | GOOD | NONE | Exact hand-derived pinned formula/threshold values (scripts/tests/test_kelly_extended.py:24-30,100-108) plus real subprocess CLI invocation for dollar-sizing path (scripts/tests/test_kelly_extended.py:248-259). |
| `scripts/tests/test_kelly_vectorized.py` | GOOD | NONE | Independently hand-derived expected values (not re-derived via kelly()) explicitly to avoid survivor mutants (scripts/tests/test_kelly_vectorized.py:1-14,36-41); mutation-kill comments per assertion. |
| `scripts/tests/test_knowledge_golden.py` | GOOD | NONE | Real libsql :memory: DB with actual migration applied (scripts/tests/test_knowledge_golden.py:44-51); regex-vs-substring, scope pass-through, embedder-degrade-to-FTS all covered; shipped golden file schema pinned (scripts/tests/test_knowledge_golden.py:282-296). |
| `scripts/tests/test_knowledge_mcp.py` | GOOD | NONE | Real libsql :memory: DB, asserts read-only source pin via regex scan for write SQL keywords (scripts/tests/test_knowledge_mcp.py:420-429) and side-effect-free import via get_db explode monkeypatch (scripts/tests/test_knowledge_mcp.py:433-439). |
| `scripts/tests/test_knowledge_pipeline.py` | GOOD | NONE | Real sqlite/libsql migration DB, mocked network at requests.post boundary, asserts secret-scrub content at scripts/tests/test_knowledge_pipeline.py:126-132. |
| `scripts/tests/test_knowledge_retrieve.py` | GOOD | NONE | Real FTS5/vector DB, hand-computed RRF math verified at scripts/tests/test_knowledge_retrieve.py:122-128, exercises scope/source/cap/rerank edge cases. |
| `scripts/tests/test_knowledge_sources.py` | GOOD | NONE | Window-relative dates only (repo standard), real mini-schema DB, tests prune-authority hazard from a real incident at scripts/tests/test_knowledge_sources.py:203-214. |
| `scripts/tests/test_knowledge_store.py` | GOOD | NONE | Real migration-applied libsql DB, verifies FTS mirror sync and content-hash boundary collisions at scripts/tests/test_knowledge_store.py:92-96. |
| `scripts/tests/test_leap_scanner.py` | GOOD | NONE | Deterministic (random.seed(42) at scripts/tests/test_leap_scanner.py:33), covers the real null-coercion regression at scripts/tests/test_leap_scanner.py:224-280. |
| `scripts/tests/test_llm_token_index.py` | IMPROVE | P1 | Solid unit coverage of blend/median/normalize/fetch/persistence, but docstring at scripts/tests/test_llm_token_index.py:9 promises exit-code coverage and main() (scripts/llm_token_index.py:258) is never called or asserted anywhere in the file. |
| `scripts/tests/test_local_scheduler_reliability.py` | GOOD | NONE | Real shell scripts run via subprocess with fake bin/launchctl/python3.13 fixtures; deterministic fixture output drives assertions, e.g. scripts/tests/test_local_scheduler_reliability.py:115-130. |
| `scripts/tests/test_margin_debt.py` | GOOD | NONE | Ground-truth values read from checked-in real FINRA/NYSE fixtures per scripts/tests/test_margin_debt.py:1-8, sqlite migration exercised directly at scripts/tests/test_margin_debt.py:266-289. |
| `scripts/tests/test_market_calendar.py` | GOOD | NONE | Pure function, fixed calendar-fact inputs (not wall-clock-relative), e.g. Juneteenth check at scripts/tests/test_market_calendar.py:22-24. |
| `scripts/tests/test_menthorq_client.py` | IMPROVE | P1 | Weak call-count-only assertion at scripts/tests/test_menthorq_client.py:142; internal scrape/parse methods (_scrape_eod_fields, _extract_via_vision) stubbed everywhere so their real logic is never exercised in this file. |
| `scripts/tests/test_menthorq_client_timeouts.py` | REFACTOR | P1 | Regex-greps raw source text for literals/constant names (scripts/tests/test_menthorq_client_timeouts.py:31,57,72-77) instead of exercising behavior; a behavior-preserving rename/refactor of _login breaks it. |
| `scripts/tests/test_menthorq_cta.py` | GOOD | NONE | Pure computation + tmp_path cache round-trip, covers a real UnboundLocalError regression at scripts/tests/test_menthorq_cta.py:264-285. |
| `scripts/tests/test_menthorq_dashboard_bootstrap.py` | GOOD | NONE | Hand-rolled fake Playwright module drives real _bootstrap_dashboard_session logic; asserts real WAF-UA and OAuth-redirect invariants at scripts/tests/test_menthorq_dashboard_bootstrap.py:203-226 from a documented live incident. |
| `scripts/tests/test_menthorq_dashboard_client.py` | GOOD | NONE | Boundary-fake HTTP session pins auth/timeout sanitization, malformed-array rejection, jar-expiry fast-fail (scripts/tests/test_menthorq_dashboard_client.py:180,229,362). |
| `scripts/tests/test_menthorq_integration.py` | IMPROVE | P1 | Opt-in integration suite but assertions are almost all isinstance/len>0 (scripts/tests/test_menthorq_integration.py:99,104,109,280) so a wrong-but-well-typed payload passes. |
| `scripts/tests/test_menthorq_skill.py` | GOOD | NONE | Deterministic doc/config structure checks against real files on disk (scripts/tests/test_menthorq_skill.py:34,100,151). |
| `scripts/tests/test_microstructure.py` | GOOD | NONE | Pure-function tests with hand-derived known values for imbalance/microprice, plus degenerate zero/empty edge cases (scripts/tests/test_microstructure.py:28,61,80). |
| `scripts/tests/test_migrate.py` | GOOD | NONE | Pure-function split/list tests plus deterministic retry-backoff tests with sleep captured via monkeypatch, not real sleep (scripts/tests/test_migrate.py:106,178,190). |
| `scripts/tests/test_migration_0011.py` | GOOD | NONE | Content-pins migration SQL against the real splitter to prevent trigger-body mid-split regressions (scripts/tests/test_migration_0011.py:62,71). |
| `scripts/tests/test_migration_0012.py` | GOOD | NONE | Same pattern as 0011/0013: idempotency + column + splitter-survival checks against the real SQL file (scripts/tests/test_migration_0012.py:58,74). |
| `scripts/tests/test_migration_0013.py` | GOOD | NONE | Idempotency + append-only schema + splitter-survival checks against the real SQL file (scripts/tests/test_migration_0013.py:59,70,80). |
| `scripts/tests/test_monitor_daemon/test_base_handler.py` | GOOD | NONE | Covers is_due/run/state-roundtrip contract with a concrete subclass; timestamp check uses a generous <1s window not a sleep-based sync (scripts/tests/test_monitor_daemon/test_base_handler.py:96,98). |
| `scripts/tests/test_monitor_daemon/test_base_handler_contract.py` | GOOD | NONE | Pins the error-vs-ok latch contract post-incident plus an AST-based lint that fails CI if any handler returns status:error instead of raising (scripts/tests/test_monitor_daemon/test_base_handler_contract.py:137,285). |
| `scripts/tests/test_monitor_daemon/test_base_handler_structural_heartbeat.py` | GOOD | NONE | Verifies structural heartbeat on ok/skip/error/raise plus self-recorded-cycle non-clobber and best-effort DB-failure swallow (scripts/tests/test_monitor_daemon/test_base_handler_structural_heartbeat.py:100,124,158). |
| `scripts/tests/test_monitor_daemon/test_cash_flow_sync_cadence.py` | GOOD | NONE | Deterministic clock injection via patched _now_utc (no real sleep) covers ET window, weekend/holiday skip, DST boundaries, circuit-breaker composition (scripts/tests/test_monitor_daemon/test_cash_flow_sync_cadence.py:63,145,197). |
| `scripts/tests/test_monitor_daemon/test_cash_flow_sync_timeout_retry_budget.py` | GOOD | NONE | Simulates a full ET evening of daemon cycles via patched clock (no real sleep) to pin a bounded Flex SendRequest budget against a documented incident (scripts/tests/test_monitor_daemon/test_cash_flow_sync_timeout_retry_budget.py:97,127). |
| `scripts/tests/test_monitor_daemon/test_daemon.py` | IMPROVE | P2 | test_is_market_hours_true_during_trading builds two unused nested datetime mocks then asserts via a direct _is_market_hours_time call, never exercising the mocked path (scripts/tests/test_monitor_daemon/test_daemon.py:63-75). |
| `scripts/tests/test_monitor_daemon/test_exit_orders.py` | GOOD | NONE | FakeJournalDb boundary fake exercises pending-order load, 40% gap threshold edge, and journal status/order_id update after placement (scripts/tests/test_monitor_daemon/test_exit_orders.py:147,264,311). |
| `scripts/tests/test_monitor_daemon/test_fill_monitor.py` | GOOD | NONE | Covers new/partial/complete fill detection, journal mirror-write, dedupe-on-repeat, and DB-write-failure non-crash (scripts/tests/test_monitor_daemon/test_fill_monitor.py:216,238,256). |
| `scripts/tests/test_monitor_daemon/test_fill_monitor_action.py` | IMPROVE | P1 | Only 5 assertions cover _side_to_action/_structure_label; no SELL_TO_CLOSE-for-short case or STK sec_type variant despite the OPT-vs-STK branch existing (scripts/tests/test_monitor_daemon/test_fill_monitor_action.py:10-33). |
| `scripts/tests/test_monitor_daemon/test_handler_heartbeat.py` | GOOD | NONE | Pins ok/error service_health heartbeat across fill_monitor, exit_orders, journal_sync, cash_flow_sync, flex_token_check with a real-module-preserving fake db.writer (scripts/tests/test_monitor_daemon/test_handler_heartbeat.py:42,123,254). |
| `scripts/tests/test_monitor_daemon/test_journal_gap_sli.py` | GOOD | NONE | Deterministic clock/DB injection covers zero/nonzero gaps, BAG-parent skip, min-age fresh-fill filtering, composite exec-id coverage, and an alert-only never-writes assertion (scripts/tests/test_monitor_daemon/test_journal_gap_sli.py:181,215,298). |
| `scripts/tests/test_monitor_daemon/test_journal_reconcile.py` | GOOD | NONE | Real gap-detection logic (exec_id parts, ±1-day fallback, BAG skip) asserted precisely; DB unavailable raises per scripts/tests/test_monitor_daemon/test_journal_reconcile.py:324-329. |
| `scripts/tests/test_monitor_daemon/test_journal_sync.py` | GOOD | NONE | Exercises real IB fill->journal labeling incl. sell-close vs sell-to-open regression and checksum-recovery path; window-relative dates at scripts/tests/test_monitor_daemon/test_journal_sync.py:772. |
| `scripts/tests/test_monitor_daemon/test_menthorq_session_check.py` | GOOD | NONE | Window-relative cookie expiries (NOW+timedelta), covers 2026-08-07 incident regression precisely at scripts/tests/test_monitor_daemon/test_menthorq_session_check.py:76-91. |
| `scripts/tests/test_monitor_daemon/test_post_close_grace.py` | GOOD | NONE | Drives real MonitorDaemon.run_once via pinned clock; window-relative trading-day/holiday helpers at scripts/tests/test_monitor_daemon/test_post_close_grace.py:61-89. |
| `scripts/tests/test_monitor_daemon/test_service_health_prune_handler.py` | GOOD | NONE | Covers success, raise-on-failure (no latch), missing-symbol skip, and negative assertion that unrelated prune fn is not called at scripts/tests/test_monitor_daemon/test_service_health_prune_handler.py:74-84. |
| `scripts/tests/test_monitor_daemon/test_throttle_backoff.py` | GOOD | NONE | Pure state-machine tests with explicit now_utc injection, no real clock; escalation ladder and cap verified at scripts/tests/test_monitor_daemon/test_throttle_backoff.py:66-81. |
| `scripts/tests/test_naked_short_audit.py` | IMPROVE | P1 | Entire TestFindNakedShortViolations class (core violation-detection logic) is class-level skipped at scripts/tests/test_naked_short_audit.py:93, leaving only cancel/dry-run wiring under active test. |
| `scripts/tests/test_nav_history.py` | GOOD | NONE | Hand-verified equity-curve math incl. drawdown-from-peak and OCC option-id parsing at scripts/tests/test_nav_history.py:96-107. |
| `scripts/tests/test_nightly_forecast.py` | GOOD | NONE | setattr on real submodules (not sys.modules swap) documented and justified at scripts/tests/test_nightly_forecast.py:6-11; verifies per-step failure isolation and ordering. |
| `scripts/tests/test_no_sync_libsql_in_api.py` | GOOD | NONE | AST-based lint with both the real repo tree scan (test_no_sync_libsql_in_api_tree) and fixture-based positive/negative proofs the checker actually fires, e.g. scripts/tests/test_no_sync_libsql_in_api.py:159-167. |
| `scripts/tests/test_orders_sync_loop.py` | GOOD | NONE | Covers market-hours/test-mode/pool-disconnected skip guards and exception-swallow-and-continue loop behavior at scripts/tests/test_orders_sync_loop.py:126-142. |
| `scripts/tests/test_paper_fills.py` | GOOD | NONE | Cross-reconciles paper-fill slippage against the live costs.py model exactly, e.g. scripts/tests/test_paper_fills.py:39-47. |
| `scripts/tests/test_paper_matcher.py` | GOOD | NONE | Correct exchange-semantics matching for LIMIT/STOP both sides incl. exact-at-limit and pre-triggered-stop edge cases at scripts/tests/test_paper_matcher.py:126-137. |
| `scripts/tests/test_paper_place_route.py` | GOOD | NONE | Covers marketable-fill, non-marketable-no-persist, idempotency key, and route error/validation paths at scripts/tests/test_paper_place_route.py:60-79,151-174. |
| `scripts/tests/test_performance_explainer_report.py` | GOOD | NONE | Skips cleanly when the gitignored runtime cache is absent (scripts/tests/test_performance_explainer_report.py:31-32) instead of faking data; asserts real contract fields. |
| `scripts/tests/test_performance_lock.py` | GOOD | NONE | Verifies dedup/in-flight piggyback with concurrent asyncio.gather and asserts single underlying build call at scripts/tests/test_performance_lock.py:141-153; atomic-write no-temp-file check at :169. |
| `scripts/tests/test_phase2_writers.py` | GOOD | NONE | Runs real SQL migrations against in-memory sqlite and asserts actual row state/idempotency, e.g. scripts/tests/test_phase2_writers.py:158-162 upsert replace-on-key. |
| `scripts/tests/test_phase34_writers.py` | GOOD | NONE | Same in-memory-sqlite migration approach; covers replace-session-clears-old-orders semantics at scripts/tests/test_phase34_writers.py:89-101. |
| `scripts/tests/test_phase4_wirings.py` | GOOD | NONE | Verifies dual-write call shapes and disk-fallback-on-DB-failure at scripts/tests/test_phase4_wirings.py:121-138 with a stub writer module (not the module under test). |
| `scripts/tests/test_pnf.py` | GOOD | NONE | Hand-computed P&F ladder/signal cases with explicit reasoning shown in comments, e.g. buy/sell transition walk-through at scripts/tests/test_pnf.py:69-80. |
| `scripts/tests/test_polymarket_client.py` | GOOD | NONE | Injects fake requests.Session, asserts URL/param shape and 404/500 error mapping at scripts/tests/test_polymarket_client.py:134-143. |
| `scripts/tests/test_pool_order_manage.py` | GOOD | NONE | Live-money order cancel/modify: asserts master-client-any-clientId path and VOL-field-reset regression precisely at scripts/tests/test_pool_order_manage.py:213-215. |
| `scripts/tests/test_portfolio_attribution.py` | GOOD | NONE | Full M1-M7 behavior coverage w/ real dict fixtures; scripts/tests/test_portfolio_attribution.py:511 only boundary-mocks the DB reader for the integration test. |
| `scripts/tests/test_portfolio_performance.py` | GOOD | NONE | Behavior tests on real pandas/numpy math (scripts/tests/test_portfolio_performance.py:223-244) plus boundary-mocked IB/UW/cache fetchers; assertions are numerically derived, not blob-matched. |
| `scripts/tests/test_portfolio_refresh_wrapper.py` | REFACTOR | P1 | Greps for literal source strings (scripts/tests/test_portfolio_refresh_wrapper.py:9-11) instead of exercising the script; any behavior-preserving rewrite of the retry logic fails it. |
| `scripts/tests/test_portfolio_report_db_source.py` | GOOD | NONE | Small but exercises real filtering behavior of load_trade_log over journal rows (scripts/tests/test_portfolio_report_db_source.py:12-24). |
| `scripts/tests/test_portfolio_risk.py` | GOOD | NONE | Synthetic correlated/anti-correlated price series drive real correlation-matrix and risk-budget math (scripts/tests/test_portfolio_risk.py:65-91, 102-129); no live IB/DB access. |
| `scripts/tests/test_preset_rebalance.py` | GOOD | NONE | Exercises real HTML/JSON parsing, atomic-write, fail-closed multi-source validation with tmp_path fixtures (scripts/tests/test_preset_rebalance.py:264-306); no live network calls, urlopen mocked at the boundary. |
| `scripts/tests/test_presets_traversal.py` | GOOD | NONE | Security regression pinning containment (not substring-strip) behavior with parametrized traversal payloads (scripts/tests/test_presets_traversal.py:24-47) plus a positive-existence attack test at line 56. |
| `scripts/tests/test_price_cache.py` | GOOD | NONE | Read/write/TTL/prune behavior on tmp_path dirs plus deterministic frozen-datetime market-hours tests (scripts/tests/test_price_cache.py:139-153); no real wall-clock dependence. |
| `scripts/tests/test_ratio_detection.py` | GOOD | NONE | Direct behavior assertions on detect_structure_type/format_structure_description across ratio, plain, and synthetic leg combos (scripts/tests/test_ratio_detection.py:36-77, 124-133). |
| `scripts/tests/test_realized_vol.py` | GOOD | NONE | Numeric parity to 1e-12 against an independent numpy reference implementation across window sizes and edge counts (scripts/tests/test_realized_vol.py:21-24, 34-44). |
| `scripts/tests/test_repair_cri_rvol_cache.py` | GOOD | NONE | Rebuild-payload behavior verified against derived history length, cor1m propagation and change math (scripts/tests/test_repair_cri_rvol_cache.py:38-73). |
| `scripts/tests/test_replica_safe_default.py` | REFACTOR | P1 | Asserts exact opt-in-gate source strings are literally present (scripts/tests/test_replica_safe_default.py:24-53) instead of invoking the DB clients; any semantically-equivalent rewrite of the boolean check fails it. |
| `scripts/tests/test_replica_watchdog.py` | GOOD | NONE | Thorough coverage of heal/throttle/failure/journalctl-unavailable/naive-timestamp paths with subprocess.run mocked at the OS boundary (scripts/tests/test_replica_watchdog.py:213-246, 399-445). |
| `scripts/tests/test_run_cta_sync_wrapper.py` | GOOD | P0 | Real subprocess execution of run_cta_sync.sh with a literal $-containing password to pin the env-file shell-expansion regression (scripts/tests/test_run_cta_sync_wrapper.py:35-93); genuinely catches the class of bug documented in feedback_env_file_shell_expansion.md. |
| `scripts/tests/test_run_portfolio_refresh_retry.py` | FRAGILE | P1 | Uses a real time.sleep(0.5) to coordinate the fake server's late start against the script's first curl attempt (scripts/tests/test_run_portfolio_refresh_retry.py:82); a slow CI runner can invert the intended ordering and flip the assertion. |
| `scripts/tests/test_run_pytest_affected.py` | GOOD | NONE | Direct, deterministic assertions on resolve_pytest_targets over real repo paths (scripts/tests/test_run_pytest_affected.py:6-19). |
| `scripts/tests/test_run_vcg_refresh_wrapper.py` | GOOD | NONE | Real subprocess execution of run_vcg_refresh.sh against a real one-shot HTTP server with a bounded connect-poll (not a sleep race) at scripts/tests/test_run_vcg_refresh_wrapper.py:128-137; covers reachable/unreachable/holiday branches. |
| `scripts/tests/test_rv_ratio_history.py` | GOOD | NONE | Extensive in-memory sqlite coverage of ensure_history's backfill/incremental/splice-detection/session-clamp branches with window-relative dates (scripts/tests/test_rv_ratio_history.py:124-163, 279-327, 333-351). |
| `scripts/tests/test_rv_ratio_math.py` | GOOD | NONE | align_sessions/compute_ratio/classify_divergence/build_payload all checked against independently-derived numpy values, with sigma-boundary edge cases (scripts/tests/test_rv_ratio_math.py:148-170, 252-277). |
| `scripts/tests/test_rv_ratio_scan_cli.py` | GOOD | P0 | Pins stdout-JSON-only/stderr-progress contract and non-fatal Turso failure + disk fallback (scripts/tests/test_rv_ratio_scan_cli.py:80-105, 126-139), matching the repo's stdout-discipline rule for subprocess bridges. |
| `scripts/tests/test_rv_ratio_writer.py` | GOOD | P0 | In-memory sqlite verifies idempotent upsert, per-symbol snapshot replacement, and the chunked multi-row-insert-not-executemany contract for Hrana bounding (scripts/tests/test_rv_ratio_writer.py:178-215). |
| `scripts/tests/test_scan_cache_gate.py` | GOOD | NONE | Session-date resolution and cache-freshness gate covered with explicit fixed datetimes and tmp_path cache files across stale/fresh/force/corrupt paths (scripts/tests/test_scan_cache_gate.py:84-113). |
| `scripts/tests/test_scan_service_health.py` | GOOD | NONE | Boundary-only mocks of db.writer, covers ok/error/never-raises for every migrated scan writer, e.g. scripts/tests/test_scan_service_health.py:96 |
| `scripts/tests/test_scan_ticker_args.py` | GOOD | NONE | Small deterministic pure-function tests for parse_ticker_list covering dedupe/order/empty at scripts/tests/test_scan_ticker_args.py:7-30 |
| `scripts/tests/test_scan_time_timezone.py` | GOOD | NONE | Regression pins tz-aware scan_time emission across vcg/flow/cri_scan with a real behavioral check at scripts/tests/test_scan_time_timezone.py:59 |
| `scripts/tests/test_scanner.py` | GOOD | NONE | analyze_signal scoring covered with concrete flow-data fixtures and score assertions, e.g. scripts/tests/test_scanner.py:36-38 |
| `scripts/tests/test_scanner_parallel.py` | FRAGILE | P2 | Concurrency asserted via real time.sleep(0.05) inside mocked fetch and counting distinct thread idents, scripts/tests/test_scanner_parallel.py:51-59 |
| `scripts/tests/test_scanner_refactor.py` | IMPROVE | P2 | Weak source-text greps stand in for behavior checks, e.g. scripts/tests/test_scanner_refactor.py:16-19 ("watchlist.json" not in source) |
| `scripts/tests/test_scenario_analysis.py` | GOOD | NONE | Portfolio stress math verified with explicit dollar figures and window-relative expiry, e.g. scripts/tests/test_scenario_analysis.py:296-297 |
| `scripts/tests/test_server_lifespan_nonblocking.py` | GOOD | NONE | Deploy-rollback regression: asyncio.wait_for bounds the assertion instead of real sleeps, verified at scripts/tests/test_server_lifespan_nonblocking.py:71 |
| `scripts/tests/test_service_cycle.py` | GOOD | NONE | service_cycle contract (heartbeat-every-exit, error+embargo, no state knob) pinned with monkeypatched writer, scripts/tests/test_service_cycle.py:203-215 |
| `scripts/tests/test_service_health_prune.py` | GOOD | NONE | Retention cutoff math verified against real datetime bounds without touching a DB, scripts/tests/test_service_health_prune.py:78-79 |
| `scripts/tests/test_service_registration_completeness.py` | GOOD | NONE | AST-based conformance check ensures every writer is registered in serviceHealthWindows.ts, sentinel assertions guard collector blindness at scripts/tests/test_service_registration_completeness.py:187-217 |
| `scripts/tests/test_skew.py` | GOOD | NONE | Fixture-derived exact-precision assertions plus injectable `now` for market-hours branches, e.g. scripts/tests/test_skew.py:249-251, sqlite migration coverage at 461-477 |
| `scripts/tests/test_spx01_grace_wait.py` | GOOD | NONE | Order-placement grace-wait for async 201 errors covered end to end with injectable clock, only wall-clock use is a generous <5s bound on a mocked-sleep path, scripts/tests/test_spx01_grace_wait.py:182-187 |
| `scripts/tests/test_spx02_orders_place_logging.py` | GOOD | NONE | Route-level logging + structured 502 detail preservation verified via TestClient and caplog, scripts/tests/test_spx02_orders_place_logging.py:114-123, 148-153 |
| `scripts/tests/test_spx03_short_availability.py` | REFACTOR | P1 | Docstring admits the helpers under test are copy-pasted duplicates of server.py, not imports (scripts/tests/test_spx03_short_availability.py:36-124 vs scripts/api/server.py:4133-4284); a real bug fix to server.py's shortability logic would not be caught. Replace with imports from server.py (extract to a shared pure module if the 3.10 syntax import chain is the real blocker). |
| `scripts/tests/test_straddle.py` | GOOD | NONE | Real Cboe CSV fixtures drive exact ratio/stats assertions and idempotent-upsert sqlite migration check, scripts/tests/test_straddle.py:97-123, 223-232 |
| `scripts/tests/test_strength_confirmation_scanner.py` | GOOD | NONE | Full multi-factor scan_ticker verdict logic covered with injected `now` and a realistic boundary client, scripts/tests/test_strength_confirmation_scanner.py:165-218 |
| `scripts/tests/test_theta_harvester_scanner.py` | GOOD | NONE | Strangle selection, DTE window, min-credit filter, and earnings annotation all covered with concrete numeric assertions and injected `now`, scripts/tests/test_theta_harvester_scanner.py:82-90, 350-353 |
| `scripts/tests/test_timezone_aware_writers.py` | GOOD | NONE | entry_date/last_sync tz-correctness pinned with a controlled fake clock crossing the ET midnight boundary, scripts/tests/test_timezone_aware_writers.py:115-121 |
| `scripts/tests/test_trade_blotter_formatting.py` | IMPROVE | P2 | format_pnl assertions only check substring presence, not sign/formatting, scripts/tests/test_trade_blotter_formatting.py:23-24,28-29 |
| `scripts/tests/test_utils.py` | GOOD | NONE | Market-hours boundary conditions, holiday calendar, and IB client-id registry all covered with concrete fixed datetimes and exact-value assertions, scripts/tests/test_utils.py:160-168 |
| `scripts/tests/test_uw_client.py` | GOOD | NONE | Exhaustive endpoint/retry/auth coverage on a real error-hierarchy with mocked requests.Session (scripts/tests/test_uw_client.py:48-59); asserts URL/params/status mapping not just no-throw. |
| `scripts/tests/test_vectorized_greeks.py` | GOOD | NONE | Pinned exact-value tests derived from an independent stdlib reference (scripts/tests/test_vectorized_greeks.py:26-45), catches sign/multiplier/constant mutations; cross-validated to 1e-12. |
| `scripts/tests/test_vol_surface.py` | GOOD | NONE | Fits synthetic arbitrage-free SVI smiles and checks closed-form values, residuals, butterfly/calendar no-arb constraints, and graceful degradation (scripts/tests/test_vol_surface.py:103-190). |
| `scripts/tests/test_vol_surface_integration.py` | GOOD | NONE | Verifies risk_reversal/leap_iv_scanner consume smile-residual not raw IV diff, with a rich-leg perturbation and explicit fallback path (scripts/tests/test_vol_surface_integration.py:79-104). |
| `scripts/tests/test_watchdog/test_ack.py` | GOOD | NONE | Deterministic injected `now`, verifies ack insert/replace/expiry/clear/status against real db_conn rows (scripts/tests/test_watchdog/test_ack.py:11-20). |
| `scripts/tests/test_watchdog/test_check.py` | GOOD | NONE | Covers healthy/stale/error/dormant/acked paths, severity mapping, replica-disabled guard, and a real embargo-suppression incident regression (scripts/tests/test_watchdog/test_check.py:277-351). |
| `scripts/tests/test_watchdog/test_check_bucket_daily.py` | GOOD | NONE | Daily/error bucket firing logic including a real weekend-false-positive regression at line 65-87. |
| `scripts/tests/test_watchdog/test_check_bucket_market_hours.py` | GOOD | NONE | Intraday bucket market-open gating, hysteresis, and open-bell grace/weekend edge cases with fixed calendar dates (scripts/tests/test_watchdog/test_check_bucket_market_hours.py:91-121). |
| `scripts/tests/test_watchdog/test_cli.py` | GOOD | NONE | Drives main() directly, asserts DB rows and stdout content, plus off-hours no-op gate (scripts/tests/test_watchdog/test_cli.py:63-74). |
| `scripts/tests/test_watchdog/test_cooldown.py` | GOOD | NONE | Hysteresis + cooldown state machine fully covered incl. flap-floor/24h ceiling/per-service isolation, injected now throughout (scripts/tests/test_watchdog/test_cooldown.py:97-124). |
| `scripts/tests/test_watchdog/test_dispatch_suppression_heartbeat.py` | GOOD | NONE | Small focused test asserting heartbeat_ok fires even when all outcomes are cooldown-suppressed (scripts/tests/test_watchdog/test_dispatch_suppression_heartbeat.py:34-36). |
| `scripts/tests/test_watchdog/test_dispatcher_writer_semantics.py` | GOOD | NONE | Verifies dispatcher-health-only row semantics incl. no downstream leak into last_error and DB-write-failure swallow (scripts/tests/test_watchdog/test_dispatcher_writer_semantics.py:140-144,154-174). |
| `scripts/tests/test_watchdog/test_emergency_cancel.py` | GOOD | NONE | P1 emergency cancel-by-tag lifecycle incl. transport-failure retry (scripts/tests/test_watchdog/test_emergency_cancel.py:115-137). |
| `scripts/tests/test_watchdog/test_external_probe_deadman.py` | GOOD | NONE | Off-box deadman probe covering healthy/stale/red/recovered-local-aggregate paths and P1 vs P2 severity split (scripts/tests/test_watchdog/test_external_probe_deadman.py:64-83,110-126). |
| `scripts/tests/test_watchdog/test_grouping.py` | GOOD | NONE | Thorough IB-grouping cohort logic: threshold, cooldown, auth-state branches, health-fetch failure fallback, meta-row non-leak (scripts/tests/test_watchdog/test_grouping.py:379-417,463-505). |
| `scripts/tests/test_watchdog/test_market_state.py` | GOOD | NONE | Small, precise: verifies holiday weekday classified closed vs ordinary weekday/weekend, fixed calendar-anchored dates (scripts/tests/test_watchdog/test_market_state.py:15-31). |
| `scripts/tests/test_watchdog/test_notify.py` | GOOD | NONE | Channel gating, P1/P3 pushover routing, dispatcher-health row semantics, heartbeat overwrite regression (scripts/tests/test_watchdog/test_notify.py:176-210). |
| `scripts/tests/test_watchdog/test_notify_escalation.py` | GOOD | NONE | P1 emergency priority/retry/expire contract, P2/P3 digest batching+cap+once-per-day flush, dead Resend channel deletion regression (scripts/tests/test_watchdog/test_notify_escalation.py:53-83,185-212). |
| `scripts/tests/test_watchdog/test_services.py` | GOOD | NONE | Cross-references TS SOT via regex parse to catch Python/TS drift on scheduled services and requires_ib flags (scripts/tests/test_watchdog/test_services.py:19-44,145-165). |
| `scripts/tests/test_watchdog/test_units.py` | GOOD | NONE | Pure function tests on units.evaluate/parse with deterministic fixtures and a subprocess-count assertion (scripts/tests/test_watchdog/test_units.py:388-396) pinning the alert-only contract. |
| `scripts/tests/test_watchdog/test_watchdog_end_to_end.py` | GOOD | NONE | Deterministic end-to-end with fixed NOW, mocked http_post (scripts/tests/test_watchdog/test_watchdog_end_to_end.py:56), asserts exact Pushover call count and DB rows. |
| `scripts/tests/test_workflow_emit_order.py` | GOOD | NONE | Confirms the order-emit confirmation gate blocks placement (scripts/tests/test_workflow_emit_order.py:65-85) and validates payload mapping with ValueError on malformed rows. |
| `scripts/tests/test_workflow_executor.py` | GOOD | NONE | Covers expression sandbox refusal (scripts/tests/test_workflow_executor.py:42-52), gate blocking, cycle rejection, and order confirmation gating end to end. |
| `scripts/tests/test_workflow_graphs.py` | GOOD | NONE | Real sqlite migrations applied in-memory (scripts/tests/test_workflow_graphs.py:34-57), disk mirror redirected to tmp_path, roundtrip/update/list-scoping all asserted against actual rows. |
| `scripts/tests/test_workflow_scanner_source.py` | GOOD | NONE | Small focused test verifying open positions are excluded from scanner rows (scripts/tests/test_workflow_scanner_source.py:27-30) via a SimpleNamespace boundary fake. |
| `scripts/tests/test_yield_curve.py` | GOOD | NONE | Real fixture CSV parsed and pinned to exact endpoint values (scripts/tests/test_yield_curve.py:43-50); persistence tests assert exact write ordering and idempotent upsert against a real migration-created sqlite table. |

#### unit-pyapi (41 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `scripts/api/tests/test_auth.py` | GOOD | NONE | Real RSA sign/verify (scripts/api/tests/test_auth.py:91-104), pins bypass boundary edges (scripts/api/tests/test_auth.py:149-159), rejects HS256/alg=none (scripts/api/tests/test_auth.py:409-425), forwarding-header defeat (scripts/api/tests/test_auth.py:190-201). |
| `scripts/api/tests/test_auth_fail_closed.py` | GOOD | NONE | Pins fail-closed regression via real TestClient status codes at scripts/api/tests/test_auth_fail_closed.py:48 and scripts/api/tests/test_auth_fail_closed.py:73, plus exempt-path and explicit-optin coverage. |
| `scripts/api/tests/test_bpi_routes.py` | GOOD | NONE | Covers cooldown per-index isolation, failure-does-not-advance-cooldown, invalid index 400s, and real asyncio.gather concurrency proving the shared lock serializes scans (scripts/api/tests/test_bpi_routes.py:143-172). |
| `scripts/api/tests/test_cors_allowlist.py` | GOOD | NONE | Regression-pins the 2026-06-29 CORS subdomain-takeover fix by asserting the actual CORSMiddleware config has no allow_origin_regex and rejects an arbitrary *.radon.run origin (scripts/api/tests/test_cors_allowlist.py:36-53). |
| `scripts/api/tests/test_cri_startup.py` | IMPROVE | P1 | Deterministic, all inputs injected (mtime_ms/now_ts/today_et), no wall-clock; but never tests a stale-cache-from-a-different-day branch, e.g. scripts/api/tests/test_cri_startup.py:34 only covers same-day close, not date mismatch. |
| `scripts/api/tests/test_db_source_truth_routes.py` | GOOD | NONE | Boundary-mocks subprocess+hrana_execute and asserts fail_disk_read/fake_hrana_execute raise if the stale JSON/legacy path is hit (scripts/api/tests/test_db_source_truth_routes.py:45-50,115-121,148-154), asserting real body fields e.g. line 127-131. |
| `scripts/api/tests/test_demo_scan_guards.py` | GOOD | NONE | Real endpoint calls via TestClient with a subprocess tripwire (server.py:56) that fails the test if any scan route reaches run_script, plus explicit UW_TOKEN leak assertions at scripts/api/tests/test_demo_scan_guards.py:115,138,150. |
| `scripts/api/tests/test_demo_trial_expiry_route.py` | GOOD | NONE | Pins concrete HTTP contract (200/400) and auth-exempt reachability with real date math assertions, e.g. scripts/api/tests/test_demo_trial_expiry_route.py:41-42 and :61-68. |
| `scripts/api/tests/test_earnings_route.py` | GOOD | NONE | Covers happy path, missing-is-200 (not 4xx per repo convention), invalid ticker 400s pre-subprocess, cache fallback on script failure, 502 with no cache, batch dedupe/cap (scripts/api/tests/test_earnings_route.py:103-127, 165-179, 237-257). |
| `scripts/api/tests/test_equity_options_chain.py` | GOOD | NONE | Verifies the equity-chain-specific timeout constant is actually passed through and that a subprocess timeout error maps to 504 (scripts/api/tests/test_equity_options_chain.py:39-59, 79-88). |
| `scripts/api/tests/test_futures_chain.py` | GOOD | NONE | Mocks only run_script boundary (scripts/api/tests/test_futures_chain.py:83), asserts status+body+call args, and stale-cache-not-502 test (scripts/api/tests/test_futures_chain.py:161) verifies a real safety contract. |
| `scripts/api/tests/test_garch_convergence_route.py` | GOOD | NONE | Boundary-mocks run_script/_read_cache (documented rationale scripts/api/tests/test_garch_convergence_route.py:3-7), asserts status codes, body content and exact subprocess args (scripts/api/tests/test_garch_convergence_route.py:104-109), covers cooldown gating (scripts/api/tests/test_garch_convergence_route.py:171-196) and universe-mismatch cache bypass (scripts/api/tests/test_garch_convergence_route.py:235-276). |
| `scripts/api/tests/test_gex_startup.py` | GOOD | NONE | Pure function tests of _is_gex_cache_stale across missing/stale/fresh/after-close cases with explicit injected timestamps (scripts/api/tests/test_gex_startup.py:6-25). |
| `scripts/api/tests/test_health_payload.py` | GOOD | NONE | Trust-scoping, probe-timeout, /health/lite account-free contract, and AUTH_EXEMPT_PATHS pin all verified with real assertions e.g. scripts/api/tests/test_health_payload.py:55,298-303 |
| `scripts/api/tests/test_historical_auth.py` | GOOD | NONE | Exercises verify_api_key against real allowed/disallowed paths including an explicit rejection list of trading routes (scripts/api/tests/test_historical_auth.py:56-62). |
| `scripts/api/tests/test_historical_pool.py` | GOOD | NONE | Small focused unit covering _get_pool's three real branches (present, None, unset) with a real HTTPException 503 assertion (scripts/api/tests/test_historical_pool.py:27-43). |
| `scripts/api/tests/test_ib_gateway_auth_recovery_heal.py` | GOOD | NONE | sqlite-behind-hrana boundary fake + real 0001 DDL; pins transition-edge heal incl. non-IB error stays (test_ib_gateway_auth_recovery_heal.py:210), cold-start no-op (:359), bounded hang (:300), canonical upsert spy (:390). |
| `scripts/api/tests/test_ib_gateway_auth_transition.py` | GOOD | NONE | Tests the real public API (handle_auth_state_transition/check_ib_gateway/restart_ib_gateway) via boundary MagicMock pool; deterministic, covers transition/steady-state/timeout/cold-start/idempotency, scripts/api/tests/test_ib_gateway_auth_transition.py:100-247 |
| `scripts/api/tests/test_ib_gateway_pool_recovery.py` | GOOD | NONE | Covers healthy no-op (scripts/api/tests/test_ib_gateway_pool_recovery.py:139), genuine-2FA-wait no-op (:118), stuck+authenticated reconnect (:85), deadlock regression pinning _derive_auth_state (:177,184), verified-fail (:214), and bounded timeout (:236); fail-loud sentinels assert zero push/restart side effects (:64-77). |
| `scripts/api/tests/test_ib_gateway_subprocess_cleanup.py` | GOOD | NONE | Tests real subprocess timeout/cancellation kill+reap paths, including a genuine child shell script that traps SIGTERM to prove process-group killing (scripts/api/tests/test_ib_gateway_subprocess_cleanup.py:92-108). |
| `scripts/api/tests/test_ib_pool_lifecycle.py` | GOOD | NONE | Uses real asyncio.gather/Event coordination to prove connect_all dedupes concurrent calls, disconnect_all waits for an active lease, and cancellation disposes a late-arriving client (scripts/api/tests/test_ib_pool_lifecycle.py:35-135). |
| `scripts/api/tests/test_ib_restart_2fa_lock.py` | GOOD | NONE | Regression-pins the stacked-2FA-push incident with real ib_2fa_lock file-backed lock acquisition/release across success, failure, TTL-expiry, and cloud-mode-skip paths (scripts/api/tests/test_ib_restart_2fa_lock.py:89-231, 309-322). |
| `scripts/api/tests/test_ib_restart_backoff.py` | GOOD | NONE | Real behavioral contract test: mocks only the IB-boundary calls (_restart_docker, _probe_authenticated) at scripts/api/tests/test_ib_restart_backoff.py:126-132, asserts documented backoff ladder scripts/api/tests/test_ib_restart_backoff.py:44-52 and state transitions. |
| `scripts/api/tests/test_ib_restart_cloud_delegate.py` | GOOD | NONE | Boundary-mocks admin_services.control_unit (collaborator, not module under test) and asserts real behavior: status codes, delegation call args, 503/409/502 mappings (scripts/api/tests/test_ib_restart_cloud_delegate.py:74-124) |
| `scripts/api/tests/test_ib_runtime_restart_policy.py` | GOOD | NONE | Deterministic monkeypatched boundary fakes on server dependencies; asserts specific error text confirming no-auto-restart policy in local-launchd (line 17-22) and cloud (line 43-46) modes, both scripts/api/tests/test_ib_runtime_restart_policy.py:26 and :48. |
| `scripts/api/tests/test_ib_sync_coordinator.py` | GOOD | NONE | Real asyncio.Event-coordinated concurrency test proves a background sync and an explicit route call share one in-flight subprocess run and result (scripts/api/tests/test_ib_sync_coordinator.py:24-48, 85-125). |
| `scripts/api/tests/test_index_options_chain.py` | GOOD | NONE | Covers unsupported-symbol 400, expiry passthrough, subprocess-failure 502, and case-insensitivity for the VIX/SPX index-options route (scripts/api/tests/test_index_options_chain.py:68-96). |
| `scripts/api/tests/test_knowledge_routes.py` | GOOD | NONE | Boundary-mocks only hybrid_search/get_embedder (scripts/api/tests/test_knowledge_routes.py:98-99); asserts exact call args, retry budget, log content, and rerank ordering behaviorally (line 511, 362). |
| `scripts/api/tests/test_leap_route.py` | GOOD | NONE | Mocks only the subprocess/cache boundary (server.py:62-63,92-93) and asserts exact subprocess args + cooldown state (test_leap_route.py:70-74,100-102), catching real regressions. |
| `scripts/api/tests/test_llm_token_index_route.py` | GOOD | NONE | Pins empty-not-404, DESC-to-ASC sort, days param flow-through, range validation 422, DB-error graceful degrade, and TTL cache keyed by days (scripts/api/tests/test_llm_token_index_route.py:62-147). |
| `scripts/api/tests/test_no_secret_leakage.py` | GOOD | NONE | Drives real upstream failure branches via TestClient + monkeypatch injection points (server.py str(exc)/result.error) and asserts secret needles absent AND resp.status_code>=400 so a no-op mock can't pass silently; see test_no_secret_leakage.py:90-110,192-198. |
| `scripts/api/tests/test_options_exposure.py` | GOOD | NONE | Validates symbol/frequency rejection and that provider errors map to sanitized statuses with secret/private text scrubbed from the response body (scripts/api/tests/test_options_exposure.py:92-131). |
| `scripts/api/tests/test_orders_place_safety_contract.py` | GOOD | NONE | Boundary-mocks only the IB subprocess call (scripts/api/tests/test_orders_place_safety_contract.py:74) and asserts db.writer never invoked (line 79); AST-walks orders_place source (line 84-116) to forbid persistence deps; cross-checks Next.js timeout budget via regex (line 133-142). Deterministic, no sleeps/network, tests real contract. |
| `scripts/api/tests/test_orders_whatif_route.py` | GOOD | NONE | Three deterministic cases (test-mode synthetic margin, mocked live success, IB-error 502) with real value assertions, e.g. scripts/api/tests/test_orders_whatif_route.py:71 checks initMargin==4250.0 and :88 checks 502 on IB error. |
| `scripts/api/tests/test_pool_recovery_escalation.py` | GOOD | NONE | Escalation ladder, cooldown, single-flight, genuine-2FA-wait all pinned with injected recover_stuck_pool/probe fakes; scripts/api/tests/test_pool_recovery_escalation.py:90-137 verify exact restart count. |
| `scripts/api/tests/test_route_authz_matrix.py` | GOOD | NONE | Enumerates live app.routes (not hardcoded) and asserts every non-exempt route denies anonymous callers; scripts/api/tests/test_route_authz_matrix.py:117-153 with a floor guard against an empty matrix. |
| `scripts/api/tests/test_rv_ratio_routes.py` | GOOD | NONE | Covers cooldown, per-symbol isolation, single-flight concurrency via asyncio.gather, and cross-symbol serialization; scripts/api/tests/test_rv_ratio_routes.py:186-237. |
| `scripts/api/tests/test_services.py` | GOOD | NONE | Deep coverage of unit allowlist, gateway push-lock lease, timeout/cancellation process-group kill via real shell scripts; scripts/api/tests/test_services.py:435-483 verifies orphan does not survive. |
| `scripts/api/tests/test_strength_confirmation_route.py` | GOOD | NONE | Ticker-vs-preset cooldown bypass and invalid-ticker rejection asserted on exact subprocess args; scripts/api/tests/test_strength_confirmation_route.py:47-51. |
| `scripts/api/tests/test_theta_harvester_route.py` | GOOD | NONE | Covers cooldown-vs-differing-params re-scan and pre-feature cache staleness; scripts/api/tests/test_theta_harvester_route.py:138-171 asserts re-scan happens despite cooldown. |
| `scripts/api/tests/test_ticker_ratings_and_pi.py` | GOOD | NONE | Covers array-unwrap, 502 propagation, and READ/MUTATE tier gating for /pi/exec with explicit-flag requirement; scripts/api/tests/test_ticker_ratings_and_pi.py:263-291. |

#### unit-pyroot (2 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `tests/test_portfolio_performance.py` | GOOD | NONE | 20 hand-derived TWR cases with exact expected math in comments (tests/test_portfolio_performance.py:79-96,142-155), covering N-gates, ACATS, splits, dividends, and a golden fixture pin. |
| `tests/test_position_return_capital.py` | GOOD | NONE | Real sqlite migration-backed DB tests for episode replay, margin-window isolation with fail-closed reasons, and immutability conflict raising (tests/test_position_return_capital.py:130-160,262-271). |

#### unit-site (9 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `site/lib/cluster-pages.test.ts` | GOOD | NONE | Pins slug registry, uniqueness, SEO length limits and lastModified never-future invariant against real exported data (site/lib/cluster-pages.test.ts:39-46). |
| `site/lib/faq-content.test.ts` | GOOD | NONE | Verifies FAQ schema mirrors visible content exactly, entry-by-entry, and bans AI cliches (site/lib/faq-content.test.ts:35-45). |
| `site/lib/pages/crash-risk-index.test.ts` | GOOD | NONE | Locks the real 4-component composite and bands, and forbids reintroducing debunked skew/term-structure copy that previously shipped in error (site/lib/pages/crash-risk-index.test.ts:23-24,52-65). |
| `site/lib/pages/fractional-kelly-position-sizing.test.ts` | GOOD | NONE | Independently re-derives the Kelly worked example arithmetic and asserts against the published copy (site/lib/pages/fractional-kelly-position-sizing.test.ts:62-73), catching a real math drift. |
| `site/lib/pages/interactive-brokers-dark-pool-terminal.test.ts` | GOOD | NONE | Content-contract tests mirror structured data to visible copy exactly and assert factual honesty claims (site/lib/pages/interactive-brokers-dark-pool-terminal.test.ts:45-52). |
| `site/lib/privacy-claims.test.ts` | GOOD | NONE | Scans the real shipped source tree for document.cookie/sessionStorage/localStorage usage and asserts an exact approved-file allowlist (site/lib/privacy-claims.test.ts:61-78), a genuine regression trap for privacy-copy honesty. |
| `site/lib/seo.test.ts` | GOOD | NONE | Asserts real sitemap route count/order/lastModified against actual clusterPages/legalPages exports and forbids request-time lastmod (site/lib/seo.test.ts:140-185). |
| `site/lib/static-asset-headers.test.ts` | GOOD | NONE | Small direct assertion that the real next.config headers() function emits noindex for _next/static (site/lib/static-asset-headers.test.ts:5-14). |
| `site/lib/theme.test.ts` | GOOD | NONE | Exhaustive pure-function coverage of theme resolution branches including explicit vs system fallback (site/lib/theme.test.ts:20-39). |

#### unit-tools (8 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `lib/tools/__tests__/daily-chg.test.ts` | REFACTOR | P0 | Never imports getOptionDailyChg from web/components/WorkspaceSections.tsx (lib/tools/__tests__/daily-chg.test.ts:1-2); defines and tests local mirror fns (:26,:43) instead, so a regression in the real production function (also duplicated in positionUtils.ts, PositionTable.tsx, MobilePositionList.tsx) is invisible to CI. |
| `lib/tools/__tests__/data-reader.test.ts` | GOOD | NONE | Writes real JSON fixtures to disk and reads them via readDataFile, covering valid, missing, schema-valid, schema-invalid, and malformed-JSON paths (lib/tools/__tests__/data-reader.test.ts:25-87). |
| `lib/tools/__tests__/exposure-breakdown.test.ts` | GOOD | NONE | Computes real delta/dollar-delta math for stock, IB-delta options, approx-delta fallback, and multi-leg spreads with hand-verified arithmetic in comments (lib/tools/__tests__/exposure-breakdown.test.ts:107-174). |
| `lib/tools/__tests__/ib-wrappers-db-source.test.ts` | GOOD | NONE | Stubs global fetch as the network boundary and asserts wrapper URL routing, success/error mapping and schema-rejection, plus a source-file regression guard (lib/tools/__tests__/ib-wrappers-db-source.test.ts:45-105). |
| `lib/tools/__tests__/kelly.test.ts` | IMPROVE | P0 | use_size (2.5% bankroll hard cap, Gate 3) exercised with dollar_size=10000>cap=2500 but only toBeDefined() asserted, lib/tools/__tests__/kelly.test.ts:33; a broken min()/cap would still pass. |
| `lib/tools/__tests__/runner.test.ts` | GOOD | NONE | Actually spawns real python scripts (kelly.py) via runScript and checks JSON output, plus a targeted regression test for the 2026-05-22 bare-python3.13 outage (lib/tools/__tests__/runner.test.ts:92-133). |
| `lib/tools/__tests__/schemas.test.ts` | IMPROVE | P2 | Almost every schema only gets an 'accepts valid shape' test; only KellyOutput has a rejection case (lib/tools/__tests__/schemas.test.ts:36-39), leaving OrdersData/PortfolioData/ScannerOutput/FetchTickerOutput/IBOrderManageOutput unchecked against malformed data. |
| `lib/tools/__tests__/ticker-detail-orders.test.ts` | REFACTOR | P0 | Combo-order sections re-implement legPriceKey/net-price/leg-action logic locally (lib/tools/__tests__/ticker-detail-orders.test.ts:105,146,125) instead of importing the real exported legPriceKey (web/lib/positionUtils.ts:332) or the inline combo logic embedded in ModifyOrderModal.tsx:161-243; the test verifies its own copy, not production code, so real drift/regressions in order-leg action or net-price computation on a live order form won't be caught. |

#### unit-web (494 files)

| Test file | Bucket | Sev | Reason |
|---|---|---|---|
| `web/tests/account-balances-complete.test.ts` | REFACTOR | P1 | Most describe blocks build a local literal array/object and assert it contains itself, e.g. web/tests/account-balances-complete.test.ts:225 checks a hardcoded array for a string it was defined with; no production build_account_summary or MetricCards code is ever imported/invoked. |
| `web/tests/account-metric-modal.test.ts` | REFACTOR | P2 | Re-implements MetricCards.tsx modal configs as local literals (web/tests/account-metric-modal.test.ts:37-101) and tests those copies, not the component; onClick tests simulate a closure pattern (web/tests/account-metric-modal.test.ts:246) rather than rendering MetricCard. |
| `web/tests/admin-components.test.tsx` | GOOD | NONE | Renders real IbGatewayCard/Ib2faControls/ServiceControlPanel via RTL, asserts confirm-gating before firing handlers (web/tests/admin-components.test.tsx:207-228) and optimistic-vs-poll reconciliation (web/tests/admin-components.test.tsx:461-495). |
| `web/tests/admin-format.test.ts` | GOOD | NONE | Pure-function unit tests over adminFormat.ts covering tone/label/backoff/relative-time edge cases including clock-skew clamp (web/tests/admin-format.test.ts:260-262). |
| `web/tests/admin-host-metrics.test.ts` | GOOD | NONE | Pure summarizeHostMetrics tests cover unordered rows, null-sample trend dropping, staleness window, and malformed JSON degrade-safe (web/tests/admin-host-metrics.test.ts:73-80). |
| `web/tests/admin-polling.test.tsx` | GOOD | NONE | Uses vi.useFakeTimers + vi.advanceTimersByTime for deterministic 5s poll/flash-timer assertions (web/tests/admin-polling.test.tsx:116-155, 277-281), no real sleeps. |
| `web/tests/admin-redesign-components.test.tsx` | GOOD | NONE | RTL renders of ReliabilityStrip/SystemStatusBar/ConfirmDialog with real type-to-confirm gating and clipboard-copy assertions (web/tests/admin-redesign-components.test.tsx:194-231). |
| `web/tests/admin-reliability-history.test.ts` | GOOD | NONE | Pure-function tests for serviceHistorySummaries/deployMarkers/reliabilityRollup: MTTR, uptime%, incident clamping at window edges (web/tests/admin-reliability-history.test.ts:59-67, 128-153). |
| `web/tests/admin-reliability-route.test.ts` | GOOD | NONE | Fake-timer bound on a hung DB read via vi.advanceTimersByTimeAsync, verifies 200+missing:true fallback and resetDb call (web/tests/admin-reliability-route.test.ts:32-48). |
| `web/tests/admin-reliability.test.ts` | GOOD | NONE | Pure-function coverage of unitVerdict/unitDependents/serviceControlDisabledReason/humanizeDetail including no-flap-on-scheduler-delay case (web/tests/admin-reliability.test.ts:156-160). |
| `web/tests/admin-slo-strip.test.tsx` | GOOD | NONE | RTL renders honest '--' rendering for null/missing SLO payloads and per-column null exclusion (web/tests/admin-slo-strip.test.tsx:57-74). |
| `web/tests/admin-slo.test.ts` | GOOD | NONE | Covers SLO attainment math incl. NULL-exclusion from denominator and exact-boundary-met case (web/tests/admin-slo.test.ts:90-96), plus route pre-migration 200+missing:true fallback (web/tests/admin-slo.test.ts:165-179). |
| `web/tests/alerts-api.test.ts` | GOOD | NONE | Real in-memory libsql client seeded with schema (web/tests/alerts-api.test.ts:24-29,43), exercises auth gate, CRUD, validation, and user-scoping (web/tests/alerts-api.test.ts:143-149) against real SQL. |
| `web/tests/alerts-panel.test.tsx` | GOOD | NONE | RTL + fetch-sequence mocking drives create/delete/retry/relative-time flows with role-based selectors (web/tests/alerts-panel.test.tsx:118,129); also checks no raw hex (web/tests/alerts-panel.test.tsx:184-189). |
| `web/tests/api-error-no-leak.test.ts` | GOOD | P0 | Deliberate strict-xfail (it.fails) pins that /api/profile,/watchlist,/bookmarks,/ticker/ratings currently LEAK secrets in error bodies (web/tests/api-error-no-leak.test.ts:94-107); test itself is well-designed to force removal of .fails once fixed. |
| `web/tests/api-routes-extended.test.ts` | GOOD | NONE | Extensive route coverage for P0 order place/cancel/modify incl. silent-IB-rejection states (Cancelled/ApiCancelled/Unknown/Inactive -> 502, web/tests/api-routes-extended.test.ts:1165-1248) and combo replace-order flow assertions on exact payload (web/tests/api-routes-extended.test.ts:919-941). |
| `web/tests/api-routes-no-cache-contract.test.ts` | GOOD | NONE | Static-source contract scan enforces force-dynamic + no-store + DB-first across every disk-backed route; catches real regressions, e.g. web/tests/api-routes-no-cache-contract.test.ts:335 real assertion on wrapper coverage. |
| `web/tests/api-routes-smoke-admin.test.ts` | GOOD | NONE | Boundary-mocked radonFetch + demo admin gate; asserts fail-closed 403 before proxy call at web/tests/api-routes-smoke-admin.test.ts:230-235, exercises real 409/502 propagation. |
| `web/tests/api-routes-smoke-flow-share.test.ts` | GOOD | NONE | Covers happy/sad path plus path-traversal 403 defense for share/content routes; web/tests/api-routes-smoke-flow-share.test.ts:366-374 asserts 403 on /etc/passwd path. |
| `web/tests/api-routes-smoke-misc.test.ts` | GOOD | NONE | Good same-ET-day comment explaining why timestamps are window-relative not hardcoded (web/tests/api-routes-smoke-misc.test.ts:187-189); covers DB/disk/FastAPI fallback chain thoroughly. |
| `web/tests/api-routes-smoke.test.ts` | GOOD | NONE | Broad route-family smoke coverage with real payload/error assertions, e.g. web/tests/api-routes-smoke.test.ts:522-542 verifies FRED CSV parse + stale fallback semantics. |
| `web/tests/api-routes.test.ts` | GOOD | NONE | Deep input-validation + DB-fallback coverage for money-relevant routes (orders/place, portfolio, journal); web/tests/api-routes.test.ts:421-437 confirms combo-legs validation before FastAPI call. |
| `web/tests/app-error-boundary.test.tsx` | GOOD | NONE | Renders real error boundary components, asserts reset callback fires and digest renders; web/tests/app-error-boundary.test.tsx:20-26 exercises retry behavior directly. |
| `web/tests/asset-cockpit-keyboard-guard.test.tsx` | GOOD | NONE | Renders real AssetCockpit, verifies keyboard-guard behavior against focused input vs body via fireEvent; web/tests/asset-cockpit-keyboard-guard.test.tsx:98-111 covers both suppressed and active paths. |
| `web/tests/asset-cockpit-render.test.tsx` | IMPROVE | P2 | Self-acknowledged CSS-class selector coupling (web/tests/asset-cockpit-render.test.tsx:18-23); real behavior asserted via click/text/callback but layout refactors will break selectors like .book-region/.act-ticket. |
| `web/tests/assistant-journal-tools.test.ts` | GOOD | NONE | Exercises real lot-matching P&L math with exact expected values, e.g. web/tests/assistant-journal-tools.test.ts:119-122 computes openNetCreditPerContract and asserts toBeCloseTo against tool output. |
| `web/tests/assistant-knowledge-tools.test.ts` | GOOD | NONE | Verifies compaction, truncation bounds, retry-once-not-twice semantics and exact degraded-message contract; web/tests/assistant-knowledge-tools.test.ts:250-264 confirms no retry on timeout abort. |
| `web/tests/assistant-loop-hardening.test.ts` | GOOD | NONE | Deterministic mocked chat sequence pins cap-hit forced-final and repeated-call short-circuit behavior with exact round counts; web/tests/assistant-loop-hardening.test.ts:84-98 asserts 7 calls and outcome. |
| `web/tests/assistant-system-prompt.test.ts` | GOOD | NONE | Asserts real SYSTEM_PROMPT content and per-request date injection format; web/tests/assistant-system-prompt.test.ts:74 checks ET-date regex on the actual request payload sent to chat(). |
| `web/tests/assistant-telemetry.test.ts` | GOOD | NONE | Verifies fire-and-forget telemetry never throws/leaks rejections and preserves route status on write failure; web/tests/assistant-telemetry.test.ts:94-103 flushes microtasks to catch unhandled rejections. |
| `web/tests/assistant-tool-loop.test.ts` | GOOD | NONE | Confirms destructive tool (place_order) is never auto-executed and returns a confirm proposal instead; web/tests/assistant-tool-loop.test.ts:148-157 is a real money-safety assertion. |
| `web/tests/attribution.test.ts` | REFACTOR | P2 | Every assertion checks a hand-built MOCK_ATTRIBUTION literal against itself (web/tests/attribution.test.ts:13-76, 79-124) — no import of any real attribution-computation function; tests nothing production. |
| `web/tests/auth-integration.test.ts` | GOOD | NONE | Behavioral radonFetch/wsTicket auth-header tests with a mocked fetch boundary; asserts real header values, e.g. web/tests/auth-integration.test.ts:35 |
| `web/tests/auth-pages.test.tsx` | GOOD | NONE | Renders real page modules with mocked Clerk/next-navigation, asserts redirect vs render branch; web/tests/auth-pages.test.tsx:37 |
| `web/tests/banner-stale-state.test.tsx` | GOOD | NONE | Verifies stale-vs-error severity precedence and copy via data-severity attribute; web/tests/banner-stale-state.test.tsx:45 |
| `web/tests/batched-prices.test.ts` | REFACTOR | P1 | handleWSMessage is a local reimplementation, not imported from lib/usePrices; real hook logic drift is invisible. web/tests/batched-prices.test.ts:45 |
| `web/tests/black-scholes.test.ts` | GOOD | NONE | Python-parity fixtures, put-call parity, T=0/sigma=0 edge cases, implied-vol round trip and arbitrage-bound nulls; web/tests/black-scholes.test.ts:19 |
| `web/tests/blotter-from-journal.test.ts` | GOOD | NONE | Extensive production-regression coverage (ISO/compact expiry lot-matching, multi-fill composite ids, legacy union); web/tests/blotter-from-journal.test.ts:265 |
| `web/tests/blotter-no-store-header.test.ts` | GOOD | NONE | Focused Cache-Control:no-store contract check across GET(db)/GET(empty)/POST; web/tests/blotter-no-store-header.test.ts:61 |
| `web/tests/book-microstructure.test.tsx` | GOOD | NONE | Pure math (imbalance/microprice) verified with exact values plus render + brand-token guard; web/tests/book-microstructure.test.tsx:43 |
| `web/tests/book-stock-close-out.test.tsx` | GOOD | NONE | Repro of a real P&L bug (short-stock close basis) pinned with exact dollar math; web/tests/book-stock-close-out.test.tsx:90 |
| `web/tests/bpi-hook.test.tsx` | GOOD | NONE | Verifies GET-only contract, cache:no-store, refresh() re-fetch, error surfacing; web/tests/bpi-hook.test.tsx:42 |
| `web/tests/bpi-panel.test.tsx` | GOOD | NONE | Deterministic fixture-driven cross-up counts, state-lozenge precedence, brush/preset re-slicing all pinned with real math; web/tests/bpi-panel.test.tsx:75 |
| `web/tests/bpi-route.test.ts` | GOOD | NONE | Real in-memory libsql client, Turso-vs-disk freshness ordering, missing:true 200 contract, force-dynamic declaration; web/tests/bpi-route.test.ts:149 |
| `web/tests/breadth-api.test.ts` | GOOD | NONE | Real in-memory libsql, Turso-first precedence, POST scan/fallback/502 paths all exercised with real assertions; web/tests/breadth-api.test.ts:106 |
| `web/tests/breadth-panel.test.tsx` | GOOD | NONE | Gating states, summary tiles, divergence chip tone mapping all asserted against real hook payloads; web/tests/breadth-panel.test.tsx:165 |
| `web/tests/cancel-order-dialog.test.tsx` | GOOD | NONE | Single/multi/partial-fill rendering, callback wiring, em-dash guard all via role/text queries; web/tests/cancel-order-dialog.test.tsx:116 |
| `web/tests/cash-flows-route-and-hook.test.ts` | GOOD | NONE | Regression-pinned no-store contract, query-param forwarding, and last_synced_at pass-through; web/tests/cash-flows-route-and-hook.test.ts:88 |
| `web/tests/cash-flows-sync-lozenge.test.tsx` | GOOD | NONE | Window-relative timestamps (Date.now()-offset, repo-standard, not flagged) drive relative-time, throttle/error tone, em-dash guard; web/tests/cash-flows-sync-lozenge.test.tsx:59 |
| `web/tests/catalyst-card.test.tsx` | GOOD | NONE | Frozen-clock (vi.useFakeTimers/setSystemTime) matrix pins recompute-at-render, weekend fossil handling, elapsed same-day filtering; web/tests/catalyst-card.test.tsx:91 |
| `web/tests/catalyst-upcoming.test.ts` | GOOD | NONE | Explicit UTC instants (no host wall-clock dependence) covering ET-boundary, holiday remap, west-of-UTC date-shift edge cases; web/tests/catalyst-upcoming.test.ts:21 |
| `web/tests/chain-atm-scroll-isolation.test.tsx` | GOOD | NONE | Real component render asserts scrollTo called on the chain wrapper element (not scrollIntoView/document); web/tests/chain-atm-scroll-isolation.test.tsx:176 |
| `web/tests/chain-combo-ratio.test.ts` | IMPROVE | P2 | First/third tests exercise real normalizeComboOrder/getComboEntryAction; second test regresses to source-string pinning. web/tests/chain-combo-ratio.test.ts:40 |
| `web/tests/chain-combo-sign-in-submission.test.ts` | REFACTOR | P0 | Entirely fs.readFileSync + string/regex matches on OptionsChainTab.tsx; a sign-flip regression that keeps the same substrings ships undetected. web/tests/chain-combo-sign-in-submission.test.ts:10 |
| `web/tests/chain-notional-calc.test.ts` | REFACTOR | P0 | fs.readFileSync + regex on source text for notional double-counting guard; a real double-count bug survives if the literal substring changes. web/tests/chain-notional-calc.test.ts:14 |
| `web/tests/chain-prefetch.test.ts` | REFACTOR | P2 | Entirely fs.readFileSync + regex/contains assertions on hook and CSS source, never exercises prefetch scheduling at runtime; web/tests/chain-prefetch.test.ts:48 |
| `web/tests/chain-side-filter.test.ts` | REFACTOR | P2 | Entirely fs.readFileSync + regex assertions against TSX/CSS source, no render or user interaction; web/tests/chain-side-filter.test.ts:36 |
| `web/tests/chain-sticky-header.test.ts` | REFACTOR | P2 | Pure CSS regex assertions against globals.css text, no layout/render verification; web/tests/chain-sticky-header.test.ts:17 |
| `web/tests/chain-url-deeplink.test.tsx` | GOOD | NONE | Real component render, URL hydration, invalid-param fallback and legs-prefill all asserted via visible DOM text/roles (web/tests/chain-url-deeplink.test.tsx:132-192). |
| `web/tests/chain-url-state.test.ts` | GOOD | NONE | Pure parser functions exercised with valid/invalid/edge cases incl. round-trip identity (web/tests/chain-url-state.test.ts:78-90). |
| `web/tests/chart-runtime-adoption.test.ts` | IMPROVE | P2 | Mostly source-text substring checks (e.g. web/tests/chart-runtime-adoption.test.ts:38-40) pin imports/JSX strings rather than rendered behavior; a harmless refactor (renamed prop, reformatted JSX) breaks it. |
| `web/tests/chart-system.test.ts` | GOOD | NONE | Exercises real exported chart-system helpers against the JSON spec with concrete expected values (web/tests/chart-system.test.ts:19-23). |
| `web/tests/chat-advanced.test.ts` | GOOD | NONE | requestAssistantReply/requestPiReply/streamMessage tested against mocked fetch with concrete success/error/edge bodies and payload assertions (web/tests/chat-advanced.test.ts:37-56, 235-263). |
| `web/tests/chat-conversational-surface.test.tsx` | IMPROVE | P2 | Reads globals.css text and regexes CSS rule blocks (web/tests/chat-conversational-surface.test.tsx:39-51) instead of asserting computed styles in rendered DOM; brittle to unrelated CSS restructuring though tied to a real UI contract. |
| `web/tests/chat-launcher-focus.test.tsx` | GOOD | NONE | Real keydown-driven open/close and focus-management assertions against the live DOM (web/tests/chat-launcher-focus.test.tsx:17-23). |
| `web/tests/chat-order-confirm.test.tsx` | GOOD | NONE | Pins the no-auto-execute / Confirm-POSTs / Cancel-dismisses order-placement contract via real ChatPanel render and mocked fetch call inspection (web/tests/chat-order-confirm.test.tsx:93-131). |
| `web/tests/chat-streaming-ux.test.tsx` | GOOD | NONE | Asserts typing indicator visible during in-flight fetch and absent after resolution, using a controlled pending Promise not a timer (web/tests/chat-streaming-ux.test.tsx:39-69). |
| `web/tests/chat.test.ts` | GOOD | NONE | Comprehensive pure-function table tests for command routing incl. real prior-bug regression case (web/tests/chat.test.ts:68-73). |
| `web/tests/click-to-fill-context.test.tsx` | GOOD | NONE | Nonce-based re-fill vs typed-value-not-clobbered behavior verified against real OrderTab + context providers (web/tests/click-to-fill-context.test.tsx:81-97). |
| `web/tests/click-to-fill.test.tsx` | GOOD | NONE | Real DepthMontage/TimeAndSales/LadderDOM components; tick-test direction and BUY/SELL emission checked with concrete price/qty assertions (web/tests/click-to-fill.test.tsx:64-87). |
| `web/tests/cockpit-header-mobile-switcher.test.tsx` | GOOD | NONE | Real CockpitHeader render with viewport/watchlist mocked at the edge; switcher presence and click behavior asserted on DOM (web/tests/cockpit-header-mobile-switcher.test.tsx:43-63). |
| `web/tests/combo-skew-panel.test.tsx` | GOOD | NONE | Renders real ComboSkewPanel with computed IV/delta values and asserts exact formatted output for strangle, RR, and non-pair cases (web/tests/combo-skew-panel.test.tsx:78-89, 140-150). |
| `web/tests/combo-skew.test.ts` | GOOD | NONE | computeComboSkew tested across strangle/RR/vertical/edge (zero delta, invalid IV backsolve) with sign and magnitude assertions (web/tests/combo-skew.test.ts:192-232, 234-254). |
| `web/tests/company-tab-offline-retry.test.tsx` | GOOD | NONE | Offline→online refetch transition asserted via fetch call counts across rerenders, no timers (web/tests/company-tab-offline-retry.test.tsx:44-50). |
| `web/tests/company-tab-short-stats.test.tsx` | GOOD | NONE | Real CompanyTab render; asserts formatted cell values, missing/ETF/INDEX gating and that the short-availability probe is never fired for indexes (web/tests/company-tab-short-stats.test.tsx:189-202). |
| `web/tests/complex-risk-profile.test.ts` | REFACTOR | P0 | Reimplements the WorkspaceSections.tsx filter inline (web/tests/complex-risk-profile.test.ts:16-24) instead of importing the real logic at web/components/WorkspaceSections.tsx:1253-1255; a regression there would not fail this test. Replace with a test that imports the real filter (export it) so the two can't drift. |
| `web/tests/connection-banner-state.test.ts` | GOOD | NONE | Pure function tested for MFA-required tone/message and null-cases with concrete assertions (web/tests/connection-banner-state.test.ts:15-19, 31, 43). |
| `web/tests/correlation-risk-banner-render.test.tsx` | GOOD | NONE | Real CorrelationRiskBanner render across null/none/critical states with concrete text and data-level assertions (web/tests/correlation-risk-banner-render.test.tsx:48-58). |
| `web/tests/correlation-risk-banner.test.ts` | GOOD | NONE | Pure derivation function tested across none/critical/info states plus percent-label formatting with concrete values (web/tests/correlation-risk-banner.test.ts:48-58, 77-83). |
| `web/tests/covered-call-order-summary.test.tsx` | GOOD | NONE | Exercises real useOrderRisk/OrderRiskGate for the EWY covered-call bug with derived (commented) expected dollar math and covers full/partial/no-coverage branches (web/tests/covered-call-order-summary.test.tsx:110-192). |
| `web/tests/cri-cache-selection.test.ts` | GOOD | NONE | selectPreferredCriCandidate tested for completeness-preference and a real prior bug (naive-timestamp UTC parsing tiebreak) with concrete winners (web/tests/cri-cache-selection.test.ts:81-108). |
| `web/tests/cri-history-chart-axis.test.ts` | GOOD | NONE | Pure tick-reduction and rotation helpers tested with concrete bounds and identity checks (web/tests/cri-history-chart-axis.test.ts:16-19, 31-33). |
| `web/tests/cri-latest-query-index.test.ts` | IMPROVE | P2 | Source-text grep for a SQL string literal across three files (web/tests/cri-latest-query-index.test.ts:16-20) proves nothing about query correctness/index usage at runtime, only that the literal wasn't edited. |
| `web/tests/cri-staleness-weekend.test.ts` | GOOD | NONE | Session-aware staleness checked against real market-session helper using window-relative next-Saturday computation, not a hardcoded date, with fresh/stale branches (web/tests/cri-staleness-weekend.test.ts:13-18, 21-61). |
| `web/tests/csp-nonce.test.ts` | GOOD | NONE | Direct unit tests of buildCspWithNonce/withNonceCsp + real next.config.mjs import; asserts on actual header values (web/tests/csp-nonce.test.ts:36). |
| `web/tests/cta-briefing-currency.test.ts` | GOOD | NONE | Renders real CtaBriefing component with realistic MenthorQ payload and asserts specific derived narrative text (web/tests/cta-briefing-currency.test.ts:53). |
| `web/tests/cta-output-tracing-config.test.ts` | GOOD | NONE | Checks the real exported next.config object (web/tests/cta-output-tracing-config.test.ts:9) plus verbatim turbopackIgnore pragma comments that ARE the bundler contract, not incidental text (web/tests/cta-output-tracing-config.test.ts:35). |
| `web/tests/cta-page-freshness.test.ts` | GOOD | NONE | Mocks data hooks, renders real CtaPage via renderToStaticMarkup, asserts staleness/fresh HTML output for both branches (web/tests/cta-page-freshness.test.ts:84). |
| `web/tests/cta-page.test.ts` | IMPROVE | P2 | Pure source-text regex assertions on component/hook names (web/tests/cta-page.test.ts:74-79, :86-99) instead of rendering and observing real DOM behavior; brittle to a behavior-preserving rename/refactor. |
| `web/tests/cta-route-freshness.test.ts` | GOOD | NONE | Mocks fs/child_process, uses fake timers pinned via vi.setSystemTime, exercises stale/fresh/missing-cache/stale-sync-override branches of the real GET handler (web/tests/cta-route-freshness.test.ts:148-187). |
| `web/tests/cta-route-no-cache.test.ts` | GOOD | NONE | Asserts the route exports force-dynamic and the hook calls fetch with cache:'no-store' (web/tests/cta-route-no-cache.test.ts:17, :41), pinning the exact cache-contract rule from web/CLAUDE.md. |
| `web/tests/cta-share-stale.test.tsx` | GOOD | NONE | Renders real ShareReportModal, fires click, asserts STALE warning text vs. fresh path (web/tests/cta-share-stale.test.tsx:81-84); trailing source-grep checks assert observability log strings actually present (web/tests/cta-share-stale.test.tsx:128-129). |
| `web/tests/cta-vol-target.test.ts` | GOOD | NONE | Pure-function tests cross-checked against the Python cri_scan formula with an exact numeric parity comment (web/tests/cta-vol-target.test.ts:21-30); covers null/zero/negative/NaN edge cases. |
| `web/tests/dashboard-mobile-newsfeed.test.tsx` | IMPROVE | P2 | Real RTL render + localStorage fail-open assertions are solid (web/tests/dashboard-mobile-newsfeed.test.tsx:112-124), but several tests regex-match raw CSS text for layout order (web/tests/dashboard-mobile-newsfeed.test.tsx:129-138) instead of measuring computed layout, so a harmless CSS refactor (shorthand, selector merge) can fail it without a real regression. |
| `web/tests/dashboard-newsfeed-pagination.test.tsx` | GOOD | NONE | Real RTL render of DashboardNewsFeed with fetch stub, covers pagination math, disabled-button states, and refresh-shrink clamp (web/tests/dashboard-newsfeed-pagination.test.tsx:147-183). |
| `web/tests/dashboard-newsfeed-tag-filter.test.tsx` | GOOD | NONE | Deterministic tag fixtures exercise AND-semantics, URL restore, clear-all, no-results, and pagination reset via real component interaction (web/tests/dashboard-newsfeed-tag-filter.test.tsx:97-112, :181-215). |
| `web/tests/data.test.ts` | GOOD | NONE | Exercises real exported nav/data constants, locks visible nav order and section coverage against drift (web/tests/data.test.ts:183-200). |
| `web/tests/day-move-ib-daily-pnl.test.ts` | GOOD | NONE | Realistic production-shaped portfolio fixture proves ib_daily_pnl wins over websocket close-based math when signs disagree (web/tests/day-move-ib-daily-pnl.test.ts:106-111). |
| `web/tests/day-move-ib-first.test.ts` | GOOD | NONE | Regression test built from a real 2026-07-28 production book; deterministic fake timers cleaned up in afterEach (web/tests/day-move-ib-first.test.ts:26-28); covers mixed-book aggregation, same-day entry-cost fallback, and sign-aware shorts. |
| `web/tests/day-move-mid-fallback.test.ts` | GOOD | NONE | Thorough pure-function coverage of resolveLastOrMid plus integration coverage of stale-last-vs-mid divergence for options (web/tests/day-move-mid-fallback.test.ts:325-366). |
| `web/tests/day-pnl-premarket-fallback.test.tsx` | GOOD | NONE | Renders real MetricCards with fake-timer session boundaries per market state (pre-market/RTH/after-hours/closed) and asserts the exact label text for each (web/tests/day-pnl-premarket-fallback.test.tsx:142-197). |
| `web/tests/db-cache.test.ts` | GOOD | NONE | Deterministic fake-timer TTL/single-flight/stale-while-error/maxStaleMs coverage on the real cachedRead implementation (web/tests/db-cache.test.ts:81-107). |
| `web/tests/db-destroy-observability.test.ts` | GOOD | P0 | Verifies dbExecute failures log attributable teardown-trigger detail (label, generation) via real dbExecute + __resetDbForTests (web/tests/db-destroy-observability.test.ts:59-84), guarding an incident-class DB-pool blind spot. |
| `web/tests/db-destroy-storm.test.ts` | GOOD | P0 | Exhaustive incident-driven coverage of the pool destroy-storm fix: cluster window, cooldown, collateral-abort generation isolation, and the request-body microtask race (web/tests/db-destroy-storm.test.ts:318-341). |
| `web/tests/db-direct-cloud.test.ts` | GOOD | P0 | Pins the direct-to-cloud default (no embedded replica) plus RADON_DB_NO_REPLICA override and URL-transport normalization against the real getDb() implementation (web/tests/db-direct-cloud.test.ts:36-41, :56-68). |
| `web/tests/db-execute-chokepoint-contract.test.ts` | GOOD | P0 | Legitimate architectural chokepoint enforcement: scans real app/api and lib source for forbidden bare getDb().execute() calls (web/tests/db-execute-chokepoint-contract.test.ts:43-53), directly matching the documented incident contract. |
| `web/tests/db-first-read.test.ts` | GOOD | P1 | Deterministic freshness-comparison coverage (DB vs disk, ties, missing timestamps, source-timeout deadlines) with injected clock (web/tests/db-first-read.test.ts:26, :208-235). |
| `web/tests/db-keepalive.test.ts` | GOOD | P1 | Fake-timer coverage of the 30s cadence, self-heal on failure, and the 3s-bounded hung-ping recovery path against real startDbKeepAlive (web/tests/db-keepalive.test.ts:52-68). |
| `web/tests/db-pool-bounded.test.ts` | GOOD | P0 | Pins the bounded undici Agent (connection cap, keepAliveTimeout, transport-deadline abort) with a fake-timer-driven AbortSignal assertion (web/tests/db-pool-bounded.test.ts:139-167). |
| `web/tests/db-read-cache-contract.test.ts` | GOOD | P1 | Static contract enforcement that hot-polled routes use cachedRead with a pinned cache key and invalidateCache on POST (web/tests/db-read-cache-contract.test.ts:41-50, :64-73); legitimate architectural pin, not incidental string matching. |
| `web/tests/db-stall-diagnostics.test.ts` | GOOD | NONE | Boundary-mocked FakeAgent/undici; getPoolStats, dbExecute warn payload and loopLagMonitor tested with injected clock (web/tests/db-stall-diagnostics.test.ts:115) |
| `web/tests/db-sync-http-mode.test.ts` | GOOD | NONE | Mocks @libsql/client at the http.js boundary to reproduce the SYNC_NOT_SUPPORTED throw and asserts no pool-teardown warn is emitted (web/tests/db-sync-http-mode.test.ts:31) |
| `web/tests/db-timeout-self-heal.test.ts` | GOOD | NONE | resetDb self-heal pinned across dbExecute/dbFirstRead/readOrdersFromDb via injected hanging promise + vi.advanceTimersByTimeAsync (web/tests/db-timeout-self-heal.test.ts:114) |
| `web/tests/db-writer-bounds.test.ts` | GOOD | NONE | Retry/backoff and circuit-breaker semantics exercised with fake timers + call-count assertions on a fake client execute (web/tests/db-writer-bounds.test.ts:98) |
| `web/tests/db-writer-direct-cloud.test.ts` | GOOD | NONE | Pure resolveClientConfig() tested for replica opt-in vs legacy kill-switch precedence and NODE_ENV=test override (web/tests/db-writer-direct-cloud.test.ts:70) |
| `web/tests/demo-admin.test.ts` | GOOD | NONE | Allowlist default-deny, revoke/extend dual-write (DB+Clerk) verified via injected fake db and setClerkMetadata spy (web/tests/demo-admin.test.ts:79) |
| `web/tests/demo-ai-quota.test.ts` | GOOD | NONE | Quota increment/limit boundary, per-user/per-endpoint scoping, and ET-day-boundary reset verified against an in-memory fake with fixed injected clock (web/tests/demo-ai-quota.test.ts:53) |
| `web/tests/demo-enforce-ai-quota.test.ts` | GOOD | NONE | 403/429/null branches of enforceDemoAiQuota all driven by injected authFn/dbFactory/now, asserting demo DB is never touched for non-demo users (web/tests/demo-enforce-ai-quota.test.ts:34) |
| `web/tests/demo-gate.test.ts` | GOOD | NONE | handleDemoGate branches (pass-through, 403 API vs redirect page, 429+Retry-After, no rate-limit on page nav) all asserted via injected rateLimiter and real NextRequest (web/tests/demo-gate.test.ts:71) |
| `web/tests/demo-order-blockade.test.ts` | GOOD | NONE | Three order-decision branches (allow/paper/block-expired) for a live-money-adjacent gate verified via injected authFn (web/tests/demo-order-blockade.test.ts:30) |
| `web/tests/demo-provision-trial.test.ts` | GOOD | NONE | Operator-never-provisioned guard, marker gating, assumeDemo OAuth path, and DB-before-Clerk write ordering all verified against a fake demo db and email-address parsing helpers (web/tests/demo-provision-trial.test.ts:102) |
| `web/tests/demo-rate-tier.test.ts` | GOOD | NONE | Pure classifyRateTier table-tested across AI/scan/write/read tiers including case-insensitivity (web/tests/demo-rate-tier.test.ts:29) |
| `web/tests/demo-role.test.ts` | GOOD | NONE | resolveDemoContext expiry logic and getDemoContext sessionClaims/publicMetadata fallback fully covered with injected authFn + fixed NOW (web/tests/demo-role.test.ts:73) |
| `web/tests/demo-signup-marker.test.ts` | GOOD | NONE | Pure shouldMarkDemoSignup strictly checks '1' vs other truthy strings, guarding prod-never-marks-demo (web/tests/demo-signup-marker.test.ts:21) |
| `web/tests/demo-svix-verify.test.ts` | GOOD | NONE | Real HMAC signing via crypto.subtle against the implementation under test; tamper, replay-timestamp, and multi-signature cases all covered (web/tests/demo-svix-verify.test.ts:78) |
| `web/tests/demo-trial-expiry.test.ts` | GOOD | NONE | computeTrialExpiry delegation to injected fetcher with default/custom tradingDays verified (web/tests/demo-trial-expiry.test.ts:37) |
| `web/tests/demo-users-view.test.ts` | GOOD | NONE | Status precedence (revoked>expired>active), countdown formatting, AI-burn summation all pure and fixed-clock (web/tests/demo-users-view.test.ts:35) |
| `web/tests/demo-users.test.ts` | GOOD | NONE | Idempotent upsert, revoke/extend reactivation, list ordering, per-day AI usage all verified against a fake demo db (web/tests/demo-users.test.ts:87) |
| `web/tests/demo-welcome-modal.test.tsx` | GOOD | NONE | Window-relative FUTURE/PAST dates (repo-standard, not fragile) drive active/expired/non-demo/session-once/env-gated branches via role-based queries (web/tests/demo-welcome-modal.test.tsx:38) |
| `web/tests/depth-book-render.test.tsx` | FRAGILE | P1 | Assertions lean almost entirely on CSS class-chain selectors (.book-row[data-lvlfirst], .book-sides, .book-nbbo-tag) rather than roles/test-ids; a CSS refactor breaks them without a behavior change (web/tests/depth-book-render.test.tsx:127) |
| `web/tests/depth-derivations.test.ts` | GOOD | NONE | groupPriceLevels/buildLadderRows/classifyTicks/deriveBookHeader/isBestLevel exhaustively covered incl. div-by-zero, one-sided/empty books, and the MU-corruption negative-L1 bug guard (web/tests/depth-derivations.test.ts:334) |
| `web/tests/desktop-touch-dropdown-css.test.ts` | GOOD | NONE | Parses real globals.css, extracts the actual media-block+selector rule via brace-depth matching, asserts real touch-target properties (web/tests/desktop-touch-dropdown-css.test.ts:44) |
| `web/tests/discover-no-store-header.test.ts` | GOOD | P2 | Cache-Control no-store verified for disk-fallback, empty-fallback, and POST passthrough via mocked fs/radonApi at the boundary (web/tests/discover-no-store-header.test.ts:64) |
| `web/tests/discover-route-staleness.test.ts` | GOOD | NONE | Naive-ISO-as-UTC parsing regression pinned against a real in-memory libsql db with fake system time locked to a fixed instant (web/tests/discover-route-staleness.test.ts:79) |
| `web/tests/dollar-delta-leverage.test.ts` | GOOD | NONE | Sign preservation, null-safety on non-finite/zero/negative NLV, and bias/format thresholds fully covered including exact boundary values (web/tests/dollar-delta-leverage.test.ts:94) |
| `web/tests/entry-dates.test.ts` | GOOD | NONE | Real journal-basis production repro (duplicated session+Flex open rows) plus re-open episode, sign-flip-through-zero, unsorted-row, and malformed-row cases all covered (web/tests/entry-dates.test.ts:56) |
| `web/tests/executed-today.test.ts` | GOOD | NONE | Fixed instants (not Date.now) exercise ET-day boundary logic incl. formatExecutedFillTime prefix rule; web/tests/executed-today.test.ts:19-29 pins the MSFT-prior-day bug directly. |
| `web/tests/exposure-breakdown-modal-leverage.test.tsx` | GOOD | NONE | Renders real component with realistic exposure fixtures, asserts exact multiplier/pct/sign/testid text; web/tests/exposure-breakdown-modal-leverage.test.tsx:75-87 checks concrete computed values not just presence. |
| `web/tests/exposure-breakdown.test.ts` | GOOD | NONE | Calls computeExposureDetailed with real PortfolioData/PriceData shapes and asserts signed leg deltas to 4 decimals; web/tests/exposure-breakdown.test.ts:110-115 pins the sign-convention rule from CLAUDE.md. |
| `web/tests/fastapi-migration.test.ts` | GOOD | NONE | Exercises real route handlers with radonFetch/db mocked at the boundary; covers success, cache-fallback, 502-no-cache, and IB silent-rejection detection (web/tests/fastapi-migration.test.ts:488-507). |
| `web/tests/favicon-exists.test.ts` | GOOD | NONE | Real filesystem read + magic-byte assertion pins a genuine prod 404 regression; web/tests/favicon-exists.test.ts:20-31. |
| `web/tests/fill-toasts-hook.test.tsx` | GOOD | NONE | renderHook with real dedupe/priming/sessionStorage-survival scenarios incl. mid-navigation fill and demo-suppression; web/tests/fill-toasts-hook.test.tsx:127-143. |
| `web/tests/fill-toasts.test.ts` | GOOD | NONE | Pure-function tests with exact string assertions and a FakeStorage boundary fake, covers correction-suffix collapse and MAX_SEEN_KEYS cap; web/tests/fill-toasts.test.ts:161-170. |
| `web/tests/flex-token-expiry.test.ts` | REFACTOR | P1 | Every assertion is `content.toContain("substring")` against raw source text (web/tests/flex-token-expiry.test.ts:107-121) — the route/handler/component are never imported or executed, so a wrong days_remaining/should_warn computation ships green. |
| `web/tests/flow-analysis-classify.test.ts` | REFACTOR | P1 | classifyPosition (web/tests/flow-analysis-classify.test.ts:19-46) is an inline TS reimplementation of scripts/flow_analysis.py logic, not an import of the real module — a fix or bug in the Python source is invisible here. |
| `web/tests/flow-analysis-no-store-header.test.ts` | GOOD | NONE | Imports the real route, mocks only fs/db/radonApi at the boundary, asserts Cache-Control across GET empty/hit and POST; web/tests/flow-analysis-no-store-header.test.ts:79-100. |
| `web/tests/flow-analysis-ticker-no-store-header.test.ts` | GOOD | NONE | Same pattern as the sibling route test, covers 404/400 envelopes too; web/tests/flow-analysis-ticker-no-store-header.test.ts:57-71. |
| `web/tests/flow-analysis-ticker-route.test.ts` | GOOD | NONE | Real GET/POST handlers exercised with ticker validation, cache-fallback and 502-no-cache paths; web/tests/flow-analysis-ticker-route.test.ts:73-81. |
| `web/tests/flow-projection-trace.test.tsx` | GOOD | NONE | Renders the real component and checks SVG path presence plus a brand-token contract (no raw hex/rgba); web/tests/flow-projection-trace.test.tsx:60-70. |
| `web/tests/flow-ratio.test.ts` | GOOD | NONE | Small pure-function test with exact numeric assertions covering premium-ratio, canonical, legacy-invert, and zero-denominator branches; web/tests/flow-ratio.test.ts:18-20. |
| `web/tests/flow-report-staleness.test.ts` | GOOD | NONE | Injects explicit `now` Date fixtures (never Date.now) and pins a real naive-UTC-vs-local timezone bug; web/tests/flow-report-staleness.test.ts:85-98. |
| `web/tests/flow-signal.test.ts` | GOOD | NONE | Covers null-safety, direction/confidence/strength across confluence and disagreement branches with concrete assertions; web/tests/flow-signal.test.ts:57-70. |
| `web/tests/flow-surprise-card.test.tsx` | IMPROVE | P2 | Component tests are solid, but the route-shape describe block asserts on raw source text via string containment rather than executing the route; web/tests/flow-surprise-card.test.tsx:121-135 would pass even if the runtime logic diverged from the string literals. |
| `web/tests/footer-telemetry-unknown.test.tsx` | GOOD | NONE | Real component render pins the 'Unknown must never render as nominal' invariant plus a pure-function unit check; web/tests/footer-telemetry-unknown.test.tsx:28-37. |
| `web/tests/forecast-band.test.ts` | GOOD | NONE | Exact-string formatting and tone-branch coverage incl. undefined-input fallback; web/tests/forecast-band.test.ts:21-51. |
| `web/tests/format-trade-date.test.ts` | GOOD | NONE | Deliberately reproduces the UTC-midnight-shift bug with a locale-agnostic assertion and an environment-conditional buggy-vs-fixed comparison; web/tests/format-trade-date.test.ts:37-52. |
| `web/tests/futures-chain-api.test.ts` | GOOD | NONE | Real route handlers with boundary-mocked radonFetch/fs, covers 400/200/502/stale-cache and a TypeBox schema branch; web/tests/futures-chain-api.test.ts:81-102. |
| `web/tests/futures-quote-fallback.test.ts` | GOOD | NONE | Real route + hook exercised with fetch mocked at the boundary, covers Globex-closed gating and symbol mapping; web/tests/futures-quote-fallback.test.ts:81-89. |
| `web/tests/futures-session.test.ts` | GOOD | NONE | Pure function tested against explicit fixed-offset Date literals (window-relative/fixed-fixture style, not Date.now), covers open/close/maintenance/weekend boundaries; web/tests/futures-session.test.ts:23-49. |
| `web/tests/futures-strip.test.tsx` | GOOD | NONE | Renders real component, asserts computed % change sign and missing-data placeholders; web/tests/futures-strip.test.tsx:17-53. |
| `web/tests/fuzz/order-risk.fuzz.test.ts` | GOOD | NONE | Property-based fuzz over the real order-risk seam with a pinned CI seed, encodes the exact monotonicity/linearity invariants that caught 3 production money-losing bugs; web/tests/fuzz/order-risk.fuzz.test.ts:243-293. |
| `web/tests/gamma-rotation-panel.test.tsx` | GOOD | NONE | Renders real component against a realistic GRG fixture and checks concrete rendered signal/asset/gate text plus chart presence; web/tests/gamma-rotation-panel.test.tsx:103-113. |
| `web/tests/gamma-rotation-route.test.ts` | GOOD | NONE | Deterministic mocks for GET (DB-first, disk not read at web/tests/gamma-rotation-route.test.ts:38-46) and POST (FastAPI proxy) prove real route contracts. |
| `web/tests/gex-laplace-contour.test.tsx` | GOOD | NONE | Renders real component and asserts structural/token contracts via data-testid and fill values, e.g. web/tests/gex-laplace-contour.test.tsx:60-68, not implementation internals. |
| `web/tests/gex-laplace-marker-lanes.test.ts` | GOOD | NONE | Pure function tests of assignMarkerLanes verify real collision-avoidance invariants (web/tests/gex-laplace-marker-lanes.test.ts:37-45) with deterministic fixtures. |
| `web/tests/gex-panel.test.tsx` | IMPROVE | P2 | 29 solid render/interaction tests, but web/tests/gex-panel.test.tsx:322-329 greps GexPanel.tsx source for a string instead of exercising the share button's actual wiring. |
| `web/tests/gex-route-prefer-newer.test.ts` | GOOD | NONE | Covers the real 2026-06-23 stale-Turso-vs-fresh-disk regression with three deterministic scenarios, e.g. web/tests/gex-route-prefer-newer.test.ts:31-39. |
| `web/tests/gex-share.test.ts` | REFACTOR | P2 | All 19 assertions are literal string-containment checks on source files (e.g. web/tests/gex-share.test.ts:36-39 pins card1_/card2_/card3_/card4_ names) with zero execution of the script or routes. |
| `web/tests/gex-staleness.test.ts` | GOOD | NONE | isGexDataStale exercised across market open/closed, missing/invalid data, and the naive-ISO UTC-host bug (web/tests/gex-staleness.test.ts:91-118) with vi.useFakeTimers pinning wall clock. |
| `web/tests/grg-freshness-badge.test.tsx` | GOOD | NONE | Mocks the staleness module deliberately to make priority ordering deterministic (web/tests/grg-freshness-badge.test.tsx:140-149) and asserts real rendered badge state/class/aria. |
| `web/tests/header-fullscreen-control.test.ts` | REFACTOR | P2 | Both tests are raw source-string containment on Header.tsx/WorkspaceShell.tsx (web/tests/header-fullscreen-control.test.ts:12-16, 25-28) with no render or interaction. |
| `web/tests/historical-trades-filter.test.tsx` | GOOD | NONE | Real render + fireEvent interactions cover filtering, partial-close realized P&L display, staleness pill, and localStorage page-size persistence (web/tests/historical-trades-filter.test.tsx:277-292). |
| `web/tests/hooks-offline-signals.test.tsx` | GOOD | NONE | Real hooks driven with header-marked fetch responses to distinguish offline-served/fetch-failure/fetch-success across two hooks, e.g. web/tests/hooks-offline-signals.test.tsx:60-62. |
| `web/tests/ib-connection-issue.test.ts` | GOOD | NONE | classifyIBConnectionError exercised for both the MFA-issue match and a clean negative case (web/tests/ib-connection-issue.test.ts:8-29). |
| `web/tests/ib-delayed-ticks.test.ts` | GOOD | NONE | Comprehensive live+delayed tick-type coverage on real handler functions, including the -1 sentinel normalization and mid-vs-close derivation (web/tests/ib-delayed-ticks.test.ts:122-138). |
| `web/tests/ib-depth-stream-contracts.test.ts` | REFACTOR | P1 | All 15 tests are regex/string matches against ib_realtime_server.js source (e.g. web/tests/ib-depth-stream-contracts.test.ts:23-28), never executing the depth/NBBO/futures-resolution logic. |
| `web/tests/ib-fundamentals.test.ts` | GOOD | NONE | parseFundamentalRatios covered for DBL_MAX sentinel filtering, malformed pairs, NaN, negative EPS, and timestamp update behavior (web/tests/ib-fundamentals.test.ts:73-80, 145-151). |
| `web/tests/ib-index-stream-contracts.test.ts` | REFACTOR | P1 | Cold-start subscription restore is validated purely via regex-extracted source blocks and string containment (web/tests/ib-index-stream-contracts.test.ts:15-28), never running restoreSubscriptions. |
| `web/tests/ib-no-security-def.test.ts` | REFACTOR | P1 | NO_SEC_DEF_REGEX and the cleanup logic are hand-copied into the test file (web/tests/ib-no-security-def.test.ts:12, 53-61) instead of imported from ib_realtime_server.js, so it verifies its own duplicate, not the real handler. |
| `web/tests/ib-realtime-port-conflict.test.ts` | GOOD | NONE | Spawns the real server as a subprocess against an occupied port and asserts real exit code/stdout/stderr (web/tests/ib-realtime-port-conflict.test.ts:47-71), a genuine integration test. |
| `web/tests/ib-realtime-restart-modes.test.ts` | REFACTOR | P1 | WS-auth trust delegation and restart-mode branching (cloud/docker vs local launchd, which avoids 2FA-stacking) are validated only via regex-block string containment (web/tests/ib-realtime-restart-modes.test.ts:40-57), never executed. |
| `web/tests/ib-status-context.test.ts` | GOOD | NONE | Real hook rendering with a MockWebSocket drives connection-state transitions, reconnect backoff, abort-bounded health polling, and degrade-to-unhealthy after repeated failures (web/tests/ib-status-context.test.ts:243-271). |
| `web/tests/ib-sync-cached-polling.test.tsx` | GOOD | NONE | Exercises real hooks to prove polling never auto-POSTs and a slow cached read can't clobber a newer manual sync (web/tests/ib-sync-cached-polling.test.tsx:189-206), covering a genuine race. |
| `web/tests/implied-value.test.ts` | GOOD | NONE | Black-Scholes implied-value pipeline cross-checked against the reference bsCall/bsPut for legs, positions, orders, and the VIX per-expiry forward-curve fix (web/tests/implied-value.test.ts:226-248). |
| `web/tests/index-options-chain-api.test.ts` | GOOD | NONE | Route tests cover 400 on missing symbol, 502 envelope on upstream failure, and correct query forwarding (web/tests/index-options-chain-api.test.ts:52-57). |
| `web/tests/index-quote-fallback.test.ts` | GOOD | NONE | Real route + merge-logic tests validate Yahoo fallback shape, symbol rejection, and that a usable live quote is never overwritten by fallback (web/tests/index-quote-fallback.test.ts:114-118). |
| `web/tests/index-symbols.test.ts` | GOOD | NONE | Pure lookup-table tests cover case-insensitivity, futures-support mapping tied to contract_resolver.py, and negative cases (web/tests/index-symbols.test.ts:58-73). |
| `web/tests/info-tooltip-flip.test.tsx` | GOOD | NONE | Deterministically mocks getBoundingClientRect per real measured popup height and asserts real render output for both flip and non-flip cases (web/tests/info-tooltip-flip.test.tsx:55-71). |
| `web/tests/informed-flow-panel.test.tsx` | GOOD | NONE | Real fetch mocks + render, asserts rendered names, empty/error states, brand-token guard; web/tests/informed-flow-panel.test.tsx:84-116. |
| `web/tests/instrument-detail-buy-to-cover.test.tsx` | GOOD | NONE | Exercises real modal + PositionTable, asserts exact submitted order JSON and derived P&L dollars; web/tests/instrument-detail-buy-to-cover.test.tsx:219-229. |
| `web/tests/instrument-detail-spread-quantity.test.ts` | GOOD | NONE | SSR render, asserts exact spread $ and % string; web/tests/instrument-detail-spread-quantity.test.ts:74. |
| `web/tests/integration.test.ts` | GOOD | NONE | Runs real python scripts via spawnSync and asserts JSON output/exit codes deterministically; web/tests/integration.test.ts:188-208. |
| `web/tests/internals-no-store-header.test.ts` | GOOD | NONE | Focused header-contract test with clean mocks; web/tests/internals-no-store-header.test.ts:50-60. |
| `web/tests/internals-skew-chart.test.ts` | REFACTOR | P2 | Reimplements fmtSigned/heights/sort locally instead of importing the component's logic, so a real component bug goes undetected; web/tests/internals-skew-chart.test.ts:11-35,33. |
| `web/tests/internals-skew-route-staleness.test.ts` | REFACTOR | P2 | Never calls the route handler despite the file/describe name; re-tests isSkewCacheFresh already covered verbatim by internals-skew-staleness.test.ts; web/tests/internals-skew-route-staleness.test.ts:32-58 vs internals-skew-staleness.test.ts:5-33. |
| `web/tests/internals-skew-staleness.test.ts` | GOOD | NONE | Direct pure-function boundary/threshold coverage incl. invalid input and custom maxStaleDays; web/tests/internals-skew-staleness.test.ts:47-52. |
| `web/tests/journal-discover.test.ts` | IMPROVE | P2 | Half the file greps source text for literals (e.g. reqMarketDataType(1), '10147') instead of exercising behavior; web/tests/journal-discover.test.ts:52-53,81. |
| `web/tests/journal-no-store-header.test.ts` | GOOD | NONE | Covers success AND 500-error no-store paths with clean mocks; web/tests/journal-no-store-header.test.ts:43-48. |
| `web/tests/journal-range-pnl.test.ts` | GOOD | NONE | Pure range/PnL logic with ET-day boundary, close-vs-activity modes, null-pnl exclusion; web/tests/journal-range-pnl.test.ts:115-119,136-146. |
| `web/tests/journal-range-ui.test.tsx` | GOOD | NONE | Fake-timers pin 'now', asserts MTD/All/custom-range UI states end-to-end through WorkspaceSections; web/tests/journal-range-ui.test.tsx:88-91,113-119. |
| `web/tests/journal-realized-pnl.test.ts` | GOOD | NONE | Derives every expected $ from fixture arithmetic, pins real incidents (dedup, cross-family commission signs, assignment/exercise, portfolio-basis fallback); web/tests/journal-realized-pnl.test.ts:177-181,526-528. |
| `web/tests/journal-sync-config.test.ts` | REFACTOR | P2 | Greps hook source for exact literals ('endpoint: "/api/journal"', 'interval: 0'), breaks on harmless reformatting without exercising runtime behavior; web/tests/journal-sync-config.test.ts:12-14. |
| `web/tests/journal-sync.test.ts` | GOOD | NONE | Pure logic covering STK/OPT/BAG cost math, dedupe by exec-id (incl. composite/correction), sequential IDs; web/tests/journal-sync.test.ts:116-119,258-273. |
| `web/tests/knowledge-routes.test.ts` | GOOD | NONE | Covers forwarding, validation, RadonApiError status passthrough, and 502 mapping for both routes; web/tests/knowledge-routes.test.ts:98-113,208-218. |
| `web/tests/leap-garch-scan-route.test.ts` | GOOD | NONE | Validates ticker normalization/dedup, malformed-input 400 rejection before FastAPI call, no-store header; web/tests/leap-garch-scan-route.test.ts:85-98,137-150. |
| `web/tests/leap-garch-scanner.test.tsx` | GOOD | NONE | Renders real components, asserts rendered rows, empty vs zero-result states, input validation alerts; web/tests/leap-garch-scanner.test.tsx:103-119,134-145. |
| `web/tests/legacy-tab-to-deck.test.ts` | GOOD | NONE | Pure mapping function fully exercised incl. case-sensitivity and VALID_DECKS membership invariants; web/tests/legacy-tab-to-deck.test.ts:69-73,91-97. |
| `web/tests/live-data-degraded-banner.test.ts` | REFACTOR | P1 | Purely greps WorkspaceShell/offlineStatus source text, never renders the shell or triggers priceError to see the banner appear; web/tests/live-data-degraded-banner.test.ts:18-30. |
| `web/tests/llm-provider.test.ts` | GOOD | NONE | Real fetch capture across 5 providers, verifies payload/auth headers, fallback-on-error, tool serialization both ways; web/tests/llm-provider.test.ts:266-290,300-324. |
| `web/tests/local-font-build.test.ts` | IMPROVE | P2 | Pure source-text grep of layout.tsx with no build/render verification; passes even if the referenced font files are missing; web/tests/local-font-build.test.ts:10-22. |
| `web/tests/lru-cache.test.ts` | GOOD | NONE | Thorough get/set/evict/promote/iteration coverage on the real class; web/tests/lru-cache.test.ts:60-72,98-112. |
| `web/tests/margin-debt-api.test.ts` | GOOD | NONE | Real in-memory libsql client seeded per test, verifies Turso-first-over-disk, disk fallback, missing:true 200 contract, service scoping; web/tests/margin-debt-api.test.ts:90-104,132-142. |
| `web/tests/margin-debt-panel.test.tsx` | GOOD | NONE | Pure helper edge cases (null/NaN, MA null-shrink) plus rendered panel gating, chip toggles, NaN-path guard on log scale; web/tests/margin-debt-panel.test.tsx:75-80,350-359. |
| `web/tests/margin-warning.test.ts` | GOOD | NONE | Full threshold matrix incl. IBKR 110% rule, cashToClear derivation verified by re-applying the deposit to confirm it actually clears; web/tests/margin-warning.test.ts:94-131,133-157. |
| `web/tests/market-phase.test.ts` | GOOD | NONE | Pure boundary tests on getMarketPhaseFromDate across ET open/close edges, deterministic fixed Dates, no wall-clock (web/tests/market-phase.test.ts:8-53). |
| `web/tests/market-session.test.ts` | GOOD | NONE | Deterministic weekday/weekend session-resolution + staleness pins with clear ET-mapping comments (web/tests/market-session.test.ts:12-54). |
| `web/tests/market-state-holiday.test.ts` | GOOD | NONE | Real holiday table imported and iterated per-year sanity check (web/tests/market-state-holiday.test.ts:56-60), deterministic fixed dates. |
| `web/tests/markov-state-graph.test.tsx` | GOOD | NONE | Uses stable data-* attribute selectors, asserts real rendered SVG attributes/stroke-width ordering (web/tests/markov-state-graph.test.tsx:50-60). |
| `web/tests/menthorq-cta-db-route.test.ts` | GOOD | NONE | Real in-memory libsql client + real route import, no over-mocking of the module under test (web/tests/menthorq-cta-db-route.test.ts:15-27,56-61). |
| `web/tests/menthorq-cta-no-store-header.test.ts` | GOOD | NONE | Boundary-only mocks (fs, child_process, db); asserts real Cache-Control header on both success and 503 paths (web/tests/menthorq-cta-no-store-header.test.ts:54-67). |
| `web/tests/menthorq-cta-route.test.ts` | GOOD | NONE | Real route exercised with fake timers + boundary fs/child_process mocks, asserts stale/fresh cache_meta and spawn call counts (web/tests/menthorq-cta-route.test.ts:70-77,112-117). |
| `web/tests/menthorq-og-route-contract.test.ts` | GOOD | NONE | Real renderToStaticMarkup of the actual renderer selection, asserts family/component routing and rendered SVG content (web/tests/menthorq-og-route-contract.test.ts:31-34,86-89). |
| `web/tests/middleware-allowlist.test.ts` | GOOD | NONE | Pure decision-function tests pinning a real prior production incident (2026-06-27), including fail-closed interlock semantics (web/tests/middleware-allowlist.test.ts:75-86). |
| `web/tests/middleware-auth.test.ts` | GOOD | NONE | Filesystem-walking default-deny matrix classifies every real route.ts on disk, hard-pins all /api/admin/* protected (web/tests/middleware-auth.test.ts:115-158). |
| `web/tests/middleware-authless.test.ts` | GOOD | NONE | Deterministic pure-function tests of localhost/env bypass logic incl. prod-never-bypasses case (web/tests/middleware-authless.test.ts:38-40). |
| `web/tests/middleware-probe-gate.test.ts` | GOOD | NONE | Exercises real timing-safe compare, bearer parsing, and gate function incl. closed-perimeter-on-unset-token case (web/tests/middleware-probe-gate.test.ts:85-90,134-140). |
| `web/tests/middleware-share-allowlist.test.ts` | GOOD | NONE | Filesystem-pin ensures share/probe/webhook allowlists match route files on disk exactly, forcing reviewed additions (web/tests/middleware-share-allowlist.test.ts:96-99,110-113,123-126). |
| `web/tests/mobile-app-bar-authless.test.tsx` | GOOD | NONE | Throwing Clerk/profile mocks positively prove hooks are never called under authless flag, real render assertions (web/tests/mobile-app-bar-authless.test.tsx:10-14,25-28). |
| `web/tests/mobile-book-legibility.test.ts` | IMPROVE | P2 | CSS text-contract via fixed 1600-char slice offset rather than rendered legibility check (web/tests/mobile-book-legibility.test.ts:14-20). |
| `web/tests/mobile-bottom-sheet.test.tsx` | GOOD | NONE | Real DOM/portal/keyboard-inset behavior via testids and CustomEvent dispatch, covers real iOS regressions (web/tests/mobile-bottom-sheet.test.tsx:68-79,126-152). |
| `web/tests/mobile-combo-instrument-switcher.test.tsx` | GOOD | NONE | Renders real component tree with getByRole selectors, asserts aria-pressed state transitions on real click (web/tests/mobile-combo-instrument-switcher.test.tsx:207-226). |
| `web/tests/mobile-flow-sparkline.test.tsx` | GOOD | NONE | Real pointerDown interaction on the rendered SVG, asserts caption text incl. null-ratio placeholder path (web/tests/mobile-flow-sparkline.test.tsx:34-49). |
| `web/tests/mobile-order-list-display.test.tsx` | GOOD | NONE | Real component render, covers partial-fill, tone-by-coverage, combo cancel wiring with call-arg assertions (web/tests/mobile-order-list-display.test.tsx:371-377). |
| `web/tests/mobile-order-ticket.test.tsx` | GOOD | NONE | P0 order-entry surface exercised end-to-end (OrderRiskGate, useOrderRisk, error formatting all real), only unrelated ComboSkewPanel stubbed (web/tests/mobile-order-ticket.test.tsx:18-33,199-222). |
| `web/tests/mobile-position-ec-sign-underlying.test.tsx` | GOOD | NONE | P0 sign-convention regression using real production position numbers (SPCX credit combo) with precise sign assertions (web/tests/mobile-position-ec-sign-underlying.test.tsx:85-90). |
| `web/tests/mobile-position-leg-tap.test.tsx` | GOOD | NONE | Real click-through to per-leg modal open, asserts correct leg props including bubbling guard (web/tests/mobile-position-leg-tap.test.tsx:67-91). |
| `web/tests/mobile-position-short-pnl.test.tsx` | GOOD | NONE | P0 regression with exact real MU short-stock numbers pinning the correct signed P&L vs the additive phantom bug (web/tests/mobile-position-short-pnl.test.tsx:48-56). |
| `web/tests/mobile-primitives.test.tsx` | GOOD | NONE | Simple presentational primitives verified via stable CSS class + DOM order for size/tone variants (web/tests/mobile-primitives.test.tsx:19-20,93-94). |
| `web/tests/mobile-sheet-ios-viewport.test.ts` | IMPROVE | P2 | Text-contract on CSS source (rule-block extraction) rather than a rendered computed-style assertion for the iOS viewport fix (web/tests/mobile-sheet-ios-viewport.test.ts:21-26,29-34). |
| `web/tests/mobile-sort-parity.test.tsx` | GOOD | NONE | Pins mobile sort-chip key parity against desktop key sets and exercises real click-driven reordering via getByRole (web/tests/mobile-sort-parity.test.tsx:29-42,101-104,157-160). |
| `web/tests/modify-order-close-pnl.test.tsx` | GOOD | NONE | Real RTL render, asserts exact P&L/proceeds values and unbounded-fallback branch; web/tests/modify-order-close-pnl.test.tsx:128-158. |
| `web/tests/modify-order-modal-layout.test.ts` | FRAGILE | P2 | Regex-extracts raw CSS text and substring-matches declarations; harmless selector reformatting (e.g. combined selectors) breaks it without a visual regression. web/tests/modify-order-modal-layout.test.ts:13-16. |
| `web/tests/modify-order-negative-risk-reversal.test.tsx` | GOOD | NONE | Full RTL render of a signed combo BAG order, asserts BID/MID/ASK/IMPLIED button labels and exact onConfirm payload; web/tests/modify-order-negative-risk-reversal.test.tsx:158-184. |
| `web/tests/modify-order-quote.test.ts` | GOOD | NONE | Pure-function tests on applyRestingLimitToQuote covering improve/no-improve branches for both sides; web/tests/modify-order-quote.test.ts:14-42. |
| `web/tests/modify-order-ticker-detail.test.ts` | REFACTOR | P1 | Regex-matches raw source text (e.g. /setModifyTarget\(null\)/, /onModify\(/) instead of rendering/exercising the modify flow, so a behavior-preserving rename breaks it while a real wiring bug can pass; web/tests/modify-order-ticker-detail.test.ts:46,56,61,86,91. |
| `web/tests/naked-short-guard.test.ts` | IMPROVE | P1 | Only the 5 SPX-05 stock-warn tests actually run; the entire option/combo naked-short blocking suite (tests 6,8,11-20) is describe.skip, leaving `_*Impl` re-enable path with zero live coverage; web/tests/naked-short-guard.test.ts:128,460. |
| `web/tests/newsfeed-auth.test.ts` | GOOD | NONE | Fake Playwright Page/Locator doubles drive real ensureAuthenticated logic, asserting persist counts and login-flow triggering on silent paywall; web/tests/newsfeed-auth.test.ts:176-205. |
| `web/tests/newsfeed-cycle-ordering.test.ts` | GOOD | NONE | DI-based ordering assertions on an array of pushed event names, not wall-clock thresholds; real setTimeout only simulates slow async work. web/tests/newsfeed-cycle-ordering.test.ts:85-97. |
| `web/tests/newsfeed-db-dual-write.test.ts` | GOOD | NONE | Hermetic in-memory libSQL, exercises real upsert/taxonomy/service_health SQL end-to-end; web/tests/newsfeed-db-dual-write.test.ts:38-51,103-118. |
| `web/tests/newsfeed-image-url.test.ts` | GOOD | NONE | Real filesystem + in-memory DB coverage of the media-URL absolutization contract across scraper, store, vision tagger, and writer; web/tests/newsfeed-image-url.test.ts:60-95,224-260. |
| `web/tests/newsfeed-lightbox-mobile-css.test.ts` | FRAGILE | P2 | Custom brace-depth parser regex-extracts a media-query block from globals.css and negative-matches alternate selector spellings; a harmless CSS restructure (renamed/merged media query) breaks it. web/tests/newsfeed-lightbox-mobile-css.test.ts:7-33. |
| `web/tests/newsfeed-lightbox.test.tsx` | GOOD | NONE | RTL render with role/testid queries covering dismiss, escape, scroll-lock, and prev/next navigation gating; web/tests/newsfeed-lightbox.test.tsx:106-166. |
| `web/tests/newsfeed-migrate-relative-image-urls.test.ts` | GOOD | NONE | Real tmp-dir filesystem migration test covering dry-run, apply, idempotency, and archive traversal; web/tests/newsfeed-migrate-relative-image-urls.test.ts:109-193. |
| `web/tests/newsfeed-offline-keeps-posts.test.tsx` | GOOD | NONE | vi.useFakeTimers + stubbed fetch, asserts held posts survive a rejected background refresh without a real sleep; web/tests/newsfeed-offline-keeps-posts.test.tsx:47-82. |
| `web/tests/newsfeed-paywall-detection.test.ts` | IMPROVE | P2 | Excellent DI coverage of the cycle-level paywall guard, but the final auth.js test reimplements the isPremium check inline instead of calling the real pageHasPremiumContent export, so it can drift silently from the implementation. web/tests/newsfeed-paywall-detection.test.ts:343-349. |
| `web/tests/newsfeed-posts-api.test.ts` | GOOD | NONE | In-memory libSQL route test covering ordering, empty state, 503-on-DB-failure, and malformed-JSON-column resilience; web/tests/newsfeed-posts-api.test.ts:98-113,115-139. |
| `web/tests/newsfeed-scraper.test.ts` | GOOD | NONE | Real tmp filesystem + jsdom/vm-executed DOM extraction covering merge diffing, archive rollover thresholds, cookie-gated downloads, and image-bleed regressions; web/tests/newsfeed-scraper.test.ts:565-661. |
| `web/tests/newsfeed-scrub-generic-image-attributions.test.ts` | GOOD | NONE | Real tmp-dir filesystem coverage of scrub apply/dry-run/idempotency; web/tests/newsfeed-scrub-generic-image-attributions.test.ts:71-138. |
| `web/tests/newsfeed-tag-hierarchy.test.ts` | GOOD | NONE | Pure-function coverage of TA parent-tag enrichment including deliberate false-positive exclusions (MOMENTUM/TREND) and a dual-tagger integration case; web/tests/newsfeed-tag-hierarchy.test.ts:41-51,123-159. |
| `web/tests/newsfeed-tagger.test.ts` | GOOD | NONE | Stubbed fetch covers 429 fallback chain, normalization, trimming, and post-cleanup retagging; asserts on real request bodies not just call counts. web/tests/newsfeed-tagger.test.ts:82-99,128-144. |
| `web/tests/newsfeed-taxonomy.test.ts` | GOOD | NONE | Real tmp-dir filesystem coverage including a genuine concurrent-writer race resolved via set-equality assertion, not order assumptions; web/tests/newsfeed-taxonomy.test.ts:85-103. |
| `web/tests/newsfeed-time.test.ts` | GOOD | NONE | Deterministic formatting tests using explicit local-time constructors and a wrapped Intl.DateTimeFormat to pin locale/hour12 without depending on host locale; web/tests/newsfeed-time.test.ts:22-45. |
| `web/tests/newsfeed-vision-tagger.test.ts` | GOOD | NONE | DI-injected fetch/readImage/taxonomy boundary fakes exercise real JSON-extraction regressions (web/tests/newsfeed-vision-tagger.test.ts:204). |
| `web/tests/next-security-headers.test.ts` | GOOD | NONE | Imports real next.config.mjs and asserts actual computed headers array (web/tests/next-security-headers.test.ts:19). |
| `web/tests/offline-banner.test.tsx` | GOOD | NONE | role/testid-based assertions on rendered banner text and states (web/tests/offline-banner.test.tsx:37). |
| `web/tests/offline-status.test.ts` | GOOD | NONE | Pure reducer with fixed NOW constant and exhaustive debounce/trip/recovery branch matrix (web/tests/offline-status.test.ts:22). |
| `web/tests/og-chart-contract.test.ts` | GOOD | NONE | Renders real chart SVGs via renderToStaticMarkup and asserts on actual markup output (web/tests/og-chart-contract.test.ts:19). |
| `web/tests/og-chart-system.test.ts` | REFACTOR | P2 | Asserts on raw source-file text (`ogChartsSource).toContain(...)`) instead of executing/rendering code (web/tests/og-chart-system.test.ts:29-31). |
| `web/tests/og-theme-contract.test.ts` | GOOD | NONE | Compares real OG theme values and family contract objects against the shared spec JSON (web/tests/og-theme-contract.test.ts:16). |
| `web/tests/open-order-combo-modify.test.ts` | GOOD | NONE | Realistic IB order payloads verify BAG cancel/modify target construction and ratio reduction (web/tests/open-order-combo-modify.test.ts:69-88). |
| `web/tests/open-order-combos.test.ts` | GOOD | NONE | Extensive realistic-data behavior coverage of combo grouping, net-quote pricing, and executed-fill descriptions (web/tests/open-order-combos.test.ts:238-252). |
| `web/tests/open-order-single-detail.test.ts` | GOOD | NONE | Verifies portfolio-derived single-order direction/strike/expiry summary text (web/tests/open-order-single-detail.test.ts:94). |
| `web/tests/options-chain-implied.test.tsx` | GOOD | NONE | Renders real OptionsChainTab and checks BS-derived values for math parity against bsCall/bsPut (web/tests/options-chain-implied.test.tsx:188-198). |
| `web/tests/options-chain-utils.test.ts` | GOOD | NONE | Broad, deterministic structure-detection/pricing coverage including the bearish-RR IB-routing edge case (web/tests/options-chain-utils.test.ts:474-479). |
| `web/tests/options-exposure-chart-export.test.ts` | GOOD | NONE | Recording canvas-context stub captures real draw calls for content assertions, no canvas dependency needed (web/tests/options-exposure-chart-export.test.ts:50-73). |
| `web/tests/options-exposure-hook.test.tsx` | GOOD | NONE | renderHook against real fetch mock validates URL, abort-on-rerender, and error sanitization/retry (web/tests/options-exposure-hook.test.tsx:43-59). |
| `web/tests/options-exposure-navigation.test.ts` | IMPROVE | P2 | Mixes real routing-resolver assertions with brittle source-text containment on page.tsx/panel.tsx (web/tests/options-exposure-navigation.test.ts:24-35). |
| `web/tests/options-exposure-panel.test.tsx` | GOOD | NONE | Role/testid-driven rendering assertions across loading/error/empty/data states (web/tests/options-exposure-panel.test.tsx:131-152). |
| `web/tests/options-exposure-route.test.ts` | GOOD | NONE | Route handler tested end to end with validation, sanitized upstream errors, and timeout mapping (web/tests/options-exposure-route.test.ts:76-105). |
| `web/tests/options-exposure-transform.test.ts` | GOOD | NONE | Deterministic aggregation/window-selection math with malformed-index resilience case (web/tests/options-exposure-transform.test.ts:72-85). |
| `web/tests/options-workspace-tabs.test.tsx` | GOOD | NONE | Role-based rendering and router-push assertions on real OptionsWorkspacePanel behavior (web/tests/options-workspace-tabs.test.tsx:44-58). |
| `web/tests/order-builder-layout.test.tsx` | IMPROVE | P2 | Component tests are solid but the 'OrderBuilder source contract' block asserts on raw TSX/CSS source text rather than rendered output (web/tests/order-builder-layout.test.tsx:63-84). |
| `web/tests/order-confirm-summary-undefined-risk.test.tsx` | GOOD | NONE | Renders real OrderConfirmSummary for the documented AAOI P0 bug shape and asserts corrected dollar Max Loss (web/tests/order-confirm-summary-undefined-risk.test.tsx:47-54). |
| `web/tests/order-cost-quotes.test.tsx` | GOOD | NONE | Ties useOrderRisk output to the real cost model via estimateRoundTripCost, not hand-derived numbers (web/tests/order-cost-quotes.test.tsx:78-87). |
| `web/tests/order-costs.test.ts` | GOOD | NONE | Every expected value is derived from the actual exported constants/functions, including net-of-cost clamp-at-zero (web/tests/order-costs.test.ts:162-168). |
| `web/tests/order-e2e.test.ts` | IMPROVE | P0 | FastAPI-backed place/modify/cancel tests only assert res.status===200 with no body verification of orderId/permId/fields (web/tests/order-e2e.test.ts:48-50, 89-90). |
| `web/tests/order-error-format.test.ts` | GOOD | NONE | Deterministic string-transform tests directly reproducing real IB rejection text including <br> variants (web/tests/order-error-format.test.ts:29-43). |
| `web/tests/order-idempotency.test.ts` | GOOD | NONE | Covers concurrent dedup, TTL expiry via vi.useFakeTimers, and failure-clears-key semantics for order placement idempotency (web/tests/order-idempotency.test.ts:46-53, 55-67). |
| `web/tests/order-margin-impact.test.tsx` | GOOD | NONE | Pure estimateInitialMargin branches plus useOrderRisk+OrderConfirmSummary render checks pin the Reg-T-not-maxLoss invariant, e.g. web/tests/order-margin-impact.test.tsx:71-76. |
| `web/tests/order-migration.test.ts` | REFACTOR | P2 | No production module is ever imported; every 'it' asserts on hand-written local literals (web/tests/order-migration.test.ts:15-21, 42-46), so it can never fail from a real regression. Replace with real component/render assertions or delete. |
| `web/tests/order-paper-toggle.test.tsx` | GOOD | NONE | Renders real OrderRiskGate, uses testid + fireEvent, and pins resolvePlacementTarget's paper/live routing directly (web/tests/order-paper-toggle.test.tsx:44-50). |
| `web/tests/order-payload.test.ts` | GOOD | NONE | Exercises real buildSingleLegOrderPayload across put/call/stock/null-position branches with expiry normalization checks (web/tests/order-payload.test.ts:139-149). |
| `web/tests/order-place-close-held-option.test.ts` | GOOD | NONE | Imports the real route POST, mocks only the IO boundary (radonFetch/data-reader), and asserts the forwarded payload for a closing SELL (web/tests/order-place-close-held-option.test.ts:66-95). |
| `web/tests/order-place-combo-negative-price.test.ts` | GOOD | NONE | Real POST route exercised; asserts negative combo limitPrice sign is forwarded unchanged (web/tests/order-place-combo-negative-price.test.ts:78-82). |
| `web/tests/order-place-idempotency-route.test.ts` | GOOD | NONE | Covers dedup-by-content, dedup-by-key, distinct-key, and no-cache-on-rejection cases against the real route (web/tests/order-place-idempotency-route.test.ts:96-111) — a real double-click money-duplication bug class. |
| `web/tests/order-place-outside-rth.test.ts` | GOOD | NONE | Verifies auto-enable of outsideRth by market state and that explicit caller value wins, against forwarded FastAPI body (web/tests/order-place-outside-rth.test.ts:69-91). |
| `web/tests/order-place-route-error-propagation.test.ts` | GOOD | NONE | Confirms upstream RadonApiError detail is preserved verbatim through the route rather than re-wrapped (web/tests/order-place-route-error-propagation.test.ts:60-63). |
| `web/tests/order-reliability.test.ts` | IMPROVE | P2 | Bulk of file is solid route/combo/net-price math, but 'OrderTab layout' has three tautological expect(true).toBe(true) tests admitting 'verified in code review' (web/tests/order-reliability.test.ts:790,802,812) that can never fail. |
| `web/tests/order-risk-chokepoint.test.tsx` | GOOD | NONE | Pins coverageStatus states, skeleton rendering, gate pairing, and two real production regressions (WULF covered short, VIX 500x combo scaling) with exact dollar math (web/tests/order-risk-chokepoint.test.tsx:265-266, 301-303). |
| `web/tests/order-risk-linear.test.tsx` | GOOD | NONE | Covers SHORT/LONG futures and stock unbounded/bounded branches plus close-out P&L sign for both SELL-close-LONG and BUY-close-SHORT (web/tests/order-risk-linear.test.tsx:248-269). |
| `web/tests/order-risk-telemetry.test.tsx` | GOOD | NONE | Verifies the sessionStorage trace ring buffer records/trims/clears correctly across coverage states (web/tests/order-risk-telemetry.test.tsx:81-97). |
| `web/tests/order-risk.test.ts` | GOOD | NONE | Exhaustive pinned-arithmetic coverage of computeOrderRisk and augmentOrderLegsWithPortfolioCoverage including GCD normalization and self-cover-first ordering, each with derived math in comments (web/tests/order-risk.test.ts:746-838, 1272-1290). |
| `web/tests/order-tab-close-position-sign.test.ts` | GOOD | NONE | Reproduces the AMD Reverse RR sign-mismatch bug, comparing rendered strip against resolveSpreadPriceData ground truth via label-scoped DOM queries (web/tests/order-tab-close-position-sign.test.ts:197-211, 238-245). |
| `web/tests/order-tab-close-realized-pnl.test.tsx` | GOOD | NONE | Real DOM interactions on OrderTab pin exact realized-P&L math (long call, short put, short stock closes); web/tests/order-tab-close-realized-pnl.test.tsx:282-283 asserts per-contract avg_cost is not double-multiplied. |
| `web/tests/order-tab-combo-sign.test.ts` | GOOD | NONE | Renders real OrderTab combo path and asserts sign-preserved net quote and close-language labels; web/tests/order-tab-combo-sign.test.ts:176-210. |
| `web/tests/order-ticket-spread-notional.test.ts` | GOOD | NONE | Renders InstrumentDetailModal/ModifyOrderModal to static markup and asserts exact resting-limit-adjusted spread math; web/tests/order-ticket-spread-notional.test.ts:109,129. |
| `web/tests/order-unified-components.test.ts` | REFACTOR | P2 | Reimplements the algorithms it means to test as inline local copies (web/tests/order-unified-components.test.ts:47-108,125-148) instead of importing web/lib/order/ — passes even if the real hook/component diverges. |
| `web/tests/orders-bulk-cancel.test.tsx` | GOOD | NONE | Real WorkspaceSections render exercises bulk-select, dialog open, requestCancel call count, and keyboard shortcuts; web/tests/orders-bulk-cancel.test.tsx:176-182. |
| `web/tests/orders-command-strip.test.tsx` | GOOD | NONE | Real render asserts working/partial/fill counts, jump anchors, combo CANCEL ALL routes to dialog not immediate cancel; web/tests/orders-command-strip.test.tsx:306-311. |
| `web/tests/orders-compare.test.ts` | GOOD | NONE | Pure compareOrders() divergence logic exercised for permId/execId drift, field-drift, and asymmetric exec_only_db informational case; web/tests/orders-compare.test.ts:103-113. |
| `web/tests/orders-display.test.ts` | GOOD | NONE | Pure display-helper functions tested directly with real edge cases (partial-fill, distance-to-fill sign convention, CLOSE-vs-OPEN intent); web/tests/orders-display.test.ts:172-216. |
| `web/tests/orders-empty-state.test.tsx` | GOOD | NONE | Renders real MobileOrderList/MobileExecutedList empty states and asserts brand-token/em-dash contract text; web/tests/orders-empty-state.test.tsx:44-54. |
| `web/tests/orders-manage.test.ts` | IMPROVE | P1 | First half calls real cancel/modify route handlers (GOOD); second half regexes scripts/clients/ib_client.py and ib_order_manage.py source text for keywords (web/tests/orders-manage.test.ts:124-186) instead of executing them, so a compliant rename or refactor of clientId/host/port handling breaks the test without a real bug, and a bug that preserves the strings passes it. |
| `web/tests/orders-no-store-header.test.ts` | GOOD | NONE | Exercises real GET/POST route handlers and asserts Cache-Control: no-store per the web/CLAUDE.md cache contract; web/tests/orders-no-store-header.test.ts:36-46. |
| `web/tests/orders-read-from-db.test.ts` | GOOD | NONE | Real readOrdersFromDb tested against mocked DB rows: ET day-cut fill filtering, malformed-payload skipping, last_sync derivation, replica-sync-failure recovery; web/tests/orders-read-from-db.test.ts:101-116,165-182. |
| `web/tests/orders-route.test.ts` | GOOD | NONE | Real GET/POST /api/orders route with Turso-first, 503/502 failure paths and X-Sync-Warning fallback fully exercised; web/tests/orders-route.test.ts:91-141. |
| `web/tests/orders-ux.test.ts` | GOOD | NONE | Pure UX helper functions tested directly: shortcut resolution, selection toggling, combo row flattening, page-size/density parsing; web/tests/orders-ux.test.ts:127-214. |
| `web/tests/output-trace-audit.test.ts` | GOOD | NONE | Builds real temp nft.json manifests and exercises auditOutputTraces() logic for count/byte limits and catastrophic-dir rejection; web/tests/output-trace-audit.test.ts:77-105. |
| `web/tests/panel-mounts.test.tsx` | GOOD | NONE | Real render of DashboardSurface/WorkspaceSections asserts panel mount + nav wiring; uses window-relative fixture date (repo-standard, not fragile); web/tests/panel-mounts.test.tsx:41,103-124. |
| `web/tests/performance-chart-axes.test.ts` | IMPROVE | P2 | Only greps PerformancePanel.tsx source and globals.css for testid strings/CSS rule presence (web/tests/performance-chart-axes.test.ts:8-17); no render, so a broken axis at runtime with the right markers still passes. |
| `web/tests/performance-chart-model.test.ts` | GOOD | NONE | Calls real buildPerformanceChartModel with a full realistic PerformanceData fixture and asserts tick counts, shared domain, path prefixes; web/tests/performance-chart-model.test.ts:81-108. |
| `web/tests/performance-chart-theme.test.ts` | IMPROVE | P2 | Pure CSS regex contract test with no rendering; verifies token names exist in globals.css but not that they resolve visually; web/tests/performance-chart-theme.test.ts:7-21. |
| `web/tests/performance-freshness.test.ts` | GOOD | NONE | Pure timezone/session-boundary functions tested against real UTC-midnight and naive-timestamp edge cases pinning a prior production bug; web/tests/performance-freshness.test.ts:62-77. |
| `web/tests/performance-route.test.ts` | GOOD | NONE | Real GET/POST /api/performance route under fake timers exercises SWR stale-serve + background-rebuild vs cold-start-block paths and 502 on total failure; web/tests/performance-route.test.ts:117-127,252-285. |
| `web/tests/performance-twr.test.ts` | GOOD | NONE | Pure TWR methodology/N-gate functions and isPerformanceBehindPortfolioSync tested with exact math and naive-UTC drift-guard case; web/tests/performance-twr.test.ts:142-150. |
| `web/tests/place-order-body-schema.test.ts` | GOOD | NONE | Pure schema-validation function tested with stock, malformed combo leg, chain-style combo, option CALL/PUT normalization; web/tests/place-order-body-schema.test.ts:29-39,88-93. |
| `web/tests/portfolio-auto-sync.test.ts` | GOOD | NONE | Real GET /api/portfolio route under fake timers verifies RTH-stale warning, weekend-silence, fresh-no-sync, Turso-outage-degrade, and that IB sync is never browser-triggered; web/tests/portfolio-auto-sync.test.ts:70-79,144-163. |
| `web/tests/portfolio-snapshot-freshness.test.ts` | GOOD | NONE | Pure staleness function tested against RTH-window, weekend-carryover, and multi-day-silence cases with fixed reference instants; web/tests/portfolio-snapshot-freshness.test.ts:18-37. |
| `web/tests/portfolio-trade-log-dates.test.ts` | GOOD | NONE | Real GET /api/portfolio route verifies trade_log_dates derives only from journal (never trade_log.json fallback) and the opt_right SQL-alias remap bug guard; web/tests/portfolio-trade-log-dates.test.ts:114-131. |
| `web/tests/position-pnl-pct-entry-margin.test.ts` | GOOD | NONE | Exhaustive verified-capital denominator matrix incl. rejecting bare projection, legacy metadata, complex profiles (web/tests/position-pnl-pct-entry-margin.test.ts:71,168,275) |
| `web/tests/position-pnl-pct-structures-catalog.test.ts` | GOOD | NONE | Drives real docs/options-structures.json catalog (58 structures) through capital-resolution invariants per risk profile (web/tests/position-pnl-pct-structures-catalog.test.ts:44,230-236) |
| `web/tests/position-pnl-sign.test.ts` | REFACTOR | P2 | Tests two locally-defined format functions (fmtPnlBuggy/fmtPnlFixed), never imports real display code — verifies nothing about production behavior (web/tests/position-pnl-sign.test.ts:17-24) |
| `web/tests/position-return-surface-parity.test.tsx` | GOOD | NONE | Cross-checks positionUtils/unrealizedBreakdown/PositionTable/MobilePositionList/PositionTab all agree on the same return% via role/text queries (web/tests/position-return-surface-parity.test.tsx:53-77) |
| `web/tests/position-tab-book-focus.test.tsx` | GOOD | NONE | Exercises real focus/toggle state via testids and hook capture, covers leg-switch and ticker-change reset (web/tests/position-tab-book-focus.test.tsx:57,79-88) |
| `web/tests/position-tab-leg-sign.test.ts` | GOOD | NONE | Verifies combo mark uses live quotes not stale legs, and sign/class of long vs short legs via rendered DOM (web/tests/position-tab-leg-sign.test.ts:108,129-144) |
| `web/tests/position-tab-trade.test.tsx` | GOOD | NONE | Covers combo/leg close CTAs, closing-action defaults, credit-limit sign validation, cancel flow via role/testid queries (web/tests/position-tab-trade.test.tsx:64,93,107) |
| `web/tests/position-table-column-toggle.test.tsx` | GOOD | NONE | Covers column defaults, header/cell presence toggling, and P&L%/dollar cell separation via querying real thead/tr text (web/tests/position-table-column-toggle.test.tsx:125-296) |
| `web/tests/position-table-covered-call-pnl.test.tsx` | GOOD | NONE | Real stock-share math regression for covered calls verified against explicit dollar strings, incl. rejecting the prior wrong +$3,332,443 bug (web/tests/position-table-covered-call-pnl.test.tsx:128-144) |
| `web/tests/position-table-credit-combo-sign.test.tsx` | GOOD | NONE | Pins the EWY credit-combo sign regression for Avg Entry/Initial Value while confirming P&L stays unaffected (web/tests/position-table-credit-combo-sign.test.tsx:59-80) |
| `web/tests/position-table-implied.test.tsx` | GOOD | NONE | Cross-checks rendered Implied/Implied MV cells against real bsPut() output, covers missing-IV and stock-only dash states (web/tests/position-table-implied.test.tsx:135-149,173-188) |
| `web/tests/position-table-initial-value.test.tsx` | GOOD | NONE | Comprehensive sign-scoping matrix (stock/option/combo/short-stock) for Initial Value plus per-leg LegRow flow-through (web/tests/position-table-initial-value.test.tsx:274-380) |
| `web/tests/position-table-leg-row-runtime.test.ts` | GOOD | NONE | Regression test for a leg-row rtLast reference crash, asserts real rendered market values after expand click (web/tests/position-table-leg-row-runtime.test.ts:114-126) |
| `web/tests/position-table-ratio-risk-reversal.test.tsx` | GOOD | NONE | Pins raw ratio-label display and near-zero avg-entry drift fix with explicit dollar assertions (web/tests/position-table-ratio-risk-reversal.test.tsx:101-116) |
| `web/tests/position-table-short-stock-avg-entry.test.tsx` | GOOD | NONE | Both pure getAvgEntry and rendered-cell coverage for short-stock (positive), short-option (negative), long variants (web/tests/position-table-short-stock-avg-entry.test.tsx:152-218) |
| `web/tests/position-today-pnl-blended.test.ts` | IMPROVE | P1 | resolveTodayPnl and computeOptionsRt are reimplemented copies inline rather than imported from source, so a real fix diverging from the copy would pass tests but not fix prod (web/tests/position-today-pnl-blended.test.ts:46-91) |
| `web/tests/position-today-pnl.test.ts` | IMPROVE | P1 | Same pattern as blended test: computeOptionsRt is an inline replica of PositionTable.tsx logic, not an import (web/tests/position-today-pnl.test.ts:34-65) |
| `web/tests/position-trade.test.ts` | GOOD | NONE | Imports real buildPositionTradeOrder/closingActionFor, verifies combo leg-action structure, closeOut basis sign, and opening-vs-closing routing with exact numbers (web/tests/position-trade.test.ts:62-154) |
| `web/tests/previous-close-yahoo-daily-array.test.ts` | GOOD | P1 | Real route import, mocked fetch fixtures pin the exact previous-close-array bug + fallback + null-cases with clear evidence (web/tests/previous-close-yahoo-daily-array.test.ts:90-124,198-230) |
| `web/tests/price-bar-quote-telemetry.test.ts` | GOOD | NONE | Real component rendered to static markup, asserts label ordering and MARK-vs-LAST + spread formatting from actual output (web/tests/price-bar-quote-telemetry.test.ts:39-87) |
| `web/tests/price-chart-shell.test.ts` | IMPROVE | P2 | usePriceHistory + Liveline are fully mocked so the assertions only confirm PriceChart forwards a theme string, not that the shell derives correct data (web/tests/price-chart-shell.test.ts:6-38) |
| `web/tests/price-chart-spread.test.tsx` | GOOD | NONE | Real PriceChart rendered, only usePriceHistory/liveline/color-resolver mocked as boundaries; asserts NET CREDIT/DEBIT labeling, signed formatting, neutral color, no reference line (web/tests/price-chart-spread.test.tsx:96-173) |
| `web/tests/price-chart-theme.test.ts` | REFACTOR | P2 | Regexes over PriceChart.tsx source text instead of rendering it — a behavior-preserving refactor (renaming var, restructuring JSX) breaks it even though price-chart-shell.test.ts already covers the same behavior via real render (web/tests/price-chart-theme.test.ts:20-49) |
| `web/tests/price-chart.test.ts` | GOOD | NONE | Pure deterministic tests of mockPriceGenerator seeded RNG, bounds, and monotonic time ordering (web/tests/price-chart.test.ts:31-83) |
| `web/tests/prices.test.ts` | GOOD | NONE | Real imports for symbol normalization, price formatting, and resolveRealtimePrice mid/last resolution with concrete numeric assertions (web/tests/prices.test.ts:76-104) |
| `web/tests/pricesProtocol.test.ts` | GOOD | NONE | Exhaustive real-function coverage of optionKey/contractsKey/normalization/portfolioLegToContract edge cases (null strike, malformed expiry, dedup) (web/tests/pricesProtocol.test.ts:18-291) |
| `web/tests/probe-freshness-route.test.ts` | GOOD | NONE | Mocks only the DB boundary (web/tests/probe-freshness-route.test.ts:12-13), asserts real degrade paths incl. bounded-read timeout at :179-195. |
| `web/tests/probe-freshness.test.ts` | GOOD | NONE | Pure evaluation-matrix tests against real exported functions; e.g. RTH-only applicability pinned at web/tests/probe-freshness.test.ts:67-70. |
| `web/tests/profile-bookmarks-watchlist-api.test.ts` | GOOD | NONE | Real in-memory libsql schema (web/tests/profile-bookmarks-watchlist-api.test.ts:31-40) exercises actual SQL; only Clerk auth() mocked at :17-19. |
| `web/tests/quote-telemetry-fallback.test.ts` | GOOD | NONE | Behavioral fallback-precedence assertions on real buildQuoteTelemetryModel, e.g. web/tests/quote-telemetry-fallback.test.ts:51-61 hollow-tick case. |
| `web/tests/quote-telemetry-wrappers.test.ts` | GOOD | NONE | Renders real components via renderToStaticMarkup and asserts computed spread text, e.g. web/tests/quote-telemetry-wrappers.test.ts:61-63. |
| `web/tests/radon-api-service-token.test.ts` | GOOD | NONE | Small focused test of the demo service-token header contract; only global fetch stubbed, web/tests/radon-api-service-token.test.ts:32-33. |
| `web/tests/radon-api.test.ts` | GOOD | NONE | Comprehensive radonFetch coverage incl. structured-error unwrap regression at web/tests/radon-api.test.ts:174-195 and timeout/abort at :240-259. |
| `web/tests/rate-limit.test.ts` | GOOD | NONE | Deterministic via mocked Date.now (web/tests/rate-limit.test.ts:12), pins un-spoofable XFF selection (CWE-348) at :117-126. |
| `web/tests/rate-limiter.test.ts` | GOOD | NONE | Real RateLimiter class under vi.useFakeTimers(), asserts FIFO/throttling/reject paths, e.g. web/tests/rate-limiter.test.ts:45-57. |
| `web/tests/realized-pnl-date-filter.test.ts` | GOOD | NONE | Pinned system time, exercises DST/UTC-naive edge cases against real computeRealizedPnlFromFills, e.g. web/tests/realized-pnl-date-filter.test.ts:86-96. |
| `web/tests/realized-pnl.test.ts` | GOOD | NONE | Window-relative 'today' fixture (repo-accepted), sums real function output, web/tests/realized-pnl.test.ts:15-17,45-47. |
| `web/tests/realtime-socket-auth.test.ts` | GOOD | NONE | Stubs only window.location + getWsTicket boundary; asserts real resolveRealtimeWebSocketUrl/buildAuthenticatedWebSocketUrl incl. unauth reject at web/tests/realtime-socket-auth.test.ts:69-76. |
| `web/tests/reconnect-strategy.test.ts` | GOOD | NONE | Deterministic via mocked Math.random, exercises real backoff/cap/reset math, web/tests/reconnect-strategy.test.ts:26-40. |
| `web/tests/regime-close-transition-retry.test.ts` | IMPROVE | P2 | 3 real pure-logic tests are solid, but final block string-greps hook source instead of behavior, web/tests/regime-close-transition-retry.test.ts:83-89. |
| `web/tests/regime-cor1m-live.test.ts` | IMPROVE | P2 | Only 1 real behavioral assertion (web/tests/regime-cor1m-live.test.ts:19-28); remaining 4 tests are panelSource/helperSource string/regex matches at :34-50. |
| `web/tests/regime-corrupt-cache.test.ts` | REFACTOR | P1 | readLatestCriBuggy/readLatestCriFixed (web/tests/regime-corrupt-cache.test.ts:27-78) and isMarketOpenNow (:169-176) are inline 'replicas', never import app/api/regime/route.ts. |
| `web/tests/regime-cri-staleness.test.ts` | GOOD | NONE | Pure isCriDataStale matrix on real import, market-hours-aware cases pinned, web/tests/regime-cri-staleness.test.ts:33-48. |
| `web/tests/regime-day-change.test.ts` | REFACTOR | P2 | Reimplements computeDayChange/computePointChange inline as 'Replica' instead of importing RegimePanel's real logic, web/tests/regime-day-change.test.ts:10-16,66-69; a real bug in the component ships untested. |
| `web/tests/regime-detail-panels-responsive.test.ts` | IMPROVE | P2 | Entire file is string-contains checks against panel/CSS source, no rendered layout exercised, web/tests/regime-detail-panels-responsive.test.ts:13-23. |
| `web/tests/regime-history-backfill.test.ts` | GOOD | NONE | Real backfillRealizedVolHistory exercised against synthetic history array, web/tests/regime-history-backfill.test.ts:17-36. |
| `web/tests/regime-history-responsive.test.ts` | IMPROVE | P2 | Entire file is regex/string matches on panel + CSS source text, no render, web/tests/regime-history-responsive.test.ts:13-22. |
| `web/tests/regime-history-tooltip.test.ts` | IMPROVE | P2 | First describe is pure source-index grepping (web/tests/regime-history-tooltip.test.ts:13-22); second describe validates real SECTION_TOOLTIPS content, which is fine. |
| `web/tests/regime-live-strip.test.ts` | GOOD | NONE | Pure behavioral tests of resolveRegimeStripLiveState market-open gating, web/tests/regime-live-strip.test.ts:5-35. |
| `web/tests/regime-llm-card.test.tsx` | GOOD | NONE | RTL render/waitFor on real component; empty/error/data states covered incl. brand-token hex sweep at web/tests/regime-llm-card.test.tsx:126-133. |
| `web/tests/regime-market-closed-values.test.ts` | IMPROVE | P2 | Real resolveRegimeStripLiveState coverage at web/tests/regime-market-closed-values.test.ts:56-95 is good, but two describe blocks (29-50, 98-120) are pure panelSource/helperSource string/regex checks. |
| `web/tests/regime-market-closed.test.ts` | IMPROVE | P2 | Entire file greps RegimePanel.tsx source text/regex for identifiers, never renders or calls real logic, web/tests/regime-market-closed.test.ts:20-83. |
| `web/tests/regime-relationship-axis.test.ts` | GOOD | NONE | Pure function tests for tick density/index math; deterministic, real assertions on output shape. web/tests/regime-relationship-axis.test.ts:9 |
| `web/tests/regime-relationship-model.test.ts` | GOOD | NONE | Tests quadrant/spread/z-score computation incl. a real production repro (full-sample vs 20-session window). web/tests/regime-relationship-model.test.ts:60-85 |
| `web/tests/regime-relationship-tooltips.test.ts` | REFACTOR | P2 | Asserts literal source lines of the component (exact JSX expressions), pinning implementation not behavior. web/tests/regime-relationship-tooltips.test.ts:52-54 |
| `web/tests/regime-relationship-zoom.test.tsx` | GOOD | NONE | Real render + fireEvent pointer/click interactions on brush and preset chips, testid-based, verifies bar counts change. web/tests/regime-relationship-zoom.test.tsx:100-111 |
| `web/tests/regime-relationship.test.ts` | GOOD | NONE | Pure function tests for spread/z-score/quadrant summarization with concrete expected numeric results. web/tests/regime-relationship.test.ts:57-66 |
| `web/tests/regime-route-cache-selection.test.ts` | GOOD | NONE | Exercises real route logic incl. a hung DB promise never resolving, verifying bounded fallback within 6s. web/tests/regime-route-cache-selection.test.ts:105-131 |
| `web/tests/regime-share.test.ts` | IMPROVE | P2 | Every assertion greps source files for substrings (route wiring, function names) instead of exercising behavior. web/tests/regime-share.test.ts:38-48 |
| `web/tests/regime-spy-subscription.test.ts` | IMPROVE | P2 | Source-inspection only (parses WorkspaceShell.tsx text for literals) rather than rendering and asserting the actual subscribed symbol set. web/tests/regime-spy-subscription.test.ts:16-24 |
| `web/tests/regime-strip-responsive.test.ts` | REFACTOR | P2 | Pins exact CSS selector/value text (nth-child chains, literal grid-template-columns strings) instead of testing rendered layout. web/tests/regime-strip-responsive.test.ts:23-32 |
| `web/tests/regime-sync-config.test.ts` | REFACTOR | P2 | 16-line test asserts literal source text of the polling config object rather than observed polling behavior. web/tests/regime-sync-config.test.ts:12-15 |
| `web/tests/regime-tab-routes.test.tsx` | GOOD | NONE | Renders RegimePanel with mocked router/pathname, verifies correct panel mounts and correct push() targets per tab click via role queries. web/tests/regime-tab-routes.test.tsx:168-172 |
| `web/tests/risk-reversal-chart.test.ts` | GOOD | NONE | Pure function branch coverage for last/mid price fallback including all null-combination edge cases. web/tests/risk-reversal-chart.test.ts:83-95 |
| `web/tests/robots-noindex.test.ts` | GOOD | P1 | Calls real robots(), isPublicRoute(), and next.config headers() and asserts actual returned values, not source text. web/tests/robots-noindex.test.ts:27-42 |
| `web/tests/route-cache-meta.test.ts` | GOOD | P1 | Invokes real GET handlers with mocked fs boundary, verifies computed age_seconds/is_stale thresholds precisely. web/tests/route-cache-meta.test.ts:109-123 |
| `web/tests/routes-db-unavailable-503.test.ts` | GOOD | P0 | Verifies every user-data route degrades to 503 (not 500) on a realistic Turso destroy-storm error, and that resetDb is called. web/tests/routes-db-unavailable-503.test.ts:57-63 |
| `web/tests/rv-ratio-hook.test.tsx` | GOOD | P1 | renderHook + real fetch mock covers stale-then-fresh swap, single in-flight POST guard, abort-on-unmount, sanitized errors. web/tests/rv-ratio-hook.test.tsx:126-144 |
| `web/tests/rv-ratio-panel.test.tsx` | GOOD | NONE | Behavior-driven render tests via testid/role queries covering regime badges, stat tiles, loading/error/empty states, brush interaction. web/tests/rv-ratio-panel.test.tsx:71-88 |
| `web/tests/rv-ratio-route.test.ts` | GOOD | P1 | Uses a real in-memory libsql client (not a mock of the module under test) plus session-relative staleness cases incl. holiday-shortened weeks. web/tests/rv-ratio-route.test.ts:193-207 |
| `web/tests/rv-ratio-transform.test.ts` | GOOD | P1 | Payload-guard accept/reject matrix and session-relative currency checks with explicit deterministic clock instants. web/tests/rv-ratio-transform.test.ts:147-172 |
| `web/tests/same-day-pnl.test.ts` | GOOD | P0 | Regression suite for same-day P&L incl. real production bug repros (AMD RR, ET-date producer fix) with an explicit invariant check. web/tests/same-day-pnl.test.ts:186-244 |
| `web/tests/scan-ticker-list.test.ts` | GOOD | NONE | Pure validator tests covering dedupe, cap, pair-parity, malformed symbol rejection. web/tests/scan-ticker-list.test.ts:50-59 |
| `web/tests/scanner-discover-route.test.ts` | IMPROVE | P2 | 21-line source-string check for the redirect literal, never invokes the page/redirect at runtime. web/tests/scanner-discover-route.test.ts:11-13 |
| `web/tests/scanner-header-tooltips.test.tsx` | GOOD | NONE | Renders WorkspaceSections with boundary-faked data hooks, verifies hover-triggered tooltip content and discover-mode tab switch by role/testid. web/tests/scanner-header-tooltips.test.tsx:133-153 |
| `web/tests/scanner-mode-tabs.test.tsx` | GOOD | NONE | Role/testid-based component tests with real fireEvent click, clamped meter value assertions. web/tests/scanner-mode-tabs.test.tsx:22-26 |
| `web/tests/scanner-no-store-header.test.ts` | GOOD | P1 | Exercises real GET/POST route handlers and checks actual Cache-Control response header across disk-fallback and empty-fallback branches. web/tests/scanner-no-store-header.test.ts:58-73 |
| `web/tests/section-empty-state.test.tsx` | GOOD | NONE | Role/testid-based render tests incl. accessibility attrs (role=alert, aria-selected equivalents), no raw hex/em-dash regressions. web/tests/section-empty-state.test.tsx:99-111 |
| `web/tests/section-tile-grid-inset.test.ts` | IMPROVE | P2 | Regex-matches literal CSS property values (web/tests/section-tile-grid-inset.test.ts:28,42) instead of rendered output; a behavior-preserving CSS refactor breaks it. |
| `web/tests/service-health-banner.test.tsx` | GOOD | NONE | Deterministic mock of useServiceHealth (web/tests/service-health-banner.test.tsx:30-54) drives real banner render/humanize behavior including JSON-leak and dormant-chip regressions. |
| `web/tests/service-health-error-humanize.test.ts` | GOOD | NONE | vi.useFakeTimers + fixed FIXED_NOW (web/tests/service-health-error-humanize.test.ts:32-37) makes retry-window assertions deterministic; covers every known error shape plus fallback. |
| `web/tests/service-health-error.test.ts` | GOOD | NONE | Pure formatter tests with explicit precedence and truncation assertions (web/tests/service-health-error.test.ts:56-64,92-104), no timing or network dependence. |
| `web/tests/service-health-replica-retirement.test.ts` | GOOD | NONE | Mocks fs.existsSync and db.execute per repo convention (web/tests/service-health-replica-retirement.test.ts:12-28) to assert retired-replica rows are excluded from every consuming route. |
| `web/tests/service-health-route.test.ts` | GOOD | NONE | Route-level tests cover ok/error/stale/dormant classification and DB-down graceful degrade (web/tests/service-health-route.test.ts:117-133) with a real 200-status assertion. |
| `web/tests/service-health-staleness.test.ts` | GOOD | NONE | Window-relative Date.now() offsets (web/tests/service-health-staleness.test.ts:51,120-137) exercise real market-aware coercion paths per repo's sanctioned pattern. |
| `web/tests/service-health-transport-ui.test.tsx` | GOOD | NONE | Stubs a rejecting fetch to prove the footer/banner surface a hard failure instead of stale-cached nominal data (web/tests/service-health-transport-ui.test.tsx:32-40,47). |
| `web/tests/service-health-windows.test.ts` | GOOD | NONE | Fixed Date.parse timestamps injected as `now` params (web/tests/service-health-windows.test.ts:129-143) pin named weekend/holiday regressions without real-clock coupling. |
| `web/tests/share-pnl-entry-exit.test.ts` | GOOD | NONE | Calls the real exported positionGroupShareData (web/tests/share-pnl-entry-exit.test.ts:49,129) with fixed ISO fixtures covering entry/exit fallback chains and the post-exit-date guard. |
| `web/tests/share-pnl-position-group.test.ts` | GOOD | NONE | Mutation-derived arithmetic pinned inline (web/tests/share-pnl-position-group.test.ts:407-416,481-494) against the real positionGroupShareData/closedGroupReturnPct; kills documented survivor mutants. |
| `web/tests/share-pnl.test.ts` | REFACTOR | P2 | groupExecutedOrders/execOrderDescription/execOrderShareData/blotterShareData are LOCAL reimplementations (web/tests/share-pnl.test.ts:60-261,771-1011), not the app's real functions — tests verify a copy, not the code. |
| `web/tests/share-report-path.test.ts` | GOOD | NONE | Security-surface path-traversal and cross-type-card tests against the real isAllowedShareCardPath (web/tests/share-report-path.test.ts:13-31) protecting the unauthenticated share route. |
| `web/tests/short-availability-chip.test.tsx` | GOOD | NONE | Renders the real LocateFeeChip with a live AAPL production repro fixture (web/tests/short-availability-chip.test.tsx:161-179) and asserts token colors + status via testids. |
| `web/tests/short-availability.test.ts` | IMPROVE | P1 | Route tests are solid, but the final 'Locate chip enablement logic' block (web/tests/short-availability.test.ts:216-263) only asserts inline re-derived booleans, never calling the app's resolveLocateChipEnabled. |
| `web/tests/sidebar-navigation.test.ts` | GOOD | NONE | Single focused role-based render test (web/tests/sidebar-navigation.test.ts:25-29) confirms hidden nav items are actually absent while visible ones render. |
| `web/tests/signout-cache-purge.test.tsx` | GOOD | NONE | Asserts Clerk hook never invoked in authless mode via a throwing mock, web/tests/signout-cache-purge.test.tsx:20-26; deterministic, real regression guard. |
| `web/tests/skew-api.test.ts` | GOOD | NONE | In-memory libsql seeds Turso-first/disk-fallback/missing-shape/service-isolation contract, web/tests/skew-api.test.ts:96-147; strong assertions on exact envelope. |
| `web/tests/skew-panel.test.tsx` | GOOD | NONE | Pure helper math plus gated component states with fixed timestamps, web/tests/skew-panel.test.tsx:68-88; NaN-path guard at 290-296 catches real chart regressions. |
| `web/tests/skill-stack-shell-chrome.test.ts` | REFACTOR | P2 | Grep-asserts raw source text/identifier names (e.g. web/tests/skill-stack-shell-chrome.test.ts:35-36 `toContain("sectionNeedsPrices")`) instead of rendering/behavior; a behavior-preserving rename breaks it. Replace with rendered DOM/computed-style + a11y-role assertions. |
| `web/tests/sort-th-tooltip.test.tsx` | GOOD | NONE | Real DOM events confirm the help bubble doesn't toggle sort while the header click does, web/tests/sort-th-tooltip.test.tsx:40-50. |
| `web/tests/spectral-bars.test.tsx` | GOOD | NONE | Renders and counts sign-toned bars plus enforces the no-raw-hex brand rule, web/tests/spectral-bars.test.tsx:22-25,46-58. |
| `web/tests/spread-price-bar.test.ts` | GOOD | NONE | Verified net-bid/ask/last combo math with worked comments and a stale-vs-guarded-mark regression, web/tests/spread-price-bar.test.ts:76-83,116-127. |
| `web/tests/stale-frozen-last.test.ts` | GOOD | NONE | TDD regression for a real AAOI stale-LAST bug against the real ib_tick_handler.js, web/tests/stale-frozen-last.test.ts:22-52; covers stock-exempt and re-genuine-trade paths. |
| `web/tests/stale-option-last.test.ts` | GOOD | NONE | Pure resolveRealtimePrice cases cover stale-below/above/wide-spread and close-fallback ordering with real math, web/tests/stale-option-last.test.ts:17-30,110-120. |
| `web/tests/stale-option-quote-guard.test.ts` | GOOD | NONE | Extracts safeInitialState from real ib_realtime_server.js source and tests the 8h staleness boundary with a documented anti-flake buffer, web/tests/stale-option-quote-guard.test.ts:20-22,117-128. |
| `web/tests/straddle-api.test.ts` | GOOD | NONE | Same solid Turso-first/disk-fallback/missing-shape contract as skew-api, web/tests/straddle-api.test.ts:97-137. |
| `web/tests/straddle-panel.test.tsx` | GOOD | NONE | Pure ratio/live-ratio math verified against Python-derived constants plus panel gating and live-cell fallback priority, web/tests/straddle-panel.test.tsx:253-268,290-312. |
| `web/tests/strength-confirmation-route.test.ts` | GOOD | NONE | Covers Turso-fresher-than-disk split-brain, stale-cache-on-upstream-down, and dual-failure 502, web/tests/strength-confirmation-route.test.ts:111-137,195-229. |
| `web/tests/strength-confirmation-scanner.test.tsx` | GOOD | NONE | Exercises desktop/mobile render, ticker-search validation, tooltip content, and empty state via real interaction, web/tests/strength-confirmation-scanner.test.tsx:102-141. |
| `web/tests/sw-decisions.test.ts` | GOOD | NONE | Pure decision-table coverage for request classification, cache eligibility, and eviction ordering, web/tests/sw-decisions.test.ts:44-90,131-147. |
| `web/tests/sw-smoke.test.ts` | GOOD | NONE | Real sw.js evaluated in a vm sandbox with mocked network-only boundary; asserts exact-Response identity online and byte-identical offline replay, web/tests/sw-smoke.test.ts:166-185,286-298. |
| `web/tests/sync-fallback.test.ts` | GOOD | NONE | Portfolio/orders 502-only-when-no-snapshot contract with mocked db execute by table, web/tests/sync-fallback.test.ts:26-39,102-110,206-214. |
| `web/tests/sync-hooks.test.ts` | IMPROVE | P2 | Type-shape block is tautological (constructs a literal then asserts its own field), web/tests/sync-hooks.test.ts:120-131, and mockSpawn (line 22) is declared but never asserted, so the claimed POST/spawn coverage in the header comment is untested. |
| `web/tests/sync-mutex.test.ts` | IMPROVE | P2 | Mutex coalescing/error-reset logic is well tested (web/tests/sync-mutex.test.ts:21-76), but the 'Route-level integration' block only greps route source for a substring instead of exercising behavior, e.g. web/tests/sync-mutex.test.ts:111-113. |
| `web/tests/table-filter.test.ts` | GOOD | NONE | Renders the real hook and asserts filtering/clear behavior against concrete fixtures, web/tests/table-filter.test.ts:23-48. |
| `web/tests/theme-provider-hydration.test.tsx` | GOOD | NONE | Regression test for a real React #418 hydration mismatch, asserting first-render SSR parity then post-mount update, web/tests/theme-provider-hydration.test.tsx:76-90,105-122. |
| `web/tests/theta-earnings-backfill.test.ts` | GOOD | NONE | Pure backfill logic including fail-open on fetch error and skip-when-already-annotated, web/tests/theta-earnings-backfill.test.ts:98-111. |
| `web/tests/theta-harvester-route.test.ts` | GOOD | NONE | Turso-vs-disk freshness split-brain plus earnings-backfill-only-when-missing-key contract, web/tests/theta-harvester-route.test.ts:124-150,207-221. |
| `web/tests/theta-harvester-scanner.test.tsx` | GOOD | NONE | Extensive real-interaction coverage: keyboard nav excluded while typing, filter chips, selection bar, earnings chip classes, criteria pips, web/tests/theta-harvester-scanner.test.tsx:440-463,297-352,407-423. |
| `web/tests/ticker-chain-position-focus.test.tsx` | GOOD | NONE | Full component render with real fetch mock asserting the deep-linked expiry wins over the default and the exact chain URL fetched, web/tests/ticker-chain-position-focus.test.tsx:210-220. |
| `web/tests/ticker-detail-spread-notional.test.ts` | GOOD | NONE | SSR-rendered markup pins spread dollars/percent and explicitly forbids the old bps regression, web/tests/ticker-detail-spread-notional.test.ts:62-66. |
| `web/tests/ticker-info-cache.test.ts` | GOOD | NONE | Pure function tests on real exports covering empty/populated/TTL-expired paths; deterministic (web/tests/ticker-info-cache.test.ts:14-33). |
| `web/tests/ticker-info-float.test.ts` | GOOD | NONE | Route test with fs/fetch boundary mocks; covers miss, 500-degradation, legacy self-heal, and cache-hit skip (web/tests/ticker-info-float.test.ts:141-189). |
| `web/tests/ticker-nav.test.ts` | IMPROVE | P2 | RESERVED/TICKER_RE/tab-url logic are hardcoded copies mirrored from [ticker]/page.tsx, not imported (web/tests/ticker-nav.test.ts:7-15) so a real source drift is invisible. |
| `web/tests/ticker-search-disconnected.test.ts` | GOOD | NONE | Renders real TickerSearch against a mocked WebSocket boundary; asserts onSearchUnavailable for disconnected/unreachable cases (web/tests/ticker-search-disconnected.test.ts:57-113). |
| `web/tests/ticker-search-filter.test.ts` | GOOD | NONE | Renders real TickerSearch, verifies STK/IND/FUT pass and WAR/BOND are filtered via rendered DOM text (web/tests/ticker-search-filter.test.ts:124-147). |
| `web/tests/ticker-search.test.ts` | REFACTOR | P2 | Never renders TickerSearch; reimplements filter/debounce/keyboard-nav logic inline and asserts against itself (web/tests/ticker-search.test.ts:78-86,100-119,121-157) — zero coverage of real component. |
| `web/tests/typography-foundation.test.ts` | GOOD | NONE | Regex-extracts real CSS declaration blocks from globals.css and asserts token/rule presence; deterministic, low-stakes contract test (web/tests/typography-foundation.test.ts:14-29). |
| `web/tests/typography-readability-contract.test.ts` | GOOD | NONE | Same CSS-block-regex contract pattern applied to prose/heading/table rules against the real globals.css (web/tests/typography-readability-contract.test.ts:15-40). |
| `web/tests/typography-semantics-contract.test.ts` | FRAGILE | P2 | Asserts exact literal JSX substrings against source files (web/tests/typography-semantics-contract.test.ts:13,25,29-33); any Prettier/attribute-order reformat breaks it with no behavior change. |
| `web/tests/unrealized-breakdown-signed.test.ts` | GOOD | NONE | Realistic multi-leg portfolios (combo, credit RR, stock) verify signed entry/MV and P&L=MV-entry invariant with parsed round-trip checks (web/tests/unrealized-breakdown-signed.test.ts:99-114,164-171). |
| `web/tests/use-blotter-refresh.test.ts` | GOOD | NONE | Real useBlotter hook against a fetch mock branching GET/POST; covers success promotion and error-with-cached-fallback (web/tests/use-blotter-refresh.test.ts:44-93). |
| `web/tests/use-catalysts-refresh.test.tsx` | GOOD | NONE | Real useCatalysts hook exercised through a visibilitychange event with sequential fetch mocks (web/tests/use-catalysts-refresh.test.tsx:13-38). |
| `web/tests/use-dashboard-section-visibility.test.ts` | GOOD | NONE | Real hook against real/synthetic localStorage covering default, hide, rehydrate, legacy-all-hidden reset, last-visible guard, malformed values (web/tests/use-dashboard-section-visibility.test.ts:41-118). |
| `web/tests/use-gex.test.ts` | GOOD | NONE | Pure function under a pinned fake system time, asserting stale-vs-fresh retry logic deterministically (web/tests/use-gex.test.ts:6-20). |
| `web/tests/use-journal-extract-timestamp.test.ts` | GOOD | NONE | Exercises real exported __TEST_CONFIG__.extractTimestamp with a trades array, verifying max filled_at and null-on-empty (web/tests/use-journal-extract-timestamp.test.ts:74-91); source is exported so the runtime path is not a skipped fallback. |
| `web/tests/use-llm-token-index.test.ts` | GOOD | NONE | Route + hook tests cover success, param forwarding, defaulting, upstream failure (502), HTTP error surfacing, and empty-payload-as-data (web/tests/use-llm-token-index.test.ts:27-79,124-155). |
| `web/tests/use-markov-state.test.ts` | GOOD | NONE | Pure computeMarkovState tests cover empty/short history, band classification, transition counting, and row-stochastic matrix invariant (web/tests/use-markov-state.test.ts:20-75). |
| `web/tests/use-offline-status.test.tsx` | GOOD | NONE | Real OfflineStatusProvider reducer driven by fake timers for the 2s debounce and 45s window; covers transient blips and instant-clear-on-success (web/tests/use-offline-status.test.tsx:41-123). |
| `web/tests/use-portfolio-inactive-load.test.ts` | GOOD | NONE | Real usePortfolio hook against a fetch mock; verifies single cached read when inactive and transition-triggered second fetch (web/tests/use-portfolio-inactive-load.test.ts:49-100). |
| `web/tests/use-portfolio-sync.test.ts` | REFACTOR | P1 | Explicitly tests inline reimplemented staleness/backoff arithmetic instead of usePortfolio itself (web/tests/use-portfolio-sync.test.ts:9-42) — no import from the hook, zero regression coverage on real sync/backoff logic. |
| `web/tests/use-previous-close-indexes.test.ts` | GOOD | NONE | Real shouldBackfillPreviousClose tested across regime-index exclusions, normal backfill, and null/zero/option-symbol exclusions (web/tests/use-previous-close-indexes.test.ts:35-53). |
| `web/tests/use-prices-ws-stability.test.ts` | GOOD | NONE | Extensive real usePrices state-machine coverage (subscription diff, idempotent connect, auth ticketing, deadlines, stale-socket isolation, backoff, lifecycle, message hardening) via deterministic fake timers and mock WS (web/tests/use-prices-ws-stability.test.ts:88-576). |
| `web/tests/use-regime.test.ts` | GOOD | NONE | Pure needsCurrentEtSessionRetry under pinned fake system time, covering stale/fresh intraday and prior-session cases (web/tests/use-regime.test.ts:14-42). |
| `web/tests/use-service-health-error.test.tsx` | GOOD | NONE | Real useServiceHealth hook against a 503 fetch mock, verifying error propagation and loading completion (web/tests/use-service-health-error.test.tsx:14-22). |
| `web/tests/use-skew-realtime.test.tsx` | GOOD | NONE | Mocks the shared useSyncHook dependency (not the module under test) and asserts useSkew wires correct interval/endpoint for open vs closed market (web/tests/use-skew-realtime.test.tsx:25-39). |
| `web/tests/use-sync-hook-inactive-load.test.ts` | GOOD | NONE | Real useSyncHook against fetch mocks covers inactive cached read, active transition POST sync, and loadWhenInactive=false gating (web/tests/use-sync-hook-inactive-load.test.ts:28-111). |
| `web/tests/use-viewport.test.ts` | GOOD | NONE | classifyViewport boundaries + resize/orientationchange re-classification exercised with real DOM events; web/tests/use-viewport.test.ts:81-91 |
| `web/tests/utils-extended.test.ts` | GOOD | NONE | Pure formatter functions covered with real edge cases (nulls, nested objects, empty arrays); web/tests/utils-extended.test.ts:184-191 |
| `web/tests/utils.test.ts` | GOOD | P2 | Solid pure-function coverage but overlaps utils-extended.test.ts on several formatters (formatPortfolioPayload, formatPiPayload); web/tests/utils.test.ts:83-115 |
| `web/tests/vcg-history-chart.test.tsx` | GOOD | NONE | Tests real user-visible behavior via testids/roles, drag interaction, bar counts; web/tests/vcg-history-chart.test.tsx:202-229 |
| `web/tests/vcg-panel-badge.test.tsx` | IMPROVE | P2 | Single narrow assertion on style string content('var(--warning)'); doesn't verify other severity tiers render different tokens; web/tests/vcg-panel-badge.test.tsx:65 |
| `web/tests/vcg-route-freshness.test.ts` | GOOD | P1 | Regression test for DUR-01 dual-source staleness bug with fake timers pinned via vi.setSystemTime, not real wall clock; web/tests/vcg-route-freshness.test.ts:68-69 |
| `web/tests/vercel-ignore-build.test.ts` | GOOD | NONE | Pure functions tested via dependency injection (execDiffQuiet, resolveShas callbacks), deterministic; web/tests/vercel-ignore-build.test.ts:62-79 |
| `web/tests/watchlist-content.test.tsx` | GOOD | NONE | Behavior-level assertions via getByRole/testid, verifies navigation and hook delegation; web/tests/watchlist-content.test.tsx:68-85 |
| `web/tests/whatif-margin.test.tsx` | IMPROVE | P1 | 'stays idle' tests use a real 600ms setTimeout wait to prove no-fetch instead of asserting on the debounce timer directly; web/tests/whatif-margin.test.tsx:131,146 |
| `web/tests/workflow-api.test.ts` | GOOD | P0 | Auth gate + user-scoped CRUD isolation on a real in-memory libsql DB, covers the leak-another-user's-graphs case; web/tests/workflow-api.test.ts:115-121 |
| `web/tests/workflow-composer.test.ts` | GOOD | P1 | Pure client contract asserting exact request shapes and gate-name interpretation of run reports; web/tests/workflow-composer.test.ts:117-124 |
| `web/tests/workspace-chrome-alignment.test.ts` | IMPROVE | P2 | Regex-parses raw globals.css text to assert CSS custom-property usage, brittle to formatting though not to refactors of markup; web/tests/workspace-chrome-alignment.test.ts:22-28 |
| `web/tests/workspace-orders-implied.test.tsx` | GOOD | P1 | Verifies real Black-Scholes-derived implied values in rendered table cells against bsCall/bsPut, not mocked math; web/tests/workspace-orders-implied.test.tsx:290-298,377-387 |
| `web/tests/workspace-sections-table-search-headers.test.ts` | GOOD | P2 | Confirms filter inputs land inside correct section headers via within(), not brittle selectors; web/tests/workspace-sections-table-search-headers.test.ts:112-126 |
| `web/tests/ws-keepalive-client.test.ts` | GOOD | P1 | Fake timers drive ping/pong and 60s stale-reconnect logic deterministically via vi.advanceTimersByTime, no real sleeps; web/tests/ws-keepalive-client.test.ts:85-91 |
| `web/tests/ws-ticket-local.test.ts` | IMPROVE | P2 | Second describe block only asserts route.POST is a function, doesn't exercise route behavior; web/tests/ws-ticket-local.test.ts:56-61 |
| `web/tests/yield-curve-api.test.ts` | GOOD | P1 | Turso-first vs disk-fallback vs missing:true contract exercised against a real in-memory libsql DB; web/tests/yield-curve-api.test.ts:102-152 |
| `web/tests/yield-curve-live-api.test.ts` | GOOD | P1 | Covers scale-sanity rejection and cache-TTL no-refetch behavior with injected fetch, not real network; web/tests/yield-curve-live-api.test.ts:104-131 |
| `web/tests/yield-curve-panel.test.tsx` | GOOD | P1 | Covers gating, tone thresholds, live-estimate cell, and a NaN-path-coordinate regression guard on the log axis; web/tests/yield-curve-panel.test.tsx:317-325 |

</details>

---

## 5 · Top missing tests, ranked by blast radius

Produced by six independent critical-path sweeps over SOURCE files, each verified against the existing test files before being claimed (a finding was discarded if any test already covered it). Items marked **[LIVE BUG]** describe behavior that is wrong in the code today, not merely untested. Each is a backlog task (§9) — the task IDs are the contract.

**Duplicate-order cluster (worst case: doubled live position):**
1. **[LIVE BUG] T-010** — exit-orders re-places a live SELL every 5-minute cycle while the post-placement journal UPDATE keeps failing (`handlers/exit_orders.py:198` swallows; status stays PENDING). No dedupe, no cap. Test: `test_exit_orders_journal_failure_does_not_replace`.
2. **[LIVE BUG] T-011** — FastAPI `_run_ib_script_with_recovery` re-runs the same script after a gateway restart with no carve-out for non-idempotent `ib_place_order.py` (`scripts/api/server.py:3959`). Test: `test_recovery_never_reruns_ib_place_order_after_gateway_restart`.
3. **[LIVE BUG] T-021** — Next `/api/orders/place` 30s timeout (`place/route.ts:307`) rejects as TimeoutError → `orderIdempotency.ts:82` deletes the key → generic 500 → identical retry re-places an order that may be live inside the 25s-vs-30s ambiguity window. Test: `order-place timeout retains key + returns explicit ambiguity code`.

**Order-placement confirm loop (`scripts/ib_place_order.py`):**
4. T-030 — LimitOrder kwargs (action/qty/price/tif/outsideRth) never asserted; both existing tests MagicMock the constructor without kwarg checks (`test_spx01_grace_wait.py:120`, `test_ib_whatif_margin.py:64`).
5. T-032 — partial fill in the permId window is discarded: error and ok returns omit filled/remaining/avgFillPrice (`ib_place_order.py:452-466`); operator re-places into a real partial position.
6. T-031 — errorEvent + permId==0 exit path returns no structured IB code (`ib_place_order.py:402-418`).
7. T-012 — **[LIVE BUG]** `_extract_json_payload` reverse-walk lets any trailing JSON line shadow the real order result (`scripts/api/subprocess.py:121-132`) — a rejected order can render as HTTP 200 success.
8. T-033/T-034 — combo 12s vs single 6s budget unpinned (`ib_place_order.py:385`); qty/price ≤ 0 accepted (`:177-178`) while the modify path validates (`ib_order_manage.py:189-192`).

**Fill → journal → basis integrity:**
9. **[LIVE BUG] T-013** — `fill-monitor:order-{id}:filled-{n}` trade_id collides across sessions/contracts and `ON CONFLICT(trade_id) DO UPDATE` destroys the older row (`fill_monitor.py:299` + `db/writer.py:46-53`).
10. **[LIVE BUG] T-014** — `_fills_to_entries` labels fills in delivery order, no sort by exec time (`journal_sync.py:496`) — out-of-order delivery writes SELL_TO_OPEN phantoms; both sibling paths sort (`journal_rehydrate.py:135`, `backfill:328`).
11. T-015 — journal_sync's PRODUCTION configuration (`trade_log_path=None`, `run.py:97`) is never instantiated by any test: journal-table dedupe branch, empty-journal abort, and the fact that the JRN-02 reconcile retry never runs in prod (`journal_sync.py:151`) are all uncovered.
12. **[LIVE BUG] T-023** — journal dates derived from UTC exec time / host wall-clock (`journal_sync.py:624`, `fill_monitor.py:310`): every fill after ~20:00 ET lands on the next session date, shifting basis cutoffs and same-day P&L.
13. T-022 — ib_sync completeness guard compares `abs(journal_net) == abs(position_size)` (`ib_sync.py:776`): a sign-flipped journal applies a long lot's basis to a short.
14. T-035/T-045 — correction-suffix exec ids double-write (`journal_sync.py:506`); read-side composite overlap (A, B, A+B rows) double-counts basis (`journal_basis.py:204,283`).

**Web order seam:**
15. **[LIVE BUG] T-020** — ratio combos flattened to `ratio: 1` ignoring `leg.contracts` (`positionTrade.ts:94`); closing a 5×3 structure over-trades the 3-lot side — and `position-trade.test.ts:76-79` asserts the wrong behavior.
16. **[LIVE BUG] T-019** — close detection ignores held quantity (`positionTrade.ts:171-174`): SELL 10 vs 5 held renders a riskless close, hiding 5 naked shorts (the linear branch gates correctly, `computeLinearRisk.ts:70,84-89`; the options branch doesn't).
17. **[LIVE BUG] T-018** — demo blockade absent on `/api/orders/modify` + `/cancel` (modify's replaceOrder does a REAL cancel+place, `modify/route.ts:139`), and `resolveDemoOrderDecision` fails OPEN on an auth throw (`orderBlockade.ts:32`).
18. T-036 — blotter `fromJournal` has no journal-vs-journal exec-id dedupe: duplicate/overlapping rows double realized P&L on `/orders` (`fromJournal.ts:679,760`).

**Persistence:**
19. **[LIVE BUG] T-024** — `replace_open_orders_for_session` = unconditional DELETE + N INSERTs over an autocommitting pipeline (`db/writer.py:1069-1090`): a mid-loop failure leaves `/orders` empty and indistinguishable from a flat book (`readOrdersSnapshotFromDb` coerces null → EMPTY_ORDERS).
20. T-046 — `delete_portfolio_snapshots_before`'s incident-born degradation ladder (fallback + abort + count accounting, `writer.py:361-451`) is dead code under test.
21. T-042 — `dbFirstRead` serves whichever source is NEWER even if structurally empty (`dbFirstRead.ts:99-108`) — the SW has `API_BODY_VALIDATORS` for exactly this; the DB chokepoint has nothing.
22. T-043/T-053 — the Python transport-vs-statement retry classifier is untested (`writer.py:320-358`; its JS twin IS pinned, `db-writer-bounds.test.ts:79`); `hrana_http` failure modes (timeout vs statement error vs malformed body) collapse into one string (`hrana_http.py:141-183`) with zero direct tests.

**Market data:**
23. **[LIVE BUG] T-016** — zero prices pass the `< 0` filter (`scripts/ib_tick_handler.js:12`): bid=0/ask=0 derives mid 0 and publishes `last = $0.00` into P&L and order-ticket defaults.
24. **[LIVE BUG] T-017** — `restoreSubscriptions` rebroadcasts raw cached state on every reconnect, bypassing `safeInitialState` (`ib_realtime_server.js:1806`) — prior-session quotes replayed as live to every client.
25. T-041 — `applyDepthDelta` position-shift semantics (OOB insert/update/delete, numRows cap) wholly untested; the existing "contract tests" grep source strings (`ib_realtime_server.js:1350`).

**Risk & kill switches:**
26. T-037/T-038 — margin-warning equality boundaries (cushion exactly 0.05/0.01) and NaN inputs silently yield `none` (`marginWarning.ts:122`); Kelly's 2.5% cap inverts on negative bankroll and propagates NaN (`kelly.py:68`), CLI drops sizing keys for bankroll 0.
27. T-039/T-040 — a P1 page with missing Pushover creds is stamped `notified` and cooldown-muted up to 24h (`notify.py:447`); a NEWLY-failing IB service is absorbed into a muted grouped alert (`grouping.py:292`) — `exit-orders` can go dark for a trading day.
28. T-052 — Gate-4 naked-short `_Impl` bodies have zero executing coverage (skipped suites call the short-circuited public entry points, `naked-short-guard.test.ts:128,460`; `test_naked_short_audit.py:93`) and the re-enable runbook `docs/naked-short-reenable.md` does not exist — re-enabling per CLAUDE.md §Gates is currently unverifiable.

Also examined and found adequately covered (no action): concurrent idempotent dedupe of identical in-flight orders (`order-idempotency.test.ts:14-28`), stale-quote guards on the three subscribe paths (`stale-option-quote-guard.test.ts:139-161`), 2FA push-lock storms (`test_ib_2fa_lock.py`, `test_ib_watchdog_2fa_storm_2026_07_05.py`), destroy-storm cooldown (`db-destroy-storm.test.ts`), auth perimeter matrix (`test_route_authz_matrix.py`, set-equality pinned).

---


---

## 6 · Fragile tests — mechanism and fix

The 3-run protocol (§3) found **zero nondeterministic tests**; the entries below are fragile by MECHANISM (they can fail without a real bug or pass with one under the right conditions), plus the three deterministic environment-coupled reds proven by the runs (`test_performance_explainer_report.py` — live-cache dependence, red 3/3 locally, silently skipped in CI; `test_deploy_corrections.py[int,hup]` — darwin signal coupling, red 3/3 locally, green in CI; `web/e2e/prices-performance.test.js` — kills the whole Playwright run). Standard fixes by mechanism: real sleep → condition polling/injected clock or fake timers; wall-clock → injected `now`; `waitForTimeout` → web-first assertions; CSS/nth selectors → roles/test-ids/text; partial stubs → stub every route the page calls; retry-until-green → fix the underlying race.

| File | Sev | Mechanisms (evidence) | Finding |
|---|---|---|---|
| `scripts/test_ib_realtime.py` | P1 | real network + sleep-as-sync (scripts/test_ib_realtime.py:134); hardcoded latency threshold (scripts/test_ib_realtime.py:460) | Real IB/WS network calls with sleep-based sync (scripts/test_ib_realtime.py:134,151,175) and hardcoded avg_latency<100ms threshold (scripts/test_ib_realtime.py:460); explicitly opts out of pytest via __test__=False. |
| `scripts/tests/test_batched_relay.py` | P1 | real sleep as synchronization for background flush loop (scripts/tests/test_batched_relay.py:44-47); timing-dependent flush-count assertion (scripts/tests/test_batched_relay.py:115-127) | Uses real asyncio.sleep(0.03) against a 10ms flush interval as the synchronization mechanism (scripts/tests/test_batched_relay.py:44-47), racy under CI load. |
| `scripts/tests/test_run_portfolio_refresh_retry.py` | P1 | real sleep as synchronization (scripts/tests/test_run_portfolio_refresh_retry.py:82) | Uses a real time.sleep(0.5) to coordinate the fake server's late start against the script's first curl attempt (scripts/tests/test_run_portfolio_refresh_retry.py:82); a slow CI runner can invert the intended ordering and flip the assertion. |
| `scripts/tests/test_scanner_parallel.py` | P2 | real sleep used to induce thread overlap for a concurrency assertion (scripts/tests/test_scanner_parallel.py:51) | Concurrency asserted via real time.sleep(0.05) inside mocked fetch and counting distinct thread idents, scripts/tests/test_scanner_parallel.py:51-59 |
| `site/e2e/surface-preview.spec.ts` | P2 | DOM-structure-bound selectors (nth-index child selection instead of role/test-id) (site/e2e/surface-preview.spec.ts:14,17-18) | Locates tiles via index-based `:scope > div` nth(0)/nth(1) and `div.grid` class chain (site/e2e/surface-preview.spec.ts:13-18), so a behavior-preserving layout reorder breaks it without any real regression. |
| `web/e2e/chain-strikes-selector.spec.ts` | P2 | real sleep as synchronization (web/e2e/chain-strikes-selector.spec.ts:86) | setStrikesPerSide uses a hardcoded page.waitForTimeout(100) as the sync mechanism for the React re-render (web/e2e/chain-strikes-selector.spec.ts:82-87) instead of a web-first row-count assertion. |
| `web/e2e/chat-launcher-focus.spec.ts` | P2 | retry-until-green / synthetic-event workaround for an unreliable native trigger (web/e2e/chat-launcher-focus.spec.ts:39-48) | Opens the dialog via a documented retry-until-green toPass loop dispatching synthetic keydown events because headless Chromium drops the real shortcut (web/e2e/chat-launcher-focus.spec.ts:39-48). |
| `web/e2e/crox-bull-call-stale-price.spec.ts` | P1 | DOM-structure-bound selector (positional td index) for a P&L-adjacent portfolio row (web/e2e/crox-bull-call-stale-price.spec.ts:279-283) | Portfolio row assertions are keyed by raw column index (`cells.nth(4)`, `.nth(6)`, `.nth(9)`, `.nth(10)`, web/e2e/crox-bull-call-stale-price.spec.ts:280-283) instead of testid/role/header text — any column reorder silently mis-attributes values. |
| `web/e2e/fill-toast.spec.ts` | P1 | real wall-clock wait for a background poll interval as synchronization (web/e2e/fill-toast.spec.ts:196-203); real sleep as synchronization (web/e2e/fill-toast.spec.ts:191) | Second test waits out the real 30s orders-poll interval with test.setTimeout(120_000) rather than triggering/advancing it deterministically (web/e2e/fill-toast.spec.ts:195-204), and the first test uses a bare waitForTimeout(1_000) to let a priming effect 'settle'. |
| `web/e2e/flow-surprise.spec.ts` | P2 | unstubbed dependent /api routes on a page known to require them (contra the repo's e2e stubbing convention) (web/e2e/flow-surprise.spec.ts:60-61) | Only /api/flow-surprise is stubbed before navigating to '/' (web/e2e/flow-surprise.spec.ts:60-61); WorkspaceShell's other required routes (portfolio/orders/regime/blotter/ib-status) are left unstubbed and hit whatever the dev server actually returns. |
| `web/e2e/ib-mfa-reconnect-alert.spec.ts` | P1 | real sleep coupled to a hardcoded mock-internal delay as synchronization (web/e2e/ib-mfa-reconnect-alert.spec.ts:268); real sleep coupled to a hardcoded mock-internal delay as synchronization (web/e2e/ib-mfa-reconnect-alert.spec.ts:276) | The 'generic disconnect/reconnect' tests wait a real waitForTimeout tied to the mock's own internal setTimeout delay (2300ms mock delay vs 2300/2800ms test wait, web/e2e/ib-mfa-reconnect-alert.spec.ts:152-162 vs 268/276) — any timing skew races the assertion. |
| `web/e2e/margin-warning-toast.spec.ts` | P1 | sleep as synchronization for a negative assertion (web/e2e/margin-warning-toast.spec.ts:146); sleep as synchronization to prove no auto-dismiss (web/e2e/margin-warning-toast.spec.ts:195) | Uses waitForTimeout(500) to assert absence of a toast and waitForTimeout(6500) to prove persistence, instead of web-first no-toast/visible assertions (web/e2e/margin-warning-toast.spec.ts:146,195). |
| `web/e2e/mobile-shell.spec.ts` | P2 | real unstubbed network calls to backend routes (web/e2e/mobile-shell.spec.ts:1-30) | No page.route API stubbing anywhere in the file (unlike every sibling mobile-*.spec.ts), so tests hit live/unstubbed backend endpoints and inherit whatever real disk/DB state the dev server has. |
| `web/e2e/modify-order-spread-telemetry.spec.ts` | P1 | unmocked real network/WebSocket dependency for price data (web/e2e/modify-order-spread-telemetry.spec.ts:205); tautological assertion derives expected value from the same DOM text it verifies (web/e2e/modify-order-spread-telemetry.spec.ts:277-283) | Dispatches a `ws-price` CustomEvent that no app code listens to (repo-wide grep outside e2e/ finds zero matches) and the file never mocks the real WebSocket, so displayed bid/ask depend on an unmocked live relay connection; the assertion then recomputes 'expected' spread from that same rendered bid/ask (web/e2e/modify-order-spread-telemetry.spec.ts:215-227,277-283). |
| `web/e2e/regime-cor1m-live-route.spec.ts` | P1 | real uncontrolled filesystem/production data dependency (web/e2e/regime-cor1m-live-route.spec.ts:103) | Reads live production data/cri.json / cri_scheduled with no fixture control (web/e2e/regime-cor1m-live-route.spec.ts:42-58,103); pass/fail depends on whatever is on disk at run time. |
| `web/e2e/regime-rvol-history-live-cache.spec.ts` | P1 | real uncontrolled production data dependency (web/e2e/regime-rvol-history-live-cache.spec.ts:70) | Does not mock /api/regime; asserts 20 RVOL dots and non-'---' strip value against whatever live cache is on disk at run time, web/e2e/regime-rvol-history-live-cache.spec.ts:60-72. |
| `web/e2e/regime-rvol-history-live-route.spec.ts` | P2 | shared mutable filesystem state across parallel test workers (web/e2e/regime-rvol-history-live-route.spec.ts:140-141) | Writes/overwrites shared repo files under data/cri.json and data/cri_scheduled/ that other concurrently running specs' disk-backed /regime route also reads, despite backup/restore, web/e2e/regime-rvol-history-live-route.spec.ts:140-148. |
| `web/e2e/regime-rvol-history.spec.ts` | P2 | shared mutable filesystem state across parallel test workers (web/e2e/regime-rvol-history.spec.ts:120) | Writes a fixture file into the real data/cri_scheduled/ dir consumed by the disk-backed /regime route with no isolation from parallel workers, web/e2e/regime-rvol-history.spec.ts:85-120. |
| `web/e2e/risk-reversal-midprice.spec.ts` | P1 | e2e waitForTimeout sleep as synchronization (web/e2e/risk-reversal-midprice.spec.ts:309) | Core positive-case assertion (MIDPRICE badge appears) is disabled via test.fixme (web/e2e/risk-reversal-midprice.spec.ts:226), and the remaining test uses a real waitForTimeout(500) as synchronization (line 309). |
| `web/e2e/sync-fallback.spec.ts` | P1 | fixed sleep as synchronization (web/e2e/sync-fallback.spec.ts:227); fixed sleep as synchronization (web/e2e/sync-fallback.spec.ts:246) | Uses page.waitForTimeout(2_000) as the synchronization/assertion window for absence of errors (web/e2e/sync-fallback.spec.ts:227,246) instead of a web-first wait. |
| `web/e2e/ticker-search-live.spec.ts` | P2 | fixed sleep as synchronization for debounce (web/e2e/ticker-search-live.spec.ts:179); fixed sleep as synchronization for debounce (web/e2e/ticker-search-live.spec.ts:270); fixed sleep as synchronization for debounce (web/e2e/ticker-search-live.spec.ts:364) | Relies on page.waitForTimeout(500) three times to await debounced WS search results (web/e2e/ticker-search-live.spec.ts:179,270,364) instead of a web-first wait on the dropdown. |
| `web/e2e/ws-connection-stability.spec.ts` | P1 | fixed sleep as synchronization (web/e2e/ws-connection-stability.spec.ts:362); fixed sleep as synchronization (web/e2e/ws-connection-stability.spec.ts:381); fixed sleep as synchronization (web/e2e/ws-connection-stability.spec.ts:415); fixed sleep as synchronization (web/e2e/ws-connection-stability.spec.ts:438) | All four tests gate assertions behind fixed page.waitForTimeout (500/1000/2000/1500ms) instead of web-first waits (web/e2e/ws-connection-stability.spec.ts:362,381,415,438). |
| `web/tests/depth-book-render.test.tsx` | P1 | DOM-structure-bound selectors (class chains) for rendered depth-book structure (web/tests/depth-book-render.test.tsx:137); DOM-structure-bound selectors (web/tests/depth-book-render.test.tsx:239); DOM-structure-bound selectors (web/tests/depth-book-render.test.tsx:377) | Assertions lean almost entirely on CSS class-chain selectors (.book-row[data-lvlfirst], .book-sides, .book-nbbo-tag) rather than roles/test-ids; a CSS refactor breaks them without a behavior change (web/tests/depth-book-render.test.tsx:127) |
| `web/tests/modify-order-modal-layout.test.ts` | P2 | CSS source-text regex matching instead of rendered/computed style (web/tests/modify-order-modal-layout.test.ts:21-37) | Regex-extracts raw CSS text and substring-matches declarations; harmless selector reformatting (e.g. combined selectors) breaks it without a visual regression. web/tests/modify-order-modal-layout.test.ts:13-16. |
| `web/tests/newsfeed-lightbox-mobile-css.test.ts` | P2 | CSS source-text regex matching instead of computed style in a real viewport (web/tests/newsfeed-lightbox-mobile-css.test.ts:42-58) | Custom brace-depth parser regex-extracts a media-query block from globals.css and negative-matches alternate selector spellings; a harmless CSS restructure (renamed/merged media query) breaks it. web/tests/newsfeed-lightbox-mobile-css.test.ts:7-33. |
| `web/tests/typography-semantics-contract.test.ts` | P2 | exact-string equality on formatted source code (web/tests/typography-semantics-contract.test.ts:13) | Asserts exact literal JSX substrings against source files (web/tests/typography-semantics-contract.test.ts:13,25,29-33); any Prettier/attribute-order reformat breaks it with no behavior change. |

---

## 7 · Refactor-completely list — replacement strategy per item

Dominant anti-patterns and their replacements: **(a) self-asserting literals** (test builds a local copy of expected data and asserts it against itself) → import the real module/component and assert its rendered/computed output; **(b) copy-pasted logic mirrors** (test re-implements the production helper and tests the copy) → export the real helper (test-only barrel if needed) and test it; **(c) source-string grepping** (assert the implementation file contains a substring) → execute the code path and assert behavior, keep at most one AST-level contract pin; **(d) swallowed assertions** (try/except-print harnesses that cannot fail) → let assertions throw, mark `integration` if they need live services; **(e) duplicate-without-signal** → delete, keeping the strongest sibling. Each row states what its reader found; the reason names the replacement.

| File | Sev | Finding + replacement |
|---|---|---|
| `.pi/tests/startup-protocol.test.ts` | P2 | Tests at .pi/tests/startup-protocol.test.ts:50-56 and :58-165 build a literal string then assert.ok(ui.hasMessage(same literal)) inline, never calling any startup-protocol.ts code; they should be deleted since :171-350 already exercise the real StartupTracker/summarizeFreeTradeError exports. |
| `lib/tools/__tests__/daily-chg.test.ts` | P0 | Never imports getOptionDailyChg from web/components/WorkspaceSections.tsx (lib/tools/__tests__/daily-chg.test.ts:1-2); defines and tests local mirror fns (:26,:43) instead, so a regression in the real production function (also duplicated in positionUtils.ts, PositionTable.tsx, MobilePositionList.tsx) is invisible to CI. |
| `lib/tools/__tests__/ticker-detail-orders.test.ts` | P0 | Combo-order sections re-implement legPriceKey/net-price/leg-action logic locally (lib/tools/__tests__/ticker-detail-orders.test.ts:105,146,125) instead of importing the real exported legPriceKey (web/lib/positionUtils.ts:332) or the inline combo logic embedded in ModifyOrderModal.tsx:161-243; the test verifies its own copy, not production code, so real drift/regressions in order-leg action or net-price computation on a live order form won't be caught. |
| `scripts/tests/test_code_quality.py` | P2 | TestSafeValue re-implements a hand-copied '_safe_value' mirror instead of importing IBRealtimeServer's real code (scripts/tests/test_code_quality.py:24-34); asserts nothing about production behavior and silently stops catching drift. |
| `scripts/tests/test_menthorq_client_timeouts.py` | P1 | Regex-greps raw source text for literals/constant names (scripts/tests/test_menthorq_client_timeouts.py:31,57,72-77) instead of exercising behavior; a behavior-preserving rename/refactor of _login breaks it. |
| `scripts/tests/test_portfolio_refresh_wrapper.py` | P1 | Greps for literal source strings (scripts/tests/test_portfolio_refresh_wrapper.py:9-11) instead of exercising the script; any behavior-preserving rewrite of the retry logic fails it. |
| `scripts/tests/test_replica_safe_default.py` | P1 | Asserts exact opt-in-gate source strings are literally present (scripts/tests/test_replica_safe_default.py:24-53) instead of invoking the DB clients; any semantically-equivalent rewrite of the boolean check fails it. |
| `scripts/tests/test_spx03_short_availability.py` | P1 | Docstring admits the helpers under test are copy-pasted duplicates of server.py, not imports (scripts/tests/test_spx03_short_availability.py:36-124 vs scripts/api/server.py:4133-4284); a real bug fix to server.py's shortability logic would not be caught. Replace with imports from server.py (extract to a shared pure module if the 3.10 syntax import chain is the real blocker). |
| `scripts/trade_blotter/test_integration.py` | P0 | Every assertion is wrapped in try/except that swallows AssertionError and returns False/prints instead of failing pytest (scripts/trade_blotter/test_integration.py:141-146), so a broken P&L calc collected by pytest never fails the run. |
| `web/e2e/admin-visual-snapshot.spec.ts` | P2 | File's own docstring says 'NOT a regression test' (web/e2e/admin-visual-snapshot.spec.ts:3-4); only assertion is admin-page visible (line 108), duplicating admin-panel.spec.ts render coverage with zero added signal. |
| `web/e2e/prices-performance.test.js` | P2 | Standalone script (not a Playwright test file) spawning a real dev server, hardcoded 10s sleep, real network navigate, and a wall-clock >15000ms pass/fail threshold instead of assertions (web/e2e/prices-performance.test.js:7,11,20,34-38). |
| `web/e2e/regime-closed-refresh.spec.ts` | P1 | Reimplements fetch/render/sync logic inline in page.setContent script (web/e2e/regime-closed-refresh.spec.ts:29-92) instead of exercising real app code; only imports a config constant. |
| `web/tests/account-balances-complete.test.ts` | P1 | Most describe blocks build a local literal array/object and assert it contains itself, e.g. web/tests/account-balances-complete.test.ts:225 checks a hardcoded array for a string it was defined with; no production build_account_summary or MetricCards code is ever imported/invoked. |
| `web/tests/account-metric-modal.test.ts` | P2 | Re-implements MetricCards.tsx modal configs as local literals (web/tests/account-metric-modal.test.ts:37-101) and tests those copies, not the component; onClick tests simulate a closure pattern (web/tests/account-metric-modal.test.ts:246) rather than rendering MetricCard. |
| `web/tests/attribution.test.ts` | P2 | Every assertion checks a hand-built MOCK_ATTRIBUTION literal against itself (web/tests/attribution.test.ts:13-76, 79-124) — no import of any real attribution-computation function; tests nothing production. |
| `web/tests/batched-prices.test.ts` | P1 | handleWSMessage is a local reimplementation, not imported from lib/usePrices; real hook logic drift is invisible. web/tests/batched-prices.test.ts:45 |
| `web/tests/chain-combo-sign-in-submission.test.ts` | P0 | Entirely fs.readFileSync + string/regex matches on OptionsChainTab.tsx; a sign-flip regression that keeps the same substrings ships undetected. web/tests/chain-combo-sign-in-submission.test.ts:10 |
| `web/tests/chain-notional-calc.test.ts` | P0 | fs.readFileSync + regex on source text for notional double-counting guard; a real double-count bug survives if the literal substring changes. web/tests/chain-notional-calc.test.ts:14 |
| `web/tests/chain-prefetch.test.ts` | P2 | Entirely fs.readFileSync + regex/contains assertions on hook and CSS source, never exercises prefetch scheduling at runtime; web/tests/chain-prefetch.test.ts:48 |
| `web/tests/chain-side-filter.test.ts` | P2 | Entirely fs.readFileSync + regex assertions against TSX/CSS source, no render or user interaction; web/tests/chain-side-filter.test.ts:36 |
| `web/tests/chain-sticky-header.test.ts` | P2 | Pure CSS regex assertions against globals.css text, no layout/render verification; web/tests/chain-sticky-header.test.ts:17 |
| `web/tests/complex-risk-profile.test.ts` | P0 | Reimplements the WorkspaceSections.tsx filter inline (web/tests/complex-risk-profile.test.ts:16-24) instead of importing the real logic at web/components/WorkspaceSections.tsx:1253-1255; a regression there would not fail this test. Replace with a test that imports the real filter (export it) so the two can't drift. |
| `web/tests/flex-token-expiry.test.ts` | P1 | Every assertion is `content.toContain("substring")` against raw source text (web/tests/flex-token-expiry.test.ts:107-121) — the route/handler/component are never imported or executed, so a wrong days_remaining/should_warn computation ships green. |
| `web/tests/flow-analysis-classify.test.ts` | P1 | classifyPosition (web/tests/flow-analysis-classify.test.ts:19-46) is an inline TS reimplementation of scripts/flow_analysis.py logic, not an import of the real module — a fix or bug in the Python source is invisible here. |
| `web/tests/gex-share.test.ts` | P2 | All 19 assertions are literal string-containment checks on source files (e.g. web/tests/gex-share.test.ts:36-39 pins card1_/card2_/card3_/card4_ names) with zero execution of the script or routes. |
| `web/tests/header-fullscreen-control.test.ts` | P2 | Both tests are raw source-string containment on Header.tsx/WorkspaceShell.tsx (web/tests/header-fullscreen-control.test.ts:12-16, 25-28) with no render or interaction. |
| `web/tests/ib-depth-stream-contracts.test.ts` | P1 | All 15 tests are regex/string matches against ib_realtime_server.js source (e.g. web/tests/ib-depth-stream-contracts.test.ts:23-28), never executing the depth/NBBO/futures-resolution logic. |
| `web/tests/ib-index-stream-contracts.test.ts` | P1 | Cold-start subscription restore is validated purely via regex-extracted source blocks and string containment (web/tests/ib-index-stream-contracts.test.ts:15-28), never running restoreSubscriptions. |
| `web/tests/ib-no-security-def.test.ts` | P1 | NO_SEC_DEF_REGEX and the cleanup logic are hand-copied into the test file (web/tests/ib-no-security-def.test.ts:12, 53-61) instead of imported from ib_realtime_server.js, so it verifies its own duplicate, not the real handler. |
| `web/tests/ib-realtime-restart-modes.test.ts` | P1 | WS-auth trust delegation and restart-mode branching (cloud/docker vs local launchd, which avoids 2FA-stacking) are validated only via regex-block string containment (web/tests/ib-realtime-restart-modes.test.ts:40-57), never executed. |
| `web/tests/internals-skew-chart.test.ts` | P2 | Reimplements fmtSigned/heights/sort locally instead of importing the component's logic, so a real component bug goes undetected; web/tests/internals-skew-chart.test.ts:11-35,33. |
| `web/tests/internals-skew-route-staleness.test.ts` | P2 | Never calls the route handler despite the file/describe name; re-tests isSkewCacheFresh already covered verbatim by internals-skew-staleness.test.ts; web/tests/internals-skew-route-staleness.test.ts:32-58 vs internals-skew-staleness.test.ts:5-33. |
| `web/tests/journal-sync-config.test.ts` | P2 | Greps hook source for exact literals ('endpoint: "/api/journal"', 'interval: 0'), breaks on harmless reformatting without exercising runtime behavior; web/tests/journal-sync-config.test.ts:12-14. |
| `web/tests/live-data-degraded-banner.test.ts` | P1 | Purely greps WorkspaceShell/offlineStatus source text, never renders the shell or triggers priceError to see the banner appear; web/tests/live-data-degraded-banner.test.ts:18-30. |
| `web/tests/modify-order-ticker-detail.test.ts` | P1 | Regex-matches raw source text (e.g. /setModifyTarget\(null\)/, /onModify\(/) instead of rendering/exercising the modify flow, so a behavior-preserving rename breaks it while a real wiring bug can pass; web/tests/modify-order-ticker-detail.test.ts:46,56,61,86,91. |
| `web/tests/og-chart-system.test.ts` | P2 | Asserts on raw source-file text (`ogChartsSource).toContain(...)`) instead of executing/rendering code (web/tests/og-chart-system.test.ts:29-31). |
| `web/tests/order-migration.test.ts` | P2 | No production module is ever imported; every 'it' asserts on hand-written local literals (web/tests/order-migration.test.ts:15-21, 42-46), so it can never fail from a real regression. Replace with real component/render assertions or delete. |
| `web/tests/order-unified-components.test.ts` | P2 | Reimplements the algorithms it means to test as inline local copies (web/tests/order-unified-components.test.ts:47-108,125-148) instead of importing web/lib/order/ — passes even if the real hook/component diverges. |
| `web/tests/position-pnl-sign.test.ts` | P2 | Tests two locally-defined format functions (fmtPnlBuggy/fmtPnlFixed), never imports real display code — verifies nothing about production behavior (web/tests/position-pnl-sign.test.ts:17-24) |
| `web/tests/price-chart-theme.test.ts` | P2 | Regexes over PriceChart.tsx source text instead of rendering it — a behavior-preserving refactor (renaming var, restructuring JSX) breaks it even though price-chart-shell.test.ts already covers the same behavior via real render (web/tests/price-chart-theme.test.ts:20-49) |
| `web/tests/regime-corrupt-cache.test.ts` | P1 | readLatestCriBuggy/readLatestCriFixed (web/tests/regime-corrupt-cache.test.ts:27-78) and isMarketOpenNow (:169-176) are inline 'replicas', never import app/api/regime/route.ts. |
| `web/tests/regime-day-change.test.ts` | P2 | Reimplements computeDayChange/computePointChange inline as 'Replica' instead of importing RegimePanel's real logic, web/tests/regime-day-change.test.ts:10-16,66-69; a real bug in the component ships untested. |
| `web/tests/regime-relationship-tooltips.test.ts` | P2 | Asserts literal source lines of the component (exact JSX expressions), pinning implementation not behavior. web/tests/regime-relationship-tooltips.test.ts:52-54 |
| `web/tests/regime-strip-responsive.test.ts` | P2 | Pins exact CSS selector/value text (nth-child chains, literal grid-template-columns strings) instead of testing rendered layout. web/tests/regime-strip-responsive.test.ts:23-32 |
| `web/tests/regime-sync-config.test.ts` | P2 | 16-line test asserts literal source text of the polling config object rather than observed polling behavior. web/tests/regime-sync-config.test.ts:12-15 |
| `web/tests/share-pnl.test.ts` | P2 | groupExecutedOrders/execOrderDescription/execOrderShareData/blotterShareData are LOCAL reimplementations (web/tests/share-pnl.test.ts:60-261,771-1011), not the app's real functions — tests verify a copy, not the code. |
| `web/tests/skill-stack-shell-chrome.test.ts` | P2 | Grep-asserts raw source text/identifier names (e.g. web/tests/skill-stack-shell-chrome.test.ts:35-36 `toContain("sectionNeedsPrices")`) instead of rendering/behavior; a behavior-preserving rename breaks it. Replace with rendered DOM/computed-style + a11y-role assertions. |
| `web/tests/ticker-search.test.ts` | P2 | Never renders TickerSearch; reimplements filter/debounce/keyboard-nav logic inline and asserts against itself (web/tests/ticker-search.test.ts:78-86,100-119,121-157) — zero coverage of real component. |
| `web/tests/use-portfolio-sync.test.ts` | P1 | Explicitly tests inline reimplemented staleness/backoff arithmetic instead of usePortfolio itself (web/tests/use-portfolio-sync.test.ts:9-42) — no import from the hook, zero regression coverage on real sync/backoff logic. |

---

## 8 · Structural findings

- **Mocking strategy is a coherent boundary in the strong regions**: in-memory sqlite impersonating the Hrana pipeline with real migration DDL (`test_ib_gateway_auth_recovery_heal.py:64-99`), `fakeDemoDb` modelling the demo tables (`web/tests/helpers/fakeDemoDb.ts:13`), a real-FastAPI spawn harness with clean skip semantics (`web/tests/fastapiHarness.ts:93-168`), and `vitest.setup.ts` enforcing global teardown. The weak regions are the opposite pole: patch-the-module-under-test suites (e.g. `test_spx01_grace_wait.py:120` patching `LimitOrder` and never inspecting it) and **source-string "tests"** that grep the implementation file for substrings instead of executing it (`web/tests/ib-depth-stream-contracts.test.ts`, `scripts/tests/test_menthorq_client_timeouts.py:31,57`, `scripts/tests/test_replica_safe_default.py:24`) — these pass under any behavioral regression that keeps the string.
- **Recommendation — fakes over mocks where money flows:** a single reusable fake IB client (fills, orderStatus transitions, errorEvent injection, permId assignment) would replace the per-file MagicMock soups on the place/manage/fill paths and make scenario tests (partial fill, disconnect mid-order, correction replay) one-liners. The sqlite-behind-hrana fixture should be promoted from `test_ib_gateway_auth_recovery_heal.py` into a shared conftest fixture.
- **Test data is realistic where it matters most** (multi-tranche AAOI/MU fixtures with real strikes and journal rows; checked-in Cboe/FINRA/NYSE fixture files under `scripts/tests/fixtures/`), toy elsewhere (single-digit ints in options math tests hide multiplier/sign errors that realistic premiums expose).
- **Integration tests mostly do integrate**: `web/tests/integration.test.ts` + `kelly/runner.test.ts` spawn real Python; `trade_blotter/test_integration.py` however wraps every assertion in try/except-print (REFACTOR — it can never fail); the 90 MenthorQ `integration`-marked tests are opt-in by design and were not executed (they hit live services).
- **UI tests are split**: the better half asserts rendered text/roles via Testing Library; a visible minority binds to CSS classes and column indexes (`crox-bull-call-stale-price.spec.ts` cells.nth(4/6/9/10), `site/e2e/surface-preview.spec.ts` `:scope > div` chains) — the account-metric-cards subset failure (all 9 cases timing out on `.metric-card`) is this class of coupling failing in practice.
- **The e2e stub strategy is inconsistent**: some specs stub every API the page touches; others stub one route and let the rest hit a dead backend (`web/e2e/flow-surprise.spec.ts:60-61`). With no CI gate to keep them honest, the stubbed world and the real app have diverged (§3.5).

---

## 9 · Remediation backlog — the frozen PART B contract

Base for PART B: dedicated git worktree off HEAD (pin SHA at audit close; 2a75496a as of drafting).
Concurrent session radon-49 is editing the main tree — PART B never touches the main working tree.
Order of work: T-001..T-003 (signal repair / quarantine) → P0 (T-010+) → P1 (T-030+) → P2 (T-050+).
Every new test must first FAIL (against current buggy code or a deliberately injected fault) — the red run is logged in REMEDIATION_LOG.md before the green run.

## Signal repair (quarantine equivalents — do first)
- T-001 [P1] Playwright suite is unrunnable: `web/e2e/prices-performance.test.js` matches Playwright's default testMatch, self-spawns `npm run dev` (spawn /bin/sh ENOENT → unhandled error) and kills the whole run in 7s. Fix: add `testIgnore: ["**/*.test.js"]` (or delete the file — it's a legacy node script, not a spec). AC: `npx playwright test --list` exits 0 and lists ~123 specs; no spec self-spawns a server.
- T-002 [P2] `cloud/tests/test_deploy_corrections.py::test_external_signal_status_is_preserved_after_recovery[int|hup]` hangs on darwin (macOS bash signal propagation; SIGKILL at cloud/tests/test_deploy_corrections.py:469) — deterministic local red, green in CI. Fix: `pytest.mark.skipif(sys.platform == "darwin", reason=...)` scoped to those two params, keeping linux coverage. AC: cloud suite green locally 3×; the two tests still COLLECT and run on linux (marker inspected in test).
- T-003 [P1] `scripts/tests/test_performance_explainer_report.py::test_build_html_mentions_shared_chart_contract` asserts against live gitignored `data/performance.json` (red locally on stale cache: missing `period_label`; auto-skips in CI → build_html has ZERO CI coverage). Fix: pinned fixture payload (with period_label) checked into tests; live-cache pass stays behind an env flag. AC: test runs+passes with no data/performance.json; deleting `period_label` from the fixture makes it fail (red→green demonstrated).

## P0 — money-losing gaps
- T-010 exit_orders re-places a live SELL when the post-placement journal UPDATE fails (`_update_journal_trade` swallows, status stays PENDING, next 300s cycle re-places; scripts/monitor_daemon/handlers/exit_orders.py:198). Test: journal UPDATE raises; run execute() twice; assert place_order called ONCE. Fix: in-cycle placed-set / persist-first ordering / error state that blocks re-arm. AC: red (called twice today) → green; test_monitor_daemon suite green.
- T-011 `_run_ib_script_with_recovery` re-runs the SAME script after gateway restart with no carve-out for non-idempotent `ib_place_order.py` (scripts/api/server.py:3959 via :3906; /orders/place at :2064). Test: recovery path with script=ib_place_order.py; assert runner invoked once and result marked indeterminate. Fix: placement carve-out. AC: red→green; scripts/api/tests green.
- T-012 `_extract_json_payload` reverse-walk returns the FIRST parseable line from the END — trailing JSON shadows the real order result (scripts/api/subprocess.py:121-132; consumed at server.py:2077,2093). Test: stdout = result JSON then a trailing JSON progress line; assert the result (with status/permId) wins. Fix: prefer last parseable dict containing "status", fallback preserved for array outputs (leap case). AC: red→green; test_api_subprocess green incl. existing junk-before cases.
- T-013 fill_monitor synthetic journal trade_id `fill-monitor:order-{id}:filled-{n}` collides across sessions/contracts → ON CONFLICT(trade_id) destroys an older journal row (fill_monitor.py:299; writer.py JOURNAL_UPSERT_SQL ON CONFLICT DO UPDATE). Test: two contracts, same orderId/filled progression → distinct trade_ids + two rows survive. Fix: fold permId/conId + ET date into the key. AC: red→green.
- T-014 journal_sync labels fills in DELIVERY order — no sort by execution.time (journal_sync.py:496; siblings sort: journal_rehydrate.py:135, backfill:328). Out-of-order delivery mislabels SELL as SELL_TO_OPEN. Test: get_fills() returns [sell@14:05, buy@14:00]; assert BUY_OPTION + SELL_OPTION. Fix: sort per contract by exec time. AC: red→green.
- T-015 journal_sync PRODUCTION wiring (trade_log_path=None, run.py:97) has zero tests: journal-table dedupe branch (journal_sync.py:123), empty-journal RuntimeError → 'journal read failed:' cycle abort (:378-379→:129), reconciled hard-wired 0 (:151). Tests for all three (fault-injected upsert retry proves at-least-once). Fix only what the tests prove broken. AC: prod-config tests green; any behavior fix red→green.
- T-016 Relay accepts ZERO prices: normalizeNumber rejects only <0, so bid=0/ask=0 derives mid 0 → published last=0 (scripts/ib_tick_handler.js:12). Test: 0/0 leaves last null; 0/0.05 still derives 0.025; legit zero SIZES unaffected. Fix in tick handler. AC: red→green; vitest green.
- T-017 restoreSubscriptions broadcasts raw cached state bypassing safeInitialState — prior-session quotes replayed as live on EVERY reconnect (ib_realtime_server.js:1806; guard pinned only on 3 subscribe paths per stale-option-quote-guard.test.ts:139-161). Test: 9h-old cached state → emitted price has nulled bid/ask. Fix: route through safeInitialState (extract if needed). AC: red→green (allowed: minimal export-for-test refactor of the relay).
- T-018 Demo-order blockade absent on /api/orders/modify + /api/orders/cancel (modify replaceOrder does REAL cancel+place: modify/route.ts:139, cancel/route.ts:36) AND resolveDemoOrderDecision fails OPEN on authFn throw (orderBlockade.ts:32). Tests: demo claims on modify/cancel never reach real endpoints; authFn rejection ≠ allow. Fix: wire blockade + fail-closed. AC: red→green.
- T-019 buildPositionTradeOrder classifies close purely from direction+action, ignoring HELD quantity — SELL 10 vs 5 held → riskless-close short-circuit hides 5 naked shorts (positionTrade.ts:171-174; useOrderRisk closeOut short-circuit; linear branch does gate: computeLinearRisk.ts:70,84-89). Test: qty>held → not a pure close (naked residue carried into risk input). Fix mirroring linear split. AC: red→green; order-risk + fuzz suites green.
- T-020 Ratio combos flattened: positionTrade.ts:94 hardcodes ratio:1 per leg, ignoring leg.contracts — closing a 5x3 ratio structure sends 1:1 BAG (over-trades the 3-lot side). Existing test PINS the defect (position-trade.test.ts:76-79 asserts ratio:1 on a 5x3 fixture). Test: 5x3 fixture → legs ratio 3 and 5 (GCD-consistent). Fix builder; correct the defect-pinning assertion (documented in log — correction of a wrong pin, not weakening). AC: red→green.
- T-021 30s Next timeout vs 25s FastAPI: TimeoutError rejection deletes the idempotency key (orderIdempotency.ts:82) and returns generic 500 → identical retry re-places a possibly-live order (place/route.ts:307,371-380). Test: radonFetch rejects TimeoutError → response carries explicit ambiguity code AND immediate identical resubmit does NOT re-call place. Fix: retain key on timeout-class failures; distinguishable error envelope. AC: red→green.
- T-022 ib_sync journal-vs-position completeness guard compares magnitudes only — `abs(journal_net) == abs(position_size)` lets a sign-flipped journal apply a long lot's basis to a short (ib_sync.py:776; test_ib_sync_basis_guard.py has no opposite-sign case). Test: +10 journal vs -10 position → override rejected; journal_net==0 vs -10 → rejected. Fix: signed compare. AC: red→green.
- T-023 Journal dates derived from UTC (journal_sync.py:624 strftime on UTC exec time; fill_monitor.py:310 host wall-clock) — fills after ~20:00 ET stamp the NEXT day, shifting basis cutoffs and same-day P&L. Test: exec time 23:30Z (19:30 ET) → date 2026-06-25; DST boundary case. Fix: ZoneInfo("America/New_York") session date. AC: red→green.
- T-024 replace_open_orders_for_session: unconditional DELETE then N INSERTs, single trailing commit, direct-cloud autocommits per statement → failure mid-loop leaves /orders EMPTY and indistinguishable from a flat book (writer.py:1069-1090; readOrdersSnapshotFromDb coerces null→EMPTY_ORDERS). Test: execute raises on 2nd INSERT → pre-existing rows intact OR error surfaced distinguishably. Fix: stage-and-swap or single-statement multi-row INSERT + guarded DELETE. AC: red→green; test_phase34_writers green. (If Hrana tx semantics block a clean fix: BLOCKED entry with measured behavior + design note.)

## P1 — correctness gaps
- T-030 Place-path LimitOrder kwargs contract: assert action/totalQuantity/lmtPrice/tif/outsideRth wired verbatim (ib_place_order.py:314-320; currently only MagicMock-patched, kwargs never asserted). Test-only. AC: new test green; a deliberate kwarg swap makes it red (demonstrated then reverted).
- T-031 permId==0 + errorEvent poll exit returns unstructured error (ib_place_order.py:402-403,410-418): test + fix to include ib_error_code/ib_error_text. AC: red→green.
- T-032 Partial-fill surfacing on place result: terminal-failed + ok paths omit filled/remaining/avgFillPrice (ib_place_order.py:452-466). Test: partially-filled-then-cancelled reports filled qty, not bare error. Fix: include fill fields. AC: red→green.
- T-033 Combo 12s / single-leg 6s confirm budget unpinned (ib_place_order.py:385). Test-only pin via injected clock. AC: green; mutating 12.0→6.0 goes red (demonstrated then reverted).
- T-034 Place-path input bounds: qty/limitPrice ≤0 accepted (ib_place_order.py:177-178; modify path guards at ib_order_manage.py:189-192). Test + refuse-before-placeOrder fix; mirror in placeOrderBodySchema (positive constraints). AC: red→green.
- T-035 Correction-suffix exec ids (EXEC.01.01 → .01.02) treated as new fills → double-counted (journal_sync.py:506 exact-string dedupe). Test: correction supersedes (one row, corrected qty). Fix: dedupe on correction root. AC: red→green (journal_sync; mirror check in rehydrate _is_duplicate).
- T-036 blotter fromJournal: no journal-vs-journal exec-id dedupe — duplicate/overlapping rows double realized P&L (fromJournal.ts:679,760). Test: rows 'a' and 'a+b' → one trade, P&L counted once. Fix: exec-id-part dedupe. AC: red→green.
- T-037 margin-warning boundaries + NaN: cushion exactly 0.05/0.01, ewl == mmr*1.1, NaN inputs (marginWarning.ts:122; no equality/NaN cases in margin-warning.test.ts). Test table + Number.isFinite guard fix. AC: red→green.
- T-038 Kelly cap domain: bankroll 0/negative/NaN breaks the 2.5% cap (kelly.py:68 np.minimum with negative cap; CLI `if args.bankroll:` drops size keys for 0). Test + clamp fix (finite, ≥0; CLI emits use_size=0). AC: red→green.
- T-039 watchdog dispatch stamps 'notified' + arms cooldown when Pushover creds absent → P1 silently muted up to 24h (notify.py:447). Test: no-creds dispatch → cooldown still allows fire. Fix: only mark notified on actual delivery. AC: red→green.
- T-040 Grouped-cooldown absorbs a NEWLY-failing IB service into a muted grouped key (grouping.py:292) — exit-orders outage maskable for 24h. Test: healthy→failing service after grouped fire must page. Fix: grouped_handled = only services in the originally-fired set. AC: red→green.
- T-041 applyDepthDelta position-shift semantics: no bounds/length invariant, OOB update degrades to insert (ib_realtime_server.js:1350; existing "tests" are source-string matches). Extract reducer + behavioral tests (insert/update/delete in & out of bounds, numRows cap). Fix OOB handling. AC: red→green.
- T-042 dbFirstRead serves the NEWER source even when structurally empty/degraded (dbFirstRead.ts:99-108; SW has API_BODY_VALIDATORS, this chokepoint has none). Test: fresher `{results:[]}`/`missing:true` never beats populated older snapshot. Fix: validity gate. AC: red→green.
- T-043 Python delete-retry classifier untested + asymmetric with JS twin (writer.py:320-358; JS pin at db-writer-bounds.test.ts:79). Test-only: transport texts retry once, statement errors never re-execute. AC: green; classifier mutation red (demonstrated then reverted).
- T-044 exit_orders quote-state gates: bid=0/ask=0, one-sided book, stale/halt marker → no order (exit_orders.py:266; mid<=0 skip at :270 uncovered). Tests + minimal guard fix if red. AC: green with all three cases.
- T-045 Read-side composite overlap: journal rows A, B, and A+B double-count net qty/basis (journal_basis.py:204,283; write-side dedupe tested, read-side never). Test: A(8)+B(69)+A+B(77) → net 77, single-lot basis. Fix: exec-id-part-aware accumulation. AC: red→green.
- T-046 delete_portfolio_snapshots_before degradation ladder dead-code under test (writer.py:361-451): transport-fail → per-key fallback; wedged page → abort; count never inflated. Tests with injected HranaHttpError. Fix count accounting if red. AC: green ×3 cases.

## P2 — fragility/structure
- T-050 pytest coverage counts test files themselves (trade_blotter/test_blotter.py in the report) — exclude tests from --cov to make the ratchet honest. AC: cov config excludes test modules; ratchet still ≥64 after re-measure (adjust threshold only DOWNWARD-never; if honest measure <64, report, don't lower gate silently — BLOCKED entry for maintainer decision).
- T-051 Root `tests/test_portfolio_performance.py` (TWR builder, 20 money-math cases, passes locally) is not in the CI pytest command — add `tests` to the CI gate invocation. AC: ci.yml runs it; local 3× green. (Do NOT touch untracked tests/test_position_return_capital.py — concurrent-session WIP.)
- T-052 Gate-4 naked-short _Impl bodies have zero executing coverage and the re-enable runbook docs/naked-short-reenable.md doesn't exist (nakedShortGuard.ts:316; describe.skip at naked-short-guard.test.ts:128,460; skipped class test_naked_short_audit.py:93). Port skipped cases to call _Impls via a __test_only__ barrel so re-enable has a tripwire. AC: impl tests run green in CI (not skipped).
- T-053 hrana_http failure-mode contract: socket.timeout / URLError / statement-error / malformed body each produce classifiable HranaHttpError; timeout honored (hrana_http.py:141-183; zero direct tests today). Test-only. AC: green.
- T-054 scripts/test_ib_realtime.py collects 0 tests (pytest exit 5) yet pyproject comment claims broad collection protects it — correct the comment; keep the file as the manual harness it is. AC: comment matches reality; collection unchanged for trade_blotter.

Deferred (out of backlog, documented in audit as follow-ups): relay backpressure/high-water mark (needs design), futures front-month TTL/rollover re-resolution (needs live IB verification window), @fwd subscription teardown, per-client batch stale-over-fresh ordering, Reg-T/what-if live verification, e2e-in-CI loopback blocker.

---

## 10 · Appendix

- **Artifacts** (session scratchpad `/private/tmp/claude-501/-Users-joemccann-dev-apps-finance-radon/99843f61-2b84-4cad-997a-d8a39756966d/scratchpad`): `runs/` (all logs + junit XML for the 3x runs, extras, playwright), `classify/` (per-batch classification JSON), `gaps/order-placement.json`, `audit-data.json`, `missing-behaviors.json` (per-file classifier suggestions, 140 entries — supplementary to the ranked §5), `fragile-refactor.json`.
- **Environment change made during audit:** `pip install --break-system-packages mcp==1.28.1` (matches `requirements.txt:83`; local-only collection fix). No repo file touched.
- **Conflict note:** `scripts/api/tests/test_demo_trial_expiry_route.py` was classified by two readers (identical bucket); first record kept.
- **PART B ground rules recap:** frozen backlog in §9; isolated worktree off `2a75496a`; REMEDIATION_LOG.md tracks every task; never weaken an assertion to go green.

## NEW_FINDINGS (post-freeze)

_Discoveries made during PART B land here; they do not expand the frozen backlog._

- **Retry classifier bypassed by isinstance (found landing T-043):** `_hrana_with_retry`'s gate retries ANY `HranaHttpError` once — since `hrana_execute`/`hrana_query` wrap every failure (including genuine SQL errors) as `HranaHttpError`, `_is_delete_transport_error` is effectively dead code at the real DELETE call sites. Today's callers are idempotent DELETEs so the blast radius is nil, but wiring this helper onto any INSERT path would silently gain re-execution semantics. Pinned loudly in `scripts/tests/test_db_writer_transport_classifier.py`; fix deliberately out of scope.
- **Production journal_sync bricked on an empty journal table (fixed under T-015's umbrella):** beyond "untested", the prod wiring raised the recovery-path "journal table is empty" error on every cycle of a fresh host — first fill could never import. Fixed via `allow_empty=True` on the prod path only.
- **Legacy fill-monitor persistence test was latently address-dependent (fixed under T-023):** `"5" in trade_id` only held while mock reprs leaked into the key; realistic fixture identities restore determinism without weakening the assertion.
- **Cockpit misses portfolio arrival after client-side navigation (found by wave-2 spec repair; FIXED 2026-08-08 in #14):** `TickerDetailContext` previously wrote portfolio/orders into a ref with no state update (`getPortfolio` read the ref, `TickerWorkspace` sampled it only during its own render), so a snapshot landing after mount never re-rendered the cockpit — reproduced 3/3 on `/portfolio → View details` click-throughs (position renders flat, ticket shows an opening form for a HELD spread), masked in production by constant WS re-renders. Now dual-writes to React state; `TickerWorkspace` consumes the reactive `portfolio`/`orders`. Tests: `web/tests/ticker-detail-portfolio-reactivity.test.tsx`.
- **FillsModal can itemize fills its own displayed total excludes (found by wave-2 spec repair; FIXED 2026-08-08 in #14):** `MetricCards` handed the modal raw `executed_orders` while `totalRealizedPnl` was ET-day-cut (`lib/realized-pnl.ts`), so a multi-day fills payload showed prior-session rows above a total that omitted them (`/orders` already re-filtered via `WorkspaceSections.tsx`). `MetricCards` now passes `filterExecutedToEtToday(executedOrders)` into `FillsModal`. Tests: `web/tests/fills-modal-et-day-cut.test.tsx`.
- **Watchdog-notify test env-isolation (fixed in wave-2):** `test_writes_watchdog_alerts_row` deleted only `PUSHOVER_USER`; with a `load_dotenv`-visible `.env` in the tree, the leftover token read as half-configured (a T-039 error state) depending on import order. Both vars now cleared.
- **E2E testid backlog (open):** the wave-2 repair agents catalogued ~17 places where specs must hang on CSS classes/nth-child because components expose no `data-testid`/role (MetricCards cards+toggles, AssetCockpit ticket anchor, OrderTab submit buttons, SharePnl trigger/popover/toggles, exec-group rows, toast kinds). Full list in the wave-2 workflow result (`tasks/wlnotw78h.output`); adopting them removes the dominant residual fragility class in `web/e2e/`.
- **`next dev` cannot run in the CI Playwright container (open, infra):** neither turbopack nor webpack `next dev` readies inside `mcr.microsoft.com/playwright` — cold-compiling this app's heavy routes on demand hangs in the resource-constrained container (CI runs 31268084987 / 31268824260 timed out after the middleware line). The `e2e-financial-smoke` job therefore builds once and serves a prebuilt `next start` (the perimeter-smoke pattern). A follow-up could give the container more resources or warm the dev routes, but production start is the pragmatic path.
- **`pytest cloud/tests` is 10-red on any macOS runner, identically on `origin/main` (found running the gates, 2026-08-17; NOT fixed):** `test_bootstrap_control_plane.py::test_root_verifier_rejects_installed_target_drift[missing|symlink|hash|unreadable]` and `test_monorepo_cutover.py::{test_bootstrap_manifest_format_passes_deploy_preflight, test_deploy_rejects_readiness_marker_that_does_not_match_manifest, test_unsafe_non_helper_manifest_target_fails_before_deploy_mutation[missing|symlink|unreadable|directory]}` all die on `assert shutil.which("sha256sum") is not None` (`cloud/tests/test_monorepo_cutover.py:175` and the sibling in `test_bootstrap_control_plane.py`). macOS ships `shasum`/`openssl`, not the coreutils `sha256sum`, so the precondition can never hold here. Verified pre-existing, not introduced by this remediation: a clean `origin/main` worktree gives the identical `10 failed, 848 passed, 4 skipped`. Green in CI (ubuntu), so the control-plane verifier coverage is real — the damage is local: every weekend-loop run must hand-diff 10 known reds to see a genuine new one, which is how a real cloud regression gets waved through. Same class as T-002 (darwin-scoped skip preserving linux coverage), but the honest fix here is a portable digest (`shasum -a 256` fallback, or `hashlib` in-process) rather than another skip, so the assertion keeps running on both platforms. NOT changed in this pass: it is not a backlog item and swapping a security-verifier's hash tool deserves its own red/green.
- **One unreproduced 10-failure vitest round under back-to-back gate load (observed 2026-08-17; NO named tests):** the first of three scripted gate rounds reported `Tests 10 failed | 6706 passed (6716)`; rounds 2 and 3, four further sequential full runs, and two full runs executed DELIBERATELY concurrent with a full `pytest` all reported `6716 passed`. The failing test names were not captured because the loop's gate command pipes vitest through `tail`, keeping only the summary line. This is the load-sensitive class the audit already names (T-062 was exactly this shape and is fixed), so a second instance is plausible, but it must not be called a finding without names. Actionable regardless of diagnosis: the weekend loop should persist per-test vitest output on gate runs so the next occurrence is nameable in one shot. Recorded as a lesson in `.claude/skills/testing-weekend/SKILL.md`.
- **`e2e/performance-twr-payload.spec.ts` is permanently RED and pins a superseded payload contract (found landing T-073, 2026-08-17; NOT fixed):** the spec serves a hand-built `TWR_SNAPSHOT` and asserts the hero renders `+10.00%`, but it renders `--`. Reproduced under BOTH `next dev` and a production `next start` (so it is not the Day Move dev/prod class), fully `page.route`-mocked, no live data. Root cause: the fixture carries no `schema_version`, so `isV2Payload` (`web/lib/performanceData.ts:302-304`) is false → `resolveStatus` returns `"degraded"` for any legacy payload regardless of its own declared `status: "ok"` (`:87-89`) and `resolveFlowsStatus` returns `"failed"` (`:94-96`) → `twrCumReturn` is nulled at `:808-809` → `fmtPct(null)` renders `--`. A second latent landmine sits behind it: the fixture's hardcoded `period_end: "2026-03-20"` is now months past `NAV_STALENESS_BUDGET_SESSIONS = 2`, so even a v2 rewrite must use window-relative dates (staleness is decided at READ TIME from the NAV date and the clock, `:787-792`). Invisible because the file is not in the CI subset. Deliberately NOT rewritten in this remediation pass: making it green means constructing a valid v2 payload (the canonical builder already exists at `web/tests/fixtures/performanceScenarios.ts:198 goldenOkPayload()`) and deciding what the spec should now assert — a v2 payload rendering `+10.00%`, or a legacy payload correctly rendering `--`. That is a contract decision, not a mechanical fix. It is also why this spec was excluded from the T-073 CI curation.
- **Day Move renders differently under `next start` vs `next dev` (open, possible prod bug):** `day-move-ib-daily-pnl.spec.ts` asserts the IB-daily Day Move value (`acct.daily_pnl` → `-$3,688`), which renders under the dev server but shows "MARKET CLOSED / ---" under a production `next start` with the identical portfolio stub — even though sibling `account_summary` fields (Net Liq etc.) render fine in both. Reproduced 3/3 in isolation. The spec is HELD OUT of the CI subset (still runs locally) pending a decision on whether this is an SSR/hydration ordering bug in the TodayPnlRow Day Move path or a harness artifact; it is the one spec that does not survive the dev→prod server switch.

- **`resolveSpreadPriceData` sibling still stamps the wall clock (found landing T-158, 2026-08-26; NOT fixed):** `resolveSpreadPriceData` (`web/lib/positionUtils.ts:486`) stamps `timestamp: new Date().toISOString()` for the ticker-detail spread header, the same defect class T-158 just closed in `comboQuotePriceData`. It was NOT named in T-158, so it was left alone rather than chased mid-loop. The fix is now a one-liner: `asOf` is available at that call site after the T-158 threading, and `oldestQuoteTimestamp()` (`web/lib/pricesProtocol.ts`) is the helper. Blast radius is the spread header's BID/MID/ASK freshness labelling, not an order price, so it is a rung below T-158 in severity - but the same stale-rendered-as-live shape. Candidate for the next delta audit to number.

- **Six more producers construct their API client outside a health block (found landing T-163, 2026-08-26; NOT fixed):** the T-163 class contract (`scripts/tests/test_service_registration_completeness.py::TestRegisteredProducersHeartbeatBeforeDying`) walks every `scripts/` module declaring `SERVICE`/`SERVICE_NAME` that owns a `record_service_health` writer, and found six offenders of the same silent-death shape beyond the two Equibles ones T-163 fixed: `fetch_credit_spread.py::fetch_uw_closes`, `fetch_iei_hyg.py::fetch_uw_closes`, `fetch_ivrank.py::_real_ib_fetch`, `fetch_trin.py::sample_live`, `fetch_vixcor.py::run`, `ib_reconcile.py::connect_ib`. They are OUT of T-163's scope, so they are pinned in a documented `_UNGUARDED_CTOR_BASELINE` with a companion `test_the_baseline_has_no_stale_entries` that reds if an entry is fixed and left stale - the list can only shrink, never grow silently. Note the credit-spread and IEI/HYG entries are `fetch_uw_closes`, a DIFFERENT function from the `run()` that T-162 wrapped in the same weekend, so the two remediations do not conflict (verified green together in the landed tree). Each needs its own red/green; candidate for the next delta audit to number.

## Delta audit 2026-08-16

Range `d681d247..71de8a33` — 202 commits, 1169 files, +149464/-16816.
508 non-test source files changed; 237 new test files added.
New findings continue the frozen numbering at **T-055**. PART A (§1–§10) is
untouched; nothing above this line was rewritten.

### Standing sweeps

**Gates ×3 from the repo root (clean tree, HEAD 71de8a33):**

| Gate | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `npx vitest run --config vitest.config.ts` (CI excludes applied) | 6555 passed / 623 files | 6555 passed | **6554 passed, 1 FAILED** |
| `python3.13 -m pytest scripts/tests scripts/api/tests scripts/trade_blotter tests` | 6823 passed, 1 skipped, 90 deselected | identical | identical |
| `python3.13 -m pytest cloud/tests -q` | 858 passed, 4 skipped | identical | identical |

The single vitest failure is a real load-sensitive race, not pre-existing
flake — isolated 8×, 8/8 green; see **T-062**. Both pytest layers are
deterministic across three runs.

**Determinism scope note:** the delta touches 263 of the `web/tests` files
and 100 of `scripts/tests`, i.e. effectively the whole suite, so the
"re-run only delta-touched files 3×" rule collapsed into three full-gate
runs. That is what the table records.

**Coverage-ratchet honesty — clean.** `git diff d681d247..HEAD --
vitest.config.ts pyproject.toml` is empty on both files. Thresholds are
byte-identical (vitest lines 75 / functions 78 / branches 65 at
`vitest.config.ts:41-45`; pytest `--cov-fail-under=56` at `ci.yml:157`).
No `omit`/`exclude`/`testIgnore` entry was added or widened anywhere in the
delta. Nothing was lowered, nothing newly inflated. Two pre-existing blind
spots did GROW in absolute terms without the config moving — logged as
T-072, which is the T-050 discipline applied to the web side.

**New skips — none.** All 92,898 added lines under the test directories were
scanned for `test|it|describe|suite.(skip|todo|fixme|only)`, `xit`,
`xdescribe`, `@pytest.mark.(skip|skipif|xfail)`, `pytest.skip(`,
`pytest.xfail(`: **zero hits**. No `.only(` anywhere in the delta. Every
`skip` string in the diff is TWR/parser domain vocabulary
(`subperiod.skip_reason`, `skipped_no_transaction_id`).

### Re-triage of the standing NEW_FINDINGS items

- **E2E testid backlog** — partially worked down organically, still open. The
  delta added 98 `data-testid` lines to `web/components/**` + `web/app/**`
  against 18 removed (net +80). Two NEW specs nonetheless landed on
  structural selectors — T-077, T-070.
- **`next dev` in the CI Playwright container** — unchanged, still infra-open.
  `ci.yml:298` still prebuilds and serves `next start`.
- **`next start` Day Move divergence** — unchanged. `web/e2e/day-move-ib-daily-pnl.spec.ts`
  still exists, still held out of the CI subset, untouched in the delta.
- **T-050 coverage-ratchet threshold** — the pytest side is settled (honest
  metric, gate 56, `pyproject.toml` omit unchanged). The equivalent web-side
  decision is now open as T-072 and needs a maintainer call, not a silent
  edit.

---

### P0 — money-losing gaps

- **T-055 [P0] The exit-order broker-truth index adopts the working TARGET sell as if it were the STOP, so the stop-loss is never placed.** `scripts/monitor_daemon/handlers/exit_orders.py:341-364` (`_active_sell_index` / `_find_active_sell`), consumed at `:426` and `:467`. The index keys only on `("conid", conId)` / `("local", localSymbol)` — no order type, price, or quantity discriminator — while the caller loops `for order_type in ("target", "stop")` at `:119` over two SELL legs of the SAME contract. Cycle 1: the target places (near spot, passes `_can_place_order`); the stop is gated out (a protective stop is by construction far from spot). Cycle 2: `_find_active_sell` returns the working target for the stop candidate, the handler writes `exit_orders["stop"] = {status: "PLACED", order_id: <target's orderId>}` and `continue`s past placement. The stop never reaches IB, the journal claims it is live, and when the target fills the position sits with zero downside protection. Coverage: `grep -n 'order_type\|"stop"' scripts/tests/test_monitor_daemon/test_exit_orders{,_guard_durability,_ack}.py` → 0 matches. The nearest test, `test_exit_orders_guard_durability.py:167-198` (`test_live_open_sell_is_adopted_not_duplicated`), uses a fixture declaring only `exit_orders: {"target": …}` (`:47-48`) and a `_status(db)` helper hard-coded to `["exit_orders"]["target"]` (`:133-134`); the only negative control uses a different conId AND symbol, so the same-contract/different-exit-type collision is never constructed. **AC:** `TestBrokerCrossCheck::test_working_target_is_not_adopted_as_the_stop` — journal trade with BOTH legs, `get_open_orders()` returning the working target SELL on the same conId. Assert `client.place_order.call_count == 1` and `["exit_orders"]["stop"]["order_id"] != 99`. Red today (adopted, never placed, `orders_adopted == 1`) → green once the index keys on exit identity.
- **T-056 [P0] Two equal same-day partial fills of one contract collide in the backfill fingerprint; the second is silently dropped from the journal.** `scripts/backfill_journal_from_executed_orders.py:149-151` (`JournalCoverage.covers_fill`), applied at `:419` and `:449`, backed by `scripts/clients/journal_basis.py:108-138` (`contract_fill_fingerprint`). The fingerprint is `(contract_bucket, ET session date, signed_qty)` — no exec id, no fill price, no time, no ordinal. Two genuine partials (BUY 10 EWY 215C at 10:02 and again at 14:31 — routine IB behaviour) fingerprint identically; their exec-id roots differ so `covers_exec_id` correctly returns False, control falls to `covers_fill`, and the second fill is skipped. `coverage.record` at `:455` seeds the fingerprint in-run, so two identical fills inside one batch collide too. `scripts/monitor_daemon/handlers/evening_execution_sweep.py:250` calls `backfill(..., dry_run=False)` unattended, so a real fill is permanently absent: net open quantity understated by half, `compute_open_basis_for_ticker` returns a half-position basis, and `ib_sync.fetch_positions` overrides IB's correct `avgCost` with it. Coverage: `grep -rn "contract_fill_fingerprint" scripts/tests scripts/api/tests tests cloud/tests` → 0 matches; the function has no direct unit test. `test_backfill_journal_from_executed_orders.py:589-694` (`TestDualIdConventionIdempotency`) has five cases, all probing the true-positive direction or a different contract — `:628` changes ticker/strike so the bucket differs, `:650` flips side so `signed` flips. No case constructs two same-contract, same-day, same-side, same-size fills. **AC:** `test_two_equal_partial_fills_on_one_day_both_land` — assert `[a["status"] for a in actions] == ["inserted_from_eo"]` and 2 journal rows. Red today (`["skipped"]`, 1 row) → green once the fingerprint carries a discriminator both writers still agree on (fill price, or a per-contract/day ordinal).
- **T-057 [P0] The rewritten `/orders/modify` route reports a flat 500 when a destructive `/orders/replace` leaves orders cancelled and the replacement's fate unknown.** `web/app/api/orders/modify/route.ts:163-167` (the new single `radonFetch("/orders/replace", { timeout: 180_000 })`) and its catch-all at `:263`. `/orders/replace` (`scripts/api/server.py:2555+`) cancels every target before placing, and raises `REPLACE_PARTIAL` (502) / `REPLACE_INDETERMINATE` (504) with a structured `detail`. (a) A non-`RadonApiError` throw — the 180 s abort, a socket reset, `TypeError: fetch failed` — falls to `:263` and returns `{ error: "Order modification failed" }` 500; the delta made this strictly worse, since it previously returned `error.message`. At that instant the operator's working exits are already cancelled at IB. Reading a flat failure, the operator either leaves the position unhedged or retries and double-places. `web/app/api/orders/place/route.ts` has `INDETERMINATE_PLACEMENT_MESSAGE` for exactly this class; the replace path has nothing. (b) On the `RadonApiError` branch at `:262`, `error.detail` is an OBJECT returned as `{ error: <object> }` into a client contract that carries `error: string` everywhere else. Coverage: `grep -rln "orders/replace" web/tests` → only `web/tests/api-routes-extended.test.ts`, whose two cases (`:938-958`, `:960-1007`) are happy paths — `mockRadonFetch` is never made to reject on this path, so neither `:262` nor `:263` is ever entered. **AC:** two tests in that file — `mockRejectedValueOnce(new DOMException("aborted","AbortError"))` must produce a non-bare-500 whose `error` string names the indeterminate state and the cancelled orders; `mockRejectedValueOnce(new RadonApiError(502, {code:"REPLACE_PARTIAL", …}))` must yield `typeof body.error === "string"` containing `replacementOrderRef`. Both red today.

### P1 — correctness gaps

- **T-058 [P1] `.pi/tests/**` is collected by ZERO gates; the entire DeepSec security remediation is CI-green if reverted line by line.** `vitest.config.ts:15-21` `test.include` has no `.pi/**` entry. Empirical: `vitest list --config vitest.config.ts` → 6582 tests / 626 files, `grep -c 'pi/tests'` → **0**. Orphans: `.pi/tests/browser-tools.test.ts` (98 lines, 6 tests, ADDED in this delta by `4eaaf5e9`) and `.pi/tests/startup-protocol.test.ts` (pre-existing file, +97 lines / 3 new security cases at `:47`, `:62`, `:388`, `:416` from the same commit). No runner exists — `grep -rn "pi/tests"` across `*.json`/`*.yml`/`*.sh` hits only `TEST_AUDIT.md`; root `package.json` has one script (`db:migrate`). They do pass when invoked by hand (`bun .pi/tests/browser-tools.test.ts` → PASS ×6). Unguarded surface: `parseBrowserCommand`'s verb/option allowlist and no-shell-evaluation boundary, `buildBrowserInvocation` argv separation, `isExecutableFile`, session-name validation, `createWorkspaceTrust` / `resolveTrustedWorkspaceScript` (canonical-root requirement, pinned-revision match, symlink-escape and traversal guards on arbitrary script execution), and `runBoundedStartupJob`'s timeout / output-bytes bound / `process.kill(-pid, "SIGTERM")` group termination. **AC:** a bare glob is NOT sufficient — both files use a hand-rolled `function test(name, fn)` over `node:assert` and register zero vitest tests, so adding `.pi/tests/**/*.test.ts` to `test.include` fails with "No test suite found in file". Either port both to `describe/it/expect` and add the glob at `vitest.config.ts:21`, or add `- run: bun .pi/tests/*.test.ts` to the `web-tests` job at `ci.yml:103`. Prove: collected file count 626 → 628, then delete the `assert.throws(... /unsupported browser command/)` at `.pi/tests/browser-tools.test.ts:34` and confirm CI goes red. (`startup-protocol.test.ts` is already noted unreached at `TEST_AUDIT.md:40`; the browser-tools file and all four new security cases are new here.)
- **T-059 [P1] The 58-structure exhaustive guard on the open-position Return % denominator is self-asserting and survives an `Math.abs` removal in the entry-cost fold.** `web/tests/position-pnl-pct-structures-catalog.test.ts:337-338`, same shape at `:260-267`, `:287`, `:322`, `:366`, `:378`. Line 337-338 reads `const ec = resolveEntryCost(pos); expect(getPnlCapital(pos)).toBe(ec > 0 ? ec : null);`. `getPnlCapital` → `resolveReturnCapital` (`web/lib/positionUtils.ts:302,286-296`) computes `entryCost = resolveEntryCost(pos)` and returns exactly that via the `isFullLossDebit` branch, which short-circuits `true` at `positionUtils.ts:239` for any defined profile with `ec > 0`. For all 37 DEFINED structures the assertion reduces to `expect(resolveEntryCost(pos)).toBe(resolveEntryCost(pos))`. Lines `:262-267` re-implement `isFullLossDebit` (`positionUtils.ts:240-245`) in the test body for the 19 UNDEFINED structures; `:287/:322/:366/:378` assert `getPnlPct === (getPnlDollars(pos)/capital)*100`, verbatim `getPnlPct`'s own body (`positionUtils.ts:336-339`) with the source supplying the numerator. Mutation that keeps all 58 green: drop the `Math.abs` at `positionUtils.ts:109-112` (`return s + sign * l.entry_cost;`) — every multi-leg combo's signed entry cost flips magnitude, the Return % denominator and the rendered Return % both go wrong, and every assertion moves in lockstep. This is precisely the regression `web/CLAUDE.md` warns about under "Avg Entry / Initial Value sign is scoped by leg count". **AC:** replace each `expect(...).toBe(<expression built from resolveEntryCost/getPnlDollars>)` with a hard numeric literal derived from the fixture's own `perUnit`/`contracts`/`mult` (the builder at `:133-155` knows all three). At minimum one literal-valued case per risk_profile bucket, including a multi-leg credit combo whose exact signed net is written out. The `Math.abs` drop must turn that case red.
- **T-060 [P1] `route-local-authz-matrix.test.ts` is 100% source-grep; ~55 protected routes including live order placement have no runtime authz coverage.** `web/tests/route-local-authz-matrix.test.ts:29-75`, all 13 assertions via `readFileSync(...).toContain(...)` (helper at `:25-27`). It never imports or invokes a handler. The test named "guards every reported protected route BEFORE privileged work" asserts only that `requireRouteAccess`, `const access = await requireRouteAccess`, and `if (!access.ok) return access.response` appear SOMEWHERE in the file — never their position, never that they run on the handler being invoked. `web/app/api/orders/route.ts`, `portfolio/route.ts`, `journal/route.ts` and `performance/route.ts` each export two handlers and `preferences/route.ts` exports three; the test cannot tell how many are guarded. Defect that stays green: add `export async function DELETE(...)` to `web/app/api/orders/place/route.ts`, or move the guard below the `radonFetch` in `POST` — all three regexes still match and an unauthenticated caller places or cancels live IB orders. `:62` asserts `operatorOnly: true` as a substring; a comment containing it (as already exists at `preferences/route.ts:16`) satisfies it regardless of what the live call passes. No runtime authz coverage exists elsewhere — `web/tests/route-access.test.ts` tests `requireRouteAccess` in isolation; `api-routes-smoke-admin.test.ts` mocks it and covers only `/api/admin/*`. **AC:** import each route module, call EVERY exported HTTP method with `requireRouteAccess` mocked to deny (pattern already in `api-routes-smoke-admin.test.ts:36-39`), assert 401/403 AND that the downstream side-effect (`radonFetch` / `readOrdersSnapshotFromDb`) was never invoked; assert the options object from `mock.calls[0][1]`, not file text. Moving the guard below `radonFetch` in `orders/place` must turn it red.
- **T-061 [P1] The new IB-sourced contract-multiplier branch in `journal_sync` is structurally unreachable under every existing test double, and admits a $0 cost basis.** `scripts/monitor_daemon/handlers/journal_sync.py:948-955` (`_execution_to_entry`). The delta replaced a hard-coded `multiplier = 100 if sec_type in ("OPT","BAG") else 1` with a read of `contract.multiplier` behind an `isinstance(..., (str,int,float))` guard and `float(multiplier_raw or default)`. `total_cost` at `:955` is what `compute_open_basis_for_ticker` derives per-unit open basis from, which `ib_sync.fetch_positions` uses to OVERRIDE IB's `avgCost`. Failures the new branch admits: an adjusted or non-US-listed option reporting `multiplier="1"`/`"10"` writes a basis 100×/10× too small; and `"0"` is truthy in Python, so `float("0")` yields `0.0` and `total_cost` collapses to just `commission` — a ~$0 basis row that overstates realized P&L on the eventual close by the entire premium. Coverage: `grep -n "multiplier" scripts/tests/test_monitor_daemon/*.py` → 0 matches in the whole daemon package; `grep -rn "_execution_to_entry" scripts/tests` → 0 matches. The branch cannot execute under the existing doubles: `test_journal_sync.py:34-40` sets `fill.contract = MagicMock()` and assigns only symbol/secType/strike/right/expiry, so `getattr(contract, "multiplier", None)` returns a `Mock`, fails the `isinstance` guard at `:949`, and falls back to the old `sec_type` default. Every `total_cost` literal in that file (`:516, :691, :773, :816, :864, :999, :1052, :1252, :1420, :1473`) is an INPUT fixture on a pre-existing journal row, never an assertion on a computed fill. **AC:** `TestExecutionCostBasis` with a `multiplier`-aware `_mock_fill` — (a) `multiplier="100", shares=5, price=12.0, commission=1.0` → `total_cost == 6001.0`; (b) `multiplier="10"` → `601.0`, proving the IB value is honoured; (c) `multiplier="0"` → the writer refuses or falls back to 100, never emitting `total_cost == commission`. Case (b) must go red against reverting `:948-952`; today that revert breaks nothing.
- **T-062 [P1] `admin-components.test.tsx` asserts a post-`await` React state synchronously and flakes the whole vitest gate under load.** `web/tests/admin-components.test.tsx:481-482`, in `"absent or failed power callbacks never change optimistic gateway state"`, added by `4eaaf5e9`. `await waitFor(() => expect(failedStop).toHaveBeenCalledTimes(1))` resolves the moment the mock is CALLED, but `failedStop` is `mockResolvedValue(false)` — the component's `await onStopGateway(...)` and the subsequent pending-state reset in `web/components/admin/Ib2faControls.tsx:278` land in a later microtask/act flush. Line 482 then reads `textContent` synchronously. Observed red on full-suite run 3 of 3 this audit: `expected 'Working...' to be 'Stop Gateway'`. Isolated 8×: 8/8 green — it is a load-sensitive race, not a broken assertion, which is exactly the class that produces intermittent CI red on the operator gateway-power path. **AC:** wrap the assertion in `waitFor` (asserting the settled state, not a synchronous snapshot — this strengthens the contract, it does not weaken it). Prove by making `failedStop` resolve on a macrotask (`() => new Promise(r => setTimeout(() => r(false), 0))`): the current form must go red, the fixed form green, then run the full gate 3×.
- **T-063 [P1] `test_evening_execution_sweep` compares a fill-derived ET date against `now`, and is deterministically red between 00:00 and 02:59 ET — the window this runner executes in.** `scripts/tests/test_monitor_daemon/test_evening_execution_sweep.py:197`, fixture at `:38-42`, wired at `:62`. `_recent_after_hours_fill_time()` returns `datetime.now(timezone.utc) - timedelta(hours=3)`; the assertion is `entry["date"] == datetime.now(ET).strftime("%Y-%m-%d")`. The handler derives that field via `journal_sync.py:961 → et_session_date(ib_time)` (`handlers/base.py:50-63`) — the ET calendar date OF THE FILL, not of now. Any run where ET local time is 00:00–02:59 puts `now-3h` on the previous ET day and the assertion fails; the window widens on the spring-forward Sunday. The fixture docstring cites the window-relative house rule, but the rule was applied to the input and not to the expectation. Money path: `date` drives the journal basis cutoff and same-day P&L (T-023). **AC:** bind `fill_time = _recent_after_hours_fill_time()` in the test, pass it as `when=`, and assert `entry["date"] == et_session_date(fill_time)`; or patch `_now_utc` to a pinned 20:30 ET instant on a resolved trading day, as the sibling `test_cash_flow_sync_timeout_retry_budget.py:118-121` already does. Prove: `TZ=America/New_York faketime '01:30' pytest scripts/tests/test_monitor_daemon/test_evening_execution_sweep.py`.
- **T-064 [P1] The subprocess order-lane test never asserts its own precondition, so a broken reservation passes as green.** `scripts/api/tests/test_subprocess_order_lane.py:56-60`, repeated at `:143-147`. The saturation helper loops `for _ in range(200): if _active_subprocesses >= count: break; await asyncio.sleep(0.01)` and then returns — with no assertion that the general lane was ever saturated. On a cold or contended runner, spawning 2–3 CPython interpreters exceeds the fixed 2 s budget; the loop falls through silently, the subsequent "order lane is admitted while scans saturate the pool" assertion passes because there was spare capacity all along, and `RESERVED_ORDER_SLOTS` could be entirely broken while the test stays green. Money path: order-placement admission. **AC:** replace the fall-through with `assert subprocess_mod._active_subprocesses >= count, "lane never saturated"`, or gate on an `asyncio.Event` the stub sets. Prove by stubbing `RESERVED_ORDER_SLOTS = 0` — the test must go red.

### P2 — fragility / structure

- **T-065 [P2] `run_script_raw` and `run_module` release the concurrency slot on cancellation without killing the child, so `MAX_CONCURRENT_SUBPROCESSES` stops bounding anything.** `scripts/api/subprocess.py:290-294` added `except asyncio.CancelledError` to `run_script` only; `run_script_raw` (`:332-365`) and `run_module` (`:376-423`) have just `finally: _release_subprocess_slot()` at `:365` and `:423`. On a cancelled request (Next.js client abort, `radonFetch` timeout, FastAPI shutdown) the slot returns while the child keeps running, so `_active_subprocesses` under-counts live processes — the exact FD / IB-client-id / quota exhaustion REL-023 exists to prevent — and degrades the reserved order lane, since a "free" order slot can already be occupied by an orphan. For `run_module` the orphan is typically `trade_blotter.flex_query`, which keeps spending Flex `SendRequest`s against a token under a 24h–168h throttle embargo. Coverage: `grep -rn "CancelledError" scripts/tests scripts/api/tests tests` → the only subprocess-cancellation tests are `scripts/api/tests/test_ib_gateway_subprocess_cleanup.py:56-86`, covering `ib_gateway._docker_compose` / `_run_shell`, not `subprocess.run_script*`. `scripts/tests/test_api_subprocess.py` (26 tests) has no cancellation case and never asserts on `_active_subprocesses`. **AC:** `TestSlotAccountingUnderCancellation` in `test_subprocess_order_lane.py`, patterned on the `_BlockingProcess`/`_factory` doubles — for each of `run_script`, `run_script_raw`, `run_module`: start as a task, `await proc.started.wait()`, `task.cancel()`, `pytest.raises(asyncio.CancelledError)`, then assert BOTH `proc.killed is True` AND `_active_subprocesses == 0`. `run_script` passes today (currently-unpinned regression); the other two go red.
- **T-066 [P2] `admin-page-gate.test.ts` guards the destructive operator control plane entirely by grepping route source.** `web/tests/admin-page-gate.test.ts:26-46`; all three `it` blocks are `readFileSync(...).toContain(...)`. `:39-46` is the only delta test covering `app/api/admin/ib/restart/route.ts`, `app/api/admin/stack/restart/route.ts` and `app/api/admin/services/[unit]/[action]/route.ts`, and asserts nothing beyond the presence of `requireRouteAccess` and `operatorOnly: true`. Defect that stays green: change the live call in `admin/ib/restart/route.ts` to `operatorOnly: false` while leaving the phrase in the header comment (the pattern already present at `preferences/route.ts:16`) — any signed-in demo-trial user can then restart IB Gateway, which per `docs/ib-gateway-recovery.md` triggers a 2FA push lock. **AC:** invoke each of the six handlers with a mocked `requireRouteAccess`; assert the deny path returns without touching the subprocess/systemd call, and that `mock.calls[0][1].operatorOnly === true`. Flipping it to `false` in source must go red.
- **T-067 [P2] `security-remediation-supplemental-routes.test.ts` counts timed fetches instead of requiring all fetches to be timed.** `web/tests/security-remediation-supplemental-routes.test.ts:14-64`; 7 of 8 `it` blocks are source-grep (only `:66-82` executes code). `:46-51` asserts `info.match(/signal: AbortSignal\.timeout\(PROVIDER_TIMEOUT_MS\)/g)` has length 5 — a count of timed fetches, not of total fetches. Adding a sixth upstream `fetch()` to `web/app/api/ticker/info/route.ts` with no `signal` leaves the count at 5 → green, and the route regains the unbounded-upstream hang the remediation closed. `:39-44` inverts the same flaw: it slices `generate_regime_share.py` between `def build_preview` and `# ── Main` and asserts no `<script>`; renaming the `# ── Main` marker shrinks the slice toward empty and the CSP check evaporates without failing. **AC:** assert the complement — count every `fetch(` call site and require the `AbortSignal.timeout` count to equal it; better, stub `globalThis.fetch`, call `GET`, and assert every recorded call received a non-null `init.signal`. For the CSP slice, assert `preview.length > 200` before the `not.toContain` checks.
- **T-068 [P2] `ws-trust-fail-closed.test.ts` pins the relay's production network boundary with a systemd substring that a comment or a later override satisfies.** `web/tests/ws-trust-fail-closed.test.ts:33-49`, 2 of 4 `it` blocks. `:33-40` asserts `toContain("Environment=WS_BIND_HOST=127.0.0.1")` against `cloud/services/radon-relay.service`. systemd uses `#` line comments and last-assignment-wins, so either `# Environment=WS_BIND_HOST=127.0.0.1` or a later `Environment=WS_BIND_HOST=0.0.0.0` satisfies `toContain` while the relay binds all interfaces with `RADON_WS_REQUIRE_CLERK` off. `:42-49` asserts `scripts/ib_realtime_server.js` contains the literal `new URL(req.url || "/", "http://relay.invalid")` — a Host-header-poisoning fix that passes if the string survives only in a comment while the live `upgrade` handler reverts to host-derived parsing in a different template form (the negative grep at `:48` bans one exact spelling). **AC:** parse the unit into key/value pairs (strip `#`, take the last assignment per key) and assert `WS_BIND_HOST === "127.0.0.1"` and `RADON_WS_REQUIRE_CLERK === "1"`. For the URL fix, export the upgrade-target parser from `scripts/lib/wsTrust.js` (already imported behaviorally at `:8-12`) and call it with `{ url: "/ws?x=1", headers: { host: "evil.example" } }`, asserting the resolved origin is `relay.invalid`.
- **T-069 [P2] The COR1M crash-trigger threshold is guarded only as display copy; moving it from 60 to 50 keeps the file green.** `web/tests/regime-market-closed-values.test.ts:47-49` and `:97-107`. The threshold lives at `web/lib/regimeLiveStrip.ts:55` (`args.correlation > 60`). The only behavioral case is `:99-106` with `correlation: 72` → `{correlationMet: true, triggered: true}`; the boundary is never probed (no 59 / 60 / 60.1, no `correlationMet: false`, no `liveCorrelation: false`). The number 60 is pinned only by `expect(panelSource).toContain("COR1M > 60")` at `:48` — a grep for the JSX LABEL at `RegimePanel.tsx:733`. Mutation that keeps everything green: change `regimeLiveStrip.ts:55` to `> 50`. 72 > 50 still holds, the label literal is untouched, and the CRI crash trigger fires ~10 correlation points early while the UI still reads "COR1M > 60". `:29-45` compounds it — six of seven assertions are `not.toContain` on removed identifiers, which pass permanently and fire only on a harmless rename. **AC:** table-drive `resolveCrashTriggerState` over 59.9 / 60 / 60.1, plus `liveCorrelation: false` falling through to `cachedCorrelationMet`, plus `spxBelowMa` and `realizedVolMet` each independently suppressing `triggered`. Derive the panel label from the same exported constant instead of grepping the literal. `> 60` → `> 50` must go red.
- **T-070 [P2] `ticker-depth-props-wiring.test.ts` verifies live L2 delivery entirely by regex over three `.tsx` files and one CSS file.** `web/tests/ticker-depth-props-wiring.test.ts:8-30`, all 10 assertions. `:11` requires the exact source spelling `depths={activeSection === "ticker-detail" ? depths : undefined}`; `:22` requires `const depths = depthsProp ?? getDepths()` character-for-character. Nothing renders. Defect that stays green: `WorkspaceShell` passes `depths` correctly but `TickerWorkspace` is wrapped in `React.memo` with a comparator omitting `depths` — wired in source, never re-renders, montage frozen at its first book. Conversely, extracting the ternary condition into a named `const isTickerDetail` (a pure-readability refactor this repo's own style rules ask for) turns the file red with zero behavior change. `:28` is a CSS regex over `globals.css` asserting `flex: 1 1 auto` inside a nested block — breaks on whitespace, passes if a later declaration overrides it. **AC:** render `WorkspaceSections`/`TickerWorkspace` in jsdom with a stub `DepthMontage` echoing `Object.keys(depths)`; assert the focused symbol's book arrives, then re-render with a mutated `depths` and assert the child re-rendered. Deleting the prop at the call site must go red; renaming the intermediate variable must not.
- **T-071 [P2] `regime-tab-routes.test.tsx` accepts a `/regime/*` page that 404s in production.** `web/tests/regime-tab-routes.test.tsx:61-87` (15 route pages × 2 assertions), plus `:33-57` and `:392-404`. The `describe.each` asserts only `existsSync(path)` and that the file text matches `/import\s+WorkspaceShell/` and `/section=["']regime["']/`. A page that imports `WorkspaceShell`, keeps `section="regime"` in a dead branch, and actually returns `null` or `notFound()` passes both. `:33-44` and `:46-57` pin the two `redirect()` targets by regex rather than calling the default export with `redirect` mocked — even though the file already mocks `next/navigation` at `:93-98`. `:392-404` asserts the ABSENCE of a `useState` spelling: passes forever, breaks on formatting. **AC:** import each page module and render it with `WorkspaceShell` stubbed, asserting the stub received `section="regime"` and the expected `tab`; for the two redirect pages invoke the default export and assert `redirect` was called with `/regime/cri` and `/scanner?mode=vol-cone`. A page body returning `null` must go red.
- **T-072 [P2] The web coverage ratchet is blind to 664 new hook lines and 8801 new component lines; the `use*.ts` exclude rationale is provably stale. NEEDS A MAINTAINER THRESHOLD DECISION (T-050 rule — report, never silently move).** `vitest.config.ts:56` excludes `web/lib/use*.ts` with the comment "React hooks need jsdom". That is no longer true: `web/tests/vixcor-hook.test.tsx:2` carries `@vitest-environment jsdom` and uses `renderHook`, and it runs in the gate today; `grep -rhoE "@/lib/(use[A-Za-z]+)" web/tests | sort -u` → 49 distinct hooks imported by COLLECTED tests. Delta churn inside the exclude: `git diff --numstat d681d247..HEAD -- 'web/lib/use*.ts'` → +664/-207, including new `useNewsfeedPosts.ts` (+169), `useVixcor.ts`, `useSkew2d.ts`, `useVolCone.ts`, `useCor.ts`; total excluded surface 70 files / 6415 LOC. Same class, larger: coverage `include` (`vitest.config.ts:46-52`) omits `web/components/**` entirely, and the delta added 37 new components / 8801 lines there, exercised by 173 collected `*.test.tsx` files that contribute nothing to the 75/78/65 thresholds. SWR fetch keys, refresh cadence and stale/error branches for every indicator hook are therefore neither covered nor missing, so the ratchet structurally cannot see a regression in them. **AC:** replace the `web/lib/use*.ts` blanket exclude with an explicit per-file list for hooks that genuinely have no test, and add `web/components/**/*.tsx` to coverage `include`; then re-measure and re-baseline `thresholds` to ~2 pts under the new honest figure — the same discipline as the pytest T-050 rebase, and the same rule: if the honest figure lands below a current threshold, report it, do not lower the gate silently. Prove: `--coverage` prints a non-zero statement count for `web/lib/useVixcor.ts`, and deleting its error branch drops `lines` below threshold.
- **T-073 [P2] 15 new Playwright specs landed and the CI curated subset did not move.** `.github/workflows/ci.yml:304-316` lists exactly 9 specs; `git show d681d247:.github/workflows/ci.yml` shows the identical 9. New in the delta: `web/e2e/{act-ticket-scroll,bpi-tab,chain-expiry-preserves-builder,cor-tab,dashboard-panel-chrome,mobile-newsfeed-layout,mobile-stop-order,modify-order-correlation-risk,performance-twr-payload,portfolio-request-cadence,portfolio-same-day-equity-pnl,skew2d-tab,stop-order-desktop,vixcor-tab,vol-cone-tab}.spec.ts` (137 tracked specs total). Sharpest instance: `web/e2e/portfolio-same-day-equity-pnl.spec.ts` is the equity sibling of `e2e/portfolio-same-day-combo-pnl.spec.ts`, which IS curated at `ci.yml:313` — same money path, not added. Blast radius is bounded: every new indicator has gated unit coverage (`web/tests/{vixcor,vol-cone,skew2d,cor}-{api,panel,hook}.test.*`, `scripts/tests/test_{vixcor,skew2d,cor,equibles_*}.py`, `web/tests/same-day-stock-pnl.test.ts`, `web/tests/stop-order.test.ts`), so this is lost browser-layer confirmation, not unguarded surface. Note the job is non-gating and absent from `deploy.needs` (`ci.yml:251`, `:327`), so even the curated 9 gate nothing today. **AC:** add `e2e/portfolio-same-day-equity-pnl.spec.ts` at minimum; add `e2e-financial-smoke` to `deploy.needs` after the documented run of green observations. Breaking an assertion in the added spec must red the job.
- **T-074 [P2] `test_app_preferences` lets a 50 ms sleep decide which side of a generation race wins.** `scripts/tests/test_app_preferences.py:597-598`: `worker.start(); time.sleep(0.05); app_preferences.set_value("RADON_MAX_ORDER_QTY", 50, ...)`. `refresh_snapshot` captures `generation_at_start = _WRITE_GENERATION` on its first line (`scripts/app_preferences.py:363-364`) and only skips the adopt if the generation moved (`:386-388`). If the worker thread has not entered `refresh_snapshot` within 50 ms (thread-start latency, GIL contention), it captures the POST-write generation, adopts the stale `500` row, and `assert order_limits.max_order_qty() == 50` fails. Money path: order quantity cap. **AC:** add `entered = threading.Event()`, set it inside `_slow_query` immediately before `released.wait(2.0)`, and replace the sleep with `assert entered.wait(5)`. Prove: 20 consecutive runs under CPU stress, 20/20.
- **T-075 [P2] `portfolio-request-cadence.spec.ts` cannot distinguish "one owner" from "slow runner".** `web/e2e/portfolio-request-cadence.spec.ts:65` waits `page.waitForTimeout(1_200)`, then `:69` asserts `expect(portfolioReads).toBeLessThanOrEqual(2)`. An upper bound taken after a fixed window: a shell regression issuing a third `/api/portfolio` read at t=1.3 s is green, while a `next dev` remount storm inside 1.2 s is red. **AC:** replace with an explicit settle signal — `waitForLoadState("networkidle")` plus `expect.poll(() => portfolioReads).toBe(1)` (or `<=2` with a stated timeout) — then re-assert the count is unchanged after the palette interaction, which the test already does correctly at `:72-77`. Prove by injecting a 3rd read at 1.5 s and confirming red.
- **T-076 [P2] `test_checkpoint`'s fixed kill budget rots into a no-op on a cold runner.** `scripts/tests/test_checkpoint.py:168-172` loops `for kill_after in (0.05, 0.1, 0.15)`, `Popen`, `time.sleep(kill_after)`, SIGKILL. On any runner where CPython startup plus the worker's imports exceed 150 ms (routine on cold CI), all three kills land before a single `record_finding` is written; the final clean run then trivially produces 200 unique findings and the test passes — never exercising the mid-run resume/dedupe path it exists to protect, and never going red about it. **AC:** make the kill point data-driven — the worker writes a sentinel after N findings, the test kills on its appearance with a bounded wait and `pytest.fail` if it never appears — and assert `0 < len(read_findings(job_dir)) < 200` after each kill so a no-op kill is caught.
- **T-077 [P2] `mobile-newsfeed-layout.spec.ts` hangs its `beforeEach` entry point and four `evaluate` lookups on CSS classes, in a file that already uses testids elsewhere.** `web/e2e/mobile-newsfeed-layout.spec.ts:84` (`page.locator("li.news-feed-item").first()`) sits in `beforeEach`, repeated at `:274`, `:284`, `:308`, `:350`; the bodies reach deeper — `:88` `locator("div.news-feed-tags")`, `:109-112` `querySelector(".news-feed-footer"/".news-feed-link-pill"/".news-feed-timestamp"/".star-toggle")`, `:328-329`, `:363`. Renaming `news-feed-item` or changing the `li` wrapper in any CSS refactor makes EVERY test in the file block in `beforeEach` for the full Playwright timeout instead of failing fast, and the non-null-asserted `querySelector(...)!` calls throw an opaque "null is not an object" inside `evaluate` rather than naming the missing element. The convention already exists in this same file at `:258` (`[data-testid=...]`). **AC:** add `data-testid="news-feed-item"` / `-tags` / `-footer` to the components and switch the entry-point wait and the `evaluate` lookups to `getByTestId`. Prove by renaming the class locally: the suite must fail in under 5 s with a named locator, not time out.
- **T-078 [P2] `vixcor-tab.spec.ts` asserts that a specific live market episode is still open.** `web/e2e/vixcor-tab.spec.ts:327-328`: `// The live 2026-08 episode is open and unresolved.` then `expect(section.locator('[data-testid="vixcor-episode-open"]')).toHaveCount(1)` — in the LIVE half of the spec (real `/api/vixcor`, per the header at `:8-12`). The moment VIX/COR3M recouple, the episode closes, the count goes to 0, and the assertion fails on correct production data. There is no date at which it is expected to keep holding. Mitigated only by the file being excluded from CI (`playwright.no-server.config.ts`), so it rots silently as a local-only spec that no longer runs green. **AC:** narrow the live assertion to shape invariants that survive a regime change — chart paths render, `vixcor-base-rate` visible, no `NaN` in any `d` (all already present at `:326`, `:329-334`) — and drop the open-episode count, which the pinned-fixture half already covers deterministically at `:263`.

### Not findings (checked, clean)

- **Sibling recurrence of the previous-close wall-clock class** (`e585a9f7`, `54ba227c`): none. `web/tests/market-session.test.ts:12-33` and `web/tests/bpi-staleness.test.ts:16-18` use hardcoded instants; `web/e2e/bpi-tab.spec.ts:84` uses `page.clock.install`; `web/tests/cri-staleness-weekend.test.ts:13-31` passes `currentMarketOpen`/`lastCompletedSession` explicitly. T-063 is a distinct class (fixture instant vs `now` inside the same test).
- **vitest collection maps 1:1** — 626 tracked files under the four include dirs vs 626 collected; `comm -23` and `comm -13` both empty. No `.test.js` under `web/tests`, no `.test.ts` under `scripts/lib`, no tests under `site/components/**`.
- **pytest reaches every `test_*.py`** outside the known `scripts/test_ib_realtime.py` (T-054). The new `scripts/tests/test_monitor_daemon` and `scripts/tests/test_watchdog` subpackages recurse correctly.
- **New indicator surface is inside the coverage numerator** — `web/app/api/{cor,vixcor,vol-cone,skew2d,equibles-*}/route.ts` are all matched by `web/app/api/**/*.ts` (`vitest.config.ts:50`) and no `exclude` glob; new `scripts/*.py` are under `--cov=scripts` and the pyproject `omit` lists test files only.
- Pre-existing unreached files, unchanged and out of delta: `site/e2e/{branding,surface-preview,theme-toggle}.spec.ts` (only `web/playwright.site.config.ts` targets them, no workflow invokes it) and `cloud/scripts/tests/test_preflight.sh` (`TEST_AUDIT.md:43`).

### Runner-clone hygiene (operator note)

This audit began with the runner clone dirty: modified `scripts/utils/uw_cache.py`,
`scripts/tests/test_uw_cache.py`, `scripts/tests/test_host_metrics_sampler.py`, plus
an untracked `scripts/tests/test_watchdog/test_disk_check.py` importing a
`scripts.watchdog.disk` module that does not exist — which aborted pytest at
COLLECTION (exit 2, `90 deselected, 1 error`) before a single test ran. None of it
exists on any `reliability/*` branch, so it is orphaned WIP from a capped or killed
prior run. Parked recoverably, not discarded:
`git stash list` → `stash@{0}` "testing-weekend-2026-08-16: parked pre-existing
runner-clone WIP". All gate counts above are from the clean tree. `git stash pop`
on the runner recovers it.

## Delta audit 2026-08-22

Range `71de8a33..4985a7f8` — 167 commits, 565 files, +35120/-24351
(474 code files once docs/context/site/data are excluded). 151 test
files changed, 29 new `test_*.py` + 39 new `web/tests/*` + 5 new
`web/e2e/*.spec.ts`. The range includes last weekend's own remediation
(T-055…T-079, merged via PR #38) and the reliability loop's REL-027…REL-038
source commits; both were re-triaged as ordinary delta, not exempted.
New findings continue the numbering at **T-080**. PART A (§1–§10) and the
2026-08-16 delta are untouched.

### Standing sweeps

**Gates from the repo root, serial (clean tree, HEAD 4985a7f8, darwin runner):**

| Gate | Run 1 | Run 2 |
|---|---|---|
| `python3.13 -m pytest` (= CI paths; 7307 collected, 90 deselected) | **1 FAILED**, 7215 passed, 1 skipped (159.7s) | 7216 passed, 1 skipped, 90 deselected (147.0s) |
| `npx vitest run` (root config, 672 files) | 7036 passed / 0 failed (52.1s) | 7036 passed / 0 failed (52s) |
| `python3.13 -m pytest cloud/tests -q` | **12 failed**, 910 passed, 4 skipped (131.0s) | 12 failed, 910 passed, 4 skipped (133.4s); `FAILED` list byte-identical |

- The pytest red is `scripts/tests/test_run_portfolio_refresh_retry.py::TestDeployWindowRetry::test_server_up_after_first_refusal_succeeds`, isolated 3×: `7 passed` ×3. It is a real sleep-as-sync race, filed as **T-089**, not called flake.
- The 12 cloud reds are the 10 known darwin `sha256sum` reds (NEW_FINDINGS 2026-08-17, list byte-identical) **plus 2 new** from `574d7ce1` (#67) — filed as **T-088**. The darwin baseline is now `12 failed, 910 passed, 4 skipped`.

**Determinism scope note:** 151 of the delta's files are tests (39 new
`web/tests`, 29 new python modules, most of the rest edited), so the
"re-run only delta-touched files 3×" rule again collapsed into full-gate
runs; two serial rounds are what the table records, plus the 3× isolation
run of the one red file. Gates were NOT run concurrently with each other.

**Coverage-ratchet honesty — clean.** `git diff 71de8a33..HEAD -- vitest.config.ts pyproject.toml .github/workflows/ci.yml`: the only threshold change is functions 78→71 (T-072, reported with measurement in `TEST_LOG.md`); `web/components/**/*.tsx` was ADDED to the coverage include and the `web/lib/use*.ts` exclude was REMOVED (both widen measurement); `.pi/tests` added to `test.include` (T-058). `pyproject.toml` has zero diff; `--cov-fail-under=56` unchanged (`ci.yml:159`); vitest `--exclude` flags unchanged (`ci.yml:105-107`). No new `omit`/`exclude`.

**New skips — none.** 35,661 added lines parsed (python3.13 over the patch) for `test|it|describe.(skip|only|todo|fixme)`, `pytest.mark.skip|skipif|xfail`, `pytest.skip(`: zero hits. No `.only(`.

**CI workflow delta — 5 lines.** `ci.yml:313-317` curated five more specs (T-073, `d1462b62`). No job invocation, timeout, `continue-on-error`, or `deploy.needs` changed; the Playwright job is still `(non-gating)` and absent from `deploy.needs` (`ci.yml:332`). Over the last 12 `main` runs it is 11 success / 1 cancelled — the "observe green first" precondition for promotion is now met (see T-090).

**Gate reach — clean.** All 39 new `web/tests/*`, 8 new `site/lib/*.test.ts`, `scripts/lib/depthBudget.test.js` match `vitest.config.ts:16-26` (there is no `web/vitest.config.*`; root is the only config). All 29 new `test_*.py` sit under `scripts/tests` (incl. the `test_monitor_daemon/` and `test_watchdog/` packages), `scripts/api/tests`, `tests/`, or `cloud/tests`; `--collect-only` over the CI paths → 7217/7307, no collection errors.

### Re-triage of the standing NEW_FINDINGS items

- **`next start` Day Move divergence** — superseded, not resolved. `d45849d7` / `f2fbe0a7` rewrote the Day Move path (`MetricCards.tsx:636-656`, new `web/lib/ibDailyPnlSession.ts`) and `web/e2e/day-move-ib-daily-pnl.spec.ts` is untouched in the delta and still held out of CI. The new code introduces its own gap on the per-position column — **T-083**.
- **`performance-twr-payload.spec.ts`** — fixed 2026-08-17 (T-079, `3b5dd742`); still not curated (candidate under T-090).
- **E2E testid backlog** — still open; three NEW indicator specs and the scanner strip spec landed on CSS classes where `aria-current` / a testid exists — **T-091**.
- **darwin `sha256sum` cloud reds** — grew from 10 to 12 (**T-088**).
- **`next dev` in the CI Playwright container** — unchanged, infra-open.

---

### P0 — money-losing gaps

- **T-080 [P0] The combo worst-case-loss cap is a hardcoded $10M and nothing is tested between the $250k notional cap and it; a 75-lot naked strangle risking $9.7M now clears every server-side limit.** `scripts/order_limits.py:41` (`_MAX_COMBO_LOSS_DOLLARS = 10_000_000.0`, a module literal — not an `app_preferences` value like every other cap at `:45-63`), `:158-175` (`combo_max_loss`), `:237` (the only check). `38ccbcbf` moved assignment risk out of `order_notional` (premium-only now) onto this constant; `9af245ef` nets credit into it. Tests `scripts/tests/test_order_limits.py:211` ($65M refused), `:244` ($11M refused), `:172` ($2M passes) pin the cap only from far above and below. Evidence: 75-lot SPY 600P/700C @ −$0.20 → `check_order_limits` = `None` (max_loss 9,748,500) at HEAD; the same params at `71de8a33` → `ORDER_NOTIONAL_LIMIT`. The $10M figure appears in no doc or audit. A remediator who bumps the constant to $50M, or drops the `combo_max_loss` branch for non-strangle shapes, keeps the file green. Introduced by 38ccbcbf. **AC:** red — `test_short_strangle_under_ten_million_is_still_refused` (75-lot and 40-lot strangles → `ORDER_MAX_LOSS_LIMIT`) plus a boundary test at exactly cap±1 so the constant cannot drift; green — cap reads `RADON_MAX_COMBO_LOSS` from `app_preferences` with a default tied to bankroll (or the $10M is an explicit, documented policy decision by the operator, in which case only the boundary test is required). Either way the $250k..$10M gap must be covered.

### P1 — correctness gaps

- **T-081 [P1] `load_flows_from_turso` — the Flex-outage flows source — is mocked in every test and double-counts a date that carries both a backfilled `deposit` row and a builder-mirrored `external` row.** `scripts/perf_twr_builder.py:549-568` sums every `flow_type` row per date; writers use distinct PK `flow_type` values (`scripts/migrate_perf_twr.py:140-150,178` → `deposit|withdrawal|acats`; `perf_twr_builder.py:1497-1508,1541` → `"external"`; PK `scripts/db/migrations/0035_perf_twr.sql:34`). `grep -rn load_flows_from_turso tests scripts/tests | grep -v monkeypatch.setattr` → no hits (`tests/test_perf_twr_flows_turso_fallback.py:56-121`, `test_perf_twr_incident_end_to_end.py:48`, `test_perf_twr_flex_single_request.py:64,77,97` all stub it). Stubbing `_query_turso` with the two rows returns `{'2026-01-13': 160014.26}` for an $80,007.13 deposit. Introduced by 64f9fd28. **AC:** red — sqlite with migration 0035, insert a `deposit` and an `external` row same date/amount, `load_flows_from_turso()` == 80007.13 (fails today); green — single canonical `flow_type` per date (precedence or shared classifier), test stays.
- **T-082 [P1] `wait_for_streaming_data` has only an all-ready positive test; the account predicate is `dailyPnL OR unrealizedPnL` while Phase 6 reads `dailyPnL` with no fallback wait.** `scripts/ib_sync.py:848-875`, call `:1614`, Phase 6 `:1668-1679` ("No fallback sleep"); `scripts/clients/ib_client.py:207-213` `pnl_is_ready`. Only test: `scripts/tests/test_ib_event_waits.py:230-256` (every field valid → `True`). Deleting the ticker loop or the `pnl_requests` loop from `_ready` stays green; when IB streams `unrealizedPnL` a tick before `dailyPnL` the wait returns early and `account_summary.daily_pnl` lands `None` where the old 2.5s floor caught it. Introduced by 1979a0d5. **AC:** red — ticker with no quote → `False`; `dailyPnL=nan, unrealizedPnL=1.0` → not ready (fails today); green — `_ready` requires a valid `dailyPnL` for the account object (or the existing 8s `get_account_pnl` wait stays on the main path).
- **T-083 [P1] The non-trading-day gate covers the Day P&L card but not the per-position Day P&L column, and the regression fixtures null the field that would expose it.** Gated: `web/components/MetricCards.tsx:636-656`. Ungated consumers of the same re-baselined IB number: `web/components/PositionTable.tsx:396`, `web/lib/positionUtils.ts:563,630`; producer `scripts/ib_sync.py:583-585` writes `reqPnLSingle.dailyPnL` with no trading-day gate. `grep -n "sessionPositions\|isIbDailyPnlCurrent" PositionTable.tsx positionUtils.ts` → 0. `web/tests/day-pnl-non-trading-day.test.tsx:53,78` set `ib_daily_pnl: null`. On a Saturday the table shows a five-figure per-row "Day P&L" under a card reading MARKET CLOSED. Introduced by d45849d7 / f2fbe0a7. **AC:** red — render `PositionTable` at `2026-08-22T21:23Z` with an equity option carrying `ib_daily_pnl: 13951.76`, row Day P&L must be blank (fails today); green — route through `currentIbDailyPnl` (or gate at write time), keep the crypto carve-out.
- **T-084 [P1] The `iei_hyg_history` / `credit_spread_history` idempotent-upsert tests execute a dead SQL constant, not the production writer.** `scripts/db/writer.py:972-981` `IEI_HYG_UPSERT_SQL` is unused by `upsert_iei_hyg_rows` (`:995-1021`, inline INSERT at `:1012-1019`); `:925` `CREDIT_SPREAD_UPSERT_SQL` unused by `upsert_credit_spread_rows` (`:961`). `scripts/tests/test_iei_hyg.py:243-249` and `test_credit_spread.py:255-264` execute the constants; `TestPersistResult` in both stubs the real writer. Replacing both `upsert_*_rows` with a raising stub → `12 passed`. A column swap or dropped `ON CONFLICT` in the chunked INSERT ships to Turso daily with no signal. Introduced by 4985a7f8, ae033f86. **AC:** red — swap the two floats in `_iei_hyg_params` with a recording-sqlite test on the REAL `upsert_*_rows` (pattern: `test_ivrank.py:339-378`), called twice for one date; green — correct columns, delete the dead constants.
- **T-085 [P1] `order-dedup-visibility.test.tsx` asserts the suppressed-submit contract on six order surfaces by counting `placeOrderFeedback(` in source.** `web/tests/order-dedup-visibility.test.tsx:70-88` (`calls(read(rel)) >= 1|2`); only `MobileOrderTicket` is rendered (`:180-205`). A call whose result is discarded next to a hardcoded success toast keeps the count. Desktop single-leg and combo tickets (`OrderTab.tsx:565-566,918-919`, `SingleLegOrderTicket.tsx:260`, `lib/chat.ts:281`) have no behavioral check. T-060/T-070 class, now on the order path. Introduced by b3667d73. **AC:** per surface, render with a fetch stub returning `{deduplicated:true, orderId:42}` and assert a `warning`-toned notification matching `/NOT sent again/`; red via the discarded-result mutation.
- **T-086 [P1] The `find_stale_rows` keyset-paging test is verified by a fake DB that implements the keyset itself.** `scripts/rehash_position_execution_facts.py:88-94` (row-value `WHERE (account_id, exec_id, revision) > (?, ?, ?)`); `scripts/tests/test_position_execution_fact_tolerated_hash.py:117-153` `_TieShufflingDb.execute` ignores the WHERE text and re-implements tuple `>` on the PK triple. Mutation to `account_id >= ? AND exec_id > ? AND revision >= ?` (skips every row of a second account sorting below the cursor) → `1 passed`. Introduced by ac556e2c. **AC:** red — in-memory sqlite with migration 0037, 25 rows across 2 accounts with tied `ingested_at`, `PAGE_SIZE=10`, all 25 found fails under the AND-form; green with the row-value comparison.
- **T-087 [P1] `relay-blackout-honesty.test.ts` "same source" case asserts on a hand-mirrored relay loop, not the relay.** `web/tests/relay-blackout-honesty.test.ts:58-150` (`runRelayLoop` reimplements the `last_tick_at`/`tick_age_secs` payload) and `:210-217`; real loop `scripts/ib_realtime_server.js:2751-2785` is never imported. The R-061 bug (`tick_age_secs` from `freshness.lastTickAt`, `last_tick_at` from `lastTickTimestamp`) can be reintroduced green. Introduced by 757107bc. **AC:** extract `buildRelayHealthDetail(now, lastTickTimestamp, freshness)` into `scripts/lib/staleDataMachine.js`, call it from the relay, assert on its output; red with the builder computing age from `freshness.lastTickAt`.

### P2 — fragility / structure

- **T-088 [P2] `cloud/tests/test_sync_scheduled_units.py` ships no `sha256sum` shim, so the darwin cloud baseline grew 10→12 and every weekend run must hand-diff a longer known-red list.** `cloud/scripts/deploy-root-helper.sh:100` `SHA256SUM="${RADON_TEST_SHA256SUM:-$(command -v sha256sum)}"` is empty on macOS → `:858` bash 127 `: command not found`; `test_sync_installs_allowlisted_unit_and_reloads_only` and `test_sync_is_idempotent_when_live_already_matches` red. The sibling `cloud/tests/test_install_units.py:53-75` already ships a portable shim and exports `RADON_TEST_SHA256SUM` (with it exported: `14 passed`). Introduced by 574d7ce1. **AC:** red on darwin today; green — reuse the shim fixture (and, per the 2026-08-17 note, the honest fix for the other 10 is a portable digest, not a skip).
- **T-089 [P2] `test_server_up_after_first_refusal_succeeds` lets a 0.5s `time.sleep` decide whether the first POST is refused, and reds the full pytest gate under load.** `scripts/tests/test_run_portfolio_refresh_retry.py:95-109`: `start_late()` sleeps 0.5s then binds; under full-suite load the bash script's first attempt landed after the bind, the run succeeded with no retry, and `assert "retry" in …` failed (gate run 1 this audit; 3/3 green in isolation). T-074 class. Pre-existing file (`f8b9cd49`), surfaced by this run's gate. **AC:** red — reproduce by delaying the script start (e.g. `sleep 1` wrapper) → assertion fails deterministically; green — bind after observing the script's first attempt (a refusing listener that closes, or a socket the test holds and releases on the first connect), no wall-clock sleep.
- **T-090 [P2] Delta to T-073: the combo-modify close-out P&L browser regression landed outside the curated subset, plus five un-curated indicator/mobile specs; the Playwright job is green 11/12 on `main` and still not in `deploy.needs`.** `web/e2e/modify-combo-order.spec.ts:23-57` (new test for `c3e7f2b8`, `grep -c modify-combo-order ci.yml` → 0) is money-path; `credit-spread-tab`, `iei-hyg-tab`, `ivrank-tab`, `mobile-scanner-mode-tabs-overflow`, `mobile-vol-cone-table` are not. `performance-twr-payload.spec.ts` (T-079) is green under `next start` and also uncurated. **AC:** curate `modify-combo-order.spec.ts` (+ `performance-twr-payload.spec.ts`) after the T-073 pre-flight under `PLAYWRIGHT_WEBSERVER_CMD="npx next start"`; red by mutating `"$15,000"` at `:55`. Promotion into `deploy.needs` is an operator decision — the observed-green precondition is now met.
- **T-091 [P2] Three new regime-tab specs and the scanner strip spec key tab/shell state on CSS class names where `aria-current` or a testid exists, and pin the mode count as a literal.** `web/e2e/ivrank-tab.spec.ts:152`, `iei-hyg-tab.spec.ts:91`, `credit-spread-tab.spec.ts:80` (`toHaveClass(/active/)` on `.regime-rail__item`) while `RegimeRail.tsx:69` emits `aria-current="page"`; `mobile-scanner-mode-tabs-overflow.spec.ts:59-62` (`.scanner-mode-tabs-shell`, `toHaveCount(7)`) and `web/tests/scanner-mode-tabs.test.tsx:16` (`toHaveLength(7)`); `ScannerModeTabs.tsx:102` has no testid. T-077 class. Introduced by 732c9e5a, 4985a7f8, f05679de. **AC:** red — rename the `active` modifier with no behaviour change, three specs fail; green — `toHaveAttribute("aria-current","page")`, shell testid, count derived from the exported mode list.
- **T-092 [P2] `flex-token-expiry.test.ts` prefers the operator's gitignored runtime file over the committed example and pins exact default values, so it is host-dependent.** `web/tests/flex-token-expiry.test.ts:13-17` (`existsSync(live)` wins), `:33-51` (`reminder_days` `toEqual([30,14,7,1])`, breadcrumb literal). `data/flex_token_config.json` is now ignored (`.gitignore:101`) and mutated at runtime by `scripts/monitor_daemon/handlers/flex_token_check.py`. Introduced by 851f5af3. **AC:** red — live file with `reminder_days: [30, 7]` reds locally while CI is green; green — always read the example, assert shape.
- **T-093 [P2] `llms-txt.test.ts` "matches the published public file" compares against a file the same commit deleted, inside a swallowing try/catch, via `process.cwd()`.** `site/lib/llms-txt.test.ts:47-55`; `site/public/llms.txt` deleted in 5621732c. Passes for two independent reasons, catches nothing. **AC:** red — change `llmsTxt`, test stays green; green — delete the test, or resolve via `import.meta.dirname` and make a missing file a failure.
- **T-094 [P2] Watchdog disk wiring is asserted by `inspect.getsource` substring.** `scripts/tests/test_watchdog/test_disk.py:69-78` (`"check_disk" in source`); `scripts/watchdog/__main__.py:90,170-171` — deleting the `observed_outcomes.append(disk_outcome)` at `:171` keeps the token present and a 96%-full disk never pages (the exact R-069 failure). Introduced by 794293b8. **AC:** monkeypatch `disk_usage` to 96%, capture `dispatch_with_grouping`, run the continuous bucket, assert a P1 `disk` outcome; red by commenting `:171`.
- **T-095 [P2] The `CREDIT_IB_HISTORY_CLIENT_IDS` "unused elsewhere" test hand-builds the taken set and is already false: IEI/HYG reuses `(56, 69)` on an overlapping timer.** `scripts/tests/test_credit_spread.py:356-362`; `scripts/fetch_credit_spread.py:64` and `scripts/fetch_iei_hyg.py:71` both `(56, 69)`; `cloud/services/radon-credit-spread.timer:8,10` (21:45 + 300s) overlaps `radon-iei-hyg.timer:10,12` (21:55 + 300s). An overlap hands IEI/HYG IB error 326 and it silently falls to UW/Yahoo (rule 7). Introduced by ae033f86, 4985a7f8. **AC:** red — import every scheduled `*_CLIENT_IDS` tuple, assert pairwise disjoint (fails today); green — give IEI/HYG its own pair in the documented range.
- **T-096 [P2] Six smaller net-negative assertions in the delta (bundle).** (a) `web/tests/sortable-table-contract.test.ts:46-48` file-level regex — a comment or one sortable table satisfies a file with 7 tables; (b) `web/tests/daily-dark-pool-history-layout.test.ts:11-26` / `dashboard-newsfeed-article-layout.test.tsx:103-111` `readFileSync(globals.css)` first-rule regex, the `@media` override at `globals.css:18070` never read; (c) `web/tests/info-tooltip-hover-grace.test.tsx:99-108` `not.toThrow()` on a post-unmount timer — React 18 never throws, removing the cleanup at `InfoTooltip.tsx:78` passes; (d) `web/tests/fills-modal-sort.test.tsx:28-52` only SYMBOL sorted, a formatted-string `realizedPNL` would `localeCompare` and pass; (e) `scripts/tests/test_flex_error_taxonomy.py:121-124` asserts a docstring substring is absent from `cash_flow_sync.py`; (f) `cloud/tests/test_sync_scheduled_units.py:187-212` substring/`.index()` ordering over `deploy.sh`. **AC:** each replaced by a behavioural assertion (render/sort/invoke) that fails under the named mutation; log per-letter in TEST_LOG.

### Not findings (checked, clean)

- **Money-path fixes with real red-on-revert tests:** c3e7f2b8 (`modify-order-close-pnl.test.tsx:364-398` asserts `$15,000` and the absence of the opening-risk figures), 3b5d05ff (58-structure catalog + CBRS + short-put null), 9af245ef netting (`test_order_limits.py:297-371`), a86bc21c (strangle/straddle, `not.toContain("Risk Reversal")`), 6b89929d (150-lot/1-lot ratio), 4fa19d5d (`TestOutlierGate` + real-sqlite upsert), the Flex 1025/1018/1019 chain (`http.assert_not_called()` under lockout, ladder pins), signals-refresh (real `run_signals_refresh.sh` under bash), T-055/T-061 fixes, REL-038 batch (paging test with `JOURNAL_SCAN_PAGE_ROWS=10`, retention, demo blockade, depth budget, abort, checkpoint, disk metric).
- **No new sleep-as-sync in added test lines** (13 hits, all bounded polls or fake timers); every d45849d7/f2fbe0a7 unit test is `vi.useFakeTimers()+setSystemTime` or passes explicit `Date`s; indicator fixtures are inert; no new market-episode pin (T-078 remediated in range); no new `elapsed < N` budgets; no test reads `.env` or unguarded `os.environ`.
- **`order-dedup-ttl`, `route-authz-runtime` (T-060 class answered), `admin-trading-controls` / `trading-kill-switch`, `mobile-order-ticket-combo-risk-scale`, `day-pnl-non-trading-day` (card half), `uw-counted-fetch`, `test_flex_token_embargo`, `test_exit_orders_guard_durability`, `test_journal_sync::TestExecutionCostBasis`, `test_install_units`** — behavioural, independently derived pins.

### Runner-clone hygiene

Clean at start (`git status --porcelain` empty, no stash, HEAD = `origin/main`). All counts above are from the clean tree.

## Delta audit 2026-08-22 (second pass)

A SECOND, independently-run audit of the same range, `71de8a33..4985a7f8`
(167 commits, 565 files, +35120/-24351; 214 non-test source files changed,
82 new test files added, 207 test files touched). It ran on a different
host and fanned out seven read-only agents over the rubric dimensions.

**This section is additive only.** The first pass's `## Delta audit
2026-08-22` section above and its T-080…T-096 are untouched and remain
authoritative for the findings they cover. Eight findings this pass
produced independently were the SAME defects and have been dropped rather
than renumbered — they are listed under "Converged with the first pass"
below, because two independent readers reaching the same file:line is
evidence worth recording. Numbering continues at **T-097**.
PART A (§1-§10) is untouched; nothing above this line was rewritten.

### Standing sweeps

**Gates x3 from the repo root (clean tree, HEAD `4985a7f8`):**

| Gate | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `python3.13 -m pytest` | 7216 passed, 1 skipped, 90 deselected (199.6s) | **1 FAILED** (T-089), 7215 passed, 1 skipped, 90 deselected (212.8s) | 7216 passed, 1 skipped, 90 deselected (214.2s) |
| `npx vitest run` | 7036 passed / 672 files, 0 failed | 7036 passed / 672 files, 0 failed | 7036 passed / 672 files, 0 failed |
| `python3.13 -m pytest cloud/tests -q` | 34 failed, 888 passed, 4 skipped (102.6s) | 34 failed, 888 passed, 4 skipped (90.0s) | 34 failed, 888 passed, 4 skipped (119.2s) |

**The first pytest round of this run was a FALSE red and the reason is worth
recording.** It reported `107 failed, 7109 passed`. Every one of the 107 was
`async def functions are not natively supported` — the shared runner venv
(`~/radon-weekend/venv`, new this delta via `e013d825`) had `pytest 9.1.1`
but no `pytest-asyncio`. CI installs it explicitly at
`.github/workflows/ci.yml:128`; nothing in the repo declares it. Installing
`pytest-asyncio==1.4.0` + `pytest-cov==7.1.0` into the venv (environment
change only, no repo file touched) took the same tree from 107 red to
**7216 passed, 0 failed**. Filed as **T-119**.

**The 34 cloud failures are PRE-EXISTING and environmental, attributed by
direct comparison, not by count.** A `git worktree` at the previously
audited SHA `71de8a33` run under the identical interpreter produced
`34 failed, 824 passed, 4 skipped`, and `diff` of the sorted `FAILED` lists
between base and HEAD is **empty** — byte-identical. The delta added 64
passing cloud tests and zero failures. Root cause is NOT the `sha256sum`
story recorded on 2026-08-17: it is `/bin/bash` 3.2.57 on darwin, which has
no dynamic file-descriptor allocation, so
`cloud/scripts/bootstrap-control-plane.sh:96` dies with
`exec: {DEPLOY_LOCK_FD}: not found` (exit 127) before any assertion runs.
`cloud/tests/test_bootstrap_control_plane.py:253` invokes `["bash", ...]`,
which resolves to the 3.2 system bash. The recorded macOS baseline of "10
failed" is therefore stale — on a runner without Homebrew bash it is 34.
Filed as **T-118**.

**Determinism scope note.** The rule is "re-run 3x only the test files
touched in the delta." The delta touches 207 test files, so as in the
2026-08-16 audit the scoped re-run collapses into three full-gate runs.
That is what the table records; no narrower scoping happened.

**Coverage-ratchet honesty — CLEAN, and the ratchet moved the honest way.**
`git diff 71de8a33..HEAD -- pyproject.toml` is EMPTY (zero commits in
range), so `[tool.coverage.run] omit` and `--cov-fail-under=56`
(`ci.yml:157-159`) are untouched. `vitest.config.ts` thresholds: lines
75 -> 75, branches 65 -> 65, functions 78 -> 71 — the functions move is the
documented one-time T-072 rebase and nothing else moved down. Coverage
`include` GREW (`+ "web/components/**/*.tsx"`, 12.4k -> 25,807 measured
lines); coverage `exclude` SHRANK (`- "web/lib/use*.ts"`). Zero new exclude
entries anywhere in the diff.

**New skips / `.only` — ZERO.** `git diff 71de8a33..HEAD -U0` over
`*.ts *.tsx *.js *.py` parsed with a `python3.13` heredoc against added
(`^+`) lines only, matching `(describe|test|it|context)\.(skip|only|fixme|todo)`,
`\.only\(`, `pytest\.mark\.(skip|skipif|xfail)`, `pytest\.(skip|xfail)\(`,
`@unittest\.skip`, `xdescribe\(`, `xit\(`. Four hits, all the substring
`xit(` inside `sys.exit(` / `os._exit(` / `_run_with_exit(`. The pattern
finds 23 real markers repo-wide at HEAD and every one is pre-existing —
of the 7 skip-bearing files, 6 have zero commits in the range and the one
that was touched (`cloud/tests/test_deploy_corrections.py`) has no `+` line
containing a skip marker. **No `.only` anywhere in the repo at HEAD.**

**Gate reachability — CLEAN for unit suites.** `npx vitest list` collects
672 files / 7036 cases; disk enumeration less the collected set leaves
exactly 146 files, **all Playwright e2e**. Every new directory in the delta
lands inside an include glob and was confirmed present in the listing:
all 8 new `site/lib/*.test.ts`, both `.pi/tests/*` (T-058's fix is live),
and the new `scripts/lib/depthBudget.test.js` (6 cases collected).
Pytest: CI's `pytest scripts/tests scripts/api/tests scripts/trade_blotter
tests` and a bare root `python3.13 -m pytest` collect a **byte-identical**
420-file set; `pytest cloud/tests` collects all 23 on disk. The 2 files on
disk that neither reaches are `scripts/test_ib_realtime.py` (manual
harness, T-054) and the integration-deselected
`scripts/tests/test_menthorq_integration.py`. All 29 delta-added Python
test files are inside a CI-collected path.

### Re-triage — second-pass additions

The first pass re-triaged the same standing items; recorded here are only the
points where this pass measured something it did not.

- **`e2e/performance-twr-payload.spec.ts` permanently RED — RESOLVED.**
  `3b5dd742` (T-079) rewrote it to the v2 contract with window-relative
  dates (`isoDaysAgo`, `:10-14`). Closed; it remains uncurated pending the
  observe-green-first rule.
- **E2E testid backlog — STILL OPEN, and it did not move where it matters.**
  The delta added 45 `data-testid` attributes across 13 components, but
  every one landed on indicator panels, the newsfeed, or admin
  (`IeiHygPanel` 9, `TradingKillSwitch` 8, `CreditSpreadPanel` 6,
  `DashboardNewsFeed` 6, `IvRankPanel` 5, …). Measured base-vs-HEAD on the
  six money-path surfaces the backlog actually names: `MetricCards` 4 -> 4,
  `SharePnlButton` 0 -> 0, `SingleLegOrderTicket` 1 -> 1, `Toast` 0 -> 0,
  `ModifyOrderModal` 0 -> 0, `CancelOrderDialog` 0 -> 0. Net movement on the
  backlog: zero.
- **`next dev` cannot ready in the CI Playwright container — STILL OPEN
  (infra).** `web/playwright.config.ts:61-66` still documents it and CI
  still overrides with `PLAYWRIGHT_WEBSERVER_CMD`. No change in the delta
  (`web/playwright.config.ts` has a zero-line diff over the range).
- **`pytest cloud/tests` red on macOS — STILL OPEN, and the recorded
  baseline was wrong.** See the sweeps above: 34, not 10, and the cause is
  bash 3.2, not `sha256sum`. Promoted to **T-118** because the suite gives
  no signal distinguishing an unrunnable environment from a real
  regression.
- **The one unreproduced 10-failure vitest round (2026-08-17) — NOT
  reproduced.** Three full vitest rounds this run, all `7036 passed / 0
  failed`, plus round 1 executed concurrently with seven read agents under
  load. Still no named tests; left as an observation, not promoted.
- **Day Move `next start` vs `next dev` divergence — SUPERSEDED, and the
  spec is now weekday-dependent for a NEW reason.** The 2026-08-17
  observation ("MARKET CLOSED / ---" under a production server) was recorded
  before the session gate existed. `f2fbe0a7`/`d45849d7` have since made
  that render the CORRECT output on any non-trading day — see **T-117**.

### Converged with the first pass (filed there, dropped here)

Eight findings this pass produced independently name the SAME defect as a
first-pass entry, at the same file:line. They are dropped rather than
renumbered; recorded because independent convergence is itself evidence.

| First pass | Also found here | Subject |
|---|---|---|
| T-080 | yes | combo worst-case-loss cap hardcoded at $10M with no test in the band (this pass measured the boundary at 70 lots / $9,098,600 → `check_order_limits(...) is None`) |
| T-081 | yes | `load_flows_from_turso` unfiltered multi-account aggregation, stubbed in every test |
| T-085 | yes | `order-dedup-visibility.test.tsx` counts `placeOrderFeedback(` in source on six surfaces (this pass proved it by mutating `web/lib/chat.ts:285` — 12 passed) |
| T-087 | yes | `relay-blackout-honesty.test.ts` asserts against a hand-mirrored relay loop |
| T-089 | yes | `test_server_up_after_first_refusal_succeeds` 0.5s sleep race (this pass also red on gate round 2, reproduced 3/3 on demand) |
| T-091 | yes | regime-tab specs key activation on a CSS class where `aria-current` exists |
| T-092 | yes | `flex-token-expiry.test.ts` prefers the gitignored runtime config |
| T-093 | yes | `llms-txt.test.ts` published-file case is a swallowed assertion on a `process.cwd()` path |

Three further entries below are DELTAS to a first-pass finding rather than
duplicates — they name a different mechanism in the same file or subsystem,
and each says so inline: **T-110** (delta to T-094 — the disk check's
outcome on the DB-down path, not its `inspect.getsource` wiring test),
**T-116** (delta to T-090 — the `mobile` Playwright PROJECT collects zero
specs in CI, not the file-count curation), and **T-118** (delta to T-088 —
on a runner with no Homebrew bash the darwin cloud baseline is 34, not 12,
and the cause is `/bin/bash` 3.2 rather than the `sha256sum` shim).

### P1 — correctness gaps
- **T-097 [P1] `IBClient.wait_until`'s "bounded" timeout is denominated in
  nominal sleep steps, not wall clock, and the delta's new test pins that
  design in place.** `scripts/clients/ib_client.py:465-472` accumulates
  `elapsed += step` where `step` is the REQUESTED sleep, never
  `time.monotonic()`. `ib_insync`'s `IB.sleep(secs)` runs the event loop for
  AT LEAST `secs`, so iteration count is fixed at `ceil(timeout/poll)` and a
  `timeout=2.0` wait becomes `40 x actual_step` — 20s if a blocked handler
  makes each 0.05s step take 0.5s. The docstring at `:457-458` states the
  wall-clock decoupling is deliberate FOR TEST CONVENIENCE. Call sites are the
  hot path: `:611` `get_pnl` (2.0s), `:857`/`:911` `get_quote` (2.0s), `:785`
  cancel-all drain, `:804` order-end, plus `scripts/ib_sync.py:152,875,894,963`.
  The new test cements it: `scripts/tests/test_ib_event_waits.py:109-113`
  asserts `mock_ib.sleep.call_count == 4` for `timeout=0.2, poll=0.05`, and
  because `mock_ib.sleep` is a `MagicMock` returning instantly in every test in
  the file, no test can observe a step overrun. This does not flip the runner —
  it flips PRODUCTION on a loaded gateway, with the suite green throughout.
  **AC:** RED — a test whose `mock_ib.sleep` side effect does
  `time.sleep(0.2)` per call, asserting `wait_until(lambda: False,
  timeout=0.2, poll=0.05)` returns within 0.5s wall; it takes ~0.8s today.
  GREEN — bound the loop on a `time.monotonic()` deadline captured at entry,
  and relax the step assertion to `call_count <= ceil(timeout/poll)`.
- **T-098 [P1] The R-077 tolerated-hash-drift fix rewrites `payload` and
  `payload_sha256` but not the denormalized economic columns, so a restated
  fill price or multiplier leaves the ledger row permanently
  self-contradictory — and the new test asserts only the two metadata
  columns.** `scripts/db/writer.py:1546-1561` UPDATEs exactly
  `payload_sha256, payload, perm_id, order_ref`; `price`, `multiplier`,
  `currency`, `con_id`, `side`, `quantity`, `filled_at` keep their insert
  values. The conflict gate `_execution_economically_conflicts`
  (`:1487-1509`) never checks `multiplier` or `currency`, and checks `price`
  only when `payload["price"]` is explicitly present —
  `scripts/position_return_capital.py:84-89` documents that replay storage
  supplies `avgPrice` instead, and the `avgPrice`-derived `price` IS in
  `_LIFECYCLE_HASH_FIELDS` (`:1449`). So an `avgPrice` restatement changes the
  hash, passes the gate, and takes the tolerated branch. Executed against the
  real 0037 schema and the real writer (in-memory sqlite): insert
  `avgPrice=4.15` then resync `avgPrice=9.99` → `price` COLUMN `4.15`,
  `payload.avgPrice` `9.99`; resync `multiplier=1` → `multiplier` COLUMN
  `100.0`, `payload.multiplier` `1`. The next identical sync returns `False`
  (idempotent), so the row NEVER converges. Before R-077 the branch wrote
  nothing, so payload and columns stayed mutually consistent — the fix created
  the divergence. `scripts/tests/test_position_execution_fact_tolerated_hash.py:85-89`
  asserts only `perm_id`, `order_ref`, `payload["permId"]` and the hash.
  **AC:** RED — extend `TestToleratedHashConverges` with an `avgPrice`
  4.15 → 9.99 resync asserting `SELECT price == 9.99`, and a `multiplier=1`
  resync asserting `SELECT multiplier == 1`; both fail today. GREEN — carry
  `price, multiplier, currency, con_id, side, quantity, filled_at` from `item`
  in the UPDATE at `:1548-1557`, or add `multiplier`/`currency` and the
  `avgPrice`-derived price to the conflict gate so the case raises instead of
  half-applying.
- **T-099 [P1] Two of the three new indicator jobs write
  `service_health = ok` with a fresh `scan_time` when IB AND UW AND Yahoo all
  fail, pinning the watchdog's 26h staleness window open forever — and neither
  new test file has a both-feeds-down case.** `scripts/fetch_iei_hyg.py:412-424`:
  `fetch_closes()` returning `{}` yields `fresh = []`, `merge_series(cached, [])`
  returns the disk cache, `payload["series"]` is non-empty, so `persist_result`
  is not refused; `:394-399` then writes `upsert_scan_snapshot(SERVICE,
  scan_time, ...)` and `record_service_health("iei-hyg", "ok", ...)` with a
  FRESH `scan_time`. `scripts/fetch_credit_spread.py:470-485` + `:452-457` is
  the identical shape. Executed with all three fetchers stubbed to `{}` over a
  populated cache: writer calls were
  `['guard', ('snapshot','iei-hyg',<now>), ('health','iei-hyg','ok')]`,
  payload `source: "none"`, `count: 2` — a green heartbeat over data no source
  confirmed. `scripts/watchdog/services.py:123,129,340-345` gates both purely
  on a 26h heartbeat window, so a permanent three-source outage never pages and
  the regime tab serves frozen data indefinitely. The SIBLING indicator got it
  right and has the test to prove the intended contract:
  `scripts/tests/test_ivrank.py::TestBothFeedsDown` asserts
  `status == "stale_source"` and `fake.health[0][1] == "error"`. The two new
  suites instead encode the opposite — `test_iei_hyg.py:219-224` and
  `test_credit_spread.py:227-233`
  (`test_unchanged_day_heartbeats_without_row_upserts`) assert "unchanged →
  heartbeat ok", which is byte-identical to the outage path and therefore
  cannot distinguish them. **AC:** RED — stub all three fetchers to `{}` with a
  populated cache and assert `("health", <service>, "error")` plus a payload
  status marker; fails today (`"ok"`). GREEN — mirror `fetch_ivrank.run`'s
  `stale_source` branch when `combine_source(sources)` is empty.
- **T-100 [P1] The token-wide Flex 1025 embargo is enforced from a single
  ephemeral host-local file whose write failure is swallowed, its advertised
  durable record is stubbed out in 100% of its tests and never read back, and
  the suite pins the fail-open path as correct.** Write side:
  `scripts/utils/flex_embargo.py:79-91` wraps `SIDECAR.write_text(...)` in
  `except OSError: pass` and returns the deadline string regardless, so on a
  full or read-only `data/` the embargo is armed nowhere while the caller
  (`scripts/cash_flow_sync.py:736-741`) treats it as recorded and exits 15.
  Read side: `active_until()` (`:57-65`) consults `_read_sidecar()` only —
  grep for `flex-web-service` across `scripts/`, `web/`, `cloud/` returns only
  the constant at `:22` and the freshness window at
  `web/lib/serviceHealthWindows.ts:143`; nothing ever reads the embargo back
  from `service_health`, contradicting the module's own docstring at `:9-11`.
  That also makes it a per-HOST breaker on a shared token: under the
  two-mode deployment the laptop and Hetzner share `IB_FLEX_TOKEN` and one
  Turso DB but have separate `data/` trees, so a lockout armed on Hetzner
  leaves `raise_if_blocked()` a no-op for
  `scripts/trade_blotter/flex_query.py:86`,
  `scripts/perf_twr_builder.py:283` and `scripts/cash_flow_sync.py:671` on the
  laptop — and every further SendRequest extends the 7-day lockout. The
  dual-write `_heartbeat` (`:99`) is monkeypatched to a no-op in EVERY test
  that touches the module (`scripts/tests/test_flex_token_embargo.py:26`,
  `scripts/tests/test_flex_query_lockout.py:23`,
  `tests/test_perf_twr_flex_timeouts.py:113,144,173`), so breaking its
  `record_service_health` call keeps all of them green; and
  `test_flex_token_embargo.py:59` (`test_no_sidecar_is_not_blocked`) asserts
  the fail-open as intended behaviour. **AC:** RED (three) — (1) point
  `SIDECAR` inside a `chmod 0o500` directory, call `record_lockout("1025")`,
  assert `is_blocked() is True`; (2) do NOT stub `_heartbeat`, spy
  `db.writer.record_service_health` and assert one call with
  `("flex-web-service", "error")` carrying `next_attempt_at`; (3) seed
  `service_health` with a live `next_attempt_at`, leave the sidecar absent,
  assert `raise_if_blocked()` raises. All three fail today. GREEN — make
  `record_lockout` signal failure when neither sink lands, and have
  `active_until()` fall back to the `service_health` row (rehydrating the
  sidecar) when the file is missing.
- **T-101 [P1] The "Cancel All Orders" confirm gate — the only thing between
  one click and a master global cancel of every working order, exits
  included — has no test, and neither does Halt.**
  `web/components/admin/TradingKillSwitch.tsx:137-140`:
  `onClick={() => setConfirmFor("cancel-all")}`. That indirection is the whole
  protection: `runAction("cancel-all")` unconditionally posts
  `{confirm:true}` (`:59-64`), so the route's own confirm check
  (`web/app/api/admin/trading/[action]/route.ts:80-86`) is always satisfied by
  this component and the dialog is the only human gate.
  `web/tests/trading-kill-switch.test.tsx` covers three cases (`:66`, `:73`,
  `:102`) — status render, kill (typed-token gated), resume — and never
  references `trading-cancel-all-button` or `trading-halt-button`; grep across
  `web/tests` and `web/e2e` for `trading-cancel-all-button` returns zero hits.
  Change `:137` to `onClick={() => void runAction("cancel-all")}` and both
  `trading-kill-switch.test.tsx` and `admin-trading-controls.test.ts` stay
  green — the route test only ever posts directly, never through the
  component. Same gap on Halt at `:125`, which blocks all new placement.
  **AC:** RED — render `<TradingKillSwitch />`, click
  `trading-cancel-all-button`, assert ZERO POSTs to
  `/api/admin/trading/cancel-all` before `admin-confirm-action` is clicked and
  exactly one after; mirror for Halt. GREEN — the same tests pass against the
  current dialog wiring and fail under the mutation above.
- **T-102 [P1] The Day P&L non-trading-day guard is pinned to a holiday inside
  a static table that ends at 2027, and nothing tests the table's horizon —
  from 2028-01-01 every market holiday silently reads as a trading day and the
  card resumes printing IB's re-baselined `daily_pnl` as "TODAY".**
  `web/tests/day-pnl-non-trading-day.test.tsx:146-153` pins the holiday case
  at `2026-12-25`. The gate is `web/lib/ibDailyPnlSession.ts:12-16` →
  `isUsTradingDay` (`web/lib/serviceHealthWindows.ts:597-602`) →
  `isHolidayIso` (`:587-590`), reading `scripts/config/market_holidays.json`,
  whose top-level keys are `2023..2027` only; the documented fallback at
  `serviceHealthWindows.ts:579-580` is "years missing from the table fall back
  to weekday-only". So `isUsTradingDay("2028-01-17")` (MLK, a Monday) returns
  `true`. No test asserts the table covers the current or next year —
  `web/tests/market-state-holiday.test.ts` contains no `2027`, `2028`,
  `getFullYear`, or coverage assertion. The suite therefore cannot go red when
  the guard expires, and the +$13,951.76 phantom "TODAY" P&L this test was
  written for returns on every 2028 holiday. **AC:** RED —
  `expect(Object.keys(staticHolidays)).toContain(String(new Date().getFullYear() + 1))`
  plus a case asserting `isUsTradingDay` is `false` for next year's MLK
  Monday; both fail today for 2028. GREEN — extend
  `scripts/config/market_holidays.json` through 2029+ and KEEP the horizon
  assertion so the next expiry fails a year early instead of silently.
- **T-103 [P1] The deploy-collateral `in_flight` suppression was widened from
  60 minutes to 24 hours with zero test coverage, and the same delta proves
  the transition journal that drives it can be stranded on disk forever — so
  every SIGTERM'd `radon-*` unit is silently downgraded P1 → P3
  indefinitely.** `scripts/watchdog/units.py:147` sets
  `"in_flight": TRANSITION_JOURNAL_PATH.exists()` — presence only, no age
  check — and `:179-180` now does
  `if deploy.get("in_flight"): return age <= KILL_BEFORE_GREEN_FROZEN_CAP_SECS`
  (`:73` = 24h), where pre-delta it used `DEPLOY_COLLATERAL_WINDOW_SECS`
  (60 min, `:66`). The commit justifies the widening only for
  kill-before-green; `in_flight` was carried along. The SAME delta adds
  `scripts/watchdog/external_probe.py:36-42`
  `TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS = 3600` with the comment "an
  interrupted deploy leaves the transition journal on disk forever … a state
  that also blocks every later deploy, so it cannot self-clear" — `units.py`
  got no equivalent guard. Measured live:
  `units._is_deploy_collateral({"Result":"signal",...},
  {"marker_mtime":None,"in_flight":True}, now)` → `True` at 0.2h / 2h / 12h /
  23h, `False` only at 25h. Coverage: `in_flight=True` appears in exactly one
  test repo-wide (`scripts/tests/test_watchdog/test_units.py:439`, a ~12-min
  kill asserting P3), and both new frozen-cap tests
  (`test_suppression_bounds.py:247,:269`) pass `in_flight: False`. MUTANT
  PROOF: patching `_is_deploy_collateral` to `return True` whenever
  `deploy["in_flight"]` — removing the cap entirely — leaves
  `pytest scripts/tests/test_watchdog/ -q` at **289 passed**. **AC:** RED —
  `deploy={"marker_mtime": None, "in_flight": True}`, `Result=signal`, kill
  20h before `now`; assert severity `P1`. Returns P3 today. GREEN — give
  `units._read_deploy_evidence` the `external_probe` staleness rule (journal
  mtime older than `TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS` ⇒
  `in_flight=False`), or bound the branch back to
  `DEPLOY_COLLATERAL_WINDOW_SECS`.
- **T-104 [P1] `deploy.sh` installs and `enable --now`s the new release's
  systemd timers BEFORE the post-deploy gate, and the rollback path never
  reverts them — a rolled-back release leaves armed timers pointing at
  scripts that do not exist at the restored SHA.**
  `cloud/scripts/deploy.sh:872` calls `install_release_units` inside
  `restart_services()`, after `activate_staged_release` and before
  `start_services_after_transition`.
  `cloud/scripts/deploy-root-helper.sh` `install_manifest_units` does
  `mv -f candidate → /etc/systemd/system/<unit>` then
  `systemctl enable --now <unit>` for each newly installed `.timer`. The gate
  runs AFTER: `deploy_gate` → on failure `rollback "$prev_commit"` (`:1113`),
  and `restore_release_backup` does a `git reset --hard "$previous_sha"` plus
  `ROLLBACK_ARTIFACTS` moves (`:111-112`) — nothing touches
  `/etc/systemd/system`, and there is no `install-units` /
  `sync-scheduled-units` call on the rollback path. Concrete instance IN THIS
  DELTA: `radon-credit-spread.timer` and `radon-iei-hyg.timer` are new and in
  `cloud/config/installed-units.sha256:13,48`, both `Persistent=true`, whose
  services `ExecStart` the new `scripts/fetch_credit_spread.py` /
  `scripts/fetch_iei_hyg.py`. A gate-failing deploy after 21:45 UTC enables
  them, they fire immediately (Persistent + never-run), rollback removes the
  scripts, and the timers then fail every fire with 203/EXEC and page forever.
  The next deploy of the OLD SHA cannot undo it: the unit is absent from that
  SHA's manifest, so `install_manifest_units` never revisits it. Coverage:
  `cloud/tests/test_install_units.py:261` pins only the happy ordering
  (`stop-clean, activate, install-units, restart-managed`) and `:276` the
  non-fatal case; of the 5 `cloud/tests` files mentioning `rollback`, none
  mentions `unit`, `timer`, or `install`. **AC:** RED — a shell test stubbing
  `deploy_gate` to fail, asserting the sudo call log contains an
  `install-units` (or equivalent revert) invocation AFTER
  `restore_release_backup`; fails today. GREEN — move
  `install_release_units` after `deploy_gate` succeeds, or call it again from
  `rollback()` so the restored SHA's manifest re-converges and units absent
  from it are disabled.
- **T-105 [P1] The R-060 fix in the pool-recovery escalation guard (scope the
  counter reset to the STUCK roles) is unpinned — the only test file stubs the
  predicate with a lambda that ignores `roles`, so reverting to the ANY-role
  form stays green.** `scripts/api/server.py:366` computes
  `stuck_roles = _pool_disconnected_roles(ib_pool)` and `:374` does
  `if _pool_has_connected_accounted_slot(ib_pool, roles=stuck_roles):
  _pool_recovery_state["consecutive_failures"] = 0; return`. The `roles=`
  kwarg IS the fix: a healthy `orders`/`sync` slot must no longer reset the
  ladder while `data` is wedged.
  `scripts/api/tests/test_pool_recovery_escalation.py:74` does
  `monkeypatch.setattr(ibg, "_pool_has_connected_accounted_slot",
  lambda pool, roles=None: accounted)` — the stub returns `accounted`
  unconditionally, so `f(ib_pool)` and `f(ib_pool, roles=stuck_roles)` are
  indistinguishable in every test in the file, and no test constructs the
  mixed state. `grep -rn "_recover_stuck_pool_guarded" scripts` returns only
  this file; the role-scoped assertions in
  `scripts/api/tests/test_ib_gateway_pool_recovery.py:293,364` cover a
  different function and say nothing about the counter. Regression that ships
  green: revert `:374` to `_pool_has_connected_accounted_slot(ib_pool)` — with
  orders+sync healthy and data wedged, `consecutive_failures` resets every 15s
  tick, `POOL_RECOVERY_MAX_BEFORE_RESTART` is never reached, and radon-api
  never self-restarts out of the wedge. That is R-060 verbatim. **AC:** RED —
  replace the lambda with a `status()`-driven fake
  (`{"sync":ok,"orders":ok,"data":disconnected}`), drive
  `POOL_RECOVERY_MAX_BEFORE_RESTART` ticks, assert exactly one restart; fails
  against the ANY-role form. GREEN — keep `roles=stuck_roles`.
- **T-106 [P1] The vol-cone tab stamps the whole table "LIVE THIS SESSION"
  when as few as one name got a live refresh; every un-refreshed row still
  shows yesterday's IV and percentile with no per-row distinction.**
  `scripts/fetch_vol_cone.py:879,885`:
  `live = any(name.get("is_intraday") for name in names)` then
  `payload["is_intraday"] = live`, and `source_as_of` is set to today's
  session on that same `any()` (`:881-883`). But `intraday_targets`
  (`:765-776`) deliberately refreshes only watchlist names plus names with
  `atm_percentile <= 0.40` or `wing_score <= 0.40`, capped at
  `_INTRADAY_PAIR_CAP = 80` (`:76`), against a `_UNIVERSE_CAP = 120` tickers x
  up to 8 monthlies — so the large majority of rows are NOT refreshed.
  Refreshed names DO carry a per-name `"is_intraday": True` (`:841-844`); the
  web type drops it — `web/lib/volCone.ts:47` puts `is_intraday?: boolean` on
  `VolConeData` only, `VolConeName` (`:18-38`) has no such field, and
  `web/components/VolConePanel.tsx:183,227` reads only `data.is_intraday`. At
  11:00 ET the strip reads `SOURCE (LIVE) / <today> / LIVE THIS SESSION` while
  a RICH-tail row 40 places down shows yesterday's ATM IV and percentile; the
  operator clicks it and the analysis panel computes `expectedMove` and the
  strangle/straddle legs off stale IV (`web/lib/volCone.ts:171-183,207-231`),
  and the "Open trade" href is built from it. Neither covering test constructs
  a mixed payload: `web/tests/vol-cone-panel.test.tsx:357-374` flips
  `is_intraday: true` on a payload whose two names are byte-identical clones
  from `buildData()` (`:153-172`) and asserts only the strip cell;
  `scripts/tests/test_vol_cone.py:617-631` seeds exactly ONE ticker/expiry, so
  `any()` and "all names refreshed" are indistinguishable. Mutation that stays
  green: `fetch_vol_cone.py:885` → `payload["is_intraday"] = True`
  unconditionally. **AC:** RED — a two-name payload where only NVDA carries
  `is_intraday: true`; assert the SMH row renders a stale/as-of marker
  distinct from NVDA's. Python side: seed two tickers, make one rich enough to
  fail `intraday_targets`, assert only the refreshed name has
  `name["is_intraday"] is True`. GREEN — add `is_intraday` to `VolConeName`,
  render a per-row marker, and make the strip label honest ("PARTIAL LIVE" /
  a count) when the payload flag is true but not all names carry it.
- **T-107 [P1] The route-change re-fetch feature is tested only through a
  hand-supplied context value; nothing covers the `usePathname()` → context
  wiring or that `Providers` mounts the provider, so the whole feature can be
  deleted green.** `web/lib/RouteRefreshContext.tsx:9` is
  `createContext<string>("")` and `:16` is `const pathname = usePathname() ?? ""`.
  Every consumer bails on the empty default — `web/lib/usePortfolio.ts:181`
  `if (!routeKey || routeKey === lastRouteKeyRef.current) return;`,
  `web/lib/useOrders.ts:153`, `web/lib/useSyncHook.ts:202` — so if the
  provider is absent the context is `""`, the guard short-circuits, and no
  hook re-fetches on navigation, silently and with zero errors. The only test,
  `web/tests/use-route-change-sync.test.tsx:53-59`, wraps hooks in
  `<RouteRefreshContext.Provider value={routeKey.current}>` directly — it never
  renders `RouteRefreshProvider` and never mocks `next/navigation`. Nothing in
  the repo touches the wiring: `grep -rl "RouteRefreshProvider\|Providers"
  web/tests web/e2e` returns nothing. Mutation that stays green: delete the
  `<RouteRefreshProvider>` wrapper at `web/components/Providers.tsx:19`/`:24`,
  or set `RouteRefreshContext.tsx:16` to `const pathname = ""` — all four
  tests pass and production silently reverts to the mount-only fetch that
  `bb000186` was written to fix. **AC:** RED — a jsdom test mocking
  `next/navigation.usePathname` with a mutable value, rendering `usePortfolio`
  inside the REAL `RouteRefreshProvider` (or inside `Providers`), flipping the
  pathname and asserting a second GET. GREEN — the current provider passes it;
  removing the provider reds it.
- **T-108 [P1] The demo mirror's account-data purge — the guarantee that
  `flow_analysis_snapshots` never exists in the public demo DB — has zero
  behavioral coverage, swallows its own failure, and the source-grep guard
  that "covers" it still passes if the purge loop is deleted outright.**
  `scripts/db/mirror_market_snapshots_to_demo.js:53-55` states the table "must
  never exist in the public demo database". `:185-192` is the only
  enforcement; on failure it does `console.warn("[mirror] SKIP purge ...")`
  and continues. It is NOT wrapped in `retryOperation` (unlike every mirror
  read/write, `:126,145`) and its table is never added to `failures` (`:220`),
  so the job prints `[mirror] done` and exits 0 with account-derived rows
  still live in the demo DB. Every `runMarketMirror` call in the covering
  suite passes `purgedAccountTables: []`
  (`scripts/lib/demoMirrorReliability.test.js:172,205,240`), so the purge path
  never executes. The only other coverage is a source grep:
  `scripts/tests/test_demo_seed_guard.py:77-84` asserts
  `'PURGED_ACCOUNT_TABLES = ["flow_analysis_snapshots"]' in source` and
  `"DELETE FROM ${table}" in source` — both survive deleting `:185-192`, since
  the constant declaration is untouched and `"DELETE FROM ${table}"` is a
  substring of the prune SQL at `:200` and `:210`. With the loop gone,
  previously-mirrored `flow_analysis_snapshots` rows are never removed and
  `web/app/api/flow-analysis/route.ts:56` / `scripts/api/demo_scan.py:34`
  serve them on demo.radon.run. **AC:** RED — call `runMarketMirror` with the
  real `PURGED_ACCOUNT_TABLES` default and a `dst` spy, asserting
  `dst.execute` received `DELETE FROM flow_analysis_snapshots`; second case,
  make that call reject with a Turso 502 and assert the run retries or lands
  in `failures` (today it warns and reports success). GREEN — route the purge
  through `retryOperation` and push its table into `failures` on persistent
  error.
- **T-109 [P1] The UW budget brake is not applied to the two schedulers that
  caused the quota burn, and the only regression guard for them is a string
  grep over a shell DEFAULT that a runtime env var overrides.** The counting
  and attribution half of `09a168a4` is genuinely asserted
  (`scripts/tests/test_uw_budget.py:87-146` pins `by_caller`/`by_endpoint`,
  the `<T>` collapse, argv/env caller fallback, `top_callers`, rollover
  archival; `scripts/tests/test_uw_usage_record.py` covers the FastAPI mirror
  and its `1..500` bound). The brake `should_block_universe_scan`
  (`scripts/utils/uw_budget.py:224`) is wired into exactly two scanners —
  `scripts/theta_harvester_scanner.py:719` and
  `scripts/strength_confirmation_scanner.py:891` — and into NEITHER GARCH nor
  LEAP, the two jobs the commit message names as "roughly 30k of the 40k daily
  cap". Their entire guard is `scripts/tests/test_uw_budget.py:149-165`, which
  reads `scripts/run_garch_refresh.sh`, `scripts/run_leap_refresh.sh` and
  `web/components/WorkspaceSections.tsx` as text and asserts
  `'"indexes"' not in source` and `"largecaps" in source`. That does not
  describe runtime behaviour: `scripts/run_garch_refresh.sh:96` is
  `PRESET="${RADON_GARCH_REFRESH_PRESET:-largecaps}"` and
  `scripts/run_leap_refresh.sh:99` is the same shape, so setting
  `RADON_GARCH_REFRESH_PRESET=indexes` in `~/radon-cloud/.env` restores the
  2,494-ticker x 3-request scan three times a trading day with the test still
  green and no budget check anywhere in the call path. A superset preset
  defaulted in both scripts also passes, since `largecaps` already appears in
  each file's comment block (`:17`). **AC:** RED — call the GARCH and LEAP
  preset-scan entry points with `should_block_universe_scan` monkeypatched to
  `True` and assert they refuse and emit a degraded-scan telemetry row,
  mirroring `scripts/tests/test_scan_degraded_telemetry.py:109-171`; no such
  guard exists today. GREEN — apply `should_block_universe_scan()` in the
  GARCH/LEAP preset paths as `theta_harvester_scanner.py:719` does, and
  replace the string grep with a resolved-ticker-count assertion
  (`len(load_preset(default).tickers) < N`) so the guard is about size, not
  spelling.

### P2 — fragility / structure
- **T-110 [P2] (delta to T-094) The new root-disk check is wired into the DB-down path, where
  every fired outcome becomes an uncooled Pushover EMERGENCY page — so a P2
  "root fs 86% used" repeats every 5 minutes for as long as Turso is
  unreachable.** `scripts/watchdog/__main__.py:90` appends
  `disk.check_disk(now=now)` inside `_handle_snapshot_unavailable`, and `:94`
  sends every `outcome.fired` straight to `notify.send_direct_page(...)` with
  no cooldown ("the cooldown table is unreachable").
  `scripts/watchdog/notify.py:289-307` hardcodes `severity="P1"` in
  `build_pushover_payload`, and `_emit_pushover`'s docstring at `:312` states
  P1 is emergency priority that "cuts through iOS DnD and repeats until
  acknowledged". `scripts/watchdog/disk.py:52-70` returns
  `fired=True, severity="P2"` at >=85% and keeps returning it every cycle
  (`consecutive_failures` is a literal `1`) — unlike its two path-mates
  (`units`, `external_probe`) this is a slow-moving condition that persists
  for days. On the normal path it is correctly gated
  (`grouping.dispatch_with_grouping` → `_cooldown_allows_fire(service,
  severity="P2")`, `scripts/watchdog/grouping.py:365`, P2 → daily digest); the
  asymmetry exists only on the snapshot-unavailable branch. Coverage is a
  source grep: `scripts/tests/test_watchdog/test_disk.py:69-85` (`TestWiring`)
  does `inspect.getsource(cli._handle_snapshot_unavailable)` and asserts
  `"check_disk" in source`. Nothing asserts what a P2 outcome does on the
  direct-page path. **AC:** RED — `_handle_snapshot_unavailable(bucket=
  "continuous", ...)` with `disk_usage` at 90% and units/external-probe
  healthy; assert `send_direct_page` is not called (or at most once per
  file-based cooldown window). It is called every invocation today. GREEN —
  filter the direct-page loop to `outcome.severity == "P1"`, or give
  `root-disk-usage` the same file-based cooldown `_handle_snapshot_unavailable`
  already keeps for `watchdog-blind` (`__main__.py:103-125`).
- **T-111 [P2] `test_suppression_bounds.py` exists to prove three watchdog
  suppressions are BOUNDED, but every expectation is derived from the
  constant under test, so all three can be set to effectively infinite and
  the file stays green.** `scripts/tests/test_watchdog/test_suppression_bounds.py:90`
  (`ceiling = grouping.WARMUP_SUPPRESS_MAX_CONSECUTIVE`), `:170` and `:206`
  (`... TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS + 600`), `:255`
  (`... KILL_BEFORE_GREEN_FROZEN_CAP_SECS + 3600`) — every "past the bound"
  case is `constant + delta` and every "inside the bound" case is dominated by
  the constant. Those three names appear ONLY in this file and in
  `scripts/watchdog/{grouping.py:212, external_probe.py:42, units.py:73}`; no
  test anywhere pins a magnitude. PROVEN: with
  `TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS = 10**9` (~31 years),
  `KILL_BEFORE_GREEN_FROZEN_CAP_SECS = 10**9`, and
  `WARMUP_SUPPRESS_MAX_CONSECUTIVE = 500` (~41h of muted IB pages at the
  5-minute cycle) injected via an out-of-tree pytest plugin, the file reports
  `8 passed in 4.25s` — R-056 / R-057 / R-064 all restored in practice while
  the suite that forbids them is green. The repo already has the right
  convention elsewhere (`tests/test_twr_math.py:1135` pins
  `NAV_STALENESS_BUDGET_SESSIONS == 2`;
  `scripts/tests/test_cash_flow_sync_cli.py:347` pins
  `FLEX_POLL_BUDGET_SECONDS >= 300`). Same run also surfaced that the file
  imports `scripts.watchdog.{external_probe,units}` at `:30` but
  `watchdog.grouping` inside the tests at `:88,:119`, so the two halves patch
  DIFFERENT module objects. **AC:** RED — add
  `assert grouping.WARMUP_SUPPRESS_MAX_CONSECUTIVE <= 6`,
  `assert external_probe.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS <= 2*3600`,
  `assert units.KILL_BEFORE_GREEN_FROZEN_CAP_SECS <= 36*3600`; they pass today
  and fail under any widening, which is the point. GREEN — keep the derived
  cases (they test the mechanism), add the three literal ceilings (they test
  the policy), and settle on one import path for `watchdog`.
- **T-112 [P2] `test_iei_hyg.py` never executes `fetch_closes`, so the
  IEI/HYG source ladder — governed by Mandatory Rule 7 — has zero coverage;
  reordering it Yahoo-first leaves the suite green.**
  `scripts/tests/test_iei_hyg.py:253-267` (`TestCli`) monkeypatches
  `fetch_closes` wholesale, and nothing else in the file imports or calls it
  (`:16-28` has no `fetch_closes`, `fetch_ib_closes`, `fetch_uw_closes`,
  `fetch_yahoo_closes`). `pytest scripts/tests/test_iei_hyg.py
  --cov=fetch_iei_hyg --cov-report=term-missing` → **50%**, missing `250-256`
  (`_take`), `267-273` (the `fetch_closes` body — the three ordered `_take`
  calls at `scripts/fetch_iei_hyg.py:268-270`), `86-100` (Yahoo), `106-198`
  (IB), `215-238` (UW). Mutation that stays green: swap `:268-270` to
  `yahoo → uw → ib` — all 25 tests pass. The near-identical credit-spread
  ladder IS covered by four tests (`test_credit_spread.py:280-339`, including
  `test_yahoo_is_last_resort`), so this is an oversight, not a design choice.
  **AC:** RED — port `TestFetchClosesCascade` from `test_credit_spread.py`,
  injecting `fetch_ib`/`fetch_uw`/`fetch_yahoo` and asserting an IB hit never
  calls UW or Yahoo, that DXY skips UW (`UW_SKIP`, `fetch_iei_hyg.py:69`), and
  that only the gap is passed downstream. GREEN — no source change needed; the
  ladder is correct today, the tests just have to hold it there.
- **T-113 [P2] `table-scroll-wrapper-contract.test.ts` only inspects
  classNames that ALREADY contain the literal `table-scroll`/`table-wrap`, so
  renaming or composing the wrapper class reintroduces the exact mobile
  overflow bug it documents, with the contract green.**
  `web/tests/table-scroll-wrapper-contract.test.ts:45-52` collects a className
  only when `tokens.some((t) => /table-(scroll|wrap)/.test(t))` — anything
  else is SKIPPED, not flagged — and the attribute regex at `:47` requires a
  quote or backtick immediately after `className=`/`className={`, so
  `className={cn("table-wrap", x)}` is invisible to it. PROVEN: renaming both
  wrappers in `web/components/VolConePanel.tsx:256,397` from `table-wrap` to
  the undefined class `volcone-shell` leaves the 9-column vol-cone table with
  no `overflow-x` container at 390px — the documented 2026-08-18 bug — and
  `npx vitest run web/tests/table-scroll-wrapper-contract.test.ts` reported
  **1 passed**. **AC:** RED — apply that rename; the contract must fail.
  GREEN — invert the check: enumerate every `<table>`-bearing component and
  require its nearest wrapper `<div>` to carry a class with an `overflow-x`
  rule (or an explicit `data-overflow-exempt="<reason>"`). The same inversion
  covers `sortable-table-contract.test.ts:49`, whose `hasSort` regex matches
  `SortTh` anywhere in a file, imports and comments included.
- **T-114 [P2] `use-market-hours-first-paint.test.tsx` cannot fail against the
  first-paint placeholder it exists to guard — `renderHook` flushes the mount
  effect before the assertion reads state.** The file's only case
  (`web/tests/use-market-hours-first-paint.test.tsx:14-20`) asserts
  `result.current === MarketState.OPEN` after `renderHook(() =>
  useMarketHours())`. RTL wraps render in `act()`, so `useMarketHours`'s mount
  effect (`web/lib/useMarketHours.ts:47-49`, which calls `check()`
  immediately) has already corrected the state by the time `result.current` is
  read; the lazy initializer at `:44` — the entire fix — is unobserved.
  PROVEN: replacing `useState<MarketState>(marketStateAt)` with
  `useState<MarketState>(MarketState.CLOSED)` restores the CLOSED first-paint
  placeholder and the file still reports **1 passed**. **AC:** RED — apply
  that mutation; the case must fail. GREEN — assert the pre-effect value
  directly: test the pure `marketStateAt(new Date("..."))` (export it), or
  assert on `renderToStaticMarkup` of a component that renders the hook's
  value, which never runs effects.
- **T-115 [P2] The default `chromium` Playwright project has no `testIgnore`,
  so both new `mobile-*` specs are ALSO collected at Desktop Chrome 1280x720
  and their phone-geometry assertions fail deterministically on every full
  local run.** `web/playwright.config.ts:45-57`: the `mobile` project carries
  `testMatch: /mobile-.*\.spec\.ts$/`, but `chromium` matches everything.
  Proven by COLLECTION: `npx playwright test --list
  mobile-vol-cone-table mobile-scanner-mode-tabs-overflow` → 6 tests, three of
  them `[chromium]`. `web/e2e/mobile-scanner-mode-tabs-overflow.spec.ts:67`
  asserts `data-overflow-right === "true"` at rest, while
  `web/components/ScannerModeTabs.tsx:74` states the opposite for that
  viewport ("No-op on desktop where the strip never overflows") — at 1280px
  the seven tabs fit, `computeScrollAffordance` returns
  `{left:false,right:false}` (`:49-56,102-105`), the attribute is `"false"`,
  FALSE-RED. `web/e2e/mobile-vol-cone-table.spec.ts:134`
  (`wrapper.scrollWidth > clientWidth`) is at the same risk and `:152`
  (`box.x < 393`) passes vacuously at desktop width. Not caught by CI only
  because the curated subset excludes both files. **AC:** RED —
  `cd web && npx playwright test --project=chromium
  mobile-scanner-mode-tabs-overflow` fails at `:67` with a correct product.
  GREEN — add `testIgnore: /mobile-.*\.spec\.ts$/` to the `chromium` project;
  the same command then collects 0 tests and `--project=mobile` still runs 3.
- **T-116 [P2] (delta to T-090) The `mobile` Playwright project gates NOTHING in CI: the
  curated subset grew by five this delta and every addition was desktop,
  while the delta simultaneously added two mobile specs.**
  `.github/workflows/ci.yml:307-321` runs `npx playwright test` against 14
  explicit filenames with no `--project`, so both projects are active — but
  none of the 14 starts with `mobile-`, so the `mobile` project's `testMatch`
  intersects the file list at the empty set. 18 `mobile-*.spec.ts` exist on
  disk including money-path ones (`mobile-orders`, `mobile-order-ticket`,
  `mobile-stop-order`, `mobile-blotter`, `mobile-positions`,
  `mobile-executed-journal`). Concrete failure: a mobile-shell regression that
  breaks the order ticket at 393px — the exact class
  `web/tests/mobile-order-ticket-combo-risk-scale.test.tsx` was added for in
  this delta — ships green, because the jsdom unit test asserts props and no
  CI browser ever renders that route at a phone viewport. Distinct from T-073
  (file-count curation): this is a whole PROJECT dimension at zero, and it did
  not change when the subset moved. **AC:** RED — add `--project=mobile` to
  `ci.yml:307` as-is and the step reports 0 tests run. GREEN — curate at least
  one money-path mobile spec (`e2e/mobile-order-ticket.spec.ts` or
  `e2e/mobile-stop-order.spec.ts`) into the list after observing green runs
  locally, per the promote-only-after-green rule at `ci.yml:300-302`.
- **T-117 [P2] Two untouched e2e specs were made weekday-dependent by this
  delta's source change: they assert IB's `daily_pnl` renders, which is now
  correctly suppressed on any non-trading day.** `f2fbe0a7`/`d45849d7`
  introduced `web/lib/ibDailyPnlSession.ts` and wired it into
  `web/components/MetricCards.tsx:637` as `const sessionToday =
  isIbDailyPnlCurrent()` — called with NO argument, so it reads the real
  `new Date()` at render time; `:652` then nulls `ibDailyPnl` and `:640`
  filters `sessionPositions` down to spot crypto. Neither spec was touched:
  `web/e2e/day-move-ib-daily-pnl.spec.ts:269-271` asserts the Day Move row
  contains `-$3,688` and `:280` asserts the modal shows `-$3,688.02`;
  `web/e2e/account-day-move-ib-daily-pnl.spec.ts:216,223` assert `-$3,405` and
  `-$3,405.31`. Both fixtures hold a single WULF option position and no
  crypto, so on a Saturday, Sunday or full-closure holiday the card renders
  `---` / `MARKET CLOSED` and all three tests fail with a CORRECT product.
  Proof by composition rather than a spec run: the delta's own unit test
  `web/tests/day-pnl-non-trading-day.test.tsx:134-145` renders the same
  component with `vi.setSystemTime(new Date("2026-08-22T21:23:00Z"))` — the
  instant this audit ran — and ASSERTS the MARKET CLOSED render. Neither spec
  is in the CI curated list, so this is a local false-red, not a CI break.
  Both also reach for `.metrics-grid-3 .metric-card` / `.metric-label` by CSS,
  which is the standing testid backlog. **AC:** RED — run either spec on a
  weekend; it fails on a correct product. GREEN — pin the clock with
  `page.clock.install({ time: <a Wednesday RTH instant> })` before
  `page.goto` (the pattern `web/e2e/catalyst-card-weekend.spec.ts` and
  `bpi-tab.spec.ts:84` already use), and add a companion assertion that the
  SAME fixture renders `MARKET CLOSED` under a pinned Saturday.
- **T-118 [P2] (delta to T-088) `pytest cloud/tests` is 34-red on any host whose `/bin/bash`
  is 3.2, the suite gives no signal that the environment rather than the code
  is at fault, and the recorded macOS baseline of "10 failed" is stale.**
  `cloud/tests/test_bootstrap_control_plane.py:253` invokes
  `subprocess.run(["bash", str(ROOT_HELPER), ...])`, which on darwin resolves
  to the system `GNU bash 3.2.57`. `cloud/scripts/bootstrap-control-plane.sh:96`
  uses dynamic file-descriptor allocation (`exec {DEPLOY_LOCK_FD}<...`), a
  bash 4.1+ feature, so the script dies with
  `exec: {DEPLOY_LOCK_FD}: not found` and exit 127 before any assertion runs —
  e.g. `test_held_deploy_lock_fails_closed` reports `assert 127 == 75`, and 21
  further cases in `test_ib_gateway_control.py` fail the same way. Attributed
  by construction, not by count: a `git worktree` at `71de8a33` under the
  identical interpreter also reports `34 failed, 824 passed, 4 skipped` and
  the sorted `FAILED` lists are byte-identical. This does NOT contradict
  T-088's count of 12: that pass ran on a host whose `bash` resolved to a 4.x
  build, where only the `sha256sum` class fails. Two different missing GNU
  tools, same lesson — the darwin baseline is a LIST, and it is per-host.
  The cost is that a real
  control-plane regression is indistinguishable from the environment on any
  developer machine, so these tests are only ever truly run in CI. **AC:**
  RED — on a darwin host, `pytest cloud/tests -q` reports 34 failures with
  tracebacks that never reach an assertion about the code under test.
  GREEN — resolve the interpreter explicitly (prefer a bash >= 4 from
  `shutil.which("bash")` candidates) and add a module-level
  `pytest.mark.skipif` with the reason `"requires bash >= 4.1 (dynamic fd
  allocation)"` when none is found, so the suite reports SKIPPED with a cause
  instead of 34 opaque reds.
- **T-119 [P2] The Python test toolchain is unpinned and undeclared: CI
  installs `pytest pytest-asyncio pytest-cov` at floating latest and no repo
  file records them, so any other environment silently runs 16 async test
  files as hard failures.** `.github/workflows/ci.yml:128` is
  `pip install pytest pytest-asyncio pytest-cov` with no version constraint;
  `requirements.txt` and `requirements-forecasting.txt` contain no `pytest`
  entry, there is no `requirements-dev.txt`, and `pyproject.toml`'s
  `[tool.pytest.ini_options]` (`:19-49`) sets no `asyncio_mode` or
  `asyncio_default_fixture_loop_scope`. This run demonstrated the cost: the
  shared runner venv had `pytest 9.1.1` and no `pytest-asyncio`, so the first
  gate reported `107 failed` — every failure the message "async def functions
  are not natively supported" — on a tree that is green. 16 files carry
  `@pytest.mark.asyncio`. The second-order risk is CI's own determinism: a
  `pytest-asyncio` major bump changes the semantics of those 16 files with no
  repo commit, and `asyncio-1.4.0` already warns that the unset default loop
  scope is deprecated. **AC:** RED — in a clean venv,
  `pip install -r requirements.txt -r scripts/requirements-api.txt` then
  `python -m pytest`; today it reports 107 failures because nothing in those
  manifests pulls the test plugins. GREEN — add a `requirements-dev.txt`
  pinning `pytest`, `pytest-asyncio`, `pytest-cov` (and whatever
  `cloud/requirements-test.txt` needs), have `ci.yml:128` install from it, and
  set `asyncio_mode` explicitly in `pyproject.toml` so the plugin's default
  cannot move underneath the suite.
- **T-120 [P2] `countedUwFetch` adoption is guarded by a hardcoded four-file
  list rather than a walk of the tree, so any new Next.js route that fetches
  UW spends the quota invisibly and green.** The wrapper itself is correct and
  well-tested in isolation (`web/lib/uwCountedFetch.ts:19-25`;
  `web/tests/uw-counted-fetch.test.ts:26-71` proves one record per resolved
  response, none on a thrown fetch, and that a failed record cannot break the
  data path). The adoption guard is `web/tests/uw-counted-fetch.test.ts:74-94`:
  a literal `ROUTES` array of four paths, each checked with
  `(src.match(/api\.unusualwhales\.com/g) ?? []).length ===
  (src.match(/countedUwFetch\(/g) ?? []).length`. The list happens to be
  complete today (`grep -rl "unusualwhales.com" web/app` returns exactly those
  four), but it is a literal list, not a derived set, and it is a regex count
  over raw text — a `countedUwFetch(` inside a comment or a string satisfies
  it, and a UW base URL assembled from a variable matches neither regex and
  passes trivially. Mutation that stays green: add
  `web/app/api/ticker/flow/route.ts` doing a bare
  `fetch("https://api.unusualwhales.com/api/stock/${t}/flow-alerts")` —
  nothing fails, and traffic on that route is invisible to `/uw/usage` and to
  `should_block_universe_scan`, which is R-062 verbatim. **AC:** RED —
  replace `ROUTES` with a recursive walk of `web/app` collecting every file
  containing `unusualwhales`, assert the set is non-empty, assert each file
  imports `@/lib/uwCountedFetch` and contains no bare `fetch(` against a UW
  URL; add a fixture route with a bare UW fetch and confirm it reds. GREEN —
  the derived enumeration passes on the current four and fails automatically
  on the fifth.

### Not findings (checked, clean)

- **The three new indicator suites are date-injected, not clock-reading.**
  `test_ivrank.py`, `test_iei_hyg.py`, `test_credit_spread.py` and all eight
  new JSON fixtures under `scripts/tests/fixtures/` pass `now=NOW_RUN` rather
  than reading the wall clock. `scripts/tests/conftest.py` already isolates
  the UW budget, UW HTTP cache, dark-pool cache and JVM forensics dir, and
  neutralizes DUR-10 quiet windows.
- **Vol-cone percentile math is genuinely pinned, not self-asserting.**
  `percentile()` (`scripts/fetch_vol_cone.py:209-219`) was verified
  independently against `numpy.percentile` linear interpolation on the
  18-point fixture; `WEEKLY_P10/P90` (`scripts/tests/test_vol_cone.py:57-58`)
  are correct hand values, `rank_strictly_below` is pinned at three distinct
  checkable values (`:162-168`) and `classify_regime` bounds are pinned
  inclusively on both tails (`:192-207`). The `39439f1f` "unpin from a
  gitignored preset dir" change is correct: `:512-537` monkeypatches
  `_preset_tickers` and reads no runtime-owned path, with the degrade-to-seed
  branch explicitly pinned at `:524-528`.
- **`scripts/fetch_catalysts.py` has no wall-clock rot** — every test injects
  `FIXED_NOW` (`scripts/tests/test_fetch_catalysts.py:26`), the read-time
  recompute in `web/lib/catalystUpcoming.ts:78-93` means a stale snapshot's
  baked `days_until` is never trusted, and the FRED overlay's three refusal
  paths each have a test (`:311-370`).
- **The demo-mirror Turso 502 retry IS bounded and asserted** —
  `scripts/lib/demoMirrorReliability.test.js:216` pins `src.execute` at
  exactly 3 under `maxAttempts: 3`, and `:245` at exactly 1 for a
  non-transient SQL error. (The PURGE half of the same file is T-108.)
- **`web/lib/ibDailyPnlSession.ts` itself is behaviorally pinned with injected
  dates** (`web/tests/day-pnl-non-trading-day.test.tsx`,
  `web/tests/dashboard-kpis.test.ts:123-141`) — no wall-clock flake at the
  unit layer. The wall-clock exposure is only in the two untouched e2e specs
  (T-117) and the table horizon (T-102).
- **The six new indicator web suites are fixture-literal, not
  self-asserting** — `credit-spread-{api,panel}`, `iei-hyg-{api,panel}`,
  `ivrank-{api,panel}` assert against fixture literals over a real in-memory
  libsql, and their NaN loops were confirmed non-vacuous (`paths.length > 0`
  holds).
- **`order-dedup-ttl` brackets the real TTL** — 10s/20s literal bounds
  straddle the 15s `CONTENT_HASH_TTL_MS`, so the 300s → 15s change is pinned
  from both sides.
- **`cloud/tests/test_install_units.py` + `test_sync_scheduled_units.py`
  (736 lines) do pin the root helper's refusals** — manifest digest, symlink
  refusal, control-plane refusal, path token, non-GitHub remote and
  job-cancel class are all covered. The gap is the ROLLBACK path only
  (T-104).
- **`subprocess.py`'s `CancelledError` child-kill is parametrized over all
  three runners** (`scripts/api/tests/test_subprocess_order_lane.py:243`) —
  T-065's fix stayed pinned.
- **`backfill_journal_from_executed_orders.py`'s counted fingerprint, keyset
  paging and `written_at` delta re-check are covered**
  (`scripts/tests/test_backfill_journal_from_executed_orders.py:673-870`) —
  T-056's fix stayed pinned.
- **`grok_page_responder.attempt_oneshot_rerun`** has 14 tests including
  allowlist-vs-polkit pinning and the horizon boundary;
  `mediaPermissions.js`, `depthBudget.js`, `scan_health.py` and
  `drift_audit.py`'s expiry ratchet are all covered.
- **`site/lib/*.test.ts`** are otherwise sound — only the `llms-txt`
  published-file case (filed by the first pass as T-093) is vacuous; `accept`, `negotiate`, `openapi`,
  `markdown-pages`, `markdown-route`, `developer-pages` and
  `not-found-recovery` assert real behaviour.

### Runner-clone hygiene (operator note)

The clone was CLEAN at the start of this run — `git status --porcelain`
empty, `git stash list` empty, no orphaned WIP. The 2026-08-16 stash
(`testing-weekend-2026-08-16: parked pre-existing runner-clone WIP`) is NOT
present in this clone; that stash lived in the shared tree this loop no
longer uses. `rtk` is not installed on this runner, so the 2026-08-16
"always use `rtk proxy git`" rail does not apply here — bare `git` IS git on
this host, verified before any state decision was taken.

Two environment changes were made during the audit, both outside the repo:

1. `pip install pytest-asyncio pytest-cov` into the shared runner venv
   (`~/radon-weekend/venv`). Without it the pytest gate reports 107 false
   failures — see T-119. No repo file touched.
2. `git worktree add /tmp/wk-audit/base71 71de8a33`, used only to attribute
   the 34 cloud failures. Remove with
   `git worktree remove /tmp/wk-audit/base71`.

**Branch reconciliation.** This pass finished after the first pass had
already pushed `testing/weekend-2026-08-22` with T-080…T-096. Its commit was
NOT force-replaced: this branch was reset onto `f5c1c5a2` and this section
appended on top, with the eight converged findings dropped and the rest
renumbered from T-097. The first pass's audit text, ledger line and
`TEST_LOG.md` row are byte-unchanged.

## Delta audit 2026-08-23 (second host, surfaced by remediation)

### P1 — correctness gaps

- **T-121 [P1] Six rendered tables sit in no horizontal-overflow container at
  all — the 2026-08-18 vol-cone mobile bug, unfixed, in six other places.**
  Surfaced by inverting `web/tests/table-scroll-wrapper-contract.test.ts`
  under T-113: the old contract only inspected classNames that ALREADY
  contained `table-scroll`/`table-wrap`, so a table with no wrapper class was
  never looked at. Enumerating every `<table>` in `web/components` +
  `web/app` and requiring an ancestor with an `overflow-x` rule (global CSS or
  the component's CSS module) or an inline `overflowX` leaves six unwrapped:
  `WorkspaceSections.tsx:OrdersSections` and `:HistoricalTradesSection` (both
  inside `div.section > div.section-body`),
  `equibles-cot/EquiblesCotPanel.tsx:CotBoardTable` (`.data-table`, which has
  NO stylesheet rule of any kind),
  `flow-analysis/DailyDarkPoolHistory.tsx:DailyTable` (`.ticker-flow-daily`),
  `ticker-detail/RatingsTab.tsx:RatingsChangesTable` (`.pos-legs-table` inside
  `div.ratings-changes` — note `.pos-legs-table-wrap { overflow-x: auto }`
  exists at `globals.css:8577` and is simply not used here), and
  `ticker-detail/SeasonalityTab.tsx:SeasonalityDetailTable`
  (`div.seasonality-detail`). Verified per class: none of `.section-body`,
  `.data-table`, `.ticker-flow-daily`, `.ratings-changes`,
  `.seasonality-detail` names an `overflow-x` rule anywhere in `globals.css`
  or a module. Not a measurement artefact and not a design choice — the one
  table that IS deliberately unwrapped
  (`OptionsExposurePanel`, `table-layout: fixed` with per-column widths and
  ellipsis truncation) now says so with `data-overflow-exempt`. Pinned as
  `KNOWN_UNWRAPPED_T121` in the inverted contract under an EQUALITY assertion,
  so a seventh reds immediately and fixing one reds until its entry is
  removed. **AC:** RED — the entries above are the red list today; green —
  wrap each in a container carrying an `overflow-x` rule (or reuse
  `.pos-legs-table-wrap`), remove its `KNOWN_UNWRAPPED_T121` entry, and verify
  at 390px in a browser per Mandatory Rule 3. NOT fixed by the run that filed
  it: six UI changes need browser verification, which is outside a
  test-quality remediation task.


## Remediation 2026-08-23

- **T-080 DONE.** Operator decision: $10M is not policy; it stays the default
  but is modifiable. `_MAX_COMBO_LOSS_DOLLARS` replaced by
  `order_limits.max_combo_loss_dollars()` reading `RADON_MAX_COMBO_LOSS_DOLLARS`
  from `app_preferences` (default 10_000_000, band $10k..$50M, group "Order
  Limits", applies immediately — editable via `PUT /api/preferences`). Tests:
  `TestComboLossCapIsOperatorTunable` pins the default, the 70-lot / $9,098,600
  strangle clearing it, a lowered cap refusing the same order, the cap±1
  boundary, and env clamping at the $50M ceiling. Red 3/4 before the change;
  `test_order_limits.py` 33 green, `scripts/tests` 6229 green after.
- **T-081 DONE.** `load_flows_from_turso` (`scripts/perf_twr_builder.py`) buckets rows by `flow_type`: classified rows win per date over the builder's `external` mirror (`_MIRRORED_FLOW_TYPE`, reused by `_external_flow_rows`). New `TestLoadFlowsFromTursoCountsAMirroredRowOnce` (3 tests, real sqlite with 0035 applied, only `get_db` stubbed) in `tests/test_perf_twr_flows_turso_fallback.py`. Red 160014.26 for an 80007.13 deposit; green after. 210 perf-TWR tests green.
- **T-097 DONE.** `scripts/clients/ib_client.py` `wait_until` is now a wall-clock deadline (`time.monotonic()`) plus a `ceil(timeout/poll)` step cap. Red 1.24s wall for a 0.2s timeout under a 0.2s-overrunning sleep; green after. Step assertion relaxed to `<= ceil(timeout/poll)` (the `== 4` pinned the defect).
- **T-082 DONE.** `scripts/ib_sync.py` `wait_for_streaming_data` releases only on a valid account `dailyPnL` (`account_daily_pnl_is_ready` in `ib_client.py`). Red: nan `dailyPnL` + valid `unrealizedPnL` reported ready; green after. Guard tests for a quoteless ticker and a nan PnLSingle pin the two loops against deletion.
- **T-086 DONE.** Real-sqlite keyset test (`TestRehashKeysetPagingRealSqlite`, 25 rows / 2 accounts / tied `ingested_at` / `PAGE_SIZE=10`) in `scripts/tests/test_position_execution_fact_tolerated_hash.py`. Red under the AND-form WHERE mutation (10 of 25 rows skipped), green with the row-value comparison; source unchanged.
- **T-098 DONE.** `scripts/db/writer.py` tolerated-drift UPDATE carries the full denormalized column set through `_execution_fact_columns(item)`, shared with INSERT, so a converged row equals a fresh insert. Red: `price` column stuck at 4.15 after a 9.99 restatement, `multiplier` stuck at 100.0 after 1; green after. Option (a) chosen over widening the gate because `normalize_execution` documents that avgPrice drift must not raise.
- **T-102 DONE.** Holiday table extended 2028…2030 (29 dates, NYSE observance rules, independently re-derived and byte-matched). Both the TS and Python readers now carry a `currentYear+2` horizon assertion plus a derived MLK-Monday non-trading-day check, so the next expiry fails a year early instead of silently.
- **T-105 DONE.** Role-scoped counter reset pinned with a status()-driven fake and per-tick assertions; reconnect mirror added. Red 2 failed under the ANY-role mutation, green 8 passed with source unchanged.
- **T-083 DONE.** Per-position Day P&L now gated on the IB session via `withSessionIbDailyPnl()` in `PositionTable`; crypto carve-out kept. Red `+$13,952` rendered on a Saturday for an equity option; green blank, with weekday and crypto controls. 454 related tests green, tsc clean.
- **T-101 DONE.** Cancel All / Halt confirm gates now behaviorally pinned (zero POSTs before confirm, exactly one after, zero on dismiss). Red 4 failed under the direct-`runAction` mutation, green 7/7 with source unchanged.
- **T-107 DONE.** `usePathname` → context → `Providers` wiring pinned end to end. Red under both deletion mutations (empty pathname; provider removed), green 2/2 with source unchanged.
- **T-103 DONE.** `in_flight` deploy-collateral suppression bounded to the 60-min window and gated on a live (not stranded) transition journal, sharing `external_probe`'s rule. Red `['P3'] == ['P1']` for a 20h-old in-flight kill; green 297 in `test_watchdog/`; mutant caught.
- **T-084 DONE.** Idempotent-upsert tests for `iei_hyg_history` / `credit_spread_history` execute the production writers on a recording sqlite; dead SQL constants removed. Red 4 failed under a column-swap mutation, green after with the mutation reverted.
- **T-099 DONE.** Both indicator jobs now heartbeat `error` + `stale_source` when IB, UW and Yahoo all fail (mirrors `fetch_ivrank`); no cache raises. Red `('health', <svc>, 'ok')` written over unconfirmed data, green after; the legitimate unchanged-day `ok` path is pinned separately.
- **T-087 DONE.** Relay health detail now produced by an extracted builder the test imports; R-061 mutation reds 2, green 11/11 and 373 related.
- **T-108 DONE.** Demo-mirror account-table purge retries via `retryOperation` and fails the run on persistent error instead of warning. Red: transient 502 not retried, persistent 502 resolved `{failures: []}`; green after. Source-grep guard now reds when the loop is deleted.
- **T-085 DONE.** Suppressed-submit contract asserted behaviorally on all eight order surfaces; ChatPanel found genuinely defective (dedup message discarded) and fixed. Red 8/8 under the mutation (ChatPanel red unmutated), green 189 across 27 files.
- **T-100 DONE.** Flex 1025 embargo is now token-wide and durable: `service_health` row read back when the sidecar is missing, unwritable sidecar still arms via the row, `record_lockout` raises when neither sink lands. Three AC reds reproduced; green across 17 files. Pre-existing `hrana_execute`-for-SELECT bug in the monitor handler noted for the next audit.
- **T-106 DONE.** Vol-cone payload carries `intraday_count`; the panel labels a partial live pass honestly and marks un-refreshed rows AS OF their own last session. Red `KeyError: 'intraday_count'` / `LIVE 1/2 NAMES` not found; green after.
- **T-109 DONE.** GARCH and LEAP preset scans now honour the UW universe-scan brake with the theta harvester's degraded-telemetry row; the shell-default grep is a resolved-ticker-count guard (1666 ceiling; `indexes` at 2600 reds it). Five reds reproduced, green across nine files.
- **T-104 DONE (deploy control-plane change - needs operator eye).** Rollback now reverts exactly the units the failed release promoted via a helper-side transaction journal (`revert-units`); the restore branch call sequence is pinned. Red `'revert-units' in [...]` on a gate-failing deploy; green 24 in `test_install_units.py`, cloud FAILED list unchanged. Control-plane sources changed: root bootstrap run required before the first deploy.

## Delta audit 2026-08-25

Range `4985a7f8..27665c43` — 68 commits, 513 files, +36639/-2008. 187 test
files changed (90 added: 44 `scripts/tests|scripts/api/tests|tests` python,
7 `cloud/tests`, 29 `web/tests|site|lib`, 5 `web/e2e`; 97 modified). The
range contains the merged 2026-08-22 weekend PRs (#75 testing, #78/#85
reliability), the CI sharding rewrite (`424e66da`, #88), four new regime
indicators (DIVYIELD, HYAD, HHLEV, TRIN), and the orders realized-P&L
basis change (`c09fc347`/`7cfcdfd9`). PR #82 (`testing/weekend-2026-08-23`,
T-081…T-121 remediation, files **T-121**) is still OPEN and NOT in this
range; it was re-triaged from its branch only for numbering, which
continues at **T-122**. PART A (§1–§10) and the prior delta sections are
untouched. Six read-only agents fanned out over the rubric dimensions;
every finding below was spot-verified by the lead against the cited lines.

### Standing sweeps

**Gates from the repo root, serial (clean tree, HEAD `27665c43`, darwin
runner `Joes-Mac-Mini`, bash 3.2.57, homebrew `sha256sum` present):**

| Gate | Round 1 |
|---|---|
| `python3.13 -m pytest` (recursive, = pre-shard CI) | 7816 passed, 1 skipped, 90 deselected (274.4s) |
| `npx vitest run` (root config) | 701 files / 7328 passed, 0 failed |
| `python3.13 -m pytest cloud/tests -q` | **34 failed**, 1025 passed, 4 skipped (185.7s) |

- The 34 cloud reds are the T-118 darwin baseline: `git worktree add
  /tmp/tw0825/base 4985a7f8` + the same invocation → `34 failed, 888
  passed, 4 skipped`, sorted `FAILED` lists **byte-identical** (`diff`
  empty). Delta: +137 cloud passed, 0 new reds. 13 in
  `test_bootstrap_control_plane.py`, 21 in `test_ib_gateway_control.py`.
- CI at the same SHA (run 32808681820) is green — but see **T-122**: CI's
  pytest is no longer the recursive invocation this table records.

**Determinism, scoped to the delta's ADDED test files (3× each, serial,
after the gates):** 44 python files `483 passed` ×3 (45.3/45.1/45.0s); 29
vitest files `238 passed` ×3; 7 cloud files `92 passed` ×3. The 97
modified test files were covered by the single full-gate round only; the
scoped ×3 over 187 files would have been three more full runs, and the
skill's own lesson says to say so rather than pretend.

**Gate reach — RED.** The sharded pytest matrix (`ci.yml:161-178`)
expands `scripts/tests/test_[a-c]*.py`-style globs that match FILES only:
`scripts/tests/test_monitor_daemon/` (33 files) and
`scripts/tests/test_watchdog/` (23 files) are collected by no job.
`--collect-only` full: 463 files; union of the eight shard path sets: 407;
difference = exactly those 56 files, **752 tests**. CI evidence: shard sum
7064 passed vs `7788 passed` on the last pre-shard run (job 97594311235,
`7cfcdfd9`); combined coverage `TOTAL 53304 19630 63%` vs `65.80%`
pre-shard, ratchet 56 still green. Filed as **T-122 (P0)**. Vitest
sharding is complete: 8 shards = 698 files / 7293 tests (88+88+87×6),
matching the pre-shard 695/7281 plus this delta's 3 new files.

**Coverage-ratchet honesty — two moves, neither reported.** (1)
`--cov-branch` was dropped from the pytest invocation (`ci.yml` diff
`-… --cov=scripts --cov=api --cov-branch --cov-report=term-missing
--cov-fail-under=56` → `+… --cov=scripts --cov=api --cov-fail-under=0`)
and `pyproject.toml` gained only `relative_files = true`; the 56 ratchet
now scores statement-only (`Stmts Miss Cover` header, no `Branch`
column), the metric it was rebased on under T-050 was statement+branch —
**T-123**. (2) `vitest.config.ts:60-63` thresholds unchanged at 75/71/65
and the new `scripts/ci/merge_vitest_coverage.py` reproduces the pre-shard
`All files` line to within 0.05pt, but its denominator is whatever the
shards emitted and `web/lib/**/*.tsx` is outside every `include` glob —
**T-135**. No blanket exclude added; the `--exclude` trio is unchanged.

**New skips — none.** 36,639 added lines parsed (python3.13 over the
patch) for `test|it|describe.(skip|only|todo|fixme)`,
`pytest.mark.skip|skipif|xfail`, `pytest.skip(`: zero hits; every `xit(`
match is `exit(`. No `.only(`.

**CI workflow delta — 256 lines.** Sharding (above); `deploy.needs`
gained `web-coverage`, `py-coverage`, `cloud-tests` (`ci.yml:494`);
`cloud-tests` invocation unchanged; `-m 'not integration'` still applied
via `addopts` (local collect on the `scripts-ghjm` globs = CI's `[1115
items]`); `DOCS_CONTRACT_BASE` + `fetch-depth: 0` only on `scripts-df`,
which is the shard that holds the only history-dependent test
(`test_docs_contract.py`). The Playwright job is still `(non-gating)`,
its curated list (`ci.yml:468-483`) and `web/playwright.config.ts` are
byte-unchanged while the delta added five specs (T-090/T-116 evidence,
not refiled). The new "Demo isolation guard" step is a permanent no-op —
**T-130**.

**Runner toolchain:** `python3.13 -c "import pytest_asyncio"` ok, node
v24.14.0, `node_modules/.bin/vitest` present, tree clean, no stash. The
T-119 toolchain pins landed in this delta (`requirements-dev.txt`:
pytest 9.0.2 / pytest-cov 7.1.0 / xdist 3.8.0).

### Re-triage of the standing NEW_FINDINGS items

- **E2E testid backlog — STILL OPEN, still zero movement on the money
  surfaces.** Base-vs-HEAD `data-testid` counts: `MetricCards` 4 → 4,
  `SharePnlButton` 0 → 0, `SingleLegOrderTicket` 1 → 1, `Toast` 0 → 0,
  `ModifyOrderModal` 0 → 0, `CancelOrderDialog` 0 → 0. The five new
  regime/book specs key on CSS classes again — **T-139** (delta to T-091).
- **`next dev` in the CI Playwright container — STILL OPEN (infra).**
  `web/playwright.config.ts` zero-line diff; CI still overrides with
  `PLAYWRIGHT_WEBSERVER_CMD`.
- **`pytest cloud/tests` red on macOS — STILL OPEN (T-088/T-118).** 34 on
  this host, byte-identical to the base SHA. One NEW hard dependency on a
  GNU `sha256sum` binary landed in the delta — **T-141**.
- **Unreproduced 10-failure vitest round (2026-08-17) — NOT reproduced.**
  One full round `7328 passed`, plus 3× of the 29 added files under
  agent load; still no named tests.
- **Day Move `next start` vs `next dev` / T-117 — MOVED, and the T-117
  remedy is now wrong.** `web/lib/ibDailyPnlSession.ts:32-40` compares
  the snapshot's `last_sync` date with the clock; pinning `page.clock`
  to a Wednesday with the specs' `new Date()` fixtures renders MARKET
  CLOSED — **T-154**.
- **`e2e/performance-twr-payload.spec.ts` — RESOLVED (T-079), still
  uncurated.** Unchanged this delta.

### Convergences (two agents, same file:line, filed once)

| Finding | Agents | What both landed on |
|---|---|---|
| T-122 | lead, blast-radius | shard globs cannot match `test_monitor_daemon/`, `test_watchdog/` |
| T-125 | money-path, net-negative | `ModifyOrderModal.tsx:397-401` exclude arg has no modal-level test |
| T-126 | money-path, net-negative | `evening_execution_sweep.py:286` overlay call deletable green |
| T-130 | gate-drift, net-negative | `check_demo_isolation` step satisfied by a comment; never executes |
| T-136 | gate-drift, fragility | `_free_port` bind-0/close/rebind under `-n auto` |

### P0 — money-losing gaps

- **T-122 [P0] CI's sharded pytest no longer collects
  `scripts/tests/test_monitor_daemon/` or `scripts/tests/test_watchdog/`:
  752 tests over the fill monitor, exit orders, journal sync, cash-flow
  sync, throttle ladder and the entire watchdog gate nothing, and CI reads
  green.** `.github/workflows/ci.yml:161-178` (`paths:
  "scripts/tests/test_[a-c]*.py"` … `"scripts/tests/test_[r-s]*.py"`,
  `scripts/api/tests`, `scripts/trade_blotter tests`), executed at
  `:219` as `python -m pytest ${{ matrix.paths }}`. A glob ending in `.py`
  cannot match a directory and no shard names either subdir. Pre-shard the
  invocation was recursive (`git show 4985a7f8:.github/workflows/ci.yml`
  `:157`). Measured: `--collect-only` recursive 463 files vs shard-union
  407; `pytest --collect-only scripts/tests/test_monitor_daemon
  scripts/tests/test_watchdog` → `752 tests collected`. CI: shard sum 7064
  passed (jobs 97683726376…97683726687) vs `7788 passed` at `7cfcdfd9`
  (job 97594311235); coverage 65.80% → 63%, ratchet 56 untouched, deploy
  ran. Every test T-055…T-121 wrote under those two dirs (T-063 evening
  sweep, T-094/T-110 disk, T-111 suppression bounds, T-103 in-flight
  suppression, the exit-orders OCA test added in this delta) is now
  decorative. The delta's own guard,
  `scripts/tests/test_ci_deploy_concurrency.py:181-200`, asserts the shard
  NAMES and `"test_[a-c]" in include`, never the union.
  **AC:** RED — a test that expands every `matrix.include[*].paths` glob
  against the tree and asserts set-equality with `pytest --collect-only -q`
  over `scripts/tests` (fails today by 56 files). GREEN — a ninth shard
  (`paths: "scripts/tests/test_monitor_daemon scripts/tests/test_watchdog"`)
  or `.py`-less globs; keep the union test. Re-measure the combined
  coverage and record it (it will rise back toward 65.8); the threshold
  stays 56 per T-050.

### P1 — correctness gaps

- **T-123 [P1] The pytest coverage ratchet silently changed metric from
  statement+branch to statement-only.** `ci.yml` removed
  `--cov-branch --cov-report=term-missing` with the shard rewrite
  (`git diff 4985a7f8..HEAD -- .github/workflows/ci.yml` lines
  `-          --cov=scripts --cov=api --cov-branch --cov-report=term-missing`
  / `+          --cov=scripts --cov=api`); `pyproject.toml:12-20` has no
  `branch = true`; `ci.yml:269` applies `--fail-under=56` to the combined
  file. Pre-shard job 97594311235 printed `Name Stmts Miss Branch BrPart
  Cover` / `TOTAL 53152 17089 16094 2207 66%`; post-shard job 97683987384
  prints `Name Stmts Miss Cover` / `TOTAL 53304 19630 63%`. Statement-only
  ran ~2pt above combined pre-shard (67.85% vs 65.80%), so the comparable
  combined figure today is ~61%, unmeasured; the deleted T-050 comment in
  `ci.yml` stated the rebase was on "combined statement+branch". Neither
  `TEST_AUDIT.md` nor `TEST_LOG.md` mentions the shard change (grep
  `shard|424e66da`: 0). A dropped `else`/guard on exit-orders or
  order-limits no longer moves the number at all. **AC:** RED — `coverage
  report` on the combined data prints no `Branch` column. GREEN — `branch =
  true` under `[tool.coverage.run]` (or restore `--cov-branch` at
  `ci.yml:221`), re-measure with T-122 fixed, log the figure in
  `TEST_LOG.md`; if it lands under 56, report per T-050, never lower.
- **T-124 [P1] `journal_realized` replay treats a `CLOSED` row as an
  opening short and can stamp a journal realized P&L onto an OPENING fill;
  a close journaled under two id namespaces silently falls back to IB's
  drifted figure.** `scripts/clients/journal_realized.py:35`
  (`CLOSING_ACTIONS = {"SELL_OPTION", "BUY_TO_CLOSE"}` — no `CLOSED`),
  `:155-184` (`_journal_entry` accepts `CLOSED` because
  `journal_basis._signed_qty` at `journal_basis.py:93` maps it to −qty),
  `:195-232` (replay dedupes only by `_claim_exec_parts`), `:112-121`
  (`apply_journal_realized_pnl` keys on `execId` alone, never the side).
  `journal_basis.py:108-133` already documents both hazards
  (`contract_fill_fingerprint` exists for the API-execId/Flex-tradeID
  namespace split and explicitly skips `CLOSED` at `:133`); the new module
  uses neither. `scripts/journal_rehydrate.py:229,431-445` emits `CLOSED`
  rows with `fill_price`/`contracts`/composite `ib_exec_id` from Flex.
  Reproduced in-process: rows `[CLOSED a+b 10@2.00, BUY_OPTION o1
  10@1.00]` → `{'o1': 998.0}`; rows `[BUY o1 10@1, SELL e2 10@3, SELL 777
  10@3]` → `{}`. The delta's test `scripts/tests/test_journal_realized.py`
  covers only same-namespace composites (`:121-128`) and never a `CLOSED`
  row. Consequence: `WorkspaceSections.tsx:168` (`isClosing =
  e.realizedPNL != null`) shows the re-entry OPEN as a +$998 close and
  `web/lib/realized-pnl.ts:55` adds it to day-realized; the dual-namespace
  case keeps `realizedPNLSource: "ib"` on exactly the fills `c09fc347`
  shipped to correct. **AC:** RED — (a) the `CLOSED`+`BUY` case must yield
  no `o1` key and `apply_journal_realized_pnl` must leave a `BOT`-side
  fill untouched; (b) the dual-namespace case must yield `{"e2": 2000.0}`.
  GREEN — skip `CLOSED` like `journal_basis.py:133`, dedupe by
  `contract_fill_fingerprint` before replay, gate `apply_` on the closing
  side.
- **T-125 [P1] `bc08e87b` (modify modal counted its own order against
  held combo units) is guarded only at the pure helper; the one-line
  wiring that was actually broken can be reverted green.**
  `web/components/ModifyOrderModal.tsx:397-401` passes `{ permId, orderId
  }` as the exclude arg; `web/tests/combo-close-out-working-orders.test.ts:138-148`
  calls `workingSellComboUnits(...)` directly with its own `exclude`.
  `web/tests/modify-order-close-pnl.test.tsx` renders the modal seven
  times (`:108,123,138,351,380,401,422`) with zero `openOrders`, so
  `ModifyOrderModal.tsx:304` defaults it to `null` and
  `workingSellComboUnits` is always 0 — dropping the third argument at
  `:400` keeps every test green. Failure: modify a working SELL 250×
  combo against 250 held → `workingSellUnits=250` consumes the position →
  opening credit-spread branch → Max Gain / Max Loss instead of Est.
  Realized P&L, broker what-if on a "new" spread. **AC:** RED — render
  `ModifyOrderModal` with `order.permId=9001` and
  `openOrders.open_orders=[that same 250-unit order]`, portfolio holding
  250; expect `Est. Realized P&L` present and `Max Loss` absent; fails
  with the exclude arg removed or keyed on the wrong id. GREEN — current
  code.
- **T-126 [P1] The evening-sweep half of `7cfcdfd9` (persist journal
  realized P&L at write time) is unwired-green: the overlay call is
  deletable and its `try/except` swallows the import.**
  `scripts/monitor_daemon/handlers/evening_execution_sweep.py:110-118`
  (`except Exception: logger.warning("… overlay skipped")`), `:286` (the
  call). `scripts/tests/test_monitor_daemon/test_evening_execution_sweep.py`
  has no `overlay|get_db|journal_realized` reference; `:246-247` mocks
  `upsert_executed_order` without asserting payload P&L. Contrast the
  `ib_orders` leg, which has a real test
  (`scripts/tests/test_ib_orders_dual_write.py:271-291`). After-hours
  fills are mirrored ONLY by the sweep (`ib_client.py:999-1002` `fills()`
  is session-scoped), so an outsideRth close keeps IB's drifted
  `realizedPNL` in `executed_orders` permanently. And per T-122, this
  file is not run by CI at all today. **AC:** RED — sweep test with
  journal rows for the contract and `db.client.get_db` stubbed (the
  `_FakeDb` in `test_journal_realized.py:199-209`), asserting the mirrored
  payload carries `realizedPNL == journal value` and `realizedPNLSource ==
  "journal"`; second case `get_db` raising → IB figure kept AND a warning
  logged. Must catch removing `:286`.
- **T-127 [P1] `run_module` spawns without `start_new_session=True`; the
  R-136 "children are signalled as a group" test is a substring check
  that two of three sites satisfy, and on the third the cancel helper
  `killpg`s the API's own process group.** `scripts/api/subprocess.py:284-289`
  and `:366-371` carry `start_new_session=True`; `:420-425` (`run_module`)
  does not; `:236-238` `_terminate_child` does `os.killpg(os.getpgid(pid),
  signal.SIGKILL)`. `scripts/tests/test_silent_degradation_bounds.py:173-178`
  asserts `"start_new_session=True" in src`; its `_Proc` fakes
  (`:125-140`) define no `pid`, so the `killpg` branch executes in no
  test. `scripts/api/server.py:4104` calls
  `run_module("trade_blotter.flex_query", ["--json"], timeout=120)`;
  uvicorn is the unit's main process (`cloud/services/radon-api.service:44`).
  A `/blotter` Flex fetch over 120s (routine under a 1018/1025 throttle)
  → `asyncio.TimeoutError` → `_terminate_child` → `getpgid(child)` is
  uvicorn's group → SIGKILL lands on radon-api. **AC:** RED — parametrize
  over `run_script`, `run_script_raw`, `run_module` with a real `sleep 30`
  child; assert `os.getpgid(proc.pid) != os.getpgid(os.getpid())` before
  termination and that the test process survives `_terminate_child`;
  `run_module` fails today. GREEN — `start_new_session=True` at `:420`,
  and `_terminate_child` refuses `killpg` when the child shares the
  caller's group.
- **T-128 [P1] The R-179/R-180/R-186 auth-perimeter fixes on the
  WS-ticket, GARCH-scan and service-health routes are pinned by regexes
  over route SOURCE; no behavioural test exercises the guard, the rate
  limit, or the reduced probe payload.**
  `web/tests/auth-perimeter-delta.test.ts:21-37`
  (`toContain("requireRouteAccess")`, `not.toMatch(/^\s*token,\s*$/m)`,
  `toMatch(/access\.principal|principal\.token/)`), `:52-65` (`guardAt <
  workAt` via `indexOf`, `toMatch(/rate:\s*\{/)`), `:157-174`
  (`toMatch(/probeRate|rateLimit|checkRate/i)`,
  `toMatch(/probeView|probePayload|forProbe/)`). Behavioural siblings never
  cover the case: `web/tests/ws-ticket-local.test.ts:19,37,56`,
  `web/tests/leap-garch-scan-route.test.ts:32-152` (no 401/429),
  `web/tests/service-health-probe-bearer.test.ts:48-77` (auth only).
  Sources `web/app/api/ib/ws-ticket/route.ts:20-28`,
  `web/app/api/garch-convergence/scan/route.ts:24-25,56`,
  `web/app/api/service-health/route.ts:118-131,202-203`. Mutations that
  stay green: delete the `requireRouteAccess` CALL and keep the import;
  `token: undefined,` (not matched by the shorthand regex); a comment
  naming `rateLimit`; dropping `rows.map(forProbe)` at `:202` while
  `forProbe` at `:118` remains. **AC:** RED — import each handler with
  `routeAccess` mocked denied → 401 and `radonFetch` not called; allowed →
  upstream receives `token: <principal token>`; service-health: 31st probe
  in a minute → 429, probe body has no `last_error` while an operator body
  does. GREEN — current code; delete the regex file.
- **T-129 [P1] `test_a_degraded_payload_records_error` monkeypatches
  `_record_perf_twr_health` and then asserts on the lambda it installed;
  the real writer, which swallows every exception, never runs.**
  `scripts/tests/test_watchdog_catalog_and_journal_bound.py:129-140`
  (`monkeypatch.setattr(builder, "_record_perf_twr_health", lambda …)`,
  then `builder._record_perf_twr_health("degraded", …)`, `assert
  recorded[0][0] == "degraded"`); `:122-127` is `"record_service_health"
  in builder or "service_cycle" in builder`. Source
  `scripts/perf_twr_builder.py:1662-1671` (`except Exception: print(…
  non-fatal)`), `:1697-1710`. R-159 was "perf-twr writes no row on a Flex
  1025": replace the wrapper body with `pass` or break the `db.writer`
  import — both tests green, no row written, `check.py` reads "no row" as
  dormant. **AC:** RED — patch `db.writer.record_service_health`, call the
  real `_record_perf_twr_health("degraded", error=…)`, assert one call
  `("perf-twr", "error", …)`; then `main()` with `build_and_persist`
  stubbed to `{"status": "degraded"}` asserting the row. Must catch
  `_perf_twr_state("degraded")` → `"ok"`.
- **T-130 [P1] The "Demo isolation guard" CI step is a permanent no-op —
  the secrets it reads do not exist — and the test that says it is
  "wired" is satisfied by a comment.** `.github/workflows/ci.yml:278-288`
  exits 0 when both `TURSO_DEMO_DB_URL` and `TURSO_DB_URL` are empty; job
  97683987384 prints `no demo env configured on this runner — nothing to
  check`; `gh api repos/joemccann/radon/actions/secrets` lists only
  `TURSO_AUTH_TOKEN`, `TURSO_DB_URL`, the job binds no `environment:`,
  and `environments/production/secrets` has neither demo key.
  `scripts/tests/test_demo_isolation_is_wired.py:75-80` asserts
  `"check_demo_isolation" in workflow`, which the comment at `ci.yml:270-277`
  satisfies with the step deleted. `docs/demo-environment.md:27` names
  the wrong job. R-156/REL-065 closed as "wired" while the guard's only
  executions are the unit test's dict fixtures. **AC:** RED — a test that
  parses `ci.yml` as YAML and asserts a step whose `run` invokes the
  script AND that it cannot short-circuit (no unconditional `exit 0`), or
  a step that runs `check_demo_isolation.py` against a committed demo env
  file so exit 1 is reachable. GREEN — provision the two secrets on the
  runner (operator) or check in a demo env fixture; fix the doc.
- **T-131 [P1] TRIN's R-099 "delayed print may not promote to IN ZONE"
  guard is dead in production: `build_output` calls `classify_state(ma10)`
  without `source`, and the only test of the guard calls the classifier
  directly.** `scripts/fetch_trin.py:249` (`"state": classify_state(ma10)`),
  `:140-154` (guard fires only when `source is not None`);
  `scripts/tests/test_honest_exhaustion_oneshots.py:130-148` passes
  `source=sample["source"]` by hand; `scripts/tests/test_trin.py:171-179`
  drives `build_output` with samples carrying no `source`. Coverage of
  the four indicator suites: `fetch_trin.py:154` never executed.
  `web/components/TrinPanel.tsx:179-206` colours on `current.state`. IB
  answers type 3/4 at 10:00 ET → `_build_sample` records
  `source="ib-delayed"` (correct) → `build_output` renders a live IN ZONE
  badge off a 15-minute-old MA(10), heartbeat `ok`. REL-049 recorded R-099
  as DONE. **AC:** RED — `build_output([ten samples trin=0.60,
  source="ib-delayed"], daily)["current"]["state"] != "in_zone"` (returns
  `"in_zone"` today). GREEN — pass the latest sample's `source` (or the
  window's dominant source) at `:249`.
- **T-132 [P1] (delta to T-084) The DIVYIELD/HYAD/HHLEV/TRIN
  "idempotent upsert" tests execute DEAD SQL constants or a hand-built
  tuple; none of the four production writer bodies is executed by any
  test.** `scripts/db/writer.py:972-983` `DIVYIELD_UPSERT_SQL` is unused by
  `upsert_divyield_rows` (`:986-1024`, inline SQL at `:1015-1022`);
  `:1027-1037` vs `:1040-1077` (HYAD); `:1080-1089` vs `:1092-1131`
  (HHLEV); `:1209-1226` `_trin_sample_params` is called only from the
  writer. Tests: `scripts/tests/test_divyield.py:324-335`,
  `test_hyad.py:346-356`, `test_hhlev.py:383-394` (`db.execute(writer.X_UPSERT_SQL, …)`),
  `test_trin.py:260-268`; every `TestPersistResult` stubs the writer.
  Replacing all six writer functions with raising stubs leaves the four
  `TestStorage` classes `19 passed` (only the three `test_writer_arity`
  signature checks fail). A column swap in `params.extend(...)`
  (`count_above`/`total`, `adv`/`dec`) ships to Turso on the next timer
  fire with green CI. **AC:** RED — recording-sqlite test on the REAL
  `upsert_*_rows` (pattern `test_ivrank.py:339-378`) called twice for one
  date, asserting the SELECT-back row and a single row; swap two params →
  red. GREEN — no source change; delete the three dead constants.
- **T-133 [P1] `run()` of DIVYIELD, HYAD and HHLEV is never executed; a
  regression that stops writing history rows while heartbeating `ok` is
  invisible.** `scripts/fetch_divyield.py:473-512` (`rows = [new_row]`
  `:494`, `persist_result(payload, rows)` `:511`);
  `scripts/fetch_hyad.py:385-414` (`changed` `:402-404`, persist `:413`);
  `fetch_hhlev.py:323-334`. No test imports `run`, `fetch_window`,
  `fetch_constituents`, `load_history` or `_turso_history`
  (`test_divyield.py:25-36`, `test_hyad.py:26-33`, `test_hhlev.py:35-44`).
  Coverage: `fetch_divyield.py` missing `159-176, 422-428, 474-512`;
  `fetch_hyad.py` missing `303-329, 376-382, 388-414`; `fetch_hhlev.py`
  missing `324-334`. `changed` computed against a stale `stored_by_date`
  → `persist_result(payload, [])` daily: snapshot + `ok` heartbeat
  written, `*_history` never grows, the 26h/120h watchdog windows stay
  green. **AC:** RED — drive `run()` with the fetchers and
  `_turso_history` stubbed (populated history, one new date) and assert
  `("rows", 1)` in the recorded writer calls; mutate `changed → []` /
  `rows → []` → red. GREEN — no source change.
- **T-134 [P1] `test_grok_responder_fails_closed.py` pins the Grok
  agent's sandbox to a secrets path the delta EMPTIED, and the assertion
  is satisfied by a comment; the new secrets file is readable by the
  agent.** `a0716084` moved secrets to `/etc/radon/env`
  (`cloud/scripts/setup-vps.sh:21`, `:473-475` chmod 0600 chown
  radon:radon); `cloud/services/radon-grok-page-responder.service:9`
  runs `User=radon` and `:27` lists `InaccessiblePaths=/var/run/docker.sock
  /usr/bin/docker /usr/local/sbin/radon-deploy-root /home/radon/radon-cloud`
  — `/etc/radon/env` absent. `scripts/tests/test_grok_responder_fails_closed.py:160-164`
  asserts `"/home/radon/radon-cloud/.env" in unit`, matched by the comments
  at unit `:11` and `:24`; `cloud/tests/test_p2_host_paths.py:25-27`
  explicitly exempts this unit. Green while the agent can read IB Flex,
  Clerk and UW secrets. **AC:** RED — assert `/etc/radon/env` appears in
  the unit's `InaccessiblePaths=` line (parse the directive, not the
  file). GREEN — add it, keep the legacy path.

### P2 — fragility / structure

- **T-135 [P2] (delta to T-072) The merged vitest denominator is
  execution-dependent for `web/lib/**/*.tsx`; the merge script never
  reconciles against `include`.** `vitest.config.ts:66-72` (`web/lib/**/*.ts`
  — no `.tsx`; the two explicit `.tsx` excludes at `:76-77` show the leak
  is known); `scripts/ci/merge_vitest_coverage.py:54-72` unions whatever
  the shards emitted. Run 32808681820 artifacts: shard 1 = 665 files /
  27005 lines, shard 5 = 663 / 26867; `web/lib/ThemeContext.tsx`,
  `web/lib/og-charts.tsx` present only where executed; of 19
  `web/lib/**/*.tsx` on disk, `IBStatusContext.tsx` and
  `RealtimeAuthContext.tsx` never enter the denominator. Direction is
  inflation. (Otherwise honest: merged 78.84/74.86/66.90 vs pre-shard
  78.81/74.81/66.89.) **AC:** RED — `scripts/tests/test_merge_vitest_coverage.py`
  case asserting every merged key matches an `include` glob (fails on
  `ThemeContext.tsx`). GREEN — add `web/lib/**/*.tsx` to `include`,
  re-measure, report per T-050 if a threshold dips.
- **T-136 [P2] `_free_port` bind-0/close/rebind stubs plus a 10s dead
  sleep, now run under `-n auto --dist loadfile`.**
  `scripts/tests/test_leap_garch_no_duplicate_scan.py:49-55` (used
  `:192,:210,:229`; `:229` `test_connection_refused_still_falls_back` needs
  NOBODY on a port it just released), `:80` `time.sleep(10)` on a
  single-threaded `HTTPServer` whose `__exit__` `shutdown()` (`:99-100`)
  blocks for it (measured 10.5s/10.3s per param, `6 passed in 23.23s`);
  `scripts/tests/test_run_flow_refresh_wrapper.py:28-34` (used
  `:216,:236,:262,:281,:298`); `test_run_vcg_refresh_wrapper.py:35`,
  `test_run_signals_refresh_wrapper.py:42`, `test_run_portfolio_refresh_retry.py`
  — all `scripts-rs` (`ci.yml:183-184`), 4 workers, no `xdist_group`
  anywhere in the gated dirs. **AC:** RED — two copies in parallel workers
  with a forced identical port → refused-case fails. GREEN — stubs bind
  `("127.0.0.1", 0)` and read `server_port`; refused case targets a socket
  the test holds bound-but-not-listening; the hang handler waits on a
  `threading.Event` released in `__exit__`.
- **T-137 [P2] Wall-clock `elapsed < 1.0` assertions with ~0.6s headroom
  in the TimeoutStartSec tests, and a 1s real-time window in the flow-tab
  cooldown test.** `scripts/tests/test_bpi_scan.py:378-391,398-411`,
  `scripts/tests/test_divyield.py:357-368` (0.4s faked sleeps vs
  `SWEEP_BUDGET_S=0.15`, source waits in-flight workers:
  `fetch_divyield.py:266`, `bpi_scan.py:334`);
  `scripts/api/tests/test_flow_tab_cooldown.py:93-98` (`_flow_tab_last =
  monotonic() - (COOLDOWN - 1)` then two `TestClient` POSTs inside the
  second). 3× isolated `test_divyield.py`: 32/32/32 in 1.63/1.33/1.03s —
  a load-margin risk, not an observed flake. **AC:** RED — raise the fake
  latency to 0.9s → fails with the budget logic unchanged. GREEN — assert
  the deterministic observable (`errors >= len(tickers) - FETCH_WORKERS`,
  `len(fetched) <= FETCH_WORKERS`, hang call-count `== 1`); inject
  `time.monotonic` in the cooldown test.
- **T-138 [P2] `test_relay_container_watchdog.py` gives a cold Python
  interpreter 0.5s to deliver `READY=1`.**
  `cloud/tests/test_relay_container_watchdog.py:26` (`WATCHDOG_SEC =
  0.25`), `:70-76` and `:92-96` (`_collect_notify(..., monotonic() +
  WATCHDOG_SEC * 2)` then `assert "READY=1" in messages`); child is
  `Popen([sys.executable, "-c", CHILD])` at `:64-69`. Durations 0.52/0.51s
  — the window is consumed by design. **AC:** RED — shim `sys.executable`
  with `sleep 0.6` before exec → both fail. GREEN — block on `READY=1`
  with a 10s bound, then open the `WATCHDOG_SEC*2` observation window.
- **T-139 [P2] (delta to T-091) Four new regime specs and the book spec
  key state on CSS classes where `aria-current` exists.**
  `web/e2e/divyield-tab.spec.ts:99`, `hhlev-tab.spec.ts:99`,
  `hyad-tab.spec.ts:100`, `trin-tab.spec.ts:91`
  (`.regime-rail__item[data-tab=…]` `toHaveClass(/active/)`) while
  `web/components/RegimeRail.tsx:70` renders `aria-current="page"`; also
  `svg path[stroke]` at `:117/:117/:119/:110`;
  `web/e2e/book-depth-client-nav.spec.ts:132` `.book-feed-pill`
  (`OrderBook.tsx:126`). **AC:** RED — rename the `active` class → four
  specs fail with no behaviour change. GREEN —
  `getByRole("button", { name: /Dividend Yield/ })` +
  `toHaveAttribute("aria-current", "page")`.
- **T-140 [P2] Calendar-triggered reds (bundle).** (a)
  `web/tests/display-honesty-delta.test.ts:117-125` asserts
  `market_holidays.json` contains `thisYear + 1` from
  `new Date().getUTCFullYear()`; keys are `2023…2027`, so the vitest gate
  goes red at 2027-01-01T00:00Z with no code change — a tripwire in a
  merge gate. (b) `cd15f40a` "midnight-safe test dates" is incomplete:
  `scripts/tests/test_hhlev.py:55,350` (import-time `date.today()`) vs
  `:206` (call-time) — not red today because nothing compares the two
  clocks. **AC:** (a) RED — `vi.setSystemTime("2027-01-01")` → fails now;
  GREEN — add 2028 now and freeze the test clock to a fixed date that
  documents the horizon (or move the horizon check to a scheduled job).
  (b) single `_TODAY` in `test_hhlev.py`.
- **T-141 [P2] (delta to T-088) `test_refresh_control_plane.py`
  hard-asserts a GNU `sha256sum` binary while the same file ships a
  `shasum -a 256` shim.** `cloud/tests/test_refresh_control_plane.py:498-499`
  (`assert shutil.which("sha256sum") is not None`), consumed at
  `:524-535,:538-544`; shim at `:131-135`. Passes here only because
  `/opt/homebrew/bin/sha256sum` exists. **AC:** RED — run with homebrew
  off PATH → both preflight tests `AssertionError`. GREEN — write the
  `:131-135` shim into `tmp_path` and pass it as `RADON_SHA256SUM`.
- **T-142 [P2] The display/serving honesty tranches (R-124/R-125/R-135/
  R-193/R-196/R-167/R-168) guard component and route WIRING by
  substring; the behavioural halves test the helper only.**
  `web/tests/panel-freshness-honesty.test.ts:27-39`
  (`toContain("PanelRefreshError")` — `TrinPanel.tsx:165` `error={null}`
  stays green; no other test references the component), `:65-75`
  (`toMatch(/result\.ok && result\.fresh/)`) and
  `web/tests/serving-honesty-delta.test.ts:27-31`
  (`toContain("isDegraded")` — `web/app/api/trin/route.ts:53` `isDegraded:
  () => false` stays green; `trin-api.test.ts` has no
  fresher-degraded-vs-older-real case), `:120-143` (lozenge by
  `indexOf`/first-statement regex — `CashFlowsSection.tsx:134-152`, moving
  the early return one statement down reintroduces R-135 green),
  `serving-honesty-delta.test.ts:147-161` (`FLAGGABLE_REGIME_TABS`
  substrings), `web/tests/relay-disconnect-honesty.test.ts:83-92,127-146`
  (`toContain("disconnected")` satisfied by `EventName.disconnected` at
  `ib_realtime_server.js:2425`; deleting the write at `:2760-2778` stays
  green — T-087 class). **AC:** per item, render/route the observable:
  `TrinPanel` with `{ error: "boom", data }` → `trin-refresh-error`
  visible; `GET /api/trin` with a newer degraded db row and an older real
  disk row → `source === "disk"`; `CashFlowsSection` throttled with no
  `last_synced_at` → `Never synced · Flex throttled`; relay stale-check
  extracted into `scripts/lib/staleDataMachine.js` and asserted via a
  `writeRelayHealth("error", {reason: "ib_disconnected"})` call.
- **T-143 [P2] `test_unbounded_io_bounds.py` pins the incident-store
  sweep with a conditional that is `assert True` in the current tree, and
  verifies `_executed_orders_since` paging with a fake that implements
  the keyset itself (T-086 class).**
  `scripts/tests/test_unbounded_io_bounds.py:215-217` (`assert
  "prune_resolved" in src.split("def reconcile")[1] if "def reconcile" in
  src else True`; `scripts/incident_watchdog/store.py` has no `def
  reconcile` — the call site is `record_cycle` at `:132`), `:19-36`
  (`page = [r for r in self.rows if cursor is None or r[0] > cursor]`,
  `cursor = params[-2]`), `:73-89`;
  `scripts/monitor_daemon/handlers/expiry_sweep.py:328-343`. `exec_id > ?`
  → `>= ?` at `:332` never advances on a table over 500 rows (daemon
  loops) while the fake still pages. **AC:** RED — call `record_cycle` on
  a dir with an expired resolved incident and assert it is gone;
  in-memory sqlite with 1,001 `executed_orders` rows → 1,001 returned in
  exactly 3 SELECTs. Must catch `>=` and a `LIMIT` without `ORDER BY`.
- **T-144 [P2] Seven smaller substring/tautology guards from the
  REL-052/-068/-069 tranches (bundle).** (a)
  `scripts/tests/test_uw_budget_coverage_delta.py:113-121`
  `"_HOLD_MARKET_STATUS" in inspect.getsource(vc)`; `fetch_vol_cone.py:937`
  `"closed" if hold else "open"` stays green, `test_vol_cone.py:596` never
  reads `market_status`. (b) `scripts/tests/test_flex_caller_coverage.py:92-97,109-115`
  `"raise_if_blocked" in source` — `blotter_service.py:163` import vs
  `:166` call; delete the call, still green. (c)
  `scripts/tests/test_resource_accounting_bounds.py:193-214` constants by
  name; `scripts/newsfeed/media.js:233-235` (name in a comment), `:311`
  `length: wanted.length` stays green; no JS test for `media.js`. (d)
  `scripts/tests/test_cadence_and_growth_bounds.py:79-105`
  `"pruneMediaTree" in src` — satisfied by the import at `push_media.js:12`
  with the call at `:101` removed; `pruneMediaTree` has no test anywhere.
  (e) `scripts/tests/test_indicator_storage_hygiene.py:86-95`
  `"credit_spread_history" in source` + `:97-124` with `_turso_series`
  mocked — the SELECT never executes (T-084 shape). (f)
  `cloud/tests/test_control_plane_bounds.py:55-76` `"continue" in loop or
  "failed_enables" in loop`, `re.search(r"return 1", block)` — `|| {
  failed_enables+=("$unit"); return 1; }` reintroduces the R-184
  truncation green; `test_install_units.py` already runs the helper under
  bash with shims. (g) `web/tests/flow-tabs-get-only.test.ts:9-15`
  `toContain("interval: 0")` — a comment satisfies it. **AC:** per item,
  replace the substring with the observable: (a) `run_intraday(...)["market_status"]
  == "held"` on a budget hold; (b) `raise_if_blocked` raising →
  `SendRequest` never called; (c) 12 URLs with a fetch stub recording
  concurrency → max 4; (d) `pruneMediaTree(tmp)` removes over-retention
  files; (e) sqlite-backed `_turso_series`; (f) run the enable loop under
  bash with a failing `systemctl` shim, assert every unit attempted; (g)
  spy `useSyncHook` options.
- **T-145 [P2] Web money-path residue from the delta (bundle).** (a)
  `web/tests/auto-sync-claim.test.ts:41-58` re-implements the claim store
  from `web/lib/useAutoSyncOnStale.ts:27-55`; `:61-72`'s
  `filter(Boolean).length == 1` holds with or without the lock because
  `claimUnderStore` is synchronous — only `toHaveBeenCalledTimes(5)`
  distinguishes; make the fake store's `write` assert the lock is held.
  (b) `scripts/api/server.py:434,439-461,508-515` — a persistent
  capacity-shed keeps stamping orders-sync `ok` every 5 min (a wedged lane
  per T-065 never pages); `scripts/tests/test_orders_sync_loop.py`
  hand-types the shed marker instead of importing it from
  `scripts/api/subprocess.py:277`; RED — N≥3 consecutive shed ticks must
  stop stamping `ok`. (c) `web/lib/boundedShutdown.ts:13`
  `SHUTDOWN_GRACE_MS` and the `register()` wiring in
  `web/instrumentation.ts:22-27` are unpinned (`bounded-shutdown.test.ts:28,42,54`
  pass `graceMs` explicitly); RED — default grace < the deploy's 60s
  inactive wait; `10_000 → 100_000` must fail. (d)
  `web/components/PositionTable.tsx:269` stock-leg branch of `displaySign`
  has no assertion (`position-table-short-leg-sign.test.tsx` has no stock
  leg). (e) `web/tests/breadth-panel.test.tsx` "recomputes when the range
  preset changes" uses `pearsonCorrelation` as its own oracle — assert a
  hand-derived value.
- **T-146 [P2] (delta to T-112) HHLEV `fetch_rows` is unexecuted, and
  its ladder falls back to the keyed FRED API only on a transport
  exception — a 200 with an empty/HTML body never engages the fallback.**
  `scripts/fetch_hhlev.py:286-296` (`except Exception → fetch_via_api()`;
  the success path returns `[]` untouched), `:119-137`
  (`parse_fredgraph_csv` yields `[]` without the two columns), `:326`
  (`ensure_plausible_series` raises). Tests import neither `fetch_rows`
  nor `fetch_via_api` (`test_hhlev.py:35-44`); coverage missing
  `271-283, 290-296`. **AC:** RED — stub `fetch_fredgraph_csv` → `"<html>"`
  and `fetch_via_api` → fixture rows; assert `fetch_rows()` returns 304
  rows (returns `[]`). GREEN — fall back when the parse yields `<
  MIN_QUARTERS`; add the ordering test (CSV hit must not call the API).
- **T-147 [P2] HYAD backfill (`c9093e53`, year windows fixing observed
  2020-11-24 corruption) and DIVYIELD backfill have zero tests.**
  `scripts/fetch_hyad.py:419-453` (year loop `:431-437`),
  `scripts/fetch_divyield.py:517-632` (`monthly_yields_for_chart`,
  `build_backfill_rows`, `run_backfill`); coverage missing
  `fetch_hyad.py:427-453`, `fetch_divyield.py:520-632`. **AC:** RED —
  stub `fetch_breadth_page` to record `(bond_type, start, end)`; assert
  windows tile `BACKFILL_START..today` with no gaps/overlaps; mutate
  `today.year + 1 → today.year` → red. DIVYIELD: 14-month synthetic chart,
  TTM window excludes month 13.
- **T-148 [P2] `_LockedIB` serialization (`055a6766`) is untested;
  removing the lock stays green while two scanners call it from thread
  pools.** `scripts/utils/uw_surface.py:96-105` (`with self._lock:` at
  `:104`), callers `scripts/leap_scanner_uw.py:571-573`,
  `scripts/theta_harvester_scanner.py:751-752` (ThreadPoolExecutor over
  the shared adapter); `scripts/tests/test_uw_surface.py:150-198` assert
  connect kwargs / disconnect order only. ib_insync is not thread-safe:
  concurrent `reqHistoricalData` corrupts the session and the "IB-first"
  rule silently becomes UW-first after the first collision. **AC:** RED —
  fake IB whose `get_historical_data` sleeps 50ms and records overlap;
  call from 4 threads, assert max concurrency == 1; delete the `with` →
  red.
- **T-149 [P2] `scripts/testing_weekend.sh` (rewritten in `33ec31c4` as
  the daily cycle) has no runner lock, a single-attempt `git fetch`, and
  zero tests; its reliability twin got both, with tests.**
  `scripts/testing_weekend.sh:128-133` (`git fetch origin --quiet` then
  `reset --hard`), no `acquire_runner_lock` (cf.
  `scripts/reliability_weekend.sh:27,50`); `grep -rn testing_weekend
  scripts/tests` → 0. `scripts/tests/test_ops_plane_bounds.py:219-260` and
  `test_weekend_runner_fetch_retry.py:33-59` cover only the reliability
  script. A 00:00 fire overlapping a still-running cycle `reset --hard`s
  under the running agent; one port-22 blackhole at midnight (the
  2026-08-23 event) kills the day. **AC:** RED — port both test classes
  to `testing_weekend.sh` (`--lock-lib-only; fetch_origin_with_retry` with
  a git shim failing twice; held-lock refusal). GREEN — source a shared
  helper lib rather than copy.
- **T-150 [P2] (delta to T-094/T-066) The `a0716084` + `59650568` cloud
  cutover is guarded entirely by substring greps; the caddy-traverse
  guard and restart path are never executed.**
  `cloud/tests/test_p2_host_paths.py:16-94` (all `read_text` + `in text`;
  `:82-94` asserts `"usermod -aG radon caddy"` and `"restart caddy"`
  within 400 chars) over the `cloud/scripts/setup-vps.sh` block added in
  `59650568`. The grant inside a branch that never runs → every
  media.radon.run request 403s again (the 2026-08-23 regression) with green
  CI. **AC:** RED — run the function under bash with `id`/`usermod`/
  `systemctl` shims (pattern `test_bootstrap_control_plane.py`), asserting
  `usermod` then `restart caddy` when `id` lacks `radon` and neither when
  it has it; invert the guard → red.
- **T-151 [P2] `scripts/utils/ipv4_first.py` (`cd15f40a`, the fix for the
  60s/request VPS v6 blackhole) has no test; flipping the sort key to
  IPv6-first stays green.** `scripts/utils/ipv4_first.py:23-29`; installed
  at import in `fetch_divyield.py:64-66`, `fetch_hyad.py:53-55`,
  `fetch_hhlev.py:52-54`; `grep -rln ipv4_first scripts/tests` → none.
  **AC:** RED — monkeypatch `_original_getaddrinfo` → `[AF_INET6,
  AF_INET]`, assert `socket.getaddrinfo(...)[0][0] == AF_INET` after
  `prefer_ipv4()`, plus `fetch_divyield.socket.getaddrinfo is
  _ipv4_first_getaddrinfo`.
- **T-152 [P2] The scan admission gate (`9bee5b85`) is tested
  sequentially only; the concurrent double-admit the lock exists for is
  unexercised.** `scripts/api/server.py:3047-3063` (`async with
  gate.lock:` then re-`_admit()`), `scripts/api/tests/test_scan_gate.py:112-160`
  (sequential `client.post` pairs). Remove the lock or the inner
  `_admit()` → the 5s poll storm from the 2026-08-24 incident spawns N
  parallel `cri_scan.py` again, tests green. **AC:** RED —
  `asyncio.gather` two `_gated_scan` calls against a `run` that awaits an
  Event; assert `run` invoked once and the second returns `read_cached()`;
  delete the inner `_admit()` → red.
- **T-153 [P2] Untouched cash-flow cadence/seed tests now depend on a
  gitignored runtime file.** `scripts/monitor_daemon/handlers/cash_flow_sync.py:341`
  (`is_due` False if `_shared_embargo_until()`) and `:290` →
  `utils/flex_embargo.active_until()` (`:151`) reads `SIDECAR` =
  `data/flex_token_embargo.json` (`:29`); Turso rehydrate is inert under
  pytest. Untouched tests never patch `SIDECAR`:
  `test_cash_flow_sync_cadence.py:68,103,125,169,178,224,309`,
  `test_cash_flow_sync_timeout_retry_budget.py:261,277`,
  `test_corrupt_state_preserves_embargo.py:92`. Red on any host with a
  live sidecar (any local Flex 1025 within 7 days). **AC:** RED — write
  `{"next_attempt_at": <now+1d>}` to the real path, run the cadence file.
  GREEN — autouse fixture in `test_monitor_daemon/conftest.py`
  monkeypatching `utils.flex_embargo.SIDECAR` to `tmp_path` (pattern
  `test_flex_query_lockout.py:20-24`).
- **T-154 [P2] (delta to T-117) T-117's prescribed GREEN is now invalid:
  the daily P&L gate compares the snapshot's `last_sync` date with the
  clock.** `web/lib/ibDailyPnlSession.ts:32-40` (`:37` falls back to wall
  clock only when `last_sync` is absent), wired at
  `web/components/MetricCards.tsx:642`, `web/lib/dashboardKpis.ts:47`.
  Untouched specs generate `last_sync: new Date().toISOString()` in the
  Node process: `web/e2e/day-move-ib-daily-pnl.spec.ts:6,64` (asserts
  `:269-280`), `web/e2e/account-day-move-ib-daily-pnl.spec.ts:6,63`
  (`:216-239`), `web/e2e/account-metric-cards.spec.ts:25,50` (curated in
  CI). Pinning `page.clock` to a Wednesday now renders MARKET CLOSED on
  every real day but that one. `web/tests/day-pnl-non-trading-day.test.tsx:155-162`
  has zero `last_sync` keys, so "TODAY on a trading day" passes via the
  `!lastSync` fallback and never exercises the new gate. **AC:** RED —
  pin the clock to 2026-08-19 with fixtures as-is. GREEN — derive fixture
  `last_sync` from the pinned instant; add a prior-session `last_sync` +
  trading-day clock case expecting MARKET CLOSED.
- **T-155 [P2] `test_grouping_newcomers.py` proves its IB late-joiner
  contract with a service that is no longer IB-classified or produced.**
  `exit-orders` was removed from `scripts/watchdog/services.py`
  SCHEDULED_SERVICES/BUCKETS (`f54ce626`; `requires_ib` `:527-535`
  returns False for unknown) and from `web/lib/serviceHealthWindows.ts`;
  the handler is unregistered (`scripts/monitor_daemon/run.py:91-95`,
  R-141). `scripts/tests/test_watchdog/test_grouping_newcomers.py:31`
  (`NEWCOMER = "exit-orders"`, docstring "Continuous-bucket IB writer"),
  assertions `:84-107`; `grouping.py:191` now routes it down the
  per-service path, so it pages for an unrelated reason.
  `test_monitor_daemon/test_handler_heartbeat.py:146-190` pins heartbeat
  rows for an unregistered handler. **AC:** RED — `assert
  services_mod.requires_ib(NEWCOMER)` at module top. GREEN — `NEWCOMER =
  "fill-monitor"` (`services.py:78`).

### Not findings (checked, clean)

- Adequately tested in the delta (a revert or mutation is caught):
  `c09fc347` core average-cost math (`test_journal_realized.py:66-79`,
  hand-derived 749.0/2247.0); `7cfcdfd9` `ib_orders` leg
  (`test_ib_orders_dual_write.py:271-291`); `89be36ff`
  (`position-table-short-leg-sign.test.tsx:104-111,124-139`, real
  `PositionTable`); `27665c43` 429 handling
  (`use-portfolio-sync-429.test.ts:56-68,98-128`); `b7532743`/`c60f6e7d`
  (real provider/component tests); `c54f72fe` (`test_flex_token_embargo.py`
  reconstruct cases, satisfies T-100 AC(3)); `872a3ed6`/`91459df1`
  (`test_run_flow_refresh_wrapper.py` real bash + HTTP stub); `48d27c41`
  (`test_units.py` oneshot latch — but see T-122); `26168ed5`/`d6b566d9`
  TimeoutStartSec budgets (real tarpit tests, unit-file read); migrations
  0054–0057 applied via `executescript` in all four indicator suites; the
  four API routes (`divyield-api.test.ts:92-140` and siblings, in-memory
  libsql); `a80ab063` (`test_systemd_services.py:523-538` pins the
  OnCalendar); `d7451d2b` (`test_deploy_corrections.py` sources the real
  `deploy.sh`); `33ec31c4` notify half (`test_weekend_notify.py:15-154`).
- Best new tests in the delta, cited as the standard:
  `test_journal_realized.py:66-79`; `auto-sync-claim.test.ts:61-72` (five
  tabs, one claim — modulo T-145a); `test_backfill_journal_from_executed_orders.py:894-919`
  (real sqlite, re-run after partial); `test_nested_deadlines.py:75-120`
  (fake monotonic clock charges HTTP latency to the poll budget);
  `test_monitor_daemon/test_exit_orders_oca.py:87-100` (OCA group per
  journal row — not run by CI, T-122).
- T-089 remediated in-delta (Popen-driven bind); T-062/T-097/T-102 no new
  instances; `position-pnl-pct-structures-catalog.test.ts:508-538`
  rewrote the 24% expectation to `null` in the correct direction (T-059
  resolved, not pinned). No new bare `toBeDefined()` / `assert result`.
- Shared-state checks clean: `test_ib_executor_isolation.py:31-35`,
  `test_probe_thread_loop.py:50-54`, `test_scan_gate.py:89-95`,
  `test_flow_tab_cooldown.py:26-32`, `resetAutoSyncCooldowns` in
  `beforeEach`. Every other time-dependent delta test uses fake timers or
  an injected clock (`bounded-shutdown.test.ts:16`,
  `newsfeed-shutdown.test.ts:14`, `auto-sync-loop-bounded.test.ts:33`,
  `test_nested_deadlines.py:81-82`, `test_menthorq_bootstrap_deadline.py:129`).
- Untouched tests judged still honest against the delta's source
  changes: `test_cash_flow_sync_cli.py:423-443`, `test_flex_query_lockout.py`,
  `kpi-strip.test.tsx`, `open-order-combos.test.ts` +
  `open-order-combo-modify.test.ts` + `options-chain-utils.test.ts` (77/77
  vs the R-166 ratio change), `fastapi-migration.test.ts:166,211`,
  `iei-hyg-api.test.ts` (IEI/HYG only in a comment after the TSY/HY
  rename), `test_demo_scan_guards.py`, `test_suppression_bounds.py:261,279`,
  `test_historical_pool.py:53-75`, `test_docs_contract.py` (all five
  instruction files still carry the Yahoo rule).

### Runner-clone hygiene (operator note)

Tree clean at start, no stash, no parked WIP. `git worktree` at
`/tmp/tw0825/base` (`4985a7f8`) was used for the cloud baseline and
removed after. PR #82 (`testing/weekend-2026-08-23`) is still open; when
it merges, this PR's `TEST_AUDIT.md` / `TEST_LOG.md` appends will need a
trivial append-order conflict resolution — nothing here rewrites its
lines.

## Delta audit 2026-08-26

Range `27665c43..HEAD` — 33 commits, 236 files, +17168/-1517.
86 non-test source files changed; 118 test files changed, 26 added.
New findings continue the frozen numbering at **T-156**. PART A (§1–§10)
is untouched; nothing above this line was rewritten.

**Range overlap.** The ledger SHA is the previous audit's HEAD, not the
merge of its PR, so `e690c85b` ("Testing weekend 2026-08-25", squash-merged
2026-08-25 12:31Z) sits INSIDE this range and re-contains T-122…T-134's
remediation. Those commits were re-triaged as ordinary delta rather than
exempted; T-175 and T-176 are deltas to test files that remediation touched.
`origin/testing/weekend-2026-08-25` is 15 commits ahead of `main` only
because the PR was squash-merged — the content is on `main` (verified:
the T-122 ninth shard is at `ci.yml:205-206`). Nothing is orphaned.

### Runner environment

`rtk` is NOT installed on this host, so bare `git` is the only git available
and its output was trustworthy (the 2026-08-16 rtk rail does not apply here).
`node` needed `~/.nvm/versions/node/v24.14.0/bin` prepended in each Bash-tool
shell. `pytest-asyncio`, `pytest-xdist` and `pytest-cov` were already present
in the shared venv (`~/radon-weekend/venv`) from the 2026-08-25 install; no
new install was needed and the repo was not touched. `caddy` is absent (see
T-164). Load average was 40 at pre-flight and 13–28 across the gate runs;
no round was load-red.

### Standing sweeps

**Gates ×1 serial from the repo root (clean tree, HEAD `1b326772`):**

| Gate | Round 1 |
|---|---|
| `python3.13 -m pytest` (recursive) | **7996 passed**, 1 skipped, 90 deselected (435.4s) |
| `npx vitest run` | **723 files / 7498 passed** (90.5s) |
| `python3.13 -m pytest cloud/tests` | 34 failed, 1062 passed, 5 skipped (230.6s) |

Zero unexplained reds. The tree was clean at start (only the wrapper's own
`.weekend-runner.lock/` untracked); no stash, no parked WIP.

**CI cross-check — the shard sum matches the recursive run exactly.** Pulled
the per-job summary from every `pytest (*)` job of run `32926657735` at this
same SHA (`gh api repos/{owner}/{repo}/actions/jobs/<id>/logs`) and summed:
`1606 + 960 + 759 + 381 + 748 + 1170 + 546 + 766 + 721 + 339 = 7996`,
identical to the local recursive `7996 passed`. T-122's ninth shard is
holding and no module is silently uncollected. Re-ran the T-122 set-diff
independently (expand every `matrix.include[*].paths` token, directories
recursively, against disk): **py-tests 466 on disk / 466 sharded / 0
uncovered / 0 double-covered; cloud-tests 30 / 30 / 0 / 0.** No shard drift.

**Cloud baseline is unchanged — attributed by running the base SHA.** Sorted
the `FAILED` lines at HEAD and at `27665c43` (`git worktree add --detach
/tmp/base27665c43`) and diffed: **byte-identical, 34 both sides**. All 34 are
the known darwin `sha256sum` / bash-3.2 class (T-118). Passed moved
1025 → 1062 and skips 4 → 5; the single added skip is the new caddy
mechanism test (T-164). The recorded darwin baseline stays **34 failed**.

**Coverage-ratchet honesty — thresholds unchanged, but the ratchets left the
deploy gate.** `vitest.config.ts` lines 75 / functions 71 / branches 65 are
byte-identical, and `include`/`exclude`/`coverage.include` are unchanged.
`pyproject.toml` moved the HONEST way: `[tool.coverage.run] branch = true`
was ADDED (the T-123 fix landing). Nothing was lowered and no exclude was
widened. What did change is who enforces them — see **T-160**: `deploy.needs`
dropped `web-coverage` and `py-coverage`, and `main` carries no
`required_status_checks` at all (`gh api
repos/{owner}/{repo}/branches/main/protection` returns no such key), so both
ratchets are now advisory for the push-to-main deploy path. No T-050
threshold decision is needed; the metric is honest, the gate is not wired.

**New skips — one, and it never runs anywhere.** Scanned every added line in
the delta for `test|it|describe.skip/only/todo/fixme`, `xit`, `xdescribe`,
`@pytest.mark.skip|skipif|xfail`, `pytest.skip(`: exactly **one** hit,
`cloud/tests/test_caddyfile.py:229` (`skipif shutil.which("caddy") is None`),
with no linked T-###. It is filed as **T-164** — not because a binary-gated
mechanism test is wrong, but because no CI job installs caddy, so the only
executable proof of the edge-502 fix is skipped in every environment. No
`.only(` anywhere in the delta.

**Determinism — scoped to the 26 ADDED test files.** The delta touches 75 of
`web/tests` and 36 of `scripts/tests`, so the "re-run delta-touched files 3×"
rule would again collapse into full-gate runs; as in 2026-08-25 the scope was
the added files instead. Counts recorded in `TEST_LOG.md`.

### Re-triage of the standing NEW_FINDINGS items

- **E2E testid backlog** — regressed. The delta added a net **+2**
  `data-testid` lines across `web/components/**` + `web/app/**` while adding
  **11 new test files** that query `QuoteTelemetry`'s presentational class
  names because that component exposes no testid at all. Filed as **T-174**.
- **`next dev` in the CI Playwright container** — unchanged, still infra-open.
  `ci.yml` still prebuilds and serves `next start`.
- **`next start` Day Move divergence** — unchanged. `web/e2e/day-move-ib-daily-pnl.spec.ts`
  still exists, still held out of the curated subset (`ci.yml:478` names it),
  untouched in the delta.
- **`e2e/performance-twr-payload.spec.ts`** — remains rewritten (T-079) and
  still not curated. Unchanged this delta.
- **10-failure vitest round of 2026-08-17** — no recurrence; this run's single
  vitest round was 7498/7498 with full reporter output persisted to a file per
  the lesson. Note that **T-161** would now mask exactly this class in CI.
- **T-050 / T-072 coverage-ratchet thresholds** — no threshold moved. The
  pytest side got MORE honest (`branch = true`). The open question changed
  shape: it is no longer "is the number honest" but "does anything enforce it"
  (**T-160**).

### P0 — money-losing gaps

- **T-156 [P0] A `scripts/`- or `cloud/`-only push skips the vitest gate,
  which is the ONLY runner of 11 test files that live under `scripts/` and of
  ~30 `web/tests` files whose subject is `scripts/`/`cloud/` source — and
  `deploy` accepts the skip as a pass.** `scripts/ci/path_filter.py:25-32`
  puts `scripts/` and `cloud/` in `PYTHON_PREFIXES`; `_matches` (`:43-44`) is a
  prefix match, so `scripts/lib/…` does not match the `lib/` entry in
  `WEB_PREFIXES` (`:16-24`). `classify` (`:62-68`) therefore returns
  `web=False`, `.github/workflows/ci.yml:84-85` skips `web-tests`, and
  `ci.yml:621` accepts `needs.web-tests.result == 'skipped'`. Reproduced
  in-process: `classify(["scripts/ib_realtime_server.js",
  "scripts/lib/ibTickHandler.test.js"])` → `(True, False)` — **changing a
  vitest test file does not turn on the gate that runs it.** `vitest.config.ts:18`
  includes `scripts/lib/**/*.test.js` (11 files: `wsTrust`, `staleDataMachine`,
  `demoMirrorReliability`, `sendBackpressure`, `reconnectGate`, `ibTickHandler`,
  `marketCalendar`, `futuresRollover`, `forwardSubRegistry`, `depthLadder`,
  `depthBudget`) and pytest cannot collect `.js`. Cross-tree consumers include
  `web/tests/refresh-schedule.test.ts:26` (reads `cloud/services/<unit>` and
  binds `OnCalendar=` to the UI freshness copy — the CLAUDE.md "never hardcode
  cadence copy" contract), `web/tests/ws-trust-fail-closed.test.ts:13,79`,
  `web/tests/market-state-holiday.test.ts:19` (imports
  `scripts/config/market_holidays.json`, which CHANGED in this delta), and
  `web/tests/security-remediation-supplemental-routes.test.ts:50,58,94` (reads
  `scripts/api/server.py`). This delta itself hit the case: `scripts/lib/staleDataMachine.js`
  gained 24 lines. Worse, `scripts/tests/test_path_filter.py:15-18`
  (`test_scripts_only_skips_web`) PINS the behaviour as correct.
  **AC:** red — a test that parses `vitest.config.ts`'s `test.include` globs,
  derives each glob's root prefix, and asserts `classify([<a file under that
  root>])` returns `web=True` for every one; fails today on `scripts/lib/`.
  Extend it to grep `web/tests/**` for literal `../../scripts/` and
  `cloud/` reads and require those prefixes too. Green — `path_filter.py`
  routes any path matched by a vitest include glob (or read by a `web/tests`
  file) to the web gate as well as the python gate.

- **T-157 [P0] The symmetric hole: a `web/`- or `site/`-only push skips
  pytest AND cloud-tests, which is where the ⛔ PII guard and the DUR-07
  replica guard live — so each guard is skipped by exactly the change it
  exists to catch.** Same source lines; `ci.yml:177-178` and `:329-330` gate
  `py-tests` / `cloud-tests` on `needs.changes.outputs.python == 'true'`.
  Verified in-process: `classify(["site/public/plates/dashboard-x.png"])` →
  `(False, True)`, `classify(["web/lib/chat.ts"])` → `(False, True)`.
  The skipped guards: `tests/test_no_public_account_assets.py:7-9`
  (`assert not list((ROOT/"site"/"public"/"plates").glob("dashboard-*"))` — the
  enforcement of site/CLAUDE.md's ⛔ rule that public plates must not carry real
  account figures) and `:12-16` (the hero must use the synthetic plate);
  `tests/test_no_tracked_account_figures.py:7-10` (`web/lib/chat.ts` carries no
  hardcoded `$981,353` account snapshot); `scripts/tests/test_replica_safe_default.py:26,32`
  (pins `web/lib/db.ts` to `RADON_DB_USE_REPLICA === "1"` and scans `web/lib`,
  `web/app`, `web/components` — the DUR-07 contract in CLAUDE.md);
  `cloud/tests/test_nextjs_db_watchdog.py:22,213` (reads `web/middleware.ts`
  and `web/lib/serviceHealthWindows.ts`);
  `scripts/tests/test_silent_degradation_bounds.py:72`;
  `scripts/tests/test_indicator_storage_hygiene.py:75`.
  A `site/`-only commit that drops a real-account dashboard plate into
  `site/public/plates` skips its own guard and deploys to radon.run.
  **AC:** red — `classify(["site/public/plates/dashboard-x.png"])[0] is True`
  and `classify(["web/lib/chat.ts"])[0] is True`; both fail today. Green —
  the filter forces the python gate whenever a path read by any
  `tests/**` / `scripts/tests/**` / `cloud/tests/**` module changes. Pair with
  T-156's test so the rule is derived from the tree, not hand-listed.

- **T-158 [P0] Every combo order ticket stamps its quote with the CURRENT
  clock, so the new 5-minute live-quote gate can never fire on a combo — the
  operator confirms a multi-leg order against hours-old bid/ask rendered as
  live, and those are the numbers that prefill the net limit price.**
  `web/lib/quoteTelemetry.ts:114` — `comboQuotePriceData` returns
  `timestamp: new Date().toISOString()`, unconditionally. `hasLiveQuote`
  (`:171-176`) decides liveness from `Date.parse(priceData.timestamp)` against
  `LIVE_QUOTE_MAX_AGE_MS = 5 * 60 * 1000` (`:167`), so `hasLiveQuote` is true
  for every combo whose legs carry any non-null bid/ask no matter how stale
  those leg quotes are. `ComboNetQuote` (`:75-80`) carries only
  `symbol/bid/ask/last` — no leg-level freshness is propagated, so the model
  has no way to know. Callers: `OrderTab.tsx:791`, `PositionTradeTicket.tsx:129`,
  `MobileOrderTicket.tsx:219`, `OptionsChainTab.tsx:338`, `ChatPanel.tsx:148`,
  plus `ModifyOrderModal.tsx:296` (`resolveOrderPriceData`'s BAG branch, wired
  to `OrderQuoteTelemetry` at `:628` since `c6552773`). With the relay dark, a
  single-leg ticket correctly relabels to `CLOSE`/`---` (pinned by
  `web/tests/quote-telemetry-fallback.test.ts:76-89`, a 19-hour-old tick) while
  a combo ticket on the same underlying shows stale BID/MID/ASK/SPREAD as live.
  The combo path has exactly one test — `web/tests/order-quote-telemetry.test.tsx:138-144`
  — and it only counts rendered labels. Nothing in `web/tests/` or `web/e2e/`
  passes a stale timestamp through a combo.
  **AC:** red — add `timestamp` (or `asOf`) to `ComboNetQuote`, then
  `buildQuoteTelemetryModel(comboQuotePriceData({symbol:"AAOI", bid:-1.2,
  ask:-0.8, last:-1.0, timestamp:"2026-08-12T20:00:00Z"}), null,
  Date.parse("2026-08-13T15:00:00Z"))` must yield `last.label === "CLOSE"` and
  `bid.value === "---"`; today it yields `MARK` / `$-1.20`. Green — thread the
  oldest leg timestamp through `comboQuotePriceData` instead of stamping `now`.

### P1

- **T-159 [P1] `docs/`, `.claude/`, `tasks/`, `.agents/` and `notebooks/` skip
  BOTH test gates for any file type — and two of them hold runtime data that
  vitest asserts on, so a push can deploy to production having run zero
  tests.** `scripts/ci/path_filter.py:34-40` `SKIP_PREFIXES` is matched by
  prefix against the whole path (`:55`), not restricted to `.md` (which has its
  own clause on the same line). `classify` then returns `(False, False)`
  (`:69-70`), all five test jobs skip, and `ci.yml:617-625` accepts every one as
  `skipped`. Verified in-process: `classify(["docs/options-structures.json"])`
  → `(False, False)`. That file is runtime data, not documentation: it is read
  by `web/tests/position-pnl-pct-structures-catalog.test.ts:43` and
  `web/tests/synthetic-combo-structures-catalog.test.ts:11`, and consumed by
  `site/lib/pages/`. `docs/owners.json` is the glob→owner map
  `scripts/tests/test_docs_contract.py:19` runs on, so breaking it disables the
  docs contract silently. `.claude/hooks/*.py` and `.claude/workflows/*.mjs`
  are also executable code in the skip set.
  **AC:** red — `classify(["docs/options-structures.json"]) == (True, True)`
  and `classify(["docs/owners.json"]) == (True, True)`; both fail today.
  Green — restrict `SKIP_PREFIXES` matching to documentation extensions
  (`.md`, images) rather than whole subtrees.

- **T-160 [P1] Both coverage ratchets left the deploy gate this delta, and
  nothing replaced them — `main` has no required status checks at all.**
  At `27665c43`, `deploy.needs` was `[secret-scan, web-tests, web-coverage,
  py-tests, py-coverage, cloud-tests, perimeter-smoke]` with a plain
  `if: github.ref == 'refs/heads/main' && …`, so a ratchet failure blocked the
  deploy. At HEAD (`ci.yml:616`) `web-coverage` and `py-coverage` are gone from
  `needs` and appear nowhere in the `if` (`:617-625`). The thresholds are intact
  (`--cov-fail-under=56` at `ci.yml:302`; lines 75 / functions 71 / branches 65
  at `vitest.config.ts:64-68`) but advisory. The comment at `ci.yml:613-615`
  defers to "required workflow job" status; `gh api
  repos/{owner}/{repo}/branches/main/protection` returns `enforce_admins`,
  `allow_force_pushes`, `lock_branch` and friends but **no
  `required_status_checks` key**, so that mechanism does not exist. Compounding:
  `web-coverage` is `if: needs.web-tests.result == 'success'` (`:157`) and
  `py-coverage` `if: needs.py-tests.result == 'success'` (`:267`), so under
  T-156/T-157 a one-sided push skips the ratchet entirely. And the guard suite
  pins the weakening: `scripts/tests/test_ci_deploy_concurrency.py:290-300`
  (`test_coverage_ratchets_do_not_serialize_deploy`) asserts
  `"web-coverage" not in needs`.
  **AC:** red — a test asserting that for each of `web-coverage` / `py-coverage`
  EITHER the job is in `deploy.needs` with a `success || skipped` clause OR the
  job name appears in the repo's `required_status_checks` contexts; fails today
  on both arms. Green — restore them to `deploy.needs` with the same
  `success || skipped` shape the four test jobs use. This is the T-050
  discipline: report, do not silently accept.

- **T-161 [P1] `vitest.config.ts` now retries every test once in CI, so any
  intermittent money-path failure is converted into a green deploy gate.**
  `vitest.config.ts:32` — `retry: process.env.CI ? 1 : 0`, new in this delta
  (`8cf32a15`). The comment (`:29-31`) names exactly two jsdom timeout cases
  (newsfeed pagination on shard 5, theta-harvester on shard 7) but the setting
  is global across all 7498 tests, including every order-safety and money-math
  file. This is the mechanism that would have hidden T-062, and it directly
  suppresses the signal the repo's own rule ("re-run the suspect test file in
  isolation before concluding") depends on. It also masks T-177's inert 8s
  locator timeouts and any order-dependence a future change introduces.
  **AC:** red — a config-contract test asserting the global `test.retry` is `0`;
  fails on line 32 today. Green — move the retry to the two named files
  (per-file `test.retry`) or raise `testTimeout` for them, leaving the suite
  fail-fast. If a suite-wide retry is genuinely wanted, it needs an operator
  decision and a CI annotation on every retried test, not a silent pass.

- **T-162 [P1] Two new tests assert that a producer writes NO health row when
  every source is down — pinning as correct the exact silence
  `docs/operations.md:187` declares worse than staleness, in the same delta
  that wrote that rule.** `scripts/tests/test_credit_spread.py:283-290`
  (`test_no_cache_raises_and_never_heartbeats` → `assert persist_calls == []`)
  and `scripts/tests/test_iei_hyg.py:302-309` (identical). The source path is
  new here too: `_serve_cached` raises at `scripts/fetch_credit_spread.py:542`
  and `scripts/fetch_iei_hyg.py:488` with no `try` in either `main()`
  (`:592`, `:531`); both also `return` before the heartbeat on an empty series
  (`:476`, `:423`). `docs/operations.md:187`: *"Every producer must construct
  its client, resolve its ticker universe, and take its `parser.error(...)`
  exits INSIDE the health-reporting block."* Both services are registered with
  26h windows (`scripts/watchdog/services.py:141,147`), so a Turso read failure
  (swallowed at `fetch_credit_spread.py:529`) plus a wiped `data/` after a host
  rebuild produces a hard outage that reads as staleness a day later.
  **AC:** red — rename both to `test_no_cache_heartbeats_error_before_it_raises`
  and require `pytest.raises(RuntimeError)` AND
  `("health", "<service>", "error") in persist_calls`; fails today. Green —
  `run()`/`main()` records the error row before re-raising. Do NOT weaken the
  raise; the contract is "raise loudly AND leave a row".

- **T-163 [P1] The Equibles silent-death fix was applied to 2 of 5 oneshots,
  the class has no contract test, and the preflight assertion that covered the
  gap was deleted while two docs still state the guard exists.**
  `3b2945b7` fixed `fetch_equibles_smart_money_13f.py` and
  `fetch_equibles_filing_forensics.py`. Still constructing the client outside
  any health-reporting block: `scripts/fetch_equibles_ats_venue_share.py:592-593`
  (`client = EquiblesClient()` above the `try:` at `:598`; `main()` at `:642`
  has no `except`) and `scripts/fetch_equibles_short_crowding.py:604-605`
  (`main()` at `:651`, same). `EquiblesClient.__init__` raises
  `EquiblesAuthError` when the key is unset (`scripts/clients/equibles_client.py:223-227`),
  and all five are freshness-registered (`scripts/watchdog/services.py:323-327`),
  so a construction death writes no row at all. Then `1b326772` removed
  `EQUIBLES_API_KEY` from `cloud/config/required-env.txt` **and deleted its only
  assertion** (`cloud/tests/test_scripts.py::test_requires_the_equibles_key_the_scheduled_units_construct_with`)
  — a defensible operational call, taken for a stated reason, but it leaves
  `docs/cloud-services.md:747-748` ("**Fail-closed:** `EQUIBLES_API_KEY` is in
  `cloud/config/required-env.txt`, so `cloud/scripts/check-env.py` refuses the
  deploy preflight when it is unset") and `docs/operations.md:187` ("The key is
  now in `required-env.txt`") asserting a guard that no longer exists, with no
  test that would notice. `scripts/tests/test_service_registration_completeness.py:339`
  asserts window registration, never "every producer heartbeats before exit".
  **AC:** red — per producer, `test_run_heartbeats_error_when_the_key_is_absent`
  (`monkeypatch.delenv("EQUIBLES_API_KEY")` → `pytest.raises(EquiblesAuthError)`
  plus an `error` row), mirroring `scripts/tests/test_equibles_smart_money_13f.py:661-673`;
  red today for both files. Then promote it to a class test that walks every
  module exporting `SERVICE` + `_record_health`. Separately: a test asserting
  `docs/cloud-services.md`'s fail-closed claim matches
  `cloud/config/required-env.txt` — red today. The re-add is gated on the host
  (`sudo grep -c '^EQUIBLES_API_KEY=' /etc/radon/env`), so the doc, not the
  contract, is what to correct now.

- **T-164 [P1] The only executable proof of the edge-502 fix is skipped in
  every environment, and the assertions that DO run cannot catch the one change
  the Caddyfile explicitly warns against.** `717e8d5d` fixed a real incident
  (raw 502s for ~7s during the #89 promote) and shipped
  `cloud/tests/test_caddyfile.py:236` `TestRestartWindowMechanism`, which drives
  a real caddy against a real dead port — gated by
  `@pytest.mark.skipif(shutil.which(CADDY_BIN) is None)` at `:229`. No workflow
  installs caddy (`grep -i caddy .github/workflows/*.yml` → nothing) and the
  binary is absent on this runner, so the class never executes anywhere; it is
  the single skip added by this delta and this run's cloud gate shows
  `5 skipped` against the base SHA's `4`. What runs is three regexes over the
  Caddyfile text (`:174-201`). Meanwhile `cloud/caddy/Caddyfile:38` warns *"Do
  NOT add fail_duration: marking the single upstream down would take the retry
  loop out of the picture and 502 immediately again"* — and `fail_duration`
  appears nowhere but that comment. **Surviving mutation:** add
  `fail_duration 30s` to either upstream block; `lb_try_duration 15s` is still
  spelled correctly, all three CI assertions pass, production returns to
  instant 502s on every promote.
  **AC:** red — `test_no_fail_duration_on_the_single_upstream_blocks` asserting
  `"fail_duration" not in reverse_proxy_block(content, upstream)` for
  `localhost:3000` and `localhost:8321`; red against a Caddyfile carrying it,
  green as shipped. Separately, install caddy in the `cloud-tests` job (or set
  `RADON_CADDY_BIN`) so the mechanism test stops being decorative — that half is
  a CI change and needs an operator eye.

- **T-165 [P1] Gate 3 announces "correlated clusters within budget" on a book
  it only partially measured — `insufficient_data` is carried on the output and
  read by none of the three branches that decide the level.**
  `web/lib/correlationRiskBanner.ts:74` reads `report.insufficient_data ?? []`
  and copies it into all three returns (`:105`, `:116`, and the breach branch),
  but `level` is derived at `:76`, `:88` and `:105` from `breaches` and
  `clusters` alone. The component's escape hatch
  (`web/components/CorrelationRiskBanner.tsx:26-28`) only fires when
  `level === "none" && insufficientData.length > 0`, so the moment any two
  measurable names form a within-budget cluster the headline reads
  `Gate 3: N correlated clusters within budget` while the rest of the book was
  never measured. `9fd59f7c` makes partial measurement the steady state rather
  than an edge: `scripts/portfolio_risk.py` bounds the ladder to
  `BACKFILL_MAX_SYMBOLS_PER_RUN = 4` with `BACKFILL_RETRY_S = 6 * 3600`, so any
  underlying no source can serve stays unmeasured for six-hour stretches
  indefinitely. The existing test
  `web/tests/correlation-risk-banner.test.ts:70-75` imports the function and
  asserts only `insufficientData`, passing a report with EMPTY clusters — it
  never asserts `level`. Per the Four Gates, a gate that reads clean on
  unmeasured exposure is a gate failure, not a display bug.
  **AC:** red — `correlationRiskBanner(report({clusters:[calmCluster],
  breaches:[], insufficient_data:["THIN","NEW"]}))` must not return a level
  that reads as a clean verdict: assert either a new `"unmeasured"` level or
  `level === "info"` AND a headline naming the unmeasured count; fails today.
  Pair with a render test asserting `data-level` is not a calm value while
  `insufficientData.length > 0`.

- **T-166 [P1] `9fd59f7c` moved an unbounded live-fetch ladder inside the
  per-minute portfolio sync, and every test stubs all three rungs so the
  latency is structurally invisible.** `scripts/ib_sync.py:1370` calls
  `attach_correlation_risk_report` inside the snapshot builder, before `result`
  is returned and persisted. That path is no longer a disk read:
  `scripts/portfolio_risk.py` → `backfill_price_history` →
  `_fetch_closes_via_ladder` → `_fetch_ib_closes`
  (`client.connect(client_id="auto", timeout=10)` + `get_historical_data(timeout=20)`),
  `_fetch_uw_closes`, `_fetch_yahoo_closes` (`urlopen(..., timeout=30)`). There
  is no wall-clock budget on `backfill_price_history`; worst case is ~60-90s per
  symbol × `BACKFILL_MAX_SYMBOLS_PER_RUN = 4` against a loader the commit
  message says runs every minute during RTH. A stall delays the
  `portfolio_snapshots` write, which is the source for positions, bankroll and
  `account_summary` → `assessMargin`. The new tests are thorough on ladder
  ORDERING and throttling (`scripts/tests/test_portfolio_risk.py::TestTursoPriceSource`)
  but `_stub_fetchers` replaces all three rungs with instant recorders. The
  `except Exception` fail-open at `ib_sync.py:1381-1383` — which writes
  `risk_budget = None`, silently removing the Gate 3 banner — is untested.
  **AC:** red — (a) `test_backfill_is_wall_clock_bounded`: monkeypatch
  `_fetch_ib_closes` to `time.sleep(5)` and assert
  `backfill_price_history(["A","B","C","D"])` returns inside a
  `BACKFILL_TOTAL_BUDGET_S` deadline (fails today: no such budget exists);
  (b) `test_measurement_failure_does_not_block_the_snapshot`: make
  `load_price_series_for_portfolio` raise and assert
  `attach_correlation_risk_report` returns with `risk_budget is None` and does
  not propagate. Do not use a real sleep in the green version — assert on a
  monotonic-clock budget with an injected clock.

- **T-167 [P1] The whole new portfolio-performance contract file is
  source-string grepping, and the server seed it was written to protect can be
  stubbed out entirely with the suite green.**
  `web/tests/portfolio-startup-performance-contract.test.ts` — every `it`
  asserts `toContain`/`toMatch` over a file read as text (`:24-34`, `:36-46`,
  `:48-52`). Nothing calls `readPortfolioSnapshotSeed`. The only other test
  touching it (`web/tests/portfolio-auto-sync.test.ts:147-176`) asserts the
  TIMEOUT path `resolves.toBeUndefined()`. **Surviving mutation:**
  `web/lib/portfolio/readPortfolioSnapshot.server.ts:111` — replace
  `if (!cached.value) return undefined;` with a bare `return undefined;`.
  `app/portfolio/page.tsx` still contains the identifier so `:40` passes, and
  `portfolio-auto-sync` already expects `undefined`. Production silently
  reverts to the client-GET waterfall `3cf640d7` was written to remove, with no
  red anywhere. Same class as T-128, which replaced eight such `it`s.
  **AC:** red — in `portfolio-auto-sync.test.ts`, stub `mockExecute` to resolve
  a snapshot row, `await readPortfolioSnapshotSeed()`, and assert
  `result?.data.bankroll` equals the fixture and `result?.warning` is `null`;
  fails on the mutation, passes on HEAD.

- **T-168 [P1] The portfolio cache TTLs are pinned by grepping the constant
  DECLARATION, so the constants can keep their values while the cache is
  configured with `0`.** `web/tests/orders-performance-cache.test.ts:86-91`
  reads `lib/portfolio/portfolioReadCache.ts` as text and asserts
  `/PORTFOLIO_SNAPSHOT_CACHE_TTL_MS\s*=\s*15_000/` and the 60_000 sibling. The
  use sites are elsewhere: `portfolioReadCache.ts:27` passes the constant into
  `cachedReadResult`, `:38`/`:48` into `cachedRead`. **Surviving mutation:**
  change `:27` to pass `0`; both regexes still match, the whole suite is green,
  and every portfolio read misses the cache — the entire coalescing win of
  `3cf640d7`, gone silently. Aggravating: this delta changed
  `web/tests/portfolio-auto-sync.test.ts:208` from the literal
  `vi.advanceTimersByTime(3_001)` to
  `vi.advanceTimersByTime(PORTFOLIO_SNAPSHOT_CACHE_TTL_MS + 1)`, making the one
  remaining timer-based portfolio test self-referential — it advances by
  whatever the constant says and can never disagree with it, and it still
  passes under the mutation (`0 + 1` ms is past a 0 ms TTL). The orders half of
  the very same file (`:38-51`) already does this correctly with fake timers.
  **AC:** red — with `RADON_DB_CACHE_FORCE=1` and fake timers, call
  `readCachedPortfolioSnapshot(fetcher)` twice at `t=0` and once at `t=14_999`
  and assert `fetcher` was called ONCE; advance to `t=15_001` and assert TWICE.
  Fails on the mutation.

- **T-169 [P1] The post-mutation orders cache invalidation is pinned by a
  bridging regex that ignores reachability, and no test ever drives a mutating
  orders route with the cache actually enabled.**
  `web/app/api/orders/place/route.ts:437-446` invalidates, awaits
  `/orders/refresh` (up to 10s), invalidates AGAIN in `finally`, then reads —
  and the code says why: *"A GET racing the refresh may have repopulated the
  old snapshot."* The test is
  `web/tests/orders-performance-cache.test.ts:76-83`, a `readFile` plus
  `/invalidateOrdersSnapshotCache\s*\(\s*\)[\s\S]*?readOrdersSnapshot(?:FromDb|BestEffort)\s*\(\s*\)/`.
  **Surviving mutation:** delete only the `finally { invalidateOrdersSnapshotCache(); }`
  at `:441-443`; the regex still matches via the pre-refresh call. A concurrent
  `GET /api/orders` during the refresh stores the pre-fill rowset at the current
  generation (`web/lib/dbCache.ts:108-114`), and the place route's own read hits
  it inside the 2s TTL — so the response to the operator omits the order they
  just placed. Same hole at `cancel/route.ts:76-82` and
  `modify/route.ts:217-222,274-279`. Compounding: `dbCache.ts:88` bypasses the
  cache entirely under `NODE_ENV === "test"` unless `RADON_DB_CACHE_FORCE=1`,
  and no test sets that flag while invoking a MUTATING orders route. The sibling
  assertion at `:93-98` has the same bridging shape for
  `/api/portfolio/route.ts:201,209` — wrapping the call in
  `if (process.env.X === "1")` keeps it green.
  **AC:** red — `web/tests/orders-place-cache-race.test.ts` (node env), set
  `RADON_DB_CACHE_FORCE=1`, mock `radonFetch` so `/orders/refresh` resolves on a
  controlled deferred, mock `dbExecute` to return the pre-fill rowset first and
  the post-fill rowset after; `POST` from `app/api/orders/place/route`, call
  `GET` from `app/api/orders/route` while the refresh is pending, and assert the
  POST response's `orders.open_orders` contains the new order. Must fail with
  the `finally` invalidate removed.

- **T-170 [P1] The chunk-isolation assertion matches on the relative import
  form in a file that imports via the `@/` alias, so the eager import it
  forbids can be reintroduced verbatim.**
  `web/tests/portfolio-startup-performance-contract.test.ts:33` —
  `expect(workspace).not.toContain('from "./PositionTable"')`.
  `web/components/WorkspaceSections.tsx` uses `@/`-aliased imports throughout.
  **Surviving mutation:** add `import PositionTable from "@/components/PositionTable";`
  to the top of `WorkspaceSections.tsx` — the literal `from "./PositionTable"`
  never appears, `:33` passes, and the all-routes workspace chunk once again
  eagerly pulls `PositionTable`, which is exactly the regression the `it` names
  (`:28`, `:32` are unaffected).
  **AC:** red — replace the string match with an import-graph assertion: parse
  every `import … from "<spec>"` in `WorkspaceSections.tsx`, normalise
  `@/components/X` and `./X` to `X`, and assert the set excludes
  `PositionTable` and `PortfolioSections`; fails on the mutation.

- **T-171 [P1] The T-083 remediation landed the per-position Day P&L gate on
  the WALL CLOCK while the card it was matched to uses the provenance-aware
  gate, so R-107 is re-opened one level down: on Monday after a Saturday-stalled
  producer the card reads `--` while every row prints Saturday's phantom Day
  P&L.** `web/components/PositionTable.tsx:596` — `const sessionToday =
  isIbDailyPnlCurrent()` with NO argument, then `:598`
  `withSessionIbDailyPnl(positions)`. `web/lib/ibDailyPnlSession.ts:18` is
  `isIbDailyPnlCurrent(now = new Date())` — clock only. The card path uses
  `currentIbDailyPnl` → `isIbDailyPnlFromCurrentSession(lastSync, now)`
  (`:32-40`), whose own docstring at `:22-30` describes precisely this failure:
  *"Producer's last success Saturday, operator opens the dashboard Monday 08:00
  ET, `isUsTradingDay` is true, and the Saturday-captured phantom daily P&L
  renders labelled TODAY."* `withSessionIbDailyPnl` (`:78-81`) takes no
  `lastSync` parameter at all, so the correct call is not expressible without a
  signature change. The new test file
  `web/tests/position-table-day-pnl-non-trading-day.test.tsx` has three `it`s
  (Saturday masks, Friday RTH shows, Saturday crypto keeps) and **zero
  `last_sync` keys**, pinning the wall-clock-only behaviour as correct.
  Distinct from T-154, which is about the card path's specs.
  **AC:** red — render `PositionTable` at Monday 08:00 ET with
  `last_sync` = the previous Saturday and an equity option carrying
  `ib_daily_pnl: 13951.76`; the row's Day P&L cell must be blank. Fails today.
  Green — `withSessionIbDailyPnl(positions, now, lastSync)` delegating to
  `isIbDailyPnlFromCurrentSession`, and `PositionTable` passing
  `portfolio.last_sync`.

- **T-172 [P1] Two `/orders` e2e specs still route the bare
  `**/api/portfolio` glob, so the new `?include=entry-dates` request escapes the
  mock and hits the real server — a live Turso read inside an e2e run, with no
  red to show for it.** `web/lib/usePortfolio.ts:36-38` now builds
  `"/api/portfolio?include=entry-dates"` when `includeEntryDates` is set, and
  `web/components/WorkspaceShell.tsx:94` sets it for `isOrdersPage`. Commit
  `1e15b612` widened the glob to `**/api/portfolio**` in 23 specs and missed the
  two that `goto` an absolute origin: `web/e2e/open-order-single-detail.spec.ts:155`
  (route) + `:203` (`goto("http://127.0.0.1:3000/orders")`) and
  `web/e2e/modify-order-resting-limit.spec.ts:147` + `:169`. Confirmed against
  the installed `playwright-core` `urlMatch`: `'/api/portfolio?include=entry-dates'`
  vs `'**/api/portfolio'` → **false**; vs `'**/api/portfolio**'` → true. Both
  specs stay green because their assertions are orders-based, which is exactly
  why the isolation break is silent.
  **AC:** red — in both specs, route `**/api/portfolio**` recording every URL,
  and add a `page.on("request")` allowlist assertion that no unrouted
  `/api/portfolio*` request reached the server; fails with the bare glob today.

### P2

- **T-173 [P2] The cloud shard-partition guard uses `.glob`, not `.rglob`, so
  the T-122 failure mode is still open one directory down for `cloud/tests`.**
  `scripts/tests/test_ci_deploy_concurrency.py:324-341`
  (`test_cloud_infra_shards_partition_cloud_tests`) enumerates
  `(root/"cloud"/"tests").glob("test_*.py")` — files only — while its py-tests
  sibling at `:412-428` correctly uses `_test_modules_under` → `rglob`. The
  `cloud-tests` matrix added in this delta shards by `test_[a-l]*.py` /
  `test_[m-z]*.py` (`ci.yml`), and a glob cannot match a directory. Today
  `cloud/tests` is flat (30 files, all covered — verified), so this is latent,
  not live. A file starting with a digit or uppercase would also fall outside
  both ranges and the guard's `fnmatch` loop would catch that; a SUBDIRECTORY
  would not.
  **AC:** red — create `cloud/tests/test_subdir/test_x.py` and the guard must
  fail; today it passes. Green — switch to `rglob` and assert set-equality the
  way the py-tests guard does.

- **T-174 [P2] Eleven new test files hang on `QuoteTelemetry`'s presentational
  class names because the component exposes no testid — and the delta added a
  net +2 `data-testid` lines against 11 new files that needed them.**
  `web/components/QuoteTelemetry.tsx:24-39` emits only `price-bar`,
  `price-bar-item`, `price-bar-label`, `price-bar-value`, `price-bar-empty`,
  `price-bar--tight`. Queried by `web/tests/order-quote-telemetry.test.tsx:62,103-105`,
  `order-tab-quote-telemetry.test.tsx:96,102,108-110,215-216`,
  `book-tab-order-quote-telemetry.test.tsx:42-44,78-80,145`,
  `chain-order-builder-quote-telemetry.test.tsx:166,173,213,235,243`,
  `chat-approval-gate-quote-telemetry.test.tsx:165,181`,
  `futures-order-quote-telemetry.test.tsx:83-84,105-106`,
  `listed-contract-order-quote.test.tsx:80,134,140,151-152,218,228,242`,
  `mobile-chain-detail-quote-telemetry.test.tsx:106,125`,
  `mobile-order-ticket-quote-telemetry.test.tsx:108,114-117,142`,
  `modify-order-full-telemetry.test.tsx:115,130,148-149`,
  `single-leg-ticket-quote-telemetry.test.tsx:72,91,96,165`,
  `position-trade-ticket-quote-telemetry.test.tsx:127,138,143`. A rename inside
  `VARIANT_CLASSES`, or switching one surface from variant `"bar"` to
  `"compact"`, reds all eleven at once — several as a `TypeError`, e.g.
  `futures-order-quote-telemetry.test.tsx:84` does
  `within(container.querySelector(".price-bar") as HTMLElement)` on `null`.
  This is a new, concentrated instance of the standing e2e-testid backlog.
  **AC:** red — rename `label: "price-bar-label"` at `QuoteTelemetry.tsx:28`;
  ≥11 files fail. Green — emit `data-testid="quote-field"` /
  `"quote-field-label"` / `"quote-field-value"` and query those.

- **T-175 [P2] (delta to T-113) The rewritten table-overflow contract
  mis-parses any JSX tag with four or more nested brace levels, so a wrapped
  table can read as unwrapped and an unwrapped one can vanish from the sweep
  entirely.** `web/tests/table-scroll-wrapper-contract.test.ts:104-105` — the
  `TAG` regex models at most three levels:
  `\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}`. Verified against that exact
  regex: `<div className="table-wrap" style={{ a: { b: 1 } }}>` matches;
  `<div className="table-wrap" style={{ a: { b: { c: 1 } } }}>` does NOT. An
  unmatched opening tag is never pushed onto the ancestor stack, so a correctly
  wrapped `<table>` beneath it is reported unwrapped; the same brace on a
  `<table>` tag removes it from the sweep. The assertion at `:238` is an
  EQUALITY against `KNOWN_UNWRAPPED_T121` (`:42-49`), so drift in either
  direction reds — which is the right design, and why a parser bug here is a
  false-red generator rather than a silent hole. Speed is NOT an issue: the TSX
  sweep timed at 75ms over 271 files / 2.15MB, so the T-113 precompute fix
  (`:75-93`) is genuinely holding.
  **AC:** red — add `style={{ a: { b: { c: 1 } } }}` to the `.table-wrap` div in
  `web/components/PositionTable.tsx:665`; the file reds with a phantom
  violation. Green — replace the fixed-depth alternation with a brace-counting
  scan.

- **T-176 [P2] (delta to T-117 / T-083) A third assertion in an untouched e2e
  spec became weekend-dependent when the T-083 remediation gated the
  per-position Day P&L column.** T-117 named `web/e2e/account-day-move-ib-daily-pnl.spec.ts:216,223`
  (the card and the modal, gated by `MetricCards`). `:239` —
  `await expect(wulfRow.locator("td")).toContainText(["-$3,405"])` — is the
  per-position ROW cell and was not weekday-dependent before this delta. It is
  now: `web/components/PositionTable.tsx:596-599` masks `ib_daily_pnl` on a
  non-trading day, and `web/lib/positionUtils.ts:656` is the branch that returns
  it (`if (pos.ib_daily_pnl != null) return pos.ib_daily_pnl;`) for the
  fixture's non-same-day `structure_type: "Long Call"` with
  `ib_daily_pnl: -3_405.31` (`:19`, `:27`). Rendered at
  `PositionTable.tsx:507-508`. The spec's single `addInitScript` (`:98`) is the
  price fixture, not a clock pin. Confirmed live, not a no-op: playwright's
  `to.contain.text` with an array is a subsequence match over the row's `<td>`s.
  Triggers Sat 2026-08-29, Sun 2026-08-30, Labor Day Mon 2026-09-07. Impact is
  local-only — this spec is not in the curated CI subset — which is why it
  matches T-117's P2 rather than escalating.
  **AC:** red — run the spec with the clock at `2026-08-29`; `:239` fails.
  Green — pin the spec's clock to a trading day, the way the new unit test does
  (`web/tests/position-table-day-pnl-non-trading-day.test.tsx:91-92,100-101`
  uses `vi.setSystemTime` with explicit `SATURDAY`/`FRIDAY_RTH` constants).

- **T-177 [P2] Four `findByRole(..., { timeout: 8000 })` calls are unreachable
  under vitest's 5000ms default, so they read as protection that does not
  exist.** `web/tests/chat-approval-gate-quote-telemetry.test.tsx:219,240,259,276`.
  `vitest.config.ts` sets no `testTimeout`, so the default 5000ms kills the test
  before the locator's own 8s ceiling can be reached, and the failure surfaces
  as an opaque "test timed out in 5000ms" instead of the intended locator error.
  The triggering condition is exactly the one T-161's retry was added for — a
  shard VM under `--coverage`.
  **AC:** red — set `testTimeout: 1000` for this file and observe the `8000`
  values are inert. Green — drop the `{ timeout: 8000 }` args, or add a matching
  per-file `testTimeout` above 8000.

- **T-178 [P2] The new portfolio cadence specs detect requests through a 5s
  `expect.poll` while deliberately not awaiting a 90s navigation.**
  `web/e2e/portfolio-request-cadence.spec.ts:189,278,279` use
  `await expect.poll(() => portfolioGets).toBe(1)`. `web/playwright.config.ts:43`
  sets `navigationTimeout: 90_000` but no `expect: { timeout }`, so `expect.poll`
  runs on playwright's 5000ms default while `page.goto` is intentionally left
  un-awaited (`:187`, `:276`) because the route handler gates on a promise.
  Against a cold `next dev --turbopack` (the config default, `:68`) the
  `/portfolio` compile exceeds 5s and the poll fails long before the 90s test
  timeout matters. Secondary: `:279`'s `toBe(1)` is an equality on a
  monotonically increasing counter, so two entry-date GETs landing before the
  first tick make it unsatisfiable. The fixed-window negatives at
  `:190`/`:226`/`:280` (`waitForTimeout(250|500)` then `expect(posts).toBe(0)`)
  prove the no-recovery claim only for that window — a false-green, not a
  false-red. Also new positional selectors where a testid belongs:
  `:200`, `:225` `getByText("AAPL", { exact: true }).first()`.
  **AC:** red — kill any running dev server, leave `PLAYWRIGHT_WEBSERVER_CMD`
  unset, and run the file. Green — pass `{ timeout: 60_000 }` to each
  `expect.poll` (or set `expect.timeout` in the config) and change `:279` to
  `toBeGreaterThanOrEqual(1)` with a separate upper-bound assertion.

- **T-179 [P2] Every pre-existing `/api/risk-free-rate` stub in the tree is now
  silently rejected, so seven files hold inert fixtures that look like rate
  coverage.** `web/lib/useRiskFreeRate.ts:38-45` now discards the response
  unless `data.source === "FRED:DFF"` AND `data.stale === false` AND the rate is
  a finite number. Every existing stub is `{ rate: 0 }` with neither field:
  `web/tests/options-chain-implied.test.tsx:122`,
  `chain-expiry-preserves-legs.test.tsx:141`, `chain-url-deeplink.test.tsx:125`,
  `chain-atm-scroll-isolation.test.tsx:126`,
  `ticker-chain-position-focus.test.tsx:175`,
  `web/e2e/order-margin-impact-unavailable.spec.ts:206`,
  `web/e2e/theta-harvester-prefill.spec.ts:181`. They still pass only because
  the rejected path leaves the store at `0` — the same value the stub supplied.
  Changing one to `{ rate: 0.05 }` to test rate sensitivity is now a no-op, and
  a Black-Scholes implied-value test written against it would pass without ever
  seeing the rate. Note the module-level store (`:5-9`) has no
  `NODE_ENV === "test"` bypass unlike `web/lib/dbCache.ts:88`; isolation rests
  on vitest's per-file module isolation plus
  `resetRiskFreeRateCacheForTests()`, which only
  `web/tests/use-risk-free-rate-dedup.test.tsx:18,23` calls — so this becomes
  order-dependent if `isolate: false` is ever set.
  **AC:** red — stub `{ rate: 0.05, source: "FRED:DFF", stale: false }` in
  `options-chain-implied.test.tsx` and assert a different implied value than
  with `rate: 0`; fails with the current stub shape.

- **T-180 [P2] The assistant order gate's quote plumbing is pinned textually,
  so the price map can be emptied at the source with the test green.**
  `web/tests/chat-launcher-prices-threading.test.tsx:80-93` reads
  `WorkspaceShell.tsx` as text and asserts `launcher[0]` contains
  `prices={prices}` and the file contains `const prices = usePreviousClose(`.
  **Surviving mutation:** `web/components/WorkspaceShell.tsx:329` →
  `const prices = usePreviousClose({});`. Both assertions still match verbatim;
  the gate renders the empty "No real-time data" panel and the operator
  confirms a live order with no bid/ask — word for word the failure the file's
  own docstring says it prevents. The first `it` (`:66-79`) is real.
  **AC:** red — render the real `WorkspaceShell` with `usePrices`/`usePreviousClose`
  unmocked over a stubbed relay payload, open the launcher, and assert the
  gate's `.price-bar` shows the fixture's `$121.40` / `$121.60`.

- **T-181 [P2] Eleven of the new quote-telemetry surface tests assert labels
  and never a single number, so they cannot distinguish a correct quote from a
  mangled one.** `web/tests/modify-order-full-telemetry.test.tsx:119-149` and
  `web/tests/mobile-chain-detail-quote-telemetry.test.tsx:91-125` supply fully
  populated `PriceData` (93.5 / 96.5 / 812 / 99.4) and assert only field labels
  and the `price-bar--tight` class. Only `order-quote-telemetry.test.tsx` and
  `futures-order-quote-telemetry.test.tsx` assert values, so a per-surface
  value defect — a swapped bid/ask, a sign flip on a credit spread — has no
  coverage on nine of eleven surfaces.
  **AC:** red — swap `bid`/`ask` in the fixture feed of each surface; today
  nine files stay green. Green — one `expect(screen.getByText("$93.50"))`-class
  value assertion per file.

- **T-182 [P2] A real-sleep wall-clock upper bound with 25% headroom.**
  `scripts/tests/test_ib_event_waits.py:124-131` —
  `mock_ib.sleep.side_effect = lambda seconds: time.sleep(0.2)` then
  `assert elapsed < 0.5`. The loop makes one or two real 0.2s sleeps, so the
  floor is 0.4s against a 0.5s ceiling. Any runner where `time.sleep(0.2)`
  overshoots by >25% — a loaded xdist shard, this macOS runner under the
  parallel gate — crosses the bound with nothing wrong.
  **AC:** red — lower the stub to `time.sleep(0.24)` and the assertion fails
  with correct behaviour. Green — assert `elapsed < 1.0` (still an order of
  magnitude under the 0.8s nominal-step bug it guards), or assert on
  `mock_ib.sleep.call_count <= 2` alone, which already carries the contract.

- **T-183 [P2] Both e2e specs added this delta are in no CI invocation, and
  one of them is the Gate 1 surface.** `web/e2e/order-payoff-ratio.spec.ts` and
  `web/e2e/dashboard-vol-cone-hero.spec.ts` were added; the curated Playwright
  list at `ci.yml:518-535` is unchanged at 14 specs. `order-payoff-ratio` covers
  the leveraged payoff multiple shown before execution — the Gate 1 (convexity)
  figure. The job is explicitly non-gating and not in `deploy.needs`, so the
  impact is observability, not a shipped defect.
  **AC:** pre-flight both under the server CI actually uses
  (`npm run build` then `PLAYWRIGHT_WEBSERVER_CMD="npx next start -p 3030"`,
  the T-073 method) and, if green across repeated runs, add
  `order-payoff-ratio.spec.ts` to the curated list. Red — mutate the asserted
  payoff multiple and confirm the exact curated invocation reds.

- **T-184 [P2] Two genuinely distinct same-day partials of equal size are
  silently dropped from the realized-P&L replay when one arrived via the live
  daemon and the other via Flex backfill.**
  `scripts/clients/journal_realized.py:248-251` classifies an id as `"flex"`
  (digit-only) or `"api"` (otherwise), and
  `_already_journaled_under_other_namespace` dedupes on
  `contract_fill_fingerprint` = `(contract, ET date, signed qty)`
  (`scripts/clients/journal_basis.py:123-152`). Two real partials of equal size
  on the same day — one captured live by the monitor daemon with a dotted API
  `execId`, one backfilled by Flex rehydrate with a numeric `tradeID`, the
  realistic state when the daemon was down for part of a session — collide on
  that fingerprint and the second is dropped. Realized P&L is understated and
  the replay under-closes the position. The tests encode both NEIGHBOURS and
  not this case: `scripts/tests/test_journal_realized.py:144` (same close under
  both ids → counted once, correct) and `:152` (two equal partials from ONE
  writer → both count, correct). The docstring at `journal_realized.py:257-259`
  acknowledges the one-writer carve-out; the cross-writer collision is
  undocumented and unpinned. Related to the composite-Flex-row case logged out
  of scope under T-124.
  **AC:** red — `test_two_distinct_same_day_partials_across_writers_both_count`:
  a BUY of 20, then a SELL of 10 at 3.00 on `2026-08-24` under an API id and
  another SELL of 10 at 3.00 the same day under a numeric Flex id, with
  distinct `exec_time`s. Today it yields one key; it should yield both. If the
  drop is a deliberate tradeoff, the test must instead assert the drop AND that
  the contract is marked incomplete so the blotter falls back to IB's figure
  rather than silently halving.

- **T-185 [P2] SUSPECTED — the `radon-app-runtime` cgroup fix pins the exact
  argument string but nothing covers the failure class it belongs to.**
  `cloud/scripts/radon-app-runtime.sh:109` changed
  `--cgroup-parent="system.slice/${unit}"` → `--cgroup-parent=system.slice`, and
  `cloud/tests/test_app_runtime.py:156-158` asserts the literal
  `"--cgroup-parent=system.slice --env-file"` plus the absence of
  `system.slice/{unit}` — tight enough that this specific regression cannot
  return. What is untested is the class: the bug was `docker run` REJECTING the
  flag at runtime, and no test asserts `cmd_run` surfaces a non-zero
  `docker run` as a non-zero exit / health row rather than a silently dead app
  container. Every assertion is a grep over a fake-docker log.
  **AC:** red — stub `RADON_DOCKER_BIN` with a script exiting 125 (docker's
  "daemon rejected the run" code) and assert `cmd_run` propagates non-zero.

- **T-186 [P2] Two assertions in the new performance contract cannot fail.**
  `web/tests/portfolio-startup-performance-contract.test.ts:31` —
  `expect(workspace).toMatch(/case "portfolio":[\s\S]*return null;/)` is
  unanchored and greedy across a ~4,000-line file, so ANY `return null;`
  anywhere after the case label satisfies it. `:42` —
  `expect(page).not.toContain("fetch(")` is a substring trap: adding
  `router.prefetch(`, `refetch(`, or the word in a comment to
  `app/portfolio/page.tsx` reds a performance contract that is not violated.
  Both are in the file already filed as T-167; listed separately because the
  fix is different (anchor the patterns vs. replace grepping with execution).
  **AC:** red — insert an unrelated `return null;` far below the case label and
  confirm `:31` still passes; add a `// fetch(` comment and confirm `:42` reds.
  Green — anchor to the case body (`/case "portfolio":\s*(?:\/\/[^\n]*\n\s*)*return null;/`)
  and use `/\bawait\s+fetch\(/`.

- **T-187 [P2] SUSPECTED — `stage-release` SSHes to the production host and
  runs `deploy.sh` with no `needs:`, concurrently with every test job.**
  `ci.yml:544-549` — no `needs:`, `if: github.ref == 'refs/heads/main' &&
  github.event_name == 'push'`, `continue-on-error: true`, invoking
  `cloud/scripts/deploy.sh "$SHA"` with `RADON_DEPLOY_STAGE=1` (`:608-609`). The
  four `RADON_DEPLOY_STAGE` branches (`cloud/scripts/deploy.sh:1512,1536,1698,1717`)
  take the deploy lock, run `build_staged_release` + `write_prestage` into an
  isolated worktree and return before any service restart, so no unit is
  touched — but build-time side effects (worktree, install, `next build`) from
  an ungated SHA land on the production host even when the gates go red.
  `scripts/tests/test_ci_deploy_concurrency.py:97-115` asserts this shape
  deliberately. Marked SUSPECTED: `build_staged_release` was not read in full.
  **AC:** if tightened — `needs: [changes]` at minimum, plus a test asserting
  `RADON_DEPLOY_STAGE=1` never reaches `sync_scheduled_units` or any
  `systemctl` path. Confirm the SUSPECTED half first by reading
  `build_staged_release` end to end.

- **T-188 [P2] SUSPECTED — `4069ea9b` fixed the weekend-wrapper race's root
  cause but left a 4-second wall-clock window.**
  `scripts/tests/test_weekend_wrapper_self_rewrite.py:345` builds with
  `agent_sleep=4` (→ `STUB_CLAUDE_SLEEP`, `:185`). The lock-file poll at
  `:355-359` is correctly deadline-bounded, but `second = _run(cfg, "audit")`
  at `:360` must spawn bash, read the wrapper and reach the lock check before
  the holder's 4s stub finishes and releases, or `second.returncode` is 0 and
  `:364` fails. The commit did fix the real 127-vs-3 cause (the stub clobbering
  the file the second instance was reading); it narrowed the timing dependency
  rather than removing it. Same load class that produced the original report.
  **AC:** red — set `agent_sleep=1` and run the file under `pytest -n 8`
  alongside the rest of `scripts/tests`. Green — have the holder's stub block on
  a FIFO/sentinel the test removes after `second` returns, instead of a sleep.

- **T-189 [P2] SUSPECTED — ephemeral-port TOCTOU in the new caddy mechanism
  test.** `cloud/tests/test_caddyfile.py:235-238` (`free_port()`) binds port 0,
  reads the number, then CLOSES the socket; `:270-271` allocates two and caddy /
  the stub server bind them later at `:283` and `:302`. Two xdist workers, or
  any process claiming the port in that window, make caddy fail to listen and
  `wait_for_listener` fail at `:295`. Correctly skipped on hosts without caddy
  (`:229`), so this does NOT add to the darwin baseline — it only bites where
  caddy is on PATH, i.e. exactly the environment T-164 asks CI to become.
  **AC:** red — bind the returned port from a second process before
  `caddy run`. Green — hold the probe socket open and hand the live port to
  caddy, or retry allocate-and-bind on `EADDRINUSE`.

### Cross-cutting note

T-156, T-157 and T-159 are one root cause with one fix: `path_filter.py`'s
two-bucket model assumes `scripts/`↔pytest and `web/`↔vitest are disjoint sets,
and that `docs/` is inert. Neither holds — this repo deliberately uses each
gate to assert contracts on the other tree's source, and `docs/` carries
runtime JSON. The durable fix is to DERIVE the classification from the test
configuration (vitest's `include` globs, the pytest collection roots, and the
cross-tree paths tests actually read) rather than from a hand-maintained prefix
list, so the filter cannot drift away from the suites again. T-160 belongs to
the same commit and compounds all three.

## Delta audit 2026-08-27

Range `1b326772..HEAD` (`789aabea`) — 43 commits, 264 files, +15059/-946.
139 non-test source files changed; 109 test files changed, 53 added.
New findings continue the frozen numbering at **T-190**. PART A (§1–§10)
is untouched; nothing above this line was rewritten.

**Range overlap.** As in the last two audits, the ledger SHA is the previous
audit's HEAD rather than the merge of its PR, so PR #99 ("Testing weekend
2026-08-26", merged 2026-08-26T21:39Z as merge commit `17e21c5a`) sits INSIDE
this range and re-contains the whole T-156…T-189 remediation. Those commits
were re-triaged as ordinary delta, not exempted — T-192, T-195, T-196, T-204,
T-205, T-206, T-207 and T-216 are all findings ON last weekend's own
remediation. Unlike the 2026-08-25 branch this one was a true merge, not a
squash, so no ancestry check was ambiguous.

### Runner environment

`rtk` is NOT installed on this host (`command -v rtk` empty), so bare `git`
is the only git available and its output was trustworthy — the 2026-08-16 rtk
rail does not apply here. `node` v24.14.0 needed
`~/.nvm/versions/node/v24.14.0/bin` prepended per Bash-tool shell.
`pytest-asyncio`, `pytest-xdist` and `pytest-cov` were already present in the
shared venv from the 2026-08-22/25 installs; nothing was installed and the
repo was not touched. `caddy` is absent (T-205). Tree was clean at start —
only the wrapper's own `.weekend-runner.lock/` untracked; no stash, no parked
WIP. `origin/testing/weekend-2026-08-27` did not exist at pre-flight and the
empty branch was pushed immediately as the cross-host collision signal.
**Load average was 56 at pre-flight** (the reliability loop runs concurrently
in its own clone) and 12–36 across the gate runs, so counts were read with the
load-sensitivity rule in mind.

**Scratch-path collision with the reliability loop (new hazard).** Mid-run,
`/tmp/delta_section.md` — this audit's draft — was OVERWRITTEN by the
reliability loop's own draft (REL-numbered content) running concurrently in
`~/radon-weekend/radon`. The two loops keep separate CLONES but share `/tmp`,
and both had independently chosen the same generic filename. No repo file was
affected and no finding was lost (the P0 block was rewritten from the agent
reports), but gate outputs were also sitting in a shared `/tmp/gates`. All
scratch for this run was moved to `/tmp/tw-2026-08-27/` and the gate counts
re-verified from the copies. Recorded as a lesson.

### Standing sweeps

**Collection union — CLEAN on all three gates.** Root pytest collects
8161/8251 (90 deselected) across 478 unique files; the union of the ten CI
shard globs, expanded with python `glob` honouring shell semantics and
walking directories recursively, is 479. `comm -23` is EMPTY — zero silent
drops. The single `comm -13` entry is `test_menthorq_integration.py`, matched
by a shard but locally deselected by `addopts = -m 'not integration'`.
`cloud/tests` 1146 tests / 33 files vs shards `al 21` + `mz 12` = 33, no
overlap, no gap. Vitest `list --filesOnly` 758 files vs a disk sweep of 758,
`comm` empty both directions. **T-122 is holding on all three.**

**Enforcement — STRENGTHENED, nothing dropped. T-160 is FIXED.** `deploy.needs`
went from 7 entries at `1b326772` to 9 at HEAD: `web-coverage` and `py-coverage`
are back (`ci.yml:634`), with success-or-skipped conditions at `:640`/`:642`.
`stage-release` lost `continue-on-error: true` and gained a real `needs:` list.
Branch protection on `main` is unchanged and still carries **no
`required_status_checks` key at all**, so the new
`.github/required-status-checks.json` declares `"contexts": []` and is
structurally inert — filed as T-222 for the ledger, not as drift.

**Coverage-ratchet honesty — nothing lowered; the measurement got STRICTER
twice.** `vitest.config.ts` diffs to exactly one hunk and it is
`retry: process.env.CI ? 1 : 0` → `retry: 0` (the T-161 fix); thresholds
lines 75 / functions 71 / branches 65 and all 21 `coverage.exclude` entries are
byte-identical. `pyproject.toml` is `diff -q` IDENTICAL to base — `branch = true`
retained, `omit` unchanged at 4 entries. `--fail-under=56` unchanged.
`scripts/ci/merge_vitest_coverage.py` moved the HONEST way on both counts:
`_pct` now returns `None` instead of `100.0` when `total <= 0` and `evaluate`
treats that as a FAILURE ("a broken report, not full coverage"), and the new
`--expect-shards 8` refuses to gate on a partial artifact glob. **No T-050
threshold decision is needed this run.**

**New skips — four constructs, eight skipped outcomes, none linked to a T-###.**
Parsed every added line of the delta patch with python (BSD grep on this host
lacks `--glob`, mangles piped output, and does not support `\|` alternation —
which silently returned an empty result once during this run and briefly
looked like missing content). Zero `test.skip` / `it.skip` / `xit` /
`xdescribe` / `xfail` / `@unittest.skip` / `.todo`. Four hits in code, all in
two files NEW in this delta, measured at `28 passed, 8 skipped`:
`cloud/tests/test_app_plane_cutover_safety.py:131,181,184` (T-204) and
`cloud/tests/test_caddy_edge_timeouts.py:228` (T-205). The five pre-existing
whole-tree skips are byte-identical at base and are not delta findings.
**Zero `.only` anywhere** — the one `.only(` hit in the patch is the previous
audit's own prose at `TEST_AUDIT.md:3336`.

**Cloud baseline on darwin — 34 failed, unchanged.** `pytest cloud/tests` at
HEAD reads `34 failed, 1099 passed, 13 skipped` (358s); the sorted `FAILED`
list was diffed against the same command run at `1b326772` in
`git worktree add --detach /tmp/base_1b326772` — **byte-identical, 34 both
sides**. All 34 are the known `sha256sum` / bash-3.2 darwin class (T-118).
Passed moved 1062 → 1099 and skips 5 → 13; the eight added skips are exactly
the two new files above. **The recorded darwin baseline stays 34 failed.**

**Gates ×1 serial from the repo root (clean tree, HEAD `789aabea`).**

| Gate | Round 1 |
|---|---|
| `python3.13 -m pytest` (recursive) | **7 failed**, 8153 passed, 1 skipped, 90 deselected (397.8s) |
| `python3.13 -m pytest cloud/tests` | 34 failed, 1099 passed, 13 skipped (358.4s) |

The 7 pytest reds are ALL in one file and are **deterministic, not load
flake** — re-run in isolation they reproduce 7/7 in 2.26s. They are filed as
**T-237** and they are real: `main` is red. CI at this SHA agrees
(`pytest (scripts-npsz)` failed, deploy skipped). Load average was 21 at the
start of the run and 10.8 at the end, so load is not the explanation.
The vitest gate was deliberately held until the agent fan-out drained and is
recorded in `TEST_LOG.md`.

**Determinism.** The delta touches 109 test files — 53 of them added — so the
"re-run delta-touched files 3×" rule again collapses into full-gate runs, as
in 2026-08-25 and 2026-08-26. Scope was the added files; counts in
`TEST_LOG.md`.

### Re-triage of the standing NEW_FINDINGS items

- **E2E testid backlog** — STILL OPEN, partially improved. Net **+13**
  `data-testid` lines this delta (vs +2 last), all on new surfaces
  (`FreshnessRail` +3, `OptionsChainTab` +5, `TicketRiskBlock` +2,
  `MobileOrderTicket` +2, `MobilePositionList` +1). The originally catalogued
  components are untouched: `QuoteTelemetry.tsx` still has **0** (T-174 open),
  `SharePnlButton.tsx` 0, `Toast.tsx` 0, `OrderTab.tsx` 1. Ten test files added
  this delta still reach for class names — worst are
  `chain-ticket-rail-layout.test.tsx` (6 CSS selectors) and
  `ticket-risk-block.test.tsx` (5). Extended this delta as T-234 and T-235.
- **`next dev` in the CI Playwright container** — unchanged, still infra-open
  (`ci.yml:479-481`).
- **`next start` Day Move divergence** — unchanged; `day-move-ib-daily-pnl.spec.ts`
  is 0 bytes changed and still held out (`ci.yml:481-483`). But the source
  underneath it moved: see **T-210**.
- **`e2e/performance-twr-payload.spec.ts`** — unchanged, still uncurated
  (`grep performance-twr-payload .github/workflows/ci.yml` → no hit).
- **`resolveSpreadPriceData` wall-clock sibling** — STILL OPEN and now numbered
  as **T-217**. `web/lib/positionUtils.ts:489` still stamps
  `timestamp: new Date().toISOString()`.
- **Six unguarded-ctor producers** — STILL OPEN, all six, none fixed
  incidentally. Ran the guard: 3 passed including
  `test_the_baseline_has_no_stale_entries`, so the baseline is accurate and no
  seventh appeared. Confirms the 2026-08-26 note that `run()` (T-162) and
  `fetch_uw_closes` are different functions.
- **`pytest cloud/tests` darwin reds** — unchanged at 34. Worth recording that
  the portable pattern the finding asks for already exists three files over:
  `cloud/tests/test_install_units.py:74-78` shims `sha256sum` for macOS, so this
  is now a copy-an-existing-pattern fix, not a design decision.
- **Unreproduced 10-failure vitest round (2026-08-17)** — no recurrence. Note
  the persistence rule is still agent-discipline: `scripts/testing_weekend.sh`
  only `tee`s a run log and reports `tail -c 1500`.
- **Retry classifier bypassed by isinstance** — STILL OPEN, same shape, code
  moved to `scripts/db/writer.py:357-359`. Blast radius still nil.
- **T-130 (demo isolation, operator-blocked)** — unchanged; `ci.yml:320-327`
  still short-circuits and emits `::warning title=Demo isolation guard SKIPPED::`.
  `scripts/ci/check_demo_isolation.py` has still never executed.
- **T-072 PARTIAL row** — stale bookkeeping only; the components half landed
  (`vitest.config.ts:76-79`).

### P0 — money-losing gaps

- **T-190 [P0] The mobile order ticket builds its payoff from RAW legs while
  the desktop rail normalises to one combo, so every breakeven and every
  unbounded-risk dollar figure on mobile is wrong by the quantity factor.**
  `web/components/mobile/MobileOrderTicket.tsx:393-401` maps `legs` directly;
  the desktop twin at `web/components/ticker-detail/OptionsChainTab.tsx:447-455`
  maps `quotingLegs = normalizeComboOrder(legs).legs`, GCD-normalised at
  `web/lib/optionsChainUtils.ts:188-201`, with an explicit comment that the
  normalisation is what makes the curve match the `RISK · PER 1× COMBO`
  heading. `netPremium` remains per-1-combo (`MobileOrderTicket.tsx:628`).
  A SELL 10× 970 call @ 2.98 therefore shows breakeven **970.30** instead of
  972.98, and the 10-lot short put's "reaches $X at zero" line
  (`MobileOrderTicket.tsx:411`, `× 100 × totalQty` on an already-scaled leg)
  reads **-$9,697,020** instead of -$967,020 — the exact number the operator
  is asked to acknowledge before transmitting unbounded risk.
  **AC:** red — render `MobileOrderTicket` with `SHORT_CALL` at `quantity: 10`,
  `limitPrice: 2.98`; assert the `BREAKEVENS` cell reads `972.98` and that
  `ticket-unbounded-warning` contains `972.98`. Add the desktop twin at qty 10
  asserting the same number so the surfaces are pinned to each other. Green —
  line 395 maps the normalised legs; mutating `OptionsChainTab.tsx:449` to
  `legs.map` must red the desktop assertion.

- **T-191 [P0] `reconcile_tables` copies the ROUNDED row over the good one
  whenever `z_score_3m` is null, republishing the exact percentile inversion
  the module was written to prevent.** `scripts/utils/cta_percentiles.py:96-103`:
  `gap` is `math.inf` when `implied is None`, and the tie-break at `:102` is
  strict (`gap < best[key][1]`), so `inf < inf` is False and the FIRST table
  encountered wins. `_row_key` (`:56-59`) is `(name, position_today, _num(z))`,
  so with `z` null in both rows the rounded and the good row collapse to one
  key. **Reproduced in-process:** two tables for the same MAX LONG contract,
  `z_score_3m: None` in both, `main` carrying `[0,0,0]` and `index` carrying
  `[81,81,81]` → output is `main [(0,0,0)]`, `index [(0,0,81)]`. The good 3m
  percentile of 81 is destroyed. The vision extractor returns a null `z` for
  any row it cannot read, so this is reachable on ordinary input.
  **AC:** red — the fixture above; assert the `index` row keeps 81.
  Green — prefer a finite-gap candidate over an infinite-gap one (and, when
  both are infinite, prefer the row whose trio is not all-rounded), then
  mutating the tie-break back must red it. Every fixture in
  `scripts/tests/test_cta_percentiles.py:22-32,59-63,71-75,80-84` carries a
  numeric `z`, which is why this survived.

- **T-192 [P0] A `vitest.config.ts`-only commit skips the entire python gate —
  including the ONLY test that pins the vitest coverage thresholds — so a
  ratchet can be lowered with no test running anywhere.**
  `scripts/ci/path_filter.py:29-31` puts `vitest.config.ts`, `package.json` and
  `bun.lock` in `WEB_PREFIXES`, while `PYTHON_READS` (`:82-98`) lists
  directories only. Verified in-process: `classify(["vitest.config.ts"])` →
  `(False, True)`; same for `["package.json"]` and `["bun.lock"]`. `py-tests`
  and `cloud-tests` are both `if: needs.changes.outputs.python == 'true'`
  (`ci.yml:181`, `:333`), so **8161 + 1146 = 9307 python tests skip**, and
  `deploy.if` accepts `'skipped'` (`ci.yml:642-644`). The only assertion in the
  repo pinning the vitest numbers is
  `assert load_thresholds(text) == {"lines": 75, "functions": 71, "branches": 65}`
  at `scripts/tests/test_merge_vitest_coverage.py:38` — a python test. This is
  the same class as T-156/T-157, and the T-157 remediation's own new guard
  (`test_every_tree_read_by_a_python_test_routes_to_the_python_gate`,
  `scripts/tests/test_path_filter.py:225`) cannot see it because
  `_top_level_trees()` filters `git ls-files` with `if "/" in line` (`:47`) —
  root-level FILES are never derived. Directly violates the loop's own rail 5.
  **AC:** red — `assert classify(["vitest.config.ts"]) == (True, True)`, and
  extend `_top_level_trees()` to yield root-level tracked files so every root
  file read by a module under `tests/`, `scripts/tests/`, `cloud/tests/`
  routes `python=True`. Green — add the three names to `PYTHON_READS`;
  `classify(["pyproject.toml"])` must stay `(True, False)`.

- **T-193 [P0] `/api/orders/modify` returns 502 "Modify not confirmed" on a
  modify IB has already accepted, whenever the orders refresh times out.**
  `web/app/api/orders/modify/route.ts:296` is
  `const orders = refreshed ? await readOrdersSnapshotFromDb() : null;`, which
  feeds `isModifyConfirmed(null, …)` — and that returns `false` at `:105`
  (`if (!order) return false`). The refresh has a 10s budget (`:289`), so a
  slow Turso read turns a SUCCESSFUL modify into a reported failure on a live
  resting order; the operator re-modifies, issuing a second cancel/replace
  against an order that was already replaced. The cancel route's twin
  (`cancel/route.ts:88-92`) correctly returns `orders: null` inside a
  `status: "ok"` body. The only test of this change is the source grep at
  `web/tests/p2-operability-remainder.test.ts:97-105`;
  `web/tests/orders-place-cache-race.test.ts:200-226` drives the route only on
  the path where the refresh RESOLVES (`:133`).
  **AC:** red — in `orders-place-cache-race.test.ts`, reject `/orders/refresh`
  after the modify succeeds; assert the response is not a 502-unconfirmed.
  Green — distinguish "refresh unavailable" from "refuted by the book"; a
  mutation to `if (orders && !isModifyConfirmed(...))` must flip the new test
  while `p2-operability-remainder.test.ts:99-103` stays green either way,
  proving the grep has no signal.

- **T-194 [P0] The R-250 fat-finger order gate is tested only on `--type stock`
  — never on options, which is Radon's actual instrument.** All five cases in
  `scripts/tests/test_ib_execute_order_limits.py:71,83,95,106,115` pass
  `["--type", "stock", …]`; `grep -c '"--type", "option"'` on that file is
  **0**. The invocation the fix's own docstring names —
  `scripts/risk_reversal.py:501`, `--type option … --side SELL … --yes` — takes
  the untested branch. Worse, the tests cannot distinguish WHICH cap fired:
  `test_over_cap_quantity_is_refused_before_placement:68-77` sets
  `RADON_MAX_STOCK_ORDER_QTY=10` with qty 5000, and 5000 also exceeds the
  option default of 500, so it stays green even if the stock cap is never
  selected. A refactor that moves the `check_order_limits` block inside
  `if args.type == 'stock':` (`scripts/ib_execute.py:449-464`), or drops
  `"type": args.type` from the params dict at `:456` — which changes `is_stock`
  at `order_limits.py:197` and the ×100 at `:166` — leaves the live option
  placer uncapped with all six existing tests green. Concrete pass-through:
  500 short 300-strike puts at $1.50 → notional $75,000 (< $250,000 default)
  and qty 500 (== cap), against ~$15M of assignment exposure, since
  `combo_max_loss` returns `None` for `type == "option"` (`order_limits.py:177`).
  Independently found by two agents.
  **AC:** red — add `--type option … --qty 20 --limit 200.00` with
  `RADON_MAX_ORDER_NOTIONAL=100000` (the mocked `get_option_contract` at
  `test_ib_execute_order_limits.py:43` already supports it); assert
  `placed == []` because 20 × 200 × **100** = $400,000. Add a second case
  pinning `RADON_MAX_ORDER_QTY` as the binding qty limit. Green — setting
  `multiplier = 1` at `order_limits.py:166`, or indenting the gate under the
  stock branch, must red the new cases while the six existing ones stay green.

- **T-237 [P0] HEADLINE — `main` is RED right now: a test file merged at HEAD
  references a constant that has never existed, 7 tests fail deterministically,
  and production has not deployed since.**
  `scripts/tests/test_portfolio_risk_gate3_measurability.py:153,172` read
  `portfolio_risk.BACKFILL_WALL_CLOCK_BUDGET_S`. That attribute does not exist
  and never has (`git log -S'BACKFILL_WALL_CLOCK_BUDGET_S' -- scripts/portfolio_risk.py`
  returns NOTHING); the real constant is `BACKFILL_TOTAL_BUDGET_S = 20.0`
  at `scripts/portfolio_risk.py:84`. Python reports it directly:
  `AttributeError: module 'portfolio_risk' has no attribute
  'BACKFILL_WALL_CLOCK_BUDGET_S'. Did you mean: 'BACKFILL_TOTAL_BUDGET_S'?`
  **Reproduced 7/7 in isolation in 2.26 s** (`7 failed, 1 passed`), so this is
  a deterministic red, NOT the load-sensitive class — the full-gate run that
  first surfaced it was under load average 21, but the isolated re-run settles
  it. The file was added by `789aabea`, the reliability loop's PR #100, which
  is `origin/main` as of this audit.
  **CI agrees and the gate held.** The ci.yml run at `789aabea`
  (`33038426120`) is `failure`: `pytest (scripts-npsz)` failed, and
  `Prestage VPS release`, `pytest coverage ratchet` and `Deploy to VPS` were
  all correctly SKIPPED. So the deploy protection worked exactly as designed —
  but the consequence is that **`main` has been red and undeployed since that
  merge**, and every subsequent push inherits a red baseline in which a genuine
  new regression is indistinguishable from this one.
  This is a cross-loop finding: the defect is the reliability loop's, but the
  testing loop is where it surfaces, and the audit's job is to name it.
  **AC:** red — `python3.13 -m pytest scripts/tests/test_portfolio_risk_gate3_measurability.py`
  is 7-red today. Green — rename both references to `BACKFILL_TOTAL_BUDGET_S`
  (the test's intent and the source's semantics match; the source is correct
  and the test is wrong, so the TEST is what changes here), then the file is
  8-passed and the `scripts-npsz` shard is green. Do NOT skip or delete the
  tests — they assert a real budget bound that `portfolio_risk.py:446` honours.
  **Needs the operator's eye:** production has not deployed since `789aabea`.

### P1 — correctness gaps

- **T-195 [P1] The contract test written to stop the META market-value
  regression is GREEN against that exact regression.**
  `web/tests/market-value-single-source-contract.test.ts:35,50`. The
  `MV_ACCUMULATION` regex requires a `sign`-named identifier right after `+=`,
  and the line pre-filter requires `rtMv|marketValue|mv` on the same line.
  The actual pre-fix defect was
  `total += (leg.direction === "LONG" ? 1 : -1) * last * leg.contracts * 100;`
  (`git show 8fdd116e^:web/components/mobile/MobilePositionList.tsx:92`) —
  neither matcher fires. **Verified in-process:** running the lint against the
  pre-fix file yields `violations == []`, and both `MV.test(buggyLine)` and the
  name guard are `False`. Two further vacuities in the same file: the
  `catch { continue; }` at `:41-43` means renaming a surface makes the check
  silently pass (its comment claims "the list below is the pin"; there is no
  such pin), and the `positionUtils.ts` check slices a magic `indexOf(...) + 1_400`
  byte window, so adding a doc comment can push the target out of range.
  **AC:** red — point `POSITION_SURFACES` at the pre-fix file and assert the
  lint reds. Green — replace the regex with a behavioural assertion (rendered
  MV string equals `resolveRealtimeMarketValue(pos, prices) ?? resolveMarketValue(pos)`
  per surface), add `existsSync` on every entry, and parse to the matching
  brace instead of a byte window. `it #2` (the import check) DOES catch this
  case and should stay.

- **T-196 [P1] The 5000-run same-day P&L property suite is algebraically a
  tautology — it asserts `X === X` and cannot fail.**
  `web/tests/fuzz/same-day-pnl.fuzz.test.ts:173-175,182,194,208`.
  `surfaceTotalPnl` (`:174`) is
  `getPnlDollars(pos, resolveRealtimeMarketValue(pos, prices) ?? resolveMarketValue(pos))`.
  `resolveRealtimeMarketValue` (`web/lib/positionUtils.ts:575-582`) IS
  `computeRtMv` for non-Stock, and `getPnlDollars` (`:344-351`) is
  `mv - resolveEntryCost(pos)` — byte-for-byte the same expression
  `getTodayPnlDollars` evaluates in its same-day branch (`:681-686`). P1/P2
  therefore reduce to an identity for all 1000×3 draws; P3 (`:208`) mirrors
  `getOptionDailyChg`'s formula (`:591-601`); P4's body (`:223`) asserts only
  `Number.isFinite`. Commit `8fdd116e`'s own message claims a pre-fix
  verification for the SURFACES fuzz and makes no such claim for this file.
  **AC:** red — mutate `positionUtils.ts:683` `resolveMarketValue(pos)` to
  `resolveMarketValue(pos) * 2`; P1/P2/P5 must currently stay green because
  `surfaceTotalPnl` inherits the same mutation. Green — compute the expected
  value independently inside the property from the ARBITRARY SPEC
  (`sign * price * contracts * multiplier`), not from library helpers; or
  delete the file and keep `same-day-pnl-surfaces.fuzz.test.tsx`, which reads
  the DOM.

- **T-197 [P1] `run_flow_refresh.sh` calls `_flow_health` 49 lines before the
  function is defined, so the market-state-probe outage writes NO
  `service_health` row — the exact silence R-221/R-265 exist to close.**
  Call site `scripts/run_flow_refresh.sh:97`; definition `:146`. Bash resolves
  functions at execution time in source order, so the call aborts with
  `command not found`. **Reproduced live** on a staged copy with a broken
  calendar probe: `scripts/run_flow_refresh.sh: line 97: _flow_health: command
  not found`, `RC=1`, no row. The two other call sites (`:264`, `:269`) are
  after the definition and are fine.
  `scripts/tests/test_flow_refresh_shed_honesty.py:74` asserts only
  `returncode != 0` and `"Market closed" not in combined` — never the row,
  never a clean stderr; the row test at `:192` is
  `assert "service_health" in source`, a grep satisfied by the BODY of
  `_flow_health` even if no caller ever reaches it. Ran the file: **8 passed**
  with the bug present. This is a source defect the audit identified, so
  fixing the script is in scope alongside the test.
  **AC:** red — run the wrapper with a failing market-state probe and a stubbed
  `write_service_health_http`; assert exactly one `("flow-refresh","error")`
  write and that stderr carries no `command not found`. Green — move the
  definition above line 97.

- **T-198 [P1] Every app unit now gates its start on a LIVE GHCR call with no
  local-image fallback, so a registry outage parks all five units.**
  `cloud/scripts/radon-app-runtime.sh:53-70` (`image_available` / `resolve_image`)
  probes with `docker manifest inspect` and never falls back to
  `docker image inspect`. Under `set -euo pipefail` a GHCR 429, outage or
  expired root credential fails both the SHA and `latest` probes, `resolve_image`
  returns 69, and `image="$(python_image)"` (`:118`) exits — while the correct
  image is already in the local store. With `Restart=always` +
  `StartLimitBurst=5`, radon-api, radon-nextjs, radon-relay, radon-monitor and
  radon-newsfeed all park `start-limit-hit`. The only coverage is two string
  greps (`cloud/tests/test_app_plane_cutover_safety.py:155,162`); nothing
  executes `resolve_image`, despite the file shipping a full
  `RADON_APP_RUNTIME_TEST_MODE=1` harness with a stub `$RADON_TEST_DOCKER`.
  **AC:** red — with the stub docker: (a) `manifest inspect` fails for the SHA,
  succeeds for `latest` → run uses `:latest`; (b) fails for BOTH while
  `image inspect` succeeds → the unit still runs off the local image;
  (c) absent everywhere → exit 69. (b) is red today.

- **T-199 [P1] `_durable_store_available` is the sole switch between Flex
  fail-closed and fail-open, and every test monkeypatches it out of existence.**
  `scripts/utils/flex_embargo.py:157-165` returns `False` whenever `read_env()`
  raises or credentials are not loaded at that instant; on that path a
  transient Turso read error with no sidecar sends every Flex caller into a
  live IBKR 1025 lockout, and each `SendRequest` EXTENDS the lockout at IBKR's
  end — the R-212 incident, unfixed. It also branches on `PYTEST_CURRENT_TEST`
  in production code (`:165`), which guarantees the natural path can never run
  under pytest. Both fail-closed tests replace the function with a lambda:
  `scripts/tests/test_flex_embargo_fail_closed.py:56` (`lambda: True`) and
  `:152` (`lambda: False`). The real body has **zero** assertions.
  **AC:** red — direct tests with `RADON_DB_TEST_WRITE_OK=1`: configured
  URL+token → `True`; `read_env` raising → must be `True` when a `TURSO_DB_URL`
  is present, so the guard fails CLOSED (currently `False`). Plus a test that
  the module contains no `PYTEST_CURRENT_TEST` branch. Never exercise this
  against a live gateway.

- **T-200 [P1] The R-211 bounded-lock fix converted the two READ paths and left
  the WRITE path — the one that holds the exclusive guard across two fsyncs —
  unbounded, and neither half has a test.**
  `scripts/api/ib_gateway.py:246` (`_acquire_gateway_push_lease`) and `:1346`
  call `ib_2fa_lock.acquire_2fa_push_lock` with no `guard_timeout_secs`, so
  `_guard(exclusive=True)` takes the blocking-flock branch
  (`scripts/utils/ib_2fa_lock.py:206-213`). `POST /ib/restart` then blocks
  forever inside the FastAPI handler while `ib_watchdog` holds the guard — no
  timeout, no log line. `ib_watchdog.py:751-756` and `:1564-1586` DO pass
  `LOCK_OP_TIMEOUT_SECS`, so only the API side hangs. Separately the new
  `LOCK_OP_TIMEOUT_SECS` / `_check_2fa_push_lock_bounded` /
  `_remaining_lock_secs_bounded` helpers (`:105-133`) have no test reference
  anywhere: grep across `scripts/**/*.py` returns only `ib_watchdog.py` and
  `ib_gateway.py`. `scripts/api/tests/test_ib_restart_2fa_lock.py:128-154`
  acquires a real but UNCONTENDED lease, so bounded and unbounded calls are
  indistinguishable.
  **AC:** red — an AST walk asserting every `ib_2fa_lock.*` call site in
  `ib_gateway.py` passes `guard_timeout_secs`; plus patch
  `check_2fa_push_lock` to raise `GuardLockTimeout` and assert
  `restart_backoff_state()["push_lock"]["holder"] == "guard-timeout"` and
  `_remaining_lock_secs_bounded` → `-1`. Read-only; nothing touches a live
  gateway.

- **T-201 [P1] `check_2fa_push_lock` blocks the FastAPI event loop for up to
  ~1.85 s per `/health` poll, and both suites zero the interval so the cost is
  unobservable.** R-210 put 3 × `_PORT_PROBE_TIMEOUT_SECS=0.35` connects plus
  2 × `time.sleep(ORPHAN_CONFIRM_INTERVAL_SECS=0.4)` inside `_is_orphaned`
  (`scripts/utils/ib_2fa_lock.py:294-306`). `check_2fa_push_lock` →
  `restart_backoff_state()` (`scripts/api/ib_gateway.py:203`) →
  `check_ib_gateway()` (`:1221`, `:1245`) is an `async def` calling it WITHOUT
  `asyncio.to_thread`. Precisely when a lease is held past grace and the port
  is down — the incident state — every `/health` poll stalls the loop, and
  `/health` is polled by the watchdog, by deploy's `wait_for_gateway_ready`,
  and by the UI. In `acquire_2fa_push_lock` (`:375`) the same probes run INSIDE
  the exclusive guard, burning the 5 s reader deadline. Masked by autouse
  fixtures at `scripts/tests/test_ib_2fa_lock_orphan_confirmation.py:40` and
  `test_ib_2fa_lock.py:27` (`ORPHAN_CONFIRM_INTERVAL_SECS = 0.0`).
  **AC:** red — with a monotonic fake and the REAL interval, assert
  `check_2fa_push_lock` on a down port costs ≥3 probes; then assert
  `restart_backoff_state` is reached via `asyncio.to_thread` from
  `check_ib_gateway` (or that a concurrent `asyncio.sleep(0)` task is not
  starved).

- **T-202 [P1] `migrate.py --demo` works only because of one line in
  `__main__`, and no test calls `main` with an argv.**
  `scripts/db/migrate.py:223` has `main(argv=None)` deliberately parsing `[]`,
  so `--demo` reaches argparse only via `sys.argv[1:]` at `:242`. Revert that
  single line and `radon-demo-mirror.service`'s `ExecStartPre` migrates
  **prod**, exits 0, demo stays at `schema_migrations` max=26 — the 2026-08-26
  P1 recurs with a green preflight. The unit wiring IS guarded
  (`cloud/tests/test_systemd_services.py:1085` reds if the ExecStartPre is
  removed; `test_unit_install_acknowledgment.py:78` reds on an unbumped
  sha256), but nothing guards that `--demo` is parsed.
  `scripts/tests/test_migrate.py:194` tests `resolve_target(demo=True)` in
  isolation, and the only `main()` test
  (`test_migration_partial_alter.py:108`) calls it bare on the prod path.
  **AC:** red — `migrate.main(["--demo"])` with `TURSO_DEMO_*` set and
  `_connect_with_retry` spied; assert the connect URL contains `radon-demo`.
  Plus a subprocess test that `python3.13 scripts/db/migrate.py --demo`
  resolves the demo target.

- **T-203 [P1] The restored `EQUIBLES_API_KEY` guard greps the RAW file, so
  commenting the key out passes — and there is no general code↔contract parity
  test at all.** `cloud/tests/test_scripts.py:210-217` is
  `assert "EQUIBLES_API_KEY" in required` against `read_text()`, comments
  included. Write `# EQUIBLES_API_KEY` and the substring survives while
  `cloud/scripts/check-env.py:106` (`required_keys`) skips `#` lines — the
  deploy preflight stops requiring it and all five `radon-equibles-*` oneshots
  die on `EquiblesAuthError` at every fire, which is T-163 verbatim, green.
  Nothing asserts that env vars a `cloud/services/*.service` ExecStart chain
  requires appear in `required-env.txt`, so the next key repeats the incident
  with zero signal. `scripts/tests/test_docs_contract.py:305` checks
  docs↔contract only.
  **AC:** red — switch the assertion to a parsed, comment-stripped key set
  (`cloud/tests/test_env_example.py:193` already does this properly for the
  allowlist interlock); commenting the line must red. Green — add a parity test
  shaped like `scripts/tests/test_watchdog_catalog_parity.py`: walk each
  service's ExecStart chain, collect `os.environ[...]` / `.get(...)` names read
  without a default, assert each is in `required-env.txt` or a reasoned
  `EXEMPT` map.

- **T-204 [P1] A new parametrized cutover guard silently skips 5 of its 6
  cases, so only `radon-api` is actually asserted — and the skip is unlinked.**
  `cloud/tests/test_app_plane_cutover_safety.py:181,184`.
  `test_every_example_that_resets_execstart_also_resets_execstartpre` is
  parametrized over 6 `runtime-container.conf.example` files. Measured
  (`-q -rs`): `runtime-container.conf.example declares no ExecStart override`
  ×1 and `radon-{monitor,newsfeed,nextjs,relay}.service has no ExecStartPre to
  reset` ×4 — **1 of 6 params reaches the assertion**. Until someone adds an
  `ExecStartPre=`, a drop-in that resets `ExecStart` and orphans
  `ExecStartPre` (running as root against production Turso, per the test's own
  failure message) is caught for one unit only. No `T-###`, no issue link.
  A sibling `pytest.skip` at `:131` skips 1 of 6 on the same file.
  **AC:** red — assert the parametrization yields ≥2 executed (non-skipped)
  cases. Green — convert the runtime `pytest.skip` to
  `pytest.param(..., marks=pytest.mark.skip(reason="T-###: …"))` so the skip is
  declared at collection and countable, and pin the skipped units in a
  documented baseline with a `test_the_baseline_has_no_stale_entries`
  companion — the shape already used at
  `scripts/tests/test_service_registration_completeness.py:559`.

- **T-205 [P1] A SECOND caddy-binary-gated mechanism class was added, and still
  no CI job installs caddy — a direct T-164 recurrence.**
  `cloud/tests/test_caddy_edge_timeouts.py:228-231` puts a class-level
  `@pytest.mark.skipif(shutil.which(CADDY_BIN) is None)` over `TestEdgeMechanism`,
  whose two tests (`:233` hung-upstream-becomes-a-bounded-5xx, `:265`
  a-severed-POST-is-not-replayed) are the ONLY executable proof of the R-219
  and R-220 fixes. `grep -rn caddy .github/workflows/` returns **nothing** —
  verified, zero hits. R-220 is an order-duplication guard on
  `POST /api/orders/place`, so on the gate that ships production both
  order-duplication and front-end-hang protections are covered only by regex
  assertions over the Caddyfile text (`:79`, `:98`, `:118`). A Caddy version
  bump that changes `retry_match` semantics ships green.
  **AC:** red — a test asserting some CI job installs caddy or provides
  `RADON_CADDY_BIN` fails today. Green — one `cloud-tests` shard adds a caddy
  install step and the skipif becomes an explicit `RADON_SKIP_CADDY_E2E=1`
  local-dev escape; a CI run must show `TestEdgeMechanism` passed, not skipped.
  **Needs an operator eye** (CI workflow change).

- **T-206 [P1] Eight of the ten `it`s in the panel-fault suite never mount a
  component; they regex the source text.**
  `web/tests/panel-fault-visibility.test.ts:53-88`. `:80` requires
  `/GammaRotationBody data=\{data\}[^>]*refreshFailed=\{error\}/` — matching
  `GammaRotationPanel.tsx:442` only in its current PROP ORDER, so a
  `GammaRotationBody` that receives `refreshFailed` and renders nothing for it
  is green while a harmless prop reorder is red. `:61`
  (`expect(src).toMatch(/coneMissing/)`) is satisfied by a variable assigned
  and never rendered; `:68`/`:73` pin a destructuring and an `if (error…)`
  head, not that any fault text reaches the DOM. Net: the fault-rendering
  behaviour R-245/246/247 exist for is untested.
  **AC:** red — delete every USE of `refreshFailed` inside `GammaRotationBody`
  while keeping the parameter; the file stays green. Green — render
  `VolConePanel` / `GammaRotationPanel` / `OptionsExposurePanel` with a stubbed
  sync hook returning `{data, error: "fetch failed"}` and assert the fault
  string is in `container.textContent` and the freshness badge is not the fresh
  tone. `coneFillPct` (`:90-105`) is genuinely behavioural — keep.

- **T-207 [P1] The R-263 visibility gate and failure backoff are verified by
  three `toContain` greps; inverting the guard keeps them all green.**
  `web/tests/p2-operability-remainder.test.ts:114-126` asserts only that
  `useOrders.ts` CONTAINS `document.visibilityState === "hidden"`,
  `MAX_POLL_INTERVAL_MS` and `failureStreakRef`. Inverting
  `web/lib/useOrders.ts:109` to `!== "hidden"` makes the hook skip the network
  while the tab is VISIBLE — the open-orders book on the live trading surface
  never refreshes — and all three greps still pass. A backoff whose streak
  resets every render also passes. Separately a real defect sits at
  `useOrders.ts:120`: `if (errorRef.current) failed = true;` runs in the hidden
  branch where no request was made, so `failureStreakRef` increments every 30 s
  while backgrounded and the first failure after the tab returns jumps straight
  to the 5-minute ceiling instead of walking the ladder.
  **AC:** red — with fake timers, mount `useOrders(true)`, set
  `visibilityState` to `"hidden"`, advance 5 minutes, assert zero `fetch`
  calls; flip to `"visible"`, advance one interval, assert exactly one. Then
  fail three polls and assert gaps of 60 s / 120 s / 240 s, not 300 s.
  Green — inverting `:109` must red the behavioural test while `:117` stays
  green, proving the grep has no signal.

- **T-208 [P1] `test_braked_units_reach_the_start_limit_before_two_cycles`
  asserts the dead `flap` alert path as CORRECT, so fixing it turns the test
  red.** `scripts/tests/test_watchdog_catalog_parity.py:198-216` asserts
  `to_park < cycle_seconds` — i.e. that a braked unit always parks `failed`
  before the watchdog can ever observe two consecutive `auto-restart` samples,
  permanently blessing a broken P1 alarm. A genuine fix (a shorter watchdog
  cycle, or keying on `NRestarts` instead of `SubState`) reds it. The
  companion `test_the_docstring_states_the_limitation` (`:187-196`) asserts a
  COMMENT contains the words `Unreachable` and `start-limit-hit`, so the
  acceptance criterion for a broken alarm is prose — and deleting `_flap_alert`
  entirely keeps it green. `:207`'s `if not (restart_sec and burst): continue`
  also silently skips any unit missing those directives, so dropping
  `StartLimitBurst` from `radon-api.service` passes with zero units checked.
  **AC:** red — the `StartLimitBurst` deletion above must fail, not pass.
  Green — either delete `_flap_alert` and assert `"flap" not in BUCKETS`, or
  drive `units.py` with two synthetic `auto-restart` samples and assert an
  alert is emitted. `TestCatalogParity` (`:143-168`) is genuinely good — keep.

- **T-209 [P1] The test guarding THIS loop's own dead-man forensics is
  satisfied by the exact inverse behaviour.**
  `scripts/tests/test_weekend_loop_deadman.py:157-166` asserts only
  `"launchd-cycle" in rotation`. The real rotation block
  (`scripts/testing_weekend.sh:122-126`) EXCLUDES the sinks via
  `grep -v -e '^launchd-cycle\.log$' -e '^launchd-cycle\.err$'`; flipping
  `grep -v` to `grep` would rotate ONLY the sinks — deleting exactly the
  forensics the test exists to protect — and the assertion still passes.
  Same file, same class: `:98-103`
  `assert len(re.findall("CYCLE_DEADLINE - CAP_SECS", body)) >= 2` counts
  substring occurrences, so a duplicated dead line satisfies it; `:131`
  `assert "report" in lock_branch` matches any identifier containing "report".
  **AC:** red — flip to `grep`; the test must fail. Green — run the rotation
  block in a `tmp_path` `LOG_DIR` seeded with 40 files including
  `launchd-cycle.log`/`.err`, assert both sinks still exist afterwards and that
  ≥1 old file was removed.

- **T-210 [P1] BLAST RADIUS — a source change made two UNTOUCHED e2e specs
  false-red every weekend, on this runner.** The delta swapped
  `isIbDailyPnlCurrent()` for `isIbDailyPnlFromCurrentSession(lastSync)` in
  `web/components/PositionTable.tsx:611-615`, and
  `PortfolioSections.tsx:129/160/191` now threads `portfolio` into every
  `PositionTable`, so the Day-P&L path is gated on BOTH the wall clock and
  `etDate(last_sync) === etDate(now)`. `isIbDailyPnlFromCurrentSession`
  (`web/lib/ibDailyPnlSession.ts:32-40`) short-circuits `false` whenever
  `isIbDailyPnlCurrent` is false, and that is `isUsTradingDay(etDate(now))`
  (`:18-20`). `web/e2e/day-move-ib-daily-pnl.spec.ts:6,64` fixture
  `last_sync: new Date().toISOString()` with a non-null `ib_daily_pnl`, then
  assert the literal `-$3,688` at `:269,271,280`;
  `account-day-move-ib-daily-pnl.spec.ts:216,223,239` is the same class in the
  same file (`:239` is the prior audit's still-unfixed case). **On any
  Saturday, Sunday or US market holiday the field is nulled and both specs
  fail.** Nothing in the changed-test list would have surfaced this — it is the
  inverse question the 2026-08-22 lesson mandates.
  **AC:** red — run either spec with the host clock on a Saturday. Green — use
  the `freezeToTradingDay(page)` init-script helper that
  `e2e/portfolio-same-day-equity-pnl.spec.ts:100` and the new
  `e2e/mobile-same-day-pnl-parity.spec.ts:85` already use, pinned to a known
  trading day, and derive `last_sync` from that same constant.

- **T-211 [P1] Mobile `handleSubmit` has no `transmitArmed` guard, so the
  unbounded-risk acknowledgement is enforced by a `disabled` attribute alone.**
  `web/components/mobile/MobileOrderTicket.tsx:453-457` guards on
  `confirmStep`, `isValid` and `okToSubmit` but not `transmitArmed`. The
  desktop equivalent has the defence-in-depth check —
  `OptionsChainTab.tsx:500-502`, with the comment
  `// Defence in depth: the disabled button is UI, this is the actual gate.`
  On mobile the ack is enforced only by `disabled` on the button (`:607`).
  `web/tests/mobile-ticket-transmit-gate.test.tsx:103-120` asserts only
  `submit.disabled` toggling — it never fires the handler while unarmed and
  never asserts that no POST reached `/api/orders/place`. The correct shape
  already exists at `position-trade-ticket-risk-gate.test.tsx:137`.
  **AC:** red — stub `fetch`, advance to confirm WITHOUT ticking the ack,
  invoke the submit handler directly; assert `fetch` was never called with
  `/api/orders/place`. Green — add `if (!transmitArmed) return;`; deleting it
  again must red.

- **T-212 [P1] `TicketRiskBlock` prints AGGREGATE position dollars under a
  heading that says PER 1× COMBO, directly above the transmit button.**
  `web/components/ticker-detail/TicketRiskBlock.tsx:98-108` heads the panel
  `RISK · PER 1× COMBO` (`:99`) while `maxGain`/`maxLoss` come from
  `riskState.summary`, which `computeOrderRisk` returns multiplied by
  `comboQuantity * MULTIPLIER` (`computeOrderRisk.ts:398-410`) — and
  `OptionsChainTab.tsx:416-422` deliberately feeds RAW (non-ratio) leg
  quantities so `comboQuantity` is the full contract count. `BREAKEVENS` on the
  same grid is derived from a strictly per-1×-combo curve, so the two halves of
  one panel are on different scales. `web/tests/ticket-risk-block.test.tsx:36-56`
  passes `maxGain={298}` for a 1× strangle, where the two coincide; no test
  renders the rail at quantity > 1.
  **AC:** red — render the chain rail with 10 contracts staged and assert MAX
  LOSS against the heading's stated scale. Green — pin whichever is intended
  (divide the cell down, or change the heading), then mutating
  `OptionsChainTab.tsx:416` to feed `normalizedOrder.legs` must red it. This is
  the 2026-05-27 VIX regression the docblock at `:405-415` describes, and
  nothing currently guards the rail's rendering of it.

- **T-213 [P1] `PortfolioSnapshotCorruptError` is thrown but mapped by nobody,
  so a corrupt snapshot pages as a Turso outage.**
  `web/lib/portfolio/readPortfolioSnapshot.server.ts:56-71` throws at `:67`
  with the stated purpose "so the route can report corruption rather than
  DB_UNAVAILABLE" (`:40-41`), but a repo-wide grep finds `SNAPSHOT_CORRUPT`
  only in the file that defines it. `GET /api/portfolio`
  (`web/app/api/portfolio/route.ts:182-185`) funnels every throw into
  `unavailablePortfolioResponse(...)` → `503` / `code: "DB_UNAVAILABLE"`
  (`:158`). The operator restarts the database instead of re-running the
  portfolio sync, and staleWhileError retries the same unparseable row forever.
  The only assertion is `expect(src).toContain("PortfolioSnapshotCorruptError")`
  at `web/tests/p2-operability-remainder.test.ts:107-113`.
  **AC:** red — mock `dbExecute` to return `rows: [{taken_at, payload: "{trunc"}]`
  and assert `GET /api/portfolio` returns `code: "SNAPSHOT_CORRUPT"`, not
  `DB_UNAVAILABLE`. Green — map the error at the route. The mutation the
  current suite cannot see: delete the `try/catch` at `:61-70` so the raw
  `SyntaxError` escapes, then restore only the throw without the route mapping
  — the grep goes green while the route test stays red.

- **T-214 [P1] Nulled percentiles are now persisted to the CTA cache, and two
  readers were never updated — one crashes, one prints "Noneth".**
  `reconcile_tables` can emit `percentile_3m: None`
  (`scripts/utils/cta_percentiles.py:119`); `scripts/fetch_menthorq_cta.py:239`
  applies it and `:253` writes it to `data/menthorq_cache` (`:149-160`).
  `generate_cta_share.py` was patched for `None` (`:398-400`, `:452`, `:132`);
  the other two readers were not. `scripts/cri_scan.py:1307-1310` does
  `pctl_3m = spx.get("percentile_3m", 50)` — the key EXISTS with a null, so the
  default never fires and `None < 25` raises `TypeError` inside
  `generate_html_report`, called unguarded at `:1687`: the CRI report run dies
  and no HTML is written. `scripts/generate_regime_share.py:511` raises the
  same way on `spx_pctile < 1`, and `:369`'s `pctile_label(spx_pctile)`
  (`:86-90`, no `None` branch) prints "SPX CTAs at the **Noneth** percentile"
  into card copy. The only test calling `fetch_menthorq_cta()`
  (`scripts/tests/test_menthorq_cta.py:265-286`) returns before line 239, and
  the cache-shape test (`:317-333`) hand-builds integer percentiles.
  **AC:** red — write a reconciled cache with `percentile_3m: None` and call
  each reader; both must raise today. Green — both handle `None` explicitly
  (skip the band rather than defaulting to 50).

- **T-215 [P1] A nulled prior session is republished as the 50th percentile, so
  the change-since-prior copy asserts a move that did not happen.**
  `scripts/utils/cta_history.py:87,109` apply `reconciled_payload` (`:37-43`)
  to every archived payload on both load paths. A prior day whose percentiles
  the z-guard nulls reaches `_spx_delta` (`:186-195`), whose `pctile` /
  `prior_pctile` come from `assess_positioning` →
  `normalize_pctile(r.get("percentile_3m", 50))`
  (`scripts/generate_cta_share.py:153`) — and `normalize_pctile(None)` returns
  **50** (`scripts/utils/cta_percentiles.py:41-42`). The same substitution
  drives `is_extreme` (`generate_cta_share.py:158`) and therefore
  `regime_label` / `_regime_run` (`cta_history.py:163-174`, `:198-211`), so a
  genuinely max-short archived session scores "in-range" and the regime-run
  counter reports a false regime change. `test_cta_share_history.py:188-222`
  asserts only dates, ordering and the lookback bound; no test in the tree
  references `reconciled_payload`.
  **AC:** red — archive a payload whose percentiles null out, load it, assert
  `prior_pctile` is not a fabricated 50. Green — propagate `None` through
  `_spx_delta` and suppress the change copy when either side is unknown.

- **T-216 [P1] The shard-partition guard globs FILES only, so a new
  subdirectory under `scripts/tests/` silently drops — the T-122 recurrence
  vector, unguarded.** The eight `scripts/tests/test_[x-y]*.py` shard globs
  (`.github/workflows/ci.yml:195-209`) cannot match a directory, which was the
  exact T-122 defect; the two existing subdirs are covered only by the
  hardcoded literal shard `scripts-daemons:
  "scripts/tests/test_monitor_daemon scripts/tests/test_watchdog"` (`:209`),
  carrying 56 files. The guard meant to prevent regression,
  `test_pytest_filename_shards_partition_scripts_tests`
  (`scripts/tests/test_ci_deploy_concurrency.py:308-320`), builds its universe
  from `(… / "scripts" / "tests").glob("test_*.py")` — non-recursive, files
  only. A third subdirectory is invisible to both the shards AND the guard:
  0 tests run, CI stays green. (The letter half IS covered — a `test_q*.py`
  file would be caught by the existing `missing == []` assert.)
  **AC:** red — enumerate `p.is_dir() and p.name.startswith("test_")` under
  `scripts/tests` and assert every one appears verbatim as a shard token; add a
  fixture dir in the test's tmp-checkout and assert it is not unassigned.
  Green — the guard reds for any unlisted directory. Same shape at
  `cloud/tests` (`:395-400`), lower risk as it has no subdirs.

- **T-217 [P1] `resolveSpreadPriceData` stamps the wall clock, the same defect
  class T-158 closed in `comboQuotePriceData`.** Promoted from NEW_FINDINGS
  (filed by `ae8fa127` landing T-158, deliberately not chased mid-loop).
  `web/lib/positionUtils.ts:489` returns
  `timestamp: new Date().toISOString()` for the ticker-detail spread header,
  so a stale quote renders as live. `asOf` is available at that call site after
  the T-158 threading and `oldestQuoteTimestamp()`
  (`web/lib/pricesProtocol.ts`) is the helper, so the fix is a one-liner. Blast
  radius is the spread header's BID/MID/ASK freshness labelling, not an order
  price — a rung below T-158, same shape.
  **AC:** red — build a spread whose leg quotes are 20 minutes old and assert
  the returned `timestamp` is the oldest leg quote, not now. Green — thread
  `asOf` through as T-158 did.

### P2 — fragility / structure

- **T-218 [P2] The blotter honesty suite pins one exact string spelling.**
  `web/tests/portfolio-blotter-honesty.test.tsx:147-156` is a single
  `not.toContain('(t.realized_pnl ?? 0) >= 0 ? "positive" : "negative"')`.
  Any equivalent expression that still paints a null P&L green — e.g.
  `t.realized_pnl == null || t.realized_pnl >= 0 ? …` — is green, which is
  R-248 unfixed. `:105-118` greps for `"sign * legEc"`; a `sign` computed with
  inverted polarity for a short leg keeps the substring and the bug. The file
  already renders `PortfolioSections` at `:74-95`, so DOM assertions were
  available. **AC:** render a row with `realized_pnl: null` and assert the cell
  carries neither `positive` nor `negative`.

- **T-219 [P2] A magic-count assertion on guard call sites.**
  `web/tests/risk-free-rate-unavailable.test.tsx:139-140` counts
  `riskFreeRate == null` occurrences and requires `>= 5`. A sixth call site
  added WITHOUT a guard passes, and a guard returning `0` instead of `"—"`
  passes. Nothing renders `PositionTable`, though
  `position-table-short-leg-sign.test.tsx` (same delta) already has the
  `seedRiskFreeRateForTests` harness. **AC:** render `PositionTable` with the
  FRED fetch stubbed to the fallback payload, assert every Implied cell reads
  `"—"`, then seed `0.0433` and assert it changes. The hook tests (`:56-121`)
  are strong — keep.

- **T-220 [P2] The surfaces fuzz silently passes with zero assertions if a
  column header is renamed.**
  `web/tests/fuzz/same-day-pnl-surfaces.fuzz.test.tsx:209,232` — a returned
  fast-check property is a PASSING case, so `if (todayIdx < 0 || pnlIdx < 0)
  return;` and `if (mvIdx < 0) return;` turn all 40 desktop draws into no-ops
  when a header changes or a column is hidden by default via
  `columnVisibility` — exactly the S2/S3 regression class the file exists to
  catch. `money()` (`:172-175`) returns `null` when no `$` figure is present,
  so a row rendering `—` in both Today and P&L satisfies
  `expect(today).toBe(headline)`. **AC:** hoist the header lookup out of the
  property and assert the index once, outside; make `money()` throw on no
  match. Renaming `TODAY P&L` to `DAY P&L` must red S2.

- **T-221 [P2] The IV-rank freshness rail e2e accepts every value the component
  can emit.** `web/e2e/ivrank-tab.spec.ts:180-190` asserts
  `toHaveAttribute("data-state", /^(current|behind|overdue)$/)` and
  `fillWidth >= 0 && <= 100`. A rail counting down to the WRONG slot — the page
  reading a different schedule constant than `IV_RANK_REFRESH` — passes.
  **AC:** mock a fixed clock and assert the exact countdown string and
  `data-state` that `freshness-rail.test.ts` predicts for that instant.
  Hardcoding `data-state="current"` must red it.

- **T-222 [P2] `.github/required-status-checks.json` is structurally inert and
  its arm of the ratchet test is vacuous.** The file declares
  `"contexts": []` (`:16`), and
  `gh api repos/joemccann/radon/branches/main/protection` returns
  `enforce_admins:true, allow_force_pushes:false, allow_deletions:false` and
  **no `required_status_checks` key at all**. It is consumed only by
  `scripts/tests/test_ci_deploy_concurrency.py:24,351`, where
  `required_check = {job, jobs[job]["name"]} & declared` is permanently empty,
  so the `on_needs or required_check` assert is carried 100% by
  `deploy.needs`. Consequence for the ledger: **a PR can be merged red and a
  commit pushed straight to `main` with zero passing checks**; production is
  protected only because `deploy.if` self-gates (`ci.yml:636-646`). Honest by
  construction, not drift. **AC:** apply protection over the API with the four
  ratchet/scan contexts, then assert
  `_declared_required_status_checks() != set()`. **Needs an operator eye.**

- **T-223 [P2] The Playwright job runs 14 of 150 specs, gates nothing, and the
  delta's one new spec was not curated.** `web/e2e/*.spec.ts` on disk = **150**;
  the curated list (`ci.yml:525-538`) names **14**, all of which exist.
  `e2e-financial-smoke` (`ci.yml:465`, named "non-gating") appears in neither
  `stage-release.needs` (`:553`) nor `deploy.needs` (`:635`), so all 150 specs
  are advisory — the job can be red and the VPS still deploys. The delta added
  `web/e2e/mobile-same-day-pnl-parity.spec.ts` and it is not in the 14, so
  `web/CLAUDE.md`'s mandatory "E2E browser verification for all UI work" has no
  CI arm. Documented as deliberate at `ci.yml:519-520`, so this is a standing
  gap, not new drift. **AC:** add the new spec to the curated list; promote the
  job into `deploy.needs` only after green runs are observed, per the ci.yml
  rule. **Needs an operator eye.**

- **T-224 [P2] The new caddy edge tests carry four wall-clock timing
  assertions, a leaked listener and a TOCTOU port race.**
  `cloud/tests/test_caddy_edge_timeouts.py:182,185,259,283` and
  `cloud/tests/test_caddyfile.py:333-336,399,417`:
  `assert waited < header_timeout + 20`, `urlopen(request, timeout=40)`,
  `time.sleep(1.5)` then `assert waited >= 1.0`. Handlers block 120 s and
  `server.shutdown()` is called without `server_close()`, so the listener leaks
  for the session. `free_port()` binds, closes, then returns the number, so two
  concurrent edge tests can collide. **AC:** hold the probe socket open and
  pass the bound socket; add `server_close()` in the `finally`; assert the
  ORDERING fact (`status == 200` after the gap) rather than an elapsed float.

- **T-225 [P2] BLAST RADIUS — a copy-mirror test went green while no longer
  mirroring anything.** `web/tests/account-metric-modal.test.ts:51` declares
  itself a mirror of `MetricCards.tsx`'s modal content and hardcodes
  `"Source: Interactive Brokers reqPnL() — account-level, updated in real-time"`.
  The delta's em-dash sweep changed the component to `"reqPnL(), account-level"`.
  Because the test asserts against its own LOCAL copy and never renders
  `MetricCards`, the drift is invisible; same for the "Day Move" and
  "Unrealized P&L" formulas. **AC:** import the formula strings from a shared
  module consumed by both, or render the modal and read the text off the DOM;
  changing a formula in the component must then red.

- **T-226 [P2] New module-level mutable globals in `ib_2fa_lock` leak across
  seven test files that never reset them.** The delta added `_orphan_reported`
  / `_orphan_seen_up` (`scripts/utils/ib_2fa_lock.py:97,108`) and a real
  `time.sleep(0.4)` in `_is_orphaned` (`:311`). Two files zero the interval and
  call `reset_orphan_state()` in an autouse fixture; seven other importers do
  not — `test_ib_watchdog_2fa_lock.py`,
  `test_ib_watchdog_2fa_storm_2026_07_05.py`,
  `test_ib_watchdog_loop_2026_06_15.py`,
  `test_ib_gateway_no_unmanaged_restart.py`,
  `scripts/api/tests/test_ib_restart_2fa_lock.py`, `test_ib_restart_backoff.py`,
  `test_ib_gateway_pool_recovery.py`. Within one xdist worker the globals
  persist across files, so a revocation outcome depends on file order and each
  unguarded revocation costs a real 0.4 s. **AC:** move
  `reset_orphan_state()` + the zeroed interval into a shared
  `scripts/tests/conftest.py` autouse fixture; the storm file must produce
  identical results alone and after the orphan-confirmation file.

- **T-227 [P2] A test that reds if the run straddles 16:00 ET.**
  `scripts/tests/test_bpi_truncated_sweep.py:44,93` anchors its fixture on
  `bpi.last_completed_session_date()` at BUILD time, while
  `build_index_payload` calls the same function again at ASSERT time to compute
  `stale` (`scripts/bpi_scan.py:211`).
  `market_calendar.last_completed_session_date` flips at exactly 16:00 ET on a
  trading day (`market_calendar.py:281-289`), so a straddling run builds
  against yesterday's session and evaluates `stale` against today's →
  `assert payload["stale"] is False` fails. The 1000×60-date fixture makes the
  window real. **AC:** resolve the anchor once and inject it, or monkeypatch to
  a fixed trading day.

- **T-228 [P2] Three same-day P&L files red if the run crosses midnight ET.**
  `web/tests/same-day-pnl-surface-parity.test.tsx:56,74`,
  `web/tests/fuzz/same-day-pnl.fuzz.test.ts:58` and
  `web/tests/fuzz/same-day-pnl-surfaces.fuzz.test.tsx:57` evaluate
  `const TODAY = todayET()` at MODULE LOAD, while
  `positionUtils.isSameDay` (`web/lib/positionUtils.ts:525-528`) calls
  `todayInET()` at ASSERTION time. Crossing 00:00 ET flips `isSameDay` to false
  mid-suite, the same-day branch stops firing, and the identity no longer
  holds. The 1000-run fuzz widens the window. Verified TZ-safe otherwise (a
  `Pacific/Honolulu` + `Pacific/Kiritimati` sweep over 11 date-touching changed
  files, 171 tests, all green), so this is purely the midnight seam — but it is
  a silent one. **AC:** `vi.setSystemTime(...)` in `beforeEach` and derive
  `TODAY` from that frozen instant.

- **T-229 [P2] The newsfeed scheduler now exits 75 on a mid-cycle SIGTERM, but
  its unit has no `SuccessExitStatus=`.** `scripts/newsfeed/scheduler.js:76-86`
  returns `exit(truncated ? 75 : 0)`. `cloud/services/radon-newsfeed.service`
  has `Restart=on-failure`, `RestartSec=30`, `StartLimitIntervalSec=300`,
  `StartLimitBurst=5` and no `SuccessExitStatus=`. The scraper loops every
  120 s, so most deploys SIGTERM mid-cycle — leaving the unit `failed` after an
  ordinary `systemctl stop` (which `scripts/watchdog/units.py` pages on), and
  5 × 30 s < 300 s parks it at `start-limit-hit`: a silent newsfeed outage. The
  repo's own convention is explicit elsewhere —
  `cloud/services/radon-db-retention.service:18` and
  `radon-db-backup.service:24` both pair exit 75 with `SuccessExitStatus=75`.
  There is no test file for `scheduler.js` anywhere in the tree. **AC:** unit —
  `createShutdown({isCycleInFlight: () => true})` exits 75, `false` exits 0.
  Contract — assert `radon-newsfeed.service` declares `SuccessExitStatus=75`.

- **T-230 [P2] `_SUBJECT_SCAN_GATES` grows without bound on caller-supplied
  input.** `scripts/api/server.py:3069-3077` keys a dict on `ticker` with no cap
  and no eviction, in a long-lived process; `/gex/scan` accepts any ≤10-char
  alnum symbol (`:3964`). Each novel value mints a gate whose cooldown and
  backoff are COLD — so it also bypasses the cooldown and spawns another 120 s
  `run_script` — while the dict grows for the process lifetime.
  `scripts/tests/test_scan_admission_and_shed_honesty.py:92-113` covers
  identity, isolation and cooldown independence for two fixed tickers only.
  **AC:** call `_scan_gate_for("gex", t)` for 500 distinct tickers, assert the
  map stays under a stated ceiling and that eviction never drops a gate
  currently in cooldown or backoff.

- **T-231 [P2] Two thin spots in the R-225 BPI unwind fix.** (a) The call site
  of `install_sigterm_unwind()` is unpinned: `test_bpi_truncated_sweep.py:141`
  calls it DIRECTLY, so deleting `scripts/bpi_scan.py:656` keeps the suite
  green while a systemd `Result=timeout` again kills the process without
  unwinding `service_cycle`'s `finally` — `bpi_history` partially written, no
  error row, `NRestarts=0`, silent. (b) `PERSIST_RESERVE_S = 600`
  (`bpi_scan.py:83`) is never read by production code; grep shows it only at
  `test_bpi_truncated_sweep.py:121-125`, so the "reserve" is a comment the test
  does arithmetic on. **AC:** spy `signal.signal` and assert `run_scan` installs
  the handler as part of its OWN execution; gate the persist loop on
  `time.monotonic() < persist_deadline` with a fake clock.

- **T-232 [P2] A cluster of grep-not-behaviour tests introduced in this delta.**
  Each stays green through a refactor that breaks the behaviour it names:
  `scripts/tests/test_flow_refresh_shed_honesty.py:192`
  (`"service_health" in source` — delete every CALL, the definition still
  matches; this is what hid T-197); `:146` (regex `"timeout" in body` —
  `timeout 0s …` means NO limit in GNU coreutils, restoring the R-222 unbounded
  fallback); `cloud/tests/test_app_plane_cutover_safety.py:155,162` (hid
  T-198); `scripts/tests/test_migration_partial_alter.py:59`
  (`inspect.getsource(apply_pending_migrations)` — the R-153 commit-ordering
  invariant is a text scan that already had to be re-pointed once in
  `9565d37d` when the loop moved);
  `scripts/tests/test_run_flow_refresh_wrapper.py:194-209`
  (`'run_one "scanner" …' in source`). **AC (pattern):** replace each with an
  execution assertion against the harness that already exists — `_run(repo,
  python_bin, port)` for the wrapper, `RADON_APP_RUNTIME_TEST_MODE=1` + stub
  docker for the runtime, a `check.py` invocation over a synthetic
  `service_health` table for the watchdog buckets.

- **T-233 [P2] `vitest.config.ts` pins `NODE_ENV` and `retry` but not `TZ` or
  `LC_ALL`.** The new `web/tests/vitest-config-contract.test.ts` guards
  `NODE_ENV` (`vitest.config.ts:43`) and `retry: 0` (`:36`) — good — but the
  suite's TZ-independence is currently a PROPERTY that has to be re-audited
  each delta rather than a guarantee. It held this run (verified by the
  two-timezone sweep noted in T-228). `web/tests/same-day-pnl-surface-parity.test.tsx:126,134`
  escapes a locale bug only by accident: bare `toLocaleString()` against a
  component formatting with `toLocaleString("en-US")`, saved because the value
  is `103` (no separator) and because `.` matches `,` inside a `RegExp`.
  Node's default locale IS env-driven — verified: `LANG=de-DE` → `12.120`.
  **AC:** add `env: { NODE_ENV: "test", TZ: "America/New_York" }` to the config
  and extend the contract test to pin both.

- **T-234 [P2] Six raw CSS-class locators for the CTA share modal.**
  `web/e2e/regime-cta-share-pattern.spec.ts:134-138,150,160-161` selects
  `.cta-share-backdrop`, `.cta-share-modal`, `.cta-share-title`,
  `.cta-share-iframe`, `.cta-share-close`. Class names are presentation, not
  contract, and the delta's own brand work (`radius-contract.test.ts`,
  `surface-contract.test.ts`) is actively rewriting `globals.css`. Related, in
  `web/e2e/ivrank-tab.spec.ts:152,186`: `.regime-rail__item[data-tab="ivrank"]`
  asserted via `toHaveClass(/active/)`, and `.freshness-rail-track-fill` read
  through inline `style.width` — moving the fill to a CSS variable or
  `transform: scaleX()` yields `style.width === ""`, and
  `Number.parseFloat("")` is `NaN`, so a pure styling change fails the spec.
  Extends the standing e2e testid backlog. **AC:** `data-testid` on the five
  share-modal nodes; expose the rail fill as `data-fill-pct` and assert the
  attribute.

- **T-235 [P2] The one number the mobile parity spec exists to compare is
  reached by a substring text match plus a parent hop.**
  `web/e2e/mobile-same-day-pnl-parity.spec.ts:173` uses
  `card.locator("text=Today").locator("..")` — unquoted, so substring and
  case-insensitive — then a single-level DOM-parent hop. Any wrapper `<div>`
  added to the card, or a label change to "Today's P&L" / "Day", silently
  resolves to a different element, while the sibling headline already has
  `data-testid="mobile-position-pnl"`. Extends the standing e2e testid backlog.
  **AC:** add `data-testid="mobile-position-today"` in `MobilePositionList` and
  select it directly; wrapping the metric row in an extra div must not change
  the result.

- **T-236 [P2] The unit-manifest guard globs the top level only, so the
  fleet-wide drop-in has no sha256 pin.**
  `cloud/tests/test_unit_install_acknowledgment.py` is genuinely strong — `:78`
  recomputes real sha256 per unit and reds on any unbumped
  `cloud/services/*.{service,timer}`, and the manifest has zero orphans — but
  `_unit_files()` globs the top level, so
  `cloud/services/radon-.service.d/common.conf` (the drop-in that sets
  `RADON_DB_NO_REPLICA=1` on every `radon-*` unit, the DUR-07 belt-and-braces
  named in CLAUDE.md) is unpinned. Untouched in this delta, so not drift.
  **AC:** extend `_unit_files()` to walk `*.service.d/*.conf` and require a
  manifest entry; editing `common.conf` without a sha bump must red.

- **T-238 [P2] A new contract test sits at ~50% of the default 5 s timeout, so
  it flakes under any concurrent load.**
  `web/tests/portfolio-startup-performance-contract.test.ts:172`
  (`renders nothing for the portfolio section so the workspace never owns it`)
  failed the full vitest gate at **5041ms** — the 5000ms default, blown by
  41 ms — under load average 36. Re-run in isolation it is **8 passed ×3** at
  2.91 / 3.30 / 3.65 s wall (tests 2.37–2.79 s), so the assertion is correct
  and this is the load-sensitive class, NOT a regression. It is filed anyway
  because the margin is the defect: the file is the T-167/T-170 behavioural
  rewrite from last weekend, and its import-graph walk plus four component
  renders consume roughly half the budget with nothing left for a busy runner
  — precisely the 2026-08-23 lesson about timing a new tree-walking contract
  rather than only making it green. This host runs two weekend loops
  concurrently, so contention is the normal condition, not the exception.
  **AC:** red — run the file 3× and assert the reported per-test duration for
  `:172` stays under half the configured timeout; it does not today under
  load. Green — either hoist the import-graph parse into a
  module-scope `beforeAll` shared across the file's cases (the parse is
  invariant), or raise this file's `testTimeout` via `vi.setConfig` with the
  measured headroom stated in a comment, the pattern T-161 already established
  for the two named jsdom suites. Do NOT re-enable a blanket `retry` — T-161
  removed it deliberately.

## Remediation 2026-08-27

Worked the 2026-08-27 backlog top-down. **Every P0 and P1 this cycle's audit
filed is DONE — T-190 … T-217 (5 P0, 23 P1)** — plus three P2s taken because
they blocked honest verification (T-226, T-232, T-238). 26 commits on
`testing/weekend-2026-08-27`, PR #112.

**T-237, the sixth P0, was deliberately NOT worked here.** Open PR #109
(`fix/portfolio-risk-gate3-api-drift`) already fixes it, source and test.
Reproduced locally first: the audit named one of THREE causes. Renaming
`BACKFILL_WALL_CLOCK_BUDGET_S` alone still leaves 7 red, because the ladder
stubs take one argument where `_fetch_closes_via_ladder` takes
`(symbol, deadline, clock)`, and `BACKFILL_SYMBOL_WORST_CASE_S` does not exist
either. Merging #109 is what un-reds `main`.

### Findings whose remediation exposed a live defect

- **T-205 → `cloud/caddy/Caddyfile` does not adapt, and never did.**
  `retry_match` and bare `dial_timeout` / `response_header_timeout` /
  `read_timeout` are the JSON and transport spellings, not `reverse_proxy`
  subdirectives. `configure_caddy` (`setup-vps.sh:542`) and
  `deploy-root-helper.sh:604` both gate on `caddy validate`, so the file could
  never be installed: **R-219, R-220 and R-258 were never in force at the
  edge** while all 14 text-regex assertions shipped green. R-220 is the
  POST-replay guard on `/api/orders/place`. Confirmed independently against
  caddy v2.11.4 — pre-fix errors `unrecognized subdirective dial_timeout, at
  Caddyfile:59`; post-fix logs `adapted config to JSON`.
- **T-209 → both weekend wrappers died silently in their prologue.**
  `report()`, `on_crash()`, `notify_phase()` and `resolve_pr_url()` are defined
  below the prologue in `main()`, so the REFUSED branches and the prologue ERR
  trap called a function that did not exist yet. Held lock, wrong clone, full
  disk and moved clone all produced no issue comment and no page — the exact
  dead-man failure this loop exists to prevent. The guarding test asserted
  `_comments == []`, encoding the dead report as the contract.
- **T-207 → the orders backoff could never leave rung one.**
  `failureStreakRef.current = 0` ran right after `await fetchOrders()`, which
  swallows its error into `errorRef` rather than throwing, so the streak was
  zeroed before the failure was counted. Measured 60s/60s/60s against an
  intended 120s/240s ladder; the 5-minute ceiling was unreachable.
- **T-190/T-211 → the mobile ticket.** The payoff was built from raw legs
  while the desktop rail ratio-normalises, so every breakeven and the
  unbounded-risk figure the operator reads before transmitting were wrong by
  the lot size (a 10-lot short straddle read `$9,694,040` against `$964,040`);
  and `handleSubmit` had no `transmitArmed` guard, so an unacknowledged naked
  short call reached `/api/orders/place`.

### Corrections to the audit's own text

- **T-190:** a bare 10-lot short put cannot produce the unbounded-loss
  sentence — `legRisk` bounds a short put and returns
  `maxLossUnbounded: false`. The repro needs an uncovered short call. The
  rendered mobile breakeven is `969.47`, not `970.30`, because `payoffCurve`
  samples 96 points and interpolates across the strike kink. The lot-size
  factor is confirmed exactly.
- **T-191:** the rounded row destroys the good percentile in BOTH tables, not
  only in `main` — slightly worse than recorded.
- **T-226:** the storm file's pass/fail was already order-independent, since
  its assertions never read the globals. The provable leak is the state itself
  plus the wasted real sleeps.
- **T-208:** there is no `flap` key in `BUCKETS`, so that half of the AC did
  not apply; `_flap_alert` was kept, not deleted, because `test_units.py`
  already reds on its removal.

### Deferred

- **Older non-P2 stragglers from T-081…T-189 were not re-triaged this run.**
  The 28 items from this cycle's own audit consumed the budget. They remain
  open backlog.
- **Follow-up filed in prose, not as a task:** `acquire_2fa_push_lock` still
  runs its confirmation probes INSIDE the exclusive guard
  (`ib_2fa_lock.py:375`), burning the 5s reader deadline. Named in T-201's
  text, outside its AC.

## Delta audit 2026-08-28 (surfaced by remediation)

This cycle's audit phase filed nothing (see the Remediation section below), so
this is not a delta audit of the codebase. It is the ONE finding that
remediation could not avoid making, because it is why the audit produced
nothing.

- **T-239 [P0] A phase the harness truncates exits 0, and both weekend
  wrappers page `OK` on it.**
  `claude -p` terminates unfinished background tasks at its print-mode
  background-wait ceiling (600 s by default), prints
  `Background tasks still running after 600s; terminating.` and then exits
  **0**. `scripts/testing_weekend.sh:284-297` and
  `scripts/reliability_weekend.sh:332-345` classify the phase on that exit
  code alone, so a run cut off with its last agent still working is
  indistinguishable from one that finished. Neither wrapper sets
  `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, so the ceiling is a second, shorter,
  silent cap sitting inside the `timeout "$remain"` the wrapper believes is
  the only one.
  **Observed, not theorised:** the 2026-08-28 audit phase was cut at 600 s. It
  left `origin/testing/2026-08-28` an empty branch at `c6d08fbd`, no
  `## Delta audit 2026-08-28` section, no ledger line and no PR, against a
  24-commit / 262-file / +23,193-line delta — and reported **OK** on all three
  dead-man channels. Issue #83's last comment carries the harness message
  verbatim. This is the T-209 failure mode (a dead-man signal that lies)
  re-entering through a different door, and it would have recurred nightly.
  **AC:** the agent child process must actually see
  `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (spawn it and read the value back —
  an assignment that never reaches the child satisfies any source grep); and a
  run log carrying the harness message must classify as something other than
  `OK`, with the TIMEOUT / FAILED / OK classifications pinned unchanged so the
  fix cannot become a blanket downgrade.

### Findings surfaced by the 2026-08-28 remediation (T-240 … T-246)

Each was found while working an unrelated backlog item, verified at the cited
line by the lead, and left UNFIXED because it is outside the AC that surfaced
it. Filed so the next audit does not have to rediscover them.

- **T-240 [P1] `letClerkLoad`'s route handler outlives the page, and the pattern
  is copy-pasted across roughly a dozen regime tab specs.** Its proxying
  `route.fetch` is still in flight at teardown, so a run fails on a route
  callback rather than on an assertion, and hits a DIFFERENT test each time.
  Fixed in `web/e2e/ivrank-tab.spec.ts` under T-234 with
  `page.unrouteAll({ behavior: "ignoreErrors" })` in `afterEach`; unfixed in
  `cor`, `hyad`, `bpi`, `hhlev`, `trin`, `divyield`, `vixts`, `vixcor`,
  `margin-debt`, `skew2d`, `straddle` and `credit-spread`. Likely a broad source
  of what reads as flake in this suite. **AC:** apply the same `afterEach` to
  every spec using `letClerkLoad`, and assert in a contract test that a spec
  importing it also unroutes.

- **T-241 [P2] `toHaveClass(/active/)` is a SUBSTRING match, so renaming
  `active` to `is-active` does not red it.** Roughly 15 regime tab specs rely on
  that assertion for "this tab is selected". Confirmed under T-234: the styling
  refactor that broke every class LOCATOR left the `toHaveClass` assertions
  passing. The assertion is weaker than it reads. **AC:** assert
  `data-state="active"` (or an exact class list) instead; renaming the class
  must red.

- **T-242 [P1] The BPI stale flag is computed against a DIFFERENT session
  resolution than the one the bars were filtered by.** `ensure_member_history`
  resolves `last_completed_session_date()` once at `scripts/bpi_scan.py:247`
  (`last_complete`, used to pick laggards to fetch) and `build_index_payload`
  resolves it AGAIN at `:213` to compute `"stale": latest["date"] <
  last_completed_session_date()`. Verified at both lines. A real run whose fetch
  phase straddles 16:00 ET filters member bars against yesterday's session and
  then labels the resulting payload stale against today's, so a COMPLETE sweep
  is persisted to Turso and the disk mirror with a spurious `stale: true`. This
  is the production half of T-227, which fixed only the test fixture.
  **AC:** resolve the anchor once in `scan_index` and thread it into
  `build_index_payload`; red — a fake clock that flips the resolution between
  the two calls must currently produce `stale: true` on a complete sweep.

- **T-243 [P2] No deploy path reinstalls the fleet-wide drop-in.**
  `cloud/services/radon-.service.d/common.conf` — the drop-in that sets
  `RADON_DB_NO_REPLICA=1` on every `radon-*` unit, the DUR-07 belt-and-braces
  named in CLAUDE.md — is written ONLY by `setup-vps.sh:install_fleet_dropin`,
  at provisioning time. `install-units`' regex
  (`deploy-root-helper.sh:779`) matches bare `radon-*.{service,timer}`,
  `sync-scheduled-units` is allowlist-driven, and the file is absent from
  `CONTROL_PLANE_SOURCES`. So an edit reaches production only via a manual root
  copy or a full re-provision. T-236 added the sha256 pin, which makes the
  divergence visible at review time, and `drift_audit.py` reports it live as
  `fleet-dropin` — but nothing closes it. **AC:** either add it to an installer
  verb, or assert in a test that the pin and the drift report are the only
  intended mechanism and document it as manual.

- **T-244 [P2] Two `fmtUsd` implementations disagree on where the minus sign
  goes, and one docstring is wrong about its own function.**
  `web/lib/positionUtils.ts:15` is
  `` `$${normalizeRoundedZero(n, 0).toLocaleString("en-US", …)}` ``, so a
  negative renders `$-50` — minus AFTER the dollar sign. `web/lib/format.ts:23`
  `fmtUsd` renders `-$50` (explicit `value < 0 ? "-" : ""` prefix). Both
  position surfaces import the `positionUtils` one, so every short's Market
  Value cell publishes `$-50`. Verified at both lines. Additionally
  `web/lib/format.ts:19-21` `fmtUsdRound` has the SAME shape as the
  `positionUtils` one while its docstring promises `"$45,678" / "-$1,234"` — the
  docstring describes behaviour the function does not have. Surfaced under
  T-220: this is exactly why that file's `money()` helper never matched negative
  market values, so S3's comparison was `null === null` for every negative draw.
  Values agreed, so there is no cross-surface mismatch; it is a formatting
  inconsistency plus a false docstring. **AC:** one shared formatter, or a
  contract test asserting both spellings agree; red — assert `fmtUsd(-50)` is
  `"-$50"` from both modules.

- **T-245 [P2] `ScanGate.mark_failure()` does not clear `_last_success`.**
  `mark_success()` clears `_last_failure`, but not the reverse. In `_gated_scan`,
  `on_fresh` raising `HTTPException` (the GEX ticker-mismatch 502 path at
  `scripts/api/server.py:4007`) calls `mark_failure()` while a prior success's
  cooldown is still live. `retry_after()` takes the `max` of both so it is
  benign today, but `_admit()` then reads the cache under `in_cooldown()`, and
  for `/gex/scan` the cache is ticker-scoped, so a mismatched write can leave the
  gate reporting a cooldown for a payload that never landed. Surfaced under
  T-230. **AC:** a case where a success is followed by a failure and the gate is
  asked for its cache; assert it does not serve a cooldown for an unlanded
  payload.

- **T-249 [P2] The header-timeout guard accepts Caddy's UNLIMITED default.**
  `cloud/tests/test_caddyfile.py::test_the_header_timeout_is_short_enough_to_surface`
  asserts the parsed value is `<= 60`, and `0 <= 60`, so
  `response_header_timeout 0s` — which is Caddy's spelling for "no limit" —
  passes the very guard meant to bound it. Demonstrated under T-224: mutating
  the `localhost:3000` block to `0s` left all EIGHT regex/text assertions in the
  file passing, and only the new wire-level mechanism test caught it ("the edge
  forwarded the upstream's late response header instead of giving up first").
  The mechanism test now covers this mutant, so the exposure is closed in
  practice, but the text assertion is still wrong on its own terms and will
  mislead the next reader. **AC:** require `0 < value <= 60`; a `0s` config must
  red the text assertion, not only the mechanism one.

- **T-248 [P2] A standing, clock-INDEPENDENT false-red in the day-move spec,
  caused by a credential prerequisite rather than by anything under test.**
  `web/e2e/account-day-move-ib-daily-pnl.spec.ts:244` asserts
  `toContainText("C$4.48")` on `td.last-price-cell`, and receives `"$4.48"` — the
  plain portfolio-fixture `market_price` with no `C` calculated-price marker — at
  BOTH a weekend clock and the pinned trading-day clock. The spec's
  `MockWebSocket` is never constructed and no ws-ticket request is made, so no
  live quote ever reaches the row. Root cause: `RealtimeAuthProvider`
  (`web/lib/RealtimeAuthContext.tsx`) sources `getToken` unconditionally from
  Clerk's `useAuth()`, and `buildAuthenticatedWebSocketUrl`
  (`web/lib/realtimeSocketAuth.ts:29-40`) throws "Realtime auth token
  unavailable" on a null token. Neither runner clone has a `web/.env`, so the
  spec cannot go fully green on this machine regardless of the clock. CI holds
  this spec out of the curated e2e list (`ci.yml:500-503`), so it is a LOCAL-only
  red — but it is a permanent one that will keep costing a re-run to attribute.
  **AC:** either stub the realtime auth boundary so the spec is self-contained,
  or mark it `test.skip` behind an explicit `web/.env` precondition with the
  reason linked here. Do NOT leave it silently red.

  Related environment fact worth recording: **this spec fails 100% under
  `next dev --turbopack`, at any clock**, which is `playwright.config`'s default
  webServer. The spec replaces `window.WebSocket` globally, which breaks Next's
  dev HMR client (`socket.addEventListener is not a function`), hydration aborts,
  zero `/api/*` requests fire and the page renders the SSR shell. Only a prebuilt
  `next start` gives a meaningful signal. Anyone re-verifying must build first.

- **T-247 [P0] The T-124 realized-P&L correction is INERT in production, and
  the test that guards it passes only on a fixture accident.**
  `_ordered` (`scripts/clients/journal_realized.py:190-201`) sorts a day by
  `(day, execution_time, written_at, index)`. A real Flex row carries NO
  `execution_time` — `journal_rehydrate` never writes the key (grep: zero
  occurrences in the file) — so `str(... or "")` yields `""`, which sorts
  FIRST. The Flex row therefore wins attribution and the DAEMON's row is the one
  suppressed. The surviving key is then a Flex `tradeID`
  (`scripts/trade_blotter/flex_query.py:274` takes
  `trade.get("tradeID") or trade.get("execId")`, so it is digit-only), and no IB
  fill ever carries a `tradeID`, so `apply_journal_realized_pnl` matches
  nothing and **both** partials ship IB's drifted figure. All three lines
  verified by the lead. This hits the GENUINE-duplicate case too, which means
  the SLV +$18,511-vs-+$30,069 correction that T-124 exists to deliver never
  lands in production at all.
  `test_same_close_under_api_and_flex_ids_counts_once` passes only because its
  fixture gives NEITHER row an `execution_time`, so the tie falls through to
  `written_at` and the daemon row happens to survive — the opposite of what
  production does. Pinned as-is under T-184 by
  `test_a_real_flex_row_has_no_execution_time_so_it_wins_the_attribution`; the
  behaviour is recorded, not fixed.
  **AC:** the root fix is upstream — `flex_query.py:274` prefers `tradeID` over
  the Flex `ibExecID`; carrying `ibExecID` would let `_claim_exec_parts` dedupe
  exactly and retire the namespace heuristic entirely. Red: a fixture where the
  daemon row HAS an `execution_time` and the Flex row does not must currently
  suppress the daemon row and produce an unmatchable key; green: the daemon row
  survives and `apply_journal_realized_pnl` matches an IB fill.

- **T-246 [P2] A matcher wider than intended.**
  `web/tests/same-day-pnl-surface-parity.test.tsx:157` builds a `RegExp` from an
  unescaped label, so its `.` matches `,`. Harmless now that T-233 pins the
  locale, but it is still a matcher that accepts strings it was not meant to.
  **AC:** escape the label before constructing the pattern; a label containing a
  regex metacharacter must match literally.

## Remediation 2026-08-28

**This cycle's AUDIT PHASE PRODUCED NOTHING — operator action item.** The
wrapper created and pushed `origin/testing/2026-08-28` at `c6d08fbd` and then
exited without appending a `## Delta audit 2026-08-28` section, a ledger line,
or a PR. The range is NOT empty: `789aabea..c6d08fbd` is 24 commits / 262 files
/ +23193 lines, including the whole IB Flex file-ingest rework, the RTH-fill
orders surface, the B2 nightly-dump job and the VPS disk-cleanup timer. **That
delta is unaudited.** Next audit must treat `789aabea` (not `c6d08fbd`) as the
base so the range is re-covered.

**Backlog state at the start of this remediation: zero un-DONE P0 or P1.**
- T-190 … T-217 (5 P0, 23 P1) all landed 2026-08-27, PR #112.
- T-237, the one P0 left open there, is fixed on `main` — PR #109 merged
  2026-08-27 12:51Z. Re-verified here:
  `pytest scripts/tests/test_portfolio_risk_gate3_measurability.py` is
  12 passed / 0.32 s at `c6d08fbd`.
- T-003 … T-053 (the frozen PART A P0/P1s) landed pre-log via PRs #13/#14,
  per the `TEST_LOG.md` header contract; T-050 remains the one open straggler
  there and is still a maintainer threshold decision.

So this run works the newest **P2** stragglers in severity-then-recency order,
which is the next rung of the PART B contract.


## Delta audit 2026-08-29

Range `789aabea..f7b5eeb9` — 61 commits, 366 files, +36023/-2510.
113 added / 252 modified / 1 deleted; **74 test files added, 116 modified**.
New findings continue the frozen numbering at **T-250**. PART A (§1–§10) is
untouched; nothing above this line was rewritten.

**Base note.** The base is `789aabea` (the 2026-08-27 ledger SHA), NOT
`c6d08fbd`: the 2026-08-28 audit phase was truncated at the harness
background-wait ceiling (T-239) and explicitly did not advance the ledger. The
range therefore overlaps last weekend's own remediation — PRs #111/#112 and
#127/#128 are inside it — and those commits were re-triaged as ordinary delta
rather than exempted.

**Pre-flight.** Runner clone verified (`.radon-weekend-runner` present, tree
clean apart from the wrapper's own lock). `rtk` is NOT installed on this host,
so bare `git` is correct and its output trustworthy (the 2026-08-16 rtk rail
applies only where the proxy exists). `node` was absent from the agent PATH
until `~/.nvm/versions/node/v24.14.0/bin` was prepended. The SYSTEM
`python3.13` has no pytest at all; every gate ran from
`~/radon-weekend/venv/bin/python3.13` (pytest 9.1.1 + `pytest-asyncio` +
`pytest-xdist`). `gh` GraphQL is 401 on this host — all GitHub reads went
through `gh api` REST. `origin/testing/2026-08-29` did not exist at pre-flight;
it was created and pushed EMPTY immediately per the collision rail. Open PRs at
pre-flight were #126 and #125, both WIP docs/profile work matching no finding
subject. All scratch was namespaced to `/tmp/tw-2026-08-29/`.

**Today is Saturday 2026-08-29,** so this audit ran with the weekend-dependent
class LIVE rather than hypothetical — see the note under T-273.

**Load.** The host ran at load average **74 → 224** for the whole audit
(`corespotlightd` at 589% CPU, six audit agents, and the reliability loop in
its own clone). Every timing-shaped red below was re-run in isolation before
being called anything.

### Standing sweeps

**Gates, round 1, serial, from the repo root (clean tree, HEAD `f7b5eeb9`).**
Load average during the run was **74 → 224** (`corespotlightd` at 589% CPU plus
six audit agents plus the reliability loop in its own clone), so every
timing-shaped red below was re-run in isolation before being called anything.

| Gate | Round 1 | Isolated re-run |
|---|---|---|
| `python3.13 -m pytest` (recursive) | **3 failed** / 8558 passed / 1 skipped / 90 deselected, 870 s | all 3 green, `4 passed in 14.82s` |
| `npx vitest run` | **9 failed** / 7934 passed / 8 skipped (7951), 787 files, 145 s | 9 failed / 18 passed in 3.35 s — DETERMINISTIC, not load |
| `python3.13 -m pytest cloud/tests` | **37 failed** / 1263 passed / 7 skipped, 272 s | see the darwin baseline below |

**The pytest reds are load, and the distribution says so.** Three failures in
three unrelated files, all timing-shaped: `assert 2.5310465410002507 < 1.0`
(`test_divyield.py:420`) and two `assert 1 >= 2` on a retry that had not fired
(`test_leap_capacity_shed_retry.py:241`,
`test_leap_garch_no_duplicate_scan.py`). All four tests pass together in
14.8 s in isolation, at load 162. This is the 2026-08-23 profile
(scattered + timing-shaped = load), not the 2026-08-27 one (concentrated +
AttributeError-shaped = real). CI at this SHA had all ten pytest shards green,
which agrees.

**The vitest reds are NOT load, and are all in files CI does not run.** All 9
are in `web/tests/integration.test.ts`, `lib/tools/__tests__/kelly.test.ts` and
`lib/tools/__tests__/runner.test.ts` — the exact three files `ci.yml:143-145`
excludes with `--exclude`. Isolated they are 9 failed / 18 passed in 3.35 s,
3/3 identical. So the CI-GATED vitest set is fully green at 7934, and 27 tests
run nowhere while 9 of them are permanently red on a developer machine.

**The darwin cloud baseline moved 35 → 37, and the LIST explains all of it.**
Attributed by running the base SHA in a worktree
(`git worktree add --detach /tmp/tw-2026-08-29/base-wt 789aabea`), sorting both
`FAILED` lists and diffing them, per the 2026-08-22 rail. Base `789aabea`:
`35 failed, 1098 passed, 13 skipped`. HEAD `f7b5eeb9`:
`37 failed, 1263 passed, 7 skipped` (+165 passing tests, matching the five new
`cloud/tests` files). The diff is exactly four lines:

- **NEW at HEAD (3), all deliberate:**
  `test_caddy_edge_timeouts.py::TestEdgeMechanism::{test_a_hung_upstream_becomes_a_5xx_within_a_bound, test_a_severed_post_is_not_replayed, test_the_shipped_caddyfile_adapts}`.
  These are T-205's fix working as designed — this host has no `caddy` binary
  and `RADON_CADDY_BIN` is unset, so the suite now FAILS loudly where it used
  to skip silently. Confirmed: `command -v caddy` empty. Not a regression.
- **GONE at HEAD (1), an improvement:**
  `test_relay_container_watchdog.py::test_wedged_child_misses_watchdog_inside_container_env`
  failed at the base SHA and passes at HEAD.

**Recorded darwin baseline for the next run: `37 failed, 1263 passed, 7 skipped`
on a host with no `caddy` and no GNU coreutils** (34 of the 37 are the standing
`sha256sum` / `bash >= 4` class, T-118).

**Collection union — clean; T-122 holds.** Recursive `pytest --collect-only -q`
and the union of all ten `py-tests` shard path-sets both report 8562 collected
(8652 − 90 deselected); `comm -23` empty in both directions. My local run's
8558 passed + 3 failed + 1 skipped = 8562 exactly, and the ten CI shard pass
counts sum to 8561 (= 8562 − 1 skipped). `cloud/tests` 38/38 files, 1307 tests.
`npx vitest list --filesOnly` = 787 = the filesystem count under the six include
globs. Every one of the delta's 35 new pytest files and 30 new vitest files is
reached. The T-122 directory class is now actively asserted by
`scripts/tests/test_ci_deploy_concurrency.py:336,444`.

**Enforcement — improved at the workflow level, still absent at the branch.**
`stage-release.needs` GAINED `web-coverage` and `py-coverage` in this delta
(`ci.yml:580`); `deploy.needs`/`if:` byte-identical at both refs
(`ci.yml:664`). No T-160-class drop. But
`gh api repos/joemccann/radon/branches/main/protection` still returns no
`required_status_checks` key at all — re-confirmed this run, unchanged since
T-222.

**Coverage-ratchet honesty — clean.** vitest lines 75 / functions 71 /
branches 65 unchanged; `coverage.exclude` 20 entries unchanged; the three
ci.yml `--exclude` flags unchanged; pytest `--fail-under=56` unchanged;
`[tool.coverage.run] omit` 4 entries unchanged; `branch = true` still set;
`--expect-shards` still 8. `pyproject.toml`, `.coveragerc` and `setup.cfg` are
untouched in the delta. The only `vitest.config.ts` change PINS `TZ`/`LC_ALL`/
`LANG` — a tightening. The only `ci.yml` changes are the `stage-release.needs`
widening and a caddy-install step that UN-skips
`cloud/tests/test_caddy_edge_timeouts.py::TestEdgeMechanism`. Nothing lowered,
nothing newly inflated.

**New skips — one, and it is honest.**
`cloud/tests/test_app_plane_cutover_safety.py:100` marks from a single-entry
`DROP_IN_SKIP_BASELINE` (`:82-89`) whose reason cites **T-204** and whose
comment says the dict "may shrink, never grow"; declared at collection so `-rs`
counts it. Zero added `it.skip` / `describe.skip` / `.only` / `.todo` / `xit` /
`xdescribe` / `@unittest.skip` / `pytest.mark.xfail` across all 47,036 added
lines (parsed with a python3.13 line-tracker over added lines only, per the
BSD-grep lesson). The `skipif` hits at `test_caddy_edge_timeouts.py:333-335`
are a meta-test asserting the skipif EXISTS, paired with the new install step —
the opposite of a skip. Eight added bare `return`s, all in handler callbacks or
following an `expect(...)`.

**Determinism scope.** The delta touches 190 of the repo's test files (74 added,
116 modified), so the "re-run only delta-touched files 3×" rule again collapses
into full-gate runs. The scoped 3× was therefore run over the 35 ADDED pytest
files and the 30 ADDED vitest files instead; results in the table above.

### Re-triage of the standing NEW_FINDINGS items

- **`resolveSpreadPriceData` still stamps the wall clock — CLOSED, FIXED.**
  Recorded 2026-08-26 as "the fix is now a one-liner" and left for this audit to
  number. It does not need numbering: `web/lib/positionUtils.ts:503` now reads
  `timestamp: natural.asOf ?? ""`, and `grep "new Date().toISOString()"
  web/lib/positionUtils.ts` returns nothing. Landed inside one of the two
  weekend PRs in this range. Removing it from the standing list.

- **Six more producers construct their API client outside a health block —
  STILL OPEN, unchanged.** `_UNGUARDED_CTOR_BASELINE`
  (`scripts/tests/test_service_registration_completeness.py:485-492`) still
  holds exactly the six entries recorded on 2026-08-26
  (`fetch_credit_spread.py::fetch_uw_closes`,
  `fetch_iei_hyg.py::fetch_uw_closes`, `fetch_ivrank.py::_real_ib_fetch`,
  `fetch_trin.py::sample_live`, `fetch_vixcor.py::run`,
  `ib_reconcile.py::connect_ib`), with the companion
  `test_the_baseline_has_no_stale_entries` still guarding shrink-only. Nothing
  in this delta touched them. Carried forward, still un-numbered by design —
  each needs its own red/green.

- **E2E testid backlog — STILL OPEN and now measurably WORSE, promoted to
  T-271 and T-309.** The delta added five e2e specs; none adopted testids for
  the surfaces they drive (`chain-deck-ticket-scroll.spec.ts` hangs on six raw
  class chains) and none entered the curated CI list. This is the first delta
  where the backlog grew rather than eroded.

- **`pytest cloud/tests` is N-red on macOS — the baseline MOVED again, 35 → 37,
  and every line of the move is explained.** See the darwin baseline paragraph
  in the sweeps above: the list, not the count, is what settled it, and the base
  SHA was RUN in a worktree rather than reasoned about. The item stays open —
  the honest fix is still a portable digest, not another skip.

- **The unreproduced 10-failure vitest round (2026-08-17) — the remedy
  WORKED.** That item's one actionable was "the weekend loop should persist
  per-test vitest output on gate runs so the next occurrence is nameable in one
  shot." This run's gate script writes the full reporter output to
  `/tmp/tw-2026-08-29/gates/vitest-r1.txt`, and the 9 reds were named,
  attributed and reproduced in one pass (T-276) instead of being logged as an
  unnamed observation. Keeping the item open only as the record of why the gate
  script writes files.

- **`e2e/performance-twr-payload.spec.ts` permanently RED, and the Day Move
  dev-vs-`next start` divergence — BOTH UNCHANGED.** Neither spec appears
  anywhere in `.github/workflows/ci.yml` (grep returns nothing), so both remain
  local-only reds held out of the curated list. No delta commit touched either
  contract. Carried forward.

- **Pre-existing cross-file pollution in `orders-place-cache-race.test.ts` —
  REPRODUCED, and the DIAGNOSIS was wrong. Numbered as T-311.** The 2026-08-28
  log recorded one red under a three-file combination and stated the file was
  "6-passed ×3 in isolation", concluding cross-file pollution. Re-run at HEAD:
  the three-file combination is `2 failed / 27 passed`, and the file **in
  isolation, with no other file in the run**, is `1 failed | 5 passed` on run 1
  and `6 passed` on run 2 — the same case, the same
  `AssertionError: expected 0 to be greater than 0`. So the other two files are
  incidental and the race is intra-file, at
  `web/tests/orders-place-cache-race.test.ts:131`. Promoted out of the standing
  list and filed as **T-311**.

### P0

- **T-250 [P0] `claim_flex_delivery` reads an attribute the libsql driver does
  not have, so it returns `False` unconditionally and the ENTIRE Flex ingest is
  a silent no-op.** `scripts/db/writer.py:687` is
  `int(getattr(result, "rows_affected", 0) or 0) > 0`. Verified in-process
  against the repo's pinned `libsql-experimental==0.0.55`
  (`requirements.txt:43`): `Connection.execute()` returns a `builtins.Cursor`
  whose attributes are
  `['arraysize','close','description','execute','executemany','executescript','fetchall','fetchmany','fetchone','lastrowid','rowcount']`
  — **`rows_affected` is absent**, `rowcount` is 1 on the first insert and 0 on
  the conflicting one. `getattr(..., 0)` therefore yields `0` and the claim is
  `False` even on FIRST sight. Every other rowcount consumer in the repo uses
  `rowcount` (`scripts/db/writer.py:311`, `:2238`,
  `scripts/db/retention.py:191`); `rowsAffected` is the JS driver's name
  (`scripts/db/writer.js:280`). Second defect on the same lines: this is the
  only INSERT among 60+ writers in `writer.py` with no `db.commit()`.
  **What ships:** `scripts/flex_delivery_ingest.py:47-60` takes the
  `"outcome": "duplicate"` branch for every delivery and returns `ok: True`, so
  `cash_flow_sync`, `perf_twr_builder.build_and_persist` and
  `journal_rehydrate.rehydrate` are never invoked for any Flex statement.
  `scripts/flex_sftp_pull.py:253-255` sees `ok=True`, increments `ingested`,
  heartbeats `"ok"` and exits 0. `radon-flex-pull.timer` (Tue..Sat 07:30 +
  08:30 ET) reports green forever while `cash_flows`, `journal` and the TWR
  series never advance — and the claim row IS inserted, so it is unrecoverable
  without a manual `DELETE FROM flex_deliveries`. Nothing pages. This goes live
  with the first IBKR drop on **2026-08-31, in two days**.
  **Why no test catches it:** all four test references
  (`scripts/tests/test_flex_delivery_fingerprint.py:82,114,135`,
  `test_flex_delivery_ingest_atomicity.py:42`) `monkeypatch.setattr` the
  indirection at `flex_delivery_ingest.py:34`. `db.writer.claim_flex_delivery`
  has **zero** callers in any test.
  **AC:** RED — bind a real libsql/sqlite `:memory:` over `db.client.get_db`,
  apply `scripts/db/migrations/0059_flex_deliveries.sql`, assert
  `claim_flex_delivery(sha, classified_as="activity") is True` then `is False`,
  assert the row survives a fresh connection (pins the commit), and assert an
  end-to-end `ingest_xml` runs both writers on the first call and neither on
  the second. GREEN — `rowcount` + `db.commit()`. Restoring `rows_affected`
  must red it. *(Converged: found independently by the coverage agent and the
  flex agent, and re-verified by the lead against the installed driver.)*

- **T-251 [P0] The file CLAUDE.md names as the wire-assertion REFERENCE does
  not assert the wire.** `web/tests/chain-transmit-gate.test.tsx:143-146`:
  `fetchMock.mock.calls.some(([input]) => String(input).includes("/api/orders/place"))`.
  `OptionsChainTab.tsx:556-560` POSTs a combo body with
  `action: getComboEntryAction(...)`, `quantity`, a SIGNED `limitPrice`, and
  `legs[].ratio`. `.some(includes(...))` accepts a wrong action, an inverted
  sign on `limitPrice` (a debit transmitted as a credit), a wrong leg ratio,
  and — because it is `.some`, not `.toHaveLength(1)` — a double-send. The
  paired closed-gate half at `:128` stops at
  `expect(transmitButton().disabled).toBe(true)`, i.e. at the button, so
  nothing in this file proves the gate is closed ON THE WIRE either. CLAUDE.md
  cites this exact file as the exemplar for the rule it violates.
  **AC:** RED — negate `signedLimitPrice` in `OptionsChainTab.tsx`; the file
  stays green. GREEN — the
  `web/tests/admin-action-request-assertions.test.tsx:164-167` shape:
  `expect(sent).toHaveLength(1)`, `sent[0].url === "/api/orders/place"`,
  `sent[0].method === "POST"`, `JSON.parse(sent[0].body)` matched field by
  field; plus a `toHaveLength(0)` after driving the React `onClick` past
  `disabled` while unacknowledged.

- **T-252 [P0] (delta to T-211) The mobile transmit gate's ARMED half asserts a
  count over a URL substring — no method, no payload.** T-211 fixed the CLOSED
  half; the armed half was not. `web/tests/mobile-ticket-transmit-gate.test.tsx:108-110`
  defines `placeCalls()` as
  `fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/orders/place"))`
  and `:167` asserts only `toHaveLength(1)`. `MobileOrderTicket.tsx:483-518`
  builds `action`, `quantity`, `limitPrice`, `tif`, `legs[]` and
  `ibPlaceFields`. Mutations that stay green: flipping `action` at `:504` (a
  long call transmits as a naked short call), `quantity: 1` against the 10-lot
  fixture, dropping `...ibPlaceFields(...)` so a STP goes as a bare market
  order, or switching `method` to `"GET"`.
  **AC:** RED — flip `action` at `MobileOrderTicket.tsx:504`. GREEN — capture
  `[input, init]`; assert `String(input) === "/api/orders/place"`,
  `init.method === "POST"`, and `JSON.parse(init.body)` deep-equals the
  expected option body.

- **T-253 [P0] `basis_source: "mixed"` is minted for the exact money-path case
  it was added to protect, and NOTHING consumes it — the blended `entry_cost`
  and `max_risk` still ship.** `scripts/ib_sync.py:503-513`
  (`_position_basis_source`), `:661` (the label), `:542-547`
  (`total_entry_cost` sums leg `entry_cost` unconditionally), `:580-587`
  (`max_risk = total_entry_cost`, or `width - abs(total_entry_cost)`). The
  source comment at `:650-660` states the remedy explicitly: *"`mixed` names
  it, so the display layer can refuse to aggregate rather than presenting a
  blended basis as fact."* No display layer does — `grep -rn basis_source web`
  returns **0 hits**, and `web/lib/types.ts` gained `outsideRth` and
  `LeapBestContract` in this delta but not `basis_source`. R-374 is LABELLED,
  not fixed.
  **What ships:** roll the short leg of a debit vertical intraday and hold the
  long leg overnight. The rolled leg gets today's session VWAP, the held leg
  keeps IB's lagged `avgCost`, `collapse_positions` sums them, `max_risk`
  inherits the blend, and the ticket renders a max-loss for a trade that was
  never placed. **Gate 3 sizes the 2.5% bankroll cap off that number.**
  **Why no test catches it:** `scripts/tests/test_session_fill_basis_arithmetic.py:205-225`
  tests `_position_basis_source` as a pure function over synthetic leg dicts.
  Nothing feeds a genuinely mixed leg set through `collapse_positions` and
  asserts the resulting `entry_cost` / `max_risk`, and no web test renders a
  `mixed` position.
  **AC:** RED — build two legs for one ticker/expiry, one covered by
  `fill_basis_lookup` and one not; run `fetch_positions` → `collapse_positions`;
  today the collapsed row carries a blended number. GREEN — `entry_cost` and
  `max_risk` are `None` (or carry an explicit `unmeasured` marker) and the web
  cells render `---`. Making `_position_basis_source` return `"session_fills"`
  for the mixed case must red both halves.

- **T-254 [P0] The deploy rollback restores a release whose own CI gate never
  passed, and logs that it did.** `cloud/scripts/deploy.sh:1476` prints
  `"Rollback complete. Previous release ${prev_commit} passed the deploy gate."`
  It is not checked. Observed live this run: CI run `33239774951` at HEAD
  `f7b5eeb9` had all 27 test jobs GREEN and `Deploy to VPS` FAILED
  (`[ERROR] [gate] relay listener never accepted a connection` …
  `[ERROR] Post-deploy gate failed`), and rolled back to `e4bc7171`. But
  `e4bc7171`'s own CI run `33197706791` concluded **failure** at
  `pytest (cloud mz)`, with `Prestage VPS release` and `Deploy to VPS`
  **skipped** — verified over the REST API. So production is now serving a SHA
  the gate rejected, while the log asserts the opposite. Eight commits on
  `main` (`f7b5eeb9, 35071d85, ae96c05b, 923d9fd8, 8cd7909b, 6d55f99e,
  5d37599c, 9cd607b8`) are not in production, including BOTH
  `fix(health): sidecar … must not page edge aggregate_down` commits, so the
  paging defect they fix is still live. The release gate is enforced on the
  forward path only.
  **AC:** RED — a `cloud/tests` case driving the rollback path with a
  `prev_commit` whose CI conclusion is `failure`; today it rolls back and
  prints the reassurance. GREEN — the rollback resolves the target SHA's own
  `ci.yml` conclusion and refuses (or downgrades the message to an explicit
  warning) when it is not `success`. *(The deploy failure itself is the
  reliability loop's lane; the untested, false assertion in the rollback path
  is this one's.)*

### P1

- **T-255 [P1] `sftp ls` is multi-column by default, so only the LAST file on
  each line is ever pulled.** `scripts/flex_sftp_pull.py:79` sends the batch
  `cd outgoing\nls` with no `-1`, and `:86-89` parses
  `line.strip().split()[-1]`. `man 1 sftp` documents `-1  Produce single
  columnar output` precisely because columnised output is the default.
  **What ships:** IBKR drops three files; sftp prints
  `Trades.xml.gpg  Activity.xml.gpg  NAV.xml.gpg` on one line;
  `list_remote_gpg` returns `["NAV.xml.gpg"]`. The other two are never fetched
  and `run()` reports `ok` with exit 0 because `ingested == 1`. A partial
  delivery is indistinguishable from a complete one.
  **Why no test catches it:** `FakeSftp.__call__`
  (`scripts/tests/test_flex_sftp_pull.py:70`) emits `"\n".join(self.files)` —
  one name per line, the exact shape the parser assumes — and
  `test_list_dir_uses_sftp_dash4_and_batch_stdin` (`:97`) uses a single file.
  **AC:** RED — a `FakeSftp` whose `ls` stdout is `"a.gpg  b.gpg  c.gpg\n"`
  (and a mixed `"a.gpg  b.gpg\nc.gpg\n"`); assert
  `list_remote_gpg(...) == ["a.gpg","b.gpg","c.gpg"]`. GREEN — `ls -1`, or a
  parser that splits the whole line.

- **T-256 [P1] A no-trade session's Trade Confirmation is rejected and pages,
  because `has_trade` is a ROW check while its sibling `has_transfer` is a
  SECTION check.** `scripts/lib/flex_classify.py:73-74`:
  `has_transfer = root.find(".//Transfers") is not None` sits directly above
  `has_trade = root.find(".//Trade") is not None`, and the comment three lines
  up (`:66-68`) explains why the transfer check was relaxed — *"Requiring a
  `<Transfer>` child rejected every quiet session."* The same reasoning was
  never applied to `Trades`. Verified: `<FlexStatement><Trades></Trades></FlexStatement>`
  → `FlexClassifyError ambiguous_or_incomplete:nav=0 cash=0 transfer=0 trade=0`.
  **What ships:** on any session where the account traded nothing, the nightly
  `radon-flex-pull` classifies the trade-confirm file as ambiguous,
  `flex_sftp_pull.py:256-259` sets `failed=True`, heartbeats `error` and exits
  1 — `flex-pull` goes red in the watchdog daily bucket on a completely healthy
  day.
  **Why no test catches it:** `test_flex_classify.py:48`
  (`test_empty_transfers_section_is_activity`) covers exactly this case for
  Activity and there is no `Trades` analogue; the fixture
  `flex_trade_confirm_sample.xml` always carries one `<Trade>`.
  **AC:** RED/GREEN — `test_empty_trades_section_is_trades`: a
  `FlexQueryResponse` with `<Trades></Trades>` and no other section asserts
  `classify_flex_xml(xml) == TRADES`; pair with a `run()` case asserting
  `code == 0` and an `ok` heartbeat.

- **T-257 [P1] The delivery claim is never released, so a FAILED ingest is
  permanently skipped.** `scripts/flex_delivery_ingest.py:47` claims before any
  writer (correct for idempotency), but the failure branches at `:73-81`
  (`cash_flow_sync` non-zero) and `:97-105` (`journal_rehydrate` `ok:False`)
  return without deleting the claim row — and the atomicity suite's own
  docstring establishes that a failed `cash_flow_sync` leaves earlier chunks
  committed.
  **What ships:** `X.xml` ingests, `cash_flow_sync` exits 3 leaving
  `cash_flows` half-written and TWR unbuilt. The operator fixes the transient
  cause and re-drops `X.xml`; the second run returns
  `{"ok": True, "outcome": "duplicate"}` — green, exit 0 — and the half-written
  state is never repaired. Recovery requires a manual DELETE.
  **Why no test catches it:** `test_flex_delivery_ingest_atomicity.py:34-42`
  stubs the claim to always-True via an autouse fixture and says so in the
  docstring, putting the fail-then-retry interaction outside both suites by
  construction.
  **AC:** RED — with the stateful fake claim already written at
  `test_flex_delivery_fingerprint.py:75`, make `cash_flow_sync.main` return 3
  then 0; the second `ingest_path` returns `outcome == "duplicate"`. GREEN — it
  re-runs the writers.

- **T-258 [P1] The "exactly ONE Flex request per run" property — the one that
  costs a 24h-to-168h token embargo when broken — is asserted in a comment,
  not a test.** CLAUDE.md makes it throttle-critical.
  `scripts/perf_twr_builder.py:717-718` (`_flows_query_id` falls back to the
  NAV id) and `:793-806` (`already_attempted = document.query_id == query_id`)
  implement it. No test references `resolve_flows`, `_flows_query_id` or
  `IB_FLEX_FLOWS_QUERY_ID`. The only mention anywhere is
  `scripts/tests/test_nested_deadlines.py:129`,
  `query_ids = 1  # resolve_flows reuses the single NAV document` — a hardcoded
  constant inside a systemd-timeout computation that ASSUMES the property.
  **What ships:** someone sets `IB_FLEX_FLOWS_QUERY_ID`, or a refactor makes
  `_flows_query_id()` diverge from the NAV id; `already_attempted` is False, a
  second SendRequest fires in the same run, and the token takes the documented
  1025 embargo. Every test still passes.
  **AC:** RED/GREEN — drive `build_and_persist()` with `fetch_flex_xml`
  replaced by a counter; assert exactly 1 with `IB_FLEX_FLOWS_QUERY_ID` unset,
  and still 1 (or an explicit refusal) when it is set to a different id.

- **T-259 [P1] The production sFTP→ingest wiring is never exercised end to end
  — which is the seam that hid T-250.** `scripts/flex_sftp_pull.py:192-203`
  (`_default_ingest`), `:125-141` (`_gpg_decrypt`), `:106-122` (`pull_gpg`),
  `:226` (`ingest_fn = ingest or _default_ingest`). All ten tests in
  `scripts/tests/test_flex_sftp_pull.py` inject `decrypt=` and, where ingest is
  reached, `ingest=` (`:209-210`), so the default callables — which are what
  `main()` (`:287-292`) and `cloud/services/radon-flex-pull.service:17`
  actually run — have zero coverage. Two defects a test would have caught:
  `_default_ingest` does `del source_path` at `:193`, discarding the caller's
  `dest.with_suffix(".xml")` path from `:252` so the delivery's provenance
  records a random `/tmp` name; and `retain_newest_gpg(inbox, keep=0)` at
  `:173` evaluates `files[:-0]` → `files[:0]` → deletes nothing.
  **AC:** RED/GREEN — call `pull.run(...)` WITHOUT `ingest=`, with
  `db.writer.get_db` bound to an in-memory DB carrying migration 0059, and
  assert rows were actually written rather than `outcome == "duplicate"`;
  add a `retain_newest_gpg(inbox, keep=0)` case asserting every `.gpg` is
  removed. Reverting T-250's fix must red the first.

- **T-260 [P1] Three of the four `summaryFiguresAreFinite` gates on
  `okToSubmit` are untested, so Transmit can arm on a NaN risk verdict.**
  `web/lib/order/risk/useOrderRisk.ts:303` (helper); call sites `:741` (linear
  close-out), `:771` (linear open), `:834` (option close-out), `:881` (option
  open). `web/tests/instrument-detail-stp-nan-risk.test.tsx:169-196` covers
  ONLY `:881`. The linear close-out path is reachable with a blank Limit:
  `grossCash = Math.abs(input.limitPrice * input.quantity * input.multiplier)`
  → `NaN` → `estimatedPnl` `NaN`. Deleting the guard at `:741`, `:771` or
  `:834` passes the whole suite today, and a stock close ticket re-arms
  Transmit over a summary reading `NaN` in every tile. Adjacent and
  pre-existing: `:741` is the only `okToSubmit` in the hook that does not also
  require `coverageStatus === "resolved"`, so a linear close-out arms under
  `no-portfolio`.
  **AC:** RED/GREEN — four `renderHook(() => useOrderRisk(...))` cases (linear
  open, linear close-out, option close-out, option open), each with one
  non-finite input asserting `okToSubmit === false` while
  `coverageStatus === "resolved"`, plus finite twins asserting `true`.
  Reverting any single call site must red exactly one case.

- **T-261 [P1] `run_offbox`'s "NEVER raises" contract, and its effect on the
  backup's health state, are untested.** `cloud/scripts/db_backup.py:546-568`
  (`run_offbox`), called at `:676`; `main()` at `:729-731` sets
  `state = "error" if detail.get("offbox_error") else "ok"` and returns 1. The
  docstring is the contract — *"Best-effort off-box leg. Returns
  `(summary, error)`; NEVER raises."* — and nothing tests it.
  `grep run_offbox cloud/tests` has no hits;
  `cloud/tests/test_db_backup_offbox.py` exercises `sync_offbox` one layer down
  and its `test_upload_failure_propagates` (`:305`) asserts the OPPOSITE
  behaviour. So the wrapper that converts a propagating failure into a
  non-fatal `(None, error)` is unverified.
  **What ships:** if `run_offbox` ever raises (e.g. `s3_config_from_env()` on a
  malformed endpoint), `run_backup` aborts AFTER the local dump landed and
  pruning ran, `main`'s outer handler writes `"backup failed: ..."`, and the
  operator is told the dump failed when it did not.
  **AC:** RED/GREEN — (a) `sync_offbox` raises → `run_offbox(tmp_path)` returns
  `(None, "<Type>: msg")` and does not raise; (b) `s3_config_from_env` → `None`
  gives the credentials-missing string; (c) `main()` with a stubbed
  `write_service_health` yields `state == "error"`, `detail["path"]` still
  naming the landed dump, rc 1. Removing the `try/except` reds (a) and (c).

- **T-262 [P1] An EMPTY dump is written, prunes the good ones, uploads, and
  heartbeats `ok`.** `cloud/scripts/db_backup.py:386` returns
  `{"tables": 0, "rows": 0}` without complaint, and `run_backup` (`:650-690`)
  has no plausibility floor between the dump and the prune/upload.
  **What ships:** `dump_database` sees an empty `sqlite_master` (a credential
  rotation pointing at a fresh DB, or a libsql read returning no rows). A
  ~120-byte valid gzip is written, `select_prunable` deletes dumps past the
  30-day window, `sync_offbox` uploads the empty artifact to B2, and
  `write_service_health` records `ok` with
  `summary = "dumped 0 tables / 0 rows -> ... (118 bytes); b2 1/1"`. Within 30
  days every local AND remote copy is an empty dump — the disaster-recovery
  artifact is gone and the health row never said so. Contrast
  `lib/vixts_math.py`, which has `MIN_SERIES_ROWS` for exactly this class.
  **AC:** RED/GREEN — `run_backup` raises (or heartbeats `error` and skips both
  the prune and the upload) when `stats["tables"] == 0` or `stats["rows"] == 0`;
  assert the older dumps still exist and `client.upload_file` was not called.

- **T-263 [P1] The VIXTS changed-source path never ages its own `data_date`;
  only the 304 path does.** `scripts/fetch_vixts.py:295-326`
  (`restate_cached_payload`, R-333) computes `expected_session`, `lag_days` and
  `status` — and runs ONLY on the all-304 branch (`:264-275`). The rebuild
  branch (`:277-292`) calls `build_payload` (`:186-201`), which emits no
  `status`, no `lag_days` and no `expected_session` at all, and
  `_write_db(..., rows_changed=True)` heartbeats `ok`. This matters because
  `scripts/clients/cboe_client.py:6-9` states the failure mode outright:
  *"Cboe re-touches Last-Modified intraday WITHOUT appending the session
  row."*
  **What ships:** Cboe re-touches the files for two weeks without publishing
  new sessions. Every night the conditional GET returns 200 → full rebuild →
  4,252 rows → passes `ensure_plausible_series` (row count and ratio band,
  never recency) → `service_health` green, API fresh inside the 48h
  `scan_time` budget, and `/regime/vixts` renders a confident regime badge for
  a two-week-old session. The defence R-333 built exists on the branch that
  CANNOT have new data and is missing on the branch that can. Secondary
  contract bug: `payload.status` is present on one path and absent on the
  other.
  **Why no test catches it:** `TestAll304PathReAgesTheVerdict`
  (`test_vixts_health_and_plausibility.py:183-232`) exercises only the 304
  branch; `test_changed_source_rebuilds_and_writes_rows` (`test_vixts.py:337`)
  asserts row writes, never freshness.
  **AC:** RED/GREEN — drive `run()` with all three sources returning changed
  text whose newest joined session is 10 calendar days behind
  `last_completed_session_date(now)`; assert `status == "stale_source"`,
  `lag_days == 10`, and a non-null `health_error` into `_write_db`. Assert
  `status == "ok"` with `expected_session` present on the healthy rebuild, so
  the contract is pinned on BOTH branches.

- **T-264 [P1] VIXTS's "a rejected value writes no row" is never asserted —
  only "an error row exists" is.**
  `scripts/tests/test_vixts_health_and_plausibility.py:141-162` monkeypatches
  `ensure_plausible_series` to raise and asserts an `error` heartbeat landed.
  The `_Recorder` stubs `upsert_vixts_rows` (`:112`) and `upsert_scan_snapshot`
  (`:109`) as no-op `pass` and never records their calls, and
  `_write_json_cache` is not intercepted at all. The ordering in `_run_cycle`
  (`:281-291`) is correct TODAY — validate, then write — but a plausible future
  change ("persist what we pulled, then flag it") would push a corrupt 4,252-row
  series into `vixts_history`, overwrite `data/vixts.json`, AND still emit the
  error heartbeat this test asserts on. The suite stays green while a ratio of
  50 lands in the canonical store.
  **AC:** RED/GREEN — record calls on `_Recorder` and spy `_write_json_cache`;
  on the plausibility-failure path assert `upsert_vixts_rows` count 0,
  `upsert_scan_snapshot` count 0, and `VIXTS_JSON` byte-identical to its
  pre-run contents. Use the REAL `ensure_plausible_series` with a genuinely
  corrupt series (one row at `ratio=50`) rather than a stubbed raise, so the
  guard and the ordering are proven together.

- **T-265 [P1] The IB connect-budget assertion is algebraically incapable of
  failing, and hardcodes a third copy of the caller cap.**
  `scripts/tests/test_ib_option_chain_connect_retry.py:99-109` (and its
  duplicate `test_equity_chain_connect_retry.py:94-114`) computes
  `worst_connect_s = attempts * CONNECT_TIMEOUT_S + (attempts - 1) * CONNECT_BACKOFF_S`
  and asserts `worst_connect_s + 2 * IB_REQUEST_TIMEOUT_S <= 45.0`. But
  `scripts/ib_option_chain.py:44-49` DEFINES
  `CONNECT_TIMEOUT_S = (CONNECT_BUDGET_S - (ATTEMPTS-1)*BACKOFF) / ATTEMPTS`, so
  the test's expression is that definition solved back and is identically
  `CONNECT_BUDGET_S` for any attempts, any backoff. Measured in-process at
  HEAD: `ATTEMPTS 2, TIMEOUT 4.5, BACKOFF 1.0, BUDGET 10.0, CAP 45.0` →
  `worst = 10.0`, `total = 40.0`, and `total` is pinned at
  `45 - _ENVELOPE_MARGIN_S` by construction. `CONNECT_ATTEMPTS = 12` is green.
  The `45.0` is a third hardcoded copy of
  `scripts/api/server.py:4475 _EQUITY_OPTIONS_CHAIN_TIMEOUT_S` (the source
  holds a second at `_CALLER_CAP_S`), so lowering the server's real cap to 20 s
  — R-352's exact failure mode, the JSON error envelope never rendering — reds
  nothing. The sibling
  `test_the_budget_is_derived_from_the_cap_not_hardcoded`
  (`test_equity_chain_connect_retry.py:112-114`) asserts `10.0 < 45.0` and is
  named for a property the source does not have.
  **AC:** RED/GREEN — import the cap
  (`from scripts.api.server import _EQUITY_OPTIONS_CHAIN_TIMEOUT_S as CAP`),
  assert `ib_option_chain._CALLER_CAP_S == CAP`, and assert the WALL CLOCK:
  monkeypatch `time.sleep` into an accumulator, drive `main()` against an
  always-failing client, assert
  `elapsed + 2*IB_REQUEST_TIMEOUT_S + startup_margin < CAP`. Setting
  `_EQUITY_OPTIONS_CHAIN_TIMEOUT_S = 20.0` in `server.py` must red it.

- **T-266 [P1] The mobile ticket's transmit guard is verified by grepping the
  source for the guard's TEXT, with the substitution written down as the
  rationale.** `web/tests/mobile-ticket-payoff-parity.test.tsx:203-243`:
  `expect(submitBody(sourceOf("components/mobile/MobileOrderTicket.tsx"), "const handleSubmit")).toMatch(/if\s*\(\s*!transmitArmed\s*\)\s*return/)`.
  The header comment argues the invariant is "structural" because React will
  not dispatch to a `disabled` button. It is not: the production bug CLAUDE.md
  cites (2026-08-27) had the guard text PRESENT and still shipped, because
  `handleSubmit` was memoised without `transmitArmed` in its deps and closed
  over a stale `false`. Wrapping `handleSubmit` in
  `useCallback(..., [legs, prices])` leaves all three of these tests green
  while the armed button silently sends nothing. The `inFlightRef` greps have
  the same hole (`inFlightRef.current = true` present but never reset in
  `finally` matches, and permanently bricks the second order), and
  `submitBody` slices from the handler name to the first `fetch(`, so a guard
  MOVED BELOW the fetch still matches.
  **AC:** RED/GREEN — delete the describe block; drive
  `reactOnClick(submit)()` twice in one `act()`, unacknowledged then
  acknowledged, and assert `placeCalls()` is `[]` then exactly 1 with the full
  payload. Wrapping `handleSubmit` in a `useCallback` missing `transmitArmed`
  must red it.

- **T-267 [P1] Market-value fidelity on the ticker detail is asserted by
  grepping a source branch.**
  `web/tests/market-value-multiplier-fidelity.test.ts:114-133` slices
  `PositionTab.tsx`'s combo-quote branch out of the file text and asserts
  `not.toMatch(/mv:\s*spreadPriceData\.last\s*\*/)` plus
  `toMatch(/resolveRealtimeMarketValue/)`. It never renders `PositionTab`.
  `mv: (spreadPriceData.last) * units * mult` reintroduces the R-285 defect and
  evades the negative regex (parenthesised); `mv: rtMv * getMultiplier(position)`
  — double-applying the multiplier the shared walk already applied — satisfies
  BOTH regexes. Either mutation makes a covered call disagree with
  `PositionTable` by the full option notional on a money surface.
  **AC:** RED/GREEN — render `PositionTab` with the existing `COVERED_CALL`
  fixture plus a `spreadPriceData.last`; assert the displayed market value
  equals `resolveRealtimeMarketValue(COVERED_CALL, prices)` (`412500`).
  `mv: spreadPriceData.last * units * mult` must red it.

- **T-268 [P1] Three source greps plus a verbatim hand-copy of the loop under
  test.** `web/tests/rel130-derived-copy-and-null-guards.test.ts:35-67` greps
  `route.tsx` for `"const DEFAULT_LOOKBACK_DAYS = 20"` and
  `"data.tables[key] = kept"`; `:69-101` then defines
  `function truncate(data, max)` — a re-typed copy of `route.tsx:310-320`,
  commented *"The loop verbatim, so the behaviour is tested and not just the
  text."* The clone is what is exercised, so a real edit to `route.tsx:310-320`
  (e.g. `budget -= rows.length` instead of `kept.length`, which re-opens the
  negative-budget path) leaves the clone untouched and both target strings
  still literally present. Separately, `DEFAULT_LOOKBACK_DAYS = 20` is grepped
  as a STRING, and `:52` greps `server.py` for the literal `20` — pinning two
  hardcoded copies of one number rather than proving they agree.
  **AC:** RED/GREEN — extract the loop to `lib/ctaImageLayout.ts` (alongside
  `computeCtaImageHeight`, which the sibling test already imports) and call the
  real export with `{tables:{main:null}}`; `budget -= rows.length` must red it.
  For the constant, import `DEFAULT_LOOKBACK_DAYS`, assert the rendered strings
  contain `String(DEFAULT_LOOKBACK_DAYS)`, and parse the `--days` arg out of
  `server.py` to compare against the imported value.

- **T-269 [P1] A JSX prop's PRESENCE is grepped out of the component source.**
  `web/tests/gate3-unmeasured-book.test.tsx:69-82` slices from
  `src.indexOf("<CorrelationRiskBanner")` to the next `/>` and asserts
  `toMatch(/showUnavailable/)`. `AttributionPanel.tsx:228` renders
  `<CorrelationRiskBanner report={riskBudget} showUnavailable />`.
  `showUnavailable={false}` matches the regex. So does
  `showUnavailable={someFlagThatIsAlwaysFalse}`. So does moving the banner
  behind a `riskBudget?.clusters.length > 0` conditional, because the JSX TEXT
  is unchanged — which is precisely the "no Gate-3 module rendered at all"
  defect the file's own header describes. `CorrelationRiskBanner.tsx:27` gates
  the unavailable branch on `showUnavailable && ...`, so the false-literal
  mutation restores the original bug in full.
  **AC:** RED/GREEN — render
  `<AttributionPanel riskBudget={NOTHING_MEASURED} ... />` and assert
  `getByTestId("correlation-risk-banner")` has `data-level="unmeasured"`.
  `showUnavailable={false}` must red it.

- **T-270 [P1] The outside-RTH modify test asserts a property of its own
  fixture, and never inspects the modify REQUEST.**
  `web/tests/modify-order-outside-rth-init.test.tsx:73-74` builds
  `const order = stockOrder()` and then asserts
  `expect(order.outsideRth).toBeUndefined()` — a literal constructed one line
  above, which can never fail and tests no product code. More seriously all
  three tests stop at `box.checked`; `onConfirm` is a bare `vi.fn()` that is
  never inspected. `ModifyOrderModal.tsx:606-610` builds `request.outsideRth`
  only when `outsideRthChanged`. Delete `:609`, or invert it to
  `request.outsideRth = !outsideRth` — the checkbox still seeds correctly, all
  three tests pass, and an operator un-ticking EXT on a resting GTC order
  silently sends no change (or the opposite one) to IB. The only other coverage
  of that payload is `web/tests/modify-order-ticker-detail.test.ts:106-111`,
  which is itself `expect(contextSource).toMatch(/outsideRth/)` — a source
  grep, not a request.
  **AC:** RED/GREEN — drop `:74`; render with `outsideRth: true`, click the
  checkbox off, confirm, and assert `onConfirm` received exactly
  `{ outsideRth: false }`; plus a paired case where only the price changed and
  `outsideRth` is ABSENT from the request. Deleting
  `ModifyOrderModal.tsx:609` must red it.

- **T-271 [P1] (delta to T-223) All FIVE e2e specs added in this delta are
  absent from the curated CI list, and the job still gates nothing.**
  `ci.yml:541-558` runs a hand-typed list of **14 of 155** spec files,
  byte-identical at base and HEAD. Missing:
  `chain-deck-ticket-scroll.spec.ts`, `leap-order-prefill.spec.ts`,
  `mobile-orders-session.spec.ts`, `orders-session-window.spec.ts`,
  `vixts-tab.spec.ts`. So order prefill, the session window (an order-ROUTING
  attribute), the mobile orders shell and the entire new VIXTS tab have zero CI
  browser evidence. Compounding: the job is titled non-gating (`ci.yml:486`)
  and appears in neither `stage-release.needs` nor `deploy.needs`, so even the
  14 curated specs cannot block a release. T-223 filed the 14/150 ratio; this
  is the delta that shows the ratio is not merely stale but actively diverging
  — five specs written this week, zero adopted.
  **AC:** RED/GREEN — a contract test that reds when `ls web/e2e/*.spec.ts`
  contains a spec absent from BOTH the ci.yml arg list and an explicit,
  reasoned hold-out allowlist (`performance-twr-payload.spec.ts` and
  `account-day-move-ib-daily-pnl.spec.ts` are the existing documented
  hold-outs).

- **T-272 [P1] Two new tests burn 240 REAL 8 ms sleeps each — 2.5 s against
  vitest's 5000 ms default — on the shared CI gate.**
  `web/tests/assistant-stream-and-proposal-guards.test.ts:53-68` and `:71-85`
  both call `streamMessage("m", "y".repeat(400_000), ...)`, and
  `web/lib/chat.ts:359-365` loops `MAX_STREAM_CHUNKS = 240` times with
  `await sleep(8)` on real timers — no `vi.useFakeTimers()` anywhere in the
  file, and `vitest.config.ts` sets no `testTimeout`. Measured on an idle box:
  `2516ms` and `2480ms`. This file runs on the CI vitest gate (8 shards,
  `ci.yml:83-139`), and this repo has documented hard
  `Test timed out in 5000ms` at load average 35-42 (T-161, T-238 — cited in
  `web/tests/portfolio-startup-performance-contract.test.ts:18-26`). A 2×
  overshoot false-reds the deploy gate.
  **AC:** RED/GREEN — `vi.useFakeTimers()` + `vi.advanceTimersByTimeAsync`;
  both tests must still assert `writes.length ∈ (240, 241]` and full-text
  equality, and the file's wall time must drop below 200 ms.

- **T-273 [P1] A live-clock minute-boundary race in the IV-rank header
  assertion.** `web/tests/ivrank-degraded-status.test.tsx:106-115` (mirrored at
  `:99-104`) computes
  `const nowClock = new Date().toLocaleTimeString("en-US", {hour:"numeric",minute:"2-digit"})`
  and asserts `header.textContent` contains it. But the rendered string comes
  from `IvRankPanel.tsx:154` formatting `data.scan_time`, which the fixture
  stamped with a SEPARATE `new Date()` at `:57` inside `buildData()`. Two
  independent live clock reads bracket a full d3 panel render (measured
  53-354 ms). A minute rollover between them is a silent, unreproducible red on
  the shared gate. The new `vitest.config.ts` TZ pin does not help — the race is
  temporal, not zonal.
  **AC:** RED/GREEN — `vi.setSystemTime(new Date("2026-08-26T19:00:00Z"))` and
  derive BOTH the fixture `scan_time` and the expected clock string from that
  one frozen instant. Setting the clock to `...T18:59:59.900Z` and letting the
  render cross the minute must red the current form and not the fixed one.

- **T-274 [P1] The sidecar paging downgrade asserts its own safety in a
  COMMENT, and no test asserts the composition.**
  `scripts/health_service/probes.py:139-144` adds `radon-monitor.service` and
  `radon-newsfeed.service` to `DEPENDENCY_UNITS`, and `:257-261` maps a
  dependency-only `starting` to `degraded`.
  `scripts/watchdog/external_probe.py:94-95` accepts `degraded` as a valid
  recovery sample and `:262-269` grants the P1 emergency only to a validated
  off-box DOWN verdict — so after these two commits a dead or flapping
  `radon-monitor.service` produces NO off-box page. `radon-monitor` is the
  fill / order / journal daemon: a money path. The justification is a code
  comment at `probes.py:133-137` — *"these units already have their own on-box
  alarms."* The two commits' own tests
  (`scripts/tests/test_health_service.py:417`, `:482`) pin the DOWNGRADE;
  nothing pins the claim that makes the downgrade safe. The on-box side is
  tested only for `failed`
  (`scripts/tests/test_watchdog/test_units.py:112-119`,
  `active="failed"`, `result="start-limit-hit"`); there is no
  `active="inactive"` case for a long-lived unit in that file, and no test
  anywhere composes the two subsystems.
  **What ships:** a future edit to `_failed_alert` / `_flap_alert`, or a
  unit-name typo in the watchdog catalogue, makes `radon-monitor` un-alerting
  on-box. Every test in both files still passes — the health tests only assert
  "degraded", the watchdog tests only exercise `failed`. Fills stop being
  recorded and nothing pages from either path.
  **AC:** RED/GREEN — one test that owns the COMPOSITION, driven off
  `probes.DEPENDENCY_UNITS` itself so the frozenset cannot grow without on-box
  cover: for each member assert (a) `aggregate_state` yields `degraded` not
  `down` when only that unit is down, and (b) `units.evaluate` fires for that
  same unit name in `failed` AND in `activating`-flap. Removing the unit from
  the watchdog's alerting catalogue, or adding a sixth member to
  `DEPENDENCY_UNITS`, must red it. (The `_UNGUARDED_CTOR_BASELINE` pattern from
  T-163 is the local precedent.)

- **T-275 [P1] A new API test writes into the repo's REAL `data/` directory,
  breaking the clean-tree precondition both weekend loops depend on.**
  `scripts/api/tests/test_flow_report_capacity_shed.py` (ADDED in this delta)
  POSTs `/flow-analysis/JOBY` at `:80`, `:97` and `:111` and never redirects the
  cache target, while `scripts/api/server.py:2397` defines
  `_FLOW_REPORTS_DIR = DATA_DIR / "flow_reports"` — the real one.
  **Reproduced deterministically in isolation, 0.56 s:**
  `rm -rf data/flow_reports` → clean tree → `pytest <that file> -q` →
  `3 passed` → `git status --porcelain` shows `?? data/flow_reports/` with a
  178-byte `JOBY.json`. `git check-ignore` reports the path is NOT ignored.
  The isolation pattern already exists and is followed correctly by the sibling
  file for the SAME route: `scripts/tests/test_api_flow_cache.py` patches
  `patch.object(server, "_FLOW_REPORTS_DIR", tmp_path)` on all six of its cases
  (`:85`, `:111`, `:139`, `:178`, `:205`, `:212`). The new
  `scripts/api/tests/conftest.py` adds an autouse fixture but only for the
  ib_2fa_lock orphan state.
  **What ships:** (a) running the gate dirties the tree, which is the exact
  precondition that aborted a prior weekend run at pytest COLLECTION
  (2026-08-16 lesson); (b) `deploy.sh` carries a tracked-drift guard and the
  repo already had to untrack `data/tag_taxonomy.json` for runtime mutation
  tripping it; (c) on a developer machine the write seeds a REAL cache entry
  that both `web/app/api/flow-analysis/[ticker]/route.ts:18` and the FastAPI
  GET handler serve, so a test run makes the app show a stub flow report for a
  live ticker.
  **AC:** RED/GREEN — an autouse fixture in `scripts/api/tests/conftest.py`
  pointing `server._FLOW_REPORTS_DIR` at `tmp_path`, plus a contract test
  asserting the repo `data/` tree is byte-identical before and after the
  route-touching cases. Removing the fixture must red the contract test.

- **T-276 [P1] Twenty-seven vitest tests run NOWHERE, and nine of them are
  permanently red.** `ci.yml:143-145` excludes
  `web/tests/integration.test.ts`, `lib/tools/__tests__/kelly.test.ts` and
  `lib/tools/__tests__/runner.test.ts` from every shard (the Bun job has no
  `python3.13`). Locally those three files are the ONLY failures in the whole
  vitest gate: **9 failed / 18 passed in 3.35 s**, deterministic in isolation,
  not load. So the CI-gated set is fully green at 7934 while these 27 tests are
  excluded from CI and red on a developer machine. The frozen PART A audit
  judged two of them GOOD specifically because they spawn real python
  (`TEST_AUDIT.md:633`, `:832`); that judgement is now inoperative. Every
  weekend gate run must hand-attribute these nine, which is exactly how a real
  vitest regression gets waved through.
  **AC:** RED/GREEN — either give the shard a python that can run them (and
  drop the `--exclude` flags), or move them behind an explicit
  `describe.skipIf(!hasPython3_13)` with the reason linked here so they skip
  honestly instead of failing. Do NOT simply delete them: `runner.test.ts`
  carries the 2026-05-22 bare-`python3.13` outage regression (`:92-133`).

- **T-277 [P1] Two live tests pin OPPOSITE `is_blocked()` contracts, and which
  one is right is decided by an ambient environment variable neither
  declares.** The delta deleted the `PYTEST_CURRENT_TEST` escape hatch in
  `scripts/utils/flex_embargo.py:157-178`, replacing it with
  `return bool(os.environ.get("TURSO_DB_URL"))` on the credential-read failure
  path. Now `scripts/tests/test_flex_embargo_fail_closed.py:57-65` (CHANGED in
  the delta) asserts no-sidecar + unreadable store ⇒ `is_blocked() is True`,
  while the UNTOUCHED
  `scripts/tests/test_flex_token_embargo.py:219-231`
  (`test_service_health_outage_fails_open_without_raising`) asserts the
  identical input shape ⇒ `is_blocked() is False`, docstring *"Documented
  fail-open"*. **Reproduced by the lead:**
  `pytest scripts/tests/test_flex_token_embargo.py` → `15 passed`;
  `TURSO_DB_URL=libsql://radon-fake.turso.io TURSO_AUTH_TOKEN=x pytest ...` →
  `1 failed, 14 passed`, `assert True is False`. Production (Hetzner,
  `EnvironmentFile=`) always has `TURSO_DB_URL`, so the untouched test asserts
  the opposite of what production does and is green here only because this
  runner clone has no `.env`.
  **AC:** RED/GREEN — the file must give the same verdict with and without
  `TURSO_DB_URL` in the environment. Add an explicit
  `monkeypatch.delenv`/`setenv` pair so each test STATES which store
  configuration it describes, and invert `:230-231` to the fail-closed verdict
  the delta actually shipped.

- **T-278 [P1] `POST /api/blotter` is now a 404 and the client stopped POSTing,
  so the e2e that exists to prove the refresh is dead-red.**
  `scripts/api/server.py` `blotter_sync()` raises 404 (`26668ef8`),
  `web/app/api/blotter/route.ts:101-112` returns 404, and
  `web/lib/useBlotter.ts:18` sets `hasPost: false` (every POST in
  `web/lib/useSyncHook.ts:177,223,232,250` is gated on it). The UNTOUCHED
  `web/e2e/orders-historical-trades-refresh.spec.ts:154` mocks `FRESH_BLOTTER`
  on POST only (`:136-150`) and asserts `text=2 TRADES` and
  `GOOG 20260320 180C` (`:161-162`), which exist ONLY in `FRESH_BLOTTER`
  (`:58-80`) — `STALE_BLOTTER` has one AAPL trade. The POST branch is
  unreachable, so the spec must time out. Invisible because Playwright is not
  in the gating CI path.
  **AC:** RED/GREEN — rewrite the spec against the GET-only journal-derived
  blotter (or delete it and let `web/tests/blotter-from-journal.test.ts` own the
  contract). It must pass with no POST mock.

- **T-279 [P1] `resolvePreviousSessionClose`'s 7-day gap kills `DayChange` in
  an untouched layout spec, which now asserts a node that cannot render.**
  `bc2649f7` added `web/lib/regimeLiveStrip.ts:130-146`
  (`MAX_PREVIOUS_CLOSE_GAP_DAYS = 7`) and made history authoritative over the
  relay tick close whenever `data.history.length > 0` (`:162-166`), with
  `sessionDate` defaulting to the live `lastCompletedSessionDate()`
  (`RegimePanel.tsx:210` passes none). The UNTOUCHED
  `web/e2e/regime-strip-responsive.spec.ts` mocks 20 history rows dated
  `2026-02-01 … 2026-02-20` (`:43-49`) and asserts
  `expect(vixChangeTextBox).not.toBeNull()` at `:335-346` and `:359-371`.
  `calendarDaysBetween("2026-02-20", lastCompletedSessionDate())` ≈ 190 > 7, so
  `vixClose = null`, `<DayChange>` renders nothing, the testid is absent and
  `boundingBox()` returns null. Same class as T-117/T-248, but PERMANENT rather
  than weekend-scoped, because the fixture dates never move.
  **AC:** RED/GREEN — anchor the mock's history to `lastCompletedSessionDate()`
  (window-relative), or thread `sessionDate` through the page under test. It
  must pass at HEAD and keep passing six months from now.

- **T-280 [P1] `web/tests/regime-day-change.test.ts` is a hand-copied replica,
  so the file NAMED for the subject could not have caught the defect it is
  named after.** `:10-16` is
  `/** Replica of DayChange logic from RegimePanel.tsx */ function computeDayChange(last, close) {...}`
  and the file imports nothing from `web/lib` or `web/components`, while
  `DayChange` is a real exported component (`web/components/RegimeStrip.tsx:32`,
  consumed at `RegimePanel.tsx:716,724,732,752`). The 2026-08-28 incident (VIX
  rendered `-1.89 (-11.35%)` off a stale 16.65 baseline) was entirely a
  BASELINE-SELECTION defect; the arithmetic was never wrong. Nine assertions in
  the file named `regime-day-change` passed throughout.
  **AC:** RED/GREEN — import `resolveRegimeStripLiveState` (or render
  `RegimeStrip`) and assert the day change from a CRI payload + WS price pair
  with an explicit `sessionDate`. The new case must fail at `bc2649f7^` and
  pass at HEAD.

- **T-281 [P1] The ExecStartPre drop-in guard globs `.example` only, and this
  delta shipped the REAL installed drop-ins.** `702ae26a` added five installed
  control-plane artifacts
  (`cloud/services/radon-{api,nextjs,relay,monitor,newsfeed}.service.d/runtime-container.conf`)
  and taught `cloud/scripts/bootstrap-control-plane.sh:142-146,180-184` to
  install them to `/etc/systemd/system/`; `8cd7909b` pinned their hashes into
  `cloud/config/installed-units.sha256:152-156`. But
  `cloud/tests/test_app_plane_cutover_safety.py:91-104` builds its parameters
  from `SERVICES.glob("*.service.d/runtime-container.conf.example")`, so
  `TestDropInsResetExecStartPre` (`:238-252`) and its own anti-decorative
  meta-guard (`:255-`) inspect DOCUMENTATION and never the deployed file. The
  guard's failure message says *"a base-unit ExecStartPre runs as root, against
  production Turso, and 203/EXECs once the host .venv is retired."* All five
  current `.conf` files do reset `ExecStartPre=` (verified, identical sha
  `74dbd6db…`), so this is a coverage hole rather than a live defect — a SIXTH
  unit added without the reset ships green.
  **AC:** RED/GREEN — extend the glob to `*.service.d/runtime-container.conf`
  (both forms) and raise the meta-guard's floor to cover the five installed
  files. Deleting `ExecStartPre=` from one `.conf` must red the suite; today it
  does not.

- **T-282 [P1] `resolve_flows`'s fetch-enabled default is now a TEST-ONLY path,
  so the whole Turso-mirror fallback contract is verified through a branch
  production never enters.** `26668ef8`/`3b7f6ff5` added
  `allow_fetch: bool = True` at `scripts/perf_twr_builder.py:776-780`, and every
  production caller now passes `False` (`build_and_persist` →
  `resolve_flows(..., allow_fetch=bool(sendrequest) and not from_file)`), with
  `POST /performance` and `/performance/background` hard 404s.
  `get_external_flows_for_nav` (`:838-841`) hardcodes the old default and has
  ZERO non-test callers. All five cases in
  `tests/test_perf_twr_flows_turso_fallback.py:66,80,90,100,118` use the
  fetch-enabled default, and `test_a_live_flex_success_never_consults_turso`
  (`:105-120`) asserts a live-Flex success path P2 deliberately killed. The
  REAL path — `allow_fetch=False` with no document, which returns
  `_flows_after_fetch_failure("file_ingest_no_fetch")` (`:812`) and therefore
  also falls back to Turso — has no test. Both files pass (`11 passed`)
  vacuously with respect to shipped behaviour. This is the 2026-08-17 incident's
  contract.
  **AC:** RED/GREEN — parametrize the class over `allow_fetch in (True, False)`
  and add a case calling `resolve_flows(None, allow_fetch=False)` asserting
  `flows.source == "turso"`. A regression in the `file_ingest_no_fetch` branch
  must red it.

- **T-283 [P1] The new leap capacity-shed tests drive a REAL bash+python
  subprocess with real sleeps, and they red under load.** `_run`
  (`scripts/tests/test_leap_capacity_shed_retry.py:169-192`) spawns
  `bash scripts/run_leap_refresh.sh` with `RADON_LEAP_SHED_WAIT_SECS=3` and
  `RADON_LEAP_REFRESH_RETRY_DELAY_SECS=1` against a real `ThreadingHTTPServer`,
  under a 90 s `subprocess.run` timeout; the wrapper
  (`scripts/run_leap_refresh.sh:137-164`) then does a real `sleep "$delay"`
  inside a `SECONDS`-based deadline. **Observed this run** at load ~200:
  `test_persistent_capacity_shed_no_duplicate_still_fails` (`:241`) and
  `test_leap_garch_no_duplicate_scan.py::test_capacity_502_does_not_launch_a_direct_duplicate[leap]`
  both failed `assert 1 >= 2` on `stub.calls == ['/leap/scan']` — the retry
  never fired inside the wrapper's wall-clock budget. Both are green in
  isolation (4 passed in 14.82 s at load 162), so the CONTRACT is right and the
  MECHANISM is load-bound. Distinct from T-136 (that is the `_free_port`
  bind/close/rebind race; here the stub bound fine and served one request).
  **AC:** RED/GREEN — drive the retry ladder without wall-clock waits: have the
  wrapper's sleep honour a `RADON_LEAP_SLEEP_CMD` injection point (or assert
  against a recorded sleep rather than a slept one), so `stub.calls` reaches 2
  deterministically. The persistent-shed contract (`returncode != 0`, no
  duplicate launch, no `fallback` in the output) must still fail when the
  wrapper stops retrying.

- **T-311 [P1] The orders cache-race suite has an INTRA-file `vi.waitFor` race,
  not the cross-file pollution the 2026-08-28 log hypothesised — and it is in
  the CI-gated set.** `web/tests/orders-place-cache-race.test.ts:131`:
  `await vi.waitFor(() => expect(refreshCallCount()).toBeGreaterThan(0))` inside
  the shared `raceGetThroughRefresh` helper (`:126-144`), which every case in
  the file goes through. `vi.waitFor`'s default budget is 1000 ms at a 50 ms
  interval, and it is waiting for the in-flight `POST` route handler to reach
  its `/orders/refresh` call — a scheduling dependency, not a timer.
  **Measured at HEAD, in ISOLATION (no other file in the run):**
  run 1 → `1 failed | 5 passed`, run 2 → `6 passed`. The failure is always the
  same case, `returns the just-placed order even though a concurrent GET cached
  the pre-fill snapshot`, always `AssertionError: expected 0 to be greater than
  0`. The three-file combination the 2026-08-28 log recorded reproduces too
  (2 failed / 27 passed), but the isolated red proves the other two files are
  incidental. It did NOT fire in this run's full vitest gate, which is why it
  reads as pollution: the full run's different scheduling happens to be kinder.
  This corrects the standing "Recorded, not remediated" entry from the
  2026-08-28 remediation, which stated the file was "6-passed ×3 in isolation".
  **What ships:** nothing — the production contract is right. The cost is a
  nondeterministic red on the shared deploy gate that costs a re-run to
  attribute every time it fires.
  **AC:** RED/GREEN — remove the wall-clock dependency. Await the refresh
  deterministically: have the mocked `/orders/refresh` implementation resolve a
  `Deferred` that the helper awaits (so the helper is woken BY the call rather
  than polling for evidence of it), or raise the `vi.waitFor` timeout and prove
  the fix by running the file 20× with zero reds. Reverting to the polled form
  must red under a `setTimeout`-delayed route handler.

### P2

- **T-284 [P2] (delta to T-137) The DIVYIELD sweep-budget `elapsed < 1.0` is no
  longer "a load-margin risk" — it was OBSERVED red this run.** T-137 filed
  `scripts/tests/test_divyield.py` (then `:357-368`, now `:402-421`) as a
  margin risk with the note "3× isolated: 32/32/32 in 1.63/1.33/1.03s — a
  load-margin risk, not an observed flake." This run, under load ~200, it read
  `assert 2.5310465410002507 < 1.0` — a 2.5× overshoot of the bound with
  `SWEEP_BUDGET_S = 0.15` and 0.4 s faked latency, and green again in isolation.
  Promoting the record from predicted to observed; the AC is unchanged from
  T-137 (assert the deterministic observable —
  `errors >= len(tickers) - FETCH_WORKERS`, `len(fetched) <= FETCH_WORKERS`,
  hang call-count — rather than the elapsed time).

- **T-285 [P2] `list_remote_dumps` pagination is never exercised; the stub
  always answers `IsTruncated: False`.** `cloud/scripts/db_backup.py:434-453`
  (the `ContinuationToken` loop) vs
  `cloud/tests/test_db_backup_offbox.py:62-69`, whose
  `_StubClient.list_objects_v2` hardcodes
  `{"Contents": [...], "IsTruncated": False}`. With
  `REMOTE_RETENTION_DAYS = 365` (`:58`) and one dump/day the steady state sits
  under S3's 1000-key page, so the loop only matters once retention lapses or
  the timer fires more often — at which point an unlisted tail is BOTH
  re-uploaded every night by `select_uploadable` AND invisible to
  `select_remote_prunable`, so the remote grows unbounded while burning the
  3600 s upload budget re-sending objects already in the bucket.
  **AC:** page `_StubClient` at N keys with a real `NextContinuationToken`;
  assert all keys are returned across pages and exactly `ceil(n/N)` calls are
  issued. Deleting the `if not resp.get("IsTruncated"): break` continuation
  must red it.

- **T-286 [P2] `cleanup_caches` is the only destructive category with no
  reclaim ceiling, and neither it nor `cleanup_journal` has a test.**
  `cloud/scripts/disk_cleanup.py:642-651` and `:653-659`, against
  `_enforce_ceiling` at `:573` which IS applied to `docker-images` (`:542`) and
  `release-worktrees` (`:606`). `cleanup_caches` root-`rmtree`s every
  `CACHE_TARGETS` entry with no `MAX_PRUNE_PATHS` / `MAX_RECLAIM_BYTES` bound.
  It does route through `remove_tree`, so `is_protected_path` and
  `has_symlink_component` still cover it — the residual gap is blast-radius
  accounting plus the fact that an uncaught `ValueError` from one target
  abandons every LATER target in the list.
  `cloud/tests/test_disk_cleanup.py:183` only asserts the targets are not
  protected; no test calls either function.
  **AC:** point `CACHE_TARGETS` at tmp dirs; assert `cleanup_caches` enforces
  the ceiling like its siblings, and that it continues past a target whose
  removal raises and reports the rest.

- **T-287 [P2] `summarizeSessionWindows` has no test.**
  `web/lib/orders/sessionWindow.ts:193-204`, consumed at
  `web/components/WorkspaceSections.tsx:3104`. Every other export in that file
  is heavily covered by `web/tests/order-session-window.test.ts` (37 cases
  across R-336/337/338/367); the aggregator is the sole exception —
  `grep summarizeSessionWindows web/tests web/e2e` has no hits. Worst case is a
  wrong RTH/EXT count in the orders header, not a wire defect.
  **AC:** feed a mixed row set (STK + outsideRth, OPT, grouped BAG combo) at a
  frozen ET clock; assert `{rth, ext}` and `rth + ext === rows.length`.
  Swapping the increments must red it.

- **T-288 [P2] sFTP host-key PINNING is not enforced; only strictness is, and
  by whole-file substring.** `scripts/flex_sftp_pull.py:38-58` checks
  `REQUIRED_CONFIG` with `line not in text` over the ENTIRE file, and does not
  require `UserKnownHostsFile` at all — despite the module docstring claiming
  "pinned known_hosts" and `85d17d1d` ("S6 host key pinned"). So (a) a config
  whose required directives live under a DIFFERENT `Host` stanza while the
  `ibkr-flex` stanza sets `StrictHostKeyChecking ask` passes validation, and
  (b) a config with the directives but no `UserKnownHostsFile` passes, falls
  back to the systemd user's unmanaged `~/.ssh/known_hosts`, and the operator's
  likely remediation is a manual TOFU append — defeating the pin.
  `_ssh_config` (`scripts/tests/test_flex_sftp_pull.py:21-38`) always writes a
  perfect config, so the missing check is invisible.
  **AC:** `validate_ssh_config` rejects (i) no `UserKnownHostsFile`, (ii) a
  `UserKnownHostsFile` path that does not exist or holds no entry for the
  alias, (iii) `StrictHostKeyChecking yes` appearing only in a comment.

- **T-289 [P2] `_write_gpg`, the atomic writer, is dead code, and downloads
  land non-atomically.** `scripts/flex_sftp_pull.py:98-103` implements
  tmp-write + `chmod` + `replace`; `pull_gpg` at `:106-122` never calls it —
  sftp `get` writes `dest` in place, and there are zero callers repo-wide. A
  download killed mid-transfer (the 120 s `TimeoutStartSec`) leaves a truncated
  `.gpg` that `retain_newest_gpg` counts toward the keep-3 window. No test
  references `_write_gpg`, so neither the dead helper nor the non-atomic path
  is flagged.
  **AC:** a `FakeSftp` whose `get` writes a partial file then raises; assert no
  partial file remains at `inbox/<name>.gpg`. Delete `_write_gpg` if in-place
  is the accepted decision.

- **T-290 [P2] `test_flex_p2_routes.py` never asserts its own stated
  contract.** The module docstring is *"P2: page-driven Flex POSTs are 404.
  GET blotter still reads journal."* All four tests are POST-404 assertions
  (`:24-41`); nothing exercises a GET. `scripts/api/server.py:4302-4308` shows
  the 404s are explicit handlers, so deleting the GET routes entirely leaves
  the file green.
  **AC:** add `test_get_blotter_still_reads_journal` — stub the journal read,
  `client.get("/blotter")`, assert 200 and a journal-derived row.

- **T-291 [P2] The stale-worktree-admin prune is structurally dead, and both
  test files lock it in.** `cloud/scripts/disk_cleanup.py:627` routes admin-dir
  removal through `remove_tree`; `WORKTREE_ADMIN_DIR = LIVE_TREE/.git/worktrees`
  (`:79`), `LIVE_TREE` is the first entry of `PROTECTED_PATHS` (`:187`), and
  `is_protected_path` returns True for any descendant (`:319`,
  `resolved in candidate.parents`). So `remove_tree(admin)` ALWAYS raises
  `ValueError`, is always caught at `:628`, and `pruned_admin` is permanently 0
  while the note reports "pruned 0 stale worktree record(s)" under state `ok`.
  The two suites assert both halves and never join them:
  `test_disk_cleanup.py:194-208` asserts the selector RETURNS the path;
  `test_disk_cleanup_safety.py:171-179` asserts removing that exact path
  RAISES. `.git/worktrees` grows unbounded and the R-370 fix silently became a
  no-op. (This runner currently carries 12 stale worktrees from the 2026-08-27
  run under `/tmp/tw-0827/`, which is the same shape locally.)
  **AC:** an integration test over `cleanup_release_worktrees()` with a fake
  live tree asserting `pruned_admin == 1` for a stale admin dir, forcing an
  explicit allowlisted `.git/worktrees/<name>` carve-out rather than the
  blanket refusal.

- **T-292 [P2] Off-box upload confirmation is byte-count only.**
  `cloud/scripts/db_backup.py:456-459` (`_confirm_upload` returns
  `ContentLength`) and `:516-521` compares it to `path.stat().st_size`. A
  same-length-but-wrong object (a stale multipart reassembly, an overwritten
  key) passes, and `select_uploadable` (`:237`) re-uploads only on a size
  DIFFERENCE, so the corruption is never re-detected while the local original
  ages out. `cloud/tests/test_db_backup_path_and_upload.py:131-172` tests
  short / missing / correct — all by length.
  **AC:** assert `upload_file` is called with `ChecksumAlgorithm`/`ContentMD5`
  (or that `head_object`'s checksum is verified against a locally computed
  digest), and add a case where `ContentLength` matches but the digest does not,
  asserting a raise.

- **T-293 [P2] The chain-deck layout contract parses CSS as TEXT, first
  occurrence only, and is blind to the cascade.**
  `web/tests/chain-deck-layout.test.tsx:105-145` does
  `css.slice(css.indexOf(`${selector} {`), css.indexOf("}", at))`.
  `globals.css` declares `.order-builder--rail` TWICE — at `12820` and again at
  `12849` inside `@media (max-width: 1180px)` — so `ruleBody` returns the first
  block only and the media-query variant is never inspected: adding
  `max-height: 100dvh` there re-opens the "ticket sized off the viewport"
  defect for every viewport under 1180px with the test green. The same applies
  to any later override in the 12k-line file. Separately
  `expect(railBody).toMatch(/flex:\s*1/)` also matches `flex: 1 0 520px`, which
  is the fixed-box defect the sibling test claims to have removed.
  **AC:** delete the CSS-text describe block and let
  `web/e2e/chain-deck-ticket-scroll.spec.ts` (which measures real
  `scrollHeight`/`clientHeight`) own it; if a unit guard is wanted, assert
  `getComputedStyle` in the existing jsdom render. Appending
  `.order-builder--rail { max-height: 100dvh }` to `globals.css` must red it.

- **T-294 [P2] `maxDuration` existence is grepped and its VALUE never read; the
  row cap is self-asserted in a range.** `web/tests/share-image-bounds.test.ts:44-64`
  asserts `src` matches `/export const maxDuration\s*=/`, so `= 300`, `= 0` and
  `= undefined` all pass — the bound the test exists to enforce is never read,
  and the file's own header says the defect is an UNBOUNDED satori render.
  `:38-41` asserts `MAX_IMAGE_ROWS` is `> 0` and `< 5000` — a self-asserted
  range on a constant imported from the module under test, so
  `MAX_IMAGE_ROWS = 4999` passes while asking satori for a ~140,000px canvas.
  (The rest of the file — the `computeCtaImageHeight` literals and the
  font-cache poisoning test — is solid.)
  **AC:** `const route = await import(rel); expect(route.maxDuration).toBeGreaterThan(0);
  expect(route.maxDuration).toBeLessThanOrEqual(60);` and replace the range with
  `expect(computeCtaImageHeight({sectionCount: 3, totalRows: MAX_IMAGE_ROWS})).toBeLessThanOrEqual(MAX_IMAGE_HEIGHT)`.
  `maxDuration = 0` and `MAX_IMAGE_ROWS = 4999` must both red it.

- **T-295 [P2] "The failed-ticker count was lost" is satisfied by the COVERED
  count.** `scripts/tests/test_producer_cycle_coverage.py:100-111` asserts
  `err.get("covered") == 3 and err.get("requested") == 40` and then
  `any(isinstance(v, int) and v > 0 for v in err.values())` with the message
  *"the failed-ticker count was lost"*. The second assertion is ENTAILED by the
  first — `covered == 3` is already an `int > 0` in `err.values()` — so the
  named invariant is not tested. Remove `dropped`/`failed` from the health
  error payload and the test stays green while the watchdog loses the one field
  that distinguishes a 3-of-40 cycle from a 37-of-40 one.
  **AC:** name the key — `assert err["failed"] == 37`. Dropping that key from
  `_record_health`'s error dict must red it.

- **T-296 [P2] Two assertions over the test file's OWN literals.**
  `cloud/tests/test_env_contract_parity.py:234-236` computes
  `blank = sorted(name for name, reason in EXEMPT.items() if not reason.strip())`
  and asserts `not blank`, where `EXEMPT` is a dict literal at `:33-62` of the
  same file — it can only fail if someone edits the test. Same class at
  `scripts/api/tests/test_flow_report_deadline_and_jitter.py:144-148`, which
  asserts two `server.py` constants merely DIFFER: `8.0` vs `8.000001` passes
  while both retry chains stay effectively synchronised, which is the defect
  R-355 names. (Both files are otherwise strong; these are the noise lines.)
  **AC:** delete the `EXEMPT`-reason test — reason strings are review material,
  not runtime behaviour. For the delays assert a real separation:
  `abs(FLOW_REPORT_SHED_RETRY_DELAY_SECS - ORDERS_SYNC_SHED_RETRY_DELAY_SECS) >= 2.0`.

- **T-297 [P2] `_STARVATION_TIMEOUT_SECS = 2.0` turns an event-loop probe into
  a load detector.** `scripts/api/tests/test_ib_health_event_loop.py:36`, used
  at `:44-49` and `:71`. The worker raises `EventLoopBlocked` if a 5 ms-poll
  heartbeat fails to run within 2.0 s wall clock — a genuine assertion about
  the code, with a threshold that is wall clock on a shared host. Under the
  10-way pytest shard matrix (`ci.yml:179-215`) plus a concurrent weekend loop,
  a >2 s scheduling stall blames `check_ib_gateway`.
  **AC:** keep the fact (`ticks["n"] == 1`) but make the release edge-triggered
  — block on `released.wait()` with no timeout and let pytest's own timeout
  catch a true hang — or raise the bound to ≥30 s. The nominal-blocking build
  must still fail.

- **T-298 [P2] `elapsed < 1.0` sits at 1.5× the file's OWN documented
  overshoot floor.** `scripts/tests/test_ib_event_waits.py:147`:
  `mock_ib.sleep.side_effect = lambda s: time.sleep(0.2)` with
  `call_count <= 2`. The docstring at `:127-131` states `time.sleep(0.2)`
  returns in ~0.34 s on this macOS gate (72% overshoot), so the two-step path
  already measures ~0.68 s against a 1.0 s ceiling. It reds for host load,
  never for the bug — `call_count` already carries the contract.
  **AC:** drop the `elapsed` assertion and rely on `call_count <= 2` plus
  pytest's timeout, or raise the bound to 5 s and rename it a hang guard. The
  nominal-step build must still fail on `call_count`.

- **T-299 [P2] The leap stub HTTP server falls through WITHOUT listening and
  says nothing.** `scripts/tests/test_leap_capacity_shed_retry.py:114-127`
  starts a thread, polls `socket.create_connection` for 2 s, and on timeout
  executes a bare `return self` — the context manager yields a server that is
  not accepting, and the test then fails downstream with a `ConnectionRefused`
  naming neither the retry ladder nor the timeout.
  **AC:** replace the trailing `return self` with
  `raise AssertionError("stub HTTP server never listened")` and raise the
  deadline to 15 s. A genuinely broken shed-retry ladder must still fail on its
  own assertions. (Related to T-283, which is the same file's wall-clock
  problem on the other side.)

- **T-300 [P2] `retain_newest_gpg` ordering is manufactured with
  `time.sleep(0.01)` and then asserted exactly.**
  `scripts/tests/test_flex_sftp_pull.py:263-277` writes five files 10 ms apart
  and asserts `remaining == ["2.gpg", "3.gpg", "4.gpg"]`.
  `scripts/flex_sftp_pull.py:171-174` sorts on `st_mtime` and `sorted` is
  stable, so ties fall back to `glob` order, which is `os.scandir` order. On a
  filesystem with coarse mtime granularity (HFS+ 1 s, some NFS/overlay stacks,
  container bind mounts) all five tie and the wrong three survive,
  nondeterministically.
  **AC:** delete the sleep; set explicit mtimes with
  `os.utime(p, (base + i, base + i))`. The test must still fail if
  `retain_newest_gpg` sorts descending or uses `st_ctime`.

- **T-301 [P2] Three independent live-clock reads must agree on "today".**
  `scripts/tests/test_session_open_entry_date.py:29` captures `TODAY_ET` at
  MODULE IMPORT; each `_fill()` (`:59`) re-reads `datetime.now(ET)`; and
  `convert_to_portfolio_format` reads `today = datetime.now(ET)` a third time
  at `scripts/ib_sync.py:1498`. The assertions at `:76`, `:80`, `:105-110` and
  `:128` require all three to be the same ET calendar day, and the
  `scripts-rs` shard is minutes long while this loop's own cycle starts at
  00:00 local. Also: the fixture expiry `"20260828"` (`:32`, `:47`) is already
  in the past.
  **AC:** one module-level
  `NOW_ET = datetime(2026, 8, 26, 11, 0, tzinfo=ZoneInfo("America/New_York"))`
  passed into `_fill(when=...)`, with `ib_sync.datetime` monkeypatched (or a
  `now=` parameter added) so `convert_to_portfolio_format` reads the same
  instant; assertions become `== "2026-08-26"`. Pin the expiry relative to
  `NOW_ET` too.

- **T-302 [P2] The same `bc2649f7` change silently voided an untouched
  live-stream spec's day-change coverage.**
  `web/e2e/regime-live-stream-values.spec.ts:26` supplies a single history row
  hardcoded `date: "2026-03-11"`, 171 days stale, so at HEAD
  `vixClose`/`vvixClose`/`spyClose` all resolve to `null` where before the
  change they used `prices.VIX.close = 24.8`. The spec stays GREEN only because
  its assertions (`:202-212`) cover the COR1M lane, which uses
  `cor1m_previous_close`. The VIX/VVIX day-change render path is now
  unexercised and nobody is told. Sibling case to T-279, which reds; this one
  passes vacuously.
  **AC:** make the fixture window-relative (`date: daysAgo(1)`) and add
  `await expect(vixCell.locator('[data-testid="regime-day-chg"]')).toContainText(...)`.
  Reverting `resolvePreviousSessionClose` to the relay close must change the
  asserted figure.

- **T-303 [P2] jsdom globals installed in `beforeEach` and never restored.**
  `web/tests/use-orders-visibility-backoff.test.tsx:36` assigns
  `global.fetch = vi.fn(...)` (a plain assignment — `vi.restoreAllMocks()` does
  not undo it) and `:65-68` does
  `Object.defineProperty(document, "visibilityState", { get: () => visibility })`,
  permanently replacing the jsdom accessor. The `afterEach` at `:72-75` only
  calls `vi.useRealTimers()` and `vi.restoreAllMocks()`. Contained today by
  per-file jsdom instances, so latent rather than live — it bites the moment
  `isolate: false` is set or a case is appended to this file expecting the real
  accessor.
  **AC:** capture `const realFetch = global.fetch` and the original descriptor
  via `Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState")`,
  restore both in `afterEach`, and add a trailing case asserting
  `document.visibilityState === "visible"` after restore.

- **T-304 [P2] A CSS-class element COUNT stands in for a structural
  assertion.** `web/tests/orders-command-strip.test.tsx:397` asserts
  `strip.querySelectorAll(".orders-command-strip__stat")` has length 4, while
  the surrounding test ("puts RTH/EXT counts next to ORDERS, not on the command
  strip") already makes the real claim via two `querySelector(...)` null checks
  at `:394-395`. The count false-reds on any unrelated stat being added or the
  class being renamed, and goes wrongly green if the RTH chip is added while a
  different stat is dropped.
  **AC:** assert the strip's stat LABELS
  (`["WORKING","PARTIAL","FILLS","LAST SYNC"]`) so a wrong stat, not a wrong
  count, is what fails.

- **T-305 [P2] `test_bootstrap_installs_runtime_wrapper_not_dropins` now
  asserts the OPPOSITE of what bootstrap does, and still passes.**
  `cloud/scripts/bootstrap-control-plane.sh:142-146,180-184` (`702ae26a`) now
  installs five drop-ins, while `cloud/tests/test_app_runtime.py:353-357`
  (UNTOUCHED) asserts `"runtime-container.conf.example" not in text`. The
  assertion is a substring proxy on `.example`, which the new lines do not
  contain, so the test stays green under a name and intent that are now false.
  Verified: `pytest '...::test_bootstrap_installs_runtime_wrapper_not_dropins' -q`
  → `1 passed`.
  **AC:** rename to `test_bootstrap_installs_the_wrapper_and_the_pinned_dropins`
  and assert the five
  `/etc/systemd/system/radon-*.service.d/runtime-container.conf` paths
  POSITIVELY, keeping the `.example` check as the negative. Removing a drop-in
  line from bootstrap must red it.

- **T-306 [P2] The aggregate-recovery fail-closed guard is inert: its
  monkeypatch target no longer gates the branch.** `f7b5eeb9` swapped
  `scripts/watchdog/external_probe.py:183` from `_local_aggregate_is_healthy()`
  to the new `_local_aggregate_clears_offbox_down()` (`:75-96`). The UNTOUCHED
  `scripts/tests/test_watchdog/test_external_probe_deadman.py:225-242`
  (`test_legacy_aggregate_unhealthy_stays_fail_closed`) still patches the OLD
  symbol. Proved by direct exercise: patching the old symbol → `fired: True,
  status: error`; patching the new one → `fired: False, status: healthy`. The
  patch is a no-op. Its purpose was to prove that broadening the reason match
  to the legacy `aggregate_unhealthy` string would NOT pick up the recovery
  bypass; today, if someone broadens `:182`, the unpatched real
  `_local_aggregate_clears_offbox_down()` fails closed in CI (no `:8330`
  listener) and the test passes anyway. Contrast
  `test_watchdog/test_suppression_bounds.py:162,183`, which patches
  `_local_aggregate_is_healthy` and is CORRECT because that symbol still gates
  the deploy-window 5xx branch at `:211`.
  **AC:** patch `_local_aggregate_clears_offbox_down → True`. Widening the
  reason match at `external_probe.py:182` must then red it; today it does not.

- **T-307 [P2] `test_flows_d7_one_flex_fetch_per_distinct_query_id` kept its
  name and lost its subject.** `tests/test_perf_twr_ingest.py:297-307` (CHANGED
  by `26668ef8`) went from asserting
  `len(calls) == len(set(calls))` and `set(calls) == {"1497709"}` to
  `assert calls == []`. The name still advertises the query-id dedup guard —
  the mechanism that stopped a transient 1001 escalating into a 1025 — while
  the body asserts a different property. Dedup now survives only in
  `tests/test_perf_twr_flex_single_request.py:74,89,107`, so a reader grepping
  `test_perf_twr_ingest` for the dedup contract finds a test that no longer
  tests it.
  **AC:** rename to `test_file_ingest_never_sendrequests` and leave dedup to
  `test_perf_twr_flex_single_request.py`. No behavioural change; removes a
  false signpost.

- **T-308 [P2] Fixture drift: `flex_statement_xml(include_transfer_section=)`
  default flipped False → True.** `tests/fixtures/twr_scenarios.py:779`
  (`3b7f6ff5`), to satisfy `scripts/lib/flex_classify.py:69`. Consumers are
  `tests/test_perf_twr_ingest.py:255,287,300,405` only (the other importers use
  the unrelated `flex_xml` helper). Every statement built with the default now
  carries `<Transfers></Transfers>`, so `_transfers_section_warnings()`
  (`scripts/perf_twr_builder.py:728-749`) returns `[]` instead of the
  `FLOWS_TRANSFERS_SECTION_ABSENT` error warning. Contained today; a latent
  weakening of the assertion surface for any test written against the default
  from now on.
  **AC:** make `include_transfer_section` a required keyword with no default,
  forcing each call site to state which statement shape it means.

- **T-309 [P2] `chain-deck-ticket-scroll.spec.ts` hangs on six raw CSS class
  chains and a font-metric-dependent overflow.**
  `web/e2e/chain-deck-ticket-scroll.spec.ts:200-232` locates
  `.asset-deck.open .asset-deck-body`, `.chain-grid`,
  `.chain-rail-main > .chain-expiry-bar`, `.chain-mid.chain-clickable` and
  `.order-builder--rail` with zero `data-testid`, then asserts
  `box.ticketOverflows === true` at viewport 1440×600 — which requires the
  ticket to be taller than 600 px minus chrome, a function of installed font
  metrics and the exact five legs clicked. It also goes wrongly GREEN if the
  deck renders but the ticket collapses to zero legs.
  **AC:** add `data-testid="chain-deck-body" / "order-ticket-rail" /
  "order-ticket-submit"`; keep the overflow claim but derive the trigger height
  from the MEASURED ticket (`viewport = ticketHeight - 100`) rather than a
  literal 600.

- **T-310 [P2] Eleven regime e2e specs exercise only the FALLBACK branch.**
  `web/lib/regimeLiveStrip.ts:162-166` consults the relay `close` only when
  `data.history` is empty, and `web/e2e/regime-day-change.spec.ts:33` mocks
  `history: []` (assertions at `:112-116`), as do ten untouched siblings
  (`regime-cor1m.spec.ts`, `regime-live-index-stream*.spec.ts`,
  `regime-market-closed-eod.spec.ts`, `regime-stale-market-open.spec.ts`,
  `regime-vix-live-badge.spec.ts`, …). The real `/api/regime` payload always
  carries `history: CriHistoryEntry[]` (`web/lib/useRegime.ts:9-18,72`), so
  production never takes the branch these specs cover. Every e2e assertion
  about the VIX/VVIX/SPY day change describes the fallback.
  **AC:** give at least one spec a `history` anchored to
  `lastCompletedSessionDate()` and assert the day change comes from the HISTORY
  close, not the WS close — make the two disagree so the branch is provable.

## Delta audit 2026-08-30

Range `f7b5eeb9..fda36450` — 154 commits, 472 files, +38160/-1917.
164 test-tree paths changed: **66 added, 95 modified, 2 deleted, 1 renamed**
(73 `web/tests`, 41 `scripts/tests`, 27 `cloud/tests`, 7 `web/e2e`,
7 `scripts/api/tests`). New findings continue the frozen numbering at
**T-312**: **34 findings, T-312…T-345 (1 P0, 14 P1, 19 P2)**. PART A (§1–§10) is untouched; nothing above this line was rewritten.

**Base note.** The base is `f7b5eeb9`, yesterday's ledger SHA. The range
therefore contains yesterday's own remediation (PR #140, T-250…T-283 + T-311)
and the reliability loop's REL-131…REL-148 source commits (PR #139); both were
re-triaged as ordinary delta, not exempted, per the 2026-08-22 lesson.

**Pre-flight.** Runner clone verified (`.radon-weekend-runner` present; tree
clean apart from the wrapper's own `.weekend-runner.lock/`). `rtk` is not
installed here, so bare `git` is correct. Toolchain: `node` v24.14.0 via
`~/.nvm`, `vitest` in `node_modules/.bin`, `python3.13` from
`~/radon-weekend/venv` with `pytest 9.1.1` + `pytest-asyncio` +
`pytest-xdist`, `/bin/bash` 3.2.57, and — NEW since yesterday —
`/opt/homebrew/bin/caddy` is present, which moves the darwin cloud baseline
(below). `origin/testing/2026-08-30` did not exist at pre-flight; it was
created and pushed EMPTY immediately. Open PRs at pre-flight: #176 (code-path
map), #126, #125 — none matches a finding subject. Two stale agent worktrees
live INSIDE the clone at `.claude/worktrees/agent-*` (2.1 GB, gitignored, not
collected by either runner; excluded from every sweep). All scratch was
namespaced to `/tmp/tw-2026-08-30/`.

**Today is Sunday 2026-08-30,** so the weekend-dependent class (T-117 / T-248 /
T-273) is LIVE for the second consecutive audit.

**The runner clone gained `web/.env` since yesterday.** Commit `14065b74`
(in this range) provisions `web/.env` into the nightly runner clones; the copy
in this clone carries `TURSO_DB_URL` + `TURSO_AUTH_TOKEN`. That single
environment change is the cause of the pytest round-1 reds — see T-312 — and it
is exactly the class T-277 filed yesterday.

**Load.** Load average was 3.8 at pre-flight and gate round 1 ran BEFORE the
agent fan-out (2026-08-29 lesson): pytest under load 4–5, vitest peaked at 25
(its own workers), cloud at 6–8, and round 1 produced no timing-shaped red.
Round 2 overlapped the reliability loop's vitest in its own clone plus a
`MediaAnalysis` system daemon at 200% CPU (load 255) and produced three, all
attributed by isolation — see the table.

### Standing sweeps

**Gates, two full serial rounds from the repo root (clean tree, HEAD
`fda36450`).** Round 1 ran BEFORE the agent fan-out at load 4–8; round 2 ran
while the reliability loop's own vitest was executing in its clone and the
host hit load average **255** (a `MediaAnalysis` system daemon at 200% CPU on
top), so round 2's vitest reds were attributed by isolation before being
called anything.

| Gate | Round 1 | Round 2 | Isolated re-run |
|---|---|---|---|
| `python3.13 -m pytest` (recursive) | **22 failed** / 8973 passed / 1 skipped / 90 deselected, 376 s | **22 failed** / 8973 passed, 406 s — identical FAILED list | the five files: `22 failed, 35 passed in 0.86s` with creds visible, `57 passed in 1.59s` with creds masked (T-317) |
| `npx vitest run` | 819 files / **1 failed** / 8265 passed, 95 s | **3 failed** / 8263 passed, 283 s under load 255 | r1: `chain-prefill-expiry-contract` `3 passed` ×3 (T-321, race); r2: `integration` 12/12, `portfolio-startup-performance-contract` 8/8, `regime-route-cache-selection` 2/2 — all three were bare timeouts (5009 / 5016 / 20013 ms) in unrelated files = load, T-238 class |
| `python3.13 -m pytest cloud/tests` | **35 failed** / 1426 passed / 6 skipped, 261 s | **35 failed**, FAILED list byte-identical to r1 | see the darwin baseline |

**The pytest red is one deterministic cluster, not load.** All 22 are in five
Flex-embargo files, reproduce in 0.86 s in isolation, and flip to green when
`TURSO_DB_URL`/`TURSO_AUTH_TOKEN` are masked. CI's `scripts-ac` and
`scripts-daemons` shards were green at `92278f6a` because CI has no
credentials. Filed as **T-317**; it is the T-277 class recurring in files
T-277's fix did not touch, triggered by `14065b74` provisioning `web/.env`
into this clone.

**The darwin cloud baseline moved 37 → 35 and its composition changed.**
`/opt/homebrew/bin/caddy` is now installed here, so yesterday's three
deliberate `test_caddy_edge_timeouts.py` reds are GONE and the 29-test edge
mechanism class runs green on this host. One NEW red appeared:
`test_integration.py::TestSecurity::test_no_real_secrets_in_tracked_files`,
which is a test defect, not environment (**T-325**). Recorded FAILED list, by
file: 21 `test_ib_gateway_control.py` + 13 `test_bootstrap_control_plane.py`
(both `/bin/bash` 3.2: `mapfile`, `exec {fd}<>`) + 1 `test_integration.py`
(T-325) = **35**. Installing bash ≥ 4 moves the first 34; landing T-325 moves
the last one.

**`git status --porcelain` after every gate run** (the 2026-08-29 rail): only
the wrapper's `.weekend-runner.lock/` at every checkpoint, across both rounds
and the determinism runs. No test wrote into the checkout this time (T-275 is
holding). The only checkout artifact the gates produce is `__pycache__`
bytecode — which is exactly what T-325 trips on.

**Determinism.** The delta touched 164 test-tree paths (95 modified), so the
"re-run 3× ONLY the touched files" rule again collapses into the full gate
(2026-08-16 lesson); the two full rounds above are that check for the
modified set. The 66 ADDED files were run 3× each in isolation as a scoped
set: vitest 27 files / 239 tests `3 passed` ×3 (green 3/3); pytest 24
`scripts/`+`scripts/api` files `357 passed` ×3; cloud 6 files `80 passed` ×3.
(The first attempt put the cloud files in the same invocation as
`scripts/tests` and died at collection on
`ImportPathMismatchError: tests.conftest` — the two roots cannot share one
pytest process, which is also why CI runs them as separate jobs.)

**Collection union (T-122 sweep).** `pytest --collect-only -q` over the tree
= **8996** items across 532 files; the union of CI's ten `py-tests` shard
globs = the same 532 files; CI's shard pass counts at `92278f6a` sum to 8995
passed + 1 skipped = **8996**. Cloud: 44/44 files, 1467 collected locally vs
CI's 1463 + 3 skipped = 1466 — the one-test gap is `2f46a166` adding a test
to `test_app_images.py` after that run. Vitest: 819 files / 8266 tests
locally = CI's eight shards (921+970+970+1216+1053+986+996+1136 = 8248 passed
+ 18 skipped = **8266**). No file is unreached. The new `edge` cloud shard's
`omit:` arithmetic is pinned by
`test_ci_deploy_concurrency.py::test_cloud_shard_union_equals_recursive_collection`
(agent F), so T-122 holds on all three gates — but see T-322 for what the
split did to a caddy-dependent class that stayed on `al`.

**Enforcement.** `deploy.needs` grew 9 → 14 (`app-images`, three
`contract-*` jobs, `prepull-images`) and `stage-release.needs` 8 → 12; both
coverage ratchets remain in both. `deploy.if` still accepts `skipped` for
every test job, which is what T-312 exploits, and accepts
`prepull-images.result == 'failure'` (fail-closed inside `deploy.sh:381-393`
— agent F verified the synchronous re-pull aborts before teardown). `main`
still has NO `required_status_checks` (`gh api
repos/{owner}/{repo}/branches/main/protection` returns none; `rules/branches/main`
is `[]`) — T-222 re-confirmed for the fourth audit running. `e2e-financial-smoke`
still gates nothing (T-223/T-271 class).

**CI was red for 52 minutes inside this range, three times over, and the
gate held every time.** Runs `33292268590`…`33293579469` (04:20–05:12Z,
2026-08-30): gitleaks on #184's `TWS_PASSWORD` fixture (fixed by `7016f982` +
`41b0f596`), `test_docker_compose_ports_bound_to_localhost` on #178's
`${IB_GATEWAY_TAILSCALE_BIND:-…}` bind (fixed by `41b0f596`), then five
`caddy is not on PATH` reds after `44683993` wrote `--ignore=` INSIDE a
pytest positional argument (pytest ignores it for explicit targets; fixed by
`92278f6a`'s `omit:` logic). `Deploy to VPS` was skipped on all nine runs.
This is recorded because T-312 makes the window matter: any `.md`-only push
during it would have deployed.

**Ratchets.** `--fail-under=56` unchanged (`ci.yml:392`); vitest thresholds
75/71/65 identical at base and HEAD; `coverage.exclude` untouched; the only
`vitest.config.ts` change adds `scripts/mktnews/**/*.test.js` (7 files, 47
tests, outside `coverage.include` like `scripts/lib`); `--expect-shards 8`
matches the matrix. Nothing moved.

**Skips.** Seventeen `+` lines match the skip/only/xfail scan; excluding
`TEST_AUDIT.md` prose and `sys.exit`/`process.exit` false positives: eight
`it.skipIf(!hasPython313())`/`describe.skipIf`/`test.skipIf` in
`lib/tools/__tests__/{kelly,runner}.test.ts` and `web/tests/integration.test.ts`
(T-276's by-name skips, each carrying `python313Label`), and two
`@pytest.mark.skipif(os.environ.get("RADON_SKIP_CADDY_E2E") == "1")` in
`cloud/tests/test_caddy_edge_timeouts.py:467,716` whose reason names T-205.
All linked. No `.only`, no `xfail`, no `@unittest.skip`. The unlinked skip
that matters is the one the delta CREATED without adding a marker: T-322.

### Re-triage of the standing NEW_FINDINGS items and yesterday's appendix

- **E2E testid backlog** (open) — unchanged; agent E confirms every NEW e2e
  locator in this delta uses `data-testid` except one bounding-box ordering
  check (`flow-analysis-ticker.spec.ts:340`).
- **`next dev` in the CI Playwright container** (open, infra) — unchanged.
- **`pytest cloud/tests` darwin baseline** — re-recorded above (35; list by
  file). The NEW_FINDINGS text still says "10-red … `sha256sum`"; that has been
  stale since 2026-08-29 and the skill's Lessons already say so.
- **`performance-twr-payload.spec.ts`** and **Day Move dev/prod divergence** —
  both are documented hold-outs in `web/e2e/ci-curation-ledger.txt` now;
  unchanged.
- **Six producers outside a health block** (T-163 class, open) — untouched by
  this delta.
- **Yesterday's remediation appendix, items 1–4:** (1) `useOrderRisk.ts:741`
  → numbered **T-345**. (2) `resolveEntryCost` still a `number` → the
  test-side evidence is **T-315**. (3) divergent `IB_FLEX_FLOWS_QUERY_ID`
  semantics — still an operator decision; both pins are green and name each
  other; not numbered. (4) e2e non-gating → T-222/T-271 class; re-confirmed
  above, not renumbered.
- **`orders-place-cache-race.test.ts`** (T-311, DONE yesterday) — green in
  both full rounds and never in the isolation set; holding.
- **Weekend false-red class (T-117 / T-248 / T-273)** — LIVE today (Sunday)
  and did NOT recur as a weekday gate; the delta adds no new
  `isMarketOpen`/`getDay` gate (agent E swept the source hunks). It recurred
  in a clock-INDEPENDENT form instead (**T-339**) and as a calendar cutover
  (**T-318**, red from Tuesday).

### Method

Six read-only agents (A: python/cloud source without tests; B: web +
mktnews source without tests; C: net-negative python tests; D: net-negative
web tests; E: fragile mechanisms + blast radius; F: gate drift), launched
AFTER gate round 1 drained, each scoped to the diff plus its blast radius
with `.claude/worktrees/` excluded. The lead reproduced or re-read the cited
lines of every P0/P1 before numbering (T-312 `select_gates` + HEAD run jobs;
T-313 the three sites; T-314 lock path + binds; T-315 four `positionUtils`
sites + fixture; T-316 `:393-403`; T-317 end to end; T-318 source + test;
T-319 config + test; T-320 owner + grep; T-321 both effects + hook; T-322
skipif + job summaries; T-325 in-process; T-345 both `okToSubmit` sites).
Convergences: T-313 (A+C), T-314 (A+C), T-325 (lead+C+E+F), T-328 (A+B+D),
T-326 (A+C). Findings are numbered FIRST and the prose written after
(2026-08-22 lesson); every `T-3xx` above was grepped back to its subject.

### P0 — the gate itself

- **T-312 [P0] A documentation-only push to `main` deploys the PREVIOUS tip's
  runtime tree with every test gate skipped, and the path filter's base is
  `github.event.before`, not the last SHA whose gate passed — so a docs commit
  after a red `main` ships the red tree as a green release.**
  `.github/workflows/ci.yml:82` sets `BASE_SHA: github.event.pull_request.base.sha
  || github.event.before`; `scripts/ci/path_filter.py:253-256` classes every
  `.md` as documentation, `:284-285` returns `(False, False)` when nothing else
  changed, `:306-316` diffs `before...head` only; `ci.yml:826-844` (`deploy.if`)
  accepts `skipped` for every test and coverage job; `:993` runs `deploy.sh
  "$SHA"` on the docs commit's full tree. Reproduced in-process:
  `select_gates(["docs/x.md"])`, `["CLAUDE.md"]`, `["TEST_AUDIT.md"]` all →
  `GateSelection(python=False, web=False, contract_tests=())`. **LIVE at this
  audit's HEAD:** `fda36450` touches only `tasks/todo.md`; run `33295066378`
  logged `python=False web=False contracts=False files=1`, every Vitest / pytest
  / cloud / perimeter / contract / coverage job is `skipped`, and `Prestage VPS
  release`, `Prepull exact app images`, `Deploy to VPS` are all `success`. Its
  base `2f46a166` happened to be green. Ninety minutes earlier `main` was red
  for 52 minutes (runs `33292268590`…`33293579469`, 04:20–05:12Z: gitleaks on
  #184's fixture, then `test_docker_compose_ports_bound_to_localhost` on #178's
  `${VAR:-default}` bind, then five `caddy is not on PATH` reds after `44683993`
  put `--ignore=` inside a pytest path argument) and every one of those runs
  correctly SKIPPED deploy — a `.md`-only push during that window would have
  deployed the identical red runtime with a green run. The filter's premise
  ("a range that touches no runtime file cannot regress the gate") silently
  assumes the base was green. Force-push and all-zero `before` are fail-closed
  (`:307-308` → `[]` → both gates on; an unreachable `before` fails `changes`).
  **AC:** RED — a `scripts/tests/test_path_filter.py` case where the base's
  gate conclusion is not `success` (a `last-green-sha` marker the deploy job
  writes, or the Deployments API for `Production`) must yield
  `python=true web=true`; today it yields `false/false`. GREEN — `changes`
  resolves the base as the last SHA with a successful deploy and falls back to
  `(True, True)` when unknown. Reverting to `event.before` must red it.
  *(Agent F; lead re-ran `select_gates` and read the HEAD run's job list.
  Forward-path twin of T-254; T-159 fixed only the skip-prefix half.)*

### P1

- **T-313 [P1] An exit order the R-427 limit refuses is never placed, retried
  every 5 minutes, and `exit-orders` heartbeats `ok` — and the file that
  claims to prove the funnel never calls the handler.**
  `scripts/monitor_daemon/handlers/exit_orders.py:687-706`: on a
  `check_order_limits` violation the branch increments `orders_failed`,
  appends to `failed`, and `continue`s — it never sets `result["error"]`,
  while the sibling not-acknowledged branch at `:714-726` does;
  `handlers/base.py:216-227` records `error` only when `result["error"]` is
  truthy, else `ok`. Reproduced by driving `ExitOrdersHandler().execute()`
  with a stub client and one pending leg of `max_order_qty()+1`: `placed: []`,
  `orders_failed: 1`, `result['error']: None`, heartbeat `ok`. The position
  sits unprotected with a green watchdog.
  `scripts/tests/test_rel145_order_limits_at_every_funnel.py:57-78`
  (`test_an_over_cap_quantity_never_reaches_ib`) builds a `_Client` and a
  `placed` list, `monkeypatch.setattr(mod, "_refuse_over_limit", …,
  raising=False)` on an attribute that does not exist, calls
  `check_order_limits(...)` DIRECTLY and asserts `placed == []` — nothing could
  ever append to it; `execute()` is never invoked. `:80-95` and `:98-108` are
  `body.index("check_order_limits") < body.index("client.place_order(")`
  greps: deleting the `continue` at `:706` (order placed anyway), deleting the
  `return` at `scripts/ib_order_manage.py:286`, and `violation = None and
  check_order_limits(...)` all leave the file `11 passed`.
  **AC:** RED — replace `:57-78` with a test that instantiates the handler,
  stubs `IBClient`/`_load_pending_orders`/`_can_place_order`/`_is_halted`,
  runs `execute()` with `contracts = max_order_qty()+1`, and asserts
  `place_order` never called, `orders_failed == 1`, AND `result["error"]`
  truthy naming the limit (red today on the third). GREEN — set
  `result["error"] = violation["message"]` mirroring `:721`. Deleting the
  `if violation:` block must red it. *(Converged: agents A and C independently;
  lead re-read the three cited sites.)*

- **T-314 [P1] The R-423 `cri_scan` advisory lock lives in `/tmp`, which the
  containerised FastAPI and the host `radon-refresh` timer do not share, so
  the double-scan it was written to prevent is still open in production — and
  the tests assert the words `fcntl` and `LOCK_NB` appear in the file.**
  `scripts/cri_scan.py:1565` `CRI_SCAN_LOCK_PATH = "/tmp/radon-cri-scan.lock"`,
  `:1569-1583` `_acquire_scan_lock`, `:1641-1655` cache-or-`exit(75)`. The
  timer side runs on the HOST (`cloud/services/radon-refresh.service:17,20` →
  `scripts/data_refresh.py:193`); the browser side runs INSIDE the container
  (`cloud/services/radon-api.service.d/runtime-container.conf`,
  `scripts/api/server.py:3437`); `cloud/scripts/radon-app-runtime.sh:358-360`
  binds only `data/`, `media/` and `ib-lease/` — `/tmp` is a different inode
  on each side, while `data/cri.json` IS shared. A `/regime/scan` POST during
  the 15-minute refresh fire still runs a second 180 s scan against the same
  file and the same IB client-id range with CI reporting the lock present.
  `scripts/tests/test_rel148_operability_fixes.py:75-81` asserts `"fcntl" in
  body or "flock" in body`, `:83-86` `"LOCK_NB" in source` (raw file, comments
  count); changing `except OSError: return None` at `cri_scan.py:1579-1580` to
  `pass` (lock failure swallowed, second scan proceeds) stays green. Same file
  `:90-99` asserts `_record_scan_gate_saturation` appears inside
  `_scan_gate_for`'s text — wrapping the call in `if False:` passes; nothing
  drives `_scan_gate_for` past `MAX_SUBJECT_SCAN_GATES`.
  **AC:** RED — (1) with `RADON_CRI_SCAN_LOCK=<tmp>` flocked by the test, run
  `cri_scan.main()` `--json` with a fresh `data/cri.json`: assert the cached
  payload is printed and no IB client is constructed; with no cache assert
  `SystemExit(75)`; (2) assert `CRI_SCAN_LOCK_PATH` is under a path
  `radon-app-runtime.sh` binds — red today; (3) fill `_SUBJECT_SCAN_GATES` to
  the cap, call `_scan_gate_for("x","new")`, assert the stubbed
  `_write_scan_gate_saturation_row` fired once. GREEN — move the lock under
  the shared data dir. The `except OSError: pass` mutation must red (1); `if
  False:` must red (3). *(Converged: agents A and C.)*

- **T-315 [P1] T-253's client remediation is partial: the P&L cell, the
  portfolio P&L total, the unrealized breakdown, Today P&L and the CLOSE-TICKET
  realised figure all still derive from the blended `resolveEntryCost`, and the
  new test only proves the cells it fixed because its fixture has no marks.**
  `web/lib/positionUtils.ts:129-131` `hasBlendedLegBasis`; `:133-141`
  `resolveEntryCost` sums `legs[].entry_cost` with no mixed check; `:377-384`
  `getPnlDollars = mv - resolveEntryCost(pos)`; `:308-312` only
  `resolveReturnCapital` gates on mixed; `:724-729` Today P&L same-day branch.
  `web/components/PositionTable.tsx:412-413,556-559` renders `pnl` unguarded
  while `:509-510,547-553` suppress Avg Entry / Entry Cost / Initial Value;
  `MetricCards.tsx:624-628` sums the blend into the portfolio card;
  `web/lib/unrealizedBreakdown.ts:30,52`; `web/lib/order/positionTrade.ts:320`,
  `ModifyOrderModal.tsx:426`, `ticker-detail/OrderTab.tsx:869` feed
  `entryCostDollars` into `useOrderRisk.ts:712-715,808` as `pnl = proceeds -
  entryCostDollars` on the confirm gate. Reproduced out-of-repo against the
  landed tree: `resolveEntryCost(MIXED) === 1000`, `getPnlDollars(MIXED, 4500)
  === 3500`; `PositionTable` rendered with the test's own MIXED fixture plus
  leg marks shows Entry Cost `—` and P&L `+$3,500` on the same row.
  `web/tests/position-mixed-basis-refuses-aggregate.test.tsx:55-68` sets
  `market_value: null` on the position AND every leg, so `resolveMarketValue`
  (`positionUtils.ts:85-93`) is null and the P&L branch is unreachable; the
  `:145-149` assertion `not.toContain("$1,000")` is satisfied by `mv == null`,
  not by any guard. This is yesterday's remediation appendix item 2
  (`resolveEntryCost` still returns a `number`), now with the test-side
  evidence. **AC:** RED — give the fixture leg marks; assert the row's P&L
  cell is `—` and `pnl_pct` `N/A`; assert `computeUnrealizedBreakdown` omits
  or flags the row and the `MetricCards` total excludes it; assert a pure
  combo close of a mixed position yields `closeOut: null` (or a
  `basisUnavailable` marker) so the ticket renders no realised figure. GREEN —
  gate `getPnlDollars`/`resolveEntryCost` on `hasBlendedLegBasis`; deleting
  the gate must bring `+$3,500` back. *(Agent B; lead re-read the four cited
  `positionUtils.ts` sites and the fixture.)*

- **T-316 [P1] (new angle on T-184) R-383's identical-cash carve-out turns a
  silently DROPPED fill into a silently PUBLISHED wrong realised P&L, and
  `test_rel136_taint_scope.py` pins the no-warning as the contract.**
  `scripts/clients/journal_realized.py:395-402`: `if same_direction: if not
  cash_matched: basis_tainted = True` — a same-direction cross-writer
  suppression at equal cash is exact for the AVERAGE only until a later
  opening fill blends against the short-by-one inventory;
  `:484-499` collapses on `(contract, date, signed_qty)` + disjoint id
  namespace, so a SECOND equal-price 10-lot partial from Flex is suppressed as
  a duplicate of the daemon's one. Agent C ran it in-process at HEAD: api BUY
  10@1.00, flex BUY 10@1.00, flex BUY 10@1.00 (distinct `exec_time`), BUY
  10@3.00, SELL 20@5.00 → `realized_pnl_by_exec_id` publishes `{'c1':
  6000.0}`; true basis (20×1+10×3)/30 = 1.667 → 6666.67; replay position 0 vs
  true 10 long; no warning (the identical-cash path logs at `info`).
  `scripts/tests/test_rel136_taint_scope.py:58-72` pins `{"c1": 3000.0}` WITH
  the caplog-empty assertion; `:101-126` (R-406) pins that the flat-position
  reset clears the taint, so the next cycle publishes on top of the wrong
  inventory. T-184's AC said "if the drop is a deliberate tradeoff, the test
  must assert the drop AND that the contract is marked incomplete"; this
  delta did the opposite. **AC:** RED — a five-row case as above expecting
  `c1` withheld (or `{}`) plus a WARNING, not `6000.0`. GREEN — either restore
  `basis_tainted = True` for any same-direction cross-writer suppression, or
  keep the carve-out but taint on the NEXT same-direction fill after a
  suppressed open, and change `:58-72` to assert the warning. Deleting `if
  not cash_matched:` at `:400` must red the new test. *(Agent C; lead read
  `:393-403`. This re-opens a deliberate R-383 decision — the reliability
  loop should weigh in before the carve-out is reverted; the AC's second
  option keeps it.)*

- **T-317 [P1] (T-277 recurring in five files) Provisioning `web/.env` into
  the runner clone (`14065b74`) flips 22 CI-green pytest tests red, and the
  provisioning commit's own contract test pins the false premise that made it
  look safe.** `scripts/cash_flow_sync.py:95-97` loads `.env`, `.env.ib-mode`
  and `web/.env` at IMPORT (May 2026, unchanged); `scripts/utils/flex_embargo.py:157-178`
  `_durable_store_available` = "Turso creds readable", `:181-190` `_health_rows`
  raises `FlexEmbargoStoreUnavailable` on a read failure with creds present,
  `:227-246` `active_until` then fails CLOSED for `UNKNOWN_STATE_BLOCK_HOURS =
  1.0`; `scripts/db/hrana_http.py:135` is the pytest guard that refuses every
  real Turso connection. Round 1: `22 failed` — `test_cash_flow_sync_flex_errors.py`
  (10), `test_monitor_daemon/test_cash_flow_sync_cadence.py` (7),
  `test_corrupt_state_preserves_embargo.py` (3),
  `test_cash_flow_sync_timeout_retry_budget.py` (1),
  `test_cash_flows_route_last_synced.py` (1), every one `FlexTokenLocked:
  Flex token locked until <now+1h>` or the `urlopen.call_count == 0` that
  follows. Deterministic: the five files are `22 failed, 35 passed in 0.86s`
  with the clone's `web/.env` visible and `57 passed in 1.59s` with
  `TURSO_DB_URL= TURSO_AUTH_TOKEN=` masked (python-dotenv does not override an
  existing key). Yesterday's round 1 in this clone had zero failures here
  (`/tmp/tw-2026-08-29/gates/pytest-r1.txt`); `web/.env` was copied in by
  `setup_testing_weekend.sh` after `14065b74` merged. A `load_dotenv` spy
  names `cash_flow_sync.py:97` as the only loader that sets `TURSO_DB_URL`
  during the import — and twenty-one more `scripts/*.py` producers carry the
  identical three-line block (`bpi_scan.py:43`, `cri_scan.py:53`,
  `fetch_dispersion.py:42`, `fetch_credit_spread.py:44`, …).
  `scripts/setup_testing_weekend.sh:116`, `scripts/setup_reliability_weekend.sh:116`
  and `scripts/tests/test_weekend_runner_env_provisioning.py:169` all state
  "web/.env is read by Next, never by pytest"; `:164-180` pins it by checking
  that the ROOT `.env` is not copied — true and irrelevant. None of the 22
  tests isolates `flex_embargo`, so their verdict is a property of the HOST:
  green in CI (no creds), red on the operator's laptop and the runner, and on
  Hetzner the branch they never deliberately exercise is the live one.
  **AC:** RED — with a `web/.env` carrying a Turso URL + token,
  `python3.13 -m pytest scripts/tests/test_cash_flow_sync_flex_errors.py` must
  be green (today `10 failed`). GREEN — an autouse fixture in
  `scripts/tests/conftest.py` that `delenv`s `TURSO_DB_URL`/`TURSO_AUTH_TOKEN`
  (or patches `flex_embargo._durable_store_available` to `False`) for every
  test not explicitly about the durable store, ONE deliberate test of the
  fail-closed path THROUGH `cash_flow_sync` with the store patched to raise,
  and the two shell comments plus the provisioning test corrected to name the
  22 loaders. Removing the fixture with creds present must red. *(Lead:
  traced, reproduced both directions, blamed.)*

- **T-318 [P1] `test_flex_sftp_pull.py:411-412` goes deterministically red on
  the CI-gated path from ET Tuesday 2026-09-01 — in two days — because R-389
  made a duplicate-only re-pull an error after the cutover and the untouched
  test asserts `code_again == 0` on the live clock.**
  `scripts/flex_sftp_pull.py:38` `FIRST_DELIVERY_DATE = date(2026, 8, 31)`;
  `:303-308` `empty_remote_is_expected(now)` reads
  `datetime.now(ZoneInfo("America/New_York"))` when `now` is None; `:394-395`
  (R-389) a `duplicate` outcome no longer counts as `ingested`; `:410-416`
  `if not ingested and not empty_remote_is_expected(now): … return 1`. The
  second `pull.run(...)` at `test_flex_sftp_pull.py:411` passes no `now=`, so
  from Tuesday it returns 1 and `:412 assert code_again == 0` fails. Agent C
  replayed the test body with the clock pinned to 2026-09-01 08:00 ET:
  `FAILED … :412: assert 1 == 0`; `1 passed` at HEAD on today's clock.
  `test_rel146_flex_sftp_honesty.py:169-176` already pins the post-cutover
  contract at `now=2026-09-02`, so the product is right and the gate is the
  defect: the python job reds on every push from Tuesday and "flaky, retry"
  will not clear it. **AC:** RED today with `now=datetime(2026, 9, 1, 8, 0,
  tzinfo=ZoneInfo("America/New_York"))` on both `pull.run` calls at `:400`
  and `:411`. GREEN — the second run asserts the R-389 contract
  (`code_again == 1`, last heartbeat `error`) under a post-cutover `now=`, or
  passes a pre-cutover `now=` if the test's only point is the default-ingest
  wiring; pin `now=` on every `pull.run` in the file that lacks one (`:183,
  :200, :221, :248, :268, :331, :359, :400, :411`). *(Agent C; lead read
  `:36-39, :303-308, :408-416` and `:398-413`. Class of T-117/T-210 — a
  source change made an untouched test date-dependent.)*

- **T-319 [P1] The R-405 "ET, not process-local" tests run under the suite's
  `TZ=America/New_York` pin, which makes ET and process-local indistinguishable,
  so they cannot red the regression they are named for — and the one case
  that claims to pin the mechanism asserts a function's source has non-zero
  length.** `vitest.config.ts:62` `env: { TZ: "America/New_York" }`;
  `web/tests/rel142-day-change-anchor-and-tz.test.ts:93-96` says so in its own
  comment ("Under the suite's TZ pin the old code answered ET"); `:112-117`
  `expect(String(formatHoldDuration).length).toBeGreaterThan(0)`;
  `web/lib/holdTime.ts:20-46` docblock: "The pin made TZ-independence
  untestable rather than guaranteed". Agent D ran the four `:99-110`
  assertions against a mutated `holdTime.ts` with process-local getters (the
  pre-R-405 code): `TZ=America/New_York` → all 4 PASS; `TZ=UTC` →
  `isEarlierLocalDay("2026-08-28", "2026-08-29T01:00:00Z")` flips to `true`.
  On the UTC Hetzner runtime that is a same-day exit classified as a
  prior-day entry (entry-before-exit rejection and hold-duration copy
  diverge from what the suite proved). Same class:
  `web/tests/flow-report-staleness.test.ts:132-139` ("dates the report in
  market time, not UTC") against `web/lib/flowReportStaleness.ts:76-77`.
  **AC:** RED — wrap the "trading-day classification is ET" block in
  `beforeAll(() => { process.env.TZ = "UTC"; })`/`afterAll(restore)` (Node
  re-reads `TZ`), or run it under a vitest project whose `env.TZ` is
  `Pacific/Kiritimati`; delete `:112-117`; the process-local mutation must
  red, HEAD stays green. Apply the same to `flow-report-staleness:132-139`.
  *(Agent D; lead read `vitest.config.ts:62` and `rel142:93-117`. Agent B's
  "deterministic regardless of process TZ" note is true of the test's
  RESULT, not of its power.)*

- **T-320 [P1] (delta to T-270) The modify ticket is now asserted at
  `onConfirm`, but the component that OWNS the `/api/orders/modify` fetch is
  still only source-grepped, so the EXT flag can be dropped on the wire with
  every test green.** `web/lib/OrderActionsContext.tsx:276-281`: `const {
  outsideRth, ...requestBody } = request; fetch("/api/orders/modify", {
  method: "POST", body: JSON.stringify({ orderId, permId, ...requestBody,
  outsideRth }) })`. `web/tests/modify-order-outside-rth-init.test.tsx:59-70,
  115-118, 126-127, 135-140` assert only `onConfirm.mock.calls` (imports
  `ModifyOrderModal` alone, `:7-11`); the only owner-level coverage is
  `web/tests/modify-order-ticker-detail.test.ts:110-112`
  `expect(contextSource).toMatch(/outsideRth/)`; no file under `web/tests`
  references both `"/api/orders/modify"` and `outsideRth` (lead grep).
  Mutating `:281` to omit the key, or to `outsideRth: outsideRth ?? false`,
  leaves all 7 modal cases green, the `/outsideRth/` grep still matches the
  destructure at `:277`, and `scripts/api/server.py:3095-3097` never receives
  the flag: un-ticking EXT on a resting GTC order shows a success toast and
  leaves it working outside RTH; the `?? false` variant flips an untouched EXT
  order to RTH-only on a price-only change. **AC:** RED — `renderHook(() =>
  useOrderActions(), { wrapper: OrderActionsProvider })`, stub `fetch`, call
  `requestModify(stockOrder({outsideRth:true}), { outsideRth: false })`;
  assert `url === "/api/orders/modify"`, `method === "POST"`,
  `JSON.parse(body)` deep-equals `{ orderId: 2, permId: 1002, outsideRth:
  false }`; paired case `{ newPrice: 51.25 }` → body has no `outsideRth` key.
  The drop-the-key mutation must red. Then delete
  `modify-order-ticker-detail.test.ts:99-113`. *(Agent D; lead re-read
  `:274-283` and re-ran the grep.)*

- **T-321 [P1] (T-311 class) `chain-prefill-expiry-contract.test.tsx` reads
  the LAST `router.replace` synchronously after a `waitFor(toHaveBeenCalled)`
  that is already true on the FIRST call, and its `useSearchParams` mock can
  never see what `replace` wrote — so the R-378 guard's production branch is
  untested.** `web/tests/chain-prefill-expiry-contract.test.tsx:23-34`
  (`useSearchParams: () => new URLSearchParams(searchParamsString)` — the
  ORIGINAL string every render; `replace: replaceMock` records only),
  `:146-147, 154-156`. Round 1: `1 failed / 8265 passed`; `3 passed` ×3 in
  isolation. The failing URL is the tell —
  `/MU?deck=c&strikes=100&legs=BUY%3A1x970C&src=leap`, no `expiry=` at all:
  `OptionsChainTab.tsx:1127-1146` sets `initialFocusAppliedRef` in the same
  commit it schedules `setSelectedExpiry`, and the URL-writer effect declared
  after it (`:1213-1220`) fires in that commit with the closure's
  `selectedExpiry === null`, so `useChainUrlState.ts:149` deletes `expiry`
  and keeps `legs`/`src` (write #1); write #2 strips them a tick later, and
  `.at(-1)` reads whichever has happened. In a browser write #1 has already
  REMOVED `expiry` from the URL; if `useSearchParams` reflects that before
  the second effect, `requested` at `useChainUrlState.ts:159` is `null`, the
  strip at `:160-163` is skipped, `legs=…&src=leap` survive, and
  `OptionsChainTab.tsx:1153` (`requestedExpiry && …`) lets the LEAP prefill
  arm against the fallback expiry under the scanner label — the exact defect
  REL-131 shipped to close. A static mock cannot answer whether the App
  Router commits the URL before React's next render; no `web/e2e` spec
  deep-links an unlisted expiry. **AC:** RED — make the navigation mock
  stateful (`replace` updates `searchParamsString`) and assert the FINAL URL
  has neither `legs=` nor `src=` and no `PREFILLED FROM LEAP SCAN`; wrap the
  URL assertion in `await waitFor(...)`; run the file 10× under
  `--sequence.shuffle` (today red ~1 in 4 full runs). GREEN — 10/10; deleting
  `useChainUrlState.ts:160-163` must red it. *(Lead: reproduced, read both
  effects and the hook.)*

- **T-322 [P1] (T-164 → T-205 recurring a third time) `92278f6a`'s `edge`
  shard moved the caddy binary off `al`, and
  `test_caddyfile.py::TestRestartWindowMechanism` — the executable proof that
  a request arriving during the deploy restart window is held and served, not
  502'd — now silently skips in CI.** `.github/workflows/ci.yml:422-433`
  (matrix: `al` = `test_[a-l]*.py` minus `test_caddy_edge_timeouts.py`; `edge`
  = that one file), `:463` `Install caddy` `if: matrix.shard == 'edge'` (was
  `'al'` at base); `cloud/tests/test_caddyfile.py:487-503`
  `@pytest.mark.skipif(shutil.which(CADDY_BIN) is None)` on the class,
  collected by `al`; `cloud/tests/test_caddy_edge_timeouts.py:415-445`, the
  only CI self-check, is scoped to ITS OWN file. CI: `al` at `91c0c128`
  (caddy on `al`) `747 passed, 2 skipped`; `al` at `92278f6a` `726 passed,
  3 skipped`; the +1 skip is this class, and `-q` prints no skip reasons.
  A Caddy release changing dial/retry semantics, or a Caddyfile edit breaking
  the `proxy_block`, ships green. **AC:** RED — generalise
  `test_caddy_edge_timeouts.py:415-445`: for EVERY module under `cloud/tests`
  that references `shutil.which(CADDY_BIN)`, the shard that collects it must
  be named in the caddy-install step's `if:`; today `test_caddyfile.py` → `al`
  ∉ `'edge'`. GREEN — move the mechanism class into the edge module, add an
  edge row for it, or install caddy on `al` too; add `-rs` to the cloud shard
  invocation so skips are visible. *(Agent F; lead read `:485-504` and the
  two job summaries.)*

- **T-323 [P1] REL-134's state-dir confinement contract is green with the
  whole `/var/lib/radon` re-mounted `:rw`.**
  `cloud/tests/test_rel134_state_dir_confinement.py:53` `re.search(r"-v
  \S+:/var/lib/radon(?:\s|$)", log)` demands whitespace or EOL right after the
  path, so `:rw`, `:z`, `:rslave` and `--mount type=bind,dst=/var/lib/radon`
  all escape it; `:58-61` is an exact-literal negative on the source that the
  same suffix escapes. Agent C appended `-v "${STATE_DIR}:/var/lib/radon:rw"`
  after `cloud/scripts/radon-app-runtime.sh:360` in a scratch copy: `4
  passed`. That is the R-381 hole the file exists to close — uid-radon
  containers (incl. newsfeed's headless Chromium with `--ipc host`) get write
  on the parent of `control-plane-ready`, the manifest and
  `deploy/active-units`. The behavioural harness (`test_app_runtime._run`,
  fake docker logging argv) is already what `:50-55` uses; only the assertion
  is wrong. **AC:** parse the logged `run ` argv, collect every `-v`/`--mount`
  destination (`src:dst[:opts]`, `dst=`), assert none equals `/var/lib/radon`
  with any suffix and only `/var/lib/radon/media` + `/var/lib/radon/ib-lease`
  sit under it. RED with the `:rw` and `--mount` mutants; GREEN at HEAD.
  *(Agent C.)*

- **T-324 [P1] R-380 "bring the app tier back before propagating" is asserted
  by regex ORDER over `deploy.sh`'s text; a `return 1` placed before the
  restart call passes.** `cloud/tests/test_rel133_control_plane_recovery.py:82-95`
  matches `if ! refresh_control_plane; then(?:(?!\bfi\b).)*start_services_after_transition`
  over the comment-stripped `restart_services` body — the token only has to
  APPEAR inside the block. Agent C swapped `cloud/scripts/deploy.sh:1311-1312`
  to `return 1` followed by a dead `start_services_after_transition`:
  `-k test_restart_services_restarts_the_app_tier` → `passed`. That is the
  R-380 outage class itself: `refresh_control_plane` fails between
  `stop_services_for_transition` and `start_services_after_transition` (exit
  66 on a pre-`702ae26a` rollback, 75 during a 2FA transition) and all five
  app units stay down. A source-and-override harness for `deploy.sh` exists
  in the SAME delta (`cloud/tests/test_sync_control_plane.py:219-245`).
  **AC:** `source deploy.sh`; stub `prepare_release_transition`,
  `payload_paths_changed(){ return 0; }`, `stop_services_for_transition`,
  `activate_staged_release`, `refresh_control_plane(){ return 1; }`,
  `start_services_after_transition(){ touch "$T/started"; }`,
  `install_release_units`; run `restart_services <sha> <prev>`; assert rc ==
  1, `$T/started` exists, `install_release_units` not called. RED under the
  mutant and under `refresh_control_plane || return 1`; GREEN at HEAD. Keep
  the regex only as a comment-stripped structural smoke. *(Agent C.)*

- **T-325 [P1] A delta test's "runtime-constructed" fixture key is
  constant-folded into its `.pyc`, and `test_no_real_secrets_in_tracked_files`
  walks `__pycache__` — so the local cloud gate carries a NEW permanent red
  that CI never sees because the two files live in different shards, and the
  scanner's "tracked files" contract is false in both directions.**
  `cloud/tests/test_integration.py:30-42` `tracked_files` = `os.walk(ROOT)`
  skipping only `.git` (`ROOT` = `cloud/`, `cloud/tests/conftest.py:7`; never
  `git ls-files`), `:243-263`; `cloud/tests/test_next_clerk_guard.py:17`
  `KEY = "pk_live_" + "fixture" * 4`, `:52` `"pk_live_" + "other" * 4` —
  CPython folds both into literals in
  `cloud/tests/__pycache__/test_next_clerk_guard.cpython-313-pytest-9.1.1.pyc`
  written at collection. Round 1: `FAILED …::TestSecurity::test_no_real_secrets_in_tracked_files`
  naming that `.pyc`; reproduced 2/2 in isolation. CI stays green only because
  `test_integration.py` (`al`) and `test_next_clerk_guard.py` (`mz`) are
  never collected in one checkout. Both ways false: it reds on a gitignored
  artifact, and it never asserts the scanned set IS the tracked set.
  **AC:** RED — `python3.13 -m pytest cloud/tests/test_next_clerk_guard.py
  cloud/tests/test_integration.py -k no_real_secrets` is `1 failed` today.
  GREEN — `tracked_files` enumerates `git -C root ls-files -z` (or at minimum
  prunes `__pycache__`/`*.pyc`), plus a companion assertion that
  `test_next_clerk_guard.py` IS in the scanned set; a `pk_live_` + 20 alnum
  literal written into a tracked file under `cloud/` must red it.
  *(Converged ×4: lead, agents C, E and F. Also moves the darwin baseline —
  see the standing sweeps.)*

- **T-345 [P1] (from yesterday's remediation appendix, item 1)
  `useOrderRisk.ts:741` is the only `okToSubmit` in the hook that does not
  require `coverageStatus === "resolved"`, so a stock/futures close-out arms
  Transmit under `no-portfolio` coverage while the option close-out at
  `:832-835` refuses — and nothing pins either contract.**
  `web/lib/order/risk/useOrderRisk.ts:741` `okToSubmit:
  summaryFiguresAreFinite(closeSummary)`; `:832-835` `okToSubmit:
  coverageStatus === "resolved" && residualRisk == null &&
  summaryFiguresAreFinite(closeSummary)` (lead re-read both). T-260 surfaced
  it and left the behaviour unchanged. A close-out is by definition a trade
  against a position the ticket cannot see when coverage is `no-portfolio`;
  arming it is the direction Gate 3 refuses everywhere else in the hook.
  **AC:** RED — a `useOrderRisk` case for a stock close-out with
  `coverageStatus: "no-portfolio"` asserting `okToSubmit === false`; today
  `true`. GREEN — mirror `:832-835` at `:741`, or if the asymmetry is
  deliberate, pin it in the test AND in the copy the operator sees. Either
  way the mutation to the other contract must red. *(Lead. Operator
  decision on which contract; the test is owed regardless.)*

### P2

- **T-326 [P2] `ib_order_manage.modify_order`'s R-428 limit guard is pinned
  by source text only; no modify test sends an over-cap quantity.**
  `scripts/ib_order_manage.py:265-285`; `test_rel145_order_limits_at_every_funnel.py:95-108`
  (text scans; deleting the `return` at `:286` passes); every `modify_order`
  case in `scripts/tests/test_ib_order_manage.py:211-380` is within limits;
  BAG branch (`check_quantity_limit` only) untested. The transport funnel
  still refuses at the socket, but as an uncaught `IBOrderError` instead of
  the `output("error", …)` JSON the FastAPI caller parses (T-057 class).
  **AC:** `new_quantity=max_order_qty()+1` → `place_order` not called and
  stdout is the error JSON containing "(order not modified)"; plus a BAG
  variant; deleting `:283-285` must red both. *(Agents A and C.)*

- **T-327 [P2] `scenario_analysis.run_full_analysis`'s new
  `entry_cost is None` skip has no test.** `scripts/scenario_analysis.py:739-741`,
  introduced with the T-253 `entry_cost: None` contract in
  `scripts/ib_sync.py:646-658`; every fixture in
  `scripts/tests/test_scenario_analysis.py:25-64` has numeric `entry_cost`.
  Removing the skip raises `TypeError` for any portfolio holding one mixed
  position. **AC:** one `entry_cost=None` position beside a normal one;
  assert it is absent from all three scenario lists and the others unchanged;
  deleting `:739-741` must red. *(Agent A.)*

- **T-328 [P2] The assistant's capability model is THREE independent tables
  with zero cross-checks, and the 118-file `radonCapability` sweep is inert at
  runtime.** No consumer: `grep -rn radonCapability web/lib web/components
  web/app web/middleware.ts scripts/api` minus the `export const` lines → 0.
  Runtime authorization is `web/lib/assistant/catalog.ts:189-213`
  (`classifyDenied`, a hand-written denylist) + `OPERATIONS` `:71-125`;
  FastAPI has a third table in `scripts/api/assistant_catalog.py:82-89,116`.
  `web/tests/assistant-catalog-pin.test.ts:149-165,177-198` is a genuine
  tree-walker (a route file lacking the export, or `orders/place` annotated
  `read`, DOES fail), but `:254-285` are six `it`s asserting `PINNED[...]`
  literals against the same `PINNED` dict at `:21-145`; adding
  `op("DELETE", "/api/alerts/{id}", "read", "next", …)` to OPERATIONS while
  PINNED says `mutate.workspace` stays green; `scripts/api/tests/test_assistant_catalog.py:49-60`
  pins `/orders/place` only, so re-keying `("POST", "/orders/replace")` to
  `mutate.workspace` passes. **AC:** one test that for every OPERATIONS entry
  with `surface: "next"` asserts `PINNED[path][method] === op.capability`
  (PINNED imported from a shared module) and no entry carries a refused
  capability; a prefix rule on the FastAPI side (`POST /orders/*` except
  `refresh`/`whatif`, `/paper/place`, `/workflow/run` → `mutate.trading`);
  delete `:254-285`. *(Converged: agents A, B and D.)*

- **T-329 [P2] R-413's "strike not listed" guard is evaluated against a
  `strikes` state that is never reset on expiry or ticker change, and the
  contract test's chain mock returns one strike list for every expiry.**
  `web/components/ticker-detail/OptionsChainTab.tsx:1234-1275` (writers
  `:1239`, `:1259` only; no clear), `:1168-1177` (waits only on
  `strikes.length === 0`, burns the signature, names `selectedExpiry` in the
  message while the list may be the previous expiry's), `:1093-1099` (ticker
  change resets refs, not `strikes`);
  `web/tests/chain-prefill-expiry-contract.test.tsx:122-127` ignores the
  `expiry` query. A strike present in the old list but absent from the new
  one arms as a phantom leg; the reverse is refused with the wrong expiry
  named and never recovers. **AC:** per-expiry chain mock (NEAR →
  `[950,960,970]`, FAR → `[1000,1050]`), mount with `?expiry=FAR&legs=BUY:1x1000C`
  after NEAR's strikes populated; assert `1000C` arms with no
  `prefill-unavailable`; mirror case refused naming FAR. GREEN — track the
  expiry the list belongs to or clear `strikes` at the top of the effect.
  *(Agent B.)*

- **T-330 [P2] The assistant's `read.spawn` budget is enforced on `call_api`
  only; `fetch_backend` reaches the same FastAPI scan POSTs with no budget.**
  `web/lib/assistant/dispatch.ts:27,207-212`; `tools.ts:454-478,764-784,916-930`;
  `backend.ts:9-15` allows `read.spawn`; `web/app/api/assistant/route.ts:38-45`
  still advertises "via fetch_backend or call_api";
  `web/tests/assistant-call-api.test.ts:155-169` (A8) exercises `call_api`
  only. **AC:** `executeTool("fetch_backend", {method:"POST", path:"/scan"},
  PRINCIPAL, budget)` ×3 with a shared budget → third is `ok:false /spawn/`
  and `radonFetch` called twice. *(Agent B.)*

- **T-331 [P2] `web/lib/useHeadlines.ts` (new, 126 lines) has zero tests;
  every consumer test mocks it.** `:54-72` `applyFrame` (snapshot, dedupe by
  id, `slice(-50)`, `upstream-down` → down, `upstream-open` ignored though
  `scripts/mktnews/hub.js:201-204` emits it), `:78-105` reconnect backoff,
  `:96` unconditional socket on every dashboard mount;
  `web/tests/dashboard-feed-tabs.test.tsx:44` `vi.mock("../lib/useHeadlines")`.
  **AC:** stub `WebSocket`; pin snapshot → `live`, `upstream-down` → `down`,
  decide and pin `upstream-open`, 51 frames → 50 newest-last, duplicate id
  moves to the end, `close` schedules a reconnect that unmount cancels.
  *(Agent B.)*

- **T-332 [P2] `LeapScanner`'s R-415 staleness gate uses the catalog's `open`
  window unconditionally, and the rel148 test never samples the 26h–72h band
  where it disagrees with the catalog.** `web/components/LeapScanner.tsx:70`
  `LEAP_STALE_MS = SERVICE_FRESHNESS_WINDOWS["leap-scan"].open` (26 h; closed
  is 3 d, `serviceHealthWindows.ts:391`); `cloud/services/radon-leap.timer:10`
  Mon–Fri 10:00 ET; `web/tests/rel148-prefill-label-and-leap-age.test.tsx:102-118`
  samples 10 m, 3 h, 72 h. Every Saturday 12:00 ET → Monday 10:00 ET the meta
  reads `STALE` and the headline order link vanishes while the rail says
  healthy. Conservative direction. **AC:** fake clock Saturday 14:00 ET,
  `lastSync` Friday 10:05 ET → decide (likely `getFreshnessWindowMs`) and
  pin; mutation back to `.open` must red. *(Agent B.)*

- **T-333 [P2] Five smaller source-grep / self-referential assertions in
  the python delta (bundle).** (a) `scripts/tests/test_rel143_leap_best_contract.py:80-86`
  NEGATIVE grep for `if gap_20 > best_gap or best_leap is None:`; the R-388
  bug re-lands as `if best_leap is None or gap_20 > best_gap:` at
  `scripts/leap_scanner_uw.py:559` and passes (verified on a copy); a
  behavioural `scan_ticker` case with a group mispriced only via `gap_60`
  is feasible. (b) `test_rel139_soft_fail_and_persist_reserve.py:76-82`
  asserts `_embargo_deadline` on a dict the test built without
  `next_attempt_at`. (c) `test_rel147_flex_cash_double_booking.py:130-143`
  `"previous digest" in install or "replacing" in install` on the
  `refresh_install_file` body, unrelated to the file's subject.
  (d) `scripts/api/tests/test_knowledge_routes.py`
  `test_lifespan_schedules_knowledge_embedder_warm` asserts the literal
  `asyncio.create_task(_warm_knowledge_embedder_on_startup())` is in the
  source (a commented-out call passes) and `…_loads_off_loop` asserts only
  that the builder ran, not `to_thread`. (e) `scripts/tests/test_weekend_loop_deadman.py`
  `test_each_setup_checks_the_bash_version` greps `BASH_VERSINFO` in the raw
  script while `test_weekend_runner_env_provisioning.py:77` stubs `bash` to
  `exit 0`, so the version check never executes under test. **AC:** per item
  as stated; (b) delete. *(Agent C.)*

- **T-334 [P2] "deploy prefers the privileged refresh" is a tautology.**
  `cloud/tests/test_host_role_split.py:179-184`: `privileged_at =
  body.index("refresh-control-plane-privileged")`, `unit_at =
  body.index("refresh-control-plane")` — the second string is a prefix of the
  first, so `privileged_at < unit_at` is never true and the assertion
  collapses to `body.count("refresh-control-plane") >= 2`. Hard-wiring
  `verb="refresh-control-plane"` at `cloud/scripts/deploy.sh:399` passes
  (verified). **AC:** `source deploy.sh` with a `sudo()` shim (pattern at
  `:321-346`) that grants `-n -l` for the privileged verb; run
  `refresh_control_plane`; assert the last logged line is `<helper>
  refresh-control-plane-privileged`; second case not-granted → plain verb.
  *(Agent C.)*

- **T-335 [P2] Three cloud pins that a real regression walks past (bundle).**
  (a) `cloud/tests/test_host_role_split.py:250-254` `'HOST_ROLE" != "app"' in
  body` — the string occurs 4× in `cloud/scripts/operator-radon.sh:447,464,479,489`
  (one per stop/start/restart/status arm); deleting any single guard stays
  green, and the `body.replace(" ", "")` alternative can never match (needle
  has spaces, haystack has none). (b) `:156-166` `TestNoGatewayPartOf` is an
  exact-literal negative on the BASE units only: `PartOf =
  radon-ib-gateway.service` (systemd ignores the whitespace), `Requires=` or
  `BindsTo=` in `services/radon-relay.service:3` reinstate the stop cascade
  and pass (verified in-process). (c) `cloud/tests/test_media_backup.py:311-343`
  (and the twin `test_db_backup_offbox.py:449-453`) asserts every retry value
  equal to the module constant it came from (`S3_MAX_ATTEMPTS = 0` passes);
  both `is_transient_s3_error` doubles satisfy the class-name AND the
  message path at once, so deleting either classification path stays green.
  **AC:** (a) drive `operator-radon.sh stop|start|restart|status` with
  `RADON_HOST_ROLE=app` and a logging helper shim, assert never invoked,
  remove one guard → red; (b) parse `[Unit]` with key normalisation over base
  + `.d/*.conf`; (c) pin `1 < S3_MAX_ATTEMPTS <= 5`, `S3_READ_TIMEOUT >= 60`,
  one case per classification path. *(Agent C.)*

- **T-336 [P2] `rel148` pins the prefill-label switch by slicing its SOURCE
  and comparing string indices.** `web/tests/rel148-prefill-label-and-leap-age.test.tsx:31-34`
  (`readFileSync(OptionsChainTab.tsx)`), `:47-58` (`indexOf("function
  prefillLabelForSource")`, `thetaAt < defaultAt`), `:60-65`, `:123-129`
  (CSS text slice, T-293 class). Mutation `case "link": return "PREFILLED
  FROM LINK"; default: return "PREFILLED FROM THETA HARVESTER";` passes both
  `it`s while an absent `src` is stamped THETA HARVESTER again (the R-414
  defect); what actually guards it is `chain-url-deeplink.test.tsx:214-224`.
  **AC:** delete `:46-66`; add a misspelled `src=thetta` case beside
  `chain-url-deeplink:214`; for `:123-129` assert computed `overflow-y` on a
  rendered `.chain-tab` or drop it. *(Agent D.)*

- **T-337 [P2] The `REALIZED P&L` total in `FillsModal` sums IB-fallback
  rows with no provenance, and the rel144 test stops at the row marker.**
  `web/tests/rel144-realized-pnl-provenance.test.tsx:52-81` (only
  `realized-source-<execId>` per row); `web/components/FillsModal.tsx:103-111`
  (row marker), `:120-138` (total renders `totalRealizedPnl` built at
  `:39-42` by summing every row regardless of source; no marker). The
  $11,558 SLV-shape discrepancy the file header describes lands in the
  headline total unmarked. **AC:** render `[journal 4200, ib 18511]`, assert
  a `realized-total-source` marker (or "includes N IB figures") present, and
  absent for all-journal fills. *(Agent D.)*

- **T-338 [P2] (T-268 class) "display constants mirror
  `scripts/lib/dispersion_math.py`" is two independent literal pins.**
  `web/tests/dispersion-panel.test.tsx:38-45` and
  `scripts/tests/test_dispersion.py:146-161` each pin `STRESS_Z=1.0`,
  `COMPRESSED_Z=-1.0`, `WINDOW=60`, `ZSCORE_BASE_START="2017-01-01"` against
  their own side; neither reads the other. **AC:** parse the four constants
  out of `dispersion_math.py` (or ship them in the fixture payload) and
  compare to the TS exports; delete the literal pin. *(Agent D.)*

- **T-339 [P2] (delta to T-310 / T-302) R-404 deleted the relay-close
  fallback that eleven regime e2e specs were describing; four VIX/VVIX
  day-change assertions in untouched specs are now clock-INDEPENDENT reds,
  and one passes for the wrong reason.** `web/lib/regimeLiveStrip.ts:190-194`
  (`1cd6e16b`; no relay fallback, `:155` null for empty history — pre-delta
  `f7b5eeb9:158-166` consulted `prices.VIX?.close`); `RegimePanel.tsx:725,733`;
  `regime-day-chg` not emitted on a null close (pinned by the delta's own
  `regime-day-change.test.tsx:104-115` and `rel142:66-72`). Untouched specs
  with `history: []`: `web/e2e/regime-live-index-stream.spec.ts:26` → `:227
  "+1.50 (+6.25%)"`, `:228`; `regime-live-index-streaming.spec.ts:26` →
  `:205`, `:209`; `regime-day-change.spec.ts:112-116` asserts the node ABSENT
  and now passes because it aborts `/api/prices` (`:106`). All in the
  untriaged ledger backlog, so local-only; but the only browser evidence for
  "a live VIX tick renders a signed day change against a real close" is now
  four assertions that cannot pass. T-310's premise ("consults the relay
  close only when history is empty") no longer holds. **AC:** RED — run
  `regime-live-index-stream.spec.ts` at HEAD: `:227-228` time out. GREEN —
  give both fixtures `history: [{ date: lastCompletedSessionDate(), … }]`
  (as `regime-strip-responsive.spec.ts:15` does) with figures that disagree
  with the WS `close`; re-adding the fallback must red. *(Agent E.)*

- **T-340 [P2] (T-233 class) A new e2e assertion `73 days old` is an
  ET-midnight/DST off-by-one.** `web/e2e/flow-analysis-ticker.spec.ts:303-337`
  computes `stamped = new Date(Date.now() - 73 * 86_400_000)` while
  `web/lib/flowReportStaleness.ts:99-112` counts ET CALENDAR days; across a
  DST change the 73×24 h subtraction lands one wall-clock hour off, so runs
  at 23:00–24:00 ET while the window spans 2026-11-01, and 00:00–01:00 ET
  while it spans 2027-03-14, read `72`/`74`. The 00:00 PT runner (03:00 ET)
  misses both; an operator's evening run will not. **AC:** RED — pin
  `page.clock` to `2026-11-15T23:30:00-05:00`: `72 days old`. GREEN — derive
  `stamped` by walking the ET calendar. *(Agent E.)*

- **T-341 [P2] (T-074 / T-089 class) Two NEW tests let a real sleep decide
  the verdict.** `cloud/tests/test_app_runtime.py:423` fake docker `sleep 3`,
  `:436-437` 5 s socket poll then `time.sleep(0.8)`, `:439` `sendto` — the
  proxy exits when its parent (the `run` bash) returns at t≈3 s
  (`radon-app-runtime.sh:235,241`), so bash + python start + socket + 0.8 s
  must fit in 3 s or the test reds with a healthy proxy (≈2.2 s headroom);
  `:418` `mkdtemp(dir="/tmp")` never removed.
  `scripts/tests/test_rel137_weekend_wrapper_survivability.py:421-437`: the
  winner holds `sleep 3` and never releases, `acquire_runner_lock`
  (`scripts/testing_weekend.sh:54-60`) reclaims a dead pid, and the 8
  sequential `Popen`s at `:432-435` have no start barrier, so any late
  starter legitimately reclaims and `sum(...) == 1` fails. **AC:** fifo the
  test releases AFTER `sendto`; a shared start barrier and a winner that holds
  until signalled; removing the `kill -0` at `testing_weekend.sh:54` must red.
  *(Agent E.)*

- **T-342 [P2] (T-039 class) `assistant-model-selection.test.ts` inherits
  the ambient provider env instead of clearing it.** `:17-28` `ENV_KEYS`
  omits `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`, `GEMINI_API_KEY`; `:63-67`
  `beforeEach` saves but never deletes (contrast
  `web/tests/llm-provider.test.ts:68-69`); `:153-160,173-181` delete only the
  xAI keys then assert `resolveProvider({})` is `"anthropic"`, which
  `web/lib/llm/provider.ts:160,512` decides from `LLM_PROVIDER`/`GEMINI_API_KEY`
  first. **AC:** RED — `LLM_PROVIDER=xai npx vitest run
  web/tests/assistant-model-selection.test.ts` → `:159` fails. GREEN — add
  the keys and `delete` all of them in `beforeEach`. *(Agent E.)*

- **T-343 [P2] The `web/README.md` cross-tree contract can never fire.**
  `scripts/ci/path_filter.py:214-219` maps `web/README.md` to
  `test_docs_contract.py::TestThinIndex::test_web_readme_does_not_teach_npm`,
  but `:295-302` filters `_is_documentation` BEFORE pattern matching and
  `:253-254` classes every `.md` as documentation:
  `select_gates(["web/README.md"])` → `contract_tests=()`. Dead code that
  reads as coverage. **AC:** RED — assert that node id is in
  `select_gates(["web/README.md"]).contract_tests`. GREEN — match contracts
  before the documentation filter, keeping `(False, False)` for the full
  gates. *(Agent F.)*

- **T-344 [P2] `radon-python:latest` / `radon-node:latest` are pushed to
  GHCR before any test gate, so a red `main` republishes `latest`.**
  `.github/workflows/ci.yml:86-95` (`app-images` has no `needs`);
  `.github/workflows/app-images.yml:43-46,91-94` tags `:${{ github.sha }}`
  AND `:latest`; `cloud/scripts/radon-app-runtime.sh:45-57` accepts only a
  40-hex SHA, so nothing on the host resolves `latest` today — but the
  public registry's `latest` pointed at `44683993` (gate red) between 04:56
  and 05:12Z on 2026-08-30, and any future fallback-to-`latest` would be
  pre-armed with untested images. **AC:** `cloud/tests/test_app_images.py`
  asserts no moving tag in the pushed tag list, or that `latest` is retagged
  only from `deploy` post-gate. *(Agent F.)*

## Delta audit 2026-08-31 (landed by the remediation phase)

Range `fda36450..39bf6f5e` — 51 commits, 247 files, +20997/-853.
94 test-tree paths changed: **25 added, 69 modified** (35 `scripts/tests`,
34 `web/tests`, 15 `cloud/tests`, 5 `scripts/api/tests`, 3 `scripts/mktnews`,
2 `web/e2e`). New findings continue the frozen numbering at **T-346**:
**34 findings, T-346…T-379 (8 P1, 26 P2)**. PART A (§1–§10) is untouched;
nothing above this line was rewritten.

**How this section got here.** The audit phase (`20260831T000008`) fanned out
six read-only agents, numbered T-346…T-378 in `/tmp/tw-2026-08-31/findings.md`,
launched its gates from a detached script, and then ended its turn on a
progress message 18 minutes in — zero commits, no PR, wrapper status `OK`
(T-379, filed by the remediation phase that found the draft). The remediation
phase landed the draft verbatim under this heading, added T-379, ran the
sweeps the audit had not reached, and recorded the audit's own round-1 gate
output. The audit lead had re-read the cited lines of every P1 before
numbering and reproduced three in-process (see Method below); the remediation
lead re-derived T-346, T-350 and T-379 from source before landing.

**Base note.** The base is `fda36450`, yesterday's ledger SHA. The range
contains yesterday's own remediation (PR #198, T-312…T-325 + T-345), the
three new nightly-loop wrappers (`e3f5d3a3`, `c6d93beb`, `6d10f1e1`) and the
host-role split series (#205–#209); all re-triaged as ordinary delta.

**Pre-flight.** Monday 2026-08-31, load average 4.6 at 00:18. Runner clone
verified (`.radon-weekend-runner` present; tree clean apart from the
wrapper's `.weekend-runner.lock/`). `rtk` is NOT installed here, so bare
`git` is correct. Toolchain: `node` v24.14.0 via `~/.nvm`, `vitest` in
`node_modules/.bin`, `python3.13` from `~/radon-weekend/venv` with
`pytest-asyncio` + `pytest-xdist`, `/bin/bash` 3.2.57, `caddy` present.
`origin/testing/2026-08-31` existed at pre-flight (pushed empty by the audit,
= `origin/main`). Open PRs at pre-flight: #211, #210 (sibling loops), #176,
#126, #125 — none matches a finding subject. Scratch namespaced to
`/tmp/tw-2026-08-31-rem/`.

**Gates (round 1, the audit's detached serial script, load 4.6 → 13 → 20).**
pytest **9508 passed / 1 skipped / 0 failed** (90 integration deselected) in
720 s — the first fully green pytest round on this clone since `web/.env`
was provisioned (T-317's fix in force: the five Flex-embargo files are green
with `TURSO_DB_URL` present). vitest **832 files / 8392 passed / 0 failed**
in 103 s. cloud **35 failed / 1500 passed / 6 skipped** in 296 s. Tree clean
after each gate (`git status --porcelain` = lock dir only; T-275 class not
recurring). The cloud FAILED list is NOT byte-identical to the 2026-08-30
baseline of 35: `+test_bootstrap_control_plane.py::test_termination_after_daemon_reload_restores_bundle_and_readiness`
(new in the delta via `1b85a8b3` REL-157; bash-3.2 `exec {fd}<>` class, same
cause as its 13 siblings) and
`-test_integration.py::TestSecurity::test_no_real_secrets_in_tracked_files`
(T-325 fixed). The recorded darwin baseline stays at 35 with that one-for-one
swap; the sorted list is in `/tmp/tw-2026-08-31/cloud_r1_failed.txt` and the
34 remaining reds are 14 `test_bootstrap_control_plane.py` + 20
`test_ib_gateway_control.py` (`mapfile` / `exec {fd}<>`, bash 4+).

**Collection union (T-122 sweep, from the gate-drift agent).** pytest root
collect 9509 tests / 541 files (90 integration deselected); CI run
33359053839 at `92d8e0a4` sums its ten shards to 9508 passed + 1 skipped =
9509 — MATCH. cloud 1541 / 44 files; shard union 44; CI `al` 765 + 2 skipped,
`edge` 30, `mz` 744 = 1541 — MATCH. vitest `list` 8392 / 832 files; CI 8 × 104
files, 8391 tests, +1 `it(` added by `08bdd41b` after that run — MATCH. All
13 delta python test files and all 13 delta vitest files are reached by a CI
shard. Playwright curated list unchanged at 19 specs (`ci.yml:677-696`);
`streaks-tab.spec.ts` went to the ledger backlog, not CI (T-378).

**Enforcement.** `deploy.needs` and `stage-release.needs` are IDENTICAL at
base and HEAD (14 and 12 jobs, both coverage ratchets retained); `deploy.if`
unchanged. Branch protection on `main`: `enforce_admins` true, no force push,
still NO `required_status_checks`, rulesets empty (T-222 unchanged).
`ci.yml` +34/-4 in range: T-312's resolved-gate-base (`path_filter.py:324-386`,
`ci.yml:68-70, :85-95`) and `-rs` on the cloud invocation (`:520`); no
`--ignore` / `deselect` / `exclude` added; `pyproject`, `vitest.config.ts`
and `playwright.config.ts` byte-identical base → HEAD; no threshold moved.
CI inside the range: two red pushes (`77afe08b`, `5d92cb30`), both re-gated by
`642da3c4`; HEAD `39bf6f5e` is a docs-only push whose gate base resolved to
the last green SHA and deployed with tests skipped — the T-312 mechanism
working as designed.

**Skips.** 17 `+` lines match the skip / `.only` / `xfail` scan and every one
is prose (`TEST_AUDIT.md`, `.claude/skills/ci-performance/SKILL.md`); zero in
code. CI-observed skips unchanged (vitest 18 incl. the pre-existing
whole-file `lib/tools/__tests__/kelly.test.ts`, cloud 2 = T-204, pytest 1).

**Delta-file determinism.** The audit prepared the lists (38 `scripts/*` +
`scripts/api` files, 15 `cloud/tests` files, 37 vitest files, one script per
collection root) and never reached them; the remediation phase ran them 3×
and the counts are in `TEST_LOG.md` under `## Remediation 2026-08-31`.

**Status changes.** T-312 FIXED (in force at HEAD, above). T-317 FIXED (pytest
green with `web/.env` present). T-322 FIXED (`ci.yml:484, :520`; CI `al` log
shows 0 caddy skips). T-325 FIXED (cloud diff above). T-222 unchanged.
T-122 / T-276 holding.

### Method

Six read-only agents over the delta plus its blast radius (untested source,
net-negative tests, fragile mechanisms, gate drift, blast radius of source
changes on untouched tests, and the three NEW nightly-loop wrappers), each
told the lead owns the gates and capped at six single-file runs. The lead
re-read the cited lines of every P1 before numbering (all held) and
reproduced three in-process: `server.py:873` with both app-role route tests
stubbing the predicate to `False` at `:143`/`:165`; the streaks ladder tuple
at `routes/streaks.py:171-175`; `security_nightly.sh:151-174` `report()`
against the two negative substring greps at
`test_security_loop_contract.py:213-219`. Where two agents landed on the same
file:line independently (T-346 ×2, T-348 ×2, T-349 ×2, T-350 ×2, T-356 ×2,
T-368 ×3) the convergence is recorded in the finding. Numbering continues
from T-345.

### P1

- **T-346 [P1] The app-host Gateway-mutation gate — the one line that stops
  a compromised Next.js from restarting IBKR without a Clerk JWT — is never
  exercised through the middleware; both route tests that claim to cover the
  app role stub it to `False`.**
  `scripts/api/server.py:873`
  `if is_trusted_local_request(request) and not _is_app_role_gateway_mutation(request): return await call_next(request)`;
  the predicate at `:2025-2042` covers `/ib/restart`, `/ib/reset-backoff`
  and `/admin/services/radon-ib-gateway.service/{start,stop,restart}` under
  `RADON_HOST_ROLE=app`, and its docstring (`:2028-2030`) names the threat.
  `scripts/api/tests/test_ib_restart_cloud_delegate.py:26-27` forces
  `is_trusted_local_request` → `True`, `:35-36` sends no bearer, and both
  app-role route tests do
  `monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)`
  at `:143` and `:165` before POSTing. The only assertion on the predicate
  is the pure-function `:207-219`, which covers `/ib/restart`, `/health`
  and `/admin/stack/restart` — not `/ib/reset-backoff` and not the
  `radon-ib-gateway.service/` prefix branch at `:2038-2040`. Deleting
  `and not _is_app_role_gateway_mutation(request)` at `:873`, or the prefix
  branch, leaves the file `12 passed`. Two agents (untested-source,
  net-negative) converged on this line independently.
  **AC:** RED — `RADON_HOST_ROLE=app`, `CLERK_JWKS_URL` set, un-stubbed
  `is_trusted_local_request`, `client.host` `127.0.0.1`, no `Authorization`:
  `POST /ib/restart`, `POST /ib/reset-backoff`,
  `POST /admin/services/radon-ib-gateway.service/start` → 401 and
  `remote_gateway_action` never awaited; `GET /health` and
  `POST /admin/services/radon-api.service/restart` still bypass; with a
  valid JWT → 200 and awaited once. GREEN at HEAD; the `:873` mutation must
  red the first three.

- **T-347 [P1] The app→broker mTLS client has zero coverage: every test
  patches `remote_gateway_action` wholesale, and a broker outage is
  reported as a 400 client error.**
  `scripts/api/services.py:417-490` — `_remote_url_allowed` (`:417-425`),
  `_remote_ssl_context` (`:442-452`), `_remote_http` (`:455-476`, verb →
  method/path mapping), the 409/lease mapping (`:479-490`) — called from
  `_control_gateway` `:625` and `show_unit` `:319`.
  `scripts/api/tests/test_services.py:125` and
  `test_ib_restart_cloud_delegate.py:187` `patch.object(...,
  "remote_gateway_action", ...)`; `grep _remote_http|_remote_ssl_context|_remote_url_allowed`
  over all test trees → 0 hits. The broker side IS tested over real mTLS
  (`scripts/tests/test_ib_gateway_remote.py:225-353`) and its harness
  (`mint_mtls` `:31-103`, `make_server(port=0)` `:133-137`) is reusable, so
  the agent wired `remote_gateway_action` against a real `serve.make_server`
  over loopback (`/tmp/tw-2026-08-31/agents/a1/remote_roundtrip.py`): it
  works at HEAD — status `ok/running/0`, restart `ok/0`, helper rc 75 →
  `returncode 409` (`PUSH_LOCK_HELD_RC`, `services.py:504`), helper rc 1 →
  `ok False/1`, broker down → `returncode -1`. Nothing pins any of it:
  `method="GET"` for mutations (broker 404s), dropping `load_cert_chain`
  (handshake fails), `path=f"/{verb}"` for status all ship green. And the
  observed `-1` for an unreachable broker reaches `server.py:2152`
  `400 if result.returncode == -1 else 502` — a broker outage is a client
  error to the admin UI.
  **AC:** RED — tests through `remote_gateway_action` against
  `serve.make_server` with a stub helper: status running; restart ok; rc 75
  → `PUSH_LOCK_HELD_RC` and route 409; rc 1 → route 502; broker down → not
  400; plus `_remote_url_allowed` negatives. Each mutation above must red
  one case.

- **T-348 [P1] The Clerk-token forwarding that T-346's gate made
  load-bearing — three Next admin proxy routes — is pinned only by a
  source-grep, and the smoke suite is green with the `token:` line
  deleted.**
  `web/app/api/admin/ib/restart/route.ts:20-24`,
  `web/app/api/admin/ib/reset-backoff/route.ts:20-24`,
  `web/app/api/admin/services/[unit]/[action]/route.ts:51-55` add
  `token: access.principal.token`; `web/lib/radonApi.ts:65-68` sets
  `Authorization: Bearer` only when `token` is passed. On the production
  app host this token is the only thing that lets Force 2FA / Start Gateway
  through the `:873` gate.
  `web/tests/admin-page-gate.test.ts:48-56`
  `expect(source(rel)).toContain("token: access.principal.token")` (text,
  not behaviour); `web/tests/api-routes-smoke-admin.test.ts:56-59` resolves
  a principal with NO `token` field and `:85-93` asserts only `res.status`
  and `body.ok`, never `mockRadonFetch` args; the delta's wire test
  `admin-action-request-assertions.test.tsx:242-270` asserts the
  browser→Next hop (`/api/admin/ib/restart`), not Next→FastAPI. Verified:
  `api-routes-smoke-admin.test.ts` 19 passed; deleting line 23 of the
  restart route cannot fail any test in the tree. Two agents converged.
  **AC:** RED — in `api-routes-smoke-admin.test.ts`, principal carries a
  token and
  `expect(mockRadonFetch).toHaveBeenCalledWith("/ib/restart", expect.objectContaining({ method: "POST", token: "<principal token>" }))`
  for all three routes; red with `token:` removed, green at HEAD. Then
  delete the grep at `admin-page-gate.test.ts:48-56`.

- **T-349 [P1] Rule 7 ("Yahoo is last resort") is unpinned in four of the
  ladders `46897eec` changed: the streaks ladder can be re-ordered
  Yahoo-before-Robinhood green, and three ladders never prove a Robinhood
  miss still reaches Yahoo.**
  `scripts/api/routes/streaks.py:171-175` tuple `uw → robinhood → yahoo`,
  `:181` `>= MIN_ACCEPT_BARS` wins else longest.
  `scripts/api/tests/test_streaks_route.py:91-129`: `:97` gives RH `{}` so
  Yahoo wins in either order; `:117-119` gives RH 5 / Yahoo 4 so "longest"
  picks RH in either order; no case has RH ≥ 21 with a Yahoo spy. Swapping
  the two tuples keeps `7 passed`.
  `scripts/rv_ratio_scan.py:333-339` (`bars = _fetch_rh_daily(symbol); if bars: return bars, "rh"`):
  `scripts/tests/test_robinhood_priority.py:87-127` has RH-hit, index-skip
  and UW-hit only — `return bars, "rh"` unconditionally passes all three.
  `scripts/leap_scanner_uw.py:304-309`: `:162-176` covers RH ≥ 60 and
  UW-hit only. `scripts/cri_scan.py:507-511` quotes: `:182-201` RH-hit and
  IB-preferred only. Pinned both ways and judged clean: credit_spread
  (`test_credit_spread.py:457-498`), iei_hyg (`:514-540`), portfolio_risk
  (`test_robinhood_priority.py:45-71`), garch (`:130-151`), cri history
  (`:203-249`). Two agents converged on the streaks case.
  **AC:** RED — streaks: RH `_closes(30)`, Yahoo `MagicMock()`, assert
  `source == "robinhood"` and `yahoo.assert_not_called()`; swap the tuple
  → red. Per ladder (rv_ratio, leap, cri quote): RH `{}`/`None` for an
  equity → Yahoo stub called and `source == "yahoo"`; the unconditional
  return must red it.

- **T-350 [P1] The security loop's rail 7 ("never post the run-log tail to
  the public dead-man") is two negative substring greps; `tail -c 800`
  publishes scanner output to a public GitHub issue green.**
  `scripts/security_nightly.sh:151-174` `report()` builds
  `--body "$body"` from `${detail}`; `:437-448` is the caller.
  `scripts/tests/test_security_loop_contract.py:211-219` asserts only
  `"tail_text" not in body` and `'tail -c 1500 "$RUN_LOG"' not in body`
  over `_uncommented(WRAPPER)`. `report "$status" "$(tail -c 800 "$RUN_LOG")"`,
  `tail -n 40`, `head -c`, or `$(< "$RUN_LOG")` pass both. The file already
  owns a behavioural harness (`_stub_bin` `:138-160` logs every `gh` argv,
  `_clone` `:162-172`), and `test_weekend_wrapper_self_rewrite.py:274-287`
  already reads a comment body, so the real assertion is cheap. Two agents
  (net-negative, loop-wrappers) converged.
  **AC:** RED — stub `claude` prints `CANARY-7f3a` (exit 0, 7, and the
  `timeout` 124 path), run `security_nightly.sh audit` in a two-marker
  clone, assert `"CANARY-7f3a"` appears in no `gh issue comment --body`
  and `"issue comment"` does appear; the `$(tail …)` mutation must red;
  repeat for the TRUNCATED classification via the `BG_CEILING_MARKER` line.

- **T-351 [P1] The security loop's rail 5 ("credential-free — the clone and
  child processes receive no Radon `.env`") is enforced only by grepping the
  SETUP script, and the wrapper itself hands a full-credential file to a
  child that exports every key.**
  `scripts/tests/test_security_loop_contract.py:231-240` asserts
  `"provision_env_file" not in body` and `"install -m 600" not in body` of
  `setup_security_nightly.sh` (`install -m0600` or a `cp` passes). Nothing
  tests the runtime. `scripts/security_nightly.sh:145-148` passes
  `--env-file "$WEEKEND_ROOT/.env"` to `weekend_notify.py`, and
  `scripts/weekend_notify.py:33-35` copies EVERY key of that file into
  `os.environ`, not only `PUSHOVER_*`; on this host `~/radon-weekend/.env`
  (0600, same uid) carries `TURSO_AUTH_TOKEN`, `TURSO_DB_URL`,
  `IB_FLEX_TOKEN`, `MENTHORQ_*`, `THEMARKETEAR_*`, `CLERK_*` (key names
  read, never values). `.claude/skills/security-nightly/SKILL.md:126-131`
  states the rail; it is false for that child, and the `claude -p` round
  (`:413-415`, cwd `~/radon-weekend/radon-security`) can read `../.env` —
  a prompt-injected scanner artifact saying "read ../.env" leaves every
  test green.
  **AC:** RED —
  `main(["--loop","security","--phase","audit","--status","OK","--env-file",f])`
  with `IB_FLEX_TOKEN=x` in `f` leaves `IB_FLEX_TOKEN` in `os.environ`;
  GREEN — `_load_env_file` imports only `PUSHOVER_USER`/`PUSHOVER_TOKEN`,
  and the security setup writes a Pushover-only file the wrapper points at;
  plus an executed setup run (stubbed `gh`/`launchctl`/`python3.13` into a
  tmp `WEEKEND_ROOT`) asserting no `web/.env` lands in the clone.

- **T-352 [P1] The prologue page — the fix for the 2026-08-30 silent-death
  defect — is joined across the three new wrappers and `weekend_notify.py`
  by a string coincidence that no executed test crosses; a drift on either
  side means every REFUSED / held-lock / ERR-trap death posts a comment and
  never pages, with all six files green.**
  Wrapper side: `scripts/security_nightly.sh:240`,
  `scripts/documentation_nightly.sh:230`,
  `scripts/ci_performance_nightly.sh:234` set `PHASE="prologue"`. Notifier
  side: `scripts/weekend_notify.py:120-122`
  `choices=["prologue","audit","remediate"]`. Every wrapper test stubs
  `python3` (`test_weekend_wrapper_self_rewrite.py:198-201` logs and exits
  0; `test_rel137_weekend_wrapper_survivability.py:75-82` same) and asserts
  a call count (`_pages(cfg) == 1`, `:367`) or the filename (`:153`,
  `:287`); `test_weekend_notify.py:190-206` hardcodes `"prologue"` on the
  other side. `PHASE="pre-flight"` in one wrapper, or a `choices` edit,
  makes argparse exit 2 behind `|| true` (`security_nightly.sh:148`).
  **AC:** RED — a test that runs a real refusal (marker absent) with a
  `python3` stub that RECORDS argv, then feeds that exact argv to the real
  `weekend_notify.main()` with `_http_post` patched and asserts one POST
  with `title == "radon <loop> prologue"`; red on either drift.

- **T-379 [P1] (T-239 recurring) The audit phase of THIS cycle exited 0
  after 18 minutes with zero commits, no ledger advance and no PR, and the
  wrapper reported it `OK` on all three dead-man channels.**
  `logs/testing-weekend/audit-20260831T000008.log` holds three lines: the
  start banner, one progress sentence ("Draft findings numbered T-346…T-378
  … wait on its completion") and `audit done rc=0`. The agent answered a
  mid-run nudge with text and no tool call; print mode treats that as the
  end of the turn, so `claude -p` returned 0 while the agent's detached gate
  script (`/tmp/tw-2026-08-31/run_gates.sh 1`, pid 13464) was still running
  the cloud gate. `scripts/testing_weekend.sh:174-191` `phase_status` has
  four arms — `124` → TIMEOUT, `rc != 0` → FAILED, `BG_CEILING_MARKER` in
  the round's log slice → TRUNCATED, else `OK` — and none of them looks at
  the phase's work product. `origin/testing/2026-08-31` sat at `39bf6f5e`
  (`= origin/main`) with the drafted findings only in
  `/tmp/tw-2026-08-31/findings.md`. T-239 fixed the harness-ceiling variant
  (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, `:391`) and this is the other
  way the same silent-success happens. The SKILL contract makes at least one
  commit on the nightly branch mandatory for every phase (audit: ledger line
  + PR even for an empty range; remediate: the closing gate rows), so "rc 0
  and the branch tip did not move" is a truthful INCOMPLETE. The same four
  arms exist in `scripts/reliability_weekend.sh`, `scripts/security_nightly.sh`,
  `scripts/documentation_nightly.sh` and `scripts/ci_performance_nightly.sh`
  (reported, out of this loop's lane).
  **AC:** RED — an executed-wrapper case (rel137 harness) whose stub `claude`
  prints a line and exits 0 without committing yields a `gh issue comment`
  body and a notify call carrying a non-`OK` status naming the cause; GREEN
  — a stub that commits once during the phase still reads `OK`; TIMEOUT /
  FAILED / TRUNCATED precedence unchanged.

### P2

- **T-353 [P2] (T-335 class, new tests) The remote-daemon role skip is
  grepped, and the arm that implements it is deletable green.**
  `cloud/tests/test_host_role_split.py:290-294`
  (`test_role_skip_covers_remote_unit`) asserts
  `"services/radon-ib-gateway-remote.service" in helper` and
  `"role_skips_control_plane_source" in helper` — both strings already
  occur at `cloud/scripts/deploy-root-helper.sh:31` (manifest), `:70`
  (install path) and `:260` (caller). Deleting the dedicated arm
  `:274-276` falls to `*) return 1` — app hosts install the broker-only
  mTLS daemon — and the test passes. `:278-289` are exact-literal
  negatives on unit text (`"\nRequires="`, `"PartOf=radon-ib-gateway"`):
  `Requires =` or a `.d/*.conf` drop-in passes. **AC:** source the helper
  with `read_host_role` shimmed to `app`, call
  `role_skips_control_plane_source services/radon-ib-gateway-remote.service`,
  assert rc 0; delete `:274-276` → red.

- **T-354 [P2] Three `assistant-catalog-freshness` assertions compare the
  catalog to itself.** `web/tests/assistant-catalog-freshness.test.ts:81-99`:
  `catalogOperations()` is `buildRuntimeCatalog(loadPinSourcesFromDisk())`
  (`web/lib/assistant/catalog.ts:90-93,250-252`) and `buildRuntimeCatalog`
  is `advertisedPins(sources).map(toOperation)` (`catalogBuild.ts:142-158`),
  so "every advertised FastAPI pin is in the runtime catalog" (`:81-87`),
  "every advertised Next-only pin…" (`:89-95`) and "runtime catalog is
  exactly buildRuntimeCatalog(disk pins)" (`:97-99`) cannot fail — an
  `isAdvertisedPin` that drops every `GET` shrinks both sides equally. The
  real guards are `:56-79` and `:117-136`. **AC:** delete `:81-99`; add a
  fixture assertion that a known `read` GET pin (`/quote/{ticker}`) is
  present.

- **T-355 [P2] (T-331 class, new file) `web/lib/useStreaks.ts` (72 lines,
  owns the fetch) has zero tests.** `web/tests/streaks-panel.test.tsx:81-84`
  mocks `@/lib/useStreaks`; `:280` asserts the mock was called with
  `"QQQ"` and `:299` that `refresh` was called. `useStreaks.ts:38-42`
  (`/api/streaks?symbol=…`, `cache: "no-store"`, 65 s abort), `:43,49,53`
  seq guard, `:44-47` error mapping are unexecuted anywhere; a path typo or
  a dropped stale-response guard is green. **AC:** stub `fetch`,
  `renderHook(() => useStreaks("spy"))`, assert the exact URL string;
  rerender with `"QQQ"` and resolve the first request last →
  `data.symbol === "QQQ"`.

- **T-356 [P2] (T-092 / T-277 class) The Robinhood "unconfigured means no
  network" verdicts depend on the host's token file, and the guard the
  tests plant is swallowed by the source.**
  `scripts/tests/test_robinhood_priority.py:76-84` and
  `test_rh_crowding.py:199-215` delete only `ROBINHOOD_MCP_TOKEN`;
  `robinhood_configured()` (`scripts/clients/robinhood_client.py:383-391`)
  also reads `RobinhoodTokenStore()` whose path defaults to
  `data/rh_mcp_token.json` (`:70,150`) and honours
  `ROBINHOOD_MCP_REFRESH_TOKEN`; on the operator checkout (CLAUDE.md: tokens
  live in that 0600 file) 3 tests red. Independently: `:753` and `:770`
  `except Exception as exc: print(...)` swallow the `AssertionError` that
  `test_robinhood_client.py:71-78` (`no_network`, used `:126-131`),
  `test_credit_spread.py:500-516`, `test_iei_hyg.py:542-557` and
  `test_robinhood_priority.py:76-84` plant on `requests.Session.post` —
  probe (`/tmp/tw-2026-08-31/agents/a1/rh_guard_probe.py`): with a token
  and the patched `post`, `fetch_robinhood_closes(["SPY"]) == {}` and
  `fetch_robinhood_quote("SPY") is None` with a stderr line, so "no
  network" is unproven by every one of those cases. The default rung is
  also used un-stubbed in `test_iei_hyg.py:483-495` and the
  `test_credit_spread.py` cascade cases lacking `fetch_rh=`, which on a
  configured host make real MCP calls (7 s timeout, `:77`). The new
  autouse at `scripts/tests/conftest.py:177-213` strips only Turso keys;
  only `test_robinhood_client.py:51-61` isolates correctly. Two agents
  converged. Not exposed in THIS clone (no `data/rh_mcp_token.json`).
  **AC:** conftest autouse: `delenv` the four `ROBINHOOD_MCP_*` keys, set
  `ROBINHOOD_MCP_TOKEN_FILE` to `tmp_path`, reset `_refresh_disabled`; the
  no-network cases assert `Session.post.call_count == 0` on a spy (not a
  raise); RED today with a dummy `data/rh_mcp_token.json` present.

- **T-357 [P2] The "crowding cannot trip the Four Gates" pin is a source
  grep plus a signature tautology, and `fetch_crowding` never runs.**
  `scripts/tests/test_rh_crowding.py:249-256` regex
  `robinhood|rh_crowding|popular_watchlist` over `workflow/gates.py`,
  `kelly.py`, `evaluate.py` — a gate importing a `retail_overlay.py` that
  reads `rh_crowding` passes; `:266-280` inspects `inspect.signature` for
  names the test picked. `TestConfiguredRun` (`:218-230`) stubs
  `fetch_crowding` and `persist`, so `scripts/fetch_rh_crowding.py:156-177`
  (scan-id probing `scan.get("id") or scan.get("scan_id")`, the
  `MAX_SCANS_PER_RUN` slice, the per-scan error swallow) has no executing
  test. **AC:** drive `fetch_crowding()` against a `RobinhoodClient` double
  whose `get_scans` returns 7 rows (one without id) and whose `run_scan`
  raises once → assert 5 calls and 4 result keys.

- **T-358 [P2] Every ladder rung wrapper in `test_robinhood_priority.py` is
  mocked at the wrapper, so the dict→list adapters are unexecuted.**
  `scripts/garch_convergence.py:196-197`, `scripts/leap_scanner_uw.py:294-295`,
  `scripts/cri_scan.py:400-401` (`[(date, close) for date in sorted(closes)]`)
  and `cri_scan.py:404-411` (`index=ticker in YAHOO_TICKERS`) are replaced
  at `:134, :156, :210, :185`. `_fetch_rh_current_quote("VIX")` calling the
  equity tool, or a wrapper returning the dict unsorted, is green. **AC:**
  monkeypatch `clients.robinhood_client.fetch_robinhood_closes` /
  `fetch_robinhood_quote` and assert each wrapper's output shape and the
  `index=True` kwarg for `VIX`.

- **T-359 [P2] The rogue-client-cert case passes for the wrong reason.**
  `scripts/tests/test_ib_gateway_remote.py:303-310`: `_ctx(certs, rogue=True)`
  (`:141`) loads `rogue_ca` as the CLIENT's trust store, so `urlopen` fails
  verifying the SERVER cert before the server evaluates the client cert;
  `scripts/ib_gateway_remote/serve.py:122-123` (`load_verify_locations(ca)`,
  `CERT_REQUIRED`) is not what raises. A server that additionally trusts the
  rogue CA is not caught (only the `CERT_NONE` regression is, by `:293-301`).
  **AC:** rogue ctx with `cafile=certs["ca"]` plus the rogue chain; expect
  the handshake to fail with `SSLError`.

- **T-360 [P2] "The wall clock aborts a turn whose loop never settles" mocks
  the loop to settle on abort with the test's own string.**
  `web/tests/assistant-call-api-bounds.test.ts:179-214`: `runAssistantLoop`
  is replaced (`:182-201`) and `done.content` is compared to the literal the
  mock returned (`:213`); `web/app/api/assistant/route.ts:296` is
  `setTimeout(() => turn.abort(), …)` — abort only, no race. A real loop
  stuck in a non-abortable await hangs the turn and this test passes.
  **AC:** mock loop that ignores the signal and never resolves; assert the
  route still emits `done`/`error` within `ASSISTANT_TURN_WALL_CLOCK_MS + 1`,
  or document abort-only as the contract and rename the test.

- **T-361 [P2] (T-333 class, bundle) The three new loop contract files are
  identity greps over the wrapper text, and two of them never execute their
  wrapper.** `test_security_loop_contract.py:231-240` (rail 5 negatives);
  `test_ci_performance_loop_contract.py:65-82,164-188` and
  `test_documentation_loop_contract.py:65-103,184-229` are all
  `_uncommented()` + `in body` (`DEADMAN_LABEL="…"`, `gh label create …`,
  the sibling-lock path before `python3.13 -m venv` — an `echo` of the path
  satisfies it); `test_the_pre_reset_stands_down_on_a_live_lock`
  (`:147-159` / `:168-180` / security `:325-327`) asserts `kill -0` occurs
  in the plist string. The docs and ci-perf identity files execute nothing
  but `git check-ignore` and one `weekend_notify.py` run (`:88-111`,
  `:90`); their docstrings (`:11-14`) claim registration in the three
  shared `LOOPS` dicts (`test_weekend_loop_deadman.py:62-68`,
  `test_rel137…:34-40`, `test_weekend_wrapper_self_rewrite.py:44-64`) and
  no test checks it — dropping `"documentation"` from one dict loses every
  executed dead-man test for that loop green. Also
  `test_weekend_wrapper_self_rewrite.py:549` and `:560` parametrize four
  setups and omit `setup_ci_performance.sh`, whose guard (`:82-85` before
  `:104-106`) is therefore unpinned. **AC:** each identity file asserts its
  loop key is in each of the three `LOOPS`; run each setup with stubbed
  `gh`/`launchctl`/`python3.13` and a live `<sibling>/.weekend-runner.lock/pid`
  → exit ≠ 0 before `venv`; add `setup_ci_performance.sh` to both
  parametrizes (red on deleting `:82-85`).

- **T-362 [P2] `fetch_dispersion` is the one scheduled close ladder with no
  UW or Robinhood rung, and the R-434 test pins Yahoo-only as an `ok`
  heartbeat.** `scripts/fetch_dispersion.py:318-335` `fetch_closes_ladder`:
  IB → Yahoo. `scripts/tests/test_dispersion.py:881-899` asserts
  `source == {"prices": "yahoo", …}` with state `ok`. Rule 7 says Yahoo is
  never the only fallback for a series UW serves (S&P names, sector ETFs);
  `46897eec`'s "every close/quote ladder" has no cross-module pin. **AC:** a
  rung-order contract across ladder modules, or a documented exemption the
  test asserts.

- **T-363 [P2] Untested app-role side branches (bundle).** (a)
  `scripts/api/server.py:2117-2121` `/ib/reset-backoff` app-role
  `remote_gateway_action("reset-lease")` — `grep reset-lease` over the
  api/scripts test trees → only `cloud/tests/test_ib_gateway_control.py:124-130`
  (the helper, not this route). (b) `scripts/api/services.py:307-328`
  `show_unit` app-role: only the `ok+running` branch is driven
  (`test_services.py:108-129`); the unconfigured early return `:317-318`
  and the `"stopped"` branch `:324-326` are not. **AC:** one route case
  asserting `result["remote"]` and the `reset-lease` verb; two `show_unit`
  cases (`can_control False` / `inactive,dead`).

- **T-364 [P2] Widened-to-null close-out basis with no producer and no test
  (bundle).** `web/lib/order/risk/useOrderRisk.ts:715-720` linear
  `basis == null ? null : …` — every linear producer passes a number
  (`positionTrade.ts:421,439`; `OrderTab.tsx:402-477`;
  `ModifyOrderModal.tsx:466-489`; `BookTab.tsx:381`;
  `MobileOrderTicket.tsx:337,355`; `FuturesOrderForm.tsx:150` passes
  `closeOut: null`), so the branch is dead and untested.
  `web/components/mobile/MobilePositionList.tsx:57-60` `fmtEntryCost(null)`
  → `—` has no test (none of the seven `MobilePositionList` test files
  mention `mixed`). **AC:** a `MobilePositionList` render of the T-315
  `MIXED` fixture asserting `—`; delete the linear null branch or add a
  producer plus test.

- **T-365 [P2] (T-074 / T-089 / T-341 class, three NEW files) The mktnews
  suites let real timers decide negative assertions.**
  `scripts/mktnews/rel155-upstream-liveness.test.js:108-138` (server
  `setInterval(…,40)` ×6 against `idleTimeoutMs: 120`; `:132` asserts no
  `idle` event, `:133` `connections === 1`; `:158-163` sleeps 200 ms then
  asserts no idle after `stop()`);
  `upstream-down-fallback.test.js:185-191` (`flashPollMs: 25`, 15 ms
  frames, `setTimeout 200`, `historyCalls === 1`) and `:253-254`;
  `rel163-hub-bounds.test.js:112-121` (`pingIntervalMs: 50`, `:120` sleeps
  200 ms then `clientCount === 1`). Any event-loop stall > 120 ms between
  two 40 ms frames (coverage, shard load) fires the idle clock. One
  load-proof run (12 hogs) passed 7/7 before the lead stopped it (gate in
  progress); not proven, class-filed. **AC:** injected clock (the `delayFn`
  hook already exists at `rel155…:92`; extend to the idle timer) or fake
  timers; red when the client fires `idle` under a stalled loop.

- **T-366 [P2] `headlines-hook-unmount.test.tsx` passes vacuously if the hook
  gains one more await.** `web/tests/headlines-hook-unmount.test.tsx:61-66`
  resolves the deferred ticket then drains exactly three `Promise.resolve()`
  ticks; `:67-68` filters `MockWebSocket.instances` for `readyState !== 3`
  and asserts length 0. If the socket is never constructed (one extra
  await in `useHeadlines.open()`), `instances` is `[]` and the R-462 fix is
  green without executing. **AC:** `expect(MockWebSocket.instances).toHaveLength(1)`
  before the leak filter; red under a 4-await hook.

- **T-367 [P2] Two budget tests are wall-clock races on `SIGALRM` / real
  sleeps.** `scripts/tests/test_refresh_model_catalog.py:651-660`:
  `PROVIDER_BUDGET_S = 0.1`, provider stub `time.sleep(1.5)`,
  `assert elapsed < 1.0`; the budget is `signal.setitimer(ITIMER_REAL, …)`
  (`scripts/refresh_model_catalog.py:182`), main-thread only and shared
  with any `pytest-timeout` signal method; the 0.9 s margin is real time.
  `scripts/tests/test_dispersion.py:1161-1163` real
  `time.sleep(deadline - monotonic + 0.05)` then `:1181`
  `seen["remaining"] > 0` against a 0.5 s budget. **AC:** inject the
  deadline clock; red when the provider overruns the budget, green without
  sleeping.

- **T-368 [P2] (T-039 class; T-317's fix is one-third done) The
  `_strip_turso_credentials` autouse covers `scripts/tests` only, and
  `RADON_HOST_ROLE` — a per-call `os.environ` read — is scrubbed by no
  conftest.** `scripts/tests/conftest.py:177-213`;
  `scripts/api/tests/conftest.py:21-70` has three autouse fixtures, none
  touching `TURSO_*`; `cloud/tests/conftest.py` likewise;
  `scripts/api/server.py:94-97` `load_dotenv(web/.env)` at import and the
  api tree reaches `flex_embargo.active_until` at `server.py:4624-4626`
  and `:5655-5656`, which fails CLOSED under creds + pytest
  (`scripts/utils/flex_embargo.py:234-246`). No api test flips today
  (`test_flex_p2_routes.py` 4 passed with creds exported;
  `test_performance_background_cooldown.py:81,115` patch `active_until`),
  so the exposure is structural — the next unpatched reach of `:5655` reds
  only on hosts with `web/.env`. `RADON_HOST_ROLE`
  (`scripts/api/services.py:109-114`, `server.py:2049-2056`,
  `services.py:307-328`): every non-delta Gateway test is insulated only by
  `is_cloud_mode → False` pins (`test_ib_restart_backoff.py:103-104`,
  `test_ib_restart_2fa_lock.py:80-83`, …) — verified `RADON_HOST_ROLE=app`
  → 13 passed either way. The regression pin
  `test_weekend_runner_env_provisioning.py:263-269` is vacuous on any host
  without `web/.env` (CI). Three agents converged. **AC:** hoist the strip
  to a shared conftest reached by `scripts/api/tests` and add
  `delenv("RADON_HOST_ROLE")`; a test asserting `"TURSO_DB_URL" not in
  os.environ` inside `scripts/api/tests` is red today on a provisioned
  clone, and one asserting `host_role() == "combined"` with the var
  exported is red today.

- **T-369 [P2] (T-091 class, new files) The streaks e2e spec and unit test
  key on CSS classes and bare SVG tags where `aria-current` and testids
  belong.** `web/e2e/streaks-tab.spec.ts:100`
  `.regime-rail__item[data-tab="streaks"]` + `toHaveClass(/active/)` while
  `web/components/RegimeRail.tsx:70-71` emits `aria-current="page"`;
  `:117-118` `path[stroke]` / `rect.streaks-bar`;
  `web/tests/streaks-panel.test.tsx:237-242` `querySelectorAll("path")`,
  `rect.streaks-bar` `toHaveLength(16)`. **AC:**
  `getByRole("link", { current: "page" })` and testids on the bars.

- **T-370 [P2] The mTLS broker tests shell out to `openssl -addext` with no
  skip guard, mint five RSA-2048 keys per test, and never shut their
  servers down.** `scripts/tests/test_ib_gateway_remote.py:21-28` asserts
  `openssl` returncode 0 (no `shutil.which`/`skipif`); `:56-61,77-82` use
  `-addext`; `mint_mtls(tmp_path)` runs per test — measured
  `18 passed in 8.78s`, slowest 1.33 s. Servers are `serve_forever` daemon
  threads never `shutdown()` (`:134-137`); port is ephemeral (`:135`). A
  host whose `openssl` lacks `-addext` reds the whole `TestDaemon` class on
  the `_openssl` assert. **AC:** module-scoped cert fixture plus a guard
  whose red message names the missing tool; `shutdown()` in a fixture
  finalizer.

- **T-371 [P2] `vol-cone-api.test.ts` does not pin the bound its own comment
  states.** `web/tests/vol-cone-api.test.ts:137-150` names a ~73 h
  holiday-Monday gap but inserts a 60 h snapshot; the route budget is the
  catalog `closed` window `4 * DAY` (`web/lib/serviceHealthWindows.ts:271`,
  `web/app/api/vol-cone/route.ts:36`). A regression to 3 d keeps 60 h
  green. Not day-dependent. **AC:** add a 73 h case; red under a 3 d
  window.

- **T-372 [P2] `test_flex_sftp_pull.py:293-303` orders files by a 10 ms
  sleep** (pre-existing lines in a delta-touched file). `retain_newest_gpg`
  sorts on `st_mtime` (`scripts/flex_sftp_pull.py:230`); coarse-mtime
  filesystems collapse ties to glob order. **AC:** `os.utime` explicit
  stamps; red if the sort key regresses to name.

- **T-373 [P2] Migrations `0064` and `0065` stamp the wrong version literal
  (`63`), no test pins literal-to-filename, and ALTER-added columns are
  invisible to the only schema-pin walker.**
  `scripts/db/migrations/0064_dispersion_source.sql:13` and
  `0065_assistant_turns_provenance.sql:20` both
  `INSERT OR IGNORE INTO schema_migrations … VALUES (63, …)` (verified by
  the lead; `0066` correctly says 66). Harmless only because
  `scripts/db/migrate.py:203-208` records the version itself. `0063` adds
  `flex_deliveries.status`/`claimed_at` by `ALTER TABLE`; the only
  migration-directory enumerator,
  `scripts/tests/test_monitor_daemon/test_expiry_sweep.py:628-640`
  (`_table_columns`), parses `CREATE TABLE` bodies only; the sqlite replay
  tests apply a hand-picked list (`test_db_readers.py:35-39`);
  `test_migration_partial_alter.py:3` still says "0050 is the only real
  ALTER TABLE". **AC:** a test that, for each `NNNN_*.sql` containing
  `INSERT … schema_migrations … VALUES (n`, asserts `n == NNNN` — red on
  0064/0065 today; extend `_table_columns` to fold `ALTER TABLE … ADD
  COLUMN`.

- **T-374 [P2] The 30 s default fetch bound in both taggers (the REL-165
  incident constant) is exercised by no test.** `scripts/newsfeed/tagger.js`
  and `vision_tagger.js` `DEFAULT_FETCH_TIMEOUT_MS = 30_000`,
  `signal: AbortSignal.timeout(timeoutMs)`; `web/tests/newsfeed-tagger.test.ts:31-136`
  `mockResolvedValue` and never reads `init.signal`; the delta's `:283-310`
  injects `timeoutMs: 50`, so the default is a free variable. **AC:** build
  the tagger with NO `timeoutMs`, assert `init.signal` is an `AbortSignal`
  and (fake timers) aborts at 30 000 ms; red if the default is dropped.

- **T-375 [P2] `.radon-security-runner` is not gitignored; the marker
  survives only by one `--exclude`.** `.gitignore:272` ignores
  `.radon-weekend-runner` only (`git check-ignore -q .radon-security-runner`
  → rc 1, verified); the security clone shows `?? .radon-security-runner`.
  The wrapper's `ground_truth` excludes it (`security_nightly.sh:340`), but
  any agent-side `git clean -fd` / `git stash -u` deletes it → every
  subsequent fire exits 2 at `:266-270` (reported and paged, hence P2), and
  SKILL rail 12 ("dirty shared state → OPERATOR_REQUIRED") faces a
  permanently dirty tree. **AC:** `git check-ignore -q .radon-security-runner`
  exits 0.

- **T-376 [P2] Five nightly loops are pinned to 00:00 by their own tests;
  the collision is asserted, the cadence never decided.**
  `test_security_loop_contract.py:322`,
  `test_documentation_loop_contract.py:160`,
  `test_ci_performance_loop_contract.py:139` each assert
  `StartCalendarInterval == {"Hour":0,"Minute":0}`; `launchctl list` shows
  all five `com.radon.*-daily` loaded. Five concurrent `claude -p` agents
  plus this loop's full gate on one Mac mini; `ci_performance_nightly.sh:19-21`
  concedes local timings are contaminated by it, and this run's round 1
  pytest took 720 s (vs ~275 s) for the same reason. **AC:** a test over
  `PLISTS` asserting the five `(Hour, Minute)` tuples are pairwise
  distinct, or a recorded decision to keep 00:00 that the test cites.

- **T-377 [P2] No setup script checks for coreutils `timeout`; every test
  stubs it.** `net_bounded` (`security_nightly.sh:93`) and the round launch
  (`:413`) require GNU `timeout` (`/opt/homebrew/bin`, plist PATH
  `config/com.radon.security-daily.plist:27`); the `[1/4] toolchain`
  blocks (`setup_security_nightly.sh:60-69`, `setup_ci_performance.sh:54-63`)
  never `check "timeout"`; stubs at `test_weekend_wrapper_self_rewrite.py:163-178`,
  `test_security_loop_contract.py:151-154`. On a rebuilt host without
  coreutils, `ground_truth` fails 3× (`:329`) and the `gh` comment fails
  127 behind `|| true`. **AC:** run each setup with a PATH lacking
  `timeout`; red unless output contains `MISSING  coreutils timeout`.

- **T-378 [P2] (delta to T-073 / T-090 / T-116) The new `streaks-tab.spec.ts`
  went to the ledger backlog, not the curated CI list, and `deploy` still
  accepts `skipped` for every test job.** `web/e2e/ci-curation-ledger.txt:155`;
  `.github/workflows/ci.yml:677-696` (19 specs, unchanged since base);
  `ci.yml:849-863` (`needs`/`if` accept `skipped`); `ci.yml:672`
  (`e2e-financial-smoke` "NOT in deploy.needs yet"). Reported only because a
  new page-route spec landed. **AC:** `test_e2e_ci_curation.py` gains an
  assertion that a spec whose page route is new must be curated (or that
  the untriaged ledger section did not grow); green — `streaks-tab.spec.ts`
  in the `ci.yml` arg list and out of the ledger.

## Remediation 2026-09-02

**Two consecutive cycles produced no audit — the range `39bf6f5e..db25990d`
(32 commits / 259 files / +26958-1135) is UNAUDITED.** The next audit must
take `39bf6f5e` (the 2026-08-31 ledger SHA) as its base so the range is
re-covered. It includes the profile/credential-store overhaul (#125), the
hosted MCP endpoint (#234), the MA RATIO tab (#235), two orders-surface fixes
(#236, #237) and the nightly-wrapper billing rework (#238, #239).

- **2026-09-01:** both phases exited 1 four seconds in — `claude -p` refused
  with "out of usage credits" before any work; the subscription-only billing
  fix (#238/#239) merged later that day, after the fire. The wrapper pushed
  `origin/testing/2026-09-01` empty and reported FAILED on all three
  dead-man channels, correctly. No T-number: the cause was fixed in-range.
- **2026-09-02 (this cycle):** the audit phase computed the delta, launched
  its serial gates as a detached script, then exited 0 after ~4 minutes on a
  text-only progress message ("Gates at 53% on pytest") with ZERO findings
  drafted — the T-379 shape recurring verbatim despite the 2026-08-31 skill
  rail. T-379's wrapper detection WORKED: the phase was posted as
  `INCOMPLETE (agent exited 0 without committing to the nightly branch)` at
  07:06:32Z. Unlike 2026-08-31 there is no findings draft to land;
  `/tmp/tw-2026-09-02/` holds only delta scaffolding plus the still-running
  gates script, which this remediation adopted as its round 1.

**Backlog state at the start of this remediation: zero un-DONE P0 or P1.**
T-346…T-352 and T-379 all landed 2026-08-31 via PR #213 (squash `4584e84a`,
merged and deployed). So, per the 2026-08-28 precedent, this run files one
new finding against the recurrence itself (T-380, below) and works the
newest **P2** stragglers (T-353…T-378) in value order. Evidence rows land in
`TEST_LOG.md` under `## Remediation 2026-09-02`.

### New finding

- **T-380 [P1] (T-239 → T-379 recurring; third lost audit night in six fired
  cycles) The dead-man now DETECTS a zero-commit phase but nothing RECOVERS
  it — an INCOMPLETE audit still forfeits the whole night's audit, and the
  skill-rail fix alone demonstrably does not prevent the exit.**
  `scripts/testing_weekend.sh` — T-379 (`743408fd`) downgrades a zero-commit
  OK to `INCOMPLETE (…)` and reported it correctly tonight at 07:06:32Z, but
  the wrapper then proceeds straight to remediate; the unaudited range
  compounds (now 32 commits). Lost audit nights: 2026-08-28 (T-239),
  2026-08-31 (T-379, draft recovered), 2026-09-02 (nothing to recover). The
  2026-08-31 Lessons rail ("wait INSIDE a tool call") was in tonight's
  prompt and the agent still replied with text mid-gates.
  **AC:** RED — in `test_weekend_loop_deadman.py`'s real-git harness, a stub
  `claude` whose first audit invocation prints-and-exits-0 without
  committing and whose second invocation commits: today's wrapper runs the
  audit once, posts INCOMPLETE, never retries → red. GREEN — the wrapper
  retries the audit phase exactly once when (and only when) the dead-man
  check downgrades it to INCOMPLETE, within the same wall-clock cap; a retry
  that also lands nothing still posts INCOMPLETE (no third attempt);
  TIMEOUT / FAILED / TRUNCATED arms and exit codes unchanged.

- **T-381 [P1] (found by this run's adopted gate round 1; T-317/T-277 env
  class, but LEAKS instead of reds) `test_notify_cred_reads_a_crlf_env_file`
  inherits the runner's environment, and because `_notify_cred` prefers an
  exported `PUSHOVER_*` over the env file BY DESIGN, a wrapper-launched
  pytest prints the runner's LIVE Pushover token and user key into the gate
  log.** `scripts/tests/test_nightly_issue_format.py:519-526` ran
  `subprocess.run(["/bin/bash", script], …)` with no `env=`;
  `security_nightly.sh` `_notify_cred` returns `${!key}` first. Under the
  wrapper (which exports the real credentials to page) the assertion diff
  embedded both live values in `/tmp/tw-2026-09-02/gates/pytest-r1.txt` —
  the only red in a 10,764-test round, `93 passed` in isolation, so it also
  false-reds every wrapper-launched full gate. **Fixed this run** (same
  commit): explicit `env={"PATH": …}` on the subprocess. RED reproduced
  with dummy exports (`PUSHOVER_TOKEN=env-wins-tok … → 1 failed`); GREEN
  `1 passed` + full file `93 passed` with and without the planted env.
  Residual: the live values sit in this run's local gate log under
  `/tmp/tw-2026-09-02/` (root-only host, never committed); operator MAY
  rotate the Pushover token if that residue is a concern.

### Observations and notes for the next audit — recorded, not chased

1. **vitest r1 exited 1 with 845 files / 8526 passed / 0 failed** — one
   `EnvironmentTeardownError: Closing rpc while "onUserConsoleLog" was
   pending`, attributed to `regime-dead-feed-degraded.test.ts`, under load
   33 with the reliability loop concurrent; 3/3 clean in isolation. Load
   class (T-062 family), but note the SHAPE: a teardown race reds the gate
   EXIT CODE while every test passes, so a wrapper keying on exit alone
   would call this a red round.
2. **`npx next build` (default mode) fails prerendering `/cta` and
   `/dashboard` at this HEAD** — only the repo's compile-mode `npm run
   build` works. Surfaced by the T-369 e2e pre-flight, reproducible,
   untriaged. The next audit should number it if it holds.
3. **The T-380 retry interacts with every wrapper-inspecting suite** whose
   stub `claude` exits 0 without moving HEAD — the model-ladder stubs had
   to learn to "commit" (move a fake HEAD) or their exact model sequences
   gain a duplicate final rung. Sibling loops adding the same retry will
   hit the same fixture assumption.

## Delta audit 2026-09-03

Range `39bf6f5e..HEAD` (`0202e32d`): 49 commits / 340 files / +41249−1406.
Thursday run; no weekend-date hazard live. Five read-only agents (money-path,
cash-flow pipeline, fragile+blast-radius, gate drift, MCP/loop-infra); every
P0/P1 below was re-read at the cited lines by the lead before numbering.
CI on `main` is green at this HEAD; the red runs visible at audit time were
all on the reliability loop's PR branch. 27 new findings: 1 P0, 10 P1, 16 P2
(T-382…T-408; T-408 numbered after the standing build-probe re-triage).

### T-382 · P0 — `_mirror_ib_orders_snapshot` is a DELETE+INSERT replace of `open_orders` whose body has zero test coverage; a slow gateway writes an empty book as truth
`c0fd81d6` added the mirror at `scripts/monitor_daemon/handlers/fill_monitor.py:285-305`,
fired on every fill (`:272-274`). It calls `fetch_open_orders(client)` →
`save_orders(...)`, a full snapshot replace of `open_orders`
(`scripts/ib_orders.py:280-287`, DELETE+INSERT). The source set is
`IBClient.get_open_orders` (`scripts/clients/ib_client.py:877-898`), which
waits on `openOrderEndEvent` hard-capped at **0.5 s** and returns
`openTrades()` regardless — a slow gateway on fill_monitor's short-lived
connection returns `[]`, which the mirror writes as truth, wiping every
working order off `/orders` during exactly the extended-hours window the
commit exists to cover; the operator seeing an empty working book can
double-place. The handler swallows failures by design (`:303-305`).
Every test patches the method out: autouse disable at
`scripts/tests/test_monitor_daemon/test_fill_monitor.py:22-31`; the three
"mirror" tests assert only `mock_mirror.assert_called_once_with(...)`
(`:196-201`, `:225`, `:238`). The body (fetch → build → save) never executes
under test.
**Red:** stub `fetch_open_orders_for_mirror → []` while
`fetch_executed_orders_for_mirror` returns one fill; assert
`save_orders_snapshot` is not called (or preserves the prior open set) —
fails today. **Green:** an incomplete/timed-out book never replaces a
non-empty snapshot; the guard is itself under test.

### T-383 · P1 — stock close card derives a 100× wrong entry price; the new test asserts only `pnlPct`
`085d2254` made `closedGroupCloseCash` non-null for stock-only groups
(`web/components/WorkspaceSections.tsx:334-350`, STK ×1), feeding the
fully-closed fallback `entryPrice = -(openCash / 100) / comboUnits`
(`:474-481`) whose `/100` is an unconditional option multiplier. The
commit's own fixture (AAPL 100 sh @ 252.50, `web/tests/share-pnl.test.ts:329-348`)
yields `entryPrice = 2.475` against a true $247.50; before the commit the
card rendered blank. The delta test asserts only `pnl`/`pnlPct` (`:346-348`);
`entryPrice` is returned unasserted.
**Red:** `expect(data.entryPrice).toBeCloseTo(247.5, 2)` on that fixture —
fails today at 2.475. **Green:** line ~478 divides by a per-group multiplier
(100 only when the group carries an OPT/BAG fill).

### T-384 · P1 — mixed OPT+STK fill group blends ×100 and ×1 cash into one denominator, untested
`closedGroupCloseCash` now accepts STK (`WorkspaceSections.tsx:334-337`);
symbol-window bucketing (`:656-677`, ±60 s when no durable correlation)
puts an option close and a stock close of the same ticker in one group,
summing across multipliers into `closeCash` → `pnlPct`/`entryPrice`. Every
delta test uses a homogeneous group (`share-pnl.test.ts` has exactly one
`secType: "STK"` hit, `:340`).
**Red:** a group of one OPT SLD (1 lot @ 5.00) + one STK SLD (100 sh @ 250)
same minute, no `orderRef`; assert the exact expected `pnlPct` under
per-fill multipliers — today it silently blends. **Green:** per-fill
multiplier arithmetic pinned.

### T-385 · P1 — the R-431 stock-cap fix is proved only against a hand-built fixture — the exact failure mode the commit blames
`bdbe31de`'s `scripts/api/tests/test_modify_snapshot_contract_shape.py:33-58`
hand-builds "a working stock order exactly as `fetch_open_orders`
serializes it" without executing the serializer. Nothing binds
`serialize_contract` (`scripts/ib_orders.py:80-121`) to
`_working_order_shape`'s readers (`scripts/order_limits.py:329-334`).
Consequences: serializer legs carry no `secType` key, so the STK-leg
exemption at `order_limits.py:299-300` can never match a real snapshot leg
(untested either way); and the only `/orders/modify` limit route test posts
with no snapshot present (`scripts/api/tests/test_order_limits_routes.py:58-69`),
exercising the `None` fallback, not the stock-cap branch — no full-URL wire
test of the changed money-path gate (`scripts/api/server.py:3206-3216`).
**Red:** (a) run `fetch_open_orders` over a stub ib_insync trade, feed the
row to `_working_order_shape`, assert `("stock", None)` / `("combo", legs)`
— reds the moment a serializer key moves; (b) route test: seed the snapshot
with that serialized row, POST `/orders/modify` `{"orderId": 41,
"newQuantity": 10000}` → 200 + `--new-quantity` spawn; 60000 → 422
`ORDER_QTY_LIMIT`. **Green:** both pass end-to-end from serializer output.

### T-386 · P1 — `Providers` keyless early-return silently drops the realtime tree; the e2e suite's socket coverage is ambient-env-dependent
`web/components/Providers.tsx:18-23`: module-scope `CLERK_CONFIGURED`;
when falsy everything below — including `RealtimePricesProvider` (`:35`),
since 0f7e66bf the sole socket owner — is skipped.
`web/playwright.config.ts:77-81` sets no Clerk key, so whether
`ws-connection-stability.spec.ts`, `day-move-ib-daily-pnl.spec.ts`,
`spread-price-bar.spec.ts` etc. exercise a live socket depends on the
runner's ambient `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` / `web/.env`.
**Red:** boot the e2e webServer with the key unset; a live-prices spec
should fail loudly (today it degrades to placeholders). **Green:**
`playwright.config.ts` pins a `pk_test_*` stub in `webServer.env` plus a
contract test asserting the key is present in that env block.

### T-387 · P1 — the setup-mode gate runs before the authless test bypass, 503/302-ing every e2e request on a keyless runner
`web/middleware.ts:459` calls `handleSetupModeGate` before the authless
bypass at `:470`; `isSetupMode` (`web/lib/setup/setupMode.ts:28-35`) is
true whenever both Clerk keys are blank and `RADON_SETUP_COMPLETE` unset —
so on a keyless runner every page 302s to `/setup` and every `/api/*` call
503s `SETUP_MODE` before the Playwright token is consulted. Same root
cause as T-386, different blast surface.
**Red:** `handleSetupModeGate` on `/portfolio` with no Clerk env and a
valid `x-radon-authless-test` header returns a redirect today. **Green:**
bypass ordered ahead of (or excluded from) the setup gate, pinned in
`web/tests/middleware-authless.test.ts`.

### T-388 · P1 — `portfolio-startup-performance-contract.test.ts` mocks a hook the component no longer calls; the test silently flipped branches
`web/tests/portfolio-startup-performance-contract.test.ts:92-93` mocks
`@/lib/usePrices` with `connected: true`, then renders `WorkspaceShell` —
which since 0f7e66bf calls `useRealtimePrices()`
(`web/components/WorkspaceShell.tsx:17,267`) and, with no provider in the
tree, receives the context default `connected: false`
(`web/lib/RealtimePricesContext.tsx:76-90`). The mock is dead; the test
now measures the disconnected branch while green. File untouched in the
delta (blast radius).
**Red:** assert the shell observed `connected === true` — fails today.
**Green:** wrap in `RealtimePricesProvider` with a stubbed value, or mock
`@/lib/RealtimePricesContext`.

### T-389 · P1 — the new socket-ownership contract test asserts substrings, not its claims
`web/tests/realtime-socket-ownership-contract.test.ts:28-35`: "mounted in
the root Providers tree" is satisfied by the import line at
`Providers.tsx:8` even though the JSX sits below the `CLERK_CONFIGURED`
early return (blind to T-386); "exactly one production call site" asserts
presence, not uniqueness (a second `usePrices()` caller stays green).
**Red:** repo-wide scan for `/\busePrices\s*\(/` across `components/**` +
`lib/**` expecting exactly one hit, plus an assertion that `Providers.tsx`
has no early `return` before `RealtimePricesProvider` — both red today (the
early return exists). **Green:** both pass on a fixed `Providers.tsx`.

### T-390 · P1 — "store wins over `.env`" is a docstring with zero coverage
`scripts/api/routes/credentials.py:320-350`: line 347 unconditionally
`os.environ[name] = value`, but every test deletes the conflicting env var
first (`scripts/tests/test_rel189_credential_store_durability.py:137-138`,
`scripts/api/tests/test_credentials_routes.py:131,154,172`);
`test_env_fallback_flagged` (`:117-129`) asserts display flags only.
Inverting line 346 to `if value and name not in os.environ:` keeps the
whole suite green while every rotated credential silently does nothing
until reboot.
**Red:** set `os.environ["UW_TOKEN"]="stale"`, store a different value,
call `bootstrap_exported_names()`, assert env holds the stored value; add
the PUT-path mirror (`credentials.py:266`). **Green:** passes; the
inversion mutation reds it.

### T-391 · P1 — the internet-facing `/mcp` route has no request-body bound and no test that would notice
`cloud/caddy/Caddyfile:78-86` has timeouts only; no `request_body max_size`
anywhere in the repo. Existing bounds are on the wrong axis
(`scripts/mcp_hosted/server.py:65,100-102` upstream response;
`auth.py:35,137` JWT header). `stateless_http=True` hands Starlette the
full body pre-auth under `MemoryMax=512M` — an anonymous POST is a cheap
OOM-kill. `cloud/tests/test_caddyfile.py` asserts six properties of the
block, none about size; `scripts/tests/test_mcp_hosted.py:398-448` posts
only small well-formed JSON-RPC. (Product defect flagged by the
reliability loop's 2026-09-02 PR #243; this entry is the test-suite gap.)
**Red:** a `test_caddyfile.py` assertion via the existing `handle_block()`
helper that the `/mcp*` block contains `request_body max_size` ≤ 1MB, and a
transport test posting an oversized body asserting 413 pre-execution —
both fail today. **Green:** both pass, scoped to the `/mcp*` block.

### T-392 · P2 — wall-clock sleep before interaction in a delta-modified e2e spec
`web/e2e/mobile-order-ticket.spec.ts:329` `waitForTimeout(400)` to let the
ATM auto-center settle before `cell.hover()` (the 600 ms at `:332` is a
legitimate long-press dwell). **Red/green:** wait on the settled state
(stable `boundingBox()` poll or a `data-atm-centered` testid).

### T-393 · P2 — three new subprocess launches inherit the runner env while siblings in the same file pass `env=` (T-381 class)
`scripts/tests/test_github_pr_output.py:199-204`,
`scripts/tests/test_nightly_issue_format.py:280-295,898-902` — no `env=`;
siblings at `:555,:656,:743,:826,:870,:993` pass it. No proven leak today
(children are pure formatters), but any future `set -x`/traceback dumps
`GH_TOKEN`/`ANTHROPIC_API_KEY` into `capture_output` and CI logs.
**Red/green:** minimal explicit `env=` at the three sites.

### T-394 · P2 — `regime-spy-subscription.test.ts` narrates the pre-0f7e66bf wiring and passes by string accident
`web/tests/regime-spy-subscription.test.ts:31-33` asserts
`source.toContain("symbols: allSymbols")`, now satisfied by
`publishSubscriptions({ symbols: allSymbols, ... })` at
`WorkspaceShell.tsx:278`, not a `usePrices` call. Whole file is source-text
grep. **Red/green:** assert `publishSubscriptions` is the recipient,
render-based.

### T-395 · P2 — stale count comment in the regime tab test
`web/tests/regime-rail.test.tsx:204` still reads "not all 22 REGIME_TABS"
against 30 at `:44-49`/`:167-175`; third stale number in that comment's
history. **Red/green:** derive the phrase from `REGIME_TABS.length` or
drop the number.

### T-396 · P2 — the "applied duplicate heartbeats ok" test asserts a contract the source does not implement
`scripts/flex_delivery_ingest.py:99-115` heartbeats `ok` for any status
`!= "in_progress"`, including `None` (lost claim + vanished row);
`scripts/tests/test_cash_flows_from_sftp_delivery.py:124-138` stubs
`"applied"` and is green on the weaker implementation.
**Red:** `flex_delivery_status → None` asserting `heartbeats == []` fails
today; passes once the source reads `== "applied"`.

### T-397 · P2 — daemon-registration test is a module-wide AST grep that misses three registration shapes
`test_cash_flows_from_sftp_delivery.py:37-49` collects only
`register(Name(...))` literals: false-greens on `h = …; register(h)`,
`register(handlers.CashFlowSyncHandler())`, and factory registration.
**Red:** build the daemon and assert `"cash-flow-sync" not in
{h.service_name for h in daemon.handlers}` — reds on all three variants.

### T-398 · P2 — the capacity-shed marker is a byte literal in three places; cross-boundary drift is invisible
`scripts/api/server.py:465` defines it; `scripts/run_garch_refresh.sh:113`
and `scripts/tests/test_garch_capacity_shed_retry.py:36` (plus
`test_leap_capacity_shed_retry.py:43`,
`test_flow_refresh_shed_honesty.py:190,264`,
`test_leap_garch_no_duplicate_scan.py:85`) re-declare it. Changing
`server.py` alone regresses garch/leap/flow to "indeterminate → P1 page"
with all suites green. **Red:** a parity test asserting
`server._CAPACITY_SHED_MARKER` equals the string the shell scripts grep.

### T-399 · P2 — `test_leap_garch_refresh_defaults.py:33-36` additions are source-string mirrors
Asserting the shell script contains its own text; the behavioural
equivalent exists at `test_garch_capacity_shed_retry.py:244-261`. The one
marginal value (240 s default fits `TimeoutStartSec=3900`) belongs as
arithmetic against the unit file.

### T-400 · P2 — no python↔TypeScript parity test for the widened `cash-flow-sync` health window
`scripts/watchdog/services.py:82-88` and
`web/lib/serviceHealthWindows.ts:118-125` hand-mirror `open: 3 * DAY`; the
parity test at `scripts/tests/test_cadence_and_growth_bounds.py:144-151`
covers `SIGNALS_SERVICES` only. Each side has its own 73 h test, but the
two can diverge with both suites green. **Red/green:** add
`cash-flow-sync` to the parametrized parity set.

### T-401 · P2 — ~57 tests pin the scheduling behaviour of a handler production no longer registers
`scripts/monitor_daemon/run.py:107-112` dropped `CashFlowSyncHandler`
(module marked "NOT REGISTERED since 2026-09-02" at
`handlers/cash_flow_sync.py:3-10`), but
`test_cash_flow_sync_cadence.py` / `_exit_codes.py` /
`_timeout_retry_budget.py` still assert the 08:00 ET window, breaker and
backoff of a path never entered. Green while cash flows are broken.
**Red/green:** trim to the R-104/R-108/R-109 embargo contracts or mark the
files as covering a retired path.

### T-402 · P2 — mtime-tie flake in the new prune test on coarse-mtime filesystems
`scripts/tests/test_flex_sftp_pull.py:519-531` writes five files 10 ms
apart; `retain_newest_gpg` (`scripts/flex_sftp_pull.py:233-238`) sorts
`iterdir()` by `st_mtime` — 1 s-granularity filesystems tie and `iterdir`
order decides the exact-name assertion. APFS/ext4 fine; latent CI-image
dependency. **Red/green:** set explicit distinct mtimes via `os.utime`.

### T-403 · P2 — the three cross-tree contract jobs resolved `skipped` on the gating main run while listed in `deploy.needs`
Run 33695685875 (`0202e32d`): `Cross-tree contracts (root)/(cloud)/(scripts)`
all skipped (path-filter-driven empty set, `ci.yml:172,196,217`) yet deploy
ran via the `!cancelled() && result == 'success'` idiom (`ci.yml:914-917`).
Not new drift; flagged because a routinely-skipping `needs:` entry is
indistinguishable from one that stopped working. **Red/green:** a contract
asserting the skip reason is the path filter (outputs empty), not a broken
invocation.

### T-404 · P2 — the `MAX_RESPONSE_BYTES` truncation raise has never executed
`scripts/mcp_hosted/server.py:100-102` raises `ValueError` inside a tool
body; every test injects `FakeHttp` (`test_mcp_hosted.py:85-93`) bypassing
`_http_get`. Under FastMCP the raise surfaces as a transport error, not
the module's `{"error", "status"}` envelope. **Red:** `_http_get` against
a localhost fixture serving `MAX_RESPONSE_BYTES + 1` asserting the
envelope; a 2 MiB-exact body still returns `{"data": ...}`.

### T-405 · P2 — the MCP loopback bind is tested as a default, not a deployment
`scripts/mcp_hosted/server.py:236-237` reads `RADON_MCP_HOST` with no
allowlist; `test_mcp_hosted.py:362-365` asserts the default only. The real
defence is unit-file line ordering (`Environment=` after
`EnvironmentFile=` in `cloud/services/radon-mcp.service`), untested.
**Red:** assert the `Environment=RADON_MCP_HOST=` line index exceeds the
`EnvironmentFile=` index; swapping the lines reds it.

### T-406 · P2 — `/design.md` is contract-tested as a file, never as a URL
`web/tests/design-artifact-contract.test.ts:111` asserts disk existence;
`middleware-auth.test.ts:85-89` asserts the matcher exemption; nothing
asserts Next actually serves `.md` from `public/` (the fragile bit
`middleware.ts:173-178` itself names). **Red/green:** route-level GET of
`/design.md` asserting 200 + a known `rd-*` class in the body.

### T-407 · P2 — any `closeOut` suppresses the entire Gate-3 critical banner; the proportion policy is undecided and untested
`31c39238`: `web/lib/order/risk/OrderRiskGate.tsx:213-220` →
`web/lib/correlationRiskBanner.ts:95-105`. Callers gate `closeOut` on held
size (`ModifyOrderModal.tsx:452-471`, `positionTrade.ts:402-405`) so
over-close/flip is bounded, but a 1-share trim of a 10,000-share leg
suppresses a 73 %-cluster banner. `resolveCorrelationOrderContext` has no
direct test. **Red/green:** pin the intended token-reduce policy in
`web/tests/correlation-risk-banner.test.ts`.

### T-408 · P1 — the web tree does not type-check, and no gate runs the type checker
`npx next build` (default mode) fails at "Running TypeScript" with
`./app/api/setup/complete/route.ts:42:9 — Type '"SETUP_ALREADY_COMPLETE"'
is not assignable to type 'ErrorCode | undefined'` (same literal also at
`web/app/api/setup/validate/route.ts:29` and `status/route.ts:26`, from
the b0322b6f setup wizard). The repo's `npm run build` is
`next build --experimental-build-mode=compile` (`web/package.json:14`),
which skips type checking, so CI builds, tests and deploys green over a
tree `tsc` rejects. This re-triages the 2026-09-02 note-2 standing item:
the prerender failure could not be reached because the build now dies
earlier, at types.
**Red:** `npx tsc --noEmit` (or default-mode build) in `web/` — fails
today. **Green:** `ErrorCode` gains the literal (or the routes use an
existing code) and a CI step or vitest contract runs the type check so
the gap cannot reopen.

### Backlog rows

| ID | Sev | AC (red → green) |
|---|---|---|
| T-408 | P1 | `tsc --noEmit` green in `web/` and enforced by a gate; red today at `app/api/setup/complete/route.ts:42`. |
| T-382 | P0 | Empty/timed-out `get_open_orders` book must not replace a non-empty `open_orders` snapshot; mirror-body test executes fetch→build→save with a stub client. Red: stub empty book asserts no destructive save (fails today). |
| T-383 | P1 | `share-pnl.test.ts` stock-only fixture asserts `entryPrice ≈ 247.50` (red at 2.475); source uses per-group multiplier. |
| T-384 | P1 | Mixed OPT+STK group asserts exact `pnlPct` under per-fill multipliers (red: blended today). |
| T-385 | P1 | Serializer-fed `_working_order_shape` contract + `/orders/modify` full-URL route test with seeded snapshot (200/422 pair). |
| T-386 | P1 | `playwright.config.ts` pins a Clerk stub key in `webServer.env`; contract test asserts it (red today: absent). |
| T-387 | P1 | Authless bypass ordered ahead of setup gate; pinned in `middleware-authless.test.ts` (red today: 302 to /setup). |
| T-388 | P1 | Startup-perf contract asserts `connected === true` observed (red today); provider-wrapped render. |
| T-389 | P1 | Ownership contract: exactly-one `usePrices(` call site repo-wide + no early return before `RealtimePricesProvider` (red today). |
| T-390 | P1 | Bootstrap precedence test: stale env + stored value → env holds stored value; inversion mutation reds it. |
| T-391 | P1 | `/mcp*` `request_body max_size` Caddyfile assertion + oversized-POST 413 transport test (both red today). |
| T-392 | P2 | Replace `waitForTimeout(400)` with settled-state wait. |
| T-393 | P2 | Explicit `env=` at the three subprocess sites. |
| T-394 | P2 | Rewire `regime-spy-subscription` to assert `publishSubscriptions` recipient. |
| T-395 | P2 | Derive or drop the "22 tabs" comment number. |
| T-396 | P2 | `flex_delivery_status → None` asserts no heartbeat; source narrows to `== "applied"`. |
| T-397 | P2 | Behavioural registration check via `daemon.handlers` service names. |
| T-398 | P2 | Shed-marker parity test across server.py and the shell wrappers. |
| T-399 | P2 | Replace string mirrors with unit-file arithmetic (240 < TimeoutStartSec). |
| T-400 | P2 | Add `cash-flow-sync` to the py↔TS window parity parametrization. |
| T-401 | P2 | Trim or re-label the retired-path cadence/exit/retry test files. |
| T-402 | P2 | Explicit `os.utime` mtimes in the prune test. |
| T-403 | P2 | Contract distinguishing path-filter skip from broken contract job. |
| T-404 | P2 | Execute `_http_get` oversize path; assert error envelope. |
| T-405 | P2 | Unit-file line-order assertion for `RADON_MCP_HOST`. |
| T-406 | P2 | Route-level GET `/design.md` → 200 + `rd-*` body. |
| T-407 | P2 | Pin token-reduce banner policy in `correlation-risk-banner.test.ts`. |

### Standing-item re-triage

- **`npx next build` default-mode prerender failure (2026-09-02 note 2):**
  re-probed at this HEAD; result recorded in the ledger line below.
- **vitest teardown-race exit-code shape (note 1):** did not recur; round 1
  vitest exited 0, 8598 passed.
- **T-311 `orders-place-cache-race`:** not re-run this audit; remains open
  on the backlog.


## Delta audit 2026-09-04

Range `0202e32d..2b936ebc` — 50 commits / 282 files / +17076−1128. Base is the
2026-09-03 ledger HEAD. Run on a **Friday**, so the T-117/T-248 weekend-false-red
class is dormant today and was reasoned about rather than observed.

Five read-only rubric agents over the delta plus its blast radius; every P0/P1
below was re-read at its cited line by the lead before numbering. The
reliability loop was mid-gate in its own clone (`~/radon-weekend/radon`) for the
whole run, so gate rounds here are load samples — see the ledger line.

31 new findings: 2 P0, 10 P1, 19 P2 (T-409…T-439).

### P0

**T-409 — `web/tests/realtime-socket-ownership-contract.test.ts:61-75` pins T-389 back open.**
The test named "mounts RealtimePricesProvider on EVERY boot path" asserts only
that the *text offset* of `<RealtimePricesProvider>` precedes the first `return`
in `web/components/Providers.tsx`. The source was since refactored so the whole
tree lives in a `const core` declared above both returns
(`Providers.tsx:23-40`), so `mountAt < firstReturnAt` is now unconditionally
true. Change the keyless branch at `Providers.tsx:41-43` from
`return <ThemeProvider>{core}</ThemeProvider>` to
`<ThemeProvider>{children}</ThemeProvider>` — the exact T-389 regression, the
realtime tree dropped on the no-Clerk-key boot path — and the test still passes.
No test renders `Providers` with the key unset; `web/tests/route-refresh-provider-wiring.test.tsx:21`
forcibly stubs a key in.
*AC:* render `<Providers>` twice with `vi.resetModules()` between (key set, key
unset) and assert `useContext(RealtimePricesContext)` is non-default in BOTH.
Red today by deleting `{core}` from either branch; a source-offset assertion
cannot go red that way.

**T-410 — `scripts/monitor_daemon/handlers/exit_orders.py:709-718`: the refusal latch heartbeats `ok` while the position is unprotected.**
On the first limit refusal the handler calls `_note_error` and sets
`self._limit_refusals[refusal_key]` (`:738-747`), so the cycle correctly
heartbeats `error`. From the SECOND cycle onward the latch at `:709` matches,
increments `result["orders_skipped"]` and `continue`s **without** calling
`_note_error`. `scripts/monitor_daemon/handlers/base.py:222-227` derives the
service-health state solely from `result["error"]`, so `exit-orders` reports
`ok` while the target/stop is still unplaced — precisely the condition the
adjacent comment at `:737-738` says must page ("T-313: the position stays
unprotected, so this cycle must heartbeat error, not ok"). The first cycle's
page is a one-shot; the standing condition is silent.
*AC:* drive `execute()` twice with a `check_order_limits` stub that always
returns a violation; assert the SECOND `result["error"]` is non-empty and the
recorded cycle health is `error`. Red today — the second call returns no
`error` key. This is a source defect, not only a coverage gap; fix the source
and keep the test.

### P1

**T-411 — `scripts/tests/test_monitor_daemon/test_exit_orders.py:557-563` is a tautology on a builtin.**
`test_a_refused_leg_is_latched_until_params_change` constructs the handler,
writes `handler._limit_refusals[key] = (9000, 3.5)` itself, then asserts
`dict.get(key)` returns what it just stored and does not equal a different
tuple. No line of `exit_orders.py` after `__init__` executes. Deleting the
entire latch block from the source leaves this test green. It is the only test
naming the latch, which is why T-410 shipped.
*AC:* replace with the two-cycle `execute()` drive from T-410.

**T-412 — `exit_orders.py:704-707`: `refusal_key` collides when `journal_trade_id` is missing.**
The key is `(order_info.get("journal_trade_id"), order_info["order_type"])`.
Two exit legs that both lack a `journal_trade_id` share the key
`(None, "target")`, so once the first is refused the second is skipped as
`limit_refused_previously` and **never submitted to `place_order`**, regardless
of its own size or price. A second position's protective leg silently never
gets placed.
*AC:* two pending exits, one with `journal_trade_id=None`; refuse the first and
assert the second still reaches `place_order`. Red today.

**T-413 — `scripts/flex_sftp_pull.py` delivery-staleness gate is entirely untested.**
`delivery_is_stale` (`:240-246`), `statement_period_end` (`:229-238`) and
`MAX_DELIVERY_LAG_DAYS = 1` (`:45`) are wired at `:450` into the
`if not ingested` paging branch. Zero hits for either symbol across
`scripts/tests`, `scripts/api/tests`, `cloud/tests`. The nearest suite
(`scripts/tests/test_rel146_flex_sftp_honesty.py:170-193`) feeds a
`<FlexQueryResponse/>` with no `FlexStatement`, so `period_end` is `None` →
`stale=True` → every case exercises the OLD path. A sign inversion, a `toDate`
parse regression, or a `last_completed_session_date` format change silently
disables the nightly "IBKR stopped delivering" page that guards the
cash-flow/TWR series.
*AC:* XML with `FlexStatement toDate` == last completed session plus
`outcome="duplicate"` → expect ok/exit 0; the same XML dated 5 sessions back →
expect error/exit 1.

**T-414 — `cloud/tests/test_rel194_mcp_unit.py:24-37` inspects only the `ExecStart=` line for secrets.**
The internet-facing MCP unit's actual containment is three directives the test
never reads: `EnvironmentFile=/etc/radon/mcp.env` and
`InaccessiblePaths=-/etc/radon/env` in `cloud/services/radon-mcp.service`.
Delete the `InaccessiblePaths` line, or repoint `EnvironmentFile` at
`/etc/radon/env`, and the MCP process can read `UW_TOKEN` / Turso / B2 creds off
disk as user `radon` — the test stays green, because `secret not in exec_line`
examines one line.
*AC:* parse the unit with a systemd-ini parser; assert
`EnvironmentFile == /etc/radon/mcp.env` and `/etc/radon/env ∈ InaccessiblePaths`.

**T-415 — `cloud/tests/test_rel201_history_scan.py:12-39` is 100% substring-grep over a workflow it never parses (T-205 shape).**
Four tests substring-match `.github/workflows/gitleaks-history.yml`.
`assert "issue" in src` (`:35`) matches `issues: write`, a comment, or the word
in any step name; `assert "gitleaks detect" in src` says nothing about config or
args. The workflow could have invalid YAML, a broken `sha256sum --check`, or
scan the wrong path and all four pass. `cloud/tests/test_gitleaks_policy.py:84`
already demonstrates the right shape (`yaml.safe_load`).
*AC:* `yaml.safe_load` it; assert `on.schedule[0].cron` exists, that the
`if: failure()` step actually opens an issue, and that the detect step's argv
contains `--config cloud/.gitleaks.toml` as a parsed token. Red when the YAML
stops parsing.

**T-416 — `cloud/tests/test_p2_host_paths.py:88-101` proves secret-file ownership by grepping a shell script and a markdown file.**
`assert 'chown root:radon "$ENV_FILE"' in setup` passes if that string sits in a
dead branch, in a comment, or is followed later by `chown radon:radon`.
`:90-91` asserts markdown prose (``"mode `0640`" in claude``) — documentation,
zero mechanism. The sibling `cloud/tests/test_setup_vps_privileged_paths.py`
already proves the working pattern (source the function, run it against stub
binaries).
*AC:* source the env-writing function under a `chmod`/`chown` stub-bin harness
and assert the FINAL observed mode/owner of the temp env file is `0640
root:radon`. Red if a later chown overwrites it.

**T-417 — the CI caddy pin removed the T-205/T-164 freshness signal, and production is not pinned to match.**
`.github/workflows/ci.yml:550-552` now installs a frozen `ver="2.11.4"` with
`sha512sum -c`, replacing a `releases/latest` fetch whose deleted comment read
"Deliberately tracks stable rather than pinning: detecting that semantics change
is the point." Production is pinned to nothing: `cloud/scripts/setup-vps.sh:646-649`
adds the Cloudsmith `stable` deb repo and runs `apt-get install -y caddy`; no
version literal exists anywhere in `cloud/`. So CI is frozen at 2.11.4 forever
while production drifts forward, and `cloud/tests/test_caddy_edge_timeouts.py::TestEdgeMechanism`
(R-219 hung-upstream bound, R-220 non-replay of a severed `POST /api/orders/place`)
can no longer observe a `retry_match` semantics change. `cloud/caddy/Caddyfile:177-183`
states the restriction "rests on an unpinned Caddy default", and that endpoint
has no idempotency key — so the failure mode is duplicate order submission with
two fills as the only evidence. The pin itself is correct supply-chain
hardening and is now locked by `cloud/tests/test_actions_node24.py:87,92-99`;
the finding is that no compensating control replaced the lost signal.
*AC:* either pin the apt package in `setup-vps.sh` to the same version and
assert CI `ver=` equals it, or add a scheduled job that re-runs the edge tests
against `releases/latest` and pages (mirroring `gitleaks-history.yml`, added in
this same delta).

**T-418 — `scripts/mktnews/rel182-flash-rest-degraded.test.js:169-177`: 1000 ms wall-clock budget over a real WebSocket reconnect.**
`expect(okAt - reopenedAt).toBeLessThan(1_000)` where `okAt` comes from a real
`recordHealth` callback driven by `healthTickMs: 100` across a real loopback
socket teardown, TCP reconnect and upstream frame. The intended margin is two
100 ms ticks; the budget spans the whole Node event loop under `vitest`
parallelism. False-reds under load with no code change (the paired
`waitFor(..., 2_000)` at `:171` times out first on the same pressure);
never false-greens.
*AC:* assert on the *tick count* between `reopenedAt` and the ok row, not
elapsed ms. Red-check: inject a 300 ms scheduling stall — a test measuring the
reset must still pass.

**T-419 — `web/e2e/chat-launcher-focus.spec.ts:54-63` false-greens the keybinding it exists to test.**
`expect(async () => { … dispatchEvent(new KeyboardEvent(...)) … }).toPass({ timeout: 15_000 })`
re-fires a synthetic Meta+J keydown until the dialog appears; the comment at
`:51-53` concedes the native press never reaches React. Because the dispatch is
document-level and synthetic, **deleting the launcher's keyboard handler
entirely leaves this test passing** — the regression it names cannot be caught.
It also false-reds whenever hydration exceeds 15 s on a cold shared runner.
*AC:* await a hydration marker, then a single `page.keyboard.press`. Red today
by removing the launcher's key handler.

**T-420 — `scripts/health_service/probes.py`: a new ET wall-clock gate on the gateway dwell path is pinned by no test.**
`DWELL_ESCALATE_UNITS` changed from `DEPENDENCY_UNITS - {radon-ib-gateway.service}`
to `DEPENDENCY_UNITS`, with the exclusion re-expressed as
`_gateway_dwell_suppressed()` → `_market_closed_et(_now_et(None))` — the real
ET clock when `now_et` is omitted. `aggregate_state(..., now_et=None)` and
`build_status(..., now_et=None)` both default it, and every call in the
untouched `scripts/tests/test_sidecar_paging_composition.py:88-93`,
`test_health_service.py:265-533` and
`test_rel140_recovery_evidence_and_heartbeats.py:140,150` omits it. Those cases
also omit `non_up_secs`, so none reaches the branch today — which is the blind
spot: gateway dwell escalation is now weekday/weekend dependent and any future
gateway case added to those files silently inherits the machine clock.
*AC:* a gateway unit with `non_up_secs=901, result="success"` asserted
`degraded` at a Tuesday 10:00 ET injected `now_et` and non-escalating at a
Saturday `now_et`; both clock-injected.

### P2

**T-421** — `scripts/tests/test_nightly_deliver_phase.py:399-465` asserts ~44 English
sentences across four `SKILL.md` files (11 sentences × 4 loops at `:416`, plus
`:430`). `:464` — `assert "Next section" in text or "Next" in text` — is a
straight tautology. The deliver phase can be wholly broken while all pass; a
harmless copy-edit reds CI. *AC:* delete the prose class; the behavioural
siblings at `:249-388` already run the wrapper, and `:351` already asserts the
`NIGHTLY DELIVER INCOMPLETE:` stdout contract.

**T-422** — `web/tests/api-error-code-contract.test.ts:1-50` re-implements the
TypeScript compiler with regexes; its own comment concedes it aims to red
"before tsc/CI does". `/\bcode:\s*"([A-Z0-9_]+)"/` misses every code emitted via
a variable, template literal or helper, and the union parse breaks on reformat.
*AC:* replace with a route-level test hitting each setup route in its error
state and asserting the JSON `code` on the wire.

**T-423** — `scripts/tests/test_rel220_twr_retry_convergence.py:69`:
`cash_flow_sync.main` is monkeypatched at `:47` to append its argument, so
`assert cash_runs[0] == cash_runs[1]` compares the same `tmp_path` string to
itself. The docstring's claim (an id-keyed upsert makes re-ingested rows
identical) is never exercised — break the upsert into an INSERT and duplicate
every cash row and this stays green. The claim-release half at `:60-64` is
sound. *AC:* run the second ingest against a real in-memory `cash_flows` table;
assert the row set is byte-identical after the retry.

**T-424** — `scripts/tests/test_rel195_ma_ratio_honesty.py:107`:
`assert "ib" in source` is a substring test on a provenance label, satisfied by
`"yahoo-fallback-via-ib-library"` — a label reporting the wrong provider, which
is exactly what a file named `ma_ratio_honesty` exists to prevent (CLAUDE.md
data-source rule 7). *AC:* `assert source == "ib"`.

**T-425** — `scripts/ib_watchdog.py:1468-1469`: the `note_remote_cert(...)` call
inside `_run_cycle_steps` is unpinned. `scripts/tests/test_rel178_remote_cert_expiry.py:90-119`
patches `wd._REMOTE_CERT_ALERT` directly and `:81-88` tests `classify_remote_cert`
pure, so nothing asserts a `/health` payload carrying
`gateway.remote.cert_days_left` actually reaches the alert. Deleting the call
leaves the file green and the cert expires unwarned. *AC:* run one cycle with a
stubbed `fetch_health` returning `remote_cert_days_left=3`; assert the written
health row is `error`.

**T-426** — the `cloud-tests` shard partition has no set-equality guard.
`scripts/tests/test_ci_deploy_concurrency.py:614-643` implements the T-122
guard for `scripts/tests`, `scripts/api/tests`, `scripts/trade_blotter` and
`tests`, but not for `cloud/tests`; the cloud assertions at `:218-231` are
literal row-string checks (`rows["al"] == "cloud/tests/test_[a-l]*.py"`).
A `cloud/tests/test_sub/` subdirectory would run zero tests with CI green —
the exact T-122 shape. Harmless today (union verified clean, all 49 files flat).
*AC:* assert cloud shard-union == recursive `rglob("test_*.py")`; red on
`mkdir cloud/tests/test_sub && touch cloud/tests/test_sub/test_x.py`.

**T-427** — CSS/class locators where the testid convention already exists:
`web/e2e/chat-launcher-focus.spec.ts:90-93,104` (`.chat-message.assistant`,
`.chat-role`, `.chat-message-body`, `.chat-launcher__panel`) and
`web/e2e/open-order-combo.spec.ts:270,284,300,310,313,317`
(`locator("tbody tr")`, `.modify-dialog`, `td.last-price-cell`,
`table.position-table-sticky`) — while the same combo spec uses
`getByTestId("open-order-row-71-7101")` at `:297`. A CSS-module rename false-reds
all of them at once. *AC:* rename `.last-price-cell`; the suite must stay green.

**T-428** — `scripts/tests/test_rel196_ats_exit_and_tail.py:43-51`: `assert elapsed < 10`
around a subprocess that cold-imports `fetch_equibles_ats_venue_share` and the
clients package. The signal is ~600 s vs ~0 s, so the bar has huge headroom, but
it is a machine-speed assertion under `-n auto`. `:60-71` already pins the
mechanism. *AC:* raise to 25 s (still 24× the real cost) or assert only against
the `timeout=30` expiry.

**T-429** — `web/tests/orders-display.test.ts:434,449` pass a live `new Date()`
into `summarizeOpenOrderRows` while sibling tests in the same file correctly
freeze the clock (`OVERNIGHT_NOW` / `AH_NOW`, `:349-350`). Benign today — the
fixtures use `status: "Submitted"` and `mapOrderStatus`
(`web/lib/orders/orderDisplay.ts:104-118`) returns `Working` without consulting
the session — but one fixture edit from a weekend flip, and the assertion at
`:460` (`toBeLessThanOrEqual(1)`) is weak enough to absorb a regression as a
pass. *AC:* pass `AH_NOW`, already in scope; assertions unchanged.

**T-430** — `scripts/tests/test_equibles_ats_venue_share.py:35-39`: `mondays()`
computes `latest = today - timedelta(days=today.weekday())`, anchoring fixtures
to the CURRENT, in-progress week, and `:53-79` emits five consecutive days from
it — i.e. future-dated rows on any Mon-Thu run, while `run()` sets
`end_date = now.date()` as the fetch upper bound
(`scripts/fetch_equibles_ats_venue_share.py:692`). Latent, not currently red
(the stub client bypasses the range filter, and today is Friday). *AC:* anchor
`mondays()` to `date(2026, 8, 31)`; the file must stay green.

**T-431** — `web/tests/iv-spread-api.test.ts:212-225`: the "within the budget"
case uses a date-only `yesterday` that the route pins to `T22:15:00Z`
(`web/app/api/iv-spread/route.ts:41-43`) against a 48 h budget (`:34`), so age
ranges 24h00m..47h59m and the margin is under 60 seconds for runs at 22:14 UTC.
Cannot cross the boundary today, but any tightening to 47 h reds it for a
one-hour window only. *AC:* use an explicit 30 h age or freeze the clock.

**T-432** — `scripts/health_service/probes.py:178` keeps
`STATUS_SCHEMA_VERSION = 2` while `build_status` gained
`"degraded_reasons"`. The untouched pins at
`scripts/tests/test_health_service.py:225` and
`test_health_status_trust_split.py:108` (`body["schema_version"] == 2`) are now
vacuous — v2 no longer denotes one shape, so a consumer pinned to v2 cannot
detect the new field. *AC:* bump to 3 (both assertions red), update the pins,
add a `degraded_reasons` key assertion.

**T-433** — `scripts/watchdog/units.py:94` added
`GRACEFUL_EXIT_MARKER_DWELL_SECS = 15*60`, narrowing exit-code/143 collateral
from the 24 h horizon to 15 minutes (`:266-272`), but the new boundary has no
test: every long kill-to-marker case in
`scripts/tests/test_watchdog/test_units.py` (`:657`, `:692`, `:709` — 2.5 h,
9475 s) uses `result="signal"`, and the only 143 cases (`:757`, `:778`) sit at
61 s. The class docstring at `:541-542` still asserts 143 "is the same
collateral as Result=signal", which the change made false. *AC:*
`result="exit-code", exec_main_status=143` at 3600 s → P1; at 600 s → P3.

**T-434** — `scripts/health_service/probes.py:181`: `DEPENDENCY_PROBES` grew
from `frozenset({"ib-gateway"})` to `frozenset({"ib-gateway", "radon-mcp"})`
with no equality pin. The deliberate-growth guard at
`scripts/tests/test_sidecar_paging_composition.py:33-38,76-79` pins only
`DEPENDENCY_UNITS`, so the probe set — which now degrades the public edge
aggregate on a non-up `radon-mcp` — grew unobserved, the exact gap that file's
docstring exists to close. *AC:* add `PINNED_DEPENDENCY_PROBES` equality plus
the degrade/on-box-pages pair for `radon-mcp`.

**T-435** — `web/middleware.ts` moved `isAuthlessTestBypassEnabled(...)` ahead of
`handleSetupModeGate` / `handleAuthMisconfiguredGate` (both now inside
`if (!authlessBypass)`), so the untouched `web/e2e/iv-spread-tab.spec.ts`,
`ivrank-tab.spec.ts` and `vixcor-tab.spec.ts` — all of which send
`x-radon-authless-test` — now skip both gates. Directionally this removes a
false-red, but it also removes their only signal on a runner in setup-mode or
with Clerk misconfigured. *AC:* one spec WITHOUT the bypass header asserting the
setup redirect.

**T-436** — `scripts/tests/test_position_reconcile_spine.py:34-37,158-160` use
module- and class-level `date.today()`, evaluated at import. Safe today
(`find_position_discrepancies` reads the clock only via `_now_et()`, which every
test patches at `:154-156`, and `_is_past_expiry` is pure calendar arithmetic),
but the 14:00 ET "during session" case at `:175-180` would flip on a Saturday
the moment the cutoff becomes market-aware. *AC:* pin `TODAY` to a fixed weekday
and derive `_now_et` from it.

**T-437** — `web/e2e/open-order-combo.spec.ts` mixes a hardcoded past expiry
(`"2026-04-17"`, `:44,68`) with a live `new Date().toISOString()` last_sync
(`:6,31`) in one fixture. Nothing breaks today (assertions at `:275-277` read
only `COMBO` / `Short Put 150` / `Long Call 165`), but any expired-contract badge
or filter added to the orders table reds this spec for unrelated reasons.
`scripts/tests/test_position_reconcile_spine.py:32-33` documents the
window-relative alternative. *AC:* `today + 180d`; assertions unchanged.

**T-438** — the e2e curation guard checks classification but never whether a
CHANGED held-out spec was re-run anywhere. `web/e2e/chat-launcher-focus.spec.ts`
and `web/e2e/open-order-combo.spec.ts` were both modified in this delta and both
are held out (`web/e2e/ci-curation-ledger.txt:42,98`), so their changes have no
browser evidence in CI. Not a gate regression — the curated list did not shrink
and the ledger mechanism is test-enforced — but "spec edited in the delta" is
the one signal `scripts/tests/test_e2e_ci_curation.py` does not cover. *AC:*
assert a spec modified since the merge-base is either curated or carries a dated
ledger annotation.

**T-439** — `cloud/tests/test_app_runtime.py::test_run_api_cleans_staged_credential_on_pre_exec_failure`
is a NEW darwin-only red introduced by this delta, and it is deterministic, not
load flake: 2/2 red in isolation at load 6.3, reproducing in 10-13 s. The
harness at `cloud/tests/test_app_runtime.py:144` gives the subprocess
`timeout=10`; with `RADON_TEST_PYTHON=/bin/false` the script emits the expected
`radon-app-runtime.sh: line 299: /bin/false: No such file or directory` on
stderr and then HANGS rather than cleaning up, so the run is SIGKILLed
(returncode -9) and the assertion is never reached. CI is green at this HEAD
(run on `2b936ebc`, `CI (test gate + deploy)` success) and the file is inside the
`al` shard (`cloud/tests/test_[a-l]*.py`), so the test genuinely runs and passes
on Linux. Effect on this loop: the darwin cloud baseline moves 38 → 37 with one
substitution, and every future audit must re-derive the list rather than trust
a count. *AC:* determine whether the hang is the bash-3.2 `exec` path (the same
class as the other 33 darwin reds) or a real missing-cleanup path; if the
former, skip it on `bash < 4` with a linked reason like the sibling reds; if the
latter, fix the script's pre-exec failure path so it exits instead of hanging.


### Standing sweeps

- **Collection union CLEAN on all three gates** (T-122 holds). Full-tree
  `pytest --collect-only -q` = 595 files; the union of all 10 `py-tests` shard
  globs expanded per row = 595; `comm` empty both directions. `cloud/tests`
  full collect = 49; `al`(minus omit) + `edge` + `mz` = 49; `comm -23` empty.
  All 41 test files added in the delta land inside a CI path set.
- **Vitest include coverage:** the only tracked `*.test.*` outside every include
  glob is `web/e2e/prices-performance.test.js`, deliberately excluded at
  `web/playwright.config.ts:27` with a rationale. Not a regression.
- **Playwright curated list:** 19 specs, all 19 exist on disk; no new
  `web/e2e/*.spec.ts` in the delta; ledger unchanged, no stale entries
  (19 + 142 = 161 = spec count).
- **CI invocation shape base→HEAD:** the ONLY change to any pytest/vitest/
  playwright invocation is the caddy install step (T-417). No new `--ignore`,
  no narrowed glob, no `norecursedirs` / `testPathIgnorePatterns` change
  (`pyproject.toml` untouched). `gitleaks-history.yml` is purely additive.
- **`deploy:` job block byte-identical** base vs HEAD (14 jobs in `needs`).
- **Coverage ratchet honest:** `vitest.config.ts` byte-identical base vs HEAD;
  thresholds unmoved (lines 75 / functions 71 / branches 65); no new blanket
  excludes.
- **Zero new `test.skip` / `it.skip` / `pytest.mark.skip` / `xfail` / `.only`**
  in the delta (python-parsed over the added lines of the code diff; note that
  a naive `xit\(` pattern false-matches `sys.exit(` on this tree).
- **`main` still has NO `required_status_checks`** in branch protection
  (`gh api .../branches/main/protection --jq 'has("required_status_checks")'`
  → `false`). Standing T-222, unchanged.
- **The `py-tests` letter globs skip `q`**, but no `test_q*.py` exists and
  `test_pytest_filename_shards_partition_scripts_tests` would fail the moment
  one is added. Not a finding.

### Re-triage of standing items

- **T-117 / T-248 weekend false-red class:** re-swept this delta. Four
  pre-flagged date candidates were checked and CLEARED — `test_rel215_future_nav_keys.py`
  (calendar-day age, no trading-day arithmetic), `vol-cone-api.test.ts`
  (correctly frozen with `vi.setSystemTime` on an explicit Sunday and Labor Day,
  with `vi.useRealTimers()` in `finally` — this is the pattern the others should
  copy), `ma-ratio-api.test.ts` (window-relative against a daily 22:45 UTC
  writer), and `test_rel209_ext_gate_fails_to_rth.py:39` (skips weekends and
  holidays explicitly). The residue is filed as the latent T-429/T-430/T-431/T-436.
- **e2e testid backlog:** still open, extended by T-427.
- **`next start` Day Move divergence / T-408:** unchanged this cycle.

### Backlog rows

| ID | Sev | AC (red → green) |
|---|---|---|
| T-409 | P0 | Render `<Providers>` twice (Clerk key set / unset, `vi.resetModules()` between); assert `RealtimePricesContext` non-default in BOTH. Red by deleting `{core}` from either branch. |
| T-410 | P0 | Drive `execute()` twice with `check_order_limits` always violating; assert the SECOND `result["error"]` non-empty and cycle health `error`. Fix the source latch to `_note_error` on the skip path. |
| T-411 | P1 | Replace the `_limit_refusals` dict tautology with the T-410 two-cycle drive; deleting the source latch must red. |
| T-412 | P1 | Two pending exits, one `journal_trade_id=None`; refuse the first, assert the second still reaches `place_order`. Key the latch on a per-leg identity. |
| T-413 | P1 | `FlexStatement toDate` == last completed session + `outcome="duplicate"` → ok; same XML 5 sessions back → error. Covers `delivery_is_stale` / `statement_period_end`. |
| T-414 | P1 | Parse `radon-mcp.service` with a systemd-ini parser; assert `EnvironmentFile == /etc/radon/mcp.env` and `/etc/radon/env ∈ InaccessiblePaths`. Red on removing either. |
| T-415 | P1 | `yaml.safe_load` `gitleaks-history.yml`; assert the cron, the `if: failure()` issue step, and `--config cloud/.gitleaks.toml` as parsed argv. Red when the YAML stops parsing. |
| T-416 | P1 | Source the env-writing function under `chmod`/`chown` stub bins; assert the FINAL observed mode/owner is `0640 root:radon`. Red if a later chown overwrites it. |
| T-417 | P1 | Pin production caddy in `setup-vps.sh` to the CI `ver=` and assert equality, OR add a scheduled job running the edge tests against `releases/latest`. Red on a version skew. |
| T-418 | P1 | Assert tick COUNT between `reopenedAt` and the ok row, not elapsed ms; a 300 ms injected scheduling stall must not red. |
| T-419 | P1 | Await a hydration marker then a single `page.keyboard.press`; removing the launcher key handler must red the spec. |
| T-420 | P1 | Gateway unit `non_up_secs=901, result="success"`: `degraded` at an injected Tuesday 10:00 ET, non-escalating at an injected Saturday. Both clock-injected. |
| T-421 | P2 | Delete the 44-sentence prose class (`:399-465`); keep the behavioural siblings at `:249-388`. The `"Next" in text` tautology goes with it. |
| T-422 | P2 | Replace the regex type-checker with a route-level test asserting the JSON `code` on the wire for each setup route error state. |
| T-423 | P2 | Run the second ingest against a real in-memory `cash_flows` table; assert byte-identical rows after the retry. Red if the upsert key changes. |
| T-424 | P2 | `assert source == "ib"` (exact documented label) instead of `"ib" in source`. |
| T-425 | P2 | One cycle with `fetch_health` stubbed to `remote_cert_days_left=3`; assert the written health row is `error`. Red by deleting the `note_remote_cert` call. |
| T-426 | P2 | Assert cloud shard-union == recursive `rglob("test_*.py")`. Red on `mkdir cloud/tests/test_sub && touch cloud/tests/test_sub/test_x.py`. |
| T-427 | P2 | Replace CSS/class locators with `data-testid` in `chat-launcher-focus.spec.ts` and `open-order-combo.spec.ts`; renaming `.last-price-cell` must keep the suite green. |
| T-428 | P2 | Raise the subprocess-exit bound to 25 s, or assert only against the `timeout=30` expiry; the pre-fix executor version must still red. |
| T-429 | P2 | Pass `AH_NOW` (already in scope) instead of live `new Date()`; assertions unchanged. |
| T-430 | P2 | Anchor `mondays()` to `date(2026, 8, 31)`; the file must stay green on every weekday. |
| T-431 | P2 | Use an explicit 30 h age or freeze the clock; the assertion must hold at every hour of the UTC day. |
| T-432 | P2 | Bump `STATUS_SCHEMA_VERSION` to 3 (both v2 pins red), update the pins, add a `degraded_reasons` key assertion. |
| T-433 | P2 | `result="exit-code", exec_main_status=143` at kill-to-marker 3600 s → P1; at 600 s → P3. Correct the stale class docstring at `:541-542`. |
| T-434 | P2 | Add `PINNED_DEPENDENCY_PROBES` equality plus the degrade/on-box-pages pair for `radon-mcp`. |
| T-435 | P2 | One e2e spec WITHOUT `x-radon-authless-test` asserting the setup-mode redirect. |
| T-436 | P2 | Pin `TODAY` to a fixed weekday and derive the patched `_now_et` from it. |
| T-437 | P2 | Move the fixture expiry to `today + 180d`; assertions unchanged. |
| T-438 | P2 | Assert a spec modified since the merge-base is either curated or carries a dated ledger annotation in `ci-curation-ledger.txt`. |
| T-439 | P2 | Classify the darwin hang in `test_run_api_cleans_staged_credential_on_pre_exec_failure`: bash-3.2 `exec` class → skip on `bash < 4` with a linked reason; real missing-cleanup path → make the pre-exec failure path exit instead of hanging. |


## Delta audit 2026-09-05

Range `2b936ebc..391aaaea` — 118 commits / 395 files (154 test/spec files), audited on a **Saturday** (weekend-false-red class live; gates observed green on wall-clock-guarded suites). CI green on `main` at this HEAD (20 success / 5 skipped check runs). Six read-only agents + lead spot-checks; every P1 cited line re-read by the lead.

**Gates (serial, before fan-out; reliability loop was mid-gate in its sibling clone for part of round 1, so durations are load samples):** pytest **11669 passed / 0 failed / 1 skipped** (1642s under contention). vitest first read `13 failed + 38 files failed at import` — ALL one cause: the delta's new `thinking-orbs` dependency (`web/package.json:46`) absent from this clone's `node_modules`; after `bun install --frozen-lockfile` in `web/` the full failed set re-ran **322 passed / 0 failed**. Environment fixed, repo untouched (2026-08-22 rule). `site/` deps needed `npm install` (bun repeatedly failed extracting the `next` tarball on this host). cloud **33 failed / 1692 passed** on darwin — strict subset of the 2026-09-04 list: zero NEW, four GONE (`test_run_api_cleans_staged_credential_on_pre_exec_failure` = T-439 no longer reds, two `test_app_runtime` + one caddyfile + one monorepo red cleared). **Darwin baseline moves 37 → 33**; sorted list at `/tmp/tw-2026-09-05/cloud-failed.txt`. Post-gate `git status --porcelain` clean ×3 (T-275). Secret-name sweep of gate logs clean (T-381; wrapper secrets not exported into this shell, weaker evidence). Delta-touched determinism 3× NOT run: 154 touched test files collapses into full-gate runs (2026-08-16 rule) — saying so, not pretending.

**Standing sweeps:** shard-glob union CLEAN — all 441 `scripts/tests/test_*.py` matched by exactly one shard (bracket-negation globs `test_w[!e]*`, `test_r[!ou]*` partition correctly; explicit lead-module listings de-duplicated by pytest). All 42 new test files CI-reachable except the two site e2e specs (T-447). `deploy.needs`/`deploy.if` byte-identical base→HEAD (14 jobs). Coverage thresholds unmoved, no new excludes. 3 new skip lines, all conditional with reasons; zero `.only`/`xfail`. `main` still has no `required_status_checks` (T-222, fifth consecutive audit).

### P1

**T-440 · P1 — the root-layout authless bypass conjunction is untested.** `web/app/layout.tsx:91-95`: `authlessTestBypass` requires `RADON_AUTHLESS_TEST === "1"` AND `Boolean(authlessTestToken)` AND a matching `x-radon-authless-test` header before the provider tree drops the Clerk realtime token getter (`web/components/Providers.tsx:30,33` → `web/lib/RealtimeAuthContext.tsx:23`). No test imports or pins any term (`grep -rln authlessTestBypass web/tests web/e2e` → nothing; `middleware-authless.test.ts` covers middleware only). Dropping the header or token term leaves a deployment env flag alone able to disable client auth — the exact failure the code comment claims is impossible. **AC:** a test that computes the bypass under all 8 flag/token/header combinations reds when any term is removed (mutate the source to prove red), green at HEAD.

**T-441 · P1 — `compose_body_is_valid` (root-executed compose install gate) has zero tests.** `cloud/scripts/deploy-root-helper.sh:1411` (four-grep gate: `^services:`, pinned `container_name: ib-gateway`, no `privileged: true`, no host-root bind), invoked at `:1344-1351` for the new control-plane target `/etc/radon/ib-gateway-compose.yml`. No cloud test passes a bad body through (`grep -rn compose_body_is_valid cloud/tests` → nothing); the sibling polkit/`bash -n` arms each have one test per target and this new arm broke the pattern. A weakened regex (e.g. `privileged: "true"` quoted) installs a root-executed compose body unchecked. **AC:** parametrized reds for each of the four rejection arms plus a green valid body, exercised through `refresh_install_file`.

**T-442 · P1 — order-modify tested at the `vi.fn()` prop, never at the wire.** `web/tests/modify-partial-fill-quantity.test.tsx:142-169` asserts `onConfirm.mock.calls[0][0]` and `submitBtn().disabled`; the real request is `fetch("/api/orders/modify", …)` at `web/lib/OrderActionsContext.tsx:278` with body built at `web/components/ModifyOrderModal.tsx:619`, and `OrderActionsContext.tsx:84` applies `pm.newQuantity ?? o.totalQuantity` so a dropped field degrades a modify into a no-op resend at the original quantity. Violates the repo's gated-action-at-the-wire rule verbatim, including the missing closed-gate stubbed-`fetch` assertion (2026-08-27 stale-Transmit class). **AC:** render the fetch-owning component, stub `fetch`, assert full URL + method + payload with 516, and assert zero requests while disabled.

**T-443 · P1 — deploy preflight verified by grepping shell text.** `cloud/tests/test_docker_gw_shim.py:203-226` asserts byte offsets (`shim_at < direct_at < fail_at`), `"elif ("` presence, and `body.count("compose_check_ok=1") == 2` in `cloud/scripts/deploy.sh`, never executing `preflight_env()`. An inverted condition or the flag set on the failure branch keeps all three assertions green while every deploy strands on the documented deadlock. **AC:** run the function with a fake `sudo -n` refusing the shim verb and assert the direct render path actually executes (behavioural red against a mutation that inverts the elif).

**T-444 · P1 — green-SHA wrapper wiring asserted as source text across all five loops.** `scripts/tests/test_nightly_green_base.py:109-136`: `assert "resolve_green_main_sha" in body`, index ordering, `"|| true"` presence. A wrapper that computes the green SHA and checks out `origin/main` anyway passes — restoring the REL-187 defect verbatim. The `TestResolve`/`TestCli` halves are genuine; only the wiring half is grep. **AC:** execute each wrapper's branch-selection snippet with a fake `git`/resolver and assert the checked-out ref equals the resolver's answer.

**T-445 · P1 — "fail-closed" setup route: the documented fail-open branch is untested.** `web/tests/setup-complete-fail-closed.test.ts:1-14` docblock describes R-622 (FastAPI unreachable → `if (!known)` can never fire → any `SERVICE_PATTERN` id accepted before a credential-store write); every test in the file is R-629 TTL. The only R-622 coverage, `scripts/tests/test_setup_service_id_parity.py:23-39`, regex-scrapes `KNOWN_SERVICE_IDS` and asserts the literal string `"KNOWN_SERVICE_IDS.has(service)"` appears somewhere — cannot see negation or a dead branch. **AC:** a route-level test with the FastAPI probe down asserting an unknown-but-pattern-valid service id is rejected; red at HEAD if the branch is fail-open, else red under a mutation that negates the check.

**T-446 · P1 — `cloud/tests/test_p2_host_paths.py:161-180` forwards the runner's full environment into a sourced shell script.** `env = {**os.environ, …}` handed to `subprocess.run(["bash","-c","source setup-vps.sh; …"], env=env, capture_output=True)` — under the wrapper that environment carries live tokens, and captured output lands in pytest failure reports and loop logs (T-381 class). Same helper discards `returncode` (silent pass on an unexecuted path) and bare-subscripts `os.environ['PATH']`. **AC:** minimal explicit env dict; assert returncode; red demonstrated by exporting a canary var and grepping the report.

**T-447 · P1 — two new site e2e specs are reachable by zero CI jobs with no hold-out record.** `site/e2e/agent-prompt-recipes.spec.ts` and `site/e2e/libraries-fx-pack.spec.ts` (both new in range): no workflow references `site/e2e` (only `e2e-financial-smoke` runs Playwright, `working-directory: web`), and `site/e2e` has no curation ledger, so unlike the three held-out web specs (each with a REVIEWED entry, ledger lines 35/55/74) this exclusion is silent. PART A recorded `site/e2e` as ungated when it held 3 specs; the dir is now growing while invisible. **AC:** either a `site/e2e` ledger with documented hold-outs or a CI job that runs them; a deliberately-broken assertion must red something.

### P2

**T-448 · P2 — the demo build is a second compiled product covered by one spec, run in a non-gating job, and the rebuild poisons retries.** `.github/workflows/ci.yml:767-778` rebuilds with `NEXT_PUBLIC_RADON_DEMO=1` and runs only `demo-workstation-data.spec.ts`; the job (`e2e-financial-smoke`) is absent from `deploy.needs` (documented posture, `ci.yml:738-740`), so the new "deployed demo contract" step cannot block a deploy. The second build overwrites `.next`, so any re-run after that step executes against the demo bundle — ordering is load-bearing and undocumented. Existing suites are silently prod-scoped: `ticker-search-disconnected.test.ts` (4 tests) unreachable in demo mode (`TickerSearch.tsx` demo short-circuits), `sync-hooks.test.ts`/`use-sync-hook-inactive-load.test.ts` pin POST/retry semantics the demo build inverts (`useSyncHook.ts` GET/no-retry), `useChainPrefetch` 4th param default, `useHeadlines` demo poll loop (its e2e spec held out). **AC:** promote per the documented green-runs rule or annotate the step non-gating; move the demo build/spec after trace upload or into its own job.

**T-449 · P2 — Pushover alerting merged with source-text assertions only.** `web/lib/notify/pushover.ts:9,43` (`sendPushover`, `notifyDemoProvisioningFailure`) has zero behavioural tests; `web/tests/demo-provisioning-resilience.test.ts:93,175-176` asserts `toContain("notifyDemoProvisioningFailure")` on route text. A throw escaping the helper fails the Clerk webhook; the no-PII contract is unexercised. **AC:** stub `fetch`, assert no-op without creds, no-throw on 500, payload carries no email/userId.

**T-450 · P2 — global `matchMedia` shim answers `matches: query.includes("dark")` for all 885 vitest files.** `vitest.setup.ts:8-25`, installed in `beforeEach` (`:70`). Light-mode branches are untestable suite-wide; any query containing the substring matches. **AC:** default to a scoped, overridable shim; a light-mode assertion must be expressible.

**T-451 · P2 — cwd-walking checkout discovery.** `web/tests/realtime-socket-ownership-contract.test.ts:56-62` walks six `..` hops from `process.cwd()` looking for `Providers.tsx`, else throws. The in-delta correct pattern is `demo-provisioning-resilience.test.ts:22` (resolve against the test file). **AC:** import.meta.url-relative resolution; green from any invocation dir.

**T-452 · P2 — wall-clock/platform budgets in `cloud/tests/test_app_runtime.py`.** `:707-713` `timeout=90` over a script measured at 18s under load (2026-09-04 recorded a 9.5× contention factor on this host → ~170s); `:463-468` fixed 5s socket deadline + bare `time.sleep(0.8)` racing a poll interval; `:711` hardcodes `/usr/bin/false` (darwin location; not guaranteed on non-usrmerge Linux). Red on any run overlapping the sibling loop's gate. **AC:** scale budgets or gate on progress, inject the false-python path.

**T-453 · P2 — `scripts/tests/test_nightly_issue_prune.py:215-230` burns a real `sleep(30)` inside nested 10s/60s wall-clock caps.** Under contention the harness `TimeoutExpired` masks the assertion. **AC:** fake clock or signal instead of sleeping.

**T-454 · P2 — bare CSS-class selectors with exact fixture counts in new e2e.** `demo-workstation-data.spec.ts:59-61` (`.book-feed-pill` etc. `toHaveCount(10)`), `mobile-combo-instrument-switcher.spec.ts:530-552` (`.cockpit-host`, `.act-ticket` incl. a negative assertion that passes on rename), `portfolio-defined-combo-pnl.spec.ts:99-110` (`.section` + `tr` text filters; `position-table` testid exists in-repo). Plus `book-montage-spacing.spec.ts:99` screenshots to hardcoded `/tmp/…png` instead of `testInfo.outputPath` (worker collision, artifact never attached). **AC:** testids/roles; outputPath.

**T-455 · P2 — `demo-headlines.spec.ts:20` pins a headline fixture to `2026-09-04T18:51:31Z`.** Same rot the delta itself fixed in `open-order-combo.spec.ts:6` (T-437) and `iv-spread-api.test.ts:272-281` — applied to two of three sites. **AC:** window-relative timestamp.

**T-456 · P2 — FX constants suite is self-asserting.** `site/lib/libraries-fx.test.ts:16-39` restates the adjacent object literals; `web/tests/libraries-fx-pack.test.ts:34-48` mirrors one-line predicates entry-for-entry. Consumers (`CtaBeam.tsx:12`, `FourGateChips.tsx:16`, `FooterTelemetryStrip.tsx:97` — does a failed Gate 03 stop beaming?) untested. **AC:** render-level assertions on one consumer per constant.

**T-457 · P2 — `web/tests/demo-workspace-chrome.test.ts:12-14` is three `toContain` calls on component source text.** Prettier reformat reds it with no behaviour change; a stale duplicate string keeps it green while demo users see the live "Sync from IB Gateway" action. **AC:** render `WorkspaceShell` in demo mode and assert the control.

**T-458 · P2 — layout "coverage" by CSS/JSX text-matching.** `web/tests/regime-cri-chart-layout.test.ts:26-64` (slices 220 chars backward from a testid to guess the enclosing className) and `web/tests/book-montage-spacing.test.ts:39-65` (pins `column-gap: 8px` literals from the fix; a higher-specificity selector reproduces the AAOI collision green). Held-out e2e siblings exist; keep, but the unit files should assert computed structure, not stylesheet bytes. **AC:** jsdom computed-style or DOM-structure assertions.

**T-459 · P2 — fill-driven portfolio refresh never observes a refresh.** `web/tests/fill-driven-portfolio-refresh.test.tsx:76-93` asserts a `vi.fn()`; the stated defect lives in the unrendered callback at `web/components/WorkspaceShell.tsx:426-430`, and `useFillToasts.ts:57-58`'s ref pattern is exactly the stale-closure class the repo flags on sight. **AC:** render the shell, assert the portfolio endpoint is re-fetched after a new execId.

**T-460 · P2 — role-parameterized health probes silently narrowed two untouched suites.** `scripts/health_service/probes.py:412,322-338` now read `RADON_HOST_ROLE` when `host_role` is omitted; `test_rel181_gateway_suppression.py:97,106` and `test_rel135_dependency_dwell.py:101-121` stay green only because `scripts/conftest.py:18,42-44` scrubs the var, and the app-role drop-out path is invisible to them. Likewise `_heartbeat_orders_sync_skip` widened (`scripts/api/server.py:718-722`, new `pool-disconnected` branch at `:660-663`) and `test_scan_admission_and_shed_honesty.py` covers only `capacity-shed`. **AC:** explicit `host_role=` params in the two suites + one app-role case; one assertion on the non-shed branch's `state="error"` message.

**T-461 · P2 — `scripts/tests/test_equibles_ats_venue_share.py:40` pins `ANCHOR_MONDAY = date(2026, 8, 31)` while seven call sites pass live `datetime.now(timezone.utc)`.** The fixture-to-clock gap widens weekly; once past `MIN_HISTORY_WEEKS` the coverage assertions flip. Not red today, red on a calendar. **AC:** derive the anchor from the injected now.

**Re-triaged, not filed:** the 34 vitest file-level reds (environment: missing `thinking-orbs`, install recorded above); cloud 33 darwin reds (bash 3.2 + missing `caddy`, baseline list re-recorded); the shard-glob reshuffle (clean partition, T-122 holds); `positionSpreadQuoteScale` partial coverage (below bar — one integration case exists); duplicate `bun install` in e2e job (intentional per ci.yml:246-248); `main` advanced past this HEAD mid-run (newer SHAs in CI are outside this range).

### Backlog rows

| ID | Sev | AC (red → green) |
|---|---|---|
| T-440 | P1 | Test all 8 flag/token/header combos of `authlessTestBypass`; deleting any conjunction term from `layout.tsx:91-95` must red. |
| T-441 | P1 | Parametrized reds through `refresh_install_file` for each of the four `compose_body_is_valid` rejection arms (no services, wrong container, `privileged`, host-root bind incl. quoted forms) + green valid body. |
| T-442 | P1 | Render the fetch-owning modify path with stubbed `fetch`; assert `POST /api/orders/modify` full URL + payload `newQuantity: 516`; assert zero requests while disabled. Red at a `vi.fn()`-only stub. |
| T-443 | P1 | Execute `preflight_env()` with a fake `sudo -n` refusing the shim verb; assert the direct render runs. Inverting the `elif` must red. |
| T-444 | P1 | Run each wrapper's branch-selection snippet with fake `git` + resolver; assert checked-out ref == resolver output. A wrapper ignoring the resolver must red. |
| T-445 | P1 | Route-level test with the FastAPI probe down: unknown-but-pattern-valid service id rejected before any credential-store call. If fail-open at HEAD, fix source, keep test. |
| T-446 | P1 | Explicit minimal env dict + returncode assert in `test_p2_host_paths.py:161-180`; canary env var must not appear in captured output. |
| T-447 | P1 | `site/e2e` ledger with documented hold-outs, or a CI job running the specs; a broken assertion must red something in CI. |
| T-448 | P2 | Demo build/spec moved after trace-upload or to its own job; posture (non-gating) annotated at the step; promotion per the documented green-runs rule tracked. |
| T-449 | P2 | Behavioural `sendPushover` tests: no-op without creds, no-throw on 500, payload PII-free. Route text greps deleted. |
| T-450 | P2 | Scoped overridable `matchMedia` shim; a light-mode assertion expressible and passing. |
| T-451 | P2 | Resolve `Providers.tsx` relative to the test file; green from any cwd. |
| T-452 | P2 | Progress-gated or scaled budgets in `test_app_runtime.py:463-468,707-713`; false-python path injected, not `/usr/bin/false`. |
| T-453 | P2 | Fake clock/signal replaces `sleep(30)` in `test_nightly_issue_prune.py`; timeout param runs <1s. |
| T-454 | P2 | Testid/role selectors replace bare CSS classes in the three new specs; screenshot via `testInfo.outputPath`. |
| T-455 | P2 | `demo-headlines.spec.ts` fixture timestamp window-relative. |
| T-456 | P2 | One consumer-render assertion per FX constant; deleting a spread in `CtaBeam`/`FourGateChips` must red. |
| T-457 | P2 | Render `WorkspaceShell` in demo mode; assert "Sample snapshot" and absence of the sync producer action. |
| T-458 | P2 | Computed-style/DOM-structure assertions replace stylesheet-byte pins in the two layout unit tests. |
| T-459 | P2 | Render the shell; new execId ⇒ portfolio endpoint re-fetched. Stale-ref mutation must red. |
| T-460 | P2 | Explicit `host_role=` in rel181/rel135 suites + one app-role case; one assert on the `pool-disconnected` branch message/state. |
| T-461 | P2 | `ANCHOR_MONDAY` derived from injected now; suite green at any future date (verified with a frozen clock a year out). |
| T-462 | P0 | Render `OrderTab`/`WorkspaceSections` (not the leaf modal) with the relay disconnected, stub `fetch`; assert zero requests and the blocked reason renders. Red today; red again when `feedConnected={...}` is removed from the call site. |
| T-463 | P0 | Patch `_find_working_order` to `{"orderId":10,"status":"Submitted","filled":0}` behind a structured-202 cancel error; assert 502 `REPLACE_PARTIAL`, `cancelled == []`, `place.assert_not_awaited()`. Must red on `return True` at `server.py:3191`. |
| T-464 | P1 | Render `WorkspaceShell` itself (mock only data hooks), stub `fetch`, push an orders payload with a new `execId`; assert exactly one `POST /api/portfolio`. Must red when `WorkspaceShell.tsx:430`'s third argument is removed. Delete the hand-cloned harness. |
| T-465 | P1 | `useToast` test asserting `hasToastKey(key)` flips false after `dismissToast`, plus a WorkspaceShell test that a dismissed fill toast's next fill shows its own quantity. Red on either mutation. |
| T-466 | P1 | Call the real `GET` of `app/api/trin/route.ts` with a mocked `requireRouteAccess`; assert the options argument BY VALUE and that a daily-tier `success:false` yields 429 with no upstream fetch. Delete the `readFileSync` grep. |
| T-467 | P1 | Execute the digest pipeline against a temp tree: a populated tree must differ from the empty-input digest, and an empty tree must exit non-zero rather than record `e3b0c442…`. Plus a consumer assertion, or delete the digest. |
| T-468 | P1 | Add `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `-c core.hooksPath=/dev/null -c commit.gpgsign=false` to the `git_repo` fixture. Red with a host `pre-commit` that exits 1; green after. |
| T-469 | P1 | Switch the held-out-spec guard from `%cs` to `%as`. Red: stamp a spec today, `git commit --amend --no-edit` so `%cs` advances a day, assert fires. Green: amended tree passes. |
| T-470 | P1 | Reject `radonFetch` with `new RadonApiError(429, …)` on gex/regime/gamma-rotation; assert status is exactly 429, no cached body, `scan_succeeded:false`. Red on hardcoding 502 and on widening `status >= 500`. |
| T-471 | P1 | Add a `hasBlendedLegBasis` position to the `unrealized-breakdown-signed.test.ts:211` portfolio; parity must still hold AND a `col1 === "---"` row must not NaN the `:113` check. |
| T-472 | P1 | Re-render the modal with `filled` 16->100: a price-only submit transmits no `newQuantity`; a quantity submit hits the `fillRaceNotice` reseed/refuse branch, not `toIbTotalQuantity`. |
| T-473 | P1 | Re-render with `filled: 100, remaining: 900`; assert `.modify-order-info` and `#modify-quantity-input` describe the same fill count, or that the stale field is visibly flagged. |
| T-474 | P2 | Run `run_portfolio_refresh.sh` with logging `curl`/`sleep` stubs and a forced 502; assert the curl COUNT and simulated wall clock against `TimeoutStartSec`. Red when the loop bound changes without the constants. |
| T-475 | P2 | Assert the isolation invariant: demo build + demo spec are the trailing contiguous pair AND both carry `NEXT_PUBLIC_RADON_DEMO: "1"`, and no later step combines `failure()` with a `playwright test` re-run. Better: distinct `--distDir`. |
| T-476 | P2 | Delete the two shell-body greps, or replace with a run of the real arm on a poisoned candidate asserting non-zero exit AND the target file unwritten. |
| T-477 | P2 | Add `mockRadonFetch.mockReset()` to the `api-routes.test.ts:841` `beforeEach` and pin the fixture `date` to `mostRecentSessionDate()`. Red: assert `mockRadonFetch` uncalled against the leaked mock. |
| T-478 | P2 | Reset and re-stamp `mockStat` from `beforeEach` with a window-relative mtime. Red: a test setting `mtimeMs: 0` makes the next test in file order observe a stale mtime. |
| T-479 | P2 | Switch `.order-confirm-summary` and the `.pos-stat*` walkers to `getByTestId` / per-cell `data-testid`. Red today when the class is renamed in `globals.css`; invisible to the suite after. |
| T-480 | P2 | Replace the six real-timer 20ms sleeps with `waitFor` (positive) and fake timers (negative). Red probe: lower the budget to 0 and see whether the negative assertions are decorative. |
| T-481 | P2 | Move `LADDER`/`LOOPS`/`_run` into a non-test helper imported by both. Red: `git mv test_weekend_model_ladder.py test_ladder.py` currently fails `test_loop_session_limit.py` at collection. |
| T-482 | P2 | Assert `playwright.config.ts`'s webServer env unsets `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` or forces `pk_test_` whenever it sets `RADON_AUTHLESS_TEST=1`. |
| T-483 | P2 | After the rejection, advance 60_000 and assert no third fetch, then a further 60_000 and assert exactly three. |
| T-484 | P2 | Pin the interpreter `cloud/tests` shells out to (skip with a named reason when bash>=4 is absent), or record resolved `bash --version` + `command -v caddy` with every FAILED list. Red/green: the baseline artifact must be identical with `/bin` vs `/opt/homebrew/bin` first on PATH. |

## 11 · Audit ledger

The weekend loop (`.claude/skills/testing-weekend/`) reads the last line
here to scope its daily delta audit, and appends one line per run.
Delta findings continue the T-### numbering in dated `## Delta audit` sections.

- Audited through: `d681d247` on 2026-08-08 — initial full audit (T-001…T-054, PART A frozen 2026-08-07 at `2a75496a`) + PART B remediation waves (PRs #13/#14).
- Audited through: `71de8a33` on 2026-08-16 — 24 new findings (T-055…T-078: 3 P0, 7 P1, 14 P2) over 202 commits / 1169 files. Gates ×3 green except one load-sensitive vitest race (T-062). No new skips, no exclusion growth, no threshold moved.
- Audited through: `4985a7f8` on 2026-08-22 — 17 new findings (T-080…T-096: 1 P0, 7 P1, 9 P2) over 167 commits / 565 files. Gates ×2 serial: vitest 7036 green ×2; pytest 7216 green on run 2, one sleep-race red on run 1 (T-089, 3/3 isolated green); cloud 12 red ×2 on darwin (10 known sha256sum + 2 new, T-088). No new skips, no exclusion growth, no threshold moved.
- Audited through: `4985a7f8` on 2026-08-22 (second pass, different host) — 24 ADDITIONAL findings (T-097…T-120: 13 P1, 11 P2) on the same range; 8 more converged with the first pass and were dropped, 3 are deltas to T-088/T-090/T-094. pytest 7216 green on rounds 1 and 3, T-089's sleep race red on round 2; vitest 7036 green ×3; cloud 34-red ×3, byte-identical to the same run at `71de8a33` (bash 3.2 on this host, T-118). No new skips, no `.only`, no exclusion growth, no threshold moved.
- Audited through: `27665c43` on 2026-08-25 — 34 new findings (T-122…T-155: 1 P0, 12 P1, 21 P2) over 68 commits / 513 files. Gates serial: pytest 7816 green (recursive; CI's sharded matrix drops 752 of them, T-122); vitest 7328 green / 701 files; cloud 34 red on darwin, byte-identical to the base SHA (T-118). Added-file determinism 3×3 green. No new skips, no `.only`, no exclusion growth; vitest thresholds unchanged; pytest ratchet metric silently switched to statement-only (T-123).
- Audited through: `1b326772` on 2026-08-26 — 34 new findings (T-156…T-189: 3 P0, 14 P1, 17 P2) over 33 commits / 236 files. Gates serial round 1: pytest 7996 green (recursive; CI's 12 shards sum to the identical 7996, so T-122 holds); vitest 723 files / 7498 green; cloud 34 red on darwin, FAILED list byte-identical to the base SHA in a worktree (T-118). Added-file determinism 3×3 green (pytest 60, vitest 121). One new skip (`test_caddyfile.py:229`, filed as T-164), no `.only`, no exclusion growth, no threshold moved — but both coverage ratchets left `deploy.needs` and `main` has no required status checks (T-160).
- Audited through: `789aabea` on 2026-08-27 — 49 new findings (T-190…T-238: 6 P0, 23 P1, 20 P2) over 43 commits / 264 files. Gates serial: pytest **7 failed** / 8153 passed — deterministic, all in `test_portfolio_risk_gate3_measurability.py`, reproduced 7/7 in isolation, filed as T-237 (`main` is red; CI at this SHA also failed and correctly skipped deploy); cloud 34 red on darwin, FAILED list byte-identical to the base SHA in a worktree (T-118); vitest 758 files / **1 failed** / 7702 passed — a single 5041ms timeout on `portfolio-startup-performance-contract.test.ts:172` under load 36, green 8/8 ×3 in isolation, filed as T-238 (load class, not a regression). Collection union clean on all three gates (py 478/479 shard union, cloud 33/33, vitest 758/758) so T-122 holds. Enforcement STRENGTHENED — T-160 is fixed, `deploy.needs` went 7 → 9 with both coverage ratchets restored. Four new skips (8 outcomes), none linked to a T-### (T-204, T-205); no `.only`; no exclusion growth; no threshold moved — the coverage measurement got stricter twice.
- Audited through: **NOT ADVANCED** on 2026-08-28 — the audit phase was truncated by the harness background-wait ceiling at 600 s (T-239), filed no findings and no PR, and reported `OK`. `789aabea..c6d08fbd` (24 commits / 262 files / +23,193 lines) is **UNAUDITED**; the next audit must take `789aabea` as its base, not `c6d08fbd`. The remediation phase that followed worked the P2 backlog and filed T-239 against the truncation itself.
- Audited through: `f7b5eeb9` on 2026-08-29 — 62 new findings (T-250…T-311: 5 P0, 30 P1, 27 P2) over 61 commits / 366 files / +36023-2510, base `789aabea` because the 2026-08-28 audit was truncated (T-239) and did not advance the ledger. Gates round 1 serial under load 74→224: pytest **3 failed** / 8558 passed — all three timing-shaped, in three unrelated files, 4 passed in 14.8s isolated, and CI's ten shards were green at this SHA (load, filed as T-283 and T-284); vitest 787 files / **9 failed** / 7934 passed — deterministic, all 9 in the three files `ci.yml:143-145` EXCLUDES, so the CI-gated set is fully green (T-276); cloud **37 failed** / 1263 passed on darwin vs **35 failed** / 1098 at the base SHA run in a worktree — the 4-line diff is +3 deliberate caddy reds (T-205 working, no `caddy` binary here) and −1 fixed relay watchdog, so the recorded darwin baseline is now 37. Collection union clean on all three gates (pytest 8562 = shard union, cloud 38/38 files, vitest 787/787) so T-122 holds. Enforcement improved — `stage-release.needs` GAINED both coverage ratchets — but `main` still has no `required_status_checks` (T-222 re-confirmed). One new skip, honest and linked to T-204; no `.only`; no exclusion growth; no threshold moved. `resolveSpreadPriceData` closed as fixed; the standing `orders-place-cache-race` item re-diagnosed from cross-file pollution to an intra-file race and numbered T-311.
- Audited through: `fda36450` on 2026-08-30 — 34 new findings (T-312…T-345: 1 P0, 14 P1, 19 P2) over 154 commits / 472 files / +38160-1917, base `f7b5eeb9`. Gates ×2 serial: pytest **22 failed** / 8973 passed both rounds — ONE deterministic cluster in five Flex-embargo files that flips green when `TURSO_DB_URL` is masked; the runner clone gained `web/.env` via `14065b74` and CI is green only because it has no creds (T-317, the T-277 class recurring); vitest 819 files / **1 failed** / 8265 (r1, an intra-file `replaceMock.calls.at(-1)` race, 3/3 green isolated, T-321) and **3 failed** / 8263 (r2, bare timeouts in three unrelated files under load 255 with the reliability loop's vitest concurrent, 22/22 green isolated — load); cloud **35 failed** both rounds, FAILED lists byte-identical — `caddy` is now installed here so the three T-205 reds are gone, and one NEW test-defect red (`test_no_real_secrets_in_tracked_files` on a constant-folded `.pyc`, T-325) joins the 34 bash-3.2 reds. Added-file determinism 3×3 green (vitest 27 files/239, pytest 357, cloud 80). Collection union clean on all three gates (pytest 8996 = shard sum, cloud 44/44, vitest 8266 = CI) so T-122 holds. Enforcement: `deploy.needs` 9 → 14, ratchets retained, thresholds unmoved, no `.only`/`xfail`, every new skip linked — but `main` still has no `required_status_checks` (T-222) and a docs-only push deploys with every gate skipped, LIVE at this HEAD (T-312, P0). CI was red 04:20–05:12Z inside the range for three separate causes, all fixed in-range, deploy skipped each time.
- Audited through: `39bf6f5e` on 2026-08-31 — 34 new findings (T-346…T-379: 8 P1, 26 P2) over 51 commits / 247 files / +20997-853, base `fda36450`. Landed by the REMEDIATION phase: the audit phase drafted T-346…T-378 and exited 0 on a progress message without committing (T-379, T-239 class), so its ledger line is written here. Gates round 1 (audit's detached script, load 4.6→20): pytest **9508 passed / 0 failed** (first green pytest round on this clone since `web/.env`; T-317 fixed); vitest 832 files / **8392 passed / 0 failed**; cloud **35 failed** / 1500 passed on darwin — one-for-one swap against the 2026-08-30 list (+1 new bash-3.2 red from `1b85a8b3`, −1 T-325 fixed), baseline stays 35. Collection union clean on all three gates (pytest 9509 = shard sum, cloud 44/44, vitest 8392 = CI + 1) so T-122 holds. `deploy.needs` identical base→HEAD (14 jobs, ratchets retained); `main` still has no `required_status_checks` (T-222). Zero code skips in the delta, no `.only`/`xfail`, no exclusion growth, no threshold moved. Delta-file determinism 3× recorded in `TEST_LOG.md`.
- Audited through: **NOT ADVANCED** on 2026-09-01 — both phases exited 1 on "out of usage credits" four seconds in (pre-#238/#239 wrapper); the branch was pushed empty and FAILED reported on all three dead-man channels. Written retroactively by the 2026-09-02 remediation phase.
- Audited through: **NOT ADVANCED** on 2026-09-02 — the audit phase exited 0 at ~4 minutes on a text-only progress message with zero findings drafted (T-379 class; the wrapper correctly posted INCOMPLETE, so T-379's detection works — recovery does not exist, filed as T-380). `39bf6f5e..db25990d` (32 commits / 259 files / +26958-1135) is **UNAUDITED**; the next audit must take `39bf6f5e` as its base. The remediation phase adopted the audit's detached gates for round 1, filed T-380, and worked the P2 backlog (T-353…T-378).
- Audited through: `0202e32d` on 2026-09-03 — 27 new findings (T-382…T-408: 1 P0, 10 P1, 16 P2) over 49 commits / 340 files / +41249−1406, base `39bf6f5e` per the two NOT ADVANCED lines above. Gates round 1 serial BEFORE the fan-out (load 5→6): pytest **10882 passed / 0 failed**; vitest **8598 passed / 0 failed**; cloud **35 failed** / 1536 passed on darwin, FAILED list **byte-identical** to the 2026-09-02 list — baseline stays 35. Delta-touched determinism 3× each root: scripts 2651 passed ×3, vitest 817 passed ×3, cloud 21 failed / 784 passed ×3 with identical FAILED lists, all 21 a strict subset of the 35 darwin baseline. Tree clean after every gate (T-275 sweep), no runner secrets in gate logs (T-381 sweep). CI green on `main` at this HEAD (run 33695685875); the red runs at audit time were the reliability loop's PR branch. `deploy.needs` identical base→HEAD (14 jobs), coverage config diff empty, no threshold moved, no exclusion growth, 9 new skip lines all conditional/`skipif` with reasons, no `.only`/`xfail`. Shard union clean: CI 10882+1 skipped = 10883 local collection exact; cloud 1575+2 = 1577 exact (T-122 holds). `main` still has no `required_status_checks` (T-222). Standing note-2 re-triaged: the default-mode build now dies at TYPES, not prerender — numbered T-408.
- Audited through: `2b936ebc` on 2026-09-04 — 31 new findings (T-409…T-439: 2 P0, 10 P1, 19 P2) over 50 commits / 282 files / +17076−1128, base `0202e32d`. Run on a **Friday** (weekend-false-red class dormant; reasoned about, not observed). The reliability loop was mid-gate in its own clone for the entire run, so these are LOAD SAMPLES: pytest **11546 passed / 0 failed / 1 skipped** in 2617s (43m, vs the usual ~275s — contention, not a regression); vitest 855 files / **8627 passed / 0 failed / 18 skipped**; cloud **37 failed** / 1634 passed on darwin. Cloud attributed by RUNNING THE BASE SHA in a worktree and diffing sorted FAILED lists: base `0202e32d` = **38 failed**, HEAD = 37 — two `test_setup_never_replaces_live_helper_with_invalid_candidate` params FIXED in range, one NEW (`test_app_runtime.py::test_run_api_cleans_staged_credential_on_pre_exec_failure`, deterministic 2/2 in isolation at load 6.3, darwin-only — CI green at this HEAD and the file is in the `al` shard; filed T-439). The recorded baseline moves 35 → 37; note base now reads 38 where 2026-09-03 recorded 35 at the same SHA, so the darwin list drifts on this host and must be re-derived, never trusted as a count. Round 2 of the gates was deliberately STOPPED to stay inside the phase cap, and the delta-touched determinism re-runs did not happen: the delta touches 133 test files, so per the 2026-08-16 lesson scoped re-runs would have collapsed into full-gate runs — saying so rather than pretending they ran. Collection union CLEAN on all three gates (pytest 595 files = shard-glob union 595, `comm` empty both ways; cloud 49 = al+edge+mz 49; all 41 new test files inside a CI path set) so T-122 holds. `deploy:` block byte-identical base→HEAD (14 jobs); `vitest.config.ts` byte-identical, thresholds unmoved (75/71/65); zero new skip/only/xfail in the delta; Playwright curated list 19/19 present, ledger unchanged. Only CI invocation change in range is the caddy pin (T-417). `main` still has NO `required_status_checks` (T-222). Tree clean after every gate (T-275 sweep, 0 lines ×3). T-381 secret sweep: no runner secrets are exported into this phase's shell, so the sweep is clean but weaker evidence than under the wrapper.

- Audited through: `391aaaea` on 2026-09-05 — 22 new findings (T-440…T-461: 8 P1, 14 P2) over 118 commits / 395 files, base `2b936ebc`. Saturday run. pytest **11669 passed / 0 failed** (1642s under sibling-loop contention); vitest first read 13 failed + 34 files failed at import — ALL the delta's new `thinking-orbs` dep missing from this clone's node_modules; after `bun install` the failed set re-ran **322 passed / 0 failed** (environment fixed, repo untouched; `site/` needed npm — bun kept failing on the `next` tarball). cloud **33 failed** / 1692 passed on darwin, strict subset of 2026-09-04: zero NEW, four GONE (incl. T-439's red) — **baseline 37 → 33**. Post-gate tree clean ×3 (T-275); secret-name sweep clean (T-381). Delta-touched determinism 3× NOT run (154 touched test files collapses into full gates, 2026-08-16 rule). Shard-glob union clean (441/441 single-shard, T-122 holds); 40 of 42 new test files CI-reachable, the two `site/e2e` specs are not (T-447). `deploy.needs`/`if` byte-identical; thresholds unmoved; 3 new conditional skips with reasons; no `.only`/`xfail`. CI green on `main` at this HEAD (20 success / 5 skipped). `main` still has no `required_status_checks` (T-222, fifth audit running).

- Audited through: `be64e1fc` on 2026-09-05 (**second pass**, same day, same clone) — 23 new findings (T-462…T-484: 2 P0, 10 P1, 11 P2) over 61 commits / 107 files / +5789-172, base `391aaaea` (the first pass's HEAD). Saturday run. The range CONTAINS the first pass's own remediation (T-440…T-461) and the reliability loop's REL-232…REL-247, re-triaged as ordinary delta per the 2026-08-22 rule. Gates serial round 1 BEFORE the fan-out, no sibling loop, load 3-6: pytest **11762 passed / 1 skipped / 0 failed** (1730s); vitest 39 files failed at IMPORT on ONE environment cause (`thinking-orbs` + `border-beam` declared at `web/package.json:37,46` but absent from this clone's node_modules — the first pass's lesson recurring after a tree reset), and the same 39 files re-ran **323 passed / 0 failed** once installed, repo untouched; cloud **5 failed / 1785 passed**, all in `test_caddy_edge_timeouts.py` with `caddy` ABSENT. The cloud baseline reads 5, not the recorded 33, because this run's gate PATH resolves `bash` to homebrew 5.3.9 instead of `/bin/bash` 3.2 — filed as T-484, and it means every previously recorded darwin baseline was a PATH artifact. Post-gate tree clean x3 (T-275). Zero new code skips/`.only`/`xfail` in the delta. `deploy:` block byte-identical base->HEAD (14 jobs, both coverage ratchets retained); no gate config touched, no threshold moved. Shard-glob union clean (449/449 matched exactly once, T-122 holds); all 27 new test files CI-reachable. CI green on `main` at this HEAD. `main` still has no `required_status_checks` (T-222, sixth audit running). Delta-touched determinism 3x NOT run: 47 touched test files across four roots collapses into full gates (2026-08-16 rule).

## Remediation 2026-08-29 — PR #140

All **35 un-DONE P0/P1 findings** from the same cycle's audit are DONE:
T-250…T-283 plus T-311. The 27 P2s are DEFERRED. Evidence per task, with
red/green counts, is in `TEST_LOG.md` under `## Remediation 2026-08-29`.

Method: 9 worktree agents in three waves (capped at ~6 concurrent per the
2026-08-26 rail), with the lead cherry-picking serially, re-deriving the
headline evidence in the LANDED tree rather than trusting the scoped agent
report, and pushing after every task commit. Three landings were corrected by
the lead on review — see below.

### What remediation itself uncovered

Four of these findings were coverage gaps whose subject turned out to be
BROKEN, which is the pattern the 2026-08-27 lesson predicts:

- **T-262** — the empty-dump path was not hypothetical. Driving the real
  prune/upload sequence promoted a 168-byte empty dump, pruned two real ones
  against it, uploaded it off-box and wrote `db-backup = ok`.
- **T-263** — `fetch_vixts.py`'s rebuild branch emitted no freshness verdict
  at all, so a stale Cboe series was heartbeat `ok` and rendered a confident
  regime badge on a two-week-old session. `fetch_vixcor.py` already had the
  verdict on both branches; vixts was the odd one out.
- **T-283** — the leap wrapper charged process startup and the first curl
  round trip to the shed-wait budget, so on a loaded box it could give up
  with ZERO retries. That is the silent no-retry behind the 2026-08-27 page.
- **T-277** — the embargo test was green on this clone ONLY because there is
  no `.env`; with `TURSO_DB_URL` set, as production always has it, the
  untouched test pinned the opposite of production.

### Lead corrections applied on landing

- **T-258 — the finding's central premise is WRONG, and the source change it
  produced has been REVERTED.** T-258 states "no test references
  `resolve_flows`, `_flows_query_id` or `IB_FLEX_FLOWS_QUERY_ID`". The repo
  has TWO python collection roots and the finding searched only
  `scripts/tests/`. The root `tests/` tree holds
  `tests/test_perf_twr_flex_single_request.py`, which references all three
  and whose `test_a_distinct_flows_query_id_is_still_fetched` deliberately
  pins the OPPOSITE contract. Implementing the AC as written therefore made a
  divergent `IB_FLEX_FLOWS_QUERY_ID` a silent no-op — reddening that test
  (invisible to the task agent, which ran only `scripts/tests`) and turning
  CLAUDE.md's guidance not to SET the knob into code that IGNORES it. That is
  a throttle-facing product decision about an operator knob, not a test fix,
  and the loop's own rail permits a source change only when a test correctly
  fails against a real defect. The undisputed property — one request per run
  in the DEFAULT configuration — is kept and mutation-checked; the
  divergent-id case is pinned as-is in both files, each naming the other.
- **T-259** — the agent's `_default_ingest` rewrite used `Path.write_text`
  then `chmod 0600`, leaving DECRYPTED brokerage-statement plaintext
  world-readable for the width of the ingest. Replaced with
  `os.open(..., O_CREAT|O_WRONLY|O_TRUNC, 0o600)` before the first byte.
- **T-253** — withholding the blended aggregate silently made
  `total_deployed_dollars` an UNDER-statement and `remaining_capacity_pct` an
  OVER-statement, the unsafe direction for Gate 3's 2.5% cap. The payload now
  carries `unmeasured_basis_count`.
- **T-311** — the agent's race fix is correct (8/8 in isolation, where the
  pre-fix file was 3/6 red), but the file still hard-timed out in 2 of 9 runs
  under load. That is the T-238 class, not the race, and it would have redded
  the closing gate. Fixed before it per the 2026-08-27 lesson.

### New findings raised by this phase — for the next audit, NOT fixed here

Recorded rather than chased, per the "no mid-loop chases" rule. Left
un-numbered; the next audit should triage and number them.

1. **`useOrderRisk.ts:741` is the only `okToSubmit` in the hook that does not
   also require `coverageStatus === "resolved"`**, so a stock/futures
   close-out arms Transmit under `no-portfolio` while the option close-out at
   `:834` refuses. Surfaced by T-260; behaviour deliberately left unchanged.
2. **`resolveEntryCost` still returns a `number`**, so after T-253 the
   close-ticket order paths (`ModifyOrderModal`, `OrderTab`,
   `lib/order/positionTrade`) and the portfolio-level unrealized totals still
   compute a blended figure for a `mixed` position. Widening it touches 19
   sites beyond T-253's AC.
3. **The divergent-`IB_FLEX_FLOWS_QUERY_ID` semantics are genuinely
   undecided** — see the T-258 correction above. Two tests now pin today's
   behaviour and name each other; an operator decision is what settles it.
4. **The e2e job is still non-gating.** T-271 curated five more specs into it
   (14 → 19, each pre-flighted under `next start` 3/3 green), but the job is
   in neither `stage-release.needs` nor `deploy.needs`, so none of the 19 can
   block a release. Same class as T-222, which re-confirmed that `main`
   carries no `required_status_checks` at all. Both are operator actions
   outside this branch.

### Darwin cloud baseline — UNCHANGED at 37

`37 failed, 1277 passed, 7 skipped` (the audit recorded `37 failed, 1263
passed`; the +14 passing are this phase's new cloud tests). Attributed by
FILE, not by count: 21 `test_ib_gateway_control.py` + 13
`test_bootstrap_control_plane.py` — the standing `sha256sum` / `bash >= 4`
class, T-118 — plus 3 deliberate `test_caddy_edge_timeouts.py` reds (T-205
working as designed; no `caddy` binary on this host). **Zero failures in
either cloud file this phase touched.**

## Remediation 2026-09-04 — PR #274

All 31 findings from this cycle's audit are DONE: T-409…T-439 (2 P0, 10 P1,
19 P2). No DEFERRED. Evidence per task in `TEST_LOG.md` §Remediation
2026-09-04. Eight parallel worktree agents; every landing re-verified in the
main clone before commit.

Four findings' subjects were broken product code, not only test gaps, per the
standing pattern:

- **T-410 (P0)** — the exit-order refusal latch stopped calling `_note_error`
  from the second cycle onward, so `exit-orders` heartbeat `ok` while a
  position sat with no target or stop. Source fixed; the skip path records the
  error every cycle.
- **T-412** — `refusal_key` collided on `(None, "target")` for any two legs
  lacking a `journal_trade_id`, so a second position's protective leg was never
  submitted. Key now includes `contract.localSymbol`.
- **T-417** — production installed unpinned caddy from the Cloudsmith `stable`
  repo while CI froze 2.11.4, so the edge tests guarding non-replay of a severed
  `POST /api/orders/place` no longer described the production binary.
  `setup-vps.sh` pins the same version; a test asserts the literals are equal.
- **T-432** — bumping `STATUS_SCHEMA_VERSION` to 3 exposed that `health_probe`
  and `watchdog.external_probe` both hard-pinned `== 2` and would have failed
  closed against a v3 host. Both now accept the current schema and exactly one
  predecessor. **Rollout constraint:** `deploy.sh` must land `health_probe` and
  `watchdog` alongside `health_service`.

Three corrections to the audit's own claims, made on the evidence:

- **T-420** — the escalated verdict is `down`, not `degraded`; `dependency_stuck`
  returns before the dependency-degrade branch. Tests assert the real semantics.
- **T-426** — already satisfied at this HEAD by the T-173 guard at
  `test_ci_deploy_concurrency.py:671-701`. No change made; the prescribed
  `cloud/tests/test_sub/` drill was run and reds correctly.
- **T-431** — the audit's arithmetic was wrong (real age range 1h45m..25h44m,
  not 24h..47h59m), so the case could not have flaked. The determinism fix
  stands anyway: a frozen-clock sweep over all 24 UTC hours.
- **T-439** — neither hypothesis held. The pre-exec path does clean up and exit
  71; the socket bind poll is 50 forked `sleep 0.1` calls, ~12s on darwin
  against a 10s harness timeout. Bound raised at that one call site; no skip.
  The underlying latency asymmetry is left as-is and is why the test is one bad
  day from flaking on Linux too.

**T-435 is operator-only.** The Clerk publishable key is inlined as a literal
default parameter into the compiled edge middleware, so `isSetupMode()` is
constant-false in any keyed production build and no spec against the shared
Playwright `webServer` can observe the setup redirect. A true setup-mode e2e
needs a second keyless compile-mode build with its own `distDir`, its own
`next start` and a new CI job. Landed instead: a unit-level pin on the
auth-misconfigured gate, which had no coverage at all.

## Remediation 2026-09-03 — PR #260

All 11 un-DONE P0/P1 findings from this cycle's audit are DONE: T-382 (P0),
T-383…T-391, T-408. The 16 P2s (T-392…T-407) are DEFERRED. Evidence per task
in `TEST_LOG.md` §Remediation 2026-09-03. Two findings' subjects were broken
product code, per the standing pattern: T-385 (serializer legs carried no
`secType`, so the stock-leg cap exemption never matched a real snapshot — 
source fixed, wire-tested) and T-389 (the keyless early return in
`Providers.tsx` dropped the realtime tree — provider now mounts regardless).
One lead correction on landing: T-390's new file leaked the module-global
`_SESSION_EXPORTED` across files and redded `test_env_fallback_flagged`;
fixed with a snapshot/restore fixture, not by weakening either test.
T-408's enforcement half (a tsc step in ci.yml) is an operator decision and
was deliberately not made here.

## Delta audit 2026-09-05 (second pass)

Range `391aaaea..be64e1fc` — 61 commits / 107 files / +5789−172, base `391aaaea`
(the first pass's audited SHA; its own remediation T-440…T-461 and the
reliability loop's REL-232…REL-247 both land inside this range and are
re-triaged here as ordinary delta, per the 2026-08-22 rule).
Saturday run — the weekend-false-red class is live.
23 new findings: **2 P0, 10 P1, 11 P2** (T-462…T-484).

Two findings converged across independent agents at the same file:line
(T-462 from the source-coverage and blast-radius dimensions; T-464 from
net-negative, source-coverage and blast-radius). Both were re-derived from
source by the lead before numbering.

---

### T-462 — P0 — the REL-236 disconnect arm is dead in production; the new specs pin it at the leaf, not at the wire

`web/lib/order/quoteSubmitGate.ts:30` closes the submit gate on
`feedConnected === false`. Both consumers declare the prop optional —
`web/components/ModifyOrderModal.tsx:54,318,556` and
`web/components/SingleLegOrderTicket.tsx:79,154,250` — and **no production
call site supplies it**. All four call sites verified by the lead:
`web/components/WorkspaceSections.tsx:3256`,
`web/components/ticker-detail/OrderTab.tsx:1127`,
`web/components/ticker-detail/BookTab.tsx:399`,
`web/components/InstrumentDetailModal.tsx:245`. A repo-wide grep for
`feedConnected` outside the two components returns only an unrelated local
in `web/components/mobile/MobileMoreDrawer.tsx:64`. Because the guard tests
`=== false`, an absent prop is `undefined` and leaves the gate OPEN.

The new specs `web/tests/order-ticket-quote-gate.test.tsx` and
`web/tests/modify-order-quote-gate.test.tsx` pass `feedConnected` themselves
from a test harness, so the gate is verified at the leaf component and
nowhere at the surface that owns the fetch — exactly the CLAUDE.md "a gated
action is tested at the wire, not at the button" class. Deleting the
`feedConnected` field from both prop types and from both `quoteSubmitGate`
calls reds nothing. Only the quote-age arm is live for a real user.

**AC (red/green):** render `OrderTab` / `WorkspaceSections` (not the modal)
with the IB/relay status disconnected, stub `fetch`, drive Modify/Place in
its otherwise-armed state; assert zero requests fire and the blocked reason
renders. Must red today, and must red again when `feedConnected={...}` is
removed from the call site.

### T-463 — P0 — `_cancel_confirmed_at_broker`'s status arm is untested on the replace money path

`scripts/api/server.py:3191` —
`return str(payload.get("status") or "") in ("Cancelled", "ApiCancelled")` —
gates replacement placement at `server.py:3216`. Mutating 3191 to
`return True` makes a broker snapshot of `{"status": "Submitted", "filled": 0}`
read as confirmed-cancelled, so `/orders/replace` places a full-size
replacement on top of a still-working order: doubled live exposure.

Coverage proof (lead-run): `grep -rn "_cancel_confirmed_at_broker\|_find_working_order"
scripts/api/tests/ scripts/tests/ web/tests/` returns exactly one hit,
`scripts/api/tests/test_order_replace_state_machine.py:409`, which returns
`filled: 3` and is therefore caught by the earlier `filled > 0` arm at
`server.py:3186-3189`. The two happy-path tests (`:266`, `:297`) never patch
`_find_working_order`, so they exercise the `payload is None → True` arm.
The status-string arm has no test.

**AC:** patch `_find_working_order` to `{"orderId": 10, "status": "Submitted",
"filled": 0}` behind a structured-202 cancel error; assert 502
`REPLACE_PARTIAL`, `cancelled == []`, `place.assert_not_awaited()`. Must go
red on `return True` at 3191.

### T-464 — P1 — `fill-driven-portfolio-refresh-wire` asserts against a hand-cloned WorkspaceShell that has already drifted

`web/tests/fill-driven-portfolio-refresh-wire.test.tsx:59-73` defines a local
`FillRefreshHarness` its own docblock calls "WorkspaceShell's exact wiring".
It is not: `web/components/WorkspaceShell.tsx:430` calls
`useFillToasts(orders, upsertToast, onNewFills, hasToastKey)` with four
arguments; the harness at `:71` passes three (lead-verified by grep). The
fourth is `isToastLive` (`web/lib/useFillToasts.ts:49`) and sits in the
effect's dependency array (`:101`). The harness also models `isDemoMode` as
a prop that flips, while production reads
`process.env.NEXT_PUBLIC_RADON_DEMO` (`WorkspaceShell.tsx:420`), which never
flips — so the demo→live scenario the docblock sells as the stale-ref
regression is not a production state.

Defect it passes over: remove or misorder the `onNewFills` argument at
`WorkspaceShell.tsx:430` and every assertion still passes while the
positions table silently stops refreshing on fills — the original R-640 /
T-459 bug the file exists to pin.

**AC:** render `WorkspaceShell` itself (mock only its data hooks), stub
`fetch`, push an orders payload carrying a new `execId`, assert exactly one
`POST /api/portfolio`. Must red when line 430's third argument is removed.

### T-465 — P1 — `hasToastKey` has zero test coverage of its definition or its wiring

`web/lib/useToast.ts:104` defines `hasToastKey` and `:106` exports it;
`web/components/WorkspaceShell.tsx:77,430` is its only consumer.
`grep -rn "hasToastKey" web/tests/` returns **no matches** (lead-verified).

Scope note, narrower than first reported: `useFillToasts`'s `isToastLive`
BRANCH is covered — `web/tests/fill-toasts-hook.test.tsx:199` injects its
own predicate. What is uncovered is (a) `hasToastKey` itself flipping false
after `dismissToast`, and (b) WorkspaceShell actually passing it. Making
`hasToastKey` return `true` unconditionally at `useToast.ts:104` kills the
R-642 "dismissed toast forgets its running total" behaviour with the whole
suite green.

**AC:** a `useToast` unit test asserting `hasToastKey(key)` flips false after
`dismissToast`, plus a WorkspaceShell-level test that a dismissed fill
toast's next fill shows its own quantity, not the cumulative total. Red on
either mutation.

### T-466 — P1 — R-661 route coverage is a `readFileSync` + `toContain` grep

`web/tests/rel244-demo-rate-budget.test.ts:114-121` reads
`app/api/trin/route.ts` and `app/api/dispersion/route.ts` as TEXT and
asserts the substrings `rate: { key: "trin:route"` and
`durableRateTier: "A"` are present (lead-verified at the cited lines).

The literal can sit in a dead branch, a second `requireRouteAccess()` call,
an unreached early return, or an object never passed to the gate. A demo
user hammering `/api/trin` with zero budget consumed is the R-661 bug, and
this test stays green through it. The same file's other tests (`:29-112`)
drive `handleDemoGate` for real — the grep is the odd one out, and it is the
half covering the newly-fixed routes.

**AC:** call the real `GET` of `app/api/trin/route.ts` with a mocked
`requireRouteAccess`; assert the recorded options argument BY VALUE, and
that a limiter returning `success:false` on the daily tier yields 429 with
no upstream fetch. Red when the option leaves the live call path, not merely
the file.

### T-467 — P1 — the Playwright digest test greps the Dockerfile, and the digest it pins has no consumer

`scripts/tests/test_dockerfile_playwright_digest.py:13-30` asserts
`Dockerfile.python` CONTAINS `"sha256sum"` and `"/ms-playwright/.browsers.sha256"`.
The pipeline at `docker/app/Dockerfile.python:26-27`
(`find … | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1`)
produces a valid-looking digest OF NOTHING when `find` matches zero files —
a misspelled `PLAYWRIGHT_BROWSERS_PATH`, an install writing elsewhere, or
`--only-shell` fetching nothing all yield the well-known empty-stream
sha256 and a green test. The chain is `&&`-joined under `RUN` with no
`pipefail`, so a failing `find`/`xargs` mid-pipe is masked by `cut`'s exit
status. Nothing in the repo reads `.browsers.sha256`, so the stated purpose
is unimplemented. This is the 2026-08-27 Caddyfile shape: green test,
artifact does nothing.

**AC:** execute the digest pipeline against a temp tree; assert (a) a
populated tree yields a digest differing from the empty-input digest, and
(b) an empty/missing tree exits non-zero rather than recording
`e3b0c442…`. Plus a consumer assertion, or delete the digest.

### T-468 — P1 — the `git_repo` fixture inherits the host's global git config, so the operator's pre-commit hook runs inside the test

`scripts/tests/test_nightly_green_base.py:63-79` runs `git init -q`, sets
only `user.email`/`user.name`, then three `git commit` calls with
`check=True`. It never neutralizes `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM`.
On this runner `git config --global core.hooksPath` is
`/Users/joemccann/.git-hooks`, whose `pre-commit:6-15` runs `gitleaks` and
a `tsc --noEmit` branch and `exit 1` on any hit — so every fixture commit
executes them and a non-zero exit raises `CalledProcessError`, erroring all
four `TestCli` tests (`:86,95,101,108`) for a reason unrelated to
`nightly_green_base.py`. `commit.gpgsign`, `init.defaultBranch` and
`init.templateDir` are equally unpinned.

Counter-example in the same delta proving the repo knows the pattern:
`cloud/tests/test_rel234_compose_gate.py:152` passes
`env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}`.

**AC:** RED — with `core.hooksPath` pointing at a dir whose `pre-commit` is
`#!/bin/sh\nexit 1`, `TestCli` errors today. GREEN — after adding
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`,
`GIT_TERMINAL_PROMPT=0` and `-c core.hooksPath=/dev/null -c commit.gpgsign=false`,
the same setup passes.

### T-469 — P1 — the held-out-spec guard keys off the COMMITTER date, so any rebase re-reds a correctly-stamped spec

`scripts/tests/test_e2e_ci_curation.py:218-228` resolves
`_git("log","-1","--format=%cs", base+"..HEAD", "--", "web/e2e/"+spec)` and
fails when `stamped < changed_on`. `%cs` is the committer date, which git
rewrites to *now* on every rebase, cherry-pick, amend and squash-merge. A
spec stamped `# REVIEWED 2026-09-05` turns red the moment the branch is
rebased on 2026-09-06 with a byte-identical diff and an unchanged stamp.
`%cs` also renders in the commit's recorded timezone, so a commit written
21:00 PT stamps the next calendar day against an operator writing "today"
in local time. Base resolution (`:171-180`) falls back to `origin/main`, so
on a long-lived branch the window is wide and sweeps in many specs.

This is a self-inflicted guard: T-438 landed it, and the 2026-09-04 lesson
already records it firing on the branch that added it.

**AC:** RED — stamp a held-out spec today, commit, then
`git commit --amend --no-edit` so `%cs` advances a day; the assert fires.
GREEN — switch to `%as` (author date, stable across rebase) and the amended
tree passes.

### T-470 — P1 — upstream status passthrough and the `>= 500` cache-fallback guard are untested on three scan routes

`web/app/api/gex/route.ts:201-202`, `web/app/api/regime/route.ts:365-366`,
`web/app/api/gamma-rotation/route.ts:210-211`.

Mutations no test catches: (a) hardcode `const status = 502`, dropping the
`RadonApiError` status passthrough; (b) widen `if (status >= 500)` to
`if (true)`, so a 4xx upstream REJECTION silently serves a cached snapshot
instead of surfacing the client error. `rel238-scan-failure-surfaced.test.tsx:61,95,118`
rejects with a plain `Error` and only asserts `status >= 500`, so neither
mutation reds.

**AC:** reject `radonFetch` with `new RadonApiError(429, "…")`; assert the
response status is exactly 429, no cached body is served, and
`scan_succeeded: false`. Red on both mutations.

### T-471 — P1 — the `sumUnrealizedBreakdown` parity pin is now tautological

`web/lib/unrealizedBreakdown.ts`: the row builder dropped its
`if (entry == null …) return []` and the sum dropped
`if (hasBlendedLegBasis(pos)) continue`, so both predicates are now
identically `pnl != null`.
`web/tests/unrealized-breakdown-signed.test.ts:211-275`
("sumUnrealizedBreakdown matches row P&L total") previously compared two
DIFFERENT exclusion sets; it now sums set S against set S and can never
diverge. Both its fixtures (`:212-269`) are cleanly measurable.

Related, same file: `:113` and `:170` assert
`parseSigned(row.col2) - parseSigned(row.col1) ≈ row.pnl`, but `col1` can
now be the literal `"---"`, which `parseSigned` will not turn into a
number — NaN for any entry-less position. No current fixture reaches it; the
guard is absent, not proven safe.

**AC:** add a `hasBlendedLegBasis` position to the `:211` portfolio. Parity
must still hold, AND a `col1 === "---"` row must not NaN the `:113` check.

### T-472 — P1 — `modify-partial-fill-quantity.test.tsx` no longer exercises the wire path its name pins

`web/components/ModifyOrderModal.tsx:534-536` now takes `currentQuantity`
from `fillSnapshot`, and the submit branch computes
`request.newQuantity = fillSnapshot.filled + parsedQuantity`, leaving
`toIbTotalQuantity(order, …)` only as a `fillSnapshot == null` fallback.
`fillSnapshot` is seeded non-null whenever `order` is non-null, so that
fallback is unreachable from the modal.
`web/tests/modify-partial-fill-quantity.test.tsx:152-160` ("sends filled +
entered as the new TOTAL", asserting `{ newQuantity: 516 }`) still passes,
but through the snapshot arithmetic; its sibling unit assertions at
`:105-111` keep `toIbTotalQuantity` looking covered while the modal's use of
it is dead.

**AC:** re-render the modal with `filled` advanced 16→100 and submit a
price-only change: assert no `newQuantity` is transmitted. Then advance
`filled` and submit a QUANTITY change: assert the reseed/refuse branch
(`fillRaceNotice`) fires rather than `toIbTotalQuantity`.

### T-473 — P1 — display and math read different fill counts, and no untouched test re-renders the modal

`web/components/ModifyOrderModal.tsx:537` keeps
`alreadyFilled = filledQuantity(order)` (LIVE) and renders it at `:688`,
while `currentQuantity` (`:534`) and `quantityChanged` (`:549`) use the
FROZEN snapshot. `web/tests/modify-partial-fill-quantity.test.tsx:135-140`
("shows the operator what already filled", asserting `984x` and `16`)
renders once and never re-renders; a grep for `rerender` across the
`modify-*` / `order*` / `orders-*` suites matched only the new
`modify-fill-race-quantity.test.tsx`. So the divergence — info line says
"16 filled, 984x" while the field is still seeded 984 after a live advance
to 100 — has no untouched coverage.

**AC:** re-render with `filled: 100, remaining: 900`; assert the
`.modify-order-info` text and `#modify-quantity-input` describe the same
fill count, or that the stale field is visibly flagged.

---

### T-474 — P2 — the retry-budget test mirrors the script's arithmetic instead of executing it

`scripts/tests/test_rel241_retry_budget.py:32-59` regex-scrapes constants
and asserts `(retries + 1) * curl_timeout + retries * delay + 10 <= TimeoutStartSec`.
The `attempts = retries + 1` term (`:53`) is the test's OWN model of the loop
shape in `scripts/run_portfolio_refresh.sh:120-151`, never executed. Change
the loop bound to `while [ $attempt -le $RETRY_LIMIT ]` (3 retries → 4 curls)
with the constants unchanged and the test still computes 3 attempts → 106s
and passes, while the real worst case is 144s against `TimeoutStartSec=120`
and systemd kills the oneshot as `Result=timeout` — the exact failure
REL-241 exists to prevent. The regex `curl\s[^\n]*-m\s+(\d+)` is also
positionally fragile: it takes the FIRST curl in the file.

**AC:** run the real script with `curl`/`sleep` stubs that log invocations
and force a 502; assert the curl invocation COUNT and the total simulated
wall clock, then compare that measured number to `TimeoutStartSec`. Red when
the loop bound changes without the constants changing.

### T-475 — P2 — `test_ci_demo_build_order.py` pins step INDICES, not bundle isolation

`scripts/tests/test_ci_demo_build_order.py:30-65` asserts the demo build's
index exceeds the first `upload-artifact` index, the demo spec is at
`demo_build + 1`, and is `len(steps) - 1`. The actual hazard is that
`web/.next` is SHARED, not that steps are ordered: the prod-spec step can
gain `if: failure()` re-run semantics, or a `continue-on-error` plus a later
re-invocation inside the same `run` block, and the ordering assertion is
untouched. Conversely, dropping `NEXT_PUBLIC_RADON_DEMO` from the SPEC step
reds nothing — the predicate only checks the build step's env. The
`"build" in str(s.get("run",""))` predicate also matches any future step
whose script mentions "build".

**AC:** assert the isolation invariant — the demo build and demo spec are
the trailing contiguous pair AND both carry `NEXT_PUBLIC_RADON_DEMO: "1"`,
and no later step combines `failure()` with a `playwright test` re-run.
Better: build to a distinct `--distDir` so the bundles cannot collide, and
delete the ordering test.

### T-476 — P2 — two shell-body greps inside otherwise-executed cloud suites

`cloud/tests/test_rel242_preflight_and_drift.py:143-152` asserts the string
`compose_body_is_valid "$candidate"` appears in a slice of
`refresh_install_file`'s body; `cloud/tests/test_rel234_compose_gate.py:94-102`
asserts `compose_body_is_valid` appears in the `compose)` case arm. Both
pass over the call being present with its exit status DISCARDED —
`… || true`, or inside `if false; then`, or assigned and never tested —
which under `set -uo pipefail` (not `-e`) reproduces R-635 exactly: gate
present in source, gate not enforced. Mitigating: both files contain
executed counterparts (`test_rel242…:155`, the `POISONS` matrix at
`test_rel234…:118-125`), so these two add maintenance cost without coverage.

**AC:** delete them, or replace with a run of the real arm carrying a
poisoned candidate asserting non-zero exit AND the target file unwritten —
which the neighbouring tests already do.

### T-477 — P2 — `mockRadonFetch` is not reset in the `GET /api/regime` describe

`web/tests/api-routes.test.ts:841-861`'s `beforeEach` resets
`vi.resetModules()`, `mockReadFile` and `mockStat` but NOT `mockRadonFetch`.
The fixture payload `date: "2026-04-22"` is stale per
`web/lib/criStaleness.ts:56`, so `web/app/api/regime/route.ts:352` calls
`triggerBackgroundScan()` → `radonFetch("/regime/scan")` against whatever
the preceding `POST /api/gex` describe left behind (`:811` sets
`mockRejectedValue(new Error("upstream down"))`). It survives only because
`web/lib/backgroundScan.ts:56-60` swallows the rejection. It is a live order
dependence: reorder or shard the file and the fired request changes identity.

**AC:** add `expect(mockRadonFetch).not.toHaveBeenCalled()` (or assert the
scan URL) to the cached-payload test — it fails against the leaked mock.
Fix with `mockRadonFetch.mockReset()` in that `beforeEach` and pin the
fixture `date` to `mostRecentSessionDate()`.

### T-478 — P2 — a module-load-time `mockStat` default that no `beforeEach` restores

`web/tests/rel238-scan-failure-surfaced.test.tsx:15` and
`web/tests/api-routes.test.ts:96` both do
`const mockStat = vi.fn().mockResolvedValue({ mtimeMs: Date.now() })`,
evaluated ONCE at module import and frozen for the file. In `rel238-*` the
`beforeEach` (`:41-49`) resets `mockReadFile`, `mockRadonFetch` and
`mockExecute` — `mockStat` is absent, so any test that stubs it leaks into
every later test, and the "fresh" mtime silently ages as the file runs.
`api-routes.test.ts:95` documents the intent ("mtime 5 s ago (fresh)") while
only one describe re-stamps it.

**AC:** a test setting `mockStat.mockResolvedValue({mtimeMs: 0})` makes the
NEXT test in file order observe a stale mtime. Fix by resetting and
re-stamping from a `beforeEach` with a window-relative mtime.

### T-479 — P2 — presentational CSS classes used as test hooks where a testid belongs

`web/tests/modify-order-close-pnl.test.tsx:161,440,477,499,518,540` bind six
assertions to `within(document.querySelector(".order-confirm-summary") as HTMLElement)`,
a class whose only definition is a STYLE rule at `web/app/globals.css:9022`.
Renaming it during a purely visual change nulls the selector, and the
`as HTMLElement` cast turns that into a `within(null)` throw rather than a
readable failure. Same shape in
`web/tests/position-mixed-basis-refuses-aggregate.test.tsx:171-176,199-207`:
`statValue()` walks `.pos-stat` / `.pos-stat-label` / `.pos-stat-value`, and
`cellUnder()` (`:200-207`) resolves a column by HEADER INDEX into `td` order,
so inserting any column shifts every assertion. 141 component files already
carry `data-testid`.

**AC:** rename `.order-confirm-summary` to `.order-confirm-panel` in
`globals.css` and the component — the six tests throw today while the UI is
unchanged. After switching to `getByTestId` and per-cell `data-testid`, the
same rename is invisible.

### T-480 — P2 — real-timer 20 ms sleeps carrying negative "nothing fired" assertions

`web/tests/order-ticket-quote-gate.test.tsx:189,202`;
`web/tests/modify-order-quote-gate.test.tsx:176,187`;
`web/tests/use-sync-hook-pending-refresh.test.tsx:85,98` each do
`await new Promise(r => setTimeout(r, 20))` on REAL timers and then assert
`toHaveLength(0)` / `toHaveLength(2|3)`. The 20 ms budget is the entire
margin and must exceed React's commit, effect flush and any queued
microtask chain. This repo has already redded on a 41 ms margin (T-238), and
`web/tests/footer-telemetry-flex-refresh.test.tsx:22` in the SAME delta
shows the correct pattern (`vi.useFakeTimers()` + `advanceTimersByTime`).

**AC:** lower the budget to `0`; if the negative assertions still hold the
wait is decorative and should be deleted. Fix with `waitFor` for positive
assertions and fake timers for the negative ones, so no assertion depends on
wall time.

### T-481 — P2 — a test module executes a sibling TEST file at import time

`scripts/tests/test_loop_session_limit.py:26-33` builds a
`spec_from_file_location` against `test_weekend_model_ladder.py` and calls
`exec_module`, then lifts `LADDER`, `LOOPS` and the private `_run`.
Collection of this file now depends on another test file existing, importing
cleanly, and keeping a private helper; pytest separately imports the same
source under its own name, so two module objects with two copies of every
test class live in the process. Renaming or deleting the ladder test becomes
a collection ERROR, not a skip.

**AC:** `git mv scripts/tests/test_weekend_model_ladder.py scripts/tests/test_ladder.py`
— `test_loop_session_limit.py` fails at collection today. Move
`LADDER`/`LOOPS`/`_run` into a non-test helper (e.g.
`scripts/tests/_loop_harness.py`) imported by both, and the rename is inert.

### T-482 — P2 — the Playwright `RADON_AUTHLESS_TEST` / `pk_live_` invariant is unpinned

`web/middleware.ts` now invokes `assertAuthlessTestFlagAbsentInProduction()`
at TOP LEVEL, throwing when `RADON_AUTHLESS_TEST === "1"` and the
publishable key starts with `pk_live_`. `web/playwright.config.ts:77-80`
sets `RADON_AUTHLESS_TEST: "1"` on the e2e web server. If that server's
environment ever carries a `pk_live_` key, middleware fails at IMPORT and
every spec under `web/e2e/` fails at navigation rather than at its own
assertion. No `pk_live` value was found in this clone, so the e2e env is not
confirmed unsafe — only unpinned.

**AC:** a test asserting `playwright.config.ts`'s webServer env either
unsets `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` or forces a `pk_test_` value
whenever it sets `RADON_AUTHLESS_TEST=1`.

### T-483 — P2 — `demo-headlines-hook.test.tsx` stops one tick short of the cycle its name promises

`web/tests/demo-headlines-hook.test.tsx:80-102` ("polls again and preserves
the last snapshot when a refresh fails") advances exactly `60_000` and
asserts two fetches — but that second call is the poll after the SUCCESSFUL
first fetch. `web/lib/useHeadlines.ts` re-arms the demo poll at
`min(POLL_MS * 2**failures, BACKOFF_MAX_MS)`
(`web/lib/demo/headlinesPolicy.ts`, `POLL_MS = 60_000`), so the poll AFTER
the failure needs 120s and is never advanced to. The backoff the name
implies is untested.

**AC:** after the rejection, advance 60_000 and assert no third fetch, then
advance a further 60_000 and assert exactly three.

### T-484 — P2 — the recorded darwin cloud baseline is an artifact of `/bin/bash` 3.2 being first on PATH, so every prior baseline comparison is unreliable

The audit ledger records a darwin `cloud/tests` baseline of 10 → 12 → 34 →
35 → 37 → 33 failures, attributed since 2026-08-29 to "bash 3.2 + missing
`caddy`". This run's gate script exported
`PATH="…:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:…"`, which resolves
`bash` to `/opt/homebrew/bin/bash` **5.3.9** rather than `/bin/bash` 3.2, and
the result is **5 failed / 1785 passed** — every one of them in
`cloud/tests/test_caddy_edge_timeouts.py`, with `command -v caddy` returning
ABSENT (both verified this run).

So the ~30 "bash 3.2" reds were never a property of the tree or even of the
host; they were a property of which `bash` the invoking shell happened to
put first. A baseline that swings 33 → 5 on an environment variable cannot
do the job the ledger asks of it — attributing a cloud red to the delta —
and the 2026-08-22 "the baseline is a LIST, not a count" rail is necessary
but insufficient, because the LIST moves too.

**AC:** pin the interpreter the cloud suite shells out to. Either the tests
resolve `bash` explicitly (and skip with a named reason when no bash >= 4 is
present), or the loop's gate invocation records the resolved
`bash --version` and `command -v caddy` alongside the FAILED list, so a
baseline is only ever compared against a run with the same resolved
toolchain. Red/green: run `cloud/tests` twice with `/bin` and with
`/opt/homebrew/bin` first on PATH; the recorded baseline artifact must be
identical, or the difference must be an explicit skip rather than a failure.

---

**Standing sweeps (this pass):**

- **Gates, serial, round 1, launched BEFORE the agent fan-out** (2026-08-29
  rail), no sibling loop running, load 3–6 throughout:
  - `python3.13 -m pytest` → **11762 passed, 1 skipped, 0 failed**, 1730s.
  - `npx vitest run` → 39 files failed at IMPORT with only 14 test failures,
    one cause: `thinking-orbs` and `border-beam` (both declared at
    `web/package.json:37,46`) absent from this clone's `node_modules`. This
    is the 2026-09-05 first-pass lesson recurring after the clone's tree
    reset. After installing the two packages, the same 39 files re-ran
    **323 passed / 0 failed / rc=0**. Environment fixed, repo untouched;
    the npm-generated `web/package-lock.json` was removed and the tree
    verified clean.
  - `python3.13 -m pytest cloud/tests` → **5 failed / 1785 passed / 7
    skipped**, all five in `test_caddy_edge_timeouts.py`, `caddy` ABSENT on
    this host. See T-484 for why this number is not comparable to the
    recorded 33.
- **Post-gate tree sweep (T-275):** clean after pytest, after vitest and
  after cloud — 0 lines ×3.
- **Skip / `.only` / `xfail` sweep:** parsed the delta patch for added
  lines matching `test.skip|it.skip|describe.skip|pytest.mark.skip|pytest.skip|xfail|.only(`
  — **2 hits, both prose inside `TEST_AUDIT.md`**. Zero new code skips.
- **Gate-enforcement drift:** the `deploy:` block is **byte-identical**
  base → HEAD, `deploy.needs` is 14 jobs, and both coverage ratchets
  (`web-coverage`, `py-coverage`) are present. `vitest.config.ts`,
  `pytest.ini`/`pyproject`/`.coveragerc` and `playwright.config.ts` are
  untouched in the delta, so no threshold moved and no exclusion grew. The
  only `ci.yml` change is T-448's own remediation (the trace-upload step
  moved ahead of the demo rebuild, plus its guard comment) — a
  strengthening, not a weakening.
- **Shard-glob union:** all **449** `scripts/tests/test_*.py` files are
  matched by exactly one of the 14 CI shard globs (match-count distribution
  `{1: 449}`), so T-122 holds.
- **CI reachability:** all 27 new test files land in CI-reachable
  directories (`web/tests` 16, `scripts/tests` 8, `cloud/tests` 2,
  `scripts/api/tests` 1).
- **Branch protection (T-222, sixth consecutive audit):**
  `gh api repos/joemccann/radon/branches/main/protection` still returns
  `required_status_checks: null`. Unchanged, still open.
- **CI at HEAD:** `CI (test gate + deploy)` **success** on `main` at
  `be64e1fc`.
- **Delta-touched determinism 3×: NOT RUN.** The delta touches 47 test
  files across all four collection roots, which per the 2026-08-16 rule
  collapses into full-gate runs. Saying so rather than pretending it
  happened.
