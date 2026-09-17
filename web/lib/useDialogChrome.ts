"use client";

import { useEffect, useRef, useState } from "react";

type UseDialogChromeOptions = {
  /** When false the chrome is inert: no scroll-lock, no listeners, no focus moves. */
  open: boolean;
  /** Invoked when the user presses Escape. */
  onClose?: () => void;
  /**
   * When false, Escape is ignored. Defaults to true. Surfaces with their own
   * Escape semantics (e.g. preventDefault + navigation) can opt out and wire
   * their own handler.
   */
  closeOnEscape?: boolean;
  /**
   * When false, the panel does not receive initial focus and Tab is not
   * trapped. Defaults to true.
   */
  trapFocus?: boolean;
};

type UseDialogChrome<T extends HTMLElement> = {
  /** Portal mount target, resolved on the client only (null during SSR / first paint). */
  portalTarget: HTMLElement | null;
  /** Attach to the dialog panel: gives it focus, scopes the focus trap, and is the tabIndex={-1} target. */
  panelRef: React.RefObject<T | null>;
};

const activeFocusTraps: HTMLElement[] = [];

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * useDialogChrome — the shared contract for every Radon dialog/overlay.
 *
 * Owns the cross-cutting concerns that each modal previously re-implemented:
 *   - portal mount target (deferred to a client effect so SSR never touches `document`)
 *   - Escape-to-close
 *   - body scroll-lock (restores the prior value, so nested overlays compose)
 *   - initial focus moves into the panel on open
 *   - focus-trap (Tab / Shift+Tab cycle within the panel)
 *   - focus-restore (the element focused before open is refocused on close)
 *
 * Visuals + markup stay entirely with the caller; this only manages behavior.
 */
export function useDialogChrome<T extends HTMLElement = HTMLElement>({
  open,
  onClose,
  closeOnEscape = true,
  trapFocus = true,
}: UseDialogChromeOptions): UseDialogChrome<T> {
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null);
  const panelRef = useRef<T | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    setPortalTarget(document.body);
  }, []);

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusPanel = panelRef.current;
    const dialog = focusPanel?.closest<HTMLElement>('[role="dialog"]') ?? focusPanel;
    const previousOwns = dialog?.getAttribute("aria-owns");
    if (trapFocus && focusPanel) {
      activeFocusTraps.push(focusPanel);
      dialog?.setAttribute("aria-owns", [previousOwns, "radon-toast-viewport"].filter(Boolean).join(" "));
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (trapFocus && focusPanel && activeFocusTraps.at(-1) !== focusPanel) return;
      if (closeOnEscape && event.key === "Escape") {
        onCloseRef.current?.();
        return;
      }
      if (!trapFocus || event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const viewport = document.getElementById("radon-toast-viewport");
      const focusable = [
        ...panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        ...(viewport?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? []),
      ].filter((element) => element.offsetParent !== null || element === panel);
      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      // The viewport is a body portal, so native Tab order cannot bridge the
      // panel and its toast actions. Own the complete cycle in both directions.
      const index = focusable.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey
        ? (index <= 0 ? focusable.length - 1 : index - 1)
        : (index + 1) % focusable.length;
      event.preventDefault();
      focusable[next].focus();
    };

    // Listen on `window` (not `document`): a window listener catches both real
    // Escape keypresses (which bubble target -> document -> window) and events
    // dispatched directly on window, so it is a strict superset of a document
    // listener. The previous bespoke BottomSheet handler bound to window.
    window.addEventListener("keydown", handleKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (trapFocus) panelRef.current?.focus();

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (trapFocus && focusPanel) {
        const index = activeFocusTraps.lastIndexOf(focusPanel);
        if (index !== -1) activeFocusTraps.splice(index, 1);
        if (previousOwns) dialog?.setAttribute("aria-owns", previousOwns);
        else dialog?.removeAttribute("aria-owns");
      }
      document.body.style.overflow = previousOverflow;
      if (trapFocus && previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
    };
  }, [open, closeOnEscape, trapFocus, portalTarget]);

  return { portalTarget, panelRef };
}

export default useDialogChrome;
