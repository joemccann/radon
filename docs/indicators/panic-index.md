# PANIC — Radon Panic Proxy (Goldman S&T "Panic Index" reconstruction)

**Status:** shipped (draft PR). Implementers followed this literally; leave
the PR draft, never merge it yourself.
Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementation to copy throughout: `scripts/fetch_vixts.py` (multi-file
Cboe conditional GET, `_write_db` isolation, freshness verdict, `parse_index_csv`)
and `scripts/lib/vixts_math.py` / `scripts/lib/dispersion_math.py` (pure math,
stdlib only, no numpy). Panel / API / route shape: the vixts tab.

---

## 0. What this is, and what it is not

Goldman's S&T desk publishes a proprietary "Panic Index" and described its
inputs as **S&P vol, VIX vol, S&P term structure, and S&P skew**. The formula is
not public. This tab is **Radon's best-effort proxy built from those four stated
inputs using public Cboe series**. It is not Goldman's index, does not use
Goldman data, and its level is on Radon's own scale.

Every user-facing surface says so. The display name is **Panic Proxy**, the
InfoTooltip opens with "Radon Panic Proxy - not Goldman's index", and the source
footnote names the four Cboe series. No copy may imply parity with the GS number.

Source quote the build reproduces in spirit (ZH, GS S&T):

> "Yesterday, our panic index saw its largest 1d decline in the past 10 years and
> ranks as the third largest 1-day decline in our dataset. This metric involves
> S&P vol, VIX vol, S&P term structure, and S&P skew."

Chart described: "1d Change in Panic Index", ~10y span, high 5.77, low/last
-3.64, avg ~0, stddev 0.93. The **1-day change** is the headline series; the
level is context. This tab charts the change by default and the level on a
toggle.

This is a descriptive regime read. **No forward-return claim is made anywhere in
the copy.** No validation study was run.

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `panic-index` |
| Route | `/regime/panic-index` |
| Page file | `web/app/regime/panic-index/page.tsx` |
| Tab label (desktop + mobile) | `PANIC` |
| Display name | Panic Proxy |
| API route | `/api/panic-index` |
| `service_health` key | `panic-index` |
| systemd units | `radon-panic-index.service`, `radon-panic-index.timer` |
| Turso table | `panic_index_history` |
| Snapshot service | `scan_snapshots.service = 'panic-index'` |
| Disk fallback | `data/panic_index.json` |
| Rail group | **Volatility** (after `vixts`) |
| Math module | `scripts/lib/panic_index_math.py` |
| Fetcher | `scripts/fetch_panic_index.py` |
| Migration | `0077_panic_index.sql` (version 77) — **verify free** before use |

Migration number: `0076_calm_streak.sql` is the highest on `main` at spec time.
Before creating `0077`, run `git log --all --diff-filter=A --name-only -- 'scripts/db/migrations/0077_*'`
and confirm production `SELECT MAX(version) FROM schema_migrations` is 76.
`migrate.py` refuses duplicate versions, so a collision fails the deploy, not
silently.

**One-line description** (InfoTooltip first sentence):

> Radon Panic Proxy - not Goldman's index. An equal-weight average of the
> 252-session z-scores of four Cboe series that Goldman names as the inputs to
> its Panic Index: VIX (S&P vol), VVIX (VIX vol), VIX/VIX3M (S&P term structure)
> and the Cboe SKEW index (S&P skew). Higher is more panic. The chart plots the
> one-day change: a large negative print is a panic unwind, a large positive
> print is a panic surge.

No em dashes in user-facing copy. Hyphens only.

---

## B. Source

### B.1 Confirmed facts (live probe 2026-09-18 21:1x UTC, `curl -A radon/2.0`)

| | VIX | VIX3M | VVIX | SKEW |
|---|---|---|---|---|
| URL | `{base}/VIX_History.csv` | `{base}/VIX3M_History.csv` | `{base}/VVIX_History.csv` | `{base}/SKEW_History.csv` |
| HTTP | 200 | 200 | 200 | 200 |
| Header | `DATE,OPEN,HIGH,LOW,CLOSE` | `DATE,OPEN,HIGH,LOW,CLOSE` | **`DATE,VVIX`** | **`DATE,SKEW`** |
| Value column | `CLOSE` | `CLOSE` | **`VVIX`** | **`SKEW`** |
| Date format | `MM/DD/YYYY` | `MM/DD/YYYY` | `MM/DD/YYYY` | `MM/DD/YYYY` |
| Data rows | 9,275 | 4,275 | 5,106 | 9,229 |
| History start | 1990-01-02 | **2009-09-18** | 2006-03-06 | 1990-01-02 |
| Last row | 2026-09-17 | 2026-09-17 | 2026-09-17 | 2026-09-17 |
| `Last-Modified` | 18 Sep 01:51 UTC | 18 Sep 01:51 UTC | 18 Sep 12:01 UTC | 18 Sep 21:01 UTC |
| Bytes | 472,768 | 218,050 | 108,667 | 203,048 |

`{base}` = `https://cdn.cboe.com/api/global/us_indices/daily_prices`, overridable
via `CBOE_DAILY_PRICES_BASE_URL` (tests stub it). `CboeClient` sends
`If-Modified-Since`; conditional GET is proven in production by `cor`,
`straddle`, `vixcor`, `vixts`.

**The VVIX gap in the outer-loop hypothesis does not exist.** `VVIX_History.csv`
is live on the same CDN with 20 years of history. No proxy (realized vol of VIX)
is needed; do not build one.

**The effective history floor is VIX3M's 2009-09-18.** The joined series is the
four-way date intersection: **4,268 rows** on 2026-09-17. Seven sessions present
in VIX and VIX3M are absent from VVIX or SKEW and are dropped by the inner join
(`2010-11-11, 2011-02-09, 2013-05-13, 2017-09-14, 2018-12-03, 2019-07-05,
2024-11-29`). With a 252-session warm-up the first composite value lands on
**2010-09-20** and the first 1d change on **2010-09-21**; the Δ1d series holds
**4,016 rows** at spec time, of which **2,509** fall inside the trailing 10 years.

