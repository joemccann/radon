# RADON — CLAUDE.md

## ⛔ Mandatory Rules

1. **Be concise.** No preamble, no filler.
2. **E2E browser verification for ALL UI work.** Primary: `chrome-cdp`. Fallback: Playwright (`web/playwright.config.ts`). No UI change done until visually confirmed. Don't assume code changes produce the expected visual result — verify rendered output in the browser before committing.
3. **Red/green TDD for ALL code.** Failing test → fix → green → refactor. Unit: Vitest, E2E: chrome-cdp/Playwright.
4. **95% test coverage target.** Every change includes corresponding tests.
5. **API keys** in `.env` files (see Credentials below). Fallback: `~/.zshrc`.
6. **Options structure reference:** `docs/options-structures.json` + `docs/options-structures.md` — 58 structures, guard decisions, P&L attribution labels. Use for order entry, structure classification, and naked short guard logic.

## Combo / BAG Order Guardrails

1. **Never map combo `Order.action` from debit vs credit.**
   - In IB, combo leg actions define the intended structure.
   - A `SELL` BAG envelope reverses the legs.
   - For entry/open chain combos, keep the envelope on `BUY` and preserve per-leg actions.
2. **When the order-builder structure changes, clear stale top-level manual net pricing.**
   - Single-leg → combo transitions must invalidate the previous manual limit price.
   - Recompute the limit field from the normalized combo quote for the current structure.
3. **Required regressions for combo-entry bugs:**
   - unit test for combo action/ratio/net-price semantics
   - browser test for displayed combo net price and submitted payload
4. **Trace the full path before fixing:**
   - chain builder → `/api/orders/place` → FastAPI bridge → `scripts/ib_place_order.py`
   - verify whether the bug is UI state, payload semantics, or IB combo behavior before patching

## Identity

**Radon** — market structure reconstruction system. Surfaces convex opportunities from dark pool/OTC flow, vol surfaces, cross-asset positioning. Detects institutional positioning, constructs convex options structures, sizes with fractional Kelly. **Flow signal or nothing.**

Brand spec: `docs/brand-identity.md`

## ⛔ Four Gates — Mandatory, Sequential, No Exceptions

```
GATE 1 — CONVEXITY      : Potential gain ≥ 2× potential loss. Defined-risk only (long options, verticals).
GATE 2 — EDGE           : Specific, data-backed dark pool/OTC signal that hasn't moved price yet.
GATE 3 — RISK MGMT      : Fractional Kelly sizing. Hard cap: 2.5% of bankroll per position.
GATE 4 — NO NAKED SHORTS: Never naked short stock, calls, futures, or bonds. Every short call must be fully covered by long shares (1 contract = 100 shares). Violation = immediate cancel.
```

**Any gate fails → stop. No rationalization.**

## Data Source Priority

