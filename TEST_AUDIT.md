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
