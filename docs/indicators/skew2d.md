# SKEW 2D — Two-Session Change in SPX 1-Month Normalized Put/Call Skew

Two-trading-session change in the constant-maturity 30-day SPX 25-delta
put/call IV ratio. Reference: operator-supplied chart ("Change in SPX Skew" /
"2d Change in 1m SPX Normalized Put Call Skew", Aug 2006 - Oct 2026 style:
high ~0.27, low ~-0.21, avg ~0, last ~0.08, stddev ~0.03 on a multi-decade
vendor series). Radon derives the same construction from the existing SKEW
pipeline (UW greeks, history floor 2023-09-06); live 2d stats over that window
are high 0.375, low -0.255, stddev 0.048 (fixture pin).

- **slug**: `skew2d` (route `/regime/skew2d`)
- **service**: `skew2d` (kebab-case everywhere; no underscore)
- **Name**: `Skew2d` (`Skew2dPanel`, `useSkew2d`)
- **Tab label**: `SKEW 2D`
- **Migration**: `0039_skew2d.sql` (version 39)

## Signal definition

Let `ratio_t` be the SKEW level on session `t` (put_iv_30d / call_iv_30d at
constant 30-day maturity — identical to `skew_history.ratio`):

```
change_2d_t = ratio_t - ratio_{t-2}     # None when fewer than 2 prior sessions
```

- Sessions are trading days already stored in `skew_history` (not calendar days).
- The first two stored sessions have `change = null`.
- Stats (`high`, `low`, `avg`, `stddev`) are population (`statistics.pstdev`)
  over all non-null 2d changes.
- Tone rule (UI): strictly `|change| > 2 * stddev` → `var(--warning)`;
  boundary and inside band stay `var(--text-muted)`.

This is deliberately NOT the 1-session `change` already published by the SKEW
tab. A two-session window catches multi-day skew collapses that a single print
can understate.

## Source facts (research 2026-08-09)

- **Primary input**: Turso `skew_history` (ratios produced by `fetch_skew.py`
  from Unusual Whales `GET /api/stock/SPX/greeks`). No additional HTTP.
- **Fallback input**: `data/skew.json` series when Turso is empty/unreachable
  on a laptop host; never preferred over Turso when both exist.
- **Why not IB / UW direct**: IB has no historical IV-by-delta. UW is already
  consumed by the parent SKEW job; re-fetching the same chains would double
  provider traffic for a pure lag transform.
- **Cadence**: parent SKEW finalizes daily at 21:45 UTC (plus live RTH). This
  job runs at **21:50 UTC** every calendar day so the parent finalize has a
  five-minute head start, and heartbeats on weekends/holidays even when no
  new parent rows appear.
- **History depth**: inherits SKEW (UW floor 2023-09-06, ~730 sessions). The
  operator chart's 2006 start is a different vendor archive; Radon does not
  claim pre-2023 coverage.
- **Licensing**: derived from UW under the existing repo license (same as
  SKEW/GEX). Display of derived series is normal in-app use.
- **Fixture**: `scripts/tests/fixtures/skew2d_ratios_sample.json` — full
  `skew_history` dump captured 2026-08-09 (733 rows: date, expiry, dte,
  put_iv, call_iv, ratio). Expected 2d stats are computed from this file in
  pytest, not mental arithmetic.

## Ingestion — `scripts/fetch_skew2d.py`

Modeled on `fetch_margin_debt.py` (scheduled external) but **network-free**:
rehydrate ratios → compute 2d series → dual-write.

Pure functions:

- `load_ratio_rows(db=None) -> list[dict]` — Turso
  `SELECT date, expiry, dte, put_iv, call_iv, ratio FROM skew_history ORDER BY date`,
  falling back to `data/skew.json` series entries that carry `ratio`.
- `compute_change_2d_series(rows) -> list[dict]` — ascending; each output row
  keeps parent fields and sets `change` to `ratio_t - ratio_{t-2}` or None.
