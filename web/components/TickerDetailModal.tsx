"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { OpenOrder, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { fmtPrice, legPriceKey } from "@/lib/positionUtils";
import Modal from "./Modal";
import PositionTab from "./ticker-detail/PositionTab";
import OrderTab from "./ticker-detail/OrderTab";
import NewsTab from "./ticker-detail/NewsTab";
import RatingsTab from "./ticker-detail/RatingsTab";
import SeasonalityTab from "./ticker-detail/SeasonalityTab";
import CompanyTab from "./ticker-detail/CompanyTab";

type TabId = "company" | "position" | "order" | "news" | "ratings" | "seasonality";

function PriceBar({ priceData, label }: { priceData: PriceData | null; label?: string }) {
  if (!priceData) {
    return <div className="price-bar price-bar-empty">No real-time data</div>;
  }

  const { bid, ask, last, volume, close } = priceData;
  const mid = bid != null && ask != null ? (bid + ask) / 2 : null;
  const spread = bid != null && ask != null ? ask - bid : null;
  const dayChange = last != null && last > 0 && close != null && close > 0
    ? ((last - close) / close) * 100
    : null;

  return (
    <div className="price-bar">
      {label && (
        <div className="price-bar-item" style={{ gridColumn: "1 / -1" }}>
          <span className="price-bar-label">{label}</span>
        </div>
      )}
      <div className="price-bar-item">
        <span className="price-bar-label">BID</span>
        <span className="price-bar-value">{bid != null ? fmtPrice(bid) : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">ASK</span>
        <span className="price-bar-value">{ask != null ? fmtPrice(ask) : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">MID</span>
        <span className="price-bar-value">{mid != null ? fmtPrice(mid) : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">SPREAD</span>
        <span className="price-bar-value">{spread != null ? fmtPrice(spread) : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">LAST</span>
        <span className="price-bar-value">{last != null ? fmtPrice(last) : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">VOLUME</span>
        <span className="price-bar-value">{volume != null ? volume.toLocaleString() : "---"}</span>
      </div>
      <div className="price-bar-item">
        <span className="price-bar-label">DAY</span>
        <span className={`price-bar-value ${dayChange != null ? (dayChange >= 0 ? "positive" : "negative") : ""}`}>
          {dayChange != null ? (
            <>
              {dayChange >= 0 ? "+" : ""}{dayChange.toFixed(2)}%
              {dayChange > 0 && <ArrowUp size={10} className="price-trend-icon price-trend-up" />}
              {dayChange < 0 && <ArrowDown size={10} className="price-trend-icon price-trend-down" />}
            </>
          ) : "---"}
        </span>
      </div>
    </div>
  );
}

/**
 * Resolve the best price data for the PriceBar.
 * - Stock positions → underlying ticker price
 * - Single-leg option → option contract price (bid/ask from WS)
 * - Multi-leg → underlying (option-level net pricing not available via single key)
 * - No position → underlying ticker price
 */
function resolvePriceBar(
  ticker: string,
  position: PortfolioPosition | null,
  prices: Record<string, PriceData>,
): { priceData: PriceData | null; label?: string } {
  if (!position || position.structure_type === "Stock") {
    return { priceData: prices[ticker] ?? null };
  }

  // Single-leg option: use option-level prices
  if (position.legs.length === 1) {
    const leg = position.legs[0];
    const key = legPriceKey(ticker, position.expiry, leg);
    if (key && prices[key]) {
      const strike = leg.strike ? `$${leg.strike}` : "";
      const type = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : "";
      return {
        priceData: prices[key],
        label: `${ticker} ${position.expiry} ${strike} ${type}`,
      };
    }
  }

  // Multi-leg: fall back to underlying
  return { priceData: prices[ticker] ?? null, label: `${ticker} (underlying)` };
}

export default function TickerDetailModal() {
  const { activeTicker, activePositionId, closeTicker, getPrices, getPortfolio, getOrders } = useTickerDetail();
  const [activeTab, setActiveTab] = useState<TabId | null>(null);

  const prices = getPrices();
  const portfolio = getPortfolio();
  const ordersData = getOrders();

  const position: PortfolioPosition | null = useMemo(() => {
    if (!activeTicker || !portfolio) return null;
    // If a specific position ID was provided (e.g. duplicate tickers), use it
    if (activePositionId != null) {
      return portfolio.positions.find((p) => p.id === activePositionId) ?? null;
    }
    return portfolio.positions.find((p) => p.ticker === activeTicker) ?? null;
  }, [activeTicker, activePositionId, portfolio]);

  // Find open orders for this ticker
  const tickerOrders: OpenOrder[] = useMemo(() => {
    if (!activeTicker || !ordersData) return [];
    return ordersData.open_orders.filter((o) => o.contract.symbol === activeTicker);
  }, [activeTicker, ordersData]);

  // Resolve price bar data (option-level for single-leg options)
  const { priceData, label: priceLabel } = useMemo(
    () => resolvePriceBar(activeTicker ?? "", position, prices),
    [activeTicker, position, prices],
  );

  // Reset tab when ticker changes
  useEffect(() => {
    setActiveTab(null);
  }, [activeTicker]);

  // Default tab: always company
  const resolvedTab = activeTab ?? "company";

  if (!activeTicker) return null;

  const tabs: { id: TabId; label: string; hidden?: boolean }[] = [
    { id: "company", label: "Company" },
    { id: "position", label: "Position", hidden: !position },
    { id: "order", label: tickerOrders.length > 0 ? `Orders (${tickerOrders.length})` : "Order" },
    { id: "news", label: "News" },
    { id: "ratings", label: "Ratings" },
    { id: "seasonality", label: "Seasonal" },
  ];

  const positionSummary = position
    ? `${position.direction} ${position.contracts}x ${position.structure}`
    : "No Position";

  return (
    <Modal open={true} onClose={closeTicker} title={activeTicker} className="ticker-detail-modal">
      <div className="ticker-detail-content">
        {/* Position summary pill */}
        <div className="ticker-detail-header">
          <span className={`pill ${position ? "defined" : "neutral"}`} style={{ fontSize: "9px" }}>
            {positionSummary}
          </span>
        </div>

        {/* Price bar */}
        <PriceBar priceData={priceData} label={priceLabel} />

        {/* Tab bar */}
        <div className="ticker-tabs">
          {tabs.filter((t) => !t.hidden).map((tab) => (
            <button
              key={tab.id}
              className={`ticker-tab ${resolvedTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="ticker-tab-content">
          {resolvedTab === "company" && (
            <CompanyTab ticker={activeTicker} active={resolvedTab === "company"} />
          )}
          {resolvedTab === "position" && position && (
            <PositionTab position={position} prices={prices} />
          )}
          {resolvedTab === "order" && (
            <OrderTab
              ticker={activeTicker}
              position={position}
              prices={prices}
              openOrders={tickerOrders}
              tickerPriceData={priceData}
            />
          )}
          {resolvedTab === "news" && (
            <NewsTab ticker={activeTicker} active={resolvedTab === "news"} />
          )}
          {resolvedTab === "ratings" && (
            <RatingsTab
              ticker={activeTicker}
              active={resolvedTab === "ratings"}
              currentPrice={prices[activeTicker]?.last ?? priceData?.last}
            />
          )}
          {resolvedTab === "seasonality" && (
            <SeasonalityTab
              ticker={activeTicker}
              active={resolvedTab === "seasonality"}
            />
          )}
        </div>
      </div>
    </Modal>
  );
}
