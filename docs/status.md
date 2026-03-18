# Status & Decision Log

## Last Updated
2026-03-18T08:30:00-07:00

## Recent Commits
- 2026-03-18 — **fix: Share PnL entry/exit data for multi-day trades.** `positionGroupShareData()` now falls back to portfolio position data when opening fills aren't in today's executed orders (position opened on prior day). Entry price from `leg.avg_cost`, entry time from `entry_date`. Share image now correctly shows ENTRY/EXIT with PST times, no commission. 8 unit tests + 5 E2E tests.
- 2026-03-17 — **fix: Normalize open-order combo net credit by leg size for ratio spreads.** 1x2 risk-reversal entries (for example, short puts + long calls) now compute true weighted net credit in the combo last-price cell; added regression coverage in `web/tests/open-order-combos.test.ts`.
- 2026-03-17 — **ui: Update share PnL defaults and card emphasis.** X/share card rendering now scales single-value hero text larger (`$` or `%` only) while default share toggle state is now `%` only for cleaner default image output.
- 2026-03-16 — **feat: News & Catalysts milestone (M1D) — fetch_news.py classifies headlines for material catalysts (buyback, M&A, earnings, FDA, etc.) and sentiment. Integrated into evaluate.py as parallel milestone. Context-only (not a gate) but surfaces in edge determination output for operator judgment. CRM's $25B buyback now visible in evaluation.**
- 2026-03-14 — **fix: Post-bundle-optimization audit — reconnect 27 missing CSS classes, remove 4 orphaned rules, add 4 brand variables.** E2E browser verification (9 pages, dark+light themes) confirmed no visual regressions. Fixed 14 component classNames pointing to pre-rename names (e.g., `mono`→`fm`, `metric-value`→`mv`, `modal-backdrop`→`mb135`). Restored 10 accidentally deleted CSS rules. Removed 3 dead className refs. Added `--signal-deep`, `--extreme`, `--neutral`, `--line-grid` to both theme blocks. Fixed `--line-grid` which was referenced in ~15 rules but never defined.
- 2026-03-14 — **perf: Web bundle size optimization — 1124KB → 911KB raw (−19.0%), 281KB → 264KB gzip (−6.0%), 80KB → 56KB CSS (−30.0%).** 38 experiments across 4 sessions. CSS rule merging (−6KB CSS), class name shortening campaign (~300 classes renamed to 2-4 char abbreviations), orphan CSS removal, dead code cleanup, Google Fonts @import → `<link>`, template literal simplification. 30 commits squashed.
- 2026-03-13 — **feat: Ticker detail page — modal → dedicated route refactor.** Ticker detail is now a first-class page at `/{TICKER}` (e.g., `/AAPL`) with bookmarkable tabs (`?tab=chain`), browser back/forward, case normalization (`/aapl` → `/AAPL`), and 404 for invalid paths. Extracted `TickerDetailContent` from modal, created `TickerWorkspace` (URL tab sync), shared `TickerLink`, `useTickerNav` hook. Simplified `TickerDetailContext` (URL-driven). 24 unit + 8 E2E tests.
- 2026-03-13 09:31:00 -0700 — **fix: refresh `/api/performance` for the current ET session. The route now refreshes `portfolio.json` through `ibSync` before rebuilding when the cached snapshot still points at a prior ET session, and preserves the cached performance payload instead of rewriting from stale inputs if that portfolio refresh fails. Locked with Vitest route regressions.**
- 2026-03-13 09:17:01 -0700 — **fix: keep `/performance` reconstructed YTD aligned with portfolio sync. Added a shared freshness contract, made the performance panel revalidate as soon as the shell portfolio sync advances, and locked the handoff with Vitest plus Playwright regressions.**
- 2026-03-12 10:45:00 -0700 — **fix: tighten `/regime` operator telemetry. Added a shared responsive strip renderer (`5-up -> 3x2 -> stacked telemetry rail`), stacked the CRI detail panels earlier on narrow widths, and made `NORMALIZED DIVERGENCE` actionable plus hoverable. Locked with Vitest and Playwright coverage.**
- 2026-03-12 08:43:57 -0700 — **fix: unify quote telemetry across ticker, instrument, and modify-order views. Shared calculator/renderer now show `BID`, `MID`, `ASK`, `SPREAD`, with spread rendered as raw quote width plus midpoint percent. Locked with unit and Playwright regressions.**
- 2026-03-12 07:14:00 -0700 — **fix: All-long combo positions (e.g., AAOI 2x long calls) classified as "complex" → silently dropped from web UI. Fix: ib_sync.py classifies all-long combos as "defined"; UI fallback includes "complex" in undefined bucket. 13 new tests.**
- 2026-03-11 13:00:00 -0400 — **feat: High-throughput optimization — parallel scanning (15 workers), atomic state (SHA-256), batched WS (100ms flush), vectorized Kelly+Greeks (NumPy), resilient IB client (auto-reconnect + sub recovery), pacing/invalid contract handling. 96 new tests.**
- 2026-03-11 12:15:00 -0400 — **fix: Rebuild `/regime` RVOL history from CRI cache, prefer richer CRI artifacts, and refresh post-close CRI caches atomically**
- 2026-03-11 11:00:00 -0400 — **feat: Regime strip day change arrows, 20-session dual charts, portfolio arrow alignment, short leg delta sign fix**
- 2026-03-11 10:30:00 -0400 — **fix: Regime page showing "MARKET CLOSED" during market hours — corrupt CRI cache files + stale market_open override**
- 2026-03-11 10:15:00 -0400 — **fix: Classify spread directions (DEBIT/CREDIT) in flow analysis**
- 2026-03-10 17:30:00 -0700 — **fix: Brand audit — align all 7 HTML report templates with Radon design tokens (fonts, CSS vars, radius, semantic colors)**
- 2026-03-09 19:30:00 -0700 — **feat: Upgrade to Next.js 15, React 19, implement Zustand for decoupled state, direct WS snapshots, and UI Skeletons**
- 2026-03-08 20:30:00 -0700 — **feat: Scenario stress test — interactive `stress-test` command with β-SPX/oil/VIX crash-beta pricing engine, BSM IV expansion, expandable ▶ detail rows per position, HTML template**
- 2026-03-08 12:00:00 -0700 — **feat: CRI Scan launchd service — automated every 30 min (4:05 AM–8 PM ET, Mon-Fri trading days), stale-while-revalidate API route (1 min TTL)**
- 2026-03-07 21:33:00 -0700 — **feat: Add IBC Gateway setup script — automated IB Gateway management via launchd (Mon-Fri auto-start, daily restart, 2FA handling, dialog suppression)**
- 2026-03-07 16:00:00 -0800 — **fix: Enforce data source priority (IB → UW → Yahoo) in all scanners. Add UW OHLC fallback to CRI, VCG, GARCH, LEAP scanners. Yahoo is LAST RESORT only.**
- 2026-03-07 15:36:00 -0800 — feat: Add MenthorQ client Phase 2 methods + full integration tests
- 2026-03-07 08:20:00 -0800 — feat: Add realtime price chart to ticker detail modal
- 2026-03-06 09:47:00 -0800 — **feat: Context engineering pipeline — persistent memory across sessions (Constructor + Evaluator, 7 facts seeded, auto-loads at startup)**
- 2026-03-06 09:32:00 -0800 — fix: Move undefined risk table above equity positions on portfolio page
- 2026-03-06 09:28:00 -0800 — **feat: Portfolio report — self-contained IB + dark pool fetch, today-highlighted sparklines, HTML template**
- 2026-03-06 09:28:00 -0800 — docs: SKILL.md Generation Checklist — added Trade Specification (16 steps) and Portfolio Report (10 steps)
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
- **Net Liquidation**: $1,194,810
- **Open Positions**: 39 (per IB sync)
- **Defined Risk**: Spreads (AAOI, AAPL, BKD, BRZE x2, GOOG, PLTR, TMUS), LEAPs (ALAB, AMD, SOFI, WULF), Long options (BAP, ETHA, IGV)
- **Undefined Risk**: Risk reversals (APO, IWM), Synthetic (IGV), Equities (EC, ILF, MSFT, NAK, RR, TSLL, URTY, USAX)
- ⚠️ *This section is a cache — verify against IB for current state*
- **Today's Realized P&L**: +$6,513 (AAOI call sale)

