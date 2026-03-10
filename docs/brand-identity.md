# Radon Brand Identity — Design System Reference

**"Reconstructing market structure from noisy signals."**

This document is the enforced reference for all UI and asset work. The full brand specification lives in `brand/radon-brand-system.md`. Design tokens, Tailwind theme, component kit, terminal mockup, and logo assets live in `brand/`.

---

## Brand Assets Index

| File | Purpose |
|------|---------|
| `brand/radon-brand-system.md` | Full technical specification (9 sections) |
| `brand/radon-design-tokens.json` | Machine-readable design tokens |
| `brand/radon-tailwind-theme.ts` | Tailwind CSS theme extension |
| `brand/radon-component-kit.html` | Live component reference (panels, badges, tables, motifs) |
| `web/components/kit/` | React component kit (viewable at `/kit` route) |
| `brand/radon-terminal-mockup.html` | Full terminal layout mockup |
| `brand/radon-app-icon.svg` | App icon (1024x1024) |
| `brand/radon-monogram.svg` | Monogram (512x512) |
| `brand/radon-wordmark.svg` | Wordmark with tagline |
| `brand/radon-lockup-horizontal.svg` | Horizontal lockup (icon + name + sub-label) |
| `brand/radon-readme-hero.svg` | README hero banner |

---

## 1. Naming Hierarchy

| Legacy | Radon Name | Role |
|--------|-----------|------|
| Convex Scavenger | **Radon Terminal** | Primary operating environment |
| Scanner | **Radon Flow** | Flow reconstruction and event isolation |
| Alerts | **Radon Signals** | Signal detection, ranking, state changes |
| Exposure View | **Radon Exposure** | Risk and positioning surfaces |
| Vol View | **Radon Surface** | Volatility surface and dislocation analysis |
| Regime View | **Radon Structure** | Cross-asset structure and state reconstruction |
| Watchlists | **Radon Sets** | Saved universes and instrument groups |

---

## 2. Core Palette — The Radon Spectrum

| Token | Hex | Role |
|-------|-----|------|
| `bg.canvas` | `#0a0f14` | Primary background |
| `bg.panel` | `#0f1519` | Instrument panels |
| `bg.panelRaised` | `#151c22` | Hover / focus panel |
| `line.grid` | `#1e293b` | Grid and borders |
| `text.primary` | `#e2e8f0` | Primary text |
| `text.secondary` | `#94a3b8` | Secondary text |
| `text.muted` | `#475569` | Meta / supporting text |
| `signal.core` | **`#05AD98`** | **Core Radon discovery layer (flagship accent)** |
| `signal.strong` | `#0FCFB5` | High-confidence signal |
| `signal.deep` | `#048A7A` | Deep data / selected states |
| `warn` | `#F5A623` | Quality / caution |
| `fault` | `#E85D6C` | Feed fault / integrity problem |
| `violet.extreme` | `#8B5CF6` | Extreme dislocation / rare state |
| `magenta.dislocation` | `#D946A8` | Structural dislocation |
| `neutral` | `#94a3b8` | Neutral comparative states |

### CSS Variable Mapping

The palette is implemented as CSS custom properties in `web/app/globals.css` with both dark and light themes. Components use `var(--token)` so they auto-adapt to the active theme. The following variables map to signal tokens:

| CSS Variable | Dark | Light | Token |
|-------------|------|-------|-------|
| `--signal-core` | `#05AD98` | `#05AD98` | `signal.core` |
| `--signal-strong` | `#0FCFB5` | `#048A7A` | `signal.strong` |
| `--signal-deep` | `#048A7A` | `#037066` | `signal.deep` |
| `--dislocation` | `#D946A8` | `#C026A0` | `magenta.dislocation` |
| `--extreme` | `#8B5CF6` | `#7C3AED` | `violet.extreme` |
| `--fault` | `#E85D6C` | `#D4183D` | `fault` |
| `--neutral` | `#94a3b8` | `#475569` | `neutral` |
| `--text-secondary` | `#94a3b8` | `#636363` | `text.secondary` |

### Light Theme Palette

| Token | Hex | Role |
|-------|-----|------|
| `bg.canvas` | `#FFFFFF` | Primary background |
| `bg.panel` | `#FFFFFF` | Instrument panels |
| `bg.panelRaised` | `#F1F5F9` | Hover / focus panel |
| `line.grid` | `#BBBFBF` | Grid and borders |
| `border.focus` | `#05AD98` | Focus ring / active border |
| `text.primary` | `#000000` | Primary text |
| `text.secondary` | `#636363` | Secondary text |
| `text.muted` | `#878787` | Meta / supporting text |
| `signal.core` | `#05AD98` | Core accent (unchanged) |
| `signal.strong` | `#048A7A` | High-confidence signal (darkened for contrast) |
| `signal.deep` | `#037066` | Deep data / selected states |
| `positive` | `#048A7A` | Positive value |
| `negative` | `#D4183D` | Negative value / fault |
| `dislocation` | `#C026A0` | Structural dislocation |
| `extreme` | `#7C3AED` | Extreme dislocation / rare state |
| `neutral` | `#475569` | Neutral comparative states |

Light theme values are tuned for WCAG contrast on white/light surfaces. Signal colors shift darker; semantic colors shift more saturated.

### Signal Semantics (clarity scale, not P&L)

| State | Meaning | Color |
|-------|---------|-------|
| Baseline | No notable structure isolated | `neutral` |
| Emerging | Weak but non-random candidate | `signal.deep` |
| Clear | Strong structural candidate | `signal.core` |
| Strong | High-confidence reconstruction | `signal.strong` |
| Dislocated | Market structure notably out of line | `magenta.dislocation` |
| Extreme | Rare regime / high-convexity event | `violet.extreme` |

