# IEI/HYG — Treasuries vs high yield, 52-week extremes

Spec for the `/indicator` swarm. Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementation to copy throughout: the sibling CREDIT indicator
(`scripts/fetch_credit_spread.py`, `web/app/api/credit-spread/route.ts`,
`web/lib/creditSpread.ts`, `web/lib/useCreditSpread.ts`,
`web/components/CreditSpreadPanel.tsx`, `web/tests/credit-spread-*.test.*`,
`web/e2e/credit-spread-tab.spec.ts`, `scripts/tests/test_credit_spread.py`,
`cloud/services/radon-credit-spread.*`). Same IB → UW → Yahoo daily-close cascade.

## Identity

| Key | Value |
|---|---|
| slug (route) | `iei-hyg` → `/regime/iei-hyg` |
| service (kebab) | `iei-hyg` (`scan_snapshots.service`, `service_health`, unit names, API dir `web/app/api/iei-hyg/`) |
| PascalCase | `IeiHyg` (`IeiHygPanel`, `useIeiHyg`, `web/lib/ieiHyg.ts`) |
| Tab label | `IEI/HYG` |
| Migration | `0053_iei_hyg.sql` (version 53; Turso `MAX(version)` = 52 on 2026-08-22) |
| Timer | `radon-iei-hyg.{service,timer}`, `OnCalendar=*-*-* 21:55:00 UTC` daily, `Persistent=true`, `RandomizedDelaySec=300` (free slot after credit 21:45 / skew2d 21:50) |
| JSON fallback | `data/iei_hyg.json` |
| Writer | `writer.upsert_iei_hyg_rows(rows, recorded_at=...)` |

## Signal

The price ratio **IEI / HYG** (iShares 3-7y Treasury ETF over iShares HY corporate
ETF) is a credit-spread direction proxy: the ratio FALLS when high yield
outperforms Treasuries (spreads tightening, risk-on) and RISES when credit is
sold (risk-off). The TrendLabs chart (2026-08-21) marks **new 52-week lows** in
the ratio; the US Dollar Index is overlaid and is also breaking down.

Per session, with the window = the last `min(252, n)` aligned sessions INCLUDING
the latest (`WINDOW_SESSIONS = 252`):

| Condition | `state` | Reading |
|---|---|---|
| `ratio == min(window)` | `new_low` | HY outperforming Treasuries; spreads tightening; risk-on confirmation |
| `ratio == max(window)` | `new_high` | Treasuries outperforming HY; spreads widening; risk-off |
| otherwise | `neutral` | |

Equality, not `<=`: the latest session is inside its own window, so "new low"
means it IS the window minimum. If `min == max` (degenerate), state is `neutral`.

`ratio_pct_rank = (ratio - low) / (high - low)` in `[0, 1]`; `0.0` when `high == low`.

DXY is an **overlay series only** (`dxy_close`, nullable per date — ICE prints on
US holidays and has gaps); it never drives `state`.

## Source (confirmed 2026-08-22)

Priority, never skip ahead (repo rule: IB → UW → Yahoo):

1. **Interactive Brokers** — `Stock('IEI','SMART','USD')`, `Stock('HYG','SMART','USD')`,
   DXY as `Index('DX','NYBOT','USD')` (IB lists the ICE spot index as symbol `DX`,
   exchange `NYBOT`; try `Index('DXY','NYBOT','USD')` second; never substitute UUP).
   1Y daily `TRADES`, `useRTH=True`, merged over the cache. Skip the socket when
   `/health` `auth_state` is set and not `authenticated`. Client IDs: reuse the
   credit pair `(56, 69)` — the two jobs run 10 minutes apart and never overlap;
   register the reuse in `scripts/CLAUDE.md` client-ID table.
2. **Unusual Whales** — `get_stock_ohlc("IEI"|"HYG", "1d")`. UW `1d` returns up to
   three rows per date (`market_time` ∈ `pr`/`r`/`po`): **keep only
   `market_time == "r"`** (regular session) — credit's dict comprehension lets the
   post-market row win; this indicator must not. DXY is not on UW (`UW_SKIP`).
3. **Yahoo** — last resort: `IEI`, `HYG`, `DX-Y.NYB` via the chart JSON credit uses
   (UA `Mozilla/5.0`, `period1` = 2007-04-11 epoch for the first backfill).
   Reuse `fetch_credit_spread.fetch_yahoo_chart` / `parse_yahoo_chart` (null closes
   skipped).

