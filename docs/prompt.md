# Radon - Spec & Deliverables

## Goal
Autonomous options trading via convex, edge-driven bets sized by fractional Kelly criterion.

## Non-Goals
- Day trading / scalping
- Selling premium / theta strategies
- Narrative-based trades
- Legacy TA (RSI, MACD, trendlines)

## Hard Constraints
1. **Convexity**: Potential gain ≥ 2x potential loss (ALWAYS)
2. **Edge**: Institutional dark pool / OTC flow detection ONLY
3. **Position Size**: Max 2.5% bankroll per position
4. **Kelly**: Use 0.25x-0.5x fractional Kelly
5. **Undefined Risk**: NEVER (no naked options)
6. **Fresh Data**: Every data-fetching milestone MUST fetch live data at execution time. Scan results are leads, not evidence. Re-fetch during evaluation.

## Deliverables (per evaluation)
- [ ] **Data freshness header** (`📊 Data as of: [timestamp]`)
- [ ] Ticker validation (confirm company, sector, liquidity)
- [ ] Seasonality analysis (context, not a gate)
- [ ] Analyst ratings — **fetched live** (context, not a gate)
- [ ] News & catalysts — **fetched live** (buybacks, M&A, earnings, material events)
- [ ] Dark pool flow analysis — **fetched live**, 5-day minimum **including today**
- [ ] Options flow analysis — **fetched live** (chain + institutional flow alerts)
  - Chain: Put/call ratio, premium, volume, OI, bias
  - Flow: Alerts, sweeps, bid/ask side, flow strength
  - Combined: Synthesized bias with conflict detection
- [ ] OI change analysis — **fetched live** (REQUIRED)
- [ ] Edge determination (PASS/FAIL with reasoning)
- [ ] Structure proposal — **live option quotes** (if edge exists)
- [ ] Convexity calculation (R:R ratio)
- [ ] Kelly sizing (optimal % and position size)
- [ ] Final decision with all three gates documented

## Done When
An evaluation is complete when:
1. Ticker identity is VERIFIED (not assumed)
2. **All data-fetching milestones used FRESH data (fetched during this evaluation, not from a prior scan)**
3. All three gates are evaluated in order
4. Failing gate stops evaluation (no rationalization)
5. Decision is logged with full rationale
6. If TRADE: logged to trade_log.json + position synced to portfolio.json
7. If NO TRADE: documented in docs/status.md (Recent Evaluations)

## Portfolio Management

### ⚠️ IB Is the Source of Truth for Portfolio State (CRITICAL)
- **Interactive Brokers is the ONLY source of truth** for what positions are currently held.
- `docs/status.md` is a decision log, NOT a live portfolio view. It goes stale.
- `data/portfolio.json` is a cache. It may be hours or days old.
- **ALWAYS run `python3 scripts/ib_sync.py`** before claiming a position exists or doesn't exist.
- When IB is unavailable, say so — do NOT fall back to status.md or portfolio.json for current state.

### Startup Reconciliation
- Pi startup automatically runs IB reconciliation (async)
- Detects new trades, new positions, closed positions
- Shows notification if action needed
- Results in `data/reconciliation.json`

### Trade Logging
- Executed trades → `data/trade_log.json`
- Include: entry/exit fills, commissions, P&L, thesis
- Use Flex Query for historical trade data

### P&L Calculation
- Use Decimal for precision
- Always include commissions
- Return on Risk = P&L / Capital at Risk

### Position Review
- Check flow alignment for logged positions
- Flag positions below -50% stop
- Flag positions approaching expiry (<21 DTE)

### Scenario Stress Testing
- Interactive `stress-test` command: ask user for scenario, model P&L, generate HTML report
- Pricing engine: β-SPX + oil sensitivity + VIX crash-beta + Black-Scholes IV expansion
- Defined-risk P&L hard-capped at max loss (net debit)
- Per-position expandable narratives explaining why each asset is helped/hurt
- Template: `.pi/skills/html-report/stress-test-template.html`

## Data Sources (Priority Order)
1. **Interactive Brokers** — Real-time quotes, positions, executions
2. **Unusual Whales** — Dark pool flow, options activity, alerts, analyst ratings
3. **Exa (web search)** — Company research, code/docs lookup
4. **agent-browser** — Interactive pages, JS-rendered content
5. **Yahoo Finance** — **ABSOLUTE LAST RESORT** — only if ALL above sources fail (rate limited, unreliable)
