# 004 — Stop animating `grid-template-columns` on the Book tape toggle

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: LOW–MEDIUM
- **Category**: Performance
- **Estimated scope**: 1 CSS file, 1–2 lines. Trivial (default path).

## Problem

Toggling the L2 Book "Time & Sales" tape animates the grid track widths — a layout property that cannot be composited, so the 320ms reflow janks the montage beside it.

Current code (`web/app/globals.css`):

```css
/* :8380 (inside .book-body-grid) */ transition: grid-template-columns 320ms cubic-bezier(0.22, 0.61, 0.36, 1);
/* :8383 */ .book-body-grid.tape-hidden { grid-template-columns: 1fr 0fr; }
```

Per the audit rule set, animating a layout property is the wrong tool; and per the frequency rule, this toggle is occasional and does not need motion to explain itself — an instant, crisp show/hide is on-brand for a dense terminal.

## Target

**Default (do this):** remove the layout transition so the tape shows/hides instantly.

```css
/* .book-body-grid — delete the transition line entirely */
```

Leave `.book-body-grid.tape-hidden { grid-template-columns: 1fr 0fr; }` as-is.

**Alternative (only if the feel check shows the instant toggle is jarring):** keep the grid snapping instantly, but slide the tape out with a composited transform inside its cell. This requires the tape cell to clip its overflow:

```css
.book-tape-cell { overflow: hidden; }
.book-tape-cell > * { transition: transform 200ms var(--ease-out), opacity 200ms var(--ease-out); }
.book-body-grid.tape-hidden .book-tape-cell > * { transform: translateX(100%); opacity: 0; }
```

Use the alternative only if you can identify the actual tape cell element and its child wrapper; otherwise ship the default.

## Repo conventions to follow

- The tape cell is absolutely positioned to fill its grid cell (see the comment block just above `globals.css:8380`). Respect that; do not restructure the grid.
- Reuse `var(--ease-out)` if you implement the alternative.

## Steps

1. In `web/app/globals.css`, inside the `.book-body-grid` rule, delete the line `transition: grid-template-columns 320ms cubic-bezier(0.22, 0.61, 0.36, 1);`.
2. Do the feel check. If the instant toggle is acceptable (it should be), you are done.
3. Only if the feel check fails: implement the Alternative, having first confirmed the tape cell selector and its child wrapper in `web/components/ticker-detail/` (grep for `book-body-grid` and the tape cell class).

## Boundaries

- Do NOT change the grid column definitions (`1fr 1fr` / `1fr 0fr`).
- Do NOT animate `grid-template-columns`, `width`, or `height` as the fix — that reintroduces the problem.
- Do NOT restructure the montage/tape markup for the default path.
- If drift since `78bcf138`, STOP and report.

## Verification

- **Mechanical**: `grep -n "transition: grid-template-columns" web/app/globals.css` returns nothing.
- **Feel check**: open a ticker's **Book** tab (needs `RADON_DEPTH_ENABLED` and market hours for populated depth, but the toggle itself works anytime). Toggle the tape on/off several times:
  - The montage beside the tape does not stutter/reflow janky during the toggle.
  - Default path: the tape appears/disappears crisply with no half-rendered intermediate widths.
  - Alternative path (if used): the tape slides horizontally within a clipped cell; the montage width snaps once, cleanly.
- **Done when**: no layout property is transitioned on the toggle, and the montage no longer janks.
