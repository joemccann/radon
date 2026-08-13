---
name: new-indicator
description: The canonical Radon pattern for shipping a new market indicator end to end (ingestion job, Turso storage, API route, regime chart tab, tests, systemd timer, CI, production verify). Derived from the BREADTH (21c13715, f067491e, 770d4674) and FINRA margin-debt (c5f4b877, b2f452fb, 189fcad8) builds. Read before adding any indicator; /indicator orchestrates a parallel swarm on top of this pattern.
---

# New market indicator — the Radon pattern

An indicator is a **vertical slice** with seven parts. All seven ship together; the
reference implementations are the FINRA margin-debt tab (external HTTP source, daily
timer) and the NYSE breadth tab (IB + StockCharts, 5-min timer). Copy margin-debt for
scheduled external-source indicators; copy breadth for IB-fed intraday collectors.

## 0. File checklist

Create:

| File | Modeled on |
|---|---|
| `scripts/clients/<source>_client.py` (only if the source needs retry/conditional-GET logic) | `scripts/clients/finra_client.py` |
| `scripts/fetch_<name>.py` or `scripts/<name>_scan.py` | `scripts/fetch_margin_debt.py` / `scripts/breadth_scan.py` |
| `scripts/db/migrations/00NN_<name>.sql` (next free number, 4-digit, lex-ordered) | `0027_margin_debt_history.sql`, `0029_rv_ratio.sql` |
| `scripts/tests/test_<name>.py` | `scripts/tests/test_margin_debt.py` |
| `web/app/api/<name>/route.ts` | `web/app/api/margin-debt/route.ts` |
| `web/lib/<name>.ts` (pure helpers + types) | `web/lib/marginDebt.ts` |
| `web/lib/use<Name>.ts` | `web/lib/useMarginDebt.ts` |
| `web/components/<Name>Panel.tsx` | `web/components/MarginDebtPanel.tsx` |
| `web/app/regime/<slug>/page.tsx` (time-series regime only; name-ranking scanners use `/scanner?mode=<slug>`) | `web/app/regime/margin/page.tsx` / `ScannerModeTabs` |
| `web/tests/<name>-api.test.ts` | `web/tests/margin-debt-api.test.ts` |
| `web/tests/<name>-panel.test.tsx` | `web/tests/margin-debt-panel.test.tsx` |
| `web/e2e/<name>-tab.spec.ts` | `web/e2e/margin-debt-tab.spec.ts` |
| `cloud/services/radon-<name>.service` + `.timer` | `radon-margin-debt.{service,timer}`, `radon-bpi.timer` |

Modify (lockstep pins — miss one and a test fails, which is the point):

- `scripts/db/writer.py` — `upsert_<name>_rows(...)` (+ reuse `upsert_scan_snapshot` / `record_service_health`)
- IA: time-series market-state charts (CRI, GEX, breadth, margin) go on `/regime/<slug>`. Name-ranking scanners (LEAP, GARCH, cheap-wing vol cone) go on `/scanner?mode=<slug>` next to Flow / Discover. Do not park a name scanner on Regime.
- Regime only: `web/components/RegimePanel.tsx` — **four places**: the `RegimeTab` union, `REGIME_TAB_VALUES`, the `tabFromPathname` regex, the desktop `<button>` row (the mobile chip bar maps over an inline array — update it too), plus the `if (activeTab === "<slug>")` dispatch branch
- Regime only: `web/tests/regime-tab-routes.test.tsx` — add `["<slug>", "app/regime/<slug>/page.tsx"]` to the `describe.each` table + a render/navigation case
- Scanner: `ScannerModeTabs` + `WorkspaceSections` `ScannerMode` + `?mode=` parse + panel branch + `scanner-mode-tabs` tests
- `web/lib/serviceHealthWindows.ts` — staleness window entry (kebab-case service name)
- `web/tests/service-health-windows.test.ts` — the `expected` set is **exhaustive**; add the new service
- `scripts/watchdog/services.py` — same window for the Python watchdog + the daily-bucket check list
- `cloud/scripts/setup-vps.sh` — append both units to the `SERVICE_FILES` array (line ~34-97). **Deploys do NOT install units; this array is the only scripted install path.**
- `cloud/tests/test_systemd_services.py` — add both units to the canonical set

## 1. Ingestion job (`scripts/`)

- Python 3.13, stdlib-preferred parsing (margin-debt parses xlsx with `zipfile` + `ElementTree`). Composed-method style: small pure functions (`parse_*`, `merge_*`, `compute_*`, `build_output`) so pytest covers them without network.
- **Honest User-Agent** (`radon/2.0`). Never impersonate a browser; FINRA's Cloudflare *blocks* browser UAs and serves plain clients.
- **Conditional GET fast path**: persist the source `Last-Modified` in the JSON payload; send `If-Modified-Since`; on 304 skip parse + row upserts and only refresh the snapshot + heartbeat with a new `scan_time` ("source unchanged (304); refreshing snapshot only").
- **Empty-payload guard**: never cache or mirror an empty result (`persist_result` refuses; protects last-good cache — cf. feedback_dont_cache_empty_results).
- Timestamps: tz-aware UTC ISO `scan_time`; `ZoneInfo("America/New_York")` for session logic, never hardcoded offsets.
- Writes, in order, every cycle:
  1. `writer.ensure_no_replica_for_writers()`
  2. `writer.upsert_<name>_rows(series, recorded_at=scan_time)` — **only when rows changed**
  3. `writer.upsert_scan_snapshot("<name>", scan_time, payload)` — every cycle
  4. `writer.record_service_health("<name>", "ok", finished_at=scan_time)` — every cycle, or error rows latch (feedback_service_health_heartbeat)
  5. JSON fallback `data/<name>.json` (atomic write) — fallback only; **Turso is the source of truth** (Data Persistence rule: host-local files are ephemeral on the VPS)
