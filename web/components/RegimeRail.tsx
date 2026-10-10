"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  REGIME_RAIL_GROUPS,
  REGIME_TABS,
  REGIME_TAB_LABEL,
  FLAGGABLE_REGIME_TABS,
  elevatedCount,
  isFlagged,
  type RailStatuses,
  type RegimeTab,
} from "@/lib/regimeRail";

type RegimeRailProps = {
  activeTab: RegimeTab;
  onSelect: (tab: RegimeTab) => void;
  statuses: RailStatuses;
};

/**
 * Grouped indicator rail for /regime (desktop ≥1100px). Groups mirror the
 * design's IA: Composite / Volatility / Positioning / Breadth & sentiment /
 * Models. Each row carries a status dot + latest reading where a payload is
 * loaded; group headers surface flagged counts.
 */
export default function RegimeRail({ activeTab, onSelect, statuses }: RegimeRailProps) {
  const [filter, setFilter] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return REGIME_RAIL_GROUPS.map((group) => {
      const tabs = needle
        ? group.tabs.filter((tab) => REGIME_TAB_LABEL[tab].toLowerCase().includes(needle))
        : [...group.tabs];
      const flagged = group.tabs.filter((tab) => isFlagged(statuses[tab])).length;
      return { label: group.label, tabs, flagged };
    }).filter((group) => group.tabs.length > 0);
  }, [filter, statuses]);

  const scrollToActiveTab = useCallback((tab: RegimeTab, behavior: ScrollBehavior = "smooth") => {
    const list = listRef.current;
    if (!list) return;
    const item = list.querySelector<HTMLElement>(`[data-tab="${tab}"]`);
    if (!item) return;

    const listRect = list.getBoundingClientRect();
    const itemRect = item.getBoundingClientRect();

    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const scrollBehavior = prefersReducedMotion ? "auto" : behavior;

    const targetScrollTop =
      listRect.height > 0
        ? list.scrollTop + (itemRect.top - listRect.top) - listRect.height / 2 + itemRect.height / 2
        : item.offsetTop - (list.clientHeight || 0) / 2 + (item.clientHeight || 0) / 2;

    if (typeof list.scrollTo === "function") {
      list.scrollTo({ top: Math.max(0, targetScrollTop), behavior: scrollBehavior });
    } else {
      list.scrollTop = Math.max(0, targetScrollTop);
    }
  }, []);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      scrollToActiveTab(activeTab);
    });
    return () => cancelAnimationFrame(frame);
  }, [activeTab, filter, scrollToActiveTab]);

  return (
    <nav className="regime-rail" aria-label="Regime indicators">
      <div className="regime-rail__filter">
        <input
          type="search"
          value={filter}
          placeholder="Filter indicators"
          aria-label="Filter indicators"
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="regime-rail__list" ref={listRef}>
        {groups.map((group) => (
          <div key={group.label} className="regime-rail__group">
            <div className="regime-rail__group-head">
              <span>{group.label}</span>
              <span className={`regime-rail__count${group.flagged ? " regime-rail__count--flagged" : ""}`}>
                {group.flagged ? `${group.flagged} FLAGGED` : group.tabs.length}
              </span>
            </div>
            {group.tabs.map((tab) => {
              const status = statuses[tab];
              const active = tab === activeTab;
              return (
                <button
                  key={tab}
                  type="button"
                  data-tab={tab}
                  aria-label={`${REGIME_TAB_LABEL[tab]}: ${group.label}`}
                  aria-current={active ? "page" : undefined}
                  className={`regime-rail__item${active ? " active" : ""}`}
                  onClick={() => {
                    scrollToActiveTab(tab);
                    onSelect(tab);
                  }}
                >
                  <span className={`regime-rail__dot regime-rail__dot--${status?.tone ?? "neutral"}`} aria-hidden="true" />
                  <span className="regime-rail__name">{REGIME_TAB_LABEL[tab]}</span>
                  {status && <span className="regime-rail__val">{status.value}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <div className="regime-rail__foot">
        {FLAGGABLE_REGIME_TABS.length} MONITORED · {elevatedCount(statuses)} ELEVATED
      </div>
    </nav>
  );
}
