"use client";

import { useMemo, useState } from "react";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { DepthBook, PriceData, Trade } from "@/lib/pricesProtocol";
import type { OrderPrefill } from "@/lib/TickerDetailContext";
import { fmtPrice } from "@/lib/positionUtils";
import { useViewport } from "@/lib/useViewport";
import SingleLegOrderTicket, { type SingleLegOrderAction } from "@/components/SingleLegOrderTicket";
import { OrderRiskGate, type LinearOrderRiskInput } from "@/lib/order";
import { useOrderActionsOptional } from "@/lib/OrderActionsContext";
import { isIndexSymbol, hasFuturesSupport, hasIndexOptionsSupport } from "@/lib/indexSymbols";
import { FuturesOrderForm } from "@/components/ticker-detail/FuturesOrderForm";
import { IndexOptionOrderForm } from "@/components/ticker-detail/IndexOptionOrderForm";
import { OrderBook } from "@/components/ticker-detail/OrderBook";

/* ─── Types ─── */

type BookTabProps = {
  ticker: string;
  position: PortfolioPosition | null;
  prices: Record<string, PriceData>;
  openOrders: OpenOrder[];
  tickerPriceData: PriceData | null;
  /** Depth-of-book keyed by symbol (from `usePrices`). The focused book key
   *  resolves to the L2 panel; absent/unentitled falls back to `<L1OrderBook>`. */
  depths?: Record<string, DepthBook>;
  /** Time & Sales tape keyed by symbol (from `usePrices`, newest-first). Rides
   *  the same focused book key as `depths`. */
  tape?: Record<string, Trade[]>;
  /** Resolved key for the focused subject's depth book (option key for a
   *  single-leg option, else the ticker). */
  bookKey?: string;
  /** Resolved instrument kind for the depth panel; depth.kind wins when present. */
  bookKind?: "stock" | "option" | "future";
  /** Threaded to `StockOrderForm` so SELL stock against held shares
   *  short-circuits to a close-out branch, and SELL with held=0
   *  surfaces UNBOUNDED via the linear risk branch. */
  portfolio?: PortfolioData | null;
  /** Cockpit mode: render ONLY the depth montage + tape (the OrderBook), not the
   *  embedded position summary / order form / open-orders. In the cockpit those
   *  live in the always-docked Act column, so embedding them here would
   *  duplicate them. Legacy/mobile layout leaves this false (full book tab). */
  bookOnly?: boolean;
  /** Click-to-fill: a depth level / tape print was clicked. Forwarded to the
   *  OrderBook; the cockpit supplies a handler that publishes to the ticket. */
  onPriceClick?: (p: Omit<OrderPrefill, "nonce">) => void;
};

/* ─── Mobile Quote Row (Bid / Mid / Ask 3-col grid) ─── */

function MobileQuoteRow({
  bid,
  ask,
  last,
  lastLabel = "LAST",
  bidSize,
  askSize,
}: {
  bid: number | null;
  ask: number | null;
  last: number | null;
  lastLabel?: string;
  bidSize: number | null;
  askSize: number | null;
}) {
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;
  return (
    <div className="ckp-quote-row">
      <div className="ckp-quote-cell ckp-quote-cell--bid">
        <span className="ckp-quote-label">BID</span>
        <span className="ckp-quote-price positive">
          {bid != null ? fmtPrice(bid) : "---"}
        </span>
        <span className="ckp-quote-sub">{bidSize != null ? `${bidSize}` : ""}</span>
      </div>
      <div className="ckp-quote-cell ckp-quote-cell--mid ckp-quote-cell--active">
        <span className="ckp-quote-label">{lastLabel}</span>
        <span className="ckp-quote-price">
          {last != null ? fmtPrice(last) : mid != null ? fmtPrice(mid) : "---"}
        </span>
        <span className="ckp-quote-sub">MID {mid != null ? fmtPrice(mid) : "---"}</span>
      </div>
      <div className="ckp-quote-cell ckp-quote-cell--ask">
        <span className="ckp-quote-label">ASK</span>
        <span className="ckp-quote-price negative">
          {ask != null ? fmtPrice(ask) : "---"}
        </span>
        <span className="ckp-quote-sub">{askSize != null ? `${askSize}` : ""}</span>
      </div>
    </div>
  );
}

/* ─── L1 Order Book ─── */

