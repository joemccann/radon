# VIXCOR — VIX vs 3-Month SPX Implied Correlation, 20-Day Rolling

**Status:** authoritative build spec. Implementers follow this literally.

**First build step:** `git mv docs/vix-cor3m-breakdown-indicator.md docs/indicators/vixcor.md`.
`docs/indicators/` is the canonical home for indicator specs and both
`scripts/fetch_vixcor.py` and `web/lib/vixcor.ts` reference it by that path in
their header docstrings. This file lives at the root of `docs/` only because
that is where it was first written.

---

## 0. What this is, and what it is NOT

The operator's premise was: *"VIX and COR3M typically move together; when the
20-day positive correlation breaks, it is a warning shot for a higher VIX
shortly after."*

**The validation study rejected the predictive half of that premise.** Over
5,152 sessions (2006-01-31 .. 2026-08-14, 31 breakdown episodes at the shipped
definition):

- Forward VIX drawup after a breakdown is **below the all-session mean at every
  horizon** (h=5: 3.92% vs 8.96%; h=10: 8.00% vs 15.82%; h=21: 20.89% vs
  27.36%; h=42: 33.01% vs 44.01%), while **on the median the two are
  indistinguishable at 42 sessions**: 31.79% after a breakdown against 29.31%
  unconditionally, so the event median sits 2.48pp ABOVE the base (it survives
  dropping the largest observation: 31.32% vs 29.31%), P(+20% drawup) is 63.3%
  vs 63.8%, and Mann-Whitney gives p=0.683. The mean-only gap is manufactured
  by the right skew of the base distribution (mean/median ~1.50x at h=42). The
  refutation is a statement about the MEAN, and 42 sessions is exactly the span
  of the operator's four blue arrows, so the copy says so everywhere it appears
  (`G.5`, `G.6`, `web/lib/vixcor.ts`, `scripts/lib/vixcor_math.py`).
- Against a per-event **VIX-level-matched** base rate (±10% VIX pool, paired
  t-test) the deltas are −5.02pp (t=−2.42), −7.81pp (t=−3.30), −6.25pp
  (t=−1.35), −10.79pp (t=−2.24).
- A 5,000-iteration circular block-shift permutation says the post-event drawup
  is **significantly LOWER than random** at h=5 (p=0.0228) and h=10 (p=0.0158).
- The only pro-claim number is P(VIX nominally higher) at h=42 (60.0% vs 44.6%,
  permutation p=0.036). It is one hit across ~30 tests, it inverts at h=63
  (37.9%), and at **h=42** it collapses to **50.0% (15 of 30)** when anchored on
  the episode trough instead of the trigger. (The trough-anchored rate at
  **h=21** is 53.3%, 16 of 30, p=0.17; that is a different horizon and must not
  be quoted as the h=42 figure.) It does not survive.
- **Mechanism:** a Pearson correlation on price LEVELS collapses when either leg
  goes range-bound, because there is no covariance left to measure. Breakdown
  windows have 20d VIX coefficient-of-variation **6.91%** vs **11.80%** in
  coupled windows, and median trailing-20d VIX change into a breakdown is
  **−8.23%** vs −1.93% unconditionally. This is a *"VIX has gone quiet and
  drifted down"* detector. That is why P(higher) ticks up (mean reversion off a
  locally depressed level) while every magnitude measure comes in below base.

**Gate 2 (Edge) fails.** Therefore:

> **The tab ships as a descriptive correlation-regime read, never as a
> predictive warning.** No blue-arrow copy, no "warning shot", no forward-VIX
> projection, no hit-rate framing. Wherever the UI shows the historical
> episodes, it shows the base rate beside them so 4-of-4 is read against a 64%
> null. This constraint is testable and is pinned in `H.8`.

The four blue arrows on the operator's StockCharts image are literally correct
(all four resolved episodes saw a materially higher VIX inside two months) and
also uninformative: P(+20% drawup) within 42 sessions is 63.8% unconditionally.
Four for four at a 64% base rate is p≈0.17. The chart shows the five circles
that had an arrow to draw.

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `vixcor` |
| Route | `/regime/vixcor` |
| Page file | `web/app/regime/vixcor/page.tsx` |
| Tab label (desktop + mobile) | `VIX-COR` |
| Display name | VIX / COR3M Correlation |
| API route | `/api/vixcor` |
| service_health key | `vixcor` |
| systemd units | `radon-vixcor.service`, `radon-vixcor.timer` |
| Turso table | `vixcor_history` |
| Snapshot service | `scan_snapshots.service = 'vixcor'` |
| Disk fallback | `data/vixcor.json` |
| Spec | `docs/indicators/vixcor.md` |

**One-line description the UI shows** (the `InfoTooltip` first sentence and the
`docs/indicators/README.md` row):

> Rolling 20-session Pearson correlation between the VIX close and the Cboe
> 3-month SPX implied correlation index close. A low reading means the two have
> decoupled, which historically marks a quiet, range-bound VIX tape, not a
> stress warning.

No em dashes anywhere in user-facing copy. Hyphens only.

---

## B. The math

### B.1 Convention: price LEVELS, not returns. Settled.

StockCharts' `CORR(a,b,n)` correlates **plotted values**, not differences. Its
ChartSchool worked example tabulates raw `INTC` and `QQQ` prices with no
differencing step. Empirically this is the only convention that reproduces the
operator's chart:

| Convention | value on 2026-08-14 | prints as | episodes ≤ +0.30 since 2024 | min since 2024 |
|---|---|---|---|---|
| **LEVELS (ship this)** | **+0.014969** | **0.01** | **5** | **−0.074** |
| 20 log returns | +0.9219 | 0.92 | 1 | +0.255 |
| 20 simple pct changes | +0.9204 | 0.92 | 1 | — |

The return conventions never drop below +0.245 in the whole 2024+ window and
cannot produce the operator's chart at all.

### B.2 Formula

Sample Pearson over the window. The `n−1` cancels, so the population form is
identical; implement the sums form and never call a stats library that might
switch denominators:

```
x = VIX close,  y = COR3M close,  over the window's WINDOW joined observations

num = Σ (xᵢ − x̄)(yᵢ − ȳ)
den = sqrt( Σ (xᵢ − x̄)² · Σ (yᵢ − ȳ)² )
r   = num / den
```

**Guard:** `den == 0` (a degenerate window where one leg is constant) emits
`null`. Never divide. Never emit `0.0` for a degenerate window: `0.0` is a
legitimate breakdown reading and conflating the two would fabricate an episode.

### B.3 Named constants (module level, scoped to FULL history)

```python
WINDOW                     = 20    # joined sessions, trailing, INCLUSIVE of the current session
MIN_OBSERVATIONS           = 20    # == WINDOW. No partial-window value, ever.
BREAKDOWN_TRIGGER          = 0.25  # corr20 < 0.25 opens/confirms an episode
BREAKDOWN_EXIT             = 0.30  # hysteresis: the episode stays open while corr20 < 0.30
EPISODE_MERGE_SESSIONS     = 10    # sub-EXIT runs <= 10 joined sessions apart merge
EPISODE_DEBOUNCE_SESSIONS  = 42    # a trigger within 42 sessions of the prior trigger does not open a new episode
FORWARD_HORIZONS           = (5, 10, 21, 42, 63)   # joined sessions
DRAWUP_MATERIAL_THRESHOLD  = 0.20  # the "+20% drawup" column
LOOSENING_FLOOR            = 0.50  # regime band edge
PARENT_LAG_GRACE_SESSIONS  = 2     # cor3m may be this far behind before it is an error
PARENT_READ_PAGE_ROWS      = 2000  # keyset page size for the cor_history read
```

These are Python module constants in `scripts/fetch_vixcor.py` and mirrored as
TypeScript consts in `web/lib/vixcor.ts` for display copy only. **The UI never
recomputes the window against a visible slice.** Every statistic in the payload
spans the full history.

### B.4 Window and minimum-observation rule

`WINDOW = 20`, trailing, **inclusive of the current session**: joined rows
`[i−19 .. i]`. Uniquely determined by sensitivity at the calibration date:

| N | corr on 2026-08-14 |
|---|---|
| 19 inclusive | +0.0675 |
| **20 inclusive** | **+0.0150** ✅ |
| 21 inclusive | −0.0363 |
| 20 excluding today | +0.0298 |

Rows `0 .. 18` of the joined series carry `corr20: null`. The row is still
emitted so the date axis is continuous. The first emittable date on the full
series is **2006-01-31**.

### B.5 Gap handling: inner join on session date. Never forward fill.

- `cor3m` dates are a strict **subset** of VIX dates. cor3m-only dates since
  2006: **0**. VIX-only dates since 2006: **47**, all Cboe index-holiday and
  half-day gaps (2026-07-03, 2026-06-19, 2026-05-25, ...).
- Inner join therefore ≡ "the cor3m session calendar". **5,171 joined rows.**
- `cor_history.cor3m` has 15 scattered NULLs, all pre-2021 (latest 2020-11-17).
  They drop out of the joined calendar. A 20-observation window spanning one of
  them covers 21 calendar sessions. That is correct: StockCharts correlates
  plotted bars, not calendar days.
- **Forward fill is banned.** It pairs a synthetic repeated cor3m point with a
  genuinely-moved VIX and mechanically drags the correlation. Measured over
  2024+: mean |Δ| 0.0157, **max |Δ| 0.203** — enough to fabricate or erase a
  trough. The 2026-08-14 calibration value happens to agree under both (no
  holiday in that window), so the calibration pin alone does not discriminate.
  Pin the ban with the gap test in `H.3` instead.

### B.6 Breakdown-episode definition

Two thresholds with hysteresis, because the value fell +0.878 → +0.015 in 11
sessions (2026-07-30 → 2026-08-14) and a single threshold renders one break as
several.

