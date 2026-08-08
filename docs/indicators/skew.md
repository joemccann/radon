# SKEW — Change in SPX 1-Month 25-Delta Put/Call IV Ratio

Daily change in the ratio of SPX ~1-month 25-delta put implied vol to 25-delta
call implied vol. Reference: operator-supplied chart ("Change in SPX 1-Month
25d Put/Call Ratio", Sep 2024 - Sep 2026: high 0.13, low -0.16, avg ~0, last
-0.12, stddev 0.04 — the -0.12 print is a ~3-sigma single-day skew collapse).

- **slug**: `skew` (route `/regime/skew`)
- **service**: `skew` (kebab-case everywhere)
- **Name**: `Skew` (`SkewPanel`, `useSkew`)
- **Tab label**: `SKEW`
- **Migration**: `0034_skew.sql` (version 34)

## Signal definition

Per session `t`, at **constant 30-day maturity** interpolated between the two
bracketing monthly expiries (near = longest monthly at or under 30 DTE, far =
shortest beyond 30; a single rolling monthly is WRONG here — the ratio jumps
structurally on roll day because near-dated skew runs steeper, which doubles
the change-series stddev with non-market artifacts):

```
per expiry E:  put_iv_25d(E), call_iv_25d(E)  = IV linearly interpolated in
                                                delta to -0.25 / +0.25
per leg:       iv_30d = constant_maturity_leg(iv_near, dte_near, iv_far, dte_far)
               (linear in DTE to 30, clamped to the bracket edges)
ratio_t        = put_iv_30d / call_iv_30d
change_t       = ratio_t - ratio_{t-1}        (None on the first stored session)
```

- Delta interpolation: per wing, collect (delta, iv) points with both fields
  present and iv > 0, coerce strings to float, sort by delta, interpolate the
  bracketing pair; exact-hit short-circuits.
- `bracketing_monthly_expiries(as_of)`: third Fridays of the as-of month and
  the next two; near = max dte <= 30 (else shortest listed), far = min dte >
  30 (else near). 2026-08-05 -> (2026-08-21 @ 16 DTE, 2026-09-18 @ 44 DTE).
- Holiday-shifted expiry fallback per bracket: listed date, then the prior
  calendar day. A single usable bracket prices the row alone (clamped);
  neither usable -> skip the session (self-heals on a later run).
- Stats over all non-null changes: `high`, `low`, `avg`, `stddev` (population,
  `statistics.pstdev`). The UI derives z = change / stddev.
- Tone rule (UI): strictly beyond +/-2 x stddev renders `var(--warning)`;
  otherwise / null `var(--text-muted)`; boundary stays muted (strict).

## Source facts (research 2026-08-05, live-probed with repo token)

- **Primary**: Unusual Whales `GET /api/stock/SPX/greeks?expiry=<YYYY-MM-DD>&date=<YYYY-MM-DD>`
  — Bearer `$UW_TOKEN` (web/.env; the stored value is double-quoted, strip
  quotes). Returns the full per-strike chain: `call_delta`, `call_volatility`,
  `put_delta`, `put_volatility`, strike, expiry, date (values are strings).
  ~380 KB per (date, expiry). **History floor for this token: 2023-09-06**
  (~730 trading days) via the `date` param. Only MONTHLY expiries respond
  (weeklies return empty).
- **Validation series** (optional cross-check, not the indicator):
  `/api/stock/SPX/historical-risk-reversal-skew?delta=25&expiry=<E>&timeframe=2Y`
  returns daily putIV-callIV **difference** per expiry.
- **Rejected**: Cboe SKEW index (tail-risk measure from the whole strip — NOT
  the 25d ratio; never substitute). IB (no historical IV-by-delta; forward-only).
- Cadence: provisional current-session data every minute during RTH; session
  data final after a 16:45 ET grace. The timer also runs at **21:45 UTC**
  daily for finalization and weekend/holiday health heartbeats.
- Licensing: UW is the repo's licensed provider (priority #2; IB fails on fit);
  derived-indicator display is normal in-app use like GEX/IV-rank.
- Fixture: `scripts/tests/fixtures/skew_uw_sample.json` (379 KB, raw greeks
  response `{"data": [...571 rows...]}`, SPX 2026-09-18 as of 2026-08-05).

## Ingestion — `scripts/fetch_skew.py`

Modeled on `fetch_straddle.py` / `fetch_margin_debt.py`. Reuse the existing UW
client machinery in `scripts/clients/` for auth/retry/rate-limit if a suitable
one exists (grep for UW_TOKEN / UWRateLimitError); else minimal urllib with
Bearer + honest UA. Pure functions:

