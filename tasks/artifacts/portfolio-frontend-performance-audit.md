# `/portfolio` frontend performance audit

- Scope: read-only trace of the Next.js navigation, client render path, API initiation, realtime connections, polling, fallbacks, service worker, and production client assets.
- Repository: `web/` at `88514b9b` on 2026-08-25.
- Measurement boundary: the local production harness could validate request initiation and asset transfer sizes, but its in-route API requests were rejected by local Clerk middleware. No loopback API latency is presented as production latency.
- Contract retained: live disk-backed GET handlers remain `dynamic = "force-dynamic"`; their clients remain `cache: "no-store"`. Recommendations below use application-memory snapshots, server-side bounded caches/single-flight, smaller payloads, and deferred work instead of browser HTTP caching of live portfolio data.

## Executive priority

| Priority | Finding | Evidence | Expected effect | Surgical direction |
|---|---|---|---|---|
| P0 | The initial cached portfolio read can be superseded by an immediate live sync POST. | `usePortfolio` starts GET on mount, while null `lastSync` is classified stale and `useAutoSyncOnStale` calls POST. POST increments `dataGenerationRef` before the fetch, so a faster GET response is discarded. A controlled production-build trace observed GET start 146 ms, POST start 155 ms, GET finish 602 ms, POST finish 3,158 ms, and rows only at 3,476 ms. See `lib/usePortfolio.ts:50-105,125-185`, `lib/useSnapshotStaleness.ts:27-35,73-76`, `lib/useAutoSyncOnStale.ts:99-123`. | This is the reproduced deterioration trigger: first useful portfolio data waited for the live upstream sync rather than the cached snapshot. POST has a 42 s client deadline; the route gives its upstream 35 s. | Do not auto-sync while the initial cached GET is pending. Paint the GET result before starting producer refresh, then preserve true-blackout recovery by syncing if the completed GET has no usable snapshot or reports a genuinely stale timestamp. Add a deferred GET/POST regression test. |
| P0 | The portfolio loads a route-agnostic 1.20 MB raw client chunk. | `WorkspaceShell` dynamically imports `WorkspaceSections`; that file is 4,193 lines, 177,495 source bytes, has 69 static imports, and contains every workspace. Its production chunk is 1,198,604 raw / 316,954 gzip / 248,220 brotli bytes. `components/WorkspaceShell.tsx:39-41,563-575`; `components/WorkspaceSections.tsx:1280-1449,4148-4152,4193`. | Adds a large parse/compile/evaluate task before the portfolio tables settle, despite most code being unrelated to `/portfolio`. | Split sections by route or dynamically import the portfolio section directly. Move scanner, journal, admin, profile, options, workflow, and performance imports behind their own route chunks. Add a per-route compressed-JS budget. |
| P1 | Sidebar default prefetch starts a broad RSC request storm during the cold load. | Links have no `prefetch` override at `components/Sidebar.tsx:112-159,178-197`. The production browser trace initiated 21 RSC prefetch GETs across two waves for `/cta`, `/regime/cri`, `/options`, `/profile`, `/flow-analysis`, `/scanner`, `/watchlist`, `/orders`, `/performance`, `/dashboard`, and `/portfolio`. | Competes with current-page JS, APIs, authentication, and server work for routes the user may not visit. | Set `prefetch={false}` on sidebar/profile links; optionally call `router.prefetch` only on pointer intent or focus. Verify the cold `/portfolio` trace has no unrelated RSC requests. |
| P1 | `/api/portfolio` blocks its response on two journal-derived maps the portfolio UI does not consume. | The cached snapshot resolves first, then `portfolioResponseFromSnapshot` awaits `loadPortfolioEntryDates()` at `app/api/portfolio/route.ts:153-176`. That runs two queries in parallel, including full journal JSON extraction at `:70-150`. Only the orders share-P&L path reads these maps, at `components/WorkspaceSections.tsx:3701`; no portfolio view does. | Adds database query, JSON extraction, transfer, parsing, and allocation to the critical cached-read path. | Remove entry-date maps from the default portfolio response. Fetch them lazily for the orders share action, or gate with an explicit include flag. Keep the base endpoint no-store. |
| P1 | One global shell subscribes `/portfolio` to unrelated data and opens two independently authenticated realtime sockets. | `WorkspaceShell` merges portfolio, open-order, watchlist, global futures, and ticker-detail subscriptions at `components/WorkspaceShell.tsx:83-98,109-143,168-205,236-261`. `usePrices` opens a price socket, while `IBStatusProvider` opens another relay socket and health polling independently. | More subscription work, token/ticket requests, socket setup, quote traffic, and render invalidation than the portfolio needs. | Route-scope quote subjects; keep only portfolio positions and genuinely visible header data on `/portfolio`. Multiplex IB status and price status through one authenticated relay connection or a shared client connection manager. |
| P1 | Every quote update invalidates the whole prices map and broad portfolio render subtree. | `usePrices` object-spreads the complete map on every message at `lib/usePrices.ts:553-575`. `WorkspaceShell`, `MetricCards`, and `WorkspaceSections` receive the new map. `PositionRow` calculations depend on the whole map and repeat implied-value work at `components/PositionTable.tsx:350-440`; rows are not memoized at `:705-707`. | Unrelated watchlist/order/futures ticks can rerender all portfolio rows and repeatedly run exposure, day-move, and option calculations, extending time-to-settled and causing ongoing CPU load. | Use a keyed external quote store/selectors, pass only each position's relevant price slice, memoize rows, and calculate implied value once per position. Scope subscriptions first so fewer updates arrive. |
| P2 | Header fallbacks race the realtime bootstrap. | When prices are initially empty, the shell immediately invokes index and futures fallbacks at `components/WorkspaceShell.tsx:263-287`. Futures fallback can launch ES/NQ/RTY requests in parallel every 30 s; index fallback launches one per missing watched index every 60 s. | Adds up to three Yahoo-backed futures calls on normal cold loads while the relay is still authenticating, plus optional index calls. | Start fallback only after a short relay-bootstrap deadline or a definite socket failure. Batch and single-flight fallback symbols server-side. |
| P2 | Previous-close recovery opens another server-side realtime path and retries missing values every second. | `usePreviousClose` POSTs after live quotes arrive (`lib/usePreviousClose.ts:55-80`), with a 1 s missing-value retry. The route obtains auth, attempts a FastAPI ticket, opens a dedicated WS, then falls through to UW/Yahoo (`app/api/previous-close/route.ts:42-134,247-318`). | Adds a post-paint waterfall and repeated work before day-change metrics settle when close data is absent. | Include close in the relay bootstrap/snapshot or share the existing socket; use bounded exponential retry. Keep the POST no-store. |
| P2 | Risk-free rate can be fetched once per rendered position table, without client deduplication, and the route's upstream fetch is no-store. | Each `PositionTable` calls `useRiskFreeRate` at `components/PositionTable.tsx:592`; portfolio can render stock, options, and crypto tables at `components/WorkspaceSections.tsx:1375,1406,1437`. Hook: `lib/useRiskFreeRate.ts:17`. Route upstream: `app/api/risk-free-rate/route.ts:24-31`. | Up to three cold concurrent calls for one stable scalar. | Put the value in one provider/module-level in-flight cache and use a bounded server revalidation cache, such as 24 hours. This metric is not live disk state. |
| P2 | All six font files are preloaded by the root layout. | `app/layout.tsx:10-27` declares Inter variable plus five IBM Plex Mono faces. Production font transfers total 256,296 bytes: Inter 179,724 bytes and Plex 76,572 bytes. | Consumes cold-load bandwidth before all weights/styles are known to be needed above the fold. | Prefer a Plex variable file, or set non-critical italic/600/700 faces to `preload: false`; retain self-hosting and `display: swap`. |
| P2 | Noncritical shell utilities are statically reachable and start background APIs on every route. | Footer invokes service health and flex-token (`components/FooterTelemetryStrip.tsx:69-83`); Sidebar invokes profile (`components/Sidebar.tsx:84-90`); closed chat still statically imports `ChatPanel` (`components/ChatLauncher.tsx:3-5,51,71-77`); dashboard modules are statically imported by the shell. | More initial module and network work unrelated to primary portfolio content. | Defer footer telemetry until after primary paint/idle, and dynamically import closed overlays and route-only surfaces. Retain module-level profile/watchlist deduplication. |
| P3 | `/api/flex-token` violates the repository's live-disk no-store contract. | The route reads disk-backed config at `app/api/flex-token/route.ts:5-24` but lacks `dynamic = "force-dynamic"`; its caller at `components/FooterTelemetryStrip.tsx:72-83` omits `cache: "no-store"`. | Potentially stale operational status; fixing it will not make the route faster, but avoids using accidental HTTP caching as a performance mechanism. | Fix the contract, then defer the request instead of caching the live disk value in the browser. |
| Positive | Service worker does not cache or intercept portfolio navigations/APIs. | Registration occurs after `load` (`components/PwaRegister.tsx:5-26`). Policy bypasses navigation, `/api`, `/_next/data`, and `/ws`; only static assets are cache-first (`public/sw-decisions.js:16-35`, `public/sw.js:39-60`). | No service-worker API staleness or cold critical-path interception. Warm static chunks/fonts can benefit. | Preserve the policy. Never place portfolio/orders payloads in Cache Storage. |

