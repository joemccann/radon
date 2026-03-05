# Status & Decision Log

## Last Updated
2026-03-05T12:00:00-08:00

## Recent Commits
- 2026-03-05 12:00:00 -0800 — **feat: GARCH Convergence scanner — parallel fetch (8 workers, ~2.6s for 23 tickers), divergence analysis, HTML report**
- 2026-03-05 11:47:00 -0800 — Route evaluate command to python script: explicit instructions in AGENTS.md and plans.md
- 2026-03-05 11:22:00 -0800 — **feat: Improved RatingsTab — maps nested UW API response, shows buy/sell %, distribution bar, price targets with upside/downside, analyst actions with firm/grade**
- 2026-03-05 11:20:00 -0800 — **feat: News tab fallback to Yahoo Finance when UW is rate-limited (429)**
- 2026-03-05 11:18:00 -0800 — evaluate.py: Unified evaluation script with parallel milestones, 34 TDD tests
- 2026-03-05 11:15:00 -0800 — docs: Add data normalization rules, update status with recent fixes
- 2026-03-05 11:10:00 -0800 — Fix discover.py KeyError on watchlist entries with 'symbol' instead of 'ticker'
- 2026-03-05 11:05:00 -0800 — **Fix: open orders visible in ticker detail modal on ALL pages (not just /orders)**
- 2026-03-05 09:59:00 -0800 — docs: Add Calculations correctness rules section to CLAUDE.md
- 2026-03-05 09:53:00 -0800 — **Fix Day Chg % for options: use close value, not entry cost (TDD: 5 tests)**
- 2026-03-05 09:22:00 -0800 — **Compute spread net mid for BAG orders from portfolio leg prices**
- 2026-03-05 09:16:00 -0800 — Show --- for last price on spread/combo orders instead of underlying
- 2026-03-05 08:59:00 -0800 — Fix order form BID/MID/ASK buttons to use option-level prices
- 2026-03-05 08:40:00 -0800 — **Open orders modify/cancel in ticker detail, option-level pricing, news link icon**
- 2026-03-05 07:37:00 -0800 — **Ticker detail modal with position, order, news, and ratings tabs**
- 2026-03-05 07:27:00 -0800 — **Historical trades table on /orders page with pagination**
- 2026-03-04 15:45:00 -0800 — **OI Change Analysis: Made REQUIRED in every evaluation workflow**
- 2026-03-04 15:30:00 -0800 — Created fetch_oi_changes.py and verify_options_oi.py scripts
- 2026-03-04 15:15:00 -0800 — Discovered UW has OI change endpoint that shows hidden institutional positioning
- 2026-03-04 15:00:00 -0800 — Verified $95M MSFT LEAP call purchase via OI data
- 2026-03-04 13:15:00 -0800 — Added Synthetic Long/Short detection to IB sync and free trade analyzer
- 2026-03-04 13:05:00 -0800 — Startup protocol: IB sync runs before free trade analysis
- 2026-03-04 10:30:00 -0800 — Startup protocol: Batch all notifications into single multi-line message
- 2026-03-04 10:15:00 -0800 — Startup protocol: Show all processes with numbered progress (TDD)
- 2026-03-04 07:30:00 -0800 — Created exit_order_service.py and launchd integration
- 2026-03-04 07:20:00 -0800 — Created trade-specification-template.html
- 2026-03-04 07:10:00 -0800 — Placed GOOG stop loss order #6 (trigger $3.00)
- 2026-03-04 07:05:00 -0800 — Added GOOG bull call spread to trade log (Trade #8)
- 2026-03-04 07:00:00 -0800 — Created ib_fill_monitor.py and ib-order-execution skill

## Current Portfolio State
- **Net Liquidation**: $1,201,929
- **Deployed**: $1,267,897 (105% — on margin)
- **Open Positions**: 23
- **Defined Risk**: 13 positions
- **Undefined Risk**: 10 positions (7 stocks + 2 risk reversals + 1 synthetic)
- **Today's Realized P&L**: +$53,082

## Today's Trades (2026-03-04)
| Trade | Structure | P&L | Status |
|-------|-----------|-----|--------|
| GOOG | Bull Call Spread $315/$340 | -$1,040 (open) | ✓ FILLED @ $6.26 |
| PLTR | Sold 100 Calls @ $9.18 | +$48,480 | ✓ CLOSED |
| EWY | Closed Mar 6 Risk Reversal | +$9,888 | ✓ CLOSED |
| BRZE | Closed Options Structure | -$11,287 | ✓ CLOSED (loss) |
| AAOI | Bought back $90 Put @ $1.25 | +$6,001 | ✓ FREED (call now free) |
| **Net P&L Today** | | **+$53,082** | |

## Positions Requiring Attention

### ⚠️ Expiring This Week (Mar 6)
| Position | Structure | P&L | Risk |
|----------|-----------|-----|------|
| AAOI | Long Call $105 (freed) | +$6,001 from put | ✅ DEFINED |

### ✅ Closed Today
| Position | Structure | P&L | Notes |
|----------|-----------|-----|-------|
| EWY | Risk Reversal P$128/C$138 (Mar 6) | +$9,888 | Closed before expiry |

### ⚠️ Expiring in 2-3 Weeks
| Position | DTE | P&L | Action |
|----------|-----|-----|--------|
| BRZE Long Call $22.5 | 17 | -44% | Approaching stop |
| IGV Long Call $93 | 17 | -70% | Below stop |
| PLTR Long Call $145 | 24 | +116% | Consider profits |

### ⛔ Rule Violations (Logged for Audit)
| Position | Violation | Opened | Status |
|----------|-----------|--------|--------|
| ~~AAOI Risk Reversal~~ | ~~Undefined risk (short put)~~ | 2026-03-03 | ✅ RESOLVED (short put closed) |
| ~~EWY Risk Reversal (Mar 6)~~ | ~~Undefined risk (short put)~~ | 2026-03-03 | ✅ RESOLVED (closed for +$9,888) |
| EWY Risk Reversal (Mar 13) | Undefined risk (short put $130) | 2026-03-04 | ⚠️ ACTIVE |
| IGV Synthetic Long | Undefined risk (short put $90) | Active | ⚠️ ACTIVE |
| AMD Long Call | Position size 7.4% (exceeds 2.5% cap) | 2026-03-03 | ⚠️ ACTIVE |

---

## Trade Log Summary
| ID | Date | Ticker | Structure | Status | P&L |
|----|------|--------|-----------|--------|-----|
| 1 | 03-02 | ALAB | Long Call LEAP | OPEN | -8.5% |
| 2 | 03-02 | WULF | Long Call LEAP | OPEN | -5.4% |
| 3 | 02-25 | EWY | Bear Put Spread | **CLOSED** | +$17,651 |
| 4 | 03-03 | AAOI | Risk Reversal | **CONVERTED** | → Trade #13 |
| 5 | 03-03 | AMD | Long Call LEAP | OPEN | +7.5% |
| 6 | 03-03 | EWY | Risk Reversal (Mar 6) | **CLOSED** | +$9,888 |
| 7 | 02-27 | AAOI | Long Stock | **CLOSED** | +$380 |
| 8 | 03-04 | GOOG | Bull Call Spread $315/$340 | OPEN | -3.8% |
| 9 | 03-04 | NFLX | Stock Sale | **CLOSED** | $444,150 proceeds |
| **10** | **03-04** | **PLTR** | **Sold Long Calls** | **CLOSED** | **+$48,480** |
| **11** | **03-04** | **EWY** | **Closed Mar 6 RR** | **CLOSED** | **+$9,888** |
| **12** | **03-04** | **BRZE** | **Closed Structure** | **CLOSED** | **-$11,287** |
| **13** | **03-04** | **AAOI** | **Risk Reversal → Free Call** | **FREED** | **+$6,001** |

---

## Logged Position Thesis Check

### ALAB — Long Call $120 (Jan 2027)
- **Entry**: 03-02 @ $36.90 | **Current**: $32.66 (-11.5%)
- **Edge**: IV mispricing (+43.6% gap vs HV20)
- **Flow at Entry**: NEUTRAL (50.3% buy)
- **Flow Now**: NEUTRAL (49.3% buy) — unchanged
- **Thesis**: ✅ INTACT — Hold for IV normalization

### WULF — Long Call $17 (Jan 2027)
- **Entry**: 03-02 @ $5.20 | **Current**: $4.25 (-18.3%)
- **Edge**: IV mispricing + Flow confluence
- **Flow at Entry**: ACCUMULATION (59% buy)
- **Flow Now**: ACCUMULATION (56.3% buy) — still confirmed
- **Thesis**: ✅ INTACT — Flow still accumulation, hold

### AMD — Long Call LEAP (Position #5)
- **Entry**: 03-03 | **Current**: +7.5%
- **Edge**: IV mispricing (HV20 85.9% vs LEAP IV ~60%)
- **Flow at Entry**: ACCUMULATION (Feb 27 peak 91.8% buy)
- **Flow Now**: NEUTRAL (Mar 2 reverted to 45% buy)
- **Options Flow**: LEAN_BEARISH (P/C 1.49x)
- **Thesis**: ⚠️ WEAKENING — Accumulation cycle appears complete. Position size 7.4% violates 2.5% cap. Monitor closely for further deterioration.

### GOOG — Bull Call Spread $315/$340 (Trade #8) ✨ NEW
- **Entry**: 03-04 @ $6.26 net debit | **Current**: $6.15 (-1.9%)
- **Structure**: 44 contracts, Apr 17 expiry (43 DTE)
- **Edge**: EXTRAORDINARY dark pool accumulation
  - 94.87% buy ratio (5-day sustained)
  - 89.7 flow strength (threshold: 50)
  - $6.67B in dark pool premium
  - Feb 27 surge: 98.8% buy, $3.52B single day
- **Options Flow**: BULLISH (P/C 0.30, HIGH confidence)
- **Context**: Seasonality FAVORABLE (64% March), Analysts 86.6% Buy, $359 PT
- **Kelly**: 2.46% of bankroll (within 2.5% cap)
- **R:R**: 3.0:1 (max gain $82,456 / max risk $27,544)
- **Thesis**: ✅ STRONG — First fully-compliant trade from standard evaluation. All three gates passed. Highest signal score on watchlist (129.7).

---

## Recent Evaluations

### AAPL (Apple Inc.) - 2026-03-05 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-05 11:00 AM PT (LIVE — all milestones fetched fresh)
- **Reason**: Accumulation cycle FADED. Feb 27 monster print (99.3%, $3.17B) was genuine but has not sustained:
  - Mar 3: DISTRIBUTION (25.6%) — broke accumulation streak
  - Mar 4: Weak accumulation (74.2%, strength 48.4)
  - **Mar 5 (TODAY): NEUTRAL (55.2%, strength 10.5)** — signal fading
  - Sustained consecutive days = **0** (need 3+)
  - Recent strength = 10.5 (need >70 for alternative criterion)
- **What passed**: Aggregate strength 62.7 (>50), $11.8M MASSIVE OI at Apr $270C, chain LEAN_BULLISH (P/C 0.53x), seasonality FAVORABLE (65%), analysts 75.9% Buy
- **What failed**: No sustained direction from most recent day. Recent flow alerts turning BEARISH. Accumulation cycle appears completed — institutional buying peaked Feb 27 and is winding down.
- **Critical lesson**: Without today's DP data (Mar 5), this would have PASSED edge. Fetching fresh data prevented a trade against a fading signal.
- **Seasonality**: FAVORABLE (March 65% win rate, +3.8% avg)
- **Analyst Ratings**: 29 analysts, 75.9% Buy, $288 target (+9.6%)
- **Ticker Verified**: YES — Apple Inc., Technology, $3.85T market cap
- **Action**: Add to WATCH. Re-evaluate if sustained accumulation resumes (3+ consecutive days).

### GOOG - 2026-03-04 ✅ EXECUTED
- **Decision**: TRADE
- **Structure**: Bull Call Spread $315/$340 (44 contracts)
- **Fill**: $6.26 net debit ($27,544 total)
- **Gates**: All three passed (Convexity 3.0:1, Edge 89.7 strength, Risk 2.46%)
- **Thesis**: Extraordinary institutional accumulation confirmed by bullish options flow

### AMD - 2026-03-03 (LEAP IV Scan Follow-up)
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE
- **Reason**: IV mispricing confirmed (HV20 85.9% vs LEAP IV ~60%, +27% gap). However, dark pool accumulation cycle appears COMPLETED — Feb 24 distribution → Feb 26-27 strong accumulation → Mar 2 reverted to neutral. Aggregate strength only 19.5 (need >50). Options flow LEAN_BEARISH (P/C 1.49x) with put buying. Price already rallied from ~$170 to ~$198 during accumulation window.
- **Seasonality**: NEUTRAL (March 50% win rate)
- **Ticker Verified**: YES
- **Note**: Existing AMD LEAP position already in portfolio (see trade #5). Current flow suggests edge has faded — monitor for position review.

### APO (Apollo Global Management) - 2026-03-05
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE
- **Reason**: Dark pool aggregate strength 10.3 (need >50). 3 of 5 days NEUTRAL. 0 consecutive days sustained direction. Options flow is BEARISH (P/C 2.05x, $8.2M put premium, sweep activity on Jun $105 puts), but dark pool does NOT confirm distribution — flow is neutral, not selling. Without DP confirmation, put buying could be hedging, not directional. OI changes all MODERATE (<$1M per position, $3.08M total). No massive institutional positioning in either direction.
- **Seasonality**: UNFAVORABLE (March 29% win rate, -4.2% avg return)
- **Analyst Ratings**: Unavailable (rate limited)
- **Ticker Verified**: YES — Apollo Global Management, Financial Services, $63.5B market cap

### RMBS - 2026-03-03
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE
- **Reason**: Alternating accumulation/distribution pattern. Aggregate strength 42.0 (need >50). Only 1 day of recent accumulation.
- **Seasonality**: FAVORABLE (March 65% win rate)
- **Ticker Verified**: YES

### TSLA - 2026-03-03
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE
- **Reason**: Accumulation cycle appears completed. 3 days accumulation followed by neutral reversal. Aggregate strength only 20.2.
- **Seasonality**: UNFAVORABLE (March 47% win rate)
- **Ticker Verified**: YES

### MSFT - 2026-03-04 ✅ PENDING EXECUTION
- **Decision**: TRADE (pending user confirmation)
- **Structure**: Long LEAP Call $575 (Jan 2027)
- **Edge**: VERIFIED via OI change analysis
  - $95M institutional LEAP call purchase confirmed
  - $625 Call: +100,458 OI, $51M premium
  - $575 Call: +50,443 OI, $45M premium
  - $675 Call: +50,148 OI, $15M premium (short leg)
- **Flow Alerts**: Did NOT show this — discovered via OI change endpoint
- **Dark Pool**: 97-98% buy ratio Mar 2-3, strength 94-96
- **Technical**: 14-week RSI at lowest since 2008
- **Kelly**: 12.6% optimal → 2.5% allocation ($29,915)
- **R:R**: 5.7:1
- **Note**: This evaluation led to discovery of OI change methodology

### MSFT - 2026-02-28
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE
- **Reason**: 4 days accumulation followed by massive Friday distribution (0.8% buy ratio). Pattern = completed round-trip.
- **Ticker Verified**: YES

---

## Infrastructure

### Startup Protocol
The Pi startup extension (`.pi/extensions/startup-protocol.ts`) automatically runs checks with **full visibility**:

**Output Format (two-phase notification):**
```
🚀 Startup: Running 4 checks...     <- IMMEDIATE on startup
```
Then when async tasks complete:
```
[1/4] ✓ Loaded: Spec, Plans, Runbook, Status, Context Engineering
[2/4] ✓ IB trades in sync
[3/4] ✓ Monitor daemon running
[4/4] ✓ No free trade opportunities
✅ Startup complete (4/4 passed)
```

**⚠️ Auto-Reconciliation Rule:**
When startup shows `⚠️ IB: N new trades`, **IMMEDIATELY**:
1. Read `data/reconciliation.json`
2. Log each trade to `data/trade_log.json`
3. Update `docs/status.md`
4. Clear reconciliation flag
5. Validate JSON

**Do NOT wait for user prompt — this is automatic.**

**Notification Strategy:**
- **Immediate**: Show check count as soon as Pi starts
- **Deferred**: Progress messages collected during async execution
- **Final**: Single batched notification with all results when complete

**Processes tracked (in order):**
1. **docs** — Load project docs + always-on skills (sync)
2. **ib** — IB reconciliation (async, runs first)
3. **free_trade** — Free trade scan (async, waits for IB to complete)
4. **daemon** — Monitor daemon status check (sync)
5. **x_{account}** — X account scans (async, parallel)

**Status indicators:**
- `✓` success — Process completed normally
- `⚠️` warning — Process skipped or has issues (triggers auto-reconciliation if IB)
- `❌` error — Process failed

**Implementation:** Uses `StartupTracker` class with TDD (14 tests)

### IB Reconciliation (Auto-Log)
- Script: `scripts/ib_reconcile.py`
- Runs at Pi startup (non-blocking)
- Detects new trades, new positions, closed positions
- Output: `data/reconciliation.json`
- **Auto-Log**: When `needs_attention: true`, immediately:
  1. Add trades to `trade_log.json`
  2. Update `docs/status.md`
  3. Clear `needs_attention` flag
- **No user prompt required** — this is automatic

### Data Files
| File | Purpose |
|------|---------|
| `data/trade_log.json` | Executed trades (13 entries) |
| `data/portfolio.json` | Open positions from IB |
| `data/reconciliation.json` | IB sync discrepancies |
| `data/watchlist.json` | Tickers under surveillance |
| `data/discover.json` | Cached discover scan results (auto-refreshed) |

### Ticker Detail Modal (NEW)
Click any ticker across all 6 table sections → 720px modal with:
- **Price Bar**: Real-time bid/ask/mid/spread/last/volume/day-change. Option positions show option-level prices (not underlying). Spreads show net mid computed from leg prices.
- **Position Tab**: Structure, direction, qty, entry date, avg entry, last price, entry cost, market value, unrealized P&L, expiry, target/stop. Multi-leg positions show individual legs.
- **Order Tab**: If open orders exist → inline modify/cancel (reuses `useOrderActions`). Below that, new order form with BUY/SELL, qty, limit price with BID/MID/ASK quick-set (option-level), TIF, 2-step confirm. Combo positions show "close via Orders page" message.
- **News Tab**: UW headlines with date, source, MAJOR badge, external link icon.
- **Ratings Tab**: Analyst recommendation, ratings bar, price targets, recent changes (via `fetch_analyst_ratings.py`).
- **Context**: `TickerDetailContext` provides `openTicker()`/`closeTicker()` app-wide. Prices, portfolio, orders synced via refs (no re-renders).
- **API Routes**: `/api/ticker/news` (UW proxy), `/api/ticker/ratings` (Python script), `/api/orders/place` (IB order placement via `ib_place_order.py`).

### Key Scripts
| Script | Purpose |
|--------|---------|
| `clients/ib_client.py` | **IBClient** — Primary IB API client |
| `clients/uw_client.py` | **UWClient** — Primary UW API client |
| `fetch_oi_changes.py` | **⭐ OI change analysis (REQUIRED in every eval)** |
| `verify_options_oi.py` | Verify specific OI claims |
| `ib_reconcile.py` | Startup reconciliation (async) |
| `ib_sync.py` | Manual portfolio sync |
| `ib_order.py` | Place single-leg option orders |
| `ib_place_order.py` | **JSON-in/JSON-out order placement for web API** |
| `ib_fill_monitor.py` | Monitor orders for fills |
| `exit_order_service.py` | Place pending exit orders |
| `blotter.py` | Today's fills and P&L |
| `trade_blotter/flex_query.py` | Historical trades (365 days) |
| `garch_convergence.py` | **⭐ GARCH Convergence scanner (parallel, ~3s for 23 tickers)** |

### Skills
| Skill | Purpose |
|-------|---------|
| `ib-order-execution` | Order placement and fill monitoring |
| `html-report` | Trade specification + P&L templates |

### Services
| Service | Status | Description |
|---------|--------|-------------|
| Exit Order Service | 🟢 Installing | Places pending target orders when IB accepts |
| IB Reconciliation | 🟢 Active | Runs at Pi startup |

### Templates
| Template | Purpose |
|----------|---------|
| `trade-specification-template.html` | Full evaluation report (NEW) |
| `pnl-template.html` | P&L reconciliation report |

---

## Known Issues
1. ~~`fetch_ticker.py` rate-limited~~ **FIXED** — Uses UW dark pool API
2. ~~`fetch_options.py` placeholder data~~ **FIXED** — Uses UW chain + flow
3. ~~Options no real-time prices~~ **FIXED** — IB realtime server supports options
4. Flex Query sometimes times out on IB server side (retry usually works)
5. ~~`ib_order_manage.py modify` Error 103~~ **FIXED** — Reconnects as original clientId before placeOrder
6. ~~`ib_order_manage.py cancel` Error 10147~~ **FIXED** — Same clientId mismatch as modify; cancel now reconnects as original placer
7. ~~Options showing $-1.00 after hours~~ **FIXED** — IB returns -1 sentinel for LAST tick when market closed; normalizeNumber() now rejects negatives, reqMarketDataType(4) requests frozen data
8. ~~Day Chg % -206% for PLTR spread~~ **FIXED** — `getOptionDailyChg()` was dividing daily P&L by entry cost instead of yesterday's close value. Spread entry $0.52 but close $8.50 → 16x inflation. See CLAUDE.md "Calculations" section.
9. ~~Spread orders showing underlying stock price~~ **FIXED** — BAG orders now compute net mid from portfolio legs (`resolveOrderLastPrice()`). Order form BID/MID/ASK buttons now use option-level prices via `tickerPriceData` prop.
10. ~~Ticker detail modal not showing open orders on non-orders pages~~ **FIXED** — `useOrders()` now always reads cached orders on mount; IB auto-sync still only on /orders page.
11. ~~discover.py crash: KeyError 'ticker' in watchlist~~ **FIXED** — `get_existing_tickers()` handles both `ticker` and `symbol` keys. Normalize all watchlist entries to `ticker`.
12. ~~News tab fails silently when UW rate-limited~~ **FIXED** — `/api/ticker/news` route now falls back to Yahoo Finance RSS when UW returns 429. Both sources handle failure gracefully with user-facing error message.
13. ~~RatingsTab shows raw/empty data from UW API~~ **FIXED** — Complete rewrite to properly map nested UW response structure (`ratings.buy`, `target_price.mean`, `upgrade_downgrade_history`). Now shows buy/sell percentages, visual distribution bar, price targets with upside/downside calculation, and analyst actions table with firm name and grade.
14. **Data normalization rule**: All data files (watchlist.json, discover.json) must use `"ticker"` as the canonical key, never `"symbol"`. Scripts that read these files handle both for backward compatibility.

## Follow-ups
- [x] Implement trade blotter service
- [x] Set up Flex Query for historical trades
- [x] Create P&L report template
- [x] Add startup reconciliation
- [x] Create ib_fill_monitor.py script
- [x] Create ib-order-execution skill
- [x] Execute first fully-compliant trade (GOOG)
- [x] Create trade-specification-template.html
- [x] Place GOOG stop loss order
- [x] Create exit_order_service.py
- [x] Install exit order service (launchd)
- [x] **Create OI change analysis scripts (fetch_oi_changes.py, verify_options_oi.py)**
- [x] **Make OI analysis REQUIRED in every evaluation workflow**
- [x] **Document OI verification methodology**
- [x] **Journal route renders trade log table (date, ticker, structure, P&L, gates, edge)**
- [x] **Discover route with auto-sync (5-min interval) + server startup pre-warm**
- [x] **ChatPanel removed from non-dashboard routes**
- [x] **Ticker detail modal — position, order (with modify/cancel), news, ratings**
- [x] **Option-level pricing in price bar and order form BID/MID/ASK**
- [x] **Spread net mid computation for BAG orders (long leg mid - short leg mid)**
- [x] **ib_place_order.py — JSON-in/JSON-out order placement for web API**
- [x] **Fix Day Chg % for options/spreads (divide by close value, not entry cost)**
- [x] **Fix spread last price (net mid from leg prices, not underlying)**
- [x] **Ticker detail modal works identically on all pages (portfolio, orders, discover, journal, flow)**
- [x] **useOrders() always loads cached orders on mount for cross-page ticker detail**
- [x] **Fix discover.py watchlist KeyError (normalize ticker/symbol key)**
- [x] **News tab UW → Yahoo Finance fallback on 429 rate limit**
- [x] **RatingsTab rewrite: nested API mapping, buy/sell %, distribution bar, price targets, analyst actions**
- [x] **All-sources-failed graceful handling in News and Ratings tabs**
- [x] **Data normalization: canonical "ticker" key in all data files**
- [x] **evaluate.py unified evaluation script with 34 TDD tests**
- [x] **garch_convergence.py — parallel GARCH convergence scanner (8 workers, built-in + file presets, HTML report)**
- [ ] Execute MSFT LEAP call trade (pending confirmation)
- [ ] Close undefined risk positions before Friday expiry
- [ ] Review PLTR for profit-taking (23 DTE, +175%)
- [ ] Review IGV/SOFI for stop-loss exit
- [ ] GOOG target order — place when spread reaches ~$9.23
