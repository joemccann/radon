# Quant strategies: the systematic sleeve

Owner spec for `scripts/quant/` and `scripts/quant_run.py`. Status: engine
slice shipped (data, backtest, registry, CLI, storage, tests). Execution
bridge, timers and UI are specified below and not built.

## 1. What it is and what it is not

Radon's evaluation pipeline (`docs/evaluation.md`) is discretionary and gated:
flow signal, convex structure, Kelly ticket. The quant sleeve is a second,
independent book: rules-based, weight-driven, rebalanced on a calendar, run on
daily closes. It does not read dark pool, options flow, or the vol surface, and
it does not pass through Gates 1 to 3. That separation is deliberate and is the
operator's call (2026-10-31).

What still applies, because it is safety rather than methodology: the order
funnel caps in `order_limits.py`, the trading halt, the bankroll gate when the
operator has armed it, and the execution rule that every order is placed on IB.
Gate 4 is disabled repo-wide and the sleeve's long/short strategy relies on
that being so.

The first strategies are one-to-one ports of the quants.md / QuantJourney
example suite (Apache-2.0). Their published backtests are ranked in
`docs/research/quants-md-strategy-adoption.md`; none of them is an edge claim,
and the sleeve exists so Radon can run, measure and compare systematic rules
on its own data rather than trust a library's dashboard.

## 2. Components

| Piece | Path | Role |
|---|---|---|
| Indicators | `scripts/quant/indicators.py` | Rolling mean and std (sample, like pandas), returns, momentum, realised vol. numpy only, `[T, N]` matrices. |
| Engine | `scripts/quant/engine.py` | `RebalancePolicy`, `rebalance_mask`, `run_weights_backtest`, `latest_targets`, `apply_vol_target`. |
| Registry | `scripts/quant/strategies.py` | `QuantStrategy` entries with provenance, universe, policy, warmup, params. |
| Data | `scripts/quant/data.py` | `load_price_matrix`: Turso `price_history_daily` first, then IB (deep) > Robinhood > UW > Yahoo backfill, persisted back. |
| CLI | `scripts/quant_run.py` | `--list`, `--mode backtest`, `--mode targets`, `--persist`. |
| Storage | `scripts/db/migrations/0106_quant_strategies.sql`, `db/writer.py` | `quant_runs`, `quant_targets`; `upsert_quant_run`, `upsert_quant_targets`. |
| Tests | `scripts/tests/test_quant_engine.py`, `test_quant_strategies.py`, `test_quant_run.py` | Pure synthetic series; the price loader is injected. |

## 3. Engine semantics

- `targets[t]` is the weight vector wanted at the close of session `t`, built
  from data dated at or before `t`.
- On session `t` the engine marks the book carried from `t - 1` to market,
  lets weights drift with prices, and only then, if `t` is a rebalance
  session, trades to `targets[t]` and pays `cost_bps` on turnover. The new
  weights earn the return from `t` to `t + 1`. A target on the last session
  cannot move equity. `test_target_set_at_t_earns_the_return_from_t_to_t_plus_one`
  pins this.
- Rebalance calendars: `D`, `W` (last session of the ISO week), `BME` (last
  session of the month), `BMS` (first session of the month), `BQE` (last
  session of the quarter), `SIGNAL` (max abs weight change above a threshold).
  The last row's period end is judged against the next calendar weekday, so a
  month-end read on its own session reports `rebalance_today: true`.
- A symbol without a finite close on a rebalance session gets weight 0; a
  missing close on either side of a session contributes a zero return.
- Gross exposure above `max_gross` is scaled down proportionally. Every
  registry entry declares its cap; `--max-gross` overrides it.
- Costs are `cost_bps` per side on traded notional (default 5 bps). This is a
  flat model, not `scripts/costs.py`, which is an options model.
- Metrics come from `backtest/metrics.py` (`sharpe`, `sortino`, `calmar`,
  `max_drawdown`, `hit_rate`, `expectancy`, `annualized_return`) plus
  `ann_vol`, `total_return`, `avg_turnover`, `cost_drag`, `mean_gross`,
  `mean_net`. Sharpe is raw (no risk-free deduction), like the library's
  preview images; subtract the risk-free rate before comparing with quants.md's
  listed figures.
- `apply_vol_target` ports `VolTargetModel`: the scale is `target / realised`
  on the strictly prior `lookback` window, capped at `max_leverage`, held when
  realised vol is under 1%, recomputed on the first session of each month.

## 4. Registry

| Key | Source | Rule | Universe | Policy | Warmup | L/S |
|---|---|---|---|---|---|---|
| `trend_inverse_vol` | W25 | sign(SMA63 minus SMA252) divided by 63-session vol, unit gross | SPY, QQQ, IEF, USO, GLD, CORN (proxies for MES, MNQ, ZN, CL, GC, ZC) | BME | 252 | yes |
| `inverse_vol` | W05 | weights proportional to 1 / vol63, capped 0.35 | SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ | BME | 63 | no |
| `monthly_rotation` | O17 | top 3 by 126-session return, equal weight, cap 0.40 | same 8 | BMS | 126 | no |
| `dual_momentum` | W04 | top 2 by 252-session return, only if positive, cap 0.50 | same 8 | BQE | 252 | no |
| `vol_target_trend` | W17 | SMA50 above SMA200 long/cash, equal weight cap 0.34, 10% vol target, lookback 63, max leverage 1.5 | same 8 | BME | 200 | no |
| `signal_change_defensive` | W06 | SPY above SMA200: equal weight SPY, EFA, EEM, DBC, VNQ; else TLT, IEF, GLD | same 8 | SIGNAL 0.01 | 200 | no |

