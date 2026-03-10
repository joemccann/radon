# Radon Design System Rules — Figma MCP Integration

> This document instructs the Figma MCP server on how to translate Figma designs into code that matches the Radon codebase. All design-to-code output MUST follow these rules.

---

## 1. Token Definitions

### Source of Truth

| File | Purpose |
|------|---------|
| `brand/radon-design-tokens.json` | Machine-readable tokens (colors, spacing, typography, radii) |
| `brand/radon-tailwind-theme.ts` | Tailwind CSS theme extension |
| `web/app/globals.css` | CSS custom properties (runtime theming) |

### Color Tokens — The Radon Spectrum (Dual Theme)

All colors MUST reference CSS variables. Never use raw hex values in components. Components auto-adapt to dark/light theme via `var(--token)`.

#### Dark Theme (default)

```
/* Surfaces */
--bg-base:       #0a0f14   (canvas background)
--bg-panel:      #0f1519   (instrument panel background)
--bg-hover:      #151c22   (raised/hover panel state)
--border-dim:    #1e293b   (grid lines, hairline borders)
--border-focus:  #048A7A   (focus ring, active border)

/* Text */
--text-primary:    #e2e8f0
--text-secondary:  #94a3b8
--text-muted:      #475569  (meta/supporting)

/* Signal — clarity scale, NOT profit/loss */
--signal-core:     #05AD98  — flagship accent, core discovery layer
--signal-strong:   #0FCFB5  — high-confidence signal
--signal-deep:     #048A7A  — deep data, selected states

/* Semantic */
--positive:        #05AD98
--negative:        #E85D6C
--fault:           #E85D6C  — feed fault/integrity problem
--dislocation:     #D946A8  — structural dislocation
--extreme:         #8B5CF6  — extreme dislocation/rare state
--neutral:         #94a3b8  — neutral comparative states
```

#### Light Theme

```
/* Surfaces */
--bg-base:       #FFFFFF
--bg-panel:      #FFFFFF
--bg-hover:      #F1F5F9
--border-dim:    #BBBFBF
--border-focus:  #05AD98

/* Text */
--text-primary:    #000000
--text-secondary:  #636363
--text-muted:      #878787

/* Signal */
--signal-core:     #05AD98
--signal-strong:   #048A7A  (darkened for contrast on light bg)
--signal-deep:     #037066

/* Semantic */
--positive:        #048A7A
--negative:        #D4183D
--fault:           #D4183D
--dislocation:     #C026A0
--extreme:         #7C3AED
--neutral:         #475569
```

### Mapping Figma Colors to Code

When translating Figma fills to code, always use CSS variables (`var(--signal-core)`, `var(--bg-panel)`, etc.) so components work in both themes. Reference hex values for Figma-side matching:

**Dark theme mapping:**
- Teal shades → `#05AD98` (core), `#0FCFB5` (strong), `#048A7A` (deep)
- Purple/violet → `#8B5CF6` (extreme)
- Pink/magenta → `#D946A8` (dislocation)
- Amber/yellow → `#F5A623` (warn)
- Red/coral → `#E85D6C` (fault)
- Dark backgrounds → `#0a0f14` (canvas), `#0f1519` (panel), `#151c22` (raised)
- Borders → `#1e293b` (hairline only)
- Text → `#e2e8f0` (primary), `#94a3b8` (secondary), `#475569` (muted)

**Light theme mapping:**
- Teal shades → `#05AD98` (core), `#048A7A` (strong), `#037066` (deep)
- Purple/violet → `#7C3AED` (extreme)
- Pink/magenta → `#C026A0` (dislocation)
- Red → `#D4183D` (fault/negative)
- Light backgrounds → `#FFFFFF` (canvas), `#FFFFFF` (panel), `#F1F5F9` (raised)
- Borders → `#BBBFBF` (hairline only)
- Text → `#000000` (primary), `#636363` (secondary), `#878787` (muted)

### Spacing System

```
4px   — micro unit (fine adjustments)
8px   — base unit (standard spacing)
12px  — compact padding
16px  — panel padding, horizontal gutters
24px  — comfortable spacing
32px  — section gaps
```

Grid: 8px base, 4px micro. All spacing snaps to multiples of 4px.

### Border Radius

```
4px   — panels, containers, inputs (MAXIMUM for panels)
999px — badges, pills, capsule shapes
6px   — focus rings only
```

**Critical rule**: Never exceed 4px border-radius on panels or containers. No soft consumer rounding.

---

## 2. Component Library

### Location

```
web/components/          — All React components (34 .tsx files)
web/components/ui/       — Minimal shared UI primitives (Skeleton.tsx)
web/components/ticker-detail/  — Ticker detail subtabs
```

### Architecture

- **Functional React components** (React 19) with hooks
- **`"use client"`** directive for interactive components
- **No component library** (no shadcn, no MUI, no Radix) — native elements styled with CSS
- **No Storybook** — use `brand/radon-component-kit.html` as visual reference

### Key Components

