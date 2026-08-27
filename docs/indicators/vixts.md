# VIX TS — VIX / VIX3M Term-Structure Ratio

**Status:** authoritative build spec. Implementers follow this literally.
Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementation to copy throughout: `scripts/fetch_straddle.py` (multi-file
Cboe conditional GET) and `scripts/fetch_vixcor.py` (`_write_db` error isolation,
`parse_index_csv`). Panel/API/route shape: the hhlev and vixcor tabs.

---

## 0. What this is

The ratio of spot VIX (30-day implied volatility) to VIX3M (3-month implied
volatility). It measures the **slope of the volatility term structure**:

- **Below 1.00** the curve is in **contango** — near-term vol is priced below
  3-month vol. The ordinary state of a calm tape.
- **Above 1.00** the curve is in **backwardation** — near-term vol is bid above
  3-month vol. Stress has moved into the front of the curve.

This is a descriptive regime read. **No forward-return claim is made anywhere in
the copy.** No validation study was run for this indicator, so predictive framing
is not permitted (same posture as `ivrank`; contrast `vixcor`, which ran one).

The reference reading this tab was built to reproduce: **2026-08-25, VIX 15.45 /
VIX3M 18.21 = 0.8484**, which displays as **0.85**, with SPX at **7677.28**. Both
legs were verified against the live Cboe files during Step-1 research.

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `vixts` |
| Route | `/regime/vixts` |
| Page file | `web/app/regime/vixts/page.tsx` |
| Tab label (desktop + mobile) | `VIX TS` |
| Display name | VIX Term Structure |
| API route | `/api/vixts` |
| `service_health` key | `vixts` |
| systemd units | `radon-vixts.service`, `radon-vixts.timer` |
| Turso table | `vixts_history` |
| Snapshot service | `scan_snapshots.service = 'vixts'` |
| Disk fallback | `data/vixts.json` |
| Rail group | **Volatility** |
| Migration | `0058_vixts.sql` (version 58) |

`MAX(version)` in production `schema_migrations` was confirmed to be **57** during
Step 1, and `git log --all` shows no `0058` on any branch, so **58 is free**. Only
the ingestion implementer owns the migration; the other two must not create one.

**One-line description** (InfoTooltip first sentence):

> The ratio of spot VIX to 3-month VIX3M, which is the slope of the volatility
> term structure. Below 1.00 the curve is in contango and near-term volatility is
> priced below 3-month volatility. Above 1.00 it is in backwardation, which means
> stress has moved into the near term.

No em dashes in user-facing copy. Hyphens only.

---

## B. Source

### B.1 Confirmed facts (Step-1 evidence, 2026-08-26/27)

| | VIX | VIX3M | SPX |
|---|---|---|---|
| URL | `{base}/VIX_History.csv` | `{base}/VIX3M_History.csv` | `{base}/SPX_History.csv` |
| HTTP with `radon/2.0` | 200 | 200 | 200 |
| Header line | `DATE,OPEN,HIGH,LOW,CLOSE` | `DATE,OPEN,HIGH,LOW,CLOSE` | `DATE,SPX` |
| **Value column** | `CLOSE` | `CLOSE` | **`SPX`** |
| Date format | `MM/DD/YYYY` | `MM/DD/YYYY` | `MM/DD/YYYY` |
| Data rows | 9,259 | 4,260 | 1975+ |
| History start | 1990-01-02 | **2009-09-18** | 1975 |
| `Last-Modified` | served | served | served |
| `ETag` | served | served | served |

`{base}` = `https://cdn.cboe.com/api/global/us_indices/daily_prices`, overridable
via `CBOE_DAILY_PRICES_BASE_URL` (this is how tests stub it).

**The effective history floor is VIX3M's 2009-09-18.** The joined series is the
date intersection, so the chart starts there.

Conditional GET verified both directions:

