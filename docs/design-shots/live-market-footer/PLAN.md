# Live market analysis footer - better-ui polish plan

**Date:** 2026-09-19  
**Scope:** the bottom calibration rail of `web/components/DashboardNewsFeed.tsx` (`<footer className="panel-meta-rail" aria-label="Feed calibration">`) on `/dashboard`. Nothing else in the panel.  
**Status:** PLAN ONLY. No product TSX/CSS in this PR. Implement branch: `ui/live-market-footer-better-ui`. Merge of the implement PR is held until Joe says ship.  
**Baseline:** `baseline/operator-held-footer.png` (operator screenshot, HELD tab, dark theme).  
**Sources:** better-ui `make-interfaces-feel-better` (SKILL.md, surfaces.md, typography.md); `docs/design-shots/skill-stack/PLAN.md` T3/T4 and `AUDIT-impeccable.md` (the rail was specified there as `source · capture.basis · last.sample`); `docs/brand-identity.md`; `web/DESIGN_MEMORY.md` (Clear).

---

## 1. Symptom and root cause (verified on `main` @ `10606b4f`)

| Symptom in screenshot | Cause in code |
| --- | --- |
| `source` label touches the left edge of the filled footer box | `.dashboard-news .panel-meta-rail { padding-left: 0; padding-right: 0 }` (`globals.css` ~4663) was written for a transparent rail whose text should align with the header. Clear then painted a background on every rail: `.radon-clear .panel-meta-rail { background: var(--bg-subtle) }` (`clear.css` 70). Text with 0 inset inside a filled box reads as clipped. |
| Filled box is inset from the card border, square-cornered, glued to the card bottom | `.dashboard-news { padding: 18px 16px 0; border-radius: 4px }`. The rail is the last child, inset 16px on both sides, `padding-bottom: 0` on the card, no bottom radius on the rail. Nested corners are not concentric (surfaces.md, principle 1). |
| Wide uneven gaps, `last.sample` orphaned on row two | `.panel-meta-rail { display: flex; flex-wrap: wrap; gap: 18px }` with `white-space: nowrap` items. Three items do not fit the feed column at its floor (`minmax(480px, 1.2fr)`, 448px inner), so the third item wraps alone. Flex-wrap has no notion of an intentional two-row layout. |
| Labels read as a debug dump | Keys are dotted machine names (`capture.basis`, `last.sample`). Under Clear the rail is `font-family: var(--font-sans); text-transform: none`, so a dotted identifier renders in sentence-case Inter, while the tab strip directly above is mono uppercase (`.feed-tabs__tab`). Two type systems on one panel. |
| Time value can shift width between tabs | `last.sample` alternates `HH:MM:SS` and `---`. `tabular-nums` is already inherited from `.radon-clear`, but the value has no reserved width and is not mono. |

The same `.panel-meta-rail` class is shared by `ScannerHero`, `CatalystsQuadrant`, `EngineStatePanel`, `ScannerInstrumentShell` (with `instrument-section__rail`) and every scanner card. Only the news feed zeroes its rail padding and only the news feed has three items with long values in a 1.2fr column, so the defects are feed-local. **All CSS in this plan is scoped to `.dashboard-news`.** No global `.panel-meta-rail` change.

---

## 2. Target visual

One footer strip that reads as the base plate of the same instrument the tab strip sits on.

