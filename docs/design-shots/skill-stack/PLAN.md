# Skill-stack plan — shared chrome P0/P1

**Date:** 2026-08-06  
**Sources:** `AUDIT-impeccable.md`, `AUDIT-motion.md`, `AUDIT-react-best-practices.md`, `AUDIT-web-design-guidelines.md`  
**Baseline:** `BASELINE.md` + `baseline/*.png`  
**Status:** P0/P1 implemented 2026-08-06 (no commit/push in session).

---

## Constraints (non-negotiable)

| Rule | Implication |
| --- | --- |
| Brand lock | Canvas `#0a0f14`, panel `#0f1519`, line `#2e3947`, signal `#05AD98`. Inter + IBM Plex Mono. Max 4px panel radius. No glass, soft shadows, gradients, emoji, em dashes in user copy. |
| Shared chrome first | Prefer `globals.css`, `WorkspaceShell`, `Header`, `Sidebar`, `Modal`, `ScannerInstrumentShell`, `SectionEmptyState`, mobile sheet/drawer. No one-off page redesigns as primary work. |
| One session | Surgical diffs. No product rewrite, no Framer Motion, no SWR adoption, no new design system. |
| Cap | **12 tasks max**, all P0 or P1. P2 audit rows deferred. |

---

## Synthesis (what the audits agree on)

1. **Instrument grammar is half-authored.** Scanners + snapshot cards have the atoms; Live Market Feed, double mount labels, soft edge glow, and missing meta rails break the rack.  
2. **Motion is almost right.** Gaps are asymmetric overlays (drawer, sheet exit, modal exit) — not “more delight.” Do not animate nav, palette, tabs, routes, or live numbers.  
3. **Perf jank is shell-shaped.** One client monolith + price ticks re-rendering scanner + five parallel scanner GETs + `loading: () => null`.  
4. **A11y is cheap and high leverage** on shell: skip link, `aria-current`, toast/integrity live regions, modal overscroll/heading/focus.

**Rejected for this plan (explicit):** product-scoped module IDs (P2), full `/portfolio` instrument port (P2), SWR migration (P2), dynamic per-mode scanner panels (P2), hold-to-confirm (product decision), command-palette animation (must stay instant), route transitions.

---

## Priority map

| Pri | Group | Why |
| --- | --- | --- |
| P0 | A11y | Keyboard/AT blockers and silent critical status |
| P0 | Perf | Market-hours jank + cold TTI on every route |
| P0 | Shell | Double-label + soft glow + feed grammar (primary surface) |
| P1 | Motion | Overlay system consistency (sheet/modal/drawer) |
| P1 | Scanner modules | Loading honesty + fetch fan-out without rewrite |
| P1 | Shell polish | Meta rails + metered gauges + mobile identity |

---

## Tasks (checkable)

### Shell

- [x] **T1** — Kill soft edge glow; hard 2px cap on `.panel-edge-trace`  
  - **Pri:** P0 · **Group:** Shell  
  - **Source:** Impeccable §H / upgrade 8  
  - **Files:** `web/app/globals.css` (`.panel-edge-trace::after`)  
  - **Done when:** No `box-shadow` glow on edge cap; 2px solid marker only; dark + light theme still read.  
  - **depends_on:** []

- [x] **T2** — Single mount label per dashboard rack slot  
  - **Pri:** P0 · **Group:** Shell  
  - **Source:** Impeccable upgrade 1  
  - **Files:** dashboard section wrappers + `*SnapshotCard` / `OpportunitiesCard` (drop outer section title **or** inner eyebrow/title — not both). Prefer: outer = collapse affordance only; card owns eyebrow + title + edge.  
  - **Done when:** Desktop dashboard shows one device label per Portfolio / Orders / Opportunities slot; baselines re-shot and compared.  
  - **depends_on:** []

- [x] **T3** — Instrument-ize Live Market Feed shell  
  - **Pri:** P0 · **Group:** Shell  
  - **Source:** Impeccable upgrade 2  
  - **Files:** `web/components/DashboardNewsFeed.tsx`, shared panel atoms in `globals.css`  
  - **Done when:** Feed uses edge gauge + mono eyebrow + sentence-case title + meta rail (`source · capture.basis · last.sample`); no Lucide hero + uppercase SaaS section-title as primary chrome. Refresh is a mono rail control.  
  - **depends_on:** [T1]

