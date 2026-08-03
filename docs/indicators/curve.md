# CURVE — 10Y-2Y Treasury yield-curve spread regime tab

Spec for the `/indicator` swarm. Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementation to copy throughout: margin-debt (`scripts/fetch_margin_debt.py`,
`web/app/api/margin-debt/route.ts`, `web/components/MarginDebtPanel.tsx`).

## Identity

| Key | Value |
|---|---|
| slug (route) | `curve` → `/regime/curve` |
| service (kebab) | `yield-curve` (scan_snapshots.service, service_health, unit names) |
| PascalCase | `YieldCurve` (`YieldCurvePanel`, `useYieldCurve`) |
| Tab label | `CURVE` |
| Migration | `0032_yield_curve.sql` (version 32) |
| Timer | `radon-yield-curve.{service,timer}`, `OnCalendar=*-*-* 22:30:00 UTC` daily (incl. weekends; unchanged days heartbeat only) |

## Signal

10Y minus 2Y constant-maturity Treasury par yield spread, daily since 1990, charted
against the S&P 500. Inversion (spread below zero) is the classic late-cycle regime
marker; every US recession since 1990 was preceded by an inversion, with the downturn
historically starting after the curve re-steepens.

## Source (confirmed 2026-08-03 by research agent)

- CSV per calendar year, plain UA (`radon/2.0`), no auth, public domain (17 U.S.C. § 105):
  `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{YYYY}/all?type=daily_treasury_yield_curve&field_tdr_date_value={YYYY}&_format=csv`
- Columns: `Date` (MM/DD/YYYY), quoted tenor headers `"3 Mo"`, `"2 Yr"`, `"10 Yr"`, etc.
  **Rows descending (newest first). Header varies by year — parse by header NAME, never index.**
  Missing tenors appear as an absent column (whole-year gap) or an empty cell (mid-year gap).
  `2 Yr` and `10 Yr` are present in every year 1990+. Earliest year 1990 (1989 → empty 200).
  ~250 rows/year.
- Publish cadence: business days, FRBNY 3:30 PM ET quotes, row appears late afternoon ET.
  A 22:30 UTC ingest usually sees same-day data but MUST tolerate its absence (weekends,
  holidays, late publishes) by keeping the latest published business day.
- SPX overlay: Yahoo `https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?period1={epoch1990}&period2={now}&interval=1d`
  with UA `Mozilla/5.0` (plain UA gets 429). JSON paths `chart.result[0].timestamp[]`,
  `chart.result[0].indicators.quote[0].close[]`. Never use `range=max` (silently degrades
  to coarse bars). Best-effort: on failure, `spx_close` is null everywhere and the chart
  renders single-axis.
- Why not IB/UW: the official CMT par curve is a government statistic, not a tradable
  instrument; IB bond/futures yields do not reconcile with it and UW has no rates
  endpoints. treasury.gov is the primary source, not a fallback skip.
- Fixture (checked in): `scripts/tests/fixtures/yield_curve_2026_sample.csv` — 146 data
  rows, 01/02/2026 → 07/31/2026. Pins: 2026-07-31 y2=4.28 y10=4.75 spread≈0.47;
  2026-01-02 y2=3.47 y10=4.19 spread≈0.72.

## Ingestion — `scripts/fetch_yield_curve.py`

Small pure functions (composed-method), stdlib `csv` parsing, `requests` fetch:

- `fetch_year_csv(year, session=None) -> str` — UA `radon/2.0`, timeout 30s, raise on non-200/empty.
- `parse_treasury_csv(text) -> list[dict]` — by header name; MM/DD/YYYY → `YYYY-MM-DD`;
  empty cell / absent column → `None`; skip rows missing y2 or y10; return ASCENDING.
  Row: `{date, y3m, y2, y10}`.
- `compute_spread(row)` → `y10 - y2` (float; UI rounds).
- `fetch_spx_daily_closes() -> dict[str, float] | None` — Yahoo as above, best-effort.
- `build_series(rows, spx_closes) -> list[dict]` — ascending
  `{date, y3m, y2, y10, spread, spx_close}` (`spx_close` null when no match).