### Volatility Dislocation Logic

- **Teal family** = recovered or clarified structure, signal clarity
- **Magenta/Violet family** = tension, dislocation, or instability
- **Amber** = incomplete confidence / data quality concern
- **Red/Pink fault** = operational issue, not market P&L

---

## 3. Typography

| Use | Font | Size | Weight | Tracking | Leading |
|-----|------|-----:|-------:|----------|--------:|
| Product wordmark | Söhne / Inter | 22-28px | 600 | +1% | 1.1 |
| View title | Inter | 18px | 600 | +1% | 1.2 |
| Panel title | Inter | 14px | 600 | +2% | 1.2 |
| Section label | Inter | 12px | 600 | +4% | 1.2 |
| Metric value | Inter | 24-32px | 500/600 | 0% | 1.05 |
| Dense numeric table | IBM Plex Mono | 12-13px | 500 | +1% | 1.35 |
| Status/meta | IBM Plex Mono | 11-12px | 400/500 | +3% | 1.35 |
| Annotation | Inter | 12px | 400 | +1% | 1.4 |

**Rules:**
- Numbers and telemetry use **mono**; narrative UI uses **sans**
- Status/meta fields read like instrument telemetry (uppercase, `+3%` tracking, muted color)
- Avoid excessive weight contrast — use structure, not loud typography, for hierarchy

---

## 4. Container System: Instrument Panels

| Property | Spec |
|----------|------|
| Background | `bg.panel` (`#0f1519`) |
| Border | `1px solid line.grid` (`#1e293b`) |
| Corner radius | **4px max** |
| Shadow | **None** |
| Padding | 16px (compact: 12px) |
| Header row | 32px fixed height |
| Metadata rail | 20-24px |
| Hover | Background shift to `bg.panelRaised`, not lift |
| Accent | Thin signal trace or calibrated edge marker (2px left border gradient) |

### Panel Rules

- Use **hairline borders**, not soft shadows
- Favor **matte panel surfaces** with internal grid discipline
- Panel headers read like device labels (module ID + name)
- Panel metadata rails expose: sampling rate, engine source, confidence, time basis
- Modules should feel **mountable** — as if they could slide into a rack

---

## 5. Grid System

| Property | Value |
|----------|-------|
| Base unit | **8px** |
| Micro unit | **4px** |
| Columns (laptop) | 12 |
| Columns (desktop) | 16 |
| Columns (ultra-wide) | 24 |
| Panel spans | 4 / 8 / 12 / 16 / full |
| Horizontal gutter | 16px |
| Vertical rhythm | 16px |
| Section gap | 32px |
| Dense table row | 28px |
| Standard table row | 32px |

---

## 6. Instrument Motifs

Use sparingly in background layers, loading states, and drill-down transitions.

| Motif | Description | Use |
|-------|-------------|-----|
| **Spectral decomposition lines** | Parallel emission-like lines implying decomposition | Loading, factor/signal decomposition |
| **Circular scanning arcs** | Radar/detector rings suggesting sampling sweeps | Discovery states, scan progress |
| **Projection geometry** | Angled projection lines (Radon transform-inspired) | Hero backgrounds, engine overlays, regime views |

**Motif rules:**
- Keep opacity low — structural, not decorative
- Never compromise data readability
- Motion should be slow, analytical, confidence-increasing — not flashy

---

## 7. Engine Visualization

| Engine | Meaning | Visual Grammar | Behavior |
|--------|---------|---------------|----------|
| **Spectral** | Decomposition into components/frequencies | Spectral bars, energy bands, line stacks | Animate through decomposition passes |
| **Eigen** | Principal directions, correlation geometry | Matrix field, vector axes, eigenplanes | Reveal dominant components and rank |
| **Markov** | Regime transitions, state probabilities | Node graph, transition arcs, state lattice | Show prior → current → likely next state |
| **Laplace** | Curvature, convexity, local instability | Contour surfaces, curvature maps, field lines | Highlight local pressure and payoff curvature |

---

## 8. Brand Voice

**Radon's voice is:** precise, calm, scientific, unsensational, informative under stress.

### Alert Language

| Bad | Radon |
|-----|-------|
| Massive trade alert! | **Structural event detected.** |
| Huge gamma squeeze incoming | **Convexity concentration elevated.** |
| This ticker is exploding | **Volatility state shifted beyond baseline range.** |
| Something went wrong | **Data reconstruction failed. Source feed incomplete.** |

### Error Pattern

`[System] + [Failure] + [Cause if known] + [Recovery guidance if useful]`

- **Flow module unavailable. Upstream feed timed out.**
- **Signal reconstruction incomplete. Required options surface missing.**
- **Exposure view delayed. Last valid sample: 09:41:12 ET.**

### Tone Rules

- Prefer nouns and verbs over adjectives
- No emotional punctuation
- Never use emojis
- Avoid "huge," "massive," "crazy," "exploding," "insane"
- Confidence and source are better than hype

---

## 9. Contributor Acceptance Criteria

A contributed component is rejected unless it:

1. Uses approved design tokens only (see `brand/radon-design-tokens.json`)
2. Snaps to the Radon grid (8px base, 4px micro)
3. Exposes state clearly via semantic color
4. Supports both dark and light themes via CSS variables (dark-first design)
5. Keeps numerical alignment intact (mono font, right-aligned decimals)
6. Avoids stylistic drift: no glassmorphism, heavy gradients, or soft consumer shadows
7. Uses `4px max` border-radius (badges use `999px` capsule)
8. Panel containers follow the instrument panel spec (Section 4)
9. System messages follow brand voice (Section 8)
10. Empty states describe the measurement condition, not generic placeholders