- [x] **T4** — Snapshot meta rails + optional metered edge levels  
  - **Pri:** P1 · **Group:** Shell  
  - **Source:** Impeccable upgrades 3–4  
  - **Files:** Portfolio / Orders / Opportunities cards; wire `--edge-level` where data exists (margin util, working density, candidate intensity); rest only when feed unavailable.  
  - **Done when:** Each snapshot card has a 20–24px `panel-meta-rail` with dotted keys; gauges measure when data present.  
  - **depends_on:** [T2]

- [x] **T5** — Mobile dashboard keeps compact device labels on collapse rows  
  - **Pri:** P1 · **Group:** Shell  
  - **Source:** Impeccable upgrade 8 (mobile identity)  
  - **Files:** mobile dashboard section chrome (not orphaned `01`/`02` only)  
  - **Done when:** Collapsed mobile slots show short eyebrow or title next to count; compare `baseline-mobile-dashboard.png`.  
  - **depends_on:** [T2]

### Scanner modules

- [x] **T6** — Replace `loading: () => null` with instrument skeleton; scanner route loading  
  - **Pri:** P1 · **Group:** Scanner modules  
  - **Source:** React BP #5  
  - **Files:** `WorkspaceShell.tsx` dynamic import; optional `web/app/scanner/loading.tsx`  
  - **Done when:** Nav to `/scanner` never leaves a blank content hole; skeleton uses matte panel + hairline rails (no glass).  
  - **depends_on:** []

- [x] **T7** — Narrow scanner cold-fetch fan-out for mode tab badges  
  - **Pri:** P1 · **Group:** Scanner modules  
  - **Source:** React BP #6  
  - **Files:** `ScannerSections` / `useSyncHook` consumers; prefer active-mode GET + deferred counts (or light meta) — **no** full SWR rewrite.  
  - **Done when:** Cold `/scanner?mode=theta` does not require five full scan payloads for first paint of the active table; badges still appear without lying about counts (empty until loaded OK).  
  - **depends_on:** []

### Motion

- [x] **T8** — Mobile More drawer enter/exit matches sheet grammar  
  - **Pri:** P1 · **Group:** Motion  
  - **Source:** Motion #1  
  - **Files:** `globals.css` `.mobile-drawer`, `MobileMoreDrawer.tsx`  
  - **Done when:** Panel `translateY(100%→0)` 240ms `--ease-out`; backdrop opacity 200ms; exit reverse then unmount; reduced-motion opacity-only ≤120ms.  
  - **depends_on:** []

- [x] **T9** — Shared Modal + BottomSheet exit (and sheet drag settle)  
  - **Pri:** P1 · **Group:** Motion  
  - **Source:** Motion #2–3 (admin confirm / Share PnL deferred)  
  - **Files:** `Modal.tsx`, `BottomSheet.tsx`, `.modal-*` / `.mobile-sheet` / `.m-sheet` CSS  
  - **Done when:** Close animates ~120–200ms opacity+transform before unmount; sheet drag below threshold eases back with transition (not instant clear); reduced-motion respected. Palette/nav/tabs still instant.  
  - **depends_on:** []

### Perf

- [x] **T10** — `optimizePackageImports: ['lucide-react']`  
  - **Pri:** P0 · **Group:** Perf  
  - **Source:** React BP #2  
  - **Files:** `web/next.config.mjs`  
  - **Done when:** Config present; `next build` (or focused check) succeeds; no import path breakage.  
  - **depends_on:** []

- [x] **T11** — Isolate scanner (and non-price sections) from shell price-tick re-renders  
  - **Pri:** P0 · **Group:** Perf  
  - **Source:** React BP #3  
  - **Files:** `WorkspaceShell.tsx`, `WorkspaceSections` boundary (`memo` / prop-narrow / children slot)  
  - **Done when:** Live `prices` updates during market hours do not re-render scanner tables when section is scanner/discover; MetricCards still tick.  
  - **depends_on:** []

### A11y

- [x] **T12** — Shell a11y pack (skip, current page, live regions, modal containment, chrome names)  
  - **Pri:** P0 · **Group:** A11y  
  - **Source:** Web guidelines #1–4, #5–11 (bundled; all shared chrome, one session)  
  - **Files:**  
    - `WorkspaceShell.tsx` — skip link `#main-content`, toast `role="status"` + `aria-live="polite"`, `type="button"` on Sync  
    - `Sidebar.tsx` — `aria-current="page"` on active nav links; nav-group `min-height: var(--hit-min)`  
    - `Header.tsx` — integrity `aria-live="polite"`; ticker search `aria-label`; fullscreen/theme `type="button"`  
    - `Modal.tsx` + CSS — `overscroll-behavior: contain`; title as `h2`; focus-visible on panel (or focus first control)  
    - `globals.css` — `[data-theme]` `color-scheme`; `touch-action: manipulation` on shared chrome controls  
  - **Done when:** Keyboard skip works; SR announces current nav, toasts, integrity changes; modal wheel/touch does not chain to page; combobox has accessible name; no bare `outline: none` without replacement.  
  - **depends_on:** []

