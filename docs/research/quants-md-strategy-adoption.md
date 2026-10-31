# quants.md strategy library: analysis and Radon adoption design

Status: proposal (2026-10-30). Not implemented. No code in this PR.

## 1. Verdict

quants.md is a free library of 50 open-source strategies. Every one of them is the
example suite of the QuantJourney Backtester (Apache-2.0,
`github.com/QuantJourneyOrg/quantjourney-bt`, `strategies/`). They are pedagogical
templates (SMA crosses, RSI dips, rotations, pairs, order-type demos), not edges.

The strongest counterargument to adopting any of them as a Radon strategy: none of
the 50 is a flow signal, so none can clear Gate 2 on its own. On the library's own
excess-return basis the best Sharpe is 0.62 and only three strategies exceed 0.45.
Nothing beats SPY on total return except two long-only baskets that took 41% and
48% drawdowns to do it. If the ask were "three strategies to run as-is", the honest
answer is none.

What the library does hold is three pieces of machinery Radon lacks, and they are
the three picks:

| Pick | Library source | What Radon gets | Confidence it adds value |
|---|---|---|---|
| 1. CTA Book | W25 Continuous Futures Trend (best Sharpe in the library, raw 0.93, excess 0.62, max DD 9.7%) | A reconstruction of the systematic trend-following book: per-asset signal, inverse-vol weight, the price level that flips each model, and the modelled month-end rebalance flow. Trades the flip, with dark-pool confirmation, in defined-risk verticals. | High that it fits the thesis; moderate on edge until walk-forward says so |
| 2. Book Risk Overlay | W05 Inverse Vol (second-best Sharpe), W17 Vol-Target, W10 Circuit Breaker | Portfolio-level vol scaling of the Kelly deployed budget (de-risk only, never lever) and a drawdown circuit breaker with cooldown and hysteresis. Radon's current halt has no cooldown and flaps. | High for the cooldown fix; moderate for vol scaling |
| 3. Purged walk-forward admission | WF03 Anchored + pre-OOS purge, WF01 Rolling | Train/test folds with label purge, in-fold threshold search, OOS fold stability. Radon's six strategies have hand-set thresholds and quoted win rates with no out-of-sample evidence. Picks 1 and 2 enter through this gate. | High |

Everything else in the library is rejected in section 8.

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

## 4. Selection criteria

1. Four Gates: the output must be a defined-risk, R:R at or above 2, Kelly-sized
   ticket that is only ACTIONABLE with a flow confirmation (Gate 2). A price-only
   rule can nominate; it cannot trade. This is the bounce-setup precedent
   (`docs/bounce-setup.md`: "nominates candidates only").
2. Thesis fit: Radon reconstructs market structure to find where mechanical flow
   will come from. A strategy earns its place by telling Radon what a large class
   of systematic money holds and when it must trade, not by being a strategy
   Radon runs itself.
3. Data in hand: IB daily bars (`price_history_daily`, migration 0029, fed by
   rv-ratio, bpi, vixcor and the portfolio-risk backfill ladder), UW dark pool
   prints on ETFs, MenthorQ CTA positioning (`fetch_menthorq_cta.py`), Flex NAV
   history (`nav_snapshots`, migration 0035).
4. Incremental cost against existing seams: `scripts/backtest/` (engine, metrics,
   registry, replay), `scripts/kelly.py` (budget, drawdown halt), the
   new-indicator skill (`.claude/skills/new-indicator/SKILL.md`), the regime rail.

## 5. Pick 1: CTA Book (from W25)

### 5.1 What W25 does (exact, from `example_weights_25_continuous_futures_trend.py`)

```
fast   = SMA(close, 63)       slow = SMA(close, 252)
signal = sign(fast - slow)    (0 until both windows are full)
vol63  = std(pct_change(close), 63) * sqrt(252)
raw    = signal / vol63
weight = raw / sum(|raw|)     (unit gross, long/short)
rebalance at business month-end (BME)
universe: MES=F, MNQ=F, ZN=F, CL=F, GC=F, ZC=F (provider continuous series)
```

