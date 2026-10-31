# quants.md strategy library: analysis

Status: analysis (2026-10-30, revised 2026-10-31). The adoption design that
first sat here routed every strategy through the Four Gates; the operator
instead asked for a systematic sleeve that runs separately from the gated
pipeline. That design, and the shipped engine, live in
[`docs/quant-strategies.md`](../quant-strategies.md). This file keeps the
library analysis the sleeve's first six strategies were chosen from.

## 1. Verdict

quants.md is a free library of 50 open-source strategies, all from the
QuantJourney Backtester example suite (Apache-2.0,
`github.com/QuantJourneyOrg/quantjourney-bt`, `strategies/`). They are
pedagogical templates, not edges: on the library's own excess-return basis the
best Sharpe is 0.62 and only three strategies exceed 0.45. Nothing beats SPY on
total return except two long-only baskets that took 41% and 48% drawdowns to
do it. None reads flow, options or the vol surface.

Ported into the sleeve first (section 4 of the spec): W25 (best risk-adjusted
result in the library), W05 (best long-only basket), O17 and W04 (the rotation
pair, whose 0.63 versus 0.18 raw Sharpe on a cadence change is the clearest
fragility warning in the set), W17 (the vol-target overlay) and W06 (the
regime switch). Rejected: pairs, cross-sectional long/short, FX, intraday,
order-type demos, risk parity (section 4 below).

## 2. Sources and what could not be verified

- `https://quants.md/` is outside this session's allowed network, as is
  `backtester.quantjourney.cloud` (the results dashboards). The page could not be
  opened. What is known about the site comes from search-engine summaries of it:
  50 strategies, 45 result dashboards, 4 asset classes, longest backtest 26 years,
  top Sharpe W25 at 0.62, Monthly Circuit Breaker at negative 0.45, and the
  statement that the first 50 strategies come from the QuantJourney Backtester
  example suite.
- The repository was cloned read-only. All 50 strategy sources, the walk-forward
  engine, the risk models and the rebalance engine were read directly.
- Performance numbers below are read off the 45 result-preview images the
  repository ships in `docs/strategy-results/` (the "validated 2026-07-11 batch"
  that the site says its dashboards trace). Those images report RAW Sharpe
  (no risk-free deduction). The site's figures reproduce exactly from the images
  with a 2% annual risk-free deduction: W25 0.93 raw becomes 0.62, W10 0.25 raw
  becomes negative 0.46. The "excess" column below applies that same deduction.
  Confidence that this is what quants.md does: moderate (two exact reproductions).
- Costs: the backtester models commissions, slippage and volume participation,
  but the preview images do not state which cost settings the batch used.
  The repo README is explicit that W23 to W25 are price-return proxies with no
  roll, margin, financing or multiplier accounting, and that the long/short
  examples do not model borrow.

## 3. The library, ranked

Window is the backtest start year to 2025 year-end. SPY over the same windows:
2000 start 7.44x, 2001 start 7.94x, 2007 start 6.86x, 2019-05 start 2.58x.
Sharpe raw is as printed on the preview; Sharpe excess is derived (rf 2%).
Rows marked "toy" ran on days or weeks of intraday data and their statistics
are meaningless.

