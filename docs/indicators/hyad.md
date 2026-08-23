# HYAD — High Yield Bond Cumulative Advance-Decline Line

Route `/regime/hyad` · service `hy-ad` · component `HyAdPanel` · tab label `HY AD` · migration `0056`

## Signal

FINRA TRACE end-of-day corporate bond market breadth, high-yield bucket. For each
trading day, combined across `bondType CORP` and `CORP_144A` (HY is column `fieldC`
on both):

```
net = advances - declines
cum = running sum of net, starting at 0 on the first stored date (2018-01-22)
```

The chart pairs the cumulative line against SPX (log). The LEVEL of a cumulative A-D
line is arbitrary (an additive constant set by the accumulation start); only the
SLOPE and divergences against SPX carry signal - the McClellan chart's ~105,000
level at 2022-12-30 reflects an earlier accumulation start and must not be matched.
HY bond breadth is a liquidity/credit canary: the line falling while SPX rises
(negative divergence) historically precedes risk-off phases; the line confirming new
SPX highs supports the advance.

Derived at payload build (never stored): `ma21` and `ma50`, the 21-day and 50-day
simple moving averages of `cum`.

Regime label (pinned in the panel test; ties are MIXED — strict inequalities):

| condition | label |
|---|---|
| cum > ma21 and ma21 > ma50 | `ADVANCING` |
| cum < ma21 and ma21 < ma50 | `DETERIORATING` |
| anything else (including any equality) | `MIXED` |

`ma21`/`ma50` are null until enough history exists after the series start; regime
label is `MIXED` when either MA is null.

## Source (verdicts from the Step 1 research run, 2026-08-23)

- **Endpoint**: `POST https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/MarketActivityAggregates`
  - Headers: `User-Agent: radon/2.0` (FINRA Cloudflare blocks browser UAs — repo
    precedent `scripts/clients/finra_client.py`; never impersonate a browser),
    `Content-Type: application/json`, `Accept: application/json`, plus the
    **double-submit CSRF pair**: `Cookie: XSRF-TOKEN=<uuid>` and
    `X-XSRF-TOKEN: <uuid>` where any self-minted UUID works as long as cookie and
    header match. No login, no API key. GET is unsupported (400); missing token is
    401; `api.finra.org` is 401 for this dataset and MUST NOT be used.
  - Body shape (verified): `fields` is REQUIRED —
    ```json
    {"fields":["originalTradeReportedDate","bondType","dataTypeDescription","fieldA","fieldB","fieldC","fieldD"],
     "limit":5000,"offset":0,
     "sortFields":["+originalTradeReportedDate"],
     "compareFilters":[{"fieldName":"bondType","fieldValue":"CORP","compareType":"EQUAL"}],
     "dateRangeFilters":[{"fieldName":"originalTradeReportedDate","startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}]}
    ```
  - **Transport quirk**: the response envelope is
    `{"status":"success","returnBody":{"headers":{...},"data":"<json-string>"}}` —
    `returnBody.data` is a JSON **string** requiring a second `json.loads`. The
    parser must handle this (pinned in pytest against the fixture).
  - Rate limits: none observed; `Record-Max-Limit: 5000` per page.
- **Schema** (long format, 7 rows per date+bondType):
  `originalTradeReportedDate` (yyyy-MM-dd), `bondType` (`CORP`|`CORP_144A`|`AGENCY`),
  `dataTypeDescription` (`Advances`|`Declines`|`Unchanged`|`Total Issues Traded`|
  `52 Week High`|`52 Week Low`|`Dollar Volume`), and per-column values where for
  CORP/CORP_144A: `fieldA` = All, `fieldB` = Convertibles, **`fieldC` = High Yield**,
  `fieldD` = Investment Grade. For AGENCY the same columns mean different agencies —
  AGENCY rows are ignored. Validation: all counts >= 0 and
  `advances + declines + unchanged <= total` (STRICTLY less-or-equal, never assert
  equality — securities without a trade in the prior 10 business days are excluded
  from adv/dec/unch but count in Total).
- **Data-source priority**: only FINRA computes TRACE breadth aggregates — IB has no
  TRACE aggregates, UW has no bond endpoints, Yahoo has nothing comparable. Distinct
  from CREDIT (HYG OAS, price-based) and IEI-HYG (price ratio): HYAD is issue-count
  breadth.
- **Licensing**: FINRA-published aggregate statistics, same treatment as the shipped
  margin-debt indicator; display cites "Source: FINRA Fixed Income Market Activity".
- **History**: dataset starts 2018-01-22 (~2,156 trading days per bondType).
- **SPX overlay**: read `credit_spread_history.spx_close` from Turso (2007+; IB-first
  writer already live). Never fetch SPX again.

## Backfill (run once with `--backfill`)

