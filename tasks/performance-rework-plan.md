# Performance Determination — Stress Test & Rework Plan
**Date:** 2026-08-05
**Status:** Draft — replaces `scripts/portfolio_performance.py` + `web/app/api/performance` + `web/components/PerformancePanel.tsx`
**Owner:** Radon workstation

---

## 0. Decision
Hide `Performance` from all navigation until the new methodology ships.
`web/lib/data.ts:44` stays `hidden:true` (`Performance` route). `Sidebar.tsx` and `MobileMoreDrawer.tsx` both filter `!hidden`, so desktop and mobile are now consistent. No redirect added — direct `/performance` still renders but is unreachable from chrome. Re-enable by flipping `hidden` and bumping `SW_VERSION` in `sw-decisions.js`.

---

## 1. Current Logic (as-built)

### 1.1 Two-path build in `scripts/portfolio_performance.py:build_payload()`
1. **Primary — IB daily NAV** (`EquitySummaryInBase` via Flex `IB_FLEX_NAV_QUERY_ID`):
   - `fetch_ib_nav_series()` → `data/nav_history_ib.json` cache → `build_nav_based_curve()` which does `daily_return = nav[i]/nav[i-1]-1`
   - If `data/ib_twr_series.json` exists (Highcharts scrape from IB portal) → overrides `daily_return` via `(1+cumTWR_today)/(1+cumTWR_yesterday)-1` and `total_return = last cumTWR`. `annualized_return = (1+total_return)^(252/N)-1`.
   - Series aligned to `SPY` calendar (`benchmark_series` from IB bars → UW → Yahoo fallback).
   - Returns `curve_type: "ib_daily_nav"`, `return_basis: "twr_deposit_adjusted"` (misleading — see §2).

2. **Fallback — Reconstructed equity** (when NAV missing):
   - `fetch_flex_trade_fills()` → `parse_flex_trade_rows()` (Flex `IB_FLEX_QUERY_ID` trades) or `load_blotter_fallback()` (`executed_orders` JSON)
   - `extract_fill_marks()` seeds option marks from execution prices, then `_fetch_all_histories()` fetches stock bars (IB→UW→Yahoo) and option marks (UW `option_contract_historic`, `select_option_mark()` chooses `(bid+ask)/2` or last) in 8-thread pool, cached in `data/price_cache/{stocks,options}`
   - `reconstruct_equity_curve()` walks the SPY calendar: `initial_cash = final_net_liq - sum(net_cash) - final_holdings_value`, then day-by-day `equity = cash + Σ qty*multiplier*mark`, `daily_return = equity/prev-1`, `drawdown = equity/cummax-1`
   - Phantom-position fix: `_build_portfolio_positions()` compares `final_holdings` from trades vs live `portfolio.json` positions; for any delta injects a zero-cash `TradeFill` on `2025-12-31` (hard-coded) with `mult = 100|1`
   - Final-day marks injected from live `portfolio.legs[].market_price`
   - `compute_performance_metrics()` on `equity` + `benchmark_series` → 30 metrics (§1.3).

### 1.2 Frontend
- `web/app/api/performance/route.ts:13` `dynamic="force-dynamic"`, `dbFirstRead` (Turso `performance_snapshots` vs `data/performance.json`), 15-min `CACHE_TTL`, stale check `isPortfolioBehindCurrentEtSession()` → optional `radonFetch("/portfolio/sync")` (35s), `shouldRebuild = !cached || stale || isCacheBehindPortfolio`, SWR: serve stale + `triggerBackgroundRebuild()` (`POST /performance/background` 5s), cold start `POST /performance` 180s.
- `web/lib/performanceFreshness.ts:44` `isPerformanceBehindPortfolioSync()` compares `last_sync`/`as_of` as ET dates via `scanTimeToEtDate()` to avoid UTC-midnight drift.
- `web/components/PerformancePanel.tsx` renders `buildPerformanceChartModel()` (nice ticks, rebased SPY) and 30 `StatCard`s + `MetricDefinitionModal`.