- Service name is **kebab-case everywhere** (`margin-debt`, not `margin_debt` — the underscore variant once wrote a stray `service_health` row that had to be deleted by hand on the VPS).
- CLI: `--json` prints the payload to stdout; progress/summary to **stderr** (subprocess contract).
- If IB is involved: bounded awaits (`asyncio.wait_for`), range-based client IDs registered in `scripts/CLAUDE.md`, `snapshot=True` must pass `""` genericTickList.

## 2. Storage (Turso-first)

- Migration `scripts/db/migrations/00NN_<name>.sql`: `CREATE TABLE IF NOT EXISTS <name>_history (...)` with a natural PK (`date` or `(symbol, date)`), a `recorded_at TEXT NOT NULL`, a `DESC` index, and the trailing
  `INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (NN, datetime('now'));`
- Applied automatically: `scripts/db/migrate.py` runs as `radon-api` `ExecStartPre` on deploy (no per-migration registration). Local: `bun run db:migrate`.
- Writer: idempotent per-key `INSERT ... ON CONFLICT(date) DO UPDATE`. Latest-snapshot reads go through `scan_snapshots (service, scan_time, payload)` unless the payload is per-symbol (then a dedicated `*_snapshots` table like rv-ratio/bpi).
- **Verify the row lands in Turso in production before calling the task done.** `data/*.json` is a stale fallback, never verification evidence.

## 3. API route (`web/app/api/<name>/route.ts`)

