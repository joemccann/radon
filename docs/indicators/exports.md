# Taiwan and South Korea export lead

## Scope and identifiers

Route `/regime/exports`; API `/api/asia-export-lead`; service/snapshot/health key
`asia-export-lead`; component `AsiaExportLeadPanel`; hook `useAsiaExportLead`;
tab `EXPORT LEAD` (mobile `EXPORTS`); registry group `Models`; migration `0099`.
The user approved official exports first, with licensed MSCI EPS added later.

The supplied chart labels TOTAL exports, not semiconductor-only exports. This
indicator implements a stated export calculation, not an exact Bloomberg replica.
There is no licensed EPS input, EPS forecast, validated lead statistic, GDP overlay,
yield prediction, or hardcoded October 15, 2026 "no downturn" conclusion.

## Definition and interpretation

For calendar month m and each country, compute
`yoy[m] = 100 * (exports_usd_mn[m] / exports_usd_mn[m-12] - 1)` when both values
are finite and the denominator is positive. The composite `mean_yoy_pct` is the
equal arithmetic mean of BOTH country YoY rates for the same month. This differs
from averaging export-dollar levels before computing growth.

`ma3_yoy_pct` is the mean of THREE consecutive calendar-month composite readings.
Missing components or a missing month make the result null, never a smaller-window
average, interpolation, forward fill or row-offset lag. `ma3_change_pp` subtracts
the immediately preceding calendar month's valid MA3, else null. `lead_date` is m
plus SIX calendar months, preserving year rollover. Dates shifted into the future
are comparison alignment of already observed exports, never future observations.

Growth state: MA3 > 0 expanding, < 0 contracting, = 0 flat, null unavailable.
Momentum: change > 0 accelerating, < 0 slowing, = 0 unchanged, null unavailable.
Zero is a mathematical growth boundary, not an optimized trading threshold.
The proposed six-month EPS relationship remains unvalidated without licensed EPS.

## Verified primary sources and rights

### Taiwan

- Dataset: https://data.gov.tw/en/datasets/8378
- GET CSV: https://web02.mof.gov.tw/njswww/webMain.aspx?codlst0=11001001&compmode=00&cycle=41&fld0=1&funid=i8101&kind=21&outkind=1&outmode=12&sys=220&type=4&utf=1&ym=9000
- Exact captured fixture: `scripts/tests/fixtures/asia_export_lead_taiwan_sample.csv`.
- UTF-8 BOM, annual and monthly rows interleaved. Strict monthly label
  `ROC年 month月`; Gregorian year = ROC + 1911. Ignore annual/aggregate rows.
- Header `按美元計算/ 出口總值` is total exports in USD MILLION, FOB. The
  dataset's generic NTD description must not override its explicit USD fields.
  September fixture 87222.162 million agrees with MOF's US$87.22bn release.
- 309 monthly observations, 2001-01 through 2026-09 in captured response.
- General-trade series backfilled to 2001 after the 2016 methodology change;
  do not splice unverified earlier special-trade history.
- Monthly early-month preliminary release, later revisions and annual adjustments.
  September 2026 released October 8; next announced November 9, 16:00 Asia/Taipei:
  https://www.mof.gov.tw/singlehtml/384fb3077bb349ea973e7fc6f13b6974?cntId=856cf6c5ea4646208d3b8345b3bb46e2
- Methods: https://web02.mof.gov.tw/njswww/webMain.aspx?funid=i7000&kind=10&sys=100&type=1
- Rights PASS: royalty-free storage, transformation and display with attribution
  under OGDL 1.0, https://data.gov.tw/license . No paid licence or credentials.

### South Korea

- Portal: https://www.tradedata.go.kr/cts/index_eng.do
- Public machine POST: https://www.tradedata.go.kr/cts/hmpgEng/retrieveTradeEngAll.do
- Form: `priodKind=MON`, `priodFr=200001`, `priodTo=YYYYMM` (last completed
  calendar month in Asia/Taipei), `langTpcd=ENG`, `ttwgTpcd=1000`,
  `selectPaging=1`, `showPagingLine=1000`, `sortColumn=`, `sortOrder=`.
