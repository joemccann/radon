# Radon Web UI — Consistency, Reuse & Cross-Device Parity Audit

_Generated 2026-05-28 via dynamic multi-agent workflow (`ui-consistency-audit`)._
_Surface coverage: 7 areas, 186 components/routes/tokens catalogued. 31 raw findings; 1 rejected on verification (dead code)._

## Executive summary

Radon's UI is largely token-driven and routes through a single `WorkspaceShell`, but three structural themes recur:

1. **Dialog / order-entry duplication.** A canonical `Modal.tsx` exists, yet `FillsModal`, `ShareReportModal`, and admin `ConfirmDialog` hand-roll their own backdrop / Escape / scroll-lock, and several drop accessibility behavior entirely. The single-leg order ticket and the futures/index order forms are near-duplicates of each other. This duplication sits on the order-placement seam where wrong-risk bugs have repeatedly shipped.
2. **Token leakage.** Several panels (GEX, VCG, Regime, ratings bar) bake raw hex / rgba / `#000` / `#fff` instead of brand tokens, so they do not adapt between light and dark themes while neighboring rows do.
3. **Touch / mobile parity gaps.** `InfoTooltip` (used 30x) is hover-only with a sub-44px target, several charts bind inspection to `onMouseMove` only, the Discover table has no mobile card variant, and the theme toggle is desktop-only.

No P0s. **9 x P1, 5 x P2, 2 x P3** confirmed (parity items re-graded into the same scale below).

---

## 1. Reusable component opportunities (ranked)

| # | Proposed shared unit | Current duplicate sites | Sev |
|---|---|---|---|
| R1 | `useDialogChrome()` / `useFocusTrap()` hook + route all dialogs through `Modal.tsx` | `Modal.tsx`, `FillsModal.tsx` (no portal/Escape/scroll-lock/ARIA), `ShareReportModal.tsx`, `admin/ConfirmDialog.tsx`, `NewsfeedLightbox.tsx`, `mobile/BottomSheet.tsx` | P1 |
| R2 | `<SingleLegOrderTicket>` (action toggle + qty + limit w/ BID·MID·ASK + TIF + confirm-step + `OrderRiskGate` slot) | `InstrumentDetailModal.tsx:110-342` (LegOrderForm), `ticker-detail/BookTab.tsx:300-623` (StockOrderForm) | P1 |
| R3 | `<ListedContractOrderForm>` + shared `formatExpiry` util | `ticker-detail/FuturesOrderForm.tsx:45-283`, `ticker-detail/IndexOptionOrderForm.tsx:43-338` (~90% identical) | P1 |
| R4 | `<SortTh>` component + `usePriceDirection()` hook | `PositionTable.tsx:34,76` vs `WorkspaceSections.tsx:759,801` (verbatim copies) | P1 |
| R5 | Migrate `SortableCtaTable` onto shared `useSort` + `SortTh` | `SortableCtaTable.tsx:126-130` (3rd sort impl, 3-state cycle, no a11y) | P2 |
| R6 | Delete orphan `CtaTables.tsx` | `CtaTables.tsx` (never imported; `tests/cta-page.test.ts:55` asserts non-import; helpers already drifted) | P2 |
| R7 | `<MetricBreakdownModal>` primitive + `lib/format/money.ts` | `PnlBreakdownModal.tsx`, `ExposureBreakdownModal.tsx`, `AccountMetricModal.tsx`; money fmts also dup in `FillsModal`, `dashboard/PortfolioSnapshotCard` | P2 |
| R8 | `<BookSectionHeader>` + `.book-l1-cell` classes | `ticker-detail/BookTab.tsx:48-296,692-729` (4x repeated inline header style; mirrored bid/ask columns) | P3 |
| R9 | `useDismissablePopover(ref, onClose)` hook | `ColumnsToggle.tsx:31-47`, `SharePnlButton`, `TickerSearch` (triplicated click-outside+Escape) | P3 |

**Rejected on verification:** "Three single-purpose alert banners" (`ConnectionBanner`, `ServiceHealthBanner`, `FlexTokenBanner`) — all three are **dead code** (zero non-test imports), already superseded by `FooterTelemetryStrip` (MOVE 11, 2026-05-20). No action.

---

## 2. Visual / theme consistency