Radon does not run this as a book. Radon models it as the book that trend
followers run, and asks two questions of every asset every day: how far is price
from flipping this model, and how much must the model trade at the next
rebalance.

### 5.2 Outputs per asset per session

| Field | Definition |
|---|---|
| `signal` | sign(SMA63 - SMA252) |
| `gap_pct` | (SMA63 - SMA252) / SMA252 |
| `gap_slope_20` | 20-session linear slope of `gap_pct` |
| `days_to_flip` | abs(gap_pct) / abs(gap_slope_20) when the slope points at zero, else null; capped at 120 |
| `flip_price_k` for k in {1, 5, 21} | the constant close over the next k sessions at which SMA63 equals SMA252. Closed form: `X_k = (4 * A_k - B_k - 252 * (SMA63 - SMA252)) / (3k)`, where `A_k` is the sum of the oldest k closes in the 63-window and `B_k` the sum of the oldest k closes in the 252-window. For k = 1 this is `(4 * P[t-62] - P[t-251] - 252 * (SMA63 - SMA252)) / 3`. |
| `flip_distance_pct` | `flip_price_5 / close - 1` |
| `vol63`, `weight` | as W25 |
| `weight_next_bme` | the weight the model would hold if today's signal and vol persist to month-end |
| `modelled_flow_usd` | `(weight_next_bme - weight_at_last_bme) * AUM_trend`, where `AUM_trend = CTA_AUM_BN * exposure_pct / 100` from `cri_scan.cta_exposure_model` (so a realised-vol spike de-levers the whole book, consistent with CRI). Source label `APPROX`, the same convention `strength_confirmation_scanner.py` uses. |
| `menthorq_pctile`, `menthorq_agrees` | the MenthorQ CTA percentile for the matching row (index, commodity tables) and whether its sign agrees with `signal`. Disagreement is displayed, never hidden: it means real CTAs use different lookbacks than this replica. |

### 5.3 Universe and data

Phase 1 uses ETF proxies that IB serves as stocks and that already flow through
`fetch_daily_closes` (IB, then Robinhood, then UW) and `price_history_daily`:
SPY (MES), QQQ (MNQ), IEF (ZN), USO (CL), GLD (GC), CORN (ZC), plus the W17/W05
multi-asset basket EFA, EEM, TLT, DBC, VNQ so the equity, rates, FX-proxy and
commodity legs of the book are all represented. USO and CORN carry roll
distortion; so do the provider continuous series W25 uses, so the proxy is no
worse than the original.

Phase 2 adds IB continuous futures (`ContFuture`) daily bars for the six
contracts. `scripts/clients/ib_client.py` has no `ContFuture` helper today; it
needs one, bounded with `asyncio.wait_for(..., timeout=15)` per the IB request
rule, running under the shared evening history lock
(`flock /run/lock/radon-ib-history-5669.lock`, client IDs 56 and 69) because
every scanner ID in 50 to 69 is already assigned.

Stage 1 is a Turso read (`read_price_history_closes`, chunked). Missing or short
symbols are backfilled through the mandated ladder and persisted with
`upsert_price_history_rows`, exactly as `portfolio_risk.py` does, so the second
run is a pure read. Minimum history is 315 sessions (252 + 63); the first run
backfills 2 years from IB. Yahoo is not in the path.

### 5.4 Storage, API, UI, timer

- Migration `0106_cta_book.sql`: `cta_book_history (symbol, date, close, sma_fast,
  sma_slow, signal, vol63, weight, gap_pct, flip_price_1, flip_price_5,
  flip_price_21, days_to_flip, modelled_flow_usd, recorded_at)` PK `(symbol,
  date)`, DESC index on date. Latest payload via `scan_snapshots` service
  `cta-book`. Writer `upsert_cta_book_rows` in `scripts/db/writer.py` (chunked
  multi-row INSERT per the Hrana rule).
