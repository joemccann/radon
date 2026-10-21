# NYSE common-stock High-Low Line

## Scope and source

Route `high-low`; service `high-low-line`; component `HighLowLinePanel`; rail label
`HIGH LOW`, under breadth and sentiment. Additive migration `0101_high_low_line.sql`.
The requested chart is cumulative NYSE common-stock-only new 52-week highs less
new lows, with Parabolic SAR and raw StochRSI(14). Neither a zero oscillator nor
an oversold reading establishes a profitable or durable buying opportunity.

The operator answered **Approved** on 2026-10-21 to the explicit request to confirm
provider automation permission. This records the operator's confirmation; no
provider agreement was independently inspected. Keep the new API operator-only,
block demo access, and do not mirror the snapshot to the demo database.

Exact StockCharts DecisionPoint symbols are `!NEWHINYC` and `!NEWLONYC`.
The public history metadata explicitly names them NYSE Common Stock Only New
Highs and New Lows. Broad `$NYHGH`/`$NYLOW`, all-issue breadth and today's surviving
constituents are not substitutes. IB read-only metadata discovery did not resolve
these exact series; UW's catalogue does not certify them. Use this approved
specialist series rather than a differently defined broker index or Yahoo proxy.

Transport: honest `User-Agent: radon/2.0`, verified HTTPS, no login scraping or
browser impersonation. GET
`https://stockcharts.com/quotebrain/historyandquote/d?symbol=...&start=20030101&end=YYYYMMDD`.
Two bounded sequential requests; 8 MiB decoded body cap per source, bounded retries
and a 300-second service deadline. No ETag or Last-Modified was returned; use a
normalized content fingerprint for unchanged-history detection, not invented 304s.

