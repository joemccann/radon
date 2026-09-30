# RSI OVERSOLD — SPX percent of members with Wilder RSI(14) below 30

Route `/regime/rsi-oversold` · service `rsi-oversold` · component `RsiOversoldPanel` · tab label `RSI OVERSOLD` · mobile chip `RSI < 30` · migration `0092`

## Signal

For each current S&P 500 constituent, compute 14-day Wilder RSI on its own
daily closes. Per session:

```
pct_below_30 = 100 * count(rsi14 < 30) / eligible
```

`eligible` is members that already have an RSI value on or before that
session (carry-forward across missing member days, same as
`aggregate_ma_ratio` / `aggregate_bpi`). Strict inequality: `rsi14 == 30`
is not oversold. The first RSI value appears after 15 closes.

Wilder smoothing (ASSUMPTION; the vendor does not document it): seed =
simple mean of the first 14 gains/losses, then
`avg = (prev * 13 + x) / 14`. `avg_loss == 0` gives RSI 100, or 50 when
both averages are 0. Do not reuse `scripts/vol_skew_mr_scanner.py:rsi()`
(Cutler, a simple 14-change average).

Threshold `THRESHOLD_PCT = 10.0` (ASSUMPTION: the WallStreetCourier chart
does not print the line; 10% is that vendor's stated level for this
series family). State is a level condition only:

| pct_below_30 | state |
|---|---|
| > 10 | `OVERSOLD CLUSTER` |
| <= 10 | `NORMAL` |

`cross_up` is previous session `<= 10` and latest `> 10`. `highest_since`
is the most recent prior session whose value is `>=` the latest (null if
none). No duration or cluster rule. This is a regime description of
short-term oversold breadth. No forward-information or buy claim is made
anywhere in copy. The threshold line is labeled "10% oversold cluster
threshold", never "Bearish".

**Universe / survivorship (ASSUMPTION):** current S&P 500 membership from
`resolve_constituents("SPX")` is applied to all history. Point-in-time
membership is not reconstructed.

**Closes:** split-adjusted, dividend-unadjusted `price_history_daily`
closes. The difference from total-return closes is negligible for
RSI(14) except on large special-dividend days.

## Sources

1. **Constituents**: `scripts/clients/index_constituents.py`
   `resolve_constituents("SPX")` cache/seed chain (the same resolver
   ma-ratio / bpi-scan use; never fails, `MIN_PLAUSIBLE_COUNTS["SPX"] = 400`
   floor).
2. **Member daily closes**: the shared Turso `price_history_daily` store,
   maintained by `scripts/bpi_scan.py:ensure_member_history`. Same sanctioned
   Yahoo bulk-deviation as ma-ratio. No new fetcher.
3. **SPX overlay**: `ma_ratio_scan.fetch_spx_overlay_closes` (imported, not
   copied). IB-first for the single-symbol overlay, then the sweep's `^GSPC`
   bars.

Licensing: constituent lists are uncopyrightable factual data; Yahoo chart
closes follow the existing repo-sanctioned bulk-deviation precedent.

## Ingestion — `scripts/rsi_oversold_scan.py`

Composed-method style, stdlib-only computation. Pure functions:
`rsi_series(closes_by_date)`, `aggregate_rsi_oversold(member_series, sessions, member_count)`,
`classify_state`, `detect_cross_up`, `highest_since`, `build_output(...)`.
`attach_spx_series` is reused from `ma_ratio_scan`.

- Members and `^GSPC` sweep through `bpi_scan.ensure_member_history` with an
  rsi-oversold-owned wall-clock deadline. `SWEEP_BUDGET_S = 1500`: same
  SPX-only universe as ma-ratio. Nesting pinned in pytest.
- Session gating mirrors bpi R-224: the latest aggregated session must be
  reported fresh by >= 80% of constituents or the run emits a
  `missing: true` payload and persists nothing.
- A session row exists only when `eligible >= 0.8 * member_count`.
- `install_sigterm_unwind()` (shared with bpi).
- Writes, in order, every cycle: `ensure_no_replica_for_writers()` →
  `upsert_rsi_oversold_rows(rows, recorded_at=scan_time)` →
  `upsert_scan_snapshot("rsi-oversold", scan_time, payload)` →
  `record_service_health("rsi-oversold", "ok", finished_at=scan_time)` →
  atomic JSON fallback `data/rsi_oversold.json`.
- CLI: `--json`, `--no-db`, `--backfill` (2y Yahoo range).

## Storage — `scripts/db/migrations/0092_rsi_oversold.sql`

The series is fully derived from `price_history_daily`, but the history
table is kept so the chart survives snapshot rotation the same way
ma-ratio does. `scan_snapshots` still holds the latest payload.

```sql
CREATE TABLE IF NOT EXISTS rsi_oversold_history (
    date TEXT PRIMARY KEY,
    pct_below_30 REAL NOT NULL,
    count_below_30 INTEGER NOT NULL,
    eligible INTEGER NOT NULL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rsi_oversold_history_date_desc ON rsi_oversold_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (92, datetime('now'));
```

