# Trading Strategies

> **⚠️ Registry sync:** This file is the source of truth. When adding or modifying a strategy here, also update `data/strategies.json` (the machine-readable registry read by the `strategies` command). See `docs/implement.md` § 5B.

## Overview

The Radon employs six strategies, each exploiting informational or structural advantages that institutional players leave behind.

| Strategy | Edge Source | Instrument | Timeframe | Risk Profile |
|----------|-------------|------------|-----------|--------------|
| **Dark Pool Flow** | Institutional positioning | ATM/OTM options | 2-6 weeks | Defined (long options) |
| **LEAP IV Mispricing** | Volatility regime change | Long-dated calls | 1-3 years | Defined (long options) |
| **GARCH Convergence** | Cross-asset repricing lag | Medium-dated options | 2-8 weeks | Defined (long options/spreads) |
| **Risk Reversal** | IV skew exploitation | Sell put + Buy call | 2-8 weeks | **Undefined** (manager override) |
| **Volatility-Credit Gap v2 (VCG-R)** | Vol/credit divergence (VIX>28 + VCG>2.5) | HY puts, CDX protection | 1-5 days | Defined (long puts/spreads) |
| **Crash Risk Index** | CTA deleveraging | SPY puts, tail hedges | 3-5 days | Defined (long puts/spreads) |

See also: [`strategy-garch-convergence.md`](strategy-garch-convergence.md) for the full GARCH Convergence Spreads specification.
See also: [`cross_asset_volatility_credit_gap_spec_(VCG).md`](cross_asset_volatility_credit_gap_spec_(VCG).md) for the full VCG mathematical specification.

---

## Strategy 1: Dark Pool Flow Detection

### Thesis

Large institutional players accumulate or distribute positions through dark pools and OTC markets before moving the lit market price. By detecting sustained directional flow, we can position ahead of the price move using convex options structures.

### Edge Source

- **Dark pool prints**: Off-exchange block trades that signal institutional intent
- **OTC flow**: Dealer positioning data showing net buying/selling pressure
- **Options flow**: Unusual volume, sweeps, and premium imbalances
- **News & catalysts**: Material events (buybacks, M&A, earnings, FDA) that explain or contextualize flow patterns. A $25B buyback, for example, creates structural dark pool buying that might otherwise appear as noise.

### Signal Criteria

| Criterion | Threshold | Weight |
|-----------|-----------|--------|
| Sustained direction | 3+ consecutive days same direction | Required |
| Flow strength | >50 aggregate OR >70 recent days | Required |
| Options confirmation | Call/put flow alignment | Preferred |
| Price lag | Signal NOT yet reflected in price | Required |

### Options Flow Confirmation

Use `fetch_options.py` to confirm or contradict dark pool signals:

```bash
python3.13 scripts/fetch_options.py [TICKER]
```

**Chain Analysis:**
| Put/Call Ratio | Signal |
|----------------|--------|
| >2.0x | BEARISH (confirms distribution) |
| 1.2-2.0x | LEAN_BEARISH |
| 0.8-1.2x | NEUTRAL |
| 0.5-0.8x | LEAN_BULLISH |
| <0.5x | BULLISH (confirms accumulation) |

**Flow Alerts Analysis:**
- **Sweeps**: Urgency signal — aggressive buying/selling across exchanges
- **Bid-side dominant**: Selling pressure (closing longs or opening shorts)
- **Ask-side dominant**: Buying pressure (opening longs)

**Confluence Check:**
| DP Flow | Options Signal | Action |
|---------|----------------|--------|
| Accumulation | BULLISH/LEAN_BULLISH | ✅ Strong confirm |
| Accumulation | NEUTRAL | ⚠️ Proceed with caution |
| Accumulation | BEARISH | ❌ Conflict — reduce size or pass |
| Distribution | BEARISH/LEAN_BEARISH | ✅ Strong confirm |
| Distribution | NEUTRAL | ⚠️ Proceed with caution |
| Distribution | BULLISH | ❌ Conflict — reduce size or pass |

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

### Commands: Scan vs Evaluate

| Command | Data Fetched | Purpose | Speed |
|---------|-------------|---------|-------|
| `scan` | Dark pool only (5 days) | Quick ranking of watchlist | ~17-25s for 19 tickers |
| `evaluate [TICKER]` | Dark pool + options flow + OI + news + analysts | Full trade decision | ~6-15s per ticker |
| `discover` | Flow alerts market-wide | Find new candidates | Variable |

**Key difference**: `scan` skips options flow for speed. It ranks tickers by dark pool signal strength but does NOT detect conflicts with options flow. Use `evaluate` for trade decisions — it does full 7-milestone analysis including conflict detection.

**Intraday interpolation**: During market hours, all dark pool data is **automatically interpolated**. Today's partial data is blended with prior days' patterns based on trading day progress. The `aggregate` values used for scoring/edge determination are interpolated — not raw partial-day values. Confidence level (LOW/MEDIUM/HIGH) depends on how much of the trading day has elapsed.

**Workflow**: `scan` → identify top candidates → `evaluate [TICKER]` → trade decision

### Scripts