function L1OrderBook({
  bid,
  ask,
  spread,
  last,
  lastLabel = "LAST",
  bidSize,
  askSize,
}: {
  bid: number | null;
  ask: number | null;
  spread: number | null;
  last: number | null;
  lastLabel?: string;
  bidSize: number | null;
  askSize: number | null;
}) {
  return (
    <div className="book-l1">
      <div className="book-section-header">ORDER BOOK</div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto 1fr",
          gap: "16px",
          alignItems: "center",
        }}
      >
        {/* Bid side */}
        <div className="book-l1-cell">
          <div className="book-l1-cell-label">BID</div>
          <div className="positive book-l1-value">
            {bid != null ? fmtPrice(bid) : "---"}
          </div>
          <div className="book-l1-cell-sub">
            {bidSize != null ? bidSize : "---"}
          </div>
        </div>

        {/* Spread */}
        <div className="book-l1-cell">
          <div className="book-l1-cell-label">SPREAD</div>
          <div className="book-l1-value-spread">
            {spread != null ? spread.toFixed(2) : "---"}
          </div>
          <div className="book-l1-cell-sub">
            {last != null ? `${lastLabel} ${fmtPrice(last)}` : ""}
          </div>
        </div>

        {/* Ask side */}
        <div className="book-l1-cell">
          <div className="book-l1-cell-label">ASK</div>
          <div className="negative book-l1-value">
            {ask != null ? fmtPrice(ask) : "---"}
          </div>
          <div className="book-l1-cell-sub">
            {askSize != null ? askSize : "---"}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Position Summary ─── */

