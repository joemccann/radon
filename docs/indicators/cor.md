# COR — SPX Implied Correlation (Cboe COR1M/COR3M/COR6M/COR1Y)

Regime tab charting the Cboe SPX implied correlation index family: the
market-implied average pairwise correlation of the top-50 SPX constituents,
backed out from index option prices vs single-stock option prices, per tenor.
Values are index points (percent, 0-100).

**Signal.** Implied correlation is the price of index-level diversification.
Low readings = dispersion regime: index vol cheap relative to single-stock vol,
short-correlation / dispersion positioning crowded. Extreme lows (all tenors at
the ~1st percentile of 20 years as of 2026-08-07) are a fragility marker —
correlation snaps toward 1 in systemic selloffs, so compressed implied
correlation means index hedges are cheap exactly when crowding makes the snap
larger. Spikes >90th percentile mark systemic stress already in the price.

Regime classification (strict inequalities, pinned by tests):

- `percentile < 0.10` → `COMPRESSED` (tone `var(--warning)`)
- `percentile > 0.90` → `STRESS` (tone `var(--negative)`)
- otherwise (boundaries included) → `NEUTRAL` (tone `var(--text-muted)`)

Percentile is computed on the **COR6M** tenor (deepest-signal tenor, matches
the operator's reference chart) as the fraction of all non-null historical
closes strictly below the latest close.

## Source (researched 2026-08-09)

- Primary: Cboe daily-prices CSVs, one per tenor —
  `https://cdn.cboe.com/api/global/us_indices/daily_prices/<SYMBOL>_History.csv`
  for `COR1M`, `COR3M`, `COR6M`, `COR1Y` (COR9M exists but is not ingested;
  COR2Y/COR3Y do not exist → 403). Same CDN + client as STRADDLE
  (`scripts/clients/cboe_client.py`, UA `radon/2.0`, conditional GET → 304).
- Format: `DATE,OPEN,HIGH,LOW,CLOSE`, dates `MM/DD/YYYY`, ascending after sort.
  **Ingest CLOSE only** — pre-2022 rows are close-only backfill
  (OPEN=HIGH=LOW=CLOSE); OHLC is synthetic there.
- Depth: 2006-01-03 → present (~5,181 rows/tenor, ~260KB/file). No ICJ/JCJ/KCJ
  splice needed — Cboe backfilled the COR family itself. COR3M is missing 15
  scattered dates the other tenors have → merge per-tenor by date, null where
  absent.
- Cadence: EOD-settled, file overwritten after each session (Last-Modified
  observed ~18:31 GMT; Cboe also re-touches on weekends without new rows —
  conditional GET heartbeats those cheaply). `cache-control: max-age=900`.
- Licensing: public delayed/historical CDN feeds already consumed by Radon
  (`cri_scan.py` COR1M dashboard JSON, STRADDLE SPX/VIX1D CSVs). Internal
  indicator use OK; never re-serve raw history publicly.
- Priority: IB is unreliable for COR in both channels
  (feedback_ib_cor1m_lags_official_close — bars and tick-9 lag a session);
  Cboe official history is the established anchor. Yahoo `^COR1M` exists but
  is sparse/delayed — fallback only. UW does not serve the series.

## Identifiers

- slug `cor` · service `cor` (kebab-case everywhere) · Name `Cor` · tab label
  `COR` · migration `0039` · timer `radon-cor.timer`.

## Payload (`scan_snapshots.service = 'cor'` + `data/cor.json`)

```json
{
  "scan_time": "2026-08-10T02:20:00Z",
  "source_last_modified": {
    "cor1m": "Fri, 07 Aug 2026 22:31:04 GMT",
    "cor3m": "...", "cor6m": "...", "cor1y": "..."
  },
  "count": 5181,
  "current": {
    "date": "2026-08-07",
    "cor1m": 7.38, "cor3m": 10.48, "cor6m": 12.16, "cor1y": 14.19,
    "term_spread": 6.81,
    "change_1d": { "cor1m": 0.76, "cor3m": 1.07, "cor6m": 0.98, "cor1y": 0.74 }
  },
  "stats": {
    "cor1m": { "high": 96.59, "low": 2.93, "avg": 37.198579, "stddev": 17.867378, "percentile": 0.010037 },
    "cor3m": { "...": "per-tenor, same shape" },
    "cor6m": { "high": 86.35, "low": 9.67, "avg": 44.342579, "stddev": 15.781472, "percentile": 0.0083 },
    "cor1y": { "...": "..." }
  },
  "series": [ { "date": "2006-01-03", "cor1m": 23.5, "cor3m": 31.34, "cor6m": 38.21, "cor1y": 42.83 }, "..." ]
}
```

- `series` is the **union of dates** across tenors, ascending, `null` where a
  tenor is missing that date (COR3M's 15 gaps, e.g. 2006-05-18).
- `term_spread = cor1y − cor1m` (positive = upward-sloping term structure;
  null if either leg null).
- `change_1d` per tenor = latest non-null close minus the **previous non-null**
  close for that tenor (null-skipping, so a COR3M gap does not zero the change).
- `stats.stddev` is population stddev (`statistics.pstdev`), matching STRADDLE.
- `stats.<tenor>` is `null` when the tenor has no non-null values.
- `percentile` = share of that tenor's non-null closes **strictly below** the
  latest close (all-equal history → 0.0).

## Ingestion — `scripts/fetch_cor.py` (copy `fetch_straddle.py`)

- `_TENORS = ("COR1M", "COR3M", "COR6M", "COR1Y")`; payload/stamp keys are the
  lowercase symbol.
- Pure functions pinned by pytest: `parse_index_csv(text)` (CLOSE column,
  malformed rows skipped, ascending ISO dates), `merge_series(by_tenor)`
  (dict of lowercase tenor → parsed rows ⇒ union-of-dates series),
  `compute_stats(series)`, `_build_current(series)`, `run(client=None, now=None)`.
- Conditional GET across all four files via `CboeClient.fetch_history(symbol,
  if_modified_since=...)`; when **all four** return 304 reuse the cached payload
  with a fresh `scan_time`, write snapshot + heartbeat only (`rows_changed=False`),
  log `[cor] all sources unchanged (304); refreshing snapshot only`. Any change
  → refetch missing, rebuild everything.
- Empty guard: zero-row series raises (`persist` never caches empty).
- Writes per cycle (order): `ensure_no_replica_for_writers()` →
  `upsert_cor_rows(series, recorded_at=scan_time)` only when rows changed →
  `upsert_scan_snapshot("cor", scan_time, payload)` →
  `record_service_health("cor", "ok", finished_at=scan_time)` → atomic
  `data/cor.json`. Db mirror is best-effort try/except like STRADDLE.
- CLI: `--json` payload to stdout; summary to stderr.

## Storage — `scripts/db/migrations/0039_cor.sql`

```sql
CREATE TABLE IF NOT EXISTS cor_history (
  date        TEXT PRIMARY KEY,
  cor1m       REAL,
  cor3m       REAL,
  cor6m       REAL,
  cor1y       REAL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cor_history_date ON cor_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (39, datetime('now'));
```

- `scripts/db/writer.py`: `COR_UPSERT_SQL` (single-row, 6 args, `ON
  CONFLICT(date) DO UPDATE` all four tenor columns + recorded_at) +
  `upsert_cor_rows(series, recorded_at)` using **chunked multi-row INSERTs**
  (~400 rows/statement, exemplar `upsert_price_history_rows` — 5,181 rows via
  executemany is a Hrana per-row round-trip storm; never do that).

## API — `web/app/api/cor/route.ts` (copy `straddle/route.ts`)

- GET-only, `dynamic = "force-dynamic"`, `runtime = "nodejs"`.
- `dbFirstRead`: Turso `scan_snapshots WHERE service = 'cor'` latest, disk
  fallback `data/cor.json`.
- `COR_MAX_AGE_MS = 48h` — radon-cor.timer fires daily 02:20 UTC including
  weekends (Cboe re-touch days heartbeat via conditional GET), so a snapshot
  older than two days means the writer is down.
- Missing contract: HTTP 200 + `{ missing: true, scan_time: null, count: 0,
  series: [], current: null, stats: null }`.
- Cache headers: `maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600,
  cacheState: "HIT", tags: ["cor"]`.
- Hook `web/lib/useCor.ts`: `useSyncHook({ endpoint: "/api/cor", interval:
  3_600_000, extractTimestamp: d => d.scan_time })` (hourly poll of a daily
  series; no POST).

## Types + helpers — `web/lib/cor.ts`

- `CorEntry = { date: string; cor1m: number|null; cor3m: number|null;
  cor6m: number|null; cor1y: number|null }`; `CorTenorKey`; `CorTenorStats`;
  `CorData` (payload above, `missing?` variant).
- `TENOR_OPTIONS: { key, label }[]` for `cor1m→1M, cor3m→3M, cor6m→6M,
  cor1y→1Y`.
- `formatCor(v)` → 2-decimal string (`"12.16"`), `"---"` on
  null/undefined/non-finite.
- `formatCorChange(v)` → signed 2-decimal (`"+0.98"`, `"-1.07"`), `"---"` null.
- `formatPercentile(v)` → fraction → 1-decimal percent (`0.0083 → "0.8%"`),
  `"---"` null.
- `corRegime(percentile)` → `"COMPRESSED" | "STRESS" | "NEUTRAL"` per the
  strict thresholds above; null → `"NEUTRAL"`.
- `corRegimeColor(regime)` → tones above.
- `termSpread(current)` → `cor1y − cor1m`, null-safe.
- `latestSourceStamp(stamps)` → newest parseable RFC-1123 stamp of the four
  (unparseable ignored; all missing → null).
- `buildCorChartRows(series, tenor)` → `[{ date, value }]`, nulls preserved
  (NaN guard depends on this).
- `formatSourceDate` is imported from `@/lib/straddle` (already generic).

## UI — `web/components/CorPanel.tsx` (copy `StraddlePanel.tsx`)

- Gate order: `SpectralLoader` label `"Loading Cboe implied correlation
  series"` while `(loading || syncing) && !data` → `SectionEmptyState` on
  `missing: true` with title `"No implied correlation data yet"` (copy may
  reference the radon-cor refresh timer) → content.
- Header strip (`RegimeStrip` desktop / `MetricCell` grid mobile): cells
  `COR 1M`, `COR 3M`, `COR 6M`, `COR 1Y` (each `formatCor` value +
  `formatCorChange` 1d change), `1Y-1M SPREAD` (signed), `6M PCTILE`
  (`formatPercentile`, `data-testid="cor-regime-value"` toned by
  `corRegimeColor` with the regime word rendered), `SOURCE UPDATED`
  (`formatSourceDate(latestSourceStamp(...))`).
- Chart: `CriHistoryChart`, title `"SPX IMPLIED CORRELATION"`, single right
  series = selected tenor. Tenor chips `1M/3M/6M/1Y`, default **6M**.
  `HistoryRangeChips` + `BrushMinimap` (`testIdPrefix="cor-brush"`), default
  preset `All` (multi-decade series).
- Brand tokens only, 4px radius, `InfoTooltip` explaining the signal +
  thresholds, no em dashes in copy, no hardcoded cadence copy (freshness
  renders payload `scan_time` + source stamps only).
- No live/intraday cell: COR is EOD; live COR1M already renders on the CRI
  regime strip.
- Route `web/app/regime/cor/page.tsx`: 5-line `<WorkspaceShell
  section="regime" />` page (copy `regime/straddle/page.tsx`).

## Registration (lockstep pins)

- `RegimePanel.tsx` four places (`RegimeTab` union, `REGIME_TAB_VALUES`,
  `tabFromPathname` regex, desktop button row + mobile chip array) + dispatch
  branch for `"cor"`.
- `web/tests/regime-tab-routes.test.tsx` table + render case.
- `web/lib/serviceHealthWindows.ts`: `"cor": { open: 26h, extended: 26h,
  closed: 26h, category: "scheduled" }` (daily 02:20 UTC timer, every calendar
  day) + `web/tests/service-health-windows.test.ts` exhaustive set.
- `scripts/watchdog/services.py`: same 26h window + daily-bucket list.
- `cloud/services/radon-cor.{service,timer}` + `cloud/scripts/setup-vps.sh`
  `SERVICE_FILES` + `cloud/tests/test_systemd_services.py` canonical set
  (+ `cloud/services/installed-units.sha256` per repo CI contract if present).

## Scheduling

- `radon-cor.service`: `Type=oneshot`, `User=radon`,
  `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/home/radon/radon-cloud/.env`,
  `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_cor.py`
  (venv python direct, never run_*.sh), `TimeoutStartSec=300`.
- `radon-cor.timer`: `OnCalendar=*-*-* 02:20:00 UTC` — after Cboe's EOD
  overwrite (~18:31 ET latest observed) with hours of slack, 5 min after
  radon-straddle to stagger CDN hits; `Persistent=true`,
  `RandomizedDelaySec=120`, `WantedBy=timers.target`.

## Tests

- `scripts/tests/test_cor.py` — fixtures `scripts/tests/fixtures/
  cor_sample_{1m,3m,6m,1y}.csv` (full Cboe files, captured 2026-08-09).
  Ground truth derived from the fixtures 2026-08-09 (not mental arithmetic).
- `web/tests/cor-api.test.ts` — mirrors `straddle-api.test.ts`.
- `web/tests/cor-panel.test.tsx` — mirrors `straddle-panel.test.tsx`.
- `web/e2e/cor-tab.spec.ts` — mirrors `straddle-tab.spec.ts` (route mocks +
  live screenshot pass).