```bash
# Scan watchlist for flow signals (dark pool only, fast)
python3.13 scripts/scanner.py

# Discover new candidates market-wide (default)
python3.13 scripts/discover.py

# Discover from a preset
python3.13 scripts/discover.py ndx100
python3.13 scripts/discover.py sp500-semiconductors

# Discover specific tickers
python3.13 scripts/discover.py AAPL MSFT NVDA

# Fetch dark pool flow (5-day)
python3.13 scripts/fetch_flow.py [TICKER]

# Fetch options chain + flow analysis
python3.13 scripts/fetch_options.py [TICKER]

# Full evaluation output (JSON)
python3.13 scripts/fetch_options.py [TICKER] --json
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

### Data Source Priority

When fetching any market data, **ALWAYS** use sources in this priority order:

| Priority | Source | Best For | Limitations |
|----------|--------|----------|-------------|
| **1st** | Interactive Brokers | Real-time quotes, options chains, analyst ratings, fundamentals | Requires TWS/Gateway connection |
| **2nd** | Unusual Whales | Dark pool flow, options activity, institutional signals, analyst ratings | API key required |
| **3rd** | Exa (web search) | Company research, news, data not in IB/UW | API key required |
| **4th** | agent-browser | Interactive pages, JS-rendered content | Slow, fallback only |
| **5th** | Cboe official index feeds | COR1M historical fallback before Yahoo | COR1M-specific, delayed historical feed |
| **6th ⚠️** | Yahoo Finance | **ABSOLUTE LAST RESORT** — only if ALL above fail | Rate limited, unreliable, delayed |

**For COR1M, use the official Cboe dashboard historical feed before Yahoo Finance.**
**Yahoo Finance is the ABSOLUTE LAST RESORT. Never use it if IB, UW, Exa, agent-browser, or an official Cboe feed can provide the data.**

### Scripts

```bash
# Scan State Street sector ETFs
python3.13 scripts/leap_iv_scanner.py --preset sectors

# Scan specific tickers
python3.13 scripts/leap_iv_scanner.py AAPL MSFT NVDA EWY

# Scan with custom parameters
python3.13 scripts/leap_iv_scanner.py --min-gap 20 --years 2027 2028

# Scan portfolio holdings
python3.13 scripts/leap_iv_scanner.py --portfolio
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

## Strategy 3: GARCH Convergence Spreads

### Thesis

When a structural catalyst elevates realized volatility in a sector, individual components reprice IV at different rates — creating measurable mispricings between correlated assets. The trade captures convergence as lagging assets' IV catches up to leaders.

### Signal Criteria

| Criterion | Threshold |
|-----------|-----------|
| IV/HV60 divergence (leader vs lagger) | ≥ 0.15 |
| Lagger HV20 − LEAP IV | ≥ +10 points |
| Shared vol driver | Same fundamental catalyst |
| Lagger IV rank | < 50th percentile |
| Lagger has LEAPs | OI > 100 |

### Signal Strength

| Tier | Conditions | Action |
|------|-----------|--------|
| Strong | Divergence ≥ 0.30, HV gap ≥ 20, IV rank < 30% | Full size (2.5%) |
| Moderate | Divergence ≥ 0.20, HV gap ≥ 15, IV rank < 40% | Half size (1.25%) |
| Weak | Divergence ≥ 0.15, HV gap ≥ 10 | Monitor only |

### Scripts

```bash
# ⭐ ALWAYS use the unified scanner
python3.13 scripts/garch_convergence.py --preset all          # All 4 built-in presets (~3s)
python3.13 scripts/garch_convergence.py --preset semis        # Semiconductors only
python3.13 scripts/garch_convergence.py --preset mega-tech    # Mega-cap tech
python3.13 scripts/garch_convergence.py --preset energy       # Energy sector
python3.13 scripts/garch_convergence.py --preset china-etf    # China/Asia
python3.13 scripts/garch_convergence.py --preset sp500-semiconductors  # File preset
python3.13 scripts/garch_convergence.py NVDA AMD GOOGL META   # Ad-hoc pairs
python3.13 scripts/garch_convergence.py --preset all --json   # JSON output
```

### Built-in Pair Presets

| Preset | Pairs |
|--------|-------|
| `semis` | (NVDA,AMD), (TSM,ASML), (AVGO,QCOM), (MU,AMAT) |
| `mega-tech` | (AAPL,MSFT), (GOOGL,META), (AMZN,NFLX) |
| `energy` | (XOM,COP), (SLB,HAL), (XLE,OIH) |
| `china-etf` | (FXI,BABA), (EWY,FXI) |

Also supports any file preset from `data/presets/` (150+ presets with pairs).

Full strategy specification: [`strategy-garch-convergence.md`](strategy-garch-convergence.md)

---

## Strategy 4: Risk Reversal

### Thesis

OTM puts consistently trade at higher implied volatility than equivalent-delta OTM calls due to persistent demand for downside protection. A bullish risk reversal (sell OTM put, buy OTM call) monetizes this skew — the rich put premium funds the cheap call, creating costless or credit-generating directional exposure.

**The edge is skew, not direction.** The structural IV imbalance means you sell expensive vol and buy cheap vol. Direction is the operator's thesis; skew is the structural advantage.

### ⚠️ Undefined Risk — Manager Override (Gate 4 Exception)

This strategy involves selling naked options (short put for bullish, short call for bearish). This is an explicit **manager override** of Gate 4 (Naked Short Protection). The override is justified when:

