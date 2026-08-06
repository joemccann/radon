# Audit: Vercel Web Interface Guidelines

**Scope (shared chrome only):**  
`web/components/Header.tsx`, `Sidebar.tsx`, `WorkspaceShell.tsx`, `Modal.tsx`, `ScannerInstrumentShell.tsx`, `SectionEmptyState.tsx`, `web/app/globals.css` (chrome selectors).

**Source:** [vercel-labs/web-interface-guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md) (fetched 2026-08-06).

**Brand lock:** Canvas `#0a0f14`, panel `#0f1519`, line `#2e3947`, signal `#05AD98`. Findings are a11y / interaction / semantics only; no redesign.

**Cap:** 15 findings. Severity: **P0** blocks keyboard/AT users or loses critical status; **P1** clear guideline miss with practical impact; **P2** polish / consistency.

---

## Findings

### 1. P0 — No skip link to main content

| | |
|---|---|
| **File** | `web/components/WorkspaceShell.tsx` (~490), `web/app/globals.css` (`.main`) |
| **Issue** | Guidelines: skip link for main content. Shell renders `<main className="main">` after a full sidebar nav (many links / collapsible groups) with no “Skip to content” control. Keyboard users re-tab the entire rail every route. |
| **Fix** | Add first-focusable control in the app shell (layout or `WorkspaceShell`): `<a href="#main-content" className="skip-link">Skip to content</a>`. Set `id="main-content"` on `<main>`. Style `.skip-link` visually hidden until `:focus-visible` (brand tokens, solid panel, 4px radius max). |

### 2. P0 — Modal scroll chaining / overscroll

| | |
|---|---|
| **File** | `web/app/globals.css:5960–5979` (`.modal-backdrop`, `.modal-content`); `web/components/Modal.tsx:32–41` |
| **Issue** | Guidelines: `overscroll-behavior: contain` in modals/drawers. Modal body can scroll (`max-height: 85vh; overflow-y: auto`) without contain; wheel/touch can chain to the page behind despite body `overflow: hidden` in `useDialogChrome`. |
| **Fix** | On `.modal-backdrop` and `.modal-content`: `overscroll-behavior: contain`. Optionally `touch-action: pan-y` on `.modal-content` only. |

### 3. P0 — Primary nav active route not exposed to AT

| | |
|---|---|
| **File** | `web/components/Sidebar.tsx:90–99` |
| **Issue** | Profile link correctly sets `aria-current="page"`. Workspace `Link` items only use class `active`. Screen readers do not get “current page” on Dashboard / Portfolio / Scanner / etc. |
| **Fix** | On each nav `Link`: `aria-current={item.route === activeSection ? "page" : undefined}`. Keep visual `.nav-item.active`. |

### 4. P0 — Async toast surface has no live region

| | |
|---|---|
| **File** | `web/components/WorkspaceShell.tsx:558` mounts toasts; related chrome: IB uplink / margin / fill toasts |
| **Issue** | Guidelines: async updates (toasts) need `aria-live="polite"`. Shell-owned feedback (IB uplink lost/restored, margin warning, fills) is critical operator state and is not announced. (Implementation lives in `Toast.tsx` / `.toast-container`, but the shell is the chrome owner.) |
| **Fix** | On the toast container: `role="status"` + `aria-live="polite"` + `aria-atomic="true"` (or polite region per toast). Errors may use `role="alert"` / assertive only for critical faults. Do not nest interactive-only chrome without a live region for the message text. |

### 5. P1 — Feed integrity changes not announced

| | |
|---|---|
| **File** | `web/components/Header.tsx:126–135` |
| **Issue** | Integrity chip text changes (`Nominal` → `Gateway offline` / `Degraded`) with no `aria-live`. Operators on keyboard/AT miss uplink transitions that the visual rail already shows. |
| **Fix** | Wrap integrity text (or the whole `.rail-integrity` node) in `aria-live="polite"` `aria-atomic="true"`. Keep decorative dot `aria-hidden`. |

### 6. P1 — Missing `color-scheme` on root for themed native UI

| | |
|---|---|
| **File** | `web/app/globals.css` (`:root` / `[data-theme="dark"|"light"]`); layout applies `data-theme` only |
| **Issue** | Guidelines: `color-scheme: dark` on `<html>` for dark themes (scrollbars, form controls, UA widgets). Token themes exist; document `color-scheme` does not track `data-theme`. Only an isolated control sets `color-scheme: dark` (~11997). |
| **Fix** | `[data-theme="dark"] { color-scheme: dark; }` and `[data-theme="light"] { color-scheme: light; }` on `html` (or those selectors as currently applied). |

### 7. P1 — Modal title is not a heading

| | |
|---|---|
| **File** | `web/components/Modal.tsx:35` |
| **Issue** | Guidelines: semantic HTML / heading hierarchy. Dialog uses `aria-labelledby` on a `<span className="modal-title">`. No heading landmark inside the dialog for AT outline / jump. |
| **Fix** | Use `<h2 className="modal-title" id={titleId}>{title}</h2>` (or `h2` + existing styles). Keep `aria-labelledby`. |

### 8. P1 — Header ticker search combobox lacks accessible name

| | |
|---|---|
| **File** | `web/components/Header.tsx:161–167` → `TickerSearch` input (`role="combobox"`, placeholder only) |
| **Issue** | Guidelines: form controls need `<label>` or `aria-label`. Combobox relies on placeholder (`Search ticker…`); placeholders are not names. |
| **Fix** | Pass `aria-label="Search ticker"` (or `aria-labelledby`) on the input from `TickerSearch` / Header. Keep placeholder as example pattern ending in `…`. Prefer `name="ticker-search"` + `autoComplete="off"` (already off). |

