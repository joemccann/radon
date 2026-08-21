# CREDIT — HYG vs S&P 500 credit-equity divergence

Spec for the `/indicator` swarm. Pattern authority: `.claude/skills/new-indicator/SKILL.md`.
Reference implementation to copy throughout: yield-curve (`scripts/fetch_yield_curve.py`,
`web/app/api/yield-curve/route.ts`, `web/components/YieldCurvePanel.tsx`).

## Identity

| Key | Value |
|---|---|
| slug (route) | `credit` → `/regime/credit` |
| service (kebab) | `credit-spread` (scan_snapshots.service, service_health, unit names) |
| PascalCase | `CreditSpread` (`CreditSpreadPanel`, `useCreditSpread`) |
| Tab label | `CREDIT` |
| Migration | `0051_credit_spread.sql` (version 51) |
| Timer | `radon-credit-spread.{service,timer}`, `OnCalendar=*-*-* 21:45:00 UTC` daily (incl. weekends; unchanged days heartbeat only) |

## Signal

High-yield credit (HYG daily close) versus the S&P 500. The two usually rise together
(risk-on). **Divergence** is the tweet's warning: equities up over 168 sessions while
HYG is down. That is the EWI CCC-vs-SPX chart, rebuilt from public market prices.

ICE BofA CCC OAS (`BAMLH0A3HYC` on FRED) is **not stored and not displayed**. ICE
copyright forbids reproduction without written permission (FRED series notes + FRED
ToS § copyrighted / pre-approval). FRED also cut that series to a rolling 3-year
window in April 2026, so it cannot reconstruct the 2022 analog. HYG (inception 2007)
can. VCG already treats HYG as the credit proxy.

Regime (strict inequalities; a zero return is **not** divergent):

| `spx_ret` | `hyg_ret` | `regime` |
|---|---|---|
| `> 0` | `< 0` | `divergent` |
| `> 0` | `> 0` | `coupled` |
| `< 0` | `< 0` | `risk-off` |
| `< 0` | `> 0` | `credit-lead` |
| either `== 0` | | `coupled` |

`LOOKBACK_SESSIONS = 168` (8 × 21 trading days). Return is
`last / window[0] - 1` on the aligned common-date series.

`NEAR_HIGH_RATIO = 0.97`. `near_high` is true iff `spx_last >= 0.97 * max(SPX in window)`.
Pin: fixture last/max ≈ 0.97976 → true at 0.97, false at 0.98.

## Source (confirmed 2026-08-21)

- Tweet: https://x.com/elliottwaveintl/status/2090449826399862935 (2026-08-20).
  CCC OAS inverted vs SPX weekly; 8-month divergence from Jan 2026. Bloomberg
  series, not FRED. Do not claim the tab is ICE CCC OAS.
- Yahoo chart JSON, no API key, UA `Mozilla/5.0` (plain `radon/2.0` gets 429, same
  as the yield-curve SPX overlay):
  `https://query1.finance.yahoo.com/v8/finance/chart/{HYG|%5EGSPC}?period1={epoch2007-04-11}&period2={now}&interval=1d`
- Paths: `chart.result[0].timestamp[]`, `chart.result[0].indicators.quote[0].close[]`.
  Skip null closes. Date = UTC `YYYY-MM-DD` from the unix timestamp.
  Never use `range=max` (silently degrades to coarse bars).
- No `Last-Modified` / `ETag` on the chart endpoint (live GET 2026-08-21). Diff on
  persisted `{date, hyg_close, spx_close}` tuples; unchanged days heartbeat only.
- History: HYG from 2007-04-11; backfill uses the same URL with `period1` at that
  epoch. Daily runs fetch the same window and merge over cache (Yahoo is cheap).
- Why not IB/UW: IB ticks HYG/SPX live but historical daily closes for an unattended
  timer require an authenticated gateway (2FA lock fails the job). UW has no credit
  OAS or HYG history endpoints. Yahoo is the scheduled source, not a skipped-to
  fallback after a live IB attempt.