- Public client documentation: https://www.tradedata.go.kr/cts/js/ets/hmpgEng/trade/ETS0200012Q.js
- Exact fixture: `scripts/tests/fixtures/asia_export_lead_korea_sample.json`.
- JSON object `count`, `items`; strict `priodTitle` YYYY.MM; `expUsdAmt` is a
  padded comma-separated integer in THOUSAND USD. Divide by 1000 for USD million.
  Skip `TOTAL`; do not use weights, counts, imports or daily averages.
- Captured response count 322 includes one TOTAL and 321 months, 2000-01 to
  2026-09. Detect truncated/paginated responses; never silently retain page one.
- Finalized monthly data normally appears around the 15th. Full-month provisional
  release is around the 1st; days 1-10 around the 11th; days 1-20 around the 21st,
  with holiday shifts. No verified release time-of-day for the bulk feed:
  https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do?bbsId=2280&mi=10600&nttSn=10141577&nttSnUrl=862449616390be79d16b6d008088964d
- Partial-month estimates are OUT OF SCOPE. Do not mix them into monthly history.
- Revisions matter: September KCS fixture is US$121.050082bn versus MOTIR's
  preliminary US$120.94bn. Compute growth from the same KCS vintage, rather than
  pairing revised levels with the preliminary press-release YoY rate.
- Rights PASS: KCS public-data policy permits free reuse including commercial use:
  https://www.customs.go.kr/kcs/cm/cntnts/cntntsView.do?cntntsId=2611&mi=7422 .
  Use numerical public data, excluding third-party works or protected information.

Use honest `User-Agent: radon/2.0`, verified TLS, bounded requests and retries.
Both feeds work without authentication, cookies or browser impersonation.
Neither currently sends a verified Last-Modified; preserve null when absent.
IB contract OHLC, Robinhood stock/option history and UW economic-calendar events
are not these sovereign customs series; official specialized feeds are appropriate.
No Yahoo or unofficial proxy is needed. Historical data is latest-revised, not
point-in-time; no backtest or historical availability claim is made.

## Python contract and persistence

`scripts/fetch_asia_export_lead.py` exposes:

- `parse_taiwan_csv(raw: bytes)` and `parse_korea_json(raw: bytes)` returning
  ascending unique `{date: YYYY-MM, exports_usd_mn: finite nonnegative number}`.
  Reject malformed numeric monthly rows, conflicting duplicates and Korean
  count/pagination mismatches; a malformed/non-JSON/HTML source never becomes
  an empty success. First refresh requires the verified country history floor
  and contiguous calendar coverage through each source's reported latest month.
  Every replacement refuses loss of any previously observed country month.
  A first-ever response cut at a valid monthly tail cannot be distinguished
  from a delayed release with these payloads; no release date is invented.
- `compute_series(taiwan_rows, korea_rows)` emits the sorted union of source months,
  preserving nulls when either country is missing. Keep country-only prefix/tail
  levels so a conditional GET can reuse every previously fetched observation.
- `build_output(taiwan_rows, korea_rows, *, scan_time=None, source_metadata=None)`.
- `_write_db_cache(payload, scan_time, *, rows_changed)` writes Turso in this order:
  `ensure_no_replica_for_writers`, changed history rows, `upsert_scan_snapshot`,
  `record_service_health`. DB failure must not be reported as successful persistence.
- `persist_result(payload, *, previous=None, path=None)` refuses empty/invalid
  output, compares normalized series for changed history, then writes DB and an
  atomic disk fallback. Every successful cycle refreshes snapshot and heartbeat. CLI failures attempt an
  error heartbeat and remain nonzero even when that heartbeat cannot be stored.
- `refresh(client, *, previous=None, now=None, path=None)` uses client methods
  `fetch_taiwan(if_modified_since=None) -> (bytes|None, Last-Modified|None)` and
  `fetch_korea(end_month: str) -> bytes`. A Taiwan 304 requires valid cached Taiwan
  levels; Korea POST is refreshed normally. Unchanged normalized rows skip upserts.

