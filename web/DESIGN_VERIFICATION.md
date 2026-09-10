# Clear production UI verification

Date: 2026-09-05. Design: approved A / Clear. Scope: real Next.js application, not the comparison mockup. Final comprehensive evidence uses the compiled non-demo build at `http://localhost:3002`.

## Post-handoff repair: normal localhost realtime

The user's signed-in localhost screenshot exposed an unverified handoff path: **the browser relay was blocked by CSP even though the relay process and IB API were healthy**. The defect is repaired and verified in the rebuilt preview. The earlier test results below remain real but did not establish this normal-policy realtime transport by themselves.

- Actual normal compiled HTML sends `connect-src 'self' wss: https:` from `middleware.ts:87`. The compiled client's local default is `ws://localhost:8765` from `lib/realtimeSocketAuth.ts:26`; it is a different port/origin from the page on localhost:3000.
- An isolated Chromium check on the actual `/sign-in` response reproduces `Connecting to 'ws://localhost:8765/' violates ... connect-src 'self' wss: https:` and captures `securitypolicyviolation { effectiveDirective: "connect-src", blockedURI: "ws://localhost:8765/" }`. Its WebSocket errors without reaching the relay. This needs no user credential or authentication bypass.
- Source trace matches the screenshot: `usePrices.ts` maps WebSocket.onerror to `Connection lost`; `IBStatusContext.ts` maps a disconnected browser socket to `relay_offline`. An independent agent confirmed the compiled URL and policy mismatch.
- Runtime checks still show Next and both SSH forwards listening, cloud API/relay services active, API HTTP 200 / IB authenticated / all three pool connections active, and relay HTTP 426. Those checks do not overcome browser CSP.
- Earlier fixture browser tests took the authless middleware branch, which returns before CSP. Public sign-in tests exercised CSP for form assets but did not attempt a relay connection. API health and a rejected unauthenticated handshake were insufficient evidence for a working signed-in handoff.
- Red/green repair: `middleware.ts` admits only the exact `ws://localhost:8765` origin, only when generating CSP for an HTTP loopback request. It does not admit bare `ws:`. App/demo deployed responses remain `connect-src 'self' wss: https:` and relay ticket authentication is unchanged. Two independent reviews agreed that a new local same-origin proxy would be a broader change.
- Regression verification: the new loopback assertion first failed against the original policy; then 23 focused CSP/realtime/ticket tests passed. Full Vitest passed 8,381 tests across 836 files in 200.14s. Typecheck passed; lint reported zero errors and 15 existing warnings; the production build passed all 196 output-trace manifests.
- Final runtime verification: local `/sign-in` is HTTP 200 and carries `connect-src 'self' wss: https: ws://localhost:8765`; current `https://app.radon.run/sign-in` continues to omit the local origin. A Chromium page carrying the fixed local policy opened the tunnel with a single-use relay ticket, received a `status` frame with `ib_connected=true` and `ib_issue=null`, and recorded zero CSP violations.
- The rebuilt normal-auth preview is running at `http://localhost:3000`; its six desktop/mobile anonymous Clerk and admin-perimeter checks pass. API health remains ok, IB authenticated and all three pool connections active. No broker restart, authentication bypass, live order, commit or deployment occurred. Existing browser documents must reload once to receive the new CSP.

## Coverage boundary

The inventory test compares every `app/**/page.tsx` file with `e2e/clear-route-inventory.ts`: **61 source pages, no omissions or duplicates**. Dynamic/catchall routes use explicit concrete paths. Redirects are followed to their final destination, including `/internals → /regime → /regime/cri`.

- 58 regular/holding/setup/kit/redirect source pages run at desktop 1440×1000 and mobile 390×844: 116 page checks plus 1 inventory check.
- Sign-in/sign-up use real anonymous Clerk forms on the canonical localhost origin at both sizes. They do not use the authless test header.
- Admin's server page is operator-gated. Anonymous perimeter tests verify its redirect and absence of the operator console. Separately, the **actual production AdminWorkspace component** is bundled in an isolated browser document with the application's compiled CSS, theme/body/font classes and mocked transport. Its loaded gateway, service/writer tables, reliability, SLO, host metrics and typed confirmation are exercised at both sizes. This is explicitly component coverage, **not authenticated end-to-end admin-route coverage**.
- Six interaction checks cover options symbol entry, measurement tabs, browser history, journal date filters/realized totals, and identity-scoped watchlist sorting/instrument navigation across both sizes.
- Shell/research specs add 360px, 768px, 1024px and 1440px controls, navigation, disclosure, responsive tables and overflow checks. Their final result is recorded by the shell agent/root below.

