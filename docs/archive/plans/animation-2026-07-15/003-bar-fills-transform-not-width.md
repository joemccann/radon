# 003 — Drive data-bar fills with `transform: scaleX/scaleY`, not `width`/`height`

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: MEDIUM
- **Category**: Performance
- **Estimated scope**: 1 CSS file + `RegimePanel.tsx` + `WorkspaceSections.tsx`. Small–medium.

## Problem

Four data-bar fills animate `width` or `height` — layout properties that run off the GPU (layout → paint → composite every frame). Animating `transform` instead keeps them on the compositor.

Current code (`web/app/globals.css`):

```css
/* :3329 */ .strength-fill      { height: 100%; background: var(--text-primary); transition: width 400ms var(--ease-out); }
/* :3349 */ .flow-spark-bar     { width: 6px; min-height: 2px; transition: height 400ms var(--ease-out); }
/* :9080 */ .regime-hero-bar-fill { height: 100%; transition: width 0.3s var(--ease-out); }
/* :9397 */ .regime-bar-fill      { height: 100%; transition: width 0.3s var(--ease-out); }
```

The dynamic size is set inline in React:

```tsx
/* web/components/RegimePanel.tsx:103 */  <div className="regime-bar-fill" style={{ width: `${pct}%`, background: barColor }} />
/* web/components/RegimePanel.tsx:457 */  <div className="regime-hero-bar-fill" style={{ width: `${cri.score}%`, background: color }} />
/* web/components/RegimePanel.tsx:569 */  <div className="regime-hero-bar-fill" style={{ width: `${cri.score}%`, background: color }} />
/* web/components/WorkspaceSections.tsx:913 */ <div ... className="flow-spark-bar neutral" style={{ height: 2 }} />
/* web/components/WorkspaceSections.tsx:916 */ <div ... className={`flow-spark-bar ${cls}`} style={{ height: h }} title={...} />
```

`.strength-fill`'s dynamic-width source was not located from the CSS class alone — the executor must find it (see Steps).

## Target

**Regime bars (clean conversion — do these fully):** the fill fills its track at `width: 100%` and is scaled horizontally from the left edge.

```css
/* globals.css */
.regime-bar-fill      { height: 100%; width: 100%; transform-origin: left center; transition: transform 0.3s var(--ease-out); }
.regime-hero-bar-fill { height: 100%; width: 100%; transform-origin: left center; transition: transform 0.3s var(--ease-out); }
```

```tsx
/* RegimePanel.tsx — replace the inline width with a clamped scaleX; keep background */
style={{ transform: `scaleX(${Math.max(0, Math.min(1, pct / 100))})`, transformOrigin: "left center", background: barColor }}
/* and for the two hero bars, using cri.score: */
style={{ transform: `scaleX(${Math.max(0, Math.min(1, cri.score / 100))})`, transformOrigin: "left center", background: color }}
```

**`.flow-spark-bar` (sparkline redraw — remove the tween):** a sparkline bar changing height on a data refresh does not need a 400ms animation. Delete the transition so it updates instantly:

```css
.flow-spark-bar { width: 6px; min-height: 2px; }   /* transition removed */
```

Leave the inline `height` values in `WorkspaceSections.tsx` unchanged.

**`.strength-fill`:** locate its width source (Steps). If it is a simple inline `width: \`${n}%\``, convert it exactly like the regime bars (`width: 100%` + `transform-origin: left center` + `transition: transform 400ms var(--ease-out)`, inline `transform: scaleX(n/100)`). If the source is not a simple inline percentage, STOP and report rather than guessing.

## Repo conventions to follow

- Reuse `var(--ease-out)`. Keep each element's existing duration (400ms for strength-fill, 0.3s for regime bars).
- Inline dynamic style already lives on these fills (`RegimePanel.tsx` sets `background` inline) — keep that pattern; just swap `width` for `transform`.

## Steps

1. `globals.css:9080` and `:9397`: change `.regime-hero-bar-fill` and `.regime-bar-fill` to add `width: 100%; transform-origin: left center;` and change `transition: width 0.3s var(--ease-out);` → `transition: transform 0.3s var(--ease-out);`.
2. `RegimePanel.tsx:103`: replace `width: \`${pct}%\`` with `transform: \`scaleX(${Math.max(0, Math.min(1, pct / 100))})\`, transformOrigin: "left center"`. Keep `background: barColor`.
3. `RegimePanel.tsx:457` and `:569`: replace `width: \`${cri.score}%\`` with `transform: \`scaleX(${Math.max(0, Math.min(1, cri.score / 100))})\`, transformOrigin: "left center"`. Keep `background: color`.
4. `globals.css:3349`: remove `transition: height 400ms var(--ease-out);` from `.flow-spark-bar` (delete just that declaration).
5. `.strength-fill`: run `grep -rn "strength-fill" web/components web/app --include=*.tsx`. If a match sets an inline `width: \`${…}%\``, convert per Target. Otherwise leave `globals.css:3329` unchanged and note it in your report.

## Boundaries

- Do NOT change the bars' colors, heights, or track (parent) elements.
- Do NOT convert `.flow-spark-bar` to `scaleY` — its height IS the datum and there is no fixed track; just remove the transition.
- Do NOT touch any other `transition: width`/`height` outside these four (e.g. sidebar widths — not in scope).
- If a regime bar's parent track is not full-width (so `width: 100%` + scaleX would render wrong), STOP and report.
- If drift since `78bcf138`, STOP and report.

## Verification

- **Mechanical**: `cd web && bunx tsc --noEmit` clean. `grep -n "transition: width\|transition: height" web/app/globals.css` no longer lists lines 9080/9397/3349.
- **Feel check**: open `/regime`. Trigger a CRI/regime data refresh (or reload):
  - The regime bars grow smoothly from the **left edge** (not the center, not the right) to their value.
  - In DevTools Performance, record a refresh: the bar animation shows on the Compositor, no "Layout" entries per frame for these elements.
  - Bar end position matches the old percentage exactly (e.g. a 62% bar fills 62% of its track).
  - Flow spark bars update instantly on refresh with no height tween, and don't look broken.
- **Done when**: regime bars animate via `transform`, land at the correct width, and no `transition: width/height` remains on these four selectors.