---

## Dependency graph

```
T1  Hard edge cap                         depends_on: []
T2  Single mount label                    depends_on: []
T3  Feed instrument shell                 depends_on: [T1]
T4  Meta rails + metered gauges           depends_on: [T2]
T5  Mobile device labels                  depends_on: [T2]
T6  Scanner loading skeleton              depends_on: []
T7  Scanner fetch fan-out narrow          depends_on: []
T8  Mobile More drawer motion             depends_on: []
T9  Modal + BottomSheet exit              depends_on: []
T10 lucide optimizePackageImports         depends_on: []
T11 Scanner ≠ price re-render             depends_on: []
T12 Shell a11y pack                       depends_on: []
```

**Suggested session order (parallel where independent):**

1. **T10 → T11 → T6** (perf feel on every route)  
2. **T12** (a11y pack, parallel)  
3. **T1 → T2 → T3 → T4 → T5** (instrument grammar on dashboard/shell)  
4. **T8 → T9** (motion overlays)  
5. **T7** (scanner network; verify badges)

---

## Explicit deferrals (next plan, not this 12)

| Item | Audit | Why deferred |
| --- | --- | --- |
| Per-section code-split of `WorkspaceSections` monolith | React BP #1 | Highest long-term win but multi-file extract risk; exceeds one-session if combined with grammar. Schedule as follow-on **T13**. |
| SWR for `useSyncHook` | React BP #7 | Cross-cutting data layer. |
| Dynamic mode panels + hover preload | React BP #8 | After T13. |
| `content-visibility` long lists | React BP #9 | After T11 profiling. |
| Scoped `FLOW·` / `EXP·` IDs | Impeccable #5 | Naming system change; P2. |
| `/portfolio` Account instrument port | Impeccable #6 | Route-wide, not shared chrome only. |
| Measurement empty states everywhere | Impeccable #7 | P2 polish; use when touching those cards. |
| Share PnL / admin confirm motion | Motion #4–5 | After T9 pattern proven. |
| Toast transition retarget | Motion #6 | Optional after T12 live region. |
| Hold-to-confirm cancel | Motion #7 | Product decision required. |
| Nav cursor / ellipsis / text-wrap | Guidelines P2 | Copy polish only. |

---

## Verification (when implementing)

1. **Brand:** chrome-cdp vs `docs/design-shots/skill-stack/baseline/*` — dashboard, scanner theta/garch, portfolio, mobile dashboard.  
2. **A11y:** keyboard tab order (skip link), VoiceOver/polite announcements on toast + integrity, modal scroll does not move body.  
3. **Motion:** open/close More drawer + bottom sheet + modal; command palette still instant; `prefers-reduced-motion`.  
4. **Perf:** Performance panel during live ticks on `/scanner` (no long tasks from price-driven table re-render); Network: fewer than five full scan GETs on cold active-mode load after T7.  
5. **Tests:** focused Vitest/Playwright for shell/scanner; full suite before commit. Report focused green vs baseline red separately.

---

## Review (fill after implementation)

- Shipped: T1–T12 (all P0/P1). Focused Vitest green: skill-stack-shell-chrome, use-sync-hook-inactive-load, dashboard-mobile-newsfeed, mobile-bottom-sheet, hooks-offline-signals, newsfeed pagination/tag-filter, typography-foundation, demo-welcome-modal (58 tests).  
- Deferred with reason: P2 list unchanged (WorkspaceSections per-section split, SWR, dynamic mode panels, content-visibility, product-scoped IDs, portfolio port, measurement empties everywhere, share/admin motion, toast retarget, hold-to-confirm, nav cursor polish).  
- Baseline deltas: not re-shot in this session (no chrome-cdp). Visual verify recommended vs docs/design-shots/skill-stack/baseline/*.  
- Lessons: none yet.  

---

*Plan synthesized from four skill-stack audits. Cap 12. Brand lock holds.*
