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
import { computeRealizedPnlFromFills } from "@/lib/realized-pnl";
import { usePreviousClose } from "@/lib/usePreviousClose";
import { type OptionContract, type IndexContract, optionKey, portfolioLegToContract } from "@/lib/pricesProtocol";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import ChatPanel from "@/components/ChatPanel";
import MetricCards from "@/components/MetricCards";
import WorkspaceSections from "@/components/WorkspaceSections";
import ToastContainer from "@/components/Toast";
import ConnectionBanner from "@/components/ConnectionBanner";
import { useTickerDetail } from "@/lib/TickerDetailContext";

type WorkspaceShellProps = {
  section?: WorkspaceSection;
  tickerParam?: string;
};

export default function WorkspaceShell({ section, tickerParam }: WorkspaceShellProps) {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const pathname = usePathname();
  const activeSection: WorkspaceSection = section ?? resolveSectionFromPath(pathname, "dashboard");
  const navLabel = navItems.find((item) => item.route === activeSection)?.label ?? "Dashboard";
  const activeLabel = activeSection === "ticker-detail" && tickerParam ? tickerParam : navLabel;
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
  // Always fetch orders so fills are available for the Realized P&L card on all pages
  const { data: orders, syncing: ordersSyncing, error: ordersError, lastSync: ordersLastSync, syncNow: ordersSyncNow, updateData: updateOrdersData } = useOrders(true);

  // Trigger a fresh IB sync every time the user navigates TO the orders page.
  // place/modify/cancel all sync orders.json immediately after the action, so
  // this primarily catches IB-side changes (partial fills, status updates, etc.)
  // that happened while the user was on another page.
  useEffect(() => {
    if (isOrdersPage) {
      ordersSyncNow();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOrdersPage]);

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

  const regimeStocks = useMemo(
    () => activeSection === "regime"
      ? ["SPY"]
      : [],
    [activeSection],
  );

  const tickerSymbols = useMemo(
    () => tickerParam ? [tickerParam] : [],
    [tickerParam],
  );

  const allSymbols = useMemo(
    () => [...new Set([...portfolioSymbols, ...orderSymbols, ...regimeStocks, ...tickerSymbols])],
    [portfolioSymbols, orderSymbols, regimeStocks, tickerSymbols],
  );

  const tickerDetail = useTickerDetail();

  const allContracts = useMemo(
    () => [...portfolioContracts, ...orderContracts, ...tickerDetail.chainContracts],
    [portfolioContracts, orderContracts, tickerDetail.chainContracts],
  );

  const regimeIndexes = useMemo<IndexContract[]>(
    () => activeSection === "regime"
      ? [
          { symbol: "VIX", exchange: "CBOE" },
          { symbol: "VVIX", exchange: "CBOE" },
          { symbol: "COR1M", exchange: "CBOE" },
        ]
      : [],
    [activeSection],
  );

  const {
    prices: rawPrices,
    fundamentals,
    connected: wsConnected,
    ibConnected: rawIbConnected,
    ibIssue,
    ibStatusMessage,
  } = usePrices({
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

  // Blotter (historical flex query — kept for legacy reference)
  const { data: blotterData } = useBlotter();

  // Realized P&L derived from today's session fills (executed_orders), not IB account summary.
  // IB's reqPnL().realizedPnL can include non-trade events and diverges from fill-level data.
  const executedOrders = useMemo(() => orders?.executed_orders ?? [], [orders]);
  const todayRealizedPnl = useMemo(
    () => computeRealizedPnlFromFills(executedOrders),
    [executedOrders],
  );

  // Sync prices + portfolio into ticker-detail context (refs, no re-renders)
  const { setActiveTicker, setPrices: setTickerPrices, setFundamentals: setTickerFundamentals, setPortfolio: setTickerPortfolio, setOrders: setTickerOrders } = tickerDetail;
  useEffect(() => { setTickerPrices(prices); }, [prices, setTickerPrices]);
  useEffect(() => { setTickerFundamentals(fundamentals); }, [fundamentals, setTickerFundamentals]);
  useEffect(() => { setTickerPortfolio(portfolio); }, [portfolio, setTickerPortfolio]);
  useEffect(() => { setTickerOrders(orders); }, [orders, setTickerOrders]);

  // Sync tickerParam to context
  useEffect(() => {
    setActiveTicker(tickerParam ?? null);
  }, [tickerParam, setActiveTicker]);

  const prevIbConnectedRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevIbConnectedRef.current !== null && prevIbConnectedRef.current !== ibConnected) {
      if (ibConnected) {
        addToast("success", "IB Gateway reconnected", 4000);
      } else if (ibIssue === "ibc_mfa_required") {
        addToast(
          "warning",
          ibStatusMessage ?? "Interactive Brokers Gateway is reconnecting. Check the push notification from Interactive Brokers on your phone to approve MFA.",
          8000,
        );
      } else {
        addToast("error", "IB Gateway connection lost", 6000);
      }
    }
    prevIbConnectedRef.current = ibConnected;
  }, [ibConnected, ibIssue, ibStatusMessage, addToast]);
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

  useEffect(() => {
    const syncFullscreenState = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && document.fullscreenElement) {
        event.preventDefault();
        void document.exitFullscreen().catch(() => {});
      }
    };

    syncFullscreenState();
    document.addEventListener("fullscreenchange", syncFullscreenState);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreenState);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const resolvedTheme = theme ?? "dark";

  const actionTone = useMemo(() => {
    return resolvedTheme === "dark" ? "#e2e8f0" : "#0a0f14";
  }, [resolvedTheme]);

  const toggleTheme = () => {
    const next = resolvedTheme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  };

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Ignore denied fullscreen requests; the button stays in sync via fullscreenchange.
    }
  }, []);

  const syncLabel = lastSync
    ? `Last sync: ${new Date(lastSync).toLocaleTimeString()}`
    : error
      ? `Sync error`
      : "No sync yet";

  return (
    <div className="app-shell" suppressHydrationWarning>
      <Sidebar activeSection={activeSection} actionTone={actionTone} ibConnected={ibConnected} lastSync={lastSync} />

      <main className="main">
        <Header
          activeLabel={activeLabel}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
          onToggleTheme={toggleTheme}
          theme={resolvedTheme}
        >
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

        <ConnectionBanner
          ibConnected={ibConnected}
          wsConnected={wsConnected}
          ibIssue={ibIssue}
          ibStatusMessage={ibStatusMessage}
        />

        <div className="content">
          {activeSection === "dashboard" ? <ChatPanel activeSection={activeSection} /> : null}

          {activeSection !== "dashboard" && activeSection !== "ticker-detail" ? <MetricCards portfolio={portfolio} prices={prices} realizedPnl={todayRealizedPnl} executedOrders={executedOrders} section={activeSection} /> : null}

          {activeSection !== "dashboard" ? (
            <WorkspaceSections
              section={activeSection}
              portfolio={portfolio}
              portfolioLastSync={portfolioLastSync}
              orders={orders}
              prices={prices}
              tickerParam={tickerParam}
              theme={resolvedTheme}
            />
          ) : null}
        </div>
      </main>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  );
}