```
If-Modified-Since: Thu, 27 Aug 2026 01:50:46 GMT  -> 304, 0 bytes  (VIX)
If-None-Match: "08b56d559cbf4e4e6dde2f71331ef997" -> 304, 0 bytes  (VIX3M)
```

`CboeClient` sends `If-Modified-Since` only. That is sufficient and already proven
in production by `cor`, `straddle`, and `vixcor`.

### B.2 Traps

1. **`VXV_History.csv` is a dead stub.** It returns HTTP 200 (492 bytes) so a naive
   reachability check passes, but its header is `DATE,VXV`, it has 23 rows, and it
   **ends 10/19/2017** while still advertising a recent `Last-Modified`. VIX3M's
   old ticker was VXV; do not use that file. `VIX3M_History.csv` is the only
   current source.
2. **VIX3M is not listed on Cboe's public historical-data page.** The file exists,
   is current, and is same-family, but it is undocumented. This is a mild
   durability risk and is precisely why §E.3's plausibility guard must raise
   loudly rather than latch `ok` if the file ever goes stale or truncates.
3. **A 304 does not mean "no new session."** Cboe re-touches `Last-Modified`
   intraday without appending the session row. All downstream logic keys off
   **dates parsed from rows**, never off the stamp. Documented in `CboeClient`'s
   own docstring and in `docs/indicators/vixcor.md`.
4. **`SPX_History.csv`'s value column is `SPX`, not `CLOSE`** (same quirk as
   `VVIX_History.csv`). `parse_index_csv` must take the column as a parameter, as
   `fetch_straddle.py:60` already does.
5. **Do not build fixtures with shell `head`/`tail` redirects in this
   environment.** The `rtk` hook rewrites those commands and corrupted a fixture's
   header line during Step 1. Use Python.

### B.3 Data-source priority

**Ladder: Cboe CDN only. No IB rung, no UW rung, Yahoo not used.**

This does not violate the Yahoo-last rule. The repo rule explicitly permits
official feeds (Cboe, Treasury, FINRA) ahead of Yahoo when a script documents them
as the source for that metric, and there is three-indicator precedent on this exact
CDN (`cor`, `straddle`, `vixcor`).

- **IB cannot serve this cleanly.** `VIX3M` is absent from the canonical
  `secType=IND` map in `scripts/utils/index_symbols.py`, so it would need a new
  registration plus its mirror in `web/lib/indexSymbols.ts` (pinned by
  `scripts/tests/test_index_symbols_sync.py`). More decisively,
  `feedback_ib_cor1m_lags_official_close` records that IB daily bars and IB tick-9
  closes both lag the official Cboe close by a session, and
  `docs/indicators/vixcor.md` states the rule flatly: *anchor to the official Cboe
  history, never to IB*. IB also requires an authenticated 2FA Gateway for a
  once-daily series and carries no 2009-depth history for VIX3M.
- **UW has nothing here.** `/api/stock/{ticker}/volatility/term-structure` is
  per-ticker option IV by expiry, not the VIX/VIX3M index ratio. UW has no
  index-history surface at all. **Do not add a UW rung.**

**Because there is no fallback rung, the plausibility guard in §E.3 is the only
protection against a silently bad source. Spec it and test it.**

### B.4 SPX overlay

Use **Cboe `SPX_History.csv` through the same `CboeClient`**, exactly as
`fetch_straddle.py` already does.

The alternative in-repo pattern is `fetch_hyad.py::_spx_by_date()`, which reads
Turso `credit_spread_history.spx_close`. **Rejected here on evidence:** that table
currently holds only **254 rows** in production, so it cannot span VIX3M's
2009-09-18 start. The Cboe file covers 1975+, is self-contained, adds no dependency
on another indicator's writer having run, and its 2026-08-25 value reproduces the
reference SPX of **7677.28** exactly.

### B.5 Licensing

**Verdict: permitted for Radon's internal single-operator dashboard, on
established in-repo precedent. Never publicly re-serve the raw series.**

