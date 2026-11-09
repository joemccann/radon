# Quant strategies: the systematic sleeve

Owner spec for `scripts/quant/`, `scripts/quant_run.py`, `scripts/quant_execute.py`,
the two `radon-quant-*` timers and the `/regime/quant` tab.

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
| CLI | `scripts/quant_run.py` | `--list`, `--mode backtest`, `--mode targets`, `--persist`, `--scheduled`. |
| Storage | `scripts/db/migrations/0109_quant_strategies.sql`, `0110_quant_execution.sql`, `db/writer.py` | `quant_runs`, `quant_targets`, `quant_orders`; `upsert_quant_run`, `upsert_quant_targets`. |
| Tests | `scripts/tests/test_quant_engine.py`, `test_quant_strategies.py`, `test_quant_run.py`, `test_quant_execute.py` | Pure synthetic series; the price loader is injected. |

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
python3 scripts/quant_run.py --scheduled
```

Exit codes: 0 ok, 1 not enough history for the warmup, 2 bad arguments or
unknown strategy. stdout carries only the JSON (`--json`); the one-line
summary and all progress go to stderr.

`--persist` writes `data/quant/<strategy>_<mode>.json` (atomic, fallback
only), then the Turso rows. Manual persist does not write a heartbeat; the
scheduled run writes the one `quant-targets` heartbeat. A failed Turso write
re-raises; the disk copy is never treated as success.

## 7. Storage

- `quant_runs (strategy, run_at, mode, since, until, n_sessions,
  n_rebalances, ann_return, ann_vol, sharpe, max_drawdown, payload)`, PK
  `(strategy, run_at)`, latest by `run_at DESC`. Migration `0109`.
- `quant_targets (strategy, as_of, symbol, weight, notional, close,
  rebalance_today, recorded_at)`, PK `(strategy, as_of, symbol)`.
  `close` and `rebalance_today` come from migration `0110`.
- `quant_orders` logs each bridge placement, including dry-run `planned`
  rows. It never substitutes for the broker ledger. Migration `0110`.
- Verify the row lands in Turso in production before calling a run done;
  the disk file is not evidence.

## 8. Execution bridge (`scripts/quant_execute.py`)

The bridge turns the latest `quant_targets` session into IB stock orders. It
is the only piece of the sleeve that can touch the broker, and it is off
until the operator arms it.

```
python3 scripts/quant_execute.py --strategy inverse_vol            # plan only, nothing placed
python3 scripts/quant_execute.py --strategy inverse_vol --json --persist
python3 scripts/quant_execute.py --scheduled                      # radon-quant-execute.timer
```

| Env | Default | Meaning |
|---|---|---|
| `RADON_QUANT_EXECUTE` | unset | `1` places orders. Anything else logs the plan with status `planned`. |
| `RADON_QUANT_STRATEGIES` | empty | Comma-separated registry keys the timers run. Empty is a clean no-op. |
| `RADON_QUANT_SLEEVE_PCT` | `0` | Fraction of net liquidation (latest portfolio snapshot) the sleeve may hold. Targets persist `notional = weight x sleeve NAV`; `0` leaves notional null and the bridge has nothing to size. |
| `RADON_QUANT_ALLOW_SHORT` | unset | `1` lets a `long_short` strategy hold negative shares. Otherwise negative targets clamp to zero and a sell never exceeds what the sleeve holds. |
| `RADON_QUANT_LIMIT_OFFSET_BPS` | `20` | Limit offset from the target session's close: buys at close + offset, sells at close - offset, DAY. |

Admission, per strategy, before any order:

1. The latest targets exist and carry `rebalance_today = 1` (the 16:25 ET run
   sets it on BME/BMS/BQE/W sessions, or on a SIGNAL change). `--force`
   overrides for a manual run.
2. `as_of` is at most 4 calendar days old. Stale targets never trade.
3. No `quant_orders` rows exist for that `as_of`. A second run on the same
   session is a no-op, so a timer retry cannot double-trade. An armed run
   writes those rows (status `planned`) before the first `place_order`, so
   Turso down or a SIGKILL after ack cannot leave live orders unrecorded
   (R-748).

Sizing: `target_shares = sign(notional) * floor(|notional| / close)`,
`delta = target - held`; a `|delta| * close` under $50 is dust and skipped;
a symbol the sleeve holds but the targets no longer name is sold to zero.
Holdings are the signed sum of journal fills whose `order_ref` starts with
`quant:<strategy>` (`read_quant_sleeve_fills`), so the sleeve only ever
counts positions it opened. The discretionary book is invisible to it and
untouchable by it.

Every order goes through `ib_place_order.place_order` as
`{"type": "stock", "symbol", "action", "quantity", "limitPrice", "tif": "DAY",
"orderRef": "quant:<strategy>"}`. The order funnel's fat-finger caps, the
trading halt and the armed bankroll gate apply unchanged inside that call.
Kelly does not. Results land in `quant_orders` (`ok`, `error`,
`indeterminate`, or `planned` on a dry run), the disk fallback
`data/quant/<strategy>_execution.json`, and in scheduled mode one
`quant-execute` heartbeat (`error` when any order was rejected or left
indeterminate, so the watchdog pages).

Timers (`cloud/services/`): `radon-quant-targets.timer` fires
`quant_run.py --scheduled` Mon..Fri 16:25 America/New_York;
`radon-quant-execute.timer` fires `quant_execute.py --scheduled` Mon..Fri
09:35 America/New_York. Both are registered in `setup-vps.sh`,
`installed-units.sha256`, `test_systemd_services.py`,
`web/lib/serviceHealthWindows.ts` and `scripts/watchdog/services.py` (74h
windows, `quant-execute` is IB-dependent) and `web/lib/refreshSchedule.ts`.

## 9. API and UI

- `GET /api/quant/[strategy]` (`web/app/api/quant/[strategy]/route.ts`):
  Turso through `dbFirstRead`, label `quant:<strategy>`, freshness budget
  from the `quant-targets` window. Returns the latest backtest run (dates,
  equity, metrics, final weights), the latest targets session (rows with
  weight, notional, close, `rebalance_today`) and that session's order log.
  No rows is HTTP 200 with `missing: true`; an unknown key is 400.
- Regime tab `/regime/quant` in the Models rail (`QuantPanel.tsx`): strategy
  chips, equity curve with drawdown, metrics strip, freshness rail driven by
  `QUANT_TARGETS_REFRESH`, targets table with estimated shares, and the order
  log. Copy states that the sleeve runs outside the Four Gates.

## 10. Tests

```
python3 -m pytest scripts/tests/test_quant_engine.py scripts/tests/test_quant_strategies.py scripts/tests/test_quant_run.py scripts/tests/test_quant_execute.py -q
```

Engine: rebalance calendars incl. a month ending on a weekend, no look-ahead
(two constructions), drift between rebalances, cost per unit turnover, missing
prices, gross cap, SIGNAL trigger, JSON-serialisable metrics, vol-target
scale and prior-window isolation. Strategies: registry contract and each rule
on synthetic closes. CLI: alignment, `--list`, exit codes, backtest and
targets payloads, `--persist` with stubbed writers, migration 0109 into
in-memory sqlite, idempotent upserts.