- Script `scripts/cta_book_scan.py`: pure functions `trend_signals`,
  `inverse_vol_weights`, `flip_price`, `days_to_flip`, `modelled_rebalance_flow`,
  `build_payload`; stdout reserved for `--json`; heartbeat `cta-book`.
- Route `web/app/api/cta-book/route.ts` through `dbFirstRead`, `missing: true`
  at 200, freshness window from `serviceHealthWindows.ts` ("closed" bucket).
- Tab `/regime/cta-book` in the Positioning rail group (`regimeRail.ts`,
  `RegimePanel.tsx`). Panel: per-asset strip (signal, gap, days to flip, flip
  price, modelled flow, MenthorQ agreement), a `CriHistoryChart` of `gap_pct`
  per selected asset with the flip line at zero, `FreshnessRail` with
  `CTA_BOOK_REFRESH`.
- Candidate rows (the trade surface) go on the same tab, not on the scanner:
  the series is the product, the candidates are derived from it.
- Timer `radon-cta-book.timer`: `Mon..Fri *-*-* 16:25:00 America/New_York`
  (20:25 UTC EDT, 21:25 UTC EST; both slots are free in `cloud/services/`,
  and the ET anchor follows `radon-ma-ratio.timer`). Service `Type=oneshot`,
  `EnvironmentFile=/etc/radon/env`, `RADON_DB_NO_REPLICA=1`, venv python called
  directly. Register in `setup-vps.sh`, `installed-units.sha256`,
  `test_systemd_services.py`, `refreshSchedule.ts`, `watchdog/services.py`.

### 5.5 Trade expression (Four Gates)

```
nominate   abs(flip_distance_pct) <= 3%  AND  days_to_flip <= 10
           direction = sign of the forthcoming flip (opposite of current signal)
Gate 2     dark-pool read for the ETF from the flow scanner's latest snapshot
           (data/scanner.json / scanner_snapshots, the bounce-setup pattern):
           direction must match AND flow_strength > 50
           (DARK_POOL_FLOW_STRENGTH_THRESHOLD). Otherwise the row is WATCH.
Gate 1     debit vertical in the flip direction on the ETF, 3 to 6 weeks out
           so the next BME rebalance sits inside the life of the trade;
           width chosen so max_gain / max_loss >= 2. Chain deep-link
           ?deck=c&src=cta-book (a new src value; the indicators README says a
           reused src mislabels the builder).
Gate 3     kelly_ticket as today. p_source "estimated" with the configured
           haircut until the walk-forward harness has >= 30 OOS trades for the
           cta_flip rule, then "measured".
Exit       the model flipped (thesis realised) or the flip price moved more than
           3% away for 3 sessions (thesis invalidated) or 5 DTE.
```

The edge claim is the same one CRI already makes: predictability of mechanical
flow, not direction. The flip is the catalyst; the dark pool print is the
evidence that someone is already positioning for it.

### 5.6 Backtest wiring

Register `cta_flip` in `scripts/backtest/strategies.py` with a loader that reads
`cta_book_history` (or an injected history for tests) and an entry rule that
returns +1 or -1 under the nomination conditions. Because the history is pure
price math it can be backfilled 10 years from IB and walk-forwarded on day one.
The flow-confirmation leg cannot be replayed until `ticker_flow_history` covers
ETFs; the registry must say so (`insufficient_data` for the confirmed variant),
the same honesty the dark-pool strategy already practises.

### 5.7 Tests (write first)

- `scripts/tests/test_cta_book.py`: synthetic close series where the crossing
  date is known; assert `signal`, `gap_pct`, `flip_price_1` (set tomorrow's
  close to it and check SMA63 == SMA252 to 1e-9), `flip_price_5`, `days_to_flip`
  monotone as price approaches, weights sum to unit gross, `modelled_flow_usd`
  sign and magnitude against a hand-built expectation; migration executed into
  in-memory sqlite; writer idempotency; no network.