Reference capture: 2026-10-21; each response has 5,990 intervals, comprising 5,988
valid matching sessions from 2003-01-02 through 2026-10-20, and paired all-null
placeholders on 2012-10-29/30. Those two Sandy closures are independently documented
by the [SEC](https://www.sec.gov/rules/sro/nysearca/2013/34-70097.pdf). Reject other
unknown null rows, mismatched dates, duplicate dates, unknown identities, future
sessions and nonfinite/negative values. SQL_DAILY timestamps carry a 16:00 ET label
even on half-days; this is not a verified publication timestamp.

Keep published `0.0001` values exactly: 64 highs and 211 lows in this capture.
Do not round them or infer they mean zero. Two low observations are literal zero.
Describe inputs as published breadth values; weekly totals are sums of daily
readings, not distinct stocks that reached a high/low during an entire week.
The repository is public. Captured provider history is retained only in private
verification artifacts. Checked-in fixtures are explicitly synthetic source-schema
examples; they do not redistribute the approved provider history.

## Mathematics and availability

`net = highs - lows`. Seed is zero **before** the first included observation on
2003-01-02. Accumulate with decimal arithmetic before serializing finite numbers.
The screenshot's 216667.98 has an unknown anchor/settings and is not a golden
value. Live levels are available only through the operator-only API and panel. Revisions recompute the whole derived history. Never restart the
oscillator at a selected viewport boundary.

Wilder RSI14: seed gain/loss averages using 14 changes; update each with weights
13/14 and 1/14. Both averages zero uses neutral RSI50, an explicit Radon convention.
Raw StochRSI14 is `(RSI - min(last14RSI))/(max(last14RSI)-min(last14RSI))`, range
0–1, no K/D smoothing. First possible result is the 28th cumulative observation.
Warmup and a flat RSI range are null, not zero. `reset_to_zero` requires the
immediately previous valid result to be greater than zero and current raw result
to equal zero; a formatted small positive `0.000` must not trigger it. Zone is
oversold at <=0.2, overbought at >=0.8, neutral between, unavailable for null.

SAR follows Wilder extreme/reversal/two-prior-low/high bounds. Screenshot parameters
appear to be `SAR(1.0,1.0)`; SharpCharts interprets these as start and maximum, with
increment equal to start. Use start=increment=maximum=1.0 and disclose this.
Daily OHLC is a close-only adaptation (high=low=cumulative close), not claimed
intraday/vendor parity. Weekly OHLC is the range of daily cumulative closes,
open=previous weekly close (or first observation initially), close=last daily
close. Seed trend from first two closes, SAR from the first low/high, first output
null; maintain explicit direction and reversal state.

Expose Daily and Weekly calculation selectors, default Weekly. Accumulate daily first, then calculate weekly
RSI/SAR from weekly cumulative OHLC; never take only Friday's daily net or sample
a daily oscillator as a weekly oscillator. Weekly date is its last observed
session, period_start is Monday and period_end Friday. `period_complete` becomes
true after Friday 18:00 America/New_York, conservatively even in holiday weeks.
An incomplete current week is included and explicitly marked Week in progress;
any zero/reset condition is provisional. Daily results use completed source bars
only, excluding OTHER/intraday and the current date before 18:00 ET.

Historical exchange-calendar completeness before 2023 is not independently
certified by the repo's rolling calendar. Require exact high/low date joins and
the known source start; after initial ingestion refuse loss of any previously
observed session before any persistence. Do not fill missing readings with zero.
Check current source lag against shared `last_completed_session_date(now-2h)`.
Lagged source is degraded even when fetch succeeds; retain as-of/sample distinction.

## Payload and persistence contract

Top-level fields: `missing`, `degraded`, `scan_time` (aware UTC),
`source_last_modified` (null when absent), `data_date`, `expected_session_date`,
`count`, `weekly_count`, `current`, `weekly_current`, `series`, `weekly_series`,
`source_hash`, `source`, `baseline`, `settings`.

Each entry: `date`, `period_start`, `period_end`, `period_complete`, `highs`,
`lows`, `net`, `cumulative`, `rsi14`, `stoch_rsi14`, `sar`, `sar_direction`
(`rising`/`falling`/null), `reset_to_zero`, `source_fractional`. All numerical
outputs finite; three rolling values may be null. Current entries add `zone`.
`source` identifies provider/name/url, exact symbols, history_start/history_end,
fractional_policy=`preserved`, fractional_observations and excluded_non_sessions.
`baseline={date:"2003-01-02",value:0,convention:"before_first_observation"}`.
`settings={rsi_period:14,stoch_period:14,smoothing:"Wilder",sar_start:1,
sar_increment:1,sar_max:1,daily_ohlc:"close_only",
weekly_ohlc:"daily_cumulative_range",default_interval:"weekly"}`.

Functions: `parse_stockcharts_history(raw,symbol,now=...)` returns
`{rows:[{date,value}], non_observations:[date]}`; `wilder_rsi(values,period=14)`;
`stoch_rsi(values,period=14)` consumes cumulative values, not precomputed RSI;
`parabolic_sar(ohlc,start=1,increment=1,maximum=1)` returns `(sar,direction)` pairs;
`build_output(highs,lows,now=...)` accepts parsed dictionaries and creates payload;
`collect(client=None,now=None)` uses `HighLowSourceClient.fetch_history(symbol,now=...)`;
`persist_result(payload,previous=None)` validates prior-session retention before
calling any writer. `main(argv=None)` writes an error heartbeat and exits nonzero
on collection/persistence failure, retaining the last good snapshot.

History table `high_low_line_history` stores the entry fields plus interval
(`daily`/`weekly`) and recorded_at, PK(interval,date), UNIQUE(interval,period_start),
descending date index. Weekly updates replace the same period_start row as its
last-observed date advances, preventing duplicate forming-week history.
`upsert_high_low_line_rows(rows,interval=...,recorded_at=...)` uses bounded
multi-row statements and fresh/bounded database I/O. Snapshot service and health
key `high-low-line`. Every successful cycle updates snapshot and health, even
unchanged; only changed history writes rows. Atomic JSON fallback
`data/high_low_line.json`; Turso remains authoritative. Never create a replica.

## API, schedule and UI

GET `/api/high-low-line`: dynamic Node, capability read, explicit operatorOnly
authorization before database/disk access. Bounded `dbExecute` and `dbFirstRead`
compare actual content scan_time. Authenticated absent data returns HTTP200:
`{missing:true,degraded:false,scan_time:null,source_last_modified:null,data_date:null,
expected_session_date:null,count:0,weekly_count:0,current:null,weekly_current:null,
series:[],weekly_series:[],source_hash:null,source:null,baseline:null,settings:null}`.
Malformed data fails closed to the missing contract; staleCollapse retains the
original scan_time. All-state freshness 26h, cache300/3600 plus private cache
semantics for this operator-only dataset. Hourly no-store GET hook, no POST.
Register static assistant loader/catalog, demo operator-blocked policy and route
classification; omit demo mirroring. Ensure output tracing excludes mutable data.

Timer runs **daily 00:30 UTC**, including weekends for writer health. This is
Radon's check time, not a verified vendor publication SLA. Service300s,
Persistent=true, no randomized delay; setup plus hash manifest installs/enables
both. `HIGH_LOW_LINE_REFRESH=[{hours:[0],minutes:[30]}]`. Shared health26h in all
states, no IB runtime dependency. Data lag and writer age are separate conditions.

Strip: cumulative, latest net, StochRSI with neutral/oversold/overbought state and
SAR direction. Under strip mount the shared freshness rail, schedule above,
`testId="high-low-line-freshness-rail"`, asOf testId
`high-low-line-strip-asof`, payload data_date. Show actual sampled UTC clock and
calculation interval/as-of separately, with Week in progress when applicable.

Upper `CriHistoryChart`: cumulative and SAR dots (extend renderAs with points
surgically, preserving existing line/bar behavior). Lower chart: raw StochRSI on
0–1 with 0.2/0.5/0.8 references and oversold/overbought bands. Both share the same
filtered rows and one range selector/BrushMinimap. 1Y/3Y/5Y/All, default5Y, finite
guards, readable phone date ticks, Clear theme tokens in both themes. Source,
baseline, fractional-value and SAR-construction notes remain visible. No buy
recommendation, untested durability claim or automatic alerts/execution.

## Verification and ownership

Red Python/API/panel tests precede three near-disjoint implementation worktrees.
Ingestion owns Python/client/fixtures/migration/writer/watchdog/units/manifest.
API owns types/hook/API/health/schedule/assistant loader/private-demo pins.
UI owns panel/route/rail/Regime registration/shared point-rendering and its tests,
Playwright plus exhaustive Clear/TesterArmy route inventory/missing fixture.
Root owns this spec, task log, integration, independent formula review, full gates,
real-data DB/API/browser screenshots, PR/CI/admin merge and production checks.
Coordinate all local suites through the exclusive test gate; never run pytest and
Vitest concurrently. No fabricated production history, mislabelled synthetic
samples or weakened pins.

Source/formula research and raw capture audit are retained outside the checkout:
`/Users/joemccann-mac-mini/.codex/state/jobs/high-low-line-20261020/`.

Primary references:

- https://articles.stockcharts.com/article/articles-chartwatchers-2007-01-new-nyse-common-stock-only-indicators/
- https://help.stockcharts.com/learning-more/policies-and-limitations/usage-limitations
- https://chartschool.stockcharts.com/table-of-contents/market-indicators/net-new-52-week-highs
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stochrsi
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/parabolic-sar

## Verification

The 2026-10-21 captured full history has 5,988 daily observations and 1,243 weekly
observations. An independent 42-digit Decimal implementation checks every RSI,
raw StochRSI, SAR value and SAR direction; zero disagreements beyond floating-point
precision (maximum oscillator error 2.30e-12). The live source through 2026-10-20
is validated privately against independent calculations; current readings are
served through the operator-only API, not published as fixture data.

Persistence regression tests cover source-session retention before any write,
last-good preservation on source/write failure, bounded reconstruction of the
2.37 MiB previous snapshot, unchanged-source heartbeats, published fractions and
one canonical weekly history row as its last-observed date advances.

API tests exercise real SQLite SQL, exact missing/stale contracts, private cache,
clock precedence, malformed-source rejection and real operator/demo/anonymous
access decisions before I/O. Shared-chart tests preserve line/bar behavior while
checking points and finite fixed domains. Production-build Playwright verifies
both themes and phone/desktop, intervals, common brush/ranges, finite SVGs,
source/sample/freshness labels, missing data, overflow and the exhaustive route
inventory. The new browser spec is curated in CI. Final verification includes
actual Turso rows/API content and authenticated production browser inspection.