## Navigation-to-settled call tree

```text
GET /portfolio (RSC/HTML navigation)
└─ app/portfolio/page.tsx:1-5
   └─ <WorkspaceShell section="portfolio">                    client boundary
      ├─ root Providers
      │  ├─ Clerk/auth bridge
      │  ├─ IBStatusProvider
      │  │  ├─ GET realtime token -> POST /api/ws-ticket -> WS /ws/prices
      │  │  └─ GET /edge-health/status (or /api/admin/health)
      │  └─ other state-only providers
      ├─ usePortfolio
      │  ├─ GET /api/portfolio                                 starts on mount
      │  │  ├─ cached Turso snapshot query (3 s server TTL)
      │  │  └─ then two journal entry-date queries (10 s server TTL)
      │  └─ useSnapshotStaleness(null) -> useAutoSyncOnStale
      │     └─ POST /api/portfolio                              can overlap/supersede GET
      │        └─ POST FastAPI /portfolio/sync -> entry-date queries
      ├─ useOrders
      │  └─ GET /api/orders
      │     └─ open orders query -> then executed orders query
      ├─ useWatchlist
      │  └─ GET /api/watchlist                                 module-deduplicated
      ├─ usePrices (waits for subscription subjects from portfolio/orders/watchlist)
      │  └─ get Clerk token -> POST /api/ws-ticket -> WS /ws/prices
      │     └─ subscribe -> price messages -> replace whole prices object
      ├─ quote fallback branch, conditional on initially missing prices
      │  ├─ GET /api/futures-quote?symbol=ES|NQ|RTY           parallel when Globex open
      │  └─ GET /api/index-quote?symbol=...                    parallel per watched index
      ├─ usePreviousClose, after price objects exist without close
      │  └─ POST /api/previous-close
      │     └─ ticket -> server WS -> UW -> Yahoo fallback
      ├─ Sidebar -> useProfile -> GET /api/profile             module-deduplicated
      ├─ FooterTelemetryStrip
      │  ├─ GET /api/service-health
      │  └─ GET /api/flex-token
      ├─ Next Link viewport prefetch
      │  └─ unrelated route RSC GETs (21 requests observed across two waves)
      ├─ dynamic import WorkspaceSections (1,198,604 raw bytes)
      │  └─ PortfolioSections
      │     ├─ filter/partition positions
      │     ├─ PositionTable[stock]   -> GET /api/risk-free-rate
      │     ├─ PositionTable[options] -> GET /api/risk-free-rate
      │     └─ PositionTable[crypto]  -> GET /api/risk-free-rate
      └─ after window load: register /sw.js -> precache static PWA icons/manifest
```

