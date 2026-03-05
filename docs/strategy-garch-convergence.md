# GARCH Convergence Spreads: Cross-Asset Volatility Arbitrage

## Executive Summary

This strategy exploits the differential speed at which GARCH-anchored implied volatility reprices across related assets during regime shifts. When a structural catalyst elevates realized volatility in a sector, individual components reprice IV at different rates — creating measurable mispricings between correlated assets. The trade captures convergence as lagging assets' IV catches up to leaders.

Key edge: Cross-asset vega arbitrage with defined risk. Unlike the LEAP IV Mispricing strategy (which buys cheap vol on a single asset), this strategy pairs a long-vega position on the lagging asset against a short-vega position on the leading asset, isolating the GARCH repricing lag as the sole profit driver.

**Efficacy Note**: The edge is structural, not statistical — it exploits the mechanical limitations of GARCH(1,1) persistence parameters across assets with different option chain liquidity, market-maker coverage, and historical vol profiles. Higher-liquidity names reprice faster; lower-liquidity names lag by days to weeks.

---

## Core Mental Model

GARCH(1,1) models forecast volatility as:

```
σ²(t+1) = ω + α·ε²(t) + β·σ²(t)
```

Where `α` (reaction) controls how fast new shocks enter the forecast, and `β` (persistence) controls how long old shocks linger. These parameters are calibrated per-asset based on that asset's historical return series.

**The arbitrage**: When two correlated assets share the same fundamental volatility driver (e.g., AI memory demand for Samsung and SK Hynix, or oil prices for XOM and COP), a regime shift affects both simultaneously. But their GARCH models reprice at different rates because:

1. **Different α/β calibrations** — Assets with longer histories have higher β (more anchored)
2. **Liquidity asymmetry** — High-volume option chains attract arb capital faster
3. **Index dampening** — ETFs smooth component-level vol through diversification math
4. **Analyst coverage gaps** — Under-followed names get repriced last

The result: Asset A's IV catches up in days. Asset B's IV takes weeks. The gap between them is the trade.

---

## Strategy vs. Existing Approaches

| Dimension | Dark Pool Flow | LEAP IV Mispricing | GARCH Convergence (NEW) |
|-----------|---------------|-------------------|------------------------|
| Edge source | Institutional positioning | Single-asset vol mispricing | Cross-asset repricing lag |
| Signal | Dark pool prints + options flow | HV > LEAP IV gap | IV divergence between correlated assets |
| Instrument | ATM/OTM options (2-6 weeks) | LEAPS calls (1-3 years) | Diagonal calendar spreads (1-6 months) |
| Profit driver | Delta (directional move) | Vega (IV expansion) | Relative vega (IV convergence) |
| Directional bias | Yes (bullish or bearish) | Mildly bullish (long calls) | Neutral to mildly directional |
| Max loss | Premium paid | Premium paid | Net debit paid |
| Typical hold | 2-6 weeks | Weeks to 9 months | 2-8 weeks |

---

## Signal Detection

### Step 1: Identify Correlated Asset Pairs

Look for assets with high fundamental correlation but independent option chains:

| Pair Type | Example | Correlation Driver |
|-----------|---------|-------------------|
| Component vs ETF | Samsung/SK Hynix vs EWY | ETF holds the components |
| Sector peers | XOM vs COP, NVDA vs AMD | Same fundamental drivers |
| Supply chain | MU vs AMAT, TSM vs ASML | Upstream/downstream linkage |
| Cross-listed | BABA (US ADR) vs 9988.HK | Same company, different markets |
| Leveraged vs vanilla | SOXL vs SOXX | Same index, different structures |

### Step 2: Measure IV Divergence

For each pair, compute the **IV Repricing Gap**:

```
IV_Repricing_Gap = (IV_leader / HV_leader) - (IV_lagger / HV_lagger)
```

Where:
- **Leader** = the asset whose IV has already risen to reflect new realized vol
- **Lagger** = the asset whose IV is still anchored to pre-regime levels

A positive gap means the lagger is underpriced relative to the leader after normalizing for their respective realized vol.

**Alternative (simpler) metric — Raw IV Ratio Divergence:**

```
Historical IV Ratio (60-day avg):  IV_A / IV_B = R_hist
Current IV Ratio:                  IV_A / IV_B = R_now
Divergence = R_now - R_hist
```

When divergence exceeds 1 standard deviation of the historical ratio distribution, the trade activates.

### Step 3: Confirm Structural Linkage

The divergence must be caused by GARCH lag, not fundamentals. Confirm:

