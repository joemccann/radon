export type ScannerMode = "flow" | "discover" | "theta" | "strength" | "leap" | "garch";

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
];

/**
 * The scanner's six-mode tab strip. Each tab carries a small count chip when
 * its scan has a known result count, so signal presence reads across modes
 * without clicking through them.
 */
export function ScannerModeTabs({ mode, onModeChange, counts }: ScannerModeTabsProps) {
  return (
    <div className="scanner-mode-tabs" role="tablist" aria-label="Scanner mode">
      {TABS.map((tab) => {
        const count = counts[tab.mode];
        return (
          <button
            key={tab.mode}
            type="button"
            role="tab"
            aria-selected={mode === tab.mode}
            className={`scanner-mode-tab${mode === tab.mode ? " scanner-mode-tab--active" : ""}`}
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
  );
}
