# IV SPREAD — NDX vs SPX 1-Month ATM Implied Volatility Spread

**Status:** authoritative build spec. Implementers follow this literally.

---

## 0. What this is

The difference between NDX and SPX 30-day (1M) at-the-money implied
volatility, in volatility points: `spread = (ndx_iv - spx_iv) x 100`. It is
the premium the market pays for tech-index optionality over broad-index
optionality. Over the trailing five years it has averaged about 5.3 points
with a 1.6 point standard deviation; it compresses toward zero (and briefly
inverted, 2025-04-08, when SPX vol led a tariff crash) and blows out past 12
points when single-name tech events dominate (2026-06-23).

Descriptive regime read: a wide spread means tech vol is rich relative to the
index, a thin spread means the tech premium has been bid away. No validation
study was run, so no copy may claim a forward-return edge.

Research verdict (2026-09-02, IB probe on the cloud gateway, evidence in
`scripts/tests/fixtures/iv_spread_ib_sample.json`):

- **IB is the only source.** `reqHistoricalData` with
  `whatToShow="OPTION_IMPLIED_VOLATILITY"`, `barSizeSetting="1 day"`,
  `useRTH=True` on `Index("SPX", "CBOE", "USD")` (conId 416904) and
  `Index("NDX", "NASDAQ", "USD")` (conId 416843) returns daily annualized
  30d ATM IV closes as decimals (SPX `0.12104312`, NDX `0.1758578` on
  2026-09-02). `reqHeadTimeStamp` says history reaches **2006-01-06** on both
  legs. A `5 Y` pull returned 1253 bars per leg with every date present on
  both legs (no unpaired sessions). Verified live with `client_id="auto"`
  (20-49 range), two requests, seconds of wall clock.
- **No UW or Yahoo rung.** Unusual Whales serves no index-level 30d IV history
  for NDX; Yahoo serves no implied vol at all. The job is IB-only: an IB
  outage re-serves the cached payload as `stale_source` with an error
  heartbeat (section D.1). Licensing: IB market data is account-licensed; a
  derived daily scalar is internal derived data, no redistribution concern.
- **Cross-check against the reference chart** (Bloomberg-style, 2Sep2021 to
  Sep2026): reference High 12.56 / Avg 5.35 / StdDev 1.53; the IB capture
  gives High 12.64 (2026-06-23) / Mean 5.32 / StdDev 1.57. Consistent. The
  reference Low 1.9 differs from IB's -3.30 because IB carries the real
  2025-04-08 inversion; it is not a bad print (both legs moved with the crash,
  neither trips the section D.2a gate).

No em dashes in user-facing copy. Hyphens only.

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `iv-spread` |
| Route | `/regime/iv-spread` |
| Page file | `web/app/regime/iv-spread/page.tsx` |
| Tab label (desktop + mobile) | `IV SPREAD` |
| Rail group | Volatility, immediately after `ivrank` |
| Display name | NDX vs SPX 1M IV Spread |
| API route | `/api/iv-spread` |
| service_health key | `iv-spread` |
| systemd units | `radon-iv-spread.service`, `radon-iv-spread.timer` |
| Turso table | `iv_spread_history` |
| Snapshot service | `scan_snapshots.service = 'iv-spread'` |
| Disk fallback | `data/iv_spread.json` |
| Job | `scripts/fetch_iv_spread.py` |
| Refresh constant | `IV_SPREAD_REFRESH` in `web/lib/refreshSchedule.ts` |
| Spec | `docs/indicators/iv-spread.md` |

**One-line description** (InfoTooltip first sentence):

> NDX 30-day implied volatility minus SPX 30-day implied volatility, in
> volatility points. A wide spread means the market is paying up for tech
> optionality relative to the broad index; a thin or negative spread means
> that premium has been bid away. Read against its own five-year mean and
> standard deviation, nothing more.

---

## B. The math

