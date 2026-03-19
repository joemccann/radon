"use client";

import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { useOrderActions } from "@/lib/OrderActionsContext";
import { fmtPrice, legPriceKey } from "@/lib/positionUtils";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import { checkNakedShortRisk, type NakedShortPortfolio, type OrderPayload } from "@/lib/nakedShortGuard";
import { OrderConfirmSummary, type OrderSummary } from "@/lib/order";

type OrderTabProps = {
  ticker: string;
  position: PortfolioPosition | null;
  portfolio?: PortfolioData | null;
  prices: Record<string, PriceData>;
  openOrders?: OpenOrder[];
  /** Resolved price data (option-level for single-leg options, underlying otherwise) */
  tickerPriceData?: PriceData | null;
};

/* ─── Convert PortfolioData to NakedShortPortfolio ─── */

function toNakedShortPortfolio(portfolio: PortfolioData | null | undefined): NakedShortPortfolio {
  if (!portfolio) return { positions: [] };
  return {
    positions: portfolio.positions.map((p) => ({
      ticker: p.ticker,
      structure_type: p.structure_type,
      contracts: p.contracts,
      direction: p.direction,
      legs: p.legs.map((l) => ({
        direction: l.direction,
        type: l.type,
        contracts: l.contracts,
        strike: l.strike,
      })),
    })),
  };
}

/* ─── Resolve price data for an order's contract ─── */

function resolveOrderPriceData(order: OpenOrder, prices: Record<string, PriceData>): PriceData | null {
  const c = order.contract;
  if (c.secType === "STK") return prices[c.symbol] ?? null;
  if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
    const expiryClean = c.expiry.replace(/-/g, "");
    if (expiryClean.length === 8) {
      const key = optionKey({
        symbol: c.symbol.toUpperCase(),
        expiry: expiryClean,
        strike: c.strike,
        right: c.right as "C" | "P",
      });
      return prices[key] ?? null;
    }
  }
  return null;
}

/* ─── Existing order row with modify/cancel ─── */