1. **Candidate runs.** Maximal runs of consecutive joined sessions with
   `corr20 < BREAKDOWN_EXIT` (0.30).
2. **Merge.** Two candidate runs separated by `<= EPISODE_MERGE_SESSIONS` (10)
   joined sessions become one run.
3. **Confirm.** A merged run is an **episode** only if it contains at least one
   session with `corr20 < BREAKDOWN_TRIGGER` (0.25). Runs that dip below 0.30
   but never below 0.25 are not episodes.
4. **Trigger.** The episode's `trigger` is the first session in the merged run
   with `corr20 < BREAKDOWN_TRIGGER`.
5. **Debounce.** If a confirmed episode's trigger falls within
   `EPISODE_DEBOUNCE_SESSIONS` (42) joined sessions of the previously *emitted*
   episode's trigger, it is absorbed into that episode: extend the prior
   episode's `end` and re-evaluate its trough. It does not emit a new episode.
6. **Extent.** `start` = first session of the merged run (the 0.30 cross),
   `end` = last session of the merged run. `trough` / `trough_date` = the
   minimum `corr20` and its date within `[start, end]`.
7. **Open episodes.** An episode is `open: true` when its `end` equals the
   latest series date **and** that session's `corr20 < BREAKDOWN_EXIT`. An open
   episode is excluded from all forward-statistics aggregates (its forward
   window has not resolved).

All comparisons strict (`<`), consistently. `corr20 == 0.25` is NOT a trigger.
`corr20 == 0.30` closes a run.

**Expected counts at these constants (regression pins):** 31 episodes over
2006-01-31 .. 2026-08-14; 5 episodes with `trigger >= 2024-01-01`; the newest
one `open: true`.

### B.7 Forward statistics (descriptive only)

For a session index `i` and horizon `h`:

```
drawup(i, h)   = max(VIX[i+1 .. i+h]) / VIX[i] - 1        # None if fewer than h forward rows
p_higher(i, h) = VIX[i+h] > VIX[i]
```

Two aggregates over `FORWARD_HORIZONS`, both computed on the joined series:

- `forward_stats.event` — over resolved (`open: false`) episode **triggers**.
- `forward_stats.base` — over **every** emittable session (`corr20` non-null)
  with a full forward window. This is the null the UI must display alongside.

Each aggregate emits `{n, mean_drawup, median_drawup, p_higher, p_drawup_20}`
where `p_drawup_20` is the share with `drawup >= DRAWUP_MATERIAL_THRESHOLD`.

### B.8 Regime bands (strict inequalities)

| Band | Condition | Token |
|---|---|---|
| `DECOUPLED` | `corr20 < BREAKDOWN_TRIGGER` (0.25) | `var(--dislocation)` |
| `LOOSENING` | `0.25 <= corr20 < LOOSENING_FLOOR` (0.50) | `var(--warning)` |
| `COUPLED` | `corr20 >= 0.50` | `var(--text-muted)` |
| `null` | degenerate or pre-window | `var(--text-muted)`, renders `---` |

`DECOUPLED` is `--dislocation`, not `--negative`. It is a structural state, not
a fault. Using the fault token would be the UI over-claiming.

### B.9 Full-history distribution (n = 5,152 non-null, 2006-01-31 .. 2026-08-14)

mean +0.7621 · median +0.8446 · sd 0.2341 · min −0.5324 · max +0.9932

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|
| −0.1135 | +0.2586 | +0.4570 | +0.6795 | +0.8446 | +0.9250 | +0.9582 | +0.9723 | +0.9857 |

share > +0.75 = 67.45% · ≤ +0.50 = 12.29% · ≤ +0.25 = **4.89%** · ≤ 0.00 = 1.77%

`BREAKDOWN_TRIGGER = 0.25` is therefore the ~5th percentile of all history.

### B.10 Hand-computable worked examples

Implementers must pin these exactly. `corr_window(xs, ys)` is the pure function.

**Example 1 — nominal (use WINDOW=5 for the unit; the function takes the window
length from the slice it is handed).**

```
x = [10, 12, 14, 16, 18]      x̄ = 14
y = [20, 21, 23, 24, 27]      ȳ = 23
dx = [-4, -2, 0,  2,  4]      dy = [-3, -2, 0, 1, 4]
Σ dx·dy = 12 + 4 + 0 + 2 + 16 = 34
Σ dx²   = 16 + 4 + 0 + 4 + 16 = 40
Σ dy²   =  9 + 4 + 0 + 1 + 16 = 30
den     = sqrt(40 · 30) = sqrt(1200) = 34.641016151377546
r       = 34 / 34.641016151377546 = 0.9814954576223638
```

Assert `abs(r - 0.9814954576223638) < 1e-12`.

**Example 2 — perfect negative.**

```
x = [1, 2, 3]   y = [6, 4, 2]
dx = [-1, 0, 1]  dy = [2, 0, -2]
Σ dx·dy = -4 ; Σ dx² = 2 ; Σ dy² = 8 ; den = sqrt(16) = 4
r = -4 / 4 = -1.0        # exactly
```

**Example 3 — degenerate.**

```
x = [1, 2, 3]   y = [5, 5, 5]
Σ dy² = 0  →  den = 0  →  r is None      # not 0.0
```

**Example 4 — the live calibration pin.** Against the checked-in fixtures, the
20-session inclusive window ending 2026-08-14 must produce
`+0.014969` (assert to 6 decimal places), with `vix_close == 14.25` on that
date. This is the number the operator's chart prints as `0.01`.

### B.11 Events since 2024 (must reproduce the operator's five circles)

| # | trigger | corr@trigger | episode start .. end | trough | corr@trough | VIX@trigger |
|---|---|---|---|---|---|---|
| 1 | 2024-02-22 | +0.248 | 2024-02-16 .. 2024-03-26 | 2024-03-20 | −0.074 | 14.54 |
| 2 | 2024-06-03 | +0.219 | 2024-06-03 .. 2024-06-14 | 2024-06-05 | +0.118 | 13.11 |
| 3 | 2025-10-01 | +0.072 | 2025-09-30 .. 2025-10-09 | 2025-10-02 | +0.019 | 16.29 |
| 4 | 2026-05-22 | +0.235 | 2026-05-21 .. 2026-05-26 | 2026-05-22 | +0.235 | 16.70 |
| 5 | 2026-08-11 | +0.233 | 2026-08-11 .. 2026-08-14 | 2026-08-14 | +0.015 | 15.28 (open) |

---

## C. The schema — migration `0049_vixcor.sql`

Migration number **0049** is verified free: Turso `MAX(version) = 48` and the
local tree tops out at `0048_watchdog_pages.sql`. Do not renumber. Before
writing, re-check `SELECT MAX(version) FROM schema_migrations` and grep for
in-flight `ind/*` worktrees — parallel indicator builds have collided on a
migration number before, and the symptom is "nothing to apply".

```sql
-- 0049_vixcor.sql — VIXCOR indicator: the rolling 20-session Pearson
-- correlation between the VIX close and the Cboe 3-month SPX implied
-- correlation close, over the inner join of the two session calendars
-- (== the cor3m calendar; cor3m dates are a strict subset of VIX dates).
--
-- corr20 is NULL for the first WINDOW-1 joined sessions and for any
-- degenerate window where one leg is constant. vix_close and cor3m_close
-- are denormalized onto the row so the chart renders both panes from one
-- table without a second join at read time. Breakdown episodes are NOT
-- stored: they are a pure function of corr20 plus two named constants and
-- are recomputed in the ingest job on every run (see docs/indicators/vixcor.md
-- section C.2).

CREATE TABLE IF NOT EXISTS vixcor_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD, a joined session date
  vix_close   REAL NOT NULL,      -- Cboe VIX_History.csv CLOSE
  cor3m_close REAL NOT NULL,      -- cor_history.cor3m for the same date
  corr20      REAL,               -- NULL for the first 19 rows / degenerate windows
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vixcor_history_date ON vixcor_history (date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (49, datetime('now'));
```

Rules honored: natural PK on `date`, `recorded_at TEXT NOT NULL`, one `DESC`
index, trailing `schema_migrations` insert with the bare integer. No
registration elsewhere; `scripts/db/migrate.py` runs as the `radon-api`
`ExecStartPre`, and locally it is `bun run db:migrate`.

### C.1 No second table for VIX. Extend `price_history_daily`.

`price_history_daily (symbol, date, close, source, fetched_at)` already exists
(migration `0029_rv_ratio.sql`, 1,271,508 rows) and already has the chunked
batched writer `upsert_price_history_rows(symbol, rows)` /
`upsert_price_history_symbol_rows(rows)` at `scripts/db/writer.py:130-165`.

**Decision:** VIX daily closes land there as `symbol = 'VIX'`,
`source = 'cboe'`.

Justification:

- Zero new schema, zero new writer, and the existing writer already obeys the
  Hrana chunking rule.
- VIX daily closes are a general-purpose asset. `price_history_daily` currently
  has **zero** rows for VIX / ^VIX / $VIX. Putting them there makes them
  available to every other indicator instead of burying them in one child's
  table.
- `vixcor_history.vix_close` denormalizes the same number onto the joined row
  so the chart and the payload need no join. That duplication is deliberate and
  is a materialized read path, not a second source of truth. **`price_history_daily`
  with `symbol='VIX'` is the source of truth for VIX closes.**

### C.2 Episodes are derived, not persisted. Justification.

No `vixcor_episodes` table. Episodes are recomputed by
`scripts/fetch_vixcor.py` on every run from the full `corr20` series and
emitted into the payload (`scan_snapshots` + `data/vixcor.json`).

- They are a pure function of one persisted column and four named constants
  (`BREAKDOWN_TRIGGER`, `BREAKDOWN_EXIT`, `EPISODE_MERGE_SESSIONS`,
  `EPISODE_DEBOUNCE_SESSIONS`). A table would need a rebuild-and-reconcile path
  every time a constant is retuned, and would silently drift from the series
  the moment one run wrote and the next did not.
