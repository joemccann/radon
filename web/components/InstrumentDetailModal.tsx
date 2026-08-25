"use client";

import { useEffect, useMemo, useState } from "react";
import type { PortfolioLeg } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import Modal from "./Modal";
import SingleLegOrderTicket, { type SingleLegOrderAction } from "./SingleLegOrderTicket";
import { InstrumentOrderQuoteTelemetry } from "./QuoteTelemetry";
import { type OrderRiskInput } from "@/lib/order";
import {
  type IbOrderType,
  ibPlaceFields,
  pricesValidForOrderType,
  riskPriceForOrderType,
} from "@/lib/order/stopOrder";
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

export default function InstrumentDetailModal({ leg, ticker, expiry, prices, onClose, portfolio }: InstrumentDetailProps) {
  const [quantity, setQuantity] = useState(() => String(leg?.contracts ?? ""));

  useEffect(() => {
    if (!leg) {
      setQuantity("");
      return;
    }
    setQuantity(String(leg.contracts));
  }, [leg, ticker, expiry]);

  if (!leg) return null;

  const isStock = leg.type === "Stock";
  const priceKey = legPriceKey(ticker, expiry, leg);
  const priceData = isStock
    ? prices[ticker.toUpperCase()] ?? prices[ticker] ?? null
    : priceKey
      ? prices[priceKey] ?? null
      : null;

  // Derive header label: "AAOI $105 Call 2026-03-20"
  const strikeStr = !isStock && leg.strike != null ? `$${leg.strike} ` : "";
  const title = isStock ? `${ticker} Stock` : `${ticker} ${strikeStr}${leg.type} ${expiry}`;

  // Position summary
  const mult = isStock ? 1 : 100;
  const rtLast = priceData?.last != null && priceData.last > 0 ? priceData.last : null;
  const legMv = rtLast != null ? rtLast * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;
  const avgEntry = Math.abs(leg.avg_cost) / mult;

  // Price bar label
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : "";
  const priceLabel = isStock ? `${ticker} STOCK` : `${ticker} ${expiry} ${strikeStr}${right}`;

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

/* ─── Single-instrument order form ─── */

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
  portfolio: PortfolioData | null | undefined;
}) {
  const orderActions = useOrderActionsOptional();
  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;
  const isStock = leg.type === "Stock";

  const defaultAction: SingleLegOrderAction = leg.direction === "LONG" ? "SELL" : "BUY";
  const [action, setAction] = useState<SingleLegOrderAction>(defaultAction);
  const [limitPrice, setLimitPrice] = useState("");
  const [orderType, setOrderType] = useState<IbOrderType>("LMT");
  const [stopPrice, setStopPrice] = useState("");

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const parsedStop = parseFloat(stopPrice);
  const isValid =
    !isNaN(parsedQty)
    && parsedQty > 0
    && pricesValidForOrderType({ orderType, limitPrice: parsedPrice, stopPrice: parsedStop });

  const strikeStr = !isStock && leg.strike != null ? `$${leg.strike} ` : "";
  const right: "C" | "P" | null = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
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
    const multiplier = isStock ? 1 : 100;
    const riskPrice = riskPriceForOrderType(orderType, parsedPrice, parsedStop);
    const totalCost = parsedQty * riskPrice * multiplier;
    const description = isStock
      ? `${action} ${parsedQty} ${ticker} Stock @ ${fmtPrice(riskPrice)}`
      : `${action} ${parsedQty}x ${ticker} ${strikeStr}${right} @ ${fmtPrice(riskPrice)}`;

    if (isStock) {
      const closingLong = leg.direction === "LONG" && action === "SELL" && parsedQty <= leg.contracts;
      const closingShort = leg.direction === "SHORT" && action === "BUY" && parsedQty <= leg.contracts;
      return {
        type: "linear",
        ticker,
        instrument: "stock",
        action,
        quantity: parsedQty,
        limitPrice: riskPrice,
        multiplier: 1,
        heldQuantity: leg.direction === "LONG" ? leg.contracts : 0,
        heldShortQuantity: leg.direction === "SHORT" ? leg.contracts : 0,
        description,
        closeOut:
          closingLong || closingShort
            ? {
                entryCostDollars: parsedQty * Math.abs(leg.avg_cost),
              }
            : undefined,
      };
    }

    if (right == null || leg.strike == null) return null;
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
        chainLegs: [{ action, right, strike: leg.strike, expiry, quantity: parsedQty }],
        netPremium: action === "SELL" ? -riskPrice : riskPrice,
        description,
        totalCost: proceeds,
        closeOut: { entryCostDollars },
      };
    }
    return {
      ticker,
      chainLegs: [
        { action, right, strike: leg.strike, expiry, quantity: parsedQty },
      ],
      netPremium: action === "SELL" ? -parsedPrice : parsedPrice,
      description,
      totalCost: action === "SELL" ? -totalCost : totalCost,
      // FU7: thread the single-leg live quote for net-of-cost risk.
      quote: { bid, ask },
    };
  }, [isValid, parsedQty, parsedPrice, parsedStop, orderType, action, ticker, strikeStr, right, isStock, leg.strike, leg.direction, leg.contracts, leg.avg_cost, expiry, bid, ask]);

  // Deliberately no `priceData` below: the modal already renders the telemetry
  // block above this form, and handing it to the ticket as well prints the
  // same nine fields twice.
  return (
    <SingleLegOrderTicket
      defaultAction={defaultAction}
      defaultTif={isStock ? "DAY" : "GTC"}
      quantity={quantity}
      onQuantityChange={onQuantityChange}
      quantityPlaceholder={isStock ? "Shares" : "Contracts"}
      bid={bid}
      mid={mid}
      ask={ask}
      showQuickButtonPrices={true}
      isValid={isValid}
      limitPrice={limitPrice}
      onLimitPriceChange={setLimitPrice}
      orderType={orderType}
      onOrderTypeChange={setOrderType}
      stopPrice={stopPrice}
      onStopPriceChange={setStopPrice}
      onActionChange={setAction}
      riskInput={riskInput}
      portfolio={portfolio}
      riskSurface="instrument-modal"
      buildPayload={({ action, quantity, limitPrice, tif, orderType, stopPrice }) =>
        isStock
          ? {
              type: "stock",
              symbol: ticker,
              action,
              quantity,
              tif,
              ...ibPlaceFields(orderType, limitPrice, stopPrice),
            }
          : {
              type: "option",
              symbol: ticker,
              action,
              quantity,
              tif,
              expiry: expiryClean,
              strike: leg.strike,
              right,
              ...ibPlaceFields(orderType, limitPrice, stopPrice),
            }
      }
      buildSuccessMessage={({ action, quantity, limitPrice, orderType, stopPrice }) => {
        const executionPrice = riskPriceForOrderType(orderType, limitPrice, stopPrice);
        return isStock
          ? `Order placed: ${action} ${quantity} ${ticker} Stock @ ${fmtPrice(executionPrice)}`
          : `Order placed: ${action} ${quantity}x ${ticker} ${strikeStr}${right} @ ${fmtPrice(executionPrice)}`;
      }}
      onSuccessToast={(message, tone) => orderActions?.pushNotification({ type: tone, message })}
      suppressInlineSuccess
    />
  );
}