Load previous snapshots from Turso first with disk fallback; do not depend on an
ephemeral file to preserve the durable series. Do not overwrite last-good data or
write an OK heartbeat after source/DB failures. CLI `--json` stdout contains only
JSON; progress/error text goes to stderr. No network in pytest.

Payload schema (version 1): `missing:false`, `scan_time` aware UTC ISO string,
`source_last_modified:null|string`, `data_date` latest common export month END as
YYYY-MM-DD, `count`, `series`, `current`, `sources`, `comparison`.

Each series row has `date` YYYY-MM, `lead_date` YYYY-MM, nullable
`taiwan_exports_usd_mn`, `korea_exports_usd_mn`, `taiwan_yoy_pct`, `korea_yoy_pct`,
`mean_yoy_pct`, `ma3_yoy_pct`, `ma3_change_pp`. `current` is the latest month
with both country levels, retaining null statistics when the required history has
gaps. `current.growth` and `current.momentum` use the states above. `sources`
contains `taiwan` and `korea` provenance with exact keys `name`, `url`,
`licence_url`, `latest_month`, `last_modified` (nullable Last-Modified) and
`sampled_at` (aware UTC timestamp). `comparison` is
`{available:false, label:"MSCI US forward 12-month EPS", reason:"Licensed EPS series not connected"}`.

Migration `0099_asia_export_lead.sql` creates `asia_export_lead_history`, date TEXT
PRIMARY KEY, the eight other row fields above (`lead_date` TEXT NOT NULL, numeric
fields REAL nullable), `recorded_at TEXT NOT NULL`, descending date index and
schema_migrations version 99. Writer `upsert_asia_export_lead_rows(rows,
recorded_at=None)` is idempotent, bounded-batch, correct placeholder arity. Source
and comparison metadata live in the snapshot, not invented historical timestamps.

Fixture acceptance: common YoY starts 2002-01; MA3 starts 2002-03. Latest
2026-09 Taiwan YoY 60.86788525833797, Korea 83.67605192743797, mean
72.27196859288797, MA3 58.43498029405504. Shifted MA3 range 2002-09 to 2027-03.

## API, cache and schedule

Next GET `/api/asia-export-lead`: dynamic force-dynamic, runtime nodejs,
radonCapability read; registered static assistant loader. Bounded `dbExecute`
reads use the shared principal-aware `snapshotMaxAgeMs`: real principals retain
the 26-hour window, while demo principals use the shared mirrored-snapshot
window. The demo route policy, rate-bucket segment and middleware authorization
classification are pinned. The route remains subject to the existing default-deny
authentication perimeter. `dbFirstRead` reads only
service asia-export-lead snapshots, compares actual content timestamps against disk
`data/asia_export_lead.json`, and uses shared closed freshness window 26h.
Missing is HTTP 200 with exact `{missing:true,scan_time:null,
source_last_modified:null,data_date:null,count:0,series:[],current:null,sources:null,
comparison:{available:false,label:"MSCI US forward 12-month EPS",reason:"Licensed EPS series not connected"}}`.
Cache max-age 300s, stale-while-revalidate 3600s and request ID via shared helpers.
An expired snapshot uses shared `staleCollapse`, retaining its scan_time and
`stale:true` alongside the missing contract instead of erasing outage provenance.
Hook hourly GET only, no-store via useSyncHook; no POST scan action.

`radon-asia-export-lead.timer`: `OnCalendar=*-*-* 09:40:00 UTC`, daily including
weekends for heartbeat, Persistent=true, RandomizedDelaySec=0. It samples after
Taiwan's usual local afternoon release; it does not promise daily source updates.
Service uses standard oneshot/venv/env pattern, direct cloud writes,
TimeoutStartSec=300. Frontend `ASIA_EXPORT_LEAD_REFRESH = [daily(9,40)]` must be
pinned against that timer. Watchdog and all three frontend health windows 26h,
scheduled, requires_ib=false; add daily-bucket registration. Manifest hashes,
setup SERVICE_FILES and canonical systemd tests are lockstep.

## UI and verification