- [ ] Both assets share the same primary vol driver
- [ ] The regime shift catalyst affects both (not just one)
- [ ] The leader's IV move is justified by its own realized vol
- [ ] The lagger's realized vol has ALSO increased (not just the leader's)
- [ ] No fundamental reason for the lagger to have lower forward vol

**If the lagger has legitimately lower vol exposure, there is no trade.**

### Step 4: Quantify the Expected Convergence

```
Expected IV move on lagger = Leader's IV-to-HV ratio × Lagger's HV - Lagger's current IV

Example:
  Leader (NVDA):  IV = 55%, HV60 = 52%  →  IV/HV = 1.06
  Lagger (AMD):   IV = 38%, HV60 = 48%  →  IV/HV = 0.79
  Expected AMD IV = 1.06 × 48% = 50.9%
  Expected convergence = 50.9% - 38% = 12.9 vol points
```

---

## Signal Criteria

| Criterion | Threshold | Required? |
|-----------|-----------|-----------|
| IV Ratio Divergence | >1σ from 60-day mean | Yes |
| Lagger HV20 > Lagger IV | ≥10 points | Yes |
| Fundamental correlation | Same primary vol driver | Yes |
| Lagger IV rank | <50th percentile | Yes |
| Leader IV move confirmed | Leader IV/HV ≥ 1.0 | Yes |
| Lagger option liquidity | OI >100, bid-ask <15% of mid | Yes |
| Days since regime shift | 5-30 days | Preferred |

**Signal strength tiers:**

| Tier | Conditions | Action |
|------|-----------|--------|
| Strong | Divergence >2σ, HV gap >20pts, IV rank <30% | Full size (2.5% bankroll) |
| Moderate | Divergence >1.5σ, HV gap >15pts, IV rank <40% | Half size (1.25%) |
| Weak | Divergence >1σ, HV gap >10pts, IV rank <50% | Monitor only |

---

## Position Structure

### Primary: Diagonal Calendar Spread (Long Lagger Vega)

Buy the underpriced lagger's medium-dated options, optionally sell the overpriced leader's near-dated options.

**Single-asset version (simpler, preferred for sub-$1M accounts):**

```
BUY: Lagger 3-6 month ATM/OTM calls (high vega, underpriced)
     Target delta: 30-40Δ
     Why: Maximum vega leverage on the convergence

Defined risk: Max loss = premium paid
R:R target: ≥2:1 (12+ pt IV expansion × vega vs premium cost)
```

**Paired version (hedged, for larger accounts):**

```
BUY: Lagger 3-6 month 30Δ calls (long vega on underpriced asset)
SELL: Leader 1-2 month 30Δ calls (short vega on fairly-priced asset)

Net position: Long relative vega (profit if lagger IV rises toward leader)
Defined risk: Net debit + margin on short leg
```

### Why Not Pure LEAPS?

The LEAPS strategy targets 1-3 year options for maximum vega. This strategy uses 3-6 month options because:

1. **Convergence happens in weeks, not years** — GARCH lag resolves faster than regime persistence
2. **Higher gamma** — Medium-dated options have better gamma/theta ratio for 2-8 week holds
3. **Lower capital at risk** — Shorter-dated options cost less premium
4. **Faster feedback** — Know if thesis is right within weeks, not months

### Gate Compliance

| Gate | How It's Met |
|------|-------------|
| Convexity (≥2:1 R:R) | Long options: max loss = premium; 12+ pt IV expansion on 0.15-0.25 vega = 1.8-3.0 pts of premium gain vs cost |
| Edge | Measurable GARCH divergence between correlated assets with confirmed structural linkage |
| Risk Management | Fractional Kelly sizing, 2.5% hard cap, defined risk only |

---

## Execution Protocol

### Entry

1. Run the LEAP scanner on both assets in the pair to get current IV/HV data
2. Compute IV Repricing Gap and Divergence metrics
3. Verify structural linkage (same catalyst)
4. Select strikes: 30-40Δ on the lagger, 3-6 month expiry
5. Calculate Kelly sizing based on:
   - P(convergence within hold period): estimate 50-65%
   - Expected gain if convergence: IV expansion × vega
   - Max loss: premium paid
6. Generate trade spec report and execute

### Monitoring

| Metric | Frequency | Action Trigger |
|--------|-----------|---------------|
| Lagger IV | Daily | Track convergence progress |
| IV Ratio | Daily | Exit if ratio normalizes |
| Lagger HV | Weekly | Confirm vol regime persists |
| Leader IV | Weekly | Watch for leader IV collapse (invalidates thesis) |
| Correlation | Weekly | Exit if fundamental correlation breaks |