1. Interactive Brokers (TWS/Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Yahoo Finance — fallback only
4. Web scrape — last resort

**Never skip to Yahoo/web without trying IB → UW first.**

**Clients:** `scripts/clients/` — `IBClient`, `UWClient`, `MenthorQClient`. Legacy `scripts/utils/{ib_connection,uw_api}.py` preserved; new code uses clients.

**Credentials:**

| File | Loader | Contains |
|------|--------|----------|
| `.env` (root) | `python-dotenv` | `MENTHORQ_USER`, `MENTHORQ_PASS`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `ALLOWED_USER_IDS` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` |

## Market Hours

```bash
TZ=America/New_York date +"%A %H:%M"   # 9:30–16:00 ET, Mon–Fri
```

- **Open**: Fetch fresh. Cache TTL: flow 5min, ratings 15min.
- **Closed**: Use latest. Flag stale data.

### CRI/Regime Staleness

`/api/regime` triggers `cri_scan.py` during market hours only. Logic: `web/lib/criStaleness.ts` (single source of truth). Tests: `web/tests/regime-cri-staleness.test.ts`.

| Condition | Stale? | Action |
|-----------|--------|--------|
| `data.date !== today (ET)` | YES | Background scan |
| `market_open + mtime > 60s` | YES | Background scan |
| `market_open === false + date = today` | NO | Serve cached EOD |

### VCG (Volatility-Credit Gap) Tab

Tabbed into `/regime` page alongside CRI. Detects divergence between vol complex (VIX/VVIX) and credit markets (HYG).

| Component | File |
|-----------|------|
| Hook | `web/lib/useVcg.ts` (`VcgData` type, adaptive polling) |
| Staleness | `web/lib/vcgStaleness.ts` (anchored to `scan_time` age) |
| API route | `web/app/api/vcg/route.ts` (GET cached + SWR) |
| Panel | `web/components/VcgPanel.tsx` |
| Scanner | `scripts/vcg_scan.py` (20-session history) |
| Share | `scripts/generate_vcg_share.py` (4 cards + tweet) |
| FastAPI | `POST /vcg/scan` (60s cooldown), `POST /vcg/share` |
| Cache | `data/vcg.json` |

**VCG-R thresholds:** RO = VIX > 28 + VCG > 2.5 + sign_ok. EDR = VIX > 25 + VCG 2.0–2.5. BOUNCE = VCG < -3.5. VVIX is severity amplifier (Tier 1/2/3), not a gate. HDR removed. Credit 5d gate removed. VCG adj replaces vcg_div.

### RegimePanel Market-Closed Rules

When `market_open === false`:
- Use `data.vix`/`data.vvix`/`data.spy` only (never WS `last`)
- `activeCorr` = `data.cor1m` (not rebuilt from sector ETFs)
- `liveCri` / `intradayRvol` = `null` (use `data.cri` / `data.realized_vol`)
- Don't update VIX/VVIX timestamps
- COR1M badge = DAILY

Tests: `regime-market-closed-values.test.ts`, `regime-market-closed-eod.spec.ts`, `regime-cor1m.spec.ts`

### RegimePanel Day Change (Market Open)

| Metric | Source | Display |
|--------|--------|---------|
| VIX/VVIX/SPY | WS `last` vs `close` | `+1.50 (+6.25%) ↑` |
| RVOL | `intradayRvol - data.realized_vol` | `-0.01% intraday ↓` |
| COR1M | `data.cor1m_5d_change` (always visible) | `+6.88 pts 5d chg ↑` |

**Tests**: `web/tests/regime-market-closed-values.test.ts`, `web/e2e/regime-market-closed-eod.spec.ts`, `web/e2e/regime-cor1m.spec.ts`

### RegimePanel Day Change Indicators

During market hours (`market_open === true`), the regime strip shows day change for live metrics:

| Metric | Component | Source | Display |
|--------|-----------|--------|---------|
| VIX | `DayChange` | WS `last` vs WS `close` | `+1.50 (+6.25%) ↑` |
| VVIX | `DayChange` | WS `last` vs WS `close` | `-5.00 (-4.35%) ↓` |
| SPY | `DayChange` | WS `last` vs WS `close` | `$+0.47 (+0.07%) ↑` |
| RVOL | `PointChange` | `intradayRvol - data.realized_vol` | `-0.01% intraday ↓` |
| COR1M | strip value from WS `last` when available, otherwise `data.cor1m`; `PointChange` remains `data.cor1m_5d_change` | `37.25` + `-0.50 pts 5d chg ↓` |

**Arrow placement**: Arrow icon is always to the **right** of the change text (not left, not above). Uses `display: flex` with `gap: 4px` in `.regime-strip-day-chg`.

**Tests**: `web/tests/regime-day-change.test.ts` (12 unit), `web/e2e/regime-day-change.spec.ts` (3 E2E)

### Regime History Charts

Two D3 charts, 20 sessions. Left: VIX (`#05AD98`) + VVIX (`#8B5CF6`), dual Y. Right: RVOL (`#F5A623`) + COR1M (`#D946A8`), dual Y. Height 440px. Component: `CriHistoryChart.tsx`.

### Portfolio Table Arrows

Price arrows in `PositionTable.tsx`/`WorkspaceSections.tsx`: `td.last-price-cell { white-space: nowrap }`, `.price-trend-icon { margin-left: 4px }`.

### Options Chain Sticky Header

`OptionsChainTab.tsx` — three required CSS rules:
1. `background: var(--bg-panel-raised)` on `.chain-header` + `.chain-side-label`
2. `position: sticky; top: 0` / `top: 24px`
3. `.chain-grid thead { position: relative; z-index: 10 }`

All three required or overlap bug returns. Tests: `chain-sticky-header.test.ts` (8).

## Exposure Delta Sign Rule

`rawDelta = sign * lp.delta` where `sign = -1` for SHORT. LONG Call → +, SHORT Call → −, LONG Put → −, SHORT Put → +. Impl: `web/lib/exposureBreakdown.ts`. Tests: `exposure-breakdown.test.ts` (3).

## FastAPI Server Architecture

Next.js routes call FastAPI (`localhost:8321`) via `radonFetch()` (`web/lib/radonApi.ts`). No `spawn()`.

### Three-Service Dev Stack (`npm run dev`)

| Service | Port |
|---------|------|
| Next.js | 3000 |
| IB WS relay | 8765 |
| FastAPI | 8321 |

### FastAPI Files (`scripts/api/`)

| File | Purpose |
|------|---------|
| `server.py` | 26 endpoints, CORS, Clerk JWT auth middleware, IB pool, health, auto-restart. `POST /performance/background` = fire-and-forget, 202, dedup |
| `auth.py` | Clerk JWT verification — JWKS validation, single-tenant allowlist (`ALLOWED_USER_IDS`), graceful bypass when unconfigured |
| `ws_ticket.py` | Short-lived single-use WS tickets (30s TTL) — avoids passing JWTs in WebSocket URLs |
| `ib_pool.py` | Role-based IB pool (sync/orders/data), auto-reconnect |
| `ib_gateway.py` | Health check + auto-restart via IBC launchd. Detects CLOSE_WAIT (upstream dead) |
| `subprocess.py` | Async `run_script()`, `run_module()` — uses `sys.executable` (not `python3`) to match server interpreter |

### Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| FastAPI + IB up | Normal |
| FastAPI up, IB down | Auto-restart Gateway, retry once, else 503 + cached |
| FastAPI up, IB CLOSE_WAIT | Detected at startup + script errors; auto-restart + kill lingering processes |
| FastAPI down | Cached from disk, `is_stale: true` |

No spawn fallback. Always try FastAPI first.

### Authentication (Clerk)

All FastAPI routes are protected by Clerk JWT middleware. Next.js routes are protected by Clerk middleware (`web/middleware.ts`). WebSocket connections use a ticket-based flow to avoid leaking JWTs in URLs.

| Component | File | Purpose |
|-----------|------|---------|
| FastAPI auth middleware | `scripts/api/auth.py` | Validates Clerk JWTs via JWKS, enforces `ALLOWED_USER_IDS` allowlist |
| FastAPI auth dependency | `scripts/api/auth.py` | `verify_clerk_jwt` — used by `/ws-ticket` endpoint via `Depends()` |
| WS ticket service | `scripts/api/ws_ticket.py` | Issues 30s single-use tickets for WS auth |
| WS ticket proxy | `web/app/api/ib/ws-ticket/route.ts` | Next.js route proxies to FastAPI (same-origin for browser) |
| Next.js middleware | `web/middleware.ts` | Clerk `auth.protect()` on all routes except public share pages |
| WS ticket client | `web/lib/wsTicket.ts` | Browser calls `/api/ib/ws-ticket` (same-origin) before WS connect |
| `radonApi.ts` | `web/lib/radonApi.ts` | Attaches Clerk Bearer token to all `radonFetch()` calls |

**Auth-exempt paths:** `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`.

**Localhost bypass:** Both the auth middleware and `verify_clerk_jwt` dependency skip validation for requests from `127.0.0.1`/`::1` (server-to-server). The WS relay also skips ticket validation for localhost connections. This enables local dev without Clerk sign-in.

**Graceful fallback:** When `CLERK_JWKS_URL` is not set, auth middleware passes all requests through (local dev without Clerk).

**Public share routes:** `/api/regime/share`, `/api/vcg/share`, `/api/internals/share`, `/api/menthorq/cta/share` are public (no auth).

**Tests:** `scripts/api/tests/test_auth.py` (Python), `web/tests/auth-integration.test.ts`, `web/tests/ws-ticket-local.test.ts` (TS).

### IB Gateway Auto-Recovery

Startup: check port 4001 + CLOSE_WAIT detection (`lsof`), restart if needed, poll 45s. Runtime: IB subprocess errors trigger Gateway health check FIRST — only restart if port not listening or CLOSE_WAIT detected. Subprocess failures from client ID collisions, VOL errors, or transient timeouts do NOT trigger restart. Restart script snapshots pre-existing PIDs and only force-kills those that survived SIGTERM (prevents killing newly-spawned processes). Manual: `POST /ib/restart`. Health: `GET /health` returns `upstream_dead: true` when CLOSE_WAIT detected.

### Health Check

```bash
curl http://localhost:8321/health
# Returns: ib_gateway, ib_pool (sync/orders/data), uw
```

## Cancel / Modify Failure Propagation

1. **Cancel and modify MUST use subprocess with original clientId.**
   - IB scopes both `cancelOrder` and `placeOrder` (modify) by clientId.
   - Master client (clientId=0) can SEE all orders via `reqAllOpenOrders()` but CANNOT cancel/modify them (Error 10147 for cancel, Error 103 for modify).
   - The subprocess (`ib_order_manage.py`) detects the original clientId from `trade.order.clientId` and reconnects as that client before executing.
   - Pool-based cancel/modify does NOT work because orders are placed by subprocess clientIds (range 20-49), not pool clientIds (0-2).
2. **Clear VOL fields before modify.**
   - IB open-order snapshots may contain stale `volatility` and `volatilityType` values. Re-submitting on a non-VOL order causes Error 321.
   - Reset both to IB sentinel values (`1.7976931348623157e+308` and `2147483647`) before `placeOrder`.
3. **Do not trust the original IB `Trade` object as the only confirmation source.**
   - IB can confirm a cancel by removing the order from refreshed open orders without mutating the original `Trade` instance in place.
   - Cancel/modify flows must confirm against a refreshed open-order snapshot, not just the stale object reference.
4. **Treat disappearance after cancel as success.**
   - If the target order no longer appears in refreshed open orders after the cancel request, that is a valid IB acknowledgement.
5. **Preserve the real upstream error detail end to end.**
   - If a subprocess script exits non-zero with JSON on stdout, FastAPI must surface the human-readable `detail` / `message` / `error` field.
   - Next order routes must preserve upstream HTTP status/detail instead of collapsing provider failures to generic `500`s.
6. **Required regressions for cancel/modify bugs:**
   - Python/unit coverage for refreshed open-order confirmation semantics
   - route coverage for upstream status/detail propagation
   - browser coverage for the visible toast/error state

## Naked Short Protection (Gate 4)

**Hard rule — no exceptions.** The system must never allow naked short exposure.

| Scenario | Rule | Action |
|----------|------|--------|
| SELL stock, no long shares | Naked short stock | BLOCK |
| SELL call, no long shares or long calls | Naked short call | BLOCK |
| SELL call, long calls at same expiry (any strike) | Vertical spread | ALLOW |
| SELL N call contracts, shares < N × 100 | Short a tail | BLOCK |
| SELL put (cash-secured) | Defined risk | ALLOW |
| Vertical spread (BUY C + SELL C) | Long call covers short | ALLOW |
| Short risk reversal (SELL C + BUY P) | Naked short call — long put does not cover | BLOCK |
| 1x2 ratio spread (BUY 1C + SELL 2C) | 1 uncovered short call | BLOCK (unless stock covers) |
| Jade Lizard / Seagull (BUY C + SELL C + SELL P) | Call spread covers short call; put is cash-secured | ALLOW |
| Combo closing (action=SELL) | Reduces exposure | ALLOW |
| BUY anything | No short exposure | ALLOW |

**Enforcement layers:**
1. **UI pre-submission** — `checkNakedShortRisk()` in `OrderTab.tsx` blocks form submission
2. **API gate** — `orders/place/route.ts` returns 403 if guard fails
3. **Post-sync audit** — `naked_short_audit.py` runs after every `ib_sync`, cancels violating open orders

**Combo check design**: IB BAG orders always use `action=BUY` envelope. Guard inspects leg-level `right` and `action` fields. `sellCallRatio - buyCallRatio` = uncovered short calls. Checked before the BUY early-return.

**Implementation**: `web/lib/nakedShortGuard.ts` (shared guard), `scripts/naked_short_audit.py` (audit + cancel)
**Tests**: `web/tests/naked-short-guard.test.ts` (21 tests), `scripts/tests/test_naked_short_audit.py`

---

## High-Throughput Architecture

500+ symbols, <500ms signal-to-order.

**Parallel scanning:** `scanner.py` (15 workers), `discover.py` (10 workers). `--workers N` CLI. `UWRateLimitError` skips ticker, doesn't crash batch.

**Atomic state:** `scripts/utils/atomic_io.py` — `atomic_save()` (temp + `os.replace()` + SHA-256), `verified_load()`. Writers: `ib_sync.py`. Readers: reconcile, flow, free_trade, performance, leap scanner.

**Batched WS relay:** `ib_realtime_server.js` — per-client last-write-wins, 100ms flush. 5000 msg/s → 10 batched/s. Initial state immediate.

**Stale tick detection:** Relay tracks `lastTickTimestamp`, checks every 30s during market hours. No ticks for 45s → auto-restart Gateway (120s cooldown).

### WebSocket Connection State Machine (`usePrices.ts`)

`idle → connecting → open → closed`. Key design:
- `connStateRef` (ref) — `connect()` idempotent
- `socketGenRef` — ignores stale socket events
- Diff-based subscribe/unsubscribe over existing connection
- Callback refs eliminate stale closures
- Exponential backoff: `min(1000 * 2^n, 30000) + jitter`, max 10 attempts

Tests: `use-prices-ws-stability.test.ts` (25), `ws-connection-stability.spec.ts` (4).

**Vectorized math:** `kelly_size_batch()` (NumPy), `portfolio_greeks_vectorized()`. Cross-validated with TS `approxDelta()` to 10⁻¹².

**Resilient IBClient** (`scripts/clients/ib_client.py`): Subscription tracking, disconnect recovery (5 attempts, 2ⁿs capped 30s), pacing violations (162/366: 10s backoff, 3 retries), invalid contracts (200/354: no retry, added to `_failed_contracts`).

**Incremental sync:** `scripts/utils/incremental_sync.py` — diff by `(ticker, expiry)` + contract count, skip full sync when unchanged.

### Performance Page Optimization

`scripts/portfolio_performance.py` — two-phase parallel fetch:
- **Phase A** (sequential): IB stock history + cache checks
- **Phase B** (ThreadPoolExecutor): UW/Yahoo fallbacks + option history. Per-worker `UWClient`.

`PERF_FETCH_WORKERS` env (default 8, clamped 1-20). Disk cache: `data/price_history_cache/`, SHA-256 filenames, TTL 15min/24h. SWR: cached → background rebuild via `POST /performance/background`. Cold start blocks on sync `POST /performance` (180s). Tests: 211 total (160 Python + 51 TS).

## Evaluation — 7 Milestones (Stop on Failure)

1. Validate ticker → `scripts/fetch_ticker.py`
1B. Seasonality (context) | 1C. Analyst ratings (context) | 1D. News/catalysts (context)
2. Dark pool flow → `scripts/fetch_flow.py` (with intraday interpolation)
3. Options flow → `scripts/fetch_options.py`
3B. OI changes → `scripts/fetch_oi_changes.py` (REQUIRED)
4. Edge decision — PASS/FAIL (FAIL = stop)
5. Structure — convex position (R:R < 2:1 = stop)
6. Kelly sizing — enforce 2.5% cap
7. Log → `trade_log.json` or `docs/status.md`

## Intraday Dark Pool Interpolation

When evaluating during market hours, today's partial data is volume-weighted interpolated to estimate full-day values. **Always output BOTH actual and interpolated values.**

### Why Interpolation is Required

Comparing today's partial data (e.g., 45% of day) to yesterday's full-day data is misleading. A "55% buy ratio" at noon could become 75% by close, or could be masking active distribution.

### Calculation Method

**Step 1: Trading Day Progress**
```
Progress = Minutes Since 9:30 AM ET / 390 minutes
```

**Step 2: Project Today's Volume**
```
Projected Volume = Actual Volume / Progress
Projected Buy = Actual Buy Volume / Progress
Projected Sell = Actual Sell Volume / Progress
```

**Step 3: Blend with Prior Pattern**
```
Actual Weight = Progress (e.g., 0.45 at noon)
Prior Weight = 1 - Progress (e.g., 0.55)

Prior Avg Buy Ratio = Mean of prior 5 days' buy ratios
Blended Ratio = (Today's Projected Ratio × Actual Weight) + (Prior Avg × Prior Weight)
```

**Step 4: Recalculate Aggregate**
Use interpolated today + actual prior days for aggregate strength.

### Confidence Levels

| Progress | Confidence | Blending |
|----------|------------|----------|
| 0-25% | VERY_LOW | 75%+ prior weight |
| 25-50% | LOW | 50-75% prior weight |
| 50-75% | MEDIUM | 25-50% prior weight |
| 75-100% | HIGH | <25% prior weight |

### Volume Pace

```
Expected Volume = Avg Prior Volume × Progress
Volume Pace = Actual Volume / Expected Volume
```

Pace >1.1x = above average (signal more reliable). Pace <0.9x = below average (signal less reliable).

### Output Format (MANDATORY)

Always show both when `is_interpolated: true`:

```
TODAY'S FLOW (45% of trading day)
                      ACTUAL          INTERPOLATED
  Buy Ratio:           25.4%           53.3%
  Direction:          DISTRIBUTION   NEUTRAL
  Strength:            49.3             0.0

AGGREGATE (5-Day)
                      ACTUAL          INTERPOLATED
  Buy Ratio:           70.4%           65.3%
  Strength:            40.7            30.6
```

### Edge Assessment with Interpolation

Use **interpolated values** for edge determination, but flag confidence level:
- LOW/VERY_LOW confidence → recommend re-evaluation after 2 PM ET
- Volume pace >1.2x → signal is real despite partial data
- Today's actual direction opposite to prior pattern → likely reversal, not noise

## Commands

| Command | Action |
|---------|--------|
| `scan` | Watchlist dark pool scan |
| `discover` | Market-wide flow for new candidates |
| `evaluate [TICKER]` | Full 7-milestone eval |
| `portfolio` | Positions, exposure, capacity |
| `journal` | Recent trade log |
| `sync` | Pull live portfolio from IB |
| `blotter` | Today's fills + P&L |
| `blotter-history` | Historical trades (Flex Query) |
| `leap-scan [TICKERS]` | LEAP IV mispricing |
| `garch-convergence [TICKERS]` | Cross-asset GARCH vol divergence |
| `seasonal [TICKERS]` | Monthly seasonality |
| `x-scan [@ACCOUNT]` | X post sentiment |
| `analyst-ratings [TICKERS]` | Ratings + targets |
| `vcg-scan` | Vol-credit gap divergence |
| `cri-scan` | Crash Risk Index (CTA deleveraging) |
| `menthorq-cta` | MenthorQ CTA positioning |
| `menthorq-dashboard [CMD]` | Dashboard image (vol/forex/eod/intraday/futures/cryptos_technical/cryptos_options). `--ticker` for eod/intraday/futures/crypto (16 tickers) |
| `menthorq-screener [CAT] [SLUG]` | Screener (6 categories, 45 sub-screeners) |
| `menthorq-forex` | Forex gamma levels + blindspot (14 pairs) |
| `menthorq-summary [CAT]` | Summary tables (futures: 93 rows, cryptos: 16) |
| `menthorq-quin [PROMPT]` | QUIN AI screener. Presets: `docs/menthorq-prompts.md` |

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/cloud.sh` | Hybrid dev: local services + VPS IB Gateway via Tailscale (default workflow) |
| `scripts/local.sh` | Fully local: stop VPS gateway, start local Docker gateway, launch dev |
| `scripts/api/server.py` | FastAPI — 21 endpoints, IB pool, auto-restart |
| `scripts/api/ib_pool.py` | Role-based IB pool (sync=3, orders=4, data=5) |
| `scripts/api/ib_gateway.py` | IB Gateway health + auto-restart |
| `scripts/api/subprocess.py` | Async subprocess helper |
| `scripts/clients/ib_client.py` | IBClient — orders, quotes, options, fills, flex, resilient reconnect |
| `scripts/clients/uw_client.py` | UWClient — dark pool, flow, chain, ratings, seasonality, 50+ endpoints |
| `scripts/clients/menthorq_client.py` | MenthorQClient — browser automation, dashboards, screeners, CTA |
| `scripts/scanner.py` | Watchlist batch scan (ThreadPoolExecutor) |
| `scripts/discover.py` | Market-wide flow scanner |
| `scripts/kelly.py` | Kelly calc — scalar + vectorized batch |
| `scripts/ib_sync.py` | Sync IB portfolio (atomic writes). Detects: covered calls, verticals, synthetics, risk reversals, straddles, all-long combos |
| `scripts/ib_reconcile.py` | Reconcile fills vs trade_log |
| `scripts/ib_place_order.py` | JSON-in/out order placement (client ID 26) |
| `scripts/ib_order_manage.py` | Cancel/modify open orders |
| `scripts/exit_order_service.py` | Pending exit orders |
| `scripts/portfolio_performance.py` | Parallel price history + performance calc |
| `scripts/cri_scan.py` | Crash Risk Index |
| `scripts/vcg_scan.py` | Vol-Credit Gap scanner (20-session history) |
| `scripts/generate_vcg_share.py` | VCG X share report (4 cards + preview) |
| `scripts/fetch_menthorq_cta.py` | MenthorQ CTA (S3 + Vision) |
| `scripts/fetch_menthorq_dashboard.py` | MenthorQ dashboards (S3/screenshot + Vision) |
| `scripts/ib_realtime_server.js` | WS relay — batched, 100ms flush, ticket-based auth on upgrade |
| `scripts/utils/atomic_io.py` | Atomic JSON save/load + SHA-256 |
| `scripts/utils/vectorized_greeks.py` | NumPy portfolio delta/gamma |
| `scripts/utils/incremental_sync.py` | Diff-based portfolio sync |
| `scripts/utils/price_cache.py` | Price cache — SHA-256 filenames, atomic, TTL, thread-safe prune |
| `scripts/run_cri_scan.sh` | Holiday-aware CRI wrapper for launchd |
| `scripts/monitor_daemon/run.py` | Monitor daemon — fills, exit orders, rebalance, Flex token check |
| `scripts/benchmarks/autoresearch.sh` | Scanner benchmark (timing + metrics) |

## Critical Data Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** trade journal |
| `docs/options-structures.json` | Options structure catalog — 58 structures, guard decisions, bias, risk profile |
| `data/watchlist.json` | Surveillance tickers |
| `data/ticker_cache.json` | Ticker → company cache |
| `data/reconciliation.json` | IB reconciliation |
| `data/seasonality_cache/` | Per-ticker seasonality |
| `data/menthorq_cache/` | CTA + dashboard cache (daily) |
| `data/cri_scheduled/` | Intraday CRI time-series |
| `data/vcg.json` | VCG scan cache (signal, 20-session history) |
| `data/price_history_cache/` | Stock + option price histories (auto-pruned at 500) |

## Seasonality Fallback

UW → EquityClock Vision → Cache. Route: `web/app/api/ticker/seasonality/route.ts`.
1. Cache check (`data/seasonality_cache/{TICKER}.json`)
2. UW API — all 12 months valid → done
3. Missing months → EquityClock chart → Claude Haiku Vision extraction
4. Merge (UW priority), cache as `uw+equityclock`, expires 1st of next month
5. Vision fails → return UW partial

API key: `resolveApiKey()` checks `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY`.

## ⭐ Trade Specification Report — MANDATORY

Required for any eval reaching Milestone 5.

```
Template : .pi/skills/html-report/trade-specification-template.html
Output   : reports/{ticker}-evaluation-{YYYY-MM-DD}.html
Reference: reports/goog-evaluation-2026-03-04.html
```

**10 required sections:** Header + gate status | 6 Summary Metrics | Milestone pass/fail | Dark Pool Flow | Options Flow | Context (seasonality + ratings) | Structure & Kelly | Trade Spec (exact order) | Thesis & Risk | Four Gates table.

Workflow: Complete M1-6 → Generate HTML → User confirmation → Execute via IB → Update `trade_log.json`, `portfolio.json`, `docs/status.md`.

## P&L Report

```
Template: .pi/skills/html-report/pnl-template.html
Output:   reports/pnl-{TICKER}-{YYYY-MM-DD}.html
Return on Risk = P&L / Capital at Risk (debit=net debit, credit=width−credit, long=premium)
```

## Share PnL Card

1200x630 PNG via `next/og` (Satori). Route: `web/app/api/share/pnl/route.tsx`. Component: `SharePnlButton.tsx`. Fonts: IBM Plex Mono `.woff` (Satori requires woff, not ttf). Theme: `web/lib/og-theme.ts`.

Wired into Executed Orders + Historical Trades on `/orders`. Position grouping: `groupExecutedOrders()`, `positionGroupShareData()`, `deriveGroupDescription()` in `WorkspaceSections.tsx`. Clipboard: `navigator.clipboard.write()` with `ClipboardItem`.

Tests: `share-pnl.test.ts` (24), `share-pnl.spec.ts` (6).

## Calculations — Correctness Rules

### Credit/Debit Sign Convention

**Preserve the sign throughout the entire display pipeline.** Never use `Math.abs()` or equivalent on option prices/values without explicit approval. Credits must display as negative, debits as positive. This applies to P&L cards, share images, order forms, and all price displays.

### Daily Change %

```
Day Chg % = Daily P&L / |Yesterday's Close Value| × 100
NEVER divide by entry cost.
```

Per-leg: `sign × (last - close) × contracts × 100`. Denominator: `sign × close × contracts × 100`. Impl: `getOptionDailyChg()` in `WorkspaceSections.tsx`. Tests: `daily-chg.test.ts`.

### Spread Net Mid

```
Spread Mid = SUM(sign × (bid + ask) / 2) per leg
```

Via `legPriceKey()` WS bid/ask. Never use underlying for spread orders. Impl: `resolveOrderLastPrice()`.

### Combo Natural Market Bid/Ask

**CRITICAL:** Always use cross-fields for natural market, never `sign * bid` and `sign * ask`.

```
To BUY combo:  pay ASK on BUY legs, receive BID on SELL legs
To SELL combo: receive BID on BUY legs, pay ASK on SELL legs

Example (bull call spread: BUY $200C, SELL $210C):
  $200C: bid=4.50, ask=4.70
  $210C: bid=2.00, ask=2.20
  
  netAsk (cost to open) = 4.70 - 2.00 = 2.70
  netBid (proceeds to close) = 4.50 - 2.20 = 2.30
  mid = 2.50

WRONG (mid-mid):
  netBid = sign*bid = 4.50 - 2.00 = 2.50
  netAsk = sign*ask = 4.70 - 2.20 = 2.50
  Result: bid = ask = mid = 2.50 ❌
```

**Implementations (all use correct algorithm):**
- `computeNetOptionQuote()` in `optionsChainUtils.ts`
- `ComboOrderForm.netPrices` in `OrderTab.tsx`
- `resolveOrderPriceData()` for BAG in `ModifyOrderModal.tsx`

Tests: `order-reliability.test.ts` ("ComboOrderForm net price calculation").

### Total P&L %

```
P&L % = (Market Value - Entry Cost) / |Entry Cost| × 100
```

### Per-Leg P&L (expanded combo rows)

```
Leg P&L = sign × (|MV| − |EC|)   // LONG: MV−EC, SHORT: EC−MV
```

Sum of legs = position P&L. Uses WS price, fallback IB sync. Impl: `LegRow` in `PositionTable.tsx`.

### Price Resolution Priority

| Context | Source |
|---------|--------|
| Stock | `prices[ticker].last` |
| Single-leg option | `prices[optionKey(...)].last` |
| Multi-leg spread | Net from each leg's `prices[legPriceKey(...)]` |
| BAG order last | `resolveOrderLastPrice()` — net mid from legs |
| BAG modify BID/MID/ASK | `resolveOrderPriceData()` in `ModifyOrderModal.tsx` |
| Order form BID/MID/ASK | Same as PriceBar |
| PriceBar in modal | `resolvePriceBar()` — option-level for single-leg, underlying for multi-leg |

**Never show underlying price where user expects option/spread price. Show "---" if unavailable.**

### Position Structure Classification (`ib_sync.py`)

`detect_structure_type()`:

| Structure | Risk Profile |
|-----------|-------------|
| Stock | `equity` |
| Long Call/Put | `defined` |
| Short Call/Put | `undefined` |
| Bull/Bear Spread | `defined` |
| Synthetic Long/Short | `undefined` |
| Risk Reversal | `undefined` |
| Straddle/Strangle (both long) | `defined` |
| Covered Call | `defined` |
| **All-long combo** (no shorts, no stock) | **`defined`** |
| Unrecognized | `complex` → routed to Undefined Risk table |

Tests: `test_covered_call_detection.py` (7), `test_all_long_combo.py` (8), `complex-risk-profile.test.ts` (5).

### IB Combo (BAG) Order Leg Convention

**ComboLeg.action = spread structure, NOT trade direction.** `Order.action` (BUY/SELL) controls open/close; IB reverses legs when SELL.

**Rule:** Always `LONG → BUY`, `SHORT → SELL` in ComboLeg.action regardless of order direction. Never flip — causes double-reversal → IB error 201.

Impl: `ComboOrderForm` (`OrderTab.tsx`), `OrderBuilder` (`OptionsChainTab.tsx`).

### Data Normalization

JSON data files: always `"ticker"`. IB contracts: `"symbol"`. Read defensively: `t.get("ticker") or t.get("symbol")`.

## UW API Quick Reference

```
Base: https://api.unusualwhales.com | Auth: Bearer $UW_TOKEN
```

| Endpoint | Use |
|----------|-----|
| `/api/darkpool/{ticker}` | Dark pool (primary edge) |
| `/api/option-trades/flow-alerts` | Sweeps, blocks |
| `/api/stock/{ticker}/info` | Validation |
| `/api/stock/{ticker}/option-contracts` | Chain |
| `/api/stock/{ticker}/greek-exposure` | GEX |
| `/api/screener/analysts` | Ratings |
| `/api/seasonality/{ticker}/monthly` | Seasonality |
| `/api/shorts/{ticker}/interest-float/v2` | Short interest |

Full spec: `docs/unusual_whales_api.md`

## Signal Interpretation

**P/C Ratio:** >2.0 BEARISH | 1.2–2.0 LEAN_BEAR | 0.8–1.2 NEUTRAL | 0.5–0.8 LEAN_BULL | <0.5 BULLISH
**Flow Side:** Ask-dominant = buying | Bid-dominant = selling
**Analyst Buy%:** ≥70% BULL | 50–69% LEAN_BULL | 30–49% LEAN_BEAR | <30% BEAR
**Discovery Score:** 60–100 Strong | 40–59 Monitor | 20–39 Weak | <20 None
**Seasonality:** >60% FAVORABLE | 50–60% NEUTRAL | <50% UNFAVORABLE

> Seasonality/ratings = context, not gates. Strong flow overrides weak seasonality.

## IB Gateway

Three modes controlled by `IB_GATEWAY_MODE` env var (default: `docker`):

### Docker Mode (Primary)

Image: `ghcr.io/gnzsnz/ib-gateway` (pinned to digest). Config: `docker/ib-gateway/`.

| Command | Action |
|---------|--------|
| `scripts/docker_ib_gateway.sh start` | Start (validates secrets, checks launchd not running) |
| `scripts/docker_ib_gateway.sh stop` | Stop |
| `scripts/docker_ib_gateway.sh restart` | Restart |
| `scripts/docker_ib_gateway.sh status` | Status |
| `npm run ib:start` (from web/) | Convenience alias |

Docker handles reliability via `restart: unless-stopped` + healthcheck. `READ_ONLY_API=no` (Radon places orders). Password via Docker secrets (`docker/ib-gateway/secrets/ib_password.txt`, chmod 600).

### Cloud Mode (Tailscale)

Gateway runs on a Hetzner VM accessible via Tailscale MagicDNS at `ib-gateway:4001`. Set `IB_GATEWAY_HOST=ib-gateway` and `IB_GATEWAY_MODE=cloud` in root `.env`. Both Python (`ib_client.py` loads dotenv at import) and Node (`ib_realtime_server.js` loads dotenv at startup) read this automatically. All scripts import `DEFAULT_HOST` from `ib_client` — no hardcoded `127.0.0.1` in IB connection code.

**Cloud mode behavior:** Health check = TCP port probe only. No local restart, no CLOSE_WAIT detection, no docker/launchd lifecycle management. `POST /ib/restart` returns 503 with "manage it on the remote host". Stale tick detection in the WS relay disconnects and reconnects (no restart attempt).

**Management commands** (local Mac via Tailscale SSH, or directly on VPS):

| Command | Action |
|---------|--------|
| `ibstart` | Start container, wait for port 4001 |
| `ibstop` | Stop and remove container |
| `ibrestart` | Restart, wait for port 4001 |
| `ibstatus` | Container state, port, connections |
| `iblogs` | Last 50 lines (`iblogs 100`) |
| `ibhealth` | Docker healthcheck status |

VPS script: `/usr/local/bin/ibgw`. Local: `ibgw()` in `~/.zshrc` wraps SSH.

### Docker Mode (Primary)

Image: `ghcr.io/gnzsnz/ib-gateway` (pinned to digest). Config: `docker/ib-gateway/`.

| Command | Action |
|---------|--------|
| `scripts/docker_ib_gateway.sh start` | Start (validates secrets, checks launchd not running) |
| `scripts/docker_ib_gateway.sh stop` | Stop |
| `scripts/docker_ib_gateway.sh restart` | Restart |
| `scripts/docker_ib_gateway.sh status` | Status |
| `npm run ib:start` (from web/) | Convenience alias |

Docker handles reliability via `restart: unless-stopped` + healthcheck. `READ_ONLY_API=no` (Radon places orders). Password via Docker secrets (`docker/ib-gateway/secrets/ib_password.txt`, chmod 600).

### LaunchD Mode (Legacy Fallback)

Global service: `local.ibc-gateway` (shared with market-data-warehouse). Install: `~/ibc-install/`, config: `~/ibc/`. Credentials in macOS Keychain.

| Command | Action |
|---------|--------|
| `~/ibc/bin/start-secure-ibc-service.sh` | Start |
| `~/ibc/bin/stop-secure-ibc-service.sh` | Stop |
| `~/ibc/bin/restart-secure-ibc-service.sh` | Restart |
| `~/ibc/bin/status-secure-ibc-service.sh` | Status |

**Lifecycle:** Mon-Fri 00:00 start → 2FA approve on IBKR Mobile → 11:58 PM daily restart (no 2FA) → Sunday 07:05 cold restart (2FA).

### Gateway Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `IB_GATEWAY_MODE` | `docker` | `docker`, `cloud` (remote, no restart), or `launchd` |
| `IB_GATEWAY_HOST` | `127.0.0.1` | Gateway host (`ib-gateway` for cloud mode) |
| `IB_GATEWAY_PORT` | `4001` | Gateway port |

### Ports

| Port | Service |
|------|---------|
| 3000 | Next.js |
| 8321 | FastAPI |
| 8765 | IB WS relay |
| 4001 | IB Gateway Live |
| 4002 | IB Gateway Paper |
| 7496/7497 | TWS Live/Paper |
| 7462 | IBC Command Server |

### Client ID Ranges

| Range | Usage |
|-------|-------|
| 0-9 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10-19 | WS relay (rotates on conflict) |
| 20-49 | Subprocess scripts (`client_id="auto"`) |
| 50-69 | Scanners (CRI/VCG rotating) |
| 70-89 | Daemons (fill=70, exit=71) |
| 90-99 | CLI/standalone |

**Rule:** On-demand scripts MUST use `client_id="auto"` (range 20-49). Never hardcode — pool holds persistent connections. Tests: `test_client_id_allocation.py` (17).

### Remote Access (Phase 1)

macOS SSH over Tailscale. Requires: Tailscale on Mac + iPhone, macOS Remote Login, SSH client (Termius/Blink/Prompt). Runbook: `docs/ibc-remote-access.md`.

IB error `10358` = Reuters inactive → auto-fallback.

### Log Rotation

Two layers prevent log bloat in `logs/`:

| Layer | Mechanism | Config |
|-------|-----------|--------|
| Python | `RotatingFileHandler` in `scripts/monitor_daemon/run.py` | 10MB max, 2 compressed backups |
| System | `newsyslog` via `/etc/newsyslog.d/radon.conf` | 10MB max, 2 bzip2 backups, covers all `logs/*.log` |

Python rotation handles `monitor-daemon.log` (the largest writer). System newsyslog catches launchd stdout/stderr (`*.out.log`, `*.err.log`) that Python doesn't control.

## Cloud Deployment Notes

When deployed on the Hetzner VPS via radon-cloud:

- **FastAPI auth**: Clerk JWT validated on all external requests. Localhost requests (Next.js → FastAPI on 127.0.0.1:8321) bypass auth — port is never public.
- **Clerk middleware**: API routes (`/api/*`) are excluded from Next.js Clerk middleware `protect()`. Server-side fetches from pages don't carry session cookies, so middleware must not block them. Auth is handled by FastAPI for external API access.
- **`NEXT_PUBLIC_*` env vars**: Baked at build time. After changing `.env`, rebuild Next.js: `cd web && npm run build`
- **Root `node_modules`**: The root `package.json` has shared deps (`@sinclair/typebox`) used by `lib/tools/`. Must be installed before `web/` build.
- **`requirements.txt`**: Includes `cryptography` (needed by PyJWT for RS256), `fastapi`, `uvicorn`, `python-dotenv`, `numpy`, `pytz`, `playwright`.
- **`@sinclair/typebox`**: Pinned to exact `0.34.48` in both root and web `package.json` to prevent version mismatch build failures.
- **Production Clerk**: Uses different user IDs than dev. `ALLOWED_USER_IDS` must be updated. Requires own Google/GitHub OAuth credentials and 5 CNAME DNS records.

## Output Rules

- Always: `signal → structure → Kelly math → decision`
- State probabilities; flag uncertainty
- Failing gate = immediate stop, name the gate
- **Never rationalize a bad trade**
- Executed → `trade_log.json` | NO_TRADE → `docs/status.md`

## Startup Checklist

- [ ] `scripts/cloud.sh` (default — local dev services + VPS IB Gateway via Tailscale) — OR `scripts/local.sh` (fully local with Docker gateway)
- [ ] If local mode: approve 2FA on IBKR mobile for cold start
- [ ] `curl http://localhost:8321/health` — verify `ib_gateway.port_listening: true`
- [ ] Reconciliation auto-runs → `data/reconciliation.json`
- [ ] Exit order service auto-runs (PENDING_MANUAL)
- [ ] CRI scan service running (30-min intervals)
- [ ] X scan if >12h stale
- [ ] Check market hours

## ⛔ Brand Identity — Mandatory for UI Work

Full spec: `docs/brand-identity.md` + `brand/radon-brand-system.md`. Tokens: `brand/radon-design-tokens.json`. Tailwind: `brand/radon-tailwind-theme.ts`. Kit: `/kit` route. Logo: `brand/radon-app-icon.svg`.

**System name:** Radon (not "Convex Scavenger" in UI).

**Typography:** Inter (UI) + IBM Plex Mono (numeric tables, telemetry) + Söhne (display only).

**Radon Spectrum:**

| Token | Hex | Meaning |
|-------|-----|---------|
| `signal.core` | `#05AD98` | Core accent |
| `signal.strong` | `#0FCFB5` | High-confidence |
| `signal.deep` | `#048A7A` | Deep data / selected |
| `warn` | `#F5A623` | Caution |
| `fault` | `#E85D6C` | Feed fault |
| `violet.extreme` | `#8B5CF6` | Extreme dislocation |
| `magenta.dislocation` | `#D946A8` | Structural dislocation |
| `neutral` | `#94a3b8` | Neutral |

**Surfaces (dark):** canvas `#0a0f14` | panel `#0f1519` | raised `#151c22` | grid `#1e293b`
**Surfaces (light):** canvas `#FFFFFF` | panel `#FFFFFF` | raised `#F1F5F9` | grid `#BBBFBF`

**CSS variables:** `--bg-base`, `--bg-panel`, `--bg-panel-raised`, `--bg-hover`, `--border-dim`, `--line-grid`, `--signal-core`, `--signal-strong`, `--signal-deep`, `--dislocation`, `--extreme`, `--fault`, `--neutral`, `--text-secondary` — all auto-adapt dark/light in `globals.css`.

**Non-negotiable:**
- 4px max border-radius on panels (badges: 999px capsule)
- All colors via tokens — no raw hex
- Mono for machine, sans for product — never reversed
- Empty states describe measurement condition, not generic placeholders
- Voice: precise, calm, scientific — no hype/emojis
- Grid: 8px base, 4px micro, 16px gutters, 32px section gaps
- No decorative elements (glassmorphism, gradients, soft shadows)
- Panels = instrument modules (hairline borders, matte, device-label headers)
- Signal semantics: Baseline → Emerging → Clear → Strong → Dislocated → Extreme
