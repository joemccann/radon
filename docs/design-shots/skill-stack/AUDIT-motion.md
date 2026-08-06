# AUDIT — Motion opportunities (find-animation-opportunities + emil-design-eng)

**Date:** 2026-08-06  
**Scope:** `web/app/globals.css` interactive chrome + shared shells (Modal, Toast, BottomSheet, MobileMoreDrawer, CommandPalette, Share PnL, InfoTooltip, primary CTAs).  
**Posture:** Restraint. Radon is a dense trading terminal — motion budget is low. Only candidates that pass Frequency → Purpose → Speed → Function are listed.  
**Cap:** Max 12 opportunities; 7 survive the gate.  
**Status:** Read-only audit. No implementation.

---

## Existing motion vocabulary (extend, do not fork)

| Token / pattern | Value | Where |
| --- | --- | --- |
| `--ease-out` | `cubic-bezier(0.25, 1, 0.5, 1)` | `:root` |
| `--transition-controls` | paint-only colors `150ms var(--ease-out)` | buttons, tabs, inputs |
| `--transition-press` | controls + `transform` + `opacity` `150ms` | pressable chrome |
| `--press-scale` | `0.96` | primary / order / admin CTAs (deliberately **not** tabs/chips/cells) |
| Modal enter | backdrop `150ms ease-out`; content `translateY(12px)→0` + opacity `150ms` | `.modal-*` |
| Toast | enter `200ms ease-out` / exit `150ms var(--ease-out)` (keyframes) | `.toast` |
| Mobile sheet enter | `translateY(100%)→0` `240ms var(--ease-out)` | `.mobile-sheet`, `.m-sheet` |
| Banner | `opacity` + `translateY(-4px)` `200ms` | connection / health banners |
| Lightbox | fade `180ms` + panel `scale(0.97) translateY(6px)` `220ms` | newsfeed lightbox |
| Asset deck | opacity-only `160–180ms` | `.asset-deck` |
| Reduced motion | global `animation/transition-duration: 0.01ms` | `:root` media query |

Personality: instrument workstation. Prefer **opacity + transform only**, **ease-out**, **≤300ms** UI (sheets ≤240ms already). No bounce. No route choreography. No decorative mouse trails.

---

## Part 1 — Should animate (surviving opportunities)

Ordered by leverage. Exact values only.

