# HHLEV — US Household Leverage (percent of net worth)

Route `/regime/hhlev` · service `hhlev` · component `HhLevPanel` · tab label `HH LEV` · migration `0057`

## Signal

Federal Reserve Z.1 Financial Accounts, household + nonprofit sector (B.101 family):

```
leverage_pct = 100 * TLBSHNO / TNWBSHNO
```

where `TLBSHNO` = Total Liabilities, Level and `TNWBSHNO` = Net Worth, Level (both
$ millions, quarterly, end-of-period, not seasonally adjusted). Quarterly since
1945Q4 (continuous from 1951Q4). Anchors verified from the captured fixture:
1960Q1 10.26, 2009Q1 series max 24.26, 2026Q1 (latest) 11.78 — reproducing the
J.P. Morgan chart exactly. Low leverage = deleveraged household sector (balance
sheet capacity, secular tailwind); high leverage marked the 2008-09 credit bust.

Regime label thresholds (pinned in the panel test with boundary cases; half-open
ranges):

| leverage_pct | label |
|---|---|
| < 12 | `DELEVERAGED` |
| >= 12 and < 16 | `MODERATE` |
| >= 16 and < 20 | `ELEVATED` |
| >= 20 | `STRETCHED` |

## Source (verdicts from the Step 1 research run, 2026-08-23)

- **Primary**: `GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=TLBSHNO,TNWBSHNO`
  — keyless, both series in one ~8.5 KB CSV (323 lines, 304 populated quarters).
  **UA quirk (mandatory)**: `fred.stlouisfed.org`'s edge TCP-resets the bare UA
  `radon/2.0`; use **`radon/2.0 (+https://radon.run)`** (verified 200). Honest bot
  UA, no browser impersonation.
- **Fallback**: keyed FRED API, exact repo precedent `scripts/clients/fred_client.py`
  (`FRED_API_KEY` already in env):
  `GET https://api.stlouisfed.org/fred/series/observations?series_id={TLBSHNO|TNWBSHNO}&api_key=$FRED_API_KEY&file_type=json`.
- **CSV schema**: header `observation_date,TLBSHNO,TNWBSHNO`; dates are
  quarter-START (`2026-01-01` = 2026Q1, value as of quarter end); missing sentinel
  is an EMPTY field in fredgraph CSVs and `"."` in the keyed API — the parser skips
  both. Empties occur only 1946Q1-1951Q3 (annual-only early data).
- **Revisions**: Z.1 revises full history each release → **re-upsert the entire
  series every run** (one small CSV, chunked Hrana-bounded multi-row upsert).
- **Cadence**: quarterly releases Mar/Jun/Sep/Dec with ~10-week lag; next release
  2026-09-10 (FRED release id 52 schedule); series last updated 2026-06-11. Daily
  cheap check + heartbeat is the margin-debt convention (a monthly/quarterly-lag
  series with a daily writer).
- **Licensing**: Board of Governors Z.1 is a US government work (public domain);
  fredgraph.csv is FRED's own public no-key download route; fallback is the
  sanctioned keyed API. Display attribution: "Source: Board of Governors via FRED".
- **Data-source priority**: IB/UW/Yahoo carry no Z.1 sector aggregates — rule
  inapplicable, FRED/Fed is the only machine source. NOT a duplicate of margin-debt
  (FINRA broker margin debits, monthly, different sector and meaning).

## Ingestion — `scripts/fetch_hhlev.py`

