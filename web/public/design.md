# Radon design.md

One file that teaches any agent how to build a page that looks and reads like Radon. Load this URL, link the public stylesheet, and use only the vocabulary documented here.

- Stylesheet: `https://app.radon.run/radon.css`
- Rendered example: `https://app.radon.run/design-example.html`
- Monogram asset: `https://app.radon.run/brand/radon-monogram.svg`

Radon is a market-structure reconstruction terminal: it surfaces convex options opportunities from dark pool and OTC flow, vol surfaces, and cross-asset positioning. Every artifact you build represents that instrument. The register is a lab instrument report, not a marketing page and not a SaaS dashboard.

## Scope

Use this file for artifacts produced outside the Radon codebase: trade evaluation briefs, flow reports, portfolio reviews, incident postmortems, proposals, one-off pages. Do not use it to restyle the Radon app itself; the app owns its own component system.

## Reader and task

The reader is an operator deciding whether to act on a reconstruction. Shape every page for two passes:

1. A ten second executive read: what was measured, what state it is in, what the decision is.
2. A detailed audit: the evidence tables, the comparison scales, the caveats, the data provenance.

Lead with the finding and the decision. Evidence supports the decision; it never buries it. Every figure carries its source and time basis. If a trade decision appears, present it as signal, then structure, then sizing math, then decision, and name any gate that fails.

## Voice

Precise, calm, scientific, unsensational. Prefer nouns and verbs over adjectives.

- Say "Structural event detected", never "Massive trade alert!".
- Say "Volatility state shifted beyond baseline range", never "This ticker is exploding".
- Errors read as `[System] + [Failure] + [Cause if known] + [Recovery guidance]`, for example "Flow module unavailable. Upstream feed timed out."
- No emojis, no exclamation points, no hype words (huge, massive, crazy, insane).
- No em dashes anywhere in copy. Use a period, a colon, or a comma.
- State probabilities and uncertainty honestly. Flag model-derived estimates as such.
- Empty states describe the measurement condition ("No block prints above threshold in this window"), never generic placeholders ("Nothing here yet").

## Color semantics

Color encodes signal clarity, never profit and loss and never decoration.

| State | Meaning | Class |
|---|---|---|
| Baseline | No notable structure isolated | `rd-baseline` |
| Emerging | Weak but non-random candidate | `rd-emerging` |
| Clear | Strong structural candidate | `rd-clear` |
| Strong | High-confidence reconstruction | `rd-strong` |
| Dislocated | Market structure notably out of line | `rd-dislocated` |
| Extreme | Rare regime, high-convexity event | `rd-extreme` |
| Warn | Incomplete confidence, data quality concern | `rd-warn` |
| Fault | Operational failure, not market P&L | `rd-fault` |

The teal family means recovered or clarified structure. Magenta and violet mean tension and dislocation. Amber means incomplete confidence. Red means an operational fault. Use `rd-delta-up` and `rd-delta-down` only for signed numeric changes.

Never introduce your own colors. The stylesheet's tokens are the entire palette. Do not write hex values, `rgb()`, or named colors in artifact markup.

## Observable rules

Each rule is checkable by looking at the rendered page.

1. Panels have hairline borders, matte surfaces, and at most 4px corner radius. No box shadows, no gradients, no glassmorphism.
2. Evidence tables use the full width available to them. Never squeeze a table to prose width when the page is wider.
3. All numerals in tables, stat values, and telemetry are set in the mono font with tabular figures. Numeric columns are right-aligned via `rd-num`.
4. Comparable values sit on a single scale. When peers are compared, use one `rd-bar` group so magnitudes read against each other.
5. Status and meta text reads like instrument telemetry: mono, uppercase, muted, small.
6. Hierarchy comes from structure and spacing, not from loud type. One `rd-title` per page.
7. Every page ends with a `rd-footer` naming data sources and the measurement window.
8. Dark is the default. For a light artifact set `data-theme="light"` on the `html` element; never hand-tune colors per theme.
9. Layout snaps to an 8px rhythm. Sections are separated by 32px; the grid gutter is 16px.
10. The decision or summary lives in one `rd-callout` near the end of the executive read, not scattered across the page.

## Named failure patterns

Recognize these and do not produce them.

- **SaaS gloss**: rounded cards above 4px radius, drop shadows, gradient headers, icon confetti. Radon panels are instruments, not cards.
- **P&L color leakage**: coloring a signal state green or red because a trade would make or lose money. Color is clarity state only.
- **Hype copy**: superlatives, exclamation marks, urgency language. The data carries the weight.
- **Prose-width tables**: an evidence table constrained to text column width with dead space beside it.
- **Split scales**: peer values plotted on separate axes or separate widgets so they cannot be compared.
- **Decoration motifs**: background illustrations, emoji bullets, stock imagery. The only permitted ornament is low-opacity structural geometry, and omitting it is always correct.
- **Orphaned figures**: a number with no source, no time basis, or no unit.

