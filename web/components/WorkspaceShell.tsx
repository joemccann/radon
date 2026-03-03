"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import type { WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";
import { resolveSectionFromPath } from "@/lib/chat";
import { usePortfolio } from "@/lib/usePortfolio";
import { useOrders } from "@/lib/useOrders";
import { useToast } from "@/lib/useToast";
import { usePrices } from "@/lib/usePrices";
import { type OptionContract, portfolioLegToContract } from "@/lib/pricesProtocol";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import ChatPanel from "@/components/ChatPanel";
import MetricCards from "@/components/MetricCards";
import WorkspaceSections from "@/components/WorkspaceSections";
import ConnectionBanner from "@/components/ConnectionBanner";
import ToastContainer from "@/components/Toast";

type WorkspaceShellProps = {
  section?: WorkspaceSection;
};

export default function WorkspaceShell({ section }: WorkspaceShellProps) {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);
  const pathname = usePathname();
  const activeSection: WorkspaceSection = section ?? resolveSectionFromPath(pathname, "dashboard");
  const activeLabel = navItems.find((item) => item.route === activeSection)?.label ?? "Dashboard";
  const { toasts, addToast, removeToast } = useToast();

  const { data: portfolio, syncing: portfolioSyncing, error: portfolioError, lastSync: portfolioLastSync, syncNow: portfolioSyncNow } = usePortfolio();

  const portfolioSymbols = useMemo(
    () => (portfolio?.positions ?? []).map((p) => p.ticker),
    [portfolio],
  );

  const portfolioContracts = useMemo<OptionContract[]>(() => {
    const contracts: OptionContract[] = [];
    for (const pos of portfolio?.positions ?? []) {
      if (pos.structure_type === "Stock") continue;
      for (const leg of pos.legs) {
        const c = portfolioLegToContract(pos.ticker, pos.expiry, leg);
        if (c) contracts.push(c);
      }
    }
    return contracts;
  }, [portfolio]);

  const { prices, connected: wsConnected, ibConnected } = usePrices({
    symbols: portfolioSymbols,
    contracts: portfolioContracts,
  });

  const prevIbConnectedRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevIbConnectedRef.current !== null && prevIbConnectedRef.current !== ibConnected) {
      addToast(ibConnected ? "success" : "error", ibConnected ? "IB Gateway reconnected" : "IB Gateway connection lost", ibConnected ? 4000 : 6000);
    }
    prevIbConnectedRef.current = ibConnected;
  }, [ibConnected, addToast]);

  const isOrdersPage = activeSection === "orders";
  const { data: orders, syncing: ordersSyncing, error: ordersError, lastSync: ordersLastSync, syncNow: ordersSyncNow } = useOrders(isOrdersPage);
  const syncing = isOrdersPage ? ordersSyncing : portfolioSyncing;
  const error = isOrdersPage ? ordersError : portfolioError;
  const lastSync = isOrdersPage ? ordersLastSync : portfolioLastSync;
  const syncNow = isOrdersPage ? ordersSyncNow : portfolioSyncNow;
  const syncTarget = isOrdersPage ? "orders" : "portfolio";

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") {
      setTheme(saved);
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const systemTheme = prefersDark ? "dark" : "light";
      setTheme(systemTheme);
      document.documentElement.setAttribute("data-theme", systemTheme);
    }
  }, []);

  const resolvedTheme = theme ?? "dark";

  const actionTone = useMemo(() => {
    return resolvedTheme === "dark" ? "#f0f0f0" : "#0a0a0a";
  }, [resolvedTheme]);

  const toggleTheme = () => {
    const next = resolvedTheme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  };

  const syncLabel = lastSync
    ? `Last sync: ${new Date(lastSync).toLocaleTimeString()}`
    : error
      ? `Sync error`
      : "No sync yet";

  return (
    <div className="app-shell" suppressHydrationWarning>
      <Sidebar activeSection={activeSection} actionTone={actionTone} />

      <main className="main">
        <Header activeLabel={activeLabel} onToggleTheme={toggleTheme} theme={resolvedTheme}>
          <div className="sync-controls">
            <span className={`sync-status ${error ? "sync-error" : syncing ? "sync-active" : ""}`}>
              {syncLabel}
            </span>
            <button
              className="sync-button"
              onClick={syncNow}
              disabled={syncing}
              title={`Sync ${syncTarget} from IB Gateway`}
            >
              <RefreshCw size={14} className={syncing ? "spin" : ""} />
              {syncing ? "Syncing..." : "Sync Now"}
            </button>
          </div>
        </Header>

        <ConnectionBanner ibConnected={ibConnected} wsConnected={wsConnected} />

        <div className="content">
          <ChatPanel activeSection={activeSection} />

          {activeSection !== "dashboard" ? <MetricCards portfolio={portfolio} /> : null}

          {activeSection !== "dashboard" ? (
            <WorkspaceSections section={activeSection} portfolio={portfolio} orders={orders} prices={prices} />
          ) : null}
        </div>
      </main>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  );
}
