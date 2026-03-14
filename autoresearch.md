# Autoresearch: Web Dashboard Bundle Size Optimization

## Status: 1124KB → 925KB (−17.7% raw), 281KB → 264KB gzip (−6.0%), 80KB → 73KB CSS (−8.8%)

Session 9: lucide-react 0.577 upgrade (−1KB gzip), formatter consolidation (PnlBreakdown, Attribution, positionUtils), removed 8 unused CSS properties + 8 dead CSS rules from legacy CtaTables. Next.js 16.2 canary tested: +59KB regression. SortTh extraction tested: raw −572B but gzip +244B. Remaining: 456KB framework, 110KB polyfills, 337KB app code, 22KB small chunks.

## Objective
Minimize client-side JavaScript bundle size for the Radon Terminal web dashboard (Next.js 16 / Turbopack / React 19). The dashboard is at `web/` and serves a real-time trading terminal with charts, options chains, order management, and regime analysis.

## Metrics
- **Primary**: `bundle_kb` (KB, lower is better) — total client JS in `.next/static/`
- **Secondary**: `build_s` — build time in seconds, `css_kb` — CSS size, `chunk_count` — number of JS chunks

## Baseline
- **1,125 KB** total client JS (16 chunks)
- **80 KB** CSS
- **~7.6s** build time
- Largest chunks: 540KB (markdown ecosystem), 220KB (Next.js internals), 124KB (React), 112KB (app code)

## How to Run
`./autoresearch.sh` — cleans `.next/`, builds, measures JS/CSS sizes, outputs `METRIC` lines.

## Files in Scope
- `web/components/*.tsx` — UI components (23K lines total across 42 files)
- `web/components/ticker-detail/*.tsx` — Ticker detail page tabs
- `web/lib/*.ts` — Utility libraries, hooks, types
- `web/app/**/*.tsx` — Page components and API routes
- `web/next.config.ts` — Next.js/Turbopack configuration
- `web/package.json` — Dependencies
- `web/tailwind.config.ts` — Tailwind CSS config (if it exists)

## Off Limits
- `web/tests/` — Test files must not be modified
- `web/e2e/` — E2E tests must not be modified
- `web/public/` — Static assets
- `scripts/` — Python backend scripts
- `lib/tools/` — Pi tool definitions
- Do NOT change the visual output or behavior of any component
- Do NOT remove features

## Constraints
- All 95 vitest tests must pass (9 are pre-existing failures — those 9 may remain failing but no NEW failures)
- Build must succeed (`npm run build` exit 0)
- No new npm dependencies (removing deps is fine)
- Functionality must be preserved — same visual output, same interactivity

## Key Findings (Pre-Session Analysis)

### Chunk Breakdown
| Chunk | Size (KB) | Contents |
|-------|-----------|----------|
| a893bf... | 540 | **Markdown ecosystem** (micromark, mdast, hast, unified, remark-gfm, react-markdown) |
| 5a795e... | 220 | Next.js framework internals |
| f201dd... | 124 | React 19 runtime |
| a6dad9... | 112 | Application code + components |
| Others | 129 | Small chunks (Turbopack runtime, manifests, etc.) |

### High-Impact Targets
1. **react-markdown + remark-gfm (540KB)** — Used in ONE component (`ChatPanel.tsx → MarkdownRenderer.tsx`). The entire unified/micromark/mdast/hast ecosystem is bundled for rendering chat messages. Replace with lightweight alternative or lazy-load.
2. **d3 (full import)** — `import * as d3` in 2 components (`CriHistoryChart.tsx`, `RegimeRelationshipView.tsx`). Only uses ~8 functions from d3-selection, d3-scale, d3-shape, d3-time-format, d3-array. Selective imports could save significant weight.
3. **lucide-react (43MB node_modules)** — 26 icons used. Tree-shaking should handle this, but verify.

### Lower-Impact Targets
- `@fontsource/ibm-plex-mono` — font file bundling
- `@sinclair/typebox` — schema validation library
- Component code splitting — large components that could be lazy-loaded

## What's Been Tried

### Wins (1124KB → 920KB, −18.2% raw / 281KB → 264KB gzip, −6.0%)
1. Replace react-markdown + remark-gfm with 7KB inline renderer: **−137KB** (biggest single win)
2. d3 selective imports (`import * as d3` → d3-subpackages): **−16KB**
3. Rewrite CriHistoryChart from imperative d3 DOM to React SVG: **−13KB** (removes d3-selection + d3-axis)
4. Replace d3-scale with 3.9KB scales.ts: **−31KB** (removes 6 transitive deps)
5. Replace d3-shape with 2.6KB svgPath.ts: **−5KB**
6. Replace d3-array with 1.5KB arrayUtils.ts: **0KB** (tree-shaking already handled)
7. SWC removeConsole: **−1KB**
8. Dep cleanup: removed @fontsource/ibm-plex-mono, @vercel/analytics, ib, d3 umbrella, moved ws/@sinclair/typebox to devDeps
9. Strip data-testid from production build via SWC reactRemoveProperties: **−1KB**
10. Remove 37 dead CSS rule blocks: **−3KB CSS**
11. Consolidate 12 duplicate fmt* functions into shared lib/format.ts: **0KB** (minifier already deduped)

### Dead ends
- Dynamic imports (ChatPanel, MetricCards, WorkspaceSections, PriceChart, ticker tabs): +4-13KB overhead from chunk wrappers. Turbopack ships all code regardless; splitting only adds wrapper cost.
- optimizePackageImports / modularizeImports: Turbopack already handles tree-shaking.
- reactStrictMode: false: no effect on production.
- Removing dead packages: no bundle change (Turbopack tracks imports).
- Replace d3-time-format with Intl.DateTimeFormat: 0KB (already tree-shaken to one function).

### Session 3 additions
9. Remove 43 dead CSS rules (old chat, connection-banner, regime-cta, toast systems): **−3KB CSS**
10. Remove dead code: store.ts (zustand dep), useIBStatus.ts: **0KB** (Turbopack already excluded)

### Dead ends (session 3)
- .browserslistrc modern browsers: polyfill chunk unchanged (+6KB app chunk from SWC output change)
- experimental.optimizeCss: no effect
- Replace lucide-react with inline SVG icons: +6KB — lucide's shared factory minifies better
- Remove "use client" from pure-render components: all imported by client parents, no savings

### Current chunk breakdown (920KB)
| Component | Size | % | Actionable? |
|-----------|------|---|-------------|
| App code | 336KB | 37% | Only via feature removal |
| Next.js | 220KB | 24% | Framework — untouchable |
| React | 124KB | 13% | Framework — untouchable |
| React DOM | 112KB | 12% | Framework — untouchable |
| Other chunks | 129KB | 14% | Router, lucide, manifests |

The 336KB app chunk contains ~39KB of liveline (canvas charting), ~32KB lucide icons, and ~265KB of actual Radon component/hook/utility code.

