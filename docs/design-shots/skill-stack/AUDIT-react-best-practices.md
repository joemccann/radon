# AUDIT — Vercel React Best Practices (scanner / WorkspaceShell)

**Date:** 2026-08-06  
**Skill:** `~/.agents/skills/vercel-react-best-practices` (rules under `rules/`)  
**Scope (read-only):**
- `web/app/scanner/page.tsx`
- `web/components/WorkspaceShell.tsx`
- `web/components/WorkspaceSections.tsx` (scanner modes live here)
- `web/lib/useScanner.ts`, `useDiscover.ts`, `useThetaHarvester.ts`, `useStrengthConfirmation.ts`, `useLeap.ts`, `useGarchConvergence.ts`, `useSyncHook.ts`
- Adjacent shell consumers: MetricCards, Dashboard OpportunitiesCard (same hooks)

**Out of scope:** API route internals, brand chrome redesign, non-scanner section UX.

---

## Executive summary

`/scanner` is a thin route (`WorkspaceShell section="scanner"`) sitting on a **fully client, monolithic workspace**. Almost all section UIs (orders, journal, admin, regime, ticker, options, flow, scanner modes) are **statically imported** into a single ~4.1k-line client module. The shell always starts portfolio/orders polling and a realtime price WebSocket. Scanner modes mount **five independent cache GETs** on every visit for tab badge counts. There is **no SWR**, **no route `loading.tsx`**, **no Suspense**, and **no `optimizePackageImports`**.

Highest UX leverage: **code-split WorkspaceSections by section**, then **stop price-tick re-renders of scanner**, then **narrow scanner fetch + loading UI**.

---

## Evidence map

| Surface | Finding |
|---|---|
| `web/app/scanner/page.tsx` | Server page with no data; only renders client shell. No `loading.tsx` under `app/scanner/`. |
| `WorkspaceShell.tsx` | `"use client"`; dynamic-imports only `WorkspaceSections` with `loading: () => null`. Always runs `usePortfolio`, `useOrders`, `useWatchlist`, `usePrices` (+ index/futures fallbacks, previous-close). |
| `WorkspaceSections.tsx` | ~4125 LOC; **36 component imports** (Admin, Regime, TickerWorkspace, Options, Performance, all four scanner panels, mobile order/journal stacks, etc.). `ScannerSections` co-located; switch at bottom. |
| Scanner hooks | Thin wrappers over `useSyncHook`. `active` gates **poll/POST**, not **initial GET**. `ScannerSections` always mounts 5 hooks. |
| `next.config.mjs` | No `experimental.optimizePackageImports` (lucide-react barrels unoptimized). |
| App router | No `Suspense` usage under `web/`; no section-level loading boundaries. |

---

## Top 10 prioritized fixes

Impact ranks follow the skill’s category priority: waterfalls / bundle (CRITICAL) → client fetch (MEDIUM-HIGH) → re-render / rendering (MEDIUM).

### 1. Split `WorkspaceSections` into per-section dynamic imports  
**Priority:** P0 · **Impact:** CRITICAL · **Rules:** `bundle-dynamic-imports`, `bundle-conditional`, `bundle-analyzable-paths`

**Problem:** Opening `/scanner` (or any non-dashboard section) downloads and parses one client graph that statically includes admin, regime, ticker workspace, options, performance, journal/orders UI, flow-analysis panels, and all scanner mode UIs.

```35:37:web/components/WorkspaceShell.tsx
const WorkspaceSections = dynamic(() => import("@/components/WorkspaceSections"), {
  loading: () => null,
});
```

That is one dynamic boundary around a **monolith**, not section-level splitting:

```116:136:web/components/WorkspaceSections.tsx
import RegimePanel from "./RegimePanel";
import CtaPage from "./CtaPage";
import AdminWorkspace from "./admin/AdminWorkspace";
// ... Profile, Watchlist, Performance, Options, TickerWorkspace,
// Theta/Strength/Leap/Garch scanners, flow-analysis, Alerts, WorkflowComposer
```

**Fix:**
- Extract `ScannerSections` (+ Discover) into `web/components/scanner/ScannerWorkspace.tsx` (and similarly orders/journal/regime/…).
- In `WorkspaceSections` (or Shell), `next/dynamic` **each** section with a small instrument skeleton fallback.
- Keep route files as thin wrappers; do not pull unused section modules into the scanner chunk.