Model the module on MarginDebtPanel using Clear theme tokens and existing chart
components. Desktop strip and compact mobile grid: composite MA3, Taiwan YoY,
Korea YoY, MA3 change (pp), source month and sampled clock. Explicit dated growth
and momentum labels; unavailable values render `---`, never NaN/Infinity.

Mount `FreshnessRail` directly below the strip with schedule
ASIA_EXPORT_LEAD_REFRESH, `model="release"`, `asOf=data.data_date`,
`testId="asia-export-lead-freshness-rail"`,
`asOfTestId="asia-export-lead-strip-asof"`. The existing release-model countdown is labelled `Next check`: it names the
next scheduled SAMPLE, not the next monthly release. Source updated displays actual month, never scan_time
as an upstream publication timestamp.

Default chart composite MA3. Alignment chips `Observed` (default) / `Shift +6M`
move dates exactly six calendar months. View chips `Composite` / `Countries`;
countries plots both YoY rates unshifted. Both use shared percent axes, zero line,
linear scale, CriHistoryChart with gap breaks, month ticks, month-aware range chips
1Y/3Y/5Y/All (default All), BrushMinimap. No fake black EPS line or price substitute.
Visible copy: `Total exports, not semiconductor-only exports.`,
`Six-month alignment is not an earnings forecast.`,
`MSCI US forward EPS comparison unavailable.`, `Latest-revised history.`
Tooltip explains equal weighting, strict three-month smoothing and missing periods.
No copy claims a measured or validated earnings/yield lead.

Python/API/panel tests must run red before implementations. API uses real in-memory
libSQL; panel mocks only hook, uses real chart and rail, checks null/NaN, controls,
source period, and ticking countdown at a window-relative timer slot. E2E covers
mobile/desktop, active tab, chart paths, controls, brush, missing state, no overflow.
Separate real-API screenshots in both themes and mobile must show actual Turso
data, sample clock and a countdown pinned to the parsed timer, no route mocks.

## Ownership and delivery

Ingestion lane owns collector/client, migration/writer, watchdog, service/timer,
manifest/setup and relevant cloud pins. API lane owns route/types/hook, static
assistant nextLoader, refreshSchedule and serviceHealthWindows plus their tests.
UI lane owns panel, regimeRail, RegimePanel, page, route tests and E2E.
Root owns this spec, initial red tests, integration, full gates, UI review, PR,
exact-head CI, one accepted Pushover, admin merge and production verification.
Use managed worktrees; preserve primary WIP. Verify migration99, actual Turso rows
and snapshot, production API/auth perimeter, live browser, enabled timer and one
successful scheduled service heartbeat. No operator steps expected after deploy.

## Verification receipt (2026-10-19)

- Initial missing-module Python/API/panel failures were recorded before the three
  implementation lanes. Source completeness and CLI heartbeat regressions were
  separately reproduced and repaired; both countries reject lost observations
  before persistence. Independent Sol review approved the repair; Luna passed
  35 focused UI/API/hook cases.
- Focused final verification: 63 ingestion/registration, 121 API integration and
  176 UI cases passed. Collector executable line coverage 99.2%, client 100%;
  branch coverage unmeasured.
- Full Vitest: 11,330 passed, 21 skipped. Full Python: 16,438 passed, 10 skipped;
  54 unchanged local Gateway TLS-fixture failures. Full cloud: 3,264 passed,
  10 skipped; 12 unchanged Mac Linux-tooling failures. Full typecheck retains
  the two existing optional-content errors in DashboardNewsFeed and
  NewsfeedLightbox, with no export-indicator errors.
- Production compile and output trace audit passed (230 manifests). All six
  production-build Playwright cases passed, including both themes, phone and
  desktop, readable month labels, keyboard brush and resize navigation.
- Real official-source ingestion applied schema 99 and stored 321 history rows
  with September 2026 MA3 58.43498029405504%. Real Chrome used the live Turso API
  without route mocks in both themes at phone and desktop widths; the source
  period, sample timestamp, plotted data and timer-derived 09:40 UTC countdown
  were verified.

Screenshots: [light](exports-tab.png), [dark](exports-tab-dark.png),
[phone](exports-tab-mobile.png).