### B.2 Traps

1. **Value columns differ per file.** `parse_index_csv(text, value_column)` from
   `fetch_vixts.py` already takes the column as a parameter; reuse it, do not
   fork it. `_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "VVIX": "VVIX", "SKEW": "SKEW"}`.
2. **`Last-Modified` is re-touched intraday without a new row** (VVIX 12:01 UTC,
   SKEW 21:01 UTC on 2026-09-18, both still ending 09/17). A 200 is not a new
   session; all freshness logic keys off **dates parsed from rows**. Copy
   `_apply_freshness_verdict` from `fetch_vixts.py` unchanged.
3. **VVIX and SKEW publish later than VIX/VIX3M.** The 02:50 UTC fire will
   often join to the prior session because the two late files have not
   appended yet, so the timer fires twice (§I). Measured 2026-09-18 only
   (three-session hourly watch was not available in this build window):
   VIX/VIX3M `Last-Modified` 01:51 UTC, VVIX 12:01 UTC, SKEW 21:01 UTC, all
   four files still ending 09/17 at 21:3x UTC. Second `OnCalendar` left at
   13:15 UTC as specified. TODO: re-measure append time across three
   sessions (`curl -sI` hourly 22:00-14:00 UTC) and move 13:15 if the SKEW
   row still is not present by then.
4. **Early VVIX rows are implausible** (15.71 on 2006-03-15, several prints
   under 40 in 2006). They predate the 2009-09-18 floor so the join never sees
   them, but the plausibility band in §C.7 still applies to the **latest** row
   only, as in vixts; do not band the whole history.
5. **`VXV_History.csv` is a dead stub** (see `vixts.md` §B.2). VIX3M only.
6. **Do not build fixtures with shell `head`/`tail` redirects** in this
   environment; the `rtk` hook corrupts headers. Use Python.

### B.3 Data-source priority

**Ladder: Cboe CDN only. No IB rung, no UW rung, Yahoo not used.** Identical
posture to `vixts.md` §B.3, four-indicator precedent on this CDN. IB daily bars
lag the official Cboe close by a session and carry no 2009-depth VIX3M or VVIX;
UW has no index-history surface. Because there is no fallback rung, the §C.7
plausibility guard is the only protection against a silently bad source.

### B.4 Why not read `vixts_history` and `skew_history` from Turso

The hypothesis proposed reusing the existing tables. Rejected for the composite:

- **One calendar, one client, no parent lag.** Pulling four CSVs from one CDN
  gives a single Cboe session calendar and makes the join trivial. Reading
  `vixts_history` adds a dependency on `radon-vixts.timer` having run and
  needs the skew2d-style "parent lag vs broken parent" logic
  (`docs/indicators/skew2d.md` § Parent lag). Two extra conditional GETs (304s
  on the unchanged path) are cheaper than that machinery.
- **`skew_history` (UW 25d put/call ratio) starts 2023-09-06.** A 252-session
  z-score on it begins 2024-09 and yields ~500 Δ1d rows. That cannot rank
  "largest 1-day decline in 10 years", which is the entire point of the
  headline statistic. The Cboe SKEW index has history to 1990. See §O.1 for
  the tradeoff and the 25d overlay that keeps the operator's preferred skew
  measure visible.

`vix_close` and `vix3m_close` in this table will equal `vixts_history` on the
same date because both derive from the same files. That is expected
duplication, not a second source of truth for the VIX/VIX3M ratio: `vixts`
owns that indicator; this table stores the legs so the composite rebuild is
self-contained and the row is auditable.

### B.5 Licensing

Same posture as `cor`, `straddle`, `vixcor`, `vixts`: internal single-operator
dashboard use of Cboe daily-price CSVs, computed series served behind the
authenticated perimeter, **never re-serve raw history publicly**. `/api/panic-index`
returns the composite and per-leg z-scores; it may carry the raw leg levels for
the strip because every sibling route already does, but do not add an endpoint
whose purpose is raw history export.

---

## C. The math

### C.1 Named constants (module level, `scripts/lib/panic_index_math.py`)

```python
Z_WINDOW = 252            # sessions in the rolling z-score window, inclusive of t
RANK_WINDOW = 2520        # trailing sessions for the 10y rank and the Δ1d z (10 x 252)
RANK_MIN_ROWS = 504       # Δ1d z / rank are None until this many Δ1d rows exist
Z_STD_FLOOR = 1e-9        # a window with no dispersion emits None, never inf

MIN_SERIES_ROWS = 3500    # plausibility floor; the real join is ~4,268
MAX_DROPPED_SHARE = 0.01  # inner join may drop at most 1% of VIX∩VIX3M dates
VIX_SANITY = (5.0, 150.0)
VVIX_SANITY = (40.0, 250.0)   # observed 2009-09-18+ range 61.76 .. 207.59 (2024-08-05)
TS_RATIO_SANITY = (0.40, 2.50)
SKEW_SANITY = (90.0, 220.0)   # observed range 101.2 .. 183.1

ALERT_SIGMA = 3.0         # Δ1d <= -ALERT_SIGMA * trailing-10y stdev fires the push
ALERT_TOP_N = 10          # rank <= this inside the trailing 10y escalates the copy
```

Mirrored as TS display-copy constants in `web/lib/panicIndex.ts`. **The UI never
recomputes a z-score, the composite, the change, or the rank** — it renders what
the payload carries.

### C.2 Components and panic orientation

All four are oriented so that **higher = more panic**. No sign flips are needed
because every raw series already rises under stress.

| # | GS input | Radon leg | Raw value `x_i(d)` | Why higher is panic |
|---|---|---|---|---|
| 1 | S&P vol | `vix` | VIX close | Implied 30d S&P vol rises in stress |
| 2 | VIX vol | `vvix` | VVIX close | Implied vol of VIX options rises when the vol complex is bid |
| 3 | S&P term structure | `ts_ratio` | `VIX / VIX3M` | Contango (< 1) is calm; backwardation (> 1) is front-loaded stress. Same ratio as `vixts` |
| 4 | S&P skew | `skew` | Cboe SKEW index | Rises when OTM S&P put tail is bid relative to ATM |