1. The underlying is a **broad index ETF** (IWM, SPY, QQQ) — no single-name blowup risk
2. The operator has an **explicit directional thesis** with supporting data (DP flow, technicals)
3. Position is **sized to 2.5% margin** — same dollar risk discipline as defined-risk strategies
4. A **hard stop loss** is set at trade entry (e.g., close if spread value reaches –$3.00)
5. The manager **explicitly requests** the structure and acknowledges the Gate 4 override

**This strategy is NEVER auto-triggered.** It requires the `risk-reversal` command with operator confirmation. The Gate 4 naked short guard will flag the order — the operator must explicitly override it.

### Edge Source: IV Skew

| Delta | Typical Put IV | Typical Call IV | Skew |
|-------|---------------|----------------|------|
| ~50Δ | 28-32% | 28-32% | 0% |
| ~40Δ | 30-34% | 26-29% | +3-5% |
| ~35Δ | 32-36% | 25-28% | +5-8% |
| ~30Δ | 33-38% | 24-27% | +7-11% |
| ~25Δ | 35-40% | 23-26% | +9-14% |

Skew is steeper during selloffs (when put demand surges) — exactly when contrarian bullish risk reversals are most attractive.

### Signal Criteria

| Criterion | Requirement | Notes |
|-----------|-------------|-------|
| Operator direction | BULLISH or BEARISH | Manager's thesis, not auto-detected |
| IV skew | ≥3% at target delta | Ensures structural edge on the trade |
| Liquid options chain | Bid-ask ≤ $0.10 on target strikes | IWM, SPY, QQQ always qualify |
| Net cost | Costless or small credit preferred | Maximum $0.50 debit |
| DTE range | 14-60 days | Balance theta decay vs time for thesis |

### Supporting Signals (Context, Not Gates)

- **Dark pool flow**: Accumulation confirms bullish thesis, distribution confirms bearish
- **Options flow**: Extreme P/C ratio may indicate hedging (contrarian bullish signal)
- **OI changes**: Large put OI buildup = structural skew support (more put selling premium)

### Position Structure

**Bullish Risk Reversal (default):**
- **Sell**: OTM Put (25-50Δ) at bid
- **Buy**: OTM Call (25-50Δ) at ask
- Net: Costless or small credit
- Profit: Unlimited upside above call strike
- Loss: Stock assigned below put strike (undefined downside)

**Bearish Risk Reversal (`--bearish` flag):**
- **Sell**: OTM Call (25-50Δ) at bid
- **Buy**: OTM Put (25-50Δ) at ask
- Net: Costless or small credit
- Profit: Unlimited downside profit below put strike
- Loss: Stock called away above call strike (undefined upside)

### Sizing

- **Margin-based**: ~20% of notional per contract (IB requirement for short puts on index ETFs)
- **Hard cap**: 2.5% of bankroll in margin
- **Max contracts**: `bankroll × 0.025 / (put_strike × 100 × 0.20)`
- **Stop loss**: Set at trade entry — close if spread value hits –$3.00

### Combo Selection Criteria

The script ranks combinations by:

1. **Proximity to zero cost** — costless or small credit preferred
2. **Delta balance** — put delta ≈ call delta for symmetric exposure
3. **IV skew captured** — higher skew = more edge
4. **Buffer from spot** — both strikes sufficiently OTM

Three recommendations are generated:
- **Primary**: Costless, balanced deltas, longer DTE
- **Alternative**: Different expiration for faster/slower catalyst
- **Aggressive**: Generates meaningful credit but tighter downside buffer

### Exit Criteria

| Condition | Action |
|-----------|--------|
| Underlying hits call strike + 5% | Close call for profit, close put |
| Spread value reaches –$3.00 | Stop loss — close both legs |
| DTE < 5 with no momentum | Close to avoid assignment risk |
| Thesis invalidated | Close immediately |
| DP flow reverses direction | Review thesis, consider closing |

### Scripts

```bash
# Bullish risk reversal on IWM (default)
python3.13 scripts/risk_reversal.py IWM

# Bearish risk reversal
python3.13 scripts/risk_reversal.py SPY --bearish

# Custom parameters
python3.13 scripts/risk_reversal.py QQQ --bankroll 500000 --min-dte 21 --max-dte 45

# Don't open browser
python3.13 scripts/risk_reversal.py IWM --no-open

# JSON output (for programmatic use)
python3.13 scripts/risk_reversal.py IWM --json
```

### Tickers Best Suited

| Type | Tickers | Why |
|------|---------|-----|
| **Index ETFs** | IWM, SPY, QQQ, DIA | Deepest skew, most liquid, no single-name risk |
| **Sector ETFs** | XLK, XLE, XLF, EWY | Decent skew, diversified exposure |
| **Large-cap stocks** | AAPL, MSFT, NVDA, AMZN | Liquid chains, but single-name risk applies |

**Avoid**: Small-caps, illiquid options, earnings-adjacent (IV crush kills both legs).

---

## Strategy 5: Volatility-Credit Gap v2 (VCG-R)

*Strategy overhauled 2026-03-23. VIX gate inverted, HDR removed, severity tiers added. See full spec: [`cross_asset_volatility_credit_gap_spec_(VCG).md`](cross_asset_volatility_credit_gap_spec_(VCG).md)*

### Thesis

The volatility complex (VIX/VVIX) reprices faster than cash credit (HYG, JNK, LQD). When **VIX is already elevated (> 28)** and credit markets have not yet repriced, an unresolved divergence exists — credit is artificially stable and catch-up risk is high. VCG-R detects this divergence using a rolling regression model and generates a risk-off overlay signal.