## Today's Trades (2026-03-18)
| Trade | Structure | P&L | Status |
|-------|-----------|-----|--------|
| CROX | Bull Call Spread $82.5/$95 Apr 17 (163x) @ $1.68 | — | ✓ FILLED |
| **Net Realized P&L Today** | | **$0** | |

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
| ~~URTY Short Call~~ | ~~Undefined risk (short call)~~ | 2026-03-09 | ✅ NOT A VIOLATION — Covered Call (4,000 shares cover 40 contracts) |
| ~~EWY Risk Reversal (Mar 13)~~ | ~~Undefined risk (short put $130)~~ | 2026-03-04 | ✅ RESOLVED (both legs closed, trades #26/#27, 2026-03-10) |
| IGV Synthetic Long | Undefined risk (short put $90) | Active | ⚠️ ACTIVE |
| AMD Long Call | Position size 7.4% (exceeds 2.5% cap) | 2026-03-03 | ⚠️ ACTIVE |
| APO Risk Reversal | Undefined risk (short put $100) | 2026-03-06 | ⚠️ ACTIVE |
| IWM Risk Reversal | Undefined risk (short put $248) | 2026-03-06 | ⚠️ ACTIVE |

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
| **16** | **03-05** | **AAOI** | **Bull Call Spread $105/$130 (Mar 20, 100x)** | **OPEN** | — |
| **17** | **03-06** | **AAOI** | **Sold Mar 6 $105C (25x @ $5.25)** | **CLOSED** | **+$6,513** |
| **18** | **03-06** | **APO** | **Risk Reversal P$100/C$115 (Apr 17, 25x)** | **OPEN** | — |
| **19** | **03-06** | **TMUS** | **Bull Call Spread $230/$250 (Apr 17, 100x)** | **OPEN** | — |
| **37** | **03-12** | **AAOI** | **BTC 25x $130C Mar 20 @ $0.10** | **CLOSED** | **+$14,025** |
| **38** | **03-12** | **AAOI** | **BTO 25x $105C Mar 20 @ $9.45** | **OPEN** | — |
| **39** | **03-12** | **OXY** | **Bear Put Spread P$55/P$50 (Apr 17, 222x)** | **OPEN** | — |
| **40** | **03-17** | **AAOI** | **Risk Reversal Short $92C/Long $88P (Mar 27, 25x)** | **CLOSED** | **+$6,871** |
| **41** | **03-17** | **AAOI** | **Closed RR #40 (bought back $92C, sold $88P)** | **CLOSED** | **(see #40)** |
| **42** | **03-18** | **CROX** | **Bull Call Spread $82.5/$95 (Apr 17, 163x)** | **OPEN** | **—** |

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

### OXY — Bear Put Spread $55/$50 (Trade #39) ✨ NEW
- **Entry**: 03-12 @ $0.98 net debit | **Structure**: 222 contracts, Apr 17 expiry (35 DTE)
- **Edge**: THESIS TRADE — bearish oil macro view, no dark pool confirmation
  - OXY selected as best vehicle after surveying USO, SCO, XLE, XOP, COP, APA, OIH, HAL, SLB, DVN, FANG, MPC
  - USO rejected: options illiquid ($1+ wide bid/ask), IV 120% (2× HV) = massive vol premium
  - SCO rejected: 2× inverse ETF decay ~11.5%/35 days, Kelly was NEGATIVE, path-dependent
  - XLE rejected: only 0.25 oil beta — needs 4× oil move vs OXY
  - OXY chosen: 0.43 oil beta + $0.03-0.08 bid/ask + 49% HV ≈ 42-49% IV (fairly priced) + no leveraged decay
- **Dark Pool at Entry**: MIXED (53% buy aggregate, no sustained direction)
- **Kelly**: P=0.25, odds=4.15:1, full Kelly 6.93%, 0.25× = 1.73% → 222 contracts
- **R:R**: 4.1:1 (max gain $89,244 / max risk $21,756)
- **Breakeven**: OXY ≤ $54.02 (oil needs to drop ~14%)
- **Max gain at**: OXY ≤ $50 (oil needs to drop ~35%)
- **Thesis**: ⚠️ THESIS ONLY — No institutional flow confirmation. Convexity and risk management pass. Edge gate marked as thesis-based override by operator.

---

## Recent Evaluations

### PLTR — 2026-03-17 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-17 07:22 AM PT (LIVE)
- **Reason**: Aggregate flow strength 40.2 below threshold (need >50). Only 1 sustained day. Recent strength 26.7 (need >70 for alternative). Alternating pattern: Mar 10/12 strong (80%+) then Mar 16 NEUTRAL breaks streak.
- **Dark Pool**: 70.1% aggregate buy ratio, ACCUMULATION direction, but inconsistent intensity
- **OI Changes**: $50.2M total (0 MASSIVE, 1 LARGE, 15 SIGNIFICANT)
- **News**: Strong AI catalyst pipeline — Nvidia, GE Aerospace, LG CNS partnerships
- **Action**: WATCH. Re-evaluate if 3+ consecutive days of accumulation (70%+) materialize.

### OXY — 2026-03-12 ✅ EXECUTED (Thesis Trade)
- **Decision**: TRADE
- **Structure**: Bear Put Spread P$55/P$50 Apr 17 (222 contracts)
- **Fill**: $0.98 net debit ($21,756 total)
- **Gates**: Convexity 4.1:1 ✅, Risk 1.75% ✅, Edge: THESIS ONLY ⚠️
- **📊 Data as of**: 2026-03-12 09:33 AM PT (LIVE)
- **Thesis**: Bearish oil macro view. OXY selected after comprehensive vehicle comparison (12 tickers evaluated). No dark pool distribution signal — institutions are NOT selling oil/energy names. Flow shows mixed/accumulation on OXY. Operator accepted thesis-only edge.
- **Vehicle analysis**: USO (illiquid options), SCO (leveraged decay), XLE (low beta), XOP (DP accumulation = wrong way), COP (low beta), APA (1 strike available). OXY had highest usable oil beta (0.43) with best option liquidity ($0.03-0.08 spreads).

### IBM — 2026-03-05 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: KELLY (Milestone 6) — Edge passed, Structure failed
- **📊 Data as of**: 2026-03-05 2:01 PM PT (LIVE — all milestones fetched fresh)
- **Edge PASSED**: 5 consecutive days of accumulation (Feb 27–Mar 5), today is STRONGEST day (83.1%)
  - Feb 27: 73.3% → Mar 02: 80.4% → Mar 03: 82.3% → Mar 04: 81.7% → **Mar 05: 83.1%** (building)
  - Aggregate 76.4%, strength 52.8 (threshold: 50 ✅)
  - Sustained 5 days (threshold: 3 ✅)
- **Options conflict is ARTIFICIAL**: 29.58x P/C ratio driven by deep ITM puts ($300/$305 strikes on $257 stock = synthetic/hedging). Actual flow alerts: $3.85M calls vs $2.39M puts = call-heavy. Recent bias BULLISH.
- **Why no trade — Kelly negative on ALL structures tested**:
  - $265/$285 spread: R:R 2.11:1 ✅, P(ITM)=0.48, conditional value=$10.50, EV=$5.04 < cost $6.43 → **Kelly -25%**
  - $270/$285 spread: R:R 3.18:1 ✅, P(ITM)=0.40, EV=$3.80 < cost $3.59 → **Kelly -22%**
  - $275/$290 OTM: R:R higher but P(ITM) too low → **Kelly -9%**
- **Root cause**: IBM is a low-vol stock (HV20 ~37%, IV rank 42). Options are fairly priced for the expected move. The 5-day DP edge adds ~5-8% to ITM probability, but that's insufficient to overcome the option premium. IBM doesn't produce convex payoffs at current IV levels.
- **Context**: Seasonality FAVORABLE (March 75% win rate). Analysts 60% Buy, $276 target (+7.5%). OI changes all MODERATE ($4.7M total, no massive signals).
- **Ticker Verified**: YES — IBM, Technology, $250/share
- **Lesson**: A genuine DP signal on a low-vol name can still fail Kelly. Edge ≠ trade. Convexity requires sufficient vol to create asymmetric payoffs.
- **Action**: WATCH only. Would need IBM IV to compress significantly (IVR < 20) or DP flow to intensify (90%+ buy ratio) before reconsidering.

### NFLX (Netflix) - 2026-03-05 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-05 12:32 PM PT (LIVE — all milestones fetched fresh)
- **Reason**: Accumulation cycle BROKE TODAY. Feb 27–Mar 4 showed 4-day sustained accumulation (84–93% buy). But **Mar 5 (TODAY): NEUTRAL (54.4%, strength 0.0)** — broke the streak.
  - Feb 26: DISTRIBUTION (37.5%)
  - Feb 27: ACCUMULATION (92.5%, 85.1 strength)
  - Mar 2: ACCUMULATION (92.7%, 85.4 strength)
  - Mar 3: ACCUMULATION (85.0%, 69.9 strength)
  - Mar 4: ACCUMULATION (84.4%, 68.9 strength)
  - **Mar 5 (TODAY): NEUTRAL (54.4%, 0.0 strength)** ← streak broken
  - Sustained consecutive days = **0** (need 3+)
  - Recent strength = 0.0 (need >70 for alternative criterion)
- **What passed**: Aggregate strength 65.4 (>50), $39M in OI changes (1 MASSIVE signal), chain data available, 82.7% aggregate buy ratio
- **What failed**: Today's neutral print resets sustained days to 0. Same pattern as AAPL — institutions accumulated Feb 27–Mar 4 then stopped.
- **GARCH context**: HV20 (57.9%) − LEAP IV (43.3%) = +14.5 pts → LEAP vega is cheap. But without sustained DP flow, no edge to trigger the trade.
- **Ticker Verified**: YES — Netflix, Communication Services, $98.45
- **Action**: Add to WATCH. Re-evaluate if sustained accumulation resumes (3+ consecutive days). Also monitor for standalone LEAP IV play if HV20−IV gap widens further.

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

### Ticker Detail Page (`/{TICKER}`)
Navigate to any ticker via search (CMD+K), ticker links in tables, or direct URL → dedicated page at `/{TICKER}` with 8 tabs:
- **Price Bar**: Real-time bid/ask/mid/spread/last/volume/day-change. Option positions show option-level prices (not underlying). Spreads show net mid computed from leg prices.
- **Company Tab**: Fundamentals, sector, description from UW.
- **Book Tab**: Level 2 order book data.
- **Chain Tab**: Options chain with expiry selection, strike filtering.
- **Position Tab**: Structure, direction, qty, entry date, avg entry, last price, entry cost, market value, unrealized P&L, expiry, target/stop. Multi-leg positions show individual legs.
- **Order Tab**: If open orders exist → inline modify/cancel (reuses `useOrderActions`). Below that, new order form with BUY/SELL, qty, limit price with BID/MID/ASK quick-set (option-level), TIF, 2-step confirm. Combo positions show "close via Orders page" message.
- **News Tab**: UW headlines with date, source, MAJOR badge, external link icon.
- **Ratings Tab**: Analyst recommendation, ratings bar, price targets, recent changes (via `fetch_analyst_ratings.py`).
- **Seasonality Tab**: Monthly seasonality from UW with EquityClock Vision fallback.
- **URL ownership**: Active tab persisted as `?tab=chain`, position ID as `?posId=123`. Bookmarkable, shareable, no lost state on refresh. `/aapl` auto-redirects to `/AAPL`. Invalid paths return 404.
- **Architecture**: `[ticker]/page.tsx` (server validation) → `WorkspaceShell` → `TickerWorkspace` (URL tab sync) → `TickerDetailContent` (8 tabs). Navigation via `useTickerNav` hook and shared `TickerLink` component. `TickerDetailContext` provides `setActiveTicker()`/`setActivePositionId()` URL-driven state. Prices, portfolio, orders synced via refs (no re-renders).
- **API Routes**: `/api/ticker/news` (UW proxy), `/api/ticker/ratings` (Python script), `/api/orders/place` (IB order placement via `ib_place_order.py`).

### Key Scripts
| Script | Purpose |
|--------|---------|
| `clients/ib_client.py` | **IBClient** — Primary IB API client |
| `clients/uw_client.py` | **UWClient** — Primary UW API client |
| `evaluate.py` | **⭐ Unified 7-milestone evaluation (parallel fetch, auto-stops)** |
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
| `portfolio_report.py` | **⭐ Self-contained portfolio HTML report (IB + DP flow + thesis check)** |
| `context_constructor.py` | **⭐ Persistent memory: Constructor (auto-load) + Evaluator (save facts/episodes)** |
| `scenario_analysis.py` | **⭐ Stress test pricing engine — β-SPX + oil + VIX crash-beta + BSM IV expansion** |
| `scenario_report.py` | **⭐ Stress test HTML report — reference implementation with expandable ▶ detail rows** |
| `setup_ibc.sh` | **⭐ IBC Gateway service manager (install/uninstall/status/logs/start/stop)** |

### Skills
| Skill | Purpose |
|-------|---------|
| `ib-order-execution` | Order placement and fill monitoring |
| `html-report` | Trade specification + P&L + Portfolio templates |
| `context-engineering` | Persistent memory architecture (always-on) |
| `tweet-it` | Tweet copy + infographic card for X posts (base64 PNG embed) |

### Services
| Service | Status | Description |
|---------|--------|-------------|
| IBC Gateway | 🟢 Active | Automated IB Gateway management — login, 2FA, daily restart, dialog suppression |
| Monitor Daemon | 🟢 Active | Fill monitoring, exit order placement, preset rebalancing |
| IB Reconciliation | 🟢 Active | Runs at Pi startup (async) |
| Context Constructor | 🟢 Active | Loads persistent memory at Pi startup (sync) |

### Templates
| Template | Purpose |
|----------|---------|
| `trade-specification-template.html` | Full evaluation report |
| `pnl-template.html` | P&L reconciliation report |
| `portfolio-template.html` | **Portfolio report with today-highlighted sparklines** |
| `stress-test-template.html` | **⭐ Scenario stress test with expandable ▶ detail rows per position (NEW)** |

### Persistent Memory
| Directory | Count | Purpose |
|-----------|-------|---------|
| `context/memory/fact/` | 7 | Trading lessons, API quirks, portfolio state |
| `context/memory/episodic/` | 1 | Session summaries |
| `context/human/` | 0 | Human annotations (overrides) |
| Token budget | 558/8000 | 7% utilization |

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
15. ~~CRI/VCG/GARCH/LEAP scanners skip UW, fall back IB → Yahoo~~ **FIXED** — All scanners now use IB → UW → Yahoo priority. UW OHLC serves stocks/ETFs. Yahoo only for VIX/VVIX (indices UW cannot serve).
16. ~~All-long combo positions missing from web UI~~ **FIXED** — `ib_sync.py` classified all-long combos (e.g., AAOI 2x long calls at different strikes) as `risk_profile: "complex"`. The web UI only rendered `defined`/`undefined`/`equity` buckets, silently dropping `complex`. Fix: (a) all-long combos now classified as `"defined"` with descriptive names (`Long Call Combo`, `Long Put Combo`, `Long Combo`); (b) UI fallback includes `"complex"` in undefined bucket. Tests: `test_all_long_combo.py` (8), `complex-risk-profile.test.ts` (5).

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
- [x] **Ticker detail page at `/{TICKER}` — company, book, chain, position, order (with modify/cancel), news, ratings, seasonality (refactored from modal to dedicated route)**
- [x] **Option-level pricing in price bar and order form BID/MID/ASK**
- [x] **Spread net mid computation for BAG orders (long leg mid - short leg mid)**
- [x] **ib_place_order.py — JSON-in/JSON-out order placement for web API**
- [x] **Fix Day Chg % for options/spreads (divide by close value, not entry cost)**
- [x] **Fix spread last price (net mid from leg prices, not underlying)**
- [x] **Ticker detail page accessible from all pages via search/links, bookmarkable with `?tab=` and `?posId=` params**
- [x] **useOrders() always loads cached orders on mount for cross-page ticker detail**
- [x] **Fix discover.py watchlist KeyError (normalize ticker/symbol key)**
- [x] **News tab UW → Yahoo Finance fallback on 429 rate limit**
- [x] **RatingsTab rewrite: nested API mapping, buy/sell %, distribution bar, price targets, analyst actions**
- [x] **All-sources-failed graceful handling in News and Ratings tabs**
- [x] **Data normalization: canonical "ticker" key in all data files**
- [x] **evaluate.py unified evaluation script with 34 TDD tests**
- [x] **garch_convergence.py — parallel GARCH convergence scanner (8 workers, built-in + file presets, HTML report)**
- [x] **Portfolio report: self-contained script (IB + DP flow + HTML template with today-highlighting)**
- [x] **Portfolio template: `.pi/skills/html-report/portfolio-template.html` with 13 placeholders**
- [x] **SKILL.md Generation Checklist: Trade Specification (16 steps) + Portfolio Report (10 steps)**
- [x] **Web: undefined risk table moved above equity positions on portfolio page**
- [x] **Context engineering: `context_constructor.py` — Constructor + Evaluator pipeline, auto-loads at startup**
- [x] **Seeded 7 persistent facts from evaluation history + 1 episodic summary**
- [x] **IBC Gateway setup script — automated IB Gateway management via launchd**
- [x] **Scenario stress test: `stress-test` command, pricing engine, expandable HTML report with per-position narratives**
- [x] **Stress test template: `.pi/skills/html-report/stress-test-template.html` with ▶ detail rows**
- [x] **SKILL.md Generation Checklist: Stress Test Reports (14 steps)**
- [x] **Share PnL: entry/exit price+time for multi-day trades (portfolio fallback, PST timezone, no commission)**
- [ ] Execute MSFT LEAP call trade (pending confirmation)
- [ ] Close undefined risk positions before Friday expiry
- [ ] Review PLTR for profit-taking (23 DTE, +175%)
- [ ] Review IGV/SOFI for stop-loss exit
- [ ] GOOG target order — place when spread reaches ~$9.23
