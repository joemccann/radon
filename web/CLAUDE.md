# Radon Web — CLAUDE.md

Frontend rules and correctness invariants for the Next.js app. Loaded automatically when cwd is anywhere under `web/`. The project root `CLAUDE.md` covers cross-cutting rules + the operator surface; this file covers everything UI-specific.

---

## Sortable Tables

Every product `<table>` uses `SortTh` + `useSort`. Structural exceptions set `data-sortable-exempt` to one of: `chain-layout`, `matrix`, `markdown`, `chrome`, `kit-demo`. Contract: `web/tests/sortable-table-contract.test.ts`.

## Production Build Constraint

Next.js 16 prerender crashes on `/_global-error` + `/_not-found` (root ClerkProvider not materialised in workers). `web/package.json` build uses `next build --experimental-build-mode=compile`. `app/error.tsx`, `app/[ticker]/not-found.tsx`, `app/global-error.tsx` use plain `<a>` + pure JSX (no `next/link`, no `useEffect`, no `globals.css`).

---

## ⚠️ Cache Contract — Disk-Backed Routes

Every Next.js GET handler reading live disk state (`data/*.json`, `data/menthorq_cache/`) **MUST** export `dynamic = "force-dynamic"`. Every client fetch hitting these routes **MUST** pass `cache: "no-store"`.

Covered routes: `menthorq/cta`, `journal`, `discover`, `flow-analysis`, `blotter`, `vcg`, `internals`, `portfolio`, `performance`, `scanner`, `regime`, `gex`, `orders`, `service-health`. Hooks: `useMenthorqCta`, `useSyncHook`, `useJournal`, `usePortfolio`, `useDiscover`, `useOrders`. Contract test: `web/tests/api-routes-no-cache-contract.test.ts`.

**Offline-fallback exception (SW only, shipped 2026-07-22).** The service worker intercepts HTML navigations plus an allowlisted `/api` GET set NETWORK-FIRST. Cache Storage (a separate store from the HTTP cache the contract targets) is written on ok responses with real payloads and read ONLY when `fetch()` rejects (network failure) — never on `res.ok === false`, never as a race/timeout. Offline-served responses carry `X-Radon-Offline: 1` + `X-Radon-Cached-At`. Online behavior is byte-identical; `force-dynamic` + `no-store` remain mandatory and untouched. Decision logic lives in `web/public/sw-decisions.js`, pinned by `web/tests/sw-decisions.test.ts` + `web/tests/sw-smoke.test.ts`. Details that bite:

- Degraded 200s never enter the cache: `missing: true` bodies and per-route `API_BODY_VALIDATORS` (scanner needs `scan_time`, ticker-info needs non-empty `uw_info`) protect last-known-good from being overwritten by a 200-shaped failure.
- Cache schema version is folded into the pages/api cache NAMES (`radon-{pages,api}-<SW_VERSION>`); bump `SW_VERSION` in `sw-decisions.js` to invalidate. `radon-static-v1` is deliberately unversioned (cached offline HTML must keep finding its old hashed chunks) and bounded by `MAX_STATIC_ENTRIES` with the precache protected.
- Sign-out purges pages+api caches: `SignOutCachePurge` (mounted in `Providers`, needs Clerk context) posts `radon-clear-caches`.
- UX: `OfflineBanner` in WorkspaceShell ("OFFLINE · showing last known data · as of HH:MM:SS"); pure reducer in `lib/offline/offlineStatus.ts` — navigator events AND SW offline-served responses share a 2s enter debounce, 2 fetch failures in 45s trip, any success clears instantly. While offline: degraded banner suppressed (`deriveLiveDataError`), sidebar reads OFFLINE (not RELAY OFFLINE), mobile chip OFF. Hooks emit signals via `lib/offline/offlineSignals.ts` — HTTP 4xx/5xx are NOT offline signals (server reachable = online).
- Harness rule: settle every dispatched SW handler in tests — an un-awaited handler promise becomes an unhandled rejection that fails CI with all tests green (2026-07-22 repro).

---

## Combo / BAG Order Guardrails