### 1.3 Metrics in `compute_performance_metrics()`
`total_return = equity[-1]/equity[0]-1`, `annualized = (1+TR)^(252/N)-1`, `vol = σ_daily*√252`, `sharpe = mean/σ*√252` (risk_free=0), `sortino` via downside RMS, `maxDD = min(equity/cummax-1)`, `calmar = ann/maxDD`, `beta = cov/var_bench`, `alpha = (mean_p - β*mean_b)*252`, `tracking_error = σ(active)*√252`, `capture` via `Π(1+r)-1` ratio, `VaR_95 = p5`, `CVaR = mean(r≤VaR)`, `tail = |p95/p5|`, `ulcer = √(mean(dd²))`, `skew/kurt` via pandas, `hit_rate = pos/N`.

---

## 2. Stress Tests — Where It Breaks

### S1 External cash flows
**Test:** Deposit $250k on 2026-03-15, withdraw $100k on 2026-06-01, still YTD.
**Current:** `build_nav_based_curve()` comment says “IB’s daily NAV IS the source… No TWR formula, no deposit detection” but then uses `nav[i]/nav[i-1]-1`. A deposit jumps NAV +250k → `daily_return ≈ +12%` phantom gain, `total_return` inflated, `vol`, `maxDD`, `sharpe` corrupted. The fallback path assumes “external cash flows are zero” (`initial_cash = final - sum(net_cash) - holdings`), so the same deposit is mis-attributed as trading P&L. `_extract_acats_transfers()` heuristic (`chg>50k && !fills && !cash_flow`) and `_extract_cash_flows()` (Flex CashTransactions) exist but are dead code — `build_nav_based_curve()` does not call them and the warning says “Includes deposits and transfers”.
**Severity:** Critical — any funding breaks the only metric the user cares about (return).

### S2 ACATS / transfer-in
**Test:** Transfer 5,000 shares AAPL from another broker.
**Current:** No `fill` row, NAV jumps. Heuristic threshold 50k misses sub-50k transfers, and >50k false-positives on a large winning day with no fills (e.g., long option expiry). Synthetic trade injected on 2025-12-31 with `net_cash=0` creates a position that never had basis, so cost basis and `drawdown_duration` are wrong. Worse: the transferred shares’ prior cost basis is lost, so `initial_cash` is miscomputed.
**Breaks:** `total_return`, `alpha`, `benchmark` alignment.

### S3 Inception / sub-period
**Test:** Account opened 2026-05-01, YTD start is 2026-01-01.
**Current:** `start_date = f"{end_date[:4]}-01-01"` always Jan 1. For a May inception, Jan–Apr are fabricated (no NAV/trades) → `equity[0]` is synthetic, `annualized_return = (1+TR)^(252/N)` annualizes a 2-month run to a 12-month number (≈6× error if May was +5%), `vol` understated, `TRADING_DAYS=252` applied to N≈40.
**Breaks:** `annualized_return`, `calmar`, `sharpe` meaningless.

### S4 Corporate actions — splits, dividends, spin-offs
**Test:** NVDA 10:1 split on 2026-06-07, AAPL $0.24 dividend 2026-05-10.
**Current:** Stock history via IB `TRADES` bars is split-adjusted (IB adjusts), but reconstructed holdings use `qty` from Flex `quantity` which is post-split, while `marks_by_contract` from `STK:AAPL` cache may be unadjusted Yahoo fallback (div events via `events=div,splits` but code discards `events` and only takes `close`). Dividend cash never appears as `net_cash` (Flex `assetCategory=STK, quantity` for div is 0, filtered), so dividend return is missing. Option `multiplier` from Flex is stale for adjusted options (e.g., TSLA 2025 special dividend multiplier 0.9973).
**Breaks:** `equity`, `daily_return` step on split day, total return missing ~1–2% dividend yield.

### S5 Options-specific
**Test:** Weekly 0DTE SPX put spread, expired worthless; VIX option with forward-priced settlement.
**Current:** `select_option_mark()` prefers `(bid+ask)/2` then `last/close`. 0DTE after 4pm has `bid=0, ask=0` → falls back to `last` which may be stale 3.50 vs true 0.05 → holdings overstated by 70×. `align_mark_series()` (not shown) forward-fills seed execution marks for missing UW history — single fill price held for 60 days. VIX `resolveUnderlyingSpot()` logic exists elsewhere but performance uses daily `close` only, not settlement. Expired contracts stay in `final_holdings` until next `marks_by_contract` fetch fails → `missing_contracts` valued at 0, creating cliff-drop on expiry day.
**Breaks:** `vol`, `VaR`, `worst_day`, `ulcer_index` spiked.

