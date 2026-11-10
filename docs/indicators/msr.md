# MSR: Move Surprise Ratio (SPX, NDX, RUT)

Magnitude of an index's 1-day % change relative to its 1-month implied vol at
the prior close. Source idea: Goldman Sachs "move surprise ratio" note (week
of 2026-11-02): "SPX experienced the largest MSR in 19yrs this week, NDX top 2,
RTY top 5". GS divides by 1M ATM implied vol; Radon divides by the Cboe vol
index of each benchmark (VIX, VXN, RVX), which reads about 0.8x the GS level
(VIX trades ~1.23x 1M ATM IV) but reproduces the GS ranking: SPX 2026-11-05 is
#1 since 2007-11-08, NDX #2 since 2009-09-15. Operator choice 2026-11-09:
VIX-family divisor, all three indexes.

- **slug**: `msr` (route `/regime/msr`)
- **service**: `msr` (kebab-case everywhere: `scan_snapshots.service`,
  `service_health`, systemd units)
- **Name**: `Msr` (`MsrPanel`, `useMsr`, `web/lib/msr.ts`)
- **Tab label**: `MSR` (rail group `Volatility`, after `straddle`)
- **Migration**: `0111_msr.sql` (version 111)

## Signal definition

For index `X` with vol index `V` (SPX↔VIX, NDX↔VXN, RUT↔RVX) and each
session `t` with prior common session `t-1` (intersection of the X and V
date sets, sorted):

```
move_pct_t = (X_t / X_{t-1} - 1) * 100            (signed, simple return)
msr_t      = |move_pct_t| / V_{t-1}               (V in vol points, prior close)
```

Only sessions with `t >= START_DATE = "2007-11-08"` are emitted (the GS chart
start). The first emitted row needs a prior common session, which may be
before START_DATE. Depth is bounded by the vol index: VXN starts 2009-09-14,
RVX 2009-09-16, so NDX/RUT series begin 2009-09-15/2009-09-17.

Per index, over all emitted rows:

- `rank(v)` = 1 + count of rows with `msr > v` (1 = largest; ties share rank).
- `stats`: `max`, `max_date`, `p50`, `p90`, `p99` (linear-interpolated
  percentiles, numpy default `(n-1)*q`), `count`.
- `week_max`: the max-msr row among the trailing **5** emitted sessions,
  with its `rank`.
- `top`: the 10 largest rows `[{date, msr, move_pct}]`, msr desc, date desc
  on ties.

Tone (strict inequalities, pinned): `msr > p99` → `var(--negative)`
(extreme surprise: vol badly underpriced the move); `msr > p90` →
`var(--warning)`; otherwise and null → `var(--text-muted)`.

## Source facts (research 2026-11-08)

| Leg | Source | Columns | Depth |
|---|---|---|---|
| SPX close | Cboe CDN `https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv` | `DATE,SPX` | 1975-01-02 |
| VIX | `.../VIX_History.csv` | `DATE,OPEN,HIGH,LOW,CLOSE` | 1990-01-02 |
| VXN | `.../VXN_History.csv` | OHLC | 2009-09-14 |
| RVX | `.../RVX_History.csv` | OHLC | 2009-09-16 |
| RUT close | IB → Cboe `.../RUT_History.csv` (2020-11-27+) → Yahoo `^RUT` | `DATE,RUT` | IB depth discovered at runtime |
| NDX close | IB → Yahoo `^NDX` (Cboe CDN returns **403** for NDX) | | IB depth discovered at runtime |

- Dates `MM/DD/YYYY`. The CDN now 307-redirects to `cdn-api.cboe.com`;
  `requests` follows it. Weak ETags; `If-Modified-Since` → 304 works through
  the redirect.
- Cadence: session rows land after the close; observed Last-Modified on the
  Fri 2026-11-06 rows: VXN/RVX 17:31 ET, SPX 21:11 ET, VIX 21:21 ET, RUT
  21:31 ET. Last-Modified is re-touched intraday without new rows (straddle
  precedent), so 304 = bytes unchanged.
- Licensing: freely served Cboe CDN files (same as straddle/cor/vixts);
  internal single-operator storage and display OK; never publicly re-serve
  raw series.
