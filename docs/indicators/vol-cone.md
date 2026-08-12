# VOL CONE — Cheap 10% OTM wing IV scanner

Scanner that finds names like NVDA and SMH where the near monthly implied
vol, especially the 10% OTM wings, sits at the low end of that expiry's
90/10 vol cone.

Operator reference (2026-08-12): NVDA Sep 18 2026 ATM IV printed at the
floor of its Apr-Aug 90/10 cone; 10% OTM call and put IVs were in the
bottom decile of the same window.

- **slug**: `vol-cone` (route `/regime/vol-cone`)
- **service**: `vol-cone` (kebab-case everywhere: `scan_snapshots.service`,
  `service_health`, systemd units)
- **Name**: `VolCone` (`VolConePanel`, `useVolCone`)
- **Tab label**: `VOL CONE`
- **Migration**: `0041_vol_cone.sql` (version 41)

## Signal definition

Per ticker, pick one **fixed monthly expiry** (third Friday) with DTE in
`[21, 90]` closest to 45 DTE. As of 2026-08-12 that is 2026-09-18 (37 DTE).
A single rolling weekly is wrong: the cone is an expiry-local distribution.

For each session `t` with spot `S_t` and that expiry's UW greeks chain:

```
atm_iv_t      = mean( IV_call(K/S_t = 1.00), IV_put(K/S_t = 1.00) )
call_10_iv_t  = IV_call(K/S_t = 1.10)
put_10_iv_t   = IV_put(K/S_t  = 0.90)
```

Linear interpolation in **moneyness** (strike / spot), not delta. Exact
moneyness hit short-circuits. Missing bracket → that leg is null; a session
row is kept only when all three IVs are present and `> 0`.

Cone stats over the stored history of that `(ticker, expiry)` pair:

```
p10, p90          = linear order-statistic percentiles of atm_iv
atm_percentile    = share of atm_iv values strictly below the latest
call_10_percentile, put_10_percentile  = same, on each wing series
wing_score        = mean(call_10_percentile, put_10_percentile)
```

Regime (inclusive cheap bounds; rich is the other tail):

| Regime | Rule | Tone |
|---|---|---|
| `CHEAP_WINGS` | `atm_percentile <= 0.15` AND both wing percentiles `<= 0.20` | `var(--positive)` |
| `CHEAP_ATM` | `atm_percentile <= 0.15` and not `CHEAP_WINGS` | `var(--warning)` |
| `RICH` | `atm_percentile >= 0.85` | `var(--negative)` |
| `NEUTRAL` | otherwise, including exact 0.15/0.20/0.85 misses of the cheap/rich sets | `var(--text-muted)` |

A **hit** is `CHEAP_WINGS` or `CHEAP_ATM`. Hits sort by `wing_score`
ascending, then `atm_percentile` ascending.

IV is stored and transmitted as a **decimal** (0.385 = 38.5 vol points).
UI multiplies by 100.

## Source facts (research 2026-08-12, live-probed with repo token)

- **Primary, current wings**: Unusual Whales
  `GET /api/stock/{ticker}/greeks?expiry=YYYY-MM-DD`
  Bearer `$UW_TOKEN` (web/.env; strip quotes). Per-strike chain with
  `call_volatility` / `put_volatility` as decimal strings, plus deltas and
  option symbols. NVDA Sep 18 2026: 77 strikes, ~55 KB.
- **Primary, history for the cone**: same endpoint with `date=YYYY-MM-DD`.
  Confirmed for NVDA on 2026-04-17, 2026-06-15, 2026-08-11, 2026-08-12.
  Equity monthlies work (unlike the SPX weeklies-empty finding on SKEW).
- **Spot**: `GET /api/stock/{ticker}/ohlc?candle_size=1d` (755 daily bars;
  use `close`) for history; `GET /api/stock/{ticker}/stock-state` `close`
  for the live print. `iv-rank?timespan=1Y` also carries `close` (251 rows)
  and is a valid fallback.
