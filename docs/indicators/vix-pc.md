# VIX P/C  -  5-session cumulative VIX put/call ratio

**Status:** authoritative build spec. Implementers follow this literally.
Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementations: `scripts/fetch_ivrank.py` (UW job, composed
methods, stubbed `run()`), `web/app/api/ivrank/route.ts` (dbFirstRead),
`web/components/IvRankPanel.tsx` / `VixTsPanel.tsx` (strip + freshness rail).

---

## 0. What this is

For each VIX options session, the 5-session cumulative put/call ratio is

```
cum5(i) = sum(put_volume[i-4 .. i]) / sum(call_volume[i-4 .. i])
```

on the UW session calendar (five sessions, not five calendar days). The
job also stores the raw daily put volume, call volume, and single-day
ratio `put / call`.

A low reading means VIX call volume dominates put volume over the last
five sessions. That is a more cautious options tape (demand for VIX
upside / vol hedges). A high reading means VIX put volume dominates
(betting vol will fall). This is a descriptive regime read. No forward-
return claim is made anywhere in the copy.

The reference chart this tab reproduces is titled "VIX options activity
turned more cautious in October" with the subtitle that the five-session
cumulative VIX put/call ratio hit multi-year lows on October 8. Against
the captured UW fixture (2024-10-18 .. 2026-10-16) that low is
**2026-10-08, cum5 = 0.241730**, the minimum of the 496 windowed
sessions. The latest fixture session is **2026-10-16, cum5 = 0.318849**,
4.03rd percentile (20 of 496 strictly below).

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `vix-pc` |
| Route | `/regime/vix-pc` |
| Page file | `web/app/regime/vix-pc/page.tsx` |
| Tab label (desktop + mobile) | `VIX P/C` |
| Display name | VIX 5-session put/call |
| API route | `/api/vix-pc` |
| `service_health` key | `vix-pc` |
| systemd units | `radon-vix-pc.service`, `radon-vix-pc.timer` |
| Turso table | `vix_pc_history` |
| Snapshot service | `scan_snapshots.service = 'vix-pc'` |
| Disk fallback | `data/vix-pc.json` |
| Rail group | **Volatility** (after `vixts`) |
| Migration | `0097_vix_pc.sql` (version 97) |
| Freshness constant | `VIX_PC_REFRESH` |

`MAX(version)` in the local tree is **96** (`0096_newsfeed_explanations.sql`).
**97 is free.** Only the ingestion implementer owns the migration.

**One-line description** (InfoTooltip first sentence):

> The five-session cumulative VIX put/call ratio, which is the sum of
> VIX put volume over the last five sessions divided by the sum of VIX
> call volume over those same sessions. A low reading means call volume
> dominates and the VIX options tape is more cautious. A high reading
> means put volume dominates.

No em dashes in user-facing copy. Hyphens only.

---

## B. Source (Step 1, 2026-10-18)

### B.1 Ladder

1. **IB does not serve this series.** `IBClient.get_historical_data`
   `whatToShow` is `TRADES` / `MIDPOINT` / `BID` / `ASK`. Index VIX bars
   are price, not put versus call volume. The repo has zero
   `OPTION_VOLUME` usage. Reconstructing from the live chain cannot
   backfill to January 2025. Do not open an IB socket.
2. **UW is primary.** `GET https://api.unusualwhales.com/api/stock/VIX/options-volume?limit=500`
   with the existing `UW_TOKEN` Bearer (strip surrounding quotes). Honest
   User-Agent `radon/2.0`. HTTP 200. `UWClient.get_options_volume` has no
   `limit` today; the job calls `_get("stock/VIX/options-volume", params={"limit": 500})`
   (or add an optional `limit` on the wrapper). Ticker is `VIX`. `$VIX`
   and `^VIX` 422. `VIXW` is a weekly series and is not a substitute.
