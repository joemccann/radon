"use client";

import { useMemo } from "react";
import type { OpenOrder, PortfolioPosition, PortfolioData, OrdersData } from "@/lib/types";
import type { PriceData, FundamentalsData } from "@/lib/pricesProtocol";
import { legPriceKey, resolveSpreadPriceData } from "@/lib/positionUtils";
import PriceChart from "./PriceChart";
import PositionTab from "./ticker-detail/PositionTab";
import OrderTab from "./ticker-detail/OrderTab";
import NewsTab from "./ticker-detail/NewsTab";
import RatingsTab from "./ticker-detail/RatingsTab";
import SeasonalityTab from "./ticker-detail/SeasonalityTab";
import CompanyTab from "./ticker-detail/CompanyTab";
import { TickerQuoteTelemetry } from "./QuoteTelemetry";
import BookTab from "./ticker-detail/BookTab";
import OptionsChainTab from "./ticker-detail/OptionsChainTab";

type TabId = "company" | "book" | "chain" | "position" | "order" | "news" | "ratings" | "seasonality";

/**
 * Resolve the best price data for the shared ticker quote telemetry wrapper.
 * - Stock positions → underlying ticker price
 * - Single-leg option → option contract price (bid/ask from WS)
 * - Multi-leg → net spread price computed from per-leg WS bid/ask (falls back to underlying)
 * - No position → underlying ticker price
 */
function resolveTickerQuoteTelemetry(
  ticker: string,
  position: PortfolioPosition | null,
  prices: Record<string, PriceData>,
): { priceData: PriceData | null; label?: string; priceKey?: string } {
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
        priceKey: key,
        label: `${ticker} ${position.expiry} ${strike} ${type}`,
      };
    }
  }

  // Multi-leg: compute net spread price from per-leg WS prices
  const spreadData = resolveSpreadPriceData(ticker, position, prices);
  if (spreadData) {
    return { priceData: spreadData, label: `${ticker} ${position.structure}` };
  }

  // Fallback to underlying if leg prices unavailable
  return { priceData: prices[ticker] ?? null, label: `${ticker} (underlying)` };
}

export type TickerDetailContentProps = {
  ticker: string;
  positionId?: number | null;
  activeTab: string;
  onTabChange: (tab: string) => void;
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  portfolio: PortfolioData | null;
  orders: OrdersData | null;
  theme: "dark" | "light";
};

export default function TickerDetailContent({
  ticker,
  positionId,
  activeTab,
  onTabChange,
  prices,
  fundamentals,
  portfolio,
  orders,
  theme,
}: TickerDetailContentProps) {
  const position: PortfolioPosition | null = useMemo(() => {
    if (!portfolio) return null;
    if (positionId != null) {
      return portfolio.positions.find((p) => p.id === positionId) ?? null;
    }
    return portfolio.positions.find((p) => p.ticker === ticker) ?? null;
  }, [ticker, positionId, portfolio]);

  const tickerOrders: OpenOrder[] = useMemo(() => {
    if (!orders) return [];
    return orders.open_orders.filter((o) => o.contract.symbol === ticker);
  }, [ticker, orders]);

  const { priceData, label: priceLabel, priceKey: chartPriceKey } = useMemo(
    () => resolveTickerQuoteTelemetry(ticker, position, prices),
    [ticker, position, prices],
  );

  const resolvedTab: TabId = (["company", "book", "chain", "position", "order", "news", "ratings", "seasonality"] as TabId[]).includes(activeTab as TabId)
    ? (activeTab as TabId)
    : "company";

  const tabs: { id: TabId; label: string; hidden?: boolean }[] = [
    { id: "company", label: "Company" },
    { id: "book", label: "Book" },
    { id: "chain", label: "Chain" },
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
    <div className="ticker-detail-content">
      {/* Hero row: telemetry (left) + chart (right) */}
      <div className="ticker-detail-hero">
        <div className="ticker-detail-hero-left">
          <div className="ticker-detail-header">
            <span className={`pill ${position ? "defined" : "neutral"}`} style={{ fontSize: "9px" }}>
              {positionSummary}
            </span>
          </div>
          <TickerQuoteTelemetry priceData={priceData} label={priceLabel} />
        </div>
        <div className="ticker-detail-hero-right">
          <PriceChart ticker={ticker} prices={prices} priceKey={chartPriceKey} theme={theme} />
        </div>
      </div>

      {/* Tab bar */}
      <div className="ticker-tabs">
        {tabs.filter((t) => !t.hidden).map((tab) => (
          <button
            key={tab.id}
            className={`ticker-tab ${resolvedTab === tab.id ? "active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="ticker-tab-content">
        {resolvedTab === "company" && (
          <CompanyTab ticker={ticker} active={resolvedTab === "company"} priceData={prices[ticker] ?? null} fundamentals={fundamentals[ticker] ?? null} />
        )}
        {resolvedTab === "book" && (
          <BookTab
            ticker={ticker}
            position={position}
            prices={prices}
            openOrders={tickerOrders}
            tickerPriceData={priceData}
          />
        )}
        {resolvedTab === "chain" && (
          <OptionsChainTab
            ticker={ticker}
            prices={prices}
            tickerPriceData={prices[ticker] ?? null}
          />
        )}
        {resolvedTab === "position" && position && (
          <PositionTab position={position} prices={prices} />
        )}
        {resolvedTab === "order" && (
          <OrderTab
            ticker={ticker}
            position={position}
            portfolio={portfolio}
            prices={prices}
            openOrders={tickerOrders}
            tickerPriceData={priceData}
          />
        )}
        {resolvedTab === "news" && (
          <NewsTab ticker={ticker} active={resolvedTab === "news"} />
        )}
        {resolvedTab === "ratings" && (
          <RatingsTab
            ticker={ticker}
            active={resolvedTab === "ratings"}
            currentPrice={prices[ticker]?.last ?? priceData?.last}
          />
        )}
        {resolvedTab === "seasonality" && (
          <SeasonalityTab
            ticker={ticker}
            active={resolvedTab === "seasonality"}
          />
        )}
      </div>
    </div>
  );
}
