# CALM STREAK: consecutive SPX sessions without a >1% intraday band

Route `/regime/calm-streak` · service `calm-streak` · component `CalmStreakPanel` · tab label `CALM STREAK` · migration `0076`

## Signal

Per completed SPX session `t` (with a prior session close):

```
band_pct(t) = 100 * (high(t) - low(t)) / close(t-1)
streak(t)   = 0                 if band_pct(t) > 1.0     (strict: exactly 1.0 does NOT break)
            = streak(t-1) + 1   otherwise
```

The first session in the source has no prior close and emits no row. Computation
runs over the full source history (1975 onward) so the 1985 seed is warm; the
published series starts `SERIES_START = 1985-01-01`.

Reference (Goldman Sachs Garrett desk chart, Sept 2026): "27 consecutive sessions",
peak ~64 in 2017, and "between 1996 and 2016 this never happened". Verified against
the live Cboe file on 2026-09-15 with this definition: streak 27 on 2026-09-11 and
28 on 2026-09-14; all-time max 64 ending 2017-03-20; max inside 1996-01-01..2016-12-31
is 26 ending 2014-06-23. Dividing by the session low or open instead gives identical
numbers on this data; prior close is the standard intraday-range convention and the
open is 0 on many pre-1996 rows, so prior close is the definition.

Stats (computed in Python, all over the published series from 1985):

- `stats.max`: `{streak, date}` all-time maximum (latest date wins a tie).
- `stats.window`: `{start: "1996-01-01", end: "2016-12-31", streak, date}` maximum
  inside the window the GS note cites.
- `stats.percentile`: `100 * count(sessions with streak < current.streak) / sessions`,
  1 decimal.

State labels (web-side, `web/lib/calmStreak.ts`, `calmStreakStateLabel(streak, windowMax, percentile)`):

| condition (evaluated top-down) | label |
|---|---|
| `streak > windowMax` (strict) | `EXTREME CALM` |
| `percentile >= 90` | `CALM` |
| otherwise | `NORMAL` |

Descriptive only. No forward-return claim anywhere in copy.

## Source

- **Cboe official delayed-quotes historical JSON**:
  `https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json`
  (no auth, ~1.7 MB, honest UA `radon/2.0`). Shape:
  `{"timestamp": "2026-09-15 13:01:45", "symbol": "_SPX", "data": [{"date": "YYYY-MM-DD", "open": "…", "high": "…", "low": "…", "close": "…", "volume": "0.0"}]}`,
  strings, ascending, 13,033 rows 1975-01-02..latest. Same CDN path family already
  used for `_COR1M` in `scripts/cri_scan.py`.
- Seams: `open` is `0` on all of 1975-77 and scattered days through 1996 (stored as
  `NULL`, unused by the definition). high==low==close on 2 days pre-1985 only.
  Rows with non-positive high, low, or close are dropped by the parser.
- Completed sessions only: rows dated after `last_completed_session_date()`
  (16:00 ET boundary, `ZoneInfo("America/New_York")`) are dropped, so an intraday
  partial row can never break or extend the streak.
- Cadence: observed `Last-Modified: Tue, 15 Sep 2026 13:02:41 GMT` carrying data
  through 2026-09-14; the regeneration time is not published. Two daily slots
  (below) plus conditional GET cover an evening or next-morning regeneration.
- **Data-source priority**: IB serves SPX index daily bars only back to 2016-08-31
  (`docs/indicators/dispersion.md`), so it cannot supply the 1985-2016 history the
  signal's comparison window needs; UW has no SPX index history surface
  (`scripts/fetch_credit_spread.py`, `docs/indicators/straddle.md`). Cboe is the
  official feed for this index and is the documented source for this metric
  (root CLAUDE.md: official feeds rank ahead of Robinhood and Yahoo). No Yahoo rung:
  a failed fetch keeps the last-good snapshot and records an error heartbeat.
- **Licensing**: Cboe terms reserve redistribution. Repo position
  (`vixts.md`, `straddle.md`, `cor.md`): storage and display inside the private
  single-operator app is acceptable; never re-serve the raw OHLC series on a public
  or share route. The API payload carries derived streaks plus the session close for
  the overlay only.

## Ingestion: `scripts/fetch_calm_streak.py`

Stdlib only (`urllib`, `json`). Pure functions, all pinned by pytest:

