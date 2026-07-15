# 006 — Give toasts an exit animation (they currently vanish + jump)

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: MEDIUM
- **Category**: Missed opportunity / Interruptibility
- **Estimated scope**: `useToast.ts` + `Toast.tsx` + `WorkspaceShell.tsx` + `globals.css`. Small–medium.

## Problem

Toasts animate **in** (`toast-in`, 200ms) but have **no exit** — on dismiss (manual `×` or auto-timeout) they are removed from the array instantly, so the toast disappears abruptly and the stack above snaps down. An entrance without a matching exit reads as broken.

Current code:

```css
/* web/app/globals.css:5671 (.toast) */ animation: toast-in 200ms ease-out;
/* web/app/globals.css:5708 */ @keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
```

```ts
/* web/lib/useToast.ts:21 removeToast — filters immediately */
/* web/lib/useToast.ts:38 auto-dismiss — filters immediately */
```

```tsx
/* web/components/Toast.tsx:19 */ <button className="toast-close" onClick={() => onDismiss(toast.id)} ...>
/* web/components/WorkspaceShell.tsx:516 */ <ToastContainer toasts={toasts} onDismiss={removeToast} />
```

## Target

A two-phase dismissal: mark the toast **exiting**, play a 150ms fade+translate out, then remove it. Exits are slightly softer/shorter than the 200ms entrance (subtle-exit rule).

**CSS** (`globals.css`) — add beside `toast-in`:

```css
@keyframes toast-out { from { opacity: 1; transform: translateY(0); } to { opacity: 0; transform: translateY(8px); } }
.toast--exiting { animation: toast-out 150ms var(--ease-out) forwards; pointer-events: none; }
```

**`useToast.ts`** — add an exiting set and a `dismissToast` that animates then removes; route auto-dismiss through it:

```ts
const [exitingIds, setExitingIds] = useState<Set<string>>(new Set());
const EXIT_MS = 150;

const dismissToast = useCallback((id: string) => {
  setExitingIds((prev) => new Set(prev).add(id));
  setTimeout(() => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    setExitingIds((prev) => { const n = new Set(prev); n.delete(id); return n; });
  }, EXIT_MS);
}, []);
```

- In `addToast`'s auto-dismiss timer (`useToast.ts:38`), call `dismissToast(id)` instead of the inline `setToasts(... filter ...)`.
- Keep `removeToast` (immediate) for any internal/hard-remove need, but export and use `dismissToast` for UI dismissal.
- Return `{ toasts, exitingIds, addToast, dismissToast, removeToast }`.

**`Toast.tsx`** — take `exitingIds`, apply the class, call `dismissToast`:

```tsx
type ToastContainerProps = { toasts: Toast[]; exitingIds: Set<string>; onDismiss: (id: string) => void; };
// on the toast div:
className={`toast toast-${toast.type}${exitingIds.has(toast.id) ? " toast--exiting" : ""}`}
// close button:
onClick={() => onDismiss(toast.id)}   // onDismiss is now dismissToast
```

**`WorkspaceShell.tsx:516`**:

```tsx
<ToastContainer toasts={toasts} exitingIds={exitingIds} onDismiss={dismissToast} />
```
(destructure `exitingIds` and `dismissToast` from `useToast()` at `WorkspaceShell.tsx:63`.)

## Repo conventions to follow

- Reuse `var(--ease-out)` and the existing `toast-in` keyframe naming/pattern (`toast-out` mirrors it).
- The stack-close after removal stays instant — that is acceptable for occasional toasts. Do NOT add a FLIP/reflow animation; it is out of scope.

## Steps

1. `globals.css`: add `@keyframes toast-out` and `.toast--exiting` (Target) directly after the `toast-in` keyframe.
2. `useToast.ts`: add `exitingIds` state + `dismissToast`; route the auto-dismiss timer (line ~38) through `dismissToast(id)`; add both to the returned object.
3. `Toast.tsx`: add `exitingIds` to props; append `toast--exiting` class when `exitingIds.has(toast.id)`; leave the close button calling `onDismiss`.
4. `WorkspaceShell.tsx`: destructure `exitingIds` + `dismissToast` from `useToast()`; pass `exitingIds` and `onDismiss={dismissToast}` to `<ToastContainer>`.

## Boundaries

- Do NOT change toast contents, types, colors, positions, or the 5000ms default duration.
- Do NOT introduce a motion library.
- Do NOT animate the container's layout to close the gap (out of scope).
- Keep `EXIT_MS` (150) and the CSS `toast-out` duration (150ms) identical — if they drift, the toast either removes before finishing or lingers.
- If drift since `78bcf138`, STOP and report.

## Verification

- **Mechanical**: `cd web && bunx tsc --noEmit` clean (the new prop is typed). `bunx eslint components lib --ext .ts,.tsx` on the three changed files clean.
- **Feel check**: trigger a toast (e.g. cause a margin warning, or add a temporary `addToast("success","test")` and remove it after testing):
  - Click `×`: the toast fades and slides down ~8px over 150ms, then is removed — it does not vanish instantly.
  - Let a toast auto-dismiss after 5s: same exit animation plays.
  - Stack two toasts, dismiss the top one: it animates out; the lower one closes the gap after (instant close is acceptable).
  - In DevTools Animations at 10%, confirm `toast-out` interpolates opacity + translateY.
  - Toggle `prefers-reduced-motion`: dismissal still removes the toast (the global reduced-motion reset neutralizes the movement; the toast should not get stuck visible — verify removal still happens after 150ms).
- **Done when**: both manual and auto dismissal play a visible exit, and no toast is ever left stuck in the `exiting` state.