- `merge_series(cached_series, fresh_rows)` — fresh wins per date; keeps the cached
  backfill (daily runs fetch only the current year).
- `diff_new_rows(cached_series, series) -> list[dict]` — rows absent from or different
  in the cache; drives `rows_changed`.
- `build_output(series, scan_time) -> payload`:
  `{scan_time, source: "treasury", count, current: {date, y3m, y2, y10, spread}, series: [...]}`
  (`current` = last series row; `scan_time` tz-aware UTC ISO).
- `persist_result(payload, rows_changed_rows)` — refuses empty `series` (no cache write,
  no DB write); else: `writer.ensure_no_replica_for_writers()`;
  `writer.upsert_yield_curve_rows(new_rows, recorded_at=scan_time)` only when changed;
  `writer.upsert_scan_snapshot("yield-curve", scan_time, payload)` EVERY cycle;
  `writer.record_service_health("yield-curve", "ok", finished_at=scan_time)` EVERY cycle;
  atomic JSON write to `data/yield_curve.json`.
- `run(backfill: bool)` — backfill: fetch 1990..current year (all years); daily: fetch
  current year only, merge over cache. Unchanged day prints
  `[yield-curve] source unchanged; refreshing snapshot only` and skips row upserts.
- CLI: `--json` → payload to stdout (stderr for progress); `--backfill` flag.

## Storage — migration `scripts/db/migrations/0032_yield_curve.sql`

```sql
CREATE TABLE IF NOT EXISTS yield_curve_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD business day
  y3m         REAL,               -- 3-month CMT par yield, percent
  y2          REAL NOT NULL,      -- 2-year
  y10         REAL NOT NULL,      -- 10-year
  spread      REAL NOT NULL,      -- y10 - y2, percentage points
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yield_curve_history_date
  ON yield_curve_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (32, datetime('now'));
```

Writer (`scripts/db/writer.py`): `YIELD_CURVE_UPSERT_SQL` = `INSERT INTO yield_curve_history
(...) VALUES (?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET ...`;
`upsert_yield_curve_rows(rows, recorded_at)` — execute per row, commit once (margin-debt
shape); backfill passes ~9,000 rows, daily passes 0-2.

## API — `web/app/api/yield-curve/route.ts` (GET only)

Mirror margin-debt exactly:
- `dynamic = "force-dynamic"`, `runtime = "nodejs"`.
- `dbFirstRead` — fromDb: latest `scan_snapshots WHERE service = 'yield-curve'`;
  fromDisk: `../data/yield_curve.json`; `MAX_AGE_MS = 48h` (daily timer; older means the
  writer is down); label `"yield-curve"`.
- `MISSING_YIELD_CURVE = { missing: true, scan_time: null, count: 0, series: [], current: null }`
  at HTTP 200, never 4xx.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600,
  requestId, cacheState: "HIT", tags: ["yield-curve"] })`.

Hook `web/lib/useYieldCurve.ts`: `useSyncHook`, endpoint `/api/yield-curve`,
`interval: 60 * 60_000`, `hasPost: false`, `extractTimestamp: d => d.scan_time || null`.

Helpers `web/lib/yieldCurve.ts` (pure, unit-tested):
- Types `YieldCurvePoint`, `YieldCurveCurrent`, `YieldCurveData` (incl. `missing?`).
- `formatSpreadPct(v)` → `+0.47%` / `-0.85%` / `---` for null (2 decimals, explicit sign).
- `formatYieldPct(v)` → `4.75%` / `---`.
- `spreadColor(v)`: null → `var(--text-muted)`; `v < 0` → `var(--negative)`;
  `v < 0.25` → `var(--warning)`; else → `var(--positive)`. (Boundaries: `0` is warning,
  `0.25` is positive — pin both.)
- `formatSessionDate("2026-07-31")` → `31 Jul 2026`; `formatDateTick("2026-07-31")` → `Jul 2026`.

## UI — `web/components/YieldCurvePanel.tsx` + registration

- Gates: `SpectralLoader label="Loading Treasury yield curve series"` while
  `(loading || syncing) && !data`; `SectionEmptyState` headline `"No yield curve data yet"`,
  secondary `"The yield-curve refresh timer populates this tab from the US Treasury daily
  par yield feed. Data appears after the first successful pull."`
