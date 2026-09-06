"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import type { OrdersData, PortfolioSnapshotSeed, WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";
import { resolveSectionFromPath } from "@/lib/chat";
import { usePortfolio } from "@/lib/usePortfolio";
import { useOrders } from "@/lib/useOrders";
import { useMarketHours, MarketState } from "@/lib/useMarketHours";
import { useAutoSyncOnStale } from "@/lib/useAutoSyncOnStale";
import { useSnapshotStaleness } from "@/lib/useSnapshotStaleness";
import { useToast } from "@/lib/useToast";
import { useOrderActions } from "@/lib/OrderActionsContext";
import { useRealtimePrices } from "@/lib/RealtimePricesContext";
import { hasUsableIndexPrice, mergeIndexFallbackPrices, useIndexQuoteFallback } from "@/lib/useIndexQuoteFallback";
import { useFuturesQuoteFallback } from "@/lib/useFuturesQuoteFallback";
import { computeRealizedPnlFromFills } from "@/lib/realized-pnl";
import { usePreviousClose } from "@/lib/usePreviousClose";
import { useGlobexOpen, isGlobexQuoteFresh, HEADER_FUTURES } from "@/lib/futuresSession";
import FuturesStrip, { type FuturesQuote } from "@/components/FuturesStrip";
import { type OptionContract, type IndexContract, optionKey, portfolioLegToContract, uniqueOptionContracts } from "@/lib/pricesProtocol";
import { isIndexSymbol, indexExchangeFor } from "@/lib/indexSymbols";
import { useWatchlist } from "@/lib/useWatchlist";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import clearResearch from "@/components/ClearResearch.module.css";
import MetricCards from "@/components/MetricCards";
import ToastContainer from "@/components/Toast";
import DashboardSurface from "@/components/dashboard/DashboardSurface";
import ChatLauncher from "@/components/ChatLauncher";
import DemoWelcomeModal from "@/components/DemoWelcomeModal";
import MobileShell from "@/components/mobile/MobileShell";
import { useViewport } from "@/lib/useViewport";

import InstrumentSkeleton from "@/components/ui/InstrumentSkeleton";

const WorkspaceSections = dynamic(() => import("@/components/WorkspaceSections"), {
  loading: () => <InstrumentSkeleton testId="workspace-sections-skeleton" />,
});
const PortfolioSections = dynamic(() => import("@/components/PortfolioSections"), {
  loading: () => <InstrumentSkeleton testId="portfolio-sections-skeleton" />,
});
const PerformancePanel = dynamic(() => import("@/components/PerformancePanel"), {
  loading: () => <InstrumentSkeleton testId="performance-panel-skeleton" />,
});
import FooterTelemetryStrip from "@/components/FooterTelemetryStrip";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { assessMargin, rankOf, type MarginLevel } from "@/lib/marginWarning";
import { useFillToasts } from "@/lib/useFillToasts";
import OfflineBanner from "@/components/OfflineBanner";
import { useOfflineStatus } from "@/lib/offline/OfflineStatusContext";
import { deriveLiveDataError } from "@/lib/offline/offlineStatus";
import { useTheme } from "@/lib/ThemeContext";
import CommandPalette from "@/components/CommandPalette";

type WorkspaceShellProps = {
  section?: WorkspaceSection;
  tickerParam?: string;
  initialPortfolio?: PortfolioSnapshotSeed;
};