- `scripts/tests/test_backtest_strategies_wired.py`: `cta_flip` fires on a
  synthetic series only when both conditions hold, never sees future dates.
- vitest: route (Turso beats disk, `missing: true` at 200), panel (loader label,
  strip values, chart title, NaN guard, freshness rail), `regime-tab-routes`,
  `service-health-windows`, `refresh-schedule`, `assistant-catalog` pins.
- Playwright `web/e2e/cta-book-tab.spec.ts` with route mocks, screenshot evidence.

## 6. Pick 2: Book Risk Overlay (from W05, W17, W10)

### 6.1 What Radon has and what it lacks

`scripts/kelly.py` already enforces: half Kelly default, a 2.5% per-position cap,
a 20% deployed cap (`portfolio_capacity`), and a drawdown halt that refuses
tickets when `(nav_peak - nav_now) / nav_peak >= 0.15`. `scripts/portfolio_risk.py`
flags correlated clusters but never resizes.

Two gaps:

1. The halt has no memory. The session after NAV ticks 0.1% above the line,
   tickets are admitted again; the session after, refused again. W10's
   rebalance policy fixes exactly this with `circuit_breaker_cooldown_days` and
   a forced re-evaluation on exit (`backtester/portfolio/rebalance.py`,
   `max_drawdown_trigger`, `max_drawdown_action` in `{flatten, halve}`).
2. The deployed budget is a constant. A book whose realised P&L vol has doubled
   still gets 20% of bankroll to deploy. W17's `VolTargetModel` (lookback 63,
   strictly prior window, scale = target / realised) is the standard overlay.

### 6.2 Design

Module `scripts/book_overlay.py`, pure functions, no I/O:

```
book_vol_63(nav)        std(daily NAV returns, 63 prior sessions) * sqrt(252)
scale(book_vol)         clamp(target_vol / book_vol, floor=0.25, ceiling=1.0)
                        ceiling is 1.0, never W17's 1.5: the overlay only
                        de-risks; it never raises the deployed cap
effective_deployed      max_deployed_pct * scale   -> portfolio_capacity
breaker(nav, state)     drawdown >= halt_pct  -> state.halted, cooldown_until =
                        today + cooldown_days (sessions)
                        re-arm only when cooldown elapsed AND
                        drawdown < halt_pct - rearm_hysteresis
                        action "flatten": refuse every opening ticket
                        action "halve":   scale = min(scale, 0.5)
```

The overlay never places, modifies or cancels an order. "Flatten" means no new
opening risk; closing stays open through the existing verified-close exemption.
This is the same stance `portfolio_risk.py` takes ("a GUARDRAIL, not an optimizer").

W05's inverse-vol tilt across concurrent tickets is deliberately left out of
phase 1. An option premium already scales with the underlying's vol, so Kelly's
odds term carries most of that information; the tilt would add a second vol
input for little effect. It can be revisited if `kelly_size_batch` callers show
contested capacity.

### 6.3 Wiring

- Inputs: `read_nav_snapshots(account_id=...)` gives `total_net_liq` by
  `report_date` (Flex-fed). The daily job `scripts/book_overlay_refresh.py`
  computes `book_vol_63`, `scale`, breaker state, writes
  `book_overlay_state` (migration `0107_book_overlay.sql`: one row, `as_of`,
  `book_vol_63`, `scale`, `halted`, `cooldown_until`, `peak_nav`, `action`),
  mirrors `data/book_overlay.json`, heartbeats `book-overlay`. It runs after the
  NAV ingest; the slot is chosen against the Flex delivery time in
  `docs/cloud-services.md`.
- `kelly_ticket` gains `book_state: Optional[dict]`. Evaluate M6 passes the
  Turso-read state. The order-path guard (`kelly_guard.py`) reads the disk
  mirror only: the order path never does inline I/O (`app_preferences.py`
  rule 2). Refusal reasons: `DRAWDOWN_HALT` (unchanged), new `COOLDOWN`, new
  `BOOK_VOL_SCALED` surfaced as a capacity reason with the scale in the payload.