- Priority chain: VIX/VXN/RVX are Cboe-calculated indexes; Cboe official
  feeds are the documented source (CLAUDE.md "official VIX/VVIX daily
  closes"). SPX close follows the straddle precedent (Cboe official, deep, no
  gateway). NDX/RUT closes follow `scripts/rv_ratio_scan.py`: IB first every
  cycle (skip only when FastAPI `/health` `auth_state` is set and not
  `authenticated`); Robinhood and UW are skipped for index symbols (they do
  not serve indexes, `rv_ratio_scan.is_index_symbol`); Cboe CDN (RUT only);
  Yahoo last resort.
- Fixtures (full real files captured 2026-11-08):
  `scripts/tests/fixtures/msr_{spx,vix,vxn,rvx,rut}_sample.csv`.

## Ingestion: `scripts/fetch_msr.py`

Reuse `scripts/clients/cboe_client.py` (`CboeClient.fetch_history(symbol,
if_modified_since)`), extending it only if a symbol needs it. Composed-method
pure functions (all pytest-covered without network):

- `parse_close_csv(text, column) -> dict[str, float]` (`YYYY-MM-DD` keys;
  column `"SPX"`/`"RUT"` for index files, `"CLOSE"` for vol files; skips
  header/malformed/non-positive rows).
- `compute_msr_rows(closes, vols, start=START_DATE) -> list[{date, close,
  iv_prior, move_pct, msr}]` ascending, per the definition.
- `rank_of(value, values) -> int`, `percentile(values, q) -> float`.
- `summarize_index(rows) -> {current, stats, week_max, top}` (`current` = last
  row incl. its `rank`; `None` fields when rows empty).
- `merge_series(by_index) -> list[{date, spx, ndx, rut}]` (union of dates,
  ascending, msr rounded to 6 dp, `null` where an index has no row).
- `build_payload(...)` → contract below.

Index-close history for NDX/RUT lives in the shared Turso
`price_history_daily` table (`symbol`, `date`, `close`, `source`) via the
existing `writer.upsert_price_history_rows`; stored rows win (splice rule as
rv-ratio). Per run, for each of NDX/RUT:

1. Read stored closes.
2. If stored is empty or its earliest date is after `INDEX_DEPTH_TARGET =
   "2009-09-11"`: deep seed. IB `durationStr="20 Y"` daily TRADES bars
   (bounded `asyncio.wait_for`, 90s), then Cboe (RUT), then Yahoo for any
   dates still missing before the earliest stored date. Each tier stores only
   new completed-session dates with its own `source` label.
3. Otherwise incremental when the latest stored date is before
   `last_completed_session_date()`: IB `"1 Y"` → Cboe (RUT) → Yahoo
   (2 years), first non-empty wins.
4. Never store a bar dated after `last_completed_session_date()` (in-progress
   price, not a close).

IB: `IBClient().connect(client_id="auto", timeout=10)` like
`fetch_iv_spread.py` (20-49 auto range; no fixed scanner id), contract
`Index("NDX","NASDAQ")` / `Index("RUT","RUSSELL")`, every await bounded,
auth pre-check via `/health`. Yahoo/IB/Cboe fetchers are module-level functions
so tests monkeypatch them.

Conditional GET: per-file `Last-Modified` cached in the payload
`source_last_modified`. When all four Cboe files return 304 AND NDX/RUT stores
had nothing new, reuse the previous payload with a fresh `scan_time` and
`rows_changed=False` (heartbeat only). Empty computed SPX series → raise
(never cache empty). An index whose closes are unavailable is emitted with
`current: null` and an empty `top`, and the run still succeeds.

Persistence per cycle: `ensure_no_replica_for_writers()` →
`upsert_msr_rows(rows, recorded_at)` (only when changed) →
`upsert_scan_snapshot("msr", scan_time, payload)` →
`record_service_health("msr", "ok", finished_at=scan_time)` → atomic JSON
`data/msr.json` (Python readers only). CLI `--json` → payload on stdout;
progress on stderr.

## Payload contract

```jsonc
{
  "scan_time": "2026-11-09T03:00:00Z",
  "start_date": "2007-11-08",
  "source_last_modified": { "spx": "...GMT", "vix": "...", "vxn": "...", "rvx": "..." },
  "close_sources": { "SPX": ["cboe"], "NDX": ["ib"], "RUT": ["ib", "yahoo"] },
  "indexes": {
    "SPX": {
      "iv_symbol": "VIX",
      "current": { "date": "2026-11-06", "msr": 0.011162, "move_pct": 0.314534,
                   "close": 7376.86, "iv_prior": 28.18, "rank": 3736 },
      "week_max": { "date": "2026-11-05", "msr": 0.262231, "move_pct": -4.355658,
                    "iv_prior": 16.61, "rank": 1 },
      "stats": { "count": 4779, "max": 0.262231, "max_date": "2026-11-05",
                 "p50": 0.029424, "p90": 0.081531, "p99": 0.149252 },
      "top": [ { "date": "2026-11-05", "msr": 0.262231, "move_pct": -4.355658 }, ... ]
    },
    "NDX": { "iv_symbol": "VXN", ... },
    "RUT": { "iv_symbol": "RVX", ... }
  },
  "series": [ { "date": "2007-11-08", "spx": 0.002175, "ndx": null, "rut": null }, ... ]
}
```