export default function WorkspaceShell({ section, tickerParam, initialPortfolio }: WorkspaceShellProps) {
  const { theme: resolvedTheme, toggleTheme } = useTheme();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pathname = usePathname();
  const { isMobile, hasMounted } = useViewport();
  const showMobileChrome = isMobile && hasMounted;
  const activeSection: WorkspaceSection = section ?? resolveSectionFromPath(pathname, "dashboard");
  const isOrdersPage = activeSection === "orders";
  const isOptionsWorkspace = activeSection === "options";
  const navLabel = navItems.find((item) => item.route === activeSection)?.label ?? "Dashboard";
  const activeLabel = activeSection === "ticker-detail" && tickerParam ? tickerParam : navLabel;
  const headerOwnsPageHeading = activeSection !== "ticker-detail"
    && activeSection !== "watchlist"
    && activeSection !== "admin"
    && activeSection !== "workflow";
  const { toasts, exitingIds, addToast, upsertToast, dismissToast, hasToastKey } = useToast();
  const marketState = useMarketHours();
  const isMarketActive = marketState !== MarketState.CLOSED;
  // CME Globex session gate for the header ES/NQ/RTY futures strip — runs ~23h,
  // independent of the equities session above.
  const globexOpen = useGlobexOpen();

  const {
    data: portfolio,
    loading: portfolioLoading,
    syncing: portfolioSyncing,
    error: portfolioError,
    lastSync: portfolioLastSync,
    syncNow: portfolioSyncNow,
  } = usePortfolio(isMarketActive, {
    initialSnapshot: initialPortfolio,
    includeEntryDates: isOrdersPage,
  });

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

  // Poll cached orders on the orders page (always), and elsewhere during market hours.
  const shouldAutoSyncOrders = isOrdersPage || isMarketActive;
  // Live IB refresh remains an explicit operator action; initial cached read always runs.
  const {
    data: orders,
    loading: ordersLoading,
    syncing: ordersSyncing,
    error: ordersError,
    lastSync: ordersLastSync,
    syncNow: ordersSyncNow,
    updateData: updateOrdersData,
  } = useOrders(shouldAutoSyncOrders);

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
          // STRADDLE tab's LIVE cell: intraday SPX spot vs the latest close.
          { symbol: "SPX", exchange: "CBOE" },
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
    publishSubscriptions,
  } = useRealtimePrices();

  // The realtime socket is owned by RealtimePricesProvider in the root
  // Providers tree — it survives App Router navigations, which REMOUNT this
  // per-page shell. Each shell instance only publishes WHAT to stream; the
  // provider diffs the set over the already-open socket (no reconnect, no new
  // ws-ticket, no snapshot resync on a route change). Deliberately NO effect
  // cleanup: last write wins, so a page swap can never empty the set mid-swap.
  // Pin: web/tests/realtime-prices-navigation-persistence.test.tsx.
  useEffect(() => {
    publishSubscriptions({
      symbols: allSymbols,
      contracts: allContracts,
      indexes: allIndexes,
      // Single focused depth ticket for the open ticker-detail subject. The
      // detail view publishes the resolved book key (option key for single-leg
      // options, else the ticker); null releases the ticket. Never forces a
      // connection on its own — the subject already streams L1.
      depthSymbol: tickerDetail.depthSymbol,
      depthSymbols: tickerDetail.depthSymbols,
      // For a futures-backed depth subject (VIX), the order-ticket selected
      // expiry decides which listed future the relay resolves under that key.
      // Null → relay falls back to front-month.
      depthExpiry: tickerDetail.depthFutureExpiry,
    });
  }, [
    publishSubscriptions,
    allSymbols,
    allContracts,
    allIndexes,
    tickerDetail.depthSymbol,
    tickerDetail.depthSymbols,
    tickerDetail.depthFutureExpiry,
  ]);

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
      const fresh = isGlobexQuoteFresh(p?.timestamp);
      return {
        label: f.label,
        last: fresh ? p?.last ?? null : null,
        close: p?.close ?? null,
        delayed: missingFuturesFallbackSymbols.includes(f.symbol),
      };
    });
  }, [globexOpen, missingFuturesFallbackSymbols, prices]);

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

  // Demo deployment is seed-data only (no IB gateway, no realtime relay), so the
  // connection-derived "Live data degraded" banner would always be on and read
  // as broken. Suppress it in demo; production (flag unset) is unchanged.
  const isDemoMode = process.env.NEXT_PUBLIC_RADON_DEMO === "1";

  // Persistent per-execution fill toasts, diffed from the global orders poll.
  // A new fill also drives the portfolio producer: positions changed, and the
  // snapshot's own 60s timer would otherwise leave the table pre-fill under a
  // FILLED toast. Demo has no IB gateway, so it stays read-only.
  const onNewFills = useCallback(() => {
    if (isDemoMode) return;
    portfolioSyncNow();
  }, [isDemoMode, portfolioSyncNow]);
  useFillToasts(orders, upsertToast, onNewFills, hasToastKey);

  const syncing = isOrdersPage ? ordersSyncing : portfolioSyncing;
  const error = isOrdersPage ? ordersError : portfolioError;
  // Options measurements are backed by dedicated sources, not the IB
  // portfolio/order relay. Their panels report source-specific faults.
  // While the browser is offline the OfflineBanner is the single
  // explanation; the raw "Failed to fetch" degraded banner is suppressed.
  const { offline: browserOffline } = useOfflineStatus();
  const liveDataError = deriveLiveDataError({
    isDemoMode,
    isOptionsWorkspace,
    browserOffline,
    portfolioError,
    ordersError,
    priceError,
  });
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

  // Staleness is session-independent (after-hours/overnight fills exist)
  // and re-evaluated on its own clock, so an idle page cannot rot silently.
  const {
    isStale,
    state: snapshotState,
    staleAgeMinutes,
    tick: stalenessTick,
  } = useSnapshotStaleness(lastSync);

  // R-149: no snapshot at all is a BLACKOUT, and "Awaiting first sample" read
  // as a benign startup state for the rest of the session.
  const syncLabel = isDemoMode ? "Sample snapshot" : lastSync
    ? `Last sample ${new Date(lastSync).toLocaleTimeString([], { hour12: false })}`
    : error
      ? "Sync failed. Reconstruction incomplete."
      : "No sample yet. Reconstruction unavailable.";

  // A null timestamp during the initial cached read is not yet evidence of a
  // blackout. Let that GET paint first; a completed missing/stale snapshot
  // still arms recovery, while explicit syncs retain generation protection.
  const initialSnapshotSettled = isOrdersPage ? !ordersLoading : !portfolioLoading;
  useAutoSyncOnStale(
    isStale,
    syncNow,
    syncTarget,
    !isDemoMode && initialSnapshotSettled,
    stalenessTick,
  );

  // Sections that render live marks from the prices map. Scanner/discover (and
  // other non-price modules) must not receive a new `prices` identity on every
  // tick — memoized WorkspaceSections then skips re-render (skill-stack T11).
  const sectionNeedsPrices =
    activeSection === "portfolio"
    || activeSection === "orders"
    || activeSection === "regime"
    || activeSection === "profile"
    || activeSection === "watchlist"
    || activeSection === "ticker-detail";
  const pricesForSections = sectionNeedsPrices ? prices : undefined;

  return (
    <div className={`app-shell clear-workstation ${clearResearch.surfaces}`} data-workspace-section={activeSection} suppressHydrationWarning>
      <a href="#main-content" className="skip-link">Skip to content</a>
      {showMobileChrome ? (
        <MobileShell title={activeLabel} isPageHeading={headerOwnsPageHeading} ibConnected={ibConnected} lastSync={lastSync} />
      ) : null}

      <main id="main-content" className="main" tabIndex={-1}>
        <Header
          compact={activeSection === "dashboard"}
          navigation={<Sidebar activeSection={activeSection} actionTone={actionTone} ibConnected={ibConnected} lastSync={lastSync} />}
          activeLabel={activeLabel}
          isPageHeading={headerOwnsPageHeading}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
          onToggleTheme={toggleTheme}
          theme={resolvedTheme}
          futuresStrip={futuresQuotes.length > 0 ? <FuturesStrip quotes={futuresQuotes} /> : null}
          onSearchUnavailable={handleSearchUnavailable}
          lastSync={lastSync}
          onOpenPalette={() => setPaletteOpen(true)}
          isStale={isStale}
          staleAgeMinutes={staleAgeMinutes}
          onSyncNow={syncNow}
        >
          {!isOptionsWorkspace ? <div className="sync-controls">
            <span className={`sync-status ${!isDemoMode && (error || snapshotState === "unknown") ? "sync-error" : syncing ? "sync-active" : ""}`}>
              {syncLabel}
            </span>
            {!isDemoMode ? (
              <button
                type="button"
                className="sync-button"
                onClick={syncNow}
                disabled={syncing}
                title={`Sync ${syncTarget} from IB Gateway`}
              >
                <RefreshCw size={14} className={syncing ? "spin" : ""} />
                {syncing ? "Syncing…" : "Sync Now"}
              </button>
            ) : null}
          </div> : null}
        </Header>

        <div className="content">
          <OfflineBanner />
          {liveDataError ? (
            <div className="live-data-degraded" role="alert" data-testid="live-data-degraded">
              <strong>Live data degraded</strong>
              <span>{liveDataError}</span>
            </div>
          ) : null}

          {activeSection === "dashboard" ? (
            <DashboardSurface
              portfolio={portfolio}
              prices={prices}
              realizedPnl={todayRealizedPnl}
              marketState={marketState}
            />
          ) : null}

          {activeSection !== "dashboard" && activeSection !== "ticker-detail" && activeSection !== "watchlist" && activeSection !== "admin" && activeSection !== "preferences" && activeSection !== "profile" && activeSection !== "alerts" && activeSection !== "workflow" && !isOptionsWorkspace ? <div className={isStale ? "metric-cards--stale" : undefined}><MetricCards portfolio={portfolio} prices={prices} realizedPnl={todayRealizedPnl} executedOrders={executedOrders} section={activeSection} /></div> : null}

          {activeSection === "portfolio" ? (
            <PortfolioSections portfolio={portfolio} prices={pricesForSections} />
          ) : activeSection === "performance" ? (
            <PerformancePanel portfolioLastSync={portfolioLastSync} marketState={marketState} />
          ) : activeSection !== "dashboard" ? (
            <WorkspaceSections
              section={activeSection}
              portfolio={portfolio}
              portfolioLastSync={portfolioLastSync}
              orders={orders}
              prices={pricesForSections}
              depths={activeSection === "ticker-detail" ? depths : undefined}
              tape={activeSection === "ticker-detail" ? tape : undefined}
              tickerParam={tickerParam}
              theme={resolvedTheme}
              marketState={marketState}
            />
          ) : null}
        </div>

        <FooterTelemetryStrip />
      </main>

      <ToastContainer toasts={toasts} exitingIds={exitingIds} onDismiss={dismissToast} />
      <ChatLauncher activeSection={activeSection} portfolio={portfolio} prices={prices} />
      <DemoWelcomeModal />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        portfolioSymbols={portfolioSymbols}
      />
    </div>
  );
}
