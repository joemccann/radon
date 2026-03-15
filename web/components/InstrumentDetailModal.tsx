"use client";

import { useCallback, useEffect, useState } from "react";
import type { PortfolioLeg } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import Modal from "./Modal";
import { InstrumentOrderQuoteTelemetry } from "./QuoteTelemetry";

export type InstrumentDetailProps = {
  leg: PortfolioLeg | null;
  ticker: string;
  expiry: string;
  prices: Record<string, PriceData>;
  onClose: () => void;
};

type OrderAction = "BUY" | "SELL";

export default function InstrumentDetailModal({ leg, ticker, expiry, prices, onClose }: InstrumentDetailProps) {
  const [quantity, setQuantity] = useState(() => String(leg?.contracts ?? ""));

  useEffect(() => {
    if (!leg) {
      setQuantity("");
      return;
    }
    setQuantity(String(leg.contracts));
  }, [leg?.contracts, leg?.direction, leg?.strike, leg?.type, ticker, expiry]);

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
}: {
  ticker: string;
  expiry: string;
  leg: PortfolioLeg;
  priceData: PriceData | null;
  quantity: string;
  onQuantityChange: (value: string) => void;
}) {
  const bid = priceData?.bid ?? null;
  const ask = priceData?.ask ?? null;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;

  const defaultAction: OrderAction = leg.direction === "LONG" ? "SELL" : "BUY";
  const [action, setAction] = useState<OrderAction>(defaultAction);
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState<"DAY" | "GTC">("GTC");
  const [confirmStep, setConfirmStep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const parsedQty = parseInt(quantity, 10);
  const parsedPrice = parseFloat(limitPrice);
  const isValid = !isNaN(parsedQty) && parsedQty > 0 && !isNaN(parsedPrice) && parsedPrice > 0;

  const strikeStr = leg.strike != null ? `$${leg.strike} ` : "";
  const right = leg.type === "Call" ? "C" : "P";
  const expiryClean = expiry.replace(/-/g, "");

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      setConfirmStep(true);
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "option",
          symbol: ticker,
          action,
          quantity: parsedQty,
          limitPrice: parsedPrice,
          tif,
          expiry: expiryClean,
          strike: leg.strike,
          right,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        setSuccess(`Order placed: ${action} ${parsedQty}x ${ticker} ${strikeStr}${right} @ ${fmtPrice(parsedPrice)}`);
        setConfirmStep(false);
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, ticker, action, parsedQty, parsedPrice, tif, expiryClean, leg.strike, right, strikeStr]);

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
          onChange={(e) => { onQuantityChange(e.target.value); setConfirmStep(false); }}
          placeholder="Contracts"
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
          <button className="btn-quick" disabled={bid == null} onClick={() => { if (bid != null) { setLimitPrice(bid.toFixed(2)); setConfirmStep(false); } }}>
            BID{bid != null ? ` ${bid.toFixed(2)}` : ""}
          </button>
          <button className="btn-quick" disabled={mid == null} onClick={() => { if (mid != null) { setLimitPrice(mid.toFixed(2)); setConfirmStep(false); } }}>
            MID{mid != null ? ` ${mid.toFixed(2)}` : ""}
          </button>
          <button className="btn-quick" disabled={ask == null} onClick={() => { if (ask != null) { setLimitPrice(ask.toFixed(2)); setConfirmStep(false); } }}>
            ASK{ask != null ? ` ${ask.toFixed(2)}` : ""}
          </button>
        </div>
      </div>

      <div className="order-field">
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`} onClick={() => setTif("DAY")}>DAY</button>
          <button className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`} onClick={() => setTif("GTC")}>GTC</button>
        </div>
      </div>

      {error && <div className="order-error">{error}</div>}
      {success && <div className="order-success">{success}</div>}

      <div className="order-submit">
        {confirmStep ? (
          <div className="order-confirm-row">
            <button className="btn-secondary" onClick={() => setConfirmStep(false)} disabled={loading}>Back</button>
            <button
              className={`btn-primary ${action === "SELL" ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValid || loading}
            >
              {loading ? "Placing..." : `Confirm: ${action} ${parsedQty}x ${strikeStr}${right} @ ${fmtPrice(parsedPrice)}`}
            </button>
          </div>
        ) : (
          <button className="btn-primary" onClick={handlePlace} disabled={!isValid || loading} style={{ width: "100%" }}>
            Place Order
          </button>
        )}
      </div>
    </div>
  );
}
