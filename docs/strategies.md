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
| **Volatility-Credit Gap** | Vol/credit divergence | HY puts, CDX protection | 1-5 days | Defined (long puts/spreads) |
| **Crash Risk Index** | CTA deleveraging | SPY puts, tail hedges | 3-5 days | Defined (long puts/spreads) |

See also: [`strategy-garch-convergence.md`](strategy-garch-convergence.md) for the full GARCH Convergence Spreads specification.
See also: [`VCG_institutional_research_note.md`](VCG_institutional_research_note.md) for the full VCG mathematical specification.

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

### Options Flow Confirmation

Use `fetch_options.py` to confirm or contradict dark pool signals:

```bash
python3 scripts/fetch_options.py [TICKER]
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

### Scripts

```bash
# Scan watchlist for flow signals
python3 scripts/scanner.py

# Discover new candidates market-wide (default)
python3 scripts/discover.py

# Discover from a preset
python3 scripts/discover.py ndx100
python3 scripts/discover.py sp500-semiconductors

# Discover specific tickers
python3 scripts/discover.py AAPL MSFT NVDA

# Fetch dark pool flow (5-day)
python3 scripts/fetch_flow.py [TICKER]

# Fetch options chain + flow analysis
python3 scripts/fetch_options.py [TICKER]

# Full evaluation output (JSON)
python3 scripts/fetch_options.py [TICKER] --json
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
| **5th ⚠️** | Yahoo Finance | **ABSOLUTE LAST RESORT** — only if ALL above fail | Rate limited, unreliable, delayed |

**Yahoo Finance is the ABSOLUTE LAST RESORT. Never use it if IB, UW, Exa, or agent-browser can provide the data.**

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
python3 scripts/garch_convergence.py --preset all          # All 4 built-in presets (~3s)
python3 scripts/garch_convergence.py --preset semis        # Semiconductors only
python3 scripts/garch_convergence.py --preset mega-tech    # Mega-cap tech
python3 scripts/garch_convergence.py --preset energy       # Energy sector
python3 scripts/garch_convergence.py --preset china-etf    # China/Asia
python3 scripts/garch_convergence.py --preset sp500-semiconductors  # File preset
python3 scripts/garch_convergence.py NVDA AMD GOOGL META   # Ad-hoc pairs
python3 scripts/garch_convergence.py --preset all --json   # JSON output
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

### ⚠️ Undefined Risk — Manager Override

This strategy involves selling naked options (short put for bullish, short call for bearish). This is an explicit **manager override** of the "NEVER sell naked options" constraint. The override is justified when:

1. The underlying is a **broad index ETF** (IWM, SPY, QQQ) — no single-name blowup risk
2. The operator has an **explicit directional thesis** with supporting data (DP flow, technicals)
3. Position is **sized to 2.5% margin** — same dollar risk discipline as defined-risk strategies
4. A **hard stop loss** is set at trade entry (e.g., close if spread value reaches –$3.00)
5. The manager **explicitly requests** the structure

**This strategy is NEVER auto-triggered.** It requires the `risk-reversal` command with operator confirmation.

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
python3 scripts/risk_reversal.py IWM

# Bearish risk reversal
python3 scripts/risk_reversal.py SPY --bearish

# Custom parameters
python3 scripts/risk_reversal.py QQQ --bankroll 500000 --min-dte 21 --max-dte 45

# Don't open browser
python3 scripts/risk_reversal.py IWM --no-open