- Preferences (registry entries in `scripts/app_preferences.py`, each with a hard
  band and a reader in `kelly.py`, which `TestRegistryIsHonest` pins):
  `RADON_BOOK_VOL_TARGET` default 0.15 band [0.05, 0.30];
  `RADON_BOOK_CB_COOLDOWN_DAYS` default 10 band [1, 30];
  `RADON_BOOK_CB_REARM_PCT` default 0.12 band [0.05, 0.149];
  `RADON_BOOK_CB_ACTION` default `flatten`.
  `RADON_KELLY_DRAWDOWN_HALT_PCT` stays where it is.
- Backtest: the overlay is a position multiplier series the engine can apply to
  any registered strategy (`walk_forward_backtest` already accepts a fractional
  position), so its effect on CRI, VCG and dark-pool flow is measurable before
  it touches production.

### 6.4 Tests

- Synthetic NAV paths: calm (scale 1.0); vol doubles (scale 0.5, never above
  1.0 when vol halves); drawdown 16% halts; day 11 at 14% stays halted
  (hysteresis); 11% re-arms; NAV oscillating around 15% produces one halt and
  one re-arm, not a flap per session; `halve` action yields scale 0.5.
- `kelly_ticket` refusal reasons for each state; capacity math uses the scaled
  cap; a missing or stale state (older than two sessions) is treated as
  `scale = 1.0, halted = false` with a `book_overlay: stale` note, never a
  silent refusal and never a silent widening.
- Migration into in-memory sqlite; registry bands; disk mirror read path.

## 7. Pick 3: Purged walk-forward admission (from WF03, WF01)

### 7.1 Why this ranks above the remaining signal templates

Radon's backtester (`scripts/backtest/engine.py`) is a single-pass,
look-ahead-free replay with hard-coded thresholds: CRI realised vol 25 and COR1M
60, VCG VIX 28 and z 2.5, dark pool streak 3 and strength 50. `docs/strategies.md`
quotes win rates (20 to 40%, 40 to 55%, 50 to 65%) that no out-of-sample run has
produced. The library's own numbers show how fragile unvalidated parameters are
(section 3). WF03's lesson applies directly: each origin's label is a forward
return over `horizon` sessions, so the last `horizon` training origins before a
test window leak the test window's returns into training.

### 7.2 Design

Module `scripts/backtest/walkforward.py`:

```
WalkForwardConfig(scheme in {anchored, rolling, expanding},
                  train_months=24, test_months=6, step_months=6,
                  purge_bars=horizon, extra_pre_oos_purge_pct=0.0)
folds(series)     month-aligned train/test windows; drop the last purge_bars
                  training origins before each test window
tune(fold)        grid search the strategy's declared param_grid on the train
                  window, score by expectancy after costs
score(fold)       OOS metrics with (a) tuned params and (b) the documented
                  params, so the delta says whether the hand-set values were
                  near the optimum at all
aggregate         per-fold table, oos_positive_fold_frac, oos_sharpe_median,
                  n_oos_trades, concatenated OOS equity -> compute_metrics
```

Registry additions in `strategies.py`: `param_grid` per strategy (CRI
`realized_vol {20,25,30}` x `cor1m {50,60,70}`; VCG `vix {25,28,32}` x
`vcg {2.0,2.5,3.0}`; dark pool `streak {2,3,4}` x `strength {40,50,60}`;
`cta_flip` `flip_distance {2,3,4}%` x `days_to_flip {5,10,15}`).

Admission rule, written into `docs/strategies.md` and enforced by the
registry: a strategy's trade surface (ACTIONABLE rows, chain deep-links) is
enabled only when `oos_positive_fold_frac >= 0.6`, `oos_sharpe_median > 0` and
`n_oos_trades >= 30` after costs. Below that the registry row carries
`validation: insufficient` and the UI shows it.