### S6 Benchmark / calendar mismatch
**Test:** SPY calendar defines `calendar = sorted(benchmark_history.keys())`. Market closed 2026-01-01 (New Year), but `reconstruct_equity_curve()` snaps `trade_date` to `next calendar day` via loop. A trade on 2026-01-01 snaps to 2026-01-02, shifting `daily_return` and double-counting. IB bars missing for SPY on holiday → calendar empty → `raise ValueError("calendar must not be empty")`. `_last_sync_to_et_date()` fallback `last_sync[:10]` on malformed `last_sync` can misplace YTD end date.
**Breaks:** `trading_days`, `total_return`, crash on holidays.

### S7 Fees, borrow, interest, FX
**Test:** Short stock with borrow fee, FX-denominated position (EWJ JP).
**Current:** `net_cash = (-qty*price*mult)-commission` includes commission from Flex, but borrow fee appears as separate `Flex -> CashTransactions: type=Borrow Fee` never summed into `cash`. Flex NAV includes it, but reconstructed cash does not → `equity` drifts from NAV. FX rate not applied: `Stock(EWJ)` bars are in USD, but local `net_liquidation` is USD— fine — but non-USD cash balances mis-handled.
**Breaks:** `equity` drift, `tracking_error`.

### S8 Hard-coded sentinel date
**Test:** Any YTD other than 2026.
**Current:** Synthetic phantom trade uses `trade_date="2025-12-31"` literal. In 2027, that date is inside YTD, not pre-period, so `holdings` logic treats it as `trade_map` entry for `2027-01-02` instead of pre-period `holdings` seeding → phantom short persists for a year.
**Breaks:** 2027 positions wrong.

### S9 Risk-free and annualization conventions
**Test:** T-bill 5.2% in 2026, 40-day track record.
**Current:** `methodology.risk_free_rate = 0.0`, `sharpe` uses 0 → overstated by ~1.0 if true Rf=5%. `TRADING_DAYS=252` for annualization but crypto/0DTE trades 7 days → vol understated. `annualized_return` on 40-day sample uses `252/40=6.3` exponent → amplifies noise, `calmar` divides by `maxDD` which may be 0 for flat 40 days → 0.
**Breaks:** All risk-adjusted ratios.

### S10 TWR scrape fragility
**Test:** IB changes portal Highcharts JSON path.
**Current:** `ib_twr_series.json` is scraped, not an official endpoint (`fetch_ib_nav_series()` already has dedicated Flex `EquitySummaryInBase` but TWR comes from portal). If file missing, `twr_by_date` empty → falls back to NAV `nav[i]/nav[i-1]` which includes deposits per S1. If file stale, `last_twr` loops `reversed(dates)` to find match — may pick a 2-week-old TWR, silently understating return.
**Breaks:** Silent degrade, no alert.

### S11 Cache / concurrency
**Test:** 10 symbols × 60 option contracts, `PERF_FETCH_WORKERS=20`.
**Current:** `ThreadPoolExecutor` 20 threads each creating `UWClient()` → UW rate limit (`UWRateLimitError` caught, but 429 from Yahoo not caught), `write_cache()` concurrent writes to same `STOCKS_DIR` key without lock → corrupt JSON. `prune_cache()` runs after, deleting entries with no ref count.
**Breaks:** Missing history → seed-fill valuation at 0.

### S12 Frontend staleness
**Test:** User opens `/performance` at 09:31 ET after `portfolio_sync` POST 35s timeout.
**Current:** `route.ts:GET` waits `Promise.all([stale, perfRead, portfolioSnapshot])` where `perfRead = dbFirstRead(..., maxAgeMs=15min)`. If portfolio is behind (`isPortfolioBehindCurrentEtSession`), it `await radonFetch("/portfolio/sync")` 35s blocking the response. If that fails, it returns `cachedPerformance` even though `isCacheBehindPortfolio` is true — user sees stale 15-min-old curve during market open with no banner. `triggerBackgroundRebuild()` 5s fire-and-forget may be dropped by Edge.
**Breaks:** User sees “Nominal” while curve is 1-day old.

### S13 Metric nonsense on small N
**Test:** 3 trading days since inception.
**Current:** `vol = σ*√252` with `ddof=1` on N=3 → huge sampling error; `corrcoef` on 3 points → ±1 noise; `beta` variance 0 → 0; `skew = portfolio_returns.skew()` requires N>2 but pandas returns NaN for N=3 with constant returns → NaN serialized then rounded? `np.quantile` on N=3 for `VaR_95` = interpolation of 0.15 quantile → not meaningful.
**Breaks:** All 30 metrics display nonsense with no `warnings` or `N` gate.