Alignment: **inner join on IEI ∩ HYG dates**; `dxy_close` left-joined (null when
absent). History floor 2007-04-11 (HYG inception); 252-session extremes are valid
from ~2008-04.

Licensing: exchange-traded ETF prices + the ICE spot index, same class as the SPX
index credit already stores. Nothing new.

## Fixtures (captured 2026-08-22, Yahoo, 90-day window)

`scripts/tests/fixtures/iei_hyg_{iei,hyg,dxy}_sample.json`. Derived by running the
parser over them (never mental arithmetic):

| Fact | Value |
|---|---|
| aligned sessions (IEI ∩ HYG) | 62, `2026-05-26` → `2026-08-21` |
| DXY dates intersecting | 62 (DXY has 74 stamps, 12 null closes) |
| last `iei_close` / `hyg_close` | `116.41000366210938` / `79.61000061035156` |
| last `ratio` | `1.462253520532856` (= window min → `state == "new_low"`) |
| window max | `1.475760927676247` on `2026-06-26` |
| window min date | `2026-08-21` |
| `ratio_pct_rank` | `0.0` |
| previous session ratio | `1.464932210008201` |
| last `dxy_close` | `98.80000305175781` |

## Ingestion — `scripts/fetch_iei_hyg.py`

Public functions the tests import (composed-method style, pure where possible):

- `fetch_yahoo_closes(tickers, session=None) -> dict[str, dict[date, close]]` (Yahoo symbols map `{"IEI": "IEI", "HYG": "HYG", "DXY": "DX-Y.NYB"}`)
- `uw_regular_closes(rows) -> dict[date, close]` — filters `market_time == "r"`
- `fetch_closes(...)` cascade as credit, with `source` string (`"ib"`, `"ib+yahoo"`, ...)
- `align_series(iei, hyg, dxy) -> list[row]`, row = `{"date", "iei_close", "hyg_close", "dxy_close" (float|None), "ratio"}`
- `extremes_window(series, n=WINDOW_SESSIONS) -> list[row]`
- `classify_state(ratio, low, high) -> "new_low"|"new_high"|"neutral"`
- `pct_rank(ratio, low, high) -> float`
- `build_output(series, scan_time=None, source="ib") -> payload`
- `merge_series(cached, fresh)`, `diff_new_rows(cached, series)` (compare `(date, iei_close, hyg_close, dxy_close)`)
- `persist_result(payload, changed_rows)` — refuses an empty series; then in order
  `writer.ensure_no_replica_for_writers()`, `upsert_iei_hyg_rows` (only if changed),
  `upsert_scan_snapshot("iei-hyg", scan_time, payload)`,
  `record_service_health("iei-hyg", "ok", finished_at=scan_time)`, atomic JSON cache.
- `main(argv)` — `--json` prints the payload to stdout; progress to stderr.

Payload:

```json
{
  "scan_time": "2026-08-22T21:55:03Z",
  "source": "ib",
  "count": 62,
  "current": {
    "date": "2026-08-21",
    "iei_close": 116.41, "hyg_close": 79.61, "dxy_close": 98.8,
    "ratio": 1.46225,
    "ratio_52w_low": 1.46225, "low_date": "2026-08-21",
    "ratio_52w_high": 1.47576, "high_date": "2026-06-26",
    "ratio_pct_rank": 0.0,
    "window_sessions": 62,
    "state": "new_low"
  },
  "series": [{"date": "...", "iei_close": 0, "hyg_close": 0, "dxy_close": null, "ratio": 0}]
}
```

## Storage — `scripts/db/migrations/0053_iei_hyg.sql`

```sql
CREATE TABLE IF NOT EXISTS iei_hyg_history (
  date        TEXT PRIMARY KEY,
  iei_close   REAL NOT NULL,
  hyg_close   REAL NOT NULL,
  dxy_close   REAL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iei_hyg_history_date_desc ON iei_hyg_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (53, datetime('now'));
```

`writer.upsert_iei_hyg_rows(rows, recorded_at)`: chunked multi-row
`INSERT ... ON CONFLICT(date) DO UPDATE` (Hrana bounding rule), idempotent.
`ratio` is derived, not stored.