3. Robinhood options are NBBO/last + prior-close only. Skip.
4. Cboe CDN `https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/vixpc.csv`
   is dead (last row 2019-10-04). Live Cboe official volumes exist only
   as one HTML page per session. Do not scrape. Yahoo is not used.

### B.2 Schema of the UW row

Wire fields used: `date` (`YYYY-MM-DD` session), `put_volume` (int
contracts), `call_volume` (int contracts). Newest-first on the wire;
sort ascending before the window. `limit` max is 500 (OpenAPI). History
floor on this token: **2024-10-18**. Sessions from 2025-01-01: **449**.
No date cursor. Gaps are holiday 4-day gaps only.

UW tape is not the Cboe official print (same dates, UW volume ~3-6%
lower). Do not splice Cboe HTML or the frozen 2019 CSV into this series.

### B.3 Licensing

UW: **permitted** for internal single-operator storage + display.
`https://api.unusualwhales.com/docs/conventions.md`: "You may keep and
use API data for personal or internal purposes, including after you
cancel. Redistributing the data to others requires a redistribution
license." Same posture as skew / gex / ivrank. Do not publicly re-serve
raw volume.

Cboe use-of-content would be unclear if that source were chosen. It is
not chosen.

### B.4 Cadence

UW daily options-volume finalizes after the session close. Captured
Sunday 2026-10-18, latest row Friday 2026-10-16. No Last-Modified / ETag.
Unchanged path is content-equal: newest date + volumes match the last
stored row.

Timer: `OnCalendar=*-*-* 02:00:00 UTC` every calendar day.
`Persistent=true`, `RandomizedDelaySec=120`. 02:00 UTC is after the
16:00 ET close year-round (20:00/21:00 UTC) and sits before
`radon-vixts.timer` at 02:45. Weekend and holiday runs are no-new-row
heartbeats. One UW call per run.

---

## C. The math

### C.1 Named constants

```python
WINDOW = 5                 # sessions, inclusive of the current session
MIN_OBSERVATIONS = 5       # == WINDOW. No partial-window cum5, ever.
TICKER = "VIX"
UW_LIMIT = 500
BAND_PCTL_LOW = 10.0       # CAUTIOUS if percentile < 10
BAND_PCTL_HIGH = 90.0      # COMPLACENT if percentile >= 90
```

Mirrored as TS display consts in `web/lib/vixPc.ts`. The UI never
recomputes a ratio.

### C.2 Formulas

Rows sorted by `date` ascending. For session `i`:

```
daily_ratio(i) = put_volume[i] / call_volume[i]     # None if call_volume <= 0
cum5_ratio(i)  = sum(put[i-4..i]) / sum(call[i-4..i])  # None if i < 4 or denom <= 0
```

Over all non-null `cum5_ratio` values `H` (the stored history, including
the current session):

```
percentile(i) = count(v in H where v < cum5_ratio[i]) / len(H) * 100
series_min = min(H)
series_max = max(H)
series_p10 = percentile_value(H, 10)   # linear interpolation on sorted H
series_p90 = percentile_value(H, 90)
```

`percentile_value` uses the same linear interpolation as the fixture pin
script: `k = (n-1) * p/100`, then interpolate between `floor(k)` and
`ceil(k)`.

**Guards:**

- Fewer than `MIN_OBSERVATIONS` sessions → `cum5_ratio` is `None`. The
  row is still emitted (continuous date axis) with raw volumes + daily
  ratio.
- `call_volume <= 0` or a 5-session call sum `<= 0` → that ratio is
  `None`. Never divide.
- Comparisons for percentile are strict (`<`).

### C.3 Hand-computable worked examples (pin exactly)

**Example 1  -  nominal window of 5.**

```
puts  = [10, 20, 30, 40, 50]
calls = [100, 100, 100, 100, 100]
daily_ratio last = 50 / 100 = 0.5 exactly
cum5 = 150 / 500 = 0.3 exactly
```

**Example 2  -  zero call volume is None, not zero.**