Raw levels are used, **not** logs and **not** changes. Both alternatives were
run on the live files during spec research; see §C.9 for why they were
rejected.

### C.3 Rolling z-score per leg

For each leg `i` and each joined date index `t` with `t >= Z_WINDOW - 1`:

```
window   = x_i[t - 251 .. t]                       # 252 values, inclusive of t
mean     = statistics.fmean(window)
std      = statistics.stdev(window)                # sample std, ddof = 1
z_i(t)   = (x_i(t) - mean) / std   if std > Z_STD_FLOOR else None
```

`z_i(t) = None` for `t < 251` and for a degenerate window. Use `statistics`, not
numpy (`dispersion_math.py` precedent; the whole rebuild is ~4,300 rows x 4 legs
x 252 and runs in about two seconds in pure Python).

Holiday alignment: because all four files share the Cboe session calendar and
the series is the inner join, "252 sessions" means 252 **joined rows**, never
252 calendar days and never a per-leg count. The seven dropped dates in §B.1 are
simply absent; the window skips over them.

### C.4 Composite level

```
level(t) = statistics.fmean([z_vix(t), z_vvix(t), z_ts(t), z_skew(t)])
```

`level(t) = None` if **any** leg z is `None` (no partial averages; a
three-legged reading on one day and four on the next is a regime break in
disguise).

Equal weights are the hypothesis and stay in v1. Weights are not constants to
tune; if a future study wants weights it changes this spec first.

### C.5 One-day change and its trailing statistics

```
delta_1d(t) = level(t) - level(t-1)       # None if either is None
```

`t-1` is the previous **joined row**, so a change across a dropped date spans
two sessions. That is acceptable and documented; do not interpolate.

Over the trailing `RANK_WINDOW` Δ1d rows ending at and including `t` (or all
available rows if fewer, once `RANK_MIN_ROWS` exist):

```
delta_mean_10y(t)   = fmean(window)
delta_std_10y(t)    = stdev(window)                     # sample
delta_z(t)          = (delta_1d(t) - delta_mean_10y(t)) / delta_std_10y(t)
rank_decline_10y(t) = 1 + count(v in window if v < delta_1d(t))   # 1 = most negative
rank_surge_10y(t)   = 1 + count(v in window if v > delta_1d(t))   # 1 = most positive
rank_n(t)           = len(window)
```

Ranks are 1-based, ties share the better rank. Only the **latest** row's
trailing stats are required in the payload; storing them per row is optional
and, if done, the whole table is rebuilt every run so order-dependence is not a
correctness risk (see §F).

Also publish, over the **entire** Δ1d series: `stats.high`, `stats.low`,
`stats.avg`, `stats.stddev` (population, `statistics.pstdev`, matching
`skew.md`), plus `stats.low_date`, `stats.high_date`. These are what the GS
chart annotated.

### C.6 Rolling-window drift, stated once

Differencing a rolling z-score includes a tiny term from the window's own mean
and std rolling forward by one session. It is O(1/252) relative to the
component change and is not corrected. The alternative definition
`mean_i((x_i(t) - x_i(t-1)) / std_i(t))` was considered and rejected as a
second, subtly different series that the copy would have to explain.

### C.7 Plausibility guard (mandatory — the only protection)

Before any write, `ensure_plausible_series(series, dropped_dates, base_count)`
**raises** `ValueError` when:

- `len(series) < MIN_SERIES_ROWS`
- `len(dropped_dates) / base_count > MAX_DROPPED_SHARE` where `base_count` is the
  size of the VIX∩VIX3M join (a source that lost a year of VVIX rows must fail
  loudly, not silently shorten the history)
- the **latest** row's `vix`, `vvix`, `ts_ratio`, `skew` fall outside their
  sanity bands
- the latest row's `level` is `None` (four full legs must exist by now)
- any row carries a non-positive `vix3m`

Raising keeps the run retryable and writes an `error` heartbeat. **Never latch
`service_health` `ok` over an unverified series, and never cache or mirror an
empty result** (`feedback_dont_cache_empty_results`).

### C.8 Calibration anchors (live files 2026-09-18; re-derive from fixtures)

These come from a throwaway prototype run against the four full CSVs during spec
research. **Tests must pin values re-derived from the checked-in fixtures**, not
these numbers, but the implementation must reproduce them to the stated
precision when run against the live files, and the PR body must show that run.

Per-leg z and level:

```
2024-08-05  vix 38.57 z 8.908   vvix 173.32 z 8.312   ts 1.1442 z 5.596   skew 145.30 z 0.473   level  5.8223
2024-08-06  vix 27.71 z 4.632   vvix 150.09 z 5.671   ts 1.0355 z 3.126   skew 145.43 z 0.496   level  3.4812
2026-09-16  vix 17.71 z -0.134  vvix  95.41 z -0.392  ts 0.8976 z 0.330   skew 145.95 z 0.201   level  0.0013
2026-09-17  vix 15.44 z -0.825  vvix  87.72 z -1.066  ts 0.8323 z -0.768  skew 145.70 z 0.157   level -0.6256
```

Δ1d series (n = 4,016, first 2010-09-21):

```
stddev (population)        0.3574
mean                      -0.0007
excess kurtosis            9.7
most negative  1  -2.3411  2024-08-06     (day after the VIX 65 open; carry unwind)
               2  -2.0682  2011-08-09     (Fed "extended period" statement day)
               3  -2.0087  2013-10-16     (debt-ceiling resolution)
               4  -1.7310  2025-04-09     (tariff pause)
               5  -1.5449  2017-04-24     (French first round)
most positive  1   3.7143  2018-02-05     (XIV / Volmageddon)
               2   2.8166  2024-08-05
count Δ1d <= -3σ           23   (~1.4 per year)
count Δ1d >= +3σ           44
2024-08-06 delta_z        -6.567   rank_decline_10y 1
2026-09-17 delta_1d       -0.6269  (-1.75σ)
```