- **Rejected**:
  - IB: no historical IV-by-moneyness (same reject as SKEW).
  - `option-contract/{id}/historic`: live-probed NVDA Sep 200/225/245
    call and put; all returned `data: []`.
  - `atm-chains`: current ATM contract only, not a history.
  - `iv-rank` / `volatility/stats`: ATM-ish 1Y rank, not expiry-local and
    not 10% OTM wings. Useful as a cross-check, not the cone.
- Cadence: UW greeks update through RTH; session-final after the close.
  This scanner is **EOD**: one run after the 16:45 ET grace. Incremental
  days cost 1 greeks call per ticker.
- Licensing: UW is the repo's licensed provider (priority #2; IB fails on
  fit). Derived-indicator display is normal in-app use like SKEW/GEX.
- Rate limit: UWClient retries 429. 25 names × 1 incremental day ≈ 25
  requests. First-run 80-session backfill ≈ 2,000 requests at 0.3 s
  throttle (~10 min). Cap universe at 40.
- Fixtures (captured 2026-08-12):
  - `scripts/tests/fixtures/vol_cone_nvda_greeks_current.json` (77 strikes,
    as-of 2026-08-12, expiry 2026-09-18)
  - `scripts/tests/fixtures/vol_cone_nvda_greeks_hist.json` (77 strikes,
    as-of 2026-06-15)
  - `scripts/tests/fixtures/vol_cone_smh_greeks_current.json` (trimmed 61
    strikes around spot, as-of 2026-08-12, expiry 2026-09-18)
  - `scripts/tests/fixtures/vol_cone_nvda_weekly_series.json` (18 Friday
    extracts Apr-Aug 2026 plus 2026-08-11/12)
  - `scripts/tests/fixtures/vol_cone_nvda_closes.json` (251 iv-rank closes)

Fixture-derived (spot 223.95 on 2026-08-12, 212.45 on 2026-06-15):

| | ATM | 10% call | 10% put |
|---|---|---|---|
| NVDA 2026-08-12 | 0.3851329156797111 | 0.3862120615005326 | 0.39731998999142565 |
| NVDA 2026-06-15 | 0.4169907697613465 | 0.4344290213578079 | 0.4073064139284684 |
| SMH 2026-08-12 (spot 588.12) | 0.38707413534789115 | 0.3812494942003351 | 0.40943558183811746 |

Weekly NVDA cone (n=18): ATM p10=0.38790363361714875, p90=0.44298068625543574.
Latest ATM rank 0.0, 10c rank 1/18, 10p rank 2/18 → `CHEAP_WINGS`.

## Ingestion — `scripts/fetch_vol_cone.py`

Modeled on `fetch_skew.py` / `fetch_margin_debt.py`. Reuse
`scripts/clients/uw_client.py`. Pure functions:

- `parse_greek_rows(payload) -> list[dict]` — `payload["data"]`, floats coerced.
- `interpolate_iv_at_moneyness(rows, spot, target, side) -> (iv, strike) | (None, None)`
  — `side` in `{"call","put"}`; moneyness = strike/spot; linear in moneyness.
- `session_ivs(rows, spot) -> {atm_iv, call_10_iv, put_10_iv, call_10_strike, put_10_strike} | None`
- `third_friday(year, month)` + `select_target_expiry(as_of) -> date`
- `percentile(values, p) -> float` — linear order statistic on sorted values.
- `rank_strictly_below(value, values) -> float` — count `< value` / n.
- `classify_regime(atm_p, call_p, put_p) -> "CHEAP_WINGS"|"CHEAP_ATM"|"RICH"|"NEUTRAL"`
- `compute_name(ticker, expiry, series) -> name dict` — attaches p10/p90,
  percentiles, wing_score, regime; `series` ascending by date.
- `build_output(names, scan_time, source_as_of) -> payload`
- `merge_universe(watchlist, seed=DEFAULT_UNIVERSE, cap=40) -> list[str]`
- `run(client=None, *, now=None, tickers=None)`