- Section title `Yield Curve`; header clock renders `lastSync` local time (margin-debt style).
- InfoTooltip: `"Official US Treasury constant-maturity par yields, daily since 1990. The
  10Y minus 2Y spread inverts (goes negative) when short rates exceed long rates. Every US
  recession since 1990 was preceded by an inversion, and downturns have historically begun
  after the curve re-steepened. Sustained deep inversion is a late-cycle regime marker,
  not a timing signal."`
- Desktop `RegimeStrip` cells:
  1. `SPREAD 10Y-2Y` — `formatSpreadPct(current.spread)` colored by `spreadColor`,
     sub `BELOW ZERO READS AS INVERSION`, testid `yield-curve-spread-value`
  2. `10Y` — `formatYieldPct(current.y10)`, sub `CONSTANT MATURITY PAR YIELD`
  3. `2Y` — `formatYieldPct(current.y2)`, sub `CONSTANT MATURITY PAR YIELD`
  4. `LATEST SESSION` — `current.date` via `formatSessionDate`, sub `DAILY SERIES SINCE 1990`
- Mobile: `useViewport()` → `m-regime-grid2x2` of `MetricCell`, testid
  `yield-curve-mobile-grid` (labels `SPREAD`, `10Y`, `2Y`, `SESSION`).
- Chart: `CriHistoryChart`, title `S&P 500 VS 10Y-2Y TREASURY SPREAD`, left series SPX
  `chartSeriesColor("primary")` `scaleType: "log"`, right series `SPREAD %`
  `chartSeriesColor("fault")` linear, `xTickFormat = formatDateTick`. Container
  `className="breadth-history-block"` testid `yield-curve-chart-section`.
- Range: `HistoryRangeChips` (standard session presets) defaulting to **`all`** (36-year
  daily series; override like margin-debt, respect `rangeTouched`), `BrushMinimap`
  `values = series.map(p => p.spread ?? 0)`, `testIdPrefix="yield-curve-brush"`,
  `ariaLabel="Yield curve history range brush"`.
- Footnote (mono 9px muted): `Source: US Treasury daily par yield curve (FRBNY 3:30 PM ET
  quotes). SPX overlay: Yahoo Finance daily closes.` **No cadence claims anywhere in copy**
  beyond "daily series", which the data itself is; freshness is shown via the header clock
  (scan_time) and LATEST SESSION (data date). No em dashes. Tokens only, 4px max radius.
- Registration (all in the UI worktree): `RegimeTab` union + `REGIME_TAB_VALUES` +
  `tabFromPathname` regex + desktop button `CURVE` + dispatch branch in
  `web/components/RegimePanel.tsx`; route page `web/app/regime/curve/page.tsx`
  (5-line `WorkspaceShell section="regime"`); add `["curve", "app/regime/curve/page.tsx"]`
  to `web/tests/regime-tab-routes.test.tsx` + render/nav cases.

## Health / scheduling

- `web/lib/serviceHealthWindows.ts`: `"yield-curve": { open: 26*HOUR, extended: 26*HOUR,
  closed: 26*HOUR, category: "scheduled", requires_ib: false }` + add to the exhaustive
  `expected` set in `web/tests/service-health-windows.test.ts`.
- `scripts/watchdog/services.py`: `"yield-curve": {"open": 26*_HOUR, "closed": 26*_HOUR,
  "requires_ib": False}` + append to the daily-bucket check list.
