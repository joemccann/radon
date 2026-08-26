"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { useOrderActions, useOrderActionsOptional } from "@/lib/OrderActionsContext";
import { fmtPrice, legPriceKey, resolveEntryCost, resolveNaturalSpreadQuote } from "@/lib/positionUtils";
import { computeLegImpliedValue } from "@/lib/impliedValue";
import { useRiskFreeRate } from "@/lib/useRiskFreeRate";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import { buildQuoteTelemetryModel, comboQuotePriceData } from "@/lib/quoteTelemetry";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import { checkNakedShortRisk, type NakedShortPortfolio, type OrderPayload } from "@/lib/nakedShortGuard";
import { OrderRiskGate, type OrderRiskInput, useOrderRisk } from "@/lib/order";
import { heldComboUnits, isPureComboClose } from "@/lib/order/positionTrade";
import { placeOrderFeedback } from "@/lib/orders/placeOrderFeedback";
import {
  type IbOrderType,
  ibPlaceFields,
  isStopOrderType,
  pricesValidForOrderType,
  riskPriceForOrderType,
} from "@/lib/order/stopOrder";
import OrderTypeToggle from "@/components/OrderTypeToggle";
import { fmtSignedPrice, toneClass } from "@/lib/format";
import { isIndexSymbol, hasFuturesSupport, hasIndexOptionsSupport } from "@/lib/indexSymbols";
import { FuturesOrderForm } from "@/components/ticker-detail/FuturesOrderForm";
import { IndexOptionOrderForm } from "@/components/ticker-detail/IndexOptionOrderForm";

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
      expiry: p.expiry,
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

function comboQuoteClass(value: number | null, label: "bid" | "mid" | "ask"): string {
  if (value == null) return "";
  if (value < 0) return "negative";
  if (label === "bid") return "spread-price-bid";
  if (label === "ask") return "spread-price-ask";
  return "";
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
  const riskFreeRate = useRiskFreeRate();

  // Black-Scholes implied per-share value at current spot. Single OPT only;
  // STK and BAG are skipped (BAG implied is shown in the consolidated combo row).
  const impliedPrice = useMemo(() => {
    const c = order.contract;
    if (c.secType !== "OPT" || c.strike == null || !c.right || !c.expiry) return null;
    const type: "Call" | "Put" | null =
      c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : null;
    if (!type) return null;
    return computeLegImpliedValue(
      {
        ticker: c.symbol,
        expiry: c.expiry,
        strike: c.strike,
        type,
        direction: order.action === "BUY" ? "LONG" : "SHORT",
        contracts: Math.abs(order.totalQuantity),
      },
      prices,
      { riskFreeRate },
    ).perContract;
  }, [order, prices, riskFreeRate]);

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
        <div className="existing-order-detail" title="Black-Scholes implied value at current spot">
          <span className="pos-stat-label">IMPLIED</span>
          <span className="pos-stat-value">{impliedPrice != null ? fmtPrice(impliedPrice) : "---"}</span>
        </div>
      </div>

      {/* Action buttons */}
      {!isPending && (
        <div className="existing-order-actions">
          <button
            className="btn-order-action btn-modify"
            disabled={!canModify}
            title={canModify ? "Modify limit price" : "Only LMT and STP LMT orders can be modified"}
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
  orderType?: IbOrderType;
  stopPrice?: number;
}): Record<string, unknown> {
  const { ticker, action, quantity, limitPrice, tif, position, orderType = "LMT", stopPrice } = params;
  const priceFields = ibPlaceFields(orderType, limitPrice, stopPrice ?? NaN);

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
      tif,
      expiry,
      strike: leg.strike,
      right,
      ...priceFields,
    };
  }

  return {
    type: "stock",
    symbol: ticker,
    action,
    quantity,
    tif,
    ...priceFields,
  };
}

/* ─── New order form ─── */

type OrderAction = "BUY" | "SELL";

export function orderTabSubmitPermitted(
  isValid: boolean,
  loading: boolean,
  warning: string | null,
  riskState: { okToSubmit: boolean } | null,
): boolean {
  return isValid && !loading && warning == null && riskState?.okToSubmit === true;
}

export function defaultSingleOrderAction(position: PortfolioPosition | null): OrderAction {
  if (position?.legs.length === 1 && position.legs[0].direction === "SHORT") return "BUY";
  return position == null ? "BUY" : "SELL";
}

