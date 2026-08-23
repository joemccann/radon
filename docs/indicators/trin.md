# TRIN — NYSE Arms Index, 60-minute, MA(10) low zone

Spec for the `/indicator` swarm. Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementations: the BREADTH collector (`scripts/breadth_scan.py`: IB
generated-index snapshots, StockCharts quotebrain fallback, 5-minute RTH timer) for
ingestion; the CREDIT tab (`web/app/api/credit-spread/route.ts`,
`web/components/CreditSpreadPanel.tsx`) for the API/panel shape.

## Identity

| Key | Value |
|---|---|
| slug (route) | `trin` → `/regime/trin` |
| service (kebab) | `trin` (`scan_snapshots.service`, `service_health`, API dir `web/app/api/trin/`) |
| PascalCase | `Trin` (`TrinPanel`, `useTrin`, `web/lib/trin.ts`) |
| Tab label | `TRIN` |
| Migration | `0054_trin.sql` (version 54; 0053 is iei-hyg) |
| Timer | `radon-trin.{service,timer}`, `OnCalendar=Mon..Fri *-*-* 13..21:02,07,12,17,22,27,32,37,42,47,52,57 UTC` (offset 2 min from breadth's slots so the two IB snapshot jobs never collide), `Persistent=false` |
| JSON fallback | `data/trin.json` |
| Writer | `writer.upsert_trin_samples(rows, recorded_at)`, `writer.upsert_trin_daily_rows(rows, recorded_at)` |

## Signal

TRIN = (advancers / decliners) / (advancing volume / declining volume). Below 1.0
volume is concentrated in advancers. The operator reads the **60-minute TRIN with a
10-period moving average**: when MA(10) pushes down into the low zone (the
StockCharts chart's red line at 0.60; every prior touch was followed by weakness or
consolidation in SPX), that is the warning. The chart's upper reference line is 1.50.

Hourly bars: RTH only, `America/New_York`, seven buckets per session
(09:30-10:30, 10:30-11:30, ..., 14:30-15:30, 15:30-16:00). A bar's value is the
**last sample** inside the bucket; the forming bar counts (StockCharts includes the
live bar in its MA). `MA10 = mean(last 10 hourly bars)` across sessions; `null` until
10 bars exist.

| Condition (on `ma10`) | `state` |
|---|---|
| `ma10 <= ZONE_LOW` (`0.60`) | `in_zone` |
| `ZONE_LOW < ma10 <= ZONE_NEAR` (`0.65`) | `near_zone` |
| `ma10 >= ZONE_HIGH` (`1.50`) | `elevated` |
| otherwise, or `ma10 is None` | `neutral` |

Thresholds are module constants; boundaries are inclusive on the zone side
(`<=`), pinned by tests.

## Source (confirmed 2026-08-22)

No source serves hourly TRIN history (IB's generated NYSE indices return HMDS 162
for intraday bars, same as `AD-NYSE`; StockCharts quotebrain collapses every period
to `SQL_DAILY`; UW has no breadth endpoint). So the hourly series is **built by
sampling**, from deploy day forward:

1. **Interactive Brokers (sampler)** — every 5 minutes during RTH, snapshot
   `Index('TRIN-NYSE','NYSE')` (`reqMktData` snapshot=True, genericTickList `""`,
   bounded await). Generated indices may carry the value in `bid`/`ask` rather than
   `last` (`AD-NYSE` precedent): extraction order `last` → `close` → `(bid+ask)/2`,
   finite and `> 0` only. Also snapshot `AD-NYSE` and `VOL-NYSE` (bid=advancing,
   ask=declining / up-volume, down-volume) so TRIN can be recomputed as
   `(adv/dec)/(up_vol/down_vol)` for validation; store both. Skip the socket when
   `/health` `auth_state` is set and not `authenticated`. Client id `"auto"`
   (subprocess range 20-49 per `scripts/CLAUDE.md`).
2. **Unusual Whales** — nothing applicable.
3. **StockCharts quotebrain (daily, long history)** — `$TRIN` via the same
   `historyandquote/d` endpoint + UA breadth uses; keep `source == "SQL_DAILY"`
   rows. Daily closes feed the long-history context (the panel's daily line and
   the `daily_close` strip cell); they are NOT a substitute for the hourly MA.
   Outside RTH the job refreshes the daily series only and heartbeats.

Licensing: IB account feed + StockCharts public quotebrain JSON, both already used by
breadth. Nothing new.

## Fixture

`scripts/tests/fixtures/trin_sample.json` (StockCharts `$TRIN` daily, 37 sessions
2026-07-01 → 2026-08-21; last five closes `1.04, 0.96, 0.86, 0.77, 0.68`). Hourly
sampler tests use synthetic sample rows (timestamps in ET, window-relative).

## Ingestion — `scripts/fetch_trin.py`

Public functions the tests import:

- `snapshot_trin(ib) -> dict | None` (value extraction order above; None when unpriceable)
- `hourly_bucket(ts_utc) -> str | None` — bucket start `YYYY-MM-DDTHH:MM` in ET, `None` outside RTH
- `bucket_hourly(samples) -> list[{"ts", "trin"}]` — last sample per bucket, sorted
- `moving_average(values, n=10) -> float | None`
- `classify_state(ma10) -> state`
- `build_output(samples, daily, scan_time=None, source=...) -> payload`
- `parse_stockcharts_daily` — reuse breadth's by import
- `persist_result(payload, new_samples, new_daily_rows)` — refuses when both hourly and daily are empty; order: `ensure_no_replica_for_writers`, `upsert_trin_samples` (if any), `upsert_trin_daily_rows` (if any), `upsert_scan_snapshot("trin", ...)`, `record_service_health("trin", "ok", ...)`, atomic JSON
- `main(argv)` — `--json`

Payload:

```json
{
  "scan_time": "...Z", "source": "ib+stockcharts",
  "current": {
    "ts": "2026-08-21T19:55:00Z", "session_date": "2026-08-21",
    "trin": 0.68, "ma10": 0.61, "state": "near_zone",
    "adv": 1510, "dec": 1280, "up_vol": 1.9e9, "down_vol": 1.4e9,
    "daily_close": 0.68, "daily_date": "2026-08-21",
    "zone_low": 0.60, "zone_near": 0.65, "zone_high": 1.50
  },
  "hourly": [{"ts": "...", "trin": 0.0, "ma10": null}],
  "daily": [{"date": "2026-07-01", "close": 1.04}]
}
```

`hourly` carries every hourly bar stored (grows from deploy day; cap the payload at
the last 400 bars ≈ 57 sessions); `daily` carries the StockCharts history.

## Storage — `scripts/db/migrations/0054_trin.sql`

```sql
CREATE TABLE IF NOT EXISTS trin_samples (
  ts           TEXT PRIMARY KEY,      -- UTC ISO, 5-minute sample
  session_date TEXT NOT NULL,         -- ET session date
  trin         REAL NOT NULL,
  adv          INTEGER,
  dec          INTEGER,
  up_vol       REAL,
  down_vol     REAL,
  source       TEXT NOT NULL,
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trin_samples_ts_desc ON trin_samples (ts DESC);
CREATE TABLE IF NOT EXISTS trin_daily (
  date        TEXT PRIMARY KEY,
  close       REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trin_daily_date_desc ON trin_daily (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (54, datetime('now'));
```

Hourly bars are derived at read time from `trin_samples` (not stored). Retention:
samples older than 120 sessions may be pruned by a later retention entry; not in scope.

## API — `web/app/api/trin/route.ts`

- GET only, `force-dynamic`, `nodejs`; `dbFirstRead` service `trin`, disk `data/trin.json`.
- `MAX_AGE_MS = 30 min` (5-minute RTH sampler; off-hours the last snapshot is the
  close and the panel shows its timestamp).
- Missing contract: 200 + frozen `{ missing: true, scan_time: null, source: null, current: null, hourly: [], daily: [] }`.
- Cache headers 60 / 300, tags `["trin"]`.
- `web/lib/trin.ts`: types, `MISSING_TRIN`, `formatTrin(n)` (2 dp), `stateLabel`
  (`IN ZONE` / `NEAR ZONE` / `ELEVATED` / `NEUTRAL`), `stateTone` (in_zone/near_zone
  = `negative` — weakness warning; elevated = `positive`; neutral = `muted`),
  `ZONE_LOW/NEAR/HIGH` constants mirrored from the payload.
- `web/lib/useTrin.ts`: `useSyncHook` 5-minute poll, `hasPost: false`.
- Service-health window: `trin` open 15 min / extended 24h / closed 24h,
  `category: "scheduled"`, `requires_ib: true` (+ exhaustive-set test, watchdog mirror).

## UI — `web/components/TrinPanel.tsx`, `web/app/regime/trin/page.tsx`

- Loader `Loading TRIN series`; empty state `No TRIN samples yet`.
- Strip: `TRIN` (latest), `MA10`, `STATE` chip, `DAILY CLOSE` (+ date), `ADV/DEC`, `UP/DOWN VOL`.
- Chart: `CriHistoryChart` titled `TRIN 60 MIN`, hourly TRIN + MA10 (both right
  axis; MA10 `chartSeriesColor("primary")`), horizontal reference lines at 0.60 and
  1.50 using brand tokens, `HistoryRangeChips` + `BrushMinimap testIdPrefix="trin-brush"`;
  x = hourly timestamps (`xTickFormat` session-aware). A second small daily line is
  optional; if omitted, the daily close lives only in the strip.
- `InfoTooltip`: "Arms Index on 60-minute bars with a 10-period average. When the
  average drops into the low zone (0.60) the market has often weakened or
  consolidated soon after. Above 1.50 is the opposite extreme." No em dashes; no
  cadence copy.
- Register `trin` in `RegimePanel.tsx` (all places) and `regime-tab-routes.test.tsx`.

## Scheduling

`cloud/services/radon-trin.service` (oneshot, venv python, `TimeoutStartSec=240`)
+ `.timer` (above). Register in `setup-vps.sh` `SERVICE_FILES`,
`cloud/tests/test_systemd_services.py`, and `cloud/config/installed-units.sha256`
(the deploy's `install-units` verb installs + enables them).

Caveat for the operator: the hourly MA needs ten bars, i.e. about a session and a
half of sampling after the first deploy, before `state` stops reading `neutral`.