### 9. P1 — Icon / chrome controls missing explicit `type="button"`

| | |
|---|---|
| **File** | `Header.tsx:168–185` (fullscreen, theme); `Modal.tsx:36` (close); `WorkspaceShell.tsx:510–518` (Sync Now) |
| **Issue** | Explicit `type="button"` is present on palette / stale Sync / some others; missing on fullscreen, theme, modal close, and shell sync. Outside forms this is usually fine; inside future forms / dialogs default `submit` is a footgun. |
| **Fix** | Add `type="button"` to every non-submit chrome control. |

### 10. P1 — Shared chrome missing `touch-action: manipulation`

| | |
|---|---|
| **File** | `web/app/globals.css` — `.nav-item`, `.nav-group-label`, `.sync-button`, `.command-palette-trigger`, `.theme-toggle`, `.fullscreen-toggle`, `.modal-close`, `.section-empty-state__action` |
| **Issue** | Guidelines: `touch-action: manipulation` to avoid double-tap zoom delay. Present on some mobile surfaces; absent on desktop/shared chrome controls used on trackpads / hybrid. |
| **Fix** | Add `touch-action: manipulation` (and intentional `-webkit-tap-highlight-color`) to shared interactive chrome selectors above. |

### 11. P1 — Dialog panel focus has `outline: none` with no visible focus treatment

| | |
|---|---|
| **File** | `web/app/globals.css:5971–5979` (`.modal-content { outline: none }`); `Modal.tsx:33` (`tabIndex={-1}`) |
| **Issue** | Guidelines: never `outline: none` without focus replacement; prefer `:focus-visible`. Panel is focused on open (`useDialogChrome`); keyboard users get no focus ring on the dialog surface. Close button does inherit global `:focus-visible` once tabbed. |
| **Fix** | Replace bare `outline: none` with `.modal-content:focus { outline: none }` and `.modal-content:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }` (or rely on moving initial focus to the close button / first focusable and leave panel unfocused). |

### 12. P1 — Nav group disclosure hit target below hit floor

| | |
|---|---|
| **File** | `web/app/globals.css:327–343` (`.nav-group-label`); `Sidebar.tsx:71–84` |
| **Issue** | `--hit-min: 40px` / `--touch-min: 44px` are defined; `.nav-group-label` uses ~9px type and `padding: 6px 16px 4px` (~20px tall). Collapsing “Operations” etc. is hard to hit accurately. |
| **Fix** | `min-height: var(--hit-min)`; increase vertical padding; keep mono label styling. Optionally expand chevron hit via pseudo hit area (same pattern as `.theme-toggle::after`). |

### 13. P2 — Loading / status ellipsis and nbsp copy

| | |
|---|---|
| **File** | `WorkspaceShell.tsx:517` (`Syncing...`); `Header.tsx:159` (`⌘K` without nbsp) |
| **Issue** | Guidelines: loading ends with `…` not `...`; non-breaking spaces for `⌘K` (`⌘&nbsp;K` / `\u00a0`). |
| **Fix** | `"Syncing…"`; render `⌘\u00a0K` (or CSS `white-space: nowrap` on the trigger so the pair never wraps). |

### 14. P2 — Nav links use `cursor: default`

| | |
|---|---|
| **File** | `web/app/globals.css:370–375` (`.nav-item`) |
| **Issue** | Links are interactive; `cursor: default` undercuts affordance (hover background alone). Guidelines: interactive states more prominent than rest. |
| **Fix** | `cursor: pointer` on `.nav-item` / `.sidebar-user-card`. |

### 15. P2 — Empty-state / modal title typography polish

| | |
|---|---|
| **File** | `globals.css` `.section-empty-state__headline`, `.modal-title`; `ScannerInstrumentShell` title already has `text-wrap: balance` |
| **Issue** | Guidelines: `text-wrap: balance` / `pretty` on headings. Instrument titles pass; empty-state headline and modal title do not. |
| **Fix** | Add `text-wrap: balance` to `.section-empty-state__headline` and `.modal-title`. |

---

## Pass notes (do not fix)

| Area | Status |
|---|---|
| Icon-only Header controls | `aria-label` on fullscreen, theme, palette, stale Sync |
| Decorative icons | `aria-hidden` on integrity dots, nav chevrons, empty icons, logo mark |
| Modal behavior | Escape, focus trap, restore, body scroll-lock via `useDialogChrome` |
| Semantic shell | `<header>`, `<aside>` + `<nav aria-label>`, `<main>`, instrument `<section>` + `<h2>` |
| Reduced motion | Global `prefers-reduced-motion` short-circuit covers modal animations |
| Viewport zoom | `maximumScale: 5` (not locked) |
| Focus baseline | Global `:focus-visible` outline; many controls use property-listed transitions (not `transition: all`) |
| SectionEmptyState | `role="status"` / `role="alert"` by tone; measurement-style copy contract |
| Live data banner | `role="alert"` on `.live-data-degraded` |
| Hit targets (partial) | Palette, sync, modal close, theme/fullscreen pseudo expand to `--hit-min` |

---

## Out of scope (noted, not counted)

- `TickerSearch` inline styles / `Searching...` ellipsis (Header dependency only for name).
- Command palette panel (separate component).
- Mobile shell / tab bar (not in focus list).
- Brand gradients on charts (`--chart-surface`) — not shared chrome controls.

---

## Suggested fix order

1. Skip link + `aria-current` on nav (cheap, high a11y).  
2. Toast live region + header integrity live region.  
3. Modal overscroll + heading + focus-visible.  
4. `color-scheme`, combobox name, `type="button"`, touch-action, nav hit targets.  
5. Copy / cursor / text-wrap polish.

---

*Read-only audit. No code changes applied.*