### B.1 Named constants (module level, `scripts/fetch_iv_spread.py`)

```python
LEGS = (("SPX", "CBOE"), ("NDX", "NASDAQ"))   # (symbol, exchange), Index/USD
VOL_POINTS = 100.0            # decimal IV -> vol points
BACKFILL_DURATION = "5 Y"     # IB durationStr for --backfill (seed run)
INCREMENTAL_DURATION = "1 M"  # daily run: ~22 bars, survives missed runs
Z_COMPRESSED_MAX = -1.0       # regime band edges on the z-score, strict per B.4
Z_NORMAL_MAX = 1.0
Z_ELEVATED_MAX = 2.0
OUTLIER_NEIGHBOR_RATIO = 1.5  # leg-level bad-print gate, strict (D.2a)
```

### B.2 Formulas

Per session date `d` present on BOTH legs:

```
spread(d) = (ndx_iv(d) - spx_iv(d)) x VOL_POINTS
```

Over the full stored series `S` of non-null spreads (every session, not a
rolling window — the reference chart's dashed line is the whole-history
average):

```
mean   = sum(S) / n
stdev  = sample standard deviation of S (n - 1 denominator); None when n < 2
z(d)   = (spread(d) - mean) / stdev              ; None when stdev is None or 0
pctile = count(v in S where v < spread(d)) / n x 100   (strict <)
```

**Guards:**

- A date present on only one leg is dropped (not emitted) and counted in
  `dropped_unpaired`. The IB capture had zero such dates; the guard exists so
  a half-served day never prints a spread against a stale leg.
- A leg close `<= 0` on either leg drops the date the same way.
- `stdev == 0` or `n < 2` → `z` is `None`, never divide.
- A session excluded by the bad-print gate (D.2a) keeps its row (both raw leg
  IVs are stored) with `spread: null`; stats, z and percentile skip nulls.

### B.3 Hand-computable worked examples (pin exactly)

`compute_spread(spx_iv, ndx_iv)` is the pure function.

**Example 1 — nominal.** `spx 0.12, ndx 0.18` → `(0.18 - 0.12) x 100 = 6.0`
(assert to 1e-9; floating subtraction, not exact).

**Example 2 — inversion.** `spx 0.4492327, ndx 0.41626135` → `-3.297135`
(the real 2025-04-08 print, 6 dp).

**Example 3 — stats on a short series.** `S = [4.0, 5.0, 6.0, 9.0]`:
`mean = 6.0`, `stdev = sqrt(((4-6)^2 + (5-6)^2 + 0 + 9) / 3) = sqrt(14/3) =
2.160247` (6 dp), `z(9.0) = 3 / 2.160247 = 1.388730` (6 dp), `pctile(9.0) =
3/4 x 100 = 75.0` exactly, `pctile(4.0) = 0.0`.

**Example 4 — the fixture calibration pins.** Against
`scripts/tests/fixtures/iv_spread_ib_5y_closes.json` (1253 paired closes,
2021-09-07 .. 2026-09-02, scratch-derived 2026-09-02):

```
count            1253
last             2026-09-02  spx 0.12104312  ndx 0.1758578  spread 5.481468
high             12.642458 on 2026-06-23
low              -3.297135 on 2025-04-08
mean             5.318448
stdev (sample)   1.567474
z(last)          0.104002
pctile(last)     59.377494        (744 of 1253 strictly below)
change_1d        0.360352         (5.481468 - 5.121116)
regime(last)     NORMAL
outlier flags    none on either leg
```

Against `iv_spread_ib_sample.json` (the `1 M` daily pull, 22 bars per leg,
2026-08-04 .. 2026-09-02): first spread `8.992909`, last `5.481468`,
`mean 6.613825`, `stdev 1.083811`, `max 8.992909`, `min 4.655993`.

Assert all six-decimal pins with `abs=1e-6`.

### B.4 Regime bands (strict inequalities on `z`)

