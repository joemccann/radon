# CREDIT/VIX — SHY minus HYG credit proxy vs VIX, 252-session range gap

Spec for the `/indicator` swarm. Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Primary reference: IEI/HYG (`docs/indicators/iei-hyg.md`). Source idea:
https://x.com/KGBULLANDBEAR/status/2104940060508586254 (2026-09-29, Kevin Green).

## Identity

| Key | Value |
|---|---|
| slug (route) | `credit-vix` → `/regime/credit-vix` |
| service (kebab) | `credit-vix` |
| PascalCase | `CreditVix` |
| Tab label | `CREDIT/VIX` (pinned) |
| Panel title | `SHY MINUS HYG VS VIX` |
| Migration | `0093_credit_vix.sql` (version 93; #808 holds 0092) |
| Timer | `radon-credit-vix.{service,timer}`, `OnCalendar=*-*-* 22:25:00 UTC` daily, `Persistent=true`, `RandomizedDelaySec=300` (free slot after dispersion 22:20 / before yield-curve 22:30) |
| JSON fallback | `data/credit_vix.json` |
| Writer | `writer.upsert_credit_vix_rows(rows, recorded_at=...)` |

## Signal

`spread = close(SHY) - close(HYG)` in USD, regular-session daily closes,
split-adjusted but **not dividend-adjusted** (thinkorswim default). `vix` is
the VIX index daily close. Inner join on session dates. Window = the last
`min(252, n)` aligned sessions including the latest.

**A1 normalization (most faithful):** each leg is range-positioned in its own
trailing 252-session window, matching thinkorswim independent autoscale.

| Field | Formula |
|---|---|
| `rank_spread` | `pct_rank(spread, min252, max252)` — `0.0` when high == low |
| `rank_vix` | same |
| `gap` | `rank_spread - rank_vix`, in `[-1, +1]` |

**A1 alternative (PR / validation only, not shipped):** `z_gap = z252(spread) - z252(vix)` (population std).

**A3 state:** `GAP_THRESHOLD = 0.5`. `CREDIT WIDE` if `gap >= 0.5`, `VIX WIDE`
if `gap <= -0.5`, else `ALIGNED`. Both boundaries pinned.

`widest_since`: most recent prior session with `gap >=` latest gap; null if none.

**A2 dividend sawtooth:** HYG (~$0.37-0.44/month) and SHY (~$0.24/month) go ex
around the first business day of each month, so the unadjusted spread jumps
about +0.15 on ex-dates. Kept unadjusted to match the source. No adjusted
variant in this PR.

**A4 copy:** "SHY minus HYG price gap, a credit proxy". Not an OAS. Never claim
basis points. No ICE OAS. No forward-return or "front-run" claim in UI copy.
No em dashes.

Store raw inputs only (`date`, `shy_close`, `hyg_close`, `vix_close`). Compute
ranks, gap, and state in the job and put them in the payload.

## Source

- **S1 SHY + HYG:** `fetch_iei_hyg.fetch_closes(["SHY","HYG"])` imported, not
  copied. IB `Stock` → Robinhood → UW `r`-session → Yahoo. SHY is a new symbol
  through the existing ladder, not a new vendor. Per-ticker `sources` map kept.
  IB client IDs 56/69 reused; unit takes `flock /run/lock/radon-ib-history-5669.lock`.
  History before IB's 1Y comes from the Yahoo `period1` backfill, as iei-hyg does.
  Not reading HYG from `iei_hyg_history` (cross-job dependency).
- **V1 VIX:** dedicated ladder. IB `Index('VIX','CBOE')` → Cboe CDN
  `VIX_History.csv` via `CboeClient` + `parse_index_csv(text, "CLOSE")` → Yahoo
  `^VIX`. **Never** pass VIX through `fetch_iei_hyg.fetch_closes` (that would
  qualify `Stock('VIX')`).

`--no-db` skips all Turso I/O (ma-ratio / bpi precedent). Validation runs
`--no-db --json`.

Licensing: ETF prices + Cboe VIX, same class as vixts / iei-hyg. Not ICE OAS.

## Ingestion — `scripts/fetch_credit_vix.py`

Public functions: `align_series`, `extremes_window`, `pct_rank`,
`classify_state`, `session_metrics`, `widest_since`, `build_output`,
`merge_series`, `diff_new_rows`, `persist_result`, `fetch_vix_closes`,
`fetch_equity_closes` (wrapper around `fetch_iei_hyg.fetch_closes`).

Payload:

```json
{
  "scan_time": "2026-09-30T22:25:03Z",
  "source": "ib+cboe",
  "source_by_ticker": {"SHY": "ib", "HYG": "ib", "VIX": "cboe"},
  "count": 252,
  "current": {
    "date": "2026-09-29",
    "shy_close": 81.16, "hyg_close": 77.36, "vix_close": 16.04,
    "spread": 3.80,
    "rank_spread": 1.0, "rank_vix": 0.146,
    "gap": 0.854,
    "state": "CREDIT WIDE",
    "widest_since": "2015-08-17",
    "window_sessions": 252
  },
  "series": [{"date": "...", "shy_close": 0, "hyg_close": 0, "vix_close": 0, "spread": 0}]
}
```

All sources down: `_serve_cached` as `status: "stale_source"` with an `error`
heartbeat. `--json` prints payload to stdout; progress to stderr.

## Storage — `scripts/db/migrations/0093_credit_vix.sql`

```sql
CREATE TABLE IF NOT EXISTS credit_vix_history (
  date        TEXT PRIMARY KEY,
  shy_close   REAL NOT NULL,
  hyg_close   REAL NOT NULL,
  vix_close   REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_vix_history_date_desc ON credit_vix_history (date DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (93, datetime('now'));
```

Retention: `KeepLatestPolicy("credit_vix_history", "date", 3000)` (>= 252).
Fetcher rehydrates from its own table (R-123).

## API / UI

- `web/app/api/credit-vix/route.ts`: dbFirstRead, missing contract 200,
  `MAX_AGE_MS` 48h, tags `["credit-vix"]`.
- Panel strip: SPREAD, VIX, GAP, STATE, WIDEST SINCE.
- Chart: `CriHistoryChart` title `SHY MINUS HYG VS VIX`, spread on the right
  axis, VIX on the left. `FreshnessRail` with `CREDIT_VIX_REFRESH` (22:25 UTC).
- InfoTooltip documents the credit-proxy wording and the dividend sawtooth.
  No front-run claim.

## Scheduling

`cloud/services/radon-credit-vix.service` (oneshot, venv python, flock 56/69,
`SuccessExitStatus=75`, `TimeoutStartSec=960`) + `.timer` (22:25 UTC).
Register in `setup-vps.sh`, `test_systemd_services.py`, and
`installed-units.sha256`.
