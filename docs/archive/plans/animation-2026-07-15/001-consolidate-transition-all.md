# 001 — Replace `transition: all` on controls with explicit properties + a shared token

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: MEDIUM
- **Category**: Performance + Cohesion & tokens
- **Estimated scope**: 1 CSS file (13 edits) + 2 TSX files (1 edit each). Small.

## Problem

`transition: all` animates *every* property that changes (including layout properties like `padding`/`width` if any state alters them), off the GPU. It is the canonical animation anti-pattern. It appears on 11 interactive controls in `web/app/globals.css` plus 2 inline React styles, hand-typed identically 12 times (`all 150ms ease-in-out`) with one variant (`all 100ms`). That is also a cohesion/consolidation problem: one curve+duration should be a token.

Every one of these controls only changes **paint** properties on its states (`background`, `border-color`, `color`) — none animate `transform`, `opacity`, or `box-shadow` — so the correct fix is to transition exactly those three.

Current code (all in `web/app/globals.css` unless noted):

```css
/* :310  .nav-item */            transition: all 150ms ease-in-out;
/* :1189 .fullscreen-toggle */   transition: all 150ms ease-in-out;
/* :5350 .sync-button */         transition: all 150ms ease-in-out;
/* :5954 .btn-order-action */    transition: all 150ms ease-in-out;
/* :6477 .btn-quick */           transition: all 150ms ease-in-out;
/* :6656 .btn-secondary */       transition: all 150ms ease-in-out;
/* :6678 .btn-primary */         transition: all 150ms ease-in-out;
/* :6700 .btn-danger */          transition: all 150ms ease-in-out;
/* :6842 .ticker-tab (one-line rule) */         ... transition: all 150ms ease-in-out; }
/* :7500 .order-action-btn (one-line rule) */   ... transition: all 150ms ease-in-out; }
/* :7578 .futures-form-action */ transition: all 150ms ease-in-out;
/* :10927 .chain-side-toggle-btn */ transition: all 100ms;
```

```tsx
/* web/components/ShareReportModal.tsx:143 */   transition: "all 150ms",
/* web/app/kit/page.tsx:71 */                   transition: "all 150ms ease-in-out",
```

## Target

Introduce one token and reference it everywhere (150ms group), with a 100ms inline list for the single `.chain-side-toggle-btn`.

```css
/* token — add in the :root token block, immediately after the --ease-out line */
--transition-controls: background-color 150ms var(--ease-out), border-color 150ms var(--ease-out), color 150ms var(--ease-out);
```

Then every `150ms` site becomes:

```css
transition: var(--transition-controls);
```

The single 100ms site (`.chain-side-toggle-btn`, :10927) becomes explicit at its own duration:

```css
transition: background-color 100ms var(--ease-out), border-color 100ms var(--ease-out), color 100ms var(--ease-out);
```

Inline React styles become the explicit string:

```tsx
/* ShareReportModal.tsx:143 and kit/page.tsx:71 */
transition: "background-color 150ms cubic-bezier(0.25,1,0.5,1), border-color 150ms cubic-bezier(0.25,1,0.5,1), color 150ms cubic-bezier(0.25,1,0.5,1)",
```

(The inline strings cannot read the CSS var reliably in all contexts, so inline the curve — it is the value of `--ease-out`.)

## Repo conventions to follow

- Easing/motion tokens live in the top `:root` block of `web/app/globals.css`. The existing one is `--ease-out: cubic-bezier(0.25, 1, 0.5, 1);` — add `--transition-controls` right after it. Do **not** invent a new easing curve; reuse `var(--ease-out)`.
- Exemplar of an explicit, multi-property transition already in the file: `.book-montage { ... transition: border-color 200ms ease; ... }` (`globals.css:8382`).

## Steps

1. In `web/app/globals.css`, in the `:root` block, on the line immediately after `--ease-out: cubic-bezier(0.25, 1, 0.5, 1);`, add:
   `--transition-controls: background-color 150ms var(--ease-out), border-color 150ms var(--ease-out), color 150ms var(--ease-out);`
2. Replace the `transition: all 150ms ease-in-out;` declaration with `transition: var(--transition-controls);` at each of these lines: 310, 1189, 5350, 5954, 6477, 6656, 6678, 6700, 7578. (Line numbers are pre-edit; after step 1 they shift by +1. Match on the selector names listed in Problem, not raw line numbers.)
3. For the two one-line rules `.ticker-tab` (:6842) and `.order-action-btn` (:7500), replace the trailing `transition: all 150ms ease-in-out;` inside each rule with `transition: var(--transition-controls);`, leaving the rest of the one-line rule unchanged.
4. For `.chain-side-toggle-btn` (:10927), replace `transition: all 100ms;` with `transition: background-color 100ms var(--ease-out), border-color 100ms var(--ease-out), color 100ms var(--ease-out);`.
5. In `web/components/ShareReportModal.tsx:143`, replace `transition: "all 150ms",` with the explicit inline string from Target.
6. In `web/app/kit/page.tsx:71`, replace `transition: "all 150ms ease-in-out",` with the explicit inline string from Target.

## Boundaries

- Do NOT touch any other `transition:` declarations (e.g. the intentional `transition: width`/`grid-template-columns` ones — those are separate plans).
- Do NOT add `transform`/`opacity`/`box-shadow` to the token. If — and only if — while verifying you find a control whose `:hover`/`:active`/`[aria-selected]`/`.active` rule changes `transform`, `opacity`, or `box-shadow`, append that single property to *that rule's* transition (not the shared token).
- Do NOT change markup, colors, durations (keep 150ms / 100ms as-is), or the `--ease-out` curve.
- Do NOT add dependencies.
- If any listed selector no longer has `transition: all` (drift since commit `78bcf138`), STOP and report.

## Verification

- **Mechanical**: `cd web && bunx tsc --noEmit` (expect no new errors) and `bunx eslint app components --ext .ts,.tsx` on the two changed TSX files (expect clean). Grep must return zero: `grep -rn "transition: all" web/app/globals.css` and `grep -rn 'transition: .all' web/components/ShareReportModal.tsx web/app/kit/page.tsx`.
- **Feel check**: run the app (`scripts/cloud.sh`), then for each of `.nav-item`, `.ticker-tab`, `.btn-primary`, `.btn-danger`, `.order-action-btn`, `.sync-button`, `.chain-side-toggle-btn`:
  - Hover and un-hover: the background/border/color still fade over ~150ms (100ms for the chain toggle), identical to before.
  - Confirm nothing that used to animate now snaps. In DevTools Animations panel at 10% speed, hover one button and verify only color/background/border interpolate.
- **Done when**: no `transition: all` remains in the three files, all controls still fade their paint properties on hover/active, and no state transition was lost.