Read against the GS quote: this proxy ranks **2024-08-06 as the largest 1d
decline in the trailing 10 years** and 2025-04-09 second, which are the two
dates GS-desk panic-unwind commentary is known to have described. That is
directional agreement, not calibration. The GS chart's stddev of 0.93 versus
this proxy's 0.36 is a scale difference only; do not rescale to match it.

### C.9 Variants run and rejected

| Variant | Result | Verdict |
|---|---|---|
| Sum of z instead of mean | Identical shape, stddev 1.43 | Rejected; mean keeps the unit "average leg z" which the strip labels |
| `log(VIX)`, `log(VVIX)`, `log(VIX/VIX3M)` | Excess kurtosis 5.1 vs 9.7, but 2024-08-06 drops to the 3rd largest decline and 2013-10-16 becomes 1st | Rejected for v1: tames tails at the cost of demoting the event the desk called a record. Revisit only with a stated objective |
| UW 25d put/call ratio as the skew leg | History floor 2023-09-06 | Rejected as the composite leg (§B.4); kept as an overlay (§O.1) |
| 20d realized vol of VIX as the VIX-vol leg | Unnecessary; VVIX is live on the CDN | Rejected |

---

## D. Payload contract

```jsonc
{
  "scan_time": "2026-09-19T02:50:41Z",           // tz-aware UTC ISO, Z-suffixed
  "source_last_modified": {                       // per-file, lowercase keys
    "vix": "...", "vix3m": "...", "vvix": "...", "skew": "..."
  },
  "data_date": "2026-09-17",                      // latest joined row
  "expected_session": "2026-09-18",               // from _apply_freshness_verdict
  "lag_days": 1,
  "status": "ok",                                 // "ok" | "stale_source"
  "count": 4268,                                  // joined rows
  "delta_count": 4016,                            // rows with a non-null delta_1d
  "dropped_dates": ["2010-11-11", "..."],         // VIX∩VIX3M dates absent from VVIX or SKEW
  "z_window": 252,
  "rank_window": 2520,
  "current": {
    "date": "2026-09-17",
    "level": -0.6256,
    "delta_1d": -0.6269,
    "delta_z": -1.75,
    "delta_std_10y": 0.3574,
    "rank_decline_10y": 87,
    "rank_surge_10y": 2423,
    "rank_n": 2509,
    "legs": {
      "vix":   { "value": 15.44,  "z": -0.8252 },
      "vvix":  { "value": 87.72,  "z": -1.0661 },
      "ts":    { "value": 0.8323, "z": -0.7683, "vix3m": 18.55 },
      "skew":  { "value": 145.70, "z": 0.1573 }
    }
  },
  "stats": {                                      // over the whole delta_1d series
    "high": 3.7143, "high_date": "2018-02-05",
    "low": -2.3411, "low_date": "2024-08-06",
    "avg": -0.0007, "stddev": 0.3574
  },
  "alert": {                                      // §N
    "last_fired_date": "2025-04-09",
    "last_fired_kind": "decline"
  },
  "series": [
    { "date": "2009-09-18", "vix": 23.92, "vix3m": 26.54, "vvix": 88.11, "ts": 0.9013, "skew": 118.93,
      "z_vix": null, "z_vvix": null, "z_ts": null, "z_skew": null, "level": null, "delta_1d": null }
  ]
}
```

`level`, `delta_1d`, and every `z` are rounded to **4 decimal places** at build
time; `delta_z` to 2; `ts` to 4; raw closes carry the source precision.
`current` is `null` only when `series` is empty, which §C.7 makes an exception
rather than a served state. `series` carries the warm-up rows with `null`
derived fields so the chart's x-axis starts at the true history floor and the
implementer never has to explain a 2010 start to a 2009 file.

Missing contract (HTTP 200 exactly):

```ts
const MISSING_PANIC_INDEX = Object.freeze({
  missing: true, scan_time: null, source_last_modified: null, data_date: null,
  count: 0, delta_count: 0, current: null, stats: null, alert: null, series: [],
});
```

---

## E. Ingestion job — `scripts/fetch_panic_index.py`

### E.1 Shape

Copy `scripts/fetch_vixts.py` file-for-file and change the symbol table, the
math import, and the names. Pure math lives in `scripts/lib/panic_index_math.py`
so pytest covers it without network. Import `parse_index_csv` from
`fetch_vixts` rather than duplicating it.

```python
_SYMBOLS = ("VIX", "VIX3M", "VVIX", "SKEW")
_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "VVIX": "VVIX", "SKEW": "SKEW"}
SERVICE = "panic-index"
PANIC_INDEX_JSON = _PROJECT_DIR / "data" / "panic_index.json"
_MAX_CACHE_LAG_DAYS = 4

# Mirrors radon-panic-index.timer (OnCalendar 02:50 and 13:15 UTC) so heartbeat
# copy can name the next attempt instead of hardcoding cadence text.
TIMER_SLOTS_UTC = ((2, 50), (13, 15))
```

`run(client=None, *, now=None) -> dict` so tests inject a stub client and a
fixed clock.

### E.2 Conditional-GET flow

Identical to `vixts.md` §E.2 with four symbols: read the cached payload's
`source_last_modified`; one conditional GET per symbol; **if a cached payload
exists and all four return 304**, `restate_cached_payload` (re-age the
verdict, refresh snapshot + heartbeat, rewrite JSON, **no row upserts**, no
alert evaluation); otherwise re-fetch unconditionally any symbol that returned
`None`, parse, join, compute, guard, build, evaluate the alert (§N), write.

### E.3 Join

`join_series(vix, vix3m, vvix, skew) -> (series, dropped_dates, base_count)`:
ascending by date; a row is emitted only for dates present in **all four**;
`base_count = len(dates(vix) ∩ dates(vix3m))`; `dropped_dates` = those base
dates absent from VVIX or SKEW; rows with `vix3m <= 0` are skipped and counted
as dropped. Then `attach_z_scores`, `attach_level`, `attach_delta` in that
order, each a pure function over the list.

### E.4 Writes, in order, every cycle