- Licensing: HYG and SPX are exchange-traded prices. Display + storage allowed.
- Fixtures (checked in, captured 2026-08-21):
  - `scripts/tests/fixtures/credit_spread_hyg_sample.json` — Yahoo chart, 2024-01-02
    → 2026-08-20. Pins: 2024-01-02 close `77.12999725341797`; 2026-08-20 close
    `79.55999755859375`. Raw 661 stamps, 2 null closes.
  - `scripts/tests/fixtures/credit_spread_spx_sample.json` — same window. Pins:
    2024-01-02 close `4742.830078125`; 2026-08-20 close `7641.16015625`. Raw 661
    stamps, 3 null closes.
  - Aligned common dates: 658 rows, 2024-01-02 → 2026-08-20.
  - 168-session window starts 2025-12-15 (HYG `80.61000061035156`, SPX
    `6816.509765625`). `hyg_ret ≈ -0.013025716955806343`,
    `spx_ret ≈ 0.12097839201868865`, regime `divergent`.

## Ingestion — `scripts/fetch_credit_spread.py`

Small pure functions (composed-method), stdlib `json` parsing, `requests` fetch:

- `LOOKBACK_SESSIONS = 168`, `NEAR_HIGH_RATIO = 0.97`
- `HYG_SYMBOL = "HYG"`, `SPX_SYMBOL = "^GSPC"` (URL-encode as `%5EGSPC`)
- `fetch_yahoo_chart(symbol, session=None) -> str` — UA `Mozilla/5.0`, timeout 30s,
  raise on non-200/empty.
- `parse_yahoo_chart(text) -> dict[str, float]` — date → close; skip nulls.
- `align_series(hyg: dict, spx: dict) -> list[dict]` — inner join on date, ascending
  `{date, hyg_close, spx_close}`.
- `lookback_window(series, n=168) -> list[dict]` — last `n` rows (or all if shorter).
- `lookback_return(window, key) -> float | None` — `window[-1][key] / window[0][key] - 1`
  when both finite and `window[0][key] != 0`; else None. Needs `len(window) >= 2`.
- `classify_regime(spx_ret, hyg_ret) -> str` — table above; None ret → `coupled`.
- `is_near_high(last, high, ratio=0.97) -> bool` — finite and `last >= ratio * high`.
- `build_series(aligned) -> list[dict]` — the aligned rows (no per-row regime).
- `merge_series(cached, fresh)` — fresh wins per date.
- `diff_new_rows(cached, series)` — rows absent from or different in
  `(date, hyg_close, spx_close)`.
- `build_output(series, scan_time=None) -> payload`:
  ```
  {
    scan_time,                    # tz-aware UTC ISO, Z suffix
    source: "yahoo",
    count,
    current: {
      date, hyg_close, spx_close,
      hyg_ret, spx_ret,           # 168-session returns or null
      regime,                     # divergent | coupled | risk-off | credit-lead
      near_high                   # bool
    } | null,
    series: [{date, hyg_close, spx_close}, ...]
  }
  ```
- `persist_result(payload, rows_changed_rows)` — refuses empty `series`; else
  `writer.ensure_no_replica_for_writers()`;
  `writer.upsert_credit_spread_rows(new_rows, recorded_at=scan_time)` only when
  changed; `writer.upsert_scan_snapshot("credit-spread", ...)` EVERY cycle;
  `writer.record_service_health("credit-spread", "ok", finished_at=scan_time)` EVERY
  cycle; atomic JSON to `data/credit_spread.json`.
- Unchanged day prints `[credit-spread] source unchanged; refreshing snapshot only`.
- CLI: `--json` → payload to stdout (stderr for progress).

## Storage — migration `scripts/db/migrations/0051_credit_spread.sql`

```sql
CREATE TABLE IF NOT EXISTS credit_spread_history (
  date        TEXT PRIMARY KEY,   -- YYYY-MM-DD session
  hyg_close   REAL NOT NULL,
  spx_close   REAL NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credit_spread_history_date
  ON credit_spread_history(date DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (51, datetime('now'));
```