- `compute_stats(series) -> {high, low, avg, stddev} | None` over non-null changes.
- `build_payload(series, *, scan_time, source="skew_history")` — full contract.
- `run(*, now=None)` — load → compute → if zero ratio rows, raise (empty
  guard). Compare to last payload fingerprint (`count` + last date + last
  ratio + last change); on no material change refresh snapshot + heartbeat
  only (`rows_changed=False`). On change, upsert history rows then snapshot.

Persistence order every cycle:

1. `writer.ensure_no_replica_for_writers()`
2. `writer.upsert_skew2d_rows(series, recorded_at=scan_time)` when changed
3. `writer.upsert_scan_snapshot("skew2d", scan_time, payload)` every cycle
4. `writer.record_service_health("skew2d", "ok", finished_at=scan_time)` every cycle
5. Atomic JSON fallback `data/skew2d.json`

CLI: `--json` → payload on stdout; progress on stderr.

Optional live overlay (when parent snapshot has `current.is_intraday`):
append/replace a provisional current with `is_intraday:true` and
`change = current.ratio - ratio_{t-2}` from the durable base. Provisional
rows are excluded from `skew2d_history` and from stats.

## Payload contract

```jsonc
{
  "scan_time": "2026-08-09T21:50:00Z",
  "source": "skew_history",
  "market_status": "closed",
  "count": 733,
  "current": {
    "date": "2026-08-07",
    "ratio": 1.23838134631528,
    "change": -0.0036750737861990235,
    "put_iv": 0.14,
    "call_iv": 0.11,
    "expiry": "2026-08-21",
    "dte": 14
  },
  "stats": {
    "high": 0.37540121157999673,
    "low": -0.2550619076592615,
    "avg": -9.866255423597131e-05,
    "stddev": 0.04798679737838151
  },
  "series": [
    { "date": "2023-09-06", "ratio": 1.298..., "change": null },
    { "date": "2023-09-07", "ratio": ..., "change": null },
    { "date": "2023-09-08", "ratio": ..., "change": <ratio_t - ratio_{t-2}> },
    ...
  ]
}
```

Missing contract (HTTP 200 exactly):
`{ missing: true, scan_time: null, count: 0, series: [], current: null, stats: null }`

## Storage — `0039_skew2d.sql`

```sql
CREATE TABLE IF NOT EXISTS skew2d_history (
  date        TEXT PRIMARY KEY,
  expiry      TEXT NOT NULL,
  dte         INTEGER NOT NULL,
  put_iv      REAL NOT NULL,
  call_iv     REAL NOT NULL,
  ratio       REAL NOT NULL,
  change      REAL,              -- NULL on first two stored sessions
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skew2d_history_date ON skew2d_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (39, datetime('now'));
```

Writer: `SKEW2D_UPSERT_SQL` + `upsert_skew2d_rows(series, recorded_at)` chunked
multi-row (same Hrana bounding as SKEW).

## API — `web/app/api/skew2d/route.ts`

Copy the SKEW route: GET only, `dynamic="force-dynamic"`, `runtime="nodejs"`,
`dbFirstRead` (Turso `scan_snapshots service='skew2d'` latest, disk
`data/skew2d.json`), `MAX_AGE_MS = 48h` (daily timer with weekend heartbeats;
older than two days means the writer is down), exact missing contract above,
`setNoStoreResponseHeaders`.

Hook `web/lib/useSkew2d.ts`: GET-only `useSyncHook`, poll hourly when market
open (series is daily; 60s is wasteful), paused when closed.
`extractTimestamp: d => d.scan_time`.

`serviceHealthWindows.ts`: `skew2d`, scheduled, 26h open/extended/closed,
`requires_ib:false`. Python watchdog window matches.

## UI — `web/components/Skew2dPanel.tsx` (+ `web/lib/skew2d.ts`)

Helpers (mirror `skew.ts`, namespaced):

- `formatSkew2dChange`, `formatSkew2dRatio`, `formatIvPct`, `formatZ`,
  `zScore`, `skew2dChangeColor` (strict 2-sigma), `buildSkew2dChartRows`.
- Types `Skew2dEntry`, `Skew2dCurrent`, `Skew2dStats`, `Skew2dData`.