1. `writer.ensure_no_replica_for_writers()`
2. `writer.upsert_panic_index_rows(payload["series"], recorded_at=scan_time)` —
   **only when `rows_changed`**. Chunked multi-row `INSERT ... ON CONFLICT(date)
   DO UPDATE`, modelled on `upsert_vixts_rows` (~4,300 rows; per-row statements
   caused the 2026-07-21 Hrana 502).
3. `writer.upsert_scan_snapshot("panic-index", scan_time, payload)` — every cycle
4. `writer.record_service_health("panic-index", "ok"|"error", finished_at=scan_time, error=...)` — every cycle
5. Atomic write of `data/panic_index.json` — fallback only; **Turso is the truth**

`_write_db` reproduces `fetch_vixts._write_db` exactly, including the split
try blocks (R-192, R-331): a failed row upsert or snapshot must not take the
heartbeat down with it. The heartbeat reflects **this writer's health only**;
an alert that fired or did not fire never changes the row state
(`feedback_service_health_writer_state_not_event_content`).

### E.5 CLI

`--json` prints the payload to stdout; all progress and summary output goes to
**stderr**. No `--backfill`: the source files are full history every fetch, so
every changed run re-upserts the whole series idempotently. Add
`--no-alert` (skips §N dispatch; used for the first production run and for
local reproduction so a historical record never pages).

---

## F. Storage — `scripts/db/migrations/0077_panic_index.sql`

```sql
-- PANIC INDEX proxy: equal-weight mean of 252-session z-scores of VIX, VVIX,
-- VIX/VIX3M and Cboe SKEW, plus its one-day change. Unlike vixts_history the
-- derived columns ARE rolling statistics; that is safe here because the
-- fetcher rebuilds and re-upserts the ENTIRE series from full-history Cboe
-- files on every changed run, so no row is ever computed from a partial
-- history. Never write this table incrementally.
CREATE TABLE IF NOT EXISTS panic_index_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    vix3m_close REAL NOT NULL,
    vvix_close REAL NOT NULL,
    skew_close REAL NOT NULL,
    ts_ratio REAL NOT NULL,
    z_vix REAL,
    z_vvix REAL,
    z_ts REAL,
    z_skew REAL,
    level REAL,
    delta_1d REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panic_index_history_date_desc ON panic_index_history (date DESC);
CREATE INDEX IF NOT EXISTS idx_panic_index_history_delta ON panic_index_history (delta_1d);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (77, datetime('now'));
```

The `delta_1d` index exists so "top-N most negative days" is one cheap query
for the alert copy and any future report. Writer:
`upsert_panic_index_rows(rows, recorded_at=None)`, chunked, idempotent per
date, `None` z / level / delta written as SQL `NULL`.

---

## G. API — `web/app/api/panic-index/route.ts`

Copy `web/app/api/vixts/route.ts` verbatim in shape.

- `export const dynamic = "force-dynamic"; export const runtime = "nodejs"; export const radonCapability = "read";` GET only.
- `dbFirstRead({ fromDb, fromDisk, maxAgeMs, label: "panic-index" })`.
- `fromDb`: `SELECT scan_time, payload FROM scan_snapshots WHERE service = 'panic-index' ORDER BY scan_time DESC LIMIT 1`.
- `fromDisk`: `data/panic_index.json`.
- `maxAgeMs = getFreshnessWindowMs("panic-index", "closed")` from the shared
  catalog, **not** a private constant (SKILL.md §3; vol-cone `e7323e4e`).
- Missing contract from §D at HTTP **200**, never a 4xx.
- `setCacheResponseHeaders(response, { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, requestId, cacheState: "HIT", tags: ["panic-index"] })`.

Hook `web/lib/usePanicIndex.ts`: `useSyncHook` GET-only, `interval: 3_600_000`
(hourly poll of a twice-daily series), `extractTimestamp: d => d.scan_time`.

`web/lib/panicIndex.ts` holds the types, display constants (`Z_WINDOW`,
`RANK_WINDOW`, `ALERT_SIGMA`, `ALERT_TOP_N` mirrored for copy), formatters
(`formatLevel` 2dp signed, `formatDelta` 2dp signed, `formatZ` 1dp signed,
`formatRank(rank, n)` -> `"#1 of 2509"`), `deltaTone(delta, stddev)` (strictly
beyond `±2 x stddev` -> `var(--warning)`, strictly beyond `±3 x stddev` ->
`var(--negative)`, otherwise and for null -> `var(--text-muted)`; the same
tone on both sides because a panic surge is not good news, so `var(--positive)`
is never used here; boundaries stay muted, strict inequalities, pinned by
test), `SOURCE_FOOTNOTE`, and the honesty strings. **No z, composite, delta,
or rank math in TS.**

Add `"panic-index"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES` in
`web/tests/route-local-authz-matrix.test.ts` and confirm
`web/tests/assistant-catalog-freshness.test.ts` passes (a Next-only route
appears automatically and fails until `web/lib/assistant/nextLoaders.ts` gains
its static import).

---

## H. UI — `web/components/PanicIndexPanel.tsx`

Mirror `VixTsPanel.tsx` structure exactly.

**Gate order, strictly:**

1. `(loading || syncing) && !data` -> `<SpectralLoader label="Loading Cboe panic proxy series" />`
2. `!data || data.missing || !data.current` -> `<SectionEmptyState headline="No panic proxy reading yet" secondary="The four Cboe series have not been joined by the panic-index refresh timer." />`
3. content

**Honesty labeling (non-negotiable):**

- Panel header device label: `PANIC PROXY`. Directly beneath it, a muted
  sub-label in the same row: `Radon reconstruction - not the Goldman Sachs index`.
  `data-testid="panic-index-disclaimer"`; the panel test asserts its exact text.
- InfoTooltip first sentence is §A's one-liner. Body: the four legs and their
  Cboe tickers -> the 252-session z / equal-weight mean definition -> what a
  large negative and large positive 1d change mean -> `Source: CBOE (VIX, VIX3M,
  VVIX, SKEW)`.
- `SOURCE_FOOTNOTE`: `Radon Panic Proxy. Equal-weight mean of 252-session
  z-scores of Cboe VIX, VVIX, VIX/VIX3M and SKEW. Goldman Sachs' Panic Index
  formula is proprietary; this is Radon's reconstruction from its stated
  inputs and is not comparable in level.`