`DEFAULT_UNIVERSE` (25): NVDA, SMH, AAPL, MSFT, AMZN, META, TSLA, GOOGL,
AMD, AVGO, QQQ, SPY, IWM, NFLX, MU, ARM, TSM, ASML, INTC, QCOM, AMAT,
LRCX, KLAC, TLT, GLD. Union Turso watchlist, uppercase, stable sort, cap 40.

Client adapter: `fetch_greeks(ticker, expiry, as_of=None)`,
`fetch_closes(ticker) -> dict[str,float]`, `fetch_spot(ticker) -> float`.
Default wraps `UWClient`. Token: `UW_TOKEN` strip quotes.

Incremental: only fetch sessions missing from `vol_cone_history` for the
chosen `(ticker, expiry)`, looking back at most 80 trading days. Empty
greeks for a date → skip that date (self-heals). Throttle 0.3 s.

No-missing-sessions path: reuse cached payload, fresh `scan_time`,
`rows_changed=False` (heartbeat only). Analog of the 304 path.

Empty-payload guard: if zero names have a non-empty series, raise; never
cache empty.

Persistence order per cycle:

1. `writer.ensure_no_replica_for_writers()`
2. `writer.upsert_vol_cone_rows(rows, recorded_at=scan_time)` only when changed
3. `writer.upsert_scan_snapshot("vol-cone", scan_time, payload)` every cycle
4. `writer.record_service_health("vol-cone", "ok", finished_at=scan_time)` every cycle
5. Atomic JSON fallback `data/vol_cone.json`

CLI: `--json` → payload to stdout; summary to stderr.

## Payload contract

```jsonc
{
  "scan_time": "2026-08-12T20:45:00Z",
  "source_as_of": "2026-08-12",
  "count": 25,
  "hit_count": 2,
  "current": { /* best hit, else lowest wing_score name */ },
  "names": [
    {
      "ticker": "NVDA",
      "spot": 223.95,
      "expiry": "2026-09-18",
      "dte": 37,
      "atm_iv": 0.3851329156797111,
      "call_10_iv": 0.3862120615005326,
      "put_10_iv": 0.39731998999142565,
      "call_10_strike": 246.345,
      "put_10_strike": 201.555,
      "p10": 0.38790363361714875,
      "p90": 0.44298068625543574,
      "atm_percentile": 0.0,
      "call_10_percentile": 0.05555555555555555,
      "put_10_percentile": 0.1111111111111111,
      "wing_score": 0.08333333333333333,
      "regime": "CHEAP_WINGS",
      "series": [
        { "date": "2026-04-10", "spot": 188.63, "atm_iv": 0.403, "call_10_iv": 0.417, "put_10_iv": 0.397 }
      ]
    }
  ],
  "hits": [ /* names where regime is CHEAP_WINGS or CHEAP_ATM, sorted */ ]
}
```

Missing contract (HTTP 200):

```json
{
  "missing": true,
  "scan_time": null,
  "source_as_of": null,
  "count": 0,
  "hit_count": 0,
  "current": null,
  "names": [],
  "hits": []
}
```

## Storage

`scripts/db/migrations/0041_vol_cone.sql`:

```sql
CREATE TABLE IF NOT EXISTS vol_cone_history (
  ticker         TEXT NOT NULL,
  date           TEXT NOT NULL,
  expiry         TEXT NOT NULL,
  dte            INTEGER NOT NULL,
  spot           REAL NOT NULL,
  atm_iv         REAL NOT NULL,
  call_10_iv     REAL NOT NULL,
  put_10_iv      REAL NOT NULL,
  call_10_strike REAL,
  put_10_strike  REAL,
  recorded_at    TEXT NOT NULL,
  PRIMARY KEY (ticker, date, expiry)
);
CREATE INDEX IF NOT EXISTS idx_vol_cone_history_ticker_date
  ON vol_cone_history (ticker, date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (41, datetime('now'));
```

Writer: chunked `INSERT ... ON CONFLICT(ticker, date, expiry) DO UPDATE`.

## API