- `parse_greek_rows(payload) -> list[dict]` — `payload["data"]`, floats coerced.
- `interpolate_iv_at_delta(rows, side, target) -> float | None` — per spec;
  `side` in `{"call", "put"}` selects `{side}_delta` / `{side}_volatility`.
- `bracketing_monthly_expiries(as_of) -> (near, far)` + `third_friday(year, month)`
  + `constant_maturity_leg(iv_near, dte_near, iv_far, dte_far, target=30)`.
- `compute_change_series(rows) -> list[dict]` — ascending by date; attaches
  `change` (None first row).
- `compute_stats(series) -> {high, low, avg, stddev} | None` over non-null changes.
- `run(client=None, *, now=None)` — **gap-filling incremental plus live overlay**: determine the
  last FINALIZABLE ET session (16:45 boundary, `ZoneInfo`), list missing trading
  days after the last stored date (bounded to the most recent 10; use
  `scripts/utils/market_calendar`), fetch + compute each, upsert. No missing
  sessions → cached payload with fresh `scan_time`, `rows_changed=False`.
  During RTH, fetch today's same two chains and append/replace a snapshot-only
  row with `is_intraday:true` and `as_of`; its change is versus the prior
  finalized ratio and it is excluded from historical stats and
  `skew_history`. Cached provisional rows are filtered before rehydrating the
  durable base. From 16:00-16:45 ET the last live point is retained; at/after
  16:45 the normal gap fill finalizes today. Empty chain for a
  session → skip it with a stderr note (self-heals on a later run), never
  write an empty/partial row.
- `--backfill [START]` — one-shot: iterate trading days from START (default
  2023-09-06) to today, throttled ~0.3s/request, resumable (skips dates
  already in Turso).
- Persistence per cycle (order): `ensure_no_replica_for_writers()` →
  `upsert_skew_rows(series_delta, recorded_at=scan_time)` when changed →
  `upsert_scan_snapshot("skew", scan_time, payload)` →
  `record_service_health("skew", "ok", finished_at=scan_time)` → atomic JSON
  fallback `data/skew.json`. CLI `--json` → payload to stdout; progress to stderr.

## Payload contract

```jsonc
{
  "scan_time": "2026-08-05T21:45:00Z",
  "source": "unusual_whales", "market_status": "open",
  "count": 730,
  "current": {
    "date": "2026-08-05", "ratio": 1.242056, "change": -0.12,
    "put_iv": 0.147926, "call_iv": 0.119098,
    "expiry": "2026-08-21", "dte": 16,
    "expiry_far": "2026-09-18", "dte_far": 44,
    "is_intraday": true, "as_of": "2026-08-05T15:42:00Z"
  },
  "stats": { "high": 0.13, "low": -0.16, "avg": 0.0004, "stddev": 0.04 },
  "series": [ { "date": "2023-09-06", "ratio": 1.31, "change": null }, ... ]
}
```

Missing contract (HTTP 200 exactly):
`{ missing: true, scan_time: null, count: 0, series: [], current: null, stats: null }`

## Storage — `0034_skew.sql`

```sql
CREATE TABLE IF NOT EXISTS skew_history (
  date        TEXT PRIMARY KEY,
  expiry      TEXT NOT NULL,
  dte         INTEGER NOT NULL,
  put_iv      REAL NOT NULL,
  call_iv     REAL NOT NULL,
  ratio       REAL NOT NULL,
  change      REAL,              -- NULL on the first stored session
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skew_history_date ON skew_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (34, datetime('now'));
```

Writer: `SKEW_UPSERT_SQL` (single-row, sqlite3-executable, args
`(date, expiry, dte, put_iv, call_iv, ratio, change, recorded_at)`) +
`upsert_skew_rows(series, recorded_at)` chunked multi-row per Hrana rules.

## API — `web/app/api/skew/route.ts`

Copy the straddle route: GET only, `dynamic="force-dynamic"`,
`runtime="nodejs"`, `dbFirstRead` (Turso `scan_snapshots service='skew'`
latest, disk `data/skew.json`), `MAX_AGE_MS = 48h`, exact missing contract
above, and `setNoStoreResponseHeaders` so a minute snapshot cannot be hidden
behind an intermediary cache. Hook `web/lib/useSkew.ts` uses GET-only
`useSyncHook`: 60-second polling during RTH and paused polling while closed.
`serviceHealthWindows.ts`: `skew`, scheduled, 5m open / 26h off-hours,
`requires_ib:false`. Python
watchdog window matches (ingestion worktree owns `scripts/watchdog/services.py`).

## UI — `web/components/SkewPanel.tsx` (+ `web/lib/skew.ts`)

Helpers (`web/lib/skew.ts`):