Stated plainly rather than glossed: Cboe's formal terms are stricter than practice.
`https://www.cboe.com/use-of-content/` says approval in advance is required to use
Cboe Content, and the historical-data page carries a disclaimer and a copyright
notice but **no explicit public grant** covering storage or derived works. On a
strict reading this is unclear.

The operative position is the one Radon already adopted, reviewed three times, for
the same files on the same CDN:

- `docs/indicators/cor.md` — "Internal indicator use OK; never re-serve raw history publicly."
- `docs/indicators/straddle.md` — "OK for internal single-operator dashboard storage + display. Do not publicly re-serve the raw series."
- `docs/external-services.md` registers Cboe as a sanctioned public CSV feed.

`vixts` takes the identical posture. `/api/vixts` serves the **computed ratio
series** behind the authenticated perimeter. Do not add an endpoint that returns
raw VIX/VIX3M history.

---

## C. The math

### C.1 Named constants (module level, `scripts/lib/vixts_math.py`)

```python
BACKWARDATION_THRESHOLD = 1.00   # ratio >= this: front-month bid over 3-month
FLAT_THRESHOLD          = 0.95   # [0.95, 1.00): curve flattening toward the flip
STEEP_CONTANGO_THRESHOLD = 0.80  # ratio < this: rare complacency extreme

MIN_SERIES_ROWS = 2000   # plausibility floor; the real join is ~4,200 rows
RATIO_SANITY_MIN = 0.40  # any ratio outside [min, max] means a corrupt source
RATIO_SANITY_MAX = 2.50
```

Mirrored as TS display-copy constants in `web/lib/vixts.ts`. **The UI never
recomputes the ratio or the regime** — it renders what the payload carries.

### C.2 Formula

For each date present in **both** VIX and VIX3M:

```
ratio(d) = vix_close(d) / vix3m_close(d)
```

Guards:

- `vix3m_close <= 0` -> skip the row entirely (never divide by zero or negative).
- A date present in only one file is **not** emitted. The series is the inner join.
- `spx_close` is a **left** join: absent SPX for a date emits `None`, and the row
  still appears. The chart tolerates null overlay points.
- Series is ascending by date.

### C.3 Regime classification (strict inequalities, pinned by test)

```python
def classify_regime(ratio: float) -> str:
    if ratio >= BACKWARDATION_THRESHOLD:   return "BACKWARDATION"
    if ratio >= FLAT_THRESHOLD:            return "FLAT"
    if ratio >= STEEP_CONTANGO_THRESHOLD:  return "CONTANGO"
    return "STEEP CONTANGO"
```

**Boundary cases that must be pinned exactly** (SKILL.md §8 requires strict
inequalities be tested at the boundary):

| ratio | regime |
|---|---|
| `1.0000` | `BACKWARDATION` |
| `0.9999` | `FLAT` |
| `0.9500` | `FLAT` |
| `0.9499` | `CONTANGO` |
| `0.8000` | `CONTANGO` |
| `0.7999` | `STEEP CONTANGO` |

### C.4 Why these band edges

Derived from the captured fixtures, not asserted. Over the 778 common sessions in
the fixture window (2023-07-21 -> 2026-08-26):

| band | days | share |
|---|---|---|
| `< 0.80` STEEP CONTANGO | 14 | 1.80% |
| `[0.80, 0.95)` CONTANGO | 624 | 80.21% |
| `[0.95, 1.00)` FLAT | 92 | 11.83% |
| `>= 1.00` BACKWARDATION | 48 | 6.17% |

min 0.7580, max 1.2739, mean 0.8939, median 0.8783.

Full history (2009-09-18 -> 2026-08-26, n=4,260): min 0.7104, max 1.3437, mean
0.8943, median 0.8844, days above 1.00 = 324 (7.61%).