function PositionSummary({ position }: { position: PortfolioPosition }) {
  return (
    <div style={{ marginTop: "16px" }}>
      <div className="book-section-header">POSITION</div>
      <div className="instrument-summary-grid">
        <div className="pos-stat">
          <span className="pos-stat-label">DIRECTION</span>
          <span className="pos-stat-value">
            {position.direction} {position.contracts}x
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">STRUCTURE</span>
          <span className="pos-stat-value">{position.structure}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">AVG COST</span>
          <span className="pos-stat-value">
            {position.entry_cost != null
              ? fmtPrice(
                  Math.abs(position.entry_cost) /
                    (position.contracts *
                      (position.structure_type === "Stock" ? 1 : 100))
                )
              : "---"}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">MKT VALUE</span>
          <span className="pos-stat-value">
            {position.market_value != null
              ? fmtPrice(Math.abs(position.market_value))
              : "---"}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ─── Open Orders List ─── */

function OpenOrdersList({ orders }: { orders: OpenOrder[] }) {
  return (
    <div style={{ marginTop: "16px" }}>
      <div className="book-section-header">OPEN ORDERS ({orders.length})</div>
      {orders.map((o, i) => {
        const c = o.contract;
        const desc =
          c.secType === "OPT"
            ? `${c.symbol} ${c.expiry ?? ""} $${c.strike ?? ""} ${c.right ?? ""}`
            : c.symbol;

        return (
          <div
            key={o.permId || o.orderId || i}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 0",
              borderBottom: "1px solid var(--line-grid, #1e293b)",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span
                className={`pill ${o.action === "BUY" ? "accum" : "distrib"}`}
                style={{ fontSize: "9px" }}
              >
                {o.action}
              </span>
              <span>{desc}</span>
              <span style={{ color: "var(--text-secondary)" }}>
                {o.totalQuantity}x
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <span>
                {o.limitPrice != null ? fmtPrice(o.limitPrice) : "MKT"}
              </span>
              <span style={{ color: "var(--text-secondary)", fontSize: "10px" }}>
                {o.tif} / {o.status}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Stock Order Form ─── */

function StockOrderForm({
  ticker,
  position,
  portfolio,
  bid,
  ask,
  mid,
}: {
  ticker: string;
  position: PortfolioPosition | null;
  portfolio: PortfolioData | null;
  bid: number | null;
  ask: number | null;
  mid: number | null;
}) {
  const orderActions = useOrderActionsOptional();
  const defaultAction: SingleLegOrderAction = position != null ? "SELL" : "BUY";
  const [action, setAction] = useState<SingleLegOrderAction>(defaultAction);
  const [quantity, setQuantity] = useState(() => {
    if (position && position.structure_type === "Stock")
      return String(position.contracts);
    return "";
  });
  const [limitPrice, setLimitPrice] = useState("");

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid =
    !isNaN(parsedQty) &&
    parsedQty > 0 &&
    !isNaN(parsedPrice) &&
    parsedPrice > 0;

  // Stock orders route through the linear branch of `<OrderRiskGate>`.
  // Held LONG / SHORT shares are looked up from the portfolio so a SELL
  // against held shares short-circuits to a close-out (with realised P&L)
  // and a SELL without held shares surfaces UNBOUNDED (short-sell of
  // borrowed shares — no price ceiling).
  const { heldLong, heldShort, avgCost } = useMemo(() => {
    if (!portfolio) return { heldLong: 0, heldShort: 0, avgCost: 0 };
    let long = 0;
    let short = 0;
    let basisDollars = 0;
    let totalSharesForBasis = 0;
    for (const pos of portfolio.positions ?? []) {
      if (pos.ticker !== ticker) continue;
      for (const leg of pos.legs ?? []) {
        if (leg.type !== "Stock") continue;
        const c = leg.contracts ?? 0;
        if (c <= 0) continue;
        const cost = Number.isFinite(leg.avg_cost) ? leg.avg_cost : 0;
        if (leg.direction === "LONG") {
          long += c;
          basisDollars += c * cost;
          totalSharesForBasis += c;
        } else if (leg.direction === "SHORT") {
          short += c;
        }
      }
    }
    return {
      heldLong: long,
      heldShort: short,
      avgCost: totalSharesForBasis > 0 ? basisDollars / totalSharesForBasis : 0,
    };
  }, [portfolio, ticker]);

  const riskInput: LinearOrderRiskInput | null = useMemo(() => {
    if (!isValid) return null;
    const description = `${action} ${parsedQty} ${ticker} @ ${fmtPrice(parsedPrice)}`;
    // Close-out branch: SELL against held LONG ≥ qty, OR BUY against held
    // SHORT ≥ qty. Provides basis so the summary reports realised P&L.
    const isClosingLong = action === "SELL" && heldLong >= parsedQty;
    const isClosingShort = action === "BUY" && heldShort >= parsedQty;
    if (isClosingLong || isClosingShort) {
      return {
        type: "linear",
        ticker,
        instrument: "stock",
        action,
        quantity: parsedQty,
        limitPrice: parsedPrice,
        multiplier: 1,
        heldQuantity: heldLong,
        heldShortQuantity: heldShort,
        description,
        closeOut: { entryCostDollars: parsedQty * avgCost },
      };
    }
    return {
      type: "linear",
      ticker,
      instrument: "stock",
      action,
      quantity: parsedQty,
      limitPrice: parsedPrice,
      multiplier: 1,
      heldQuantity: heldLong,
      heldShortQuantity: heldShort,
      description,
    };
  }, [isValid, parsedQty, parsedPrice, action, ticker, heldLong, heldShort, avgCost]);

  return (
    <SingleLegOrderTicket
      defaultAction={defaultAction}
      defaultTif="DAY"
      quantity={quantity}
      onQuantityChange={setQuantity}
      quantityPlaceholder="Shares"
      bid={bid}
      mid={mid}
      ask={ask}
      showQuickButtonPrices={false}
      isValid={isValid}
      limitPrice={limitPrice}
      onLimitPriceChange={setLimitPrice}
      onActionChange={setAction}
      style={{ marginTop: "16px" }}
      header={<div className="book-section-header">STOCK ORDER</div>}
      riskGate={
        /* Order Summary (shown in confirm step). Linear-branch
           chokepoint surfaces UNBOUNDED for naked short stock, close-out
           P&L for SELL-against-held-LONG / BUY-against-held-SHORT. */
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface="book-tab-stock"
          variant="info"
        />
      }
      buildPayload={({ action, quantity, limitPrice, tif }) => ({
        type: "stock",
        symbol: ticker,
        action,
        quantity,
        limitPrice,
        tif,
      })}
      buildSuccessMessage={({ action, quantity, limitPrice }) =>
        `Order placed: ${action} ${quantity} ${ticker} @ ${fmtPrice(limitPrice)}`
      }
      onSuccessToast={(message) => orderActions?.pushNotification({ type: "success", message })}
      suppressInlineSuccess
    />
  );
}

/* ─── Main BookTab ─── */

export default function BookTab({
  ticker,
  position,
  prices,
  openOrders,
  tickerPriceData,
  depths,
  tape,
  bookKey,
  bookKind,
  portfolio = null,
  bookOnly = false,
  onPriceClick,
}: BookTabProps) {
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  const priceData = tickerPriceData ?? prices[ticker] ?? null;
  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;
  const spread = bid != null && ask != null ? ask - bid : null;
  const last = priceData?.last ?? null;
  const lastLabel = priceData?.lastIsCalculated ? "MARK" : "LAST";
  const isIndex = isIndexSymbol(ticker);

  const resolvedBookKey = bookKey ?? ticker;
  const depth = depths?.[resolvedBookKey] ?? null;
  // depth.kind wins; else the kind resolved by the parent; else stock.
  const kind = depth?.kind ?? bookKind ?? "stock";
  // Time & Sales rides the same focused book key as depth. Newest-first from
  // the relay; empty until prints arrive (off-hours / unentitled), which the
  // TimeAndSales header-only empty state handles.
  const trades: Trade[] = tape?.[resolvedBookKey] ?? [];

  const l1Fallback = mobile ? (
    <MobileQuoteRow
      bid={bid}
      ask={ask}
      last={last}
      lastLabel={lastLabel}
      bidSize={priceData?.bidSize ?? null}
      askSize={priceData?.askSize ?? null}
    />
  ) : (
    <L1OrderBook
      bid={bid}
      ask={ask}
      spread={spread}
      last={last}
      lastLabel={lastLabel}
      bidSize={priceData?.bidSize ?? null}
      askSize={priceData?.askSize ?? null}
    />
  );

  const orderBook = (
    <OrderBook
      symbolLabel={ticker}
      kind={kind}
      depth={depth}
      trades={trades}
      last={last}
      lastLabel={lastLabel}
      bid={bid}
      ask={ask}
      l1Fallback={l1Fallback}
      onPriceClick={onPriceClick}
    />
  );

  // Cockpit: just the depth montage + tape, filling the Book region. The ticket,
  // position summary and open-orders are owned by the docked Act column.
  if (bookOnly) {
    return <div className="book-tab book-tab-only">{orderBook}</div>;
  }

  return (
    <div className="book-tab" style={{ padding: "16px 0" }}>
      {orderBook}

      {position && <PositionSummary position={position} />}

      {isIndex ? (
        <>
          {hasFuturesSupport(ticker) && <FuturesOrderForm ticker={ticker} />}
          {hasIndexOptionsSupport(ticker) && (
            <div style={{ marginTop: hasFuturesSupport(ticker) ? "24px" : "0" }}>
              <IndexOptionOrderForm ticker={ticker} />
            </div>
          )}
          {!hasFuturesSupport(ticker) && !hasIndexOptionsSupport(ticker) && (
            <IndexNotTradeableNotice ticker={ticker} />
          )}
        </>
      ) : (
        <StockOrderForm
          ticker={ticker}
          position={position}
          portfolio={portfolio}
          bid={bid}
          ask={ask}
          mid={mid}
        />
      )}

      {openOrders.length > 0 && <OpenOrdersList orders={openOrders} />}
    </div>
  );
}

/**
 * Inline notice replacing the stock order form for index tickers
 * (VIX/SPX/NDX/...). Indices are not directly tradeable on IBKR; only
 * futures and options on the index are. Phase 2 wires the futures
 * trading path; Phase 3 wires the options path.
 */
function IndexNotTradeableNotice({ ticker }: { ticker: string }) {
  return (
    <div
      className="index-notice"
      style={{
        marginTop: "24px",
        padding: "16px",
        border: "1px solid var(--line-grid)",
        borderRadius: "4px",
        background: "color-mix(in srgb, var(--bg-panel-raised) 60%, transparent)",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "10px",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "var(--signal-core)",
          marginBottom: "8px",
        }}
      >
        Index Instrument
      </div>
      <div
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: "13px",
          color: "var(--text-secondary)",
          lineHeight: 1.5,
        }}
      >
        {ticker} is an index, not a tradeable security. To take exposure, use {ticker} futures
        (CFE) or {ticker} options (CBOE). Futures and options trading paths land in Phase 2 / 3.
      </div>
    </div>
  );
}