- **Geometry.** Full-bleed across the card interior: the rail runs edge to edge under the card border like a device footer, not a floating chip. Bottom corners concentric with the card: card `border-radius: 4px` with a `1px` border, so the rail gets `border-radius: 0 0 3px 3px` (outer 4 = inner 3 + 1 border). Top edge stays a structural hairline `border-top: 1px solid var(--line-grid)` (surfaces.md: dividers stay borders; no shadow). Height 32px to 36px, a single row on desktop.
- **Surface.** Keep Clear's `var(--bg-subtle)` fill. No gradient, no glass, no soft shadow (brand lock).
- **Type.** Keys use the tab recipe: `var(--font-mono)`, `var(--text-meta)` or 11px, `letter-spacing: var(--tracking-meta)`, `text-transform: uppercase`, `color: var(--text-muted)`. Values in `var(--font-sans)` at `var(--text-meta)`, `color: var(--text-secondary)`. The time value is `var(--font-mono)` with `font-variant-numeric: tabular-nums` and a reserved `min-width: 8ch` so `---` and `14:32:07` occupy the same box.
- **Optical alignment.** Rail horizontal padding matches the first tab's text inset (`.feed-tabs__tab { padding: 8px 14px }`), so `SOURCE` sits on the same x as `COMMENTARY`. Key to value gap 6px; item to item gap 16px; even.
- **Signal accent.** Restrained. The strip has no accent by default (the tabs already carry `--signal-core` on the active tab). Optional and only if it stays token-based: tone the basis value `var(--signal-core-text)` when it reads a live transport (`scraper`, `hub`, `flash`), `var(--negative)` on `fault`, default `var(--text-secondary)` otherwise. No raw hex.
- **Do not touch.** The empty state copy `Nothing held is waiting for review.`, the tab strip, the header, the LIVE badge, the refresh control.

Reference feel: the finished tab strip in the baseline screenshot, rotated into a footer.

---

## 3. Markup and CSS approach (high level)

### Markup (`DashboardNewsFeed.tsx`, footer block only)

Keep `<footer className="panel-meta-rail" aria-label="Feed calibration">` and the three `panel-meta-rail-item` children with `.k` / `.v`. Additions, no removals:

- Add a feed-local modifier on the footer, e.g. `panel-meta-rail dashboard-news__rail`, so the CSS below never reaches sibling rails.
- Add `data-testid="feed-rail"` on the footer and `data-k="source" | "capture.basis" | "last.sample"` on each item. The machine key moves to the attribute; the visible label is humanized (section 4). `web/tests/skill-stack-shell-chrome.test.ts` does a substring check on the TSX source for `capture.basis`, which the attribute still satisfies.
- Give the source value `title={value}` so a truncated source is still readable on hover.
- Value logic (`sampleAt`, `lastSample`, `captureBasis`, R-463 tab ownership) is unchanged.

### CSS (`web/app/globals.css`, replace the existing `.dashboard-news .panel-meta-rail` block; all selectors under `.dashboard-news`)

- Introduce `--dn-pad-x: 16px` on `.dashboard-news` and set it to `var(--nf-s3)` (12px) in the mobile card rule in `DashboardNewsFeed.module.css`, or read it from the same place the card padding is declared. The rail uses `margin-inline: calc(-1 * var(--dn-pad-x))` and `padding: 8px 14px` to bleed to the card edge while the text stays optically aligned with the tabs.
- `border-radius: 0 0 3px 3px`; keep `margin-top: auto; flex-shrink: 0`.
- Layout: `display: grid; grid-template-columns: minmax(0, 1fr) auto auto; column-gap: 16px; align-items: baseline`. The source item is the only flexible track and the designated shrink absorber (same pattern as the ScannerHero expiry track): its `.v` gets `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap`. Basis and sample never truncate. Nothing wraps on desktop.
- Item: `display: inline-flex; align-items: baseline; gap: 6px; min-width: 0`.
- Keys: the `.feed-tabs__tab` type recipe (mono, uppercase, `--tracking-meta`, muted). This overrides Clear's sans/no-transform rule for the feed rail only via the `.dashboard-news__rail` modifier, which is more specific than `.radon-clear .panel-meta-rail-item .k`; verify the cascade in the browser, not by reading.
- Sample value: `font-family: var(--font-mono); font-variant-numeric: tabular-nums; min-width: 8ch; text-align: right`.
- Motion: none. The rail is static telemetry; no transitions added (SKILL.md principle 19).

### Width budget (why the grid, not flex-wrap)

At 12px Inter and 11px Plex Mono: keys `SOURCE` + `CAPTURE BASIS` + `LAST SAMPLE` ~ 165px, values `Market Ear + Research` + `operator review` + `HH:MM:SS` ~ 275px, gaps and padding ~ 80px, total ~ 520px. Feed inner width is ~ 673px at 1440, ~ 590px at 1280, ~ 466px at the 1024 column floor. Only the floor band truncates, and it truncates the source value with an ellipsis instead of orphaning a row. If the implementer prefers zero truncation at the floor, shorten the third key to `SAMPLED` (saves ~ 26px) before touching values.