- `cloud/services/radon-yield-curve.service`: Type=oneshot, User=radon,
  WorkingDirectory=/home/radon/radon, EnvironmentFile=/home/radon/radon-cloud/.env,
  Environment=RADON_DB_NO_REPLICA=1,
  ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_yield_curve.py,
  TimeoutStartSec=300, journal out/err, StartLimitIntervalSec=300, StartLimitBurst=5.
- `cloud/services/radon-yield-curve.timer`: `OnCalendar=*-*-* 22:30:00 UTC` (comment: after
  the ~3:30 PM ET FRBNY publish settles; weekend/holiday runs are no-op heartbeats so the
  26h staleness window never widens), `Persistent=true`, `RandomizedDelaySec=300`.
- Append both units to `cloud/scripts/setup-vps.sh` `SERVICE_FILES` and to
  `cloud/tests/test_systemd_services.py`'s canonical list.

## Live intraday estimate (added 2026-08-03)

The official CMT series is EOD-only, so the tab carries a clearly-labeled
intraday ESTIMATE in the strip's fifth cell:

- **Flavor is 10Y-3M, not 10Y-2Y.** Live probe findings (2026-08-03): IB has no
  live CMT legs on this account — CME micro Treasury yield futures (2YY/10Y)
  are DELISTED (0 contracts), and TNX/CBOE returns no data without a CBOE index
  subscription; UW has no rates endpoints; Yahoo's `2YY=F` is dead (last trade
  2026-07-15). Yahoo `^TNX` (10Y) and `^IRX` (13-week bill) are both live from
  the same source with the same delay, so 10Y-3M is the only spread that needs
  no modeling. Deriving a live 2Y from note-futures prices (DV01 math) was
  rejected as fabricated precision. Note `^IRX` is a discount-basis bill quote,
  not CMT bond-equivalent — a few bp of systematic convention offset is
  acceptable for an ESTIMATED tile.
- Route: `web/app/api/yield-curve/live/route.ts` — GET-only, force-dynamic,
  Yahoo v8 chart meta (`regularMarketPrice`/`regularMarketTime`) with UA
  `Mozilla/5.0`, plain-yield sanity bounds 0-20% (a Yahoo scale change must
  read as missing, never as a 46.9% yield), 60s in-process cache that never
  caches the missing shape, `asof` = the OLDER of the two legs, contract
  `{missing:true, y10:null, y3m:null, spread_10y_3m:null, asof:null, source:null}`
  at HTTP 200 on any failure.
- Hook `useYieldCurveLive` polls every 5 min while the tab is open.
- Cell: label `10Y-3M LIVE`, value toned by `spreadColor`, sub
  `EST ^TNX MINUS ^IRX · AS OF {h:mm} ET` (derived timestamp, never a cadence
  claim) or `ESTIMATE UNAVAILABLE`. Desktop only; the official EOD 10Y-2Y
  stays the headline. Mobile grid unchanged (4 cells).

## Timer cadence (updated 2026-08-03)

Two OnCalendar passes: `Mon..Fri 20:45 UTC` (early pickup — within ~1h of the
publish in summer; may land before it in winter) plus the daily `22:30 UTC`
catch-all (also the weekend/holiday heartbeat).

## Tests (written first, red before implementation)

- `scripts/tests/test_yield_curve.py` — fixture parse pins, header-name parsing across
  year shapes, empty-cell handling, merge/diff, payload contract, persist guards,
  migration 32 schema + idempotent upsert. DB access only via monkeypatched writer.
- `web/tests/yield-curve-api.test.ts` — in-memory libsql `scan_snapshots`; Turso beats
  older disk; disk fallback; exact missing object at 200; no `margin-debt` leak;
  `dynamic === "force-dynamic"`.
- `web/tests/yield-curve-panel.test.tsx` — helper pins (formats, `spreadColor`
  boundaries), loader/empty/strip/title/brush/default-All, NaN guard.
- `web/e2e/curve-tab.spec.ts` — route mocks; active tab, strip values, ≥2 stroked paths,
  brush visible, missing-state copy.