# JSON output (for programmatic use)
python3 scripts/risk_reversal.py IWM --json
```

### Tickers Best Suited

| Type | Tickers | Why |
|------|---------|-----|
| **Index ETFs** | IWM, SPY, QQQ, DIA | Deepest skew, most liquid, no single-name risk |
| **Sector ETFs** | XLK, XLE, XLF, EWY | Decent skew, diversified exposure |
| **Large-cap stocks** | AAPL, MSFT, NVDA, AMZN | Liquid chains, but single-name risk applies |

**Avoid**: Small-caps, illiquid options, earnings-adjacent (IV crush kills both legs).

---

## Strategy 5: Volatility-Credit Gap (VCG)

### Thesis

The volatility complex (VIX/VVIX) reprices faster than cash credit (HYG, JNK, LQD). When volatility spikes but credit markets remain calm, an unresolved divergence exists — credit is artificially stable and catch-up risk is elevated. The VCG detects this divergence quantitatively using a rolling regression model and generates a risk-off overlay signal.

**The edge is timing, not direction.** VCG is a risk-budget override — it tells you *when* to reduce credit exposure, not a standalone directional bet.

### Edge Source

- **VVIX > 110**: Vol-of-vol signals convexity demand in the volatility complex
- **HYG/JNK flat**: Credit hasn't repriced to match the vol signal
- **VIX < 40**: Still in divergence discovery mode (not yet panic transmission)
- **Standardized residual > 2σ**: Statistical confirmation the gap is actionable

### VCG Metric

Rolling 21-day OLS regresses daily credit changes on VIX and VVIX changes:

```
ΔC_t = α + β₁·ΔVVIX_t + β₂·ΔVIX_t + ε_t
```

The VCG is the standardized residual:

```
VCG_t = (ε_t - μ_ε) / σ_ε     (z-score over 63-day trailing window)
```

| VCG Value | Interpretation |
|-----------|---------------|
| VCG > +2 | Credit artificially calm — catch-up risk is high (RISK-OFF) |
| VCG < -2 | Credit has overshot vol signal — tactical exhaustion possible |
| -2 to +2 | Normal regime — no signal |

### Signal Criteria

Two-stage signal: **state flag** (HDR) then **trade trigger** (RO).

**High Divergence Risk (HDR) — state flag:**

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| VVIX level | > 110 | Vol-of-vol elevated |
| 5-day credit return | > -0.5% | Credit hasn't sold off yet |
| VIX level | < 40 | Below panic threshold |

All three must be true simultaneously for HDR = 1.

**Risk-Off (RO) — trade trigger:**

| Criterion | Threshold | Notes |
|-----------|-----------|-------|
| HDR | = 1 | State conditions met |
| VCG | > 2.0 | Standardized residual confirms gap |

RO = 1 means the divergence is statistically large enough to act on.

### Panic Overlay (Rule-of-16)

VIX = 48 implies ~3% daily equity moves — the transition from growth scare to liquidity panic.

| VIX Range | Regime | VCG Behavior |
|-----------|--------|-------------|
| < 40 | Divergence | Full VCG signal — classic unresolved gap |
| 40-48 | Transition | VCG still valid but window to monetize is shorter |
| ≥ 48 | Panic | VCG suppressed — market in panic transmission, not divergence |

The panic-adjusted signal: `VCG_div = (1 - Π) × VCG` where Π ramps from 0→1 as VIX goes 40→48.

### Credit Proxies

| Proxy | Type | Notes |
|-------|------|-------|
| HYG | iShares HY Corp Bond | Primary — most liquid, purest HY credit |
| JNK | SPDR HY Bond | Alternative — similar exposure |
| LQD | iShares IG Corp Bond | Requires rate-hedging (duration component) |

For LQD, use Treasury-hedged excess returns to isolate pure credit:
```
ΔC*_t = ΔC_t - Duration × ΔYield_UST
```

### Position Structure (when RO = 1)

**Action sequence:** reduce credit beta → raise quality → add convex hedges.

- **Primary**: Buy HYG puts (ATM or slightly OTM, 1-2 week expiry)
- **Alternative**: Bear put spreads on HYG/JNK for defined risk
- **Cross-asset**: Short credit vs. duration-matched Treasury
- **Overlay**: Preserve existing equity downside hedges rather than monetizing early

### Sizing

- Fractional Kelly on estimated gap-closure probability
- Hard cap: 2.5% of bankroll per VCG position
- Position is a hedge/overlay — sized relative to existing credit exposure

### Exit Criteria

| Condition | Action |
|-----------|--------|
| VCG normalizes (< 1.0) | Close — divergence resolved |
| Credit sells off (5d return < -1.5%) | Close — catch-up has occurred |
| VIX > 48 | Close VCG positions — regime shift to panic |
| HDR flips to 0 | Close — state conditions no longer met |
| 5 trading days elapsed | Re-evaluate — gap may be structural, not transient |

### Production Refinements

1. **Orthogonalize VVIX**: Regress ΔVVIX on ΔVIX first, use residual ν_t in the main model. Isolates pure vol-of-vol shock from spot vol.

2. **Sign discipline**: If β₁ or β₂ flip positive (estimation noise), hold previous estimate or suppress signal for that day. Expected signs: β₁ < 0, β₂ < 0 (higher vol = weaker credit).

### Scripts

```bash
# Run VCG scan (check current divergence state)
python3 scripts/vcg_scan.py

