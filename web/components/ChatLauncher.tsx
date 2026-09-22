"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import dynamic from "next/dynamic";
import { subscribeAsk } from "@/lib/agent/askBus";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData, WorkspaceSection } from "@/lib/types";

// Load on first use; retain this page's conversation and draft when dismissed.
const ChatPanel = dynamic(() => import("@/components/ChatPanel"), {
  loading: () => <div className="chat-panel" role="status">Opening assistant</div>,
});

type ChatLauncherProps = {
  activeSection: WorkspaceSection;
  portfolio: PortfolioData | null | undefined;
  prices?: Record<string, PriceData>;
};

export default function ChatLauncher({ activeSection, portfolio, prices }: ChatLauncherProps) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [shortcutReady, setShortcutReady] = useState(false);
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const show = useCallback(() => {
    if (!open) openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setLoaded(true);
    setOpen(true);
  }, [open]);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => subscribeAsk((prompt) => {
    setSeedPrompt(prompt);
    show();
  }), [show]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        if (open) close(); else show();
      }
      if (event.key === "Escape" && open) {
        event.preventDefault();
        close();
      }
      if (event.key === "Tab" && open) {
        const selector = 'button:not(:disabled), a[href], textarea, select, input:not([type="hidden"]), summary, [tabindex="0"]';
        const viewport = document.getElementById("radon-toast-viewport");
        const controls = [
          ...(dialogRef.current?.querySelectorAll<HTMLElement>(selector) ?? []),
          ...(viewport?.querySelectorAll<HTMLElement>(selector) ?? []),
        ].filter((el) => !el.closest('[hidden], [inert]') && el.tabIndex >= 0);
        event.preventDefault();
        if (!controls.length) { dialogRef.current?.focus(); return; }
        const index = controls.indexOf(document.activeElement as HTMLElement);
        const next = event.shiftKey
          ? (index <= 0 ? controls.length - 1 : index - 1)
          : (index + 1) % controls.length;
        controls[next].focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    setShortcutReady(true);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, close, show]);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    const siblings = Array.from(document.body.children).filter(
      (el): el is HTMLElement => el instanceof HTMLElement && el !== dialogRef.current && !el.hasAttribute("data-toast-viewport"),
    );
    const prior = siblings.map((el) => el.inert);
    siblings.forEach((el) => { el.inert = true; });
    document.body.style.overflow = "hidden";
    return () => {
      siblings.forEach((el, i) => { el.inert = prior[i]; });
      document.body.style.overflow = previousOverflow;
      if (openerRef.current?.isConnected) openerRef.current.focus();
    };
  }, [open]);

  return <>
    {shortcutReady ? <span data-testid="chat-launcher-ready" hidden /> : null}
    {loaded ? createPortal(
      <div ref={dialogRef} className="chat-launcher radon-clear" hidden={!open}
        role="dialog" aria-modal="true" aria-label="Radon chat" aria-owns={open ? "radon-toast-viewport" : undefined} tabIndex={-1}>
        <div className="chat-launcher__scrim" onClick={close} aria-hidden="true" />
        <div className="chat-launcher__panel" data-testid="chat-launcher-panel">
          <ChatPanel activeSection={activeSection} portfolio={portfolio} prices={prices}
            isOpen={open} onClose={close} seedPrompt={seedPrompt}
            onSeedConsumed={() => setSeedPrompt(null)} />
        </div>
      </div>, document.body,
    ) : null}
  </>;
}
