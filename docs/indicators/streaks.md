# STREAKS — consecutive daily gains for any ticker

Regime tab `/regime/streaks`. The operator enters a ticker symbol; the tab
charts the daily close series (log scale) above a histogram of consecutive
daily gains, in the style of the S&P GSCI Agriculture reference chart
(price on top, per-session streak count below).

## Signal definition

- `streak[i]` = number of consecutive sessions with `close > prev close`,
  ending at session `i`. `streak[0] = 0`. A down **or flat** close resets
  the streak to 0.
- A **run** is a maximal block of sessions with `streak > 0`; its length is
  the streak value on its last session. The trailing in-progress run counts
  as a run.
- Signal read: an extreme streak relative to the symbol's own run history
  (RUNS >= CURRENT precedent count) flags momentum persistence and
  mean-reversion setups. The tab states measurements only; it makes no
  trade calls.

## Source facts (data path, not a new vendor)

On-demand fetch of daily closes for one symbol through the existing repo
clients, in repo priority order:

1. **IB** — FastAPI IB pool (`data` role), `get_historical_data` with
   `duration="10 Y"`, `bar_size="1 day"`, TRADES, RTH, bounded by
   `_bounded_pool_call` (15s). Skipped cleanly when the pool is absent or
   the gateway is unreachable.
2. **Unusual Whales** — `UWClient.get_stock_ohlc(symbol, candle_size="1d",
   limit=2500)`; rows parsed by `utils.uw_surface._as_uw_ohlc`.
3. **Robinhood** — `clients.robinhood_client.fetch_robinhood_closes`
   (READ-ONLY MCP; returns `{}` without network when unconfigured).
4. **Yahoo** — ABSOLUTE LAST RESORT; v8 chart API, 20-year lookback,
   parsed by the pure `utils.streaks.parse_yahoo_chart`.

Ladder rule: first source returning >= 21 closes wins; if none reaches 21,
the longest non-empty result is used (a genuinely young listing is short on
every source). Results are cached via `utils.price_cache` (stocks dir, TTL
15 min in market hours / 24 h after close), key
`cache_key_stock(symbol, "streaks-max", <UTC date>)`; a cache hit reports
`source: "cache"`. No Turso table and no migration: the payload is derived
on demand from upstream vendors and carries no state a deploy could lose.

Licensing: all four sources are already integrated repo clients used under
their existing terms; nothing new is stored beyond the existing price cache.

## Update cadence / freshness

On demand only. There is **no systemd timer, no service_health writer, no
serviceHealthWindows entry, no refreshSchedule constant, and no
FreshnessRail** (an indicator without a timer has no next-sample countdown;
this spec says so explicitly per the pattern skill). Freshness copy is
derived: the header clock renders the payload `scan_time`; the LAST SESSION
strip cell renders `last_date` plus the winning `source`. No copy claims a
refresh cadence.

## Payload contract (FastAPI builds it; the Next route passes it through)

`GET /streaks/{ticker}` (FastAPI, JWT-protected by default) and
`GET /api/streaks?symbol={ticker}` (Next.js proxy, `radonCapability: "read"`).

```json
{
  "symbol": "SPY",
  "scan_time": "2026-08-30T21:14:00+00:00",
  "source": "ib | uw | rh | yahoo | cache",
  "missing": false,
  "count": 2515,
  "first_date": "2016-08-30",
  "last_date": "2026-08-28",
  "current": { "date": "2026-08-28", "close": 645.31, "streak": 3, "day_change_pct": 0.81 },
  "stats": {
    "max_streak": 12,
    "max_streak_end": "2017-10-05",
    "runs_total": 620,
    "runs_ge_current": 38,
    "avg_run": 1.9,
    "up_day_pct": 53.4
  },
  "series": [ { "date": "2016-08-30", "close": 217.38, "streak": 0 } ]
}
```

- `source` uses the persisted writer vocabulary: the Robinhood rung is
  `"rh"` (`clients/robinhood_client.py` `RH_SOURCE`, pinned by
  `test_streaks_route.py`). `web/lib/streaks.ts` still accepts a legacy
  `"robinhood"` from cache envelopes written before REL-174; nothing emits
  it today.
- Closes are deduped by date (last write wins), sorted ascending; only
  finite closes > 0 survive.
- `max_streak_end` = the most recent date whose streak equals `max_streak`.
- `runs_ge_current` = runs (completed or in-progress) with length >= the
  current streak; `null` when the current streak is 0.
- `avg_run` = mean run length, 2 dp; `up_day_pct` = share of sessions with
  a gain over the prior close, 1 dp; `day_change_pct` = last close vs prior
  close, 2 dp.
- **Missing contract**: fewer than 2 usable closes from every source is
  HTTP 200 with `{ missing: true, count: 0, current: null, stats: null,
  series: [] }` — never a 4xx. An invalid symbol is a 400 (request bound,
  not an empty state). FastAPI unreachable from the Next route is a 502
  with a scrubbed detail.

