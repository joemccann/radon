# Radon Chart System

This document is the sanctioned chart-family and renderer contract for Radon.

The machine-readable source of truth is [web/lib/chart-system-spec.json](../web/lib/chart-system-spec.json). Runtime helpers live in [web/lib/chartSystem.ts](../web/lib/chartSystem.ts). Downstream OG/report surfaces consume the same contract via [web/lib/og-theme.ts](../web/lib/og-theme.ts), [web/lib/og-charts.tsx](../web/lib/og-charts.tsx), and [scripts/performance_explainer_report.py](../scripts/performance_explainer_report.py).

## Purpose

Radon does not have "one chart type." It has four sanctioned chart families that share one visual language:

- matte panel surfaces
- 1px instrument borders
- mono axis labeling and telemetry copy
- semantic series colors tied to signal meaning
- renderer choices constrained by product behavior, not individual developer preference

This keeps `/performance`, `/regime`, OG images, and HTML reports visually aligned while still allowing different chart behaviors where the product requires them.

## Source Of Truth

Use the chart-system JSON first when you need:

- chart family labels
- sanctioned renderer rules
- surface radius and header/padding contract
- axis typography contract
- semantic series roles and fallback colors

Use the brand system for:

- literal dark/light surface colors
- non-chart UI tokens
- layout, panel, and typography rules outside the plotting surface

Reason: runtime React can resolve CSS variables, but OG images and Python-generated reports cannot. Downstream surfaces therefore use the chart-system semantic fallbacks plus brand literals for fixed surfaces.

## Sanctioned Families

| Family ID | Label | Default Renderer | Interaction | Axes |
|-----------|-------|------------------|-------------|------|
| `live-trace` | Live Trace | `canvas-adapter` | scrub | optional |
| `analytical-time-series` | Analytical Time Series | `svg` | inspect | required |
| `distribution-bar` | Distribution Bar | `html-css-or-svg` | static | no |
| `matrix-heatmap` | Matrix Heatmap | `html-css-or-svg` | static | no |

### Family Notes

`live-trace`
- Use for dense, operator-driven price traces where scrubbing is the main product value.
- Canvas is allowed because the interaction model is more important than DOM-level axis composition.

`analytical-time-series`
- Default family for `/performance`, `/regime`, and similar operator charts.
- Axes are not optional. If time or magnitude drives interpretation, visible scale context is mandatory.

`distribution-bar`
- Use for stacked bars, exposure strips, compact histogram-like summaries, and gauge-style breakdowns.
- Prefer DOM/CSS when the operator benefits from reading labels directly from the layout.

`matrix-heatmap`
- Use for comparative grids like seasonality and percentile tables.
- The grid cell is the primitive; do not force these into line-chart scaffolds.

## Renderer Policy

| Renderer | Status | Rule |
|----------|--------|------|
| `svg` | default | Use for operator charts that need shared frame, axis, legend, and semantic series control. |
| `canvas-adapter` | allowed | Use only for high-frequency interactive traces where performance and scrub behavior dominate. |
| `d3-svg` | conditional | Use only when scale logic or interaction complexity materially exceeds the shared SVG primitives. |
| `html-css-or-svg` | allowed | Use for compact bars, gauges, matrix views, and telemetry-first visuals. |

### Rejection Rules

- Do not introduce a new charting library for a family already covered by the spec.
- Do not use `d3-svg` only for convenience if plain SVG can satisfy the chart.
- Do not hardcode one-off palette choices for downstream visuals. Use semantic roles.

## Semantic Series Roles

| Role | Meaning | Fallback |
|------|---------|----------|
| `primary` | primary structural signal | `#05AD98` |
| `comparison` | benchmark or baseline context | `rgba(148, 163, 184, 0.72)` |
| `caution` | elevated risk / warning context | `#F5A623` |
| `dislocation` | structural dislocation | `#D946A8` |
| `extreme` | rare or extreme state | `#8B5CF6` |
| `fault` | downside exception / fault state | `#E85D6C` |
| `neutral` | supporting neutral context | `#94A3B8` |

### Role Usage

- `primary` is the default Radon accent for the main thesis-bearing line or area.
- `comparison` is reserved for rebased benchmarks, baselines, prior state, or reference overlays.
- `caution`, `dislocation`, and `extreme` are semantic escalation states, not decorative accents.
- `fault` is for downside exceptions, feed faults, or explicit adverse conditions.

## Shared Surface Contract

The chart-system spec standardizes:

- radius: `4px`
- panel padding: `16px`
- header height: `32px`
- axis font: `IBM Plex Mono`
- axis font size: `10px`

All chart shells should preserve those values unless a surface has a documented, product-specific exception.

## Adoption Status

The current execution scope now covers both runtime and downstream chart surfaces:

- Runtime chart shells for `/performance` and `/regime` now use the shared `ChartPanel` and `ChartLegend` primitives for `analytical-time-series` panels.
- Runtime chart colors now resolve through semantic chart roles in the shared chart system instead of one-off series hex values.
- The live trace price chart still keeps its specialized canvas renderer, but it now resolves its positive/negative strokes through the shared semantic roles.
- OG image primitives now inherit semantic series roles plus axis/frame rules from the shared chart system.
- The generic MenthorQ OG image route currently uses the shared `analytical-time-series` shell and `svg` renderer fallback until command-specific family mappings are registered.
- The `/performance` HTML explainer/report now labels the equity curve as an `analytical-time-series` surface using the sanctioned `svg` renderer and the `primary` plus `comparison` semantic roles.

## Practical Guidance

When adding or refactoring a chart:

1. Pick the family first.
2. Confirm the renderer is sanctioned for that family.
3. Assign each visible series a semantic role.
4. Reuse the shared shell, axis, and legend contract where applicable.
5. Only then implement chart-specific math or interaction behavior.

If a chart does not fit one of the four families, update the audit and spec before introducing a fifth family.