`default_since` is 2007-01-03 for the multi-asset universe (DBC and VNQ
history) and 2010-06-09 for the futures proxies (CORN listing). The ETF
proxies carry roll distortion in USO and CORN; so do the provider continuous
series the source used.

Adding a strategy: one `QuantStrategy` entry with a `weights_fn(closes,
params) -> weights`, a `source` naming the file it was ported from, and a
test in `test_quant_strategies.py` that pins the rule on synthetic closes.

## 5. Data

`load_price_matrix(symbols, since=...)` reads `price_history_daily` through
`db.readers.read_price_history_closes` (chunked, bounded by `since`). A
symbol with fewer than 252 rows or a latest close older than 10 days goes
through `backfill_closes`: one IB connection for every symbol, duration sized
to `since` (capped 20 years), then Robinhood, UW and Yahoo per symbol, with
the rows written back through `upsert_price_history_rows` so the next run is
a pure read. Only IB serves multi-year history; the fallback rungs serve about
a year, enough to keep targets current, not to backtest. `--no-backfill`
reads Turso only. Yahoo is never scheduled or primary.

`align_closes` builds the union-of-dates matrix, forward-fills within a symbol
after its first observation and leaves NaN before it, so a late-listed ETF
contributes nothing until it exists.

## 6. CLI

```
python3 scripts/quant_run.py --list
python3 scripts/quant_run.py --strategy inverse_vol --mode backtest --json
python3 scripts/quant_run.py --strategy trend_inverse_vol --mode backtest --since 2012-01-01 --cost-bps 8 --max-gross 1.0 --json
python3 scripts/quant_run.py --strategy monthly_rotation --mode targets --sleeve-nav 250000 --json --persist
```

Exit codes: 0 ok, 1 not enough history for the warmup, 2 bad arguments or
unknown strategy. stdout carries only the JSON (`--json`); the one-line
summary and all progress go to stderr.

`--persist` writes `data/quant/<strategy>_<mode>.json` (atomic, fallback
only), then the Turso rows, then the `quant-<strategy>` heartbeat
(kebab-case service name). A failed Turso write records an `error` heartbeat
and re-raises; the disk copy is never treated as success.

## 7. Storage

- `quant_runs (strategy, run_at, mode, since, until, n_sessions,
  n_rebalances, ann_return, ann_vol, sharpe, max_drawdown, payload)`, PK
  `(strategy, run_at)`, latest by `run_at DESC`.
- `quant_targets (strategy, as_of, symbol, weight, notional, recorded_at)`,
  PK `(strategy, as_of, symbol)`.
- Neither table carries orders. Verify the row lands in Turso in production
  before calling a run done; the disk file is not evidence.

## 8. Execution bridge (specified, not built)

`scripts/quant_execute.py`, operator-invoked or on a timer after the targets
run:

1. Read the latest `quant_targets` for the strategy and the sleeve NAV
   (`RADON_QUANT_SLEEVE_PCT` of net liquidation from the latest portfolio
   snapshot; default 0, which disables the bridge).
2. Read current sleeve holdings: positions whose journal `orderRef` starts
   with `quant:<strategy>`. The sleeve never touches positions it did not open.
3. Diff target shares (`notional / last close`, whole shares) against
   holdings; emit one stock order per symbol with `orderRef quant:<strategy>`,
   limit at last close plus or minus a configurable offset, DAY, through
   `ib_place_order.py` (`type: stock`). Short targets are allowed only when
   `long_short` is declared and `RADON_QUANT_ALLOW_SHORT=1`.
4. `RADON_QUANT_EXECUTE` unset or `0` prints the order list and places
   nothing. Paper account first.
5. Timer: targets at 16:25 America/New_York on rebalance sessions only
   (`rebalance_today`), execution at the next open. Both units follow the
   new-indicator pattern (`setup-vps.sh`, `installed-units.sha256`,
   `test_systemd_services.py`, `serviceHealthWindows.ts`,
   `refreshSchedule.ts`, `watchdog/services.py`).
6. The order funnel's fat-finger caps, the trading halt and the all-placer
   bankroll gate apply unchanged. Kelly does not.

## 9. API and UI (specified, not built)

- `GET /api/quant/[strategy]` through `dbFirstRead`: latest `quant_runs`
  summary plus the latest `quant_targets` rows; `missing: true` at 200.
- Regime tab `quant` in the Models rail (`regimeRail.ts`): equity curve with
  SPY overlay (`CriHistoryChart`, log scale), a metrics strip, a targets table
  with `rebalance_today`, and a `FreshnessRail` once a timer exists.
- Assistant catalog pins per the new-indicator skill.

## 10. Tests

```
python3 -m pytest scripts/tests/test_quant_engine.py scripts/tests/test_quant_strategies.py scripts/tests/test_quant_run.py -q
```

Engine: rebalance calendars incl. a month ending on a weekend, no look-ahead
(two constructions), drift between rebalances, cost per unit turnover, missing
prices, gross cap, SIGNAL trigger, JSON-serialisable metrics, vol-target
scale and prior-window isolation. Strategies: registry contract and each rule
on synthetic closes. CLI: alignment, `--list`, exit codes, backtest and
targets payloads, `--persist` with stubbed writers, migration 0106 into
in-memory sqlite, idempotent upserts.