```
puts = [10], calls = [0]  -> daily_ratio is None
```

**Example 3  -  first four sessions have no cum5.**

A 4-row history emits `cum5_ratio: None` on every row.

**Example 4  -  fixture calibration (scratch-derived 2026-10-18, not from
the functions under test).** Against
`scripts/tests/fixtures/vix_pc_sample.json` (500 rows, 2024-10-18 ..
2026-10-16):

```
latest date        = 2026-10-16
latest put         = 573686
latest call        = 1729988
latest daily       = 0.33161270482801036
last-5 puts        = 97165 + 130364 + 270894 + 218214 + 573686 = 1290323
last-5 calls       = 351983 + 444822 + 913057 + 606968 + 1729988 = 4046818
latest cum5        = 1290323 / 4046818 = 0.3188487844029556
valid cum5 count   = 496
series min         = 0.2417297884911582   on 2026-10-08
series max         = 1.6973206456466932
series p10         = 0.3435408949777816
series p90         = 0.6760256318262985
latest percentile  = 20 / 496 * 100 = 4.032258064516129
oct-8 window puts  = 69370 + 182122 + 142860 + 294133 + 175180 = 863665
oct-8 window calls = 1084468 + 580029 + 717110 + 695274 + 495972 = 3572853
oct-8 cum5         = 863665 / 3572853 = 0.2417297884911582
```

Sessions on or after 2025-01-01: 449. History covers the reference
chart window.

### C.4 Regime bands (strict inequalities, percentile of history)

| Band | Condition | Token |
|---|---|---|
| `CAUTIOUS` | `percentile < 10` | `var(--warning)` |
| `NORMAL` | `10 <= percentile < 90` | `var(--text-muted)` |
| `COMPLACENT` | `percentile >= 90` | `var(--dislocation)` |
| `null` | no cum5 yet | `var(--text-muted)`, renders `---` |

`percentile == 10` is `NORMAL`. `percentile == 90` is `COMPLACENT`. Pin
the boundaries in a test. Bands are live percentiles of stored history,
not invented fixed ratio levels. The fixture p10/p90 values above are
the calibration pins for that 500-row sample only.

---

## D. Schema  -  migration `0097_vix_pc.sql`

```sql
-- 0097_vix_pc.sql  -  VIX 5-session cumulative put/call ratio.
-- Raw daily put/call volumes plus the single-day ratio. cum5_ratio is
-- NULL for the first WINDOW-1 rows and when the 5-session call sum is 0.
-- source is 'uw'. Recomputed on every run over the full stored series.

CREATE TABLE IF NOT EXISTS vix_pc_history (
  date         TEXT PRIMARY KEY,  -- YYYY-MM-DD session
  put_volume   INTEGER NOT NULL,
  call_volume  INTEGER NOT NULL,
  daily_ratio  REAL,
  cum5_ratio   REAL,
  source       TEXT NOT NULL,     -- 'uw'
  recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vix_pc_history_date
  ON vix_pc_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (97, datetime('now'));
```

---

## E. Ingestion  -  `scripts/fetch_vix_pc.py`

Model: `scripts/fetch_ivrank.py` (composed methods, stderr progress,
atomic JSON, unchanged fast path). No IB rung.

### E.1 Fetch

- `run(uw_fetch=None, *, now=None)` accepts an injected fetcher.
  Default fetcher calls UW `stock/VIX/options-volume?limit=500`.
- `parse_uw_rows(payload)` coerces `data[]` to
  `{date, put_volume, call_volume, source: "uw"}`. Skip malformed
  rows. Sort by date ascending. Dedup by date (last wins).
- Merge over `load_history()` (`SELECT date, put_volume, call_volume,
  source FROM vix_pc_history ORDER BY date`, paginate at 2000). Disk
  fallback `data/vix-pc.json` series when Turso is unreachable.