Numbers above are fixture-derived (SPX from the Cboe fixtures). msr / move /
percentiles rounded to 6 dp.

## Storage: `0111_msr.sql`

```sql
CREATE TABLE IF NOT EXISTS msr_history (
  index_symbol TEXT NOT NULL,      -- 'SPX' | 'NDX' | 'RUT'
  date         TEXT NOT NULL,      -- YYYY-MM-DD session t
  close        REAL NOT NULL,
  iv_prior     REAL NOT NULL,      -- vol index close at t-1
  move_pct     REAL NOT NULL,
  msr          REAL NOT NULL,
  recorded_at  TEXT NOT NULL,
  PRIMARY KEY (index_symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_msr_history_date ON msr_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (111, datetime('now'));
```

Writer `scripts/db/writer.py`: `MSR_UPSERT_SQL` (`ON CONFLICT(index_symbol,
date) DO UPDATE`) + `upsert_msr_rows(index_symbol, rows, recorded_at)`,
chunked multi-row inserts (~400 rows) per the Hrana rules (exemplar
`upsert_price_history_rows`).

## API: `web/app/api/msr/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, `radonCapability = "read"`,
  GET only. Static loader in `web/lib/assistant/nextLoaders.ts`.
- `dbFirstRead` on `scan_snapshots WHERE service = 'msr'` latest (copy the
  straddle route exactly; no disk read).
- `MAX_AGE_MS`: `getFreshnessWindowMs("msr", "closed")` from the shared
  catalog (26h, daily timer incl. weekends).