## API — `web/app/api/iei-hyg/route.ts`

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`, GET only.
- `dbFirstRead`: `fromDb` = latest `scan_snapshots WHERE service = 'iei-hyg'`;
  `fromDisk` = `data/iei_hyg.json`; fresher `scan_time` wins.
- `MAX_AGE_MS = 48h` — daily 21:55 UTC timer plus weekend slack; older means the
  writer is down.
- Missing contract: HTTP 200 + frozen
  `{ missing: true, scan_time: null, source: null, count: 0, current: null, series: [] }`.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600, tags: ["iei-hyg"] })`.
- `web/lib/ieiHyg.ts`: types (`IeiHygPayload`, `IeiHygRow`, `IeiHygCurrent`, `IeiHygState`),
  `MISSING_IEI_HYG`, `formatRatio(n)` (4 dp), `stateLabel(state)` → `NEW 52W LOW` / `NEW 52W HIGH` / `NEUTRAL`,
  `stateTone(state)` → `"positive" | "negative" | "muted"` (new_low = positive: risk-on).
- `web/lib/useIeiHyg.ts`: `useSyncHook({ endpoint: "/api/iei-hyg", interval: 60 * 60 * 1000, hasPost: false, extractTimestamp: d => d.scan_time })`.
- Service-health window: `web/lib/serviceHealthWindows.ts` `"iei-hyg": { open: 26h, extended: 26h, closed: 26h, category: "scheduled", requires_ib: false }`
  + add to the exhaustive `expected` set in `web/tests/service-health-windows.test.ts`;
  `scripts/watchdog/services.py` same 26h window + daily-bucket list.

## UI — `web/components/IeiHygPanel.tsx`, `web/app/regime/iei-hyg/page.tsx`

- Gate order: `SpectralLoader label="Loading IEI/HYG series"` → `SectionEmptyState`
  on `missing` (headline `No IEI/HYG snapshot`, secondary `Waiting for the iei-hyg refresh timer`) → content.
- Strip cells (`RegimeStrip` desktop / `MetricCell` mobile): `RATIO`, `52W LOW` (+ date),
  `52W HIGH` (+ date), `RANK` (pct, 0-100%), `DXY`, `STATE` chip (tone per `stateTone`).
- Chart: `CriHistoryChart`, title `IEI / HYG RATIO`, ratio on the **right** axis
  (`chartSeriesColor("primary")`), DXY on the **left** axis (secondary colour), x =
  dates (`xTickFormat`), `HistoryRangeChips` + `BrushMinimap testIdPrefix="iei-hyg-brush"`,
  default preset per `defaultPresetForLength`. Null `dxy_close` gaps the line (no NaN paths).
- Header: `lastSync` = payload `scan_time`; a `SOURCE` cell shows `payload.source`
  and `current.date`. **No cadence copy** ("refreshes daily" etc.) anywhere.
- `InfoTooltip` text: "IEI over HYG. A new 52-week low means high yield is
  outperforming Treasuries: spreads tightening, risk-on. A new 52-week high is
  the opposite. DXY is an overlay only." No em dashes.
- Brand tokens only; 4px radius.
- Register in `RegimePanel.tsx` (union, `REGIME_TAB_VALUES`, `tabFromPathname` regex,
  desktop button row + mobile chip array, dispatch branch) and add
  `["iei-hyg", "app/regime/iei-hyg/page.tsx"]` + a render case to
  `web/tests/regime-tab-routes.test.tsx`.

## Tests

- `scripts/tests/test_iei_hyg.py` (written first, red): fixture parse + alignment
  facts above; `classify_state` boundaries (equality is the edge; degenerate
  min==max → neutral); `pct_rank`; `build_output` current fields; migration executed
  into in-memory sqlite pins columns + version 53 + idempotent upsert; `persist_result`
  ordering with a monkeypatched writer and the empty-series refusal;
  `uw_regular_closes` filter; `--json` CLI.
- `web/tests/iei-hyg-api.test.ts`, `web/tests/iei-hyg-panel.test.tsx`,
  `web/e2e/iei-hyg-tab.spec.ts` per the pattern skill (mock the hook in the panel test).

## Scheduling

`cloud/services/radon-iei-hyg.service` (oneshot, venv python directly,
`TimeoutStartSec=600`) + `.timer` (above). Register in `setup-vps.sh` `SERVICE_FILES`,
`cloud/tests/test_systemd_services.py`, and add both hashes to
`cloud/config/installed-units.sha256` in the same commit — since PR #73 the deploy's
`install-units` verb installs and enables them; no root SSH.
