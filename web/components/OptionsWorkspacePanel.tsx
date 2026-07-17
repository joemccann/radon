"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
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

const TICKER_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;

function normalizeTicker(value: string | undefined): string | null {
  const normalized = value?.trim().toUpperCase() ?? "";
  return TICKER_RE.test(normalized) ? normalized : null;
}

function activeTabFromPath(pathname: string | null): OptionsWorkspaceTab {
  return pathname?.match(/^\/options\/net-gex(?:\/|$)/) ? "net-gex" : "net-gex";
}

type OptionsWorkspacePanelProps = {
  symbol?: string;
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
  const initialSymbol = normalizeTicker(symbol);
  const [tickerInput, setTickerInput] = useState(initialSymbol ?? "");
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(initialSymbol);
  const [tickerError, setTickerError] = useState<string | null>(null);
  const previousSymbolRef = useRef(symbol);

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [activeTab]);

  useEffect(() => {
    if (previousSymbolRef.current === symbol) return;
    previousSymbolRef.current = symbol;
    const nextSymbol = normalizeTicker(symbol);
    setTickerInput(nextSymbol ?? "");
    setSelectedSymbol(nextSymbol);
    setTickerError(null);
  }, [symbol]);

  const goToTab = (tab: OptionsWorkspaceTab) => {
    if (tab === "net-gex") {
      router.push(selectedSymbol ? `/options/net-gex?symbol=${encodeURIComponent(selectedSymbol)}` : "/options/net-gex");
    }
  };

  const submitTicker = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextSymbol = normalizeTicker(tickerInput);
    if (!nextSymbol) {
      setTickerError("Enter a valid ticker symbol.");
      return;
    }
    setTickerError(null);
    setSelectedSymbol(nextSymbol);
    router.push(`/options/net-gex?symbol=${encodeURIComponent(nextSymbol)}`);
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
        {selectedSymbol ? (
          <OptionsExposurePanel symbol={selectedSymbol} />
        ) : (
          <section className={styles.entry} data-testid="options-ticker-entry">
            <div>
              <div className={styles.entryEyebrow}>OPTIONS / NET GEX</div>
              <h2>Measure dealer exposure</h2>
              <p>Enter an underlying ticker to reconstruct strike-level options exposure.</p>
            </div>
            <form className={styles.entryForm} role="search" aria-label="Options ticker" onSubmit={submitTicker}>
              <label htmlFor="options-ticker-input">Ticker symbol</label>
              <div className={styles.entryControls}>
                <input
                  id="options-ticker-input"
                  type="text"
                  inputMode="text"
                  autoComplete="off"
                  autoCapitalize="characters"
                  maxLength={10}
                  placeholder="AAPL"
                  value={tickerInput}
                  onChange={(event) => {
                    setTickerInput(event.target.value.toUpperCase());
                    if (tickerError) setTickerError(null);
                  }}
                />
                <button type="submit" disabled={!tickerInput.trim()}>Load exposure</button>
              </div>
              {tickerError ? <p className={styles.entryError} role="alert">{tickerError}</p> : null}
            </form>
          </section>
        )}
      </div>
    </div>
  );
}