**On the reference chart's guide lines:** its 1.00 line is a genuine tail at
roughly 6 to 8 percent of sessions and is adopted as-is. Its 0.80 line captures
only 1.8 percent of the last three years and the three-year minimum is 0.7580, so
that band is nearly empty; it is kept as a **rare-extreme marker** rather than a
routine signal, and the intermediate 0.95 edge was added because it isolates a
meaningful ~12 percent zone that is the actual approach to the flip.

### C.5 Worked examples (pin exactly, derived from fixtures)

```
2026-08-26:  vix=15.21  vix3m=17.99  ratio=15.21/17.99 = 0.845469...  -> 0.8455  CONTANGO
2026-08-25:  vix=15.45  vix3m=18.21  ratio=15.45/18.21 = 0.848435...  -> 0.8484  CONTANGO
2026-08-24:  vix=15.85  vix3m=18.56  ratio=15.85/18.56 = 0.853987...  -> 0.8540  CONTANGO
```

The 2026-08-25 value displays as **0.85**, matching the reference chart.

Tests assert against these fixture-derived values with a tolerance, never against
mentally computed numbers.

### C.6 Stats block

```python
{
  "min": float, "max": float, "mean": float, "median": float,
  "days_backwardation": int,        # count of ratio >= 1.00 over the whole series
  "pct_backwardation": float,       # that count / len(series) * 100
  "last_backwardation_date": str | None,   # most recent date with ratio >= 1.00
}
```

`last_backwardation_date` is `None` when the series never crossed. Computed over
the **full** series, not the charted slice.

---

## D. Payload contract

```jsonc
{
  "scan_time": "2026-08-27T02:45:11Z",         // tz-aware UTC ISO, Z-suffixed
  "source_last_modified": {                     // per-file, lowercase keys
    "vix":   "Thu, 27 Aug 2026 01:50:46 GMT",
    "vix3m": "Wed, 26 Aug 2026 22:00:57 GMT",
    "spx":   "Thu, 27 Aug 2026 00:31:07 GMT"
  },
  "data_date": "2026-08-26",                    // latest series date
  "count": 4260,
  "current": {
    "date": "2026-08-26",
    "vix": 15.21,
    "vix3m": 17.99,
    "ratio": 0.8455,
    "regime": "CONTANGO",
    "spx": 7654.32
  },
  "stats": { /* C.6 */ },
  "series": [
    { "date": "2009-09-18", "vix": 23.92, "vix3m": 26.01, "ratio": 0.9197, "spx": 1068.30 }
  ]
}
```

`ratio` is rounded to **4 decimal places** at build time. `vix`, `vix3m`, `spx`
carry the source's own precision. `current` is `null` only when `series` is empty,
which the §E.3 guard makes an exception rather than a served state.

---

## E. Ingestion job — `scripts/fetch_vixts.py`

### E.1 Shape

Copy `scripts/fetch_straddle.py`'s `run()` structure, which already does
multi-file Cboe conditional GET, and `scripts/fetch_vixcor.py`'s `_write_db` for
error isolation. Pure math goes in `scripts/lib/vixts_math.py` so pytest covers it
without network.

```python
_SYMBOLS = ("VIX", "VIX3M", "SPX")
_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "SPX": "SPX"}
SERVICE = "vixts"
VIXTS_JSON = _PROJECT_DIR / "data" / "vixts.json"

# Mirrors radon-vixts.timer (OnCalendar=*-*-* 02:45:00 UTC) so heartbeat copy can
# name the next attempt instead of hardcoding cadence text.
TIMER_HOUR_UTC = 2
TIMER_MINUTE_UTC = 45
```

`run(client=None, *, now=None) -> dict` so tests inject a stub client and a fixed
clock.

### E.2 Conditional-GET flow

1. Read `data/vixts.json`; take `source_last_modified` as the per-symbol stamps.
2. For each of the three symbols, `client.fetch_history(symbol,
   if_modified_since=cached_stamps.get(symbol.lower()))`.
