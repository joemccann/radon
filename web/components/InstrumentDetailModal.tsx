"use client";

import { useEffect, useMemo, useState } from "react";
import type { PortfolioLeg } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import Modal from "./Modal";
import SingleLegOrderTicket, { type SingleLegOrderAction } from "./SingleLegOrderTicket";
import { InstrumentOrderQuoteTelemetry } from "./QuoteTelemetry";
import { OrderRiskGate, type OrderRiskInput } from "@/lib/order";
import { useOrderActionsOptional } from "@/lib/OrderActionsContext";
import type { PortfolioData } from "@/lib/types";

export type InstrumentDetailProps = {
  leg: PortfolioLeg | null;
  ticker: string;
  expiry: string;
  prices: Record<string, PriceData>;
  onClose: () => void;
  /**
   * Live portfolio snapshot. Optional today (callers pre-refactor don't
   * thread it); when omitted the order-risk gate renders "Coverage
   * indeterminate" and the operator sees the gap explicitly. Wiring the
   * prop in every call site is its own step in `tasks/order-risk-
   * chokepoint-refactor.md`.
   */
  portfolio?: PortfolioData | null;
};

export default function InstrumentDetailModal({ leg, ticker, expiry, prices, onClose, portfolio = null }: InstrumentDetailProps) {
  const [quantity, setQuantity] = useState(() => String(leg?.contracts ?? ""));

  useEffect(() => {
    if (!leg) {
      setQuantity("");
      return;
    }
    setQuantity(String(leg.contracts));
  }, [leg, ticker, expiry]);

  if (!leg) return null;

  const priceKey = legPriceKey(ticker, expiry, leg);
  const priceData = priceKey ? prices[priceKey] ?? null : null;

  // Derive header label: "AAOI $105 Call 2026-03-20"
  const strikeStr = leg.strike != null ? `$${leg.strike} ` : "";
  const title = `${ticker} ${strikeStr}${leg.type} ${expiry}`;

  // Position summary
  const mult = leg.type === "Stock" ? 1 : 100;
  const rtLast = priceData?.last != null && priceData.last > 0 ? priceData.last : null;
  const legMv = rtLast != null ? rtLast * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;
  const avgEntry = Math.abs(leg.avg_cost) / mult;

  // Price bar label
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : "";
  const priceLabel = `${ticker} ${expiry} ${strikeStr}${right}`;

  return (
    <Modal open={true} onClose={onClose} title={title} className="instrument-detail-modal">
      <div className="ticker-detail-content">
        {/* Position summary pill */}
        <div className="instrument-summary-grid">
          <div className="pos-stat">
            <span className="pos-stat-label">DIRECTION</span>
            <span className="pos-stat-value">{leg.direction} {leg.contracts}x</span>
          </div>
          <div className="pos-stat">
            <span className="pos-stat-label">AVG ENTRY</span>
            <span className="pos-stat-value">{fmtPrice(avgEntry)}</span>
          </div>
          <div className="pos-stat">
            <span className="pos-stat-label">P&L</span>
            <span className={`pos-stat-value ${legPnl != null ? (legPnl >= 0 ? "positive" : "negative") : ""}`}>
              {legPnl != null ? `${legPnl >= 0 ? "+" : ""}${fmtUsd(Math.abs(legPnl))}` : "---"}
            </span>
          </div>
        </div>

        {/* Price bar */}
        <InstrumentOrderQuoteTelemetry
          priceData={priceData}
          label={priceLabel}
        />

        {/* Order form */}
        <div style={{ paddingTop: 16 }}>
          <LegOrderForm
            ticker={ticker}
            expiry={expiry}
            leg={leg}
            priceData={priceData}
            quantity={quantity}
            onQuantityChange={setQuantity}
            portfolio={portfolio}
          />
        </div>
      </div>
    </Modal>
  );
}

/* ─── Single-leg option order form ─── */

