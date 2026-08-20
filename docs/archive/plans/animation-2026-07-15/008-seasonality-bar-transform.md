# 008 — Drive the seasonality bar with `transform: scaleX`, not `width`

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: LOW
- **Category**: Performance
- **Estimated scope**: 1 CSS file + 1 component (once the width source is located). Small.

## Problem

The seasonality heatmap bar animates `width` — a layout property — on data change.

Current code (`web/app/globals.css`):

```css
/* :8169 */ .seasonality-cell-bar-wrap { width: 100%; height: 4px; background: var(--border-dim); border-radius: 2px; overflow: hidden; }
/* :8170 */ .seasonality-cell-bar { height: 100%; border-radius: 2px; transition: width 300ms ease; }
```

The wrap already clips overflow and is full width — a clean track — so the fill can be scaled instead of widened. The dynamic width source was not found by class grep; the executor must locate it first.

## Target

```css
.seasonality-cell-bar { height: 100%; width: 100%; border-radius: 2px; transform-origin: left center; transition: transform 300ms var(--ease-out); }
```

Inline (wherever the width is set), a clamped scaleX:

```tsx
style={{ transform: `scaleX(${Math.max(0, Math.min(1, value / 100))})`, transformOrigin: "left center", /* keep any existing background */ }}
```

(`value` is whatever percentage currently drives the width — use the existing variable.)

Note the current easing is the bare `ease` keyword; the Target upgrades it to `var(--ease-out)` for consistency with the other bars (plan 003). Keep 300ms.

## Repo conventions to follow

- Mirror plan 003 exactly (same scaleX + `transform-origin: left center` + `width: 100%` pattern used for the regime bars).
- Reuse `var(--ease-out)`.

## Steps

1. `grep -rn "seasonality-cell-bar" web/components web/app --include=*.tsx` (and `.ts`) to find where the bar's width is set inline (likely `style={{ width: \`${…}%\` }}` in a seasonality panel/cell component).
2. If the width source is a simple inline percentage: change `globals.css:8170` per Target (`width: 100%`, `transform-origin: left center`, `transition: transform 300ms var(--ease-out)`), and replace the inline `width` with the clamped `transform: scaleX(...)` + `transformOrigin`.
3. If the width source cannot be found or is not a simple percentage, STOP and report — leave the file unchanged.

## Boundaries

- Do NOT change `.seasonality-cell-bar-wrap` (the track) — it is already correct.
- Do NOT alter the bar color, height, or border-radius.
- Do NOT convert any other bar here (regime/strength/flow are plan 003).
- If drift since `78bcf138`, STOP and report.

## Verification

- **Mechanical**: `grep -n "transition: width" web/app/globals.css` no longer lists 8170. `bunx tsc --noEmit` clean.
- **Feel check**: open the seasonality view. On data load/change:
  - Each cell bar grows from the **left** to its value; end width matches the old percentage.
  - DevTools Performance shows the bar animating on the Compositor (no per-frame Layout for these bars).
- **Done when**: the seasonality bar animates via `transform`, lands at the correct fill, and no `transition: width` remains on it.
