# RADON — CLAUDE.md

**Radon** = market-structure reconstruction. Surfaces convex opportunities from dark pool / OTC flow, vol surfaces, cross-asset positioning. **Flow signal or nothing.**

Brand: `docs/brand-identity.md` · Structures: `docs/options-structures.{json,md}` · UW spec: `docs/unusual_whales_api.md` · Cloud runbook: `docs/cloud-services.md`

---

## ⛔ Mandatory Rules

1. **Be concise.** No preamble.
2. **Red/green TDD always.** Vitest (unit), chrome-cdp / Playwright (E2E). Target 95% coverage.
3. **E2E browser verification for all UI work.** Primary `chrome-cdp`, fallback Playwright (`web/playwright.config.ts`). UI is not done until visually confirmed.
4. **API keys** in `.env` files (table below). Never `~/.zshrc` unless fallback.
5. **No raw hex in UI.** Use brand tokens (`docs/brand-identity.md`). 4px max border-radius on panels.
6. **No em dashes in user-facing copy.**

## ⛔ Four Gates — Sequential, No Exceptions

| Gate | Rule |
|---|---|
| 1. Convexity | Gain ≥ 2× loss. Defined-risk only. |
| 2. Edge | Specific, data-backed dark-pool / OTC signal that hasn't moved price. |
| 3. Risk | Fractional Kelly. Hard cap 2.5% bankroll / position. |
| 4. ~~No naked shorts~~ | **DISABLED 2026-04-30.** Detection logic preserved as `_*Impl`. Re-enable steps: `docs/naked-short-reenable.md`. |

Any gate fails → stop. Name the gate.

## Data Source Priority

