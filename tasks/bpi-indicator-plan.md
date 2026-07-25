# Bullish Percent Index (BPI) indicator — NDX / SPX / RUT

Operator request (2026-07-25): replicate the StockCharts $BPNDX-style Bullish
Percent chart for Nasdaq-100, S&P 500, and Russell 2000, with the 30-line
oversold-bull read ("Savvy Oversold Bull").

## 1. Definition

BPI(index, day) = 100 × (members whose Point & Figure chart is on a BUY
signal) / (members with a resolved P&F signal). P&F: close-only method,
traditional box scaling, 3-box reversal. Buy signal = double-top breakout
(current X column exceeds the previous X column top); sell = double-bottom
breakdown. State persists until the opposite signal. Members with no signal
yet resolved are excluded from the denominator.

Deviation from StockCharts (documented, deliberate): close-only P&F rather
than high/low — we already have a durable daily-close store and the BPI
regime read (30/70 bands, cross events) is insensitive to the method delta.

## 2. Data

- **Constituents**: ETF holdings CSVs — QQQ (Invesco) → NDX, IVV (iShares) →
  SPX, IWM (iShares) → RUT. Plain-UA HTTP (never impersonate a browser).
  Cache to `data/constituents/{NDX,SPX,RUT}.json` (+committed seed lists in
  `scripts/data_seeds/constituents/` as last-resort fallback). Equity common
  shares only; drop cash/futures rows; dedupe share classes by ticker.
- **Closes**: Turso `price_history_daily` (shared with RV-ratio). Member
  fetches are **Yahoo-only** (v8 chart API, concurrency ≤ 8) — IB pacing and
  UW rate limits are wrong for ~2,600 daily-bar pulls; this is a sanctioned
  deviation from the IB→UW→Yahoo chain for BULK member closes. Backfill
  `--backfill` = 2y range; incremental = 1mo range, only for members whose
  stored max(date) < last completed session.

## 3. Engine — `scripts/utils/pnf.py` (pure, unit-tested)

- `traditional_box_ladder()` — StockCharts traditional scale: 0.0625 under
  $0.25, 0.125 to $1, 0.25 to $5, 0.5 to $20, 1 to $100, 2 to $200, 4 above.
- `signal_state_series(dates, closes)` → per-date `"buy" | "sell" | None`.
  Close-only column walk: X top = floor-rung(close), O bottom =
  ceil-rung(close); extension checked before reversal; 3-box reversal;
  breakout strictly exceeds the prior same-direction column extreme.

## 4. Scan — `scripts/bpi_scan.py`

Per index: resolve constituents → ensure member close history → per-member
signal series → aggregate BPI per session over trailing ~504 sessions →
persist. Progress to stderr, exactly one JSON doc on stdout (subprocess
contract). `--index NDX|SPX|RUT|all`, `--backfill`.

Persistence (migration `0031_bpi.sql`):

```sql
CREATE TABLE IF NOT EXISTS bpi_history (
  index_symbol TEXT NOT NULL, date TEXT NOT NULL,
  bpi REAL NOT NULL, members INTEGER NOT NULL, bullish INTEGER NOT NULL,
  PRIMARY KEY (index_symbol, date)
);
CREATE TABLE IF NOT EXISTS bpi_snapshots (
  index_symbol TEXT PRIMARY KEY, taken_at TEXT NOT NULL, payload TEXT NOT NULL
);
```

Writers in `scripts/db/writer.py` (chunked multi-row INSERTs per Turso Hrana
rules). Disk mirror `data/bpi.json` = `{generated_at, indices: {NDX, SPX,
RUT}}` via `atomic_save`. `service_health` heartbeat row `bpi-scan` every
run (ok or error; event-driven 24h+ window rules apply).

### Payload contract (schema_version 1) — one per index

```json
{
  "schema_version": 1,
  "index_symbol": "NDX",
  "index_name": "Nasdaq-100",
  "taken_at": "2026-07-25T21:30:00Z",
  "as_of_session": "2026-07-25",
  "bpi": 30.39, "members": 100, "bullish": 30,
  "state": "OVERSOLD",              // <=30 OVERSOLD, >=70 OVERBOUGHT, else NEUTRAL
  "cross_up_30": false,             // prev session < 30 and latest >= 30
  "thresholds": { "oversold": 30, "overbought": 70 },
  "history": [ { "date": "2024-07-26", "bpi": 55.0 }, ... ],  // trailing ≤504 sessions
  "sources": { "constituents": "invesco|ishares|cache|seed",
               "member_close_fetches": { "yahoo": 2100, "stored": 500 } }
}
```

Missing/insufficient data → `{"missing": true, "index_symbol": ...}`; never
cache/persist an empty payload (gate writes on ≥ 30 sessions of history and
≥ 80% member coverage on the latest session).

## 5. API + cadence

- FastAPI: `POST /bpi/scan` (600s cooldown per index, asyncio lock, RV-ratio
  pattern) → subprocess `bpi_scan.py`. Authenticated (not exempt).
- Timer: `radon-bpi.{service,timer}` in `cloud/services/`, Mon–Fri 21:30 UTC
  (after settle). Oneshot; StartLimitBurst per timer-oneshot memory rule.

## 6. Web (`/regime` tab "BULLISH %")

- `GET /api/bpi` — Next route, `dynamic="force-dynamic"`, Turso-first via
  `dbFirstRead` (bpi_snapshots payloads), fallback `data/bpi.json`; 200 +
  `missing:true` when absent (never 4xx for empty).
- `useBpi.ts` hook (`cache:"no-store"`).
- `BpiPanel.tsx` + `BpiChart.tsx` under the Regime section as a new tab,
  following the regime chart conventions: 16px pad, range presets +
  BrushMinimap, SpectralLoader, brand tokens via color-mix (no raw hex),
  4px max radius. Index switcher NDX/SPX/RUT. Bands at 30/70; dots where
  history crosses UP through 30 (the chart's signal); latest-value readout
  row (BPI, state, members bullish/total, as-of session).

## 7. Tests

- pytest: `scripts/tests/test_pnf.py` (ladder bands, buy/sell breakouts,
  3-box reversal, band-crossing prices), `scripts/tests/test_bpi_scan.py`
  (aggregation, denominator exclusion, payload gating, missing path) —
  window-relative dates only.
- vitest: route contract (Turso-first, no-cache, missing:true), hook, panel
  render pins (bands, switcher, cross-up dots, SpectralLoader).

## 8. Rollout

Push → CI → deploy; install+enable timer on VPS; run `--backfill` per index
on VPS (NDX → SPX → RUT); verify live at app.radon.run/regime (light+dark);
`bpi-scan` service_health row green; memory entry.
