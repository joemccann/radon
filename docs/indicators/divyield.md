# DIVYIELD — Percent of S&P 500 stocks with dividend yield above the 10-Year Treasury yield

Route `/regime/divyield` · service `div-yield` · component `DivYieldPanel` · tab label `DIV YIELD` · migration `0055`

## Signal

For each current S&P 500 constituent, trailing yield = (sum of all cash dividends over
the trailing 12 months) / last regular-market price, specials included. The indicator is

```
pct_above = 100 * count(trailing_yield > y10) / total_constituents_with_valid_data
```

where `y10` is the 10-Year CMT par yield already maintained in Turso
(`yield_curve_history.y10`, treasury.gov, daily since 1990). Strict inequality; the
boundary (`yield == y10`) does NOT count as above — pinned in tests.

Interpretation (NDR-style, chart mean ~18, +1SD ~34, +2SD ~50, -1SD ~3): low readings
mean Treasuries out-yield nearly the whole index (equities offer no yield support —
2007, mid-2026 ~3.85%); high readings mark yield-abundant regimes (2009, 2016, 2020).

Regime label thresholds (pinned in `web/tests/divyield-panel.test.tsx` with exact
boundary cases; classify with these half-open ranges):

| pct_above | label |
|---|---|
| < 5 | `SCARCE` |
| >= 5 and < 15 | `LOW` |
| >= 15 and < 35 | `NEUTRAL` |
| >= 35 and < 50 | `ELEVATED` |
| >= 50 | `EXTREME` |

## Sources (verdicts from the Step 1 research run, 2026-08-23)

1. **Constituents — primary**: `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv`
   (plain HTTPS, UA `radon/2.0`, 503 data rows, `Symbol` column, ODC-PDDL-1.0 public-domain
   dedication, fresh to the 2026-08-18 RDDT index add).
   **Fallback**: Wikipedia MediaWiki parse API
   `https://en.wikipedia.org/w/api.php?action=parse&page=List_of_S%26P_500_companies&prop=wikitext&section=1&format=json&formatversion=2`
   (UA `radon/2.0`; extract `{{NyseSymbol|X}}`-family templates with a broad
   `\{\{[A-Za-z]*[Ss]ymbol\|([A-Z][A-Z0-9.\-]*)\}\}` match; ticker list is
   uncopyrightable factual data).
   **Final fallback**: existing `scripts/clients/index_constituents.py`
   `resolve_constituents("SPX")` cache/seed chain (never fails). Reuse that module's
   `normalize_ticker` (canonical DASH form, `BRK-B`) and treat any source returning
   fewer than `MIN_PLAUSIBLE_COUNTS["SPX"] = 400` tickers as a fetch failure.
2. **Dividends + price per ticker**: Yahoo v8 chart
   `https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?range=1y&interval=1d&events=div`
   with `headers={"User-Agent": "Mozilla/5.0"}` (repo precedent:
   `scripts/fetch_credit_spread.py`, `scripts/cri_scan.py`; no cookie/crumb needed;
   v7 batch quote is 401 and MUST NOT be used). `meta.regularMarketPrice` +
   `events.dividends` amounts. Measured full sweep: 503 requests, 6 workers, 15.7s,
   0 errors — sanctioned Yahoo bulk deviation per the `scripts/bpi_scan.py` precedent
   (IB has no bulk dividend API; UW has no dividend endpoint at all). We display only
   the aggregate percentage/count, never redistributing per-ticker quotes.
3. **10Y**: `SELECT date, y10 FROM yield_curve_history ORDER BY date DESC LIMIT 1`
   from Turso. Never fetch the 10Y again. For the backfill join, use each month-end's
   last available `y10` row at or before the bar date.

## Backfill (Option B — approximate, run once with `--backfill`)

One Yahoo call per ticker: `period1=631152000` (1990-01-01) to now, `interval=1mo`,
`events=div` (explicit period1/period2 — `range=max` silently degrades to 3mo bars).
For each month-end: trailing-12M dividend sum / that month's close per ticker, joined
against `y10`, aggregated to one monthly row. Rows written with `approximate = 1`.
Daily rows from the normal timer run are `approximate = 0`; `backfill_cutover` in the
payload is the first non-approximate date. **Honest-labeling copy (required, verbatim
tone)**: "Pre-cutover history is a survivorship-biased approximation: today's
constituents projected backward, not point-in-time index membership." Dead
high-yielders (pre-2008 banks etc.) are missing, so the approximate portion
understates historical highs.

## Ingestion — `scripts/fetch_divyield.py`