CLI and API: `backtest_run.py --strategy cri --walkforward --train-months 24
--test-months 6 --purge auto`; persisted to `backtest_runs` with `mode =
'walkforward'` (`0018_backtest_runs.sql` has no such column; `0108_backtest_mode.sql`
adds `mode TEXT NOT NULL DEFAULT 'single'` and widens the primary key);
`/api/backtest/[strategy]?mode=walkforward`; the existing `backtest` tab in the
Models rail gains a fold table and the OOS curve.

### 7.3 Known data limits

The CRI and VCG series are the trailing `history` lists embedded in the latest
snapshot; if they are shorter than three years the folds are thin and the
harness must say so (`insufficient_data`, never a thin number dressed as a
result). Dark pool replay is already `insufficient_data` in production because
`ticker_flow_history` lacks a price column. The first strategy with ten years
of replayable history is `cta_flip`, which is one more reason to build pick 1
and pick 3 together.

### 7.4 Tests

- Purge correctness: for every fold, no training origin's label window
  `[origin, origin + horizon]` intersects the test window.
- Overfit detector: a synthetic series where a threshold fits noise in-sample
  yields `oos_positive_fold_frac < 0.5`.
- Fold boundaries deterministic by month; anchored and rolling schemes produce
  the documented window counts for a known series length.
- Each fold calls `walk_forward_backtest`, so the no-look-ahead tests already in
  `test_backtest_engine.py` cover the inner loop.

## 8. Runner-up and rejected

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

## 9. Build plan

Order matters: the gate ships first so the two new strategies enter through it.

| Phase | PR | Scope | Est. |
|---|---|---|---|
| 0 | `feat/backtest-walkforward` | `walkforward.py`, registry `param_grid`, CLI flag, API mode, backtest tab fold table, tests | 2 days |
| 1a | `feat/cta-book-scan` | `cta_book_scan.py`, migration 0106, writer, timer pair, unit registrations, pytest | 2 days |
| 1b | `feat/cta-book-tab` | route, hook, panel, regime rail, vitest, Playwright evidence, `strategies.md` Strategy 7 + `data/strategies.json` entry (registry sync rule, `docs/implement.md` 5B) | 2 days |
| 1c | `feat/cta-flip-backtest` | `cta_flip` registry entry, 10-year IB backfill, first walk-forward run, admission decision | 1 day |
| 2 | `feat/book-overlay` | `book_overlay.py`, migration 0107, refresh job + timer, `kelly_ticket` and `kelly_guard` wiring, preferences, tests, `docs/risk/kelly-fortunes-formula.md` update | 2 days |
| 3 | `feat/cta-book-contfuture` | IB `ContFuture` helper, six futures series, flock-serialised history fetch | 1 day |
| 4 | optional | rotation-book model inside the CTA Book engine | 1 day |

Every PR: template body checked with `scripts/ci/check_pr_body.py`, CI green on
the exact head, one `radon PR green` Pushover, production verification that the
row landed in Turso before the task is called done.

## 10. Assumptions and open questions

- quants.md's displayed Sharpe is assumed to be the preview's raw Sharpe less a
  2% risk-free rate. Two exact matches support it; the page itself was not
  readable from this session. If the site is opened and the basis differs, the
  excess column changes but the ranking does not.
- `CTA_AUM_BN = 400` and the 10% CTA vol target are inherited from `cri_scan.py`.
  The modelled flow is an order-of-magnitude figure and is labelled APPROX.
- ETF proxies are used in phase 1 because IB serves them as stocks today. The
  six futures arrive in phase 3.
- `nav_snapshots` depth and cadence decide whether a 63-session book vol is
  available on day one; if the series is shorter, the overlay reports
  `scale = 1.0` with a `stale` note until it is.
- The flip-price closed form was checked numerically for k in {1, 5, 21}
  (holding the computed price for k sessions drives SMA63 minus SMA252 to zero
  within 1e-9).
