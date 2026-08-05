# STRADDLE — SPX Realized vs Implied 1-Day Straddle

Daily signed ratio of the SPX close-to-close move to the option-implied 1-day
straddle at the prior close. Reference: operator-supplied Bloomberg-style chart
("SPX Realized 1-Day Straddle / SPX Implied 1-Day Straddle", 2016-2026, avg
0.06, stddev 1.31). Our reproduction from Cboe data (2022-05+): avg 0.063,
pstdev 1.16, low -4.97 vs high +3.78 — same shape, shorter window.

- **slug**: `straddle` (route `/regime/straddle`)
- **service**: `straddle` (kebab-case everywhere: `scan_snapshots.service`,
  `service_health`, systemd units)
- **Name**: `Straddle` (`StraddlePanel`, `useStraddle`)
- **Tab label**: `STRADDLE`
- **Migration**: `0033_straddle.sql` (version 33)

## Signal definition

For each session `t` with prior common session `t-1`:

```
move_pct_t            = (SPX_t / SPX_{t-1} - 1) * 100                       (signed)
implied_straddle_pct  = sqrt(2/pi) * (VIX1D_{t-1} / 100) * sqrt(1/252) * 100
ratio_t               = move_pct_t / implied_straddle_pct
```

`sqrt(2/pi) ≈ 0.7978845608028654` is the Brenner-Subrahmanyam ATM-straddle
approximation (straddle ≈ 0.8 σ√T of spot); VIX1D is annualized, de-annualized
with `sqrt(1/252)` trading-day convention. The divisor uses the PRIOR close's
VIX1D — the implied 1-day straddle priced before the move happened. Friday's
VIX1D covers the weekend gap to Monday by construction (prior-common-session
convention; document, don't adjust).

Interpretation: `|ratio| > 1` (strict) means the realized move exceeded the
implied straddle breakeven — a long 1-day straddle bought at the prior close
paid off. `ratio > 1` → upside overshoot (`var(--positive)`); `ratio < -1` →
downside overshoot (`var(--negative)`); in `[-1, +1]` and null → muted
(`var(--text-muted)`). Strict inequalities; boundary `±1` is muted (pinned).

Stats over all non-null ratios: `high`, `low`, `avg`, `stddev` (**population**
stddev, `statistics.pstdev`), `hit_rate` = share of days with `|ratio| > 1`
(strict). Fixture-derived (2026-08-05, fixtures below): n=1058, high
3.775801, low -4.968372, avg 0.063255, pstdev 1.157428, hit_rate 0.367675.

## Source facts (research 2026-08-05, evidence in session)

| | SPX leg | VIX1D leg |
|---|---|---|
| URL | `https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv` | `.../VIX1D_History.csv` |
| Columns | `DATE,SPX` (`MM/DD/YYYY`) | `DATE,OPEN,HIGH,LOW,CLOSE` |
| Depth | 1975-01-02 → present | **2022-05-13** → present (binding constraint; the reference chart's 2016 start is NOT reproducible) |
| Auth | none | none |
| Conditional GET | `Last-Modified` + `ETag`, both 304-verified | same |

- Transport: plain HTTPS GET, honest UA `radon/2.0` (never impersonate a browser).
- Cadence: the day's row lands roughly **20:00 ET** after the close. Cboe
  re-touches Last-Modified intraday WITHOUT appending the row, so Last-Modified
  is "bytes changed", not "new session available" — the 304 fast path is still
  correct (304 ⇒ bytes unchanged ⇒ skip parse + row upserts, heartbeat only).
- Licensing: freely served Cboe CDN files (same files behind Cboe's public
  dashboard); OK for internal single-operator dashboard storage + display. Do
  not publicly re-serve the raw series.
- Priority chain: IB carries VIX1D but no deeper history than the CSV and needs
  an authenticated stateful Gateway for a once-daily series; UW has no index
  history surface. Cboe CDN primary, Yahoo `^GSPC` fallback for the SPX leg
  only if Cboe breaks.
- Fixtures (full real files, captured 2026-08-05):
  `scripts/tests/fixtures/spx_history_sample.csv` (13,005 data rows),
  `scripts/tests/fixtures/vix1d_sample.csv` (1,059 data rows).

## Ingestion — `scripts/fetch_straddle.py` + `scripts/clients/cboe_client.py`

Modeled on `fetch_margin_debt.py` / `finra_client.py`. Composed-method pure
functions:

- `CboeClient.fetch_history(symbol, if_modified_since=None) -> (text | None, last_modified)`
  — `None` text on 304. Symbols: `"SPX"`, `"VIX1D"`.
- `parse_index_csv(text, value_column) -> list[{date: "YYYY-MM-DD", value: float}]`
  ascending; converts `MM/DD/YYYY`; skips header/malformed rows.
- `implied_straddle_pct(vix1d: float) -> float` — the formula above.
- `compute_series(spx_rows, vix1d_rows) -> list[{date, spx_close, vix1d_close, ratio}]`
  over the **common dates** of both series (intersection, sorted); `ratio` is
  `None` on the first common date (no prior session). SPX-only dates
  (pre-2022, calendar mismatches) are excluded.
- `compute_stats(series) -> {high, low, avg, stddev, hit_rate}` over non-null
  ratios; `None` when no ratios.
- `run(client=None, *, now=None)` — conditional GET on BOTH files using the
  cached per-file stamps; **both** 304 → reuse cached payload with fresh
  `scan_time`, `_write_db_cache(..., rows_changed=False)`; otherwise rebuild
  and `rows_changed=True`. Empty parse → raise (never cache empty).
- Persistence order per cycle: `ensure_no_replica_for_writers()` →
  `upsert_straddle_rows(series, recorded_at=scan_time)` (only when changed) →
  `upsert_scan_snapshot("straddle", scan_time, payload)` →
  `record_service_health("straddle", "ok", finished_at=scan_time)` → atomic
  JSON fallback `data/straddle.json`.
- CLI: `--json` → payload to stdout; summary/progress to stderr.

## Payload contract

```jsonc
{
  "scan_time": "2026-08-05T02:15:00Z",          // tz-aware UTC ISO, Z suffix
  "source_last_modified": {                      // per-file HTTP Last-Modified
    "spx": "Wed, 05 Aug 2026 17:01:43 GMT",
    "vix1d": "Wed, 05 Aug 2026 18:31:25 GMT"
  },
  "count": 1059,                                 // series length (incl. null-ratio first row)
  "current": {
    "date": "2026-08-04",
    "ratio": 3.775801,
    "move_pct": 1.789619,
    "implied_straddle_pct": 0.473971,
    "spx_close": 7736.52,
    "vix1d_prior": 9.43
  },
  "stats": { "high": 3.775801, "low": -4.968372, "avg": 0.063255,
             "stddev": 1.157428, "hit_rate": 0.367675 },
  "series": [ { "date": "2022-05-13", "spx_close": 4023.89,
                "vix1d_close": 33.63, "ratio": null }, ... ]
}
```

## Storage — `0033_straddle.sql`

```sql
CREATE TABLE IF NOT EXISTS straddle_history (
  date        TEXT PRIMARY KEY,
  spx_close   REAL NOT NULL,
  vix1d_close REAL NOT NULL,
  ratio       REAL,               -- NULL on the first session (no prior close)
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_straddle_history_date ON straddle_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (33, datetime('now'));
```

Writer: `scripts/db/writer.py` gains `STRADDLE_UPSERT_SQL`
(`INSERT ... ON CONFLICT(date) DO UPDATE`) + `upsert_straddle_rows(series,
recorded_at)` using chunked multi-row inserts per the Hrana bounding rules
(exemplar `upsert_price_history_rows`).

## API — `web/app/api/straddle/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only (timer-driven
  daily series, no manual-scan POST).
- `dbFirstRead`: Turso `scan_snapshots WHERE service = 'straddle'` latest, disk
  fallback `data/straddle.json`, freshest content timestamp wins.
- `MAX_AGE_MS = 48h` — the timer fires daily (incl. weekends, heartbeat-only);
  older than 48h means the writer is down.
- Missing contract, HTTP 200 exactly:
  `{ missing: true, scan_time: null, count: 0, series: [], current: null, stats: null }`
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["straddle"] })`.
- Hook `web/lib/useStraddle.ts`: `useSyncHook({ endpoint: "/api/straddle",
  interval: 60 * 60_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI — `web/components/StraddlePanel.tsx` (+ `web/lib/straddle.ts`)

Pure helpers in `web/lib/straddle.ts`:

- `formatRatio(v)` → signed two decimals: `+3.78`, `-4.97`, `---` for
  null/undefined/non-finite.
- `ratioColor(v)` → strict `> 1` `var(--positive)`, strict `< -1`
  `var(--negative)`, else (incl. exactly ±1, null) `var(--text-muted)`.
- `formatStraddlePct(v)` → `0.47%`, `---` for null/non-finite.
- `formatHitRate(v)` → `36.8%` (fraction in, one decimal), `---` null.
- `formatSourceDate(httpDate)` → `05 Aug 2026` (`---` null; passthrough unparsable).
- `buildStraddleChartRows(series)` → `[{ date, ratio, spx }]` (nulls preserved).
- Types `StraddleEntry`, `StraddleData` (payload above + missing variant).

Panel:

- Gate order: `SpectralLoader` `label="Loading Cboe straddle series"` while
  `(loading || syncing) && !data` → `SectionEmptyState` on `missing: true` with
  title `No straddle data yet` (copy may reference "the straddle refresh
  timer") → content.
- Header strip (`RegimeStrip` desktop / `MetricCell` grid mobile): LAST
  (`data-testid="straddle-last-value"`, colored by `ratioColor`), AVG, STDDEV,
  HIGH, LOW, HIT RATE (share of sessions beyond breakeven), IMPLIED 1D (current
  `implied_straddle_pct`), latest date, SOURCE UPDATED (vix1d Last-Modified via
  `formatSourceDate`).
- Chart: `CriHistoryChart`, title `SPX VS 1-DAY STRADDLE RATIO`; left axis SPX
  (`chartSeriesColor("primary")`, linear — 3-year span, not multi-decade);
  right axis the ratio, linear, zero reference line; `xTickFormat` for daily
  dates. `HistoryRangeChips` presets + `BrushMinimap`
  (`testIdPrefix="straddle-brush"`), default preset per
  `defaultPresetForLength`.
- `InfoTooltip`: "Signed SPX daily move divided by the prior close's implied
  1-day straddle (0.8 x VIX1D x sqrt(1/252)). Beyond +1 or -1 the move beat
  the straddle breakeven: buyers of 1-day vol won. Persistent readings beyond
  1 mean 0DTE vol is underpriced; readings pinned inside the band mean it is
  rich." (no em dashes)
- Freshness copy derived only: header clock renders `lastSync`; SOURCE UPDATED
  renders the payload stamp. No hardcoded cadence strings.

## Registration (lockstep pins)

- `RegimePanel.tsx` four places + mobile chip array + dispatch branch
  (`activeTab === "straddle"` → `<StraddlePanel />`), label `STRADDLE`.
- `web/app/regime/straddle/page.tsx` — 5-line `WorkspaceShell section="regime"`.
- `web/tests/regime-tab-routes.test.tsx` — add `["straddle", "app/regime/straddle/page.tsx"]` + render case.
- `web/lib/serviceHealthWindows.ts` — `straddle` entry, daily category, window
  matching margin-debt's daily convention; `web/tests/service-health-windows.test.ts`
  exhaustive set += `straddle`.
- `scripts/watchdog/services.py` — same window + daily-bucket list.
- `cloud/services/radon-straddle.{service,timer}` — oneshot venv-python direct
  (`ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_straddle.py`),
  `EnvironmentFile=/home/radon/radon-cloud/.env`, `RADON_DB_NO_REPLICA=1`;
  timer `OnCalendar=*-*-* 02:15:00 UTC` (Cboe appends the session row ~20:00 ET
  ≈ 00:00-01:00 UTC; 02:15 UTC clears both DST regimes), `RandomizedDelaySec=300`,
  `Persistent=true`. Runs daily including weekends: weekend runs are 304
  heartbeats that keep `service_health` fresh inside the 48h window.
- `cloud/scripts/setup-vps.sh` `SERVICE_FILES` += both units;
  `cloud/tests/test_systemd_services.py` canonical set += both.

## Test evidence pins (fixture-derived 2026-08-05)

- vix1d fixture: 1,059 rows; first `2022-05-13` close `33.63`; last
  `2026-08-04` close `13.86`.
- spx fixture: 13,005 rows; first `1975-01-02` `70.23`; last `2026-08-04`
  `7736.52` (prior `2026-08-03` `7600.50`).
- `implied_straddle_pct(20.0) = 1.005240058486846`.
- Common dates: 1,059; first computable ratio `2022-05-16` ≈ `-0.233474`.
- Last ratio `2026-08-04` ≈ `3.775801` (move `1.789619%`, implied `0.473971%`,
  prior VIX1D `9.43`).
