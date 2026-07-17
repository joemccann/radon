"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import OptionsExposurePanel from "./OptionsExposurePanel";
import styles from "./OptionsWorkspacePanel.module.css";

type OptionsWorkspaceTab = "net-gex" | "dex" | "greeks" | "open-interest" | "volatility";

type TabDefinition = {
  id: OptionsWorkspaceTab;
  label: string;
  available: boolean;
};

const TABS: readonly TabDefinition[] = [
  { id: "net-gex", label: "Net GEX", available: true },
  { id: "dex", label: "DEX", available: false },
  { id: "greeks", label: "Greeks", available: false },
  { id: "open-interest", label: "Open Interest", available: false },
  { id: "volatility", label: "VIX / Volatility", available: false },
];

function activeTabFromPath(pathname: string | null): OptionsWorkspaceTab {
  return pathname?.match(/^\/options\/net-gex(?:\/|$)/) ? "net-gex" : "net-gex";
}

type OptionsWorkspacePanelProps = {
  symbol: string;
};

/**
 * Shared URL-driven shell for option-surface measurements. Tabs are deliberately
 * declared here before their data modules exist, so the workspace contract stays
 * stable as DEX, Greeks, OI, and volatility surfaces are added.
 */
export default function OptionsWorkspacePanel({ symbol }: OptionsWorkspacePanelProps) {
  const pathname = usePathname();
  const router = useRouter();
  const activeTab = activeTabFromPath(pathname);
  const activeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [activeTab]);

  const goToTab = (tab: OptionsWorkspaceTab) => {
    if (tab === "net-gex") {
      router.push(`/options/net-gex?symbol=${encodeURIComponent(symbol)}`);
    }
  };

  return (
    <div className={styles.workspace} data-testid="options-workspace">
      <div className={`ticker-tabs ${styles.tabBar}`} role="tablist" aria-label="Options measurements">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.id;
          const isAvailable = tab.available;
          return (
            <button
              key={tab.id}
              ref={isActive ? activeRef : undefined}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-disabled={isAvailable ? undefined : "true"}
              tabIndex={isAvailable ? 0 : -1}
              className={`ticker-tab ${styles.tab}${isActive ? " active" : ""}${isAvailable ? "" : ` ${styles.planned}`}`}
              onClick={isAvailable ? () => goToTab(tab.id) : undefined}
            >
              {tab.label}
              {!isAvailable ? <span className={styles.plannedLabel}>Planned</span> : null}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" aria-label="Net GEX">
        <OptionsExposurePanel symbol={symbol} />
      </div>
    </div>
  );
}
