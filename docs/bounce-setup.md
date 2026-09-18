# BOUNCE SETUP scanner

Mode `/scanner?mode=bounce` · service `bounce-setup` · component `BounceSetupScanner` · tab label `Bounce Setup`

Name-ranking scanner. Finds the names most stretched to the downside in the universe
whose fixed-strike implied vol has peaked and is fading while put skew eases. It
**nominates candidates only**: none of its inputs is dark pool or OTC flow, so it can
never clear Gate 2 on its own (see Gates below).

Reference case (operator, 2026-09-18): BAC Oct16 55 put. Over 20 sessions spot fell
about 5.5%, ATM fixed-strike vol rose to about +4.5 vol points by 9 Sep then faded to
about +0.9, and SKEW30 eased from about 3.5 to about 3.0.

## Window and constants

| Constant | Value | Meaning |
|---|---|---|
| `WINDOW` | 20 sessions | every leg is measured over the same trailing 20 completed sessions |
| `STAGE2_TOP_N` | 30 | only the 30 most stretched names spend UW calls |
| `STRETCH_PCTL_MAX` | 10.0 | leg 1 passes at or below the 10th percentile of the universe |
| `IV_RUNUP_MIN` | 2.0 vol pts | the fixed-strike vol must have actually run up |
| `IV_OFF_PEAK_MIN` | 0.25 | and given back at least 25% of that run-up |
| `SKEW_EASE_MIN` | 0.3 vol pts | skew at least this far below its window max |
| `SLOPE_SESSIONS` | 3 | turn confirmation: latest vs the mean of the prior 3 sessions, robust to one-day zig-zags |
| `EXPIRY_DTE` | 30-45 | monthly expiry window for the fixed-strike contract |

## Stage 1 — stretch rank (zero UW calls)

Universe: preset `largecaps` (NDX + SPX, about 520 names; BAC is SPX, not NDX). Daily
closes come from Turso `price_history_daily` via `db.readers.read_price_history_closes`
(fed by the existing bpi/ma-ratio constituent sweeps), so stage 1 makes no new fetch.

Per name, over completed sessions only:

- `rsi` = RSI(14), reusing `vol_skew_mr_scanner.rsi`
- `pct_b` = Bollinger %B(20, 2), reusing `vol_skew_mr_scanner.bollinger_pct_b`
- `ret_z` = z-score of the latest 20-session return against the name's own rolling
  20-session returns over the prior 252 sessions (needs >= 272 closes, else excluded)

`stretch_score` = mean of the three **universe percentile ranks** (0 = most oversold).
`stretch_pctl` is that score re-ranked across the universe, 0-100, 1 decimal. Rank 1 =
lowest score = "most stretched to the downside in the universe". Names with fewer than
272 closes are excluded and counted in `coverage`.

## Stage 2 — vol and skew legs (top 30 only)

For each stage-2 name (about 5 UW calls each, about 150 per run):

1. **Expiry**: the monthly (third-Friday) expiry with 30-45 DTE from `as_of`; if none,
   the nearest monthly with DTE >= 21. From `get_expiry_breakdown` (1 call).
2. **Fixed strikes**: from one `get_option_contracts(ticker, expiry=…)` call covering both
   rights, fixed on the close at **window start** (session -20): the ATM put (nearest
   that close), the skew put wing (nearest 93% of it) and the skew call wing (nearest
   107% of it). Tie goes to the
   lower strike. The strike never rolls: that is what "fixed strike" means.
3. **Fixed-strike vol**: `get_option_contract_historic(occ_symbol)` (1 call), daily
   `implied_volatility` in vol points, last `WINDOW` sessions. `fs_iv_change` = each
   session minus the window's first session.
4. **Skew**: `get_option_contract_historic` on both wings (2 calls); `skew30` = put-wing IV
   minus call-wing IV in vol points, on sessions where both quotes are tradeable.
   UW's single-name 25-delta risk-reversal history was rejected after a live check:
   it is quote noise (TDG 2026-10-16 flips sign most sessions).
5. **Liquidity gate**: a session counts only when bid > 0 and the NBBO spread is at
   most 25% of the mid (`MAX_REL_SPREAD`). Fewer than 15 counted sessions
   (`MIN_VALID_SESSIONS`) makes that leg unavailable (`illiquid_options`,
   `illiquid_skew_wings`), so the name can reach at most `WATCH` or `STRETCHED`.

OCC symbol: `TICKER + YYMMDD + P + strike*1000 zero-padded to 8`, e.g.
`BAC261016P00055000`.

## Legs

```text
stretched   stretch_pctl <= 10.0
vol plateau runup    = max(fs_iv) - fs_iv[0]             >= 2.0
            off_peak = (max(fs_iv) - fs_iv[-1]) / runup  >= 0.25
            slope    = fs_iv[-1] - mean(fs_iv[-4:-1])    <= 0
skew easing ease     = max(skew) - skew[-1]              >= 0.3
            slope    = skew[-1] - mean(skew[-4:-1])      <  0     (strict: easing means falling)
```

