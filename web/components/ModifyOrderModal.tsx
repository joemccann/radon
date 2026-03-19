"use client";

import { useEffect, useMemo, useState } from "react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import type { ModifyComboLeg, ModifyOrderRequest } from "@/lib/orderModify";
import Modal from "./Modal";
import { getQuoteMetrics } from "@/lib/quoteTelemetry";
import { applyRestingLimitToQuote } from "@/lib/modifyOrderQuote";
import { fmtPrice, legPriceKey } from "@/lib/positionUtils";
import { ModifyOrderQuoteTelemetry } from "./QuoteTelemetry";
import { OrderPriceStrip, OrderLegPills, type OrderLeg as UnifiedOrderLeg } from "@/lib/order";

type EditableComboLeg = {
  action: "BUY" | "SELL";
  expiry: string;
  strike: string;
  right: "C" | "P";
  ratio: string;
};

type ModifyOrderModalProps = {
  order: OpenOrder | null;
  loading: boolean;
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  onConfirm: (request: ModifyOrderRequest) => void;
  onClose: () => void;
};

function normalizeLegAction(action?: string | null): "BUY" | "SELL" {
  return action === "SELL" ? "SELL" : "BUY";
}

function normalizeLegRight(right?: string | null): "C" | "P" {
  return right === "P" || right === "PUT" ? "P" : "C";
}

function normalizeExpiry(expiry?: string | null): string {
  if (!expiry) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return expiry;
  const clean = expiry.replace(/-/g, "");
  if (clean.length === 8) {
    return `${clean.slice(0, 4)}-${clean.slice(4, 6)}-${clean.slice(6, 8)}`;
  }
  return expiry;
}

function buildEditableComboLegs(order: OpenOrder | null): EditableComboLeg[] {
  if (!order?.contract.comboLegs?.length) return [];
  return order.contract.comboLegs.map((leg) => ({
    action: normalizeLegAction(leg.action),
    expiry: normalizeExpiry(leg.expiry),
    strike: leg.strike != null ? String(leg.strike) : "",
    right: normalizeLegRight(leg.right),
    ratio: String(leg.ratio ?? 1),
  }));
}

function comboUnderlyingSymbol(order: OpenOrder): string {
  const comboSymbol = order.contract.comboLegs?.find((leg) => leg.symbol)?.symbol;
  if (comboSymbol) return comboSymbol.toUpperCase();

  const contractSymbol = order.contract.symbol?.replace(/\s+spread$/i, "").trim();
  if (contractSymbol) return contractSymbol.toUpperCase();

  return order.symbol.replace(/\s+spread$/i, "").trim().toUpperCase();
}