`spx_close` is nullable (optional overlay). Writer
`scripts/db/writer.py:upsert_rsi_oversold_rows(rows, recorded_at)` —
chunked multi-row `INSERT ... ON CONFLICT(date) DO UPDATE`.

## Payload (scan_snapshots service `rsi-oversold`)

```json
{
  "schema_version": 1,
  "scan_time": "2026-09-29T23:05:11+00:00",
  "data_date": "2026-09-28",
  "source": {"constituents": "cache", "constituents_count": 503,
             "member_close_fetches": {"yahoo": 490, "stored": 13}},
  "threshold": 10.0,
  "current": {
    "date": "2026-09-28",
    "pct_below_30": 12.4, "count_below_30": 62, "eligible": 500,
    "spx_close": 6630.0,
    "state": "OVERSOLD CLUSTER",
    "cross_up": false,
    "highest_since": "2026-03-13"
  },
  "series": [
    {"date": "2025-01-02", "pct_below_30": 2.1,
     "count_below_30": 10, "eligible": 490, "spx_close": 5868.55}
  ],
  "missing": false
}
```

`series` is ascending by date.

## API — `web/app/api/rsi-oversold/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only,
  `radonCapability = "read"`, static loader entry in
  `web/lib/assistant/nextLoaders.ts`.
- `dbFirstRead`: `fromDb` = latest `scan_snapshots WHERE service = 'rsi-oversold'`,
  `fromDisk` = `data/rsi_oversold.json`.
- `MAX_AGE_MS = 48h` — daily 23:05 UTC timer with slack. Missing contract:
  HTTP 200 + frozen
  `{ missing: true, scan_time: null, data_date: null, current: null, series: [], threshold: null }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["rsi-oversold"] })`.
- `web/lib/rsiOversold.ts`: types + pure helpers
  (`rsiOversoldStateLabel`, `rsiOversoldCrossUp`, formatters,
  `RSI_OVERSOLD_THRESHOLD = 10`).
- `web/lib/useRsiOversold.ts`: `useSyncHook({ endpoint: "/api/rsi-oversold",
  interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI — `web/components/RsiOversoldPanel.tsx`, `web/app/regime/rsi-oversold/page.tsx`

- Gate order: `SpectralLoader`
  (`label="Loading SPX RSI oversold breadth series"`)
  while `(loading || syncing) && !data` → `SectionEmptyState` on
  `missing: true` → content.
- Strip: `PCT < 30`, `STATE`, `MEMBERS`, `HIGHEST SINCE`, `SPX CLOSE`.
- **FreshnessRail**: `<FreshnessRail schedule={RSI_OVERSOLD_REFRESH}
  asOf={data.data_date ?? current.date} testId="rsi-oversold-freshness-rail"
  asOfTestId="rsi-oversold-strip-asof" />`.
  `RSI_OVERSOLD_REFRESH` mirrors daily 23:05 UTC.
- Chart: `CriHistoryChart`, SPX overlay on the LEFT axis (log),
  `pct_below_30` on the RIGHT axis, the 10% threshold as a right-axis
  `referenceBands` line (`from = to = 10`). Title
  `SPX PCT OF MEMBERS WITH RSI(14) BELOW 30`.
- **No em dashes. No cadence claims** except copy naming the real timer
  ("the rsi-oversold refresh timer"). No forward-return or buy claim.

## Timer — `cloud/services/radon-rsi-oversold.{service,timer}`

- Service: same oneshot shape as ma-ratio, `TimeoutStartSec=2100`,
  `ExecStart=.../scripts/rsi_oversold_scan.py`.
- Timer: `OnCalendar=*-*-* 23:05:00 UTC` — after ma-ratio's 22:45 sweep so
  members are already fresh. 23:05 was unused (nearest neighbors: ma-ratio
  22:45, dispersion's second pass 23:20, bpi 23:30). `Persistent=true`,
  `RandomizedDelaySec=300`. Weekend/holiday runs are unchanged-data
  heartbeats (ivrank convention).
- Register: `setup-vps.sh` `SERVICE_FILES` + `installed-units.sha256` +
  `cloud/tests/test_systemd_services.py`.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`:
  `rsi-oversold`, uniform 26h, `scheduled`, `requires_ib: false`.

## Tests

- `scripts/tests/test_rsi_oversold.py` — reuses
  `scripts/tests/fixtures/ma_ratio_member_closes_sample.json`. Expectations
  from an independent naive Wilder RSI inside the test.
- `web/tests/rsi-oversold-api.test.ts`, `web/tests/rsi-oversold-panel.test.tsx`.
- Lockstep pins: `regime-tab-routes.test.tsx`,
  `service-health-windows.test.ts`, `refresh-schedule.test.ts`,
  `cloud/tests/test_systemd_services.py`.
- `web/e2e/rsi-oversold-tab.spec.ts`.

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=rsi_oversold`,
`<slug>=rsi-oversold`, `<Name>=RsiOversold`, service `rsi-oversold`.
Primary reference: MA RATIO.