# VCG with specific credit proxy
python3 scripts/vcg_scan.py --proxy HYG

# VCG with LQD (rate-hedged)
python3 scripts/vcg_scan.py --proxy LQD --rate-hedge

# Historical backtest
python3 scripts/vcg_scan.py --backtest --days 252

# JSON output
python3 scripts/vcg_scan.py --json
```

### Output Reference — Field Definitions

Every field in the VCG scan JSON output (`--json`) and HTML report is defined below.

#### Header / Top-Level Fields

| Field | JSON Key | Definition |
|-------|----------|------------|
| **Scan Time** | `scan_time` | ISO 8601 timestamp when the scan ran |
| **Market Open** | `market_open` | Whether US equity market was open at scan time. If `false`, data is from last available close. |
| **Credit Proxy** | `credit_proxy` | The ETF used as the credit variable in the regression. Default: `HYG` (iShares High Yield Corporate Bond). Alternatives: `JNK`, `LQD`. |

#### Signal Card Metrics (top row of HTML report)

| Metric | JSON Key | Definition |
|--------|----------|------------|
| **VCG (z-score)** | `signal.vcg` | The standardized residual from the rolling OLS regression. Measures how far credit's actual move deviates from what VIX/VVIX predict, normalized to standard deviations. **> +2**: credit is "too calm" — catch-up risk is high (risk-off trigger). **< -2**: credit has overshot the vol signal — tactical exhaustion possible. **-2 to +2**: normal, no signal. |
| **VCG div** | `signal.vcg_div` | Panic-adjusted VCG. Formula: `(1 - Π) × VCG` where Π ramps from 0→1 as VIX goes 40→48. When VIX < 40, VCG div = VCG exactly. When VIX ≥ 48, VCG div = 0 (signal suppressed — market is in panic transmission, not divergence). |
| **VIX** | `signal.vix` | CBOE Volatility Index — forward-looking measure of expected 30-day S&P 500 volatility. Quoted in annualized % points. Divide by 16 (√252) for approximate expected daily move. |
| **VVIX** | `signal.vvix` | CBOE VIX-of-VIX — expected volatility of VIX itself. Measures convexity demand in the volatility complex. VVIX > 110 indicates elevated hedging demand and potential for rapid VIX moves. |
| **Credit Price** | `signal.credit_price` | Last close of the credit proxy ETF (e.g., HYG at $79.69). |
| **5d Return** | `signal.credit_5d_return_pct` | Simple 5-trading-day return on the credit proxy: `(C_t / C_{t-5}) - 1`, expressed as %. Used in the HDR gate — if credit has already sold off (< -0.5%), the "artificially calm" condition fails. |
| **HDR** | `signal.hdr` | High Divergence Risk state flag. Binary (0 or 1). All three conditions must be true simultaneously: VVIX > 110, credit 5d return > -0.5%, VIX < 40. HDR = 1 means the market structure is consistent with unresolved divergence. HDR = 0 means at least one condition failed. |
| **Risk-Off (RO)** | `signal.ro` | Trade trigger. Binary (0 or 1). `RO = 1{VCG > 2} × HDR`. Both the statistical gap AND the market structure conditions must be met. RO = 1 is the actionable signal. |

#### HDR Conditions Table

| Condition | JSON Key | Definition |
|-----------|----------|------------|
| **VVIX > 110** | `signal.hdr_conditions.vvix_gt_110` | Vol-of-vol is elevated — convexity demand in the volatility complex. PASS when VVIX exceeds 110. |
| **Credit 5d Return > -0.5%** | `signal.hdr_conditions.credit_5d_gt_neg05pct` | Credit hasn't already sold off. If credit has dropped more than 0.5% in 5 days, the catch-up has already started and the divergence thesis weakens. PASS when 5d return is above -0.5%. |
| **VIX < 40** | `signal.hdr_conditions.vix_lt_40` | Below panic threshold. VIX under 40 means the market is in divergence-discovery mode, not panic-transmission mode. PASS when VIX is below 40. |

#### OLS Model Coefficients

These come from the rolling 21-day ordinary least squares regression: `ΔC_t = α + β₁·ΔVVIX_t + β₂·ΔVIX_t + ε_t`, where ΔC, ΔVVIX, ΔVIX are daily log returns.

| Coefficient | JSON Key | Definition |
|-------------|----------|------------|
| **α (intercept)** | `signal.alpha` | Regression intercept. The expected daily credit log-return when both VIX and VVIX are unchanged. Typically near zero. |
| **β₁ (VVIX)** | `signal.beta1_vvix` | Sensitivity of credit returns to vol-of-vol changes. Expected sign: **negative** (higher VVIX → weaker credit). When positive, it indicates estimation noise in the 21-day window — sign discipline suppresses the signal. |
| **β₂ (VIX)** | `signal.beta2_vix` | Sensitivity of credit returns to spot implied vol changes. Expected sign: **negative** (higher VIX → weaker credit). Same sign discipline applies. |
| **Residual (ε)** | `signal.residual` | Today's model residual — the gap between what credit actually did and what the model predicted. `ε = ΔC_actual - (α + β₁·ΔVVIX + β₂·ΔVIX)`. Positive: credit stronger than expected. Negative: credit weaker than expected. This is the raw (un-standardized) input to the VCG z-score. |
| **Sign Discipline** | `signal.sign_ok`, `signal.sign_suppressed` | Whether beta coefficients have the expected negative signs. If either β₁ or β₂ is positive, `sign_ok = false` and `sign_suppressed = true` — the signal is suppressed for that day regardless of VCG magnitude. This prevents acting on a model that contradicts the economic prior (higher vol = weaker credit). |

#### Attribution Split

| Field | JSON Key | Definition |
|-------|----------|------------|
| **VVIX Component** | `signal.attribution.vvix_component` | The portion of the model-implied credit move attributable to vol-of-vol: `β₁ × ΔVVIX_t`. Raw log-return units. |
| **VIX Component** | `signal.attribution.vix_component` | The portion attributable to spot implied vol: `β₂ × ΔVIX_t`. Raw log-return units. |
| **VVIX %** | `signal.attribution.vvix_pct` | VVIX component as a percentage of total model-implied move. Tells you whether the gap is being driven by convexity demand (high VVIX %) or by a broad rise in spot vol (high VIX %). |
| **VIX %** | `signal.attribution.vix_pct` | VIX component as a percentage of total model-implied move. |
| **Model Implied** | `signal.attribution.model_implied` | Total model-predicted credit move: `α + β₁·ΔVVIX + β₂·ΔVIX`. The residual is `ΔC_actual - model_implied`. |

#### Regime & Panic Overlay

| Field | JSON Key | Definition |
|-------|----------|------------|
| **Regime** | `signal.regime` | Current market regime based on VIX level. `DIVERGENCE` (VIX < 40): vol/credit divergences are tradeable. `TRANSITION` (40 ≤ VIX < 48): divergences still exist but the window to monetize is shorter. `PANIC` (VIX ≥ 48): liquidity panic, divergence signal suppressed. |
| **Π (Pi Panic)** | `signal.pi_panic` | Panic transition scalar. `Π = clamp((VIX - 40) / 8, 0, 1)`. 0 = full divergence mode, 1 = full panic mode. Used to compute VCG div. |
| **Interpretation** | `signal.interpretation` | Human-readable signal state. One of: `NORMAL` (no signal, VCG within ±2), `HDR_ACTIVE` (state conditions met but VCG < 2), `RISK_OFF` (RO = 1, actionable signal), `SUPPRESSED` (signal would fire but sign discipline prevents it). |

#### Rolling History Table (last 10 trading days)

| Column | JSON Key (per entry) | Definition |
|--------|---------------------|------------|
| **Date** | `history[].date` | Trading date (YYYY-MM-DD) |
| **VIX** | `history[].vix` | VIX close on that date |
| **VVIX** | `history[].vvix` | VVIX close on that date |
| **Credit** | `history[].credit` | Credit proxy close price |
| **Residual** | `history[].residual` | Raw model residual ε for that date |
| **VCG** | `history[].vcg` | Standardized residual (z-score) for that date |
| **VCG div** | `history[].vcg_div` | Panic-adjusted VCG for that date |
| **β₁** | `history[].beta1` | Rolling 21-day β₁ (VVIX coefficient) as of that date |
| **β₂** | `history[].beta2` | Rolling 21-day β₂ (VIX coefficient) as of that date |

#### Model Parameters (fixed)

| Parameter | Value | Why |
|-----------|-------|-----|
| OLS window | 21 trading days | One calendar month of data. Balances responsiveness (shorter = more reactive) vs stability (longer = less noisy). |
| Z-score window | 63 trading days | Three calendar months. Longer than the OLS window for more stable standardization thresholds. |
| VCG trigger | > 2σ | Two standard deviations — ~2.3% probability under normality. Conservative enough to avoid frequent false positives. |
| VVIX threshold | 110 | Empirical level above which vol-of-vol indicates elevated hedging demand. Below 110, the vol complex is not stressed enough for a meaningful divergence. |
| Credit 5d threshold | -0.5% | If credit has already dropped more than 0.5% in a week, the catch-up is underway and the "artificially calm" premise fails. |
| VIX panic threshold | 40-48 | Rule-of-16: VIX 48 ≈ 3% daily equity moves. The transition from growth scare to liquidity panic where divergence signals lose predictive power. |

Full mathematical specification: [`VCG_institutional_research_note.md`](VCG_institutional_research_note.md)

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

**Credentials**: `MENTHORQ_USER` and `MENTHORQ_PASS` in project root `.env` (loaded via `python-dotenv`). Never committed to source.

**Cache**: `data/menthorq_cache/cta_{DATE}.json` — one file per trading day.

### Scripts

```bash
# Run CRI scan (HTML report, includes MenthorQ data if cached)
python3 scripts/cri_scan.py

