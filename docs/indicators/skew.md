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

Per session `t`, from the SPX option chain of the selected monthly expiry:

```
put_iv_25d  = IV linearly interpolated in delta to put_delta  = -0.25
call_iv_25d = IV linearly interpolated in delta to call_delta = +0.25
ratio_t     = put_iv_25d / call_iv_25d
change_t    = ratio_t - ratio_{t-1}          (None on the first stored session)
```

- Interpolation: per wing, collect (delta, iv) points with both fields present
  and iv > 0, coerce strings to float, sort by delta, take the bracketing pair
  around the target and interpolate linearly; exact-hit short-circuits.
  Fixture-derived pins (2026-08-05, expiry 2026-09-18, 571 strikes):
  `call25 = 0.12340843535214445`, `put25 = 0.15956701505157658`,
  `ratio = 1.2929992556526146`.
- Expiry selection (`nearest_monthly_expiry(as_of)`): candidates are the third
  Fridays of the as-of month and the next two months; pick min `|dte - 30|`,
  **tie breaks to the LATER expiry** (2026-08-05: Aug 21 = 16 DTE vs Sep 18 =
  44 DTE, both |14| from 30 → Sep 18, matching the fixture). If the chain for
  a candidate comes back empty (holiday-shifted expiry), retry the prior
  calendar day, then the next-nearest candidate. Tenor drifts ~16-44 DTE by
  construction — this is the standard "1M constant-maturity-ish" monthly walk;
  document, don't hide.
- Stats over all non-null changes: `high`, `low`, `avg`, `stddev` (population,
  `statistics.pstdev`). The UI derives z = change / stddev.
- Tone rule (UI): a change strictly beyond ±2 x stddev renders
  `var(--warning)` (a tail event in either direction); otherwise / null
  `var(--text-muted)`. Boundary exactly 2 sigma stays muted (strict).

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
- Cadence: session data final after the close; timer at **21:45 UTC** daily.
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
- `nearest_monthly_expiry(as_of: date) -> date` + `third_friday(year, month)`.
- `compute_change_series(rows) -> list[dict]` — ascending by date; attaches
  `change` (None first row).
- `compute_stats(series) -> {high, low, avg, stddev} | None` over non-null changes.
- `run(client=None, *, now=None)` — **gap-filling incremental**: determine the
  last COMPLETED ET session (16:00 boundary, `ZoneInfo`), list missing trading
  days after the last stored date (bounded to the most recent 10; use
  `scripts/utils/market_calendar`), fetch + compute each, upsert. No missing
  sessions → cached payload with fresh `scan_time`, `rows_changed=False`
  (heartbeat-only fast path, mirrors straddle's 304 path). Empty chain for a
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
  "source": "unusual_whales",
  "count": 730,
  "current": {
    "date": "2026-08-05", "ratio": 1.292999, "change": -0.12,
    "put_iv": 0.159567, "call_iv": 0.123408,
    "expiry": "2026-09-18", "dte": 44
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
latest, disk `data/skew.json`), `MAX_AGE_MS = 48h` (daily 21:45 UTC timer,
weekend runs heartbeat via the no-missing-sessions fast path), exact missing
contract above, `setCacheResponseHeaders` 300/3600 `tags: ["skew"]`.
Hook `web/lib/useSkew.ts`: `useSyncHook({ endpoint: "/api/skew", interval:
60 * 60_000, hasPost: false, extractTimestamp: d => d.scan_time })`.
`serviceHealthWindows.ts`: `skew`, scheduled, uniform 26h, `requires_ib:
false` (mirror straddle/margin-debt) + exhaustive pin test update. Python
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
- Freshness copy derived only (header clock = `lastSync`; latest date cell).
- Registration: RegimePanel all six places (union, values, regex, desktop row,
  mobile chips, dispatch), label `SKEW`; `web/app/regime/skew/page.tsx`;
  `regime-tab-routes` table + render case; e2e `web/e2e/skew-tab.spec.ts`.

## Scheduling

`cloud/services/radon-skew.{service,timer}`: oneshot venv-python direct,
`EnvironmentFile=/home/radon/radon-cloud/.env` (UW_TOKEN must resolve there —
confirm it exists on the VPS env; it does for GEX/leap timers),
`RADON_DB_NO_REPLICA=1`, `TimeoutStartSec=300`; timer
`OnCalendar=*-*-* 21:45:00 UTC` (comment: session greeks final after the
16:00 ET close; 21:45 UTC = 17:45 EDT / 16:45 EST clears both regimes),
`RandomizedDelaySec=300`, `Persistent=true`. Register in `setup-vps.sh`
`SERVICE_FILES` + `cloud/tests/test_systemd_services.py`.

## Test evidence pins (fixture-derived 2026-08-05)

- Fixture chain: 571 rows, `date=2026-08-05`, `expiry=2026-09-18`.
- `interpolate_iv_at_delta(rows, "call", 0.25) = 0.12340843535214445`
- `interpolate_iv_at_delta(rows, "put", -0.25) = 0.15956701505157658`
- ratio `= 1.2929992556526146`
- `nearest_monthly_expiry(2026-08-05)` = `2026-09-18` (tie 16 vs 44 DTE → later)
- `third_friday(2026, 8)` = `2026-08-21`; `third_friday(2026, 9)` = `2026-09-18`
