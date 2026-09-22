# Toast-only error audit

## Scope and baseline

The application audit covers `web/app`, `web/components`, and React presenters under `web/lib`. API/server failures continue to return their original HTTP statuses and structured errors; the presentation boundary selects safe copy. Marketing pages, backend logs, and persisted diagnostic datasets are not application notification surfaces.

Recent history reviewed: `8fb142ac` (PR #480, safe error messages and retained scanner results), including its original `97a1912f` and follow-up `0030e206`. That change standardized error wording, but `RequestError`, `PanelRefreshError`, and `OrderErrorBanner` still rendered inline.

## Changes

- One body-level toast viewport stacks legacy notifications and request errors together, above page and dialog surfaces.
- Request, refresh, broker, field validation, scanner, research, account, settings, administration and assistant failures use persistent dismissible toasts.
- Retry actions and retained-snapshot guidance travel with the toast. Dismissing a notification does not clear the underlying error or enable an invalid order.
- The Vol/Skew MR failed-scan path preserves loaded rows and retries the same explicit ticker request.
- Safe service-error and broker-error formatting remain the presentation boundaries.

## Explicit non-toast cases

| Surface | Remaining content | Reason |
| --- | --- | --- |
| `web/app/error.tsx` | Fatal route fallback, reset/dashboard actions and digest | The route cannot render; recovery must remain available without shared providers. This file is constrained to pure JSX by the Next build contract. |
| `web/app/global-error.tsx` | Fatal root fallback and reload instruction | The root layout and notification system may have failed. Pure HTML fallback is intentionally independent of React effects, context, and global CSS. |
| `web/app/[ticker]/not-found.tsx` and route not-found pages | Navigation/not-found explanation | A route outcome, not a request-error notification. |
| Order risk gate, `OrderConfirmSummary`, ticket risk warnings | Undefined risk, missing verified capital, coverage and current-fill conditions | Persistent financial decision inputs and submit guards; dismissing a toast must never hide these requirements. |
| Flow/CTA/scanner data-quality indicators | Snapshot age, missing history, insufficient provider coverage and failed signal gates | Evidence about displayed measurements remains attached to the data. Actual request failures move to toasts. |
| Admin health/writer tables and assistant tool evidence | Stored service status, prior failures, tool execution records | Diagnostic records are the content being inspected, not transient notification messages. |
| Third-party Clerk authentication UI | Provider-owned form validation | Radon does not rewrite Clerk's internal error renderer. |

These exceptions are reported explicitly; the change does not claim that every occurrence of the word “error” disappears from the application.

## Verification

No local test suites run. Regression tests and the curated Playwright browser flow execute on GitHub runners. Exact commit, CI results and screenshot review are recorded in the accompanying visual report and PR.

### Specific retained diagnostic surfaces

- `web/components/admin/RestartLog.tsx`: operator action history, including previous unsuccessful actions; current failures additionally toast.
- `web/components/admin/WriterFreshnessTable.tsx`: persisted writer `last_error` values; request failures toast.
- `web/components/equibles-ats-venue-share/AtsVenueSharePanel.tsx`: per-ticker collection coverage evidence in the returned dataset.
- `web/components/research/ResearchReliability.tsx`: golden-query pass/fail results and stored error classifications.
- `web/components/flow-analysis/TickerFlowReport.tsx`, `CtaPage.tsx`, and indicator panels: age/coverage labels remain, request failure details toast.
- `web/components/ticker-detail/OptionsChainTab.tsx`: unavailable prefilled contract is a guard against placing a different contract.
- `web/components/OfflineBanner.tsx`: cached-data provenance while offline, not a request error.

### Review workflow

T1 history/isolation → T2 shared presenters, T3 research/scanners, T4 trading/settings in parallel → T5 independent audit and regression review → T6 exact-head GitHub checks and visual report. Three agents independently owned non-overlapping component groups; the primary agent reviewed integration, order field controls, CI/browser evidence and explicit exceptions.