Two paged pulls (one per bondType compareFilter), `limit:5000`, offsets
0/5000/10000/15000 → 8 requests (~5MB total), from 2018-01-22 through today. Rows
merge CORP + CORP_144A per date by summing the HY (`fieldC`) counts.

## Ingestion — `scripts/fetch_hyad.py`

Composed-method style, stdlib parsing. Pure functions: `parse_breadth_rows(envelope)`
(handles the double-decode quirk, filters AGENCY, extracts HY columns),
`merge_bond_types(rows)` (sums CORP + CORP_144A per date),
`validate_day(row)` (counts >= 0, adv+dec+unch <= total),
`build_series(rows, spx_by_date)` (ascending, net/cum/ma21/ma50),
`build_output(...)`, `persist_result(payload, rows)`.

- Daily run: fetch a **rolling last-10-calendar-day window** (self-healing across
  bond-market holidays and late finalization) with one request per bondType, merge,
  validate, upsert only changed/new dates, then rebuild the payload series from the
  full `hyad_history` table joined to `credit_spread_history.spx_close` (paginated
  reads, Hrana-bounded).
- **Empty-window guard**: a window returning zero CORP rows on a weekday run raises
  (retryable soft failure — do not latch ok, do not persist). Weekend/holiday runs
  where the newest date already exists in Turso are unchanged-day heartbeats
  ("source unchanged; refreshing snapshot only").
- Writes, in order, every cycle: `ensure_no_replica_for_writers()` →
  `upsert_hyad_rows(rows, recorded_at=scan_time)` (changed rows only) →
  `upsert_scan_snapshot("hy-ad", scan_time, payload)` →
  `record_service_health("hy-ad", "ok", finished_at=scan_time)` → atomic JSON
  fallback `data/hyad.json`. Turso is the source of truth.
- CLI: `--json` prints payload to stdout (stdout = payload only, progress to
  stderr), `--backfill` runs the 2018+ build then exits.

## Storage — `scripts/db/migrations/0056_hyad.sql`

```sql
CREATE TABLE IF NOT EXISTS hyad_history (
    date TEXT PRIMARY KEY,
    advances INTEGER NOT NULL,
    declines INTEGER NOT NULL,
    unchanged INTEGER NOT NULL,
    total INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hyad_history_date_desc ON hyad_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (56, datetime('now'));
```

`net`/`cum`/MAs are derived at payload build, never stored (storing a cumulative
would make rows order-dependent and break idempotent revisions). Writer
`upsert_hyad_rows(rows, recorded_at)` — idempotent `INSERT ... ON CONFLICT(date) DO
UPDATE`; arity + idempotency pinned in pytest against the migration in in-memory
sqlite.

## Payload (scan_snapshots service `hy-ad`)

```json
{
  "scan_time": "2026-08-25T11:05:00+00:00",
  "data_date": "2026-08-21",
  "current": {
    "date": "2026-08-21", "advances": 1227, "declines": 1504, "unchanged": 69,
    "total": 3163, "net": -277, "cum": -2535, "ma21": -1010.4, "ma50": 850.2
  },
  "series": [
    {"date": "2018-01-22", "net": -1315, "cum": -1315, "ma21": null, "ma50": null, "spx_close": 2832.97}
  ]
}
```

`series` ascending by date; `spx_close` null where `credit_spread_history` has no
row (pre-2007 never happens here; holidays mismatch is fine).

## API — `web/app/api/hyad/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only.
- `dbFirstRead`: `fromDb` = latest `scan_snapshots WHERE service = 'hy-ad'`,
  `fromDisk` = `data/hyad.json`.
- `MAX_AGE_MS = 120h`, commented: T+1 publication plus 3-day weekends and
  bond-market-only holidays; older than 120h means the writer is down.
- Missing contract: HTTP 200 + frozen
  `{ missing: true, scan_time: null, data_date: null, current: null, series: [] }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["hyad"] })`.
- Route classification: add `"hyad"` to `MIDDLEWARE_PERIMETER_ONLY_ROUTES` in
  `web/tests/route-local-authz-matrix.test.ts` (read-only market data) — same
  commit as the route.
- Hook `web/lib/useHyAd.ts`:
  `useSyncHook({ endpoint: "/api/hyad", interval: 3_600_000, hasPost: false, extractTimestamp: d => d.scan_time })`.

## UI — `web/components/HyAdPanel.tsx`, `web/app/regime/hyad/page.tsx`

- Lib `web/lib/hyad.ts` (UI worktree owns it): types, frozen `MISSING_HYAD`,
  `hyAdRegimeLabel(cum, ma21, ma50)` per the table above, formatters (thousands
  separators for counts, signed net).
- Gate order: `SpectralLoader` (`label="Loading high yield breadth series"`) →
  `SectionEmptyState` on `missing: true` → content.
- Strip: `HY AD CUM` (signed, thousands), `1D NET` (signed), `21D MA`, `50D MA`,
  `REGIME`, `SOURCE UPDATED` (`data_date`). Header clock renders `scan_time`.