Every regular page uses browser-bound deterministic fixtures. Known financial surfaces receive populated application fixture contracts; unknown ancillary measurements explicitly return 503/unavailable rather than fabricated success. API mutations never reach a live service. The client-only Clerk SDK fixture supplies a stable client identity for identity-scoped watchlist/profile hooks and does not grant server/operator authorization. No actual order was placed, modified or cancelled by these tests.

The final compiled sweep also intercepts Radon's WebSocket transport using `e2e/clear-realtime-fixtures.ts`. Typed quote/depth/tape frames exercise the real `usePrices` path, not the demo provider. AAPL's $232.18 quote matches the portfolio's per-share mark; the Book page asserts the `SMART DEPTH` feed, subscribed stock subject and $232.18 microprice. Stock depth is never returned for an option subject. No relay connection is opened by the fixture.

## Commands and recorded evidence

Commands below are run from `web/`. The server is coordinated separately; tests reuse it. The authless test token must match the running test server; it is supplied through the environment and is not an application credential.

```sh
rtk proxy env RADON_AUTHLESS_TEST=1 PLAYWRIGHT_PORT=3002 PLAYWRIGHT_BASE_HOST=localhost npx playwright test e2e/clear-all-pages.spec.ts e2e/clear-admin-component.spec.ts e2e/clear-workspace-interactions.spec.ts --config playwright.config.ts --project chromium --output=/tmp/radon-clear-compiled-complete-pages
rtk proxy env PLAYWRIGHT_PORT=3002 PLAYWRIGHT_BASE_HOST=localhost npx playwright test e2e/clear-auth-pages.spec.ts --config playwright.config.ts --project chromium --output=/tmp/radon-clear-compiled-auth-final
```

| Check | Recorded result |
|---|---|
| Initial 61-source inventory + 116 loaded page presentations | 115/117 passed; only 2 `/internals` assertions expected the intermediate redirect. Source-confirmed final target repaired; 2/2 focused rerun passed. |
| Real anonymous Clerk forms + admin perimeter | 6/6 passed before the final Clerk style assertions. |
| Actual isolated AdminWorkspace desktop/mobile | 2/2 passed, including loaded state, typed confirmation disabled until exact unit name, Cancel and zero mutations. |
| Admin/watchlist focused Vitest | 61/61 passed across 5 files. |
| First measured typography regression | CTA provenance and RV-ratio footnote failed at 9px; after token repair 4/4 desktop/mobile passed. |
| GEX marker-label regression | New assertion failed at 8px; after 12px/lane-spacing repair 11/11 contour and 6/6 collision-lane tests passed. |
| Typography/component focused Vitest | 269/269 passed across 18 files, including CTA, RV ratio, BPI, VCG, GRG, ATS/short, flow report, performance, GEX and VIX-COR. |
| Dark theme and 200% reflow (coordinating agent) | 3/3 passed; artifacts `/tmp/radon-clear-dark-reflow-final`. |
| Earlier development/demo page/component/interaction sweep | **125/125 passed** in 1.3 minutes: 117 inventory/regular-page checks, 2 actual admin-component checks, 6 interaction checks. |
| Final compiled non-demo page/component/interaction sweep | **125/125 passed in 58.0s**, with real realtime hooks receiving isolated typed protocol frames; no runtime/hydration failures or document overflow. |
| Final inherited-metadata repairs | **10/10** affected desktop/mobile Book, CTA, Breadth, GEX and GRG checks passed after final scalar/CSS repairs; all pinned financial metadata at least 12px and no document overflow/runtime errors. |
| Final GRG inherited asset labels | **2/2 passed** after the final asset-label CSS floor; both readability reports contain zero sub-12px visible text and zero clipped controls. |
| Final Book/GRG/theme focused Vitest | **29/29 passed** across 6 files, including the red/green microprice metadata regression. |
| Final Clerk appearance rerun after production rebuild | **6/6 passed** with actual Clerk forms, 16px inputs, 44px evergreen/non-gradient button, no card shadow, no runtime/hydration errors and anonymous admin perimeter. |
| Final compiled real anonymous Clerk/perimeter rerun | **6/6 passed in 9.4s** after the final production rebuild, with all appearance and access assertions retained. |
| Final compiled non-demo text treatments | **6/6 passed in 4.2s** across dashboard, flow detail and profile at desktop/mobile after the last CSS rebuild. Explicit 12px assertions cover delayed-feed metadata, Refresh and Sign out; all six readability reports contain zero undersized visible text and zero clipped controls. |
| Extended existing mobile newsfeed layout | **11/11 passed** on the compiled localhost:3002 mobile project. Stale source-pill expectations were reconciled with commits `2c559f6c` and `72aa96a3`; timestamp/bookmark alignment remains within 1px, compact content row stays 24–28px, refresh/star effective targets remain at least 44px, and the existing bounded 32px chip exception is unchanged. No production newsfeed source changed. |