- The full-history pass is one loop over ~5,152 floats. It is free.
- The forward-statistics block needs the whole series anyway, so nothing is
  saved by persisting a subset.
- 31 episodes is a small object. It rides in the snapshot payload with no size
  concern (`SCAN_SNAPSHOT_KEEP = 30` prunes the snapshot table already).

---

## D. VIX ingestion

### D.1 Source

```
https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
```

Same CDN base `scripts/clients/cboe_client.py` already uses (`_DEFAULT_BASE_URL`).
**No new client.** Use `CboeClient().fetch_history("VIX", if_modified_since=...)`,
which returns `(text_or_None, last_modified)` and gives `(None, same_stamp)` on
a 304. The honest `radon/2.0` UA is already set. Never impersonate a browser.

Verified 2026-08-15: 9,251 data rows, `1990-01-02 .. 2026-08-14`, columns
`DATE,OPEN,HIGH,LOW,CLOSE`, `DATE` as `MM/DD/YYYY`, `Last-Modified: Sat, 15 Aug
2026 23:01:11 GMT`, final row `08/14/2026 ... 14.250000` — matching the
operator's printed VIX close of 14.25 exactly.

**Trap, already burned in this repo:** IB daily bars AND IB tick-9 close both
lag the official close by a session
(`feedback_ib_cor1m_lags_official_close.md`). Anchor to the official Cboe
history. Never to IB.

**Second trap, documented on `CboeClient`:** Cboe re-touches `Last-Modified`
intraday **without** appending the new session row. A 200 does not imply a new
session. All downstream logic keys off dates in the parsed rows, never off the
stamp.

### D.2 Parse

Reuse the exact `parse_index_csv()` shape from `scripts/fetch_cor.py:56-72`:
`csv.DictReader`, `datetime.strptime(DATE, "%m/%d/%Y")`, `float(row["CLOSE"])`,
`continue` on `KeyError | TypeError | ValueError`, sort ascending by ISO date.
Output `[{"date": "YYYY-MM-DD", "value": float}]`. Header rows, malformed dates
and empty `CLOSE` cells are skipped silently.

### D.3 Where the rows land

```python
writer.upsert_price_history_rows(
    "VIX",
    [{"date": r["date"], "close": r["value"], "source": "cboe"} for r in tail],
)
```

**Backfill strategy.**

- Default run: bounded tail only. Read `SELECT MAX(date) FROM
  price_history_daily WHERE symbol = 'VIX'` (one row), then write only parsed
  rows with `date >= that max`. On a normal day that is 1 row, one round trip.
- `--backfill`: write the full 9,251-row history. ~24 chunked round trips at
  `_PRICE_HISTORY_INSERT_CHUNK_ROWS = 400`. Run once at install, and again only
  if a lineage reset is needed (`writer.delete_price_history("VIX")` first).
- If the `MAX(date)` probe fails or returns NULL, fall back to the full write.
  An empty table means first install.

**Hrana bounding, non-negotiable:** never `executemany` (one round trip per
row). `upsert_price_history_symbol_rows` already batches at 400 rows per
statement; do not bypass it. Same rule for `vixcor_history` — see `E.3`.

### D.4 Reading the parent (`cor_history.cor3m`)

Keyset-paginated on the `date` cursor at `PARENT_READ_PAGE_ROWS = 2000`, fresh
connection for the phase:

```python
def load_cor3m_rows() -> list[dict[str, Any]]:
    """Turso cor_history first; data/cor.json series fallback when empty or unreachable.

    Keyset-paginated on date (Hrana I/O bounding): ~5,190 rows is three pages
    instead of one unbounded stream read.
    """
    try:
        from db.client import get_db
        db = get_db()
        out: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = db.execute(
                "SELECT date, cor3m FROM cor_history "
                "WHERE date > ? AND cor3m IS NOT NULL "
                "ORDER BY date LIMIT ?",
                (cursor, PARENT_READ_PAGE_ROWS),
            ).fetchall()
            if not page:
                break
            out.extend({"date": row[0], "value": float(row[1])} for row in page)
            cursor = page[-1][0]
            if len(page) < PARENT_READ_PAGE_ROWS:
                break
        if out:
            return out
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        print(f"[vixcor] turso cor3m rehydrate non-fatal: {exc}", file=sys.stderr)
    return _load_cor3m_from_json()
```

The JSON fallback reads `data/cor.json`'s `series`, keeps rows with a non-null
`cor3m`, and sorts ascending. Verification of a shipped run is always against
**Turso**, never `data/*.json`.

### D.5 Daily update path

1. Conditional GET the Cboe VIX CSV with the cached `source_last_modified.vix`.
2. `304` **and** a cached payload exists → reuse `{**cached, "scan_time": ...}`;
   write snapshot + heartbeat only; skip both row upserts
   (`rows_changed=False`). This is the weekend and holiday heartbeat path.
3. Otherwise re-fetch unconditionally if the conditional call returned `None`
   without a cache, parse, load cor3m, inner join, compute, and write
   everything.
4. `if not series: raise ValueError("vixcor series computed to zero rows")` —
   never cache empty.

---

## E. The job

### E.1 Script

`scripts/fetch_vixcor.py`. Model: `scripts/fetch_cor.py` (Cboe conditional GET
and the 304 fast path) crossed with `scripts/fetch_skew2d.py` (Turso parent read
and lag degradation). Composed method, section banners in this order:

```
#!/usr/bin/env python3
"""VIXCOR Indicator — 20-session correlation between VIX and Cboe COR3M.

Descriptive regime read, not a forecast: a low reading marks a quiet,
range-bound VIX tape. See docs/indicators/vixcor.md section 0 for the
validation study that rejected the predictive framing.

Source: Cboe CDN VIX_History.csv + Turso cor_history.cor3m.
Output is dual-written to Turso vixcor_history + data/vixcor.json;
VIX closes also land in price_history_daily as symbol='VIX', source='cboe'.

Usage:
    python3 scripts/fetch_vixcor.py              # human summary (stderr)
    python3 scripts/fetch_vixcor.py --json       # JSON to stdout
    python3 scripts/fetch_vixcor.py --backfill   # full 9,251-row VIX history write
"""
# ── path setup ──      _SCRIPT_DIR / _PROJECT_DIR, sys.path.insert(0, scripts),
#                       load_dotenv(.env) then load_dotenv(web/.env) in try/except
# ── constants ──       VIXCOR_JSON, plus every constant in section B.3
# ── parsing ──         parse_index_csv(text)               (verbatim from fetch_cor.py)
# ── parent ──          load_cor3m_rows(), _load_cor3m_from_json()
# ── math ──            join_series(vix_rows, cor3m_rows)
#                       corr_window(xs, ys) -> Optional[float]
#                       compute_corr_series(joined) -> list[dict]
#                       detect_episodes(series) -> list[dict]
#                       compute_forward_stats(series, episodes) -> dict
#                       compute_stats(series) -> dict
#                       build_current(series, episodes, stats) -> Optional[dict]
# ── persistence ──     _write_db(payload, scan_time, *, rows_changed, vix_tail)
#                       persist_json(payload) / load_prior_payload()
# ── degradation ──     _market_status(now), _hold_for_parent_lag(...)
# ── orchestration ──   run(client=None, *, now=None, backfill=False) -> dict
# ── CLI ──             _print_summary(payload) [stderr], main()
```

**stdout discipline:** every progress line is
`print(f"[vixcor] ...", file=sys.stderr)`. stdout carries the result JSON only,
and only under `--json`. The subprocess bridge parses stdout.