**UX win:** Faster TTI/LCP on `/scanner`; less main-thread parse/compile on cold nav; smaller mobile payloads.

---

### 2. Enable `optimizePackageImports` for `lucide-react` (and similar barrels)  
**Priority:** P0 · **Impact:** CRITICAL · **Rule:** `bundle-barrel-imports`

**Problem:** Widespread `import { … } from "lucide-react"` (e.g. 15+ icons in `WorkspaceSections` alone). `web/next.config.mjs` has security headers and tracing excludes but **no** package-import optimization.

**Fix:** In `web/next.config.mjs`:

```js
experimental: {
  optimizePackageImports: ["lucide-react"],
},
```

Optionally add other barrel-heavy deps if present (`date-fns`, etc.) after a bundle check.

**UX win:** Lower cold-start JS cost and faster HMR/dev; material on every shell route including scanner.

---

### 3. Isolate scanner content from shell price-tick re-renders  
**Priority:** P0 · **Impact:** HIGH–CRITICAL (UX jank) · **Rules:** `rerender-memo`, `rerender-defer-reads`, `rerender-use-ref-transient-values`

**Problem:** `usePrices` updates `prices` frequently. `WorkspaceShell` re-renders and always passes `prices` into `WorkspaceSections`. Scanner does **not** use prices, but `WorkspaceSections` is not memoized, so **every tick re-renders the scanner tree** (tables, mode tabs, mobile cards).

```541:551:web/components/WorkspaceShell.tsx
{activeSection !== "dashboard" ? (
  <WorkspaceSections
    section={activeSection}
    portfolio={portfolio}
    // ...
    prices={prices}
    // ...
  />
) : null}
```

**Fix (surgical):**
- `React.memo(WorkspaceSections)` with stable props; for `section === "scanner" | "discover"`, omit or stabilize unused props.
- Prefer children composition: Shell chrome owns prices; section slot receives only what it needs.
- Keep MetricCards as a sibling that subscribes to prices; do not force scanner to share that subscription.

**UX win:** Smooth scrolling/typing on scanner during market hours; lower input latency on mode switches.

---

### 4. Defer or narrow shell realtime/data work on scanner  
**Priority:** P1 · **Impact:** CRITICAL (network + main thread) · **Rules:** `async-cheap-condition-before-await`, `client-swr-dedup`, conditional `enabled`

**Problem:** On `/scanner`, Shell still:
1. Polls portfolio (30s) and orders when market active  
2. Opens IB WS with portfolio + order + watchlist + Globex futures symbols  
3. Runs index/futures Yahoo fallbacks + previous-close backfill  
4. Renders MetricCards (which need portfolio/prices)

Scanner tables are **cache-file GETs** (`/api/scanner`, `/api/scanner/theta`, …). Live L1 is not required for the instrument tables.

**Fix options (pick product intent):**
- **A (recommended):** Keep MetricCards, but pass `usePrices({ enabled: needsLiveQuotes })` only when symbols exist *and* section needs live marks; on pure scanner, subscribe only to HEADER_FUTURES + empty portfolio until portfolio arrives (already partly true), avoid depth/tape work.
- **B:** Scanner layout without MetricCards (or static last-sync metrics from portfolio GET only, no WS).
- **C:** Prefetch portfolio GET in parallel with scanner GET at page level; do not chain “portfolio → then feel ready.”

**UX win:** Less contention with IB/relay; faster first interactive scanner table; fewer degraded banners from unrelated uplink failures (where product allows).

---

### 5. Add Suspense / route loading UI (stop blank content)  
**Priority:** P1 · **Impact:** HIGH · **Rule:** `async-suspense-boundaries`

**Problem:**
- Dynamic `WorkspaceSections` uses `loading: () => null` → **content hole** until the monolith chunk loads.
- No `web/app/scanner/loading.tsx` (or shared workspace loading).
- No Suspense boundaries around mode panels or MetricCards.

**Fix:**
- Replace `loading: () => null` with a brand-aligned instrument skeleton (`ScannerInstrumentShell` chrome + muted table rails).
- Add `app/scanner/loading.tsx` (and/or shell-level Suspense) so chrome (Sidebar/Header) can paint while section chunk streams.
- Optional later: RSC wrapper that streams shell chrome and suspends only the section slot (larger architecture move).