The focused 269-test command was:

```sh
rtk proxy env NODE_ENV=test npx vitest run tests/gex-laplace-contour.test.tsx tests/gex-laplace-marker-lanes.test.ts tests/cta-page.test.ts tests/cta-page-freshness.test.ts tests/cta-share-stale.test.tsx tests/rv-ratio-panel.test.tsx tests/bpi-panel.test.tsx tests/vcg-history-chart.test.tsx tests/vcg-panel-badge.test.tsx tests/gamma-rotation-panel.test.tsx tests/equibles-ats-venue-share.test.tsx tests/equibles-short-crowding.test.tsx tests/flow-report-pending.test.tsx tests/flow-report-one-scan-per-refresh.test.tsx tests/performance-panel-twr-payload.test.tsx tests/vixcor-panel.test.tsx tests/internals-skew-chart.test.ts tests/share-report-path.test.ts
```

## Per-source route inventory

Every row is tested at desktop 1440×1000 and mobile 390×844 with the mode described above.

| Source | Concrete route | Loaded assertion / coverage mode |
|---|---|---|
| `app/page.tsx` | `/` | `[role="slider"][aria-label="Inspect account value history"]` |
| `app/dashboard/page.tsx` | `/dashboard` | `[role="slider"][aria-label="Inspect account value history"]` |
| `app/portfolio/page.tsx` | `/portfolio` | `[data-testid="position-table"], [data-testid="mobile-position-list"]` |
| `app/performance/page.tsx` | `/performance` | `[data-testid="performance-panel"]` |
| `app/orders/page.tsx` | `/orders` | `[data-testid="orders-command-strip"]` |
| `app/scanner/page.tsx` | `/scanner` | `[data-testid="flow-order-link-AAPL"], [data-testid="mobile-scanner-list"]` |
| `app/watchlist/page.tsx` | `/watchlist` | `[data-testid="watchlist-row-AAPL"]` |
| `app/discover/page.tsx` | `/discover` | Redirect → `/scanner?mode=discover`; `[data-testid="discover-order-link-MSFT"]` |
| `app/flow-analysis/page.tsx` | `/flow-analysis` | `[data-testid="mobile-flow-list"], .table-wrap:has(td:text-is("Long Stock"))` |
| `app/flow-analysis/[ticker]/page.tsx` | `/flow-analysis/AAPL` | `[data-testid="ticker-flow-report"]` |
| `app/[ticker]/page.tsx` | `/AAPL?tab=book` | `.book-feed-pill` = `SMART DEPTH`, subscribed stock subject, $232.18 microprice from real protocol frames |
| `app/options/page.tsx` | `/options` | `[data-testid="options-workspace"]` |
| `app/options/net-gex/page.tsx` | `/options/net-gex?symbol=AAPL` | `[data-testid="options-exposure-table-wrap"]` |
| `app/options/rv-ratio/page.tsx` | `/options/rv-ratio?symbol=AAPL` | `[data-testid="rv-ratio-stats"]` |
| `app/options/exposure/page.tsx` | `/options/exposure?symbol=AAPL` | Redirect → `/options/net-gex?symbol=AAPL`; `[data-testid="options-exposure-table-wrap"]` |
| `app/journal/page.tsx` | `/journal` | `[data-testid="journal-trade-count"]` |
| `app/cta/page.tsx` | `/cta` | `[data-testid="vol-targeting-model"]` |
| `app/alerts/page.tsx` | `/alerts` | `.alerts-rule` |
| `app/workflow/page.tsx` | `/workflow` | `.react-flow` |
| `app/preferences/page.tsx` | `/preferences` | `[data-testid="preference-input-RADON_MAX_ORDER_QTY"]`, loaded value `400` |
| `app/profile/page.tsx` | `/profile` | `.profile-field__input`, loaded value `Sample Operator` |
| `app/regime/page.tsx` | `/regime` | Loaded CRI chart/table; selected indicator |
| `app/regime/ats/page.tsx` | `/regime/ats` | Loaded ATS chart/table; selected indicator |
| `app/regime/backtest/page.tsx` | `/regime/backtest` | Loaded BACKTEST chart/table; selected indicator |
| `app/regime/bpi/page.tsx` | `/regime/bpi` | Loaded BPI chart/table; selected indicator |
| `app/regime/breadth/page.tsx` | `/regime/breadth` | Loaded BREADTH chart/table; selected indicator |
| `app/regime/cor/page.tsx` | `/regime/cor` | Loaded COR chart/table; selected indicator |
| `app/regime/cot/page.tsx` | `/regime/cot` | Loaded COT chart/table; selected indicator |
| `app/regime/credit/page.tsx` | `/regime/credit` | Loaded CREDIT chart/table; selected indicator |
| `app/regime/cri/page.tsx` | `/regime/cri` | Loaded CRI chart/table; selected indicator |
| `app/regime/curve/page.tsx` | `/regime/curve` | Loaded CURVE chart/table; selected indicator |
| `app/regime/dispersion/page.tsx` | `/regime/dispersion` | Loaded DISPERSION chart/table; selected indicator |
| `app/regime/divyield/page.tsx` | `/regime/divyield` | Loaded DIVYIELD chart/table; selected indicator |
| `app/regime/gex/page.tsx` | `/regime/gex` | Loaded GEX chart/table; selected indicator |
| `app/regime/grg/page.tsx` | `/regime/grg` | Loaded GRG chart/table; selected indicator |
| `app/regime/hhlev/page.tsx` | `/regime/hhlev` | Loaded HHLEV chart/table; selected indicator |
| `app/regime/hyad/page.tsx` | `/regime/hyad` | Loaded HYAD chart/table; selected indicator |
| `app/regime/iei-hyg/page.tsx` | `/regime/iei-hyg` | Loaded IEI-HYG chart/table; selected indicator |
| `app/regime/iv-spread/page.tsx` | `/regime/iv-spread` | Loaded IV-SPREAD chart/table; selected indicator |
| `app/regime/ivrank/page.tsx` | `/regime/ivrank` | Loaded IVRANK chart/table; selected indicator |
| `app/regime/llm/page.tsx` | `/regime/llm` | Loaded LLM chart/table; selected indicator |
| `app/regime/ma-ratio/page.tsx` | `/regime/ma-ratio` | Loaded MA-RATIO chart/table; selected indicator |
| `app/regime/margin/page.tsx` | `/regime/margin` | Loaded MARGIN chart/table; selected indicator |
| `app/regime/short/page.tsx` | `/regime/short` | Loaded SHORT chart/table; selected indicator |
| `app/regime/skew/page.tsx` | `/regime/skew` | Loaded SKEW chart/table; selected indicator |
| `app/regime/skew2d/page.tsx` | `/regime/skew2d` | Loaded SKEW2D chart/table; selected indicator |
| `app/regime/straddle/page.tsx` | `/regime/straddle` | Loaded STRADDLE chart/table; selected indicator |
| `app/regime/streaks/page.tsx` | `/regime/streaks` | Loaded STREAKS chart/table; selected indicator |
| `app/regime/trin/page.tsx` | `/regime/trin` | Loaded TRIN chart/table; selected indicator |
| `app/regime/vcg/page.tsx` | `/regime/vcg` | Loaded VCG chart/table; selected indicator |
| `app/regime/vixcor/page.tsx` | `/regime/vixcor` | Loaded VIXCOR chart/table; selected indicator |
| `app/regime/vixts/page.tsx` | `/regime/vixts` | Loaded VIXTS chart/table; selected indicator |
| `app/regime/vol-cone/page.tsx` | `/regime/vol-cone` | Redirect → `/scanner?mode=vol-cone`; `[data-testid="vol-cone-chart-section"]` |
| `app/internals/page.tsx` | `/internals` | Redirect → `/regime/cri`; Loaded CRI chart/table; selected indicator |
| `app/setup/page.tsx` | `/setup` | `[data-testid="setup-wizard"]` |
| `app/demo-pending/page.tsx` | `/demo-pending` | Setting up your demo |
| `app/trial-expired/page.tsx` | `/trial-expired` | Your demo has ended |
| `app/kit/page.tsx` | `/kit` | Radon Contributor Kit / Component Spec |
| `app/admin/page.tsx` | `/admin` | Anonymous server redirect + actual AdminWorkspace component harness; no operator session used |
| `app/sign-in/[[...sign-in]]/page.tsx` | `/sign-in` | Real anonymous Clerk form; no authless header |
| `app/sign-up/[[...sign-up]]/page.tsx` | `/sign-up` | Real anonymous Clerk form; no authless header |

