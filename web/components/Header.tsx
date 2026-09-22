"use client";

import { useRef, useEffect, useCallback, useState, useId, type ReactNode } from "react";
import { ChevronDown, Maximize2, Minimize2, Moon, Sun } from "lucide-react";
import TickerSearch from "./TickerSearch";
import { useTickerNav } from "@/lib/useTickerNav";
import { useIBStatusContext, type IBDisplayStatus } from "@/lib/IBStatusContext";
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
  isStale,
  staleAgeMinutes,
  onSyncNow,
}: HeaderProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  const telemetryRef = useRef<HTMLDivElement | null>(null);
  const telemetryTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [telemetryOpen, setTelemetryOpen] = useState(false);
  const telemetryId = useId();
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

  useEffect(() => {
    if (!telemetryOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node
        && !telemetryRef.current?.contains(event.target)
        && !telemetryTriggerRef.current?.contains(event.target)) setTelemetryOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // CSS owns the responsive chain scope. An open disclosure can outlive
      // that scope after a resize or a deck change; do not consume Escape
      // intended for the now-visible workspace or focus a hidden trigger.
      const trigger = telemetryTriggerRef.current;
      if (!trigger || window.getComputedStyle(trigger).display === "none") {
        setTelemetryOpen(false);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setTelemetryOpen(false);
      telemetryTriggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape, true);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape, true);
    };
  }, [telemetryOpen]);

  const handleSelect = useCallback(
    (symbol: string) => {
      navigateToTicker(symbol);
    },
    [navigateToTicker],
  );

  return (
    <header className={`header ${styles.header}${compact ? ` ${styles.compact}` : ""}`}>
      <div className={styles.navigationSlot}>{navigation}</div>
      <div
        ref={telemetryRef}
        id={telemetryId}
        className={styles.telemetryPanel}
        data-open={telemetryOpen}
        data-testid="chain-feed-panel"
      >
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
      </div>
      <div className="header-actions" suppressHydrationWarning>
        <button
          ref={telemetryTriggerRef}
          type="button"
          className={styles.telemetryTrigger}
          aria-expanded={telemetryOpen}
          aria-controls={telemetryId}
          aria-label={`Feed & sync: ${integrity.text}${isStale ? ", stale snapshot" : ""}`}
          title="Feed, freshness and sync"
          data-testid="chain-feed-trigger"
          data-feed-integrity={isStale ? "warn" : integrity.cls}
          onClick={() => setTelemetryOpen((open) => !open)}
        >
          <span className={`rail-integrity-dot rail-integrity-dot-${isStale ? "warn" : integrity.cls}`} aria-hidden />
          <span>{isStale ? `Stale${staleAgeMinutes != null ? ` ${staleAgeMinutes}m` : ""}` : integrity.text}</span>
          <ChevronDown size={12} aria-hidden />
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
