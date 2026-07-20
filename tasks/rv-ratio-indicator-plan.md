# RV Ratio Indicator — Final Architecture

> **STATUS: DESIGN DOC, PENDING OPERATOR REVIEW. No code changes have been made.**
> Synthesized 2026-07-18 from two competing proposals. Base architecture is Proposal A
> (data-first: durable Turso close store, Yahoo-sanctioned deep backfill, session-relative
> staleness, synchronous scan POST). Grafted from Proposal B: dedicated tab identity that
> preserves the VIX slot, shared vol-math extraction, the tiered regime classifier and
> badge, percentile stat, and the no-service-health-row-in-v1 posture.

---

## 1. Summary

Per-asset 252-session realized volatility divided by SPY's 252-session realized
volatility, charted daily over up to ~10 years, with High / Low / Avg / Last / StdDev /
Percentile stats and a full-history ±1σ band. The signal is regime divergence: the ratio
breaking above its historical band means the asset's realized vol is decoupling from
index vol (relative-value vol trades, hedging-cost shifts). Lives in the `/options`
workspace as a new **Rel Vol** tab. Benchmark fixed to SPY in v1 (configurable benchmark
priced in section 10).

**Canonical name everywhere:** `rv-ratio` / `rv_ratio`.
Script `scripts/rv_ratio_scan.py`, tables `price_history_daily` + `rv_ratio_snapshots`,
FastAPI `/options/rv-ratio/...`, Next route `/api/options/rv-ratio`, tab id `rv-ratio`,
page `/options/rv-ratio`, panel `RvRatioPanel`.

### Judgment record (why A is the base)

| Axis | Verdict |
|---|---|
| Deep-history risk | A wins. B's `durationStr="12 Y"` IB fetch has no repo precedent and B itself flags it as the de-risk item. A sidesteps it: Yahoo is the sanctioned long-history source (`fetch_margin_debt.py` precedent), IB -> UW -> Yahoo priority still honored for the recent incremental segment where IB actually has data. |
| Data durability | A wins. B leans on `scripts/utils/price_cache.py`, whose 15min/24h TTLs and 500-file LRU prune make it a request cache; TTL expiry forces daily deep re-fetches and the prune can evict SPY. A's Turso `price_history_daily` is durable, cross-host (laptop + Hetzner, direct-to-cloud), and shares SPY across all assets for free. |
| Corporate actions | A only. The splice validator + auto re-backfill is the guard against a split fabricating a fake -50% log return that poisons RV for a full 252-session window. B has no answer. |
| Freshness semantics | A wins. Session-relative currency (last completed ET session via the market-state SoT) is correct for an EOD indicator; B's 26h wall-clock `maxAgeMs` mislabels Saturday-reading-Friday as stale. |
| Polling | A wins structurally. The scan POST returns the fresh payload synchronously, so no client polling loop exists at all (`feedback_no_unbounded_polling_loops`). B's 20s missing-retry poll is bounded but still a loop. |
| Tab identity | **B wins — grafted.** The existing `volatility` slot (`OptionsWorkspacePanel.tsx:21`, label "VIX / Volatility") implies a VIX/term-structure surface. A hijacked it; B correctly adds a separate tab. |
| Say-once vol math | **B wins — grafted.** Extract `compute_realized_vol` to `scripts/utils/realized_vol.py`; cri_scan imports it. A's parity-test-only approach leaves two bodies of the same math. |
| Signal legibility | **B wins — grafted.** Tiered ±1σ/±2σ regime classifier, named regime badge, and last-value percentile give the operator the read at a glance. |
| service_health | **B wins — grafted.** On-demand writer: no row in v1. A's `service_cycle` error rows would latch stale during quiet weeks (`feedback_service_health_heartbeat`); dormant-no-row is the sanctioned watchdog posture (`feedback_watchdog_dormant_no_row`). |
| Payload shape | A wins. Parallel arrays (~60 KB) over B's row objects (~150 KB); the client zips trivially. Latest-snapshot-per-symbol PK over B's keep-3 prune (simplicity-first; no consumer of snapshot history exists). |
| Hook | A wins. Bespoke per-symbol hook in the `useOptionsExposure` mold; `useSyncHook`'s fixed-endpoint config fits global caches, not a URL that re-keys on symbol navigation. |
| Migration number | Both wrong. `0028_knowledge.sql` exists; this ships as **0029**. |

---

## 2. Indicator spec

- **Reference:** SpotGamma "Tech/Semi 1-Year Realized Vol relative to SPX" — 252-session
  realized-vol proxy, daily closes, ~10y history, multi-series ratio lines, y-axis
  "Vol Ratio (x SPX)", per-series High/Low/Avg/Last/StdDev table.
- **Radon version:** single selected asset vs SPY benchmark (fixed in v1). Ratio time
  series with window presets + brush, current-value readout, stats row over the full
  loaded history, historical ±1σ band, tiered divergence badge.
