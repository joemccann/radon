# MA RATIO — SPX percent above 50-day MA over percent above 200-day MA

Route `/regime/ma-ratio` · service `ma-ratio` · component `MaRatioPanel` · tab label `MA RATIO` · migration `0068`

## Signal

For each current S&P 500 constituent, compute the simple moving average of its own
daily closes over its trailing 50 and 200 member sessions. Per session:

```
pct_above_50  = 100 * count(close > SMA50)  / eligible_50    (members with >= 50 closes)
pct_above_200 = 100 * count(close > SMA200) / eligible_200   (members with >= 200 closes)
ratio         = pct_above_50 / pct_above_200
```

Strict inequality: a close exactly on its SMA does NOT count as above — pinned in
tests. **Zero-denominator guard**: when `pct_above_200 == 0` the ratio is `null`
(stored as SQL `NULL`, serialized as JSON `null`); the row is still written so the
component percentages survive a full washout. This is the StockCharts
`$SPXA50R:$SPXA200R` construction, computed from constituent closes rather than a
vendor ratio series (we do not scrape StockCharts).

Interpretation (reference chart, 2021-2026): the ratio oscillates around 1.0; deep
washouts print 0.25-0.5 (Jun/Sep 2022, Oct 2023, Dec 2024, Apr 2025, Apr 2026). The
zone painted on the chart is **0.25-0.5** (both reference lines confirmed on the
1 Sep 2026 StockCharts display chart; tweet sp3cul8r/2094946333945725100). The
buy-style signal is the ratio **turning up from inside that zone**: previous session
ratio within [0.25, 0.5] (inclusive at both edges, pinned in tests) and the latest
ratio strictly above the previous. A reading inside the zone that has not turned up
is a wash-out condition, not yet a signal.

State labels (web-side, `web/lib/maRatio.ts`, half-open bands pinned in tests):

| ratio | label |
|---|---|
| < 0.25 | `WASHED OUT` |
| >= 0.25 and <= 0.5 | `SIGNAL ZONE` |
| > 0.5 and < 1.0 | `50D LAGGING` |
| >= 1.0 | `50D LEADING` |

This is a regime description of breadth momentum. No forward-information claim is
made anywhere in copy.

## Sources

1. **Constituents**: `scripts/clients/index_constituents.py`
   `resolve_constituents("SPX")` cache/seed chain (the same resolver bpi-scan uses;
   never fails, `MIN_PLAUSIBLE_COUNTS["SPX"] = 400` floor).
2. **Member daily closes**: the shared Turso `price_history_daily` store, maintained
   by `scripts/bpi_scan.py:ensure_member_history` — batched Yahoo v8 spark for the
   staleness sweep with per-symbol v8 chart fallback, completed sessions only
   (`d <= last_completed_session_date()`), split-adjusted dividend-UNadjusted quote
   closes. Bulk member pulls are the **sanctioned Yahoo deviation** per the
   `bpi_scan.py` precedent (IB pacing and UW rate limits are wrong for ~500 daily-bar
   pulls; the tasks/bpi-indicator-plan.md §2 verdict carries over unchanged because
   this job rides the exact same machinery and store). Aggregate display only; no
   per-ticker quote redistribution. Yahoo requests reuse bpi's fetchers (browser UA
   for Yahoo per the fetch_credit_spread/bpi precedent — the plain UA gets 429);
   everything non-Yahoo in this slice uses the honest `radon/2.0` UA convention.
3. **SPX overlay**: `^GSPC` daily closes stored in the SAME `price_history_daily`
   store via the same sweep (the overlay symbol is appended to the member fetch
   list). Yahoo is sanctioned for this rung by the margin-debt precedent
   (`_fetch_spx_monthly_closes`); the overlay is optional — a missed fetch leaves
   `spx_close` null and the chart hides the overlay for those rows.

Licensing: constituent lists are uncopyrightable factual data; Yahoo chart closes
follow the existing repo-sanctioned bulk-deviation precedent; StockCharts is used
only as a visual reference and is never fetched.

## Ingestion — `scripts/ma_ratio_scan.py`