# JSON output
python3 scripts/cri_scan.py --json

# Don't open browser
python3 scripts/cri_scan.py --no-open

# Fetch MenthorQ CTA data (requires login, ~40s)
python3 scripts/fetch_menthorq_cta.py

# MenthorQ JSON output
python3 scripts/fetch_menthorq_cta.py --json

# MenthorQ specific date
python3 scripts/fetch_menthorq_cta.py --date 2026-03-06
```

**Cache behavior:** the CRI JSON cache now stores enough trailing SPY closes to reconstruct the prior 20 realized-vol sessions used by `/regime`. Scheduled CRI snapshots and the post-close data refresh both refresh `data/cri.json`, and the API backfills missing `history[].realized_vol` values from cached closes when a newer snapshot is less complete than the legacy cache.

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

8. **VCG → Portfolio-wide hedge**: When VCG signals risk-off, it overrides position-level sizing — reduce credit beta across the book, preserve downside hedges, and add HYG puts as portfolio overlay.

9. **VCG + Flow**: If VCG signals risk-off AND dark pool flow shows distribution in credit-sensitive names, the combined signal strengthens conviction for defensive positioning.

10. **VCG → LEAP defense**: When VCG is elevated, avoid initiating new LEAP positions in credit-sensitive sectors. Existing LEAPs in those sectors should have stops tightened.

11. **CRI → Portfolio-wide defense**: When CRI crash trigger fires, override all position-level analysis. Reduce equity exposure, add tail hedges (SPY puts), and avoid new entries until CRI normalizes below 25.

12. **CRI + VCG convergence**: If CRI is HIGH/CRITICAL AND VCG signals risk-off simultaneously, the combined signal indicates both systematic (CTA selling) and credit-specific (vol/credit divergence) risks — maximum defensive posture.

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
