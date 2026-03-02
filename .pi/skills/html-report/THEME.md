# Terminal Dashboard Theme

A precision-focused 'Terminal' style dashboard with both dark and light theme variants, utilizing a 1px grid system, monospaced data visualization, and monochromatic highlights. The layout prioritizes data density and legibility through a surgical application of whitespace and borders. Both themes preserve the same flat, high-density, keyboard-first aesthetic.

---

## Style

Monochromatic flat design principles across both dark and light modes. Typography pairs 'Satoshi' (sans-serif) for UI labels with 'JetBrains Mono' (monospaced) for numerical data and system status. The interface must support two visual modes:

* **Dark Theme**: Built on a near-black terminal palette.
* **Light Theme**: Built on a high-contrast paper-white terminal palette.

Features zero gradients, zero shadows, and strictly sharp 1px borders in both themes.

---

## Spec

### Visual Language & Aesthetic

* **Theme**: Monochromatic, High-Density Terminal/SaaS Dashboard.
* **Modes**: Must support both **Dark** and **Light** themes with identical layout, spacing, hierarchy, and component behavior.

---

### Color System

#### Dark Theme

| Token | Value |
|-------|-------|
| Background Base | `#050505` |
| Panel Background | `#0A0A0A` |
| Hover State | `#141414` |
| Border (Dim) | `#1C1C1C` |
| Border (Focus) | `#333333` |
| Primary Text | `#F0F0F0` |
| Muted Text | `#666666` |
| Accent/Inversion | `#FFFFFF` (background) with `#000000` (text) |

#### Light Theme

| Token | Value |
|-------|-------|
| Background Base | `#F5F5F5` |
| Panel Background | `#FFFFFF` |
| Hover State | `#F0F0F0` |
| Border (Dim) | `#D9D9D9` |
| Border (Focus) | `#A3A3A3` |
| Primary Text | `#0A0A0A` |
| Muted Text | `#6B6B6B` |
| Accent/Inversion | `#000000` (background) with `#FFFFFF` (text) |

---

### Typography

| Element | Font | Weight | Size | Notes |
|---------|------|--------|------|-------|
| Headings & UI | Satoshi | 500 or 700 | — | Sans-serif for hierarchy |
| Data & Numbers | JetBrains Mono | 400 | 11px–13px | All price points, timestamps, status logs |
| Captions | JetBrains Mono | 400 | 10px | Uppercase, `tracking-widest` for system-command look |

---

### UI Elements

#### Borders

* Strictly 1px solid theme border color
* No rounded corners (`border-radius: 0px`) on containers

#### Buttons

**Secondary:**
* Ghost style with 1px border
* Transitions to theme hover background on hover

**Primary:**
* Dark theme: Solid `#FFFFFF` background with `#000000` text
* Light theme: Solid `#000000` background with `#FFFFFF` text
* Bold, no radius in both themes

#### Animations

* **Pulse**: Status indicators pulse `opacity` from 1 to 0.4
* **Transitions**: `150ms ease-in-out` for hover states

#### Scrollbar

| Property | Dark Theme | Light Theme |
|----------|------------|-------------|
| Width | 6px | 6px |
| Track | `#050505` | `#F5F5F5` |
| Thumb | `#333333` | `#A3A3A3` |
| Radius | 0 | 0 |

---

## Layout & Structure

A fixed sidebar layout with a multi-pane content area. The main workspace uses a 1px border grid to separate metrics, interactive charts, and data tables. Layout remains identical across both dark and light themes.

---

### Sidebar Navigation

| Property | Value |
|----------|-------|
| Width | 220px |
| Background | Theme panel color |
| Right Border | 1px theme dim border color |

**Structure:**

* **Top section**: Logo/Brand name in monospace uppercase with a 12px square icon
  * Dark theme icon: white square
  * Light theme icon: black square
* **Navigation items**: 13px font size, vertical list, 1.5 padding
* **Active state**: Theme hover background
* **Footer section**: System status logs (Latency, Status) in 11px monospaced text with a pulsing status dot
  * Dark theme dot: white
  * Light theme dot: black

---

### Top Header Bar

| Property | Value |
|----------|-------|
| Height | 48px |
| Background | Theme panel color |
| Bottom Border | 1px theme dim border color |

**Structure:**

* **Left side**: Breadcrumb navigation in 12px monospace (e.g., `WORKSPACE / ANALYTICS`)
* **Right side**: Command-style search bar with 1px border, 11px font, placeholder `CMD+K to search...`
* Includes a gear icon for settings

