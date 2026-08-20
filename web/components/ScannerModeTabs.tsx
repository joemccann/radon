"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { computeScrollAffordance, type ScrollAffordance } from "@/lib/scrollAffordance";

export type ScannerMode = "flow" | "discover" | "theta" | "strength" | "leap" | "garch" | "vol-cone";

type ScannerTabCounts = Partial<Record<ScannerMode, number>>;

type ScannerModeTabsProps = {
  mode: ScannerMode;
  onModeChange: (mode: ScannerMode) => void;
  /**
   * Per-tab result counts (signals, mispriced, actionable, ...). A missing
   * entry renders no chip - discover self-fetches so it never carries one.
   */
  counts: ScannerTabCounts;
};

const TABS: { mode: ScannerMode; label: string }[] = [
  { mode: "flow", label: "Flow Signals" },
  { mode: "discover", label: "Discover" },
  { mode: "theta", label: "Theta Harvester" },
  { mode: "strength", label: "7-Step Strength" },
  { mode: "leap", label: "LEAP" },
  { mode: "garch", label: "GARCH" },
  { mode: "vol-cone", label: "VOL CONE" },
];

const ROVING_KEYS = ["ArrowLeft", "ArrowRight", "Home", "End"];

/**
 * The scanner mode tab strip. Each tab carries a small count chip when
 * its scan has a known result count, so signal presence reads across modes
 * without clicking through them.
 *
 * On phones the strip scrolls horizontally; the shell's data-overflow-left /
 * data-overflow-right attributes drive edge fades so off-screen modes are
 * visible as such — the strip used to end flush at the viewport edge and
 * read as three-tabs-total. Arrow keys rove focus (WAI-ARIA tabs, manual
 * activation: Enter/Space selects, so arrowing across seven modes does not
 * fire seven scans).
 */
export function ScannerModeTabs({ mode, onModeChange, counts }: ScannerModeTabsProps) {
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const [overflow, setOverflow] = useState<ScrollAffordance>({ left: false, right: false });

  const syncOverflow = useCallback(() => {
    const strip = stripRef.current;
    if (!strip) return;
    setOverflow((prev) => {
      const next = computeScrollAffordance(strip.scrollLeft, strip.scrollWidth, strip.clientWidth);
      return prev.left === next.left && prev.right === next.right ? prev : next;
    });
  }, []);

  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    syncOverflow();
    strip.addEventListener("scroll", syncOverflow, { passive: true });
    const resizeObserver =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(syncOverflow) : null;
    resizeObserver?.observe(strip);
    return () => {
      strip.removeEventListener("scroll", syncOverflow);
      resizeObserver?.disconnect();
    };
  }, [syncOverflow]);

  // When the strip scrolls horizontally (phone), keep the selected tab in view
  // so arriving on e.g. ?mode=garch does not leave the active tab off-screen.
  // No-op on desktop where the strip never overflows.
  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [mode]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!ROVING_KEYS.includes(event.key)) return;
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    if (tabs.length === 0) return;
    event.preventDefault();
    const focused = tabs.indexOf(document.activeElement as HTMLButtonElement);
    const from = focused >= 0 ? focused : tabs.findIndex((tab) => tab.tabIndex === 0);
    const target =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : event.key === "ArrowRight"
            ? (from + 1) % tabs.length
            : (from - 1 + tabs.length) % tabs.length;
    tabs[target].focus();
    tabs[target].scrollIntoView?.({ inline: "nearest", block: "nearest" });
  }, []);

  return (
    <div
      className="scanner-mode-tabs-shell"
      data-overflow-left={overflow.left ? "true" : "false"}
      data-overflow-right={overflow.right ? "true" : "false"}
    >
      <div
        className="scanner-mode-tabs"
        role="tablist"
        aria-label="Scanner mode"
        ref={stripRef}
        onKeyDown={handleKeyDown}
      >
        {TABS.map((tab) => {
          const count = counts[tab.mode];
          const isActive = mode === tab.mode;
          return (
            <button
              key={tab.mode}
              ref={isActive ? activeRef : undefined}
              type="button"
              role="tab"
              aria-selected={isActive}
              tabIndex={isActive ? 0 : -1}
              className={`scanner-mode-tab${isActive ? " scanner-mode-tab--active" : ""}`}
              onClick={() => onModeChange(tab.mode)}
            >
              {tab.label}
              {count != null && (
                <span
                  className="scanner-mode-tab__count"
                  aria-hidden="true"
                  data-hot={count > 0 ? "true" : "false"}
                  data-testid={`scanner-tab-count-${tab.mode}`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