- **Window:** 252 sessions of log returns per RV point (needs 253 closes).
- **History target:** 2520 ratio sessions (~10y), so 2772 aligned closes per leg.
- **EOD only.** Realized vol from daily closes; no intraday variant, and the panel does
  not pretend otherwise.

---

## 3. Architecture

### 3.1 Data flow

```
                       operator picks symbol on /options/rv-ratio?symbol=SMH
                                          |
                                          v
   +--------------------------- Next.js (web/) ----------------------------+
   |  RvRatioPanel  <--  useRvRatio(symbol)  <-->  /api/options/rv-ratio   |
   |  (chart, stats,      GET on mount/change       GET: dbFirstRead       |
   |   band, badge)       POST when missing/stale     fromDb: Turso        |
   +----------------------------------|----------------|------------------+
                                POST  | radonFetch     | fromDisk: data/rv_ratio/SYM.json
                                      v                |
   +------------------------- FastAPI :8321 -----------|------------------+
   |  POST /options/rv-ratio/{sym}/scan                |                  |
   |    cooldown 600s + single-flight lock             |                  |
   |    run_script("rv_ratio_scan.py", timeout=240) ---+--> returns fresh |
   |  GET  /options/rv-ratio/{sym}  (disk read, debug/parity surface)     |
   +----------------------------------|-----------------------------------+
                          subprocess  v  (own process, own GIL)
   +--------------------- scripts/rv_ratio_scan.py ------------------------+
   |  ensure_history(SYM) --+                                              |
   |  ensure_history(SPY) --+--> align -> rolling RV x2 -> ratio -> stats  |
   |        |                                    |                         |
   |        v                                    v                         |
   |  Turso price_history_daily        Turso rv_ratio_snapshots            |
   |  (durable closes, SPY shared)     + data/rv_ratio/SYM.json (atomic)   |
   |        ^                                                              |
   |  cold backfill: Yahoo 12y (sanctioned long-history source)            |
   |  incremental:   IB -> UW -> Yahoo (priority chain, trailing segment)  |
   |  splice validator: overlap mismatch -> full re-backfill (corp action) |
   +-----------------------------------------------------------------------+
```

Both deployment modes hit the same Turso DB direct-to-cloud; the subprocess owns all
libsql access, so the FastAPI event loop never touches it
(`feedback_no_sync_libsql_on_fastapi_event_loop`, satisfied by construction). Next.js
never spawns; it reaches FastAPI only via `radonFetch`.

### 3.2 File-by-file

| Layer | Path | Change |
|---|---|---|
| Shared vol math | `scripts/utils/realized_vol.py` | NEW — `compute_realized_vol(prices, window)` moved verbatim from `scripts/cri_scan.py:720`; plus vectorized `compute_realized_vol_series(closes, window)` |
| CRI re-point | `scripts/cri_scan.py` | EDIT — one-line import of the shared function; behavior identical, pinned by parity test |
| Producer | `scripts/rv_ratio_scan.py` | NEW — CLI subprocess, cri_scan-modeled |
| Migration | `scripts/db/migrations/0029_rv_ratio.sql` | NEW — `price_history_daily` + `rv_ratio_snapshots` |
| Writers | `scripts/db/writer.py` | EDIT — `upsert_price_history_rows`, `upsert_rv_ratio_snapshot` |
| FastAPI | `scripts/api/server.py` | EDIT — `GET /options/rv-ratio/{symbol}`, `POST /options/rv-ratio/{symbol}/scan` (beside the exposure endpoints; authenticated, NOT in `AUTH_EXEMPT_PATHS`, so no double-pin edits) |
| Next route | `web/app/api/options/rv-ratio/route.ts` | NEW — GET (dbFirstRead) + POST (radonFetch proxy) |
| Shared const | `web/app/api/options/_shared.ts` | EDIT — `RV_RATIO_SCAN_TIMEOUT_MS = 250_000` |
| Page | `web/app/options/rv-ratio/page.tsx` | NEW — clone of `net-gex/page.tsx` (force-dynamic, `SYMBOL_RE`, `WorkspaceShell section="options"`) |
| Tab rail | `web/components/OptionsWorkspacePanel.tsx` | EDIT — add `{ id: "rv-ratio", label: "Rel Vol", available: true }` BEFORE the untouched `volatility` slot; `activeTabFromPath` + `goToTab` matches; tab-conditional panel render |
| Domain lib | `web/lib/rvRatio.ts` | NEW — types, runtime guard, entry zipper, regime classifier, session-currency check |
| Hook | `web/lib/useRvRatio.ts` | NEW — bespoke per-symbol fetch hook, `useOptionsExposure` mold |
| Chart | `web/components/RvRatioChart.tsx` | NEW — d3-svg single-axis series + band inside `ChartPanel` |
| Panel | `web/components/RvRatioPanel.tsx` + `.module.css` | NEW |
| Presets | `web/lib/historyRange.ts` | EDIT — additive `3y` (756) and `5y` (1260) presets; safe because `HistoryRangeChips` hides presets exceeding `maxSessions`, so existing ~251-session regime charts never show them |
| Gitignore | `.gitignore` | EDIT — `data/rv_ratio/` |
| Docs | `scripts/CLAUDE.md`, `web/CLAUDE.md` | EDIT — client-ID table row (62-63), component cheat-sheet row |
| Tests | see section 9 | NEW |