**The edge is timing, not direction.** VCG-R is a risk-budget override — it identifies *when* credit is lagging a vol shock. Fires rarely (~0.26/year) with high conviction.

**Key revision from v1:** The original `VIX < 40` gate was backwards — it excluded the elevated-VIX window (28–40) where divergences are most actionable. VCG-R inverts the gate: `VIX > 28` is required. VVIX is now a severity amplifier (not a gate). The HDR three-gate conjunction is removed.

### Edge Source

- **VIX > 28**: Volatility is elevated — credit-vol divergences are addressable
- **HYG/JNK not yet repriced**: Credit is still lagging the vol signal
- **VCG z-score > 2.5σ**: Statistical confirmation the gap is actionable
- **VVIX severity**: Amplifies tier classification (not a gate)

### VCG-R Metric

Rolling 21-day OLS regresses daily credit changes on VIX and VVIX changes:

```
ΔC_t = α + β₁·ΔVVIX_t + β₂·ΔVIX_t + ε_t
```

The VCG is the standardized residual (unchanged from v1):

```
VCG_t = (ε_t - μ_ε) / σ_ε     (z-score over 63-day trailing window)
```

| VCG Value | Interpretation |
|-----------|---------------|
| VCG > +2.5 | Credit significantly below vol-implied level (RO trigger with VIX > 28) |
| VCG +2.0 to +2.5 | Divergence building — EDR watch state (with VIX > 25) |
| VCG ±2.0 | Normal regime — no signal |
| VCG < -3.5 | Credit overshot vol signal — BOUNCE (counter-signal) |

### Signal Criteria

**Risk-Off Trigger (RO):**

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| VIX | > 28 | Volatility elevated — stress zone active |
| VCG z-score | > 2.5σ | Statistical confirmation of divergence |
| Sign discipline | Both β < 0 | Model consistent with economic prior |

RO = 1 → actionable signal, size per tier.

**Early Divergence Risk (EDR) — watch state:**

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| VIX | > 25 | Approaching stress threshold |
| VCG | 2.0 to 2.5σ | Divergence building but not confirmed |

EDR = 1 → half-Kelly position, monitor for RO.

**Counter-Signal Bounce:**

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| VCG | < -3.5σ | Credit substantially overshot vol model |

BOUNCE = 1 → close HYG puts, consider small tactical long credit.

### Severity Tiers

VVIX is a severity amplifier — it sets the tier, not the gate.

| Tier | Label | VIX Gate | VVIX Level | Action Intensity |
|------|-------|----------|-----------|-----------------|
| **Tier 1** | Severe | VIX > 30 | VVIX > 120 | Maximum hedging — full Kelly, all instruments |
| **Tier 2** | High | VIX > 28 | VVIX > 100 | Standard hedging — full Kelly, primary instruments |
| **Tier 3 / EDR** | Elevated | VIX > 25 | VVIX > 90 | Watch / half-Kelly position |

### Regime Classification

| VIX Range | Regime | Signal State |
|-----------|--------|-------------|
| < 25 | DIVERGENCE | No signal — vol not elevated enough |
| 25–28 | WATCH | EDR possible — approaching RO threshold |
| 28–40 | ACTIVE | Full RO and EDR operational |
| 40–48 | TRANSITION | Signal valid, VCG adj dampened |
| ≥ 48 | PANIC | VCG adj = 0 — panic suppression |

The panic-adjusted signal: `VCG_adj = (1 - Π) × VCG` where Π ramps from 0→1 as VIX goes 40→48.

### Credit Proxies

| Proxy | Type | Notes |
|-------|------|-------|
| HYG | iShares HY Corp Bond | **Primary** — most liquid, purest HY credit |
| JNK | SPDR HY Bond | Alternative — similar exposure |
| LQD | iShares IG Corp Bond | Requires rate-hedging (duration component) |

For LQD, use Treasury-hedged excess returns to isolate pure credit:
```
ΔC*_t = ΔC_t - Duration × ΔYield_UST
```

### Position Structure

**Action sequence when RO = 1:** reduce credit beta → raise quality → add convex hedges.

| Tier | Primary | Alternative | Sizing |
|------|---------|-------------|--------|
| Tier 1 | ATM HYG puts, 2–3 week expiry | Bear put spreads on HYG | Full Kelly, ≤2.5% bankroll |
| Tier 2 | OTM HYG puts (5% OTM), 1–2 week | Bear put spreads | Full Kelly, ≤2.5% bankroll |
| Tier 3/EDR | Small HYG put position | — | Half-Kelly, ≤1.25% bankroll |
| BOUNCE | HYG calls (2–3 week) | Close prior hedges | 25% of RO size |

### Sizing

- Fractional Kelly on estimated gap-closure probability
- Hard cap: 2.5% of bankroll per VCG position (1.25% for EDR)
- Position is a hedge/overlay — sized relative to existing credit exposure
- **All positions require R:R ≥ 2:1** (use bear put spreads if naked puts fail convexity test)

### Exit Criteria

| Condition | Action |
|-----------|--------|
| VCG normalizes (< 1.0) | Close — divergence resolved |
| VCG adj < 1.0 | Close — panic-adjusted divergence resolved |
| Credit sells off (5d return < -1.5%) | Close — catch-up has occurred |
| VIX > 48 | Close — panic regime, VCG adj = 0 |
| Tier drops 1 → 2 | Reduce to Tier 2 sizing |
| BOUNCE fires (VCG < -3.5) | Close puts, optional tactical long credit |
| 5 trading days elapsed | Re-evaluate thesis |