3. **If a cached payload exists and all three return `None` (304):** reuse the
   cached payload with a fresh `scan_time`, log
   `"[vixts] all sources unchanged (304); refreshing snapshot only"` to stderr,
   call `_write_db(..., rows_changed=False)`, rewrite the JSON, return. **No row
   upserts.**
4. Otherwise re-fetch unconditionally any symbol that returned `None`, so the
   rebuild always has all three texts.
5. Parse, join, compute, guard, build payload, `_write_db(..., rows_changed=True)`,
   write JSON.

### E.3 Plausibility guard (mandatory — the only protection)

Before any write, `ensure_plausible_series(series)` **raises** `ValueError` when:

- `len(series) < MIN_SERIES_ROWS`
- the latest `ratio` is outside `[RATIO_SANITY_MIN, RATIO_SANITY_MAX]`
- any row carries a non-positive `vix3m`

Raising keeps the run retryable and writes an `error` heartbeat. **Never latch
`service_health` `ok` over an unverified series, and never cache or mirror an
empty result** (`feedback_dont_cache_empty_results`).

### E.4 Writes, in order, every cycle

1. `writer.ensure_no_replica_for_writers()`
2. `writer.upsert_vixts_rows(payload["series"], recorded_at=scan_time)` — **only
   when `rows_changed`**. Must be the **chunked** form modelled on
   `upsert_hhlev_rows` (~4,200 rows; per-row statements caused the rv-ratio
   2026-07-21 Hrana 502).
3. `writer.upsert_scan_snapshot("vixts", scan_time, payload)` — every cycle
4. `writer.record_service_health("vixts", "ok"|"error", finished_at=scan_time,
   error=...)` — every cycle, or error rows latch
5. Atomic write of `data/vixts.json` — fallback only; **Turso is the source of
   truth**

`_write_db` reproduces `fetch_vixcor.py`'s R-192 isolation exactly: a failed row
upsert must not take the snapshot and heartbeat down with it, and must surface as
an `error` heartbeat rather than a silent exit 0.

### E.5 CLI

`--json` prints the payload to stdout; all progress and summary output goes to
**stderr** (subprocess contract). `--backfill` is not needed: the source files are
full history every fetch, so every changed run re-upserts the whole series
idempotently.

---

## F. Storage — `scripts/db/migrations/0058_vixts.sql`

```sql
-- VIX TS indicator: daily VIX / VIX3M term-structure ratio from the Cboe CDN.
-- ratio is stored because it is a pure per-row function of the two closes and
-- stays idempotent under re-upsert; no rolling or cumulative statistic is ever
-- stored (that would make rows order-dependent). spx_close is a nullable left
-- join used only for the chart overlay.
CREATE TABLE IF NOT EXISTS vixts_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    vix3m_close REAL NOT NULL,
    ratio REAL NOT NULL,
    spx_close REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vixts_history_date_desc ON vixts_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (58, datetime('now'));
```

Writer: `upsert_vixts_rows(rows, recorded_at=None)`, chunked,
`INSERT ... ON CONFLICT(date) DO UPDATE SET ...`, idempotent per date.

---

## G. API — `web/app/api/vixts/route.ts`

Copy `web/app/api/hhlev/route.ts` verbatim in shape.

- `export const dynamic = "force-dynamic"; export const runtime = "nodejs";` GET only.
- `dbFirstRead({ fromDb, fromDisk, maxAgeMs: VIXTS_MAX_AGE_MS, label: "vixts" })`.
- `fromDb`: `SELECT scan_time, payload FROM scan_snapshots WHERE service = 'vixts' ORDER BY scan_time DESC LIMIT 1`.
- `fromDisk`: `data/vixts.json`.
- **`VIXTS_MAX_AGE_MS = 48 * 60 * 60_000`**, commented: the timer runs daily at
  02:45 UTC every calendar day, so a snapshot older than 48h means the writer is
  down, not that the data is merely stale over a weekend.