1. Interactive Brokers (TWS / Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Yahoo — fallback
4. Web scrape — last resort

Never skip to Yahoo / web without trying IB → UW first. Clients live in `scripts/clients/`.

## Credentials

| File | Loaded by | Contains |
|---|---|---|
| `.env` (root) | python-dotenv | MenthorQ creds, Clerk JWKS / issuer / allowlist |
| `.env.ib-mode` (root, gitignored) | overlayed after `.env` | `IB_GATEWAY_MODE`, `IB_GATEWAY_HOST` — toggled by `scripts/ib mode local\|cloud` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, Clerk keys |

**IB Flex Web Service env (Hetzner `/home/radon/radon-cloud/.env`):**

| Var | Points to | Used by |
|---|---|---|
| `IB_FLEX_TOKEN` | Flex Web Service token | All Flex pulls |
| `IB_FLEX_QUERY_ID` | `1422766` (blotter) | `scripts/trade_blotter/flex_query.py` |
| `IB_FLEX_NAV_QUERY_ID` | `1497709` (Cash Transactions, created 2026-05-05) | `scripts/cash_flow_sync.py`, `scripts/portfolio_performance.py` |

The `1442520` (journal/Trade History) query is referenced indirectly via `scripts/journal_rehydrate.py` reading `IB_FLEX_QUERY_ID` at runtime — but on Hetzner the env points at `1422766`. Journal rehydrate has its own configuration. **Don't repurpose `IB_FLEX_NAV_QUERY_ID` for trade pulls** — it's tuned for `CashTransaction` only.

---

## Architecture

`npm run dev` runs four services. Filter logs with `npm run dev -- --only <next|ib|api|scraper>`.

| Service | Port / cadence |
|---|---|
| Next.js | 3000 |
| FastAPI (`scripts/api/server.py`, 26 endpoints) | 8321 |
| IB WS relay (`ib_realtime_server.js`) | 8765 |
| Newsfeed scraper (`scripts/newsfeed/index.js`) | 120s |

Next.js routes call FastAPI via `radonFetch()` (`web/lib/radonApi.ts`). **No `spawn()` from Next.js.**

### Auth (Clerk)

All FastAPI routes JWT-protected; Next.js by `web/middleware.ts`; WebSocket via `scripts/api/ws_ticket.py` (30s TTL).

- **FastAPI localhost bypass:** `client.host in {127.0.0.1, ::1}` → auth skipped (covers Next.js → FastAPI server-to-server). `scripts/api/auth.py:51-54`.
- **Next.js localhost bypass:** auto-enabled when `NODE_ENV !== "production"`. Production builds (`next build && next start`) still enforce. Helpers in `web/middleware.ts`: `isLocalDevAuthBypassEnabled` (auto), `isLocalAuthlessTestBypassEnabled` (`RADON_AUTHLESS_TEST=1` for Playwright).
- **Auth-exempt:** `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`, all `*/share` routes.

### IB Gateway — Three Modes

`IB_GATEWAY_MODE` env, persisted to `.env.ib-mode`. Toggle via `scripts/ib mode {local|cloud}`. Switching does NOT auto-reconnect — restart the dev stack.

- **`docker`** (default for local): `ghcr.io/gnzsnz/ib-gateway`, `restart: unless-stopped`. `npm run ib:start`.
- **`cloud`** (default for dev): Hetzner VM at `ib-gateway:4001` via Tailscale. TCP probe only — `POST /ib/restart` returns 503. Laptop aliases (SSH-wrapped, defined in `~/.zshrc`): `ibstart/stop/restart/status/logs/health` for IB Gateway only. **Whole-stack control on the VPS** uses `/usr/local/bin/radon` (also reachable from laptop via `ssh root@ib-gateway radon <cmd>`):

  | Command | Effect |
  |---|---|
  | `radon stop` | `systemctl stop` IB + all `radon-*` units |
  | `radon start` | start them all (IB Gateway first) |
  | `radon restart` | stop then start |
  | `radon status` (or bare `radon`) | `systemctl list-units "radon-*"` |

  Covers `radon-{ib-gateway,api,relay,monitor,newsfeed,nextjs}` + `radon-refresh.timer`. Installed manually 2026-05-04 — **not yet wired into `radon-cloud/scripts/setup-vps.sh`, so a `wipe-vps.sh` rebuild drops it.**
- **`launchd`** (legacy): `~/ibc/bin/`, Mon-Fri auto-lifecycle.

Auto-recovery (docker mode): port + CLOSE_WAIT detection at startup (poll 45s); subprocess errors trigger health check first — only restart if port not listening or CLOSE_WAIT. Client ID collisions, VOL errors, transient timeouts do NOT trigger restart.

### Client ID Ranges

| Range | Usage |
|---|---|
| 0–9 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10–19 | WS relay |
| 20–49 | Subprocess scripts — **always `client_id="auto"`** |
| 50–69 | Scanners |
| 70–89 | Daemons (fill=70, exit=71) |
| 90–99 | CLI |

**On-demand scripts MUST use `client_id="auto"`. Never hardcode in 20–49.**

### Two-Mode Deployment

Both modes read/write the **same Turso DB** (`libsql://radon-joemccann.aws-us-west-2.turso.io`) via embedded replica at `data/replica.db`. JSON files in `data/` are written alongside as fallback.

- `scripts/cloud.sh` → `RADON_MODE=hetzner`. Schedulers run as systemd services on Hetzner VPS (`radon-{api,monitor,relay,refresh,nextjs}`); laptop runs only Next.js + newsfeed scraper. `app.radon.run` keeps serving when laptop is closed.
- `scripts/local.sh` → `RADON_MODE=local`. Laptop launchd plists own all schedulers.

Schema: `scripts/db/migrations/0001_init.sql`. Writers: `scripts/db/writer.{js,py}`. Routes prefer DB, fall back to disk.

**Image host:** `https://media.radon.run` (Caddy on Hetzner, fed by laptop rsync over Tailscale). Posts use absolute URLs. Public-IP fallback: `RADON_MEDIA_REMOTE=radon@5.78.148.38:/home/radon/radon-cloud/media/`.

**Newsfeed is fully Hetzner-resident (cutover 2026-05-03).** `scripts/newsfeed/browser.js` + `auth.js` drive headless Playwright; auth via `THEMARKETEAR_EMAIL` / `THEMARKETEAR_PASSWORD` env. Session persisted to `data/newsfeed-storage.json` (gitignored, ~30d), full re-auth every ~6h. Polls every 120s. Hetzner runs it as `radon-newsfeed.service` (`Restart=on-failure`, `RestartSec=30`); steady-state cycle ~4s. The chrome-cdp / Chrome Debug.app dependency on the laptop is retired — close the lid and `app.radon.run` keeps showing fresh content. `deploy.sh` runs `npx playwright install chromium` after npm install; one-time `npx playwright install-deps chromium` (sudo) was applied during cutover. On Hetzner `RADON_MEDIA_REMOTE=/home/radon/radon-cloud/media/` (local fs path; rsync skips SSH self-loop). Local-laptop dev still works for iteration via `node scripts/newsfeed/index.js --once`.

**Trades canonical store (shipped 2026-05-03, `6c6f90f`):** Turso `journal` table. Both `/journal` and `/orders` derive from it. `/orders` uses `web/lib/blotter/fromJournal.ts:journalRowsToBlotter()` with a **union+preference fallback to `data/blotter.json`** for legacy aggregate-only rows that lack explicit `realized_pnl` / `cost_basis` / `proceeds` (the persistence path commit `bbc776e` added to `journal_rehydrate.py` only applies to *new* rehydrate runs; existing rows pre-date it). When IB Flex Query 1442520 cooldown clears and a fresh rehydrate runs, journal rows gain explicit P&L fields and the deriver auto-prefers them; `data/blotter.json` decays to a redundant fallback with no code change. See `docs/cloud-services.md` § "Trades — single source of truth".

### Production Build Constraint

Next.js 16 prerender crashes on `/_global-error` + `/_not-found` (root ClerkProvider context isn't materialised in isolated workers). `web/package.json` build uses `next build --experimental-build-mode=compile`. `app/error.tsx`, `app/[ticker]/not-found.tsx`, `app/global-error.tsx` use plain `<a>` + pure JSX (no `next/link`, no `useEffect`, no `globals.css`).

---

## ⚠️ Cache Contract — Disk-Backed Routes

Every Next.js GET handler reading live disk state (`data/*.json`, `data/menthorq_cache/`) **MUST** export `dynamic = "force-dynamic"`. Without it Next.js 16 statically prerenders the first response for the dev server's lifetime — that's the failure that surfaced the "CTA CACHE STALE" banner with fresh data on disk.

Every client fetch hitting these routes **MUST** pass `cache: "no-store"`.

Covered routes: `menthorq/cta`, `journal`, `discover`, `flow-analysis`, `blotter`, `vcg`, `internals`, `portfolio`, `performance`, `scanner`, `regime`, `gex`. Covered hooks: `useMenthorqCta`, `useSyncHook` (shared), `useJournal`, `usePortfolio`, `useDiscover`, `useOrders`. Contract test: `web/tests/api-routes-no-cache-contract.test.ts` (18 assertions, fails CI on regression).

---

## Combo / BAG Order Guardrails

1. **Never map combo `Order.action` from debit vs credit.** IB combo legs define structure. SELL envelope reverses legs. For entry / open: keep envelope BUY, preserve per-leg actions.
2. **`ComboLeg.action` = structure, not direction.** Always `LONG → BUY`, `SHORT → SELL`. Flipping causes IB error 201.
3. **Order-builder structure change → invalidate manual net price.** Recompute from normalized combo quote on single-leg ↔ combo transitions.
4. **Combo natural market uses cross-fields:**
   - BUY combo: pay ASK on BUY legs, BID on SELL legs
   - SELL combo: receive BID on BUY legs, ASK on SELL legs
   - Impls: `computeNetOptionQuote()`, `ComboOrderForm.netPrices`, `resolveOrderPriceData()`.
5. **Trace path before fixing:** chain builder → `/api/orders/place` → FastAPI bridge → `scripts/ib_place_order.py`. Identify whether bug is UI state, payload semantics, or IB combo behavior.
6. **Required regressions:** unit (action/ratio/net-price), browser (displayed net + submitted payload).

## Cancel / Modify Failure Propagation

1. **Cancel/modify MUST use subprocess with original clientId.** Master client (0) sees all orders but can't modify (Error 10147/103). `ib_order_manage.py` reconnects as original.
2. **Clear VOL fields before modify.** Reset `volatility` / `volatilityType` to IB sentinels (`1.7976931348623157e+308` / `2147483647`) to avoid Error 321.
3. **Confirm against refreshed open-order snapshot**, not stale `Trade`. Disappearance after cancel = success.
4. **Preserve upstream error detail.** Subprocess JSON → FastAPI `detail` → Next.js. Never collapse to generic 500.
5. **Required regressions:** unit (refreshed confirmation), route (status propagation), browser (toast / error).

---

## Calculations — Correctness Rules

### Sign Convention
Credits negative, debits positive. **Never `Math.abs()` on option prices without approval.** Preserve sign through entire display pipeline.

### Daily Change %
```
Day Chg % = Daily P&L / |Yesterday's Close Value| × 100   (NEVER entry cost)
```
Per-leg: `sign × (last - close) × contracts × 100`. Impl: `getOptionDailyChg()`.

**Same-day exception:** `entry_date == today (ET)` → yesterday's close meaningless. Day Chg and Today P&L use entry-cost as baseline → Today P&L = Total P&L = `MV − EC`. **`ib_daily_pnl` is ignored same-day** (IB sometimes reports stale numbers for fresh fills).

### Entry-Date Resolution (`ib_sync.py`)

Strict ordered fallback, MOST → LEAST specific:
1. blotter (per-contract: `ticker|expiry|right|strike`)
2. trade_log (`ticker|structure`)
3. IB fills (per-contract, same-session)
4. prev portfolio (`ticker|structure|expiry`, excluding today)
5. **today** ← brand-new positions land here so same-day P&L branch fires

**Never use a per-ticker blotter fallback** — different contracts have different open dates. Regression: AMD Risk Reversal P$320/C$330 was assigned an unrelated AMD 295P date (test: `test_combo_entry_date.py`).

### Per-Leg P&L
`Leg P&L = sign × (|MV| − |EC|)`. Sum = position P&L. Impl: `LegRow` in `PositionTable.tsx`.

### Total P&L %
`(MV − EC) / |EC| × 100`

### Price Resolution
| Context | Source |
|---|---|
| Stock | `prices[ticker].last` |
| Single-leg option | `prices[optionKey(...)].last` |
| Multi-leg spread | Net from each leg's `prices[legPriceKey(...)]` |
| BAG order | `resolveOrderLastPrice()` / `resolveOrderPriceData()` |
| PriceBar | `resolvePriceBar()` — option for single-leg, underlying for multi-leg |

**Never show underlying where user expects option / spread. Show "---" if unavailable.**

### Exposure Delta Sign
`rawDelta = sign × lp.delta` where `sign = -1` for SHORT. LONG Call →+, SHORT Call →−, LONG Put →−, SHORT Put →+. Impl: `web/lib/exposureBreakdown.ts`.

### Implied (Black-Scholes) Value
TS port of `scripts/scenario_analysis.py:192-226`, verified to 4-decimal Python parity.

| Input | Source order |
|---|---|
| **S** | `prices[ticker].last` → `prices[optionKey].undPrice` → `(bid+ask)/2` |
| **σ** | `prices[optionKey].impliedVol` → bisection on `close` (T_yest = T+1/365) |
| **K** | `leg.strike` |
| **T** | `(expiry@16:00 ET − now) / 365 days` |
| **r** | `useRiskFreeRate()` → FRED DFF, 24h cache, fallback 0.0 |

Combo: signed sum across legs. Files: `web/lib/blackScholes.ts`, `impliedValue.ts`, `useRiskFreeRate.ts`. Implied / Implied MV columns gated on `positions.some(p => p.structure_type !== "Stock")`.

### Position Structure (`detect_structure_type()` in `ib_sync.py`)
Stock→equity. Long Call / Put→defined. Short Call / Put→undefined. Spreads→defined. Synthetic / Risk Reversal→undefined. Long Straddle→defined. Covered Call→defined. All-long combo→defined. Unrecognized→complex (→Undefined Risk table).

### Data Normalization
JSON: `"ticker"`. IB contracts: `"symbol"`. Read defensively: `t.get("ticker") or t.get("symbol")`.

### Margin Warning Thresholds (`web/lib/marginWarning.ts`)

Persistent toast fires on transition into a worse level. Thresholds match IBKR's published guidance.

```
critical:  excess_liquidity ≤ 0                              (active margin call)
critical:  cushion < 0.01  (< 1%)                            (imminent)
warning:   cushion < 0.05  (< 5%)                            (approaching)
warning:   equity_with_loan_value ≤ maint_margin_req × 1.10  (IBKR's own published rule)
none:      otherwise

cushion = excess_liquidity / net_liquidation
```

`assessMargin()` is a pure function — derive on the client from `portfolio.account_summary`. Toast UX in `WorkspaceShell.tsx` near the existing `prevIbConnectedRef` block; `prevMarginLevelRef` ensures we only fire on transition to a higher rank (`none < warning < critical`). User dismisses via the existing `×` close button. **Never auto-dismiss** (`addToast(..., 0)`).

This is **Stage 1 (threshold-derived)** of the margin-alerting plan. **Stage 2** swaps the data source to authoritative IBKR Web API `/fyi/notifications` once OAuth Self-Service is activated for the account; the toast UI does not change. Plan: `~/.claude/plans/identify-all-issues-with-reactive-kernighan.md`.

Tests: `web/tests/margin-warning.test.ts` (12 unit cases), `web/e2e/margin-warning-toast.spec.ts` (6 Playwright scenarios).

---

## Component Cheat Sheet

Each tab follows the same pattern: hook + staleness lib + API route + panel + scanner + cache file. Diverging behavior noted below.

| Tab | Files (under `web/`, `scripts/`) | Notes |
|---|---|---|
| **VCG** (vol-credit gap) | `useVcg.ts`, `vcgStaleness.ts`, `app/api/vcg/route.ts`, `VcgPanel.tsx`, `vcg_scan.py` (20-session), `data/vcg.json` | RO: VIX>28 + VCG>2.5. EDR: VIX>25 + VCG 2.0–2.5. BOUNCE: VCG<-3.5. VVIX = severity amplifier, not gate. FastAPI: `POST /vcg/{scan,share}`, 60s cooldown. |
| **GEX** (gamma exposure) | `useGex.ts`, `gexStaleness.ts`, `app/api/gex/route.ts`, `GexPanel.tsx`, `gex_scan.py`, `data/gex.json` | UW fields: `call_gex` positive, `put_gex` negative, `net = call_gex + put_gex` (no negation). Levels: GEX Flip, Max Magnet, Max Accelerator, Put / Call Wall. Bias: BULL / CAUTIOUS_BULL / NEUTRAL / CAUTIOUS_BEAR / BEAR from flip pos + net sign + magnet. Tests: 71. |
| **CRI / Regime** | `web/lib/criStaleness.ts`, `regime` route triggers `cri_scan.py` | Stale if `data.date != today` OR (market_open AND mtime>60s). Closed + date=today → serve EOD. |
| **Regime market-closed** | `RegimePanel` | Use `data.{vix,vvix,spy}` only (no WS `last`). `activeCorr = data.cor1m`. `liveCri / intradayRvol = null`. Don't update VIX / VVIX timestamps. COR1M badge = DAILY. |
| **Regime day-change** | `.regime-strip-day-chg` | VIX/VVIX/SPY: WS `last` vs `close`. RVOL: `intradayRvol - data.realized_vol`. COR1M: `data.cor1m_5d_change`. Arrow always **right** of change text via `display: flex; gap: 4px`. |
| **Regime history** | `CriHistoryChart.tsx` | 20 sessions, 440px. Left: VIX `#05AD98` + VVIX `#8B5CF6`. Right: RVOL `#F5A623` + COR1M `#D946A8`. |
| **Options Chain sticky header** | `OptionsChainTab.tsx` | Three required CSS rules — all three or overlap returns: (1) `background: var(--bg-panel-raised)` on `.chain-header` + `.chain-side-label`; (2) `position: sticky; top: 0` / `top: 24px`; (3) `.chain-grid thead { position: relative; z-index: 10 }`. |
| **Column visibility** | `useColumnVisibility(tableId, defaults)` | Persists to `localStorage` keyed `radon:columns:<tableId>`. Buckets: `positions-{defined,undefined,equity}`, `orders-open`. `<ColumnsToggle />` left of filter input in section header. |
| **Margin Warning Toast** | `web/lib/marginWarning.ts`, `web/components/WorkspaceShell.tsx` (`prevMarginLevelRef` block), `web/tests/margin-warning.test.ts`, `web/e2e/margin-warning-toast.spec.ts` | Stage 1 — threshold-derived from `portfolio.account_summary`. Persistent toast (`addToast(..., 0)`), fires only on transition to a worse rank. See "Margin Warning Thresholds" in Calculations. Stage 2 will swap source to IBKR `/fyi/notifications` once OAuth Self-Service activates. |
| **Cash Flows panel** (on `/orders`) | `scripts/cash_flow_sync.py` (Flex pull + classifier), `scripts/db/migrations/0002_cash_flows.sql`, `scripts/db/writer.py:upsert_cash_flow`, FastAPI `GET /cash-flows`, `web/app/api/cash-flows/route.ts`, `web/lib/useCashFlows.ts`, `web/components/CashFlowsSection.tsx`, daemon handler `scripts/monitor_daemon/handlers/cash_flow_sync.py` (86400s daily) | Surfaces IBKR `CashTransaction` rows (deposits / withdrawals / dividends / interest / fees / withholding tax) on `/orders`. Reads `IB_FLEX_NAV_QUERY_ID` (1497709, *Cash Transactions* query). Idempotent on `transactionID`. Type classifier in `_classify()` — combined "Deposits/Withdrawals" rows disambiguate by amount sign. UI: positive = green, negative = red, persistent toast not used. Tests: 15 pytest + 5 Playwright. |
| **Mobile shell** (PWA, iPhone 16 393×852) | `web/lib/useViewport.ts`, `web/lib/breakpoints.ts`, `web/components/mobile/{MobileShell,MobileAppBar,MobileTabBar,MobileMoreDrawer,MobileTickerSearch,Card,CardRow,BottomSheet}.tsx`, `web/components/PwaRegister.tsx`, `web/public/{manifest.webmanifest,sw.js}` | `useViewport()` (≤640 mobile / 641-1023 tablet / ≥1024 desktop) drives `<MobileShell>` rendered from `WorkspaceShell` when `isMobile && hasMounted`. Sets `body[data-mobile="true"]`; global CSS hides desktop sidebar/header, pads main for the 56px top bar + 64px bottom tab bar (Dashboard / Positions / Orders / Scanner / More + drawer overflow). Manifest = standalone, theme #0a0f14, 192/512 icons. Hand-written SW (~80 LOC) caches static assets only — bypasses `/api`, `/_next/data`, `/ws` to preserve the cache contract. Search button opens full-screen overlay wrapping `TickerSearch` (16px input → no iOS zoom). |
| **Mobile per-screen variants** | `web/components/mobile/{MobilePositionList,MobileOrderList,MobileBlotterList,MobileExecutedList,MobileJournalList,MobileChainLadder,MobileOrderTicket}.tsx` | Each branched in via `isMobile && hasMounted` from `PositionTable.tsx`, `WorkspaceSections.tsx` (open orders / today's executed / historical trades / journal sections), and `OptionsChainTab.tsx`. All P&L / combo math reused — no recalculation. Chain ladder is 2-col (calls / strike / puts), tap a cell → BottomSheet detail with Greeks; BUY/SELL footer adds an `OrderLeg` to the same `orderLegs` state the desktop builder uses. Pending strip → `MobileOrderTicket` (BottomSheet) with qty steppers + price ladder + DAY/GTC chips → posts `/api/orders/place` with the same body shape (combo guardrails 1-6 unchanged). |
| **Mobile tests** | `web/tests/{use-viewport,mobile-bottom-sheet}.test.{ts,tsx}` (15 vitest); `web/e2e/mobile-{shell,positions,orders,blotter,executed-journal,chain-ladder,order-ticket,ticker-search,charts,a11y-pwa}.spec.ts` (48 Playwright cases at viewport 393×852 via `playwright.config.ts` `mobile` project) | Run via `npm test` (vitest) and `PLAYWRIGHT_PORT=3033 npx playwright test --config playwright.config.ts --project=mobile`. The mobile e2e suite stubs all API routes + skips WS prices; specs use `evaluate(el => el.click())` for interactive elements that render in `BottomSheet` footers below the 393×852 viewport (force:true alone fails with "outside viewport"). |

---

## Newsfeed Scraper

Module split under `scripts/newsfeed/` (`paths`, `browser`, `auth`, `cdp`, `extract`, `media`, `store`, `tagger`, `vision_tagger`, `taxonomy`, `scheduler`, `index`). `scripts/newsfeed-scraper.js` is a back-compat shim. Output shape locked by `web/components/DashboardNewsFeed.tsx` (`MarketEarPost`).

**Key behaviors:**
- **Headless Playwright** (`browser.js` + `auth.js`) replaces the old chrome-cdp dependency. Required env: `THEMARKETEAR_EMAIL`, `THEMARKETEAR_PASSWORD`. Storage state at `data/newsfeed-storage.json` reuses the session across cycles; full re-auth every ~6h. `cdp.js` is a thin Playwright shim for backwards compatibility (`runCdpCommand`, `fetchCookieHeader`, `listTargets`, `selectMarketEarTab` still exported).
- **IPv4 forced** for `themarketear.com` CDN (`https.Agent { family: 4 }`) and `api.cerebras.ai` (undici dispatcher) — both AAAA-unreachable from residential IPv6.
- **Cookie-gated images:** `media.js` accepts `getCookieHeader` callback; `themarketear.com` cookies pulled via Playwright `context.cookies()` to follow `/images/<hash>.png` 301 → `*.cdn.digitaloceanspaces.com`.
- **Rollover** at 500 KB → archive + keep ⌈N×0.2⌉. `mergePosts` preserves `tags` across cycles.

**Tagging:**
- Router: vision tagger (`claude-haiku-4-5`, ~$0.003/post) for posts with images, text tagger (Cerebras `gpt-oss-120b` → fallback `qwen-3-235b-a22b-instruct-2507`) for text-only.
- gpt-oss-120b is a reasoning model — needs `max_tokens: 800` for chain-of-thought before final JSON.
- Exactly **3 tags per post**, free-form. Existing taxonomy shown as context to encourage reuse.
- **Naming rules** (enforced by `__normaliseTags`): UPPERCASE, multi-word UPPERCASE-KEBAB-CASE (`PUT-CALL-RATIO`), allowed `A-Z 0-9 - &`, case-insensitive dedup at taxonomy layer.
- `hydrateTags` skips posts with `tags.length >= 3` unless `force=true`.
- `data/tag_taxonomy.json` is force-tracked despite `data/*.json` gitignore. Filter chip pool auto-derives from tags actually present.
- Either `CEREBRAS_API_KEY` or `ANTHROPIC_API_KEY` is sufficient. Without both, tagging skipped (posts still scraped).

**Backfill:** `scripts/newsfeed/backfill_tags.js`. `--retag` re-tags every post (use after prompt or naming rule changes). Throttles to ~24 req/min under Cerebras 30 rpm.

**`concurrently` env quirk:** `scripts/newsfeed/index.js` explicitly loads `web/.env` and root `.env` via `dotenv` because `concurrently` doesn't inherit env to children.

**Filter UI:** Per-post chips, AND-semantics with ≥2. Active filters as top bar with × + "Clear all". Pagination below list. Deep-link: `/dashboard?tags=BTC,vol`. URL writes happen in post-commit `useEffect` to avoid React's "Cannot update a component while rendering" warning.

**Env overrides:** `RADON_NEWSFEED_DATA_DIR`, `_POSTS_FILE`, `_ARCHIVE_DIR`, `_MEDIA_DIR`, `_PUBLIC_ROOT`, `CDP_CLI`.

**Tests:** 64 cases across `newsfeed-{scraper,tagger,taxonomy,time}`, `dashboard-newsfeed-{pagination,tag-filter}`.

---

## High-Throughput Architecture

500+ symbols, <500ms signal-to-order.

- **Parallel scanning:** `scanner.py` (15 workers), `discover.py` (10 workers). `UWRateLimitError` skips ticker, doesn't crash.
- **Atomic state:** `scripts/utils/atomic_io.py` — `atomic_save()` (temp + `os.replace()` + SHA-256), `verified_load()`.
- **Batched WS relay:** `ib_realtime_server.js` — per-client last-write-wins, 100ms flush. 5000 msg/s → 10 batched/s.
- **Stale tick detection:** 30s check, 45s no-ticks → auto-restart Gateway (120s cooldown).
- **WS state machine** (`usePrices.ts`): `idle → connecting → open → closed`. `connStateRef` for idempotent connect, `socketGenRef` ignores stale events, diff-based sub/unsub, callback refs, exponential backoff (1s–30s, max 10).
- **Vectorized:** `kelly_size_batch()` (NumPy), `portfolio_greeks_vectorized()`. Cross-validated to 10⁻¹².
- **IBClient resilience:** disconnect recovery (5 attempts, 2ⁿs cap 30s); pacing violations (162/366: 10s backoff); invalid contracts (200/354: no retry, `_failed_contracts`).
- **Performance page:** Phase A sequential IB+cache; Phase B ThreadPool UW/Yahoo. `PERF_FETCH_WORKERS` env (default 8). Disk cache `data/price_history_cache/` TTL 15min/24h. SWR via `POST /performance/background`.

---

## Evaluation — 7 Milestones (Stop on Failure)

1. Validate ticker → `scripts/fetch_ticker.py`
   - 1B Seasonality · 1C Analyst ratings · 1D News / catalysts (context)
2. Dark pool flow → `scripts/fetch_flow.py` (with intraday interpolation)
3. Options flow → `scripts/fetch_options.py`
   - 3B OI changes → `scripts/fetch_oi_changes.py` (REQUIRED)
4. **Edge decision — PASS/FAIL** (FAIL = stop)
5. Structure — convex (R:R < 2:1 = stop)
6. Kelly sizing — enforce 2.5% cap
7. Log → `trade_log.json` (executed) or `docs/status.md` (NO_TRADE)

### Intraday Dark Pool Interpolation

When evaluating during market hours, today's partial data is volume-weighted interpolated. **Always output BOTH actual and interpolated values.**

`progress = minutes since 9:30 ET / 390`. Projected = actual / progress. Blend: `(projected × progress) + (prior_5d_avg × (1 - progress))`. Pace = actual / (avg_prior × progress).

| Progress | Confidence | Prior weight |
|---|---|---|
| 0–25% | VERY_LOW | 75%+ |
| 25–50% | LOW | 50–75% |
| 50–75% | MEDIUM | 25–50% |
| 75–100% | HIGH | <25% |

Use interpolated for edge assessment. LOW/VERY_LOW → re-evaluate after 2 PM ET. Pace>1.2x → real. Actual opposite prior → likely reversal.

### Signal Interpretation

- **P/C Ratio:** >2.0 BEAR | 1.2–2.0 LEAN_BEAR | 0.8–1.2 NEUTRAL | 0.5–0.8 LEAN_BULL | <0.5 BULL
- **Flow Side:** Ask-dominant = buying | Bid-dominant = selling
- **Analyst Buy%:** ≥70% BULL | 50–69% LEAN_BULL | 30–49% LEAN_BEAR | <30% BEAR
- **Seasonality:** >60% FAVORABLE | 50–60% NEUTRAL | <50% UNFAVORABLE

> Seasonality / ratings = context, not gates. Strong flow overrides weak seasonality.

### Seasonality Fallback
UW → EquityClock Vision (Claude Haiku) → Cache (`data/seasonality_cache/{TICKER}.json`). Route: `web/app/api/ticker/seasonality/route.ts`. Key resolution: `resolveApiKey()` checks `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`.

---

## Reports — Mandatory at Milestone 5

| Report | Template | Output |
|---|---|---|
| Trade Spec | `.pi/skills/html-report/trade-specification-template.html` | `reports/{ticker}-evaluation-{YYYY-MM-DD}.html` |
| P&L | `.pi/skills/html-report/pnl-template.html` | `reports/pnl-{TICKER}-{YYYY-MM-DD}.html` |
| Share PnL Card | `next/og` (Satori), 1200x630 PNG | `web/app/api/share/pnl/route.tsx` |

Reference: `reports/goog-evaluation-2026-03-04.html`. Trade Spec sections: Header+gates, Summary Metrics, Milestone pass/fail, Dark Pool, Options Flow, Context, Structure & Kelly, Trade Spec, Thesis & Risk, Four Gates table.

`Return on Risk = P&L / Capital at Risk`

---

## Commands

| Command | Action |
|---|---|
| `scan` / `discover` | Watchlist scan / market-wide flow |
| `evaluate [TICKER]` | Full 7-milestone eval |
| `portfolio` / `sync` | Positions / pull from IB |
| `blotter` / `blotter-history` | Today / historical |
| `leap-scan` / `garch-convergence` / `seasonal` | IV mispricing / GARCH / seasonality |
| `analyst-ratings [TICKERS]` | Ratings + targets |
| `vcg-scan` / `cri-scan` / `gex-scan` | Vol-credit gap / Crash Risk Index / Gamma |
| `menthorq-{cta,dashboard,screener,forex,summary,quin}` | MenthorQ tools |

---

## Critical Data Files

| File | Purpose |
|---|---|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** trade journal |
| `data/watchlist.json` | Surveillance tickers |
| `data/replica.db` | Turso embedded replica (gitignored) |
| `data/tag_taxonomy.json` | Auto-growing UPPERCASE tag list (force-tracked) |
| `data/{vcg,gex}.json` | Scan caches |
| `data/price_history_cache/` | Auto-pruned at 500 |

---

## Startup Checklist

- [ ] `scripts/cloud.sh` (default) or `scripts/local.sh`
- [ ] `curl http://localhost:8321/health` → `ib_gateway.port_listening: true`
- [ ] Reconciliation, exit orders, CRI scan auto-running
- [ ] Check market hours: `TZ=America/New_York date +"%A %H:%M"` (9:30–16:00 ET, Mon–Fri)

## Output Discipline

- Always `signal → structure → Kelly math → decision`
- State probabilities; flag uncertainty
- Failing gate = stop, name the gate
- **Never rationalize a bad trade**