"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import type { OrdersData, WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";
import { resolveSectionFromPath } from "@/lib/chat";
import { usePortfolio } from "@/lib/usePortfolio";
import { useOrders } from "@/lib/useOrders";
import { useMarketHours, MarketState } from "@/lib/useMarketHours";
import { useToast } from "@/lib/useToast";
import { useOrderActions } from "@/lib/OrderActionsContext";
import { usePrices } from "@/lib/usePrices";
import { hasUsableIndexPrice, mergeIndexFallbackPrices, useIndexQuoteFallback } from "@/lib/useIndexQuoteFallback";
import { useFuturesQuoteFallback } from "@/lib/useFuturesQuoteFallback";
import { computeRealizedPnlFromFills } from "@/lib/realized-pnl";
import { usePreviousClose } from "@/lib/usePreviousClose";
import { useGlobexOpen, HEADER_FUTURES } from "@/lib/futuresSession";
import FuturesStrip, { type FuturesQuote } from "@/components/FuturesStrip";
import { type OptionContract, type IndexContract, optionKey, portfolioLegToContract, uniqueOptionContracts } from "@/lib/pricesProtocol";
import { isIndexSymbol, indexExchangeFor } from "@/lib/indexSymbols";
import { useWatchlist } from "@/lib/useWatchlist";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import MetricCards from "@/components/MetricCards";
import ToastContainer from "@/components/Toast";
import DashboardSurface from "@/components/dashboard/DashboardSurface";
import ChatLauncher from "@/components/ChatLauncher";
import MobileShell from "@/components/mobile/MobileShell";
import { useViewport } from "@/lib/useViewport";

const WorkspaceSections = dynamic(() => import("@/components/WorkspaceSections"), {
  loading: () => null,
});
import FooterTelemetryStrip from "@/components/FooterTelemetryStrip";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { assessMargin, rankOf, type MarginLevel } from "@/lib/marginWarning";
import { useTheme } from "@/lib/ThemeContext";
import { useRealtimeAuth } from "@/lib/RealtimeAuthContext";

type WorkspaceShellProps = {
  section?: WorkspaceSection;
  tickerParam?: string;
};