| Component | Purpose |
|-----------|---------|
| `WorkspaceShell.tsx` | App shell (sidebar + header + content) |
| `MetricCards.tsx` | Metric dashboard cards |
| `PositionTable.tsx` | Portfolio data table |
| `RegimePanel.tsx` | Market regime display |
| `Modal.tsx` | Base modal component |
| `Skeleton.tsx` | Loading skeleton |
| `Sidebar.tsx` | Navigation sidebar |
| `Header.tsx` | Top header bar |

### Component Pattern

```tsx
"use client";

import { useState, useEffect } from "react";
import { SomeIcon } from "lucide-react";

interface Props {
  data: SomeType;
  onAction?: () => void;
}

export default function ComponentName({ data, onAction }: Props) {
  const [state, setState] = useState<Type>(initial);

  return (
    <div className="panel-class" style={{ background: "var(--bg-panel)" }}>
      {/* Content */}
    </div>
  );
}
```

---

## 3. Frameworks & Libraries

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Next.js | 16.1.6 (App Router) |
| UI | React | 19.2.4 |
| Language | TypeScript | 5.6.3 |
| Styling | Tailwind CSS + CSS Variables | 3.4.17 |
| Icons | lucide-react | 0.544.0 |
| State | Zustand + Context API | — |
| Bundler | Turbopack (Next.js built-in) | — |
| Testing | Vitest + Playwright | — |

### Import Aliases

```typescript
"@/*"           → "./*"
"@/components/*" → "./components/*"
"@/lib/*"       → "./lib/*"
"@/app/*"       → "./app/*"
"@tools/*"      → "../lib/tools/*"
```

---

## 4. Asset Management

- **Fonts**: Google Fonts (JetBrains Mono) + Fontshare (Satoshi) via CSS `@import`
- **Static assets**: `web/public/fonts/` (JetBrains Mono TTF fallbacks)
- **Brand assets**: `brand/` directory (SVGs for logo, monogram, wordmark, lockup, hero)
- **No CDN** — assets served from Next.js public directory
- **No image optimization pipeline** — minimal static assets

---

## 5. Icon System

### Library: lucide-react

```tsx
import { Moon, Sun, ArrowUp, ChevronDown, AlertTriangle } from "lucide-react";

// Usage in JSX
<Moon size={14} />
<ArrowUp className="price-trend-icon" />
```

### Sizing Convention

| Context | Size |
|---------|------|
| Navigation icons | 14px |
| Inline indicators | 10-12px |
| Status/alert icons | 14-16px |

### Rules
- All icons from `lucide-react` — no custom SVG icon files
- Icons are inline with text, sized via `size` prop or `width`/`height`
- Color via `className` or `color` prop, referencing CSS variables
- No icon-as-decoration — icons serve functional purposes only

---

## 6. Styling Approach

### Methodology: CSS Variables + Tailwind Utilities + Global CSS Classes

The codebase uses a **hybrid approach**:
1. **CSS custom properties** (`var(--bg-panel)`) for theming and dynamic values
2. **Tailwind utilities** for layout and common patterns
3. **Global CSS classes** in `globals.css` for complex component styles
4. **Inline styles** with CSS variables for one-off customizations

### Theme System

```css
/* Dark mode (primary — dark-first design) */
[data-theme="dark"] {
  --bg-base: #050505;
  --bg-panel: #0a0a0a;
  --bg-hover: #141414;
  --border-dim: #1c1c1c;
  --text-primary: #f0f0f0;
  --text-muted: #757575;
  --positive: #22c55e;
  --negative: #ef4444;
  --warning: #f59e0b;
}
```

**Note**: The CSS variables in `globals.css` are the *current runtime values*. The Radon brand tokens (`brand/radon-design-tokens.json`) represent the *target design system*. When generating new code, prefer the Radon token values.

### Styling Patterns

```tsx
// Pattern 1: CSS classes from globals.css
<div className="metric-card">

// Pattern 2: Tailwind utilities
<div className="flex items-center gap-2 text-sm">

// Pattern 3: Inline styles with CSS variables
<td style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>

// Pattern 4: Conditional classes
<span className={value > 0 ? "positive" : "negative"}>
```

### Key Global CSS Classes

| Class | Purpose |
|-------|---------|
| `.app-shell` | Flex container, full viewport |
| `.sidebar` | Fixed-width nav panel |
| `.header` | Top bar, 48px height |
| `.content` | Scrollable main area |
| `.metric-card` | Dashboard metric card |
| `.metrics-grid` | 4-column metric layout |
| `.section-label-mono` | Mono uppercase section label |
| `.nav-item` | Sidebar navigation link |

### Responsive Design

- **Desktop-first** — designed for 1440px+ displays
- Grid columns: 12 (laptop), 16 (desktop), 24 (ultra-wide)
- No mobile breakpoints — this is a desktop trading terminal

### Animation

- Standard transition: `150ms ease-in-out`
- Pulse animation for live status indicators
- `prefers-reduced-motion` respected via media query
- Motion should be slow, analytical — never flashy

---

## 7. Project Structure

