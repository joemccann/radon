# Radon Clear: production implementation plan

Selected direction: **A / Clear**, 2026-09-05. [Accepted design memory](DESIGN_MEMORY.md). [Full-size reference](.claude-design/a.html).

## Outcome

Apply Clear's approachable portfolio hierarchy to the complete workstation while preserving access to professional research, execution, risk and operations. The first coherent increment is the shared shell plus portfolio overview on desktop and mobile. Follow through to every application surface; a recolored dashboard alone is not completion.

Status: implementation and verification complete; the non-demo compiled application is running at `http://localhost:3000` with normal authentication. All 61 source pages have desktop/mobile coverage, with the operator-component boundary documented in [verification results](DESIGN_VERIFICATION.md). Full web suite: 8,379 passed across 836 files. Execution tracking is in `../tasks/todo.md`; measured performance and its limits are in [performance results](DESIGN_PERFORMANCE.md). No commit or deployment was performed.

## Dependency graph

```text
T1 ──┬── T2 ── T3 ──┬── T5 ──┐
     │               ├── T6 ──┤
     │               ├── T7 ──┼── T9 ── T10
     └── T4 ─────────┘        │
                     └── T8 ──┘
```

- [x] T1 `depends_on: []` — Capture current application baselines and a complete route/capability inventory, then resolve account-history, concentration and stress-data provenance.
- [x] T2 `depends_on: [T1]` — Integrate Clear's semantic colors, typography, surfaces, controls and focus treatments through the existing theme system; reconcile applicable brand documentation and visual contracts.
- [x] T3 `depends_on: [T2]` — Implement the desktop header, primary/secondary navigation and mobile navigation without dropping existing capabilities or remounting realtime ownership.
- [x] T4 `depends_on: [T1]` — Define a truthful overview presentation model using existing account, position and performance sources. Explicitly represent unavailable history and unsupported risk metrics.
- [x] T5 `depends_on: [T3, T4]` — Build the Clear portfolio overview, position preview, period interaction, concentration summary and research handoff on desktop and mobile.
- [x] T6 `depends_on: [T3, T4]` — Apply Clear to research, scanner, watchlist, flow, regime, CTA and ticker views, preserving source freshness and the path from evidence to structure.
- [x] T7 `depends_on: [T3]` — Apply Clear to positions, order lists, option-chain selection and order review using the existing risk and execution components.
- [x] T8 `depends_on: [T3]` — Complete performance, journal, alerts, workflow, preferences, profile, administration, onboarding and failure/empty states using the same visual hierarchy.
- [x] T9 `depends_on: [T5, T6, T7, T8]` — Verify complete user journeys, accessibility, responsive layout, quote rendering and network behavior; repair defects with regressions.
- [x] T10 `depends_on: [T9]` — Run required full suites, typecheck, lint, production compile, visual comparison and performance measurement; record the final implementation and release scope for handoff.

T4 can run alongside T2/T3. Once the shell and presentation model settle, assign T6/T7/T8 to isolated owners with shared component contracts. One owner controls global tokens and shell; independent reviewers verify desktop/mobile flows and performance.

## Existing source integration points

| Area | Existing files | Intended change |
|---|---|---|
| Theme and initial paint | `app/globals.css`, `app/layout.tsx`, `lib/ThemeContext.tsx`, `components/ThemeBootstrap.tsx` | Integrate Clear's visual roles; keep one theme owner, stable SSR hydration, and Next-owned browser theme metadata. |
| Application shell | `components/WorkspaceShell.tsx`, `components/Header.tsx`, `components/Sidebar.tsx`, `lib/data.ts`, `components/CommandPalette.tsx` | Replace the primary navigation composition and preserve the full route catalogue through secondary navigation and search. |
| Mobile shell | `components/mobile/MobileShell.tsx`, `components/mobile/MobileTabBar.tsx`, `lib/useViewport.ts` | Apply Clear's bottom navigation and content order. Audit the current 640px shell boundary against the mockup's 760px breakpoint; coordinate one production behavior instead of competing switches. |
| Overview and portfolio | `components/dashboard/DashboardSurface.tsx`, `components/PortfolioSections.tsx`, `lib/usePortfolio.ts`, `app/dashboard/page.tsx`, `app/portfolio/page.tsx` | Present account value, supported history, account metrics, position preview and risk context. Retain news, signals and catalysts in a discoverable home. |
| Performance data | `lib/performanceData.ts`, `lib/performanceChart.ts`, `app/api/performance/route.ts`, `app/performance/page.tsx` | Reuse verified series and distinguish NAV dollars from the base-100 TWR index. Do not interpret every normalized `series[].equity` as dollar NAV. |
| Price lifetime | `components/Providers.tsx`, `lib/RealtimePricesContext.tsx` | Preserve provider ownership, subscription set-diffing and bounded shrink behavior during navigation. |
| Order risk | `lib/order/risk/index.ts` and existing consumers | Restyle the current `useOrderRisk` / `OrderRiskGate` flow; preserve branded summaries, pending coverage, signed pricing and rejection behavior. |

New components should be introduced only when concrete reuse or clear state ownership warrants them. Do not transplant the prototype's global CSS overrides or shared dialog script into production.

## Proposed capability grouping

