# Performance Page — Reconstruction Approaches (2026-03-21)

## Problem

Match IB's YTD TWR (Time-Weighted Return) on the Radon `/performance` page. IB shows **-10.56% YTD TWR** as of 2026-03-21. The account grew from $99K to $1.05M due to $225K in cash deposits and $961K in ACATS securities transfers (stocks + options from another brokerage).

## What IB Provides via API

| Source | Data | Automated? |
|--------|------|-----------|
| Flex Query: EquitySummaryInBase (ID: 1442520) | Daily NAV snapshots (58 YTD points) | Yes |
| Flex Query: CashTransactions | Cash deposits/withdrawals ($225K total) | Yes |
| Flex Query: Transfers | ACATS securities transfers ($961K positionAmount) | Yes |
| Flex Query: Trade History (ID: 1422766) | 630 trade executions (Oct 2025 – Mar 2026) | Yes |
| Client Portal Highcharts | Exact TWR series (58 points, -10.56%) | No — requires authenticated browser session |
| TWS API (ib_insync) | Current account values, positions, daily P&L | Yes — but no historical TWR |

## Approaches Tried

### 1. Reconstructed equity curve from trade fills (original)
**Result: -27% → +23% (depending on data completeness)**

Methodology: Back-calculate starting equity from `initial_cash = final_equity - total_net_cash - final_holdings_value`, walk calendar day-by-day marking positions.

Problems:
- Blotter was stale (Feb 4 – Mar 4, missing 15 days of trades)
- 16 option contracts missing price history (UW rate-limited, valued at $0)
- No deposit/withdrawal tracking — deposits inflated starting equity
- Positions opened before blotter coverage appeared as phantom shorts
- Starting equity wildly wrong ($1.58M vs real $99K)

Fixes applied:
- Updated blotter via Flex Query (630 executions)
- Added execution-price seeding for missing option marks
- Added portfolio final-date mark injection from IB live prices
- Added synthetic opening trades for pre-blotter positions
- Portfolio position reconciliation

**Verdict: Fundamentally unreliable.** Too many compounding assumptions. Starting equity calculation is fragile. Deposits/withdrawals/ACATS make the back-calculation meaningless.

### 2. IB daily NAV (Flex EquitySummaryInBase) with deposit-adjusted TWR
**Result: -17% to -33% depending on ACATS timing assumption**

Methodology: Use IB's daily NAV as equity curve. Detect deposits from Cash Transactions and ACATS from Transfers. Compute TWR using Modified Dietz formula with various timing weights.

Problems:
- ACATS `positionAmount` ($961K) doesn't match NAV change ($725K) — difference is margin/cash rebalancing
- Modified Dietz timing (start/mid/end of day) gives -33% to -96% with ACATS positionAmount
- Using NAV change instead of ACATS amount gives -17%
- No timing weight produces -10.56% (IB's actual TWR)
- IB uses a proprietary intraday TWR algorithm we can't replicate

Weight sweep results (ACATS positionAmount as CF):
- weight=0.0 (end of day): -96%
- weight=0.5 (mid-day): -45%
- weight=1.0 (start of day): -33%

With NAV change ($725K) as CF instead:
- Start of day: -17%

**Verdict: ~6% gap from IB's TWR is irreducible without IB's exact intraday transfer timing.**

### 3. Simple NAV growth (no TWR)
**Result: +951%**

Just plot IB's daily NAV and show simple return. Honest but misleading — 90%+ of the growth is deposits/transfers, not trading.

**Verdict: Shows correct equity curve but headline % is meaningless.**

### 4. Scrape TWR from IB portal Highcharts
**Result: -10.56% (exact match)**

Extract the TWR series from IB's Client Portal Highcharts charts via CDP. Gives IB's exact 58-point TWR series.

Problems:
- Requires authenticated browser session
- Breaks when session expires
- Not automatable without Client Portal Gateway setup

**Verdict: Accurate but brittle. Not suitable for production.**

## Key Learnings

1. **IB's TWR is server-side only.** It's not available through any API — only the web portal chart.
2. **ACATS timing kills Modified Dietz.** When a securities transfer is 4x the existing portfolio, the timing assumption (start/mid/end of day) dominates the TWR result.
3. **positionAmount ≠ market value for TWR.** The Flex Transfers `positionAmount` includes cost basis adjustments, margin effects, and cross-day settlement that don't map to a simple cash flow.
4. **Daily NAV is the correct equity curve** but you need IB's proprietary TWR to get the correct % return.
5. **The equity curve chart is valuable** even without a correct % — it shows the actual account trajectory.

## Infrastructure Built (keep for future use)

| Component | File | Purpose |
|-----------|------|---------|
| NAV Flex Query | `IB_FLEX_NAV_QUERY_ID=1442520` in `.zshrc` | Daily NAV + Cash Transactions + Transfers |
| Trade Flex Query | `IB_FLEX_QUERY_ID=1422766` in `.zshrc` | 365-day trade history |
| Flex polling fix | `scripts/trade_blotter/flex_query.py` | Fixed `<FlexStatements` attribute match, `YYYYMMDD;HHMMSS` datetime, 120s timeout |
| NAV history tracking | `scripts/ib_sync.py` → `data/nav_history.jsonl` | Appends daily NAV on each portfolio sync |
| IB NAV cache | `data/nav_history_ib.json` | Cached Flex EquitySummaryInBase data |
| IB TWR cache | `data/ib_twr_series.json` | Scraped TWR from portal (58 points) |

## Recommended Future Approach

When revisiting, consider:

1. **IB Client Portal Gateway** — If set up locally (port 5000), the `/pa/performance` REST endpoint returns TWR data without browser scraping. This is the clean automated solution.
2. **Accumulate TWR from daily returns** — Going forward, if we capture accurate daily returns (from the TWR series delta), we build up the TWR organically. Only the historical period (before tracking) is approximate.
3. **Skip % return entirely** — Show equity curve + P&L dollars. The chart tells the story without a misleading number.