`web/app/api/vol-cone/route.ts` — GET only, `dynamic = "force-dynamic"`,
`runtime = "nodejs"`. `dbFirstRead` on `scan_snapshots` where
`service = 'vol-cone'`, disk `data/vol_cone.json`.
`MAX_AGE_MS = 48h` (daily timer; older means the writer is down).
Cache headers 300 / 3600, tag `vol-cone`.

Hook `web/lib/useVolCone.ts`: `useSyncHook` GET `/api/vol-cone`, hourly,
`hasPost: false`, timestamp `scan_time`.

Helpers `web/lib/volCone.ts`: types, `formatIvPct`, `formatPercentile`,
`volConeRegimeColor`, `isHit`, `buildVolConeChartRows`.

## UI

`VolConePanel`:

- Loader: `SpectralLoader` label `Loading UW vol cone scan`
- Empty: `SectionEmptyState` headline `No vol cone data yet`, secondary
  names the vol-cone refresh timer
- Strip: HITS, best ticker, ATM IV, wing score, regime, SOURCE DATE
  (`source_as_of`)
- Table of `names` (filter chips ALL / HITS): ticker, expiry, DTE, ATM,
  10C, 10P, ATM %, WING, regime. Click selects the cone.
- Chart: dedicated `VolConeChart` (CriHistoryChart is two-series only).
  ATM + 10% OTM call + 10% OTM put lines and a p10-p90 band. Title
  `{TICKER} {expiry} 90/10 VOL CONE`.
- `HistoryRangeChips` + `BrushMinimap` `testIdPrefix="vol-cone-brush"`
- Brand tokens only. No em dashes. No hardcoded "Refreshes daily".
- Tooltip: cheap-wing rule in one sentence.

`RegimePanel` four places: union, `REGIME_TAB_VALUES`, `tabFromPathname`
regex (`vol-cone` before any shorter token is not an issue; add it to
the alternation), desktop button `VOL CONE`, mobile chip (map
`vol-cone` → `VOL CONE`), dispatch `activeTab === "vol-cone"`.

`web/app/regime/vol-cone/page.tsx` — WorkspaceShell section regime.

## Timer

`cloud/services/radon-vol-cone.service` + `.timer`.

- Oneshot, User=radon, WorkingDirectory=/home/radon/radon,
  EnvironmentFile=/home/radon/radon-cloud/.env,
  `RADON_DB_NO_REPLICA=1`,
  ExecStart venv python `scripts/fetch_vol_cone.py`,
  TimeoutStartSec=900 (backfill headroom).
- Timer: `OnCalendar=Mon..Fri *-*-* 20:45:00 UTC` (16:45 ET after the
  close grace). `Persistent=true`. `RandomizedDelaySec=60`.
- Windows: 26h open / 3d closed, `category: scheduled`, `requires_ib: false`.
  Daily watchdog bucket.

## File checklist

Create: `scripts/fetch_vol_cone.py`, `scripts/db/migrations/0041_vol_cone.sql`,
`scripts/tests/test_vol_cone.py`, fixtures listed above,
`web/app/api/vol-cone/route.ts`, `web/lib/volCone.ts`, `web/lib/useVolCone.ts`,
`web/components/VolConePanel.tsx`, `web/components/VolConeChart.tsx`,
`web/app/regime/vol-cone/page.tsx`, `web/tests/vol-cone-api.test.ts`,
`web/tests/vol-cone-panel.test.tsx`, `web/e2e/vol-cone-tab.spec.ts`,
`cloud/services/radon-vol-cone.{service,timer}`.

Modify: `scripts/db/writer.py` (`upsert_vol_cone_rows`),
`web/components/RegimePanel.tsx` (four places + mobile chip + dispatch),
`web/tests/regime-tab-routes.test.tsx`,
`web/lib/serviceHealthWindows.ts` + `web/tests/service-health-windows.test.ts`,
`scripts/watchdog/services.py` (window + daily bucket),
`cloud/scripts/setup-vps.sh` `SERVICE_FILES`,
`cloud/tests/test_systemd_services.py`.