Composed-method style, stdlib-preferred (`csv`, `json`, `urllib`/existing http helper,
`concurrent.futures` with 6 workers). Small pure functions:
`fetch_constituents()` (3-tier fallback), `fetch_ticker_chart(ticker)`,
`compute_trailing_yield(chart_json)`, `compute_pct_above(yields, y10)`,
`build_series_row(...)`, `build_output(...)`.

- `date` key for the daily row = the last trading date from Yahoo `meta`
  (`regularMarketTime` in `America/New_York` via `ZoneInfo` — never a hardcoded
  offset). Weekend/holiday runs recompute the same `date` → idempotent.
- **Unchanged-day fast path** (this source has no Last-Modified): if the freshly
  computed row equals the latest stored row (same `date`, `count_above`, `total`),
  skip the row upsert and only refresh the snapshot + heartbeat
  ("source unchanged; refreshing snapshot only" to stderr).
- **Empty/degenerate-payload guard**: refuse to persist when constituents < 400 or
  valid yield computations < 80% of constituents (`quote_errors` too high). On such a
  soft failure, raise (retryable) — do not latch `service_health` ok.
- Writes, in order, every cycle: `ensure_no_replica_for_writers()` →
  `upsert_divyield_rows(rows, recorded_at=scan_time)` (only when changed) →
  `upsert_scan_snapshot("div-yield", scan_time, payload)` →
  `record_service_health("div-yield", "ok", finished_at=scan_time)` → atomic JSON
  fallback `data/divyield.json`. Turso is the source of truth.
- CLI: `--json` prints payload to stdout (stdout is ONLY the payload; progress to
  stderr), `--backfill` runs the monthly 1990+ approximate build then exits.

## Storage — `scripts/db/migrations/0055_divyield.sql`

```sql
CREATE TABLE IF NOT EXISTS divyield_history (
    date TEXT PRIMARY KEY,
    pct_above REAL NOT NULL,
    count_above INTEGER NOT NULL,
    total INTEGER NOT NULL,
    y10 REAL NOT NULL,
    approximate INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_divyield_history_date_desc ON divyield_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (55, datetime('now'));
```

Writer `scripts/db/writer.py`: `upsert_divyield_rows(rows, recorded_at)` — idempotent
`INSERT ... ON CONFLICT(date) DO UPDATE`. Arity + idempotency pinned in pytest against
the migration executed into in-memory sqlite.

## Payload (scan_snapshots service `div-yield`)

```json
{
  "scan_time": "2026-08-23T22:40:11+00:00",
  "source": {"constituents": "github-datasets", "constituents_count": 503, "quote_errors": 0},
  "data_date": "2026-08-22",
  "y10_date": "2026-08-22",
  "current": {
    "date": "2026-08-22", "pct_above": 3.78, "count_above": 19, "total": 503,
    "y10": 4.74,
    "leaders": [{"ticker": "TDG", "yield_pct": 7.5}]
  },
  "series": [
    {"date": "1990-01-31", "pct_above": 12.1, "count_above": 58, "total": 480, "y10": 8.21, "approximate": 1}
  ],
  "backfill_cutover": "2026-08-23"
}
```

`leaders` is the top 10 by trailing yield (aggregate display only). `series` is
ordered ascending by date, monthly `approximate:1` rows then daily rows.

## API — `web/app/api/divyield/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only.
- `dbFirstRead`: `fromDb` = latest `scan_snapshots WHERE service = 'div-yield'`,
  `fromDisk` = `data/divyield.json`, fresher content timestamp wins.
- `MAX_AGE_MS = 48h` — daily timer with slack; older than 48h means the writer is down.
- Missing contract: HTTP 200 + frozen
  `{ missing: true, scan_time: null, data_date: null, current: null, series: [], backfill_cutover: null }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["divyield"] })`.
- `web/lib/divyield.ts`: types + pure helpers (`divYieldRegimeLabel(pct)` per the
  threshold table, formatters). `web/lib/useDivYield.ts`:
  `useSyncHook({ endpoint: "/api/divyield", interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`
  (hourly poll for a daily series; `0` pauses).

## UI — `web/components/DivYieldPanel.tsx`, `web/app/regime/divyield/page.tsx`

- Gate order: `SpectralLoader` (`label="Loading dividend yield breadth series"`) while
  `(loading || syncing) && !data` → `SectionEmptyState` on `missing: true` → content.