Composed-method, stdlib csv/urllib (or the repo's http helper). Pure functions:
`parse_fredgraph_csv(text)` (skip empty and "." rows, require both fields),
`compute_leverage(liabilities, net_worth)`, `build_series(rows)` (ascending),
`build_output(...)`, `persist_result(payload, rows)`. Fallback path
`fetch_via_api()` reusing `fred_client` conventions when the CSV transport fails.

- Every run: fetch CSV → parse → **guard** (fewer than 250 populated quarters, or a
  latest-quarter ratio outside 2..40, raises RuntimeError — retryable, no ok-latch,
  never persist) → re-upsert ALL rows (revisions) → snapshot → heartbeat → atomic
  JSON fallback `data/hhlev.json`. Turso is the source of truth.
- `source_last_modified` = the FRED series `last_updated` when using the API, else
  the latest observation date; carried in the payload for the SOURCE UPDATED cell.
- CLI: `--json` prints payload (stdout = payload only, progress to stderr). No
  separate `--backfill`: the normal run always writes full history.

## Storage — `scripts/db/migrations/0057_hhlev.sql`

```sql
CREATE TABLE IF NOT EXISTS hhlev_history (
    date TEXT PRIMARY KEY,
    leverage_pct REAL NOT NULL,
    liabilities_musd REAL NOT NULL,
    net_worth_musd REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hhlev_history_date_desc ON hhlev_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (57, datetime('now'));
```

Writer: `HHLEV_UPSERT_SQL` (5 positional params: date, leverage_pct,
liabilities_musd, net_worth_musd, recorded_at) + `upsert_hhlev_rows(rows,
recorded_at)`, idempotent `ON CONFLICT(date) DO UPDATE`, chunked.

## Payload (scan_snapshots service `hhlev`)

```json
{
  "scan_time": "2026-08-25T13:25:00+00:00",
  "source_last_modified": "2026-06-11",
  "data_date": "2026-01-01",
  "current": {
    "date": "2026-01-01", "leverage_pct": 11.78,
    "liabilities_musd": 21560050, "net_worth_musd": 182979889
  },
  "series": [
    {"date": "1945-10-01", "leverage_pct": 3.79}
  ]
}
```

`series` ascending, leverage only (components live in `current` and Turso).

## API — `web/app/api/hhlev/route.ts`

- GET only, `dynamic = "force-dynamic"`, `runtime = "nodejs"`.
- `dbFirstRead`: `fromDb` latest `scan_snapshots WHERE service = 'hhlev'`,
  `fromDisk` `data/hhlev.json`.
- `MAX_AGE_MS = 48h` with comment: quarterly SOURCE, but a DAILY writer heartbeat —
  older than 48h means the writer is down, not the data stale.
- Missing contract: HTTP 200 + frozen
  `{ missing: true, scan_time: null, source_last_modified: null, data_date: null, current: null, series: [] }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["hhlev"] })`.
- Add `"hhlev"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES` in
  `web/tests/route-local-authz-matrix.test.ts` (read-only market data), same commit.
- Hook `web/lib/useHhLev.ts`:
  `useSyncHook({ endpoint: "/api/hhlev", interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI — `web/components/HhLevPanel.tsx`, `web/app/regime/hhlev/page.tsx`

- Lib `web/lib/hhlev.ts` (UI worktree owns it): types, frozen `MISSING_HHLEV`,
  `hhLevRegimeLabel(pct)` per threshold table, `formatQuarter("2026-01-01")` →
  `"2026 Q1"`, `formatTrillions(musd)` → `"$21.6T"` (from $ millions).
- Gate order: `SpectralLoader` (`label="Loading household leverage series"`) →
  `SectionEmptyState` on `missing: true` → content.
- Strip testids: `hhlev-leverage` (`11.78%`), `hhlev-liab` (`$21.6T`),
  `hhlev-networth` (`$183.0T`), `hhlev-regime` (label), `hhlev-updated`
  (`2026 Q1`). Header clock renders `scan_time`.
- Chart: `CriHistoryChart`, single series `leverage_pct` right axis, title
  `US HOUSEHOLD LEVERAGE PCT OF NET WORTH`, `xTickFormat` for the 80-year
  quarterly domain. `HistoryRangeChips` default `All` + `BrushMinimap`
  `testIdPrefix="hhlev-brush"`.
- `InfoTooltip`: Z.1 B.101 definition, quarterly cadence with roughly a 10 week
  publication lag, end-of-period values keyed to quarter start dates, threshold
  bands, and "Source: Board of Governors via FRED". No em dashes; no cadence claims
  beyond naming the real daily hhlev refresh timer and the quarterly source.
- Registration: `RegimePanel.tsx` (regex, mobile chips, `MOBILE_TAB_LABEL.hhlev =
  "HH LEV"`, dispatch) + `web/lib/regimeRail.ts` (union, group "Positioning" after
  MARGIN, label "HH LEV") + `web/tests/regime-rail.test.tsx` count/list pins +
  `web/tests/regime-tab-routes.test.tsx` row. Tab label `HH LEV`.

## Timer — `cloud/services/radon-hhlev.{service,timer}`

- Service: `Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/etc/radon/env`, `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_hhlev.py`,
  `TimeoutStartSec=300`.
- Timer: `OnCalendar=*-*-* 13:20:00 UTC` (comment: daily cheap conditional check of
  a quarterly source, offset from radon-margin-debt's 13:10 pass), `Persistent=true`,
  `RandomizedDelaySec=300`, `WantedBy=timers.target`.
- Register: `setup-vps.sh` `SERVICE_FILES` + `cloud/config/installed-units.sha256`
  (both hashes, same commit) + `cloud/tests/test_systemd_services.py`.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`: `hhlev`,
  uniform 26h windows cloned from the margin-debt entries, `requires_ib: false`,
  + daily-bucket check list. Data age (a quarter can be ~100+ days old) is
  legitimate and must never be conflated with writer health.

## Tests (write first; red = missing implementation modules)

- Fixtures: `scripts/tests/fixtures/hhlev_sample.csv` (full 323-line fredgraph
  response, 304 populated quarters; anchors 1960-01-01 → 10.26, 2009-01-01 → max
  24.26, 2026-01-01 → 11.78) and `hhlev_api_observations_sample.json` (fallback
  shape, "." sentinels, no API key embedded).
- `scripts/tests/test_hhlev.py` — CSV parse (empty-field skip, both-fields-required,
  304 rows), ratio anchors derived from the fixture, API-fallback parse ("."
  sentinel skip), guard (250-row floor raises; out-of-band ratio raises), full
  re-upsert behavior, write order (guard → rows → snapshot `hhlev` → health ok →
  atomic JSON), migration 0057 pin (schema, version 57, idempotent upsert), writer
  arity, payload contract keys, window-relative freshness.
- `web/tests/hhlev-api.test.ts` — Turso-beats-disk, disk fallback, exact frozen
  missing object at 200, no cross-service leak (a `margin-debt` snapshot must not
  serve), `route.dynamic === "force-dynamic"`.
- `web/tests/hhlev-panel.test.tsx` — loader label, empty state, strip testids +
  formats (`11.78%`, `$21.6T`, `$183.0T`, `2026 Q1`), regime boundaries (11.99
  DELEVERAGED, 12 MODERATE, 15.99 MODERATE, 16 ELEVATED, 19.99 ELEVATED, 20
  STRETCHED), chart title, chips default All, hhlev-brush, NaN guard,
  no-em-dash/no-cadence copy discipline.
- `web/e2e/hhlev-tab.spec.ts` — per pattern (UI worktree owns; run in Step 5).

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=hhlev`, `<slug>=hhlev`,
`<Name>=HhLev`, service `hhlev`. References: `fetch_margin_debt.py` (daily writer
over a slow source + FINRA persist pattern), `fetch_hhlev` siblings
`fetch_divyield.py`/`fetch_hyad.py` (freshest tested surfaces),
`scripts/clients/fred_client.py` (keyed FRED fallback), `divyield/route.ts`,
`DivYieldPanel.tsx`/`HyAdPanel.tsx`. Ownership split mirrors HYAD's: ingestion owns
scripts/cloud; API owns route + serviceHealthWindows (+ pin test) + authz line; UI
owns web/lib/hhlev.ts, useHhLev.ts, panel, registration, page, tab-routes +
regime-rail pins, e2e spec.