- Nowhere: "Goldman Panic Index" as a title, "GS" as a value source, or any
  comparison of Radon's level to a GS number.

**Strip — desktop `RegimeStrip`, mobile `MetricCell` 2x2 grid**, every value
wrapped in a `data-testid` span:

| testId | label | value | sub |
|---|---|---|---|
| `panic-index-delta` | `1D CHANGE` | `-0.63` (toned) | `z -1.8 vs 10y` |
| `panic-index-level` | `LEVEL` | `-0.63` | `mean z of 4 legs` |
| `panic-index-rank` | `10Y RANK` | `#87 of 2509` | `most negative 1d change` |
| `panic-index-vix` | `VIX` | `15.44` | `z -0.83` |
| `panic-index-vvix` | `VVIX` | `87.72` | `z -1.07` |
| `panic-index-ts` | `VIX / VIX3M` | `0.8323` | `z -0.77` |
| `panic-index-skew` | `SKEW` | `145.70` | `z 0.16` |
| `panic-index-source-updated` | `SOURCE UPDATED` | `2026-09-17` | `latest joined session` |

`10Y RANK` shows `rank_decline_10y` when `delta_1d < 0`, `rank_surge_10y` when
`> 0` with sub `most positive 1d change`, and `---` when `rank_n < RANK_MIN_ROWS`.

`<FreshnessRail schedule={PANIC_INDEX_REFRESH} asOf={data.data_date} testId="panic-index-freshness-rail" asOfTestId="panic-index-strip-asof" />`
directly under the strip.

**Chart:** `CriHistoryChart`, single right series, title toggles with the view
chips:

- `CHANGE` (default, matches the GS chart): title `PANIC PROXY - 1D CHANGE`,
  series `delta_1d`, with two reference lines at `±3 x stats.stddev` labelled
  `+3σ` / `-3σ` if `CriHistoryChart` supports guide lines (it does for vixts's
  1.00 line; reuse that prop).
- `LEVEL`: title `PANIC PROXY - LEVEL`, series `level`.

Preceded by `<HistoryRangeChips>` (default preset **`10Y`** if the preset set
has one, else `all`), followed by `<BrushMinimap testIdPrefix="panic-index-brush" />`
when `total >= 2`, then the source footnote. Warm-up rows with `null` values
are passed through; the chart already tolerates null points.

**Freshness copy is derived, never asserted.** No string anywhere may claim a
cadence. Grep the new strings for `Refresh|Updated|hourly|daily|5m|twice`
before shipping.

Brand tokens only, no raw hex, 4px max radius, no em dashes.

---

## I. Scheduling

**`cloud/services/radon-panic-index.service`** — copy `radon-vixts.service`,
change Description, ExecStart to `scripts/fetch_panic_index.py`,
`TimeoutStartSec=300`. `EnvironmentFile=/etc/radon/env` (the skill text saying
`radon-cloud/.env` is stale; copy the unit, not the skill).

**`cloud/services/radon-panic-index.timer`**

```ini
[Unit]
Description=Radon Panic Proxy - Cboe VIX/VIX3M/VVIX/SKEW composite, 02:50 and 13:15 UTC

[Timer]
# Two fires per calendar day, calm-streak precedent (02:40 + 14:30). VIX and
# VIX3M carry the prior session by ~01:51 UTC (vixts research), but VVIX and
# SKEW were observed re-touched at 12:01 and 21:01 UTC with the row appended
# some time after the VIX files. 02:50 sits in the free slot after vixts 02:45
# and usually joins to the prior-but-one session; 13:15 UTC (before RTH open)
# picks up the completed four-way join for the last session. Off-session
# fires are 304 heartbeats that keep service_health inside the 26h window.
# Implementer: replace "some time after" with the measured append time.
OnCalendar=*-*-* 02:50:00 UTC
OnCalendar=*-*-* 13:15:00 UTC
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

Registration: append both units to `cloud/scripts/setup-vps.sh` `SERVICE_FILES`,
append both `sha256  name` lines (**exactly two spaces**) to
`cloud/config/installed-units.sha256`, add both to
`cloud/tests/test_systemd_services.py`. A unit absent from the manifest is
**never installed**.

`web/lib/refreshSchedule.ts`: `export const PANIC_INDEX_REFRESH: RefreshSchedule = [daily(2, 50), daily(13, 15)];`
plus the `it("panic-index mirrors radon-panic-index.timer")` case in
`web/tests/refresh-schedule.test.ts` (the test parses the unit file).

---

## J. Service-health registration (both sides, same commit)

`web/lib/serviceHealthWindows.ts`, after `vixts` in the daily block:

```ts
"panic-index": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
```

`scripts/watchdog/services.py`: `SCHEDULED_SERVICES["panic-index"] = {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False}`
with a two-line comment naming the timer and both slots, and append
`"panic-index"` to the `"daily"` bucket list.
`scripts/tests/test_watchdog/test_services.py` asserts set-equality with the TS
file; a one-sided edit fails CI. A brand-new timer has no `service_health` row
until its first fire; no-row-ever is dormant, not a page.

---

## K. Registration checklist

**`web/lib/regimeRail.ts`** (3): append `| "panic-index"` to `RegimeTab`; add
`"panic-index"` to the **Volatility** group right after `"vixts"`; add
`"panic-index": "PANIC",` to `REGIME_TAB_LABEL`.

**`web/components/RegimePanel.tsx`** (4): `MOBILE_TAB_LABEL` entry `PANIC`;
`tabFromPathname` regex alternation (keep longest-prefix-first; `panic-index`
has no prefix collision); the inline mobile chip array; the
`if (activeTab === "panic-index") return renderShell(<PanicIndexPanel />);`
dispatch plus import.

---

## L. Lockstep test pins (same commit, or CI fails)

| File | Edit |
|---|---|
| `web/tests/route-local-authz-matrix.test.ts` | add `"panic-index"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES` |
| `web/tests/service-health-windows.test.ts` | exhaustive `expected` set: add `panic-index` |
| `web/tests/refresh-schedule.test.ts` | add the timer-mirror case |
| `web/tests/regime-tab-routes.test.tsx` | `describe.each` table, panel stub mock, render case, nav case |
| `web/tests/regime-rail.test.tsx` | sorted tab-key array, `groupOf`, `REGIME_TAB_LABEL` |
| `web/lib/assistant/nextLoaders.ts` + `web/tests/assistant-catalog-freshness.test.ts` | static import for the new route id |
| `cloud/tests/test_systemd_services.py` | both unit names |
| `docs/indicators/README.md` | flip the `panic-index` row from "spec" to shipped |
| `docs/cloud-services.md` | append `### Panic Proxy (radon-panic-index.timer)` |