Writer (`scripts/db/writer.py`): `CREDIT_SPREAD_UPSERT_SQL` = single-row form for the
schema pin test; `upsert_credit_spread_rows(rows, recorded_at)` chunks like
`upsert_yield_curve_rows` (`date, hyg_close, spx_close, recorded_at`).

Also add `"credit-spread"` to
`scripts/tests/test_service_registration_completeness.py`
`test_direct_health_collector_sees_hand_rolled_writers` expected set.

## API — `web/app/api/credit-spread/route.ts` (GET only)

Mirror yield-curve:

- `dynamic = "force-dynamic"`, `runtime = "nodejs"`.
- `dbFirstRead` — fromDb: latest `scan_snapshots WHERE service = 'credit-spread'`;
  fromDisk: `../data/credit_spread.json`; `MAX_AGE_MS = 48h` (daily timer; older
  means the writer is down); label `"credit-spread"`.
- `MISSING_CREDIT_SPREAD = { missing: true, scan_time: null, count: 0, series: [], current: null }`
  at HTTP 200, never 4xx.
- `setCacheResponseHeaders(..., { maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600,
  requestId, cacheState: "HIT", tags: ["credit-spread"] })`.

Hook `web/lib/useCreditSpread.ts`: `useSyncHook`, endpoint `/api/credit-spread`,
`interval: 60 * 60_000`, `hasPost: false`, `extractTimestamp: d => d.scan_time || null`.

Helpers `web/lib/creditSpread.ts` (pure, unit-tested):

- Types `CreditSpreadPoint`, `CreditSpreadCurrent`, `CreditSpreadData` (incl. `missing?`).
- `LOOKBACK_SESSIONS = 168`, `NEAR_HIGH_RATIO = 0.97`.
- `formatPct(v)` → `+12.10%` / `-1.30%` / `---` (2 decimals, explicit sign).
- `formatSessionDate("2026-08-20")` → `20 Aug 2026` (UTC, en-GB).
- `formatDateTick` → `Aug 2026` (same MONTH_LABELS as yieldCurve).
- `classifyRegime(spxRet, hygRet)` / `regimeColor(regime)`:
  `divergent` → `var(--warning)`; `coupled` → `var(--positive)`;
  `risk-off` → `var(--negative)`; `credit-lead` → `var(--text-muted)`;
  unknown/null → `var(--text-muted)`.
- `isNearHigh(last, high)` pins 0.97 vs 0.98 on the fixture ratio.

## UI — `web/components/CreditSpreadPanel.tsx` + registration

- Gates: `SpectralLoader label="Loading high-yield credit series"` while
  `(loading || syncing) && !data`; `SectionEmptyState` headline
  `"No credit series yet"`, secondary
  `"The credit-spread refresh timer populates this tab from Yahoo Finance daily closes for HYG and the S&P 500."`
- Section title `Credit`; header clock renders `lastSync`.
- InfoTooltip: `"HYG is the traded high-yield credit proxy. ICE CCC option-adjusted spreads are not stored. Equities and high-yield credit usually rise together. Divergence means the S&P 500 is up over 168 sessions while HYG is down."`
- Desktop `RegimeStrip` cells:
  1. `REGIME` — uppercased `current.regime`, colored by `regimeColor`,
     sub `168 SESSION HYG VS SPX`, testid `credit-spread-regime-value`
  2. `HYG 8M` — `formatPct(current.hyg_ret)`, testid `credit-spread-hyg-ret`
  3. `SPX 8M` — `formatPct(current.spx_ret)`, testid `credit-spread-spx-ret`
  4. `LATEST SESSION` — `formatSessionDate(current.date)`, testid `credit-spread-session`
- Mobile: `useViewport()` → `m-regime-grid2x2` of `MetricCell`, testid
  `credit-spread-mobile-grid` (labels `REGIME`, `HYG 8M`, `SPX 8M`, `SESSION`).