| # | Location | Today | Purpose | Frequency | Suggested motion |
| --- | --- | --- | --- | --- | --- |
| 1 | `globals.css` `.mobile-drawer` / `MobileMoreDrawer.tsx` | Menu mounts/unmounts with **zero** enter or exit (backdrop + panel snap) | Preventing a jarring change + spatial consistency (bottom sheet grammar already used by `.m-sheet`) | Occasional | **Enter:** panel `transform: translateY(100%) → translateY(0)`, `transition: transform 240ms var(--ease-out)`; backdrop `opacity: 0 → 1` `200ms var(--ease-out)`. **Exit:** reverse, panel `200ms`, backdrop `160ms`, then unmount. Match sheet path — same edge in and out. Reduced-motion: opacity-only `120ms`. |
| 2 | `BottomSheet.tsx` dismiss path + `globals.css` `.mobile-sheet` / `.m-sheet` | Enter only (`mobile-sheet-in`). Close via backdrop/X hard-unmounts. Drag uses distance `>80` only; release sets `style.transform = ""` with no settle curve; no velocity | Spatial consistency + gesture seam | Occasional (order ticket, chain detail, action sheets) | **Exit animation** before unmount: `translateY(0) → translateY(100%)` `200ms var(--ease-out)`. **Drag release below threshold:** `transition: transform 200ms var(--ease-out)` back to 0 (not instant clear). **Velocity dismiss:** if `Math.abs(dy) / elapsedMs > ~0.11`, dismiss regardless of distance. Optional light damping when dragging upward past 0 (`offset * 0.2`). Prefer CSS transition retarget over keyframes for interruptible drag cancel. |
| 3 | `Modal.tsx` + `.modal-backdrop` / `.modal-content` | Enter animations present; close is instant unmount (no exit) | Preventing a jarring change | Occasional (metric defs, cancel order, share report, fills) | Exit phase (~120–150ms) before unmount: backdrop `opacity: 1 → 0` `120ms var(--ease-out)`; content `opacity: 1 → 0`, `transform: translateY(0) → translateY(8px)` `120ms var(--ease-out)`. Keep transform-origin center (modals stay centered). Do **not** lengthen enter past `150ms`. |
| 4 | `.share-pnl-popover` / `SharePnlButton.tsx` | Popover appears and disappears with no transition; default origin feel | Spatial consistency (anchored to trigger) | Occasional | Origin-aware enter: `transform-origin: bottom right` (desktop anchor); `opacity: 0; transform: scale(0.97)` → settled; `transition: opacity 160ms var(--ease-out), transform 160ms var(--ease-out)`. Prefer `@starting-style` or a `data-open` class so rapid open/close retargets. Exit same properties `120ms`. Mobile bottom-sheet variant already uses sheet enter — align exit with #2. |
| 5 | `admin/ConfirmDialog.tsx` + `.admin-confirm-backdrop` / `.admin-confirm-panel` | Instant paint; no enter/exit | Preventing a jarring change | Occasional (operator restarts / cascade confirm) | Same modal recipe as shared Modal: backdrop fade `150ms var(--ease-out)`; panel `opacity 0 + translateY(12px) → settled` `150ms var(--ease-out)`; exit `120ms`. No scale(0). |
| 6 | `.toast` / `Toast.tsx` | Enter/exit already exist via **keyframes** (`toast-in` / `toast-out`) | Preventing jarring under **rapid stack** (IB / health bursts) — interruptibility | Occasional bursts | Prefer **CSS transitions** on `transform` + `opacity` (settled state default; enter via `@starting-style` or `toast--enter` class cleared next frame): `transition: transform 200ms var(--ease-out), opacity 200ms var(--ease-out)`; exit `150ms` to `translateY(8px)` + `opacity: 0`. Keep existing travel distance (`8px`). Symmetric edge (bottom-right stack). Do not add bounce or stagger between toasts. |
| 7 | `CancelOrderDialog` / `.btn-danger` cancel confirm | Destructive cancel is a plain click (modal already confirms, but no deliberate press ritual) | Feedback — reduce mis-taps on cancel-all / cancel order | Occasional / high consequence | Optional **hold-to-confirm** on the danger button only: overlay `clip-path: inset(0 100% 0 0) → inset(0 0 0 0)` over `2s linear` while `:active` / pointer down; release snap-back `200ms var(--ease-out)`; complete → fire confirm. Keep existing modal copy. Pair with existing `transform: scale(var(--press-scale))` (`0.96`). Skip if product prefers single explicit click after modal — then reject #7 and keep modal-only. |

**Handoff:** `improve-animations plan <row #>` for any surviving row. Prefer #1 and #2 first (mobile chrome currently least consistent with sheet grammar).

---

## Part 2 — Should not animate (rejected candidates)

