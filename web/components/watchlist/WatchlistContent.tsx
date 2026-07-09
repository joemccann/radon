"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FundamentalsData, PriceData } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { useViewport } from "@/lib/useViewport";
import { useWatchlist, type WatchlistEntry } from "@/lib/useWatchlist";
import { fmtPrice } from "@/lib/positionUtils";
import StarToggle from "@/components/StarToggle";
import TickerDetailContent from "@/components/TickerDetailContent";

type WatchlistContentProps = {
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  orders?: OrdersData | null;
  theme: "dark" | "light";
};

type WatchQuote = {
  last: number | null;
  abs: number | null;
  pct: number | null;
  tone: "positive" | "negative" | "neutral";
};

function quoteFor(price?: PriceData): WatchQuote {
  const last = price?.last ?? null;
  const close = price?.close ?? null;
  if (last == null || close == null || close === 0) {
    return { last, abs: null, pct: null, tone: "neutral" };
  }
  const abs = last - close;
  return {
    last,
    abs,
    pct: (abs / Math.abs(close)) * 100,
    tone: abs > 0 ? "positive" : abs < 0 ? "negative" : "neutral",
  };
}

function formatDateLabel(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "UNMAPPED";
  return parsed.toLocaleDateString(undefined, { month: "short", day: "2-digit" }).toUpperCase();
}

function findPosition(symbol: string, portfolio: PortfolioData | null | undefined): PortfolioPosition | null {
  return portfolio?.positions.find((position) => position.ticker === symbol) ?? null;
}

function openOrderCount(symbol: string, orders: OrdersData | null | undefined): number {
  return orders?.open_orders.filter((order) => order.contract.symbol === symbol).length ?? 0;
}

function WatchlistRow({
  entry,
  price,
  position,
  orderCount,
  active,
  onSelect,
  onRemove,
}: {
  entry: WatchlistEntry;
  price?: PriceData;
  position: PortfolioPosition | null;
  orderCount: number;
  active: boolean;
  onSelect: (symbol: string) => void;
  onRemove: (symbol: string) => void | Promise<void>;
}) {
  const quote = useMemo(() => quoteFor(price), [price]);
  const label = position?.structure ?? entry.sector ?? "UNASSIGNED";

  return (
    <li className={`watchlist-row${active ? " watchlist-row--active" : ""}`} data-testid={`watchlist-row-${entry.symbol}`}>
      <button
        type="button"
        className="watchlist-row__press"
        aria-pressed={active}
        aria-label={`Show ${entry.symbol} watchlist detail`}
        onClick={() => onSelect(entry.symbol)}
      >
        <span className="watchlist-row__primary">
          <span className="watchlist-row__symbol mono">{entry.symbol}</span>
          <span className="watchlist-row__label">{label}</span>
        </span>
        <span className="watchlist-row__quote">
          <span className="watchlist-row__last mono">
            {quote.last != null ? fmtPrice(quote.last) : "---"}
          </span>
          {quote.pct != null ? (
            <span className={`watchlist-row__change mono ${quote.tone}`}>
              {quote.abs != null && quote.abs >= 0 ? "+" : ""}
              {quote.pct.toFixed(2)}%
            </span>
          ) : (
            <span className="watchlist-row__change mono neutral">NO MARK</span>
          )}
        </span>
        <span className="watchlist-row__meta">
          <span>{position ? "HELD" : "FLAT"}</span>
          <span>{orderCount > 0 ? `${orderCount} ORDER${orderCount === 1 ? "" : "S"}` : "NO ORDERS"}</span>
          <span>{formatDateLabel(entry.added_at)}</span>
        </span>
      </button>
      <StarToggle active size="sm" onToggle={() => onRemove(entry.symbol)} />
    </li>
  );
}