function normalizeComboLegs(legs: EditableComboLeg[]): ModifyComboLeg[] | null {
  const normalized = legs.map((leg) => {
    const strike = Number.parseFloat(leg.strike);
    const ratio = Number.parseInt(leg.ratio, 10);
    const expiry = leg.expiry.replace(/-/g, "");
    if (!Number.isFinite(strike) || strike <= 0 || !Number.isFinite(ratio) || ratio <= 0 || expiry.length !== 8) {
      return null;
    }
    return {
      action: leg.action,
      expiry,
      strike,
      right: leg.right,
      ratio,
    } satisfies ModifyComboLeg;
  });

  return normalized.every((leg): leg is ModifyComboLeg => leg != null) ? normalized : null;
}

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

  // BAG: compute net bid/ask/mid from combo legs (order data or portfolio fallback)
  // Natural market calculation:
  //   netBid = proceeds if we SELL at market (receive bid on BUY legs, pay ask on SELL legs)
  //   netAsk = cost if we BUY at market (pay ask on BUY legs, receive bid on SELL legs)
  if (c.secType === "BAG") {
    let netBid = 0;
    let netAsk = 0;
    let netLast = 0;
    let resolved = false;

    // Primary: use combo legs from the order itself (resolved during sync)
    if (c.comboLegs?.length) {
      let allAvailable = true;
      for (const cl of c.comboLegs) {
        if (!cl.symbol || cl.strike == null || !cl.right || !cl.expiry) {
          allAvailable = false;
          break;
        }
        const expiryClean = cl.expiry.replace(/-/g, "");
        if (expiryClean.length !== 8) { allAvailable = false; break; }
        const right = cl.right === "C" || cl.right === "P"
          ? cl.right
          : cl.right === "CALL" ? "C" : cl.right === "PUT" ? "P" : null;
        if (!right) { allAvailable = false; break; }
        const key = optionKey({
          symbol: cl.symbol.toUpperCase(),
          expiry: expiryClean,
          strike: cl.strike,
          right,
        });
        const lp = prices[key];
        if (!lp || lp.bid == null || lp.ask == null) { allAvailable = false; break; }
        
        // Natural market: BUY leg = pay ask / receive bid, SELL leg = receive bid / pay ask
        if (cl.action === "BUY") {
          netAsk += lp.ask;  // To BUY combo: pay ask on BUY legs
          netBid += lp.bid;  // To SELL combo: receive bid on BUY legs
        } else {
          netAsk -= lp.bid;  // To BUY combo: receive bid on SELL legs
          netBid -= lp.ask;  // To SELL combo: pay ask on SELL legs
        }
        const sign = cl.action === "BUY" ? 1 : -1;
        netLast += sign * (lp.last ?? (lp.bid + lp.ask) / 2);
      }
      resolved = allAvailable;
    }

    // Fallback: use portfolio position legs
    if (!resolved && portfolio) {
      const pos = portfolio.positions.find(
        (p) => p.ticker === c.symbol && p.legs.length > 1,
      );
      if (pos) {
        netBid = 0;
        netAsk = 0;
        netLast = 0;
        let allAvailable = true;
        for (const leg of pos.legs) {
          const key = legPriceKey(pos.ticker, pos.expiry, leg);
          if (!key) { allAvailable = false; break; }
          const lp = prices[key];
          if (!lp || lp.bid == null || lp.ask == null) { allAvailable = false; break; }
          
          // Natural market: LONG leg = pay ask / receive bid, SHORT leg = receive bid / pay ask
          if (leg.direction === "LONG") {
            netAsk += lp.ask;  // To BUY combo: pay ask on LONG legs
            netBid += lp.bid;  // To SELL combo: receive bid on LONG legs
          } else {
            netAsk -= lp.bid;  // To BUY combo: receive bid on SHORT legs
            netBid -= lp.ask;  // To SELL combo: pay ask on SHORT legs
          }
          const sign = leg.direction === "LONG" ? 1 : -1;
          netLast += sign * (lp.last ?? (lp.bid + lp.ask) / 2);
        }
        resolved = allAvailable;
      }
    }

    if (!resolved) return null;

    // Ensure bid < ask (natural market ordering)
    const lo = Math.min(Math.abs(netBid), Math.abs(netAsk));
    const hi = Math.max(Math.abs(netBid), Math.abs(netAsk));

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
      week52High: null,
      week52Low: null,
      avgVolume: null,
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
  const [newQuantity, setNewQuantity] = useState("");
  const [outsideRth, setOutsideRth] = useState(false);
  const [editableLegs, setEditableLegs] = useState<EditableComboLeg[]>([]);

  // Reset price only when a different order is selected (by permId), not on every re-render
  const orderPermId = order?.permId ?? null;
  useEffect(() => {
    if (order?.limitPrice != null) {
      setNewPrice(order.limitPrice.toFixed(2));
    }
    if (order?.totalQuantity != null) {
      setNewQuantity(String(order.totalQuantity));
    }
    setOutsideRth(false);
    setEditableLegs(buildEditableComboLegs(order));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderPermId]);

  const marketPriceData = useMemo(
    () => (order ? resolveOrderPriceData(order, prices, portfolio) : null),
    [order, prices, portfolio],
  );

  const priceData = useMemo(
    () => applyRestingLimitToQuote({
      priceData: marketPriceData,
      action: order?.action,
      limitPrice: order?.limitPrice,
    }),
    [marketPriceData, order?.action, order?.limitPrice],
  );

  if (!order) return null;

  const currentPrice = order.limitPrice ?? 0;
  const currentQuantity = order.totalQuantity;
  const parsedNew = parseFloat(newPrice);
  const parsedQuantity = Number.parseInt(newQuantity, 10);
  const isValidPrice = !Number.isNaN(parsedNew) && parsedNew > 0;
  const isValidQuantity = Number.isInteger(parsedQuantity) && parsedQuantity > 0;
  const isComboOrder = order.contract.secType === "BAG" && editableLegs.length >= 2;
  const normalizedLegs = normalizeComboLegs(editableLegs);
  const originalLegsSnapshot = JSON.stringify(buildEditableComboLegs(order));
  const currentLegsSnapshot = JSON.stringify(editableLegs);
  const priceChanged = isValidPrice && Math.abs(parsedNew - currentPrice) >= 0.005;
  const quantityChanged = isValidQuantity && parsedQuantity !== currentQuantity;
  const legsChanged = isComboOrder && currentLegsSnapshot !== originalLegsSnapshot;
  const canSubmit = !loading && (
    isComboOrder
      ? Boolean(isValidPrice && isValidQuantity && normalizedLegs && (priceChanged || quantityChanged || legsChanged))
      : Boolean((priceChanged || quantityChanged || outsideRth) && isValidPrice && isValidQuantity)
  );

  const delta = isValidPrice ? parsedNew - currentPrice : 0;
  const hasPriceData = priceData?.bid != null && priceData?.ask != null;

  const { bid, mid, ask } = getQuoteMetrics(priceData);
  const handleLegChange = (index: number, patch: Partial<EditableComboLeg>) => {
    setEditableLegs((prev) => prev.map((leg, legIndex) => (legIndex === index ? { ...leg, ...patch } : leg)));
  };

  const submitModify = () => {
    if (!canSubmit) return;

    if (isComboOrder && normalizedLegs) {
      onConfirm({
        replaceOrder: {
          type: "combo",
          symbol: comboUnderlyingSymbol(order),
          action: order.action === "BUY" ? "BUY" : "SELL",
          quantity: parsedQuantity,
          limitPrice: parsedNew,
          tif: order.tif === "GTC" ? "GTC" : "DAY",
          legs: normalizedLegs,
        },
      });
      return;
    }

    const request: ModifyOrderRequest = {};
    if (priceChanged) request.newPrice = parsedNew;
    if (quantityChanged) request.newQuantity = parsedQuantity;
    if (outsideRth) request.outsideRth = true;
    onConfirm(request);
  };

  return (
    <Modal
      open={!!order}
      onClose={onClose}
      title="Modify Order"
      className={isComboOrder ? "modify-order-modal modify-order-modal-combo" : "modify-order-modal"}
    >
      <div className={`modify-dialog${isComboOrder ? " modify-dialog-combo" : ""}`}>
        <div className="modify-order-info">
          <strong>{order.symbol}</strong>
          <span className={`pill ${order.action === "BUY" ? "accum" : "distrib"}`}>
            {order.action}
          </span>
          <span>{order.orderType}</span>
          <span>{order.tif}</span>
          <span>{order.totalQuantity}x</span>
        </div>

        <div className={`modify-layout${isComboOrder ? " modify-layout-combo" : ""}`}>
          <div className="modify-primary-panel">
            <ModifyOrderQuoteTelemetry priceData={priceData} />

            {/* Price strip for combo orders */}
            {isComboOrder && hasPriceData && bid != null && ask != null && mid != null && (
              <OrderPriceStrip
                prices={{
                  bid,
                  mid,
                  ask,
                  spread: ask - bid,
                  spreadPct: mid > 0 ? ((ask - bid) / mid) * 100 : null,
                  available: true,
                }}
                compact
              />
            )}

            {order.orderType === "STP LMT" && order.auxPrice != null && (
              <div className="modify-stop-row">
                <span className="modify-market-label">STOP PRICE</span>
                <span className="modify-market-value">{fmtPrice(order.auxPrice)}</span>
              </div>
            )}

            <div className="modify-price-section">
              <div className={`modify-field-grid${isComboOrder ? " modify-field-grid-combo" : ""}`}>
                <label className="modify-field" htmlFor="modify-quantity-input">
                  <span className="modify-price-label">New Quantity</span>
                  <div className="modify-price-input-row">
                    <input
                      id="modify-quantity-input"
                      className="modify-price-input"
                      type="number"
                      step="1"
                      min="1"
                      value={newQuantity}
                      onChange={(e) => setNewQuantity(e.target.value)}
                    />
                  </div>
                </label>

                <label className="modify-field" htmlFor="modify-price-input">
                  <span className="modify-price-label">{isComboOrder ? "New Net Price" : "New Limit Price"}</span>
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
                </label>
              </div>

              <div className="modify-quick-section">
                <span className="modify-price-label">Reference Price</span>
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
              </div>

              {!isComboOrder && (
                <label className="modify-rth-toggle">
                  <input
                    type="checkbox"
                    checked={outsideRth}
                    onChange={(e) => setOutsideRth(e.target.checked)}
                  />
                  <span className="modify-rth-label">FILL OUTSIDE RTH</span>
                  <span className="modify-rth-hint">Pre-market &amp; after hours</span>
                </label>
              )}

              {isValidPrice && delta !== 0 && (
                <div className={`modify-delta ${delta > 0 ? "positive" : "negative"}`}>
                  {delta > 0 ? "+" : ""}{fmtPrice(Math.abs(delta))} from current {fmtPrice(currentPrice)}
                </div>
              )}
            </div>
          </div>

          {isComboOrder && (
            <div className="modify-secondary-panel">
              {/* Leg pills summary (read-only view) */}
              {(() => {
                const unifiedLegs: UnifiedOrderLeg[] = editableLegs.map((leg, i) => ({
                  id: `leg-${i}`,
                  action: leg.action,
                  direction: leg.action === "BUY" ? "LONG" : "SHORT" as const,
                  strike: Number.parseFloat(leg.strike) || 0,
                  type: leg.right === "C" ? "Call" : "Put" as const,
                  expiry: leg.expiry,
                  quantity: Number.parseInt(leg.ratio, 10) || 1,
                }));
                return (
                  <div style={{ marginBottom: "12px" }}>
                    <OrderLegPills legs={unifiedLegs} />
                  </div>
                );
              })()}

              <div className="modify-section-heading">
                <span className="modify-price-label">Edit Legs</span>
                <span className="modify-section-hint">Modify each leg before replacing the order</span>
              </div>

              <div className="modify-combo-legs">
                {editableLegs.map((leg, index) => (
                  <section className="modify-combo-leg-card" key={`${order.permId}-leg-${index}`}>
                    <div className="modify-combo-leg-title">Leg {index + 1}</div>
                    <div className="modify-combo-leg-grid">
                      <label className="modify-field" htmlFor={`modify-leg-${index}-action`}>
                        <span className="modify-price-label">Action</span>
                        <div className="modify-price-input-row">
                          <select
                            id={`modify-leg-${index}-action`}
                            className="modify-price-input"
                            value={leg.action}
                            onChange={(e) => handleLegChange(index, { action: normalizeLegAction(e.target.value) })}
                          >
                            <option value="BUY">BUY</option>
                            <option value="SELL">SELL</option>
                          </select>
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-right`}>
                        <span className="modify-price-label">Type</span>
                        <div className="modify-price-input-row">
                          <select
                            id={`modify-leg-${index}-right`}
                            className="modify-price-input"
                            value={leg.right}
                            onChange={(e) => handleLegChange(index, { right: normalizeLegRight(e.target.value) })}
                          >
                            <option value="C">CALL</option>
                            <option value="P">PUT</option>
                          </select>
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-strike`}>
                        <span className="modify-price-label">Strike</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-strike`}
                            className="modify-price-input"
                            type="number"
                            step="0.01"
                            min="0.01"
                            value={leg.strike}
                            onChange={(e) => handleLegChange(index, { strike: e.target.value })}
                          />
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-expiry`}>
                        <span className="modify-price-label">Expiry</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-expiry`}
                            className="modify-price-input"
                            type="date"
                            value={leg.expiry}
                            onChange={(e) => handleLegChange(index, { expiry: e.target.value })}
                          />
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-ratio`}>
                        <span className="modify-price-label">Ratio</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-ratio`}
                            className="modify-price-input"
                            type="number"
                            step="1"
                            min="1"
                            value={leg.ratio}
                            onChange={(e) => handleLegChange(index, { ratio: e.target.value })}
                          />
                        </div>
                      </label>
                    </div>
                  </section>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="modify-actions">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submitModify} disabled={!canSubmit}>
            {loading ? "Modifying..." : "Modify Order"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