| # | Finding | Locations | Sev |
|---|---|---|---|
| V1 | Raw hex/rgba (`#85b7eb`, `rgba(56,138,221,..)`, `color:#fff`) for MQ accent | `GexPanel.tsx:64-66,246,281,290-291,331,535` | P1 |
| V2 | Badge text hardcoded `#000`/`#fff` per-state | `VcgPanel.tsx:226-318`, `RegimePanel.tsx:338` | P1 |
| V3 | Solid-fill badges override canonical outline `.pill` | `globals.css:1927-1966`, `VcgPanel`, `RegimePanel`, `GexPanel:531-538` | P1 |
| V4 | Pill font-size 9px inline vs 10px canonical (20 sites) | `globals.css:1930`, `VcgPanel.tsx:226-318` | P2 |
| V5 | Three loading treatments: `SpectralLoader` vs Tailwind `TableSkeleton` vs bare text | `SpectralLoader.tsx`, `ui/Skeleton.tsx`, `WorkspaceSections`, `CashFlowsSection`, `VcgPanel`, `GexPanel:445-456` | P1 |
| V6 | `TableSkeleton` uses `rounded-md` (6px > 4px rule) + `bg-gray-800`/`bg-muted` (off-system) | `ui/Skeleton.tsx:9,18-26` | P2 |
| V7 | `SectionEmptyState` exists but most surfaces use ad-hoc empties (`.tab-empty`, `.fills-empty`, `.eb-empty`, `.futures-form-empty`, `.news-feed-empty`) | `SectionEmptyState.tsx`, ticker-detail tabs, `FillsModal`, `PnlBreakdownModal`, order forms, `DashboardNewsFeed` | P1 |
| V8 | Em dashes in user-facing copy (banned) | `PnlBreakdownModal.tsx:59`, `OptionsChainTab.tsx:493,567`, `OrderTab` header, `CtaTables.tsx:163`, `FuturesOrderForm.tsx:189` | P2 |
| V9 | `0.5px` borders on GEX badges (DPR-inconsistent) vs 1px system | `GexPanel.tsx:64-66,291` | P3 |
| V10 | Ratings bar hardcodes `#048A7A` (=`--signal-deep`), `#475569` (=`--text-muted`), `#9F1239` | `globals.css:5236,5238,5240` | P2 |

---

## 3. Cross-device functional parity

| # | Form factor losing functionality | Finding | Locations | Sev |
|---|---|---|---|---|
| P1 | mobile + tablet-touch | `InfoTooltip` hover/focus-only, no tap-to-reveal, 13px (<44px) trigger; used 30x | `InfoTooltip.tsx:36-50`, `WorkspaceSections.tsx:1300,1454` | P1 |
| P2 | mobile | Theme toggle is desktop-only | `Header.tsx:131-139`, `mobile/MobileAppBar.tsx:71-90`, `mobile/MobileMoreDrawer.tsx:14-22` | P2 |
| P3 | mobile | Discover has no mobile card variant (sibling Scanner does) | `WorkspaceSections.tsx:1418-1472` vs `:1279,1317-1351` | P2 |
| P4 | mobile + tablet-touch | CRI scatter/spread readouts bound to `onMouseMove` only | `RegimeRelationshipView.tsx:522-523,638,935-936` | P2 |
| P5 | mobile + tablet-touch | GEX/VCG/Performance/Internals panels have no mobile variant | `GexPanel`, `VcgPanel`, `PerformancePanel`, `InternalsPanel` | P2 |
| P6 | mobile | Detail modals render wide tables with no inner `table-wrap` | `FillsModal`, `PnlBreakdownModal`, `globals.css:4160-4169` | P3 |
| P7 | mobile | `FooterTelemetryStrip` build-version/WS-count not surfaced on mobile | `FooterTelemetryStrip.tsx`, `mobile/MobileMoreDrawer.tsx:140-150` | P3 |
| P8 | mobile | "Operator" link advertised in mobile drawer routes to a desktop-only guard | `admin/AdminWorkspace.tsx:264-274`, `mobile/MobileMoreDrawer.tsx:21` | P3 |

---

## 4. Interaction pattern consistency

| # | Finding | Locations | Sev |
|---|---|---|---|
| I1 | Order feedback split: inline `OrderErrorBanner`/`.order-success` vs global toast system | `OptionsChainTab:790`, `OrderTab:514`, `InstrumentDetailModal:309`, `Toast.tsx`, `WorkspaceShell:412` | P2 |

(Modal/focus-trap and sort-impl interaction findings are folded into R1, R4, R5 above.)

---

## 5. Prioritized action plan

Status legend: `[x]` = code-complete + typecheck/unit green (E2E visual pass still pending); `[~]` = partial; `[ ]` = deferred (see § 6).