- Chart: `CriHistoryChart`, two series — SPX overlay left axis
  (`chartSeriesColor("primary")`, `scaleType: "log"`) + HY A-D `cum` right axis.
  Title `HIGH YIELD BOND CUMULATIVE A-D LINE`, `xTickFormat` for the multi-year
  domain. `HistoryRangeChips` presets default `All` + `BrushMinimap`
  `testIdPrefix="hyad-brush"`.
- `InfoTooltip`: signal explanation, the level-is-arbitrary normalization note, the
  divergence reading, and "Source: FINRA Fixed Income Market Activity". No em
  dashes; no cadence claims except copy naming the real Tue..Sat morning timer.
- `RegimePanel.tsx` registration (four places + mobile chip array +
  `MOBILE_TAB_LABEL`) + `web/lib/regimeRail.ts` (union, group "Breadth & sentiment",
  label "HY AD") + `web/tests/regime-rail.test.tsx` count/list pins +
  `web/tests/regime-tab-routes.test.tsx` row.

## Timer — `cloud/services/radon-hyad.{service,timer}`

- Service: `Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`,
  `EnvironmentFile=/etc/radon/env` (canonical path — cloud tests enforce it),
  `Environment=RADON_DB_NO_REPLICA=1`,
  `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_hyad.py`,
  `TimeoutStartSec=300`.
- Timer: `OnCalendar=Tue..Sat 11:00 UTC` — T+1 morning after TRACE end-of-day
  finalization (FINRA publishes for days the system is open; comment this),
  `Persistent=true`, `RandomizedDelaySec=300`, `WantedBy=timers.target`.
- Register: `setup-vps.sh` `SERVICE_FILES` + `cloud/config/installed-units.sha256`
  (both hashes, same commit) + `cloud/tests/test_systemd_services.py`.
- `web/lib/serviceHealthWindows.ts` + `scripts/watchdog/services.py`: `hy-ad`,
  120h/120h scheduled windows (covers 3-day weekends + bond-only holidays),
  `requires_ib: false`, + daily-bucket check list.

## Tests (write first; red = missing implementation modules)

- Fixture: `scripts/tests/fixtures/hyad_sample.json` (36,736 bytes, raw transport
  envelope, 210 rows = 10 trading days 2026-08-10..2026-08-21 x 3 bondTypes x 7
  dataTypes). Known values to derive expectations from (HY = CORP+CORP_144A):
  2026-08-21 adv 1227 / dec 1504, 2026-08-18 adv 936 / dec 2292, mini-cum over the
  fixture window ends at -2535.
- `scripts/tests/test_hyad.py` — envelope double-decode + AGENCY filtering; merge of
  CORP+CORP_144A per date; validation (negative count raises, adv+dec+unch == total
  passes, > total raises); net/cum/ma computation (cum from fixture = -2535 on
  2026-08-21; MA windows null until enough points); empty-window guard raises;
  unchanged-day heartbeat path (no row upsert, snapshot + heartbeat written);
  migration 0056 pin (schema, version 56, idempotent upsert); writer arity;
  payload contract keys; window-relative freshness dates.
- `web/tests/hyad-api.test.ts` — in-memory libsql: Turso-beats-disk, disk fallback,
  exact frozen missing object at 200, no cross-service leak (a `credit-spread`
  snapshot must not serve), `route.dynamic === "force-dynamic"`.
- `web/tests/hyad-panel.test.tsx` — mock `@/lib/useHyAd`; loader label, empty state,
  strip testids (hyad-cum, hyad-net, hyad-ma21, hyad-ma50, hyad-regime,
  hyad-updated), regime boundaries (equalities → MIXED; cum>ma21>ma50 ADVANCING;
  cum<ma21<ma50 DETERIORATING; null MA → MIXED), chart title, chips default All,
  hyad-brush, NaN guard, no-em-dash/no-cadence copy discipline.
- `web/e2e/hyad-tab.spec.ts` — per pattern (UI worktree owns; run in Step 5).

## File checklist

Per `.claude/skills/new-indicator/SKILL.md` §0 with `<name>=hyad`, `<slug>=hyad`,
`<Name>=HyAd`, service `hy-ad`. Reference implementations: `fetch_margin_debt.py`
(FINRA client conventions + persist pattern), `fetch_divyield.py` (freshest tested
surface + Turso joins), `divyield/route.ts` sibling, `DivYieldPanel.tsx` sibling
(two-series chart: see `curve`/`credit` panels for the SPX-overlay variant).
Ownership split for the swarm mirrors DIVYIELD's: ingestion owns scripts/cloud
files; API owns route + serviceHealthWindows (+ its pin test) + authz-matrix line;
UI owns web/lib/hyad.ts, useHyAd.ts, panel, RegimePanel/regimeRail registration,
page, tab-routes test, e2e spec.