## Browser assertions and artifacts

- Loaded page-specific chart, table, form, list, source value or explicit expected holding state. Active risk tab is verified.
- No uncaught page errors or hydration mismatch messages.
- Document horizontal overflow ≤1px; wide data tables remain in local scroll regions.
- Representative financial context is at least 12px, including metric labels, chart metadata, risk strip labels, CTA source inputs, relative-volatility footnotes and journal controls.
- Every successful regular page emits a full-page screenshot and `readability.json` with visible sub-12px text and clipped control coordinates; these are diagnostics in addition to the pinned critical-label assertions. The complete sweep produced **116 readability reports, 118 screenshots and zero clipped controls**.
- Admin component confirmation remains guarded and sends no mutation; auth form tests fill an invalid-domain sample address without submitting.
- Auth style tests additionally require 16px email inputs, a 44px evergreen non-gradient submit button and no card shadow.

Artifacts (machine-local, not deployed):

| Directory | Contents |
|---|---|
| `/tmp/radon-clear-all-pages/` | Initial populated desktop/mobile screenshots and readability per page. |
| `/tmp/radon-clear-risk-mobile/` | 31/31 mobile risk-route populated screenshots from the initial focused pass. |
| `/tmp/radon-clear-admin-component/` | Initial actual admin-component desktop/mobile screenshots. |
| `/tmp/radon-clear-final-pages/` | Earlier post-type pass; subsequent complete sweep supersedes its two failures. |
| `/tmp/radon-clear-complete-pages/` | **125/125 passed**, 116 populated page screenshots/readability reports plus 2 actual admin-component screenshots. |
| `/tmp/radon-clear-compiled-complete-pages/` | **Authoritative compiled non-demo sweep: 125/125 passed**, 116 readability reports, 118 screenshots and zero clipped controls. |
| `/tmp/radon-clear-compiled-auth-final/` | **Final compiled real anonymous forms/perimeter: 6/6 passed** with Clear appearance checks. |
| `/tmp/radon-clear-compiled-type-final/` | **Last compiled typography follow-up: 6/6 passed**, zero undersized visible text and zero clipped controls across all six reports. |
| `/tmp/radon-clear-compiled-book-red/`, `/tmp/radon-clear-compiled-book-green/` | HTTP-only fixture fails to provide depth on non-demo build; isolated real-protocol fixture passes desktop/mobile, 2/2. |
| `/tmp/radon-clear-metadata-final/` | **10/10 passed** after final inherited label repairs, including microprice context; zero clipped controls. |
| `/tmp/radon-clear-grg-final/` | **2/2 passed** after the last inherited GRG asset-label repair; zero undersized visible text or clipped controls. |
| `/tmp/radon-clear-auth-complete/` | Real anonymous sign-in/up desktop/mobile form screenshots before final Clerk skin rebuild. |
| `/tmp/radon-clear-auth-final/` | Final real Clerk forms with Clear appearance and computed-style assertions, 6/6 passed. |
| `/tmp/radon-clear-readability-full.json` | Pre-repair deduplicated typography/clipping findings with concrete classes and routes. |
| `/tmp/radon-clear-font-red/`, `/tmp/radon-clear-font-green/` | Browser 9px red / 12px green evidence. |
| `/tmp/radon-clear-auth-style-red/` | Real Clerk's original 13px input fails the 16px regression before final CSS rebuild. |
| `/tmp/radon-clear-dark-reflow-final/` | Coordinating agent's dark-theme and 200% reflow screenshots, 3/3 passed. |
| `/tmp/radon-clear-newsfeed-final/` | Existing mobile newsfeed suite, 11/11 passed; article/footer screenshot and measured footer geometry. |

