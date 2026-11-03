"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties, type TransitionEvent } from "react";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const TIMINGS = { panel: { enter: 160, exit: 120 }, menu: { enter: 125, exit: 100 } } as const;
const CLOSED = { open: false, mounted: false, motion: false };

/** CSS owns the fade; React retains an inert exit and cancels it on reopen. */
export function usePointerPresence(kind: keyof typeof TIMINGS) {
  const [state, setState] = useState(CLOSED);
  const current = useRef(state);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timing = TIMINGS[kind];

  const cancelExit = useCallback(() => {
    if (exitTimer.current !== null) clearTimeout(exitTimer.current);
    exitTimer.current = null;
  }, []);
  const commit = useCallback((next: typeof CLOSED) => {
    current.current = next;
    setState(next);
  }, []);

  const setOpen = useCallback((open: boolean, pointer = false) => {
    cancelExit();
    const motion = pointer && !window.matchMedia?.(REDUCED_MOTION).matches;
    const mounted = open || (motion && current.current.mounted);
    commit({ open, mounted, motion });
    if (!open && mounted) {
      // transitionend normally removes the panel. The bounded fallback also
      // handles background tabs or a stylesheet suppressing the transition.
      exitTimer.current = setTimeout(() => {
        exitTimer.current = null;
        commit(CLOSED);
      }, timing.exit + 50);
    }
  }, [cancelExit, commit, timing.exit]);

  useEffect(() => {
    const query = window.matchMedia?.(REDUCED_MOTION);
    const changed = () => {
      if (!query?.matches) return;
      cancelExit();
      commit({ open: current.current.open, mounted: current.current.open, motion: false });
    };
    query?.addEventListener?.("change", changed);
    return () => { cancelExit(); query?.removeEventListener?.("change", changed); };
  }, [cancelExit, commit]);

  const onTransitionEnd = useCallback((event: TransitionEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget || event.propertyName !== "opacity" || current.current.open) return;
    // An entrance can finish just as the user closes. Only a completed fade
    // to zero ends this presence; a queued entrance event must not cut it off.
    if (getComputedStyle(event.currentTarget).opacity !== "0") return;
    cancelExit();
    commit(CLOSED);
  }, [cancelExit, commit]);

  return {
    open: state.open,
    mounted: state.mounted,
    setOpen,
    props: {
      "data-fade-state": state.open ? "open" : "exiting",
      "data-fade-motion": state.motion ? "pointer" : "instant",
      inert: !state.open,
      "aria-hidden": !state.open || undefined,
      onTransitionEnd,
      style: { "--pointer-fade-enter": `${timing.enter}ms`, "--pointer-fade-exit": `${timing.exit}ms` } as CSSProperties,
    },
  };
}
