# Web Bundle Size — Ideas Backlog

## Applied (1124KB → 925KB raw / 281KB → 265KB gzip / 80KB → 74KB CSS)
- Replace react-markdown + remark-gfm with 7KB inline renderer (−137KB)
- Full d3 replacement: custom arrayUtils/svgPath/scales (−65KB total)
- CriHistoryChart rewrite from d3 DOM to React SVG (−13KB)
- SWC removeConsole + reactRemoveProperties (−2KB)
- Remove dead deps: @fontsource/ibm-plex-mono, @vercel/analytics, ib, zustand
- Move @sinclair/typebox, ws to devDependencies
- Remove 45+ dead CSS rules, 2 dead keyframe animations, unused CSS properties
- Remove dead code: store.ts, useIBStatus.ts
- Consolidate inline formatters → shared format.ts (−2KB): 20→14 toLocaleString calls
- Extract inline styles to CSS utility classes (−2KB JS, +1KB CSS):
  - .font-mono (29 fontFamily properties)
  - .text-secondary (24 color properties)
  - .text-muted (11 color properties)
  - .book-section-header (4 identical section headers)
  - .w-full (9 width:100% properties)
  - .uppercase (9 textTransform properties)

## Exhaustively explored and rejected (sessions 1–8)
- Dynamic imports: Turbopack adds +4-13KB chunk wrapper overhead
- All Next.js experimental flags: no effect under Turbopack
- .browserslistrc modern browsers: Turbopack ignores — polyfills unchanged
- experimental.optimizeCss / turbopackMinify / turbopackInferModuleSideEffects: no effect
- experimental.inlineCss: no change to JS
- .swcrc custom minification: Turbopack ignores .swcrc entirely
- Replace lucide-react with inline SVG: +6KB — factory pattern minifies better
- Remove "use client" from context files: Turbopack chunking unchanged
- Module boundary inlining: ~20 bytes/module — negligible
- React.lazy for liveline: +1KB chunk overhead + test failure
- Webpack bundler: 1193KB — 273KB LARGER than Turbopack
- Google Fonts @import to layout <head>: build crash
- String/JSX pattern dedup: <0.5KB net after refactor overhead
- sideEffects: false in package.json: no change — Turbopack already tree-shakes

## Remaining micro-optimizations (diminishing returns)
- Replace inline display:"flex",alignItems:"center" with .flex.items-center classes: ~128B net
- Replace inline fontSize:"10px" etc. with CSS classes: ~120B net
- Replace inline letterSpacing:"0.05em" with CSS class: ~60B net
- All remaining inline style properties total ~311 across 16 patterns
- Most are unique or combined with other dynamic values — can't fully extract

## True floor analysis (925KB)
| Component | Size | Notes |
|-----------|------|-------|
| Framework (React+ReactDOM+Next.js) | 456KB | Untouchable |
| Core-js polyfills | 110KB | Framework-generated, no config |
| App code (53 client components) | 337KB | Down from 348KB via format+style dedup |
| Kit page (dev-only) | 14KB | Not linked from any route |
| Error page provider dupe | 5KB | Turbopack error boundary |
| Small chunks | 3KB | Router, manifest, runtime |

Production deps: next, react, react-dom, lucide-react, liveline (5)