Client IDs: `RV_RATIO_IB_CLIENT_IDS = (62, 63)` — scanner range 50-69, fixed-ID
convention per the CRI precedent (50-61 in use), disjoint from CRI. The 20-49
never-hardcode rule does not apply to scanners.

---

## 4. Math spec (pinned to house convention)

Identical semantics to `scripts/cri_scan.py:720 compute_realized_vol` — close-to-close
log returns, `np.std(..., ddof=1) * sqrt(252) * 100`, NaN below `window + 1` prices —
with `window = 252`. CRI's 20-session window is untouched.

Constants (`scripts/rv_ratio_scan.py`):

```python
RV_WINDOW = 252                                    # returns per RV point; needs 253 closes
RATIO_SESSIONS_TARGET = 2520                       # ~10 years of ratio points
CLOSES_TARGET = RATIO_SESSIONS_TARGET + RV_WINDOW  # 2772 aligned closes
BACKFILL_CALENDAR_YEARS = 12                       # fetch margin over 2772 sessions
MIN_RATIO_SESSIONS = 1                             # below -> missing:true, no writes
BENCHMARK = "SPY"
IB_REQUEST_TIMEOUT_S = 45
SCAN_COOLDOWN_S = 600
```

Pipeline, in order:

1. **Alignment first.** `D = sorted(dates(asset) INTERSECT dates(SPY))`, truncated to the
   trailing `CLOSES_TARGET` sessions. Both RV series are computed on the aligned close
   arrays so every ratio point shares an identical session set (cri_scan date-intersection
   precedent). Computing RV pre-alignment would let a halt day in one series
   desynchronize the return windows.
2. **Log returns per series:** `r_i = ln(c_i / c_{i-1})` on aligned closes.
3. **Rolling RV:** for `t >= RV_WINDOW`:
   `rv_t = std(r[t-251 : t+1], ddof=1) * sqrt(252) * 100`.
   Implemented as `compute_realized_vol_series` in `scripts/utils/realized_vol.py`,
   **parity-pinned**: for every `t`, `series[t] == compute_realized_vol(closes[:t+1], window)`
   (tested at windows 20 and 252 on synthetic GBM data). The shared scalar function is
   the contract; the vectorized series is an implementation detail behind the test.
4. **Ratio:** `ratio_t = asset_rv_t / bench_rv_t`; drop the point if `bench_rv_t` is NaN
   or `<= 0` (guard-and-drop, never emit Inf).
5. **Stats over the FULL loaded ratio series** (server-side, once — statistical scope is
   explicit and full-history, never the visible slice; `Z_SCORE_WINDOW` discipline):
   - `high`, `low`, `avg = mean`, `last = ratio[-1]`, `stddev` (ddof=1; 0.0 for a single
     point), `last_percentile` (rank of `last` within the loaded series, 0..1).
   - `band_upper = avg + stddev`, `band_lower = avg - stddev`; ±2σ derived client-side
     from `avg`/`stddev` (no extra fields).
   - `divergence` regime (grafted from B, tiered):
     `"in_band"` | `"elevated"` (> +1σ) | `"decoupled"` (> +2σ) | `"compressed"` (< -1σ).
     Classifier lives twice by necessity: computed in Python for the payload, and
     `classifyRatioRegime(stats)` in `web/lib/rvRatio.ts` is a pure formatter over the
     payload's stats — it re-derives nothing, it maps the `divergence` string to tones.
6. **Completeness:** `complete = len(ratio) >= RATIO_SESSIONS_TARGET`. Shorter (IPOs,
   thin history) serves a partial payload with `complete: false` + `coverage`.
   `< MIN_RATIO_SESSIONS` ratio points -> `missing: true, reason: "insufficient_history"`
   and **no snapshot/disk write** (`feedback_dont_cache_empty_results`).
7. **Rounding:** ratio 4dp, RV series 2dp (~2520 x 3 arrays, about 60 KB JSON).

**Adjusted-close policy (correctness-critical): split-adjusted, dividend-UNadjusted
closes everywhere.** An unadjusted split fabricates a ~-50% log return poisoning RV for
252 sessions. Yahoo v8 chart `indicators.quote[0].close` is split-adjusted,
dividend-unadjusted — use it, NOT `adjclose` (also dividend-adjusted; would mismatch IB
at the splice). IB daily `TRADES` bars match. UW `get_stock_ohlc` assumed
split-adjusted; the splice validator is the safety net and this gets verified during the
build.