function LegOrderForm({
  ticker,
  expiry,
  leg,
  priceData,
  quantity,
  onQuantityChange,
  portfolio,
}: {
  ticker: string;
  expiry: string;
  leg: PortfolioLeg;
  priceData: PriceData | null;
  quantity: string;
  onQuantityChange: (value: string) => void;
  portfolio: PortfolioData | null;
}) {
  const orderActions = useOrderActionsOptional();
  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;

  const defaultAction: SingleLegOrderAction = leg.direction === "LONG" ? "SELL" : "BUY";
  const [action, setAction] = useState<SingleLegOrderAction>(defaultAction);
  const [limitPrice, setLimitPrice] = useState("");

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && !isNaN(parsedPrice) && parsedPrice > 0;

  const strikeStr = leg.strike != null ? `$${leg.strike} ` : "";
  const right = leg.type === "Call" ? "C" : "P";
  const expiryClean = expiry.replace(/-/g, "");

  // Build the chokepoint input. Risk math + close-out detection + portfolio
  // coverage all flow through `<OrderRiskGate>` below. The previous in-line
  // `isClosingHeld` boolean was qty-blind (treated SELL N of held M < N as
  // a pure close); the gate's `closeOut` branch is qty-aware via the
  // `entryCostDollars` parameter.
  //
  // `portfolio` may be null here if the modal was opened from a surface
  // that hasn't yet been threaded with the prop — the gate then renders a
  // "Coverage indeterminate" skeleton instead of silently wrong risk.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValid) return null;
    const totalCost = parsedQty * parsedPrice * 100;
    const description = `${action} ${parsedQty}x ${ticker} ${strikeStr}${right} @ ${fmtPrice(parsedPrice)}`;
    const optionRight: "C" | "P" | null = right === "C" ? "C" : right === "P" ? "P" : null;
    if (optionRight == null || leg.strike == null) {
      return {
        ticker,
        chainLegs: [],
        netPremium: action === "SELL" ? -parsedPrice : parsedPrice,
        description,
        totalCost: action === "SELL" ? -totalCost : totalCost,
      };
    }
    // Close-out path: SELL of a held LONG (or BUY of a held SHORT) up to
    // the held-contract count is a pure close. Above the count → the
    // excess opens fresh exposure and goes through the augmentation
    // pipeline normally.
    const isClosingHeld =
      ((leg.direction === "LONG" && action === "SELL") ||
        (leg.direction === "SHORT" && action === "BUY")) &&
      parsedQty <= leg.contracts;
    if (isClosingHeld) {
      const proceeds = action === "SELL" ? totalCost : -totalCost;
      const basisMagnitude = parsedQty * Math.abs(leg.avg_cost);
      const entryCostDollars = leg.direction === "SHORT" ? -basisMagnitude : basisMagnitude;
      return {
        ticker,
        chainLegs: [],
        netPremium: action === "SELL" ? -parsedPrice : parsedPrice,
        description,
        totalCost: proceeds,
        closeOut: { entryCostDollars },
      };
    }
    return {
      ticker,
      chainLegs: [
        { action, right: optionRight, strike: leg.strike, expiry, quantity: parsedQty },
      ],
      netPremium: action === "SELL" ? -parsedPrice : parsedPrice,
      description,
      totalCost: action === "SELL" ? -totalCost : totalCost,
      // FU7: thread the single-leg live quote for net-of-cost risk.
      quote: { bid, ask },
    };
  }, [isValid, parsedQty, parsedPrice, action, ticker, strikeStr, right, leg.strike, leg.direction, leg.contracts, leg.avg_cost, expiry, bid, ask]);

  return (
    <SingleLegOrderTicket
      defaultAction={defaultAction}
      defaultTif="GTC"
      quantity={quantity}
      onQuantityChange={onQuantityChange}
      quantityPlaceholder="Contracts"
      bid={bid}
      mid={mid}
      ask={ask}
      showQuickButtonPrices={true}
      isValid={isValid}
      limitPrice={limitPrice}
      onLimitPriceChange={setLimitPrice}
      onActionChange={setAction}
      riskGate={
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface="instrument-modal"
          variant="info"
        />
      }
      buildPayload={({ action, quantity, limitPrice, tif }) => ({
        type: "option",
        symbol: ticker,
        action,
        quantity,
        limitPrice,
        tif,
        expiry: expiryClean,
        strike: leg.strike,
        right,
      })}
      buildSuccessMessage={({ action, quantity, limitPrice }) =>
        `Order placed: ${action} ${quantity}x ${ticker} ${strikeStr}${right} @ ${fmtPrice(limitPrice)}`
      }
      onSuccessToast={(message) => orderActions?.pushNotification({ type: "success", message })}
      suppressInlineSuccess
    />
  );
}
