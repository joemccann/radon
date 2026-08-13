# Web HIGH_BUG/BUG remediation status

## Reconciliation

- Assigned findings: **68**
- Fixed: **67**
- Duplicate: **1** (`HB-075` -> `HB-107`)
- Evidence-backed non-actionable: **0**
- Remaining actionable: **0**

## HIGH_BUG findings

| ID | Disposition | Current-code evidence | Exact regression |
|---|---|---|---|
| HB-062 | fixed | `web/lib/ctaPercentiles.ts` preserves integer percentile `1`; `CtaBriefing.tsx` uses the shared normalizer. | `web/tests/cta-page.test.ts` - exact first percentile remains `1`. |
| HB-063 | fixed | `web/components/CtaPage.tsx` uses the same contract-aware percentile normalizer for callouts. | `web/tests/cta-page.test.ts` - exact first percentile selects the low-percentile contract. |
| HB-064 | fixed | `web/components/CtaPage.tsx` calls `useRegime(false)`, so mounting CTA is cache-only. | `web/tests/cta-page.test.ts` - cache-only regime read contract. |
| HB-071 | fixed | `web/lib/regimeLiveStrip.ts::resolveCrashTriggerState` selects one live-or-cached COR1M value; `RegimePanel.tsx` uses it for display and trigger. | `web/tests/regime-market-closed-values.test.ts` - active live COR1M updates the crash trigger. |
| HB-075 | duplicate of HB-107 | `web/components/WorkspaceSections.tsx` only exposes the actionable correlation produced by the hardened `openOrderCombos` grouping. | `web/tests/open-order-combos.test.ts` - uncorrelated same-shape orders remain non-actionable singles. |
| HB-076 | fixed | `web/components/mobile/MobileChainLadder.tsx` labels `avgVolume` as `AVG VOL`, never OI. | `web/tests/mobile-chain-leg-tint-expiry-scope.test.tsx` - average volume is not labeled open interest. |
| HB-079 | fixed | `web/components/mobile/MobilePositionList.tsx` requires every active option leg before publishing complete P&L. | `web/tests/mobile-position-short-pnl.test.tsx` - missing active leg suppresses complete P&L. |
| HB-092 | fixed | `web/components/ticker-detail/PositionTab.tsx` multiplies signed spread marks by canonical `heldComboUnits(position)`. | `web/tests/position-tab-trade.test.tsx` - ratio-spread mark uses combo units once. |
| HB-099 | fixed | `web/lib/blotter/fromJournal.ts` classifies generic stock BUY/SELL against running signed inventory and attaches closure to the reducing execution. | `web/tests/blotter-from-journal.test.ts` - SELL opens short; later BUY closes the cover. |
| HB-102 | fixed | `web/lib/journal/realizedPnl.ts` treats BUY assignment/exercise from flat as acquired inventory, not fallback closure. | `web/tests/journal-realized-pnl.test.ts` - flat BUY assignment/exercise opens stock. |
| HB-103 | fixed | `web/lib/journal/realizedPnl.ts` synthesizes lapse only with explicit worthless-expiry proof. | `web/tests/journal-realized-pnl.test.ts` - unmatched expired option stays unresolved without proof. |
| HB-104 | fixed | `web/lib/journalDb.ts` uses conflict `DO NOTHING` by default and permits correction updates only through the explicit correction path. | `web/tests/journal-db-concurrency.test.ts` - stale reconciliation cannot overwrite a richer concurrent row. |
| HB-105 | fixed | `web/lib/journalImport.ts` normalizes execution correction roots and fails partial composite overlap closed for rehydration. | `web/tests/journal-sync.test.ts` - corrections supersede stable identity; partial composites require rehydration. |
| HB-106 | fixed | `web/lib/journalImport.ts` refuses zero-net CLOSED imports that cannot preserve gross quantity/notional and marks them for rehydration. | `web/tests/journal-sync.test.ts` - lossy net-flat round trip is rejected for authoritative rehydration. |
| HB-107 | fixed | `web/lib/openOrderCombos.ts` requires stable broker correlation before orders become one actionable combo. | `web/tests/open-order-combos.test.ts` - uncorrelated same-shape orders remain non-actionable singles. |
| HB-108 | fixed | `web/lib/optionsChainUtils.ts` preserves signed natural combo bid/ask/mid without absolute-value normalization. | `web/tests/options-chain-utils.test.ts` - debit, credit, and zero-crossing quotes retain sign. |
| HB-117 | fixed | `web/lib/performanceChart.ts` validates/derives finite normalized TWR geometry when optional legacy fields are absent; panel callers accept optional starting equity. | `web/tests/performance-chart-model.test.ts` - normalized TWR payload produces finite geometry; `npm run typecheck`. |
| HB-118 | fixed | `web/lib/portfolio/stockBasisDb.ts` returns ordered account-scoped history; `web/lib/journal/realizedPnl.ts` selects the snapshot at/before delivery. | `web/tests/journal-realized-pnl.test.ts` - assignment uses event-time basis, not latest. |
| HB-119 | fixed | `web/lib/positionUtils.ts::resolveMarketValue` returns unavailable when any economically active leg is missing. | `web/tests/security-remediation-web.test.ts` - partial multi-leg MV is never published. |
| HB-120 | fixed | `web/lib/useInformedFlow.ts` aborts/versions loads and commits only the active ticker. | `web/tests/security-remediation-hooks.test.tsx` - older ticker response is ignored. |
| HB-121 | fixed | `web/lib/usePreviousClose.ts` keys fetched values by expected ET session, clears on rollover, and reruns the request effect for the new session. | `web/tests/security-remediation-hooks.test.tsx` - ET-session rollover invalidates and refetches backfilled close. |
| HB-122 | fixed | `web/lib/useTickerFlowReport.ts` clears ticker-scoped data immediately and generation-guards commits. | `web/tests/security-remediation-hooks.test.tsx` - previous report is hidden and cannot be restored after ticker switch. |