---

### Metrics Grid

Four-column horizontal layout.

| Element | Style |
|---------|-------|
| Cell | 1px border, theme panel background |
| Header | 10px uppercase monospaced, tracking-widest |
| Value | 24px JetBrains Mono, tracking-tight |
| Footer | 11px mono, percentage change, top border 1px dim |

---

### Performance Chart Area

| Property | Value |
|----------|-------|
| Layout | 2/3 width |
| Header Height | 48px |

**Time-Period Selectors (1H, 1D, 1M, etc.):**

* Active selector uses inverted theme colors:
  * Dark theme: white background, black text
  * Light theme: black background, white text

**Visuals:**

* Surgical SVG line graph with no area fill, only stroke
  * Dark theme line: `#FFFFFF`
  * Light theme line: `#000000`
* Grid Lines: Horizontal lines using `stroke-dasharray='2 4'` with theme dim border color at 25% intervals
* Crosshair: Thin vertical and horizontal dashed line following cursor, with floating price tag using inverted theme colors

---

### Data Tables (Watchlist & Positions)

Full width or 1/3 split.

| Property | Style |
|----------|-------|
| Border | `border-collapse: collapse` |
| Headers | 10px monospace, muted text, uppercase, 12px padding |
| Rows | 1px bottom border, hover to theme hover background |
| Numbers | Right-aligned JetBrains Mono for vertical digit alignment |
| Pills | Status badges (e.g., `LONG 5x`) with 1px border, transparent background |

---

## Special Components

### Surgical Polyline Chart

A performance graph that avoids all 'friendly' visual cues like curves or gradients.

Create an SVG-based line chart where the line is a single sharp stroke with no glow, no fill, and no smoothing beyond the browser default.

| Property | Dark Theme | Light Theme |
|----------|------------|-------------|
| Line Color | `#FFFFFF` | `#000000` |
| Line Width | 1.5px | 1.5px |

* Disable anti-aliasing if possible or use `vector-effect='non-scaling-stroke'`
* Background grid: 1px dashed using theme dim border color
* Y-axis labels: 10px mono, left-aligned, financial increments (e.g., `2.5M`)

---

### Command Search Input

Technical search bar designed for keyboard-first users.

| Property | Dark Theme | Light Theme |
|----------|------------|-------------|
| Width | 256px | 256px |
| Background | `#050505` | `#F5F5F5` |
| Border | 1px dim | 1px dim |
| Border (Focus) | `#FFFFFF` | `#000000` |
| Padding | 4px horizontal, 28px left (icon) | — |
| Font | 11px JetBrains Mono | — |
| Icon | Lucide search, 12px | — |

---

## Theme Behavior

* Support manual theme switching between **Dark** and **Light**
* Both themes must feel like the same product, not two different visual systems
* Do not introduce color accents, gradients, blur, softness, or rounded modern UI conventions in either mode
* Preserve the same data-dense terminal aesthetic in both themes
* Use semantic theme tokens so every component can swap colors cleanly without changing spacing, typography, or structure

---

## Theme Tokens

Use semantic tokens rather than hardcoded per-component colors:

```css
:root {
  --bg-base: ;
  --bg-panel: ;
  --bg-hover: ;
  --border-dim: ;
  --border-focus: ;
  --text-primary: ;
  --text-muted: ;
  --accent-bg: ;
  --accent-text: ;
}
```

### Dark Theme Values

```css
[data-theme="dark"] {
  --bg-base: #050505;
  --bg-panel: #0A0A0A;
  --bg-hover: #141414;
  --border-dim: #1C1C1C;
  --border-focus: #333333;
  --text-primary: #F0F0F0;
  --text-muted: #666666;
  --accent-bg: #FFFFFF;
  --accent-text: #000000;
}
```

### Light Theme Values

```css
[data-theme="light"] {
  --bg-base: #F5F5F5;
  --bg-panel: #FFFFFF;
  --bg-hover: #F0F0F0;
  --border-dim: #D9D9D9;
  --border-focus: #A3A3A3;
  --text-primary: #0A0A0A;
  --text-muted: #6B6B6B;
  --accent-bg: #000000;
  --accent-text: #FFFFFF;
}
```

Dark and light themes should only differ through these tokens, while all sizing, layout, typography, and component rules remain shared.
