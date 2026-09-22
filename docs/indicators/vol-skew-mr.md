# Vol/Skew MR scanner

Short-term top and bottom framing from spot extension versus IV and skew
path. Mode id `vol-skew-mr`. UI label **Vol/Skew MR**. Route
`/scanner?mode=vol-skew-mr`.

Framing inspired by Options Insight / Imran Lakha (strategy inspiration
only; Radon implements the gates against Unusual Whales and existing
Radon vol/skew feeds).

## Gates

1. Technicals: RSI(14) and/or Bollinger percent B(20, 2). RSI at or
   beyond 70/30, or percent B outside 0 to 1, marks an extended high or
   low.
2. IV path into the move:
   - Extended high + falling or flat IV -> `TOP_MR`
   - Rally + rising IV with spot -> `BREAKOUT`
3. Skew: when vol and skew both diverge from spot, suggest a put spread
   (tops) or call spread (bottoms).
4. Symmetric `BOTTOM_MR` / `BREAKDOWN` on an extended low.

Verdicts: `TOP_MR`, `BOTTOM_MR`, `BREAKOUT`, `BREAKDOWN`, `NO_SIGNAL`.

## Family contract

Mirrors strength / theta: `scripts/vol_skew_mr_scanner.py`, disk cache
`data/vol_skew_mr.json`, Turso `vol_skew_mr_snapshots` (migration 0075),
GET `/api/scanner/vol-skew-mr`, POST `/api/scanner/vol-skew-mr/scan`,
comma ticker search, NDX preset.

## Shared skew snapshot

`fetch_skew_snapshot(client, ticker)` is the one reader of the skew path:
expiry selection, the dated history call, vol-point conversion, and
`series_path`. The scanner gates on it, and `scripts/flow_report.py` embeds
the same snapshot as the `skew` block of every per-ticker flow report
(`/flow-analysis/<TICKER>` renders value, direction, one-session change, and
the six-session sparkline via `web/lib/flowSkew.ts`). A skew failure degrades
that block alone; the dark pool and options sections never depend on it.

## Data sources per ticker

Daily closes come from IB first (`fetch_daily_closes`), UW OHLC only on a
short or failed IB read. Each remaining UW call is isolated so one failure
degrades a single gate instead of dropping the ticker:

| Gate | Source | Notes |
|---|---|---|
| Technicals | IB daily closes (UW fallback) | RSI(14), %B(20, 2) |
| IV path | UW `get_iv_rank` | last six readings, vol points |
| Skew path | UW `get_historical_risk_reversal_skew` | listed future expiry nearest 30 DTE, delta 25, one-month history; last six distinct sessions |

The scanner never reads strike GEX, so it spends no UW quota on it.

The historical risk-reversal endpoint requires both `expiry` and `delta`.
Discover the actual listed expiry through `get_expiry_breakdown`; choose the
future expiry nearest 30 DTE (earlier date breaks ties), then request
`expiry=<YYYY-MM-DD>&delta=25&timeframe=1M`. Every observation in a scan uses
that same maturity and delta. The endpoint's signed `risk_reversal` field is
put IV minus call IV in decimal units; multiply by 100 for vol points before
applying `PATH_FLAT_EPS`.

Ignore malformed/future dates, nonfinite values, and explicit expiry/delta
mismatches. Deduplicate by session and retain the latest six sessions. A missing
listed expiry or fewer than two valid sessions keeps skew `unknown`, leaves
the skew gate closed, and records a `skew_history:` diagnostic in the row.
Never append an undated current-chain snapshot: it cannot establish a path
and may represent a different maturity or duplicate the latest session.

## Acting on a row

Spot is a link into the ticker's chain deck (`?deck=c&src=vol-skew-mr`) for
every row with a verdict, mirroring strength / LEAP / GARCH. `NO_SIGNAL`
rows render plain spot and no structure label; `BREAKOUT` / `BREAKDOWN`
render `continue`.