### S14 Multi-currency / multi-account
**Test:** Two IB sub-accounts (U123, U456) with internal transfer.
**Current:** `IB_FLEX_TOKEN`/`query_id` assumes single Flex report covering all trades; internal transfer appears as withdrawal from U123 and deposit in U456 → `_extract_cash_flows()` double-counts if both accounts share token. No `accountId` grouping.
**Breaks:** `total_return` with phantom ±500k.

### S15 Methodology mislabel
**Current:** Returns `methodology: { curve_type: "ib_daily_nav", return_basis: "twr_deposit_adjusted" }` but code is `nav[i]/nav[i-1]-1` (unadjusted). `risk_free_rate: 0.0` but label implies T-bill. `library_strategy: "ib_equity_summary_in_base"` is vague. User copies this into a memo, it’s wrong.

---

## 3. Evaluation

**What survives:** IB Flex `EquitySummaryInBase` daily NAV is the right anchor — it’s GIPS-reconcilable, includes corporate actions, fees, and FX. SPY as benchmark and 252-day conventions are fine for US equities. The `dbFirstRead` + cache pattern and ET-date handling (`scanTimeToEtDate`) are sound.

**What fails structurally:** Return math is not time-weighted. Any external flow breaks it. Reconstructed fallback is academic — 1482-line Python mixing IB→UW→Yahoo with thread pools and seed-mark forward-fill cannot be trusted for client money. 30 metrics with no `N` gate or `Rf` invite hallucination. Hard-coded 2025-12-31, scrape-based TWR, and 5s background rebuild are maintenance time-bombs.

**Verdict:** Do not fix in place. Replace with a single TWR implementation driven solely by Flex, and surface TWR vs MWR explicitly. Hide until replaced (done).

---

## 4. New Plan — How Performance Would Work

### 4.1 Principles
- **One definition:** Time-Weighted Return (TWR) is the performance. Money-Weighted (MWR/IRR) shown only as a secondary “dollar experience” card. No blended NAV/return hacks.
- **GIPS-aligned:** Modified Dietz per sub-period, chained geometrically: `TWR_cum = Π(1+r_i)-1`, `r_i = (E - B - C) / (B + W*C)` where `W = (CD-D)/(CD-SD+1)` is standard, but we use IB’s daily segmentation (next §4.2) so `W` cancels and `r_i = E/B -1` when no mid-day flow — standard daily TWR.
- **External flows are first-class:** Deposits, withdrawals, ACATS (securities transfers valued at NAV change), internal transfers, all as `C`. Fees/borrow are *not* flows (they are returns), dividends are returns (not flows).
- **Never synthesize trades.** No `2025-12-31` phantom. If Flex history doesn’t cover inception, performance starts at first NAV date, not Jan 1.
- **Explicit `N` and `Rf`:** No metric shown under `N<20`; `Rf` is FRED DGS3MO daily series, not 0.

### 4.2 Data Model (source of truth)

```sql
-- Per-account, per-day, from Flex
CREATE TABLE nav_snapshots (
  account_id TEXT,        -- U123456, or 'ALL' for consolidated
  report_date TEXT,       -- YYYY-MM-DD ET
  total_net_liq REAL,     -- EquitySummaryByReportDateInBase.total
  cash REAL,
  stock REAL,
  options REAL,
  accrued_fees REAL,
  PRIMARY KEY (account_id, report_date)
);
CREATE TABLE external_flows (
  account_id TEXT,
  report_date TEXT,
  amount REAL,            -- +deposit, -withdrawal, +ACATS NAV change. One row per flow type, summed
  flow_type TEXT,         -- deposit|withdrawal|acats|internal
  note TEXT,
  PRIMARY KEY (account_id, report_date, flow_type)
);
CREATE TABLE twr_subperiods (
  account_id TEXT,
  report_date TEXT,
  b REAL,                 -- beginning NAV (total_net_liq_{t-1})
  e REAL,                 -- ending NAV (total_net_liq_t)
  c REAL,                 -- sum external flows on date t (from external_flows)
  r REAL,                 -- (E - B - C)/B, or E/B-1 if C==0
  cum_r REAL,             -- chained cum through t
  PRIMARY KEY (account_id, report_date)
);
```