| Function | Contract |
|---|---|
| `parse_cboe_history(payload) -> list[dict]` | rows `{date, open, high, low, close}` floats, `open` `None` when `<= 0`; drop rows with non-positive high/low/close; ascending by date |
| `completed_sessions(rows, last_completed) -> list[dict]` | keep `date <= last_completed` |
| `compute_streaks(rows, threshold_pct=THRESHOLD_PCT) -> list[dict]` | adds `band_pct` (rounded 4dp) and `streak`; first row dropped |
| `weekly_peaks(rows) -> list[dict]` | one row per ISO week: `{date: last session date in week, streak: max streak in week, close: last close}` |
| `compute_stats(rows) -> dict` | `{max, window, percentile}` as in Signal |
| `rows_to_upsert(rows, latest_stored_date) -> list[dict]` | rows with `date >= SERIES_START` and (`latest_stored_date is None` or `date > latest_stored_date`) |
| `build_output(rows, *, scan_time, source_last_modified) -> dict` | payload below; `missing: true, reason: "insufficient_history"` when fewer than 2 published rows |
| `persist_result(payload, rows)` | write order below; never called for a missing payload |
| `run(*, now=None) -> dict` | orchestration, conditional GET |

Constants: `SERVICE = "calm-streak"`, `CBOE_SPX_URL`, `THRESHOLD_PCT = 1.0`,
`SERIES_START = "1985-01-01"`, `WINDOW_START = "1996-01-01"`, `WINDOW_END = "2016-12-31"`,
`CALM_STREAK_JSON = data/calm_streak.json`.

Seams monkeypatched by tests: `_fetch_source(if_modified_since) -> (payload_or_None, last_modified)`
(`None` means HTTP 304), `_latest_stored_date() -> Optional[str]`
(`SELECT MAX(date) FROM calm_streak_history`), `_read_json_cache()`, and the
`writer` functions.

`run()`:

1. Read the JSON cache; send its `source_last_modified` as `If-Modified-Since`.
2. **304** with a cached payload: skip parse and row upserts; persist the cached
   payload with a new `scan_time` (snapshot + heartbeat + JSON). 304 without a cache:
   refetch unconditionally.
3. **200**: parse → completed sessions → streaks → `build_output`. Missing payload:
   persist nothing, record `service_health` `error`, exit non-zero. Otherwise
   `persist_result(payload, rows_to_upsert(rows, _latest_stored_date()))`. A first
   run on an empty table therefore backfills from 1985 (~10.5k rows, 27 chunked
   statements); later runs write only new sessions.
4. Fetch failure: `service_health` `error`, no snapshot write, exit non-zero.

Write order in `persist_result`: `writer.ensure_no_replica_for_writers()` →
`writer.upsert_calm_streak_rows(rows, recorded_at=scan_time)` (skipped when `rows`
is empty) → `writer.upsert_scan_snapshot("calm-streak", scan_time, payload)` →
`writer.record_service_health("calm-streak", "ok", finished_at=scan_time)` → atomic
JSON fallback. CLI: `--json` payload to stdout, progress to stderr; `--no-db`.

## Storage: `scripts/db/migrations/0076_calm_streak.sql`

```sql
CREATE TABLE IF NOT EXISTS calm_streak_history (
    date TEXT PRIMARY KEY,
    open REAL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    band_pct REAL NOT NULL,
    streak INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calm_streak_history_date_desc ON calm_streak_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (76, datetime('now'));
```

Writer `scripts/db/writer.py:upsert_calm_streak_rows(rows, recorded_at)`: chunked
multi-row (400) `INSERT ... ON CONFLICT(date) DO UPDATE` (Hrana bounding rule).

## Payload (`scan_snapshots` service `calm-streak`)

```json
{
  "schema_version": 1,
  "scan_time": "2026-09-15T14:31:02+00:00",
  "data_date": "2026-09-14",
  "source_last_modified": "Tue, 15 Sep 2026 13:02:41 GMT",
  "source": {"name": "cboe", "url": "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json"},
  "threshold_pct": 1.0,
  "current": {"date": "2026-09-14", "streak": 28, "band_pct": 0.7312, "close": 7619.98},
  "stats": {
    "max": {"streak": 64, "date": "2017-03-20"},
    "window": {"start": "1996-01-01", "end": "2016-12-31", "streak": 26, "date": "2014-06-23"},
    "percentile": 98.9
  },
  "series": [{"date": "1985-01-04", "streak": 2, "close": 163.68}],
  "missing": false
}
```

`series` = `weekly_peaks` from 1985, ascending (~2,170 rows, keeps every peak exact
while bounding the snapshot size). `current` is the latest daily row.

## API: `web/app/api/calm-streak/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only,
  `radonCapability = "read"`, static loader in `web/lib/assistant/nextLoaders.ts`.
- `dbFirstRead`: latest `scan_snapshots WHERE service = 'calm-streak'`, disk
  `data/calm_streak.json`.
- `CALM_STREAK_MAX_AGE_MS = 48h`: the timer fires twice every calendar day; older
  than 48h means the writer is down. Past it: `staleCollapse(MISSING_CALM_STREAK, result)`.
