"use client";

import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import type { PriceData } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";
import { useWatchlist, type WatchlistEntry } from "@/lib/useWatchlist";
import { fmtPrice } from "@/lib/positionUtils";
import StarToggle from "@/components/StarToggle";

type WatchlistContentProps = {
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  orders?: OrdersData | null;
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
  onOpen,
  onRemove,
}: {
  entry: WatchlistEntry;
  price?: PriceData;
  position: PortfolioPosition | null;
  orderCount: number;
  onOpen: (symbol: string) => void;
  onRemove: (symbol: string) => void | Promise<void>;
}) {
  const quote = useMemo(() => quoteFor(price), [price]);
  const label = position?.structure ?? entry.sector ?? "UNASSIGNED";

  return (
    <li className="watchlist-row" data-testid={`watchlist-row-${entry.symbol}`}>
      <button
        type="button"
        className="watchlist-row__press"
        aria-label={`Open ${entry.symbol} instrument cockpit`}
        onClick={() => onOpen(entry.symbol)}
      >
        <span className="watchlist-row__symbol mono">{entry.symbol}</span>
        <span className="watchlist-row__label">{label}</span>
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
        <span className={`watchlist-row__status mono${position ? " watchlist-row__status--held" : ""}`}>
          {position ? "HELD" : "FLAT"}
        </span>
        <span className={`watchlist-row__orders mono${orderCount > 0 ? " watchlist-row__orders--open" : ""}`}>
          {orderCount > 0 ? `${orderCount} ORDER${orderCount === 1 ? "" : "S"}` : "NO ORDERS"}
        </span>
        <span className="watchlist-row__added mono">{formatDateLabel(entry.added_at)}</span>
      </button>
      <StarToggle active size="sm" onToggle={() => onRemove(entry.symbol)} />
    </li>
  );
}

export default function WatchlistContent({
  prices,
  portfolio,
  orders,
}: WatchlistContentProps) {
  const router = useRouter();
  const { watchlist, isLoading, toggleWatch } = useWatchlist();
  const priceMap = prices ?? {};

  const stats = useMemo(() => {
    let held = 0;
    let openOrders = 0;
    let marked = 0;
    for (const entry of watchlist) {
      if (findPosition(entry.symbol, portfolio)) held += 1;
      openOrders += openOrderCount(entry.symbol, orders);
      if (priceMap[entry.symbol]?.last != null) marked += 1;
    }
    return { held, openOrders, marked };
  }, [orders, portfolio, priceMap, watchlist]);

  const handleOpen = useCallback(
    (symbol: string) => {
      router.push(`/${symbol.toUpperCase()}`);
    },
    [router],
  );

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
            Click a symbol to open its instrument cockpit: live book, order ticket,
            held position, company context, and option chain.
          </p>
        </div>
        <div className="watchlist-stats" aria-label="Watchlist summary">
          <span><b className="mono">{watchlist.length}</b> SYMBOLS</span>
          <span><b className="mono">{stats.held}</b> HELD</span>
          <span><b className="mono">{stats.openOrders}</b> ORDERS</span>
          <span><b className="mono">{stats.marked}</b> MARKED</span>
        </div>
      </header>

      <div className="watchlist-board" aria-label="Watched symbols">
        <div className="watchlist-board__head" aria-hidden="true">
          <span>Symbol</span>
          <span>Structure</span>
          <span className="watchlist-board__head-num">Mark</span>
          <span className="watchlist-board__head-num">Change</span>
          <span>Status</span>
          <span>Orders</span>
          <span>Added</span>
          <span />
        </div>
        <ul className="watchlist-list">
          {watchlist.map((entry) => (
            <WatchlistRow
              key={entry.id}
              entry={entry}
              price={priceMap[entry.symbol]}
              position={findPosition(entry.symbol, portfolio)}
              orderCount={openOrderCount(entry.symbol, orders)}
              onOpen={handleOpen}
              onRemove={handleRemove}
            />
          ))}
        </ul>
      </div>
    </section>
  );
}