### Production Refinements

1. **Orthogonalize VVIX**: Regress ΔVVIX on ΔVIX first, use residual ν_t in the main model. Isolates pure vol-of-vol shock from spot vol.

2. **Sign discipline**: If β₁ or β₂ flip positive (estimation noise), suppress signal for that day. Expected signs: β₁ < 0, β₂ < 0 (higher vol = weaker credit).

### Scripts

```bash
# Run VCG-R scan (check current divergence state)
python3.13 scripts/vcg_scan.py

# VCG with specific credit proxy
python3.13 scripts/vcg_scan.py --proxy HYG

# VCG with LQD (rate-hedged)
python3.13 scripts/vcg_scan.py --proxy LQD --rate-hedge

# Historical backtest
python3.13 scripts/vcg_scan.py --backtest --days 252

# JSON output
python3.13 scripts/vcg_scan.py --json
```

### Output Reference — Field Definitions

Every field in the VCG-R scan JSON output (`--json`) and HTML report is defined below.

#### Header / Top-Level Fields

| Field | JSON Key | Definition |
|-------|----------|------------|
| **Scan Time** | `scan_time` | ISO 8601 timestamp when the scan ran |
| **Market Open** | `market_open` | Whether US equity market was open at scan time. If `false`, data is from last available close. |
| **Credit Proxy** | `credit_proxy` | The ETF used as the credit variable. Default: `HYG`. Alternatives: `JNK`, `LQD`. |

#### Signal Card Metrics

| Metric | JSON Key | Definition |
|--------|----------|------------|
| **VCG (z-score)** | `signal.vcg` | Standardized residual. **> +2.5** with VIX > 28: risk-off trigger. **+2.0–2.5** with VIX > 25: EDR watch. **< -3.5**: BOUNCE (credit overshot). **±2.0**: normal. |
| **VCG adj** | `signal.vcg_adj` | Panic-adjusted VCG: `(1 - Π) × VCG`. Π ramps 0→1 as VIX goes 40→48. Zero when VIX ≥ 48. Replaces `vcg_div` from v1. |
| **VIX** | `signal.vix` | CBOE VIX — forward-looking 30-day SPX vol. VIX > 28 activates RO gate. |
| **VVIX** | `signal.vvix` | CBOE VVIX — vol-of-vol. Severity amplifier (not a gate). |
| **VVIX Severity** | `signal.vvix_severity` | EXTREME (>140) / VERY_HIGH (120–140) / HIGH (100–120) / ELEVATED (90–100) / NORMAL (<90) |
| **Credit Price** | `signal.credit_price` | Last close of the credit proxy ETF. |
| **5d Return** | `signal.credit_5d_return_pct` | 5-day simple return on credit proxy. Context only — no longer a gate in VCG-R v2. |
| **Risk-Off (RO)** | `signal.ro` | Trade trigger. VIX > 28 AND VCG > 2.5 AND sign_ok. |
| **EDR** | `signal.edr` | Early Divergence Risk. VIX > 25 AND 2.0 < VCG ≤ 2.5 AND sign_ok. |
| **BOUNCE** | `signal.bounce` | Counter-signal: VCG < -3.5 AND sign_ok. Credit overshot. |
| **Tier** | `signal.tier` | 1 = Severe (VIX>30, VVIX>120), 2 = High (VIX>28), 3 = Elevated (EDR). null = no signal. |

#### OLS Model Coefficients

| Coefficient | JSON Key | Definition |
|-------------|----------|------------|
| **α (intercept)** | `signal.alpha` | Expected daily credit return when VIX and VVIX are unchanged. Near zero typically. |
| **β₁ (VVIX)** | `signal.beta1_vvix` | Credit sensitivity to vol-of-vol. Expected: **negative**. Positive → sign_suppressed. |
| **β₂ (VIX)** | `signal.beta2_vix` | Credit sensitivity to spot vol. Expected: **negative**. Positive → sign_suppressed. |
| **Residual (ε)** | `signal.residual` | Raw gap: `ΔC_actual − (α + β₁·ΔVVIX + β₂·ΔVIX)`. Positive = credit stronger than vol implies. |
| **Sign Discipline** | `signal.sign_ok`, `signal.sign_suppressed` | Both betas must be negative. If either flips positive, signal is suppressed. |

#### Attribution Split

| Field | JSON Key | Definition |
|-------|----------|------------|
| **VVIX %** | `signal.attribution.vvix_pct` | % of model-implied credit move driven by VVIX (convexity demand) |
| **VIX %** | `signal.attribution.vix_pct` | % of model-implied credit move driven by VIX (broad vol) |
| **Model Implied** | `signal.attribution.model_implied` | Total model-predicted credit move |

#### Regime & Panic Overlay

| Field | JSON Key | Definition |
|-------|----------|------------|
| **Regime** | `signal.regime` | DIVERGENCE (VIX<25), WATCH (25–28), ACTIVE (28–40), TRANSITION (40–48), PANIC (≥48) |
| **Π (Pi Panic)** | `signal.pi_panic` | `clamp((VIX-40)/8, 0, 1)`. 0 = full signal, 1 = panic suppressed. |
| **Interpretation** | `signal.interpretation` | NORMAL / EDR / RISK_OFF_TIER_1 / RISK_OFF_TIER_2 / BOUNCE / SUPPRESSED |