---

## 5. Data layer

### 5.1 Migration `scripts/db/migrations/0029_rv_ratio.sql`

```sql
CREATE TABLE IF NOT EXISTS price_history_daily (
  symbol     TEXT NOT NULL,
  date       TEXT NOT NULL,   -- YYYY-MM-DD, ET session date
  close      REAL NOT NULL,   -- split-adjusted, dividend-UNadjusted
  source     TEXT NOT NULL,   -- 'ib' | 'uw' | 'yahoo'
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS rv_ratio_snapshots (
  symbol   TEXT PRIMARY KEY,  -- latest snapshot per symbol, INSERT OR REPLACE
  taken_at TEXT NOT NULL,
  payload  TEXT NOT NULL
);
```

Why not `data/price_history_cache/` (`scripts/utils/price_cache.py`): its TTLs and LRU
prune make it a request cache, not a durable 10-year store — TTL expiry would force a
full Yahoo re-backfill daily and the prune could evict SPY.

Why not `scan_snapshots`: `(service, scan_time, payload)` keying doesn't fit per-symbol
single-row snapshots, and the `scan_mirror.py` one-chokepoint-per-writer-family rule
makes this a standalone writer family with its own `scripts/db/writer.py` functions:
`upsert_price_history_rows(symbol, rows)` (batched INSERT OR REPLACE) and
`upsert_rv_ratio_snapshot(symbol, taken_at, payload)` (mirrors `upsert_cri_snapshot`,
writer.py:93).

### 5.2 `ensure_history(symbol)` — cold backfill + incremental refresh

Called for the asset and for SPY each run; returns a date-indexed close series,
trailing `CLOSES_TARGET` sessions.

1. **Read** stored rows from `price_history_daily` (script-side `db.client.get_db()`,
   the sanctioned Turso query pattern; `RADON_DB_NO_REPLICA` semantics inherited).
2. **Deep backfill (cold start):** if stored depth `< CLOSES_TARGET` sessions AND the
   earliest stored date is younger than ~11 calendar years, fetch
   `_fetch_yahoo_daily(symbol, years=BACKFILL_CALENDAR_YEARS)` (extends cri_scan's
   `_fetch_yahoo`; 0.5s courtesy sleep between symbols). Yahoo is the sanctioned
   long-history source — `fetch_margin_debt.py` documents that IB/UW carry no
   multi-decade history, so the priority chain legitimately falls through. Young
   listings store what exists; completeness semantics handle the rest.
3. **Incremental refresh:** if last stored date `<` last completed ET session
   (`utils.market_calendar`), fetch the trailing year via the standard chain, upsert
   only missing dates:
   - **P1 IB:** `ib_preflight.ib_auth_state() == "authenticated"` pre-check;
     `Stock(sym, "SMART", "USD")` with index map
     `{SPX: Index("SPX","CBOE"), NDX: Index("NDX","NASDAQ"), RUT: Index("RUT","RUSSELL")}`;
     `reqHistoricalDataAsync(durationStr="1 Y", barSizeSetting="1 day",
     whatToShow="TRADES", useRTH=True)`, every await in
     `asyncio.wait_for(..., IB_REQUEST_TIMEOUT_S)` — the canonical bounded-IB pattern
     (`feedback_ib_insync_no_request_timeouts`). Client IDs 62-63.
   - **P2 UW:** `UWClient().get_stock_ohlc(sym, candle_size="1d")`; skipped with a
     logged stderr reason for index symbols (UW cannot serve indices, cri precedent).
   - **P3 Yahoo:** `_fetch_yahoo_daily(sym, years=2)`.
4. **Splice validation / corporate-action detector:** the incremental window
   deliberately overlaps stored rows. For overlapping dates compare
   `|new - stored| / stored`; if more than 3 dates exceed 0.5%, a split/adjustment
   happened after backfill -> delete the symbol's rows and full-re-backfill from Yahoo
   in the same run (lineage reset, margin-debt splice-lineage spirit). Otherwise upsert
   only genuinely-new dates.

**SPY shared across assets:** SPY is just another symbol in `price_history_daily`. The
first scan of any asset on a given day refreshes SPY; every later asset scan that day
finds SPY current and skips the fetch entirely. Marginal cost of a new asset = one
asset-history fetch. This is the concrete payoff of the Turso close store.

### 5.3 Server-side vs client-side computation

All ratio + stats math is server-side (numpy), computed once at scan time and persisted
in the snapshot payload: parity with cri math is enforceable in one place with pytest;
stats are defined over full loaded history so they are scan-time constants, not
view-state. The client does presentation-only windowing (preset/brush slicing of
precomputed arrays). No stats recomputation in TS — say it once, in Python.

### 5.4 Failure modes