## Page skeleton

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Artifact title</title>
  <link rel="stylesheet" href="https://app.radon.run/radon.css" />
</head>
<body>
<div class="rd-page">
  <header class="rd-masthead">
    <span class="rd-wordmark">RADON</span>
    <span class="rd-meta">Artifact kind</span>
    <span class="rd-meta">Date, time basis, data status</span>
  </header>
  <h1 class="rd-title">Finding stated as a sentence</h1>
  <p class="rd-subtitle">One or two sentences of concrete, sourced context.</p>
  <!-- rd-stats strip, then rd-section blocks of evidence, then the rd-callout decision -->
  <footer class="rd-footer">
    <span class="rd-meta">Data sources and window</span>
    <span class="rd-meta">Radon</span>
  </footer>
</div>
</body>
</html>
```

## Class vocabulary

This is the complete permitted vocabulary. Do not invent classes and do not write inline styles other than `style="width: N%"` on `rd-bar-fill`.

### Page structure

| Class | Use |
|---|---|
| `rd-page` | Root container. Canvas background, max width 1120px. |
| `rd-section` | Top-level block. Provides the 32px section gap. |
| `rd-masthead` | Header row: wordmark plus meta fields, hairline bottom border. |
| `rd-wordmark` | The RADON wordmark with the signal tick. Text content is `RADON`. |
| `rd-title` | Page title. One per page. |
| `rd-subtitle` | Supporting sentence under the title. |
| `rd-footer` | Provenance row at the end of the page. |

### Typography helpers

| Class | Use |
|---|---|
| `rd-meta` | Mono uppercase telemetry text: timestamps, sources, sampling notes. |
| `rd-label` | Uppercase section label above a panel or group. |
| `rd-note` | Small muted annotation: caveats, model uncertainty, methodology. |

### Layout

| Class | Use |
|---|---|
| `rd-grid` | 12-column grid, 16px gutter. |
| `rd-span-4` `rd-span-6` `rd-span-8` `rd-span-12` | Column spans inside `rd-grid`. Collapse to full width on narrow viewports. |

### Panels

| Class | Use |
|---|---|
| `rd-panel` | Instrument panel: panel surface, hairline border, 4px radius, 16px padding. |
| `rd-panel-header` | Device-label header row inside a panel. |
| `rd-panel-title` | Panel name inside the header. |
| `rd-panel-rail` | Metadata rail at the bottom of a panel for sampling and confidence notes. |

### Stat strip

| Class | Use |
|---|---|
| `rd-stats` | Responsive strip of stat modules. |
| `rd-stat` | One stat module. |
| `rd-stat-label` | Mono uppercase label above the value. |
| `rd-stat-value` | The metric value. Large, tabular numerals. |
| `rd-stat-note` | Comparison or context line under the value. |

### Tables

| Class | Use |
|---|---|
| `rd-table` | Evidence table. Mono, dense 32px rows, hairline row separators. Give it the full available width. |
| `rd-num` | Right-aligned numeric cell or header. |

### Badges and deltas

| Class | Use |
|---|---|
| `rd-badge` | Capsule state badge. Combine with exactly one state class. |
| `rd-baseline` `rd-emerging` `rd-clear` `rd-strong` `rd-dislocated` `rd-extreme` `rd-warn` `rd-fault` | Signal state modifiers for `rd-badge`, `rd-callout`, and `rd-bar-fill` (where noted). |
| `rd-delta-up` `rd-delta-down` | Signed change values. Direction of the number, not sentiment. |

### Comparison bars

| Class | Use |
|---|---|
| `rd-bar` | One row: label, track, value. Group rows so peers share one scale. |
| `rd-bar-label` | Mono row label. |
| `rd-bar-track` | The full-scale track. |
| `rd-bar-fill` | The measured fill. Width via inline `style="width: N%"`. Modifiers: `rd-neutral`, `rd-warn`, `rd-dislocated`. |
| `rd-bar-value` | Right-aligned mono value. |
| `rd-neutral` | Neutral comparative fill for non-subject rows. |

### Callout

| Class | Use |
|---|---|
| `rd-callout` | Decision or summary panel with a 2px signal edge. Modifiers `rd-warn` and `rd-fault` recolor the edge. |

## Publishing as Radon

- The wordmark is the text `RADON` inside `rd-wordmark`; the stylesheet renders the signal tick. Do not recreate the logo, stretch it, or recolor it.
- If an image mark is required, use the monogram SVG at the asset URL above at 16px to 32px, on canvas or panel backgrounds only.
- The tagline, when used, is exactly: "Reconstructing market structure from noisy signals."
