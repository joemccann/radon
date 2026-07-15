# 007 — Add subtle press feedback to primary action buttons

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: MEDIUM (additive)
- **Category**: Physicality & origin (missed opportunity)
- **Estimated scope**: 1 CSS file, a handful of rules. Small.
- **Depends on**: 001 (uses the `--transition-controls` token). If 001 is not done, substitute the explicit paint-property transition inline.

## Problem

Almost nothing in the app confirms a press. Only `.book-row-fill:active` (`globals.css:8478`) has any `:active` transform. Primary action buttons — the ones a user deliberately clicks to place/modify orders or confirm — give no tactile "I registered your click" response. A subtle `scale(0.97)` on `:active` adds physicality without being flashy, which suits a crisp terminal.

Scope this to **primary/destructive action buttons only**, not every dense cell or tab (adding scale everywhere would feel busy and off-brand).

Targets (`web/app/globals.css`), current rules:

```css
/* :6678 */ .btn-primary { ... cursor: pointer; transition: all 150ms ease-in-out; }
/* :6700 */ .btn-danger  { ... cursor: pointer; transition: all 150ms ease-in-out; }
/* :5954 */ .btn-order-action { ... cursor: pointer; transition: all 150ms ease-in-out; ... }
/* :7500 */ .order-action-btn { ... cursor: pointer; transition: all 150ms ease-in-out; }
/* :6656 */ .btn-secondary { ... cursor: pointer; transition: all 150ms ease-in-out; }
```

## Target

Each of these buttons transitions `transform` (in addition to its paint properties) and scales down slightly while pressed. Exact values from the audit playbook: `scale(0.97)` on `:active`, `transform 160ms` ease-out.

```css
/* Add transform to the transition (assumes 001 done, so paint props are the token). */
.btn-primary,
.btn-danger,
.btn-order-action,
.order-action-btn,
.btn-secondary {
  transition: var(--transition-controls), transform 160ms var(--ease-out);
}
.btn-primary:active,
.btn-danger:active,
.btn-order-action:active,
.order-action-btn:active,
.btn-secondary:active {
  transform: scale(0.97);
}
```

If 001 is not applied yet, replace `var(--transition-controls)` above with `background-color 150ms var(--ease-out), border-color 150ms var(--ease-out), color 150ms var(--ease-out)` for these five rules.

Disabled buttons must not depress:

```css
.btn-primary:disabled:active,
.btn-danger:disabled:active,
.btn-order-action:disabled:active,
.order-action-btn:disabled:active,
.btn-secondary:disabled:active { transform: none; }
```

## Repo conventions to follow

- Reuse `var(--ease-out)` and the `--transition-controls` token from plan 001.
- Exemplar of an existing `:active` transform (subtler, for a dense row): `.book-row-fill:active { transform: translateY(0.5px); }` (`globals.css:8478`). Primary buttons warrant `scale(0.97)`, dense rows warrant the tiny translate — do not scale dense rows.
- The global `prefers-reduced-motion` reset (`globals.css:174`) already neutralizes transforms for reduced-motion users; no per-rule media query needed, but verify (below).

## Steps

1. In `web/app/globals.css`, add the grouped `transition` rule (Target) so the five listed buttons include `transform 160ms var(--ease-out)`. If those buttons already got `transition: var(--transition-controls)` from plan 001, append `, transform 160ms var(--ease-out)` to each (or use the grouped selector).
2. Add the grouped `:active { transform: scale(0.97); }` rule.
3. Add the grouped `:disabled:active { transform: none; }` rule.

## Boundaries

- Do NOT add press-scale to tabs (`.ticker-tab`), nav items, table rows/cells, chips, or the many `.btn-quick`/`.sync-button` utility controls — primary/destructive/order actions only.
- Do NOT use a scale below 0.95 or a value other than 0.97.
- Do NOT change button size, padding, colors, or hover behavior.
- Do NOT add dependencies.
- If drift since `78bcf138`, STOP and report.

## Verification

- **Mechanical**: `grep -n "scale(0.97)" web/app/globals.css` shows the new rule. `bunx tsc --noEmit` unaffected (CSS only).
- **Feel check**: open an order surface (chain → order ticket, or the Order tab). Press and hold a primary/submit and a danger/sell button:
  - The button scales to 0.97 while held and springs back on release over ~160ms — subtle, not cartoonish.
  - A **disabled** primary button does not depress when clicked.
  - Tabs, table rows, and utility chips do NOT scale (confirm the effect is scoped).
  - Toggle `prefers-reduced-motion`: pressing no longer scales (global reset), and the button still works.
- **Done when**: primary/destructive/order buttons give a subtle press response, disabled ones don't, and the effect did not leak to dense/tab controls.