| Candidate | Gate kill |
| --- | --- |
| **Command palette open/close** (`.command-palette-*`, `CommandPalette.tsx`) | **Frequency / keyboard-initiated.** 100+/day via `:` shortcut. Raycast rule: never animate. Instant mount is correct. Press scale on the header *trigger* button is fine; panel must stay instantaneous. |
| **Sidebar nav groups / nav items** (collapse chevron already rotates `150ms`) | **Frequency.** Tens–hundreds/day. Height/accordion motion on group bodies would feel laggy. Color transitions only (already on `.nav-item`). |
| **Route / workspace page transitions** (dashboard ↔ portfolio ↔ scanner) | **Frequency + function.** Core navigation. Content must not fade/slide between pages. |
| **Scanner mode tabs / ticker tabs** (`.scanner-mode-tab`, `.ticker-tab`) | **Frequency.** High. Instant active paint; existing color border transitions only. No underline spring, no shared-layout morph. |
| **Dense table / journal leg expand** (`WorkspaceSections` leg rows, chevron only) | **Frequency + function.** Expanding rows with height animation in a data grid hinders scan reading. Chevron rotate is enough. |
| **Live price / chart / sparkline “draw-on”** (beyond existing `.last-price-flash-*` `800ms` background flash) | **Function.** Data the operator is reading. Flash is already adequate state indication; no number count-up, no line-draw, no bar grow on every tick. |
| **Watchlist / dashboard hero stagger expansion** (existing `.watchlist-enter` `480ms` is already at the long end) | **Speed + frequency.** Do not add more page-load staggers or lengthen beyond current. Prefer not inventing new “grid cascade” entrances for scanners. |
| **Press-scale on every chip, tab, table cell, glyph rail** | **Frequency + product rule.** Code comment at press-scale block: terminal density — scale only high-intent CTAs. Correct. |
| **Health / integrity pulse dots** (`.rail-integrity-dot-*`, footer strip) | Already state indication; infinite pulse is fine. Do not “delight” them or add bounce. |
| **Asset deck open** (opacity-only `160–180ms`) | Already correct for high-frequency cockpit deck switching. Do not add slide-over translation that shifts book layout. |
| **InfoTooltip full popover choreography** (scale from `0`, long delay theater) | **Frequency.** Hover/focus tens/day on dense metrics. If anything later, max near-imperceptible `opacity 125ms` — not listed as a should-animate row because the cost/benefit is weak on this UI. |
| **SpectralLoader / shimmer / chat typing dots** | Loading/state already covered. Do not slow or decorative-ize. |
| **Theme toggle icon morph** | Tens/day chrome. Instant swap + existing press scale is enough. |

---

## Part 3 — Verdict

Radon is **already close to the right motion level** for a professional terminal: shared press tokens, short modal/toast/sheet enters, opacity-only deck reveal, and an explicit ban on press-scale everywhere. The gaps are not “make it lively” — they are **asymmetric surfaces**: mobile More drawer has no motion at all, bottom sheets enter but exit/drag poorly, shared Modal and admin confirm lack exit, and Share PnL pops without origin. Highest leverage is **#1 Mobile More Drawer** aligned to existing sheet grammar, then **#2 BottomSheet exit + velocity**, then **#3 Modal exit** so every occasional overlay feels like one system.

Do not animate the command palette, nav, tabs, routes, or live market numbers. When implementing, extend `--ease-out` / `--press-scale` / 150–240ms budgets; animate only `transform` and `opacity`; keep reduced-motion gentler (opacity) rather than inventing a second motion language.

---

## Gate checklist (skill compliance)

| # | Frequency | Purpose named | ≤300ms UI budget | Helps vs hinders |
| --- | --- | --- | --- | --- |
| 1 | Occasional | Spatial + anti-jar | 200–240ms | Helps mobile menu |
| 2 | Occasional | Spatial + gesture | 200ms + drag physics | Helps sheet feel physical |
| 3 | Occasional | Anti-jar | 120–150ms | Helps modal system |
| 4 | Occasional | Spatial origin | 160ms | Helps popover anchor |
| 5 | Occasional | Anti-jar | 150ms | Helps admin confirm |
| 6 | Occasional bursts | Anti-jar / interruptible | 150–200ms | Helps toast stack |
| 7 | Occasional / rare | Feedback (destructive) | press 2s linear / release 200ms | Helps only if product wants hold |

---

## Out of scope / not audited in depth

- Marketing `site/` (different motion budget).  
- Chart library internals beyond flash classes.  
- Framer Motion adoption (repo is CSS-keyframe/transition native; stay there unless gesture springs need a library).  
- Brand glass on lightbox scrim (`backdrop-filter`) — visual system issue, not motion opportunity.