**UX win:** Perceived performance; no dead zone after nav; fewer layout jumps if skeleton matches final metrics row.

---

### 6. Stop mounting all five scanner fetch hooks for badge counts  
**Priority:** P1 · **Impact:** HIGH · **Rules:** `client-swr-dedup`, `bundle-conditional`, conditional fetch

**Problem:** `ScannerSections` always calls:

```1492:1496:web/components/WorkspaceSections.tsx
const { data, syncing, error, lastSync, syncNow } = useScanner(mode === "flow");
const theta = useThetaHarvester(mode === "theta");
const strength = useStrengthConfirmation(mode === "strength");
const leap = useLeap(mode === "leap");
const garch = useGarchConvergence(mode === "garch");
```

`useSyncHook` **always** does an initial GET when the hook mounts, even if `active === false` (by design: “don’t blank the page”). Result: **five parallel GETs** on every `/scanner` visit so `ScannerModeTabs` can show counts.

Same pattern on dashboard `OpportunitiesCard` (five hooks; only one tab active for polling).

**Fix:**
- **Counts endpoint or light summary fields** on one route (e.g. `/api/scanner/meta`) for tab badges.
- Or: fetch counts only for visible mode; show badges after idle/`requestIdleCallback` or on tab hover (`bundle-preload` style).
- Gate initial GET behind `active || wantCounts` explicitly in `useSyncHook` (breaking change for OpportunitiesCard — coordinate).

**UX win:** Less bandwidth and JSON parse on cold scanner; faster TTI on constrained networks; less API fan-out under load.

---

### 7. Replace ad-hoc `useSyncHook` with SWR (or shared cache) for scanner endpoints  
**Priority:** P2 · **Impact:** MEDIUM-HIGH · **Rule:** `client-swr-dedup`

**Problem:** No `swr` usage in `web/`. Each hook instance owns isolated `useState` + `fetch`. Navigating dashboard → scanner re-fetches the same `/api/scanner` (and siblings). Dual mount of the same hook never dedupes in-flight requests.

**Fix:**
- Introduce `useSWR(endpoint, fetcher, { refreshInterval: active ? 5*60_000 : 0 })` for GET-cache paths (`useScanner`, `useLeap`, `useGarchConvergence`, `useThetaHarvester`, `useStrengthConfirmation`, `useDiscover`).
- Keep POST “run scan” as mutation (`useSWRMutation` or explicit `fetch` + `mutate`).
- Preserve offline meta header handling (`readOfflineMeta` / offline signals).

**UX win:** Instant back-navigation with stale-while-revalidate; single in-flight request per key; simpler loading semantics.

---

### 8. Dynamic-import scanner mode panels (theta / strength / leap / garch)  
**Priority:** P2 · **Impact:** MEDIUM–HIGH · **Rules:** `bundle-dynamic-imports`, `bundle-conditional`

**Problem:** Even after extracting Scanner workspace, static imports of `ThetaHarvesterScanner`, `StrengthConfirmationScanner`, `LeapScanner`, `GarchConvergenceScanner` (and Discover table) pull all mode UI into the first scanner paint.

**Fix:** After mode is known (URL `?mode=`), dynamically import that panel; keep `ScannerModeTabs` in the parent chunk. Prefetch other modes on tab hover (`bundle-preload`).

**UX win:** Smaller first scanner chunk; mode switch cost is one-time and predictable.

---

### 9. Virtualize or `content-visibility` long scanner lists  
**Priority:** P3 · **Impact:** MEDIUM · **Rule:** `rendering-content-visibility`

**Problem:** Flow/discover (and mobile `SignalCard` stacks) render full row sets with no `content-visibility` and no virtualization. Repo has zero `content-visibility` usage under `web/`.

**Fix:** CSS on row/card containers:

```css
.scanner-row, .m-signal-card {
  content-visibility: auto;
  contain-intrinsic-size: auto 48px; /* tune per row height */
}
```

For very large discover sets, virtualize the table body.

**UX win:** Faster first paint and scroll when signal counts are high.

---

### 10. Memoize heavy scanner pure work; avoid sort thrash on unrelated parent updates  
**Priority:** P3 · **Impact:** MEDIUM · **Rules:** `rerender-memo`, `rerender-simple-expression-in-memo`, `js-tosorted-immutable`