- **Missing contract**: HTTP **200** with a frozen object, never a 4xx:
  ```ts
  const MISSING_VIXTS = Object.freeze({
    missing: true, scan_time: null, source_last_modified: null,
    data_date: null, current: null, stats: null, series: [],
  });
  ```
- `setCacheResponseHeaders(response, { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, requestId, cacheState: "HIT", tags: ["vixts"] })`.

Hook `web/lib/useVixTs.ts`:

```ts
const VIXTS_SYNC_CONFIG = {
  endpoint: "/api/vixts",
  interval: 3_600_000,          // hourly poll of a daily series
  hasPost: false,
  extractTimestamp: (d: VixTsData) => d.scan_time || null,
};
export function useVixTs(): UseSyncReturn<VixTsData> {
  return useSyncHook<VixTsData>(VIXTS_SYNC_CONFIG, true);
}
```

`web/lib/vixts.ts` holds the types, the threshold display constants, formatters
(`formatRatio` -> 4dp, `formatIndex` -> 2dp), and `SOURCE_FOOTNOTE`. It must not
contain ratio or regime math.

---

## H. UI — `web/components/VixTsPanel.tsx`

Mirror `HhlevPanel.tsx` structure exactly.

**Gate order, strictly:**

1. `(loading || syncing) && !data` -> `<SpectralLoader label="Loading VIX term structure series" />`
2. `!data || data.missing || !data.current` -> `<SectionEmptyState ... />`
3. content

**Strip — five cells**, every value wrapped in a `data-testid` span:

| testId | label | value | sub |
|---|---|---|---|
| `vixts-ratio` | `RATIO` | `0.8455` | `VIX / VIX3M` |
| `vixts-regime` | `REGIME` | `CONTANGO` | band edge context |
| `vixts-vix` | `VIX` | `15.21` | 30-day |
| `vixts-vix3m` | `VIX 3M` | `17.99` | 3-month |
| `vixts-source-updated` | `SOURCE UPDATED` | `2026-08-26` | latest session in the file |

Regime tone: `BACKWARDATION` -> `var(--negative)`, `FLAT` -> `var(--warning)`,
`CONTANGO` -> `var(--text-muted)`, `STEEP CONTANGO` -> `var(--positive)`.
Brand tokens only, no raw hex, 4px max radius.

`compact` (from `useViewport()`, `hasMounted && isMobile`) switches to the
`m-regime-grid2x2` `MetricCell` layout.

**Chart:** `CriHistoryChart`, title `VIX TERM STRUCTURE — VIX / VIX3M`, a fixed
2-tuple of `ChartSeries`:

- **left axis**: SPX, `chartSeriesColor("primary")`, `scaleType: "log"` (multi-decade price)
- **right axis**: the ratio

Preceded by `<HistoryRangeChips>`; followed by `<BrushMinimap values range
onRangeChange onCustom testIdPrefix="vixts-brush" ariaLabel=... />` when
`total >= 2`, then the source footnote. Default preset **`all`** (a long daily
series back to 2009).

**Freshness copy is derived, never asserted.** The header clock renders
`data.scan_time`; the `SOURCE UPDATED` cell renders `data.data_date`. **No string
anywhere may claim a cadence** ("Refreshes daily", "Updated hourly", "5m"). Grep
the new strings for `Refresh|Updated|hourly|daily|5m` before shipping and check
each against the real `OnCalendar`.

`InfoTooltip` copy: definition -> source and what it measures -> the band edges
with their observed frequencies -> `Source: CBOE`. No em dashes.

---

## I. Scheduling

**`cloud/services/radon-vixts.service`**

```ini
[Unit]
Description=Radon VIX TS Indicator (Cboe VIX/VIX3M term-structure pull, daily)
After=network.target

StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=oneshot
User=radon
WorkingDirectory=/home/radon/radon
EnvironmentFile=/etc/radon/env
Environment=RADON_DB_NO_REPLICA=1
ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_vixts.py
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

Note `EnvironmentFile=/etc/radon/env` — the real units use this path. SKILL.md §6
still says `/home/radon/radon-cloud/.env` and is **stale**; copy the unit file, not
the skill text.

**`cloud/services/radon-vixts.timer`**

```ini
[Unit]
Description=Radon VIX TS Indicator - daily 02:45 UTC Cboe VIX/VIX3M pull