## Environmental findings and limits

The installed in-app browser failed initialization with `Cannot redefine property: process`; verification used isolated installed Playwright Chromium, not the user's Chrome profile.

Binding Next solely to 127.0.0.1 caused real anonymous Clerk development middleware to rewrite to canonical `localhost` and fail with 500/socket hang-up. Restarting the coordinated production preview bound to `localhost` restored real anonymous HTTP 200 forms without changing authentication logic. Regular fixture tests and real anonymous auth tests use their appropriate server origins.

An earlier 119-check sweep overlapping source edits finished 117 passed / 2 failed: COT emitted a hydration mismatch (server `meta` versus client workspace `div`), then passed a focused frozen-source rerun. Mobile Orders exposed an 11px freshness label from a more-specific legacy selector. A subsequent 125-check run caught a desktop flow-error label at 11px. These failures were repaired or rechecked and the complete 125-check sweep passed; none was waived.

The compiled diagnostic identified three additional non-demo treatments: desktop `.futures-delayed` at 9px, desktop `.ticker-flow-refresh` at 10px, and mobile `.mobile-drawer__signout` at 11px. All three were repaired to the 12px metadata token and added to the explicit browser font assertions. The six-case compiled follow-up passed with zero undersized visible text or clipped controls; the complete compiled sweep also measured zero clipped controls.