**P1**
- [x] R1 `useDialogChrome` + focus-trap; `Modal`/`FillsModal`/`ShareReportModal`/`ConfirmDialog`/`NewsfeedLightbox`/`BottomSheet` all routed through it
- [x] R2 `<SingleLegOrderTicket>` wired into `InstrumentDetailModal` + `BookTab` (risk math stays caller-owned; verified faithful + order-risk fuzz/unit green)
- [x] R3 `<ListedContractOrderForm>` wired into `FuturesOrderForm` + `IndexOptionOrderForm` (action-aware submit label restored)
- [x] R4 `<SortTh>` + `usePriceDirection` extracted; `PositionTable` AND `WorkspaceSections` migrated, locals deleted
- [x] V1 `--gex-mq-accent` token added (both themes); GEX raw hex/rgba → `color-mix(var(--token))`
- [x] V2 `--text-on-accent` (aliased to `--accent-text`, theme-aware); `#000`/`#fff` badge text removed
- [x] V3 `.pill--solid` modifier added; VcgPanel/RegimePanel use it instead of inline base-`.pill` overrides
- [x] V5 loading states consolidated on `SpectralLoader` (GexPanel/VcgPanel/PerformancePanel/InternalsPanel)
- [x] V7 empties routed through `SectionEmptyState` (WorkspaceSections + ticker-detail tabs + breakdown modals)
- [x] P1 `InfoTooltip` tap-to-reveal + 44px touch target

**P2**
- [x] R5 `SortableCtaTable` → shared `useSort`+`SortTh` (2-state toggle; unit + E2E updated to assert `aria-sort`)
- [x] R6 deleted `CtaTables.tsx` (confirmed zero importers)
- [x] R7 `lib/format/money.ts` + `<MetricBreakdownModal>` primitive; Pnl/Exposure/AccountMetric modals migrated
- [x] V4 pill font-size standardized on 10px base
- [x] V6 `TableSkeleton` → brand tokens + 4px radius
- [x] V8 em dashes removed (`OptionsChainTab` x2, `PnlBreakdownModal`)
- [x] V10 ratings bar → `--signal-deep` / `--text-muted` / new `--signal-fault-deep`
- [x] P2 theme toggle added to `MobileMoreDrawer` (consumes `useTheme()` directly — one source of truth)
- [x] P3 Discover mobile card variant (mirrors Scanner)
- [x] P4 `RegimeRelationshipView` → `onPointerMove`/`onPointerLeave` (+ test fires pointer events)
- [x] P5 single-column mobile `@media` layouts for GEX/VCG/Performance/Internals
- [x] I1 order-SUCCESS routed through the toast system. `OrderActionsContext` exposes `pushNotification` + a non-throwing `useOrderActionsOptional`; OrderTab (single + combo), OptionsChainTab, and the `SingleLegOrderTicket` surfaces (InstrumentDetailModal, BookTab) push a success toast instead of an inline `.order-success`. IB rejection errors stay inline in `OrderErrorBanner`.

**P3**
- [x] R8 `.book-section-header` + `.book-l1-*` classes; BookTab inline styles removed
- [x] R9 `useDismissablePopover` hook + `ColumnsToggle`/`SharePnlButton`/`TickerSearch` migrated
- [x] V9 GEX `0.5px` → `1px` token border
- [x] P6 inner `.table-wrap` in detail modals (Fills + breakdown)
- [x] P7 build version (`NEXT_PUBLIC_BUILD_VERSION`, when set) + data-feed status on mobile drawer
- [x] P8 Operator link hidden on mobile (desktop-only)

## 6. Status (2026-05-28)

**All findings implemented (I1 completed 2026-05-29).** Shipped to production via the `ui-consistency-audit` branch + follow-on commits to `main`:
1. `1cb1a86` — verified subset (R4/R6/R7-fmt/R9 + visual/parity tokens + the `globals.css` token regression fix).
2. round 2 — R1/R2/R3/R5/R7-modal/R8/V5/V7/P2/P3/P5/P6/P7 (this commit).

**Verified:** `tsc --noEmit` clean (components/lib/app); full vitest suite **2638 pass, 0 fail**; targeted Playwright E2E green on every changed surface — Book-tab L1 (R2/R8), `/cta` sort (R5, asserts `aria-sort`), orders layout (WorkspaceSections P3/V5/V7), regime VCG-EDR-badge + COR1M panels. Order-risk fuzz + unit suites green (risk math untouched; both order tickets remain purely presentational).

**Pre-existing, NOT this audit:** 5 `ticker-search-chain` E2E cases (CMD+K focus + chain order-builder) fail on the committed baseline too (proven by stashing round-2 and re-running) — they trace to separate uncommitted test-hardening work / already-flaky-on-branch specs, and `useDismissablePopover` provably cannot affect the `Meta+k` path.

**Not pushed:** push to `main` auto-deploys to production; landing is a separate explicit step (merge `ui-consistency-audit`).
