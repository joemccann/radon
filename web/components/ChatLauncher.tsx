"use client";

import { useEffect, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import type { WorkspaceSection } from "@/lib/types";
import type { PortfolioData } from "@/lib/types";

/**
 * ChatLauncher — global ⌘J overlay. Mounted in WorkspaceShell so chat is
 * one keystroke away from every page without taking up dashboard real
 * estate. Escape dismisses. Click on the dim backdrop dismisses. Inside
 * the overlay, ChatPanel renders with the active workspace section.
 */

type ChatLauncherProps = {
  activeSection: WorkspaceSection;
  portfolio: PortfolioData | null | undefined;
};

export default function ChatLauncher({ activeSection, portfolio }: ChatLauncherProps) {
  const [open, setOpen] = useState(false);

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
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) return null;

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
      <div className="chat-launcher__panel">
        <div className="chat-launcher__head">
          <span className="chat-launcher__kicker">Radon Chat</span>
          <span className="chat-launcher__hint">Esc to dismiss</span>
        </div>
        <ChatPanel activeSection={activeSection} portfolio={portfolio} />
      </div>
    </div>
  );
}