| # | Strategy | Window | Total | Ann | Sharpe raw | Sharpe excess | Max DD |
|---|---|---|---|---|---|---|---|
| W25 | Continuous Futures Trend (MES, MNQ, ZN, CL, GC, ZC proxies; SMA 63/252; inverse-vol; month-end) | 2019-25 | +46% | +5.9% | 0.93 | 0.62 | -9.7% |
| W18 | Vol-Targeted Momentum (15% target) | 2007-25 | +621% | +11.0% | 0.62 | 0.51 | -41.2% |
| O17 | Monthly Rotation (orders) | 2007-25 | +381% | +8.6% | 0.63 | 0.48 | -28.2% |
| W05 | Monthly Inverse Volatility | 2007-25 | +174% | +5.5% | 0.67 | 0.43 | -20.4% |
| W01 | Daily SMA Trend (sector ETFs) | 2000-25 | +537% | +7.4% | 0.55 | 0.40 | -34.7% |
| W11 | Quarterly TE + Cost Gate | 2007-25 | +266% | +7.1% | 0.55 | 0.40 | -25.5% |
| W20 | Risk Parity + 25% cap (sectors) | 2000-25 | +677% | +8.2% | 0.51 | 0.39 | -47.9% |
| W21 | Bollinger Band Reversion | 2000-25 | +487% | +7.0% | 0.51 | 0.36 | -35.2% |
| W17 | Vol-Targeted Trend (10% target) | 2007-25 | +169% | +5.3% | 0.51 | 0.32 | -28.9% |
| W02 | Monthly ETF Trend + 5% drift band | 2007-25 | +138% | +4.7% | 0.48 | 0.28 | -19.4% |
| W03 | Weekly RSI Reversion | 2000-25 | +373% | +6.2% | 0.40 | 0.27 | -49.2% |
| W12 | Daily Partial Drift | 2000-25 | +269% | +5.2% | 0.40 | 0.25 | -36.7% |
| O20 | Futures Donchian Contracts | 2019-25 | +36% | +4.7% | 0.44 | 0.25 | -16.7% |
| W06 | Signal-Change Defensive Rotation | 2007-25 | +132% | +4.5% | 0.37 | 0.21 | -24.6% |
| O18 | Signal-Change Rotation (orders) | 2001-25 | +130% | +3.4% | 0.47 | 0.19 | -16.3% |
| O01 | Market SMA Crossover | 2001-25 | +127% | +3.3% | 0.46 | 0.18 | -16.3% |
| W22 | MACD Trend | 2000-25 | +186% | +4.1% | 0.32 | 0.16 | -35.6% |
| O03 | Limit RSI Dip Buyer | 2001-25 | +95% | +2.7% | 0.42 | 0.11 | -19.1% |
| W19 | Risk Parity (multi-asset ERC) | 2007-25 | +85% | +3.3% | 0.26 | 0.10 | -35.6% |
| W04 | Quarterly Dual Momentum | 2007-25 | +65% | +2.7% | 0.18 | 0.05 | -42.7% |
| O08 | Stop-Limit Protection | 2001-25 | +64% | +2.0% | 0.41 | 0.00 | -14.2% |
| O06 | Protective Stop Loss | 2001-25 | +60% | +1.9% | 0.37 | -0.02 | -16.2% |
| O04 | Limit Trend Pullback | 2001-25 | +57% | +1.8% | 0.51 | -0.06 | -6.9% |
| O07 | Stop-Limit Breakout | 2001-25 | +56% | +1.8% | 0.60 | -0.07 | -6.4% |
| W13 | Pairs, ratio z-score (KO/PEP) | 2000-25 | +49% | +1.5% | 0.26 | -0.09 | -23.6% |
| O14 | OCO Dip or Breakout | 2001-25 | +46% | +1.5% | 0.33 | -0.11 | -13.1% |
| W15 | Cross-Sectional Momentum L/S | 2000-25 | +1% | +0.1% | 0.01 | ~-0.2 | -25.4% |
| O02 | Market RSI Reversion | 2001-25 | +29% | +1.0% | 0.20 | -0.20 | -16.0% |
| O05 | Stop Breakout Entry | 2001-25 | +41% | +1.4% | 0.47 | -0.20 | -10.5% |
| W16 | Cross-Sectional Reversal L/S | 2000-25 | -2% | -0.1% | -0.01 | ~-0.2 | -33.7% |
| O09 | Trailing Stop Trend | 2001-25 | +30% | +1.1% | 0.39 | -0.32 | -7.0% |
| W14 | Pairs, hedge ratio (EWA/EWC) | 2000-25 | +12% | +0.4% | 0.09 | -0.36 | -14.3% |
| O11 | Trailing Stop-Limit | 2001-25 | +28% | +1.0% | 0.39 | -0.39 | -6.2% |
| O12 | Bracket Trend | 2001-25 | +29% | +1.0% | 0.42 | -0.42 | -6.8% |
| O10 | RSI + Trailing Stop | 2001-25 | -12% | -0.5% | -0.09 | -0.45 | -39.0% |
| W10 | Monthly Trend + Circuit Breaker (15% DD, 10-day cooldown) | 2007-25 | +14% | +0.7% | 0.25 | -0.46 | -10.6% |
| O13 | Bracket RSI Reversion | 2001-25 | +9% | +0.3% | 0.09 | -0.51 | -11.6% |
| W23 | FX Time-Series Momentum | 2010-25 | -27% | -2.0% | -0.31 | -0.62 | -29.4% |
| O19 | FX Momentum, standard lots | 2010-25 | -14% | -1.0% | -0.24 | -0.72 | -15.2% |
| W24 | FX Cross-Sectional Momentum | 2010-25 | -34% | -2.6% | -0.58 | -1.03 | -41.4% |
| W07 | Intraday RSI 15m | toy (3 weeks) | +2.6% | n/a | 87.6 | n/a | -1.1% |
| W08 | Intraday EMA Scalp 1m | toy (days) | -0.2% | n/a | -1783 | n/a | -0.2% |
| W09 | Intraday SMA Trend 1h | toy (2 months) | +1.5% | n/a | 4.8 | n/a | -3.5% |
| O15 | Intraday 5m Bracket Reversion | toy (1 month) | +1.1% | n/a | 29.7 | n/a | -1.3% |
| O16 | Intraday 30m Stop Breakout | toy (2 months) | -14% | n/a | -19.3 | n/a | -14.8% |
| WF01-05 | Walk-forward and optimisation workflows | n/a | publish diagnostics, not a curve | | | | |