#### Rolling History Table (last 10 trading days)

| Column | JSON Key | Definition |
|--------|----------|------------|
| **Date** | `history[].date` | Trading date |
| **VIX** | `history[].vix` | VIX close |
| **VVIX** | `history[].vvix` | VVIX close |
| **Credit** | `history[].credit` | Credit proxy close |
| **Residual** | `history[].residual` | Raw model residual ε |
| **VCG** | `history[].vcg` | Standardized residual (z-score) |
| **VCG adj** | `history[].vcg_adj` | Panic-adjusted VCG |
| **β₁** | `history[].beta1` | Rolling 21-day VVIX coefficient |
| **β₂** | `history[].beta2` | Rolling 21-day VIX coefficient |

#### Model Parameters

| Parameter | v1 Value | **VCG-R v2 Value** | Why Changed |
|-----------|----------|---------------------|-------------|
| OLS window | 21 days | 21 days | Unchanged |
| Z-score window | 63 days | 63 days | Unchanged |
| **VCG trigger** | **> 2.0σ** | **> 2.5σ** | Reduces noise spikes |
| **VIX gate** | **< 40** | **> 28** | Inverted — divergences occur with elevated VIX |
| **VVIX gate** | **> 110 (hard gate)** | **Severity amplifier (no gate)** | Was overly restrictive |
| **Credit 5d gate** | **> -0.5% (hard gate)** | **Removed (context only)** | Failed during stress events |
| **HDR state flag** | **Required** | **Removed** | Replaced by VIX > 28 |
| VIX panic threshold | 40–48 | 40–48 | Unchanged |
| Sign discipline | Both β < 0 | Both β < 0 | Unchanged |

Full mathematical specification: [`cross_asset_volatility_credit_gap_spec_(VCG).md`](cross_asset_volatility_credit_gap_spec_(VCG).md)

---

## Strategy 6: Crash Risk Index (CRI)

### Thesis

Systematic CTA funds (~$400B AUM) use vol-targeting: they maintain 10% portfolio volatility by adjusting equity exposure inversely to realized vol. When realized vol doubles, they must halve exposure — creating predictable, mechanical selling cascades ($200B+ in March 2020). The CRI detects when three crash regime signals converge: VIX rising, the Cboe 1-Month Implied Correlation Index (COR1M) spiking, and SPX breaking below its 100-day moving average.

**The edge is predictability, not direction.** CTA selling is mechanical and time-bound (3-5 days). Knowing it's coming allows you to position defensively or profit from the cascade.

### Edge Source

- **VIX + VVIX rising**: Volatility complex repricing — CTAs must adjust
- **COR1M spiking**: The top 50 SPX names are expected to move together — diversification breaks down
- **SPX below 100d MA**: Trend-following CTAs flip from long to short
- **Realized vol > 25%**: Vol-targeting math forces exposure reduction

### CRI Composite Score (0-100)

Four components, each scored 0-25:

| Component | Inputs | 0 (Calm) | 25 (Crisis) |
|-----------|--------|----------|-------------|
| **VIX** | Level + 5d RoC | VIX < 15, flat | VIX > 40, rising fast |
| **VVIX** | Level + VVIX/VIX ratio | VVIX < 90 | VVIX > 140, ratio > 8 |
| **Correlation** | COR1M level + 5-session change | COR1M < 25 | COR1M > 70, spiking |
| **Momentum** | SPX distance from 100d MA | Above MA | 10%+ below MA |

### Signal Levels

| CRI Score | Level | Action |
|-----------|-------|--------|
| 0-24 | LOW | Normal regime, no systematic risk |
| 25-49 | ELEVATED | One or more components stressed, monitor |
| 50-74 | HIGH | Multiple triggered, CTA selling likely imminent |
| 75-100 | CRITICAL | Full crash regime, active systematic deleveraging |

### CTA Exposure Model

```
Exposure = 10% target / Realized_vol
Forced_reduction = max(0, 1 - Exposure)
Est_selling = Forced_reduction × CTA_AUM (~$400B)
```

| Realized Vol | Exposure | Reduction | Est. Selling |
|-------------|----------|-----------|-------------|
| 10% | 100% | 0% | $0B |
| 20% | 50% | 50% | $200B |
| 40% | 25% | 75% | $300B |
| 80% | 12.5% | 87.5% | $350B |

### Crash Trigger Rule

All three must fire simultaneously:
1. SPX < 100-day moving average
2. 20d realized vol > 25% annualized
3. COR1M implied correlation > 60

### COR1M Implied Correlation Signal

The Cboe 1-Month Implied Correlation Index (COR1M) measures the market's expectation of how tightly the 50 largest stocks in the S&P 500 will move together over the next month.
Implementation rule: `scripts/cri_scan.py` must fetch COR1M from IB first, then the official Cboe dashboard historical feed, and only then fall back to Yahoo Finance if both fail.

**What it captures**:
- **Market herding**: Higher COR1M means the market expects the largest SPX names to trade in lockstep.
- **Diversification regime**: Lower COR1M means more single-name dispersion and better diversification potential.
- **Relative options pricing**: COR1M reflects the spread between SPX implied volatility and the weighted average implied volatility of the largest underlying stocks.