Verdict per stage-2 row:

| Legs passing | verdict |
|---|---|
| all three | `BOUNCE_SETUP` |
| stretched + exactly one other | `WATCH` |
| stretched only, or data missing for a leg | `STRETCHED` |
| not stretched (`stretch_pctl > 10`) | `None`: no row emitted |

A leg whose data is unavailable is `null`, never a pass. Rows sort by verdict
(`BOUNCE_SETUP`, `WATCH`, `STRETCHED`) then `stretch_pctl` ascending.

## Gates

- **Gate 2 (edge)**: each row carries `flow`, joined from the latest flow-scanner
  snapshot (Turso `scanner_snapshots`, service `scanner`, falling back to
  `data/scanner.json`): `top_signals[].direction` upper-cased becomes `flow.signal`,
  `score` becomes `flow.score`, and a ticker absent from the scan is `null`. OPEN TRADE renders only when `verdict == "BOUNCE_SETUP"` and `flow` shows
  accumulation; otherwise the row reads `NO FLOW EDGE` and links to the ticker page.
- **Gate 1 (convexity)**: OPEN TRADE opens a defined-risk call spread prefill, the
  same builder `volSkewMrOrderHref` uses. The chain ticket's own risk gate still applies.
- **Gate 3 (sizing)**: unchanged, in `evaluate`.

## Payload (`data/bounce_setup.json`, `scan_snapshots` service `bounce-setup`)

```json
{
  "scan_time": "2026-09-18T21:10:00+00:00",
  "as_of": "2026-09-18",
  "window": 20,
  "universe": "largecaps",
  "coverage": {"tickers": 520, "ranked": 512, "excluded_short_history": 8, "stage2": 30},
  "bounce_count": 1,
  "results": [{
    "ticker": "BAC",
    "verdict": "BOUNCE_SETUP",
    "stretch_rank": 1, "stretch_pctl": 0.2,
    "rsi": 24.1, "pct_b": -0.12, "ret_z": -2.4, "ret_20d": -5.5,
    "contract": {"symbol": "BAC261016P00055000", "expiry": "2026-10-16", "strike": 55.0},
    "vol": {"runup": 4.5, "off_peak": 0.8, "slope": -1.9, "pass": true},
    "skew": {"ease": 0.5, "slope": -0.4, "pass": true},
    "series": [{"date": "2026-08-21", "spot_cum_pct": 0.0, "fs_iv_change": 0.0, "skew30": 2.62}],
    "flow": null,
    "errors": []
  }]
}
```

`series` feeds the detail chart. Missing contract: the route returns HTTP 200
`{ missing: true, scan_time: null, results: [], bounce_count: 0 }`.

## Surfaces

- `scripts/bounce_setup_scanner.py`: pure functions above plus `scan_universe`,
  `build_output`, `save_cache`, `main` (`--json`, `--limit`, tickers override the
  preset). `should_block_universe_scan()` before stage 2; `record_scan_degraded` on
  budget block or coverage failure (vol-skew-mr precedent). `RADON_UW_CALLER=bounce-setup`.
- `web/app/api/scanner/bounce/route.ts` (GET, dbFirstRead, `read`) and
  `web/app/api/scanner/bounce/scan/route.ts` (POST via `radonFetch`, `read.spawn`).
- `web/lib/bounceSetup.ts` (types, `bounceOrderHref`, verdict labels),
  `web/lib/useBounceSetup.ts`.
- `web/components/BounceSetupScanner.tsx`: table (Ticker, Verdict, Stretch rank, RSI,
  20d return, FS vol off peak, Skew off max, Contract, Flow, Action) and a row detail
  chart: spot cumulative % line, fixed-strike vol change bars, SKEW30 on an inverted
  right axis.
- `ScannerModeTabs` gains `{ mode: "bounce", label: "Bounce Setup" }`; `WorkspaceSections`
  parses `?mode=bounce` and renders the panel.
- Timer `radon-bounce-setup.{service,timer}`: weekdays 21:10 UTC, after the close and
  the price-history sweeps.

## Copy

- Descriptive only. `BOUNCE SETUP` names a pattern, never a forecast. No "looks like",
  no expected-return language, no em dashes.
- The rank is described as "most stretched to the downside in the universe". Radon has
  no "strategy compass".
- The scanner never explains why a name fell. News context may appear later from the
  TradingView per-ticker news slot and is never scored.

## Tests

- `scripts/tests/test_bounce_setup.py`: pure-function boundaries, verdict table,
  OCC symbol, expiry and strike selection, stage-2 cap, budget block, flow join.
- `web/tests/bounce-setup-route.test.ts`, `web/tests/bounce-setup-scanner.test.tsx`
  (including the Gate 2 wire: no OPEN TRADE without flow).
- Pins: `scanner-mode-tabs.test.tsx`, `assistant-catalog-pin`,
  `route-local-authz-matrix`, `rateTier` segment (under `scanner`, already listed),
  service-health windows, systemd canonical set + installed-units manifest.