| Condition | Behavior |
|---|---|
| `< 253` aligned closes | `missing: true, reason: "insufficient_history"`; no snapshot/disk write; HTTP 200 (`feedback_http_status_for_real_errors`) |
| `253 <= closes < 2772` | Full payload, `complete: false`, `coverage`; UI shows PARTIAL badge |
| IB down / awaiting 2FA | Preflight skips IB silently to UW/Yahoo; deep Turso history unaffected |
| All incremental sources fail, store has data | Serve stored history; route computes `stale: true`; script exits non-zero on hard fetch failure without overwriting the last good snapshot |
| Splice mismatch (corp action) | Auto re-backfill within the run |
| SPY fetch fails on cold start | Hard failure: non-zero exit, stderr diagnostic — a ratio without a benchmark is meaningless |

**No `service_health` row in v1** (grafted from B). This is an on-demand,
operator-triggered writer with no cadence: a `service_cycle` error row would latch
stale during quiet weeks (`feedback_service_health_heartbeat`), and the watchdog's
dormant-no-row rule (`feedback_watchdog_dormant_no_row`) means no row = no paging.
If a nightly warm-cache timer ships later, wrap in `db.service_cycle("rv-ratio")` then.

---

## 6. Payload shapes (schema_version 1, the one contract every layer shares)

```json
{
  "schema_version": 1,
  "symbol": "SMH",
  "benchmark": "SPY",
  "window_sessions": 252,
  "scan_time": "2026-07-17T22:31:04Z",
  "sources": { "asset": ["yahoo", "ib"], "benchmark": ["yahoo", "ib"] },
  "dates":        ["2016-08-01", "..."],
  "ratio":        [1.1841, "..."],
  "asset_rv":     [17.42, "..."],
  "benchmark_rv": [14.71, "..."],
  "stats": {
    "high": 1.62, "low": 0.94, "avg": 1.21, "last": 1.48, "stddev": 0.11,
    "last_percentile": 0.92,
    "band_upper": 1.32, "band_lower": 1.10,
    "divergence": "elevated"
  },
  "coverage": {
    "first_date": "2016-08-01", "last_date": "2026-07-17",
    "sessions": 2520, "complete": true
  },
  "units": "x SPY realized vol",
  "missing": false
}
```

- `divergence`: `"in_band" | "elevated" | "decoupled" | "compressed"` (section 4 step 5).
- Missing variant:
  `{ "schema_version": 1, "symbol": "XYZ", "missing": true, "reason": "insufficient_history", "scan_time": "..." }`.
- Cooldown variant (FastAPI POST only): `{ "status": "cooldown", "retry_in": 412 }`, HTTP 200.
- Next GET attaches route-computed `stale: boolean` (section 7) and wraps via
  `optionsJson()` (no-store headers + request id, options `_shared.ts` convention).
- `benchmark` is a payload field from day one so a configurable benchmark never changes
  the wire shape.

---

## 7. Caching / staleness design

- **Turso-first read** (`feedback_scanner_snapshot_must_mirror_turso`): Next GET uses
  `dbFirstRead({ fromDb, fromDisk, maxAgeMs: 4 * 24 * 3_600_000, label: "rv-ratio" })`.
  `fromDb` selects `taken_at, payload FROM rv_ratio_snapshots WHERE symbol = ?` through
  the bounded-pool `getDb()`/`dbExecute` path; `fromDisk` parses
  `data/rv_ratio/{SYM}.json`. Content timestamps only (`taken_at` / `scan_time`), never
  mtime. Both absent -> 200 `{ symbol, missing: true }`.
- **Session-relative freshness, not wall-clock.** A snapshot is current iff
  `coverage.last_date >= last completed ET trading session` (Python:
  `utils.market_calendar`; Next: `marketCalendar.js` — the market-state SoT rule;
  `extended` counts as `closed`). Saturday reading Friday's scan is fresh despite 20+
  wall-clock hours. `maxAgeMs` is deliberately loose (4 days) purely to survive long
  weekends; the meaningful flag is the route-computed `stale` from the session rule,
  which drives the hook's rescan.
- **Intraday**, yesterday's close is the correct latest point; readout labeled
  "as of {last_date} close".
- **Write path:** the subprocess writes Turso + disk (atomic_save,
  `data/rv_ratio/{SYM}.json`, gitignored); FastAPI writes nothing; Next writes nothing.
- **Cooldown:** per-symbol 600s (module dict, `time.monotonic()`, LEAP precedent) +
  per-symbol single-flight `asyncio.Lock` in FastAPI. Daily-close data cannot change
  intraday, so the cooldown costs nothing.
- **No polling anywhere:** the POST returns the fresh payload synchronously
  (`run_script` timeout 240s covers a cold two-leg Yahoo backfill; warm runs are
  seconds). The hook fires at most one POST per stale/missing observation
  (`feedback_no_unbounded_polling_loops`, honored structurally).
- **Route contract:** `export const dynamic = "force-dynamic"; export const runtime =
  "nodejs"` — auto-pinned by `web/tests/api-routes-no-cache-contract.test.ts`.