## BUG findings

| ID | Disposition | Current-code evidence | Exact regression |
|---|---|---|---|
| BUG-065 | fixed | `web/components/BpiPanel.tsx` renders the hook failure before the successful no-scan state. | `web/tests/bpi-panel.test.tsx` - upstream failure is a measurement fault. |
| BUG-066 | fixed | `web/components/CorPanel.tsx` carries nullable samples into `BrushMinimap`; the minimap splits paths at gaps. | `web/tests/cor-panel.test.tsx` - missing tenor creates a path break. |
| BUG-067 | fixed | `web/components/CriHistoryChart.tsx` memoizes merged history/live input; tooltip state is excluded from draw identity. | `web/tests/cri-history-chart-axis.test.ts` - tooltip motion does not rebuild SVG data. |
| BUG-068 | fixed | `web/components/DashboardNewsFeed.tsx` uses `filteredPosts` for counts and maps navigable lightbox targets back to their filtered index. | `web/tests/dashboard-newsfeed-pagination.test.tsx` - filtered total and cross-page lightbox navigation share one collection. |
| BUG-070 | fixed | `web/lib/exposureBreakdown.ts` retains mixed/approx provenance and the modal warns whenever approximation contributes. | `web/tests/exposure-breakdown-modal-leverage.test.tsx` - mixed delta provenance shows APPROX. |
| BUG-071 | fixed | `web/components/FillsModal.tsx` formats execution time in `America/New_York`. | `web/tests/security-remediation-components.test.tsx` - execution timestamp renders in ET. |
| BUG-072 | fixed | `web/components/FuturesStrip.tsx` accepts per-quote delay provenance; `WorkspaceShell.tsx` sets it per symbol. | `web/tests/futures-strip.test.tsx` - partial outage labels each delayed fallback cell. |
| BUG-073 | fixed | `web/components/GammaRotationPanel.tsx` emits contiguous finite gamma path segments only. | `web/tests/gamma-rotation-panel.test.tsx` - null observation breaks the path. |
| BUG-074 | fixed | `web/components/GammaRotationPanel.tsx` derives labels from the expanded z-score domain. | `web/tests/gamma-rotation-panel.test.tsx` - expanded domain renders its actual bound. |
| BUG-075 | fixed | `web/components/MetricCards.tsx` computes effective Day P&L once and passes the same resolved value/source to card and modal. | `web/tests/day-pnl-premarket-fallback.test.tsx` - modal matches the displayed fallback. |
| BUG-077 | fixed | `web/components/OptionsExposurePanel.tsx` resets incompatible expirations on normalized-symbol change. | `web/tests/options-exposure-panel.test.tsx` - ticker rerender resets expiration selection. |
| BUG-078 | fixed | `web/lib/usePriceHistory.ts` accepts finite signed `spread-net` last/mid and represents unavailable as null; `PriceChart.tsx` renders NET UNAVAILABLE honestly. | `web/tests/price-chart-spread.test.tsx` - negative unbooked spread last remains NET CREDIT. |
| BUG-079 | fixed | `web/components/RegimeRelationshipView.tsx` reanchors active presets when session count grows while preserving custom brushes. | `web/tests/regime-relationship-zoom.test.tsx` - active preset follows appended sessions. |
| BUG-081 | fixed | `web/components/TickerSearch.tsx` matches returned pattern to the latest normalized query before committing results. | `web/tests/ticker-search-filter.test.ts` - out-of-order older query is ignored. |
| BUG-082 | fixed | `web/components/WorkspaceSections.tsx::groupExecutedOrders` prefers orderRef/permId/orderId and uses true <=60-second proximity only as fallback. | `web/tests/workspace-orders-implied.test.tsx` - grouping uses durable identity, not calendar minute. |
| BUG-083 | fixed | `groupExecutedOrders` quantity-weights complete BAG fills and returns unavailable for incomplete aggregates. | `web/tests/workspace-orders-implied.test.tsx` - multi-fill BAG weighted price and incomplete rejection. |
| BUG-086 | fixed | `web/components/admin/SystemStatusBar.tsx` renders null freshness as unknown, not just now. | `web/tests/security-remediation-components.test.tsx` - missing freshness is unknown. |
| BUG-087 | fixed | `web/components/dashboard/ScannerHero.tsx` renders active-hook error before successful empty results. | `web/tests/scanner-hero.test.tsx` - scanner failure differs from zero candidates. |
| BUG-088 | fixed | `web/components/mobile/BottomSheet.tsx` captures/releases the pointer on the drag handle that owns move/up handlers. | `web/tests/mobile-bottom-sheet.test.tsx` - captured drag dismisses and releases state. |
| BUG-089 | fixed | `web/components/mobile/MobileJournalList.tsx` resolves `total_cost` before legacy `entry_cost`. | `web/tests/security-remediation-components.test.tsx` - mobile journal uses canonical total cost. |
| BUG-092 | fixed | `web/components/ticker-detail/NewsTab.tsx` aborts/guards ticker-scoped requests and clears old data on switch. | `web/tests/security-remediation-components.test.tsx` - stale old-ticker news cannot commit. |
| BUG-093 | fixed | `web/components/ticker-detail/SeasonalityTab.tsx` resets/guards ticker-scoped request state. | `web/tests/security-remediation-components.test.tsx` - seasonality remains ticker-scoped. |
| BUG-094 | fixed | `SeasonalityTab.tsx` excludes zero-observation padded months from favorable/unfavorable rating counts. | `web/tests/security-remediation-components.test.tsx` - unobserved months do not become unfavorable. |
| BUG-096 | fixed | `web/lib/blotter/fromJournal.ts::dedupeJournalRows` ranks explicit accounting authority first, then written time and monotonic payload ID. | `web/tests/blotter-from-journal.test.ts` - authoritative duplicate wins deterministically. |
| BUG-097 | fixed | `web/lib/dbCache.ts` generation-gates joins and writes after invalidation. | `web/tests/db-cache.test.ts` - older in-flight read cannot repopulate invalidated cache. |
| BUG-098 | fixed | `web/lib/dbFirstRead.ts` rejects source timestamps beyond the explicit skew allowance before selection/freshness. | `web/tests/db-first-read.test.ts` - implausibly future DB/disk sources are rejected. |
| BUG-100 | fixed | `web/lib/entryDates.ts` normalizes durable execution roots and deduplicates before the signed episode walk. | `web/tests/entry-dates.test.ts` - duplicate open cannot hide the close/reopen boundary. |
| BUG-101 | fixed | `web/lib/fillToasts.ts` validates finite numeric price before formatting and safely processes malformed fills. | `web/tests/fill-toasts.test.ts` - malformed price neither throws nor repeats. |
| BUG-102 | fixed | `web/lib/fillToasts.ts` retains the newest execution keys when capping persistence. | `web/tests/fill-toasts.test.ts` - newest-first feed persists newest 300. |
| BUG-103 | fixed | `web/lib/futuresSession.ts` applies official 2026 CME equity-index full closures/early closes and exposes session-aware quote freshness; `WorkspaceShell.tsx` suppresses stale futures last values. | `web/tests/futures-session.test.ts` - holiday/early close and stale active-session quote coverage. |
| BUG-104 | fixed | `web/lib/holdTime.ts` validates date ranges and round-trips date components. | `web/tests/security-remediation-web.test.ts` - impossible date-only input is rejected. |
| BUG-105 | fixed | `web/lib/journal/rangePnl.ts` requires nonzero P&L or explicit close evidence; zero alone is not closure. | `web/tests/journal-range-pnl.test.ts` - zero-P&L open row remains open. |
| BUG-106 | fixed | `web/lib/journal/realizedPnl.ts` clamps expiry synthesis to current ET day. | `web/tests/journal-realized-pnl.test.ts` - future report end cannot synthesize future expiry. |
| BUG-107 | fixed | `web/lib/journalImport.ts` calculates sells as gross less commission. | `web/tests/security-remediation-web.test.ts` - imported SELL total is net proceeds. |
| BUG-109 | fixed | `web/lib/optionsExposure.ts` validates nested expirations, levels, cells, indexes, units, and aligned lengths before transformation. | `web/tests/options-exposure-transform.test.ts` - malformed nested provider payload is rejected. |
| BUG-118 | fixed | `web/lib/portfolio/stockBasisDb.ts` keys unambiguous history by account+ticker and refuses a ticker alias across accounts. | `web/tests/stock-basis-db.test.ts` - multi-account basis is scoped and not last-write-wins. |
| BUG-120 | fixed | `web/lib/probeFreshness.ts` permits only bounded future skew; larger negative ages are unproven/stale. | `web/tests/security-remediation-web.test.ts` - future relay tick is not fresh. |
| BUG-121 | fixed | `web/lib/quoteTelemetry.ts` requires a recent timestamp/book for LAST provenance and otherwise uses CLOSE. | `web/tests/quote-telemetry-fallback.test.ts` - prior-session last is labeled CLOSE. |
| BUG-122 | fixed | `web/lib/serviceHealthWindows.ts` applies open-bell grace to RTH-only writers and extended 3-day windows to CRI/VCG schedules. | `web/tests/service-health-windows.test.ts` - premarket/Monday open transitions do not false-stale. |
| BUG-123 | fixed | `web/lib/useAlerts.ts` aborts/versions overlapping loads so older refreshes cannot commit. | `web/tests/security-remediation-shared-hooks.test.tsx` - older refresh cannot restore pre-mutation alerts. |
| BUG-124 | fixed | `web/lib/useBookmarks.ts` serializes mutations, rolls back only the affected ID, and forces a fresh post-mutation read. | `web/tests/security-remediation-shared-hooks.test.tsx` - failed mutation retains concurrent success. |
| BUG-125 | fixed | `useBookmarks.ts` marks loaded only after success and exposes retryable failure state. | `web/tests/security-remediation-shared-hooks.test.tsx` - initial failure remains retryable/not loaded. |
| BUG-126 | fixed | `web/lib/useColumnVisibility.ts` uses defaults for SSR/initial client render and hydrates storage after mount. | `web/tests/security-remediation-hooks.test.tsx` - persisted visibility hydrates after matching initial markup. |
| BUG-127 | fixed | `web/lib/useIndexQuoteFallback.ts` refreshes missing index fallbacks on a bounded interval and expires stale entries. | `web/tests/security-remediation-hooks.test.tsx` - stable missing-symbol set refreshes fallback quote. |
| BUG-128 | fixed | `web/lib/usePreviousClose.ts` rejects non-OK responses and removes missing/invalid symbols for bounded retry. | `web/tests/security-remediation-hooks.test.tsx` - HTTP failure and partial response remain retryable. |
| BUG-130 | fixed | `web/lib/yahooQuote.ts` aggregates intraday candles using first open, max high, min low, summed volume, latest close. | `web/tests/security-remediation-web.test.ts` - intraday candles aggregate to session OHLCV. |

## Dependencies and overlaps

- `HB-075` is resolved only through primary `HB-107`; heuristic groups remain display-only/non-actionable.
- `BUG-096` deterministic authority selection precedes `HB-099` inventory-direction classification.
- `BUG-118` account identity and `HB-118` event-time snapshot selection share the same historical stock-basis model.
- `BUG-078` signed spread consumption preserves the signed quote invariant established by `HB-108`.

## Verification

- Combined all-owned focused Vitest tranche: **41 files, 590 tests passed**.
- TypeScript: `npm run typecheck` passed.
- Patch hygiene: `git diff --check` passed.
- Playwright smoke: `regime-history-tooltip.spec.ts` passed; three existing data-route scenarios remained at `Awaiting first sample` under the shared security-route changes and never reached the edited UI (`account-day-move-ib-daily-pnl.spec.ts` x2, `regime-cor1m-live-stream.spec.ts` x1).