export function comboLegRatio(legContracts: number, comboUnits: number): number {
  return Math.max(1, Math.trunc(Math.abs(legContracts)) / Math.max(1, comboUnits));
}

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

  const defaultAction = defaultSingleOrderAction(position);
  const [action, setAction] = useState<OrderAction>(defaultAction);
  const [quantity, setQuantity] = useState(() => {
    if (position && position.structure_type === "Stock") return String(position.contracts);
    return "";
  });
  const [limitPrice, setLimitPrice] = useState("");
  const [orderType, setOrderType] = useState<IbOrderType>("LMT");
  const [stopPrice, setStopPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const orderActions = useOrderActionsOptional();

  // Click-to-fill: when a depth level / tape print is clicked in the book, the
  // cockpit publishes {price, action?, quantity?} to TickerDetailContext. We
  // apply it here — keyed ONLY on the nonce so it fires exactly once per click
  // and never on unrelated re-renders (price ticks, typing). It cannot clobber
  // a half-typed price unless the user deliberately clicks the book. Action is
  // applied only when the click was unambiguous; quantity only into an empty
  // field. Optional context → no-op outside the ticker-detail tree.
  const tickerDetail = useTickerDetailOptional();
  const prefillNonce = tickerDetail?.orderPrefill?.nonce;
  useEffect(() => {
    const p = tickerDetail?.orderPrefill;
    if (!p) return;
    setLimitPrice(p.price.toFixed(2));
    if (p.action) setAction(p.action);
    if (p.quantity != null && quantity.trim() === "") setQuantity(String(p.quantity));
    setConfirmStep(false);
    // Intentionally keyed on the nonce alone (see comment above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const parsedStop = parseFloat(stopPrice);
  const isValid =
    !isNaN(parsedQty)
    && parsedQty > 0
    && pricesValidForOrderType({ orderType, limitPrice: parsedPrice, stopPrice: parsedStop });

  // Build the chokepoint input. All single-leg risk math + close-out
  // detection now lives in `useOrderRisk` (via `<OrderRiskGate>` below).
  // The previous in-line `computeOrderRisk` + close-out short-circuit was
  // ~80 lines of business logic re-invented per surface; the gate owns it.
  //
  // Per-contract avg_cost note (preserved from the previous implementation
  // for the close-out branch): `onlyLeg.avg_cost` is per-contract for
  // options (IB's `pos.avgCost` is already multiplied by the contract
  // multiplier for OPT secType) and per-share for stocks. We compute
  // `entryCostDollars` here in that same unit so the gate's close-out
  // path doesn't have to re-derive multipliers.
  //   Before fix (commit d420c16): cost basis was multiplied by 100 twice,
  //   producing PnL = −$635,055 on a $19,389.45 USAX winner.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValid) return null;
    const isOption =
      position?.legs?.length === 1 &&
      position.legs[0].strike != null &&
      (position.legs[0].type === "Call" || position.legs[0].type === "Put");
    const multiplier = isOption ? 100 : 1;
    const riskPrice = riskPriceForOrderType(orderType, parsedPrice, parsedStop);
    const totalCost = parsedQty * riskPrice * multiplier;
    const typeLabel = isOption ? position?.structure ?? "Option" : "Stock";
    const description = `${action} ${parsedQty}${isOption ? "x" : ""} ${ticker} ${typeLabel} @ ${fmtPrice(riskPrice)}`;

    // Stock (linear) path. A close against the held stock must surface realised
    // P&L, mirroring the option close-out branches below — previously every
    // stock order fell through as an OPEN ("Total:" with no P&L), so a
    // buy-to-close-short (cover) showed no P&L at all.
    if (!isOption) {
      const stockLeg = position?.structure_type === "Stock" ? position.legs[0] : null;
      const heldLong = stockLeg?.direction === "LONG" ? Math.abs(stockLeg.contracts) : 0;
      const heldShort = stockLeg?.direction === "SHORT" ? Math.abs(stockLeg.contracts) : 0;
      const basis = parsedQty * Math.abs(stockLeg?.avg_cost ?? 0);
      return {
        type: "linear",
        ticker,
        instrument: "stock",
        action,
        quantity: parsedQty,
        limitPrice: riskPrice,
        multiplier: 1,
        heldQuantity: heldLong,
        heldShortQuantity: heldShort,
        description,
        closeOut:
          action === "SELL" && heldLong >= parsedQty
            ? { entryCostDollars: basis }
            : action === "BUY" && heldShort >= parsedQty
              ? { entryCostDollars: basis }
              : undefined,
      };
    }
    if (position == null) {
      return {
        ticker,
        chainLegs: [],
        netPremium: action === "SELL" ? -riskPrice : riskPrice,
        description,
        totalCost: action === "SELL" ? -totalCost : totalCost,
        underlyingSpot: tickerPriceData?.last ?? null,
      };
    }

    const onlyLeg = position.legs[0];
    const right: "C" | "P" = onlyLeg.type === "Call" ? "C" : "P";
    const legAction: "BUY" | "SELL" = action;
    const isClosingLong =
      legAction === "SELL" &&
      onlyLeg.direction === "LONG" &&
      onlyLeg.contracts >= parsedQty;
    const isClosingShort =
      action === "BUY" &&
      onlyLeg.direction === "SHORT" &&
      onlyLeg.contracts >= parsedQty;

    if (isClosingLong) {
      // Pure close (or partial close) of a held LONG. The gate's `closeOut`
      // branch surfaces proceeds + realized P&L. avg_cost is per-contract
      // for options, per-share for stocks (see comment above).
      const proceeds = parsedQty * riskPrice * multiplier;
      const entryCostDollars = parsedQty * onlyLeg.avg_cost;
      return {
        ticker,
        chainLegs: [{
          action: "SELL",
          right,
          strike: onlyLeg.strike as number,
          expiry: onlyLeg.expiry ?? position.expiry,
          quantity: parsedQty,
        }],
        netPremium: -riskPrice,
        description,
        totalCost: proceeds,
        totalLabel: "Proceeds:",
        closeOut: { entryCostDollars },
      };
    }

    if (isClosingShort) {
      // Buy-to-close (or partial close) of a held SHORT. Mirrors the
      // `closingShort` branch in lib/order/positionTrade.ts so the gate's
      // `pnl = proceeds - entryCostDollars` resolves to credit − debit:
      //   proceeds = −debit (we PAY to buy back), basis = −credit (the
      //   original entry credit), so pnl = (−debit) − (−credit) = credit − debit.
      // avg_cost is per-contract for options, per-share for stocks; use it
      // directly (NEVER × multiplier — the d420c16 double-count bug).
      const closeDebit = parsedQty * riskPrice * multiplier;
      const basisMagnitude = parsedQty * Math.abs(onlyLeg.avg_cost);
      return {
        ticker,
        chainLegs: [{
          action: "BUY",
          right,
          strike: onlyLeg.strike as number,
          expiry: onlyLeg.expiry ?? position.expiry,
          quantity: parsedQty,
        }],
        netPremium: riskPrice,
        description,
        totalCost: -closeDebit,
        totalLabel: "Close Debit:",
        closeOut: { entryCostDollars: -basisMagnitude },
      };
    }

    // Open-fresh path: chain leg carries the order; the augmentation pipe
    // looks at the held position (it's in `portfolio`) to attach any
    // cross-strike coverage automatically — same logic that powers the
    // chain builder.
    return {
      ticker,
      chainLegs: [
        {
          action: legAction,
          right,
          strike: onlyLeg.strike as number,
          expiry: position.expiry,
          quantity: parsedQty,
        },
      ],
      netPremium: legAction === "SELL" ? -riskPrice : riskPrice,
      description,
      totalCost: action === "SELL" ? -totalCost : totalCost,
      // FU7: thread the single-leg live quote so net-of-cost renders. Off-hours
      // null bid/ask falls back to the F1 estimated half-spread inside the hook.
      quote: { bid, ask },
      // Phase-1 margin: underlying spot for the naked-short Reg-T estimate.
      underlyingSpot: tickerPriceData?.last ?? null,
    };
  }, [isValid, parsedQty, parsedPrice, parsedStop, orderType, action, ticker, position, bid, ask, tickerPriceData?.last]);
  const riskState = useOrderRisk(riskInput, portfolio);

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
  const submitPermitted = orderTabSubmitPermitted(isValid, loading, nakedShortWarning, riskState);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      if (!submitPermitted) return;
      setConfirmStep(true);
      return;
    }
    if (!submitPermitted) return;

    setLoading(true);
    setError(null);

    try {
      const payload = buildSingleLegOrderPayload({
        ticker,
        action,
        quantity: parsedQty,
        limitPrice: parsedPrice,
        tif,
        position,
        orderType,
        stopPrice: parsedStop,
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
        const feedback = placeOrderFeedback(json, `Order placed: ${action} ${parsedQty} ${ticker} @ ${fmtPrice(parsedPrice)}`);
        orderActions?.pushNotification({ type: feedback.tone, message: feedback.message });
        setConfirmStep(false);
        onOrderPlaced?.();
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, ticker, action, parsedQty, parsedPrice, parsedStop, orderType, tif, position, portfolio, onOrderPlaced, orderActions, submitPermitted]);

  return (
    <div className="order-form">
      <OrderQuoteTelemetry priceData={tickerPriceData ?? null} label={ticker} />

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

      <OrderTypeToggle
        value={orderType}
        onChange={(next) => {
          setOrderType(next);
          if (isStopOrderType(next)) setTif("GTC");
          setConfirmStep(false);
        }}
      />

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

      {isStopOrderType(orderType) && (
        <div className="order-field">
          <label className="order-label">Stop Price</label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              className="modify-price-input"
              type="number"
              step="0.01"
              min="0.01"
              value={stopPrice}
              onChange={(e) => { setStopPrice(e.target.value); setConfirmStep(false); }}
              placeholder="0.00"
              data-testid="order-stop-price"
            />
          </div>
          <div className="modify-quick-buttons">
            <button className="btn-quick" disabled={bid == null} onClick={() => { if (bid != null) { setStopPrice(bid.toFixed(2)); setConfirmStep(false); } }}>BID</button>
            <button className="btn-quick" disabled={mid == null} onClick={() => { if (mid != null) { setStopPrice(mid.toFixed(2)); setConfirmStep(false); } }}>MID</button>
            <button className="btn-quick" disabled={ask == null} onClick={() => { if (ask != null) { setStopPrice(ask.toFixed(2)); setConfirmStep(false); } }}>ASK</button>
          </div>
        </div>
      )}

      {orderType !== "STP" && (
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
      )}

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

      <OrderErrorBanner error={error} />

      {/* Order Summary (shown in confirm step). Owned by `<OrderRiskGate>`. */}
      {confirmStep && (
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface="order-tab-single"
          variant="info"
        />
      )}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!submitPermitted}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button className="btn-primary" onClick={handlePlace} disabled={!submitPermitted} style={{ width: "100%" }}>
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
  const comboUnits = heldComboUnits(position);
  const [quantity, setQuantity] = useState(() => String(comboUnits));
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("GTC");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const orderActions = useOrderActionsOptional();
  const tickerDetail = useTickerDetailOptional();
  const prefillNonce = tickerDetail?.orderPrefill?.nonce;

  useEffect(() => {
    const prefill = tickerDetail?.orderPrefill;
    if (!prefill) return;
    setLimitPrice(prefill.price.toFixed(2));
    if (prefill.action) setAction(prefill.action);
    setConfirmStep(false);
    // A repeated click at the same price must still reapply after manual edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  // Combo leg actions define the SPREAD STRUCTURE, not the trade direction.
  // IB reverses all leg actions when Order.action = SELL.
  // Always: LONG leg → BUY, SHORT leg → SELL (the spread definition).
  // Order.action (BUY/SELL) controls open vs close.
  const legsWithActions = useMemo(() => {
    return position.legs.map((leg) => {
      const legAction: "BUY" | "SELL" = leg.direction === "LONG" ? "BUY" : "SELL";
      const right = leg.type === "Call" ? "C" : "P";
      const expiryClean = position.expiry.replace(/-/g, "");
      const ratio = comboLegRatio(leg.contracts, comboUnits);
      return { ...leg, legAction, right: right as "C" | "P", expiry: expiryClean, ratio };
    });
  }, [position, comboUnits]);

  // Compute net BID / ASK / MID for the combo as a structural fair value.
  //
  // The strip describes the SPREAD itself, not a side of execution, so it
  // must be invariant to the BUY/SELL action toggle and must agree on sign
  // with the InstrumentDetail header (which uses `resolveSpreadPriceData`).
  // Convention: each leg contributes `direction * leg.{bid|ask}` to the net,
  // long adds, short subtracts. Credit spreads are negative, debit spreads
  // are positive. Same math as `resolveSpreadPriceData`.
  const netPrices = useMemo(() => {
    return resolveNaturalSpreadQuote(ticker, position, prices)
      ?? { bid: null, ask: null, mid: null, timestamp: null };
  }, [position, prices, ticker]);

  // A BAG has no quote of its own, so feed the net through the same model
  // every single-leg surface uses rather than hand-building a second one.
  // Session OHLV stays null on purpose: no exchange publishes a combo's
  // high, low or volume, so those render "---" instead of a borrowed number.
  const comboQuoteModel = useMemo(() => {
    if (netPrices.bid == null && netPrices.ask == null) return null;
    return buildQuoteTelemetryModel(
      comboQuotePriceData({
        symbol: ticker,
        bid: netPrices.bid,
        ask: netPrices.ask,
        last: netPrices.mid,
        timestamp: netPrices.timestamp,
      }),
    );
  }, [ticker, netPrices.bid, netPrices.ask, netPrices.mid, netPrices.timestamp]);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && Number.isFinite(parsedPrice) && parsedPrice !== 0;

  // Naked short guard — reactive warning for combo orders
  const nakedShortWarning = useMemo(() => {
    if (action !== "SELL") return null;
    const qty = !isNaN(parsedQty) && parsedQty > 0 ? parsedQty : 1;
    const legs = legsWithActions.map((leg) => ({
      expiry: leg.expiry,
      strike: leg.strike!,
      right: leg.right,
      action: leg.legAction,
      ratio: leg.ratio,
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

  // Calculate spread width for display
  const spreadWidth = netPrices.bid != null && netPrices.ask != null
    ? (netPrices.ask - netPrices.bid).toFixed(2)
    : null;
  const spreadPct = netPrices.mid != null && spreadWidth != null
    ? ((parseFloat(spreadWidth) / Math.abs(netPrices.mid)) * 100).toFixed(1)
    : null;

  // Calculate order summary for confirmation. For BUY (opening / adding
  // to a held combo) we use the per-leg risk model so risk reversals,
  // short straddles, and ratio spreads surface their true exposure
  // instead of the legacy "max loss = net debit" assumption.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValid) return null;

    const totalCost = parsedQty * parsedPrice * 100;
    const description = `${action} ${parsedQty}x ${position.structure} @ ${fmtSignedPrice(parsedPrice)}`;
    const chainLegs = legsWithActions
      .filter((l) => l.strike != null)
      .map((l) => ({
        action: l.legAction,
        right: l.right,
        strike: l.strike as number,
        expiry: l.expiry,
        quantity: parsedQty * l.ratio,
      }));

    // Only a SELL within the held BAG units is a pure close. An oversized
    // SELL creates new exposure and must pass through the normal risk model.
    if (isPureComboClose(action, parsedQty, comboUnits)) {
      const closeCashFlow = totalCost;
      return {
        ticker,
        chainLegs: chainLegs.map((leg) => ({
          ...leg,
          action: leg.action === "BUY" ? "SELL" as const : "BUY" as const,
        })),
        netPremium: parsedPrice,
        description,
        totalCost: closeCashFlow,
        closeOut: {
          entryCostDollars: resolveEntryCost(position) * (parsedQty / comboUnits),
        },
      };
    }

    if (action === "SELL") {
      return {
        ticker,
        chainLegs: chainLegs.map((leg) => ({
          ...leg,
          action: leg.action === "BUY" ? "SELL" as const : "BUY" as const,
        })),
        netPremium: -parsedPrice,
        description,
        totalCost,
      };
    }

    // Buying to open: hand the legs to the gate. The augmentation helper
    // will look at portfolio coverage automatically.
    return {
      ticker,
      chainLegs,
      netPremium: parsedPrice,
      description,
      totalCost,
    };
  }, [isValid, parsedQty, parsedPrice, action, position, legsWithActions, ticker, comboUnits]);
  const riskState = useOrderRisk(riskInput, portfolio);
  const submitPermitted = orderTabSubmitPermitted(isValid, loading, nakedShortWarning, riskState);

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      if (!submitPermitted) return;
      setConfirmStep(true);
      return;
    }
    if (!submitPermitted) return;

    setLoading(true);
    setError(null);
    try {
      const legs = legsWithActions.map((leg) => ({
        expiry: leg.expiry,
        strike: leg.strike!,
        right: leg.right,
        action: leg.legAction,
        ratio: leg.ratio,
      }));
      const guardResult = checkNakedShortRisk({
        type: "combo",
        symbol: ticker,
        action,
        quantity: parsedQty,
        limitPrice: parsedPrice,
        legs,
      }, toNakedShortPortfolio(portfolio));
      if (!guardResult.allowed) {
        setError(guardResult.reason ?? "Order blocked: naked short exposure");
        return;
      }
      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "combo", symbol: ticker, action, quantity: parsedQty, limitPrice: parsedPrice, tif, legs }),
      });
      const json = await res.json();
      if (!res.ok) setError(json.error || "Order placement failed");
      else {
        const feedback = placeOrderFeedback(json, `Combo order placed: ${action} ${parsedQty}x ${position.structure} @ ${fmtSignedPrice(parsedPrice)}`);
        orderActions?.pushNotification({ type: feedback.tone, message: feedback.message });
        setConfirmStep(false);
        onOrderPlaced?.();
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, submitPermitted, legsWithActions, ticker, action, parsedQty, parsedPrice, tif, portfolio, orderActions, position.structure, onOrderPlaced]);

  return (
    <div className="order-form">
      <OrderQuoteTelemetry model={comboQuoteModel} label={position.structure} />

      {/* Spread price strip — always visible at top */}
      <div className="spread-price-strip">
        <div className="spread-price-item">
          <span className="spread-price-label">BID</span>
          <span className={`spread-price-value ${comboQuoteClass(netPrices.bid, "bid")}`}>
            {fmtSignedPrice(netPrices.bid)}
          </span>
        </div>
        <div className="spread-price-item">
          <span className="spread-price-label">MID</span>
          <span className={`spread-price-value ${comboQuoteClass(netPrices.mid, "mid")}`}>
            {fmtSignedPrice(netPrices.mid)}
          </span>
        </div>
        <div className="spread-price-item">
          <span className="spread-price-label">ASK</span>
          <span className={`spread-price-value ${comboQuoteClass(netPrices.ask, "ask")}`}>
            {fmtSignedPrice(netPrices.ask)}
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
          <span className={`modify-price-prefix ${Number.isFinite(parsedPrice) && parsedPrice < 0 ? "negative" : ""}`}>$</span>
          <input
            className={`modify-price-input ${Number.isFinite(parsedPrice) && parsedPrice < 0 ? "negative" : toneClass(parsedPrice) === "positive" ? "positive" : ""}`}
            type="number"
            step="0.01"
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

      <OrderErrorBanner error={error} />

      {/* Order Summary (shown in confirm step). Owned by `<OrderRiskGate>`. */}
      {confirmStep && (
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface="order-tab-combo"
          variant="info"
        />
      )}

      {/* Submit / Confirm */}
      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!submitPermitted}
            >
              {loading ? "Placing..." : "Confirm Order"}
            </button>
          </div>
        ) : (
          <button className="btn-primary" onClick={handlePlace} disabled={!submitPermitted} style={{ width: "100%" }}>
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
  const isIndex = isIndexSymbol(ticker);

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
        // R-112: a close-out must count the SELL combos already working.
        openOrders={{ open_orders: openOrders }}
        onConfirm={handleModifyConfirm}
        onClose={() => setModifyTarget(null)}
      />

      <div className="order-tab">
        {/* NEW ORDER FORM FIRST — always visible above the fold */}
        {/* Indices are not directly tradeable — show a notice and gate
           the form. Phase 2 will add a futures order form for the
           tradeable VIX-future / SPX-future paths. */}
        {isIndex ? (
          <div className="new-order-section-top">
            <div className="existing-orders-title">New Order</div>
            {hasFuturesSupport(ticker) && (
              <FuturesOrderForm
                ticker={ticker}
                portfolio={portfolio}
                priceData={tickerPriceData ?? prices[ticker] ?? null}
              />
            )}
            {hasIndexOptionsSupport(ticker) && (
              <div style={{ marginTop: hasFuturesSupport(ticker) ? "24px" : "0" }}>
                <IndexOptionOrderForm ticker={ticker} portfolio={portfolio} />
              </div>
            )}
            {!hasFuturesSupport(ticker) && !hasIndexOptionsSupport(ticker) && (
              <div
                className="index-notice"
                style={{
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
                    color: "var(--signal-core-text)",
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
                  {ticker} is an index, not directly tradeable. Futures and options trading paths
                  for {ticker} are not yet wired.
                </div>
              </div>
            )}
          </div>
        ) : (
          <>
            {/* Combo order form for multi-leg positions */}
            {isCombo && (
              <div className="new-order-section-top">
                <div className="existing-orders-title">Close Position</div>
                <ComboOrderForm key={`${ticker}:${position!.id}`} ticker={ticker} position={position!} portfolio={portfolio} prices={prices} />
              </div>
            )}

            {/* Stock / single-leg order form */}
            {!isCombo && (
              <div className="new-order-section-top">
                <div className="existing-orders-title">{position ? "Close Position" : "New Order"}</div>
                <NewOrderForm key={`${ticker}:${position?.id ?? "new"}`} ticker={ticker} position={position} portfolio={portfolio} tickerPriceData={tickerPriceData} />
              </div>
            )}
          </>
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