function ExistingOrderRow({
  order,
  prices,
  onModify,
}: {
  order: OpenOrder;
  prices: Record<string, PriceData>;
  onModify: (order: OpenOrder) => void;
}) {
  const { pendingCancels, pendingModifies, requestCancel } = useOrderActions();
  const [actionLoading, setActionLoading] = useState(false);

  const isPendingCancel = pendingCancels.has(order.permId);
  const isPendingModify = pendingModifies.has(order.permId);
  const isPending = isPendingCancel || isPendingModify;

  const priceData = resolveOrderPriceData(order, prices);
  const canModify = order.orderType === "LMT" || order.orderType === "STP LMT";

  const handleCancel = useCallback(async () => {
    setActionLoading(true);
    await requestCancel(order);
    setActionLoading(false);
  }, [order, requestCancel]);

  // Contract description
  const c = order.contract;
  const desc = c.secType === "OPT"
    ? `${c.symbol} ${c.expiry ?? ""} $${c.strike ?? ""} ${c.right ?? ""}`
    : c.symbol;

  return (
    <div className={`existing-order ${isPendingCancel ? "existing-order-cancelling" : isPendingModify ? "existing-order-modifying" : ""}`}>
      <div className="existing-order-header">
        <div className="existing-order-info">
          <span className={`pill ${order.action === "BUY" ? "accum" : "distrib"}`} style={{ fontSize: "9px" }}>
            {order.action}
          </span>
          <span className="existing-order-desc">{desc}</span>
          <span className="existing-order-qty">{order.totalQuantity}x</span>
        </div>
        <div className="existing-order-status">
          {isPending && <Loader2 size={12} className="cancel-spinner" />}
          <span className="existing-order-status-text">
            {isPendingCancel ? "Cancelling..." : isPendingModify ? "Modifying..." : order.status}
          </span>
        </div>
      </div>

      <div className="existing-order-details">
        <div className="existing-order-detail">
          <span className="pos-stat-label">TYPE</span>
          <span className="pos-stat-value">{order.orderType}</span>
        </div>
        <div className="existing-order-detail">
          <span className="pos-stat-label">LIMIT</span>
          <span className="pos-stat-value">{order.limitPrice != null ? fmtPrice(order.limitPrice) : "---"}</span>
        </div>
        <div className="existing-order-detail">
          <span className="pos-stat-label">TIF</span>
          <span className="pos-stat-value">{order.tif}</span>
        </div>
        <div className="existing-order-detail">
          <span className="pos-stat-label">LAST</span>
          <span className="pos-stat-value">{priceData?.last != null ? fmtPrice(priceData.last) : "---"}</span>
        </div>
      </div>

      {/* Action buttons */}
      {!isPending && (
        <div className="existing-order-actions">
          <button
            className="btn-order-action btn-modify"
            disabled={!canModify}
            title={canModify ? "Modify limit price" : "Only LMT orders can be modified"}
            onClick={() => onModify(order)}
          >
            MODIFY
          </button>
          <button
            className="btn-order-action btn-cancel"
            onClick={handleCancel}
            disabled={actionLoading}
          >
            {actionLoading ? "..." : "CANCEL"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ─── Order payload builder (exported for unit tests) ─── */

/**
 * Build the JSON body for POST /api/orders/place for a single-leg order.
 *
 * For stock positions (or no position), sends type="stock".
 * For single-leg option positions, sends type="option" with expiry/strike/right
 * derived from the position's leg data. Without this, IB receives secType=STK
 * and rejects an option limit price as too aggressive vs. the stock price.
 */
export function buildSingleLegOrderPayload(params: {
  ticker: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif: "DAY" | "GTC";
  position: PortfolioPosition | null;
}): Record<string, unknown> {
  const { ticker, action, quantity, limitPrice, tif, position } = params;

  // Detect single-leg option: non-stock, exactly one leg, has a strike
  const isSingleLegOption =
    position != null &&
    position.structure_type !== "Stock" &&
    position.legs.length === 1 &&
    position.legs[0].strike != null;

  if (isSingleLegOption && position != null) {
    const leg = position.legs[0];
    const right: "C" | "P" = leg.type === "Call" ? "C" : "P";
    // Normalize expiry to YYYYMMDD (strip dashes if present)
    const expiry = position.expiry.replace(/-/g, "");
    return {
      type: "option",
      symbol: ticker,
      action,
      quantity,
      limitPrice,
      tif,
      expiry,
      strike: leg.strike,
      right,
    };
  }

  return {
    type: "stock",
    symbol: ticker,
    action,
    quantity,
    limitPrice,
    tif,
  };
}

/* ─── New order form ─── */

type OrderAction = "BUY" | "SELL";

function NewOrderForm({
  ticker,
  position,
  portfolio,
  tickerPriceData,
  onOrderPlaced,
}: {
  ticker: string;
  position: PortfolioPosition | null;
  portfolio?: PortfolioData | null;
  tickerPriceData?: PriceData | null;
  onOrderPlaced?: () => void;
}) {
  const bid = tickerPriceData?.bid ?? null;
  const ask = tickerPriceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;

  const defaultAction: OrderAction = position != null ? "SELL" : "BUY";
  const [action, setAction] = useState<OrderAction>(defaultAction);
  const [quantity, setQuantity] = useState(() => {
    if (position && position.structure_type === "Stock") return String(position.contracts);
    return "";
  });
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && !isNaN(parsedPrice) && parsedPrice > 0;

  // Calculate order summary for confirmation (single leg: stock or single option)
  const orderSummary: OrderSummary | null = useMemo(() => {
    if (!isValid) return null;
    
    const isOption = position?.legs?.length === 1;
    const multiplier = isOption ? 100 : 1;
    const totalCost = parsedQty * parsedPrice * multiplier;
    const type = isOption ? position?.structure ?? "Option" : "Stock";
    const description = `${action} ${parsedQty}${isOption ? "x" : ""} ${ticker} ${type} @ ${fmtPrice(parsedPrice)}`;
    
    return {
      description,
      totalCost: action === "SELL" ? -totalCost : totalCost,
      // Single options: max loss = premium paid (for buys)
      ...(isOption && action === "BUY" ? { maxLoss: totalCost } : {}),
    };
  }, [isValid, parsedQty, parsedPrice, action, ticker, position]);

  // Naked short guard — reactive warning when action is SELL
  const nakedShortWarning = useMemo(() => {
    if (action !== "SELL") return null;
    const qty = !isNaN(parsedQty) && parsedQty > 0 ? parsedQty : 1;
    const payload = buildSingleLegOrderPayload({
      ticker,
      action: "SELL",
      quantity: qty,
      limitPrice: 1, // price doesn't matter for guard
      tif: "DAY",
      position,
    });
    const guardPortfolio = toNakedShortPortfolio(portfolio);
    const result = checkNakedShortRisk(payload as OrderPayload, guardPortfolio);
    return result.allowed ? null : result.reason ?? null;
  }, [action, parsedQty, ticker, position, portfolio]);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      setConfirmStep(true);
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const payload = buildSingleLegOrderPayload({
        ticker,
        action,
        quantity: parsedQty,
        limitPrice: parsedPrice,
        tif,
        position,
      });

      // Final naked short guard check before submission
      const guardPortfolio = toNakedShortPortfolio(portfolio);
      const guardResult = checkNakedShortRisk(payload as OrderPayload, guardPortfolio);
      if (!guardResult.allowed) {
        setError(guardResult.reason ?? "Order blocked: naked short exposure");
        setLoading(false);
        return;
      }

      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        setSuccess(`Order placed: ${action} ${parsedQty} ${ticker} @ ${fmtPrice(parsedPrice)}`);
        setConfirmStep(false);
        onOrderPlaced?.();
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, ticker, action, parsedQty, parsedPrice, tif, position, portfolio, onOrderPlaced]);

  return (
    <div className="order-form">
      <div className="order-field">
        <label className="order-label">Action</label>
        <div className="order-action-buttons">
          <button
            className={`order-action-btn ${action === "BUY" ? "order-action-active order-action-buy" : ""}`}
            onClick={() => { setAction("BUY"); setConfirmStep(false); }}
          >
            BUY
          </button>
          <button
            className={`order-action-btn ${action === "SELL" ? "order-action-active order-action-sell" : ""}`}
            onClick={() => { setAction("SELL"); setConfirmStep(false); }}
          >
            SELL
          </button>
        </div>
      </div>

      <div className="order-field">
        <label className="order-label">Quantity</label>
        <input
          className="order-input"
          type="number"
          min="1"
          step="1"
          value={quantity}
          onChange={(e) => { setQuantity(e.target.value); setConfirmStep(false); }}
          placeholder="Shares"
        />
      </div>

      <div className="order-field">
        <label className="order-label">Limit Price</label>
        <div className="modify-price-input-row">
          <span className="modify-price-prefix">$</span>
          <input
            className="modify-price-input"
            type="number"
            step="0.01"
            min="0.01"
            value={limitPrice}
            onChange={(e) => { setLimitPrice(e.target.value); setConfirmStep(false); }}
            placeholder="0.00"
          />
        </div>
        <div className="modify-quick-buttons">
          <button className="btn-quick" disabled={bid == null} onClick={() => { if (bid != null) { setLimitPrice(bid.toFixed(2)); setConfirmStep(false); } }}>BID</button>
          <button className="btn-quick" disabled={mid == null} onClick={() => { if (mid != null) { setLimitPrice(mid.toFixed(2)); setConfirmStep(false); } }}>MID</button>
          <button className="btn-quick" disabled={ask == null} onClick={() => { if (ask != null) { setLimitPrice(ask.toFixed(2)); setConfirmStep(false); } }}>ASK</button>
        </div>
      </div>

      <div className="order-field">
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`} onClick={() => setTif("DAY")}>DAY</button>
          <button className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`} onClick={() => setTif("GTC")}>GTC</button>
        </div>
      </div>

      {nakedShortWarning && (
        <div className="order-error" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={14} />
          <span>{nakedShortWarning}</span>
        </div>
      )}

      {error && <div className="order-error">{error}</div>}
      {success && <div className="order-success">{success}</div>}

      {/* Order Summary (shown in confirm step) */}
      {confirmStep && orderSummary && (
        <OrderConfirmSummary summary={orderSummary} variant="info" />
      )}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading || !!nakedShortWarning}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button className="btn-primary" onClick={handlePlace} disabled={!isValid || loading || !!nakedShortWarning} style={{ width: "100%" }}>
            Place Order
          </button>
        )}
      </div>
    </div>
  );
}

