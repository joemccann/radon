"use client";

import { useRef, useEffect, useCallback, type ReactNode } from "react";
import { Maximize2, Minimize2, Moon, Sun } from "lucide-react";
import TickerSearch from "./TickerSearch";
import { useTickerNav } from "@/lib/useTickerNav";
import { useIBStatusContext, type IBDisplayStatus } from "@/lib/IBStatusContext";

type HeaderProps = {
  activeLabel: string;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  onToggleTheme: () => void;
  theme?: "dark" | "light";
  children?: ReactNode;
  /** Center slot — the live index-futures (ES/NQ/RTY) quote strip. With the
   *  header's space-between layout, a third flex child auto-centers. */
  futuresStrip?: ReactNode;
  onSearchUnavailable?: () => void;
  /** Latest portfolio/orders sync timestamp — surfaced as SAMPLE in the
   *  telemetry rail. Replaces the previous "Last sync" pill that lived
   *  inside the sync-controls children. */
  lastSync?: string | null;
};

type IntegrityClass = "ok" | "warn" | "dead";

function formatSampleTime(lastSync: string | null | undefined): string {
  if (!lastSync) return "---";
  const sampled = new Date(lastSync);
  if (Number.isNaN(sampled.getTime())) return "---";
  return sampled.toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "America/New_York",
  });
}

function integrityFor(status: IBDisplayStatus): { text: string; cls: IntegrityClass } {
  switch (status) {
    case "connected":
      return { text: "Nominal", cls: "ok" };
    case "awaiting_2fa":
      return { text: "Awaiting 2FA", cls: "warn" };
    case "unhealthy":
      return { text: "Degraded", cls: "warn" };
    case "unreachable":
      return { text: "Unreachable", cls: "dead" };
    case "ib_offline":
      return { text: "Gateway offline", cls: "dead" };
    case "relay_offline":
      return { text: "Relay offline", cls: "dead" };
  }
}

export default function Header({
  activeLabel,
  isFullscreen,
  onToggleFullscreen,
  onToggleTheme,
  theme,
  children,
  futuresStrip,
  onSearchUnavailable,
  lastSync,
}: HeaderProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  const { navigateToTicker } = useTickerNav();
  const { displayStatus } = useIBStatusContext();
  const integrity = integrityFor(displayStatus);
  const sampleAt = formatSampleTime(lastSync);

  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const handleSelect = useCallback(
    (symbol: string) => {
      navigateToTicker(symbol);
    },
    [navigateToTicker],
  );

  return (
    <header className="header">
      <div className="telemetry-rail" aria-label="Workspace telemetry">
        <span className="rail-section">{activeLabel}</span>
        <span className="rail-sep" aria-hidden>·</span>
        <span className="rail-meta">
          <span className="rail-k">sample</span>
          <span className="rail-v">{sampleAt} ET</span>
        </span>
        <span className="rail-sep" aria-hidden>·</span>
        <span className="rail-meta">
          <span className="rail-k">feed</span>
          <span className="rail-v">IB·UW</span>
        </span>
        <span className="rail-sep" aria-hidden>·</span>
        <span
          className={`rail-integrity rail-integrity-${integrity.cls}`}
          data-integrity={integrity.cls}
        >
          <span
            className={`rail-integrity-dot rail-integrity-dot-${integrity.cls}`}
            aria-hidden
          />
          {integrity.text}
        </span>
      </div>
      {futuresStrip ?? null}
      <div className="header-actions" suppressHydrationWarning>
        {children}
        <TickerSearch
          ref={searchRef}
          onSelect={handleSelect}
          onSearchUnavailable={onSearchUnavailable}
          placeholder="⌘K to search instruments…"
          className="search-input-wrapper"
        />
        <button
          suppressHydrationWarning
          className="fullscreen-toggle"
          onClick={onToggleFullscreen}
          title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
        >
          {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button
          suppressHydrationWarning
          className="theme-toggle"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>
    </header>
  );
}