```
convex-scavenger/
├── web/                          # Next.js frontend
│   ├── app/                      # App Router pages + API routes
│   │   ├── api/                  # REST API endpoints
│   │   ├── dashboard/page.tsx    # Dashboard view
│   │   ├── portfolio/page.tsx    # Portfolio view
│   │   ├── orders/page.tsx       # Orders view
│   │   ├── regime/page.tsx       # Market regime view
│   │   ├── journal/page.tsx      # Trade journal
│   │   ├── scanner/page.tsx      # Watchlist scanner
│   │   ├── discover/page.tsx     # Market-wide discovery
│   │   ├── layout.tsx            # Root layout
│   │   └── globals.css           # Global styles + CSS variables
│   ├── components/               # React components
│   │   ├── WorkspaceShell.tsx    # App shell
│   │   ├── WorkspaceSections.tsx # Section router (~53KB)
│   │   ├── MetricCards.tsx       # Metric cards
│   │   ├── PositionTable.tsx     # Portfolio table
│   │   ├── RegimePanel.tsx       # Regime display
│   │   ├── Modal.tsx             # Base modal
│   │   ├── ui/Skeleton.tsx       # Loading skeleton
│   │   └── ticker-detail/        # Ticker subtabs
│   ├── lib/                      # Hooks, utilities, types
│   │   ├── types.ts              # TypeScript interfaces
│   │   ├── store.ts              # Zustand store
│   │   ├── usePrices.ts          # WebSocket price hook
│   │   └── usePortfolio.ts       # Portfolio data hook
│   ├── tailwind.config.ts        # Tailwind configuration
│   └── next.config.mjs           # Next.js config
├── brand/                        # Design system assets
│   ├── radon-design-tokens.json  # Machine-readable tokens
│   ├── radon-tailwind-theme.ts   # Tailwind theme extension
│   ├── radon-brand-system.md     # Full 9-section spec
│   ├── radon-component-kit.html  # Live component reference
│   ├── radon-terminal-mockup.html # Terminal layout mockup
│   └── radon-*.svg               # Logo assets
├── scripts/                      # Python backend scripts
├── lib/tools/                    # TypeBox wrappers for scripts
└── data/                         # JSON data files
```

---

## 8. Figma-to-Code Translation Rules

### MUST Follow

1. **Dark-first, dual-theme**: Designs target dark mode by default (canvas = `#0a0f14`), but components must use CSS variables (`var(--bg-base)`, `var(--text-primary)`, etc.) so they auto-adapt to light theme.
2. **4px max border-radius** on any panel or container. Badges = `999px` capsule.
3. **Hairline borders only**: `1px solid #1e293b`. No shadows, no elevation.
4. **Mono for machine**: Any numbers, timestamps, ticker symbols, or telemetry use `font-family: var(--font-mono)` (JetBrains Mono / IBM Plex Mono).
5. **Sans for product**: Titles, labels, descriptions use `font-family: var(--font-sans)` (Satoshi / Inter).
6. **Signal colors are clarity, not P&L**: Green = structure clarity, not profit. Red (`fault`) = operational issue, not loss.
7. **No decorative elements**: No glassmorphism, heavy gradients, soft shadows, or icons-as-decoration.
8. **Grid discipline**: All spacing multiples of 4px. Padding 16px. Gaps 8-16px. Section gaps 32px.
9. **Instrument panel containers**: Panels have device-label headers, metadata rails, and feel like mountable rack modules.
10. **Empty states**: Describe the measurement condition (e.g., "No flow signals detected in scan window"), not generic placeholders (e.g., "Nothing here yet").

### Typography Scale

When translating Figma text to code:

| Figma Size | Code Token | Font | Weight |
|-----------|------------|------|--------|
| 28-32px | metric | Sans | 500-600 |
| 18px | viewTitle | Sans | 600 |
| 14px | panelTitle | Sans | 600 |
| 13px | table | Mono | 500 |
| 12px | body | Sans | 400 |
| 11px | meta | Mono | 400-500 |
| 10px | (section label) | Mono | 500 |

### Code Generation Format

When generating React components from Figma designs:

```tsx
"use client";

import { useState } from "react";

// Use CSS variables for theming, Tailwind for layout
export default function InstrumentPanel() {
  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-dim)",
        borderRadius: 4,
        padding: 16,
      }}
    >
      {/* Panel header — device label style */}
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          color: "var(--text-muted)",
          marginBottom: 8,
        }}
      >
        MODULE · PANEL TITLE
      </div>

      {/* Content */}
      <div className="flex items-center gap-2">
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: "var(--text-primary)",
          }}
        >
          1,234.56
        </span>
      </div>
    </div>
  );
}
```

### DO NOT Generate

- CSS Modules (`.module.css` files)
- Styled Components or CSS-in-JS libraries
- Storybook files
- Test files (those are written separately via TDD)
- Mobile-responsive breakpoints
- Light mode overrides — components use CSS variables and auto-adapt to both themes
- `className` strings that don't exist in `globals.css` or Tailwind