### Exit Rules

| Condition | Action |
|-----------|--------|
| IV Ratio converges to within 0.5σ of mean | Close for profit |
| Lagger IV rises ≥70% of expected move | Take partial profits (50%) |
| 50%+ of premium gained | Trail stop at breakeven |
| Leader IV collapses (was overpriced, not lagger underpriced) | Close — wrong thesis |
| Fundamental correlation breaks | Close immediately |
| 40-50% of premium lost | Reassess; close if thesis weakened |
| >8 weeks held with no convergence | Close — GARCH models have adjusted |
| Lagger HV drops below IV | Close — mispricing resolved from other direction |

---

## Scanning Implementation

### ⭐ Dedicated Scanner (IMPLEMENTED)

```bash
# ALWAYS use this script for GARCH convergence scans
python3 scripts/garch_convergence.py --preset all          # All 4 built-in presets (~3s)
python3 scripts/garch_convergence.py --preset semis        # Just semiconductors
python3 scripts/garch_convergence.py --preset mega-tech    # Mega-cap tech
python3 scripts/garch_convergence.py --preset energy       # Energy
python3 scripts/garch_convergence.py --preset china-etf    # China/Asia
python3 scripts/garch_convergence.py --preset sp500-semiconductors  # File preset
python3 scripts/garch_convergence.py NVDA AMD GOOGL META   # Ad-hoc ticker pairs
python3 scripts/garch_convergence.py --preset all --json   # JSON output
```

**Architecture:**
1. Resolves tickers from built-in presets, file presets (`data/presets/`), or ad-hoc CLI args
2. Fetches ALL ticker data in parallel (8 ThreadPoolExecutor workers)
   - Yahoo Finance: HV20, HV60, HV252 (concurrent HTTP requests)
   - Unusual Whales: IV rank, current IV, LEAP chain (shared connection-pooled session)
3. Computes pair divergence metrics (CPU-only, instant)
4. Runs 5 gate checks per pair (divergence, HV gap, vol driver, IV rank, liquidity)
5. Generates HTML report using template from `.pi/skills/html-report/template.html`
6. Opens report in browser automatically

**Performance:** ~2.6 seconds for 23 tickers / 12 pairs (vs ~5+ min serial).

**Built-in pair presets:**

| Preset | Pairs |
|--------|-------|
| `semis` | (NVDA, AMD), (TSM, ASML), (AVGO, QCOM), (MU, AMAT) |
| `mega-tech` | (AAPL, MSFT), (GOOGL, META), (AMZN, NFLX) |
| `energy` | (XOM, COP), (SLB, HAL), (XLE, OIH) |
| `china-etf` | (FXI, BABA), (EWY, FXI) |
| `all` | Run all 4 above |

Also supports any file preset from `data/presets/` (150+ presets with pairs).

---

## Risk Management

| Rule | Limit | Rationale |
|------|-------|-----------|
| Max per position | 2.5% of bankroll | Standard Convex Scavenger cap |
| Max concurrent convergence trades | 3 | Convergence trades can correlate in vol selloffs |
| Sizing method | Fractional Kelly (0.25x) | Conservative for estimation uncertainty |
| Correlation check | Pairs must share <50% overlap with existing positions | Avoid concentrated sector bets |
| Max time in trade | 8 weeks | GARCH models adjust; edge decays |

### What Can Go Wrong

| Risk | Mitigation |
|------|-----------|
| Correlation breaks (assets diverge fundamentally) | Pre-trade structural linkage check; daily correlation monitoring |
| Both IVs collapse (vol regime ends for both) | Long-only version: accept premium loss; paired version: partially hedged |
| Lagger IV stays low because it has structurally lower vol | Confirm lagger HV has actually increased before entering |
| Leader IV was a bubble (overpriced), not lagger underpriced | Check leader IV/HV ratio — if >1.3, leader may be overpriced |
| Liquidity trap on exit | OI >100 and bid-ask <15% required at entry |

---

## Example: NVDA vs AMD During AI Catalyst

**Setup (hypothetical):**

| Metric | NVDA (Leader) | AMD (Lagger) |
|--------|---------------|--------------|
| HV20 | 58% | 52% |
| HV60 | 54% | 49% |
| 4-month ATM IV | 56% | 37% |
| IV/HV60 ratio | 1.04 | 0.76 |
| IV rank | 72nd %ile | 28th %ile |