- Chart: `CriHistoryChart`, title `S&P 500 VS HYG`, left series SPX
  `chartSeriesColor("primary")` `scaleType: "log"`, right series `HYG`
  `chartSeriesColor("fault")` linear, `xTickFormat = formatDateTick`. Container
  `className="breadth-history-block"` testid `credit-spread-chart-section`.
- Range: `HistoryRangeChips` defaulting to **`all`**, `BrushMinimap`
  `values = series.map(p => p.hyg_close ?? 0)`, `testIdPrefix="credit-spread-brush"`,
  `ariaLabel="Credit series history range brush"`.
- Footnote: `Source: Yahoo Finance daily closes (HYG, S&P 500). HYG is the traded high-yield credit proxy. ICE CCC OAS is not stored.`
  **No cadence claims.** No em dashes. Tokens only, 4px max radius.
- Registration (UI worktree):
  - `web/lib/regimeRail.ts` — add `credit` to the `RegimeTab` union; append to
    Positioning group (`["gex", "margin", "credit", "cot", "short", "ats"]`);
    label `CREDIT`. This bumps `REGIME_TABS` from 18 → 19
    (`web/tests/regime-rail.test.tsx`).
  - `web/components/RegimePanel.tsx` — `tabFromPathname` regex, desktop fallback
    array if still present, `if (activeTab === "credit")` dispatch.
  - Route page `web/app/regime/credit/page.tsx`.
  - `web/tests/regime-tab-routes.test.tsx` — `["credit", "app/regime/credit/page.tsx"]`
    in the `describe.each` table + stub + render/nav cases.
  - E2E `web/e2e/credit-spread-tab.spec.ts`.

## Health / scheduling

- `web/lib/serviceHealthWindows.ts`: `"credit-spread": { open: 26*HOUR, extended: 26*HOUR,
  closed: 26*HOUR, category: "scheduled", requires_ib: false }` + a 26h pin test in
  `web/tests/service-health-windows.test.ts` (same shape as yield-curve).
- `scripts/watchdog/services.py`: same 26h window + append to the daily-bucket list.
- `cloud/services/radon-credit-spread.service`: Type=oneshot, User=radon,
  WorkingDirectory=/home/radon/radon, EnvironmentFile=/home/radon/radon-cloud/.env,
  Environment=RADON_DB_NO_REPLICA=1,
  ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_credit_spread.py,
  TimeoutStartSec=300, journal out/err, StartLimitIntervalSec=300, StartLimitBurst=5.
- `cloud/services/radon-credit-spread.timer`: `OnCalendar=*-*-* 21:45:00 UTC` (comment:
  after the 16:00 ET cash close so Yahoo has the session bar; weekend/holiday runs
  are no-op heartbeats so the 26h staleness window never widens), `Persistent=true`,
  `RandomizedDelaySec=300`.
- Append both units to `cloud/scripts/setup-vps.sh` `SERVICE_FILES` and
  `cloud/tests/test_systemd_services.py`.
- Drift: add `not-installed:radon-credit-spread.{service,timer}` to
  `cloud/config/drift-allowlist.conf` (expires=2026-12-31) — deploys do not install
  units; root install-copy is owed after merge.

## Tests (written first, red before implementation)

- `scripts/tests/test_credit_spread.py` — fixture parse pins, align, lookback,
  regime boundaries, payload, persist guards, migration 51 + idempotent upsert.
- `web/tests/credit-spread-api.test.ts` — Turso beats older disk; disk fallback;
  exact missing object at 200; no `yield-curve` leak; `dynamic === "force-dynamic"`.
- `web/tests/credit-spread-panel.test.tsx` — helper pins, loader/empty/strip/title/
  brush/default-All, NaN guard (SPX close 0 on the log axis).
- `web/e2e/credit-spread-tab.spec.ts` — route mocks; active tab, strip values,
  ≥2 stroked paths, brush visible, missing-state copy.
