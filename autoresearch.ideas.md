# Web Bundle Size — Ideas Backlog

## Applied (1124KB → 925KB raw / 281KB → 264KB gzip / 80KB → 73KB CSS)
- Replace react-markdown + remark-gfm with 7KB inline renderer (−137KB)
- Full d3 replacement: custom arrayUtils/svgPath/scales (−65KB total)
- CriHistoryChart rewrite from d3 DOM to React SVG (−13KB)
- SWC removeConsole + reactRemoveProperties (−2KB)
- Remove dead deps: @fontsource/ibm-plex-mono, @vercel/analytics, ib, zustand
- Move @sinclair/typebox, ws to devDependencies
- Remove 45+ dead CSS rules, 2 dead keyframe animations, unused CSS properties
- Remove dead code: store.ts, useIBStatus.ts
- Consolidate inline formatters → shared format.ts (−2KB)
- Extract inline styles to CSS utility classes (−2KB JS, +1KB CSS)
- Upgrade lucide-react 0.544→0.577 (−1KB gzip)
- Remove 8 unused CSS custom properties (−1KB CSS)
- Remove 8 dead CSS rules from legacy CtaTables

## Exhaustively explored and rejected (sessions 1–9)
- Dynamic imports: Turbopack adds +4-13KB chunk wrapper overhead
- All Next.js experimental flags: no effect under Turbopack
- Next.js 16.2.0-canary: +59KB REGRESSION — Turbopack regression in canary
- .browserslistrc / .swcrc: Turbopack ignores both
- Replace lucide-react with inline SVG: +6KB — factory pattern minifies better
- Remove "use client" from context files: no effect
- React.lazy for liveline: +1KB + test failure
- Webpack bundler: 1193KB — 273KB LARGER than Turbopack
- Google Fonts @import to layout <head>: build crash
- String/JSX pattern dedup: <0.5KB net after refactor overhead
- sideEffects: false in package.json: no effect
- Extract shared SortTh component: −572B raw but +244B gzip (module wiring hurts compression)

## Key insight: gzip vs raw tradeoffs
Extracting duplicated code to shared modules can HURT gzip even while reducing raw size.
Gzip already deduplicates repeated byte sequences efficiently. Module wiring overhead
(import/export) is less compressible than the inline repetitions it replaces.
→ Always check gzip, not just raw, before keeping a refactor.

## True floor analysis (925KB)
| Component | Size | Notes |
|-----------|------|-------|
| Framework (React+ReactDOM+Next.js) | 456KB | Untouchable |
| Core-js polyfills | 110KB | Framework-generated, no config |
| App code (53 client components) | 337KB | Optimally minified (0.70 source→bundle) |
| Kit page (dev-only) | 14KB | Not linked from any route |
| Error page provider dupe | 5KB | Turbopack error boundary |
| Small chunks | 3KB | Router, manifest, runtime |

Production deps: next, react, react-dom, lucide-react, liveline (5)

## Remaining: diminishing returns only
- Inline display:"flex",alignItems:"center" → CSS class: ~128B
- Inline fontSize values → CSS classes: ~120B
- letterSpacing → CSS class: ~60B
- 311 remaining inline style properties across 16 patterns
- 6 remaining toLocaleString("en-US") calls (timezone, date, volume, price) — specialized, can't consolidate