/* ─── Combo order form for multi-leg positions ─── */

function ComboOrderForm({
  ticker,
  position,
  portfolio,
  prices,
  onOrderPlaced,
}: {
  ticker: string;
  position: PortfolioPosition;
  portfolio?: PortfolioData | null;
  prices: Record<string, PriceData>;
  onOrderPlaced?: () => void;
}) {
  const defaultAction: OrderAction = "SELL";
  const [action, setAction] = useState<OrderAction>(defaultAction);
  const [quantity, setQuantity] = useState(() => String(position.contracts));
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("GTC");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Combo leg actions define the SPREAD STRUCTURE, not the trade direction.
  // IB reverses all leg actions when Order.action = SELL.
  // Always: LONG leg → BUY, SHORT leg → SELL (the spread definition).
  // Order.action (BUY/SELL) controls open vs close.
  const legsWithActions = useMemo(() => {
    return position.legs.map((leg) => {
      const legAction: "BUY" | "SELL" = leg.direction === "LONG" ? "BUY" : "SELL";
      const right = leg.type === "Call" ? "C" : "P";
      const expiryClean = position.expiry.replace(/-/g, "");
      return { ...leg, legAction, right: right as "C" | "P", expiry: expiryClean };
    });
  }, [position]);

  // Compute net BID / ASK / MID for the combo using natural market prices.
  // IB reverses leg actions when Order.action = SELL, so the EFFECTIVE
  // execution direction depends on the combo action:
  //   BUY combo: LONG leg → BUY (pay ask), SHORT leg → SELL (receive bid)
  //   SELL combo: LONG leg → SELL (receive bid), SHORT leg → BUY (pay ask)
  //
  // Natural market calculation:
  //   netBid = what we receive if we SELL at market (best case)
  //   netAsk = what we pay if we BUY at market (worst case)
  const netPrices = useMemo(() => {
    let netBid = 0;
    let netAsk = 0;
    let allAvailable = true;

    for (const leg of position.legs) {
      const key = legPriceKey(ticker, position.expiry, leg);
      if (!key) { allAvailable = false; break; }
      const lp = prices[key];
      if (!lp || lp.bid == null || lp.ask == null) { allAvailable = false; break; }

      // Effective execution after IB's reversal:
      const effectivelySelling = (action === "SELL") === (leg.direction === "LONG");
      
      if (effectivelySelling) {
        // We're selling this leg → receive BID
        netBid += lp.bid;
        netAsk += lp.ask;
      } else {
        // We're buying this leg → pay ASK
        netBid -= lp.ask;
        netAsk -= lp.bid;
      }
    }

    if (!allAvailable) return { bid: null, ask: null, mid: null };
    const absBid = Math.abs(netBid);
    const absAsk = Math.abs(netAsk);
    const bid = Math.min(absBid, absAsk);
    const ask = Math.max(absBid, absAsk);
    const mid = (bid + ask) / 2;
    return { bid, ask, mid };
  }, [position, prices, ticker, action]);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && !isNaN(parsedPrice) && parsedPrice > 0;

  // Naked short guard — reactive warning for combo orders
  const nakedShortWarning = useMemo(() => {
    if (action !== "SELL") return null;
    const qty = !isNaN(parsedQty) && parsedQty > 0 ? parsedQty : 1;
    const legs = legsWithActions.map((leg) => ({
      expiry: leg.expiry,
      strike: leg.strike!,
      right: leg.right,
      action: leg.legAction,
      ratio: 1,
    }));
    const payload: OrderPayload = {
      type: "combo",
      symbol: ticker,
      action: "SELL",
      quantity: qty,
      limitPrice: 1,
      legs,
    };
    const guardPortfolio = toNakedShortPortfolio(portfolio);
    const result = checkNakedShortRisk(payload, guardPortfolio);
    return result.allowed ? null : result.reason ?? null;
  }, [action, parsedQty, ticker, legsWithActions, portfolio]);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      setConfirmStep(true);
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const legs = legsWithActions.map((leg) => ({
        expiry: leg.expiry,
        strike: leg.strike!,
        right: leg.right,
        action: leg.legAction,
        ratio: 1,
      }));

      // Final naked short guard check before submission
      const guardPortfolio = toNakedShortPortfolio(portfolio);
      const comboPayload: OrderPayload = {
        type: "combo",
        symbol: ticker,
        action,
        quantity: parsedQty,
        limitPrice: parsedPrice,
        legs,
      };
      const guardResult = checkNakedShortRisk(comboPayload, guardPortfolio);
      if (!guardResult.allowed) {
        setError(guardResult.reason ?? "Order blocked: naked short exposure");
        setLoading(false);
        return;
      }

      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "combo",
          symbol: ticker,
          action,
          quantity: parsedQty,
          limitPrice: parsedPrice,
          tif,
          legs,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        setSuccess(`Combo order placed: ${action} ${parsedQty}x ${position.structure} @ ${fmtPrice(parsedPrice)}`);
        setConfirmStep(false);
        onOrderPlaced?.();
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, ticker, action, parsedQty, parsedPrice, tif, legsWithActions, position.structure, portfolio, onOrderPlaced]);

  // Calculate spread width for display
  const spreadWidth = netPrices.bid != null && netPrices.ask != null 
    ? (netPrices.ask - netPrices.bid).toFixed(2) 
    : null;
  const spreadPct = netPrices.mid != null && spreadWidth != null
    ? ((parseFloat(spreadWidth) / netPrices.mid) * 100).toFixed(1)
    : null;

  // Calculate order summary for confirmation
  const orderSummary: OrderSummary | null = useMemo(() => {
    if (!isValid) return null;
    
    const totalCost = parsedQty * parsedPrice * 100;
    const description = `${action} ${parsedQty}x ${position.structure} @ ${fmtPrice(parsedPrice)}`;
    
    // For vertical spreads, calculate max gain/loss
    // Bull Call Spread: LONG lower strike call, SHORT higher strike call
    // Bear Put Spread: LONG higher strike put, SHORT lower strike put
    const strikes = position.legs.map((l) => l.strike).filter((s): s is number => s != null);
    const hasSpread = strikes.length === 2 && position.legs.length === 2;
    
    if (hasSpread) {
      const width = Math.abs(strikes[0] - strikes[1]);
      const netDebit = parsedPrice;
      const maxWidth = width * parsedQty * 100;
      
      // For closing a position (SELL), the P&L is inverse
      if (action === "SELL") {
        // Selling means we receive premium — this closes the position
        return {
          description,
          totalCost: -totalCost, // Negative because we receive
          maxGain: totalCost, // Receive the credit
          maxLoss: null, // Position is closed
        };
      } else {
        // Buying to open
        return {
          description,
          totalCost,
          maxGain: maxWidth - totalCost,
          maxLoss: totalCost,
        };
      }
    }
    
    return {
      description,
      totalCost: action === "SELL" ? -totalCost : totalCost,
    };
  }, [isValid, parsedQty, parsedPrice, action, position]);

  return (
    <div className="order-form">
      {/* Spread price strip — always visible at top */}
      <div className="spread-price-strip">
        <div className="spread-price-item">
          <span className="spread-price-label">BID</span>
          <span className="spread-price-value spread-price-bid">
            {netPrices.bid != null ? `$${netPrices.bid.toFixed(2)}` : "---"}
          </span>
        </div>
        <div className="spread-price-item">
          <span className="spread-price-label">MID</span>
          <span className="spread-price-value">
            {netPrices.mid != null ? `$${netPrices.mid.toFixed(2)}` : "---"}
          </span>
        </div>
        <div className="spread-price-item">
          <span className="spread-price-label">ASK</span>
          <span className="spread-price-value spread-price-ask">
            {netPrices.ask != null ? `$${netPrices.ask.toFixed(2)}` : "---"}
          </span>
        </div>
        <div className="spread-price-item spread-price-width">
          <span className="spread-price-label">SPREAD</span>
          <span className="spread-price-value">
            {spreadWidth != null ? `$${spreadWidth}` : "---"}
            {spreadPct != null && <span className="spread-pct"> ({spreadPct}%)</span>}
          </span>
        </div>
      </div>

      {/* Leg summary (compact pills) */}
      <div className="order-field">
        <label className="order-label">Legs</label>
        <div className="combo-legs-pills">
          {legsWithActions.map((leg, i) => (
            <div key={i} className={`combo-leg-pill ${leg.direction === "LONG" ? "combo-leg-long" : "combo-leg-short"}`}>
              <span className="combo-leg-dir">{leg.direction === "LONG" ? "+" : "−"}</span>
              <span className="combo-leg-strike">${leg.strike} {leg.type}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Action toggle */}
      <div className="order-field">
        <label className="order-label">Action</label>
        <div className="order-action-buttons">
          <button
            className={`order-action-btn ${action === "BUY" ? "order-action-active order-action-buy" : ""}`}
            onClick={() => { setAction("BUY"); setConfirmStep(false); }}
          >
            BUY
          </button>
          <button
            className={`order-action-btn ${action === "SELL" ? "order-action-active order-action-sell" : ""}`}
            onClick={() => { setAction("SELL"); setConfirmStep(false); }}
          >
            SELL
          </button>
        </div>
      </div>

      {/* Quantity */}
      <div className="order-field">
        <label className="order-label">Quantity</label>
        <input
          className="order-input"
          type="number"
          min="1"
          step="1"
          value={quantity}
          onChange={(e) => { setQuantity(e.target.value); setConfirmStep(false); }}
          placeholder="Contracts"
        />
      </div>

      {/* Net Limit Price */}
      <div className="order-field">
        <label className="order-label">Net Limit Price</label>
        <div className="modify-price-input-row">
          <span className="modify-price-prefix">$</span>
          <input
            className="modify-price-input"
            type="number"
            step="0.01"
            min="0.01"
            value={limitPrice}
            onChange={(e) => { setLimitPrice(e.target.value); setConfirmStep(false); }}
            placeholder="0.00"
          />
        </div>
        <div className="modify-quick-buttons">
          <button className="btn-quick" disabled={netPrices.bid == null} onClick={() => { if (netPrices.bid != null) { setLimitPrice(netPrices.bid.toFixed(2)); setConfirmStep(false); } }}>
            BID{netPrices.bid != null ? ` ${netPrices.bid.toFixed(2)}` : ""}
          </button>
          <button className="btn-quick" disabled={netPrices.mid == null} onClick={() => { if (netPrices.mid != null) { setLimitPrice(netPrices.mid.toFixed(2)); setConfirmStep(false); } }}>
            MID{netPrices.mid != null ? ` ${netPrices.mid.toFixed(2)}` : ""}
          </button>
          <button className="btn-quick" disabled={netPrices.ask == null} onClick={() => { if (netPrices.ask != null) { setLimitPrice(netPrices.ask.toFixed(2)); setConfirmStep(false); } }}>
            ASK{netPrices.ask != null ? ` ${netPrices.ask.toFixed(2)}` : ""}
          </button>
        </div>
      </div>

      {/* TIF */}
      <div className="order-field">
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`} onClick={() => setTif("DAY")}>DAY</button>
          <button className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`} onClick={() => setTif("GTC")}>GTC</button>
        </div>
      </div>

      {nakedShortWarning && (
        <div className="order-error" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={14} />
          <span>{nakedShortWarning}</span>
        </div>
      )}

      {error && <div className="order-error">{error}</div>}
      {success && <div className="order-success">{success}</div>}

      {/* Order Summary (shown in confirm step) */}
      {confirmStep && orderSummary && (
        <OrderConfirmSummary summary={orderSummary} variant="info" />
      )}

      {/* Submit / Confirm */}
      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading || !!nakedShortWarning}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button className="btn-primary" onClick={handlePlace} disabled={!isValid || loading || !!nakedShortWarning} style={{ width: "100%" }}>
            Place Combo Order
          </button>
        )}
      </div>
    </div>
  );
}

