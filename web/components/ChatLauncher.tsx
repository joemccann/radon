"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { subscribeAsk } from "@/lib/agent/askBus";
import type { PriceData } from "@/lib/pricesProtocol";
import type { WorkspaceSection } from "@/lib/types";
import type { PortfolioData } from "@/lib/types";

// The assistant is already unmounted while closed. Keep its markdown/order
// tooling out of the account's critical download without changing that state
// lifecycle or the order-risk gate inside the panel.
const ChatPanel = dynamic(() => import("@/components/ChatPanel"), {
  loading: () => <div className="chat-panel" role="status">Opening assistant</div>,
});

/**
 * ChatLauncher — global ⌘J overlay. Mounted in WorkspaceShell so chat is
 * one keystroke away from every page without taking up dashboard real
 * estate. Escape dismisses. Click on the dim backdrop dismisses. Inside
 * the overlay, ChatPanel renders with the active workspace section.
 */

type ChatLauncherProps = {
  activeSection: WorkspaceSection;
  portfolio: PortfolioData | null | undefined;
  /** Live quotes from the shell's single relay subscription, for the approval gate. */
  prices?: Record<string, PriceData>;
};

export default function ChatLauncher({ activeSection, portfolio, prices }: ChatLauncherProps) {
  const [open, setOpen] = useState(false);
  // Hydration marker: flipped by the keydown-listener effect below, so an e2e
  // spec can wait for the ⌘J handler to be ATTACHED before pressing the key
  // instead of retrying a synthetic event across hydration (T-419).
  const [shortcutReady, setShortcutReady] = useState(false);
  // A prompt handed over from another surface (newsfeed follow-up chips).
  // Consumed once by ChatPanel, then cleared so it can't re-fire on re-render.
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null);

  useEffect(
    () =>
      subscribeAsk((prompt) => {
        setSeedPrompt(prompt);
        setOpen(true);
      }),
    [],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
      if (event.key === "Escape" && open) {
        event.preventDefault();
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    setShortcutReady(true);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) return shortcutReady ? <span data-testid="chat-launcher-ready" hidden /> : null;

  return (
    <div
      className="chat-launcher"
      role="dialog"
      aria-modal="true"
      aria-label="Radon chat"
    >
      <button
        type="button"
        className="chat-launcher__scrim"
        onClick={() => setOpen(false)}
        aria-label="Dismiss chat"
      />
      <div className="chat-launcher__panel" data-testid="chat-launcher-panel">
        {/* No header bar (design-lab Variant A): the composer rail carries the
            esc affordance, so the overlay is the conversation and nothing else. */}
        <ChatPanel
          activeSection={activeSection}
          portfolio={portfolio}
          isOpen={open}
          seedPrompt={seedPrompt}
          onSeedConsumed={() => setSeedPrompt(null)}
          prices={prices}
        />
      </div>
    </div>
  );
}
