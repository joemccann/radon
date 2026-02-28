# Convex Scavenger - Spec & Deliverables

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

## Deliverables (per evaluation)
- [ ] Ticker validation (confirm company, sector, liquidity)
- [ ] Dark pool flow analysis (5-day minimum)
- [ ] Options flow analysis (if available)
- [ ] Edge determination (PASS/FAIL with reasoning)
- [ ] Structure proposal (if edge exists)
- [ ] Convexity calculation (R:R ratio)
- [ ] Kelly sizing (optimal % and position size)
- [ ] Final decision with all three gates documented

## Done When
An evaluation is complete when:
1. Ticker identity is VERIFIED (not assumed)
2. All three gates are evaluated in order
3. Failing gate stops evaluation (no rationalization)
4. Decision is logged with full rationale
5. If TRADE: position added to portfolio.json
6. If NO TRADE: reason documented in trade_log.json