/* ─── Main OrderTab ─── */

export default function OrderTab({ ticker, position, portfolio, prices, openOrders = [], tickerPriceData }: OrderTabProps) {
  const isCombo = position != null && position.legs.length > 1 && position.structure_type !== "Stock";

  const { requestModify } = useOrderActions();
  const [modifyTarget, setModifyTarget] = useState<OpenOrder | null>(null);
  const [modifyLoading, setModifyLoading] = useState(false);

  const handleModifyConfirm = useCallback(async (request: ModifyOrderRequest) => {
    if (!modifyTarget) return;
    setModifyLoading(true);
    await requestModify(modifyTarget, request);
    setModifyLoading(false);
    setModifyTarget(null);
  }, [modifyTarget, requestModify]);

  return (
    <>
      <ModifyOrderModal
        order={modifyTarget}
        loading={modifyLoading}
        prices={prices}
        portfolio={portfolio}
        onConfirm={handleModifyConfirm}
        onClose={() => setModifyTarget(null)}
      />

      <div className="order-tab">
        {/* NEW ORDER FORM FIRST — always visible above the fold */}
        {/* Combo order form for multi-leg positions */}
        {isCombo && (
          <div className="new-order-section-top">
            <div className="existing-orders-title">Close Position</div>
            <ComboOrderForm ticker={ticker} position={position!} portfolio={portfolio} prices={prices} />
          </div>
        )}

        {/* Stock / single-leg order form */}
        {!isCombo && (
          <div className="new-order-section-top">
            <div className="existing-orders-title">{position ? "Close Position" : "New Order"}</div>
            <NewOrderForm ticker={ticker} position={position} portfolio={portfolio} tickerPriceData={tickerPriceData} />
          </div>
        )}

        {/* Existing open orders for this ticker — below the form */}
        {openOrders.length > 0 && (
          <div className="existing-orders-section">
            <div className="existing-orders-title">Open Orders ({openOrders.length})</div>
            {openOrders.map((o) => (
              <ExistingOrderRow key={o.permId || o.orderId} order={o} prices={prices} onModify={setModifyTarget} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