| Band | Condition | Token |
|---|---|---|
| `COMPRESSED` | `z < -1` | `var(--text-muted)` |
| `NORMAL` | `-1 <= z < 1` | `var(--text-muted)` |
| `ELEVATED` | `1 <= z < 2` | `var(--warning)` |
| `EXTREME` | `z >= 2` | `var(--dislocation)` |
| `null` | z unavailable | `var(--text-muted)`, renders `---` |

`z == -1` is `NORMAL`; `== 1` is `ELEVATED`; `== 2` is `EXTREME`. Pin the
boundaries. `EXTREME` uses `--dislocation` (structural state), never
`--negative`.

---

## C. Schema — migration `0069_iv_spread.sql`

Number **0069** verified free 2026-09-02: Turso `MAX(version) = 68`, local
tree tops at `0068_ma_ratio.sql`. Re-check both before writing.

```sql
-- 0069_iv_spread.sql — IV SPREAD indicator: NDX minus SPX 30-day ATM implied
-- volatility in vol points. spx_iv / ndx_iv are the raw annualized decimal
-- closes from IB OPTION_IMPLIED_VOLATILITY daily bars. spread is NULL only
-- for a session the leg-level bad-print gate excluded (the raw legs stay).

CREATE TABLE IF NOT EXISTS iv_spread_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD session date
  spx_iv      REAL NOT NULL,      -- SPX 30d ATM IV close, annualized decimal
  ndx_iv      REAL NOT NULL,      -- NDX 30d ATM IV close, annualized decimal
  spread      REAL,               -- (ndx_iv - spx_iv) * 100, NULL when excluded
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_iv_spread_history_date ON iv_spread_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (69, datetime('now'));
```

`scripts/db/writer.py:upsert_iv_spread_rows(rows, recorded_at=None)` beside
`upsert_ivrank_rows`, copied with the column tuple
`(date, spx_iv, ndx_iv, spread, recorded_at)`: chunked multi-row INSERT at
`_PRICE_HISTORY_INSERT_CHUNK_ROWS` (400), `ON CONFLICT(date) DO UPDATE`,
never `executemany`, `db.commit()` once. Empty rows write nothing.

---

## D. Ingestion — `scripts/fetch_iv_spread.py`

Model: `scripts/fetch_ivrank.py` verbatim in structure (composed method,
stderr progress, atomic JSON, unchanged fast path, `_write_db` with the
row-error-folded-into-heartbeat shape), minus the UW rungs.

### D.1 Source ladder

1. **Gate:** `utils.ib_preflight.ib_auth_state()` (bounded /health read). If
   `auth_state` is present and not `"authenticated"`, skip the IB socket. Any
   other outcome (including an unreachable FastAPI) still ATTEMPTS IB.
2. **IB, both legs:** one `IBClient` connection (`client_id="auto"`), then
   `get_historical_data(Index(sym, exch, "USD"), duration, bar_size="1 day",
   what_to_show="OPTION_IMPLIED_VOLATILITY", use_rth=True)` for each leg in
   `LEGS`, `durationStr=INCREMENTAL_DURATION` (`BACKFILL_DURATION` under
   `--backfill`). Disconnect in `finally`. The injected fetcher contract is
   `ib_fetch(duration) -> {"SPX": [{date, iv}], "NDX": [{date, iv}]}`.
   A leg that raises or returns zero bars fails the whole fetch (a spread
   needs both).
3. **IB failed or skipped** → if `data/iv_spread.json` exists, re-serve it
   with a fresh `scan_time`, `status: "stale_source"`, snapshot + **error**
   heartbeat (`{"message": "iv-spread: IB unavailable (<why>); serving cached
   payload through <as_of>"}`), no row upserts. No cache → `raise`
   (never cache empty). `--backfill` with IB unavailable is a hard error.

### D.2 Merge + compute

