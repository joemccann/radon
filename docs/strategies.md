# Trading Strategies

## Overview

The Convex Scavenger employs two primary edge-detection strategies, both focused on exploiting informational or structural advantages that institutional players leave behind.

| Strategy | Edge Source | Instrument | Timeframe | Risk Profile |
|----------|-------------|------------|-----------|--------------|
| **Dark Pool Flow** | Institutional positioning | ATM/OTM options | 2-6 weeks | Defined (long options) |
| **LEAP IV Mispricing** | Volatility regime change | Long-dated calls | 1-3 years | Defined (long options) |

---

## Strategy 1: Dark Pool Flow Detection

### Thesis

Large institutional players accumulate or distribute positions through dark pools and OTC markets before moving the lit market price. By detecting sustained directional flow, we can position ahead of the price move using convex options structures.

### Edge Source

- **Dark pool prints**: Off-exchange block trades that signal institutional intent
- **OTC flow**: Dealer positioning data showing net buying/selling pressure
- **Options flow**: Unusual volume, sweeps, and premium imbalances

### Signal Criteria

| Criterion | Threshold | Weight |
|-----------|-----------|--------|
| Sustained direction | 3+ consecutive days same direction | Required |
| Flow strength | >50 aggregate OR >70 recent days | Required |
| Options confirmation | Call/put flow alignment | Preferred |
| Price lag | Signal NOT yet reflected in price | Required |

### Position Structure

- **Bullish signal**: ATM/OTM calls, bull call spreads
- **Bearish signal**: ATM/OTM puts, bear put spreads
- **Expiration**: 3-6 weeks out (balance theta decay vs. time for thesis)
- **R:R requirement**: Minimum 2:1 potential gain to max loss

### Sizing

- Fractional Kelly (0.25x) based on estimated P(ITM) and conditional payout
- Hard cap: 2.5% of bankroll per position
- Max concurrent positions: 6

### Exit Criteria

- **Target hit**: Underlying reaches target zone
- **Stop loss**: -50% of premium OR underlying breaks key support/resistance
- **Time decay**: Close if <5 DTE with no momentum
- **Signal reversal**: Flow direction flips

### Scripts

```bash
# Scan watchlist for flow signals
python3 scripts/scanner.py

# Discover new candidates market-wide
python3 scripts/discover.py

# Full evaluation of specific ticker
python3 scripts/fetch_flow.py [TICKER]
```

---

## Strategy 2: LEAP IV Mispricing

### Thesis

Long-dated options (LEAPs) are priced based on the market's forward volatility estimate, which often lags reality during regime changes. When recent realized volatility significantly exceeds LEAP implied volatility, and structural reasons exist for elevated vol to persist, the options are underpriced.

**The edge is vega, not delta.** Profit comes from IV expansion even if the underlying moves sideways.

### Edge Source

The market's forward vol estimate diverges from true expected realized vol due to:

1. **Term-structure anchoring**: Models extrapolate from 5-10 year history
2. **Mean-reversion assumptions**: GARCH/Heston models assume vol reverts
3. **Liquidity inertia**: Fewer LEAP participants = slower repricing
4. **Hidden concentration**: ETF/index composition masks true vol drivers

### Signal Criteria

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| HV20 > LEAP IV | ≥15-20 points | Primary signal |
| HV60 > LEAP IV | ≥15-20 points | Confirmation |
| Structural thesis | Required | Why vol won't revert |
| Regime shift | Identified | New cycle, policy change, concentration |

### Structural Factors to Analyze

1. **Historical vs. Current Realized Vol**
   - Pull 1Y, 3Y, 5Y, 10Y annualized HV
   - Compare to current HV20/HV60
   - Flag if LEAP IV anchored to old averages

2. **Concentration & Hidden Beta**
   - Look-through ETF holdings
   - Identify pass-through exposure (holding companies, cross-ownership)
   - Compare ETF IV to top holding IVs

3. **Forward Vol Drivers**
   - New products (leveraged ETFs, perps) requiring dynamic hedging
   - Fundamental regime change (AI cycle, policy shift)
   - Comparable asset IV movements

4. **Option Chain Diagnostics**
   - Term structure: Is long-dated IV below short-dated?
   - Vega profile: How much leverage on IV expansion?
   - Skew: Are OTM calls disproportionately cheap?

### Position Structure