- Fetched volumes win on the same date.
- Recompute `daily_ratio` / `cum5_ratio` for every row.
- `rows_changed` = any date added or any put/call volume changed.
  Unchanged → snapshot + heartbeat only
  ("source unchanged; refreshing snapshot only").

UW has no Last-Modified. The unchanged path is content-equal, not HTTP
304. Tests stub the client and assert that path heartbeats without row
upserts.

### E.2 Write order (every cycle)

```python
writer.ensure_no_replica_for_writers()
if rows_changed:
    writer.upsert_vix_pc_rows(payload["series"], recorded_at=scan_time)
writer.upsert_scan_snapshot("vix-pc", scan_time, payload)
writer.record_service_health("vix-pc", "ok", finished_at=scan_time)
```

One `try/except` printing `[vix-pc] db cache non-fatal: {exc}` to
stderr. Atomic JSON fallback `data/vix-pc.json` (tmp + `os.replace`)
always. `upsert_vix_pc_rows` in `scripts/db/writer.py`: chunked
multi-row INSERT at `_PRICE_HISTORY_INSERT_CHUNK_ROWS`,
`ON CONFLICT(date) DO UPDATE`, never `executemany`. Column tuple
`(date, put_volume, call_volume, daily_ratio, cum5_ratio, source, recorded_at)`.

Both-feeds-down (UW raised) with a cached payload: reuse it with a
fresh `scan_time`, `status: "stale_source"`, error heartbeat. No cache
→ raise. Never cache empty.

### E.3 CLI

`--json` prints the payload to stdout. Progress to stderr. `--backfill`
is accepted and is the same UW `limit=500` call (that is the whole
history this feed serves).

### E.4 Payload shape

```jsonc
{
  "scan_time": "2026-10-18T02:00:12Z",
  "status": "ok",
  "source": "uw",
  "as_of": "2026-10-16",
  "data_date": "2026-10-16",
  "expected_session": "2026-10-16",
  "market_status": "closed",
  "window": 5,
  "count": 500,
  "cum5_count": 496,
  "current": {
    "date": "2026-10-16",
    "put_volume": 573686,
    "call_volume": 1729988,
    "daily_ratio": 0.33161270482801036,
    "cum5_ratio": 0.3188487844029556,
    "percentile": 4.032258064516129,
    "regime": "CAUTIOUS",
    "series_min": 0.2417297884911582,
    "series_max": 1.6973206456466932,
    "series_p10": 0.3435408949777816,
    "series_p90": 0.6760256318262985,
    "series_min_date": "2026-10-08",
    "is_series_low": false
  },
  "stats": {
    "min": 0.2417297884911582,
    "p10": 0.3435408949777816,
    "p25": 0.3943868581804748,
    "median": 0.48050480250544647,
    "p75": 0.5889207941175402,
    "p90": 0.6760256318262985,
    "max": 1.6973206456466932,
    "mean": 0.5057092921999375
  },
  "series": [
    {
      "date": "2024-10-18",
      "put_volume": 1,
      "call_volume": 1,
      "daily_ratio": 1.0,
      "cum5_ratio": null
    }
  ]
}
```

Every numeric field is `null` when unavailable. Never `NaN`.
`data_date` equals `as_of` (max series date) and feeds the freshness
rail `asOf`.

### E.5 Writer / watchdog / units

- `scripts/db/writer.py`: `upsert_vix_pc_rows`
- `scripts/watchdog/services.py`: window
  `{"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False}`
  plus the daily-bucket list, comment naming 02:00 UTC
- `cloud/services/radon-vix-pc.service`  -  copy `radon-ivrank.service`
  (`Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/etc/radon/env`, `Environment=RADON_DB_NO_REPLICA=1`,
  `Environment=RADON_UW_CALLER=vix-pc`, venv python directly,
  `TimeoutStartSec=300`)
- `cloud/services/radon-vix-pc.timer`  -  `OnCalendar=*-*-* 02:00:00 UTC`,
  `Persistent=true`, `RandomizedDelaySec=120`, comment the 02:00 UTC
  reasoning