- **Flex queries required (two):**
  1. `EquitySummaryInBase` by `reportDate` (already `IB_FLEX_NAV_QUERY_ID`) — daily NAV.
  2. `CashTransactions` + `Transfers` (new `IB_FLEX_FLOWS_QUERY_ID`) — `type IN (Deposits, Withdrawals)` and `Transfer` with `cashTransfer`/`securityTransfer` to populate `external_flows`. ACATS detection: `Transfer` where `assetCategory` IS NULL and no `executed_order` fill that day → treat entire NAV change as `C` (same heuristic but now explicit and auditable, not hidden in code).

No portal Highcharts scrape. No UW/Yahoo. No `ib_twr_series.json`. IB Flex TWR (if IB ever adds it natively) would be ingested as `r` directly, but we compute it ourselves from `(B,E,C)`.

### 4.3 Build

```
Flex NAV + Flows → nav_snapshots + external_flows → twr_subperiods → performance_snapshots.payload
```

1. **Fetch** both Flex reports (poll `SendRequest`/`GetStatement` up to 30×3s, same as today, but with typed `xml.etree` parsing and hard fail if `ReferenceCode` missing — no silent `None`).
2. **Normalize dates** via ET conversion (`_last_sync_to_et_date` logic, but centralized in `utils/et_date.py`, tested with `zoneinfo`).
3. **Cash-flow classification** — `external_flows` excludes `Borrow Fee`, `Commission`, `Dividend` (those stay in NAV as returns). Deposits/withdrawals from `CashTransactions.type`. ACATS from `Transfers` joined to `nav_snapshots` NAV delta.
4. **TWR chain**:
   ```python
   for date in sorted(nav_snapshots):
       B = prev_E if prev_E is not None else nav_snapshots[date].total
       E = nav_snapshots[date].total
       C = sum(external_flows[date])
       r = (E - B - C)/B if B != 0 else 0
       cum = (1+prev_cum)*(1+r)-1
   ```
   First `date` is `period_start = min(nav_snapshots.date)` (not Jan 1). `start` shown to user as “Since 2026-05-01 (first Flex NAV)”.
5. **Metrics** — `compute_performance_metrics()` rewritten:
   - `total_return = cum_r_last` (TWR), not `equity[-1]/equity[0]-1` when flows exist (they’re equal only if `C==0`).
   - `annualized = (1+total_return)^(252/N)-1` only if `N>=20`, else `null` and UI shows “— (needs 20 sessions)”.
   - `vol`, `sharpe`, `sortino` with `Rf = FRED DGS3MO` daily series (cached `data/risk_free_rate.json`, fallback 0 with warning “Rf unavailable — Sharpe = excess over 0”).
   - `beta`, `alpha`, `tracking_error` only if `N>=40` and `|corr|>0.2`, else `null`.
   - `VaR/CVaR` via historical `quantile`, but gate `N>=60`.
   - `maxDD`, `drawdown_duration`, `ulcer` from `equity = nav_snapshots.total` (already TWR-consistent).
6. **Benchmark** — single SPY series from `clients/yahoo_client.py` (one source, no IB/UW fan-out), calendar = `twr_subperiods.report_date` (not benchmark history). `benchmark_total_return = Π(1+bm_daily)-1` over same `N`. `benchmark_series` aligned by `date`, not `pct_change` intersection hack.
7. **Reconstructed fallback removed.** If `nav_snapshots` has `<2` rows, `build_payload()` returns `status: insufficient_data` with `warnings: ["No Flex NAV — add IB_FLEX_NAV_QUERY_ID"]` and UI shows empty state: “Performance needs Flex NAV. Add the EquitySummary report in IB Portal.” No UW/Yahoo, no thread pool.

### 4.4 API & Frontend

- `POST /performance` → builder writes `performance_snapshots(taken_at, payload)` where `payload` is exactly the JSON `web` expects, but `methodology` now honest:
  ```json
  { "curve_type": "twr_modified_dietz_daily", "return_basis": "time_weighted", "risk_free_rate": 0.0412, "library_strategy": "fred_dgs3mo" }
  ```