**Atomic JSON write** (skew2d form, not cor's plain `write_text`):

```python
tmp = VIXCOR_JSON.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, indent=2))
os.replace(tmp, VIXCOR_JSON)
```

**Timestamps:** `scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")`.

**Client injection:** `CboeClient` is constructed lazily inside `run()` so tests
pass a `_StubClient`.

### E.2 CLI

`argparse` with exactly `--json` (`action="store_true"`) and `--backfill`
(`action="store_true"`). `run()` returns the payload. `main()` prints
`json.dumps(payload, indent=2)` to stdout under `--json`, else `_print_summary`
to stderr.

### E.3 Writer — `scripts/db/writer.py`

Add `upsert_vixcor_rows` immediately after `upsert_cor_rows`
(`scripts/db/writer.py:914-952`), copying it verbatim and changing the column
tuple:

```python
def upsert_vixcor_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """VIXCOR indicator — one row per joined session (VIX x COR3M), idempotent
    on date. corr20 is NULL for the first WINDOW-1 rows and for degenerate
    windows.

    Chunked multi-row INSERTs (Hrana I/O bounding): a changed-source run
    rewrites the full ~5,170-session series, which per-row would be thousands
    of statements on one stream (the rv-ratio 2026-07-21 502 incident).
    ~13 chunked round-trips instead.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (row["date"], row["vix_close"], row["cor3m_close"], row.get("corr20"), stamp)
            )
        db.execute(
            "INSERT INTO vixcor_history (date, vix_close, cor3m_close, corr20, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "vix_close = excluded.vix_close, cor3m_close = excluded.cor3m_close, "
            "corr20 = excluded.corr20, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()
```

Reused unchanged: `ensure_no_replica_for_writers()`, `upsert_scan_snapshot()`,
`record_service_health()`, `upsert_price_history_rows()`.

Write order every cycle:

```python
writer.ensure_no_replica_for_writers()
if rows_changed:
    writer.upsert_price_history_rows("VIX", vix_tail)   # bounded tail, or full under --backfill
    writer.upsert_vixcor_rows(payload["series"], recorded_at=scan_time)
writer.upsert_scan_snapshot("vixcor", scan_time, payload)          # EVERY cycle
writer.record_service_health("vixcor", "ok", finished_at=scan_time)  # EVERY cycle
```

All of it inside one `try/except Exception` that prints
`f"[vixcor] db cache non-fatal: {exc}"` to stderr, then the JSON fallback write
runs regardless. The snapshot and heartbeat run on **every** cycle including the
304 path, so a silently dead writer trips the staleness banner
(`feedback_service_health_heartbeat`).

### E.4 Cadence, unit and timer

`OnCalendar=*-*-* 02:35:00 UTC`, daily, every calendar day. Verified free:
straddle 02:15, cor 02:20, nothing at 02:35. It must run **after** `radon-cor`
so the parent row for the session exists; cor's `TimeoutStartSec=300` means the
gap has to clear 02:25, so 02:35 is the first safe slot.

`cloud/services/radon-vixcor.service`:

```ini
[Unit]
Description=Radon VIXCOR Indicator (Cboe VIX x COR3M 20d correlation, daily)
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=oneshot
User=radon
WorkingDirectory=/home/radon/radon
EnvironmentFile=/home/radon/radon-cloud/.env
Environment=RADON_DB_NO_REPLICA=1
ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_vixcor.py
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

Venv python **directly**. Never a `run_*.sh` wrapper: its fallback ladder
resolves `/usr/bin/python3.13` on the VPS
(`feedback_scan_wrapper_fallback_picks_system_python.md`).

`cloud/services/radon-vixcor.timer`:

```ini
[Unit]
Description=Radon VIXCOR Indicator - daily Cboe VIX x COR3M correlation

[Timer]
# 02:35 UTC, 15 minutes behind radon-cor.timer (02:20 UTC, TimeoutStartSec=300)
# so the COR3M row for the session is already in Turso when this child reads
# it. Runs every calendar day: weekend and holiday runs are 304 heartbeats
# that keep service_health fresh inside the 26h staleness window.
OnCalendar=*-*-* 02:35:00 UTC
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

A daily oneshot does not need `StartLimitBurst=10`; that applies to sub-5-minute
oneshots.

**The three CI contracts plus the sha256 bump — all in the same commit:**

1. `cloud/scripts/setup-vps.sh` — append `radon-vixcor.service` and
   `radon-vixcor.timer` to the `SERVICE_FILES` array. This is the only scripted
   install path; deploys do not install units.
2. `cloud/tests/test_systemd_services.py` — append both to
   `EXPECTED_SERVICE_FILES`.
3. `cloud/config/installed-units.sha256` — add `<sha256>  radon-vixcor.service`
   and `<sha256>  radon-vixcor.timer` (two spaces), matching the root
   install-copy. If the host install has not happened yet, instead add
   `not-installed:radon-vixcor.service` / `not-installed:radon-vixcor.timer` to
   `cloud/config/drift-allowlist.conf` for the pending window. One or the
   other, or `cloud/tests/test_unit_install_acknowledgment.py` fails.

Host install, root SSH, once by hand:

```
install -m 0644 cloud/services/radon-vixcor.service /etc/systemd/system/
install -m 0644 cloud/services/radon-vixcor.timer   /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now radon-vixcor.timer
```

**A scan endpoint with no caller is a known repo failure mode.** The timer and
the every-cycle heartbeat are part of the definition of done, not follow-up.

### E.5 service_health key and staleness windows

Key: `vixcor`. Lowercase, no underscores. The identical string appears in five
places and they are pinned against each other:
`writer.record_service_health("vixcor", ...)`,
`writer.upsert_scan_snapshot("vixcor", ...)`, the Next.js route's
`WHERE service = 'vixcor'` plus its `dbFirstRead` label,
`web/lib/serviceHealthWindows.ts`, `scripts/watchdog/services.py`.

`web/lib/serviceHealthWindows.ts`, immediately after the `"cor"` entry
(line ~169):

```ts
// ``vixcor`` — radon-vixcor.timer fires daily 02:35 UTC every calendar day,
// 15 minutes behind radon-cor (weekend and holiday runs are 304 heartbeats),
// so a uniform 26h window matches its cor / straddle siblings. Cboe CDN plus
// Turso cor_history only — no IB.
"vixcor": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
```

`scripts/watchdog/services.py`, **two** edits:

- the window dict beside `"cor"` (line ~122):
  `"vixcor": {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False},`
- the daily-bucket check list beside `"cor"` (line ~300): add `"vixcor"` with a
  comment naming the 02:35 UTC cadence, so the hourly check surfaces a missed
  run within 1h of the 26h window expiring.

The TS and Python windows must agree. `extended == closed` for a daily writer.
No row ever means dormant (a brand-new timer before its first fire) and the
watchdog must not page on it.

### E.6 Market-calendar gate

No market-hours gate on execution: this is a daily post-close job. The calendar
is used for two things only, both through `scripts/utils/market_calendar.py`:

```python
from utils.market_calendar import market_state, last_completed_session_date
```

- `_market_status(now)` returns `"open"` / `"closed"` from
  `market_state(now)["is_open"]`, wrapped in `try/except` defaulting
  `"closed"` (copy `fetch_skew2d.py:187-194`). It goes into the payload as
  `market_status`.
- `last_completed_session_date(now)` gives `expected_session` for the parent-lag
  comparison in `E.7`.

`ZoneInfo("America/New_York")` only, and only inside `market_calendar`. Never a
hardcoded TZ offset anywhere in this indicator.

### E.7 Parent-lag degradation. THE critical rule.

`cor_history` can legitimately be a session behind. A derived child that treats
parent lag as corruption fails its unit and pages the operator on every run
(`feedback_derived_indicator_parent_embargo.md`). Three-state ladder:

```python
expected_session = last_completed_session_date(now)
latest_parent    = max(r["date"] for r in cor3m_rows)
lag_sessions     = count_vix_sessions_between(latest_parent, expected_session)
```

`count_vix_sessions_between` counts VIX session dates strictly after
`latest_parent` and at or before `expected_session`. Use the VIX calendar, not
calendar days: a Friday-to-Monday gap is one session, not three.

| Condition | `status` | Rows | Snapshot | Heartbeat | Raise? |
|---|---|---|---|---|---|
| `lag_sessions == 0` | `"ok"` | upsert when changed | yes | `ok` | no |
| `0 < lag_sessions <= PARENT_LAG_GRACE_SESSIONS` (2) | `"holding"` | upsert the sessions we DO have | yes | **`ok`** | **no** |
| `lag_sessions > 2` | `"stale_parent"` | upsert the sessions we DO have | yes | `error` with `{message, next_attempt_at}` | **no** |
| zero joined rows, or the VIX fetch yields nothing and no cache | n/a | none | none | none | **yes** — `ValueError` |

Notes that implementers get wrong:

- The `holding` heartbeat is **`ok`**, not `error`. skew2d writes an `error` row
  on its embargo hold because a spent UW daily cap is an externally-scheduled
  outage with a known `next_attempt_at`. A one-session COR3M lag is routine
  publication timing and must not page. This is the deliberate divergence from
  the skew2d template.
- `stale_parent` (lag > 2 sessions) **does** page, via the `error` heartbeat and
  the watchdog, because that is a genuinely broken parent. It still must not
  `raise`: raising fails the systemd unit and produces a second, duplicate page
  plus a failed-unit that survives the next successful run.
- The `holding` and `stale_parent` paths still write the joined series for the
  sessions that DO exist and still refresh the snapshot, so the tab keeps
  serving and `as_of` tells the truth.
- `as_of` is always `max(joined dates)` — that is, the parent's latest date, not
  the VIX file's. Surface it. Never forward-fill to close the gap.
- A parsed VIX file that is itself behind `expected_session` (Cboe published
  late) with a matching `latest_parent` is `status: "ok"`, `lag_sessions == 0`.
  One shared missing session is not corruption.

---

## F. The API

### F.1 FastAPI: none.

Verified: `cor`, `straddle`, `skew2d`, `margin-debt` and `yield-curve` have no
FastAPI route. `scripts/api/routes/` holds only `assistant_market.py`,
`historical.py`, `preferences.py`. The systemd timer invokes the Python script
directly and the Next.js route reads Turso. **Do not add a FastAPI route.**
There is no operator-triggered rescan for this indicator, and no live intraday
overlay in v1.

### F.2 Next.js route — `web/app/api/vixcor/route.ts`

Copy `web/app/api/cor/route.ts` verbatim (68 lines) and rename. Exact shape:

```ts
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "vixcor.json");

// Contract: absent vixcor data is HTTP 200 with missing:true, never a 4xx.
const MISSING_VIXCOR = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  episodes: [],
  current: null,
  stats: null,
  forward_stats: null,
};

// radon-vixcor.timer fires daily at 02:35 UTC including weekends (Cboe
// re-touch days heartbeat via conditional GET), so a snapshot older than
// two days means the writer is down.
const VIXCOR_MAX_AGE_MS = 48 * 60 * 60_000;
```

`readVixcorFromDb()` selects
`scan_time, payload FROM scan_snapshots WHERE service = 'vixcor' ORDER BY scan_time DESC LIMIT 1`
and returns `{ data: JSON.parse(row.payload), timestampMs: contentTimestampMs(row.scan_time) }`.
`readVixcorFromDisk()` reads `CACHE_PATH`. `GET()` runs `dbFirstRead({ fromDb,
fromDisk, maxAgeMs: VIXCOR_MAX_AGE_MS, label: "vixcor" })` and returns
`NextResponse.json(result.ok ? result.data : MISSING_VIXCOR)` through
`setCacheResponseHeaders(response, { maxAgeSeconds: 300,
staleWhileRevalidateSeconds: 3600, requestId, cacheState: "HIT", tags:
["vixcor"] })`.

The route **never** transforms the payload, never recomputes anything, and never
returns a 4xx. Missing and pending are 200 plus `missing: true`
(`feedback_http_status_for_real_errors`).

### F.3 Payload shape

Written by the job to both `scan_snapshots` and `data/vixcor.json`; served
verbatim by the route; typed in `web/lib/vixcor.ts`.

```jsonc
{
  "scan_time": "2026-08-16T02:35:11Z",
  "source_last_modified": { "vix": "Sat, 15 Aug 2026 23:01:11 GMT" },

  "status": "ok",                 // "ok" | "holding" | "stale_parent"
  "as_of": "2026-08-14",          // max joined session date
  "parent_as_of": "2026-08-14",   // max cor_history.cor3m date
  "vix_as_of": "2026-08-14",      // max Cboe VIX date
  "expected_session": "2026-08-14",
  "lag_sessions": 0,
  "market_status": "closed",      // market_state() at scan time

  "window": 20,
  "count": 5171,                  // joined rows, including the 19 null-corr leading rows
  "corr_count": 5152,             // rows with a non-null corr20

  "current": {
    "date": "2026-08-14",
    "vix_close": 14.25,
    "cor3m_close": 12.34,
    "corr20": 0.014969,
    "change_1d": -0.041300,       // corr20 minus the prior non-null corr20
    "percentile": 0.0181,         // share of non-null corr20 STRICTLY below latest
    "regime": "DECOUPLED",        // DECOUPLED | LOOSENING | COUPLED
    "vix_cov_20d": 0.0512,        // stddev/mean of VIX over the same 20 joined sessions
    "episode": {                  // the open episode, or null
      "trigger": "2026-08-11",
      "start": "2026-08-11",
      "end": "2026-08-14",
      "sessions": 4,
      "trough": 0.014969,
      "trough_date": "2026-08-14",
      "vix_at_trigger": 15.28,
      "open": true
    }
  },

  "stats": {
    "min": -0.5324, "p01": -0.1135, "p05": 0.2586, "p10": 0.4570,
    "p25": 0.6795, "median": 0.8446, "p75": 0.9250, "p90": 0.9582,
    "p95": 0.9723, "p99": 0.9857, "max": 0.9932,
    "mean": 0.7621, "stddev": 0.2341,
    "share_below_zero": 0.0177,
    "share_below_trigger": 0.0489,
    "vix_cov_breakdown": 0.0691,   // mean 20d VIX CoV in sub-trigger windows
    "vix_cov_coupled": 0.1180      // mean 20d VIX CoV in windows >= 0.75
  },

  "episodes": [
    {
      "trigger": "2025-10-01", "start": "2025-09-30", "end": "2025-10-09",
      "sessions": 8, "trough": 0.019, "trough_date": "2025-10-02",
      "corr_at_trigger": 0.072, "vix_at_trigger": 16.29, "open": false,
      "forward": { "5": 0.058, "10": 0.330, "21": 0.554, "42": 0.622, "63": null }
    }
  ],

  "forward_stats": {
    "horizons": [5, 10, 21, 42, 63],
    "event": { "5": { "n": 30, "mean_drawup": 0.0392, "median_drawup": 0.0301,
                      "p_higher": 0.400, "p_drawup_20": 0.067 } },
    "base":  { "5": { "n": 5147, "mean_drawup": 0.0896, "median_drawup": 0.0546,
                      "p_higher": 0.462, "p_drawup_20": 0.154 } }
  },

  "series": [
    { "date": "2006-01-03", "vix_close": 11.19, "cor3m_close": 40.12, "corr20": null, "episode": false },
    { "date": "2026-08-14", "vix_close": 14.25, "cor3m_close": 12.34, "corr20": 0.014969, "episode": true }
  ]
}
```

`series[].episode` is a boolean marking membership in a detected episode, so the
chart can shade without re-deriving. Every numeric field is `null`, never `NaN`
and never a sentinel like `-1`.

### F.4 Degradation states, end to end

| Job `status` | Route | Panel |
|---|---|---|
| `ok` | 200, payload verbatim | normal render |
| `holding` | 200, payload verbatim | normal render plus an `AS OF` cell showing `as_of` and a muted `PARENT 1 SESSION BEHIND` sub-label, derived from `lag_sessions` |
| `stale_parent` | 200, payload verbatim | normal render plus the same cell with `PARENT {lag_sessions} SESSIONS BEHIND` in `var(--warning)` |
| no snapshot, no disk file | 200 with `MISSING_VIXCOR` | `SectionEmptyState` |
| snapshot older than 48h | 200 with `MISSING_VIXCOR` (`dbFirstRead` maxAge) | `SectionEmptyState` |

Never a 4xx. Never a thrown error reaching the client.

---

## G. The UI

### G.1 Files

| File | Role |
|---|---|
| `web/lib/vixcor.ts` | types, formatters, regime classifier, chart-row builder |
| `web/lib/useVixcor.ts` | `useSyncHook` wrapper |
| `web/components/VixCorChart.tsx` | the two-pane d3-svg chart |
| `web/components/VixCorPanel.tsx` | the tab body |
| `web/app/regime/vixcor/page.tsx` | 5-line `WorkspaceShell` page |
| `web/components/RegimePanel.tsx` | five registration edits |

Do **not** touch `web/components/DashboardNewsFeed.tsx`,
`web/components/NewsfeedTagBar.tsx`, `web/lib/newsfeedTime.ts`, or
`web/components/DashboardNewsFeed.module.css` — another workflow owns them. Any
`web/app/globals.css` additions go in one clearly delimited block.

### G.2 `web/lib/vixcor.ts`

Model `web/lib/cor.ts`. Exports:

- Interfaces mirroring `F.3` exactly: `VixcorEntry`, `VixcorEpisode`,
  `VixcorCurrent`, `VixcorStats`, `VixcorForwardBucket`, `VixcorForwardStats`,
  `VixcorData` (with `missing?: boolean` and `status`).
- `formatCorr(v)` → 2dp signed, `"---"` for `null` / non-finite.
- `formatVix(v)` → 2dp, `"---"` for `null`.
- `formatPercentile(v)` → `"1.8%"`, `"---"` for `null`.
- `formatDrawup(v)` → `"+33.0%"`, `"---"` for `null`.
- `vixcorRegime(corr20)` → `"DECOUPLED" | "LOOSENING" | "COUPLED" | null`, using
  the strict inequalities of `B.8`.
- `vixcorRegimeColor(regime)` → `var(--dislocation)` / `var(--warning)` /
  `var(--text-muted)`.
- `buildVixcorChartRows(series, range)` → the row type the chart consumes.
- `BREAKDOWN_TRIGGER = 0.25`, `BREAKDOWN_EXIT = 0.30`, `CORR_WINDOW = 20` as
  display-copy constants. They are never used to recompute anything.

### G.3 `web/lib/useVixcor.ts`

```ts
"use client";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixcorData } from "./vixcor";

const VIXCOR_SYNC_CONFIG = {
  endpoint: "/api/vixcor",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixcorData) => d.scan_time,
};

export function useVixcor(): UseSyncReturn<VixcorData> {
  return useSyncHook<VixcorData>(VIXCOR_SYNC_CONFIG, true);
}
```

`useSyncHook` already sends `cache: "no-store"`.

### G.4 `web/components/VixCorChart.tsx` — the two-pane chart

`CriHistoryChart` cannot express this: it is one plot with a two-element series
tuple and no reference line or shading. Build a dedicated component modeled on
`web/components/InternalsSkewChart.tsx`, which is the repo's precedent for two
panes sharing one x-scale inside a single SVG.

Wrap in `ChartPanel` with `family="analytical-time-series"`. The `d3-svg`
renderer is sanctioned here under
`chart-system-spec.json → sanctionedRenderers["d3-svg"]` ("scale logic or
interaction complexity materially exceeds the shared SVG primitives"): two
y-scales in stacked panes, a zero reference line, threshold bands and episode
shading spanning both panes.

Layout constants:

```ts
const VIX_PANE_HEIGHT  = 260;
const CORR_PANE_HEIGHT = 168;
const PANE_GAP         = 12;
const TOTAL_HEIGHT     = VIX_PANE_HEIGHT + PANE_GAP + CORR_PANE_HEIGHT;  // 440, matching CriHistoryChart
const VIX_MARGIN       = { top: 16, right: 48, bottom: 8,  left: 52 };
const CORR_MARGIN      = { top: 4,  right: 48, bottom: 28, left: 52 };
```

The 16px surface padding and 4px radius come from `ChartPanel` /
`chart-system-spec.json` (`surface.paddingPx: 16`, `surface.radiusPx: 4`). Do
not hand-roll padding on the chart.

**Upper pane — VIX.**

- Single line, `chartSeriesColor("primary")`.
- Left y-axis, linear, domain padded 4% on both ends.
- No x tick labels (the lower pane owns the shared x-axis).

**Lower pane — corr20.**

- Single line, `chartSeriesColor("comparison")`.
- Fixed y-domain `[-0.6, 1.0]`. Fixed, not data-driven: a fixed scale is what
  makes 0.01 look like a plunge and 0.90 look like the norm, which is the whole
  point of the panel. Full-history min is −0.5324, so −0.6 never clips.
- **Zero line:** a 1px solid rule at `y = 0`, `var(--border-dim)`, drawn under
  the series, labelled `0.00` in the axis font.
- **Threshold rule:** a 1px dashed rule at `y = BREAKDOWN_TRIGGER` (0.25) in
  `var(--dislocation)` at 45% opacity via `color-mix`, labelled `0.25`.
- Y ticks at `-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0`.
- x-axis at the bottom with `xTickFormat` = the shared `"05 Aug 26"` day tick
  (`toLocaleDateString("en-GB", { day: "2-digit", month: "short", year:
  "2-digit", timeZone: "UTC" })`).

**Episode marking (both panes).**

- For each episode intersecting the visible range, one `rect` spanning
  `[start, end]` on the x-scale, full height of BOTH panes plus the gap, filled
  `color-mix(in srgb, var(--dislocation) 12%, transparent)`, no stroke, drawn
  first so the lines sit above it.
- An open episode additionally gets a 1px right edge in
  `color-mix(in srgb, var(--dislocation) 55%, transparent)` and an
  `data-testid="vixcor-episode-open"` attribute, so the live unresolved one is
  distinguishable.
- **No arrows.** No annotation pointing from a trough to a later VIX high. The
  operator's blue arrows are exactly the over-claim this tab must not make.

**Tooltip.** Shared crosshair across both panes. Rows: date, `VIX`,
`COR3M`, `CORR 20D`, and `EPISODE` (`YES` / `NO`). No forward-return row.

**NaN safety.** Null `corr20` breaks the lower line into segments via
`d3.line().defined(d => d.corr20 != null)`. No path `d` attribute may ever
contain the string `NaN`; this is pinned in `H.8`.

**Theme.** All colors are `var(--token)` or `color-mix(in srgb, var(--token) N%,
transparent)`. No raw hex, no `rgba()` literals. Grid `var(--chart-grid,
var(--border-dim))`, axis `var(--chart-axis, var(--border-dim))`, tick labels
`var(--chart-axis-muted, var(--text-secondary))`, surface `var(--chart-surface,
var(--bg-panel))`. Verify both `data-theme="light"` and `data-theme="dark"`.

### G.5 `web/components/VixCorPanel.tsx`

Model `CorPanel.tsx`. Ordering is not optional.

1. `const { data, loading, syncing, lastSync } = useVixcor();` and
   `const { isMobile, hasMounted } = useViewport(); const compact = hasMounted && isMobile;`
2. `const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);`
   `const [customRange, setCustomRange] = useState<[number, number] | null>(null);`
   `const activePreset = preset ?? "all";` (multi-decade series defaults to full
   history, exactly like `CorPanel`).
3. `chartRange` via `useMemo` → `presetRange(...)` from `@/lib/historyRange`,
   with the custom-range clamp copied from `CorPanel`.
4. Loading gate:
   `if ((loading || syncing) && !data) return <SpectralLoader label="Loading Cboe VIX and implied correlation series" />;`
5. Empty gate:
   ```tsx
   if (!data || data.missing || !data.current || series.length === 0) {
     return (
       <SectionEmptyState
         icon={Unlink}
         headline="No VIX correlation data yet"
         secondary="The radon-vixcor refresh timer populates this tab from the Cboe VIX history and the COR3M series already in Turso. Data appears after the first successful pull."
       />
     );
   }
   ```
   lucide icon: `Unlink`.
6. Summary strip inside `<div className="section">` with
   `.section-header` / `.section-title`, the `Unlink` icon, an `InfoTooltip`
   carrying `VIXCOR_TOOLTIP`, and the `lastSync` clock. Then
   `compact ? <div className="m-regime-grid2x2">…MetricCell…</div> :
   <RegimeStrip>…RegimeStripCell…</RegimeStrip>`.

   Cells, in order:

   | testId | label | value | sub |
   |---|---|---|---|
   | `vixcor-strip-corr` | `CORR 20D` | `formatCorr(current.corr20)` | `1D {formatCorr(current.change_1d)}` |
   | `vixcor-strip-regime` | `REGIME` | `regime` in `vixcorRegimeColor(regime)`, `data-testid="vixcor-regime-value"` | `PCTILE {formatPercentile(current.percentile)}` |
   | `vixcor-strip-vix` | `VIX` | `formatVix(current.vix_close)` | `20D COV {formatPercentile(current.vix_cov_20d)}` |
   | `vixcor-strip-cor3m` | `COR 3M` | `formatCor(current.cor3m_close)` | `CBOE 3M IMPLIED CORRELATION` |
   | `vixcor-strip-episode` | `EPISODE` | open episode `start` or `NONE` | `{sessions} SESSIONS, TROUGH {formatCorr(trough)}` or `LAST {lastClosed.start}` |
   | `vixcor-strip-asof` | `AS OF` | `current.date` | `{lagCopy}` (see `F.4`) |

   `lagCopy` is derived from `data.lag_sessions`, never hardcoded. `0` →
   `DAILY SERIES SINCE 2006-01`. `1` → `PARENT 1 SESSION BEHIND`. `n>1` →
   `PARENT {n} SESSIONS BEHIND` in `var(--warning)`.

7. Chart block: `<div className="breadth-history-block" data-testid="vixcor-chart-section">` containing, in order:
   - `<HistoryRangeChips active={activePreset} onChange={...} maxSessions={total} ariaLabel="VIX correlation chart range" dataTestId="vixcor-range-chips" />`
   - `<VixCorChart history={slice} episodes={visibleEpisodes} title="VIX VS 3-MONTH IMPLIED CORRELATION" xTickFormat={formatDayTick} />`
   - `{total >= 2 && <BrushMinimap values={series.map(e => e.corr20)} range={chartRange} onRangeChange={setCustomRange} onCustom={() => setPreset("custom")} testIdPrefix="vixcor-brush" ariaLabel="VIX correlation history range brush" />}`
   - the 9px `var(--text-muted)` `SOURCE_FOOTNOTE`.
8. **The base-rate block** (`data-testid="vixcor-base-rate"`), immediately below
   the chart block. This is a required element, not decoration. A small table,
   `family="distribution-bar"` styling or a plain `.section` table:

   Three paired measures, because the rebuttal is scoped to the mean and the
   reader has to be able to see that for themselves. Column groups
   `MEAN DRAWUP` / `MEDIAN DRAWUP` / `SHARE REACHING +20%`, each split into
   `BREAKDOWN` and `ALL SESSIONS`:

   | HORIZON | MEAN, BREAKDOWN | MEAN, ALL | MEDIAN, BREAKDOWN | MEDIAN, ALL | +20%, BREAKDOWN | +20%, ALL |
   |---|---|---|---|---|---|---|
   | 5D | +3.9% | +9.0% | +3.8% | +5.5% | 6.7% | 15.4% |
   | 10D | +8.0% | +15.8% | +7.7% | +10.1% | 16.7% | 28.2% |
   | 21D | +20.9% | +27.4% | +14.5% | +17.9% | 36.7% | 45.9% |
   | 42D | +33.0% | +44.0% | +31.8% | +29.3% | 63.3% | 63.8% |
   | 63D | +43.9% | +57.3% | +33.1% | +39.4% | 69.0% | 72.6% |

   The 42D row is the point of the block: the mean is 11.00pp below, the median
   is 2.48pp ABOVE, and the +20% share is a wash.

   Values come from `data.forward_stats` (`mean_drawup`, `median_drawup`,
   `p_drawup_20`), never hardcoded. Header copy:
   `FORWARD VIX DRAWUP, BREAKDOWNS VERSUS ALL SESSIONS`. Footnote copy verbatim:

   > Forward VIX drawup after a breakdown is below the all-session mean at every
   > horizon, while on the median the two are indistinguishable at 42 sessions
   > and the share reaching +20% is the same. The mean gap comes from the right
   > skew of the all-session distribution. This is a regime description, not a
   > forecast.

### G.6 Copy

```ts
const VIXCOR_TOOLTIP =
  "Rolling 20-session Pearson correlation between the VIX close and the Cboe 3-month SPX implied correlation close, computed on price levels over the two series' shared session calendar. Below 0.25 is DECOUPLED, 0.25 to 0.50 is LOOSENING, 0.50 and above is COUPLED. A level correlation collapses when either leg goes range-bound, so a low reading marks a quiet VIX tape: breakdown windows average a 20-day VIX coefficient of variation of 6.9 percent against 11.8 percent when the two are coupled. Forward VIX drawup after a breakdown runs below the all-session mean at every horizon, while on the median the two are indistinguishable at 42 sessions, so read this as a regime state, not a warning.";

const SOURCE_FOOTNOTE =
  "Cboe VIX daily closes and the COR3M implied correlation index, inner joined on session date since 2006-01. Shaded bands are breakdown episodes, defined as a run below 0.30 containing at least one session below 0.25. The window and all statistics span the full history, not the visible range.";
```

Banned strings anywhere in the panel, chart, tooltip or footnote, pinned by test
`H.8.7`: `warning shot`, `warning`, `predicts`, `predictive`, `signal ahead of`,
`forecast`, `hit rate`, `4 of 4`, `4-for-4`, and any em dash.

### G.7 The current regime read (what the tab says on 2026-08-14 data)

- `CORR 20D` **+0.01**, 1D change **−0.04**
- `REGIME` **DECOUPLED**, `PCTILE` **1.8%** (below the 2nd percentile of 5,152
  sessions)
- `VIX` **14.25**, `20D COV` low
- `COR 3M` its 2026-08-14 close
- `EPISODE` **2026-08-11**, 4 sessions, trough **+0.01**, still open
- `AS OF` **2026-08-14**

The lower pane shows the fifth shaded band running to the right edge with its
open-episode edge marker and no arrow. The base-rate block sits underneath it,
so the operator reads the live decoupling against a 64% null instead of a
4-of-4 hit rate. That is the entire editorial job of this tab.

### G.8 `web/app/regime/vixcor/page.tsx`

```tsx
import WorkspaceShell from "@/components/WorkspaceShell";

export default function RegimeVixcorPage() {
  return <WorkspaceShell section="regime" />;
}
```

### G.9 `web/components/RegimePanel.tsx` — six edit sites

| Site | Current line | Edit |
|---|---|---|
| imports | 14-24 block | `import VixCorPanel from "./VixCorPanel";` |
| `type RegimeTab` union | 40 | add `\| "vixcor"` after `"cor"` |
| `REGIME_TAB_VALUES` | 42 | add `"vixcor"` after `"cor"` |
| `tabFromPathname` regex | 52 | add `\|vixcor` to the alternation. Place it **before** `cor` is irrelevant here (no shared prefix), but keep the longest-prefix-first discipline the `skew2d\|skew` pair established |
| mobile chip array | 312 | add `"vixcor"` after `"cor"`; add `MOBILE_TAB_LABEL.vixcor = "VIX-COR"` at line 44 |
| desktop button row | after line 335 | `<button className={\`ticker-tab ${activeTab === "vixcor" ? "active" : ""}\`} onClick={() => goToTab("vixcor")}>VIX-COR</button>` |
| dispatch | beside the `skew2d` branch (~437) | `if (activeTab === "vixcor") { return <div className="regime-panel">{tabBar}<VixCorPanel /></div>; }` |

---

## H. Tests. Red first, always.

Run from the repo root; cwd drift has repeatedly produced bogus failures.

```
/Users/joemccann/dev/apps/finance/radon/.venv/bin/python -m pytest -q scripts/tests scripts/api/tests scripts/trade_blotter
/Users/joemccann/dev/apps/finance/radon/.venv/bin/python -m pytest -q cloud/tests
cd web && NODE_ENV=test ASSISTANT_MOCK=1 npx vitest run --config ../vitest.config.ts web/tests
cd web && npx tsc --noEmit
cd web && npx eslint app components lib middleware.ts
```

### H.0 Fixtures

Checked into `scripts/tests/fixtures/`:

- `vix_history_sample.csv` — a real slice of `VIX_History.csv` covering at least
  `2026-06-01 .. 2026-08-14` plus enough leading sessions for a 20-window, in the
  genuine `MM/DD/YYYY` `DATE,OPEN,HIGH,LOW,CLOSE` shape, including one
  deliberately malformed row and one empty-`CLOSE` row.
- `cor3m_sample.json` — the matching `cor_history` rows, including at least one
  date where `cor3m` is null and one Cboe index holiday present in VIX and
  absent from cor3m.

Expected values are derived by inspecting the fixtures, never asserted from
memory. All date-relative assertions use window-relative dates (today minus N);
hardcoded future dates rot in CI. The 2026-08-14 calibration pin is the one
deliberate exception and is anchored to the checked-in fixture, not to the live
CDN.

### H.1 `scripts/tests/test_vixcor.py` — correlation math

1. `corr_window` on worked Example 1 → `0.9814954576223638` within `1e-12`.
2. Example 2 → exactly `-1.0`.
3. Example 3 (constant leg) → `None`, and specifically `is None`, not `== 0`.
4. Perfect positive: `x = y = [1..20]` → `1.0` within `1e-12`.
5. Order invariance: `corr_window(x, y) == corr_window(y, x)`.
6. Scale and shift invariance: `corr_window(x, [2*v + 7 for v in y])` equals
   `corr_window(x, y)` within `1e-12`.
7. Return-convention negative control: computing on `pct_change` of the fixture
   window yields ~+0.92, NOT ~+0.01. Asserts the levels convention explicitly so
   a future refactor cannot silently switch.

### H.2 Window boundaries

1. `compute_corr_series` emits `len(joined)` rows.
2. Rows `0 .. WINDOW-2` have `corr20 is None`; row `WINDOW-1` has a float.
3. The value at row `i` uses joined rows `[i-19 .. i]` inclusive: pin against a
   hand-sliced `corr_window(xs[i-19:i+1], ys[i-19:i+1])`.
4. Sensitivity pin at the calibration date: N=19 → `0.0675`, N=20 → `0.0150`,
   N=21 → `-0.0363`, N=20-excluding-today → `0.0298`, each to 4dp.
5. A joined series shorter than `MIN_OBSERVATIONS` emits all-null `corr20` and
   `corr_count == 0`, and does NOT raise.

### H.3 Gaps and the join

1. `join_series` produces exactly the cor3m calendar: every output date exists in
   both inputs; count equals the number of cor3m dates present in VIX.
2. A VIX-only date (Cboe index holiday in the fixture) is absent from the output.
3. A cor3m-null date is absent from the output.
4. **Forward-fill negative control:** build a fixture window straddling a
   holiday, compute the shipped inner-join value and a forward-filled value, and
   assert they differ. Pins the ban.
5. Windows spanning a gap still use exactly 20 observations (assert the slice
   length inside the computation, not the calendar span).

### H.4 Event detection and debounce

1. Synthetic series: a run dipping to 0.28 but never below 0.25 produces **zero**
   episodes.
2. A run crossing 0.30 then 0.25 produces one episode whose `start` is the 0.30
   cross and whose `trigger` is the 0.25 cross.
3. Two sub-0.30 runs separated by 8 sessions merge into one episode;
   separated by 12 sessions they do not merge but the second is absorbed by the
   42-session debounce; separated by 60 sessions they are two episodes.
4. Boundary strictness: `corr20 == 0.25` is not a trigger;
   `corr20 == 0.30` closes a run.
5. `trough` / `trough_date` are the minimum within `[start, end]`.
6. An episode whose `end` is the last series date with `corr20 < 0.30` has
   `open: true`; one whose last session recovered above 0.30 has `open: false`.
7. Open episodes are excluded from `forward_stats.event`.
8. **Against the real fixtures:** exactly 5 episodes with `trigger >= 2024-01-01`,
   matching the triggers, starts, ends, troughs and `vix_at_trigger` of table
   `B.11` to the stated precision, with the fifth `open: true`.
9. Full-history run (a separate slower test, marked, reading the full checked-in
   history if present) yields 31 episodes.

### H.5 The calibration value

1. `current.corr20` on the fixture's 2026-08-14 row equals `0.014969` to 6dp.
2. `current.vix_close == 14.25`.
3. `current.regime == "DECOUPLED"`.
4. `current.percentile < 0.03`.

### H.6 Job orchestration and degradation

`_StubClient` returning fixture text and a controllable `Last-Modified`, plus
`_isolate_caches(tmp_path, monkeypatch)` repointing `VIXCOR_JSON`.
`db.client.get_db()` and `hrana_http` already refuse real connections under
`PYTEST_CURRENT_TEST`.

1. `test_unchanged_source_reuses_cached_payload_without_row_writes` — 304 plus a
   cache reuses the payload with a fresh `scan_time`, calls
   `upsert_scan_snapshot` and `record_service_health("vixcor", "ok")` exactly
   once each, and calls neither `upsert_vixcor_rows` nor
   `upsert_price_history_rows`.
2. `test_changed_source_rebuilds_and_writes_rows` — a changed stamp writes rows,
   snapshot and heartbeat.
3. `test_bounded_vix_tail_write` — with an existing `MAX(date)`, only rows at or
   after it are passed to `upsert_price_history_rows`; under `--backfill` the
   full parsed history is.
4. `test_never_executemany` — assert the writer path is
   `upsert_price_history_rows` / `upsert_vixcor_rows` and that no `executemany`
   attribute is touched on the stub db.
5. `test_parent_lag_one_session_holds_with_ok_heartbeat` — cor3m one session
   behind `last_completed_session_date` → `status == "holding"`, heartbeat state
   `"ok"`, snapshot written, no raise. **This is the anti-paging pin.**
6. `test_parent_lag_two_sessions_still_ok_heartbeat` — boundary at
   `PARENT_LAG_GRACE_SESSIONS`.
7. `test_parent_lag_beyond_grace_writes_error_heartbeat_without_raising` — three
   sessions behind → `status == "stale_parent"`, heartbeat `"error"` carrying
   `message` and `next_attempt_at`, snapshot still written, **no raise**.
8. `test_shared_missing_session_is_not_lag` — VIX and cor3m both stop at the
   same date behind `expected_session` → `status == "ok"`, `lag_sessions == 0`.
9. `test_zero_rows_raises` — an empty join raises `ValueError` and writes
   nothing.
10. `test_lag_counted_in_sessions_not_calendar_days` — a Friday parent date
    against a Monday `expected_session` is `lag_sessions == 1`.
11. `test_market_status_defaults_closed_when_calendar_raises`.
12. `test_stdout_is_json_only` — `--json` puts parseable JSON on stdout and every
    `[vixcor]` progress line on stderr.

### H.7 `TestVixcorStorage`

Execute `scripts/db/migrations/0049_vixcor.sql` into in-memory `sqlite3` and
pin: the four data columns and their types, `date` as PRIMARY KEY,
`idx_vixcor_history_date` exists, `schema_migrations` gets version **49**,
`corr20` accepts NULL while `vix_close` / `cor3m_close` do not, and running
`upsert_vixcor_rows` twice is idempotent (row count unchanged, `recorded_at`
updated).

Also add `"vixcor"` to the expected set in
`scripts/tests/test_service_registration_completeness.py`
(`test_direct_health_collector_sees_hand_rolled_writers`, ~line 279), or
`TestEveryWriterIsRegistered` fails on the unregistered window.

### H.8 vitest

**`web/tests/vixcor-api.test.ts`** (`@vitest-environment node`, mocks `@/lib/db`
with a real in-memory `@libsql/client` seeded from the migration):

1. Turso snapshot beats an older disk file.
2. Disk fallback when Turso is empty.
3. Absent everywhere returns HTTP **200** and exactly `MISSING_VIXCOR`
   (`missing: true`, `status: "missing"`, `series: []`, `episodes: []`).
4. `only reads the vixcor service's snapshots` — a `cor` snapshot in the table
   does not leak in.
5. `route.dynamic === "force-dynamic"`.
6. A `holding` payload passes through verbatim, still HTTP 200, `missing`
   absent.

**`web/tests/vixcor-panel.test.tsx`** (`@vitest-environment jsdom`, stub
`ResizeObserver`, `vi.mock("@/lib/useVixcor")`, factory fixtures):

1. Loader label while `loading && !data`.
2. `SectionEmptyState` copy on `missing: true`, and separately on an empty
   `series`.
3. Strip cell values by `data-testid` for a nominal payload.
4. `vixcor-regime-value` renders `DECOUPLED` in the dislocation token for
   `corr20 = 0.015`, `LOOSENING` for `0.40`, `COUPLED` for `0.80`; boundary
   cases `0.25` → `LOOSENING` and `0.50` → `COUPLED`.
5. Chart title `VIX VS 3-MONTH IMPLIED CORRELATION` renders; range chips and
   brush present when `total >= 2`; brush absent when `total < 2`.
6. Episode shading: N episodes in the payload produce N shade rects; the open
   one carries `data-testid="vixcor-episode-open"`.
7. **Over-claim guard:** the rendered panel's `textContent` contains none of the
   banned strings in `G.6`, and contains no em dash. Also asserts the base-rate
   block `vixcor-base-rate` is present and shows both an event column and an
   all-sessions column.
8. NaN guard: no `<path>` `d` attribute contains `"NaN"`, including a payload
   whose leading rows have `corr20: null`.
9. Zero line: a rule exists at the lower pane's `y(0)`.
10. Lag copy is derived: `lag_sessions: 0` renders the default sub-label,
    `1` renders `PARENT 1 SESSION BEHIND`, `3` renders
    `PARENT 3 SESSIONS BEHIND`. No hardcoded cadence or freshness string appears
    anywhere in the component.

**`web/tests/vixcor-hook.test.tsx`:** endpoint `/api/vixcor`, interval
`3_600_000`, `hasPost: false`, `extractTimestamp` reads `scan_time`.

**Lockstep pins:**

- `web/tests/regime-tab-routes.test.tsx` — add
  `["vixcor", "app/regime/vixcor/page.tsx"]` to the `describe.each` table plus a
  nav/render case.
- `web/tests/service-health-windows.test.ts` (~line 446) — the exhaustive set
  gains `vixcor`: window defined, `category: "scheduled"`, 26h in all three
  states, `requiresIb("vixcor") === false`.
- `cloud/tests/test_systemd_services.py` — `EXPECTED_SERVICE_FILES` gains
  `radon-vixcor.service` and `radon-vixcor.timer`.

### H.9 Playwright — `web/e2e/vixcor-tab.spec.ts`

Model `web/e2e/cor-tab.spec.ts`. `page.route` mocks for `**/api/vixcor` plus the
ambient `portfolio`, `orders`, `ib-status`; abort `**/api/prices`. Assert the
active tab, both panes rendered, the shaded episodes, the open-episode marker,
the brush, the base-rate block, and the missing-state copy. Not in CI; run
locally and attach the screenshot:

```
cd web && PLAYWRIGHT_PORT=3033 RADON_AUTHLESS_TEST=1 npx playwright test vixcor-tab
```

Never touch ports 3000, 8321 or 8765.

---

## I. Acceptance criteria

Every line is checkable. The build is not done until all of them are true.

**Data and math**

1. `corr_window` reproduces worked Examples 1, 2 and 3 exactly, degenerate
   windows returning `None`.
2. Against the checked-in fixtures, `current.corr20` on 2026-08-14 is
   `0.014969` to 6dp with `vix_close == 14.25`.
3. The window-sensitivity table in `B.4` is pinned; only N=20-inclusive
   produces the calibration value.
4. The join is an inner join on session date; a forward-fill negative-control
   test proves the two differ.
5. Episode detection at the shipped constants yields the five 2024+ episodes of
   `B.11`, the fifth `open: true`, and 31 episodes over the full history.

**Persistence**

6. `scripts/db/migrations/0049_vixcor.sql` exists, is numbered 49, and 49 is the
   `MAX(version)` in Turso after `bun run db:migrate`.
7. `vixcor_history` in **Turso** contains ~5,171 rows with `corr20` non-null on
   ~5,152 of them, verified by a `SELECT`, not by reading `data/vixcor.json`.
8. `price_history_daily` in **Turso** contains ~9,251 rows for `symbol = 'VIX'`,
   `source = 'cboe'`, max date equal to the Cboe file's last row.
9. `scan_snapshots` has a row for `service = 'vixcor'` with a payload matching
   `F.3`, verified in Turso.
10. No `executemany` anywhere in the new writer path; all inserts are chunked
    multi-row at 400 rows.

**The job**

11. `python3 scripts/fetch_vixcor.py --json` prints parseable JSON on stdout and
    nothing else; every progress line is on stderr with a `[vixcor]` prefix.
12. A second immediate run takes the 304 path, writes snapshot plus heartbeat
    only, and logs `all sources unchanged`.
13. `record_service_health("vixcor", "ok", ...)` is written on **every**
    successful cycle including 304 cycles.
14. A one-session `cor_history` lag produces `status: "holding"` with an **`ok`**
    heartbeat and no exception. This is the single most important behavioral
    criterion: a derived child must not page on parent lag.
15. A lag beyond `PARENT_LAG_GRACE_SESSIONS` produces `status: "stale_parent"`
    with an `error` heartbeat and still no exception.
16. An empty join raises and writes nothing (never cache empty).
17. No hardcoded timezone offsets; the only time-zone logic is inside
    `scripts/utils/market_calendar.py`.

**Scheduling and registration**

18. `cloud/services/radon-vixcor.service` and `.timer` exist, the timer is
    `02:35:00 UTC` daily with `Persistent=true`, and the service `ExecStart` is
    the venv python directly.
19. `cloud/scripts/setup-vps.sh`, `cloud/tests/test_systemd_services.py` and
    `cloud/config/installed-units.sha256` (or the drift allowlist) all carry both
    unit names, in one commit.
20. `web/lib/serviceHealthWindows.ts` and `scripts/watchdog/services.py` (both
    the window dict and the daily-bucket list) carry `vixcor` with identical 26h
    windows, `category: "scheduled"`, `requires_ib: false`.
21. `systemctl list-timers radon-vixcor.timer` on the VPS shows a next-elapse,
    and the first real fire lands a `service_health` row.

**API**

22. `GET /api/vixcor` returns 200 with the payload; with no data anywhere it
    returns 200 with `missing: true`. It never returns a 4xx or 5xx.
23. `holding` and `stale_parent` payloads pass through untransformed at 200.
24. `export const dynamic = "force-dynamic"` and `runtime = "nodejs"` are set.
25. No FastAPI route was added.

**UI**

26. `/regime/vixcor` renders through `WorkspaceShell`, the `VIX-COR` tab is
    active on that path, and all six `RegimePanel.tsx` sites are edited.
27. The chart is two stacked panes sharing one x-scale, VIX above, corr20 below,
    with a zero rule and a 0.25 threshold rule in the lower pane.
28. Breakdown episodes are shaded across both panes; the live 2026-08 episode
    carries the open-episode marker. **There are no arrows and no forward-VIX
    annotation on the chart.**
29. The base-rate block renders below the chart with both the post-breakdown and
    the all-session columns, sourced from `forward_stats`.
30. No banned over-claim string and no em dash appears in any rendered text.
31. `HistoryRangeChips` plus `BrushMinimap` present, `SpectralLoader` on load,
    `SectionEmptyState` on missing, 16px surface padding and 4px radius from
    `ChartPanel`.
32. Zero raw hex and zero `rgba()` literals in the new components; every color is
    a brand token or a `color-mix` over one. Verified correct in both
    `data-theme="light"` and `data-theme="dark"`.
33. No freshness or cadence copy is hardcoded; the `AS OF` sub-label is derived
    from `lag_sessions` and the clock from `lastSync`.

**Gates**

34. `pytest -q scripts/tests scripts/api/tests scripts/trade_blotter` green.
35. `pytest -q cloud/tests` green.
36. `NODE_ENV=test ASSISTANT_MOCK=1 npx vitest run --config ../vitest.config.ts web/tests` green.
37. `npx tsc --noEmit` and `npx eslint app components lib middleware.ts` clean.
38. A Playwright screenshot of `/regime/vixcor` at `PLAYWRIGHT_PORT=3033` in both
    themes, attached as evidence, saved to `docs/indicators/vixcor-tab.png` and
    `docs/indicators/vixcor-tab-light.png`.
39. `docs/indicators/README.md` has the row
    `| vixcor | \`/regime/vixcor\` | \`vixcor\` | [vixcor.md](vixcor.md) |`.
40. This spec lives at `docs/indicators/vixcor.md` and is referenced by path in
    the header docstrings of `scripts/fetch_vixcor.py` and `web/lib/vixcor.ts`.

---

## Ordered build sequence

1. `git mv` this file to `docs/indicators/vixcor.md`; capture
   `scripts/tests/fixtures/vix_history_sample.csv` and `cor3m_sample.json`.
2. **Red:** `scripts/tests/test_vixcor.py`, `web/tests/vixcor-api.test.ts`,
   `web/tests/vixcor-panel.test.tsx`.
3. `0049_vixcor.sql` → `writer.upsert_vixcor_rows` → `scripts/fetch_vixcor.py`.
   Green the pytest side. Run `--backfill` once against Turso.
4. `web/app/api/vixcor/route.ts` → `web/lib/vixcor.ts` → `web/lib/useVixcor.ts`
   → `web/components/VixCorChart.tsx` → `web/components/VixCorPanel.tsx` →
   `web/app/regime/vixcor/page.tsx` → the six `RegimePanel.tsx` edits. Green
   vitest.
5. Lockstep pins: `regime-tab-routes.test.tsx`, `serviceHealthWindows.ts` and its
   test, `watchdog/services.py` (two edits),
   `test_service_registration_completeness.py`.
6. Units, `setup-vps.sh`, `test_systemd_services.py`,
   `installed-units.sha256`. Green `cloud/tests`.
7. `docs/indicators/README.md` row. Playwright locally at `PLAYWRIGHT_PORT=3033`,
   screenshots in both themes.
8. Verify in **Turso**: `vixcor_history` row count, `price_history_daily WHERE
   symbol='VIX'` row count, and `scan_snapshots WHERE service='vixcor'`.
   `data/*.json` is never verification evidence.
