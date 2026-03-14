# Web Bundle Size — Ideas Backlog

## Applied (1124KB → 920KB raw / 281KB → 264KB gzip / 80KB → 71KB CSS)
- Replace react-markdown + remark-gfm with lightweight inline renderer (−137KB raw)
- d3 selective imports instead of `import * as d3` (−16KB raw)
- Rewrite CriHistoryChart from imperative d3 DOM to declarative React SVG (−13KB)
- Replace d3-scale with 3.9KB scales.ts (−31KB, removes 6 transitive deps)
- Replace d3-shape with 2.6KB svgPath.ts (−5KB)
- Replace d3-array with 1.5KB arrayUtils.ts
- SWC removeConsole + reactRemoveProperties
- Remove dead dependencies: @fontsource/ibm-plex-mono, @vercel/analytics, ib, zustand
- Move @sinclair/typebox, ws to devDependencies
- Remove 45+ dead CSS rules (old chat, connection-banner, regime-cta, toast, fills systems)
- Remove dead code: store.ts, useIBStatus.ts
- Replace Tailwind with 22 inline utility classes + minimal CSS reset (−6KB CSS)
- Remove dead keyframe animations (last-price-flash-up/down) and unused utilities

## Explored and rejected (with reasons)
- Dynamic imports (ChatPanel, MetricCards, WorkspaceSections, PriceChart, tabs): +4-13KB overhead
- optimizePackageImports / modularizeImports: Turbopack already handles tree-shaking
- .browserslistrc modern browsers: polyfill chunk unchanged, app chunk grew 6KB
- experimental.optimizeCss: no effect
- Replace lucide-react with inline SVG: +6KB — lucide's factory pattern minifies better
- Remove "use client" from pure-render components: all imported by client parents
- Removing dead exports from lib files: tree-shaking already excludes them
- reactStrictMode: false: no effect

## Remaining (true floor — no actionable items)
- 456KB framework (React + ReactDOM + Next.js): untouchable
- 110KB core-js polyfills: shipped by framework, no config to remove
- 332KB app code: proportional to 51 client components (charts, tables, order management)
- 39KB liveline canvas charting: would require major rewrite to replace
- 71KB CSS: all rules verified as used, 12KB gzipped (compression handles repetition)

## Exhausted approaches (session 3+)
- optimizePackageImports for lucide-react: 0KB (Turbopack already tree-shakes)
- Dynamic import RegimeRelationshipView: +5KB (chunk wrapper overhead, confirmed dead end)
- Dead utils.ts exports (11 functions): 0KB (Turbopack already tree-shakes them)
- Dead CSS selectors: 0 found (all classes referenced)
- Move chat formatters server-side: ~5KB savings possible but changes API contract, too risky
- Server components: 34/36 need client interactivity, can't convert more
- CSS utility class extraction: 73KB→12KB gzipped, gzip already handles repetition efficiently