- `GET /api/performance` same cache logic but `CACHE_TTL_MS = 5*60_000` during `MarketState.OPEN`, `60*60_000` when `CLOSED`; `shouldRebuild` only when `isPerformanceBehindPortfolioSync` true *and* `twr_subperiods` newest `report_date` < `nav_snapshots` newest. No 35s `POST /portfolio/sync` block — that sync is independent (already handled by `WorkspaceShell`).
- `web/components/PerformancePanel.tsx` — card grid reduced:
  - Always: `TWR Total`, `Annualized TWR`, `Max DD`, `Sharpe (vs T-bill)`, `Equity curve`
  - Secondary (behind “Advanced”): `MWR (IRR)`, `Beta`, `Alpha`, `Tracking Error`, `VaR/CVaR` — each with `N` gate and `—` when insufficient.
  - Warnings banner surfaces `external_flows` note: “Includes $250k deposit on 2026-03-15 — TWR excludes it.”
  - Empty state (no NAV) is a measurement description per Brand Rules, not a placeholder.

### 4.5 Tests (must pass before un-hiding)

- **Python:** `tests/test_portfolio_performance.py` — 20 cases: zero flows, deposit, withdrawal, ACATS (+50k / -50k), split (NVDA 10:1), dividend, inception 2026-05-01, 3-day N gate, 40-day beta gate, 60-day VaR gate, multi-account internal transfer, hard-coded date not present, Flex missing → `insufficient_data`, holiday calendar (2026-01-01), borrow fee not counted as flow, `Rf` fallback.
  - Golden file: `tests/fixtures/perf_nav_tw_no_flows.json` → `total_return = 0.123456` exactly, to 1e-8.
- **TS:** `web/tests/performance-twr.test.ts` — asserts `curve_type === "twr_modified_dietz_daily"`, `risk_free_rate > 0`, `N<20 → annualized === null`, `isPerformanceBehindPortfolioSync` with mocked `portfolioLastSync`.

### 4.6 Rollout

1. Hide stays (today).
2. Ship builder + migration (`scripts/migrate_perf_twr.py` backfills `nav_snapshots`/`external_flows` from existing Flex XML dumps in `data/flex_cache/`).
3. Run builder on Hetzner cron `0 6 * * * America/New_York` (after Flex settles) and on-demand `POST /performance`. Verify `payload.summary.total_return` matches `ib_twr_series.json` last cum within 1e-6 for a no-flow window.
4. Ship `web` changes behind `NEXT_PUBLIC_PERF_TWR=1` flag, default off, until 2 asserts green.
5. Un-hide: set `hidden:false` in `web/lib/data.ts:44`, add `Performance` to `NavGroupId="performance"` (new group) or keep in `positions`, bump `SW_VERSION`, deploy.

### 4.7 Not Doing

- No per-trade reconstruction, no `price_cache`, no `ThreadPoolExecutor`, no `seed_marks`, no `phantom 2025-12-31`, no portal scrape, no Yahoo.
- No 30-metric dump for N=5 accounts. Metrics behind gates, not zeros.

---

## 5. Concrete File Changes

- **Delete:** `scripts/portfolio_performance.py` TWR-scrape block (lines 430–520), entire `reconstruct_equity_curve` + `_fetch_all_histories` fallback (≈400 lines), `scripts/performance_explainer_report.py` (if unused).
- **Add:** `scripts/perf_twr_builder.py` (new builder), `scripts/clients/fred_client.py` (FRED DGS3MO), `scripts/utils/et_date.py` (shared ET conversion), `data/flex_cache/README.md`.
- **Modify:** `web/lib/data.ts` (un-hide when ready), `web/app/api/performance/route.ts` (TTL + no portfolio-sync block), `web/components/PerformancePanel.tsx` (gated cards + empty state), `web/lib/performanceFreshness.ts` (no `as_of` drift — compare `twr_subperiods.max(report_date)`).
- **Tests:** Replace golden, add gates.

---

## 6. Risks & Mitigations

- **Flex late:** T+1 settlement. Show `as_of` = last `report_date` and `next update ~06:00 ET`. Do not fabricate today’s return from intraday marks.
- **ACATS mis-class:** Flow classification log kept in `external_flows.note`; UI “Flows” drill-down lets user correct (update `flow_type` from `acats` to `deposit`).
- **Multi-account:** Require `IB_FLEX_FLOWS_QUERY_ID` per consolidated report; if separate tokens, run builder per `account_id` then sum `C` for `ALL`.

---

*End plan. Implement behind flag; hide stays until `total_return` is TWR and `risk_free_rate` is not 0.*