- `cloud/scripts/setup-vps.sh` `SERVICE_FILES` append both units
- `cloud/config/installed-units.sha256` both hashes
- `cloud/tests/test_systemd_services.py` canonical set

---

## F. The API

### F.1 FastAPI: none

### F.2 Next.js  -  `web/app/api/vix-pc/route.ts`

Copy `web/app/api/ivrank/route.ts`.

```ts
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

const MISSING_VIX_PC = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  data_date: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
};

// radon-vix-pc.timer fires daily at 02:00 UTC including weekends, so a
// snapshot older than two days means the writer is down.
const VIX_PC_MAX_AGE_MS = 48 * 60 * 60_000;
```

`dbFirstRead({ fromDb, fromDisk, maxAgeMs: VIX_PC_MAX_AGE_MS, label: "vix-pc" })`.
`fromDb` selects `scan_snapshots` `WHERE service = 'vix-pc' ORDER BY
scan_time DESC LIMIT 1`. `fromDisk` reads `data/vix-pc.json`.
`setCacheResponseHeaders(..., { maxAgeSeconds: 300,
staleWhileRevalidateSeconds: 3600, tags: ["vix-pc"] })`. Never
transform. Never 4xx.

Also: `web/lib/vixPc.ts` types + formatters, `web/lib/useVixPc.ts`
(`useSyncHook({ endpoint: "/api/vix-pc", interval: 3_600_000,
hasPost: false, extractTimestamp: d => d.scan_time })`),
`web/lib/serviceHealthWindows.ts` entry:

```ts
// ``vix-pc``  -  radon-vix-pc.timer fires daily 02:00 UTC every calendar day
// (weekend runs are unchanged-data heartbeats), so a uniform 26h window
// matches its daily siblings. UW VIX options-volume only  -  no IB.
"vix-pc": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
```

`web/lib/refreshSchedule.ts`:

```ts
/** cloud/services/radon-vix-pc.timer */
export const VIX_PC_REFRESH: RefreshSchedule = [daily(2, 0)];
```

`web/tests/refresh-schedule.test.ts` PINNED row:
`["VIX P/C", VIX_PC_REFRESH, ["radon-vix-pc.timer"]]`.

`web/lib/assistant/nextLoaders.ts` static import for `"vix-pc"`.

---

## G. The UI

### G.1 Files

| File | Role |
|---|---|
| `web/lib/vixPc.ts` | types, formatters, regime helper, chart-row builder |
| `web/lib/useVixPc.ts` | hook |
| `web/components/VixPcPanel.tsx` | tab body |
| `web/app/regime/vix-pc/page.tsx` | WorkspaceShell page |
| `web/lib/regimeRail.ts` | `RegimeTab` union, Volatility group after `vixts`, `REGIME_TAB_LABEL["vix-pc"] = "VIX P/C"` |
| `web/components/RegimePanel.tsx` | `MOBILE_TAB_LABEL` if needed, `tabFromPathname` regex, mobile chip array, `if (activeTab === "vix-pc")` |

Place `vix-pc` in the `tabFromPathname` regex **before** any shorter
prefix it could collide with. There is no `vix` tab today; keep
`vixcor` / `vixts` as their full tokens.

### G.2 Panel

- Gate: `SpectralLoader` `label="Loading VIX put/call series"` while
  `(loading || syncing) && !data` → `SectionEmptyState` on
  `missing: true` (copy may say "the vix-pc refresh timer") → content.
- Strip (`RegimeStrip` desktop / `MetricCell` grid mobile):
  - `5S P/C` (`0.319`, 3dp, regime-colored) testId `vix-pc-cum5`
  - `PCTILE` (`4.0`) testId `vix-pc-percentile`
  - `REGIME` (`CAUTIOUS`) testId `vix-pc-regime`
  - `DAILY P/C` (`0.332`) testId `vix-pc-daily`
  - `SERIES LOW` (`0.242` on `2026-10-08`) testId `vix-pc-series-low`
  - Header clock / writer age from `scan_time` (same as vixts)
