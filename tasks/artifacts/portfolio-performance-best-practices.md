# `/portfolio` performance: applicable current practices

Date: 2026-08-25
Scope: Next.js 16.2 / React 19 client, shared shell, `/api/portfolio`, Turso/libSQL, and portfolio-adjacent layout/provider load paths. This is a code-and-query-plan review, not a measured production trace. Chrome DevTools MCP was unavailable, so impact is directional and confidence distinguishes strong code evidence from runtime measurement.

## Non-negotiable constraints

- Keep live disk/DB GET handlers `dynamic = "force-dynamic"` and every corresponding browser fetch `cache: "no-store"`.
- Do not put operator portfolio, orders, health diagnostics, or other authenticated trading state in browser/CDN caches. Vercel's current CDN guidance says CDN caching is not appropriate for sensitive or user-specific content, and authenticated or `no-store` responses are ineligible anyway: [Vercel CDN Cache](https://vercel.com/docs/caching/cdn-cache).
- Keep explicit freshness and warning provenance. Do not make a stale portfolio look authoritative.
- Do not reintroduce the retired embedded replica or replace Radon's bounded/self-healing HTTP transport merely because the generic Hrana spec calls WebSocket more efficient.

## Priority findings

### P0. Remove journal-derived entry-date work from the `/portfolio` critical response