- Missing contract, HTTP 200 exactly:
  `{ missing: true, scan_time: null, start_date: null, indexes: null, series: [] }`
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["msr"] })`.
- Hook `web/lib/useMsr.ts`: `useSyncHook({ endpoint: "/api/msr", interval: 60 * 60_000,
  hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI: `web/components/MsrPanel.tsx` (+ `web/lib/msr.ts`)

Pure helpers in `web/lib/msr.ts`:

- Types `MsrIndex = "SPX" | "NDX" | "RUT"`, `MsrRow`, `MsrIndexSummary`, `MsrData`.
- `MSR_INDEXES = ["SPX", "NDX", "RUT"] as const`.
- `formatMsr(v)` → 3 decimals `0.262`, `---` for null/non-finite.
- `formatRank(rank, count)` → `#1 of 4,779`, `---` when either missing.
- `msrTone(v, stats)` → strict `> p99` `var(--negative)`, `> p90`
  `var(--warning)`, else `var(--text-muted)` (null stats → muted).
- `formatMovePct(v)` → signed 2 dp `-4.36%`, `---`.
- `buildMsrChartRows(series, index)` → `[{ date, msr }]` for that index,
  rows where it is null dropped.

Panel:

- Gate order: `SpectralLoader label="Loading Cboe MSR series"` while
  `(loading || syncing) && !data` → `SectionEmptyState` on `missing` (title
  `No MSR data yet`) → content.
- Index chips `SPX` / `NDX` / `RUT` (`data-testid="msr-index-<SYM>"`,
  `aria-pressed`), default `SPX`.
- Strip (`RegimeStrip` desktop / `MetricCell` mobile) for the selected index:
  LAST MSR (`msr-last-value`, toned), RANK (`#n of count`), 5D MAX (value +
  `#rank`, `msr-week-max`), MAX (value + date), P90, P99, MOVE (current
  `move_pct`), `<IV> PRIOR` (e.g. `VIX PRIOR`), SOURCE UPDATED (vix
  Last-Modified formatted like straddle's `formatSourceDate`).
- `<FreshnessRail schedule={MSR_REFRESH} asOf={current.date}
  testId="msr-freshness-rail" asOfTestId="msr-strip-asof" />` directly under
  the strip.
- Chart: `CriHistoryChart`, title `<SYM> MOVE SURPRISE RATIO`, single series
  MSR (right axis, linear, zero floor); `HistoryRangeChips` + `BrushMinimap`
  (`testIdPrefix="msr-brush"`), default preset `All`.
- Top-10 table below the chart (`SortTh` + `useSort`, columns RANK, DATE,
  MSR, MOVE) for the selected index (`data-testid="msr-top-table"`).
- `InfoTooltip`: "Absolute 1-day % move divided by the prior close's 1-month
  implied vol index (VIX for SPX, VXN for NDX, RVX for RUT). Readings above
  the 99th percentile mean the vol market badly underpriced the move. Uses the
  Cboe vol index, which runs about 1.2x at-the-money implied vol, so levels sit
  below the Goldman series while ranks match." (no em dashes)
- No hardcoded cadence copy; brand tokens only; 4px max radius.

## Registration (lockstep pins)

- `web/lib/regimeRail.ts`: `RegimeTab` += `"msr"`, Volatility group after
  `"straddle"`, `REGIME_TAB_LABEL.msr = "MSR"`.
- `web/components/RegimePanel.tsx`: `tabFromPathname` regex, mobile chip
  array, dispatch branch `activeTab === "msr"` → `<MsrPanel />`.
- `web/app/regime/msr/page.tsx`: copy `app/regime/straddle/page.tsx`.
- `web/tests/regime-tab-routes.test.tsx`: `["msr", "app/regime/msr/page.tsx"]` + render case.
- `web/lib/serviceHealthWindows.ts`: `"msr": { open: 26h, extended: 26h,
  closed: 26h, category: "scheduled", requires_ib: false }` (IB is attempted
  but optional: Cboe/Yahoo fallbacks keep it writing);
  `web/tests/service-health-windows.test.ts` exhaustive set += `msr`.
- `scripts/watchdog/services.py`: `"msr": {"open": 26h, "closed": 26h,
  "requires_ib": False}` + daily-bucket list entry.
- `web/lib/refreshSchedule.ts`: `export const MSR_REFRESH: RefreshSchedule =
  [daily(3, 0)];` + `web/tests/refresh-schedule.test.ts` case.
- `cloud/services/radon-msr.service`: oneshot, `User=radon`,
  `WorkingDirectory=/home/radon/radon`, `EnvironmentFile=/etc/radon/env`,
  `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_msr.py`,
  `TimeoutStartSec=900` (first run seeds ~17y of NDX/RUT closes).
- `cloud/services/radon-msr.timer`: `OnCalendar=*-*-* 03:00:00 UTC`
  (Cboe's last row, RUT, lands ~21:31 ET = 02:31 UTC under EST; 03:00 clears
  both DST regimes, sits in the free slot between 02:50 and 03:10, and is well
  before IB's ~23:45 ET reset), `Persistent=true`, `RandomizedDelaySec=300`.
  Runs daily incl. weekends (304 heartbeats keep service_health fresh).
- `cloud/scripts/setup-vps.sh` `SERVICE_FILES` += both units;
  `cloud/config/installed-units.sha256` += both hashes;
  `cloud/tests/test_systemd_services.py` canonical set += both.

## Test evidence pins (fixture-derived, Cboe files captured 2026-11-08)

SPX (`msr_spx_sample.csv` + `msr_vix_sample.csv`, start 2007-11-08):

- count 4779; first row 2007-11-08 msr 0.002175; last 2026-11-06 msr
  0.011162 (move +0.314534%, close 7376.86, iv_prior 28.18), rank 3736.
- top5: 2026-11-05 0.262231, 2008-09-29 0.253577, 2018-02-05 0.236737,
  2025-04-03 0.224991, 2020-06-11 0.213798.
- p50 0.029424, p90 0.081531, p99 0.149252, max 0.262231 (2026-11-05).
- week_max 2026-11-05 0.262231 rank 1 (move -4.355658%, iv_prior 16.61).

RUT from the Cboe RUT + RVX fixtures only (closes from 2020-11-27):

- count 1492; first 2020-11-30 0.066147; last 2026-11-06 0.030719, rank 835.
- top3: 2023-11-14 0.263688, 2025-04-03 0.258181, 2026-11-05 0.225762.
- week_max 2026-11-05 0.225762 rank 3.

NDX has no Cboe fixture; its tests use stubbed IB/Yahoo closes.

## Freshness rail

`MSR_REFRESH = [daily(3, 0)]` mirrors `radon-msr.timer`; `asOf` =
`indexes[selected].current.date`; test ids `msr-freshness-rail`,
`msr-strip-asof`.
