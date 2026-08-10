"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import type { PriceData } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";
import { useWatchlist, type WatchlistEntry } from "@/lib/useWatchlist";
import { fmtPrice } from "@/lib/positionUtils";
import { useSort, type SortDirection } from "@/lib/useSort";
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

type WatchlistSortKey = "symbol" | "structure" | "mark" | "change" | "status" | "orders" | "added";

type WatchlistRowData = {
  entry: WatchlistEntry;
  quote: WatchQuote;
  position: PortfolioPosition | null;
  orderCount: number;
  label: string;
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

function watchlistSortValue(row: WatchlistRowData, key: WatchlistSortKey): string | number | null {
  switch (key) {
    case "symbol":
      return row.entry.symbol;
    case "structure":
      return row.label;
    case "mark":
      return row.quote.last;
    case "change":
      return row.quote.pct;
    case "status":
      return row.position ? 1 : 0;
    case "orders":
      return row.orderCount;
    case "added": {
      const timestamp = Date.parse(row.entry.added_at);
      return Number.isNaN(timestamp) ? null : timestamp;
    }
  }
}

function WatchlistSortHeader({
  label,
  sortKey,
  activeKey,
  direction,
  onToggle,
  numeric = false,
}: {
  label: string;
  sortKey: WatchlistSortKey;
  activeKey: WatchlistSortKey | null;
  direction: SortDirection;
  onToggle: (key: WatchlistSortKey) => void;
  numeric?: boolean;
}) {
  const active = activeKey === sortKey;

  return (
    <span
      className={`sortable-th watchlist-sort-header${numeric ? " right" : ""}${active ? " sort-active" : ""}`}
      role="columnheader"
      aria-label={label}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : undefined}
    >
      <button type="button" className="watchlist-sort-button" aria-label={`Sort by ${label}`} onClick={() => onToggle(sortKey)}>
        <span className="sort-label" aria-hidden="true">
          <span>{label}</span>
          <span className="sort-icon">
            {active ? (
              direction === "asc" ? <ChevronUp size={10} /> : <ChevronDown size={10} />
            ) : (
              <ChevronDown size={10} className="sort-icon-idle" />
            )}
          </span>
        </span>
      </button>
    </span>
  );
}

function WatchlistRow({
  row,
  onOpen,
  onRemove,
}: {
  row: WatchlistRowData;
  onOpen: (symbol: string) => void;
  onRemove: (symbol: string) => void | Promise<void>;
}) {
  const { entry, quote, position, orderCount, label } = row;

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

  const rows = useMemo<WatchlistRowData[]>(
    () => watchlist.map((entry) => {
      const position = findPosition(entry.symbol, portfolio);
      return {
        entry,
        quote: quoteFor(prices?.[entry.symbol]),
        position,
        orderCount: openOrderCount(entry.symbol, orders),
        label: position?.structure ?? entry.sector ?? "UNASSIGNED",
      };
    }),
    [orders, portfolio, prices, watchlist],
  );
  const { sorted, sort, toggle } = useSort<WatchlistRowData, WatchlistSortKey>(rows, watchlistSortValue);

  const stats = useMemo(() => {
    let held = 0;
    let openOrders = 0;
    let marked = 0;
    for (const row of rows) {
      if (row.position) held += 1;
      openOrders += row.orderCount;
      if (row.quote.last != null) marked += 1;
    }
    return { held, openOrders, marked };
  }, [rows]);

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
        <div className="watchlist-board__head" role="row">
          <WatchlistSortHeader label="Symbol" sortKey="symbol" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <WatchlistSortHeader label="Structure" sortKey="structure" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <WatchlistSortHeader label="Mark" sortKey="mark" activeKey={sort.key} direction={sort.direction} onToggle={toggle} numeric />
          <WatchlistSortHeader label="Change" sortKey="change" activeKey={sort.key} direction={sort.direction} onToggle={toggle} numeric />
          <WatchlistSortHeader label="Status" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <WatchlistSortHeader label="Orders" sortKey="orders" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <WatchlistSortHeader label="Added" sortKey="added" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <span />
        </div>
        <ul className="watchlist-list">
          {sorted.map((row) => (
            <WatchlistRow
              key={row.entry.id}
              row={row}
              onOpen={handleOpen}
              onRemove={handleRemove}
            />
          ))}
        </ul>
      </div>
    </section>
  );
}
