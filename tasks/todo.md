# Task: Margin Debt Acceleration Indicator (FINRA)

Decisions (2026-07-03): splice legacy NYSE history back to ~1959 · lives as a new
`/regime/*` tab · net (debit − free credit) and CPI-deflated / %-of-GDP views as
toggles · data-source verdict pending provider research (FRED vs FINRA XLSX;
Exa available for retrieval).

## Phase 0 — Source verdict ✅ (research 2026-07-03, verified by direct fetch)
- [x] Live primary: FINRA XLSX `https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx`
      (200, Last-Modified Jun 16 2026, Jan 1997 → May 2026, $mm; updated in place ~3rd week; next ~Jul 28).
      Fallback 1: FINRA HTML stats page; fallback 2: Alphacast dataset 8207 (free API, ~1mo lag).
      FRED does NOT carry it (only quarterly Z.1 `BOGZ1FL663067003Q` broker-dealer aggregate — cross-check only).
      Quandl/NYXDATA bot-walled + frozen Nov 2017 — do not use.
- [x] Backfill 1959–1996: Wayback-archived NYSE Facts & Figures decade tables
      (`web.archive.org/.../viewer_edition.asp?...category=8`, keys 2313/2316/1374/605/595/278/3153);
      monthly margin debt + free credit columns confirmed present. Parse HTML tables → one-time CSV.
      Methodology break at Jan 1997: NYSE members-only = 96.2% of FINRA all-members ($99,460M vs $103,337M),
      drifting to 92.6% by Nov 2017. Decide: raw concat w/ flagged break vs ratio-adjust pre-1997 ×1.039.
      Archived HTML + XLSX working copies already in session scratchpad.
- [x] XLSX parse needs NO new deps — stdlib zipfile+ElementTree suffices (verified). Drop openpyxl plan.
- [x] CPI + GDP series for normalization toggles: FRED API, CPIAUCSL monthly + GDP quarterly;
      `FRED_API_KEY` added to root `.env` (2026-07-03)
- [x] Splice treatment decided: ratio-adjust pre-1997 ×1.039 for chart display; DB stores RAW values
      + `source` column (`nyse_legacy`/`finra`) so both presentations are recoverable; null the 12
      seam-straddling YoY months

## Phase 1 — Ingestion ✅
- [x] `scripts/clients/finra_client.py` (UWClient shape; conditional GET; typed errors; exported in
      `clients/__init__.py`). ⚠️ UA must stay NON-browser: FINRA Cloudflare 403-challenges browser UAs
      but serves plain clients (verified live 2026-07-03)
- [x] XLSX parse via stdlib zipfile+ElementTree (no new deps; cells mapped by r= column ref)
- [x] `scripts/fetch_margin_debt.py`: stdout = JSON only, progress → stderr; YoY + display splice +
      net + CPI-real + %GDP computed at ingestion; FRED views best-effort w/ availability flag
- [x] Legacy backfill DONE: `scripts/backfill_margin_debt_legacy.py` parsed 5 Wayback decade pages →
      `data/margin_debt_legacy.csv` (492 months, 1959-01..1999-12). Jan-1997 NYSE = 99,460 verified,
      anchoring `NYSE_TO_FINRA_SPLICE_RATIO` against parsed data
- [x] Red/green pytest: 22 tests green (parse, YoY calendar-lookback + gap + seam, splice ratio,
      net, deflate, %GDP, migration, idempotent upsert)

## Phase 2 — Storage ✅
- [x] Migration `0027_margin_debt_history.sql` applied to Turso
- [x] `writer.upsert_margin_debt_rows(...)` + `MARGIN_DEBT_UPSERT_SQL` (ON CONFLICT(date) DO UPDATE);
      dual-write `data/margin_debt.json`
- [x] Verified against Turso: 809 rows, 1959-01..2026-05; 2026-05 level 1,415,557 / YoY +53.7%;
      seam rows null; sources tagged. (Note: full-history upsert ≈ 90s over Turso HTTP — fine for a
      monthly timer, revisit only if it ever runs hot)

## Phase 3 — API
- [ ] FastAPI `GET /margin-debt/history` per `/internals/skew-history` pattern (`scripts/api/server.py:2568-2631`),
      7-day TTL, stale-on-failure, 200 + `{missing:true}` for empty; stays auth-gated (no AUTH_EXEMPT_PATHS change)
- [ ] `web/app/api/margin-debt/route.ts` via `radonFetch()`, force-dynamic, nodejs runtime
- [ ] curl-test both routes before calling done

## Phase 4 — Frontend (regime tab)
- [ ] Extend `CriHistoryChart.tsx` with per-series `scaleType: "log" | "linear"` (SPX left axis must be log)
- [ ] `MarginDebtPanel.tsx`: ChartPanel + BrushMinimap + SpectralLoader, 0% reference line,
      optional +50/60% / −20% bands; toggles: net view, CPI-deflated, %-of-GDP, 3mo MA smoothing
- [ ] Wire tab: `RegimeTab` union + `REGIME_TAB_VALUES` + `tabFromPathname` regex in
      `web/components/RegimePanel.tsx:29-41`
- [ ] Tokens only, no raw hex; 4px radius; no em dashes in copy
- [ ] Red/green vitest for YoY/net/normalization helpers; chrome-cdp E2E of the tab

## Phase 5 — Scheduling + deploy
- [ ] `scripts/run_margin_debt_refresh.sh` (per `run_catalysts.sh`), no holiday gate
- [ ] Local launchd plist `config/com.radon.margin-debt-refresh.plist` (daily; conditional GET no-ops)
- [ ] Hetzner `radon-margin-debt-refresh.{service,timer}` on the VPS (edit-on-box, radon-cloud),
      `OnCalendar=*-*-15..28 09:00`, `RADON_DB_NO_REPLICA=1`, EnvironmentFile
- [ ] Optional `service_health` heartbeat via scan mirror
- [ ] Full test suite green → commit → gated Production approval → verify on app.radon.run

## Notes
- SPX monthly history back to 1959: Yahoo ^GSPC monthly (1950+) via existing fallback path;
  IB/UW have no multi-decade monthly series (documented exception to the source-priority chain)

## Review
(fill in as phases land)