- Freshness rail immediately under the strip:

```tsx
<FreshnessRail
  schedule={VIX_PC_REFRESH}
  asOf={data.data_date ?? current.date}
  testId="vix-pc-freshness-rail"
  asOfTestId="vix-pc-strip-asof"
/>
```

  `As of <data_date>` plus a live `Next sample` countdown to the next
  02:00 UTC `OnCalendar` slot. Do not invent cadence copy.
- Chart: `CriHistoryChart`, title `VIX 5S PUT/CALL`, one series
  `cum5_ratio` on the right axis, `chartSeriesColor("primary")`.
  `HistoryRangeChips` + `BrushMinimap` (`testIdPrefix="vix-pc-brush"`),
  default preset `All` (daily series from Jan 2025). Horizontal markers
  for series min and the live p10 / p90 when those numbers exist.
- Brand tokens + `color-mix` only, 4px radius, no em dashes, no
  predictive verbs.
- Formatters: `formatRatio(v)` → 3dp (`"0.319"`), `formatPctile(v)` →
  1dp (`"4.0"`), both `"---"` for null/non-finite.

---

## H. Test pins (write first, red before implementation)

1. `scripts/tests/test_vix_pc.py`  -  fixture integrity; C.3 examples;
   C.4 boundaries; fixture calibration to 6dp; Oct 8 is the series min;
   Jan 2025 coverage; migration 97 schema + upsert idempotency + 400-row
   chunks; unchanged path heartbeats without row upserts; UW raise +
   cache → `stale_source`; no cache → raise; writer arity.
2. `web/tests/vix-pc-api.test.ts`  -  Turso beats older disk; disk
   fallback; exact `MISSING_VIX_PC` at 200; no cross-service leak;
   `dynamic === "force-dynamic"`; cache tag `vix-pc`.
3. `web/tests/vix-pc-panel.test.tsx`  -  loader label; empty state; strip
   values; freshness rail (`vix-pc-freshness-rail` / `vix-pc-strip-asof`
   / countdown `1h 30m` at 00:30 UTC); chart title; NaN guard; no em
   dash / cadence copy.
4. Lockstep: `regime-tab-routes` describe.each + render/navigation;
   `service-health-windows` entry; `refresh-schedule` PINNED row.
5. `web/e2e/vix-pc-tab.spec.ts`  -  route mocks + ambient, abort prices;
   active tab, rendered paths, brush, missing-state copy.

---

## I. File checklist

Create: `scripts/fetch_vix_pc.py`, `scripts/db/migrations/0097_vix_pc.sql`,
`scripts/tests/test_vix_pc.py`, `scripts/tests/fixtures/vix_pc_sample.json`,
`web/app/api/vix-pc/route.ts`, `web/lib/vixPc.ts`, `web/lib/useVixPc.ts`,
`web/components/VixPcPanel.tsx`, `web/app/regime/vix-pc/page.tsx`,
`web/tests/vix-pc-api.test.ts`, `web/tests/vix-pc-panel.test.tsx`,
`web/e2e/vix-pc-tab.spec.ts`, `cloud/services/radon-vix-pc.service`,
`cloud/services/radon-vix-pc.timer`.

Modify: `scripts/db/writer.py`, `scripts/watchdog/services.py`,
`cloud/scripts/setup-vps.sh`, `cloud/config/installed-units.sha256`,
`cloud/tests/test_systemd_services.py`, `web/lib/serviceHealthWindows.ts`,
`web/tests/service-health-windows.test.ts`, `web/lib/refreshSchedule.ts`,
`web/tests/refresh-schedule.test.ts`, `web/lib/assistant/nextLoaders.ts`,
`web/lib/regimeRail.ts`, `web/components/RegimePanel.tsx`,
`web/tests/regime-tab-routes.test.tsx`.