1. **Never map combo `Order.action` from debit vs credit.** IB combo legs define structure. SELL envelope reverses legs. For entry: keep envelope BUY, preserve per-leg actions.
2. **`ComboLeg.action` = structure, not direction.** Always `LONG → BUY`, `SHORT → SELL`. Flipping causes IB error 201.
3. **Structure change → invalidate manual net price.** Recompute from normalized combo quote on single-leg ↔ combo transitions.
4. **Combo natural market uses cross-fields:** BUY combo pays ASK on BUY legs, BID on SELL legs; SELL combo receives BID on BUY legs, ASK on SELL legs. Impls: `computeNetOptionQuote()`, `ComboOrderForm.netPrices`, `resolveOrderPriceData()`.
5. **Trace path before fixing:** chain builder → `/api/orders/place` → FastAPI bridge → `scripts/ib_place_order.py`.
6. **Required regressions:** unit (action/ratio/net-price), browser (displayed net + submitted payload).
7. **Closing-trade detection (2026-05-20, commit e55b643).** `OrderRiskLeg.coveringLongContracts` tells risk model how many contracts of the exact same option are held LONG. SELL with `coveringLongContracts >= effectiveContracts` short-circuits to `maxLoss: 0`. SELL with `coveringLongContracts < effectiveContracts` flags only excess (M−N) as naked. Without this, every SELL-to-close of a long call triggered false "Uncovered short call". Now consumed internally by `useOrderRisk`; surfaces don't construct it by hand.
8. **Order placement must wait for IB to assign `permId` before disconnecting (2026-05-27).** `scripts/ib_place_order.py` polls `trade.order.permId` and `trade.orderStatus.status` for up to 12s on combo orders (6s on single-leg). Returns `status:"error"` with operator-readable hint if the order is still in `PendingSubmit`/`ApiPending` after the deadline — IB silently drops orders whose placing client disconnects before confirmation. FastAPI timeout for `/orders/place` bumped to 25s, Next.js `radonFetch` to 30s. Subprocess wrapper (`scripts/api/subprocess.py`) walks stdout from the END for the last line that parses as complete JSON so a stray progress print containing a list literal (`ratios=[1, 1]`) doesn't poison the result.
9. **IB combo router silently drops some structures, particularly bearish risk reversals (2026-05-27).** SELL CALL + BUY PUT at different strikes as a BAG hangs in `PendingSubmit` → `ApiPending` forever with no `errorEvent`. Bullish risk reversal (BUY CALL + SELL PUT) with identical strikes transmits fine. Single legs transmit fine. Bull/bear call/put spreads transmit fine. This is an IBKR-side combo-routing limitation, not a Radon bug. Workaround: split the bearish RR into two single-leg orders. Heads-up tooltip on the chain order builder is a follow-up. Full repro + diagnostic logs in `feedback_ib_combo_router_silent_drops_bearish_rr.md`.
10. **3-leg seagull labels (2026-07-22, commit 927f0666).** `detectStructure` (`lib/optionsChainUtils.ts`) names the same-expiry seagulls: SELL put + bull call spread → "Risk Reversal Call Spread"; SELL call + bear put spread → "Risk Reversal Put Spread". Leg order independent. Any other 3-leg shape (mixed expiries, long financing leg, wrong-direction vertical) keeps the generic "3-Leg Combo". The label flows to the builder title, confirm description, and `/orders` combo rows (`openOrderCombos` exact-matches the 2-leg "Risk Reversal" only — the new names don't collide).

### Order-Risk Chokepoint (2026-05-26)

Three production bugs in eight days (AAOI risk reversal, WULF bull call spread, RR covered call) shipped wrong risk math at the portfolio/order seam — each fix surgical to one surface, each followed by the next surface re-discovering the same gap. The chokepoint pattern eliminates the bug class structurally.

1. **Every order surface MUST render `<OrderRiskGate>`** from `@/lib/order/risk`. The gate owns `useOrderRisk` + `<OrderConfirmSummary>` + (future) telemetry. Wire it with `input` (an `OrderRiskInput`) + `portfolio` + `surface` (kebab-case tag for the telemetry buffer).
2. **`<OrderConfirmSummary>` only accepts `AugmentedOrderSummary`** — a branded type that can be produced ONLY by `useOrderRisk`. Plain literals fail typecheck; `as` casts trip a dev-mode runtime assertion.
3. **`computeOrderRisk` and `augmentOrderLegsWithPortfolioCoverage` are module-private** under `web/lib/order/risk/internal/`. ESLint blocks direct imports (`no-restricted-imports`). Tests reach them through `web/lib/order/risk/__test_only__.ts`, which is exempt via the global `tests/**` + `e2e/**` ignore.
4. **Pending UX is mandatory.** `portfolio === undefined` → `coverageStatus: "pending"` → skeleton "Coverage indeterminate — portfolio resolving". `portfolio === null` → `coverageStatus: "no-portfolio"` → skeleton "Coverage indeterminate — portfolio not in scope". Parent surface MUST disable submit when `state.okToSubmit !== true`. `okToSubmit` gates on coverage resolution ONLY (2026-07-21): an UNBOUNDED / undefined-risk verdict stays submittable with the Gate 1 warning rendered — Gate 4 (no naked shorts) was disabled 2026-04-30, and desktop surfaces never hard-blocked it.
5. **Close-out branch.** Pass `closeOut: { entryCostDollars }` on the input to short-circuit max-loss/max-gain (both 0 by construction; the order adds no new exposure) and surface proceeds + realized P&L instead. The hook owns the cost-basis convention (`avg_cost` is per-contract for options, per-share for stocks); surfaces just hand the dollar number. **A close is BOTH directions:** SELL-closes-a-LONG (proceeds positive, `entryCostDollars` positive) AND BUY-closes-a-SHORT (proceeds negative = a debit, `entryCostDollars` negative = the original credit, so `pnl = proceeds − entryCost = credit − debit`). `lib/order/positionTrade.ts` is the canonical single-leg/combo implementation of both. `OrderTab` re-invents single-leg detection inline (`isClosingLong` + `isClosingShort`) — keep both branches; a missing short branch makes a buy-to-close show OPENING risk (commit d0c8122 fixed exactly that for a VIX/short-put close).
6. **Order-internal netting BEFORE held coverage (2026-07-22, commit 715513ea).** `augmentOrderLegsWithPortfolioCoverage` pass 0 pools the order's own BUY contracts per right+expiry and nets them against the order's SELL legs FIRST; held options/stock cover only the naked residue. Without this, a self-contained bull call spread built while ALSO holding a long call of the same expiry got the held long injected on top of its own cover → net-long-calls synthetic → phantom "Max Gain: UNBOUNDED" (SPCX repro). Strike-agnostic on purpose: any 1:1 long-vs-short of the same right/expiry cancels the tail slope.
7. **Covered-call presentation (2026-07-21, commit 82951916).** A single-leg SELL C fully covered by held stock gets `marginImpact.requirement: 0` (margin kind `stock-covered-call` — the shares are the already-margined collateral; the buy-write's maxLoss is pre-existing stock risk, NOT margin consumed by the order) and a `summary.coverageNote` ("COVERED CALL: 25 short calls covered by 2,500 held shares @ $169.86 …"), rendered by `<OrderConfirmSummary>` as an `.order-confirm-coverage` chip on every gated surface. Partial cover deliberately stays on the naked path (unbounded + Reg-T naked estimate). Tests: `covered-call-order-summary.test.tsx`.

**Surfaces wired (2026-05-26):** `OptionsChainTab`, `OrderTab` (single + combo), `InstrumentDetailModal`, `BookTab`, `MobileOrderTicket`, `IndexOptionOrderForm`, `ModifyOrderModal`, `FuturesOrderForm`. Every order-entry surface in the app now routes through `<OrderRiskGate>`. Telemetry: writes per-render traces to `sessionStorage` under `radon:order-risk-traces` (ring-buffered to 50). Inspect via `dumpOrderRiskTraces()` from `@/lib/order/risk` in DevTools.

**`OrderRiskInput` discriminated union (2026-05-26).** Two variants:

- `OptionOrderRiskInput` — `chainLegs`, `netPremium`, augmentation pipeline, stock-cover folding. Six surfaces use this (chain, OrderTab×2, InstrumentDetailModal, IndexOptionOrderForm, ModifyOrderModal, mobile ticket). `type: "options"` is optional for backwards compatibility — absent means options.
- `LinearOrderRiskInput` — `action`, `quantity`, `limitPrice`, `multiplier`, `instrument: "stock" | "future"`, optional `heldQuantity` (LONG) / `heldShortQuantity` (SHORT). Two surfaces (`FuturesOrderForm`, `BookTab.StockOrderForm`). SHORT linear → UNBOUNDED + Gate-1; LONG linear → bounded by price-to-zero × multiplier × qty; SELL-against-held-LONG or BUY-against-held-SHORT → close-out branch with realised P&L. Routes through `computeLinearRisk` in `lib/order/risk/internal/`.

**Phase-2 IB what-if margin (2026-06-29, ships behind `NEXT_PUBLIC_WHATIF_MARGIN_ENABLED`, default OFF).** `useOrderRisk` stays a pure sync `useMemo`; the async layer lives ONLY in `OrderRiskGate`, which calls `useWhatIfMargin(input, state, enabled)`. The hook fires a real IB `whatIfOrder` round-trip ONLY for a genuine undefined-risk multi-leg combo whose client-side Reg-T estimate came back null (the "UNAVAILABLE — IB what-if required" case): predicate is `coverageStatus==='resolved' && marginImpact!=null && marginImpact.requirement===null && !linear && !closeOut && chainLegs.length>1`. Debounced ~400ms on a STRUCTURAL key (`internal/whatIfKey.ts`) that EXCLUDES price, abort-on-change, `useRef` success cache → one round-trip per confirm, not per keystroke. Path: `/api/orders/whatif` → FastAPI `POST /orders/whatif` (authenticated, not exempt) → `ib_place_order.py --whatif` → `IBClient.what_if_order` (bounded `asyncio.wait_for` timeout=8). Success merges via `mergeWhatIfMargin` (brand-preserving spread — NEVER `as`-cast a hand-built `marginImpact` elsewhere) into `source:'ib-whatif'`, rendered with an "IB margin" tag; loading shows "Calculating IB margin…"; error/withheld reverts to the existing UNAVAILABLE text. **Informational only — never flips `okToSubmit` (which gates on coverage resolution only; Gate 1 renders as an advisory warning).** Tests: `web/tests/whatif-margin.test.ts(x)`, `scripts/tests/test_ib_whatif_margin.py`, `scripts/api/tests/test_orders_whatif_route.py`. Live verification (real magnitudes, bearish-RR returns non-empty margin, off-hours sentinel → UNAVAILABLE) needs an authenticated gateway during market hours before the flag is flipped. Plan: `tasks/ib-whatif-margin-plan.md`.

**Property-based fuzz suite (`web/tests/fuzz/`, 2026-05-26).** `fast-check`-powered. Five properties pin the seam: P1 (long calls cover short calls → bounded), P2 (long stock covers short calls → bounded + finite), P3 (single-leg quantity linearity — `maxLoss(N) = N × maxLoss(1)`), P4 (coverage monotonicity — adding LONG cover never increases maxLoss), P5 (null portfolio ≡ empty portfolio). 1000 runs per property, seed `42` in CI, `RADON_FUZZ_RANDOM=1` for local exploratory runs. Total runtime ~250ms; under the 30s CI budget. The first run of P4 found a real math bug (`legRisk` SELL P returned negative `maxLoss` when premium > strike — single-leg path didn't clamp at 0 while multi-leg did) — fixed in the same commit. Full plan: `tasks/order-risk-chokepoint-refactor.md`.

### IB Error Message Rendering

IBKR rejection text embeds literal `<br>` tokens. `web/lib/orderError.ts:formatOrderError` normalises every variant to `\n` BEFORE prefix-stripping. `.order-error-detail` in `globals.css` uses `white-space: pre-line`. Never use `dangerouslySetInnerHTML` for IB text.

## Cancel / Modify Failure Propagation

1. **Use subprocess with original clientId.** Master (0) sees all orders but can't modify (Error 10147/103). `ib_order_manage.py` reconnects as original.
2. **Clear VOL fields before modify.** Reset `volatility`/`volatilityType` to IB sentinels (`1.7976931348623157e+308` / `2147483647`) to avoid Error 321.
3. **Confirm against refreshed open-order snapshot**, not stale `Trade`. Disappearance after cancel = success.
4. **Preserve upstream error detail.** Subprocess JSON → FastAPI `detail` → Next.js. Never collapse to 500.
5. **Required regressions:** unit, route, browser.

---

## Calculations — Correctness Rules

### Sign Convention
Credits negative, debits positive. **Never `Math.abs()` on option prices without approval.** Preserve sign through entire display pipeline.

**Avg Entry / Initial Value sign is scoped by leg count AND instrument (`getAvgEntry`, PositionTable `initial_value`).** A multi-leg COMBO carries the net credit/debit sign — a credit combo (e.g. EWY ratio reverse risk reversal) reads NEGATIVE for both Avg Entry and Initial Value. A single-leg **stock** keeps `Math.abs` because its Avg Entry is a per-instrument PRICE (a short stock shows +$1,134.97, not −; commit daca786). A single-leg **short option** reads NEGATIVE — its entry is a premium CREDIT, matching the signed LAST PRICE mark (a short META $625 call at $1.74 premium shows −$1.74; 2026-07-01). A single-leg long option stays positive (a debit paid). Sign comes from `leg.direction`, not `entry_cost` (stored as a positive magnitude). Initial Value follows the SAME sign scoping as Avg Entry (via `getInitialValue`): a single-leg **short option's** Initial Value reads NEGATIVE (premium credit), a single-leg **stock** and **long option** stay positive notionals, and a multi-leg **combo** keeps its net credit/debit sign. Per-leg `LegRow` stays `|leg.entry_cost|` (positive). Don't re-add `Math.abs` on the `pos.legs.length > 1` path citing a "non-negative" rule — that's the EWY credit-combo bug (2026-06-23). Tests: `position-table-short-stock-avg-entry.test.tsx`, `position-table-credit-combo-sign.test.tsx`.

### Daily Change %
```
Day Chg % = Daily P&L / |Yesterday's Close Value| × 100   (NEVER entry cost)
```
Per-leg: `sign × (last - close) × contracts × 100`. Impl: `getOptionDailyChg()`.

**Same-day exception:** `entry_date == today (ET)` → yesterday's close meaningless. Day Chg + Today P&L use entry-cost baseline → Today P&L = Total P&L = `MV − EC`. `ib_daily_pnl` ignored same-day.

**The exception covers STOCKS as well as options** (2026-08-11). It shipped options-only, so equities opened today kept the close baseline and reported a Today P&L that contradicted their own P&L (QQQ 666 sh @ $717.83, last $718.46: P&L +$421, Today P&L −$1,605). Impls: `getStockDailyChg()` + the stock branch of `getTodayPnlDollars()`, and the `isSameDay` branch in `computeDayMoveBreakdown` (which feeds the Day P&L card — a stock-only carve-out there makes the card disagree with the column it summarises). IB's own `reqPnLSingle` daily P&L for a same-day equity equals `MV − EC` to the cent, so the broker is the tiebreaker. Never reintroduce a stock-only inline copy of Today P&L in `PositionTable` / `MobilePositionList`; both must call `getTodayPnlDollars`. Tests: `same-day-stock-pnl.test.ts`, `e2e/portfolio-same-day-equity-pnl.spec.ts`.

### Entry-Date Resolution (`ib_sync.py`)

Strict ordered fallback, MOST → LEAST specific:
1. blotter (per-contract: `ticker|expiry|right|strike`)
2. trade_log (`ticker|structure`)
3. IB fills (per-contract, same-session)
4. prev portfolio (`ticker|structure|expiry`, excluding today)
5. **today** ← brand-new positions land here

**Never use per-ticker blotter fallback.** Test: `test_combo_entry_date.py`.

### Position Cache Refresh

`ib_insync.positions()` returns in-memory cache. TWS push updates `pos.position` immediately but `pos.avgCost` lags while TWS recomputes VWAP server-side. `IBClient.get_positions()` calls `reqPositions()` + `sleep(1)` BEFORE reading, draining pending updates so size and avgCost are consistent. Without this, portfolio syncs in seconds after a fill wrote mismatched `(size_new, avg_old)`. Opt out via `get_positions(refresh=False)` for tight read loops. Try/except so gateway hiccups fall back to cache. Tests: `test_ib_client.py::TestPortfolioOperations`. Added 2026-05-20 (commit 5d10def).

### Per-Contract avg_cost (CRITICAL)

`PortfolioLeg.avg_cost` is **per-contract for options (already × 100), per-share for stocks.** Set by `scripts/ib_sync.py:fetch_positions` from `pos.avgCost` (IB's native per-contract value for OPT) or from the journal lot-matcher's `open_basis / |position_size|`. Both produce the same per-contract unit. The display layer divides by 100 to render per-share.

Code that needs per-contract cost basis must use `leg.avg_cost` directly — NEVER `leg.avg_cost × 100` or `leg.avg_cost × multiplier`:

```typescript
// CORRECT — options:
const costBasis = parsedQty * onlyLeg.avg_cost;  // 65 × $102 = $6,630

// WRONG — options:
const costBasis = parsedQty * onlyLeg.avg_cost * multiplier;  // 65 × $102 × 100 = $663,000 (100× over)

// CORRECT — stocks: multiplier=1, dropping × multiplier is a no-op.
```

Production repro 2026-05-22: USAX 65× Call $45 @ avg $1.02/share = $102/contract, SELL @ $4.00. Pre-fix Est. Realized P&L: −$635,055. Post-fix: +$19,389.45. Fixed in `OrderTab.tsx` (commit d420c16). Regression test: `web/tests/order-tab-close-realized-pnl.test.tsx`. Fallback path in `WorkspaceSections.tsx` fixed in commit 600acd8.

### Journal Lot-Matched Basis (`scripts/clients/journal_basis.py`)

IB recomputes `pos.avgCost` server-side on every fill including partial closes, so for any position that fills in tranches across sessions the running VWAP drifts away from the original opening basis. `scripts/clients/journal_basis.py:compute_open_basis_for_ticker(db, ticker)` reads raw journal rows per `(symbol, expiry, right, strike)`, uses **net qty sign** (not the journal's `action` label) to identify opening fills, and returns `{ticker|YYYYMMDD|R|STRIKE: open_basis_dollars}`. `scripts/ib_sync.py:fetch_positions` calls `build_journal_basis_lookup()` and overrides `entry_cost = open_basis` per leg when a match exists; falls back to `pos.avgCost × position` otherwise. Raw IB value preserved as `leg.ib_avg_cost` for diagnostics. AAOI Risk Reversal repro 2026-05-21 (commit 32e611e): closed 50 of 75 contracts in two tranches, IB's VWAP drifted to $1.34/contract, journal lot-matcher correctly read the original ~$0/contract open_basis.

`open_basis` is also persisted per-row by `journal_rehydrate.py` since 4c85847; the lot-matcher prefers the persisted value when present and falls back to recomputation for older rows + rows written by the real-time daemon.

### Per-Leg P&L
`Leg P&L = sign × (|MV| − |EC|)`. Sum = position P&L. Impl: `LegRow` in `PositionTable.tsx`.

### Open-position Return %
`(MV − EC) / verified risk capital × 100`

Use exact positive `max_risk` for defined-risk positions. When exact max loss is
unavailable, require positive isolated broker-observed opening margin with
complete v2 execution, sample, and as-of provenance; a bare projected what-if
value is invalid. Debit paid is
a valid denominator only when it is demonstrably the full maximum loss,
including long stock. Otherwise render Return as unavailable. Never divide an
opening credit by its absolute premium.

### Price Resolution

| Context | Source |
|---|---|
| Stock | `prices[ticker].last` |
| Single-leg option | `prices[optionKey(...)].last` |
| Multi-leg spread | Net from each leg's `prices[legPriceKey(...)]` |
| BAG order | `resolveOrderLastPrice()` / `resolveOrderPriceData()` |
| PriceBar | `resolvePriceBar()` — option for single-leg, underlying for multi-leg |
| Underlying (option position) | `resolveUnderlyingSpot(ticker, expiry, prices)` |

**Never show underlying where user expects option/spread. Show "---" if unavailable.**

**Forward-priced indices (VIX) underlying.** VIX options settle against the VIX FUTURE for their OWN expiry, not cash spot — so an underlying/spot for a VIX option position must come from `resolveUnderlyingSpot()` (`lib/impliedValue.ts`): `prices[ticker].fwdCurve[YYYYMMDD]` → front-month `fwd` → cash `.last`, gated on `isForwardPricedIndex()` (only `VIX`; SPX/NDX/RUT are cash-settled). NEVER resolve a VIX option's underlying as plain `prices[ticker].last` — that shows spot (~15) for an August spread whose real underlying is the August future. The relay publishes the per-held-expiry curve in `prices[ticker].fwdCurve`; `resolveSpot()` already prices BS legs off it. Commit e02f4bd.

### Exposure Delta Sign
Normalize provider option delta to canonical option delta first, then apply position direction. LONG Call -> +, SHORT Call -> -, LONG Put -> -, SHORT Put -> +. Positive provider put deltas may be call-equivalent; convert with `delta - 1` before applying LONG/SHORT. Impl: `web/lib/exposureBreakdown.ts`.

### Implied (Black-Scholes) Value
TS port of `scripts/scenario_analysis.py:192-226`, verified to 4-decimal Python parity.

| Input | Source order |
|---|---|
| **S** | `prices[ticker].last` → `prices[optionKey].undPrice` → `(bid+ask)/2` |
| **σ** | `prices[optionKey].impliedVol` → bisection on `close` (T_yest = T+1/365) |
| **K** | `leg.strike` |
| **T** | `(expiry@16:00 ET − now) / 365 days` |
| **r** | `useRiskFreeRate()` → FRED DFF, 24h cache, fallback 0.0 |

Combo: signed sum across legs. Files: `web/lib/blackScholes.ts`, `impliedValue.ts`, `useRiskFreeRate.ts`. Columns gated on `positions.some(p => p.structure_type !== "Stock")`.

### Position Structure (`detect_structure_type()`)
Stock→equity. Long Call/Put→defined. Short Call/Put→undefined. Spreads→defined. Synthetic/Risk Reversal→undefined. Long Straddle→defined. Covered Call→defined. All-long combo→defined. Unrecognized→complex (→Undefined Risk table).

### Data Normalization
JSON: `"ticker"`. IB contracts: `"symbol"`. Read defensively: `t.get("ticker") or t.get("symbol")`.

### Margin Warning Thresholds (`web/lib/marginWarning.ts`)

```
critical:  excess_liquidity ≤ 0                              (active margin call)
critical:  cushion < 0.01                                    (imminent)
warning:   cushion < 0.05                                    (approaching)
warning:   equity_with_loan_value ≤ maint_margin_req × 1.10  (IBKR rule)
none:      otherwise

cushion = excess_liquidity / net_liquidation
```

`assessMargin()` is pure — derives on client from `portfolio.account_summary`. Toast in `WorkspaceShell.tsx`; `prevMarginLevelRef` fires only on transition to higher rank (`none < warning < critical`). Dismiss via `×`. **Never auto-dismiss** (`addToast(..., 0)`). Tests: `web/tests/margin-warning.test.ts` (12), `web/e2e/margin-warning-toast.spec.ts` (6).

---

## Component Cheat Sheet

| Tab | Key Files | Notes |
|---|---|---|
| **VCG** | `useVcg.ts`, `vcgStaleness.ts`, `app/api/vcg/route.ts`, `VcgPanel.tsx`, `vcg_scan.py`, `data/vcg.json` | RO: VIX>28 + VCG>2.5. EDR: VIX>25 + VCG 2.0–2.5. BOUNCE: VCG<-3.5. VVIX = amplifier, not gate. `POST /vcg/{scan,share}`, 60s cooldown. Autonomous 5-min via `radon-vcg-refresh.timer`. Wrapper POSTs `/vcg/scan`, fallback direct script. 15min banner window. |
| **GEX** | `useGex.ts`, `gexStaleness.ts`, `app/api/gex/route.ts`, `GexPanel.tsx`, `gex_scan.py`, `data/gex.json` | UW: `call_gex` positive, `put_gex` negative, `net = call_gex + put_gex` (no negation). Levels: GEX Flip, Max Magnet, Max Accelerator, Put/Call Wall. Bias: BULL/CAUTIOUS_BULL/NEUTRAL/CAUTIOUS_BEAR/BEAR. 71 tests. |
| **RV Ratio** (`/options/rv-ratio`) | `useRvRatio.ts`, `rvRatio.ts`, `marketSession.ts`, `app/api/options/rv-ratio/route.ts`, `RvRatioPanel.tsx`, `RvRatioChart.tsx`, `rv_ratio_scan.py`, Turso `rv_ratio_snapshots` + `price_history_daily`, `data/rv_ratio/` | Per-asset 252-session realized vol ÷ SPY with ±1σ band + divergence regime (`in_band/elevated/decoupled/compressed`). GET is Turso-first (`dbFirstRead`); a missing/stale snapshot fires exactly ONE synchronous scan POST (600s FastAPI cooldown, no polling). Freshness is session-relative to the **last COMPLETED ET session** (`lastCompletedSessionDate`, 16:00 ET boundary) — intraday, yesterday's close is current; never mark a snapshot stale for lacking today's in-progress candle. Plan: `tasks/rv-ratio-indicator-plan.md`. |
| **CRI / Regime** | `criStaleness.ts`, `regime` route triggers `cri_scan.py` | Stale if `data.date != today` OR (market_open AND mtime>60s). CRI `history` carries ~251 days; chart slices for display; statistical windows are explicit constants. |
| **Regime market-closed** | `RegimePanel` | Use `data.{vix,vvix,spy}` only. `activeCorr = data.cor1m`. `liveCri / intradayRvol = null`. Don't update VIX/VVIX timestamps. COR1M = DAILY. |
| **Regime day-change** | `.regime-strip-day-chg` | VIX/VVIX/SPY: WS `last` vs `close`. RVOL: `intradayRvol - data.realized_vol`. COR1M: `data.cor1m_5d_change`. Arrow right of change via `display: flex; gap: 4px`. |
| **Regime history** | `CriHistoryChart.tsx` | 20 sessions, 440px. L: VIX `#05AD98` + VVIX `#8B5CF6`. R: RVOL `#F5A623` + COR1M `#D946A8`. |
| **CRI spread zoom** | `RegimeRelationshipView.tsx`, `regimeRelationships.ts` | "Correlation Risk Premium" on `/regime/cri`: presets (`1M/3M/6M/1Y/All`, default `1Y`) + brush minimap (hand-built pointer events, no `d3.brushX`). `Z_SCORE_WINDOW=20` scoped to full history, not visible slice. Brand tokens, 4px radius. |
| **Options Chain sticky header** | `OptionsChainTab.tsx` | Three required CSS rules: `background: var(--bg-panel-raised)` on `.chain-header`+`.chain-side-label`; `position: sticky; top: 0`/`top: 24px`; `.chain-grid thead { position: relative; z-index: 10 }`. `src=vol-cone` labels the builder `PREFILLED FROM VOL CONE`; any other src stays `PREFILLED FROM THETA HARVESTER`. |
| **VOL CONE** | `VolConePanel.tsx`, `lib/volCone.ts` | Selected-name analysis; ticker / OPEN TRADE open the listed long strangle or ATM straddle via `volConeOrderHref`. |
| **Column visibility** | `useColumnVisibility(tableId, defaults)` | `localStorage` keyed `radon:columns:<tableId>`. Buckets: `positions-{defined,undefined,equity}`, `orders-open`. `<ColumnsToggle />` left of filter input. |
| **Fill Toasts** (2026-07-21) | `lib/fillToasts.ts` (pure), `lib/useFillToasts.ts`, mounted in `WorkspaceShell` | Persistent (duration 0, manual dismiss) success toast per new execution: `FILLED · SELL 25x EWY $175C @ $4.55`. Dedupe key strips IB execId correction suffix; first payload of a browser session primes silently (no load storm); seen set persists to sessionStorage (`radon.fill-toast-seen`, 300 cap); route-nav remounts restore the baseline and DIFF their first payload so mid-navigation fills still toast. Demo suppressed. sessionStorage access is SecurityError-safe. Tests: `fill-toasts*.test.*`. |
| **Company tab short stats** (2026-07-21) | `CompanyTab.tsx`, `useShortAvailability` (60s module TTL cache), `app/api/ticker/info/route.ts` | Three KEY STATISTICS cells: Shortable Shares (IB tick 89) + Borrow Fee (UW fee_rate) via `/api/short-availability` (probe gated on Info deck open, never for indexes); Public Float from UW `shorts/{t}/interest-float/v2` folded into the info-route cache with `short_float_checked_at` (24h recheck; structurally-empty tickers don't refetch per HIT). `fmtVol` has a B tier (AAPL float "14.7B"). Missing data renders "---". ETFs keep borrow cells, drop float; indexes get none. |
| **Margin Warning Toast** | `marginWarning.ts`, `WorkspaceShell.tsx` | Stage 1 — threshold-derived from `portfolio.account_summary`. Persistent toast, fires only on transition to worse rank. |
| **Cash Flows** (on `/orders`) | `scripts/cash_flow_sync.py`, `0002_cash_flows.sql`, `GET /cash-flows`, `useCashFlows.ts`, `CashFlowsSection.tsx`, `handlers/cash_flow_sync.py` | IBKR `CashTransaction` rows (deposits/withdrawals/dividends/interest/fees/withholding). Reads `IB_FLEX_NAV_QUERY_ID`. Idempotent on `transactionID`. **Cadence: once per ET trading day at 17:00 ET.** Skips weekends + US holidays via `utils.market_calendar`. Throttle-aware backoff on Flex 1001/1018/1019: 24h→48h→72h→168h capped. **Sync lozenge** (commits 45c58b3 + 20a6a74): route surfaces `last_synced_at` + `sync_status = {state, last_attempt_at, next_attempt_at, error_summary, is_throttled}` from `service_health.cash-flow-sync`. Lozenge renders `Synced Xh ago · Flex throttled, retry 17:00 ET tomorrow` in amber when `is_throttled`; red `--fault` for other errors; calm `--ok` when healthy. Don't manually retry — every Flex request during throttle pushes the reset further out. |
| **Dashboard** (overhauled 2026-08-09 from the Claude Design mock) | `dashboard/DashboardSurface.tsx`, `KpiStrip.tsx`, `ScannerHero.tsx`, `CatalystsQuadrant.tsx`, `EngineStatePanel.tsx`, `DashboardNewsFeed.tsx`, libs `dashboardKpis.ts`, `scannerHero.ts`, `catalystGroups.ts`, `engineState.ts`, `useNewsfeedPosts.ts` | **KPI strip + 1.2fr/1fr grid.** Strip: Net Liq / Today P&L / Buying Power (bar = available_funds/EWL) / Margin Used (maint/NLV, tone via `assessMargin`). Left: `DashboardNewsFeed` (paginated list, tag bar, lightbox, bookmarks) on ALL viewports inside `.dashboard-surface__feed` — sticky, `max-height: calc(100vh - 120px)`, internal scroll (static/unclamped ≤1280px and on mobile shell). A FeedPanel featured-story variant shipped and was reverted same-day (operator prefers the plain list). Right: `ScannerHero` theta/strength pill tabs (both scans are kept fresh by `radon-signals-refresh.timer` — 15min during ET trading hours; before it shipped 2026-08-12 nothing ever called them and the panel served the last hand-triggered scan, a day-old theta snapshot and a 16-day-old strength one. `formatScanSample` dates any sample not from today so that can't read as fresh again; only active tab polls; other scans on `/scanner`; strength gate-ladder track is `minmax(0,196px)` — the shrink absorber, or PASSED/STATE overflow the panel) above the quadrants: `CatalystsQuadrant` (category groups in operator order ECONOMIC DATA / EARNINGS / FDA / remainder, with progressive disclosure: only the leading category opens, headers toggle, a category past 6 rows previews behind SHOW ALL and then scrolls inside a 320px box; held tickers survive at any distance, lead their category and carry a HELD flag) and `EngineStatePanel` (CRI · MARKOV · VCG · GEX, real payload-derived readings; missing data = `P ---`, never fault). Section ids `feed/signals/catalysts/engine`; DashboardSurface takes `marketState`, orders prop removed. |
| **Newsfeed Lightbox** (`NewsfeedLightbox.tsx`) | `createPortal` to `document.body` z-index 9999 to escape the rail's stacking context. | **Backdrop:** two-layer canvas wash (97% base + 60% overlay) + 12px blur + 60% saturate desaturation. **Panel:** signal-core border + 4-layer shadow + top-edge spectral-bar gradient. **Image:** radial wash + corner reticles. **Entry motion:** 180ms scrim fade + 220ms panel rise 0.97→1.0; gated behind `prefers-reduced-motion: no-preference`. **Keyboard:** ←/→ cycle through `navigablePosts` (filteredPosts with at least one image — text-only posts skipped); Esc dismiss; scrim click dismiss. Cursor follows to the right pagination page when navigating off-page. Floating chevrons appear only when the corresponding direction is navigable. Image-attribution bug fixed in b405267: scraper no longer honours JSON-LD `schema.image` when article DOM has no `<img>` (themarketear inserts `generic.png` placeholder). |
| **LEAP tab** (in OpportunitiesCard, commit f2bd329) | `scripts/leap_scanner_uw.py --json`, `data/leap.json`, `app/api/leap/route.ts`, `useLeap.ts`, `POST /leap/scan` (FastAPI, 35da343) | GET-only route reads `data/leap.json`; returns `{results: []}` empty state. POST `/api/leap/scan` triggers `leap_scanner_uw.py` via FastAPI with 600s cooldown. Script mirrors output to both `reports/leap-scan-uw.json` and `data/leap.json`. Top 5 by `best_gap`. Hetzner timer `radon-leap.timer` fires Mon-Fri at 14:00 UTC. |
| **LLM Token Index** (on `/regime/llm`) | `scripts/llm_token_index.py`, `0007_llm_token_index.sql`, `GET /llm-token-index`, `useLlmTokenIndex.ts`, `LlmTokenIndexCard.tsx`, `radon-llm-index.{service,timer}` | Pulls Artificial Analysis API once daily 06:30 UTC. Per-model blended `0.7*input + 0.3*output`, `raw_avg_usd = median(basket)`. Basket: GPT-4o, Opus 4.7, Sonnet 4.5, Gemini 2.5 Pro, DeepSeek V3, Llama 3.1 405B, Mistral Large. Missing models skip. Normalised to 1.0 on first persisted UTC date. Env: `ARTIFICIAL_ANALYSIS_API_KEY`. 25h service-health window. |
| **Mobile shell** (PWA, 393×852) | `useViewport.ts`, `breakpoints.ts`, `components/mobile/{MobileShell,MobileAppBar,MobileTabBar,MobileMoreDrawer,TickerSearch,Card,BottomSheet}.tsx`, `PwaRegister.tsx`, `public/{manifest.webmanifest,sw.js}` | `useViewport()` (≤640 / 641-1023 / ≥1024) drives `<MobileShell>` from `WorkspaceShell` when `isMobile && hasMounted`. Sets `body[data-mobile="true"]`. Manifest standalone, theme #0a0f14. Hand-written SW is network-first with offline fallback (`sw-decisions.js` allowlist); `/ws`, `/health`, `/_next/data`, non-GET and non-allowlisted `/api` stay untouched. |
| **Mobile variants** | `mobile/{MobilePositionList,MobileOrderList,MobileBlotterList,MobileExecutedList,MobileJournalList,MobileChainLadder,MobileOrderTicket}.tsx` | Branched via `isMobile && hasMounted`. All P&L/combo math reused. Chain ladder 2-col, tap → BottomSheet detail with Greeks. Pending strip → `MobileOrderTicket` posts `/api/orders/place` with same body shape. |
| **Mobile tests** | `tests/{use-viewport,mobile-bottom-sheet}.test.*` (15 vitest); `e2e/mobile-*.spec.ts` (48 Playwright at 393×852) | `PLAYWRIGHT_PORT=3033 npx playwright test --project=mobile`. E2E stubs API + skips WS prices. |

---

## Theme System

- **Single source of truth:** `web/lib/ThemeContext.tsx` (`useTheme()`). Never duplicate theme state.
- **Pre-paint bootstrap:** `ThemeBootstrap.tsx` mounts in `<head>` and synchronously sets `data-theme` on `<html>` from `localStorage.theme` or `prefers-color-scheme` BEFORE React hydrates. Eliminates FOWT.
- **SSR theme pinned to `"dark"`** in `ThemeContext.tsx:SSR_THEME`. Provider's initial `useState` MUST return this constant — never read localStorage/matchMedia/`data-theme` during first render, or React #418 hydration mismatch fires for every light-theme user. Post-mount `useEffect` reconciles via `readClientTheme()`. Commit 68c6e57 + `tests/theme-provider-hydration.test.tsx`.
- **Brand tokens via `color-mix(in srgb, var(--token) X%, transparent)`** — never bake raw `rgba(R,G,B,α)`. Raw rgba doesn't shift between light/dark CSS vars; `color-mix` does. Tailwind `green-500`/`red-500` are NOT brand — replace with `var(--positive)`/`var(--negative)`.
- **`<meta name="theme-color">` owned by Next.js viewport metadata** — declare light/dark variants via `viewport.themeColor`. Do NOT mutate from client code.
- **`<head>` and `data-theme`** — root layout sets `suppressHydrationWarning` on `<html>`; `ThemeBootstrap` paints the attribute. Do not hardcode `data-theme="dark"` in JSX.
- **IB Gateway status display** — `IBStatusContext.displayStatus`: `connected | awaiting_2fa | unhealthy | unreachable | ib_offline | relay_offline`, derived from WS-relay + `/api/admin/health` (15s poll). Sidebar footer + MobileAppBar chip both read this. Amber `.status-dot-warn` for `awaiting_2fa`.
- **ETF Company tab filter** — `CompanyTab.tsx` hides equity-only stats (Market Cap, P/E, EPS, Next Earnings) when `uw_info.issue_type` matches `ETF|ETN|FUND|MUTUAL|REIT`. Drops Div Yield too for `INDEX|IDX`. Short stats scoping (2026-07-21): Shortable Shares + Borrow Fee render for stocks AND funds (real borrow markets), Public Float is equities-only, indexes get none and never fire the IB probe.

---

## Auth (Next.js side)

- **Middleware** at `web/middleware.ts` enforces Clerk JWT. Localhost auto-bypass when `NODE_ENV !== "production"`. `RADON_AUTHLESS_TEST=1` for Playwright.
- **WebSocket auth** via `scripts/api/ws_ticket.py` (30s TTL).
- **Auth-exempt at FastAPI** (also reachable from Next.js): `/health`, `/ws-ticket/validate`, `/demo/trial-expiry`, all `*/share` routes. (`/docs` + `/openapi.json` are trusted-local only since the 2026-06-28 audit — public callers get 401.)
- **Public `*/share/content` routes serve ONLY `tweet-<type>-<date>[-card-N].html` from directly inside `reports/`** (validator `web/lib/shareReportPath.ts`). They are unauthenticated (link-preview exemption) and used to serve any file under `reports/`, leaking portfolio/eval reports. Never widen to arbitrary paths.
- The middleware runs in Edge runtime — no `node:*` imports. Vitest passes in Node and won't catch it. See `feedback_middleware_edge_runtime.md` for the regression that pulled `node:crypto` in and crashed production with "Native module not found".
- **MFA required on production** — the Clerk instance (clerk.radon.run, `pk_live`) enforces MFA for all users on app.radon.run and demo.radon.run (same instance). Every sign-in requires a second factor (TOTP or SMS); Clerk only mints a complete session JWT after the second factor, so middleware (`userId` gate) and FastAPI Bearer auth need no MFA-specific code. Localhost dev bypasses Clerk entirely (`NODE_ENV !== "production"`), so MFA is not exercised locally.

---

## WebSocket State (`usePrices.ts`)

State machine: `idle → connecting → open → closed`. `connStateRef` idempotent connect, `socketGenRef` ignores stale events, diff-based sub/unsub, exponential backoff (1s–30s, max 10). Stale tick detection at 45s drives a bounded recovery ladder in the relay (pure core in `scripts/lib/staleDataMachine.js`): farm-OK → resubscribe, else up to K=3 socket reconnects, then **escalate to an alert** (writes a `service_health` error row `ib-realtime-relay` for the watchdog). The relay NEVER restarts the IB Gateway itself (avoids 2FA-stacking). Gateway farm-down (gateway authenticated but relay gets zero ticks) needs a full `radon restart`, not a relay-only restart. See `project_relay_stale_tick_ladder_2026_06_05`.

---

## L2 Order Book (depth-of-book) — `BookTab`

Depth montage / ladder + Time & Sales in the ticker Book tab. Plan + full design: `tasks/l2-orderbook-implementation-plan.md`. Mockup: `/tmp/radon-l2-montage.html`.

- **Feature flag.** Gated end-to-end on `RADON_DEPTH_ENABLED` (relay reads `process.env`). OFF (default) → relay opens NO `reqMktDepth` tickets, registers no depth handlers, stays on delayed-frozen (`reqMarketDataType(4)`); the UI shows the existing `<L1OrderBook>`. ON → relay flips the connection to realtime (`reqMarketDataType(1)`) — depth + tick-by-tick require realtime. A dedicated realtime depth client is the production follow-up; today the shared relay connection flips while depth is active.
- **IB lib.** The relay uses **`@stoqey/ib`** (migrated from the dead `ib@0.2.9`, commit f69c0d4) — events via the `EventName` enum, contracts as plain object literals, `error` is positional `(error, code, reqId)` with a `reqId>=0` guard. `reqMktDepth(id, contract, numRows, isSmartDepth)`: `isSmartDepth=true` equity/option (SMART-aggregated montage, `marketMaker`=exchange), `false` futures (`updateMktDepth`, no MM → price-level ladder). **Depth ticket budget ~3 concurrent** → relay enforces a cap + LRU recycle keyed to the focused symbol.
- **Entitlements (confirmed live).** Account is realtime-entitled with L2 depth on NASDAQ/BATS/ARCA/BEX/NYSE/IEX. Error `10089` (no depth entitlement) → relay cancels the ticket + emits `depth-unavailable`, UI degrades to L1; never latches a fault.
- **WS protocol.** New inbound: `depth` / `depth-batch` (`{updates: Record<symbol, DepthBook>}`, reuses the 100ms flush) / `depth-unavailable`. New client actions: `subscribe-depth` / `unsubscribe-depth` (**single** symbol — scarce resource, only the focused subject subscribes; distinct from the array `subscribe`). Types in `pricesProtocol.ts`: `DepthBook` (kind stock|option|future, two-sided `bid`/`ask` of `DepthLevel`, `entitled`/`feed`). `usePrices` exposes `depths` + a `depthSymbol` option (never forces a connect); `TickerDetailContent` publishes the focused book key up to `WorkspaceShell`'s `usePrices` via `TickerDetailContext`.
- **Components** (`ticker-detail/`): `OrderBook` (kind dispatch + show/hide tape toggle, grid reflow via `.tape-hidden`, localStorage `radon:book:tape`), `DepthMontage` (stock+option two-sided), `LadderDOM` (futures centered ladder, cumulative fans), `TimeAndSales` (tick-test tape). Pure derivations in `web/lib/book/depthDerivations.ts` (`groupPriceLevels`, `buildLadderRows`, `montageFill`, `classifyTicks`, `isBestLevel`) — unit-tested.
- **Verification.** Depth rows + the tape only populate during **market hours (RTH)** — empty off-hours is correct, not a bug. Migrated relay verified live off-hours (connect + L1 stream + depth subscription accepted); a populated ladder needs an RTH chrome-cdp check. Phase 1 ships an empty `Trade[]` tape (toggle/reflow exercised); the dedicated tape feed is Phase 3.