---

## M. Tests (red first)

Fixtures: `scripts/tests/fixtures/panic_index_{vix,vix3m,vvix,skew}_sample.csv`,
each the **last ~800 sessions** of the live file (2023-07 onward), written with
Python, real headers (`DATE,VVIX`, `DATE,SKEW` included). Long enough for a 252
warm-up plus ~550 Δ1d rows; the 10y rank is tested with a synthetic series.

**`scripts/tests/test_panic_index.py`** — parses the fixtures at import;
expected values derived by inspecting fixtures, never mental arithmetic:

- `parse_index_csv` reads the `VVIX` and `SKEW` value columns and skips malformed rows
- `join_series` is a four-way inner join; a date removed from the VVIX fixture
  appears in `dropped_dates` and not in `series`; `base_count` counts VIX∩VIX3M
- `rolling_z`: `None` for the first 251 rows; row 252 equals a
  hand-computed `statistics` result on the fixture window to 1e-9; a constant
  window emits `None`, never raises or emits `inf`
- `level` is `None` when any leg z is `None`; equals the mean when all four exist
- `delta_1d` is `None` on the first level row; equals `level[t] - level[t-1]` after
- `trailing_delta_stats` on a synthetic 3,000-row series: window capped at
  2,520; `rank_decline_10y == 1` for a planted minimum; ties share rank;
  `None` below `RANK_MIN_ROWS`
- `compute_stats` returns `low_date` / `high_date` matching the fixture extremes
- `ensure_plausible_series` raises on: too few rows, dropped share above 1%,
  each of the four out-of-band latest legs, latest `level is None`, non-positive `vix3m`
- conditional-GET stub client: all-304 restates the cached payload with a
  fresh `scan_time` and re-aged verdict, heartbeats, **no** row upsert, **no**
  alert dispatch; one changed file rebuilds and **does** upsert
- freshness verdict: window-relative dates; `lag_days > 4` -> `stale_source`
  with an `error` heartbeat on **both** the 304 and the 200 branch (T-263)
- `_write_db` isolation: a raising `upsert_panic_index_rows` still writes the
  snapshot and records an `error` heartbeat; a raising snapshot still heartbeats
- alert (§N): fires exactly once for a qualifying latest row; does not fire
  when `alert.last_fired_date == current.date`; does not fire when
  `data_date != expected_session`; does not fire under `--no-alert`; the
  Pushover payload is asserted at the wire (title, priority `0`, `url`,
  `url_title`, message contains the rank and the σ figure) via a stubbed
  transport, never via a `vi.fn`-style "was called" check
- migration executed into in-memory sqlite pins schema, version 77, `NULL`
  round-trip of the derived columns, and upsert idempotency
- a run against the four full live files (marked `@pytest.mark.network`, skipped
  in CI) reproduces §C.8 anchors to 4dp; paste its output into the PR body

**`web/tests/panic-index-api.test.ts`** (`@vitest-environment node`) — mock
`@/lib/db` with a real in-memory `@libsql/client` seeded from the migration:
Turso beats older disk, disk fallback, the exact `missing:true` object at
**200**, no cross-service snapshot leak, `route.dynamic === "force-dynamic"`,
`maxAgeMs` comes from `getFreshnessWindowMs`.

**`web/tests/panic-index-panel.test.tsx`** (`@vitest-environment jsdom`) — stub
`ResizeObserver`, `vi.mock` the hook, factory fixtures `buildSeries(n)` /
`hookState()`: loader label, empty state, the **disclaimer text exactly**, all
eight strip values, rank cell switches between decline / surge / `---`, chart
title toggles between `CHANGE` and `LEVEL`, chips / brush, tone at exactly 2σ
stays muted and strictly beyond turns warning, NaN guard (no `<path d>`
contains `"NaN"`) with warm-up nulls present, freshness rail renders with a
fake-timer countdown.

**`web/e2e/panic-index-tab.spec.ts`** — `page.route` mocks for
`**/api/panic-index` plus `portfolio`, `orders`, `ib-status`; abort
`**/api/prices`; assert active tab, disclaimer visible, stroked paths, brush,
missing-state copy. Attach the screenshot as `docs/indicators/panic-index-tab.png`
(and the light-theme capture if the tab renders differently).

Run from **repo root**. Never run pytest and vitest concurrently on the laptop.

---

## N. Alert — record one-day declines

### N.1 Rule

Evaluated only on the **rebuild** branch (a 304 cannot carry a new session),
only when `payload.status == "ok"`, only when `current.date == expected_session`
(never page on a historical record during a first run or a catch-up), and only
when `current.rank_n >= RANK_MIN_ROWS`.

```
fire_decline = delta_1d <= -ALERT_SIGMA * delta_std_10y
```

Fire **once per session**: skip when `cached.alert.last_fired_date == current.date`.
Persist `alert.last_fired_date` / `alert.last_fired_kind` in the payload so the
JSON / snapshot carry the dedupe state; the rebuild copies them forward from the
cached payload before evaluating.

`-3σ` on the trailing-10y Δ1d distribution fired **23 times in 16 years**
(~1.4 per year, §C.8) because the distribution is fat-tailed; that is the
intended cadence for a normal-priority push. Do **not** use ±2σ (would fire ~9
times a year). Surges (`+3σ`, 44 in 16 years) are **not** alerted in v1; the
strip and chart show them. Add a surge rule only if the operator asks.