[Timer]
# Cboe stamps the session row late: observed Last-Modified was 01:50 UTC for
# VIX and 22:00 UTC for VIX3M on 2026-08-26. 02:45 clears both the EDT
# (00:00 UTC) and EST (01:00 UTC) publication windows with margin, and sits in
# the free slot after straddle 02:15, cor 02:20 and vixcor 02:35 so the Cboe
# CDN hits stay staggered. Runs every calendar day: weekend and holiday runs
# are 304 heartbeats that keep service_health inside the 26h window.
OnCalendar=*-*-* 02:45:00 UTC
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

Registration: append both to `cloud/scripts/setup-vps.sh` `SERVICE_FILES` (before
the closing `)`), append both `sha256  name` lines (**exactly two spaces**) to
`cloud/config/installed-units.sha256`, and add both to
`cloud/tests/test_systemd_services.py`. A unit absent from the manifest is
**never installed**.

---

## J. Service-health registration (both sides, same commit)

**`web/lib/serviceHealthWindows.ts`** — insert after the `hhlev` entry in the daily
block, with a comment in the `vixcor` style:

```ts
"vixts": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
```

Do **not** add it to `RTH_ONLY_SERVICES` (that is for 5-minute RTH writers).

**`scripts/watchdog/services.py`** — **two** registrations, windows in **seconds**:

1. `SCHEDULED_SERVICES["vixts"] = {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False}`
2. append `"vixts"` to the `"daily"` bucket list with a two-line comment naming the timer and cadence

`scripts/tests/test_watchdog/test_services.py` regex-parses the TS file and asserts
set-equality with `SCHEDULED_SERVICES.keys()`. A one-sided edit fails CI.

A brand-new timer has no `service_health` row until its first fire; no-row-ever is
dormant, not a page (`feedback_watchdog_dormant_no_row`).

---

## K. Registration checklist (the pattern skill is stale here)

SKILL.md §37 tells you to edit `REGIME_TAB_VALUES` in `RegimePanel.tsx`. **That
constant does not exist.** A rail refactor moved tab registration into
`web/lib/regimeRail.ts`. There are **seven** edits across two files.

**`web/lib/regimeRail.ts`** (3):

1. append `| "vixts"` to the `RegimeTab` union
2. add `"vixts"` to the **Volatility** group in `REGIME_RAIL_GROUPS`
3. add `vixts: "VIX TS",` to `REGIME_TAB_LABEL`

`REGIME_TABS` is derived from the groups — no edit, and the desktop button row
maps over it, so **the desktop row needs no edit either**.

**`web/components/RegimePanel.tsx`** (4):

4. add `vixts: "VIX TS",` to `MOBILE_TAB_LABEL`
5. add `vixts` to the `tabFromPathname` regex alternation (no prefix collision with
   `vixcor`, but keep the longest-prefix-first discipline the comment mandates)
6. add `"vixts"` to the **inline, hand-maintained** mobile chip array
7. add `if (activeTab === "vixts") { return renderShell(<VixTsPanel />); }` plus the import

---

## L. Lockstep test pins (same commit, or CI fails)

| File | Edit |
|---|---|
| `web/tests/route-local-authz-matrix.test.ts` | add `"vixts"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES`. A new API route is UNCLASSIFIED and fails until listed. |
| `web/tests/service-health-windows.test.ts` | the `expected` set is exhaustive; add `vixts` |
| `web/tests/regime-tab-routes.test.tsx` | 4 edits: `describe.each` table, panel stub mock, render case, nav case |
| `web/tests/regime-rail.test.tsx` | 3 edits: exhaustive sorted tab-key array, `groupOf`, `REGIME_TAB_LABEL` |
| `cloud/tests/test_systemd_services.py` | both unit names |
| `docs/indicators/README.md` | append the `vixts` row |
| `docs/cloud-services.md` | append a `### VIX TS (radon-vixts.timer)` section |