---

## 4. Label map

| Machine key (`data-k`) | Current visible | Proposed visible (source text) | Rendered (CSS uppercase) | Values (unchanged) |
| --- | --- | --- | --- | --- |
| `source` | `source` | `Source` | `SOURCE` | `Market Ear`, `Market Ear + Research`, `Headlines`, `Held research` |
| `capture.basis` | `capture.basis` | `Capture basis` | `CAPTURE BASIS` | `scraper`, `awaiting`, `fault`, `hub`, `flash`, `operator review` |
| `last.sample` | `last.sample` | `Last sample` | `LAST SAMPLE` | `HH:MM:SS` or `---` |

Rules: source text is sentence case so assistive tech reads words, uppercase is a CSS transform only. No em dashes, no emoji. Values keep their existing vocabulary; the `---` placeholder stays (project-wide unavailable marker).

Alternative considered: keep the dotted keys and only restyle them mono uppercase, matching the four sibling dashboard rails. Rejected for this panel because the operator called the dotted keys out directly and Clear already un-monos rail keys everywhere else; the sibling rails are single-word keys (`scanned`, `candidates`) that never read as identifiers. If Joe prefers consistency over humanizing, drop to the alternative: zero test changes, same CSS.

---

## 5. Narrow and mobile behavior (no clip, no orphan)

| Range | Layout |
| --- | --- |
| Desktop, feed inner >= ~ 520px | One row, grid `minmax(0,1fr) auto auto`. No wrap. |
| Desktop floor, feed inner 448px to 520px | Same row; source value ellipsizes (`title` carries the full text). Basis and sample intact. |
| Mobile shell (`body[data-mobile="true"]`, <= 640px) | Stacked: `grid-template-columns: 1fr`, each item becomes a row with key left and value right (`display: flex; justify-content: space-between`), `row-gap: 4px`, `padding: 8px var(--nf-s3)`, `margin-inline: calc(-1 * var(--nf-s3))`. Time still mono tabular. |

Never `white-space: nowrap` on the footer as a whole; per-item nowrap only where a track is fixed. No element may have `overflow: hidden` that could clip the rail's own text: the ellipsis lives on the source `.v` span only.

---

## 6. Accessibility

- Keep `<footer aria-label="Feed calibration">`. Do not convert to `<dl>`; the shared `.k` / `.v` structure is what sibling rails and their tests use.
- Visible text stays readable words (`Capture basis`), uppercase via CSS only.
- Truncated source carries `title`. Values are not `aria-hidden`.
- Contrast: keys `var(--text-muted)` on `var(--bg-subtle)` must meet 4.5:1 in both themes; check the light theme (`--text-muted: #62716d` on `#f6f8f8`) with the browser contrast picker and bump to `var(--text-secondary)` if it fails.
- No hit-area concern: the rail is non-interactive. Do not add hover or press affordances.

---

## 7. Tests

Red first (the current markup fails these), then green.

**Vitest**

- `web/tests/skill-stack-shell-chrome.test.ts` T3/T4: keep passing unchanged (`panel-meta-rail` and `capture.basis` still present in the TSX via class and `data-k`).
- `web/tests/dashboard-feed-tabs.test.tsx`: add a rail block. Render each tab, select `[data-testid="feed-rail"]`, assert three `[data-k]` items in order `source`, `capture.basis`, `last.sample`; assert visible key text `Source` / `Capture basis` / `Last sample`; assert the `last.sample` value is `---` on Held and `HH:MM:SS`-shaped on Commentary with a fixture `lastUpdated`. Assert `footer` keeps `aria-label="Feed calibration"`.
- Add a CSS-source contract in `skill-stack-shell-chrome.test.ts` (same file, same style as the `.nav-group-label` regex): the `.dashboard-news__rail` block contains `grid-template-columns`, `tabular-nums`, `border-radius: 0 0 3px 3px`, and the global `.panel-meta-rail` block is byte-identical to `main` (guards the scoping constraint).