### N.2 Copy

Title: `radon panic proxy: record 1d decline`

Message (one line, no em dashes):

```
Panic Proxy 1d change {delta_1d:+.2f} ({delta_z:+.1f}σ vs 10y). Rank #{rank_decline_10y} most negative of {rank_n} sessions. Level {level:+.2f}. Legs z: VIX {z_vix:+.1f}, VVIX {z_vvix:+.1f}, TS {z_ts:+.1f}, SKEW {z_skew:+.1f}. Radon proxy, not GS.
```

When `rank_decline_10y <= ALERT_TOP_N`, prefix the message with
`TOP-{rank} IN 10Y. `. `url` = `https://app.radon.run/regime/panic-index`,
`url_title` = `Open Panic Proxy`. Priority **0** always: a market signal is
never an emergency page and never uses `send_direct_page` (that is the P1
outage path).

### N.3 Transport

Add one small public helper beside `send_direct_page` in
`scripts/watchdog/notify.py`:

```python
def send_signal_push(*, title: str, message: str, url: str, url_title: str) -> Optional[str]:
    """Priority-0 market-signal push. Returns error string or None."""
```

built on the existing `_pushover_creds`, `build_pushover_payload(severity=None)`
and `_post_pushover`, adding `url` / `url_title`. The fetcher calls it,
logs the returned error string to stderr, and **does not** change its own
`service_health` state on a delivery failure (writer state is not event
content). Missing credentials log `"[panic-index] alert not sent: pushover
unconfigured"` and continue. Do not add a second Pushover client anywhere.

---

## O. Gaps, decisions, and what to verify

### O.1 The skew leg: Cboe SKEW vs UW 25d put/call ratio

Two S&P skew measures exist in the repo's reach. **Cboe SKEW is the composite
leg** because it spans the same 2009+ window as VIX3M and the headline statistic
is a 10-year rank. The UW 25d ratio (`skew_history.ratio`) is closer to how a
desk says "skew" but begins 2023-09-06.

**Overlay, in v1, cheap:** the fetcher additionally reads
`SELECT date, ratio FROM skew_history ORDER BY date` (Turso; fall back to
`data/skew.json` series when empty or unreachable; **never raise** on this
read, it is decoration), computes `z_skew25d` with the same 252 window, and
attaches it to `series` rows and `current.legs.skew25d` as `{ "value", "z" }`
or `null`. The strip shows a ninth cell `25D SKEW` with sub `overlay, UW, not in
composite` when present and hides it when null. The chart does **not** plot it
in v1. Parent lag on `skew_history` is tolerated silently by design here: a
one-session-behind overlay is `null` for the newest row, nothing more.

Done-when includes reporting the Pearson correlation of `z_skew` and
`z_skew25d` over their overlap in the PR body. If it is below 0.3 the operator
decides whether a 25d-based second composite (`panic-25d`, history from
2024-09) is worth a v1.1 spec; do not build it speculatively.

### O.2 Publication timing of VVIX / SKEW

Unknown at spec time beyond §B.1's re-touch stamps. Measured in the PR (§B.2
trap 3). Consequence if late: the 02:50 fire serves `lag_days = 2` on a normal
Tuesday, which is inside `_MAX_CACHE_LAG_DAYS = 4` and correct; the 13:15 fire
completes it.

### O.3 Scale versus the GS chart

GS's Δ1d stddev is 0.93; this proxy's is 0.36. Different scale, unknown GS
weights and windows. **Do not rescale.** The strip's `z -1.8 vs 10y` sub-label
is the comparable unit.

### O.4 First production run

Run `fetch_panic_index.py --no-alert` once by hand after deploy so the initial
4,268-row upsert and the historical record rows do not page. Verify
`SELECT COUNT(*), MAX(date) FROM panic_index_history` and the newest
`scan_snapshots` row before enabling the timer.

---

## P. Done-when (implementer PR, draft, no merge)

The PR is ready for operator review when **all** of the following are true and
each is evidenced in the PR body with the fragment shown:

1. `python3.13 -m pytest scripts/tests/test_panic_index.py -q` green; count shown.
2. `python3.13 -m pytest cloud/tests -q` green (units registered, manifest hashes match).
3. `bunx vitest run --config vitest.config.ts web/tests/panic-index-*.test.ts* web/tests/regime-* web/tests/service-health-windows.test.ts web/tests/refresh-schedule.test.ts web/tests/route-local-authz-matrix.test.ts web/tests/assistant-catalog-freshness.test.ts` green; count shown.
4. Full `bunx vitest run --config vitest.config.ts` and the full pytest gate green locally (coverage ratchets 75/78/65 and 64 unchanged or higher).
5. `python3.13 scripts/fetch_panic_index.py --json --no-alert` against the live CDN reproduces every §C.8 anchor to 4dp; the stderr summary and the `current` block pasted.
6. Alert wire test asserts the exact Pushover payload (`priority: 0`, `url`, `url_title`, message text) via a stubbed transport, plus the four no-fire cases in §M.
7. Playwright `web/e2e/panic-index-tab.spec.ts` green locally; screenshot of the live tab checked in as `docs/indicators/panic-index-tab.png` showing the `PANIC PROXY` header **and** the `Radon reconstruction - not the Goldman Sachs index` disclaimer.
8. `rg -n "Goldman|GS " web/components/PanicIndexPanel.tsx web/lib/panicIndex.ts` shows only the disclaimer and footnote strings.
9. `rg -n "Refresh|Updated|hourly|daily|twice" web/components/PanicIndexPanel.tsx web/lib/panicIndex.ts` shows no asserted-cadence copy.
10. §B.2 trap 3 filled in with measured VVIX / SKEW append times over three sessions and the second `OnCalendar` confirmed or adjusted.
11. §O.1 correlation figure reported.
12. `docs/indicators/README.md` row flipped to shipped; `docs/cloud-services.md` section added.
13. CI on the PR head green (`gh pr checks --watch`), PR left as **draft**, not merged. First production run per §O.4 is an operator step after merge, listed under the PR's "after merge" heading.