---

## M. Tests (red first)

**`scripts/tests/test_vixts.py`** — parses the checked-in fixtures
(`scripts/tests/fixtures/vixts_vix_sample.csv`, `vixts_vix3m_sample.csv`) at
import; expected values derived by inspecting fixtures, never mental arithmetic:

- `parse_index_csv` handles `CLOSE` and the `SPX` column, skips malformed rows
- the join is an inner join on date; SPX is a left join emitting `None`
- `classify_regime` boundary table from §C.3, all six rows
- worked examples from §C.5 to 4dp
- stats block correctness including `last_backwardation_date is None` on a
  never-crossed series
- `ensure_plausible_series` raises on: too few rows, out-of-band latest ratio,
  non-positive `vix3m`
- conditional-GET stub client: all-304 refreshes snapshot + heartbeat with **no**
  row upsert; a changed file rebuilds and **does** upsert
- `_write_db` isolation: a raising `upsert_vixts_rows` still writes the snapshot
  and records an `error` heartbeat
- migration executed into in-memory sqlite pins schema, version 58, and upsert
  idempotency
- window-relative dates for anything freshness-related

**`web/tests/vixts-api.test.ts`** (`@vitest-environment node`) — mock `@/lib/db`
with a real in-memory `@libsql/client` seeded from the migration: Turso beats older
disk, disk fallback works, the exact `missing:true` object at **200**, no
cross-service snapshot leak, `route.dynamic === "force-dynamic"`.

**`web/tests/vixts-panel.test.tsx`** (`@vitest-environment jsdom`) — stub
`ResizeObserver`, `vi.mock` the hook, factory fixtures `buildSeries(n)` /
`hookState()`: loader label, empty state, all five strip values, chart title,
chips/brush, regime tone, and a **NaN guard** (no `<path d>` contains `"NaN"`).

**`web/e2e/vixts-tab.spec.ts`** — `page.route` mocks for `**/api/vixts` plus the
ambient routes (`portfolio`, `orders`, `ib-status`), abort `**/api/prices`; assert
active tab, rendered stroked paths, brush visible, missing-state copy.

---

## N. Worktree ownership

| Branch | Owns |
|---|---|
| `ind/vixts-ingestion` | `scripts/lib/vixts_math.py`, `scripts/fetch_vixts.py`, `scripts/db/migrations/0058_vixts.sql`, `scripts/db/writer.py`, `scripts/watchdog/services.py`, `cloud/services/radon-vixts.{service,timer}`, `cloud/scripts/setup-vps.sh`, `cloud/config/installed-units.sha256`, `cloud/tests/test_systemd_services.py`, `scripts/tests/test_vixts.py` |
| `ind/vixts-api` | `web/app/api/vixts/route.ts`, `web/lib/vixts.ts`, `web/lib/useVixTs.ts`, `web/lib/serviceHealthWindows.ts`, `web/tests/vixts-api.test.ts`, `web/tests/service-health-windows.test.ts`, `web/tests/route-local-authz-matrix.test.ts` |
| `ind/vixts-ui` | `web/components/VixTsPanel.tsx`, `web/lib/regimeRail.ts`, `web/components/RegimePanel.tsx`, `web/app/regime/vixts/page.tsx`, `web/tests/vixts-panel.test.tsx`, `web/tests/regime-tab-routes.test.tsx`, `web/tests/regime-rail.test.tsx`, `web/e2e/vixts-tab.spec.ts` |

Ownership is disjoint by file. The UI implementer mocks the hook in unit tests, so
it does not need the API worktree's files to go green; TypeScript integration is
checked after the merge.
