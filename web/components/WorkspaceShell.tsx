"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import type { OrdersData, WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";
import { resolveSectionFromPath } from "@/lib/chat";
import { usePortfolio } from "@/lib/usePortfolio";
import { useOrders } from "@/lib/useOrders";
import { useToast } from "@/lib/useToast";
import { useOrderActions } from "@/lib/OrderActionsContext";
import { usePrices } from "@/lib/usePrices";
import { useBlotter } from "@/lib/useBlotter";
import { usePreviousClose } from "@/lib/usePreviousClose";
import { type OptionContract, type IndexContract, optionKey, portfolioLegToContract } from "@/lib/pricesProtocol";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import ChatPanel from "@/components/ChatPanel";
import MetricCards from "@/components/MetricCards";
import WorkspaceSections from "@/components/WorkspaceSections";
import ToastContainer from "@/components/Toast";
import TickerDetailModal from "@/components/TickerDetailModal";
import { useTickerDetail } from "@/lib/TickerDetailContext";

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

  // Bridge order-actions context → toasts & orders updater
  const { drainNotifications, setOrdersUpdater } = useOrderActions();

  const isOrdersPage = activeSection === "orders";
  const { data: orders, syncing: ordersSyncing, error: ordersError, lastSync: ordersLastSync, syncNow: ordersSyncNow, updateData: updateOrdersData } = useOrders(isOrdersPage);

  const orderSymbols = useMemo(
    () => (orders?.open_orders ?? []).map((o) => o.contract.symbol),
    [orders],
  );

  const orderContracts = useMemo<OptionContract[]>(() => {
    const contracts: OptionContract[] = [];
    for (const o of orders?.open_orders ?? []) {
      const c = o.contract;
      if (c.secType !== "OPT" || c.strike == null || !c.right || !c.expiry) continue;
      const right = c.right === "C" || c.right === "P"
        ? c.right
        : c.right === "CALL" ? "C" : c.right === "PUT" ? "P" : null;
      if (!right) continue;
      const expiryClean = c.expiry.replace(/-/g, "");
      if (expiryClean.length !== 8) continue;
      contracts.push({ symbol: c.symbol.toUpperCase(), expiry: expiryClean, strike: c.strike, right });
    }
    return contracts;
  }, [orders]);

  const allSymbols = useMemo(
    () => [...new Set([...portfolioSymbols, ...orderSymbols])],
    [portfolioSymbols, orderSymbols],
  );

  const allContracts = useMemo(
    () => [...portfolioContracts, ...orderContracts],
    [portfolioContracts, orderContracts],
  );

  const regimeIndexes = useMemo<IndexContract[]>(
    () => activeSection === "regime"
      ? [{ symbol: "VIX", exchange: "CBOE" }, { symbol: "VVIX", exchange: "CBOE" }]
      : [],
    [activeSection],
  );

  const { prices: rawPrices, connected: wsConnected, ibConnected: rawIbConnected } = usePrices({
    symbols: allSymbols,
    contracts: allContracts,
    indexes: regimeIndexes,
  });

  // Debounce ibConnected: disconnections must persist >2s before surfacing to UI.
  // IB farm connectivity checks fire brief disconnected→connected sequences that
  // would otherwise flash the banner/toast every few seconds.
  const [ibConnected, setIbConnected] = useState(rawIbConnected);
  const ibDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (ibDebounceRef.current) clearTimeout(ibDebounceRef.current);
    if (rawIbConnected) {
      // Reconnection: propagate immediately (user wants to know it's back)
      setIbConnected(true);
    } else {
      // Disconnection: delay 2s to filter out brief farm-check flickers
      ibDebounceRef.current = setTimeout(() => setIbConnected(false), 2000);
    }
    return () => { if (ibDebounceRef.current) clearTimeout(ibDebounceRef.current); };
  }, [rawIbConnected]);

  // Backfill missing previous-close from Yahoo Finance / UW for day-change calc
  const prices = usePreviousClose(rawPrices);

  // Blotter for today's realized P&L
  const { data: blotterData } = useBlotter();

  // Sync prices + portfolio into ticker-detail context (refs, no re-renders)
  const { setPrices: setTickerPrices, setPortfolio: setTickerPortfolio, setOrders: setTickerOrders } = useTickerDetail();
  useEffect(() => { setTickerPrices(prices); }, [prices, setTickerPrices]);
  useEffect(() => { setTickerPortfolio(portfolio); }, [portfolio, setTickerPortfolio]);
  useEffect(() => { setTickerOrders(orders); }, [orders, setTickerOrders]);

  const prevIbConnectedRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevIbConnectedRef.current !== null && prevIbConnectedRef.current !== ibConnected) {
      addToast(ibConnected ? "success" : "error", ibConnected ? "IB Gateway reconnected" : "IB Gateway connection lost", ibConnected ? 4000 : 6000);
    }
    prevIbConnectedRef.current = ibConnected;
  }, [ibConnected, addToast]);
  const syncing = isOrdersPage ? ordersSyncing : portfolioSyncing;
  const error = isOrdersPage ? ordersError : portfolioError;
  const lastSync = isOrdersPage ? ordersLastSync : portfolioLastSync;
  const syncNow = isOrdersPage ? ordersSyncNow : portfolioSyncNow;
  const syncTarget = isOrdersPage ? "orders" : "portfolio";

  // Register the orders-data updater so the cancel provider can push fresh data
  useEffect(() => {
    setOrdersUpdater(updateOrdersData);
    return () => setOrdersUpdater(null);
  }, [setOrdersUpdater, updateOrdersData]);

  // Drain cancel-context notifications into the toast system
  useEffect(() => {
    const id = setInterval(() => {
      const notes = drainNotifications();
      for (const n of notes) addToast(n.type, n.message, n.duration);
    }, 500);
    return () => clearInterval(id);
  }, [drainNotifications, addToast]);

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
      <Sidebar activeSection={activeSection} actionTone={actionTone} ibConnected={ibConnected} lastSync={lastSync} />

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

        <div className="content">
          {activeSection === "dashboard" ? <ChatPanel activeSection={activeSection} /> : null}

          {activeSection !== "dashboard" ? <MetricCards portfolio={portfolio} prices={prices} realizedPnl={blotterData?.summary.realized_pnl ?? 0} section={activeSection} /> : null}

          {activeSection !== "dashboard" ? (
            <WorkspaceSections section={activeSection} portfolio={portfolio} orders={orders} prices={prices} />
          ) : null}
        </div>
      </main>

      <TickerDetailModal />
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  );
}