Fixture-page screenshots establish UI and client behavior, not live broker connectivity, upstream vendor availability, or a successful authenticated trading session. Performance/build/full-suite and final localhost handoff results are recorded below by the coordinating agent.

## Final verification

Owned verification is complete: **125/125 compiled non-demo comprehensive browser checks, 6/6 final compiled real anonymous auth/perimeter checks, 6/6 final compiled text-treatment checks, 10/10 affected-page font checks, 2/2 GRG asset-label checks, 11/11 existing mobile newsfeed checks, and 29/29 final focused component tests**. These checks use fixture transport and the admin coverage distinction above.

### Frozen-source release checks

| Check | Final result / evidence |
|---|---|
| Complete web Vitest suite | **8,379 passed, zero failed, 836 files**, 187.18s; `rtk proxy npm test -- --maxWorkers=2`, `/tmp/radon-clear-unit-release.log`. |
| Full mobile Playwright project | **96 passed, one existing desktop-only case skipped**, 1.3m; `/tmp/radon-clear-mobile-release.log`, screenshots and failure-free test artifacts in `/tmp/radon-clear-mobile-release/`. |
| Selected desktop trading/accessibility regressions | **13 passed, one existing midprice fixme skipped**, 19.5s; `/tmp/radon-clear-desktop-final.log`, artifacts `/tmp/radon-clear-desktop-final/`. Includes signed AAOI max loss, unavailable margin, cancel-502 retention, partial fills, combo confirmation, expiry retention, sticky headers, last-price badge, dark theme, reduced motion and 200%-equivalent reflow. |
| Research and account accessibility | 42 focused research assertions and 28 browser cases, plus sticky-table regression; 70 related metric tests and two desktop/mobile keyboard flows. See `../tasks/clear-research-audit.md`. |
| Combo Book quote isolation | Three new unit regressions and nine compiled browser checks preserve signed spread quotes and prohibit an underlying-stock fallback for missing combo quotes. Artifacts `/tmp/radon-clear-book-layout-final/`; final full mobile run includes these checks. |
| Production build and trace gates | `npm run build` passed, including **196 trace manifests**, maximum 2,427 files / 51.39 MiB. `/tmp/radon-clear-build-release.log`. |
| TypeScript | `npm run typecheck` passed after the final build. |
| ESLint | **Zero errors, 15 existing warnings**; `/tmp/radon-clear-lint-complete.log`. |
| Whitespace/conflict-marker check | `git diff --check` passed. |
| Actual normal-auth localhost handoff | **6/6 passed**, 9.7s, on the final application at localhost:3000 without a demo or server authless bypass; `/tmp/radon-clear-localhost-auth.log`, screenshots `/tmp/radon-clear-localhost-auth/`. |