export default function WatchlistContent({
  prices,
  portfolio,
  orders,
  theme,
}: WatchlistContentProps) {
  const {
    getPrices,
    getFundamentals,
    getDepths,
    getTape,
    setActiveTicker,
    setActivePositionId,
    setDepthSymbol,
  } = useTickerDetail();
  const { watchlist, isLoading, toggleWatch } = useWatchlist();
  const { isMobile, hasMounted } = useViewport();
  const detailRef = useRef<HTMLDivElement | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [activeDeck, setActiveDeck] = useState("book");

  const priceMap = prices ?? getPrices();
  const fundamentals = getFundamentals() as Record<string, FundamentalsData>;
  const depths = getDepths();
  const tape = getTape();

  useEffect(() => {
    if (watchlist.length === 0) {
      setSelectedSymbol(null);
      return;
    }
    setSelectedSymbol((current) => {
      const normalized = current?.toUpperCase() ?? null;
      if (normalized && watchlist.some((entry) => entry.symbol === normalized)) {
        return normalized;
      }
      return watchlist[0].symbol;
    });
  }, [watchlist]);

  useEffect(() => {
    setActiveTicker(selectedSymbol);
    setActivePositionId(null);
    if (!selectedSymbol) setDepthSymbol(null);
  }, [selectedSymbol, setActiveTicker, setActivePositionId, setDepthSymbol]);

  useEffect(() => {
    setActiveDeck("book");
  }, [selectedSymbol]);

  const selectedEntry = useMemo(
    () => watchlist.find((entry) => entry.symbol === selectedSymbol) ?? null,
    [selectedSymbol, watchlist],
  );

  const stats = useMemo(() => {
    let held = 0;
    let openOrders = 0;
    let marked = 0;
    for (const entry of watchlist) {
      if (findPosition(entry.symbol, portfolio)) held += 1;
      const orderTotal = openOrderCount(entry.symbol, orders);
      openOrders += orderTotal;
      if (priceMap[entry.symbol]?.last != null) marked += 1;
    }
    return { held, openOrders, marked };
  }, [orders, portfolio, priceMap, watchlist]);

  const handleSelect = useCallback((symbol: string) => {
    setSelectedSymbol(symbol.toUpperCase());
    if (hasMounted && isMobile) {
      window.requestAnimationFrame(() => {
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        detailRef.current?.scrollIntoView({ block: "start", behavior: reduceMotion ? "auto" : "smooth" });
      });
    }
  }, [hasMounted, isMobile]);

  const handleRemove = useCallback(
    async (symbol: string) => {
      try {
        await toggleWatch(symbol);
      } catch {
        // Optimistic rollback is owned by useWatchlist.
      }
    },
    [toggleWatch],
  );

  if (isLoading && watchlist.length === 0) {
    return (
      <section className="watchlist-shell watchlist-shell--empty" data-testid="watchlist-page">
        <div className="watchlist-empty">
          <span className="watchlist-eyebrow">TRACKED SYMBOLS</span>
          <h1>Watchlist</h1>
          <p>Loading watchlist...</p>
        </div>
      </section>
    );
  }

  if (watchlist.length === 0) {
    return (
      <section className="watchlist-shell watchlist-shell--empty" data-testid="watchlist-page">
        <div className="watchlist-empty">
          <span className="watchlist-eyebrow">TRACKED SYMBOLS</span>
          <h1>Watchlist</h1>
          <p>No symbols watched. Star an instrument from search or a ticker cockpit to seed this surface.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="watchlist-shell" data-testid="watchlist-page">
      <header className="watchlist-hero">
        <div className="watchlist-hero__copy">
          <span className="watchlist-eyebrow">TRACKED SYMBOLS</span>
          <h1>Watchlist</h1>
          <p>
            Click a symbol to keep the route fixed and load its live book, order ticket,
            held position, company context, and option chain in the inline cockpit.
          </p>
        </div>
        <div className="watchlist-stats" aria-label="Watchlist summary">
          <span><b className="mono">{watchlist.length}</b> SYMBOLS</span>
          <span><b className="mono">{stats.held}</b> HELD</span>
          <span><b className="mono">{stats.openOrders}</b> ORDERS</span>
          <span><b className="mono">{stats.marked}</b> MARKED</span>
        </div>
      </header>

      <div className="watchlist-grid">
        <aside className="watchlist-rail" aria-label="Watched symbols">
          <div className="watchlist-rail__head">
            <span>SYMBOL</span>
            <span>MARK</span>
          </div>
          <ul className="watchlist-list">
            {watchlist.map((entry) => (
              <WatchlistRow
                key={entry.id}
                entry={entry}
                price={priceMap[entry.symbol]}
                position={findPosition(entry.symbol, portfolio)}
                orderCount={openOrderCount(entry.symbol, orders)}
                active={entry.symbol === selectedSymbol}
                onSelect={handleSelect}
                onRemove={handleRemove}
              />
            ))}
          </ul>
        </aside>

        <div className="watchlist-detail" data-testid="watchlist-detail" ref={detailRef}>
          <div className="watchlist-detail__head">
            <span className="watchlist-detail__label">INLINE DETAIL</span>
            <span className="watchlist-detail__symbol mono">{selectedSymbol ?? "---"}</span>
          </div>
          {selectedEntry && selectedSymbol ? (
            <div className="watchlist-detail__frame">
              <div className="ticker-detail-page ticker-detail-page--embedded">
                <TickerDetailContent
                  ticker={selectedSymbol}
                  positionId={null}
                  activeTab={activeDeck}
                  onTabChange={setActiveDeck}
                  prices={priceMap}
                  fundamentals={fundamentals}
                  portfolio={portfolio ?? null}
                  orders={orders ?? null}
                  depths={depths}
                  tape={tape}
                  onDepthSymbolChange={setDepthSymbol}
                  theme={theme}
                />
              </div>
            </div>
          ) : (
            <div className="watchlist-detail__empty">Select a symbol to load instrument context.</div>
          )}
        </div>
      </div>
    </section>
  );
}