- Missing contract (HTTP 200, exact):
  `{ missing: true, scan_time: null, data_date: null, current: null, stats: null, series: [] }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["calm-streak"] })`.
- `web/lib/calmStreak.ts`: types `CalmStreakData`, `CalmStreakPoint`,
  `CALM_STREAK_THRESHOLD_PCT = 1`, `CALM_PERCENTILE = 90`, `calmStreakStateLabel`,
  `formatBandPct(v)` (`"0.73%"`, `"---"` for null/non-finite).
- `web/lib/useCalmStreak.ts`: `useSyncHook({ endpoint: "/api/calm-streak", interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI: `web/components/CalmStreakPanel.tsx`, `web/app/regime/calm-streak/page.tsx`

- Gates: `SpectralLoader label="Loading SPX intraday band series"` while
  `(loading || syncing) && !data` → `SectionEmptyState` title `No calm streak data yet`,
  body naming `the calm-streak refresh timer` on `missing: true` → content.
- Strip (desktop `RegimeStrip`, mobile `MetricCell` grid), testIds:
  `calm-streak-value` (`28`), `calm-streak-state` (label), `calm-streak-band`
  (`formatBandPct(current.band_pct)`), `calm-streak-window-max` (`26`),
  `calm-streak-max` (`64`), `calm-streak-percentile` (`98.9%`).
- `<FreshnessRail schedule={CALM_STREAK_REFRESH} asOf={data.data_date ?? current.date} testId="calm-streak-freshness-rail" asOfTestId="calm-streak-strip-asof" />`
  directly under the strip. `CALM_STREAK_REFRESH = [daily(2, 40), daily(14, 30)]` in
  `web/lib/refreshSchedule.ts`, pinned to the unit file by `refresh-schedule.test.ts`.
- Chart: `CriHistoryChart`, title `CONSECUTIVE SESSIONS WITHOUT A >1% INTRADAY BAND`;
  SPX close on the LEFT axis (`chartSeriesColor("primary")`, `scaleType: "log"`),
  streak on the RIGHT axis. `HistoryRangeChips` + `BrushMinimap`
  (`testIdPrefix="calm-streak-brush"`), default preset `All`.
- `InfoTooltip`: the band formula, strict 1% rule, the 1996-2016 comparison window,
  source (Cboe SPX daily OHLC). Brand tokens only, 4px radius, no em dashes, no
  cadence copy beyond the rail.
- Regime registration: `web/lib/regimeRail.ts` (union, `Volatility` group or the
  closest existing volatility group, label `CALM STREAK`), `RegimePanel.tsx`
  (`tabFromPathname` regex, mobile chip array, `MOBILE_TAB_LABEL` `CALM`, dispatch branch).

## Timer: `cloud/services/radon-calm-streak.{service,timer}`

- Service: `Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/etc/radon/env`, `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_calm_streak.py`,
  `TimeoutStartSec=300`, journald.
- Timer: `OnCalendar=*-*-* 02:40:00 UTC` (evening regeneration, 5 min after
  radon-vixcor's 02:35 Cboe pull) and `OnCalendar=*-*-* 14:30:00 UTC` (after the
  observed ~13:02 UTC next-morning regeneration). `Persistent=true`,
  `RandomizedDelaySec=120`. Every calendar day: 304 runs are heartbeats.
- Register: `setup-vps.sh` `SERVICE_FILES`, `cloud/config/installed-units.sha256`
  (both hashes), `cloud/tests/test_systemd_services.py` canonical set.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`: `calm-streak`,
  26h uniform window, `scheduled`, `requires_ib: false`, daily-bucket check list.

## Tests

- `scripts/tests/test_calm_streak.py`, fixture
  `scripts/tests/fixtures/calm_streak_cboe_spx_sample.json` (trimmed real capture:
  1989-11-01..1990-03-30 plus the last 120 sessions through 2026-09-14).
- `web/tests/calm-streak-api.test.ts`, `web/tests/calm-streak-panel.test.tsx`.
- Lockstep pins: `regime-tab-routes.test.tsx`, `service-health-windows.test.ts`,
  `refresh-schedule.test.ts`, `cloud/tests/test_systemd_services.py`.
- `web/e2e/calm-streak-tab.spec.ts` (route mocks; active tab, stroked paths, brush,
  missing-state copy).

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=calm_streak`,
`<slug>=calm-streak`, `<Name>=CalmStreak`. References: `scripts/fetch_margin_debt.py`
(conditional GET + JSON cache), `scripts/ma_ratio_scan.py` (persist order),
`web/app/api/ma-ratio/route.ts` (staleCollapse), `web/components/MaRatioPanel.tsx`
(chart + rail).