### Critical ordering

1. React mounts `usePortfolio` with `lastSync = null`.
2. Its mount effect starts `GET /api/portfolio` (`lib/usePortfolio.ts:177-185`).
3. `useSnapshotStaleness(null)` returns `state = "unknown"` and `isStale = true` (`lib/useSnapshotStaleness.ts:27-35,73-76`).
4. The auto-sync effect can claim the cross-tab lock and call `syncNow` (`lib/useAutoSyncOnStale.ts:99-123`).
5. `syncNow` increments `dataGenerationRef` before starting POST (`lib/usePortfolio.ts:125-164`).
6. If GET now resolves with the previous generation, the hook discards it (`lib/usePortfolio.ts:82`). The operator waits for live POST instead of seeing the cache immediately.

The Web Lock and cooldown can prevent some duplicate syncs, but they do not make the first GET authoritative. The race is deterministic from code ordering; its occurrence depends on response scheduling and lock/cooldown state.

### Reproduced regression and introducing change

- Commit `e0f508bd7a1054094bcdde1098542335a2054cc4` (`Reliability weekend 2026-08-22 — remediation`, committed 2026-08-24) introduced the relevant `unknown` state and returned `isStale: state !== "fresh"` for null/unparseable timestamps at `lib/useSnapshotStaleness.ts:29-40,73-76`.
- `WorkspaceShell` immediately passes that boolean to `useAutoSyncOnStale` (`components/WorkspaceShell.tsx:470-489`). It has no signal that the initial GET is still pending.
- Controlled Playwright mocks against the production build recorded this sequence from navigation start: cached GET began at 146 ms; producer POST began at 155 ms; GET completed at 602 ms; the deliberately delayed POST completed at 3,158 ms; position rows appeared at 3,476 ms.
- The 9 ms GET-to-POST gap proves this is not a slow cached query in that trace. The response is intentionally ignored by the generation guard at `lib/usePortfolio.ts:82` after `doSync` advances the generation.
- Correct gating must retain blackout behavior: wait for the initial read to settle, show a usable snapshot immediately, and only trigger producer recovery when the read returns no usable data or its actual `last_sync` is stale. Null hook state while the read is pending is not evidence of a backend blackout.