Composed-method style, stdlib-only computation. Pure functions:
`sma_flags_series(closes_by_date)` (per-member above-50/above-200 flags on the
member's own session axis), `aggregate_ma_ratio(member_flags, sessions)`
(carry-forward across missing member days, like bpi's `aggregate_bpi`),
`compute_ratio(pct50, pct200)` (zero-denominator guard), `attach_spx_series(...)`,
`build_output(...)`.

- Members and `^GSPC` sweep through `bpi_scan.ensure_member_history` with an
  ma-ratio-owned wall-clock deadline. `SWEEP_BUDGET_S = 1500`: this is an SPX-only
  sweep (~504 symbols incl. the overlay), one fifth of bpi's three-index universe,
  and the incremental path is ~26 spark requests with chart fallback only for
  stragglers; the divyield sibling (503 per-symbol chart calls, 6 workers) self-limits
  at 1800s inside the same 2100s unit budget, so 1500s + persist reserve fits with
  slack. Budget-vs-unit nesting pinned in pytest.
- Session gating mirrors bpi R-224: the latest aggregated session must be reported
  fresh by >= 80% of constituents or the run emits a `missing: true` payload and
  persists nothing (never cache an empty/degenerate result).
- A session row exists only when `eligible_200 >= 0.8 * member_count` — early
  sessions where too few members carry a full 200-close window are not emitted.
- `install_sigterm_unwind()` (bpi R-225): systemd SIGTERM unwinds instead of
  killing mid-write.
- Writes, in order, every cycle: `ensure_no_replica_for_writers()` →
  `upsert_ma_ratio_rows(rows, recorded_at=scan_time)` (full computed window,
  idempotent per date) → `upsert_scan_snapshot("ma-ratio", scan_time, payload)` →
  `record_service_health("ma-ratio", "ok", finished_at=scan_time)` → atomic JSON
  fallback `data/ma_ratio.json`. Turso is the source of truth. Weekend/holiday runs
  recompute the same rows (idempotent upsert) and act as unchanged-data heartbeats.
- CLI: `--json` (payload to stdout; ALL progress to stderr), `--no-db` (skip all
  Turso I/O), `--backfill` (2y Yahoo range for every member — run once to seed).

## Storage — `scripts/db/migrations/0068_ma_ratio.sql`

```sql
CREATE TABLE IF NOT EXISTS ma_ratio_history (
    date TEXT PRIMARY KEY,
    pct_above_50 REAL NOT NULL,
    pct_above_200 REAL NOT NULL,
    ratio REAL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ma_ratio_history_date_desc ON ma_ratio_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (68, datetime('now'));
```

`ratio` is nullable (zero-denominator guard); `spx_close` is nullable (optional
overlay). Writer `scripts/db/writer.py:upsert_ma_ratio_rows(rows, recorded_at)` —
chunked multi-row `INSERT ... ON CONFLICT(date) DO UPDATE` (Hrana I/O bounding; the
daily window passes ~350 rows). Arity + idempotency pinned in pytest against the
migration executed into in-memory sqlite.

## Payload (scan_snapshots service `ma-ratio`)

```json
{
  "schema_version": 1,
  "scan_time": "2026-09-02T22:45:11+00:00",
  "data_date": "2026-09-01",
  "source": {"constituents": "cache", "constituents_count": 503,
             "member_close_fetches": {"yahoo": 490, "stored": 13}},
  "zone": {"low": 0.25, "high": 0.5},
  "current": {
    "date": "2026-09-01",
    "pct_above_50": 46.5, "pct_above_200": 64.6, "ratio": 0.72,
    "count_above_50": 234, "count_above_200": 325,
    "eligible_50": 503, "eligible_200": 503,
    "spx_close": 7631.47
  },
  "series": [
    {"date": "2025-01-02", "pct_above_50": 55.1, "pct_above_200": 61.0,
     "ratio": 0.9, "spx_close": 5868.55}
  ],
  "missing": false
}
```

`series` is ascending by date. `ratio: null` rows are legal (full washout).

## API — `web/app/api/ma-ratio/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only,
  `radonCapability = "read"`, static loader entry in
  `web/lib/assistant/nextLoaders.ts`.
- `dbFirstRead`: `fromDb` = latest `scan_snapshots WHERE service = 'ma-ratio'`,
  `fromDisk` = `data/ma_ratio.json`, fresher content timestamp wins.
- `MAX_AGE_MS = 48h` — daily 22:45 UTC timer with slack; older than 48h means the
  writer is down, not merely between runs. Missing contract: HTTP 200 + frozen
  `{ missing: true, scan_time: null, data_date: null, current: null, series: [], zone: null }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["ma-ratio"] })`.
- `web/lib/maRatio.ts`: types + pure helpers (`maRatioStateLabel(ratio)`,
  `maRatioZoneTurnUp(series)`, formatters, `MA_RATIO_ZONE = { low: 0.25, high: 0.5 }`).
- `web/lib/useMaRatio.ts`: `useSyncHook({ endpoint: "/api/ma-ratio",
  interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI — `web/components/MaRatioPanel.tsx`, `web/app/regime/ma-ratio/page.tsx`

- Gate order: `SpectralLoader` (`label="Loading SPX moving average breadth series"`)
  while `(loading || syncing) && !data` → `SectionEmptyState` on `missing: true` →
  content.
- Strip (`RegimeStrip` desktop, `MetricCell` grid mobile): `RATIO` (2dp, state
  color), `% > 50D MA`, `% > 200D MA`, `SPX CLOSE`, `SIGNAL` (`TURN UP FROM ZONE` /
  `IN ZONE` / `---`). Header clock renders payload `scan_time`.
- **FreshnessRail is mandatory**: `<FreshnessRail schedule={MA_RATIO_REFRESH}
  asOf={data.data_date ?? current.date} testId="ma-ratio-freshness-rail"
  asOfTestId="ma-ratio-strip-asof" />` directly under the strip (IvRankPanel
  exemplar). `MA_RATIO_REFRESH` in `web/lib/refreshSchedule.ts` mirrors the timer
  (daily 22:45 UTC) and is pinned to the unit file by `refresh-schedule.test.ts`.
- Chart: `CriHistoryChart`, SPX overlay on the LEFT axis
  (`chartSeriesColor("primary")`, `scaleType: "log"`), ratio on the RIGHT axis, and
  the 0.25-0.5 signal zone painted as a right-axis reference band in
  `var(--warning)` (new optional `referenceBands` prop on `CriHistoryChart`,
  backwards compatible; band values fold into the right-scale domain). Title
  `SPX PCT ABOVE 50D MA / PCT ABOVE 200D MA`. `HistoryRangeChips` +
  `BrushMinimap` (`testIdPrefix="ma-ratio-brush"`).
- `InfoTooltip` explains the construction, the strict-inequality rule, the 0.25-0.5
  zone, and the turn-up signal. **No em dashes in copy. No cadence claims** except
  copy naming the real timer ("the ma-ratio refresh timer"). Brand tokens only,
  4px max radius.
- Regime registration: `web/lib/regimeRail.ts` (`RegimeTab` union, Breadth &
  sentiment group, label `MA RATIO`), `RegimePanel.tsx` (`tabFromPathname` regex,
  mobile chip array, `MOBILE_TAB_LABEL`, dispatch branch), `/regime/ma-ratio` page.
  NOT `/regime/breadth` (that is NYSE IB A/D and stays unchanged).

## Timer — `cloud/services/radon-ma-ratio.{service,timer}`

- Service: `Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/etc/radon/env`, `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/ma_ratio_scan.py`
  (venv python directly, never the wrapper), `TimeoutStartSec=2100`
  (`SWEEP_BUDGET_S=1500` + one in-flight fetch + persist slack; sized for an
  SPX-only sweep against the divyield 503-symbol precedent, NOT bpi's 3-index
  6900s budget).
- Timer: `OnCalendar=*-*-* 22:45:00 UTC` — after the 16:00 ET close year-round,
  five minutes behind radon-divyield's 22:40 so the two SPX constituent sweeps
  never share a minute. `Persistent=true`, `RandomizedDelaySec=300`. Runs every
  calendar day: weekend and holiday runs are unchanged-data heartbeats that keep
  service_health fresh inside the 26h window (ivrank convention).
- Register: `setup-vps.sh` `SERVICE_FILES` + `cloud/config/installed-units.sha256`
  (both hashes, same commit) + `cloud/tests/test_systemd_services.py` canonical set
  and a `TestMaRatioScanBudget` nesting check.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`: `ma-ratio`,
  uniform 26h window, `scheduled`, `requires_ib: false` (Yahoo + Turso only; no
  IB-gateway copy anywhere because IB is not in this job's runtime path), plus the
  watchdog daily-bucket check list.

## Tests

- `scripts/tests/test_ma_ratio.py` — fixture
  `scripts/tests/fixtures/ma_ratio_member_closes_sample.json` (a SMALL real capture:
  a handful of member symbols plus `^GSPC`, ~1y of daily closes each; capture
  metadata in a `_fixture_note`). Expectations are recomputed from the fixture with
  an independent naive SMA implementation inside the test, never mental arithmetic.
  Cover: strict-inequality boundary (close == SMA not above), zero-denominator
  guard (`pct_above_200 == 0` → ratio None), carry-forward aggregation, coverage
  gate (missing payload), migration 0068 into in-memory sqlite (schema + version +
  rerunnable + nullable ratio), writer arity + idempotency, persist order incl.
  heartbeat, sweep-budget-fits-unit nesting, payload contract keys, window-relative
  dates.
- `web/tests/ma-ratio-api.test.ts` (`@vitest-environment node`) — in-memory
  `@libsql/client`: Turso-beats-older-disk, disk fallback, exact `missing: true`
  object at 200, no cross-service leak, `route.dynamic === "force-dynamic"`.
- `web/tests/ma-ratio-panel.test.tsx` (`@vitest-environment jsdom`) — loader label,
  empty state, strip values, zone/state boundary pins, chart title, NaN guard,
  reference band rendered, freshness rail (fake timers pinned one hour before the
  22:45 UTC slot).
- Lockstep pins updated with the feature in the same commit:
  `regime-tab-routes.test.tsx`, `service-health-windows.test.ts`,
  `refresh-schedule.test.ts`, `cloud/tests/test_systemd_services.py`.
- `web/e2e/ma-ratio-tab.spec.ts` — route mocks per the divyield pattern; active
  tab, stroked paths, brush, missing-state copy. Screenshot committed at
  `docs/indicators/ma-ratio-tab.png`.

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=ma_ratio`,
`<slug>=ma-ratio`, `<Name>=MaRatio`, service `ma-ratio`. Reference implementations:
`scripts/bpi_scan.py` (member-close store + sweep machinery, reused directly),
`scripts/fetch_divyield.py` (SPX-only sweep budget + persist order),
`IvRankPanel.tsx` (FreshnessRail exemplar), `web/app/api/divyield/route.ts`
(dbFirstRead shape).