### Hook (`web/lib/useRvRatio.ts`)

`useRvRatio(symbol) -> { data, loading, scanning, error, refresh }`

1. Mount / symbol change: GET with `cache: "no-store"` + AbortController (abort prior
   in-flight on change and unmount); validate with `isRvRatioPayload`.
2. If `missing` or `stale`: set `scanning: true`, fire ONE POST
   (`RV_RATIO_SCAN_TIMEOUT_MS = 250_000` on both route and client). The POST response
   IS the fresh payload. Stale-but-present data keeps rendering underneath; errors
   suppressed while stale data is displayed (house SWR behavior).
3. `refresh()` = manual POST + re-read, guarded by an in-flight ref; the FastAPI
   cooldown backstops it server-side.

---

## 8. UI spec (mapped to house chart conventions)

### 8.1 Tab wiring (`web/components/OptionsWorkspacePanel.tsx`)

- `TABS`: insert `{ id: "rv-ratio", label: "Rel Vol", available: true }` BEFORE the
  existing `{ id: "volatility", label: "VIX / Volatility", available: false }` entry,
  which stays untouched — that slot is reserved for a future VIX/term-structure surface
  (grafted from B).
- `activeTabFromPath`: match `/options/rv-ratio`; `goToTab("rv-ratio")` pushes
  `/options/rv-ratio?symbol=X`. Symbol selection is shared for free via the URL param.
- Tabpanel render becomes tab-conditional:
  `activeTab === "rv-ratio" ? <RvRatioPanel symbol={selectedSymbol} /> : <OptionsExposurePanel ... />`
  (ticker-entry form unchanged when no symbol).
- Page `web/app/options/rv-ratio/page.tsx`: clone of `net-gex/page.tsx`. Redirect
  shims, nav entries, `WorkspaceShell` options special-casing: untouched
  (`feedback_workspace_shell_wraps_all_routes` respected by construction).

### 8.2 `RvRatioPanel` anatomy (top to bottom)

All brand tokens, 4px max radius, container queries (`container-type: inline-size`),
tabular-nums, `OptionsExposurePanel` grammar. No em dashes in any copy.

1. **Header:** `.panel-eyebrow` "OPTIONS / RELATIVE VOL" (mono 10px, 0.14em tracking);
   h2 "Realized Vol Ratio"; symbol readout "SMH / SPY"; **regime badge** from
   `stats.divergence` — `IN BAND` (neutral core) / `ELEVATED` (caution, > +1σ) /
   `DECOUPLED` (dislocation, > +2σ) / `COMPRESSED` (neutral-low, < -1σ); PARTIAL warn
   badge when `!coverage.complete`; mono telemetry line
   `AS OF 2026-07-17 CLOSE · 252-SESSION RV · 2,520 SESSIONS · BENCHMARK SPY`.
2. **States:** loading -> `<SpectralLoader label="Sampling 252-session realized vol" />`;
   cold symbol (missing -> auto-POST) swaps the label to
   `First measurement. Fetching 12 years of closes` (a cold backfill takes tens of
   seconds; the copy sets the expectation); error -> `MeasurementState kind="error"`
   with a "Retry measurement" button calling `refresh()`; insufficient ->
   `MeasurementState kind="empty"`: `INSUFFICIENT HISTORY / 252-session realized vol
   needs at least 253 aligned closes on both legs.`
3. **Stats row:** six metric tiles — HIGH / LOW / AVG / LAST / STDDEV / PCTL — seated
   flush per the fused-border rule (contract test `section-tile-grid-inset.test.ts`
   already pins it). LAST carries the divergence tone; values `1.48x` with the `x`
   suffix matching the axis. Footnote: `Stats span the full loaded history, not the
   visible range.`
4. **Chart block** (regime chart conventions: 16px pad, presets + BrushMinimap,
   SpectralLoader, 4px radius):
   - `<HistoryRangeChips>` on shared `web/lib/historyRange.ts`; additive `3y`/`5y`
     presets (section 3.2); default via `defaultPresetForLength` (`1y`).
   - `<RvRatioChart>` — d3-svg inside `<ChartPanel family="analytical-time-series">`
     (renderer sanctioned by the CriHistoryChart precedent: band + breach coloring +
     brush interplay + 2520-point tooltip bisection exceed shared primitives;
     CriHistoryChart's hard `[left, right]` dual-axis tuple doesn't fit a
     single-series + band chart, so a sibling reusing its exported
     `buildCriHistoryXAxisTickValues` / `shouldRotateCriHistoryXAxisLabels`, MARGIN
     idiom, curveMonotoneX, tooltip side-flip, touch overlay, `.chart-empty-state`).
     Rendered: full-history ±1σ band as a `color-mix` axis-muted wash with dashed avg
     midline and dotted ±2σ guides; hairline at 1.00x (parity); ratio line
     `chartSeriesColor("primary")` stroke 2; last point ringed (r=4); segments above
     +1σ tinted `caution`, above +2σ `dislocation`, below -1σ `neutral` via clipped
     paths. Because the band is full-history-fixed, a line exiting the band IS the
     divergence signal. Y-axis label "VOL RATIO (x SPY)". Zero raw hex; all roles via
     `chartSeriesColor()`/tokens.
   - Tooltip: `.chart-tooltip` rows — date, `RATIO 1.48x`, `SMH RV 24.1%`,
     `SPY RV 15.0%`; side-flips at width/2.
   - `<BrushMinimap values={ratio} range onRangeChange onCustom testIdPrefix="rv-ratio-brush" ariaLabel="RV ratio history brush" />`
     — full-history context sparkline; any drag flips the chips to Custom.
   - Mono 9px footnote: `Band = full-history avg +/- 1 sd. Log returns, ddof 1,
     annualized sqrt(252). Sources: IB / UW / Yahoo (long history).`