Observations that drive the picks:

- W25 is the only strategy with both a positive excess Sharpe above 0.6 and a
  drawdown under 10%. Its window (2019-05 to 2025) contains 2022, the best year
  for trend following in decades, and it is a price-return proxy. Treat the
  0.93 as an upper bound, not an expectation.
- The rotation family is fragile to a single parameter: O17 (monthly rebalance)
  prints raw Sharpe 0.63 while W04 (same 12-month rank, quarterly rebalance)
  prints 0.18 over the same window. That is the kind of result a purged
  walk-forward exists to expose, and it is why pick 3 is a gate, not a nicety.
- The risk-overlay family's evidence is mixed. Inverse-vol (W05) improves on the
  equal-weight trend baskets (W01, W02) on Sharpe. Vol targeting (W17) does not
  beat W05. The circuit breaker (W10) cut the drawdown to 10.6% but also cut the
  return to 0.7% a year because it flattens the whole book. Radon only inherits
  the drawdown mechanics, applied to the Kelly budget, not to signal generation,
  so it does not pay W10's return cost.
- Pairs (W13, W14) are flat since 2016 and negative on an excess basis. The
  market-neutral L/S sector examples (W15, W16) are dead. All FX examples lose.
  The order-type demos (O02 to O14) sit in cash most of the time and lose to the
  2% risk-free deduction.
- Nothing here reads dark pool, OTC, sweeps, GEX or the vol surface. Every
  signal is a function of close prices.

## 4. Runner-up and rejected

- Runner-up: O17 / W04 monthly momentum rotation as a second model inside the
  CTA Book engine (which sectors cross the top-N boundary at the next rebalance,
  and the modelled flow that implies). Same reconstruction pattern as pick 1,
  cross-sectional instead of time-series. Deferred because the library's own
  numbers show the family is cadence-fragile (0.63 vs 0.18) and it should go
  through pick 3 first.
- Rejected, pairs (W13, W14): flat since 2016, negative excess Sharpe, and
  Radon's GARCH Convergence already covers the related-asset lag with a vega
  thesis instead of a price-ratio one.
- Rejected, cross-sectional L/S (W15, W16): Sharpe 0.01 and negative 0.01.
- Rejected, FX (W23, W24, O19): every variant loses money.
- Rejected, intraday (W07 to W09, O15, O16): weeks of data, meaningless.
- Rejected, order-type demos (O01 to O14, O18): they demonstrate the fill
  engine, not a signal; most are negative after the risk-free deduction. Radon's
  exit discipline (bracket, trailing, stop) already lives in
  `exit_order_service.py` and the chain builder.
- Rejected, risk parity (W19, W20): 35% and 48% drawdowns; an equal-risk
  long-only book is the opposite of a defined-risk convex book.
