# VIX model spread

## Source and scope

Source research: [verified transport, methodology and numerical reproduction](vix-model-spread-source-research.md).
User confirmed personal, non-commercial use on 2026-10-18. Do not distribute
bulk source history, expose live values to demo principals, or add public share
routes. GET requires `requireRouteAccess(request, { operatorOnly: true })`.
Source fixtures are three factual observations per index, attributed to the
official URLs below. No bulk history or real-data screenshot is committed.

- slug/service: `vix-model-spread`; component: `VixModelSpread`; label: `VIX MODEL`.
- migration: `0098_vix_model_spread.sql`, recheck uniqueness before shipping.
- VIX/COR1M: `https://cdn.cboe.com/api/global/us_indices/daily_prices/<SYMBOL>_History.csv`, columns DATE,OPEN,HIGH,LOW,CLOSE.
- VIXEQ: same URL pattern, columns DATE,VIXEQ. Dates MM/DD/YYYY.
- Cboe CSV access is anonymous, honest UA radon/2.0; reuse CboeClient retries and conditional GET.
- Official source exception: IB COR bars have documented lag; bounded pooled
  VIXEQ requests failed, availability unknown. Existing UW account explicitly
  lacks Cboe index approval. Robinhood does not supply these index series.
  Do not replace VIXEQ with equity/ETF IV, realized vol or Yahoo proxies.
- Common history begins 2014-06-19; VIXEQ before its 2024-11-04 launch is backtested.
- Daily source files, no promised publication SLA. Observed VIX/VIXEQ update
  01:11 UTC, COR1M 21:31 UTC on the latest sample. Sampling is daily 02:50 UTC,
  including weekend unchanged-data heartbeats. This is a sampling time, not
  a promise of source publication.

## Calculation

Inner join identical source session dates only. Sort and deduplicate per date,
reject invalid ISO dates, nonfinite/nonpositive VIX/VIXEQ and COR1M outside
[0,100]. Zero correlation is valid. Never forward fill a missing index.

`model_vix = sqrt(cor1m / 100) * vixeq`

`spread = vix - model_vix` in volatility points, retaining its sign.

For each complete trailing window W in [20,252], including the current
observation, compute mean_W, population standard deviation std_W (ddof=0),
upper_W = mean_W + 2*std_W, lower_W = mean_W - 2*std_W, and
z_W = (spread-mean_W)/std_W. Warmup fields are null; zero SD yields null z.
No rounding before statistics. Default UI W=20; labeled option W=252 matches
the screenshot. Changing visible chart range never recomputes statistics.
State is `ABOVE BAND` only when spread > upper_W, `BELOW BAND` when spread <
lower_W, else `WITHIN BAND`; incomplete/zero-SD states are `WARMUP`/`NO VARIATION`.
Positive basis alone is not an overshoot. This is a descriptive approximation,
not official Cboe fair value or a forecast of VIX declines.

Calibration 2026-10-16: VIX 19.99, VIXEQ 41.71, COR1M 10.07 => model
13.235944124617633, spread 6.754055875382365. 20-session mean 4.327848928602837,
upper 5.732170299750478, lower 2.9235275574551958, z 3.4553443344621044.
252-session mean 3.9495507725205607, upper 5.781874956138282,
lower 2.1172265889028403, z 3.061145105146864.

## Python and persistence contract

`scripts/fetch_vix_model_spread.py` exports parse_index_csv(text,symbol),
compute_series(vix_rows,vixeq_rows,cor_rows), build_output(series,scan_time,
source_last_modified), persist_result(payload,rows_changed=True), and
run(client=None,previous=None). Rows entering compute_series use {date,value}.
Each output entry has date,vix,vixeq,cor1m,model_vix,spread and
mean_20,std_20,upper_20,lower_20,z_20,mean_252,std_252,upper_252,lower_252,z_252.

Payload: scan_time UTC ISO, source_last_modified keyed VIX/VIXEQ/COR1M,
source `cboe`, status `ok` or `stale_source`, data_date/as_of (common latest
date), expected_session, source_dates (latest per source), count, current
(last entry), series[]. Latest expected completed session comes from the
existing exchange calendar. Lag remains visible even on a new scan_time.
Failure/empty source must preserve last-good snapshot/cache and record error
service health. Reject a changed source whose raw newest date regresses.

On partial 304, use retained raw per-source series from previous payload
`source_series` (private operator-only payload), or unconditionally refetch
if unavailable; never treat missing bytes as an empty source. All-304
refreshes scan_time/snapshot and service health but does not upsert history.
Even on unchanged data, recompute expected_session/status from current clock.
Same-date corrections change derived series and must upsert history. A stable series_hash compares complete derived histories. Prior Turso reads fetch metadata and each raw source separately to respect the Python Hrana 1 MiB response bound; the Node route uses one query.

