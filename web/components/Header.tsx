"use client";

import { useRef, useEffect, useCallback, type ReactNode } from "react";
import { Maximize2, Minimize2, Moon, Sun } from "lucide-react";
import TickerSearch from "./TickerSearch";
import { useTickerNav } from "@/lib/useTickerNav";
import { useIBStatusContext, type IBDisplayStatus } from "@/lib/IBStatusContext";
import { pushRecentTicker } from "./CommandPalette";
import styles from "./ClearShell.module.css";

type HeaderProps = {
  activeLabel: string;
  /** The telemetry route label owns the page heading on workspace routes
   *  whose content surface does not render its own h1. */
  isPageHeading?: boolean;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  onToggleTheme: () => void;
  theme?: "dark" | "light";
  children?: ReactNode;
  navigation?: ReactNode;
  compact?: boolean;
  /** Center slot — the live index-futures (ES/NQ/RTY) quote strip. With the
   *  header's space-between layout, a third flex child auto-centers. */
  futuresStrip?: ReactNode;
  onSearchUnavailable?: () => void;
  /** Latest portfolio/orders sync timestamp — surfaced as SAMPLE in the
   *  telemetry rail. Replaces the previous "Last sync" pill that lived
   *  inside the sync-controls children. */
  lastSync?: string | null;
  onOpenPalette?: () => void;
  isStale?: boolean;
  staleAgeMinutes?: number | null;
  onSyncNow?: () => void;
};

type IntegrityClass = "ok" | "warn" | "dead" | "demo";

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
    case "demo":
      return { text: "Sample data", cls: "demo" };
  }
}

export default function Header({
  activeLabel,
  isPageHeading = false,
  isFullscreen,
  onToggleFullscreen,
  onToggleTheme,
  theme,
  children,
  navigation,
  compact = false,
  futuresStrip,
  onSearchUnavailable,
  lastSync,
  onOpenPalette,
  isStale,
  staleAgeMinutes,
  onSyncNow,
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
        if (onOpenPalette) onOpenPalette();
        else searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onOpenPalette]);

  const handleSelect = useCallback(
    (symbol: string) => {
      pushRecentTicker(symbol);
      navigateToTicker(symbol);
    },
    [navigateToTicker],
  );

  return (
    <header className={`header ${styles.header}${compact ? ` ${styles.compact}` : ""}`}>
      <div className={styles.navigationSlot}>{navigation}</div>
      <div className={`telemetry-rail${isStale ? " telemetry-rail--stale" : ""}`} aria-label="Workspace telemetry">
        {isPageHeading ? (
          <h1 className="rail-section" title={activeLabel}>{activeLabel}</h1>
        ) : (
          <span className="rail-section" title={activeLabel}>{activeLabel}</span>
        )}
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
          aria-live="polite"
          aria-atomic="true"
        >
          <span
            className={`rail-integrity-dot rail-integrity-dot-${integrity.cls}`}
            aria-hidden
          />
          {integrity.text}
        </span>
        {isStale && staleAgeMinutes != null && (
          <>
            <span className="rail-sep" aria-hidden>·</span>
            <span className="stale-pill" data-testid="stale-pill">
              stale {staleAgeMinutes}m
              {onSyncNow && (
                <button type="button" onClick={onSyncNow} aria-label="Sync now">Sync</button>
              )}
            </span>
          </>
        )}
      </div>
      <div className={styles.contextTools}>
        {futuresStrip ?? null}
        {children}
      </div>
      <div className="header-actions" suppressHydrationWarning>
        <button
          type="button"
          className="command-palette-trigger"
          onClick={onOpenPalette}
          aria-label="Open command palette"
          data-testid="command-palette-trigger"
          title="⌘K to open palette"
        >
          ⌘K
        </button>
        <TickerSearch
          ref={searchRef}
          onSelect={handleSelect}
          onSearchUnavailable={onSearchUnavailable}
          placeholder="Search any instrument"
          className="search-input-wrapper"
          ariaLabel="Search ticker"
        />
        <button
          type="button"
          suppressHydrationWarning
          className="fullscreen-toggle"
          onClick={onToggleFullscreen}
          title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
        >
          {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button
          type="button"
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
