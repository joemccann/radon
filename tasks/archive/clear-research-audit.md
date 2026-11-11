# Clear research and instrument audit

2026-09-05. Scoped independent review for implementation T6. Parent tracking: `tasks/todo.md`; approved direction: `web/DESIGN_PLAN.md`.

## Dependency graph

- [x] T1 depends_on: [] — Inspect populated scanner, exposure, CRI, curve, positions, chain and book at 360, 390, 768 and 1440px.
- [x] T2 depends_on: [T1] — Reproduce concrete failures with unit and browser regressions.
- [x] T3 depends_on: [T2] — Repair responsive presentation and held-stock chain initialization without replacing risk or price ownership.
- [x] T4 depends_on: [T3] — Verify real pointer/keyboard controls, source numbers, desktop/mobile sticky behavior, and rendered screenshots.
- [x] T5 depends_on: [T4] — Repair the remaining account-card keyboard semantics and verify account/risk drill-downs without visual or calculation changes.

## Findings and disposition

| Severity | Evidence | Disposition |
|---|---|---|
| P1 | `/AAPL?deck=c` threw `null.trim` for a held-stock snapshot with `expiry: null`. | Normalize only string expiries in `OptionsChainTab`; regression exercises the wire boundary. |
| P1 | After the crash was removed, the requested-position guard waited indefinitely for a stock expiry; actual strike rows stayed empty. | Wait only while the requested position itself is missing. Regression asserts populated strike rows, not a native select's automatic first-option display. |
| P1 | Mobile Trade button right edge was 414px at a 390px viewport. Nine 44px targets could not fit. | Four primary tools plus More; every secondary deck remains available. Escape restores More focus without closing the working deck. Desktop shortcuts remain unchanged. |
| P1 | All 21 exposure values were ellipsized at 360px. | Readable financial columns scroll inside a keyboard-focusable region below 700px container width, with pinned strike/column headers and an explanatory scroll hint. Desktop page-sticky behavior remains intact. |
| P2 | Mobile account summary consumed nearly the first screen before any position context. | Two-column account/day summaries with preserved values, disclosure state and calculation sources. |
| P2 | Risk/source and options annotations used 8–11px text; scanner status badges consumed full-width rows. | Scoped minimum-12px metadata, calmer risk hierarchy, compact noninteractive scanner badges, 44px options controls. Parent shared font-floor pass covers remaining legacy chain/company labels. |
| P2 | Existing exposure sticky test queried both column and strike row headers and ignored container padding. | Test now checks only `thead th` against the padded scroll edge. Real column headers were correctly pinned; no sticky gate was removed. |
| P2 | Account cards and collapsible metric groups used click-only divs. | Added button role, tab order, named actions, value/source descriptions, expanded state, and Enter/Space handling through existing click callbacks. Static/loading cards remain noninteractive. No nested interactive controls were introduced. |

## Verification

- Focused Vitest: 42 passed across seven files (`clear-glyph-navigation`, `asset-cockpit-render`, `options-exposure-panel`, `options-workspace-tabs`, `ticker-chain-position-focus`, `chain-prefill-expiry-contract`, `workspace-chrome-alignment`).
- Populated browser suite: 28 passed across seven surfaces and four widths. Options controls exercise metric/range/expiry/level changes and internal sticky scrolling. Mobile book exercises Trade, every More entry, Escape focus, and Company navigation with actual pointer events.
- Existing desktop exposure controls/sticky E2E: 1 passed after correcting its semantic-header assertion.
- TypeScript check: exit 0. Scoped ESLint: 0 errors; three existing hook-dependency warnings in `RegimePanel` and `OptionsChainTab`. `git diff --check`: exit 0.
- All API calls in the Clear browser suite are intercepted by `installClearFixtures`. No place/cancel/modify request was sent; no broker service was started or changed.
- Screenshot set: `/tmp/radon-clear-research-final-green/`. Inspected populated mobile chain/book/exposure/positions and desktop/mobile risk. Final font-only refinement is also covered by the parent all-route sweep.

## Scoped quality assessment

Reviewer scores, not automated accessibility certification: responsive layout 9/10; readability 8/10; instrument navigation 9/10; hierarchy 8/10. Confidence high for tested geometry, actions and source-value preservation. Global contrast, full-suite completion and production performance measurements belong to the parent verification pass.

## Account accessibility follow-up

- Parent-authorized final repair modifies only `web/components/MetricCards.tsx` plus dedicated unit/browser tests.
- New accessibility tests failed before implementation (7/7); final expanded suite has eight focused assertions covering Enter, Space, repeat/child-event guards, accessible value/source description, passive placeholders, pointer behavior and the closed-market Realized branch.
- Eight related account/P&L suites: 70 passed. Typecheck and scoped lint: exit 0; no warnings. Browser keyboard/pointer checks at 390 and 1440px: 2 passed, including focus visibility, dialog Escape/focus restoration and source attribution.
- Visual screenshots inspected at `/tmp/radon-clear-metric-a11y/`; existing card appearance preserved. The remaining accessibility issue above is now closed.
- No order/risk logic changed and no broker mutation occurred.

## Populated mobile ledger follow-up

- Historical card fixtures now assert the unchanged `MobileBlotterList` contract: quantity 5, realized P&L +$250.00, return +16.7%, commission $1.00. The removed Cost/Proceeds labels were absent in both the working source and `HEAD`; no financial component was changed to satisfy stale assertions.
- Executed-order fixtures pin the browser clock to their ET session. Journal tests verify MTD excludes an April close before selecting All and sorting P&L with real pointer actions. Empty cash-flow fixtures conform to `CashFlowResponse`, removing synthetic $NaN output. Unknown API calls receive an isolated 503; no request reaches a broker service.
- The three corrected legacy specs passed 7/7 against the compiled non-demo server. Screenshot review then found a real historical-toolbar overflow: 74px at a 360px viewport and 41px at 393px, captured by two new failing regression cases.
- Appended presentation rules in `web/app/clear.css` are limited to mobile `#orders-historical` and `#orders-cash`. Historical tools wrap with a full-width search field; cash totals use two columns with complete freshness and filter controls. Controls retain 44px targets, inputs 16px, and financial annotations at least 12px. Native select sizing required an explicit 44px height in addition to min-height.
- Updated blotter suite passed 4/4 in dev, including both overflow regressions, search/clear, Refresh, every cash-type option and all three historical page sizes, transaction counts, negative withdrawal sign and unchanged net cash total. Native controls are opened with real pointer events; every selected cash label is checked for text fit. The filter is full-width so even Withholding Tax remains readable at 360px, and historical page-size controls retain an explicit 44px height. Theme/radius/typography contracts passed 8/8; typecheck and scoped lint exited 0. Final compiled rerun remains coordinated by the parent.
- Inspected final screenshots: `/tmp/radon-clear-ledger-all-states/` (including both longest-label states) and `/tmp/radon-clear-mobile-ledger/`. No financial calculations, data hooks, authentication, order payloads or risk gates changed.
