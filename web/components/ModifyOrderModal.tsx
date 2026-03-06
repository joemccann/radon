"use client";

import { useEffect, useMemo, useState } from "react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import Modal from "./Modal";
import { fmtPrice, legPriceKey } from "@/lib/positionUtils";

type ModifyOrderModalProps = {
  order: OpenOrder | null;
  loading: boolean;
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  onConfirm: (newPrice: number) => void;
  onClose: () => void;
};

function resolveOrderPriceData(
  order: OpenOrder,
  prices?: Record<string, PriceData>,
  portfolio?: PortfolioData | null,
): PriceData | null {
  if (!prices) return null;
  const c = order.contract;

  // STK: use ticker symbol key
  if (c.secType === "STK") {
    return prices[c.symbol] ?? null;
  }

  // OPT: build composite key
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

  // BAG: compute net bid/ask/mid from portfolio legs
  if (c.secType === "BAG" && portfolio) {
    const pos = portfolio.positions.find(
      (p) => p.ticker === c.symbol && p.legs.length > 1,
    );
    if (!pos) return null;

    let netBid = 0;
    let netAsk = 0;
    let netLast = 0;
    for (const leg of pos.legs) {
      const key = legPriceKey(pos.ticker, pos.expiry, leg);
      if (!key) return null;
      const lp = prices[key];
      if (!lp || lp.bid == null || lp.ask == null) return null;
      const sign = leg.direction === "LONG" ? 1 : -1;
      netBid += sign * lp.bid;
      netAsk += sign * lp.ask;
      netLast += sign * (lp.last ?? (lp.bid + lp.ask) / 2);
    }

    // For debit spreads net natural bid < ask; ensure correct ordering
    const lo = Math.min(netBid, netAsk);
    const hi = Math.max(netBid, netAsk);

    return {
      symbol: c.symbol,
      last: Math.round(netLast * 100) / 100,
      lastIsCalculated: true,
      bid: Math.round(lo * 100) / 100,
      ask: Math.round(hi * 100) / 100,
      bidSize: null,
      askSize: null,
      volume: null,
      high: null,
      low: null,
      open: null,
      close: null,
      delta: null,
      gamma: null,
      theta: null,
      vega: null,
      impliedVol: null,
      undPrice: null,
      timestamp: new Date().toISOString(),
    };
  }

  return null;
}

export default function ModifyOrderModal({ order, loading, prices, portfolio, onConfirm, onClose }: ModifyOrderModalProps) {
  const [newPrice, setNewPrice] = useState("");

  // Reset price only when a different order is selected (by permId), not on every re-render
  const orderPermId = order?.permId ?? null;
  useEffect(() => {
    if (order?.limitPrice != null) {
      setNewPrice(order.limitPrice.toFixed(2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderPermId]);

  const priceData = useMemo(
    () => (order ? resolveOrderPriceData(order, prices, portfolio) : null),
    [order, prices, portfolio],
  );

  if (!order) return null;

  const currentPrice = order.limitPrice ?? 0;
  const parsedNew = parseFloat(newPrice);
  const isValid = !isNaN(parsedNew) && parsedNew > 0;
  const priceChanged = isValid && Math.abs(parsedNew - currentPrice) >= 0.005;
  const canSubmit = priceChanged && !loading;

  const delta = isValid ? parsedNew - currentPrice : 0;
  const hasPriceData = priceData?.bid != null && priceData?.ask != null;

  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;
  const spread = bid != null && ask != null ? ask - bid : null;

  return (
    <Modal open={!!order} onClose={onClose} title="Modify Order">
      <div className="modify-dialog">
        {/* Order info header */}
        <div className="modify-order-info">
          <strong>{order.symbol}</strong>
          <span className={`pill ${order.action === "BUY" ? "accum" : "distrib"}`}>
            {order.action}
          </span>
          <span>{order.orderType}</span>
          <span>{order.tif}</span>
        </div>

        {/* Market data section */}
        {hasPriceData ? (
          <div className="modify-market-data">
            <div className="modify-market-row">
              <span className="modify-market-label">BID</span>
              <span className="modify-market-value">{fmtPrice(bid!)}</span>
            </div>
            <div className="modify-market-row">
              <span className="modify-market-label">ASK</span>
              <span className="modify-market-value">{fmtPrice(ask!)}</span>
            </div>
            <div className="modify-market-row">
              <span className="modify-market-label">MID</span>
              <span className="modify-market-value">{fmtPrice(mid!)}</span>
            </div>
            <div className="modify-market-row">
              <span className="modify-market-label">SPREAD</span>
              <span className="modify-market-value">{fmtPrice(spread!)}</span>
            </div>
          </div>
        ) : (
          <div className="modify-market-warning">
            No real-time market data available
          </div>
        )}

        {/* Stop price (read-only for STP LMT) */}
        {order.orderType === "STP LMT" && order.auxPrice != null && (
          <div className="modify-stop-row">
            <span className="modify-market-label">STOP PRICE</span>
            <span className="modify-market-value">{fmtPrice(order.auxPrice)}</span>
          </div>
        )}

        {/* Price input */}
        <div className="modify-price-section">
          <label className="modify-price-label" htmlFor="modify-price-input">
            New Limit Price
          </label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              id="modify-price-input"
              className="modify-price-input"
              type="number"
              step="0.01"
              min="0.01"
              value={newPrice}
              onChange={(e) => setNewPrice(e.target.value)}
              autoFocus
            />
          </div>

          {/* Quick-set buttons */}
          <div className="modify-quick-buttons">
            <button
              className="btn-quick"
              disabled={!hasPriceData || bid == null}
              onClick={() => bid != null && setNewPrice(bid.toFixed(2))}
            >
              BID
            </button>
            <button
              className="btn-quick"
              disabled={!hasPriceData || mid == null}
              onClick={() => mid != null && setNewPrice(mid.toFixed(2))}
            >
              MID
            </button>
            <button
              className="btn-quick"
              disabled={!hasPriceData || ask == null}
              onClick={() => ask != null && setNewPrice(ask.toFixed(2))}
            >
              ASK
            </button>
          </div>

          {/* Change indicator */}
          {isValid && delta !== 0 && (
            <div className={`modify-delta ${delta > 0 ? "positive" : "negative"}`}>
              {delta > 0 ? "+" : ""}{fmtPrice(Math.abs(delta))} from current {fmtPrice(currentPrice)}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="modify-actions">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="btn-primary" onClick={() => canSubmit && onConfirm(parsedNew)} disabled={!canSubmit}>
            {loading ? "Modifying..." : "Modify Order"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