- `load_history()`: `SELECT date, spx_iv, ndx_iv FROM iv_spread_history WHERE
  date > ? ORDER BY date LIMIT 2000`, keyset-paginated on date; fallback to
  the `data/iv_spread.json` series (rows carrying `spx_iv`/`ndx_iv`) when
  Turso is unreachable or empty.
- `merge_history(stored, fetched_legs)`: per leg, fetched wins by date (IB
  restates the current session's bar). A date that ends up on only one leg
  is dropped and counted (B.2).
- `compute_series(rows)` → ascending `[{date, spx_iv, ndx_iv, spread}]` with
  the D.2a gate applied, then `compute_stats(series)` and `build_current`.
- `rows_changed` = any date added or any leg IV changed vs the loaded
  history. Weekend/holiday runs restate the same bars → snapshot + heartbeat
  only (`"[iv-spread] source unchanged; refreshing snapshot only"`).

### D.2a Bad-print gate

IB's `OPTION_IMPLIED_VOLATILITY` series carries occasional single-session bad
prints (ivrank saw `0.2443` between `0.1153` and `0.1251` on 2026-08-17). A
spread of two such series doubles the exposure. `detect_outliers(rows,
leg)`: a leg close **strictly** more than `OUTLIER_NEIGHBOR_RATIO` times BOTH
neighbours (or below both by the same ratio) is flagged. Edges never qualify.
Exactly `1.5x` is not an outlier. There is no second feed to repair from, so
the session is **excluded**: `spread: null`, raw legs kept, and the payload
lists `excluded: [{date, leg, iv, prev_iv, next_iv}]`. The 5Y capture flags
nothing on either leg (B.3), and the real 2025-04-08 inversion must not be
flagged (SPX `0.449` sits between `0.393` and `0.256`: `0.449/0.256 = 1.75`
but `0.449/0.393 = 1.14`, so only one neighbour qualifies).

### D.3 Write order (every cycle)

```python
writer.ensure_no_replica_for_writers()
if rows_changed and rows:
    writer.upsert_iv_spread_rows(rows, recorded_at=scan_time)   # own try/except, folded into the heartbeat
writer.upsert_scan_snapshot("iv-spread", scan_time, payload)           # EVERY cycle
writer.record_service_health("iv-spread", "ok" | "error", finished_at=scan_time, error=...)  # EVERY cycle
```

Atomic JSON write to `data/iv_spread.json` (tmp + `os.replace`) after the DB
step, every cycle. The service key is `iv-spread` (kebab) in every place.

### D.4 CLI

`argparse`: `--json` (payload to stdout) and `--backfill`. Progress lines
`print("[iv-spread] ...", file=sys.stderr)`. `run(ib_fetch=None, *, now=None,
backfill=False)` accepts an injected fetcher. `scan_time` tz-aware UTC ISO
with `Z`; session logic through `utils.market_calendar` only. No new
environment variables: every path derives from the project directory.

---

## E. Scheduling

`OnCalendar=*-*-* 22:15:00 UTC`, every calendar day. Verified free
2026-09-02 (22:10 ivrank, 22:20 dispersion, 22:30 yield-curve, 22:40
divyield, 22:45 ma-ratio). 22:15 UTC is after the 16:00 ET close year-round
(20:00/21:00 UTC), so both daily IV bars are final, and it sits between the
two neighbouring IB daily pulls so the three never share a minute (each uses
an auto client id anyway). Weekend and holiday runs are unchanged-data
heartbeats.

`cloud/services/radon-iv-spread.service` — copy `radon-ivrank.service` minus
the `RADON_UW_CALLER` line (oneshot, `User=radon`,
`WorkingDirectory=/home/radon/radon`, `EnvironmentFile=/etc/radon/env`,
`Environment=RADON_DB_NO_REPLICA=1`, venv python directly, journald,
`TimeoutStartSec=300`, `StartLimitIntervalSec=300` / `StartLimitBurst=5`).
`cloud/services/radon-iv-spread.timer` — `Persistent=true`,
`RandomizedDelaySec=120`, comment naming the 22:15 UTC reasoning.

**Same commit:** `setup-vps.sh` `SERVICE_FILES` (after the ivrank pair),
`cloud/tests/test_systemd_services.py` `EXPECTED_SERVICE_FILES` (same spot),
`cloud/config/installed-units.sha256` (sha256 of each unit, beside the ivrank
lines), `docs/operations.md` timer table row and a `### IV SPREAD
(radon-iv-spread.timer)` section in `docs/cloud-services.md` beside IV RANK
(the `ops-timers` docs-contract rule).

### E.1 service_health key + staleness windows

Key `iv-spread` in: writer heartbeat, snapshot service, route
`WHERE service = 'iv-spread'` + `dbFirstRead` label,
`web/lib/serviceHealthWindows.ts`, `scripts/watchdog/services.py`.

`web/lib/serviceHealthWindows.ts` (directly after `ivrank`):

```ts
// ``iv-spread`` — radon-iv-spread.timer fires daily 22:15 UTC every calendar day
// (weekend runs are unchanged-data heartbeats), so a uniform 26h window matches
// its ivrank sibling. IB is the ONLY feed (no UW/Yahoo rung serves index 30d IV),
// so an IB outage is the one thing that explains a missing reading: requires_ib true.
"iv-spread": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: true },
```

`scripts/watchdog/services.py`, two edits beside `ivrank`: the
`SCHEDULED_SERVICES` entry `{"open": 26 * _HOUR, "closed": 26 * _HOUR,
"requires_ib": True}` and the daily-bucket check list, comments naming 22:15
UTC. `web/tests/service-health-windows.test.ts` gets the exhaustive-set entry
plus an `it("iv-spread ...")` case mirroring the ivrank one with
`requiresIb("iv-spread")` `true`.

`web/lib/refreshSchedule.ts`:

```ts
/** cloud/services/radon-iv-spread.timer */
export const IV_SPREAD_REFRESH: UtcSchedule = { cadence: "daily", hourUtc: 22, minuteUtc: 15 };
```

`web/tests/refresh-schedule.test.ts`: `it("IV spread mirrors
radon-iv-spread.timer")` via the existing `expectedOnCalendar` /
`timerOnCalendar` helpers.

---

## F. The API

### F.1 FastAPI: none

Daily timer + Turso-reading Next.js route, same as ivrank. No operator
rescan in v1.

### F.2 Next.js route — `web/app/api/iv-spread/route.ts`

Copy `web/app/api/ivrank/route.ts` and rename.

```ts
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

// Contract: absent iv-spread data is HTTP 200 with missing:true, never a 4xx.
const MISSING_IV_SPREAD = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  excluded: [],
};

// radon-iv-spread.timer fires daily at 22:15 UTC including weekends, so a
// snapshot older than two days means the writer is down. A stale_source
// re-serve refreshes scan_time every run, so its age rides the DATA date
// (as_of) via ivSpreadContentTimestampMs; other payloads age by scan_time.
const IV_SPREAD_MAX_AGE_MS = 48 * 60 * 60_000;
```

`dbFirstRead({ fromDb, fromDisk, maxAgeMs, label: "iv-spread", isDegraded:
isMissingPayload })`, `fromDb` = latest `scan_snapshots` row for
`service = 'iv-spread'`, `fromDisk` = `data/iv_spread.json`;
`staleCollapse(MISSING_IV_SPREAD, result)` on the not-fresh path;
`setCacheResponseHeaders(..., { maxAgeSeconds: 300,
staleWhileRevalidateSeconds: 3600, requestId, cacheState: "HIT", tags:
["iv-spread"] })`. GET only; never transforms; never 4xx.

Pins in the same commit: `web/lib/assistant/nextLoaders.ts` (`"iv-spread"`),
`web/tests/assistant-catalog-pin.test.ts` (`"iv-spread": "read"`),
`web/tests/route-local-authz-matrix.test.ts` (`"iv-spread"` in the
perimeter-only list beside `"ivrank"`).

### F.3 Payload shape

```jsonc
{
  "scan_time": "2026-09-02T22:15:09Z",
  "status": "ok",                     // "ok" | "stale_source"
  "source": "ib",
  "as_of": "2026-09-02",              // max series date
  "expected_session": "2026-09-02",
  "market_status": "closed",
  "count": 1253,                      // series rows
  "spread_count": 1253,               // rows with a non-null spread
  "dropped_unpaired": 0,

  "current": {
    "date": "2026-09-02",
    "spx_iv": 0.12104312,
    "ndx_iv": 0.1758578,
    "spread": 5.481468,
    "z_score": 0.104002,
    "pctile": 59.377494,              // share of history strictly below
    "change_1d": 0.360352,            // spread minus the prior non-null spread
    "regime": "NORMAL"                // COMPRESSED | NORMAL | ELEVATED | EXTREME
  },

  "stats": {                          // over all non-null spread history
    "count": 1253,
    "high": 12.642458, "high_date": "2026-06-23",
    "low": -3.297135,  "low_date": "2025-04-08",
    "mean": 5.318448,
    "stdev": 1.567474,
    "last": 5.481468
  },

  "excluded": [],                     // [{date, leg, iv, prev_iv, next_iv}]

  "series": [
    { "date": "2021-09-07", "spx_iv": 0.12250358, "ndx_iv": 0.15679251, "spread": 3.428893 },
    { "date": "2026-09-02", "spx_iv": 0.12104312, "ndx_iv": 0.1758578,  "spread": 5.481468 }
  ]
}
```

Every numeric field is `null` when unavailable — never `NaN`, never a
sentinel. `status: "stale_source"` when IB failed and the cached payload was
re-served.

### F.4 Hook — `web/lib/useIvSpread.ts`

`useSyncHook({ endpoint: "/api/iv-spread", interval: 3_600_000, hasPost:
false, extractTimestamp: d => d.scan_time }, true)` — hourly poll for a daily
series. Types live in `web/lib/ivSpread.ts`.

---

## G. The UI

### G.1 Files

| File | Role |
|---|---|
| `web/lib/ivSpread.ts` | types mirroring F.3, `formatSpread`, `formatIvPercent`, `formatZ`, `ivSpreadRegime`, `ivSpreadRegimeColor`, `buildIvSpreadChartRows`, display consts |
| `web/lib/useIvSpread.ts` | hook |
| `web/components/IvSpreadPanel.tsx` | tab body |
| `web/app/regime/iv-spread/page.tsx` | 5-line `WorkspaceShell` page (`section="regime"`) |
| `web/lib/regimeRail.ts` | `RegimeTab` union + Volatility group after `ivrank` + `REGIME_TAB_LABEL` `"iv-spread": "IV SPREAD"` |
| `web/components/RegimePanel.tsx` | four edits: `MOBILE_TAB_LABEL`, the `tabFromPathname` regex, the mobile chip array (after `"ivrank"`), the dispatch branch |

### G.2 Formatters (`web/lib/ivSpread.ts`)

- `formatSpread(v)` → two decimals with sign only when negative: `5.481468 →
  "5.48"`, `-3.297135 → "-3.30"`; `"---"` for null/non-finite.
- `formatIvPercent(v)` → `0.1758578 → "17.6%"`; `"---"` unavailable.
- `formatZ(v)` → signed two decimals: `0.104002 → "+0.10"`, `-1.25 →
  "-1.25"`, `0 → "+0.00"`; `"---"` unavailable.
- `ivSpreadRegime(z)` and `ivSpreadRegimeColor(regime)` per B.4.
- `buildIvSpreadChartRows(series)` → `[{date, spx_iv, ndx_iv, spread}]`,
  nulls preserved (the spread line gaps, never plots a zero).
- Display consts: `Z_COMPRESSED_MAX = -1`, `Z_NORMAL_MAX = 1`,
  `Z_ELEVATED_MAX = 2`.

### G.3 Panel

- Gate order: `SpectralLoader` `label="Loading NDX vs SPX IV spread series"`
  while `(loading || syncing) && !data` → `SectionEmptyState`
  (`headline="No NDX vs SPX IV spread data yet"`, secondary copy naming "the
  iv-spread refresh timer") on `missing: true` / no current / empty series →
  content. Destructure `error` from the hook and render
  `<PanelRefreshError error={error} testId="iv-spread-refresh-error" />`
  (R-124 rule).
- Header: section title `NDX vs SPX 1M IV Spread` with `InfoTooltip` (A);
  `status === "stale_source"` renders a `STALE` pill
  (`data-testid="iv-spread-degraded"`, `var(--warning)` border, title "IB did
  not answer; showing the last good reading.") and the clock shows `as_of`
  instead of the scan time; otherwise the clock renders `scan_time` as a
  local time.
- Strip (`RegimeStrip` desktop / `MetricCell` grid mobile, test ids
  `iv-spread-strip-*`):
  - `SPREAD` value `formatSpread(current.spread)` regime-coloured
    (`data-testid="iv-spread-spread-value"`), sub `1D {formatSpread(change_1d)}
    VOL PTS`
  - `NDX 1M IV` `formatIvPercent(current.ndx_iv)`, sub `30D ATM IMPLIED VOL`
  - `SPX 1M IV` `formatIvPercent(current.spx_iv)`, sub `30D ATM IMPLIED VOL`
  - `Z-SCORE` `formatZ(current.z_score)`, sub `VS {stats.count}-SESSION MEAN
    {formatSpread(stats.mean)}` (sub reads `VS HISTORY` when stats are null)
  - `PCTILE` `pctile` at one decimal with `%`, sub `SHARE OF SESSIONS BELOW`
  - `REGIME` regime-coloured (`data-testid="iv-spread-regime-value"`), sub
    `TECH VOL PREMIUM VS MEAN`
- `<FreshnessRail schedule={IV_SPREAD_REFRESH} asOf={data.as_of ??
  current.date} testId="iv-spread-freshness-rail"
  asOfTestId="iv-spread-strip-asof" />` directly under the strip.
- Chart block (`data-testid="iv-spread-chart-section"`): `HistoryRangeChips`
  (`dataTestId="iv-spread-range-chips"`, `ariaLabel="NDX vs SPX IV spread
  chart range"`), then `CriHistoryChart` with title
  `NDX VS SPX 1M ATM IMPLIED VOL SPREAD`, `xTickFormat` day/month/year, and
  series:
  - left axis: `spread`, label `SPREAD`, `chartSeriesColor("primary")`,
    format two decimals (the spread must be on the LEFT scale because
    `referenceLevels` draw on the left scale)
  - right axis: `spx_iv`, label `SPX 1M IV`, `chartSeriesColor("comparison")`,
    format percent
  - `referenceLevels=[{ value: stats.mean, label: "AVG {formatSpread(mean)}" }]`
    when `stats.mean` is finite
  - `referenceBands=[{ from: mean - stdev, to: mean + stdev, label: "1 SD",
    axis: "left" }]` when both are finite
- `BrushMinimap` (`values = series.map(e => e.spread)`, `testIdPrefix=
  "iv-spread-brush"`, `ariaLabel="NDX vs SPX IV spread history range brush"`)
  when `total >= 2`; default preset `defaultPresetForLength(total)`.
- Stats line under the chart (`data-testid="iv-spread-stats"`, mono, muted):
  `HIGH 12.64 (2026-06-23)  LOW -3.30 (2025-04-08)  AVG 5.32  LAST 5.48
  STDEV 1.57` from `stats`; each figure `---` when null.
- Source footnote: `Source: Interactive Brokers NDX and SPX 30-day ATM implied
  volatility daily closes. Spread in volatility points against its full
  stored history. This is a regime description of relative option premium,
  nothing more.`
- Freshness copy derived only (clock = `scan_time`, rail = timer constant).
  Brand tokens + `color-mix` only, 4px radius, no em dashes, no predictive
  claim, no `Refreshes ...` string.

---

## H. Test pins (write first, red before implementation)

1. `scripts/tests/test_iv_spread.py` — fixture integrity (both fixtures);
   B.3 worked examples; B.3 calibration pins (6 dp) from the 5Y closes
   fixture and the 1M sample; B.4 boundary pins (-1/1/2); unpaired and
   non-positive dates dropped + counted; outlier gate (both sides, strict
   boundary, edges, the 2025-04-08 inversion NOT flagged, 5Y fixture flags
   nothing) and exclusion → `spread: null` + `excluded` entry; migration
   0069 executed into in-memory sqlite (columns, nullability, index, version
   row, upsert idempotency, 400-row chunking, never executemany, empty rows
   write nothing); `run()` happy path (rows + snapshot + `ok` heartbeat,
   `count`/`as_of`/`current`/`stats`, `scan_time` ends with `Z`, backfill uses
   `BACKFILL_DURATION`); unchanged source skips row upserts but snapshots +
   heartbeats; unauthenticated gateway skips the socket; unknown health still
   attempts IB; one dead leg is an IB failure; IB down with cache →
   `stale_source` + error heartbeat + no rows; no cache → raises and writes
   nothing; backfill without IB raises; row-upsert failure folds into an
   error heartbeat while the snapshot still lands; JSON mirrored atomically.
2. `web/tests/iv-spread-api.test.ts` — in-memory `@libsql/client` seeded with
   `scan_snapshots`; Turso-beats-older-disk; disk fallback; Turso throw →
   disk at 200; no cross-service leak; exact `MISSING_IV_SPREAD` at 200 for
   absent; >48h stale → `{ ...MISSING, stale: true, scan_time }`; inside 48h
   served; `stale_source` passes through; null `z_score`/`regime` intact;
   `dynamic`, `runtime`, no `POST`, cache headers + `iv-spread` tag.
3. `web/tests/iv-spread-panel.test.tsx` — formatter pins (G.2), regime
   boundary pins, colour tokens, display consts, chart-row nulls; panel
   loader label; empty state naming the timer; strip values from the
   calibration payload (`5.48`, `17.6%`, `12.1%`, `+0.10`, `NORMAL`); stale
   pill on `stale_source` and absent on `ok`; `---` for a null-spread
   current; chart title; stats line figures; NaN guard; freshness rail
   (`iv-spread-freshness-rail`, `iv-spread-strip-asof` shows the payload
   date, countdown `1h 15m` when the clock is pinned to 21:00 UTC); copy
   discipline (no predictive claim, no cadence string).
4. Lockstep pins in the same merge: `regime-tab-routes` (row + mock + two
   cases; `iv-spread` must not be swallowed by the `ivrank` alternation),
   `regime-rail` (30 tabs, Volatility group, label), `service-health-windows`
   exhaustive set, `refresh-schedule`, `assistant-catalog-pin`,
   `route-local-authz-matrix`, `cloud/tests/test_systemd_services.py`,
   `docs/indicators/README.md` row, e2e curation ledger.
5. `web/e2e/iv-spread-tab.spec.ts` — copy `ivrank-tab.spec.ts`: ambient
   mocks, active tab `aria-current`, strip values, freshness rail countdown
   `1h 15m` at `2026-08-26T21:00:00Z` (`data-state` `behind`, `Awaiting
   2026-08-26`), chart paths + brush, missing-state copy.