## API route (Next.js)

`web/app/api/streaks/route.ts` — GET only, `runtime = "nodejs"`,
`radonCapability = "read"`, `requireRouteAccess` with a 20/min rate key,
`boundedTicker` on `symbol`, per-symbol single-flight map, `radonFetch`
timeout 60s (IB 15s + UW + Robinhood + Yahoo worst case), 502 + scrubbed
detail when FastAPI is down. No disk reads, so the disk-route cache
contract does not apply; the client hook still fetches `cache: "no-store"`.

## UI spec

- **Tab**: `streaks` in the "Breadth & sentiment" rail group, label
  `STREAKS`, route `web/app/regime/streaks/page.tsx`.
- **Ticker form** (always rendered, above the gates): uppercase text input
  (`maxLength` 10) + submit button labelled `LOAD`; validates against
  `TICKER_PATTERN`; invalid input shows an inline error and does not fetch.
  Submitting the same symbol refetches. The chosen symbol is mirrored to
  `?symbol=` via `router.replace` for deep links; default symbol `SPY`.
- **Gates**: `SpectralLoader label="Loading daily close series"` while
  `loading && !data` → error state (`SectionEmptyState`, danger tone,
  headline "Streak feed unreachable") when the route errored → missing
  state (`SectionEmptyState`, headline `No daily history for {SYM}`,
  secondary names the four sources) → content.
- **Strip** (desktop `RegimeStrip`, mobile `MetricCell` 2x2 grid):
  - `CURRENT STREAK` — `N DAYS`/`1 DAY`, positive tone when > 0.
  - `RECORD STREAK` — `max_streak` with `LAST HIT {max_streak_end}` sub.
  - `RUNS >= CURRENT` — `{runs_ge_current} RUNS` or `---` when streak 0.
  - `LAST SESSION` — close (2 dp) with day-change %, sub `{last_date} · {SOURCE}`.
- **Chart** `web/components/StreaksChart.tsx` (new, `ChartPanel`
  family="analytical-time-series"): one SVG, two panes sharing the x index
  scale — top pane daily close as a log-scaled line
  (`chartSeriesColor("primary")`), bottom pane streak bars
  (`chartSeriesColor("comparison")` via `color-mix`), integer y ticks on
  the streak pane, shared hover tooltip (date / close / streak), reusing
  `.regime-relationship-*` and `.chart-tooltip` classes. Title
  `{SYM} DAILY CLOSE VS CONSECUTIVE DAILY GAINS`. No raw hex; 4px max radius.
- **Range**: `HistoryRangeChips` (session presets incl. 3Y/5Y, default
  `all` — the full-history histogram is the point of the reference chart)
  + `BrushMinimap` over closes (`testIdPrefix="streaks-brush"`).
- **Testids**: `streaks-form`, `streaks-symbol-input`, `streaks-strip-current`,
  `streaks-strip-record`, `streaks-strip-precedent`, `streaks-strip-last`,
  `streaks-mobile-grid`, `streaks-chart-section`, `streaks-range-chips`,
  `streaks-chart` (svg), `streaks-brush*`.

## File checklist

Create: `scripts/utils/streaks.py`, `scripts/api/routes/streaks.py`,
`scripts/tests/test_streaks.py`, `scripts/api/tests/test_streaks_route.py`,
`scripts/tests/fixtures/yahoo_chart_sample.json`, `web/lib/streaks.ts`,
`web/lib/useStreaks.ts`, `web/app/api/streaks/route.ts`,
`web/components/StreaksChart.tsx`, `web/components/StreaksPanel.tsx`,
`web/app/regime/streaks/page.tsx`, `web/tests/streaks-api.test.ts`,
`web/tests/streaks-panel.test.tsx`, `web/e2e/streaks-tab.spec.ts`.

Modify: `scripts/api/server.py` (mount router), `web/lib/regimeRail.ts`
(union + group + label), `web/components/RegimePanel.tsx` (import, pathname
regex, mobile chip array, dispatch branch),
`web/tests/regime-tab-routes.test.tsx` (describe.each row + stub + cases),
`web/tests/assistant-catalog-pin.test.ts` (`"streaks": "read"`).

Deliberately NOT touched (on-demand, no timer, no snapshot writer):
migrations, `scripts/db/writer.py`, `scripts/watchdog/services.py`,
`cloud/services/*`, `setup-vps.sh`, `installed-units.sha256`,
`cloud/tests/test_systemd_services.py`, `web/lib/serviceHealthWindows.ts`,
`web/lib/refreshSchedule.ts` and their pin tests. The FastAPI authz matrix
(`test_route_authz_matrix.py`) enumerates routes at runtime, so the new
JWT-protected route is automatically in scope with no test edit.