Repeated focused and comprehensive runs are not additive unique-test totals. The mobile skip is a desktop-only cockpit regression excluded by the existing project guard. The separate desktop midprice fallback case was already marked `test.fixme`; the existing last-trade badge assertion passed. No new skip was added to obtain green results.

A preceding full unit run concurrent with build/browser work passed 8,378 tests and hit the default one-second async timeout in the listed-expiry prefill case. Its unchanged isolated file passed 3/3; the final quiescent complete run with two workers passed 8,379/8,379. The timeout was not raised and the assertion was not removed. Earlier fixture/path defects and intentional red/green stages are retained in the task history; they are not outstanding final failures.

Performance diagnostics are in [DESIGN_PERFORMANCE.md](DESIGN_PERFORMANCE.md). At its disclosed compiled measurement checkpoint, first Performance navigation fell from 2,310–2,314ms to 759–781ms (about 66%), initial JavaScript transfer fell about 11.4%, and font transfer fell 12.5%. The loaded account chart remains about 5.2s under the 150ms latency / 1.6Mbps / 4x CPU profile. Native LCP identifies the loading paragraph; these small local samples do not establish production p75 LCP or INP. The final build includes later visual/quote-guard repairs, not a new claimed performance sample.

### Running localhost handoff

The final **non-demo production build** is running at [http://localhost:3000](http://localhost:3000). Use normal sign-in. The final six-browser-check run above used actual anonymous Clerk forms and verified the real `/admin` redirect on desktop and mobile; it did not submit authentication or impersonate an operator.

The frontend process was launched with all demo/authless flags removed:

```sh
rtk proxy env -u RADON_AUTHLESS_TEST -u RADON_AUTHLESS_TEST_TOKEN -u NEXT_PUBLIC_RADON_DEMO \
  RADON_API_URL=http://127.0.0.1:28321 IB_REALTIME_WS_URL=ws://localhost:8765 \
  npm run start -- --hostname localhost --port 3000
```

Its output is `/tmp/radon-clear-localhost.log`. It reuses the existing cloud broker/services via local-only SSH forwards, whose output is `/tmp/radon-clear-cloud-tunnel.log`:

```sh
rtk proxy ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:28321:127.0.0.1:8321 \
  -L localhost:8765:127.0.0.1:8765 ib-gateway
```

Verified handoff responses: sign-in HTTP 200; API health `status=ok`, IB `auth_state=authenticated`, all three reported IB connections active. A plain HTTP request to the realtime relay returns its expected 426 response; a browser-origin WebSocket handshake without a ticket returns 401. Normal ticket validation remains required. API health is connectivity evidence, not a claim that every vendor or authenticated workflow was exercised against live accounts.

The frontend and SSH forwards remain running. Temporary fixture/dev servers are stopped. `scripts/local.sh` was read but not run: it switches broker infrastructure and stops the cloud gateway, which this UI implementation does not require. Existing broker mode, schedulers, remote containers and credentials were not changed. No live order was placed, modified or cancelled. No commit, PR or deployment was performed; unrelated user changes and all selected reference artifacts were preserved. All three implementation/verification agents completed before handoff.