- **Instrument**: 25-40 delta calls, 1-3 years out
- **Structure**: Naked long calls (max convexity) or call spreads (reduced cost)
- **Sizing**: For vega exposure, not delta
- **Delta hedge**: Optional - short underlying or short-dated puts if needed

### Quantifying the Edge

```
Expected Realized Vol (your estimate): X%
LEAP IV (market price): Y%
Gap: X - Y = mispricing in vol points

Vega on 30Δ 2-year call ≈ 0.30-0.50 per 1% IV
20-point IV expansion = +6-10 points on option premium
```

### Exit Criteria

- **IV convergence**: Long-dated IV catches up to your target (e.g., 55-65%)
- **Time-based**: Re-evaluate thesis annually
- **Structural change**: Original thesis invalidated

### Example: EWY (South Korea ETF)

| Metric | Historical | Recent | LEAP Pricing |
|--------|------------|--------|--------------|
| 10Y HV | 20-30% | — | — |
| HV60 | — | 65-70% | — |
| 2028 OTM Call IV | — | — | 32-44% |
| **Gap** | — | — | **+25-35 pts** |

**Hidden factors:**
- Samsung + SK Hynix = ~47% of ETF but 90%+ of vol contribution
- Pass-through holdings amplify effective concentration
- AI memory supercycle = structural high-vol earnings
- New 2x leveraged ETFs = MM hedging = more vol

**Result:** LEAP calls doubled from IV expansion alone (32% → 44%+), even when underlying pulled back.

### Mispriced LOW (Buy Vega) When:

- Recent HV20/HV60 > long-dated IV by >15-20 pts
- Structural thesis for persistent elevated vol
- Regime shift (tech cycle, policy, concentration)
- Pass-through exposure the index hides
- Street models anchored to stale history

### Mispriced HIGH (Sell Vega) When:

- Opposite conditions
- Mature, stable business entering low-vol period
- Mean-reversion after spike with no new catalysts

### Scripts

```bash
# Scan State Street sector ETFs
python3 scripts/leap_iv_scanner.py --preset sectors

# Scan specific tickers
python3 scripts/leap_iv_scanner.py AAPL MSFT NVDA EWY

# Scan with custom parameters
python3 scripts/leap_iv_scanner.py --min-gap 20 --years 2027 2028

# Scan portfolio holdings
python3 scripts/leap_iv_scanner.py --portfolio
```

### Presets Available

| Preset | Tickers |
|--------|---------|
| `sectors` | XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY |
| `mag7` | AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA |
| `semis` | NVDA, AMD, INTC, AVGO, QCOM, MU, AMAT, LRCX, KLAC, TSM |
| `emerging` | EEM, EWZ, EWY, EWT, INDA, FXI, EWW, ILF |
| `china` | BABA, JD, PDD, BIDU, NIO, XPEV, LI, FXI, KWEB |
| `energy` | XOM, CVX, COP, SLB, EOG, PXD, OXY |
| `financials` | JPM, BAC, WFC, GS, MS, C, BLK, SCHW |

---

## Strategy Interaction

These strategies can be combined:

1. **Flow → LEAP confirmation**: If dark pool flow shows sustained accumulation AND LEAP IV is mispriced low, the signal is stronger.

2. **LEAP → Flow monitoring**: After entering a LEAP position, monitor dark pool flow for early warning of thesis invalidation.

3. **Position layering**: 
   - Core: LEAP calls for vega exposure (months to years)
   - Tactical: Short-dated options on flow signals (weeks)

---

## Risk Management (Both Strategies)

| Rule | Limit |
|------|-------|
| Max position size | 2.5% of bankroll |
| Sizing method | Fractional Kelly (0.25x) |
| Risk type | Defined only (long options, spreads) |
| Never | Naked short options, undefined risk |
| Max concurrent | 6 positions |
| Kelly > 20% | Restructure (insufficient convexity) |

---

## Performance Tracking

Log all decisions in `data/trade_log.json`:

```json
{
  "timestamp": "2026-02-28T12:00:00",
  "ticker": "EWY",
  "strategy": "LEAP_IV_MISPRICING",
  "decision": "TRADE",
  "signal": {
    "hv_20": 68.5,
    "hv_60": 62.3,
    "leap_iv": 42.0,
    "gap": 26.5,
    "structural_thesis": "AI memory supercycle, hidden Samsung/SK concentration"
  },
  "structure": "Long 2028 $150 calls (30Δ)",
  "kelly_optimal": 12.5,
  "position_size_pct": 2.5,
  "entry_cost": 24500
}
```