export default function WorkspaceShell({ section, tickerParam }: WorkspaceShellProps) {
  const { theme: resolvedTheme, toggleTheme } = useTheme();
  const getRealtimeToken = useRealtimeAuth();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const pathname = usePathname();
  const { isMobile, hasMounted } = useViewport();
  const showMobileChrome = isMobile && hasMounted;
  const activeSection: WorkspaceSection = section ?? resolveSectionFromPath(pathname, "dashboard");
  const navLabel = navItems.find((item) => item.route === activeSection)?.label ?? "Dashboard";
  const activeLabel = activeSection === "ticker-detail" && tickerParam ? tickerParam : navLabel;
  const { toasts, addToast, removeToast } = useToast();
  const marketState = useMarketHours();
  const isMarketActive = marketState !== MarketState.CLOSED;
  // CME Globex session gate for the header ES/NQ/RTY futures strip — runs ~23h,
  // independent of the equities session above.
  const globexOpen = useGlobexOpen();

  const { data: portfolio, syncing: portfolioSyncing, error: portfolioError, lastSync: portfolioLastSync, syncNow: portfolioSyncNow } = usePortfolio(isMarketActive);

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
  // Fetch orders polling on orders page (always), and on other pages only during market hours.
  const shouldAutoSyncOrders = isOrdersPage || isMarketActive;
  // Fetch orders polling based on context (market hours for non-order views, always for orders)
  // initial fetch always happens on mount
  const { data: orders, syncing: ordersSyncing, error: ordersError, lastSync: ordersLastSync, syncNow: ordersSyncNow, updateData: updateOrdersData } = useOrders(shouldAutoSyncOrders);

  // Trigger a fresh IB sync every time the user navigates TO the orders page.
  // place/modify/cancel all refresh the DB order snapshot immediately, so
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
      // OPT: subscribe to the single option contract
      if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
        const right = c.right === "C" || c.right === "P"
          ? c.right
          : c.right === "CALL" ? "C" : c.right === "PUT" ? "P" : null;
        if (!right) continue;
        const expiryClean = c.expiry.replace(/-/g, "");
        if (expiryClean.length !== 8) continue;
        contracts.push({ symbol: c.symbol.toUpperCase(), expiry: expiryClean, strike: c.strike, right });
      }
      // BAG: subscribe to each combo leg's option contract
      if (c.secType === "BAG" && c.comboLegs) {
        for (const cl of c.comboLegs) {
          if (!cl.symbol || cl.strike == null || !cl.right || !cl.expiry) continue;
          const right = cl.right === "C" || cl.right === "P"
            ? cl.right
            : cl.right === "CALL" ? "C" : cl.right === "PUT" ? "P" : null;
          if (!right) continue;
          const expiryClean = cl.expiry.replace(/-/g, "");
          if (expiryClean.length !== 8) continue;
          contracts.push({ symbol: cl.symbol.toUpperCase(), expiry: expiryClean, strike: cl.strike, right });
        }
      }
    }
    return contracts;
  }, [orders]);

  const regimeStocks = useMemo(
    () => activeSection === "regime"
      ? ["SPY"]
      : [],
    [activeSection],
  );

  // Indices (VIX/SPX/NDX/…) must route through the `indexes` channel
  // not `symbols`: subscribing to "VIX" as a Stock returns no data
  // because IBKR exposes it via secType=IND. Splitting the tickerParam
  // here keeps the `/[ticker]` page working for both stocks and indices
  // without forking the page or shell.
  const tickerSymbols = useMemo(
    () => (tickerParam && !isIndexSymbol(tickerParam) ? [tickerParam] : []),
    [tickerParam],
  );

  const tickerIndexes = useMemo<IndexContract[]>(() => {
    if (!tickerParam) return [];
    const exchange = indexExchangeFor(tickerParam);
    return exchange ? [{ symbol: tickerParam.toUpperCase(), exchange }] : [];
  }, [tickerParam]);

  // Watchlist symbols stream live quotes too (the profile tracked-symbols tab). A
  // watched symbol that is NOT also a portfolio/order ticker (e.g. SPCX) was
  // never subscribed, so it rendered "---". Like the focused ticker, split index
  // symbols (VIX/SPX/…) onto the `indexes` channel — subscribing those as plain
  // `symbols` returns no data (IBKR exposes them via secType=IND).
  const { watchlist } = useWatchlist();
  const watchlistSymbols = useMemo(
    () => watchlist.map((e) => e.symbol).filter((s) => !isIndexSymbol(s)),
    [watchlist],
  );
  const watchlistIndexes = useMemo<IndexContract[]>(() => {
    const out: IndexContract[] = [];
    for (const entry of watchlist) {
      if (!isIndexSymbol(entry.symbol)) continue;
      const exchange = indexExchangeFor(entry.symbol);
      if (exchange) out.push({ symbol: entry.symbol.toUpperCase(), exchange });
    }
    return out;
  }, [watchlist]);

  const allSymbols = useMemo(
    () => {
      const base = [...portfolioSymbols, ...orderSymbols, ...regimeStocks, ...tickerSymbols, ...watchlistSymbols];
      // Subscribe ES/NQ/RTY front-month L1 only while Globex is open (the relay
      // resolves these roots to the active future; off-session there's nothing
      // to stream). The relay returns the equity ticker of the same name unless
      // it recognises these as futures roots — it does (DEPTH_FUTURES_SYMBOLS).
      if (globexOpen) base.push(...HEADER_FUTURES.map((f) => f.symbol));
      return [...new Set(base)];
    },
    [portfolioSymbols, orderSymbols, regimeStocks, tickerSymbols, watchlistSymbols, globexOpen],
  );

  const tickerDetail = useTickerDetail();

  const allContracts = useMemo(
    () => uniqueOptionContracts([...portfolioContracts, ...orderContracts, ...tickerDetail.chainContracts]),
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

  const allIndexes = useMemo<IndexContract[]>(() => {
    // De-dup by `symbol@exchange` so a regime-tab + /VIX-page combo
    // doesn't double-subscribe.
    const seen = new Set<string>();
    const out: IndexContract[] = [];
    for (const idx of [...regimeIndexes, ...tickerIndexes, ...watchlistIndexes]) {
      const key = `${idx.symbol}@${idx.exchange}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(idx);
      }
    }
    return out;
  }, [regimeIndexes, tickerIndexes, watchlistIndexes]);

  const {
    prices: rawPrices,
    fundamentals,
    depths,
    tape,
    connected: wsConnected,
    ibConnected: rawIbConnected,
    ibIssue,
    ibStatusMessage,
    error: priceError,
  } = usePrices({
    symbols: allSymbols,
    contracts: allContracts,
    indexes: allIndexes,
    // Single focused depth ticket for the open ticker-detail subject. The
    // detail view publishes the resolved book key (option key for single-leg
    // options, else the ticker); null releases the ticket. Never forces a
    // connection on its own — the subject already streams L1.
    depthSymbol: tickerDetail.depthSymbol,
    // For a futures-backed depth subject (VIX), the order-ticket selected
    // expiry decides which listed future the relay resolves under that key.
    // Null → relay falls back to front-month.
    depthExpiry: tickerDetail.depthFutureExpiry,
    getToken: getRealtimeToken,
  });

  const missingIndexFallbackSymbols = useMemo(
    () => allIndexes
      .map((idx) => idx.symbol)
      .filter((symbol) => !hasUsableIndexPrice(rawPrices[symbol])),
    [allIndexes, rawPrices],
  );
  const indexFallbackPrices = useIndexQuoteFallback(missingIndexFallbackSymbols);
  const rawPricesWithIndexFallback = useMemo(
    () => mergeIndexFallbackPrices(rawPrices, indexFallbackPrices),
    [rawPrices, indexFallbackPrices],
  );

  // Yahoo delayed-quote fallback for the ES/NQ/RTY header strip — fires when the
  // relay isn't streaming these roots (always on the demo, relay-down in prod).
  const missingFuturesFallbackSymbols = useMemo(
    () => HEADER_FUTURES
      .map((f) => f.symbol)
      .filter((symbol) => !hasUsableIndexPrice(rawPricesWithIndexFallback[symbol])),
    [rawPricesWithIndexFallback],
  );
  const futuresFallbackPrices = useFuturesQuoteFallback(missingFuturesFallbackSymbols);
  const rawPricesWithFallback = useMemo(
    () => mergeIndexFallbackPrices(rawPricesWithIndexFallback, futuresFallbackPrices),
    [rawPricesWithIndexFallback, futuresFallbackPrices],
  );

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
  const prices = usePreviousClose(rawPricesWithFallback);

  // Header index-futures strip: ES/NQ/RTY last + prior-close, gated on Globex.
  const futuresQuotes = useMemo<FuturesQuote[]>(() => {
    if (!globexOpen) return [];
    return HEADER_FUTURES.map((f) => {
      const p = prices[f.symbol];
      return { label: f.label, last: p?.last ?? null, close: p?.close ?? null };
    });
  }, [globexOpen, prices]);

  // Realized P&L derived from today's session fills (executed_orders), not IB account summary.
  // IB's reqPnL().realizedPnL can include non-trade events and diverges from fill-level data.
  const executedOrders = useMemo(() => orders?.executed_orders ?? [], [orders]);
  const todayRealizedPnl = useMemo(
    () => computeRealizedPnlFromFills(executedOrders),
    [executedOrders],
  );

  // Sync prices + portfolio into ticker-detail context (refs, no re-renders)
  const { setActiveTicker, setPrices: setTickerPrices, setFundamentals: setTickerFundamentals, setPortfolio: setTickerPortfolio, setOrders: setTickerOrders, setDepths: setTickerDepths, setTape: setTickerTape } = tickerDetail;
  useEffect(() => { setTickerPrices(prices); }, [prices, setTickerPrices]);
  useEffect(() => { setTickerFundamentals(fundamentals); }, [fundamentals, setTickerFundamentals]);
  useEffect(() => { setTickerPortfolio(portfolio); }, [portfolio, setTickerPortfolio]);
  useEffect(() => { setTickerOrders(orders); }, [orders, setTickerOrders]);
  useEffect(() => { setTickerDepths(depths); }, [depths, setTickerDepths]);
  useEffect(() => { setTickerTape(tape); }, [tape, setTickerTape]);

  // Sync tickerParam to context
  useEffect(() => {
    setActiveTicker(tickerParam ?? null);
  }, [tickerParam, setActiveTicker]);

  const prevIbConnectedRef = useRef<boolean | null>(null);
  useEffect(() => {
    // Demo deployment has no IB uplink by design — don't surface the alarming
    // "uplink lost" / "uplink restored" toasts that the missing relay triggers.
    if (process.env.NEXT_PUBLIC_RADON_DEMO === "1") return;
    if (prevIbConnectedRef.current !== null && prevIbConnectedRef.current !== ibConnected) {
      if (ibConnected) {
        addToast("success", "IB Gateway · uplink restored", 4000);
      } else if (ibIssue === "ibc_mfa_required") {
        addToast(
          "warning",
          ibStatusMessage ?? "IB Gateway · awaiting 2FA. Approve the IBKR Mobile push to restore the uplink.",
          8000,
        );
      } else {
        addToast("error", "IB Gateway · uplink lost. Reconnect in progress.", 6000);
      }
    }
    prevIbConnectedRef.current = ibConnected;
  }, [ibConnected, ibIssue, ibStatusMessage, addToast]);

  // Margin-warning persistent toast (Stage 1: threshold-derived).
  // Fires only on transition into a worse level. duration:0 = manual dismiss only.
  const prevMarginLevelRef = useRef<MarginLevel>("none");
  useEffect(() => {
    const { level, message } = assessMargin(portfolio?.account_summary);
    if (rankOf(level) > rankOf(prevMarginLevelRef.current)) {
      addToast(level === "critical" ? "error" : "warning", message, 0);
    }
    prevMarginLevelRef.current = level;
  }, [portfolio?.account_summary, addToast]);
  const syncing = isOrdersPage ? ordersSyncing : portfolioSyncing;
  const error = isOrdersPage ? ordersError : portfolioError;
  // Demo deployment is seed-data only (no IB gateway, no realtime relay), so the
  // connection-derived "Live data degraded" banner would always be on and read
  // as broken. Suppress it in demo; production (flag unset) is unchanged.
  const isDemoMode = process.env.NEXT_PUBLIC_RADON_DEMO === "1";
  const liveDataError = isDemoMode ? null : (portfolioError ?? ordersError ?? priceError);
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

  // Surface IB-disconnected state when the user attempts a ticker search.
  // Throttle so rapid typing doesn't spam toasts.
  const lastSearchUnavailableToastRef = useRef(0);
  const handleSearchUnavailable = useCallback(() => {
    const now = Date.now();
    if (now - lastSearchUnavailableToastRef.current < 30_000) return;
    lastSearchUnavailableToastRef.current = now;
    addToast(
      "warning",
      "IB Gateway uplink lost. Instrument search unavailable.",
      5000,
    );
  }, [addToast]);

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

  const actionTone = useMemo(() => {
    return resolvedTheme === "dark" ? "#e2e8f0" : "#0a0f14";
  }, [resolvedTheme]);

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
    ? `Last sample ${new Date(lastSync).toLocaleTimeString([], { hour12: false })}`
    : error
      ? "Sync failed. Reconstruction incomplete."
      : "Awaiting first sample";

  return (
    <div className="app-shell" suppressHydrationWarning>
      {showMobileChrome ? (
        <MobileShell title={activeLabel} ibConnected={ibConnected} lastSync={lastSync} />
      ) : null}
      <Sidebar activeSection={activeSection} actionTone={actionTone} ibConnected={ibConnected} lastSync={lastSync} />

      <main className="main">
        <Header
          activeLabel={activeLabel}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
          onToggleTheme={toggleTheme}
          theme={resolvedTheme}
          futuresStrip={futuresQuotes.length > 0 ? <FuturesStrip quotes={futuresQuotes} /> : null}
          onSearchUnavailable={handleSearchUnavailable}
          lastSync={lastSync}
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

        <div className="content">
          {liveDataError ? (
            <div className="live-data-degraded" role="alert" data-testid="live-data-degraded">
              <strong>Live data degraded</strong>
              <span>{liveDataError}</span>
            </div>
          ) : null}

          {activeSection === "dashboard" ? (
            <DashboardSurface
              portfolio={portfolio}
              orders={orders}
              realizedPnl={todayRealizedPnl}
            />
          ) : null}

          {activeSection !== "dashboard" && activeSection !== "ticker-detail" && activeSection !== "admin" && activeSection !== "profile" && activeSection !== "alerts" && activeSection !== "workflow" ? <MetricCards portfolio={portfolio} prices={prices} realizedPnl={todayRealizedPnl} executedOrders={executedOrders} section={activeSection} /> : null}

          {activeSection !== "dashboard" ? (
            <WorkspaceSections
              section={activeSection}
              portfolio={portfolio}
              portfolioLastSync={portfolioLastSync}
              orders={orders}
              prices={prices}
              tickerParam={tickerParam}
              theme={resolvedTheme}
              marketState={marketState}
            />
          ) : null}
        </div>

        <FooterTelemetryStrip />
      </main>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />
      <ChatLauncher activeSection={activeSection} />
    </div>
  );
}