`vix_model_spread_history`: date TEXT PRIMARY KEY, vix/vixeq/cor1m/model_vix/
spread REAL NOT NULL, all ten rolling fields nullable REAL, recorded_at TEXT
NOT NULL; descending date index; schema_migrations version98. Writer
`upsert_vix_model_spread_rows(series,recorded_at=...)` is idempotent. Execute
ensure_no_replica_for_writers, history on change, snapshot publication
(`vix-model-spread`), record_service_health every successful run, then atomic
data/vix-model-spread.json fallback. --json stdout JSON only. Errors exit
nonzero. Reuse actual production migration runner, never auto-create schema
in the writer. Full-history snapshots exceed a single practical upload: send
64 KiB JSON chunks through bounded Hrana requests into an isolated UUID staging
row, then atomically promote the fully assembled JSON. Readers never observe a
partial document. Retain 30 canonical snapshots, remove staging on success or
failure, and expire abandoned staging older than one day on the next run. The
large-snapshot path has a 180-second work budget with 4-second request limits.

## API and freshness

GET /api/vix-model-spread, dynamic force-dynamic, runtime nodejs,
radonCapability read. Operator guard executes before any storage read.
dbFirstRead with the shared bounded dbExecute reader chooses freshest valid snapshot versus disk, isMissingPayload
degradation guard. Absent/expired: HTTP200
{missing:true,status:"missing",scan_time:null,as_of:null,data_date:null,
count:0,current:null,series:[]} plus staleCollapse diagnostics as applicable.
Daily every-calendar-day timer uses shared 48h closed freshness budget and
26h open budget in both watchdog catalogs; API uses closed window48h.
Cache headers300/3600, tag vix-model-spread. Register assistant static loader,
all route access/demo/catalog/refresh pin inventories. No demo raw fixture.

`VixModelSpreadData`, `VixModelSpreadEntry`, `SpreadWindow=20|252` in
web/lib/vixModelSpread.ts. `useVixModelSpread` GET-only useSyncHook hourly,
extractTimestamp scan_time. No client recomputation of rolling statistics. The HTTP response omits writer-only source_series; SQL removes it before transfer, and the disk reader omits it too.
`VIX_MODEL_SPREAD_REFRESH = [daily(2,50)]` mirrors
OnCalendar=*-*-* 02:50:00 UTC, no jitter; FreshnessRail directly under strip,
testId vix-model-spread-freshness-rail, asOfTestId
vix-model-spread-strip-asof, asOf=data.data_date.

## UI

Regime Volatility group includes VIX MODEL, /regime/vix-model-spread.
Panel VixModelSpreadPanel, dedicated VixModelSpreadChart using ChartPanel,
D3 and existing chart-axis helpers. Signed histogram around zero: positive
spread var(--negative), negative var(--positive); selected-window mean and
upper/lower 2-SD lines use chart tokens. Hover shows date, spread, model,VIX,
mean and bands with selected window label. Null bands break paths, no NaN.
Preserve gaps rather than fabricate source values; show actual source date.

Hero shows spread points and ABOVE/WITHIN/BELOW BAND; supporting strip VIX,
model, selected mean, selected z and source date via rail. Loader
`Loading VIX model spread`; empty headline `No aligned VIX model data`.
Window buttons `20 sessions`, `252 sessions`; chart title `VIX MODEL SPREAD`.
HistoryRangeChips and BrushMinimap with prefix vix-model-spread-brush; default
1y if sufficient sessions, all for short samples. W selection separate from
history range. Explain formula and positive historical basis in InfoTooltip.
Disclose backtested VIXEQ pre-launch history once in source footnote.
Use Clear CSS tokens and established RegimeStrip/mobile MetricCell patterns.
Handle refresh errors via existing toast helper, no inline request errors.

## Ownership and verification

- Ingestion: fetch script, writer, migration, Python tests and small source
  fixtures; watchdog services; cloud unit pair, manifest, setup-vps,
  systemd tests and any source/storage pins.
- API: types/helpers/hook, route, API tests, serviceHealthWindows and tests,
  refreshSchedule and tests, assistant loader and route capability/access
  inventories, demo/rate tier pins. Source operator gate must have tests.
- UI: panel/chart, RegimePanel/regimeRail, page/title, route/nav/panel tests,
  Playwright spec and curated CI E2E path if applicable; no API-owned edits.

Red Python, API and panel tests first; three parallel isolated implementers.
Root integrates and runs full Python/cloud/Vitest/typecheck serially across
Python/JS runtimes, browser desktop/mobile, real-source Turso ingestion,
live nonmocked browser paths and timer countdown, both themes screenshots
stored privately outside Git. PR full template, exact-head green CI,
Pushover once, authorized admin merge, gated deploy, migration/history/
snapshot/health/timer verification. No manual unit install is owed: pin both
unit hashes in installed-units.sha256 and deploy installs/enables them.