**Playwright** (`web/e2e/dashboard-feed-tabs.spec.ts`, existing route fixtures)

- Desktop 1440x900 and 1024x768: rail items share one `boundingBox().y` (single row); rail `x` equals the card border `x + 1`; the source value's `x` is >= rail `x + 12` (no flush text); rail bottom equals card bottom minus 1.
- 1024x768 with a long fixture source: source `.v` has `scrollWidth > clientWidth` and the three items still share one `y` (truncate, never orphan).
- Mobile project (`PLAYWRIGHT_PORT=3033 npx playwright test --project=mobile`, 393x852): items stack, each key `x` equals the first key `x`, no horizontal overflow on `.dashboard-news`.
- Screenshot both themes (`data-theme` dark and light) of the panel for `after/` in this folder, plus the mobile stack.

**Visual verification**: `chrome-cdp` primary, Playwright screenshots fallback, per `web/CLAUDE.md`. Compare against `baseline/operator-held-footer.png`.

---

## 8. Implement PR: Done when

- [ ] Branch `ui/live-market-footer-better-ui` off `main`; changes limited to `web/components/DashboardNewsFeed.tsx` (footer block), `web/app/globals.css` (`.dashboard-news` rail block), `web/components/DashboardNewsFeed.module.css` (mobile pad var), the three test files above, and `docs/design-shots/live-market-footer/after/*.png`.
- [ ] Global `.panel-meta-rail`, `.panel-meta-rail-item`, `.k`, `.v` rules and `clear.css` line 70 to 82 are byte-identical to `main`. `ScannerHero`, `CatalystsQuadrant`, `EngineStatePanel`, and scanner rails render pixel-identical (spot check one screenshot each).
- [ ] Rail is full-bleed inside the card with `border-radius: 0 0 3px 3px`, hairline top, `var(--bg-subtle)` fill, no shadow, no gradient, no raw hex.
- [ ] `SOURCE` x-aligns with `COMMENTARY` (within 1px) in dark and light themes.
- [ ] One row at 1440 and 1280; at 1024 the source value truncates with `title`; basis and sample never truncate or wrap; no item ever wraps alone.
- [ ] Mobile shell stacks three rows, no horizontal overflow, no clipped text.
- [ ] Time value is mono tabular with `min-width: 8ch`; switching Commentary to Held does not shift the basis item's x.
- [ ] Visible labels `Source` / `Capture basis` / `Last sample`; `data-k` carries the machine keys; `aria-label="Feed calibration"` retained; empty-state copy untouched.
- [ ] R-463 unchanged: freshness fields still belong to the open tab (Commentary scraper time, Headlines newest print, Held `---`).
- [ ] Vitest: `skill-stack-shell-chrome`, `dashboard-feed-tabs`, `panel-fault-visibility`, `scanner-hero`, `catalysts-quadrant` green. Playwright: `dashboard-feed-tabs.spec.ts` desktop and mobile green.
- [ ] `after/` screenshots committed: desktop dark, desktop light, 1024 truncation, mobile stack. PR body embeds baseline vs after.
- [ ] CI green on the head SHA; PR stays a draft until Joe says ship.

---

## Considered but rejected

| Candidate | Rejected because |
| --- | --- |
| Fix globally: add horizontal padding back to `.panel-meta-rail` under `.dashboard-news` and stop | Leaves the inset square box glued to a rounded card and the flex-wrap orphan; polishes one symptom of three. |
| Transparent rail (override Clear's fill) so 0 padding is correct again | Loses the base-plate read that mirrors the active tab's raised fill; a bare hairline under a long list looks like a leftover divider. |
| Container query on `.dashboard-news` (`container-type: inline-size`) for an intentional two-row layout at the floor | Size containment on a sticky, `height: 100%` flex child is a layout risk for a 50px band; ellipsis on one value is cheaper and matches the ScannerHero shrink-absorber precedent. Revisit if the feed column floor drops below 480px. |
| Motion or hover on the rail | Non-interactive telemetry; SKILL.md 19. |
| Rename keys in every dashboard rail | Out of scope; sibling rails are not the complaint. |