## HTTP, WebSocket, and polling inventory

| Request/channel | Initiator and code | Start condition | Relationship | Cache | Deadline/retry/cadence |
|---|---|---|---|---|---|
| `GET /portfolio` | Next navigation, `app/portfolio/page.tsx:1-5` | Direct navigation/link | Root request | Next/RSC semantics; authenticated navigation bypasses SW | Browser/navigation controlled |
| Route RSC prefetches | `next/link`, `components/Sidebar.tsx:112-159,178-197` | Links enter viewport | Parallel with current load; two waves observed | Next prefetch cache | 21 requests observed in local production topology trace |
| `GET /api/portfolio` | `usePortfolio`, `lib/usePortfolio.ts:50-105,177-185` | Every hook mount; also route-key change | Starts in first effect; may overlap POST | `cache: "no-store"` | 12 s timeout; additional GET on route key; 30 s polling only while market active; 500 ms after visibility regain |
| `POST /api/portfolio` | stale auto-sync through `lib/useAutoSyncOnStale.ts:99-123` | Initial null timestamp is stale unless cross-tab cooldown blocks | Overlaps GET and can invalidate its generation | `cache: "no-store"` | 42 s client timeout; cross-tab cooldown 1 min, exponential to 16 min (`lib/autoSyncClaim.ts:20-39`) |
| FastAPI `POST /portfolio/sync` | Next handler, `app/api/portfolio/route.ts:243-262` | Portfolio POST | Sequential inside POST, before response assembly | Not browser-cached | 35 s upstream timeout |
| Turso portfolio snapshot | `app/api/portfolio/route.ts:25-62` | Portfolio GET | First server phase | Server module TTL 3 s; stale-on-error 60 s | Single snapshot load per cache window |
| Two journal entry-date queries | `app/api/portfolio/route.ts:70-176` | Every assembled portfolio response after cache miss | Parallel to each other, but sequential after snapshot | Server module TTL 10 s | DB/client timeout governs |
| `GET /api/orders` | `useOrders`, `lib/useOrders.ts:47-83,149-177` | Every mount | Parallel with portfolio GET | `cache: "no-store"` | 12 s timeout; route-key GET; 30 s poll on orders page or market-active shell |
| Turso orders queries | `lib/orders/readOrdersFromDb.ts:40-81,119-125` via `app/api/orders/route.ts:12-27` | Orders GET | Open orders query, then executed orders query sequentially | No explicit response snapshot TTL/single-flight | DB/client timeout governs |
| `GET /api/watchlist` | `useWatchlist`, `lib/useWatchlist.ts:20-75`; shell at `components/WorkspaceShell.tsx:168-186` | Authenticated first consumer | Parallel with shell APIs | `cache: "no-store"`; module cache + in-flight dedup | One load per module lifetime unless mutation/reload |
| `GET /api/profile` | `useProfile`, `lib/useProfile.ts:21-71`; Sidebar at `components/Sidebar.tsx:84-90` | First Sidebar/profile consumer | Parallel with shell APIs | `cache: "no-store"`; module cache + in-flight dedup | One load per module lifetime unless mutation/reload |
| Client realtime token | `RealtimeAuthContext` consumed by `usePrices`; `lib/realtimeSocketAuth.ts:29-46` | Nonempty quote subscriptions | Before ticket and socket, sequential | Auth provider policy | Provider controlled |
| `POST /api/ws-ticket` for prices | `lib/wsTicket.ts:12-28`, `lib/realtimeSocketAuth.ts:29-46` | Token resolved | Token -> ticket -> WS | POST/no HTTP cache | Ticket request deadline from helper |
| Price `WS /ws/prices` | `lib/usePrices.ts:460-575,816-856` | Any symbol/option/index/depth subject | After token/ticket | Not cacheable | 8 s open deadline; stale check each 15 s; close after 60 s no data; reconnect 1 s to 30 s, max 10 attempts (`lib/reconnectStrategy.ts:20-35`) |
| Second token/ticket/IB-status WS | `lib/IBStatusContext.tsx:203-239,295-410` | Production provider mount | Independent and parallel to price socket | Not cacheable | 8 s open; 15 s stale checks/60 s cutoff; reconnect backoff |
| `GET /edge-health/status` | `lib/IBStatusContext.tsx:431-501` | Provider mount in production | Parallel; detailed fallback only if aggregate missing/unhealthy | `cache: "no-store"` | 5 s timeout, every 15 s |
| `GET /api/admin/health` | `lib/IBStatusContext.tsx:431-501` | Local mode, or detailed production fallback | Sequential fallback branch | `cache: "no-store"` | 5 s timeout, every 15 s |
| `GET /api/service-health` | `useServiceHealth`, `lib/useServiceHealth.ts:51-97`; footer at `components/FooterTelemetryStrip.tsx:69` | Footer mount | Parallel, noncritical | `cache: "no-store"` | 10 s timeout; every 60 s |
| `GET /api/flex-token` | footer, `components/FooterTelemetryStrip.tsx:72-83` | Footer mount | Parallel, noncritical | Default fetch cache; currently violates live-disk contract | 10 s timeout; one-shot per mount |
| `GET /api/futures-quote?symbol=...` | `useFuturesQuoteFallback`, `lib/useFuturesQuoteFallback.ts:22-76` | Globex open and ES/NQ/RTY price missing | Up to three parallel calls; races socket | `cache: "no-store"` | Server Yahoo fetch 5 s; repeats every 30 s |
| `GET /api/index-quote?symbol=...` | `useIndexQuoteFallback`, `lib/useIndexQuoteFallback.ts:36-83` | Watched/index subject lacks usable price | Parallel per missing index; races socket | `cache: "no-store"` | Repeats every 60 s |
| `POST /api/previous-close` | `usePreviousClose`, `lib/usePreviousClose.ts:55-80` | A received price has last but no close | After price socket/fallback; can repeat | POST/no-store semantics | Missing close retried every 1 s |
| Previous-close server ticket/WS/providers | `app/api/previous-close/route.ts:42-134,247-318` | Previous-close POST | Token -> ticket (750 ms) -> WS (up to 3 s) -> UW/Yahoo fallbacks; symbols parallel | Server day-scoped result caching | UW/Yahoo provider timeout up to 5 s each |
| `GET /api/risk-free-rate` | `useRiskFreeRate`, `lib/useRiskFreeRate.ts:17`; each table at `components/PositionTable.tsx:592` | Each mounted position table | Up to three concurrent duplicate calls | Hook default fetch; route sends 24 h public cache header, but upstream fetch is `no-store` | One per table mount |
| `GET /sw.js` | `PwaRegister`, `components/PwaRegister.tsx:5-26` | Production after `window.load` | After load, outside primary content path | `updateViaCache: "none"` for SW script; static assets cache-first | Browser SW lifecycle |