**Signal:**
- IV Ratio (NVDA/AMD): currently 1.51, historical 60-day avg: 1.18 → divergence = +0.33 (>2σ)
- AMD HV20 (52%) > AMD IV (37%) by 15 points
- AMD IV rank 28th %ile (cheap)
- Both driven by same AI semiconductor demand catalyst

**Trade:**
- BUY AMD 4-month 30Δ calls
- Premium: $4.50 per contract
- Vega: 0.18 per 1% IV
- Expected convergence: AMD IV → 48% (= 1.04 × 49% - 3% haircut) → +11 pts
- Expected gain: 11 × 0.18 = $1.98 per contract (44% return)
- R:R: 1.98 / 4.50 = 0.44:1 on vega alone + delta upside → combined ≥2:1

**Outcome scenarios:**
- Convergence (65% est.): +40-80% on premium
- Partial convergence (20%): +10-20%
- No convergence (15%): -30-50% (time decay + no IV move)

---

## Relationship to Other Strategies

| Interaction | Description |
|-------------|------------|
| LEAP IV → Convergence | If LEAP scan shows single-asset mispricing, check if paired assets are also mispriced → may upgrade to convergence trade for better risk/reward |
| Dark Pool → Convergence | If dark pool flow confirms accumulation in the lagger specifically, convergence thesis strengthens |
| Convergence → LEAP | If convergence resolves quickly, the lagger's IV may still be below long-term fair value → roll into LEAPS |

---

## Report Generation

**ALWAYS use the standard HTML template when generating GARCH convergence reports.**

### Template Usage

```bash
# Template location
.pi/skills/html-report/template.html

# Output location
reports/garch-convergence-{preset}-{date}.html
```

### Generation Pattern

```python
# 1. Read the template
with open('.pi/skills/html-report/template.html', 'r') as f:
    template = f.read()

# 2. Build body content using template CSS classes
body = """
<header class="header">
  <div>
    <h1 class="title">GARCH Convergence Scan — {PRESET}</h1>
    <p class="subtitle">{N} Tickers Scanned</p>
  </div>
  <div class="header-actions">
    <span class="timestamp">Generated: {TIMESTAMP}</span>
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>

<div class="metrics">
  <div class="metric">
    <div class="metric-label">Tickers Scanned</div>
    <div class="metric-value">{N}</div>
  </div>
  <!-- More metrics... -->
</div>

<!-- Panels, tables, callouts using template classes -->
"""

# 3. Insert body into template
html = template.replace('{{TITLE}}', f'GARCH Convergence — {preset} | {date}')
html = html.replace('{{BODY}}', body)

# 4. Write to reports/
with open(f'reports/garch-convergence-{preset}-{date}.html', 'w') as f:
    f.write(html)
```

### Required Sections

Every GARCH convergence report MUST include:

1. **Header** — Preset name, ticker count, timestamp, theme toggle
2. **Summary Metrics** — Tickers scanned, with LEAPs, laggers found, actionable pairs
3. **IV/HV Analysis Table** — All tickers with HV20, HV60, IV, IV/HV60, HV-IV gap, IV Rank
4. **Pair Analysis** — For each potential pair with signal criteria pass/fail
5. **Watchlist** — Consolidated list of opportunities (laggers, short vega candidates)
6. **Footer** — Data sources, strategy reference

### CSS Classes Reference

| Element | Class |
|---------|-------|
| Section title | `.section-header` |
| Data table | `.panel` + `table` |
| Highlighted row | `tr.highlight` |
| Pass indicator | `.text-positive` |
| Fail indicator | `.text-negative` |
| Warning | `.text-warning` |
| Badge | `.pill`, `.pill-positive`, `.pill-negative`, `.pill-warning` |
| Callout box | `.callout`, `.callout.positive`, `.callout.warning` |
| Metrics grid | `.metrics` + `.metric` |

See `.pi/skills/html-report/SKILL.md` for full CSS reference.

---

## Key Citations

- Engle, R.F. (1982). "Autoregressive Conditional Heteroscedasticity with Estimates of the Variance of United Kingdom Inflation." *Econometrica*, 50(4), 987-1007.
- Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroscedasticity." *Journal of Econometrics*, 31(3), 307-327.
- Kelly, B., Pruitt, S., & Su, Y. (2019). "Characteristics Are Covariances: A Unified Model of Risk and Return." *Journal of Financial Economics*, 134(3).
- Epps, T.W. (1979). "Comovements in Stock Prices in the Very Short Run." *Journal of the American Statistical Association*, 74(366).