5. **Mobile:** container queries at 700px (stats to `.m-regime-grid2x2` MetricCells,
   chart height 440 -> 300) and 480px (telemetry wraps). BrushMinimap already carries
   the coarse-pointer hit-halo + `touch-action: none`; overlay keeps
   `touch-action: pan-y`. E2E asserts no document-level horizontal overflow.

Theme: light/dark both verified (tokens + `color-mix` only, pre-hydration theme rules
already handled by ThemeBootstrap).

---

## 9. Phased build order (red/green TDD per layer)

Each step: failing test first, implement, full suite green (`pytest` from repo root;
`vitest` from `web/`; cwd determinism rule), commit.

1. **Vol-math extraction.** RED: `scripts/tests/test_realized_vol.py` — shared
   `utils.realized_vol.compute_realized_vol` parity with historical cri output on fixed
   numpy series (expected values derived in-test with numpy, never head arithmetic),
   NaN below `window+1`, window=252 case, and the series-vs-scalar parity property at
   every index (windows 20 and 252, synthetic GBM). GREEN: create module +
   `compute_realized_vol_series`, re-point `cri_scan.py` import. Existing cri tests
   untouched and green.
2. **Migration + writers.** RED: `scripts/tests/test_rv_ratio_writer.py` — idempotent
   upserts, per-symbol snapshot replace. GREEN: `0029_rv_ratio.sql` + writer functions.
   (`db.client` refuses real connections under `PYTEST_CURRENT_TEST`.)
3. **Scan math + payload.** RED: `scripts/tests/test_rv_ratio_math.py` — alignment
   intersection on mismatched calendars, ratio drop-on-degenerate-benchmark, stats
   block (ddof=1, percentile, band), divergence classification boundaries at exactly
   ±1σ/±2σ, completeness thresholds, payload validates against section 6. GREEN:
   `align_sessions`, `compute_ratio`, `compute_ratio_stats`, `build_payload`.
4. **History layer.** RED: `scripts/tests/test_rv_ratio_history.py` with mocked
   IB/UW/Yahoo fetchers — cold backfill, incremental gap fill, SPY skip-when-current,
   splice-mismatch -> re-backfill, IB preflight skip, index-symbol UW skip,
   insufficient-history -> no writes. Window-relative dates only (today-minus-N;
   `feedback_window_relative_test_dates`). GREEN: `ensure_history`.
5. **Script CLI contract.** RED: `scripts/tests/test_rv_ratio_scan_cli.py` — stdout is
   exactly one JSON document, progress on stderr
   (`feedback_subprocess_progress_to_stderr`), non-zero exit + no snapshot on benchmark
   failure. GREEN: `main`/`run_scan` + disk write via `atomic_save`.
6. **FastAPI.** RED: `scripts/api/tests/test_rv_ratio_routes.py` — GET missing -> 200
   `missing:true`; bad symbol -> 400; POST happy path with `run_script` mocked;
   cooldown -> 200 `status:"cooldown"`; single-flight; script failure -> 503 with
   detail. GREEN: both endpoints.
7. **Next route.** RED: `web/tests/rv-ratio-route.test.ts` — symbol validation 400
   shape, dbFirstRead wiring (DB-newer vs disk-newer, both-absent -> 200 missing),
   session-relative `stale` computation across weekend/holiday, POST proxy timeout +
   `optionsErrorResponse` mapping. The no-cache contract test picks the route up
   automatically. GREEN: `route.ts` + `_shared.ts` constant.
8. **Domain lib.** RED: `web/tests/rv-ratio-transform.test.ts` — guard accept/reject
   matrix, entry zipping, `classifyRatioRegime` tone mapping, `isSnapshotCurrent`
   marketCalendar cases. Shared fixture `web/tests/rv-ratio-fixture.ts`
   (window-relative dates; ~300-session partial + 2772-session complete variants).
   GREEN: `web/lib/rvRatio.ts`.