- Strip (desktop `RegimeStrip`/`RegimeStripCell`, mobile `MetricCell` grid via
  `useViewport()`): `ABOVE 10Y` (pct, 2dp, e.g. `3.78%`), `COUNT` (`19 / 503`),
  `10Y YIELD` (`4.74%`), `REGIME` (label per thresholds), `SOURCE UPDATED`
  (`data_date`). Header clock renders payload `scan_time`.
- Chart: `CriHistoryChart`, single series `pct_above` (right axis, percent), title
  `PCT OF SPX STOCKS YIELDING ABOVE 10Y`, `xTickFormat` for the multi-decade monthly
  domain. `HistoryRangeChips` + `historyRange.ts` presets, default `All` (long monthly
  series), `BrushMinimap` with `testIdPrefix="divyield-brush"`.
- `InfoTooltip`: explains the signal, the strict-inequality rule, TTM-includes-specials
  note, and the survivorship caveat sentence for pre-cutover history. **No em dashes in
  copy. No cadence claims** except copy that names the real daily timer ("the div-yield
  refresh timer"). Brand tokens only (`var(--token)` + `color-mix`), 4px max radius.
- `RegimePanel.tsx` registration in all four places + mobile chip bar; tab `DIV YIELD`.

## Timer — `cloud/services/radon-divyield.{service,timer}`

- Service: `Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/home/radon/radon-cloud/.env`, `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_divyield.py`
  (venv python directly, never the wrapper), `TimeoutStartSec=900` (503 fetches + slack).
- Timer: `OnCalendar=*-*-* 22:40:00 UTC` — after US close and after radon-yield-curve's
  20:45/22:30 passes have landed the day's `y10`; offset from 22:30 to avoid sharing
  the minute. `Persistent=true`, `RandomizedDelaySec=300`, `WantedBy=timers.target`.
  Weekend runs are unchanged-day heartbeats.
- Register: `setup-vps.sh` `SERVICE_FILES` + `cloud/config/installed-units.sha256`
  (both hashes, same commit) + `cloud/tests/test_systemd_services.py` canonical set.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`: `div-yield`,
  daily-writer windows cloned from the `yield-curve` entry (its category/copy/window
  values are the reference), plus the daily-bucket check list.

## Tests (write first; red = missing implementation modules)

- `scripts/tests/test_divyield.py` — fixtures
  `scripts/tests/fixtures/divyield_constituents_sample.csv` (31 lines: header + 30 of
  503 rows) and `divyield_quote_sample.json` (real XOM v8 response; 4 div events
  totaling 4.12 TTM, `regularMarketPrice` 165.11 → trailing yield 2.495%, arrays
  truncated per `_fixture_note`). Cover: constituents CSV parse (30 tickers, DASH
  form), chart parse → trailing yield 2.495 (derive from fixture, no mental
  arithmetic), strict-inequality boundary (`yield == y10` not counted),
  `compute_pct_above` on a synthetic set, migration 0055 executed into in-memory
  sqlite (schema + version + upsert idempotency + `approximate` default), writer
  arity, unchanged-day fast path (no row upsert, snapshot + heartbeat still written),
  degenerate-payload guard (constituents < 400 raises), payload contract keys,
  window-relative dates for freshness.
- `web/tests/divyield-api.test.ts` (`@vitest-environment node`) — in-memory
  `@libsql/client` seeded from the real snapshot shape: Turso-beats-older-disk, disk
  fallback, exact `missing: true` object at 200, no cross-service leak
  (`yield-curve` snapshot must not serve), `route.dynamic === "force-dynamic"`.
- `web/tests/divyield-panel.test.tsx` (`@vitest-environment jsdom`) — mock
  `@/lib/useDivYield`; assert loader label, empty state, strip values (`3.78%`,
  `19 / 503`, `4.74%`), regime label boundaries (4.99 SCARCE, 5 LOW, 15 NEUTRAL,
  35 ELEVATED, 50 EXTREME), chart title, chips, brush testid, NaN guard.
- `web/e2e/divyield-tab.spec.ts` — route mocks per pattern; active tab, stroked
  paths, brush visible, missing-state copy.

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=divyield`,
`<slug>=divyield`, `<Name>=DivYield`, service `div-yield`, reference implementations
`fetch_margin_debt.py` / `margin-debt/route.ts` / `MarginDebtPanel.tsx` (plus
`fetch_yield_curve.py` for the Yahoo header + timer conventions and `bpi_scan.py` for
the bulk-sweep concurrency pattern). No `scripts/clients/` addition needed unless the
implementer factors the 3-tier constituents fetch into
`scripts/clients/divyield_sources.py`; reusing `index_constituents.py` helpers is
preferred.