- `formatSkewChange(v)` — signed two decimals (`-0.12`, `+0.05`), `---` null/non-finite.
- `formatSkewRatio(v)` — unsigned two decimals (`1.29`), `---` null.
- `formatIvPct(v)` — IV fraction to percent, one decimal (`16.0%`), `---` null.
- `skewChangeColor(change, stddev)` — strictly `|change| > 2 * stddev` →
  `var(--warning)`; else (incl. exactly 2 sigma, nulls, stddev<=0) `var(--text-muted)`.
- `zScore(change, stddev)` — `change / stddev`, null-safe (null when either
  missing or stddev <= 0).
- `formatZ(v)` — signed one decimal (`-3.0`), `---` null.
- `buildSkewChartRows(series, view)` — `view` in `{"change", "level"}` →
  `[{ date, value }]`; change view preserves the null first row.
- Types `SkewEntry`, `SkewCurrent`, `SkewStats`, `SkewData` (+ missing variant).

Panel:

- Gate order: `SpectralLoader` `label="Loading UW skew series"` →
  `SectionEmptyState` `headline="No skew data yet"` (secondary may reference
  "the skew refresh timer") → content.
- Strip (RegimeStrip desktop / MetricCell grid mobile): CHANGE
  (`data-testid="skew-change-value"`, colored by `skewChangeColor`), LEVEL
  (ratio), Z-SCORE, 25D PUT IV, 25D CALL IV, HIGH, LOW, STDDEV, LATEST DATE,
  TENOR (`expiry` + `dte`d).
- Chart: `CriHistoryChart`, title `SPX 1M 25D PUT/CALL SKEW`, single right
  series; view chips `CHANGE` (default, matches the reference chart) and
  `LEVEL` toggle the series; `HistoryRangeChips` + `BrushMinimap`
  (`testIdPrefix="skew-brush"`), default preset per `defaultPresetForLength`;
  daily `xTickFormat`.
- `InfoTooltip`: "Ratio of SPX 25-delta put IV to 25-delta call IV on the
  monthly expiry nearest 30 days, interpolated in delta from the Unusual
  Whales chain. The chart plots the day-over-day change: sharp drops mean put
  skew collapsing or call demand spiking; sharp rises mean downside fear
  getting bid. Beyond 2 sigma is a tail repricing event." (no em dashes)
- Freshness is explicit: a `LIVE` badge requires an open-market intraday row
  with an `as_of` no more than three minutes old; latest-date metadata says
  `INTRADAY` and includes the ET observation time. Final rows render `DAILY`.
- Registration: RegimePanel all six places (union, values, regex, desktop row,
  mobile chips, dispatch), label `SKEW`; `web/app/regime/skew/page.tsx`;
  `regime-tab-routes` table + render case; e2e `web/e2e/skew-tab.spec.ts`.

## Scheduling

`cloud/services/radon-skew.{service,timer}`: oneshot venv-python direct,
`EnvironmentFile=/home/radon/radon-cloud/.env` (UW_TOKEN must resolve there —
confirm it exists on the VPS env; it does for GEX/leap timers),
`RADON_DB_NO_REPLICA=1`, `TimeoutStartSec=300`; timer runs each minute in the
broad 13:00-21:59 UTC weekday DST window (the fetcher gates on the shared ET
market calendar) plus `OnCalendar=*-*-* 21:45:00 UTC` for finalization and
off-hours health. `RandomizedDelaySec=5`, `AccuracySec=1s`,
`Persistent=false`. Register in `setup-vps.sh`
`SERVICE_FILES` + `cloud/tests/test_systemd_services.py`.

## Test evidence pins (fixture-derived 2026-08-05)

- Far fixture (`skew_uw_sample.json`): 571 rows, expiry 2026-09-18 (44 DTE):
  call25 `0.12340843535214445`, put25 `0.15956701505157658`, ratio `1.2929992556526146`.
- Near fixture (`skew_uw_sample_near.json`): 582 rows, expiry 2026-08-21 (16 DTE):
  call25 `0.11478760890347106`, put25 `0.13628591095888667`.
- Constant-maturity 30d (w_far = (30-16)/(44-16) = 0.5):
  cm_call `0.11909802212780776`, cm_put `0.14792646300523163`, cm_ratio `1.242056420101479`.
- `bracketing_monthly_expiries(2026-08-05)` = (2026-08-21, 2026-09-18);
  `(2026-08-24)` = (2026-09-18, 2026-10-16).
- `third_friday(2026, 8)` = 2026-08-21; `third_friday(2026, 9)` = 2026-09-18.
- DB row semantics: `expiry`/`dte` = the NEAR anchor of the CM interpolation;
  `put_iv`/`call_iv`/`ratio` are the CM-30d values; payload rows additionally
  carry `expiry_far`/`dte_far`.