Panel:

- Gate: `SpectralLoader` `label="Loading 2d skew series"` →
  `SectionEmptyState` `headline="No 2d skew data yet"` (secondary may
  reference "the skew2d refresh timer") → content.
- Strip: CHANGE (2d, colored), LEVEL (ratio), Z-SCORE, HIGH, LOW, STDDEV,
  LATEST DATE, TENOR.
- Chart: `CriHistoryChart`, title `2D CHANGE IN 1M SPX PUT/CALL SKEW`, single
  right series; view chips `CHANGE` (default) / `LEVEL`; `HistoryRangeChips`
  + `BrushMinimap` (`testIdPrefix="skew2d-brush"`); default preset
  `All` for the multi-year series via `defaultPresetForLength`.
- `InfoTooltip`: "Two-session change in the SPX 25-delta put/call IV ratio at
  a constant 30-day maturity. Derived from the SKEW history table (Unusual
  Whales greeks). Sharp multi-day drops mean put skew collapsing or call
  demand spiking across sessions; beyond 2 sigma is a tail repricing event."
  (no em dashes)
- Freshness: header clock = `scan_time`; no cadence claim other than the
  empty-state timer reference. Do not write "Refreshes daily" as a hard-coded
  marketing string — the timer is `OnCalendar=*-*-* 21:50:00 UTC`.
- Registration: RegimePanel all places (union, values, regex, desktop row,
  mobile chips, dispatch), label `SKEW 2D`; `web/app/regime/skew2d/page.tsx`;
  `regime-tab-routes` table; e2e `web/e2e/skew2d-tab.spec.ts`.

## Scheduling

`cloud/services/radon-skew2d.{service,timer}`: oneshot venv-python direct,
`EnvironmentFile=/home/radon/radon-cloud/.env`, `RADON_DB_NO_REPLICA=1`,
`TimeoutStartSec=120`;
`OnCalendar=*-*-* 21:50:00 UTC` (five minutes after parent SKEW finalize),
`Persistent=true`, `RandomizedDelaySec=30`. Register in `setup-vps.sh`
`SERVICE_FILES` + `cloud/tests/test_systemd_services.py`.

## File checklist

Create:

- `scripts/fetch_skew2d.py`
- `scripts/db/migrations/0039_skew2d.sql`
- `scripts/tests/test_skew2d.py`
- `scripts/tests/fixtures/skew2d_ratios_sample.json`
- `web/app/api/skew2d/route.ts`
- `web/lib/skew2d.ts`
- `web/lib/useSkew2d.ts`
- `web/components/Skew2dPanel.tsx`
- `web/app/regime/skew2d/page.tsx`
- `web/tests/skew2d-api.test.ts`
- `web/tests/skew2d-panel.test.tsx`
- `web/e2e/skew2d-tab.spec.ts`
- `cloud/services/radon-skew2d.service`
- `cloud/services/radon-skew2d.timer`
- `docs/indicators/skew2d.md`

Modify (lockstep):

- `scripts/db/writer.py` — `upsert_skew2d_rows` + `SKEW2D_UPSERT_SQL`
- `web/components/RegimePanel.tsx` — six places
- `web/tests/regime-tab-routes.test.tsx`
- `web/lib/serviceHealthWindows.ts` + `web/tests/service-health-windows.test.ts`
- `scripts/watchdog/services.py`
- `cloud/scripts/setup-vps.sh`
- `cloud/tests/test_systemd_services.py`

## Test evidence pins (fixture-derived 2026-08-09)

From `skew2d_ratios_sample.json` (733 rows):

- `change_2d` count: 731 (first two null)
- high: `0.37540121157999673`
- low: `-0.2550619076592615`
- avg: `-9.866255423597131e-05`
- stddev (population): `0.04798679737838151`
- last date: `2026-08-07`
- last ratio: `1.23838134631528`
- last change_2d: `-0.0036750737861990235`
- Hand series unit: ratios `[1.30, 1.28, 1.25, 1.20, 1.24]` →
  changes `[null, null, -0.05, -0.08, -0.01]`