9. **Hook.** RED: `web/tests/rv-ratio-hook.test.tsx` — abort on symbol change/unmount,
   stale -> single POST -> data swap, no repeat POST while scanning, error surfaced
   only without displayed data. GREEN: `useRvRatio.ts`.
10. **Chart + panel + tab wiring.** RED: `web/tests/rv-ratio-panel.test.tsx` (states,
    cold-symbol copy, six stat tiles with fixture-derived values, PARTIAL badge,
    regime-badge classes, default preset, chips -> custom on brush, tooltip rows) +
    extend `web/tests/options-exposure-navigation.test.ts` for `/options/rv-ratio`
    activation and symbol carry-through, with the `volatility` slot asserted unchanged.
    GREEN: chart, panel, module.css, `OptionsWorkspacePanel` edits, page.
11. **E2E.** `web/e2e/rv-ratio.spec.ts` (stubbed `/api/options/rv-ratio`, fixture
    payload): tab reachable from `/options/net-gex` with symbol carried; loader label;
    stats + chart + band + brush render; chips flip presets; partial-badge variant;
    mobile 393x852 no horizontal overflow. Green under `RADON_AUTHLESS_TEST=1`.
12. **Live verification before "done"** (chrome-cdp primary, Playwright fallback):
    `scripts/cloud.sh` up; drive `/options/rv-ratio?symbol=SMH` end-to-end — cold
    backfill completes, band/badge/stats render, light/dark toggle, mobile width; then
    a young-IPO ticker for the PARTIAL path. Verify the snapshot **against Turso, not
    the JSON fallback** (`SELECT taken_at FROM rv_ratio_snapshots WHERE symbol='SMH'`)
    and paste curl + Turso evidence inline. Same commits carry the doc edits:
    `scripts/CLAUDE.md` client-ID row, `web/CLAUDE.md` cheat-sheet row, `.gitignore`.

---

## 10. Configurable benchmark — cost note (deferred, priced)

SPY is hardcoded in v1; `benchmark` is echoed in the payload so no wire-shape change is
ever needed. Making it selectable costs:

- **Data: nearly free.** `price_history_daily` is symbol-keyed; QQQ/IWM closes are just
  more rows, shared like SPY.
- **Keying: the real cost.** `rv_ratio_snapshots` PK widens to `(symbol, benchmark)`
  (migration 0030); cooldown/single-flight keys, disk filenames
  (`{SYM}__{BENCH}.json`), route/hook query params, and the payload guard all grow a
  dimension; `dbFirstRead` fromDb/fromDisk re-key.
- **UI:** a benchmark segmented control (SPY/QQQ/IWM) in the panel header.
- **Tests:** route/hook/panel matrices roughly double on the keying axis.

Estimate: about one focused day; no architectural rework.

---

## 11. Non-goals (v1)

- No multi-series overlay (the SpotGamma reference plots four ratios at once; Radon v1
  is one selected asset — the workspace's single-symbol model).
- No configurable benchmark (priced above, deferred).
- No intraday realized vol; daily closes only. The exposure panel's eod/intraday toggle
  does not apply and the panel does not pretend it does.
- No visible-range stats readout (full-history only, footnoted; cheap v2 if asked).
- No scheduled warm-cache timer, no `service_health` row, no watchdog coverage
  (on-demand writer; revisit together if a nightly timer ships).
- No implied-vol or IV/RV comparison (different indicator).
- No touching the reserved `volatility` (VIX) tab slot.
- No dividend-adjusted series; split-adjusted, dividend-unadjusted policy is fixed.

---

## 12. Open questions for the operator

1. **Tab label:** "Rel Vol" (proposed) vs "RV Ratio" — the rail is tight on mobile;
   which reads better next to Net GEX / DEX / Greeks / OI?
2. **History depth:** is ~10y (2520 ratio sessions) the right target, or is 5y enough?
   Halving the target roughly halves cold-backfill time and payload size.
3. **Band width:** ±1σ band with ±2σ guides is proposed. Prefer percentile bands
   (e.g. 10th/90th) instead? Percentile bands are robust to fat tails; σ bands match
   the house z-score idiom.
4. **UW OHLC range:** if UW's `get_stock_ohlc` turns out to serve more than ~1y with
   range params, it should slot into the backfill chain above Yahoo. Worth a probe
   during the build, or defer?
5. **Divergence surfacing:** should a `decoupled` regime raise anything outside the
   panel (banner is ruled out unless actionable per `feedback_banner_only_actionable`;
   a row on the dashboard regime strip is the plausible candidate)?
6. **Cold-backfill UX:** synchronous POST holds the request up to ~4 minutes worst
   case on a cold symbol. Acceptable for v1 (copy sets the expectation), or is a
   fire-and-poll variant wanted despite the polling-loop cost?
7. **Client IDs 62-63:** confirm nothing outside the repo (ad-hoc scripts, other
   worktrees) squats on 62-63 in the scanner range before pinning.
