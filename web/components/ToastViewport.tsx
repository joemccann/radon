"use client";

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

let viewport: HTMLDivElement | null = null;
let owners = 0;

/** One body-level stack for shell notifications and errors in any dialog/page. */
export default function ToastViewport({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!viewport || !viewport.isConnected) {
      viewport = document.createElement("div");
      viewport.className = "toast-container";
      viewport.id = "radon-toast-viewport";
      viewport.dataset.toastViewport = "";
      // Modal backdrops use 10000; recovery actions must remain clickable.
      viewport.style.zIndex = "10002";
      // Keep large failure bursts reachable on narrow screens.
      viewport.style.maxHeight = "var(--toast-viewport-max-height, calc(100dvh - 48px - var(--safe-bottom, 0px)))";
      viewport.style.maxWidth = "var(--toast-viewport-max-width, calc(100vw - 40px))";
      viewport.style.overflowY = "auto";
      document.body.appendChild(viewport);
    }
    owners += 1;
    setTarget(viewport);
    return () => {
      owners -= 1;
      if (owners === 0) {
        viewport?.remove();
        viewport = null;
      }
    };
  }, []);
  return target ? createPortal(children, target) : null;
}