## Component and render path evidence

- `app/portfolio/page.tsx` is a server component with no data fetch; it only returns `WorkspaceShell`.
- `WorkspaceShell` is a monolithic client boundary (`components/WorkspaceShell.tsx:1,58`). Portfolio, orders, watchlist, market hours, quotes, prior close, global futures, notifications, sidebar, footer, chat, mobile shell, and command palette all initialize under it.
- `WorkspaceSections` is one delayed chunk, but its module statically pulls unrelated route implementations. The portfolio switch is only at `components/WorkspaceSections.tsx:4148-4152`.
- `PortfolioSections` partitions positions multiple times and instantiates three filtering and three column-visibility hooks (`components/WorkspaceSections.tsx:1280-1299`) before rendering up to three `PositionTable` trees.
- `MetricCards` recomputes aggregate totals, exposure detail, and day-move breakdown on price-map identity changes (`components/MetricCards.tsx:624-648`) even while drill-down modals are closed.
- `PositionRow` option calculations depend on the full prices object and call `computePositionImpliedValue` for both net and notional values (`components/PositionTable.tsx:350-425`).
- Every price message creates a fresh top-level prices map (`lib/usePrices.ts:553-575`), so these memo boundaries do not isolate unrelated symbols.
- The shell also pushes each new map into `TickerDetailContext` through an effect even on `/portfolio` (`components/WorkspaceShell.tsx:332-339`).

