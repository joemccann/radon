# 005 — Reveal banners without animating `max-height` (drop the 40px clip)

- **Status**: DONE (2026-07-15) — shipped as a keyframe `opacity`+`translateY` reveal, NOT the `@starting-style`+`grid-rows` variant this plan first specified. See "Final implementation" below.
- **Commit**: 78bcf138 (+ uncommitted animation-plan edits)
- **Severity**: LOW
- **Category**: Performance
- **Estimated scope**: 1 CSS file + 2 banner components (wrap only). Small.

## Problem

`banner-slide-down` animates `max-height`, `padding-top`, `padding-bottom`, opacity — all layout properties — and hardcodes `max-height: 40px`, which clips any banner taller than 40px. It is applied via `.connection-banner` (`web/components/ConnectionBanner.tsx`) and `.service-health-banner` (`web/components/ServiceHealthBanner.tsx`).

```css
/* web/app/globals.css — current keyframe */
@keyframes banner-slide-down {
  from { max-height: 0; padding-top: 0; padding-bottom: 0; opacity: 0; }
  to   { max-height: 40px; padding-top: 8px; padding-bottom: 8px; opacity: 1; }
}
```

**Why the first plan version stopped:** both banners are *conditionally mounted* (`if (!banner) return null`), and a plain CSS `transition` does not fire on first paint — the element mounts already in its final state, so a transition-only reveal never animates. The fix must supply an explicit start state on mount.

## Target

Use `@starting-style` (fires exactly on mount, no JS lifecycle change) with a `grid-template-rows: 0fr → 1fr` reveal — the accepted height-agnostic pattern. Wrap each conditionally-rendered banner in a `.banner-reveal` grid container; the banner's own padding stays static (moves off the animation).

```css
/* web/app/globals.css — replace @keyframes banner-slide-down with: */
.banner-reveal {
  display: grid;
  grid-template-rows: 1fr;      /* mounted (open) state */
  opacity: 1;
  transition: grid-template-rows 240ms var(--ease-out), opacity 240ms var(--ease-out);
}
@starting-style {
  .banner-reveal { grid-template-rows: 0fr; opacity: 0; }
}
.banner-reveal > * { overflow: hidden; min-height: 0; }
```

Then remove `animation: banner-slide-down ...` from `.connection-banner` and `.service-health-banner` (the reveal now lives on the wrapper), and keep their static padding.

```tsx
/* ConnectionBanner.tsx — wrap the returned element */
return (
  <div className="banner-reveal">
    <div className="connection-banner" role="alert" data-testid="ib-connection-banner">
      <AlertTriangle size={14} />
      <span>{banner.message}</span>
    </div>
  </div>
);
```

Apply the identical `.banner-reveal` wrap in `ServiceHealthBanner.tsx` around its rendered banner element.

**Exit:** on hide the banner unmounts (React), so it disappears instantly — there is no exit animation. That is acceptable for these occasional operational banners; do NOT add a JS hold-to-animate-exit lifecycle (out of scope, and in tension with "do not change when the banner shows").

## Repo conventions to follow

- Reuse `var(--ease-out)`; durations in `ms`.
- `@starting-style` has zero existing uses in this codebase but is Baseline-supported in the app's target browsers (Chrome 117+/Safari 17.5+/Firefox 129+; Next 16 PWA). This is the sanctioned modern pattern — introduce it here.
- Exemplar of a conditionally-mounted banner returning `null`: `ConnectionBanner.tsx:27`.

## Steps

1. `grep -rn "banner-slide-down" web/app/globals.css` — find the keyframe and every `animation:` that references it (expected: `.connection-banner`, `.service-health-banner`).
2. In `globals.css`: delete `@keyframes banner-slide-down`; add the `.banner-reveal` + `@starting-style` + `.banner-reveal > *` rules from Target. Remove the `animation: banner-slide-down ...` declaration from `.connection-banner` and `.service-health-banner` (keep their padding, colors, layout).
3. `ConnectionBanner.tsx`: wrap the returned `<div className="connection-banner">…</div>` in `<div className="banner-reveal">…</div>` (Target).
4. `ServiceHealthBanner.tsx`: same wrap around its rendered banner element. If its render is more complex than a single banner div (e.g. multiple siblings), wrap the whole rendered subtree in one `.banner-reveal` — the grid child must be a single element, so add an inner wrapper div if needed.
5. If either banner's markup cannot cleanly get a single grid child without restructuring meaning, STOP for that component and report; the other can still be done.

## Boundaries

- Do NOT reintroduce `max-height`/`padding` animation or any hardcoded height.
- Do NOT change what the banners say or the conditions under which they show.
- Do NOT add a JS mount/unmount lifecycle or exit-hold — `@starting-style` handles entry with no JS.
- Do NOT add dependencies.
- If drift since the stamp, STOP and report.

## Verification

- **Mechanical**: `cd web && bunx tsc --noEmit` clean. `grep -n "banner-slide-down\|max-height: 40px" web/app/globals.css` returns nothing.
- **Feel check**: trigger each banner (ConnectionBanner: force an IB/WS disconnect state or temporarily hardcode `banner` truthy; ServiceHealthBanner: similar). On appearance:
  - The banner expands to its **actual** content height (test a long message — no clip at 40px).
  - Content below is pushed down smoothly during the ~240ms reveal (no hard jump, no janky max-height easing).
  - In DevTools Animations at 10%, confirm `grid-template-rows` interpolates `0fr → 1fr` on mount.
  - Toggle `prefers-reduced-motion`: the banner still appears (the global reset neutralizes the row animation; opacity may still apply) — it must not be invisible or broken.
- **Done when**: both banners reveal true height via `@starting-style`+grid-rows, no `max-height` magic number remains, and long banners aren't clipped.

## Final implementation (shipped 2026-07-15)

Shipped a keyframe reveal instead of the `@starting-style`+`grid-template-rows` variant in Target. During live verification the `@starting-style` transition proved fragile on these conditionally-mounted banners (it can stick at its start opacity when the mount is perturbed before first paint), and `grid-template-rows` is a layout property — animating it runs layout every frame, against this repo's transform/opacity-only convention. The keyframe approach is simpler (no wrapper markup), GPU-composited, and is the same mechanism the original `banner-slide-down` used, so it fires reliably on mount.

```css
/* web/app/globals.css — replaces @keyframes banner-slide-down */
@keyframes banner-reveal-in {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}
/* on .connection-banner AND .service-health-banner: */
animation: banner-reveal-in 200ms var(--ease-out);
```

No `forwards` fill, so the resting state is the base rule — a fully visible, true-height banner that can never be swallowed. No wrapper div; `ConnectionBanner.tsx` / `ServiceHealthBanner.tsx` are unchanged from HEAD.

**Verified:** no `max-height: 40px` / `banner-slide-down` / `@starting-style` / `.banner-reveal` remain; `tsc --noEmit` clean; full Vitest suite green. Resting state confirmed live (opacity 1, a long 2-line message expands to 49px — no 40px clip). The animation *motion* was not feel-checked live: the verification browser tab had a frozen rAF/compositor (insertion-triggered animations sit at frame 0), so motion was verified by construction against the resting/base state rather than by observation.

## History

- **v1 (stopped):** proposed a `.open`-class toggle + `grid-rows`, which cannot animate on a conditionally-mounted (`return null`) component without a JS lifecycle rewrite the plan forbade. Rescoped to `@starting-style`.
- **v2 (`@starting-style`+`grid-rows`, superseded):** correct in principle but fragile in practice — the mount transition can stick at start opacity under pre-paint perturbation, and `grid-rows` animates layout. Replaced with the keyframe above.
