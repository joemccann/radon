# DISPERSION — Volatility below the surface (VIX vs single-stock vs cross-sector dispersion, z-scored since 2017)

**Status:** authoritative build spec. Implementers follow this literally.
Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementations to copy: `scripts/fetch_ivrank.py` (IB-first via
`IBClient`, `_serve_cached` stale_source posture), `scripts/fetch_iei_hyg.py`
(`asyncio.gather` IB sweep, Yahoo fallback), `scripts/bpi_scan.py` (Yahoo spark
batches, `last_completed_session_date` gating), `scripts/fetch_vixts.py` +
`scripts/lib/vixts_math.py` (pure-math module, plausibility guard, `_write_db`
isolation, payload/API/panel shape). Panel/API/route shape: the vixts tab.

---

## 0. What this is

A reproduction of the Wellington Management / Daily Shot chart "Volatility is
rising below the surface" (posted 2026-08-27). Three z-scored series on one axis:

| Series | Definition (from the chart's footnote) | Radon proxy |
|---|---|---|
| **Stock market volatility** | CBOE Volatility Index (VIX) | VIX daily close |
| **Single-stock volatility** | cross-sectional absolute difference between the 95th and 5th percentile of single-stock total returns in the MSCI USA Index | 95th minus 5th percentile of daily returns across the S&P 500 constituent seed (503 names) |
| **Cross-sector volatility** | the same spread across sector total returns in the MSCI USA Index | 95th minus 5th percentile of daily returns across the 11 Select Sector SPDR ETFs |

Each series is the **rolling 60-session mean** of the daily metric, then
**z-scored over the full sample from 2017-01-01 to present** ("Z-score of rolling
60-day metrics over full sample since 2017").

The thesis the tab exists to show: single-stock and cross-sector dispersion can
run 2+ sigma rich while the VIX sits below its mean. That is "volatility below
the surface": index-level hedges are cheap while idiosyncratic risk is extreme.
This is a descriptive regime read. **No forward-return claim is made anywhere in
the copy.** No validation study was run.

### Proxies and stated deviations (write these in the tooltip footnote, not as caveats in chat)

1. **Universe.** MSCI USA (~600 large+mid caps, point-in-time) is proxied by the
   current S&P 500 seed `scripts/data_seeds/constituents/SPX.json` (503 tickers,
   dash form `BRK-B`, resolved through `clients.index_constituents.resolve_constituents("SPX")`).
   This is **survivorship-biased**: names that left the index before today are
   absent, and names that joined after 2017 enter the cross-section on their first
   session. The 95/5 spread is robust to the tails, so the bias understates early
   dispersion only mildly. `n_stocks` is stored per date so the coverage is visible.
2. **Price returns, not total returns.** IB `TRADES` daily closes are
   split-adjusted, dividend-unadjusted (the repo's `price_history_daily` policy;
   `ADJUSTED_LAST` is used nowhere and would violate it). An ex-dividend day moves
   one name by ~0.5-1% once a quarter; the 95/5 spread of ~500 names is unaffected
   at display precision. Sector ETFs likewise.
3. **Sectors.** 11 Select Sector SPDRs: `XLK XLF XLV XLY XLP XLE XLI XLB XLU XLRE XLC`.
   **XLC begins 2018-06-19** (IB probe 2026-08-29: 2,060 bars from that date). Before
   it, the cross-section has 10 sectors. `n_sectors` is stored per date; the seam is
   documented, not hidden.
4. **Rolling 60-day metric** is read literally as the trailing 60-session **mean**
   of the daily cross-sectional spread (and of the daily VIX close). The
   alternative reading (spread of trailing 60-day returns) was rejected: the
   footnote calls the per-day spread the "volatility" metric and applies the
   60-day roll to it.

---

## A. Name and route

| Field | Value |
|---|---|
| Slug | `dispersion` |
| Route | `/regime/dispersion` |
| Page file | `web/app/regime/dispersion/page.tsx` |
| Tab label (desktop + mobile) | `DISPERSION` |
| Display name | Volatility Dispersion |
| API route | `/api/dispersion` |
| `service_health` key | `dispersion` |
| systemd units | `radon-dispersion.service`, `radon-dispersion.timer` |
| Turso table | `dispersion_history` |
| Snapshot service | `scan_snapshots.service = 'dispersion'` |
| Disk fallback | `data/dispersion.json` |
| Rail group | **Volatility** (append after `vixts`) |
| Migration | `0061_dispersion.sql` (version 61) |
| Pure math | `scripts/lib/dispersion_math.py` |
| Fetcher | `scripts/fetch_dispersion.py` |

`MAX(version)` in production `schema_migrations` is **60** (`0060_llm_model_catalog.sql`
is on `origin/main`), and `git log --all` shows no `0061` on any branch, so **61 is
free**. Only the ingestion implementer owns the migration.

**One-line description** (InfoTooltip first sentence):

> Three volatility gauges on one z-score axis since 2017: the VIX, the spread
> between the 95th and 5th percentile of daily single-stock returns across the S&P
> 500, and the same spread across the 11 sector ETFs. Each is a rolling 60-session
> mean. When the stock and sector lines run above 1 while the VIX sits below zero,
> volatility is rising below the surface: index hedges are cheap while
> idiosyncratic risk is extreme.

No em dashes in user-facing copy. Hyphens only.

---

## B. Source

### B.1 Confirmed facts (Step-1 evidence, 2026-08-29, prod gateway `ib-gateway:4001`, clientId 95)

| Request | Result |
|---|---|
| `Stock("AAPL","SMART","USD")` `10 Y` / `1 day` / `ADJUSTED_LAST` | 2,512 bars, 2016-08-31 to 2026-08-28, 3.7 s |
| same, `TRADES` | 2,512 bars, same span, 0.5 s |
| `Stock("XLK",...)` `10 Y` | 2,512 bars from 2016-08-31, 1.7 s |
| `Stock("XLC",...)` `10 Y` | 2,060 bars from **2018-06-19**, 0.7 s |
| `Stock("BRK B",...)` `10 Y` | 2,512 bars (IB wants the space form; seed uses `BRK-B`) |
| `Index("VIX","CBOE","USD")` `10 Y` / `TRADES` | 2,512 bars from 2016-08-31 |

- IB's `10 Y` daily window therefore starts **2016-08-31**, which is enough to warm
  a 60-session window before 2017-01-01: the first z-scored point lands on
  **2017-01-03** exactly as the reference chart's "Data from 1 January 2017".
- IB's duration windows are computed per symbol: in the captured `6 M` fixture XOM
  returned 129 bars vs 126 for every other symbol (three extra February sessions).
  **All joins are date-keyed; never align by position.**
- IB pacing limits (60 requests / 10 min) apply to bar sizes of 30 seconds or less.
  Daily bars are bounded only by the ~50 simultaneous-request slots, so a 515-symbol
  sweep at concurrency 8 is a 1-3 minute job. `HISTORICAL_CANCEL_GRACE_SECS` in
  `clients/ib_client.py:68-73` explains why the per-request timeout must be
  ib_insync's own `timeout=` argument (it is the only path that sends
  `cancelHistoricalData` and frees the slot).
- Fixture captured for pytest: `scripts/tests/fixtures/dispersion_ib_bars_sample.json`
  (13 symbols: AAPL MSFT NVDA JPM XOM UNH PG XLK XLF XLE XLV XLC VIX; `6 M` daily
  bars ending 2026-08-28; shape `{"symbols": {"AAPL": [{"date","close"}, ...]}}`).

### B.2 Data-source priority

**Ladder: IB -> Yahoo. UW deliberately skipped (documented deviation).**

- **IB is primary** for every symbol every cycle, per the repo rule. Preflight with
  `utils.ib_preflight.ib_auth_state()`: skip the socket only when the state is set
  and not `authenticated` (2FA lockout). Connect through
  `clients.ib_client.IBClient().connect(client_id="auto", timeout=10)` (20-49
  auto-allocated; **no new fixed client IDs**, the 50-69 scanner block is full) and
  drive `client.ib` (the ib_insync instance) with `asyncio.gather` under
  `asyncio.Semaphore(IB_CONCURRENCY)`, exactly the `_fetch_all` shape in
  `fetch_iei_hyg.py:186-200`.
- **UW is skipped**: `/api/stock/{t}/ohlc` is one call per symbol, so 515 calls
  per day would spend the shared UW daily cap that skew, skew2d and vol-cone
  depend on (`feedback_uw_quota_indexes_preset_and_attribution`). This is the same
  sanctioned deviation `bpi_scan.py:9-13` records for its ~2,600-name sweep.
- **Yahoo is the fallback rung only**, never scheduled as primary: for the symbols
  IB did not deliver (per-symbol failure, or the whole gateway unavailable).
  Incremental: v8 `spark` endpoint, 20 symbols per request, `range=1mo`
  (`bpi_scan.py:_fetch_spark_chunk`, empirical cap 20). Backfill: v8 `chart`
  per symbol with explicit `period1`/`period2` epochs (`rv_ratio_scan.py:_fetch_yahoo_daily`;
  `range=max` silently degrades). UA must be `Mozilla/5.0` (an honest UA gets 429
  from Yahoo; see `fetch_credit_spread.py:75-94`). Use `quote[0].close`, never
  `adjclose`. Courtesy sleep 0.25 s between requests.
- VIX rides the same ladder: IB `Index("VIX","CBOE","USD")` `TRADES`, Yahoo `^VIX`.
  (Turso `price_history_daily` already holds Cboe VIX closes from `fetch_vixcor.py`;
  it is **not** read here, so this writer never depends on another writer's cadence.)

### B.3 Licensing

IB market data under the operator's own subscription, displayed as a derived
statistic behind the authenticated perimeter. Yahoo fallback follows the existing
in-repo posture (`docs/external-services.md`). `/api/dispersion` serves only the
computed spreads and z-scores; do not add an endpoint that re-serves per-symbol
closes.

---

## C. The math (`scripts/lib/dispersion_math.py`, no network, no numpy dependency required but permitted)

### C.1 Named constants

```python
WINDOW = 60                      # rolling sessions for every metric
ZSCORE_BASE_START = "2017-01-01" # z-score sample start; series is emitted from here
MIN_STOCKS = 300                 # cross-section floor for a stock row
MIN_SECTORS = 9                  # floor for a sector row (10 before XLC, 11 after)
PCT_HIGH = 95.0
PCT_LOW = 5.0
SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC")
VIX_SYMBOL = "VIX"

# plausibility: any stored row outside these means a corrupt source
STOCK_SPREAD_MIN, STOCK_SPREAD_MAX = 0.005, 0.60
SECTOR_SPREAD_MIN, SECTOR_SPREAD_MAX = 0.001, 0.30
VIX_MIN, VIX_MAX = 5.0, 100.0
MIN_SERIES_ROWS = 400            # a backfilled series is ~2,420 rows

# regime edges (strict, pinned by test)
STRESS_Z = 1.0
COMPRESSED_Z = -1.0
```

Mirrored as TS display-copy constants in `web/lib/dispersion.ts`. The UI never
recomputes a spread, a mean or a z-score.

### C.2 Daily returns (per symbol, date-keyed)

Inputs: `closes: dict[symbol, dict[date, close]]`, `sessions: list[date]` = the
sorted VIX dates (the VIX trades every US equity session and is the master calendar).

For symbol `i` and session `t` with master-previous session `p`:

```
r_i(t) = close_i(t) / close_i(p) - 1      only if both t and p exist in closes[i]
```

No forward fill. A symbol missing either date contributes nothing on `t`.

### C.3 Cross-sectional spread

```
spread(t) = percentile(values, 95) - percentile(values, 5)
```

`values` = all `r_i(t)` available on `t`; percentile is **linear interpolation**
(numpy default, identical to pandas `quantile`). Implement with `numpy.percentile`
or a pure-python equivalent that is pinned against numpy in a test.

A stock row requires `len(values) >= MIN_STOCKS`; a sector row requires
`len(values) >= MIN_SECTORS`. A session is emitted as a raw row only when
**vix, stock_spread and sector_spread are all present**.

Raw row: `{date, vix_close, stock_spread, sector_spread, n_stocks, n_sectors}`,
spreads stored as decimals (0.0534 = 5.34 points).

### C.4 Rolling 60-session mean

For each of the three raw columns, `m(t)` = arithmetic mean of the trailing
`WINDOW` raw rows ending at `t` (inclusive), **only when a full window exists**.
No partial-window value, ever (the vixcor rule).

### C.5 Z-score over the full sample since 2017

```
base   = { m(t) : t >= ZSCORE_BASE_START }
mu     = mean(base)
sigma  = sample stdev(base)      # statistics.stdev, ddof=1
z(t)   = (m(t) - mu) / sigma     for t >= ZSCORE_BASE_START
```

Every historical z shifts a little each run as the base grows. That is the
definition ("over full sample since 2017") and why **nothing rolling or z-scored is
ever stored**: the table holds raw per-session rows only, and the payload is
rebuilt from them every run. `sigma == 0` or `len(base) < 2` raises.

### C.6 Regime classification (strict, pinned by test)

```python
def classify_regime(z_vix: float, z_stock: float, z_sector: float) -> str:
    below = max(z_stock, z_sector)
    if z_vix >= STRESS_Z:                                  return "BROAD STRESS"
    if below >= STRESS_Z:                                  return "BELOW THE SURFACE"
    if z_vix <= COMPRESSED_Z and below <= COMPRESSED_Z:    return "COMPRESSED"
    return "NORMAL"
```

| z_vix | z_stock | z_sector | regime |
|---|---|---|---|
| 1.0 | 0.0 | 0.0 | BROAD STRESS |
| 0.9999 | 1.0 | 0.0 | BELOW THE SURFACE |
| 0.9999 | 0.0 | 1.0 | BELOW THE SURFACE |
| 0.0 | 0.9999 | 0.9999 | NORMAL |
| -1.0 | -1.0 | -1.0 | COMPRESSED |
| -1.0 | -0.9999 | -1.0 | NORMAL |
| 2.0 | 2.5 | 2.5 | BROAD STRESS |

`surface_gap = max(z_stock, z_sector) - z_vix` (the amount of volatility hiding
below the surface; the reference chart's 2026-08 reading is roughly 2.4 - (-0.3) = 2.7).

### C.7 Fixture-derived worked example (pin in test, tolerance 1e-9)

Using `dispersion_ib_bars_sample.json`, the test builds the cross-section from the
seven stocks and five ETFs with `MIN_STOCKS`/`MIN_SECTORS` monkeypatched to 5 and
3, and asserts:

- the master calendar equals the VIX dates (126 sessions, 2026-03-02 .. 2026-08-28)
- `r_AAPL(2026-08-28)` equals `319.7 / close_AAPL(2026-08-27) - 1` read from the fixture
- XOM's three extra February bars produce **no** row (no VIX session there)
- `stock_spread(2026-08-28)` equals `numpy.percentile(vals, 95) - numpy.percentile(vals, 5)`
  for the seven returns, computed in the test from the fixture
- with `WINDOW` monkeypatched to 5, `m(t)` for the sixth session equals the mean of
  rows 2..6 and the first four sessions carry no metric
- the z-score of a constant series raises; the z of a two-point base is +/-0.7071067811865476

Values are computed from the fixture in the test, never typed from memory.

### C.8 Stats block

```python
{
  "base": {"start": "2017-01-03", "end": "2026-08-28", "n": 2420},
  "vix":    {"mean_60d": 18.9, "stdev_60d": 6.1, "z_min": -1.2, "z_max": 5.3},
  "stock":  {"mean_60d": 0.061, "stdev_60d": 0.014, "z_min": ..., "z_max": ...},
  "sector": {"mean_60d": 0.019, "stdev_60d": 0.006, "z_min": ..., "z_max": ...},
  "days_below_surface": 214,            # regime == "BELOW THE SURFACE" over the series
  "last_below_surface_date": "2026-08-28" | None
}
```

Computed over the **full** emitted series.

---

## D. Payload contract

```jsonc
{
  "scan_time": "2026-08-29T22:21:07Z",
  "status": "ok",                            // "ok" | "stale_source"
  "source": { "prices": "ib", "vix": "ib" }, // "ib" | "yahoo" | "mixed" (per-symbol fallback happened)
  "data_date": "2026-08-28",                 // latest emitted session
  "universe": { "index": "SPX", "n_constituents": 503, "sectors": ["XLK", ...] },
  "fetch": { "ib_ok": 512, "yahoo_ok": 2, "failed": 1, "failed_symbols": ["FOO"] },
  "count": 2420,
  "current": {
    "date": "2026-08-28",
    "z_vix": -0.31, "z_stock": 2.38, "z_sector": 2.41,
    "vix": 14.43, "stock_spread": 0.0712, "sector_spread": 0.0241,
    "m60_vix": 15.9, "m60_stock": 0.0834, "m60_sector": 0.0302,
    "n_stocks": 501, "n_sectors": 11,
    "regime": "BELOW THE SURFACE", "surface_gap": 2.72
  },
  "stats": { /* C.8 */ },
  "series": [
    { "date": "2017-01-03", "z_vix": -0.62, "z_stock": -0.41, "z_sector": -0.55,
      "vix": 12.85, "stock_spread": 0.0421, "sector_spread": 0.0132 }
  ]
}
```

z values rounded to **4 dp**, spreads to **6 dp**, VIX to the source's precision.
`series` is ascending, starts at the first date `>= ZSCORE_BASE_START` that has a
full window, and contains only emitted sessions. `current` is `null` only when
`series` is empty, which the guard makes an exception rather than a served state.

---

## E. Ingestion job — `scripts/fetch_dispersion.py`

### E.1 Shape and constants

```python
SERVICE = "dispersion"
DISPERSION_JSON = _PROJECT_DIR / "data" / "dispersion.json"
IB_CONCURRENCY = 8
IB_HISTORY_TIMEOUT_S = 45          # passed as ib_insync's own timeout= (the cancel path)
SWEEP_BUDGET_S = 600               # wall clock for the whole sweep, both rungs
IB_SWEEP_BUDGET_S = 420            # the IB rung's share of it
YAHOO_SWEEP_BUDGET_S = 180         # reserved for the Yahoo rung even when IB overran (R-446)
INCREMENTAL_DURATION = "1 M"       # IB duration for a normal daily run
BACKFILL_DURATION = "10 Y"         # --backfill; IB's daily window floor is 2016-08-31
YAHOO_INCREMENTAL_RANGE = "1mo"
YAHOO_BACKFILL_PERIOD1 = "2016-08-01"
HISTORY_READ_PAGE_ROWS = 500       # Hrana bounding on the dispersion_history read
# Mirrors radon-dispersion.timer (OnCalendar=*-*-* 22:20:00 UTC)
TIMER_HOUR_UTC, TIMER_MINUTE_UTC = 22, 20
```

`run(*, backfill=False, fetch_closes=None, now=None, universe=None) -> dict` so
tests inject a stub fetcher, a fixed clock and a small universe. `--json`
prints the payload to stdout; every progress line goes to **stderr**.

### E.2 Flow, every run

1. `writer.ensure_no_replica_for_writers()`.
2. **Rehydrate raw rows from Turso** `dispersion_history`, paged on the date cursor
   (`WHERE date > ? ORDER BY date LIMIT 500`). Turso is the source of truth; the
   JSON fallback is read only when the Turso read raises (the skew lesson: a fresh
   host once clobbered a 731-row snapshot with a 10-row JSON payload).
3. `last_complete = utils.market_calendar.last_completed_session_date()`.
   **If not `--backfill` and the stored max date `>= last_complete`:** no new
   session. Log `"[dispersion] no new session since <date>; refreshing snapshot only"`,
   rebuild the payload from stored rows, write snapshot + `ok` heartbeat + JSON,
   return. **No IB or Yahoo traffic** (this is what weekend and holiday runs do).
4. Resolve the universe: `resolve_constituents("SPX")` tickers + `SECTOR_ETFS` +
   `VIX_SYMBOL`. Members use `Stock(ticker.replace("-", " "), "SMART", "USD")`;
   VIX uses `Index("VIX", "CBOE", "USD")`.
5. Fetch closes (`fetch_closes(symbols, backfill) -> dict[symbol, dict[date, close]]`):
   IB rung for all symbols inside `IB_SWEEP_BUDGET_S`, then the Yahoo rung for
   whatever IB left empty with its own `YAHOO_SWEEP_BUDGET_S` (never the IB rung's
   spent deadline, R-446). Duration `BACKFILL_DURATION` or `INCREMENTAL_DURATION`.
   A sweep IB served nothing on (`fetch.ib_ok == 0`) still heartbeats `ok` but with
   `error.class == "ib_rung_dead"`, and the panel renders `source.prices` in the
   strip with a YAHOO FALLBACK badge: Yahoo is the last rung, never a silent
   primary (R-434).
6. If **VIX** came back empty from both rungs, or the resulting cross-section on
   `last_complete` is below `MIN_STOCKS`: `_serve_cached(...)` re-serves the stored
   payload as `status: "stale_source"` with an **`error` heartbeat** and exits
   non-zero (ivrank pattern; never latch `ok` over unconfirmed data).
7. Compute raw rows for every session in the fetched window (C.2-C.3); keep the
   new ones (`date > stored max`, or all when `--backfill`). Guard with
   `ensure_plausible_rows(rows)` (C.1 bands, **every** row). Merge into the stored
   rows by date (new wins).
8. Writes, in order: `writer.upsert_dispersion_rows(new_rows, recorded_at=scan_time)`
   **chunked** (model `upsert_hhlev_rows`; a backfill passes ~2,500 rows) — only
   when there are new rows; `writer.upsert_scan_snapshot("dispersion", scan_time, payload)`;
   `writer.record_service_health("dispersion", "ok"|"error", finished_at=scan_time, error=...)`;
   atomic write of `data/dispersion.json`.
9. `_write_db` reproduces `fetch_vixcor.py`'s R-192 isolation: a failed row upsert
   must not take the snapshot and heartbeat down with it, and surfaces as an
   `error` heartbeat rather than a silent exit 0.

If the incremental window cannot bridge the gap (stored max is older than the
sessions `INCREMENTAL_DURATION` covers), raise
`"gap since <date> exceeds the incremental window; rerun with --backfill"` and
record an `error` heartbeat. Do not silently emit a series with a hole.

### E.3 Backfill

`python scripts/fetch_dispersion.py --backfill --json` from the laptop against
the prod gateway (`IB_GATEWAY_HOST=ib-gateway`, direct-to-Turso writes) is the
Step-5 verification run. It replaces every stored row (idempotent upsert) and is
the only path that can reach 2016-08-31.

---

## F. Storage — `scripts/db/migrations/0061_dispersion.sql`

```sql
-- DISPERSION indicator: one raw row per completed session. Only pure per-session
-- functions of the day's closes are stored (VIX close, 95-5 spread across the
-- S&P 500 seed, 95-5 spread across the 11 sector SPDRs, and the cross-section
-- sizes). The 60-session means and the since-2017 z-scores are rebuilt from
-- these rows every run because the z-score base is the whole sample; storing
-- them would make rows order-dependent.
CREATE TABLE IF NOT EXISTS dispersion_history (
    date TEXT PRIMARY KEY,
    vix_close REAL NOT NULL,
    stock_spread REAL NOT NULL,
    sector_spread REAL NOT NULL,
    n_stocks INTEGER NOT NULL,
    n_sectors INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispersion_history_date_desc ON dispersion_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (61, datetime('now'));
```

Writer: `upsert_dispersion_rows(rows, recorded_at=None)`, chunked multi-row
`INSERT ... ON CONFLICT(date) DO UPDATE SET ...`, idempotent per date.

---

## G. API — `web/app/api/dispersion/route.ts`

Copy `web/app/api/vixts/route.ts` verbatim in shape (including the R-332/R-366
`staleCollapse` handling and `radonCapability = "read"`).

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only.
- `dbFirstRead({ fromDb, fromDisk, maxAgeMs: DISPERSION_MAX_AGE_MS, label: "dispersion", isDegraded: isMissingPayload })`.
- `fromDb`: `SELECT scan_time, payload FROM scan_snapshots WHERE service = 'dispersion' ORDER BY scan_time DESC LIMIT 1`.
- `fromDisk`: `data/dispersion.json`.
- **`DISPERSION_MAX_AGE_MS = 48 * 60 * 60_000`**, commented: the timer runs daily at
  22:20 UTC every calendar day (weekend runs are no-new-session heartbeats), so a
  snapshot older than 48h means the writer is down.
- **Missing contract**: HTTP **200** with a frozen object, never a 4xx:
  ```ts
  const MISSING_DISPERSION = Object.freeze({
    missing: true, scan_time: null, status: null, source: null, data_date: null,
    universe: null, fetch: null, count: 0, current: null, stats: null, series: [],
  });
  ```
- `setCacheResponseHeaders(response, { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, requestId, cacheState: "HIT", tags: ["dispersion"] })`.

Hook `web/lib/useDispersion.ts`: `useSyncHook<DispersionData>({ endpoint: "/api/dispersion", interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time || null }, true)`.

`web/lib/dispersion.ts` holds the types (`DispersionData`, `DispersionPoint`,
`DispersionCurrent`, `DispersionRegime`), the display constants (`STRESS_Z`,
`COMPRESSED_Z`, `WINDOW = 60`, `ZSCORE_BASE_START`), formatters (`formatZ` -> 2dp
signed, `formatSpreadPct` -> `(x * 100).toFixed(2) + "%"`, `formatVix` -> 2dp),
`regimeTone(regime)`, and `SOURCE_FOOTNOTE`. No math.

---

## H. UI — `web/components/DispersionPanel.tsx` + `web/components/DispersionChart.tsx`

Mirror `VixTsPanel.tsx` for the panel skeleton. **The chart is new**:
`CriHistoryChart` takes a fixed 2-tuple and this tab needs three lines on one
axis, so `DispersionChart.tsx` is a dedicated d3-svg chart inside the sanctioned
shell (`family="analytical-time-series"`, `ChartPanel` + `ChartLegend`), modeled
on `VixCorChart.tsx` / `BpiChart.tsx`.

**Gate order, strictly:**

1. `(loading || syncing) && !data` -> `<SpectralLoader label="Loading dispersion series" />`
2. `!data || data.missing || !data.current` -> `<SectionEmptyState ... />`
3. content

**Strip — six cells**, every value wrapped in a `data-testid` span:

| testId | label | value | sub |
|---|---|---|---|
| `dispersion-regime` | `REGIME` | `BELOW THE SURFACE` | `stock 2.38 / sector 2.41 / VIX -0.31` |
| `dispersion-z-stock` | `SINGLE STOCK` | `+2.38` | `95-5 spread 7.12%` (today's raw spread) |
| `dispersion-z-sector` | `CROSS SECTOR` | `+2.41` | `95-5 spread 2.41%` |
| `dispersion-z-vix` | `VIX` | `-0.31` | `14.43` (today's close) |
| `dispersion-gap` | `SURFACE GAP` | `+2.72` | `max(stock, sector) minus VIX` |
| `dispersion-source-updated` | `SOURCE UPDATED` | `2026-08-28` | `501 stocks / 11 sectors` |

Regime tone: `BROAD STRESS` -> `var(--negative)`, `BELOW THE SURFACE` ->
`var(--warning)`, `COMPRESSED` -> `var(--positive)`, `NORMAL` -> `var(--text-muted)`.
Brand tokens only, no raw hex, 4px max radius. `status === "stale_source"`
renders a muted `SOURCE STALE` badge next to the clock (copy: "re-serving the last
confirmed series").

`compact` (from `useViewport()`, `hasMounted && isMobile`) switches to the
`m-regime-grid2x2` `MetricCell` layout.

**Chart (`DispersionChart`):** title `VOLATILITY DISPERSION - Z-SCORE SINCE 2017`.
Three lines on ONE y-scale (z units): VIX = `chartSeriesColor("primary")`,
single-stock = `chartSeriesColor("dislocation")` (the signal line), cross-sector =
`chartSeriesColor("comparison")`. Dashed reference levels at `0`, `+1`, `+2`, `-1`
(labelled). Legend via `ChartLegend`. Tooltip on hover shows the date and all
three z values (2dp) plus the raw VIX/spreads. Props:
`{ entries: DispersionPoint[]; }` where `entries` is already the range-sliced
series. `xTickFormat` `%b %y` for a multi-year domain.

Preceded by `<HistoryRangeChips>`; followed by `<BrushMinimap values range
onRangeChange onCustom testIdPrefix="dispersion-brush" ariaLabel=... />` when
`total >= 2`, then the source footnote. Default preset **`all`**.

**Freshness copy is derived, never asserted.** The header clock renders
`data.scan_time`; the `SOURCE UPDATED` cell renders `data.data_date`. **No string
anywhere may claim a cadence.** Grep the new strings for
`Refresh|Updated|hourly|daily|5m` before shipping.

`InfoTooltip` copy: the one-liner (§A) -> what each line measures and the proxies
(§0: S&P 500 seed, survivorship, price returns, SPDRs, XLC seam) -> the regime
edges (§C.6) -> `Source: Interactive Brokers daily bars (Yahoo fallback)`.
No em dashes.

---

## I. Scheduling

**`cloud/services/radon-dispersion.service`** (copy `radon-vixts.service`, with
`TimeoutStartSec=900`: a 515-symbol IB sweep plus a Yahoo top-up plus a chunked
Turso write; the incremental run is 1-3 minutes, the budget is 600 s + writes).
`ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_dispersion.py`,
`EnvironmentFile=/etc/radon/env`, `Environment=RADON_DB_NO_REPLICA=1`, `User=radon`.

**`cloud/services/radon-dispersion.timer`**

```ini
[Unit]
Description=Radon DISPERSION Indicator - daily 22:20 UTC IB daily-bar sweep (S&P 500 seed + sector SPDRs + VIX)

[Timer]
# IB's daily bar for a session is final after the 16:00 ET close, which is
# 20:00 UTC in EDT and 21:00 UTC in EST. 22:20 clears EST with margin and sits
# between ivrank 22:10 (one IB request) and yield-curve 22:30 (no IB), well
# clear of the credit-spread / iei-hyg flock at 21:45-21:55. Runs every
# calendar day: weekend and holiday runs find no new completed session,
# make no IB or Yahoo requests, and refresh the snapshot + heartbeat so
# service_health stays inside the 26h window.
OnCalendar=*-*-* 22:20:00 UTC
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

Registration: append both to `cloud/scripts/setup-vps.sh` `SERVICE_FILES`, append
both `sha256  name` lines (exactly two spaces) to `cloud/config/installed-units.sha256`,
and add both to `cloud/tests/test_systemd_services.py`.

---

## J. Service-health registration (both sides, same commit)

**`web/lib/serviceHealthWindows.ts`** — after the `vixts` entry:

```ts
"dispersion": { open: 26 * HOUR, extended: 26 * HOUR, closed: 26 * HOUR, category: "scheduled", requires_ib: false },
```

`requires_ib: false` because the Yahoo rung keeps the writer alive through an IB
outage; an IB-only failure shows as `source: "yahoo"` in the payload, not as a
health fault. Not in `RTH_ONLY_SERVICES`.

**`scripts/watchdog/services.py`** — two registrations:
`SCHEDULED_SERVICES["dispersion"] = {"open": 26 * _HOUR, "closed": 26 * _HOUR, "requires_ib": False}`
and `"dispersion"` appended to the `"daily"` bucket with a two-line comment naming
the timer and cadence.

---

## K. Registration checklist (regime rail)

**`web/lib/regimeRail.ts`** (3): append `| "dispersion"` to `RegimeTab`; add
`"dispersion"` to the **Volatility** group after `"vixts"`; add
`dispersion: "DISPERSION",` to `REGIME_TAB_LABEL`. Also add it to
`FLAGGABLE_REGIME_TABS` + `buildRailStatuses` per the R-196 comment (regime
`BROAD STRESS` -> `fault`, `BELOW THE SURFACE` -> `warn`, otherwise `core`),
reading `/api/dispersion`'s `current.regime`.

**`web/components/RegimePanel.tsx`** (4): `MOBILE_TAB_LABEL` entry `dispersion: "DISPERSION"`;
`tabFromPathname` regex alternation (no prefix collision); the inline mobile chip
array; `if (activeTab === "dispersion") { return renderShell(<DispersionPanel />); }` + import.

---

## L. Lockstep test pins (same commit, or CI fails)

| File | Edit |
|---|---|
| `web/tests/route-local-authz-matrix.test.ts` | add `"dispersion"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES` |
| `web/tests/service-health-windows.test.ts` | add `dispersion` to the exhaustive `expected` set |
| `web/tests/regime-tab-routes.test.tsx` | `describe.each` table, panel stub mock, render case, nav case |
| `web/tests/regime-rail.test.tsx` | sorted tab-key array, `groupOf`, `REGIME_TAB_LABEL` (+ flaggable set if pinned) |
| `cloud/tests/test_systemd_services.py` | both unit names |
| `docs/indicators/README.md` | append the `dispersion` row |
| `docs/cloud-services.md` | append a `### DISPERSION (radon-dispersion.timer)` section |

---

## M. Tests (red first)

**`scripts/tests/test_dispersion.py`** — loads `dispersion_ib_bars_sample.json`
at import; expected values computed from the fixture inside the test:

- master calendar = VIX dates; XOM's extra February bars emit nothing
- `daily_returns` is date-keyed, no forward fill, requires the master-previous session
- `cross_sectional_spread` equals `numpy.percentile` 95 minus 5; floors `MIN_STOCKS` / `MIN_SECTORS` drop the row
- `rolling_mean` full-window only (`WINDOW` monkeypatched to 5)
- `zscore_series` uses sample stdev, base from `ZSCORE_BASE_START`, raises on zero sigma
- `classify_regime` boundary table (§C.6, all seven rows) and `surface_gap`
- `ensure_plausible_rows` raises on: too few rows for a backfill, spread out of band, VIX out of band, `n_stocks < MIN_STOCKS`
- `run()` with an injected `fetch_closes` stub and a fixed `now`: **no new session**
  path makes zero fetch calls and still writes snapshot + `ok` heartbeat; a new
  session path upserts only the new rows; VIX empty from the stub -> `stale_source`
  payload + `error` heartbeat + no row upsert; gap beyond the window -> raises
- `_write_db` isolation: a raising `upsert_dispersion_rows` still writes the snapshot and records an `error` heartbeat
- migration executed into in-memory sqlite pins schema, version 61, upsert idempotency
- dates are window-relative from ONE `_TODAY` read at module top (`feedback_import_time_vs_call_time_test_dates`)
- monkeypatch the writer; `db.client.get_db()` refuses real connections under `PYTEST_CURRENT_TEST`

**`web/tests/dispersion-api.test.ts`** (`@vitest-environment node`) — mock `@/lib/db`
with a real in-memory `@libsql/client` seeded with `scan_snapshots`: Turso beats
older disk, disk fallback works, the exact `missing:true` object at **200**, no
cross-service snapshot leak, `route.dynamic === "force-dynamic"`.

**`web/tests/dispersion-panel.test.tsx`** (`@vitest-environment jsdom`) — stub
`ResizeObserver`, `vi.mock("@/lib/useDispersion")`, factory fixtures
`buildSeries(n)` / `hookState()`: loader label, empty state, all six strip values,
chart title, three legend entries, chips/brush, regime tone, stale badge, and a
**NaN guard** (no `<path d>` contains `"NaN"`).

**`web/e2e/dispersion-tab.spec.ts`** — `page.route` mocks for `**/api/dispersion`
plus the ambient routes (`portfolio`, `orders`, `ib-status`), abort `**/api/prices`;
assert active tab, three rendered stroked paths, brush visible, missing-state copy.

---

## N. Ownership (single shared worktree, disjoint files, scoped commits)

| Implementer | Owns |
|---|---|
| ingestion | `scripts/lib/dispersion_math.py`, `scripts/fetch_dispersion.py`, `scripts/db/migrations/0061_dispersion.sql`, `scripts/db/writer.py`, `scripts/watchdog/services.py`, `cloud/services/radon-dispersion.{service,timer}`, `cloud/scripts/setup-vps.sh`, `cloud/config/installed-units.sha256`, `cloud/tests/test_systemd_services.py`, `scripts/tests/test_dispersion.py`, `docs/cloud-services.md` |
| api | `web/app/api/dispersion/route.ts`, `web/lib/dispersion.ts`, `web/lib/useDispersion.ts`, `web/lib/serviceHealthWindows.ts`, `web/tests/dispersion-api.test.ts`, `web/tests/service-health-windows.test.ts`, `web/tests/route-local-authz-matrix.test.ts` |
| ui | `web/components/DispersionPanel.tsx`, `web/components/DispersionChart.tsx`, `web/lib/regimeRail.ts`, `web/components/RegimePanel.tsx`, `web/app/regime/dispersion/page.tsx`, `web/tests/dispersion-panel.test.tsx`, `web/tests/regime-tab-routes.test.tsx`, `web/tests/regime-rail.test.tsx`, `web/e2e/dispersion-tab.spec.ts` |

The UI implementer mocks the hook in unit tests and may keep uncommitted stand-ins
for `web/lib/dispersion.ts` / `useDispersion.ts` so its panel test compiles, but
must not commit them; TypeScript integration is checked after all three land.