## Production asset evidence

Measured from the existing successful Next production output at commit `88514b9b`:

| Asset set | Raw | gzip | brotli | Notes |
|---|---:|---:|---:|---|
| Distinct `/portfolio` route entry JS | 791,851 B | 231,257 B | 200,068 B | Layout, error boundaries, portfolio route references from the client reference manifest |
| `WorkspaceSections` dynamic chunk | 1,198,604 B | 316,954 B | 248,220 B | `14xu5ck7lf8q8.js`; requested/preloaded on cold route |
| Route CSS | 385,541 B | 58,222 B | 46,170 B | Global CSS contributes 377,291 raw / 44,903 brotli bytes |
| Six WOFF2 fonts | 256,296 B | already compressed | already compressed | Inter variable 179,724 B; five Plex faces total 76,572 B |

- Cold route JS is therefore approximately 448 KB brotli before HTML and external authentication resources.
- Client JS + route CSS + fonts is approximately 750 KB of cold compressed transfer before HTML and external authentication resources.
- Compression figures are file-level comparisons, not production origin content-encoding guarantees.
- Largest relevant source files: `WorkspaceSections.tsx` 4,193 lines; `MetricCards.tsx` 1,081; `PositionTable.tsx` 724; `WorkspaceShell.tsx` 592.

## Cache boundary recommendations

### Safe, high-value application/server caches