**Problem:** Mobile path sorts a copy of `signals` on every render; desktop uses `useSort`. Parent re-renders (fix 3) amplify this. Mode tab count filters allocate new arrays each render (`leap.data.results.filter(...)`).

**Fix:**
- `useMemo` badge counts from each dataset.
- Ensure sort state only recomputes when `signals` / sort key change (already partly true via `useSort` on desktop).
- After shell memoization (fix 3), re-measure; only then add row-level memo if profiling still shows cost.

**UX win:** Cheaper mode toggles and scroll under live shell updates.

---

## Dependency graph (suggested implementation order)

```
T1  Split WorkspaceSections + per-section dynamic import   depends_on: []
T2  optimizePackageImports lucide-react                    depends_on: []
T3  Memo / prop-narrow scanner vs prices                   depends_on: [T1]  (can start before T1 finishes)
T4  Scanner loading skeleton + drop loading:null           depends_on: [T1]
T5  Narrow shell WS/poll on scanner (product decision)     depends_on: []
T6  Scanner fetch strategy (counts API or active-only GET) depends_on: []
T7  SWR for useSyncHook GET paths                          depends_on: [T6]
T8  Dynamic mode panels + hover preload                    depends_on: [T1]
T9  content-visibility on long lists                       depends_on: []
T10 Sort/count memo cleanup                                depends_on: [T3]
```

---

## Explicit non-issues / already good

| Pattern | Status |
|---|---|
| Scanner page as thin server entry | Fine; issue is client graph below. |
| Mode-gated **polling** via `active` | Correct intent; initial GET still heavy. |
| Parallel independent GETs (when all needed) | Better than sequential `await` waterfalls. |
| Shell dynamic import of WorkspaceSections | Correct *shape*; boundary too coarse. |
| Local fonts in root layout | Good (no Google Fonts network on load). |

---

## Verification plan (when implementing — not run in this audit)

1. `next build` + bundle analyzer (or `@next/bundle-analyzer`): compare `scanner` client JS before/after T1–T2–T8.  
2. Chrome Performance: record `/scanner` cold load; confirm no price-tick long tasks on scanner tables after T3.  
3. Network panel: cold `/scanner?mode=theta` should not request all five scan caches if T6 chooses active-only.  
4. Playwright: existing scanner e2e (`web/e2e/theta-harvester-prefill.spec.ts` et al.) must stay green; add assert that mode chunk lazy-loads if feasible.  
5. Visual: chrome-cdp vs `docs/design-shots/skill-stack/baseline/baseline-*-scanner-*.png` — skeleton must match brand (matte panel, no glass).

---

## Top 5 (operator shortlist)

1. **Per-section code-split** — stop shipping admin/orders/ticker with scanner.  
2. **`optimizePackageImports: ['lucide-react']`** — free barrel win.  
3. **Decouple scanner render from `prices` ticks** — kill market-hours jank.  
4. **Real loading UI + Suspense** — replace `loading: () => null`.  
5. **Narrow five-way scanner GETs** — badges without full multi-endpoint fan-out.

---

## Appendix: key file paths

| Path | Role |
|---|---|
| `/Users/joemccann/dev/apps/finance/radon/web/app/scanner/page.tsx` | Route entry |
| `/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceShell.tsx` | Client shell, data + WS |
| `/Users/joemccann/dev/apps/finance/radon/web/components/WorkspaceSections.tsx` | Monolith section switch + `ScannerSections` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useSyncHook.ts` | Shared poll/GET primitive |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useScanner.ts` | `/api/scanner` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useThetaHarvester.ts` | `/api/scanner/theta` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useStrengthConfirmation.ts` | `/api/scanner/strength` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useLeap.ts` | `/api/leap` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useGarchConvergence.ts` | `/api/garch-convergence` |
| `/Users/joemccann/dev/apps/finance/radon/web/lib/useDiscover.ts` | `/api/discover` |
| `/Users/joemccann/dev/apps/finance/radon/web/next.config.mjs` | Missing package import optimization |
| `/Users/joemccann/dev/apps/finance/radon/web/components/dashboard/OpportunitiesCard.tsx` | Same multi-hook pattern on dashboard |

---

*Read-only audit. No application code changed.*