**How it is constructed**:
- Uses one-month, approximately at-the-money options.
- Compares SPX implied volatility to the market-cap-weighted implied volatilities of the 50 largest SPX constituents.
- Solves for the average correlation coefficient that reconciles index variance with the weighted variance of the component stocks.
- Produces a percentage-style index value, so a reading like `31.1` means 31.1% implied average correlation.

#### Why COR1M Is Better Than a Sector ETF Proxy

COR1M is the better crash-regime signal because it is:

1. **Forward-looking, not backward-looking.** Sector ETF correlations are realized-price proxies. COR1M comes directly from options markets, so it reflects expected co-movement before the selloff is fully expressed in realized returns.

2. **Closer to the diversification question.** COR1M is explicitly built to quantify the market's expected diversification benefit across the largest SPX names. That is exactly the regime question CRI is trying to answer.

3. **Rooted in index-versus-component volatility.** The signal is derived from the relative pricing of SPX options versus single-name options, which is where institutional hedging and herd behavior actually show up.

### Position Structure (when Crash Trigger fires)

- **Primary**: SPY puts (ATM or slightly OTM, 2-4 week expiry)
- **Alternative**: Bear put spreads on SPY for defined risk
- **Overlay**: Preserve existing downside hedges, tighten stops on long positions
- **Avoid**: Dip-buying until vol mean-reverts

### Sizing

- Fractional Kelly on estimated cascade probability
- Hard cap: 2.5% of bankroll per CRI hedge position
- Position is a portfolio overlay — sized relative to total equity exposure

### Exit Criteria

| Condition | Action |
|-----------|--------|
| CRI normalizes (< 25) | Close hedges — crash risk subsided |
| Realized vol drops below 20% | Close — vol-targeting pressure relieved |
| COR1M < 40 | Close — implied diversification restored |
| 5 trading days elapsed | Re-evaluate — CTA selling is time-bound |

### MenthorQ CTA Positioning (Institutional Data Overlay)

The vol-targeting model estimates CTA exposure from realized vol. MenthorQ provides **actual** institutional CTA positioning data: position sizes, percentiles, and z-scores across indices, commodities, currencies, and bonds.

**Data flow**: Headless browser → screenshot CTA table images → Claude Haiku Vision → structured JSON → daily cache.

**Key fields per asset**: `position_today`, `position_yesterday`, `position_1m_ago`, `percentile_1m`, `percentile_3m`, `percentile_1y`, `z_score_3m`.

**Integration with CRI**: When MenthorQ data is available, the CRI report overlays actual SPX CTA positioning alongside the vol-targeting model. Low percentile + negative z-score confirms deleveraging pressure. When unavailable, falls back to vol-targeting model only.

**Credentials**: `MENTHORQ_USER` and `MENTHORQ_PASS` in the project root `.env` (loaded by the local env loader used by manual fetches and parsed literally by the scheduled CTA sync wrapper). Unquoted secrets with shell metacharacters are safe in the CTA wrapper path because it no longer shell-sources `.env`. Never committed to source.

**Cache**: `data/menthorq_cache/cta_{DATE}.json` — one file per trading day.

**Freshness contract**:
- `data/menthorq_cache/health/cta-sync-latest.json` records the last CTA sync state (`syncing`, `healthy`, `degraded`), `target_date`, attempt count, sanitized error detail, and any captured artifact paths. `data/menthorq_cache/health/history/cta-sync-*.json` preserves the run history, and the latest record is mirrored to `data/service_health/cta-sync.json` for older tooling.
- `/api/menthorq/cta` compares the freshest CTA cache date against the latest closed trading day and triggers one background CTA refresh when the cache is behind.
- `/cta` now renders stale state explicitly. If the cache is behind or missing, the page shows which session is being displayed, which target session is missing, and the last CTA sync failure detail instead of silently presenting old data as current.

### Scripts

```bash
# Run CRI scan (HTML report, includes MenthorQ data if cached)
python3.13 scripts/cri_scan.py

# JSON output
python3.13 scripts/cri_scan.py --json

# Don't open browser
python3.13 scripts/cri_scan.py --no-open

# Fetch MenthorQ CTA data (requires login, ~40s)
python3.13 scripts/fetch_menthorq_cta.py

# Run the hardened CTA sync runtime (used by launchd/service wrappers)
python3.13 scripts/cta_sync_service.py --source manual

# MenthorQ JSON output
python3.13 scripts/fetch_menthorq_cta.py --json

# MenthorQ specific date
python3.13 scripts/fetch_menthorq_cta.py --date 2026-03-06
```

**Cache behavior:** the CRI JSON cache now stores enough trailing SPY closes to reconstruct the prior 20 realized-vol sessions used by `/regime`. Scheduled CRI snapshots and the post-close data refresh both refresh `data/cri.json`, and the API backfills missing `history[].realized_vol` values from cached closes when a newer snapshot is less complete than the legacy cache. CTA sync now also keeps a machine-readable health file plus per-attempt log artifacts so stale MenthorQ data fails loudly and can self-heal through the dedicated sync runtime.

### `/regime` RVOL/COR1M Relationship States

The `/regime` relationship panel adds a second layer on top of the raw 20-session RVOL and COR1M history. It does **not** use fixed absolute cutoffs for the quadrant labels. Instead, it compares the latest RVOL and COR1M values against their own rolling 20-session means:

- `realized_vol_mean = mean(history[].realized_vol)`
- `cor1m_mean = mean(history[].cor1m)`