Preserve current deep links while refining visible navigation. `lib/data.ts` is the current route catalogue.

| Clear destination | Existing capabilities to retain |
|---|---|
| Portfolio | Overview (`/dashboard`), account history/performance (`/performance`), news and catalysts |
| Positions | Holdings (`/portfolio`), orders (`/orders`), journal (`/journal`) |
| Research | Scanner (`/scanner`), watchlist (`/watchlist`), flow (`/flow-analysis`), options (`/options`), instrument detail; preserve any contextual Discover links |
| Risk | Regime (`/regime/cri` and other regime subroutes), CTA (`/cta`), alerts (`/alerts`) |
| Account / workspace menu | Workflow (`/workflow`), operator (`/admin`), preferences (`/preferences`), profile (`/profile`) |

This grouping must expose the difference between account overview and individual positions through labels and page headings. Do not duplicate inaccessible desktop navigation beneath mobile chrome. Keep contextual research links and browser Back semantics intact.

## Data and risk acceptance gates

1. **Account chart:** inspect the actual available history cadence. The prototype draws invented intraday samples; a daily source cannot serve a 1D intraday chart. Enable only supported periods and display sample timestamps, units and unavailable states. Separate account value, cash-flow-adjusted return and daily P&L.
2. **Concentration:** define the exposure denominator and aggregation for stocks, signed option delta and spreads. Reuse existing canonical option-delta normalization. The example 35% watch threshold is not automatically a real account setting.
3. **Stress:** verify an actual position-repricing model and data contract before displaying a dollar loss. The prototype's linear slider is not a risk engine. If no supported model exists, keep the module unavailable with an explanation or scope a separate modeled-risk task; never fabricate a convincing number.
4. **Order review:** `portfolio === undefined` is pending coverage; missing coverage blocks submission. Preserve defined-risk max loss, limit-priced payoff, manual-price invalidation and broker error propagation. Synthetic prototype values and acknowledgement logic never replace this path.
5. **Freshness:** preserve explicit provider/source and session-relative freshness. Daily OI remains prior-close data; a green live-price indicator must not imply live OI or refreshed analytics.

Independent source review confirmed the existing performance flow is `lib/usePerformance.ts` → `app/api/performance/route.ts` → normalized IB Flex NAV/session history and gated TWR. `PortfolioData.risk_budget` exists but must not be reinterpreted as sector concentration without verifying its semantics. Mobile secondary navigation currently derives from the shared route catalogue; preserve this completeness when replacing its presentation.

## Required states and flows

- First account visit, existing session navigation and browser Back; all primary and secondary destinations remain reachable.
- Loading with reserved geometry; empty account; stale/failed/disconnected feeds; recovery that keeps the last valid snapshot; closed market and missing historical series.
- Positive and negative P&L, large dollar values, long structure labels, mixed stocks/options, partially priced positions and unavailable verified risk capital.
- Search with keyboard launch, no results, selection, Escape and focus restoration.
- Position → ticker research → chain → structure → risk review → confirmation; order cancellation/modification/rejection must keep existing execution behavior and test guards.
- Mobile overflow, safe-area navigation, bottom-sheet height, coarse-pointer tablet controls, 200% zoom and reduced motion. Do not obscure risk disclosures behind persistent action bars.
- A single selected chart period; line, fill, period result and axis data update together. Table sort/column preferences and list filters work across all positions.

## Verification and performance

Create focused Vitest regressions and relevant Playwright coverage for changed behavior. Retain the existing `tests/realtime-socket-ownership-contract.test.ts`, `tests/realtime-prices-navigation-persistence.test.tsx`, `tests/order-tab-risk-gate.test.tsx`, `tests/position-trade-ticket-risk-gate.test.tsx`, and performance math/axis contracts. Use authless/mock brokerage test execution and visual screenshots; never click live order submission during design verification.

Compare at 360, 390, 768, 1024 and 1440px, with touch capability where applicable. Target 44px touch controls, 4.5:1 normal text contrast, visible keyboard focus and full disclosure/confirmation flows. Use the winning screenshots to judge structure and emphasis, not only token similarity.

Measure the real app before and after: route navigation, number of quote sockets/tickets, repeated subscriptions, initial requests/bytes, long tasks, chart rendering and quote-update frame cost. Preserve demand-loaded heavy views and bound offscreen work. Target mobile p75 LCP ≤2.5s, INP ≤200ms and CLS ≤0.1 as implementation acceptance budgets; prototype FCP does not prove these targets. Use representative portfolio size and quote traffic, not only an empty account.

Before a code-bearing commit, run the full relevant project suites, `npm run typecheck`, `npm run lint`, and `npm run build` from the documented project context. Report baseline failures separately from new failures. The comparison's prior full web run had 8,321 passed, one `iv-spread-api` failure and one `rel148` collection failure; reproduce current state rather than treating those historical results as present verification.

## Handoff and reference lifecycle

This plan and design memory are persistent source documents. Keep the selected visual reference available during the implementation handoff. Before closing and removing the temporary design lab, export the accepted reference and needed evidence to their final project home and update links. Do not delete the only copy of the approved design or disturb unrelated user files.

Implementation is complete only after Clear's hierarchy works across the mapped application surfaces and the required checks pass. Publishing/merging/deploying follows the repository workflow when that release work is in scope.