- Retain the last successful portfolio and orders snapshots in a persistent client provider or module store. Render the memory value immediately and revalidate via `cache: "no-store"`. This is SWR behavior in application memory, not HTTP caching of live data.
- Add in-flight single-flight to portfolio/order snapshot loads so remounts or concurrent consumers share one server query cycle.
- Separate the stable/derived journal entry-date payload from the live portfolio snapshot; cache it by journal revision or short TTL only where consumed.
- Cache the risk-free-rate scalar server-side with bounded revalidation and deduplicate its client request.
- Batch and single-flight identical futures/index fallback requests; use short server TTLs appropriate for delayed quotes.

### Must remain uncached by browser/SW

- `/api/portfolio`, `/api/orders`, `/api/watchlist`, `/api/profile`, service/admin health, live disk status, websocket tickets, and realtime socket data.
- Navigations, RSC payloads, `/api`, `/_next/data`, and `/ws` must continue to bypass Cache Storage.
- Producer POSTs must not be treated as reads; they should refresh state in the background after an existing snapshot is visible.

## Suggested implementation sequence

```text
F1 initial read wins
├─> F2 split portfolio response from entry-date metadata
└─> F3 add persistent in-memory snapshot + no-store revalidation

F4 split WorkspaceSections by route
├─> F5 dynamic-load closed overlays/route-only surfaces
└─> F6 enforce route bundle budget

F7 disable viewport nav prefetch

F8 route-scope quote subscriptions
├─> F9 multiplex realtime status
├─> F10 keyed quote selectors + memoized rows
└─> F11 defer fallbacks until relay deadline

F12 deduplicate/cache risk-free rate
F13 trim font preloads
F14 defer footer telemetry and fix flex-token no-store contract
```

## Regression and validation plan

- Initial-data race unit test: defer portfolio GET and POST independently; assert GET snapshot renders before POST completes and POST cannot invalidate it.
- Response-shape test: default `/api/portfolio` does not query or serialize entry-date maps; the orders share path obtains them explicitly.
- Browser request-budget test: cold `/portfolio` has zero unrelated RSC viewport-prefetch requests and exactly one portfolio GET before any background POST.
- Bundle-budget test: parse the client reference/build output and fail if `/portfolio` pulls the omnibus workspace chunk or exceeds an agreed compressed budget.
- Realtime topology test: `/portfolio` subscription payload contains portfolio symbols plus explicitly visible header subjects, not watchlist/order/ticker-detail subjects by default.
- Render-isolation test: publish a watchlist-only quote and assert unaffected portfolio rows do not rerender; publish one position-leg quote and assert only that row and aggregates update.
- Fallback test: futures/index HTTP fallback does not start before the relay bootstrap deadline and is cancelled when the live quote arrives.
- Cache-contract test: retain `web/tests/api-routes-no-cache-contract.test.ts`; add `/api/flex-token` and its client caller.
- Service-worker test: retain assertions that navigation, `/api`, `/_next/data`, and `/ws` are bypassed.

Useful read-only commands:

```bash
git status --short --branch
rg -n 'fetch\(|new WebSocket|setInterval|setTimeout' components/WorkspaceShell.tsx lib/usePortfolio.ts lib/useOrders.ts lib/usePrices.ts lib/usePreviousClose.ts lib/useFuturesQuoteFallback.ts lib/useIndexQuoteFallback.ts components/IBStatusContext.tsx components/FooterTelemetryStrip.tsx
wc -l components/WorkspaceSections.tsx components/MetricCards.tsx components/PositionTable.tsx components/WorkspaceShell.tsx
find .next/static/chunks -type f -name '*.js' -print0 | xargs -0 wc -c | sort -n | tail
npm test -- --run tests/api-routes-no-cache-contract.test.ts
```

## Local topology trace caveat

- A production `next start` browser run confirmed the request list, duplicate RSC prefetch waves, font list, and chunk transfers.
- Local Clerk middleware rejected application API responses in that harness. The harness therefore cannot support claims about production API duration, database duration, Web Vitals, or the time at which real portfolio rows become visible.
- Timings in this report are only explicit code deadlines/intervals. No synthetic loopback time is presented as user-facing performance.