The latest point is then classified as follows:

| State | Classification Rule | Interpretation |
|-------|---------------------|----------------|
| **Systemic Panic** | `RVOL >= realized_vol_mean` and `COR1M >= cor1m_mean` | Broad realized stress plus elevated implied co-movement. Operators should read this as realized turbulence that the options market still expects to stay systemic. |
| **Fragile Calm** | `RVOL < realized_vol_mean` and `COR1M >= cor1m_mean` | Tape calm, correlation fear elevated. The market may look orderly in realized terms while the options market still prices synchronized downside or crowding risk. |
| **Stock Picker's Market** | `RVOL >= realized_vol_mean` and `COR1M < cor1m_mean` | Realized movement is elevated, but implied correlation remains contained. This is a higher-dispersion state where single-name differentiation still exists. |
| **Goldilocks** | `RVOL < realized_vol_mean` and `COR1M < cor1m_mean` | Both realized volatility and implied correlation are below recent norms. This is the cleanest diversification backdrop in the relationship model. |

**Implementation rule:** when `/regime` has intraday RVOL and/or a live IB COR1M quote, the latest relationship-state label uses those live values for the final point while keeping the rolling 20-session means anchored to the cached history window.

### Source Research

Based on systematic CTA deleveraging dynamics documented in:
[CTA Deleveraging Research](https://chatgpt.com/share/69ab7eee-fe34-8013-b489-7758297da446)

---

## Strategy Interaction

These strategies can be combined:

1. **Flow → LEAP confirmation**: If dark pool flow shows sustained accumulation AND LEAP IV is mispriced low, the signal is stronger.

2. **LEAP → Flow monitoring**: After entering a LEAP position, monitor dark pool flow for early warning of thesis invalidation.

3. **LEAP → Convergence**: If LEAP scan shows single-asset mispricing, check if paired assets are also mispriced → may upgrade to convergence trade for better risk/reward.

4. **Convergence → LEAP**: If convergence resolves quickly, the lagger's IV may still be below long-term fair value → roll into LEAPS.

5. **Position layering**: 
   - Core: LEAP calls for vega exposure (months to years)
   - Tactical: Short-dated options on flow signals (weeks)
   - Relative value: Convergence spreads (weeks to months)
   - Directional: Risk reversals for explicit manager-driven bets (weeks)

6. **Flow → Risk Reversal**: If dark pool flow shows sustained accumulation AND put skew is steep, a risk reversal exploits both the informational edge (flow) and structural edge (skew) simultaneously.

7. **Risk Reversal → Free Trade**: If the underlying rallies and the call appreciates, the short put can be closed for pennies — making the call effectively free (see free trade analyzer).

8. **VCG-R → Portfolio-wide hedge**: When VCG-R signals risk-off (RO=1, Tier 1 or 2), it overrides position-level sizing — reduce credit beta across the book, preserve downside hedges, and add HYG puts as portfolio overlay. Tier 1 = maximum hedging; Tier 2 = standard hedging; EDR = half-Kelly watch position.

9. **VCG-R + Flow**: If VCG-R signals risk-off AND dark pool flow shows distribution in credit-sensitive names, the combined signal strengthens conviction for defensive positioning.

10. **VCG-R → LEAP defense**: When VCG-R is in EDR or RO state (VIX > 25), avoid initiating new LEAP positions in credit-sensitive sectors. Existing LEAPs in those sectors should have stops tightened.

11. **CRI → Portfolio-wide defense**: When CRI crash trigger fires, override all position-level analysis. Reduce equity exposure, add tail hedges (SPY puts), and avoid new entries until CRI normalizes below 25.

12. **CRI + VCG-R convergence**: If CRI is HIGH/CRITICAL AND VCG-R signals risk-off (RO=1) simultaneously, the combined signal indicates both systematic (CTA selling) and credit-specific (vol/credit divergence) risks — maximum defensive posture, Tier 1 sizing.

13. **CRI → LEAP defense**: When CRI > 50, avoid initiating new LEAP positions. CTA deleveraging can push even fundamentally sound names 15-30% lower over 3-5 days.

---

## Risk Management (All Strategies)

| Rule | Limit |
|------|-------|
| Max position size | 2.5% of bankroll (premium or margin) |
| Sizing method | Fractional Kelly (0.25x) for defined-risk; margin-based for undefined |
| Risk type | Defined only (long options, spreads) — **unless manager override** |
| Never | Naked short options, undefined risk — **unless manager override** |
| Max concurrent | 6 positions |
| Kelly > 20% | Restructure (insufficient convexity) |

### Manager Override for Undefined Risk

The "NEVER sell naked options" constraint can be overridden by the manager (human operator) for specific strategies that require it. Currently the only strategy with this override is **Risk Reversal** (Strategy 4).

**Override conditions (ALL must be met):**
1. Manager explicitly requests the structure (e.g., `risk-reversal IWM`)
2. Underlying is a broad index ETF or highly liquid large-cap
3. Position sized to 2.5% of bankroll in **margin**, not premium
4. Hard stop loss set at trade entry
5. Trade logged with `risk_type: "undefined"` and `manager_override: true`

**The agent NEVER initiates undefined-risk trades autonomously.** The `evaluate`, `discover`, and `scan` commands will never recommend naked short options. Only the `risk-reversal` command (which requires explicit human invocation) produces undefined-risk structures.

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