- `export const dynamic = "force-dynamic"; export const runtime = "nodejs";` — GET only unless there is a real on-demand trigger (breadth's POST proxies FastAPI with cooldown + single-flight).
- Read through **`dbFirstRead`** (`web/lib/dbFirstRead.ts`): `fromDb` selects the latest `scan_snapshots` row (`WHERE service = '<name>' ORDER BY scan_time DESC LIMIT 1`), `fromDisk` reads `data/<name>.json`, and the helper serves whichever content timestamp is fresher.
- `MAX_AGE_MS` derives from the real cadence with slack (daily timer → 48h; 5-min timer → 30min), commented with the reasoning: "older than X means the writer is down."
- **Missing contract**: absent data is `HTTP 200` + a frozen `{ missing: true, scan_time: null, series: [], ... }` object — never a 4xx (feedback_http_status_for_real_errors).
- `setCacheResponseHeaders(response, { maxAgeSeconds, staleWhileRevalidateSeconds, requestId, cacheState: "HIT", tags: ["<name>"] })` scaled to cadence (daily → 300/3600).
- Hook `web/lib/use<Name>.ts`: `useSyncHook({ endpoint, interval, hasPost, extractTimestamp: d => d.scan_time })`. Poll interval matches cadence (hourly for a daily series); `0` pauses.

## 4. Chart tab (`web/components/<Name>Panel.tsx`)

- Gate order: `SpectralLoader` (`label="Loading <source> series"`) while `(loading || syncing) && !data` → `SectionEmptyState` on `missing:true` → content.
- History chart: **`CriHistoryChart`** with up to two series; SPX overlay = left axis, `chartSeriesColor("primary")`, `scaleType: "log"` for multi-decade price; the indicator on the right axis. Title `UPPERCASE`, `xTickFormat` for non-session x domains.
- Range: `HistoryRangeChips` + `web/lib/historyRange.ts` presets and **`BrushMinimap`** (`values`, `range`, `onRangeChange`, `onCustom`, `testIdPrefix="<name>-brush"`). Long monthly series default to `All`; session charts default per `defaultPresetForLength`.
- Strip: `RegimeStrip`/`RegimeStripCell` desktop, `useViewport()` → `MetricCell` grid mobile.
- **Brand tokens only** — `var(--token)` + `color-mix(...)`, never raw hex/rgba; 4px max radius; `InfoTooltip` explains the signal and thresholds; **no em dashes in copy**.
- **Freshness copy is derived, never asserted**: header clock renders `lastSync` (= payload `scan_time`); a `SOURCE UPDATED` cell renders the upstream `Last-Modified`/data date. Never write "Refreshes daily/5m" unless it names the actual timer cadence — and grep the repo for existing instances before shipping cadence copy (UI Copy rule). Empty-state copy may reference "the <name> refresh timer" only if that timer exists.
- Chart-system spec: panels inherit `surface.paddingPx: 16` / `radiusPx: 4` from `web/lib/chart-system-spec.json` (pinned by og-chart tests).

## 5. Tests (red/green TDD — write these first)

- **pytest** (`scripts/tests/test_<name>.py`): checked-in real fixture artifacts (a captured upstream file) parsed at import; expected values derived by inspecting fixtures, never mental arithmetic; **window-relative dates** for anything freshness-related (hardcoded dates rot in CI); migration executed into in-memory sqlite to pin schema + version + upsert idempotency; conditional-GET stub client asserting the 304 path heartbeats without row upserts; monkeypatch `_write_db_cache`/writer — `db.client.get_db()` and `hrana_http` **refuse real connections under `PYTEST_CURRENT_TEST`** by design.
- **vitest API** (`@vitest-environment node`): mock `@/lib/db` with a real in-memory `@libsql/client` seeded with the actual table so SQL executes; assert Turso-beats-older-disk, disk fallback, exact `missing:true` object at 200, no cross-service snapshot leak, `route.dynamic === "force-dynamic"`.
- **vitest panel** (`@vitest-environment jsdom`): stub `ResizeObserver`; `vi.mock` the hook; factory fixtures (`buildSeries(n)`, `hookState()`); assert loader label, empty state, strip values, chart title, chips/toggles, and a NaN guard (`no <path d> contains "NaN"`).
- **Playwright** (`web/e2e/<name>-tab.spec.ts`): `page.route` mocks for `**/api/<name>` + the ambient routes (`portfolio`, `orders`, `ib-status`), abort `**/api/prices`; assert active tab, rendered paths, brush visible, missing-state copy. Localhost dev server needs no login (`RADON_AUTHLESS_TEST=1` + dev-mode localhost bypass in `web/middleware.ts`).
- Run from **repo root** (cwd drift produces bogus failures): full vitest is `bunx vitest run --config vitest.config.ts`; pytest is `python -m pytest scripts/tests scripts/api/tests scripts/trade_blotter` + `python -m pytest cloud/tests -q`.

## 6. Scheduling and deploy

- Units in `cloud/services/`: `radon-<name>.service` (`Type=oneshot`, `User=radon`, `WorkingDirectory=/home/radon/radon`, `EnvironmentFile=/home/radon/radon-cloud/.env`, `Environment=RADON_DB_NO_REPLICA=1`, `ExecStart=/home/radon/radon/.venv/bin/python /home/radon/radon/scripts/fetch_<name>.py`, `TimeoutStartSec` sized to the job, journald out/err) + `radon-<name>.timer` (`OnCalendar=... UTC`, `Persistent=true`, `RandomizedDelaySec`, `WantedBy=timers.target`). Comment the OnCalendar choice.
- Register in `setup-vps.sh` `SERVICE_FILES` and `cloud/tests/test_systemd_services.py`. **`deploy.sh` does not install or enable units** — on an existing VPS, install once by hand (`install -m 0644` to `/etc/systemd/system`, `daemon-reload`, `enable --now` the timer; root SSH works, see reference_vps_root_ssh_unit_installs), and keep repo/VPS reconciled or drift_audit flags it.
- Prod env note: units call the venv python **directly** — the `run_*.sh` wrapper fallback ladder resolves the wrong python on the VPS (feedback_scan_wrapper_fallback_picks_system_python).

## 7. CI gates and shipping

CI (`.github/workflows/ci.yml`) gates every push to main, then auto-deploys on green:

1. gitleaks secret scan
2. `bunx vitest run --config vitest.config.ts --coverage` — full config, coverage ratchet lines 75 / functions 78 / branches 65 (`web/app/api/**` is inside coverage `include` — an untested route drops the ratchet)
3. `python -m pytest scripts/tests scripts/api/tests scripts/trade_blotter --cov=scripts --cov=api --cov-branch --cov-fail-under=64` + `python -m pytest cloud/tests -q` (validates your systemd units)
4. Perimeter smoke (next build + curl auth asserts)
5. Deploy to Hetzner (no manual approval; never push while a deploy is in flight — cancelled builds have corrupted production)

Playwright is **not** in CI — run it locally and attach the evidence.

Ship checklist: one focused commit (stage files explicitly, never `git add -A`), push once, `gh run watch` to green, then verify **production**: latest `scan_snapshots`/history rows in Turso, `curl` the prod API route (authenticated perimeter: anon 401/404 is the perimeter working, not an outage), and a browser screenshot of the live tab.

## 8. Gotchas that cost real debugging time

- Breadth's `serviceHealthWindows` category/copy drifted from reality twice (says "on-demand" + "IB gateway" though a 5-min timer + StockCharts now drive it). When the data source or cadence changes, sweep every copy string that mentions it.
- The `regime-tab-routes` and `service-health-windows` tests are deliberate lockstep pins — update them with the feature, in the same commit.
- In-memory libsql test schemas can drift from the real migration (breadth's fixture omits a PK column). Seed test tables from the migration file when practical.
- Divergence/threshold semantics: use strict inequalities and pin the boundary in a test.
- A brand-new timer's first `service_health` row may be absent until first fire — no-row-ever = dormant, don't page (feedback_watchdog_dormant_no_row).