- **Code evidence:** `web/app/api/portfolio/route.ts:171` awaits `loadPortfolioEntryDates()` after the latest portfolio snapshot is ready. That helper starts two journal queries at `web/app/api/portfolio/route.ts:159-162`. Both parse JSON across journal history (`:72-82` and `:111-126`). The response cannot finish until they finish.
- **Why this is route waste:** the returned `trade_log_dates` and `contract_open_dates` are consumed only by the executed-order share calculation at `web/components/WorkspaceSections.tsx:3701`; `/portfolio` rendering does not read them.
- **DB evidence:** reproducing the current schema/indexes from `scripts/db/migrations/0001_init.sql:70-80` and `0025_journal_effective_at_index.sql:10-11` with `EXPLAIN QUERY PLAN` reports `SCAN journal` plus `USE TEMP B-TREE FOR GROUP BY` for the ticker aggregate, and `SCAN journal` for contract-open rows. The current effective-time index does not match either query. By contrast, latest `portfolio_snapshots` uses the primary-key index.
- **Surgical change:** return the portfolio snapshot immediately. Fetch entry-date maps only for the orders/share surface, through a dedicated endpoint or an explicit `includeEntryDates` mode requested only when that surface becomes active. Preserve the current short in-process cache there.
- **Follow-on DB shape:** if the dedicated path remains slow, compute both maps from one canonical journal read or maintain normalized/materialized entry-date rows at journal-write time. A libSQL batch can cut network round trips, but two batched statements still perform two full scans; it is secondary to removing/reshaping the scans.
- **Official source claim:** Turso says to use `EXPLAIN QUERY PLAN`; missing suitable indexes forces full scans, and queries can scan many more rows than they return: [Turso Usage & Billing](https://docs.turso.tech/help/usage-and-billing). Turso supports expression and partial indexes, but index cost must be justified by the actual plan: [Turso `CREATE INDEX`](https://docs.turso.tech/sql-reference/statements/create-index). Hrana batches reduce round trips by carrying multiple statements in one request: [Hrana 3 protocol](https://github.com/tursodatabase/libsql/blob/main/docs/HRANA_3_SPEC.md).
- **Priority / impact / confidence:** P0 / high server latency and Turso work on every cold cache / high.

### P0. Split the all-routes client module graph before tuning individual components

- **Code evidence:** `web/components/WorkspaceShell.tsx:39-41` dynamically loads one `WorkspaceSections` chunk, but that 4,193-line, 177 KB source module statically imports 76 route implementations at `web/components/WorkspaceSections.tsx:24-140`, then selects one at runtime at `:4142-4188`. `/portfolio` therefore reaches scanner, regime, performance, admin, workflow, ticker, mobile, and order code through one client entry. `RegimePanel` alone statically imports the D3-backed chart family at `web/components/RegimePanel.tsx:8-33`.
- **Additional always-imported but conditional UI:** `WorkspaceShell.tsx:31` imports `DashboardSurface` although it renders only at `:553-559`; `:51` imports the closed-by-default command palette; `web/components/ChatLauncher.tsx:4` imports the full chat/Markdown/risk tree although it returns `null` until open at `:51`; `web/components/MetricCards.tsx:22-25` imports four click-only modals.
- **Surgical change:** extract a small `PortfolioSections` module and dynamically import route implementations with statically analyzable import functions. Independently lazy-load ChatPanel, CommandPalette, metric modals, and dashboard-only UI at their render conditions. Keep the lightweight launchers/skeletons eager.
- **Official source claim:** Next.js says lazy loading improves initial load by reducing JavaScript and defers Client Components/libraries until needed: [How to lazy load Client Components and libraries](https://nextjs.org/docs/app/guides/lazy-loading). Next.js also recommends the bundle analyzer to identify modules to split or lazy-load: [How to optimize package bundling](https://nextjs.org/docs/app/guides/package-bundling). React's `lazy` similarly defers component code until first render: [React `lazy`](https://react.dev/reference/react/lazy).
- **Priority / impact / confidence:** P0 / high parse-compile-hydration and earlier navigation prefetch / high for bundle reachability, medium until production bundle bytes are measured.

### P1. Eliminate the hydration-to-portfolio request waterfall with server-provided initial data

- **Code evidence:** `web/app/portfolio/page.tsx:1-5` renders only the client `WorkspaceShell`. The first portfolio request starts in a client effect at `web/lib/usePortfolio.ts:179-185`, after the shell JavaScript has loaded and hydrated. The positions table therefore waits for document, shared JS, hydration, `/api/portfolio`, Turso, JSON transfer, and client render.
- **Surgical change:** make the page a real Server Component data boundary. Start the bounded latest-snapshot read on the server and pass a serializable snapshot or promise into a narrow portfolio client surface as initial data. Keep client `no-store` polling for updates, but skip the redundant mount GET when the server snapshot already satisfies the same freshness window. Stream a meaningful shell/skeleton around the data boundary rather than blocking the whole page.
- **Constraint:** do not server-fetch Radon's own Route Handler over HTTP. Extract the authenticated data-access helper and call it directly. Keep request-time/dynamic behavior; this is not static caching.
- **Official source claim:** Next.js recommends Server Components for database/API reads close to the source, reducing client JavaScript and improving FCP; data can be passed to Client Components: [Server and Client Components](https://nextjs.org/docs/app/getting-started/server-and-client-components). It also documents server fetching, streaming, and starting independent requests in parallel: [Fetching Data](https://nextjs.org/docs/app/getting-started/fetching-data). React documents client-effect fetching as an expensive client-server waterfall: [React Server Components](https://react.dev/reference/rsc/server-components).
- **Priority / impact / confidence:** P1 / high cold-load latency, especially at non-local RTT / high mechanism confidence, medium net gain until TTFB and LCP are traced.

### P1. Reduce non-critical startup competition and tailor the bootstrap payload

- **Code evidence:** the shared shell starts an unconditional orders GET on mount even away from `/orders` (`web/lib/useOrders.ts:149-153`); `/portfolio` also mounts profile (`web/components/Sidebar.tsx:84-88` -> `web/lib/useProfile.ts:60-67`), watchlist (`web/components/WorkspaceShell.tsx:173-186`), service health and flex-token reads (`web/components/FooterTelemetryStrip.tsx:67-83`), two authenticated realtime sockets (`web/lib/IBStatusContext.tsx:393-416` and `web/lib/usePrices.ts:485-506`), and up to three initial futures fallbacks before live prices arrive (`web/components/WorkspaceShell.tsx:275-286`, `web/lib/useFuturesQuoteFallback.ts:40-76`).
- **Why it matters:** several of those requests also read Turso while Radon's process pool is capped at eight connections (`web/lib/db.ts:55-59`). They are parallel, but they compete with the only payload required to replace the portfolio loading state.
- **Surgical changes:**
  - Return only today's executions/realized P&L needed by portfolio metrics instead of the complete orders snapshot, or fold that small derived value into the portfolio bootstrap.
  - Delay profile, service-health detail, flex status, and watchlist expansion until after the critical snapshot/first paint. Do not remove status monitoring; change priority and mount timing.
  - Give the realtime relay a short bounded opportunity to populate ES/NQ/RTY before launching all Yahoo fallback calls; live relay still wins.
  - Preserve the existing browser module-level single-flight caches in `useProfile` and `useWatchlist`; they already prevent duplicate same-session reads.
- **Official source claim:** Next.js recommends parallel requests when all are necessary, but also says slow critical data should be optimized/cached and streamed; Server Components can reduce multiple client round trips: [Fetching Data](https://nextjs.org/docs/app/getting-started/fetching-data). This recommendation is about removing or deprioritizing non-critical work, not serializing necessary critical work.
- **Priority / impact / confidence:** P1 / medium-high under cold sessions and Turso tail latency / medium pending a network trace.

### P1. Shrink route-global CSS, then verify actual production transfer

- **Code evidence:** `web/app/layout.tsx:6` imports `web/app/globals.css` for every route. The file is 20,500 lines / 502,888 bytes source (85,135 bytes gzip and 65,982 bytes Brotli before minification in this review). It contains feature-specific styling for the entire workstation, so `/portfolio` pays beyond base tokens/shell/portfolio styles.
- **Surgical change:** leave resets, tokens, typography primitives, and shared shell rules global. Move feature/component rules into colocated CSS Modules imported by the newly split route components. Measure production CSS chunks before/after; source bytes are not transfer bytes.
- **Official source claim:** Next.js says root-imported global CSS applies to every route, recommends global CSS only for truly global styles and CSS Modules for scoped custom CSS, and production-build CSS is minified/code-split: [CSS in Next.js](https://nextjs.org/docs/app/getting-started/css).
- **Priority / impact / confidence:** P1 / medium render-blocking bytes and style calculation / high scope evidence, medium byte impact pending a production build.

### P2. Add route-specific field and server timing before setting budgets

- **Code evidence:** `web/instrumentation.ts:4-27` starts DB keepalive, loop-lag monitoring, and bounded shutdown, but there is no `useReportWebVitals` integration. `/api/portfolio` labels DB calls internally but emits no `Server-Timing` breakdown.
- **Surgical change:** report LCP, INP, CLS, FCP, and TTFB with pathname/navigation type; add sanitized `Server-Timing` spans for auth, snapshot query, entry-date query, serialization, and total API time. Do not include SQL, account data, tokens, or user identifiers.
- **Official source claim:** Next.js provides `useReportWebVitals` and recommends a separate tiny Client Component to confine the boundary: [Next.js `useReportWebVitals`](https://nextjs.org/docs/app/api-reference/functions/use-report-web-vitals). Chrome DevTools displays `Server-Timing` for network requests: [Chrome Performance features reference](https://developer.chrome.com/docs/devtools/performance/reference).
- **Priority / impact / confidence:** P2 / enables regression detection and validates rankings / high.

## Safe caching decisions

| Surface | Decision | Radon applicability |
|---|---|---|
| `/api/portfolio` HTTP response | **Do not browser/CDN cache.** Keep `force-dynamic`, `no-store`, and response no-store headers. | `web/app/api/portfolio/route.ts:18-23`, `web/lib/usePortfolio.ts:59-62`, `web/app/api/portfolio/route.ts:177` |
| Latest portfolio DB read | **Keep short in-process single-flight/stale-on-error.** The existing 3 s TTL is a coalescer, not a user-visible freshness policy. Do not increase broadly because this portfolio also feeds order-risk coverage. | `web/app/api/portfolio/route.ts:25-32`, `:200-205`; cache semantics at `web/lib/dbCache.ts:1-21` |
| Entry-date maps | **Cache only on the surface that needs them.** Longer TTL is plausible because values are day-granularity, but only after explicit fill/journal invalidation or a measured acceptable bound. | `web/app/api/portfolio/route.ts:29-32`, `:159-162`; consumer `web/components/WorkspaceSections.tsx:3701` |
| React `cache()` | **Not a fix for this Route Handler.** React documents it as Server-Component-only and request-scoped; the current duplicated cost is cross-request polling and DB scans. | [React `cache`](https://react.dev/reference/react/cache); current handler `web/app/api/portfolio/route.ts:195-240` |
| Next `use cache` / static Route Handler | **Reject for live portfolio.** Route Handlers are uncached by default, and opting a GET into static/cache behavior conflicts with live authenticated trading data. | [Next.js Route Handlers](https://nextjs.org/docs/app/getting-started/route-handlers) |
| Static JS/CSS/font/image assets | **Keep framework immutable caching.** Focus on reducing what `/portfolio` references, not weakening cache-busting. | Next self-hosting documents hashed immutable assets: [Self-Hosting](https://nextjs.org/docs/app/guides/self-hosting) |

## Practices already present or not applicable

- Independent entry-date queries already use `Promise.all` at `web/app/api/portfolio/route.ts:159-162`; the issue is unnecessary/scanning work, not a JavaScript waterfall between those two calls.
- `lucide-react` already uses `optimizePackageImports` at `web/next.config.mjs:69-72`; adding the same option again will not split Radon's own monolithic module graph.
- Local fonts already use `next/font/local` with `display: "swap"` at `web/app/layout.tsx:10-27`; do not prioritize font changes without a trace showing them on the critical path.
- The service worker registration is deferred until `load` at `web/components/PwaRegister.tsx:11-24`, and the project contract bypasses live API routes. It is not a leading cold-load suspect.
- Sidebar navigation already uses Next `<Link>` at `web/components/Sidebar.tsx:142-155`. Next automatically prefetches visible links after hydration, but current large JavaScript can delay that hydration: [Linking and Navigating](https://nextjs.org/docs/app/getting-started/linking-and-navigating).
- Do not remove route-local auth checks to save latency. Middleware plus handler defense-in-depth is a security boundary (`web/middleware.ts:321-395`, `web/lib/routeAccess.ts:77-90`). Reduce initial request count instead.

## Measurement gates for implementation

1. Run the official Next bundle analyzer on a production compile; record `/portfolio` first-load JS and the `WorkspaceSections`/D3/Markdown contributors before and after splitting.
2. Run `EXPLAIN QUERY PLAN` against production Turso for the two current journal queries, and use Turso analytics to capture latency and rows touched. Confirm the dedicated portfolio response performs only the indexed latest-snapshot lookup.
3. Capture cold and warm `/portfolio` traces with cache enabled and disabled, separating document TTFB, hydration, `/api/portfolio` wait, JSON download, scripting, and LCP.
4. Report field metrics at p75 separately for desktop/mobile. Current good thresholds are LCP <= 2.5 s, INP <= 200 ms, and CLS <= 0.1; poor begins above 4.0 s, 500 ms, and 0.25 respectively: [web.dev, Core Web Vitals thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds).
