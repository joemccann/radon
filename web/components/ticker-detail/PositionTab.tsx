"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  fmtPrice,
  resolveEntryCost,
  resolveMarketValue,
  getAvgEntry,
  getMultiplier,
  legPriceKey,
  resolveRealtimePrice,
  resolveSpreadPriceData,
} from "@/lib/positionUtils";
import { fmtSignedPrice, fmtUsd, toneClass } from "@/lib/format";
import PositionTradeTicket from "./PositionTradeTicket";
import type { TradeTarget } from "@/lib/order/positionTrade";

type PositionTabProps = {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
  /** Portfolio snapshot for coverage-aware risk + the naked-short guard. */
  portfolio?: PortfolioData | null;
  /** Fired after a trade is placed so the parent can refresh. */
  onOrderPlaced?: () => void;
};

function isTradeableLeg(leg: PortfolioPosition["legs"][number]): boolean {
  return leg.strike != null && leg.type !== "Stock";
}

function LegsDisclosure({
  position,
  prices,
  onTradeLeg,
}: {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
  onTradeLeg: (index: number) => void;
}) {
  const ctx = useTickerDetailOptional();
  const focusedBookKey = ctx?.focusedBookKey ?? null;
  // Default expanded: the legs ARE the actionable surface for a combo.
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="position-legs">
      <button
        className="pos-legs-toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        <span className="pos-legs-title">Legs ({position.legs.length})</span>
      </button>
      {expanded && (
        <div className="pos-legs-table-wrap">
        <table className="pos-legs-table">
          <thead>
            <tr>
              <th>Direction</th>
              <th>Type</th>
              <th className="right">Strike</th>
              <th className="right">Qty</th>
              <th className="right">Entry</th>
              <th className="right">Market</th>
              <th className="right">Book</th>
              <th className="right">Trade</th>
            </tr>
          </thead>
          <tbody>
            {position.legs.map((leg, i) => {
              const key = legPriceKey(position.ticker, position.expiry, leg);
              const legPrice = key ? prices[key] : null;
              const legMktResolved = resolveRealtimePrice(
                legPrice,
                leg.market_price != null ? Math.abs(leg.market_price) : null,
                Boolean(leg.market_price_is_calculated),
              ).price;
              const legSign = leg.direction === "LONG" ? 1 : -1;
              const signedEntry = legSign * (Math.abs(leg.avg_cost) / (leg.type === "Stock" ? 1 : 100));
              const signedMarket = legMktResolved != null ? legSign * legMktResolved : null;
              return (
                <tr key={i}>
                  <td className={leg.direction === "LONG" ? "positive" : "negative"}>{leg.direction}</td>
                  <td>{leg.type}</td>
                  <td className="right">{leg.strike != null ? `$${leg.strike}` : "---"}</td>
                  <td className="right">{leg.contracts}</td>
                  <td className={`right ${toneClass(signedEntry) !== "neutral" ? toneClass(signedEntry) : ""}`}>
                    {fmtSignedPrice(signedEntry)}
                  </td>
                  <td className={`right ${signedMarket != null && toneClass(signedMarket) !== "neutral" ? toneClass(signedMarket) : ""}`}>
                    {fmtSignedPrice(signedMarket)}
                  </td>
                  <td className="right">
                    {isTradeableLeg(leg) && key && ctx ? (
                      <button
                        type="button"
                        className={`pos-leg-book${focusedBookKey === key ? " active" : ""}`}
                        aria-pressed={focusedBookKey === key}
                        title={focusedBookKey === key ? "Showing this leg's book — click to return" : "Show this leg's order book"}
                        onClick={() => ctx.setFocusedBookKey(focusedBookKey === key ? null : key)}
                        data-testid={`pos-leg-book-${i}`}
                      >
                        {focusedBookKey === key ? "BOOK ✓" : "BOOK"}
                      </button>
                    ) : (
                      "---"
                    )}
                  </td>
                  <td className="right">
                    {isTradeableLeg(leg) ? (
                      <button
                        type="button"
                        className="pos-leg-trade"
                        onClick={() => onTradeLeg(i)}
                        data-testid={`pos-leg-trade-${i}`}
                      >
                        {leg.direction === "LONG" ? "SELL" : "BUY"}
                      </button>
                    ) : (
                      "---"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}

export default function PositionTab({ position, prices, portfolio, onOrderPlaced }: PositionTabProps) {
  const [tradeTarget, setTradeTarget] = useState<TradeTarget | null>(null);
  const isCombo = position.structure_type !== "Stock" && position.legs.length > 1;
  const isSingleOption =
    position.structure_type !== "Stock" &&
    position.legs.length === 1 &&
    position.legs[0].strike != null &&
    position.legs[0].type !== "Stock";

  if (tradeTarget) {
    return (
      <PositionTradeTicket
        position={position}
        prices={prices}
        portfolio={portfolio}
        target={tradeTarget}
        onClose={() => setTradeTarget(null)}
        onOrderPlaced={onOrderPlaced}
      />
    );
  }

  return (
    <PositionView
      position={position}
      prices={prices}
      canTrade={isCombo || isSingleOption}
      isCombo={isCombo}
      onTradeCombo={() => setTradeTarget({ kind: "combo" })}
      onTradeLeg={(index) => setTradeTarget({ kind: "leg", index })}
    />
  );
}

function PositionView({
  position,
  prices,
  canTrade,
  isCombo,
  onTradeCombo,
  onTradeLeg,
}: {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
  canTrade: boolean;
  isCombo: boolean;
  onTradeCombo: () => void;
  onTradeLeg: (index: number) => void;
}) {
  const isStock = position.structure_type === "Stock";
  const spreadPriceData = useMemo(
    () => (!isStock && position.legs.length > 1 ? resolveSpreadPriceData(position.ticker, position, prices) : null),
    [isStock, position, prices],
  );

  const rtData = useMemo(() => {
    if (isStock) {
      const rt = prices[position.ticker];
      const last = rt?.last != null && rt.last > 0 ? rt.last : null;
      return last != null ? { mv: last * position.contracts, lastPrice: last } : null;
    }
    if (spreadPriceData?.last != null) {
      const mult = getMultiplier(position);
      return {
        mv: spreadPriceData.last * position.contracts * mult,
        lastPrice: spreadPriceData.last,
      };
    }
    // Options: compute from leg-level prices
    let rtMv = 0;
    for (const leg of position.legs) {
      const key = legPriceKey(position.ticker, position.expiry, leg);
      const lp = key ? prices[key] : null;
      if (!lp || lp.last == null || lp.last <= 0) return null;
      const sign = leg.direction === "LONG" ? 1 : -1;
      rtMv += sign * lp.last * leg.contracts * 100;
    }
    const mult = getMultiplier(position);
    return { mv: rtMv, lastPrice: rtMv / (position.contracts * mult) };
  }, [isStock, prices, position, spreadPriceData]);

  const entryCost = resolveEntryCost(position);
  const avgEntry = getAvgEntry(position);
  const mv = rtData?.mv ?? resolveMarketValue(position);
  const lastPrice = rtData?.lastPrice ?? (mv != null ? mv / (position.contracts * getMultiplier(position)) : null);
  const pnl = mv != null ? mv - entryCost : null;
  const pnlPct = pnl != null && entryCost !== 0 ? (pnl / Math.abs(entryCost)) * 100 : null;
  const lastPriceLabel = !isStock && position.legs.length > 1 ? "Mark Price" : "Last Price";

  return (
    <div className="position-tab">
      <div className="position-summary-grid">
        <div className="pos-stat">
          <span className="pos-stat-label">Structure</span>
          <span className="pos-stat-value">{position.structure}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Direction</span>
          <span className="pos-stat-value">{position.direction}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Qty</span>
          <span className="pos-stat-value">{position.contracts}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Entry Date</span>
          <span className="pos-stat-value">{position.entry_date && position.entry_date !== "unknown" ? position.entry_date : "---"}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Avg Entry</span>
          <span className={`pos-stat-value ${toneClass(avgEntry) !== "neutral" ? toneClass(avgEntry) : ""}`}>{fmtSignedPrice(avgEntry)}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">{lastPriceLabel}</span>
          <span className={`pos-stat-value ${lastPrice != null && toneClass(lastPrice) !== "neutral" ? toneClass(lastPrice) : ""}`}>
            {fmtSignedPrice(lastPrice)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Entry Cost</span>
          <span className="pos-stat-value">{fmtUsd(entryCost)}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Market Value</span>
          <span className="pos-stat-value">{mv != null ? fmtUsd(mv) : "---"}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Unrealized P&L</span>
          <span className={`pos-stat-value ${pnl != null ? (pnl >= 0 ? "positive" : "negative") : ""}`}>
            {pnl != null ? `${pnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(pnl))} (${pnlPct!.toFixed(1)}%)` : "---"}
          </span>
        </div>
        {position.expiry !== "N/A" && (
          <div className="pos-stat">
            <span className="pos-stat-label">Expiry</span>
            <span className="pos-stat-value">{position.expiry}</span>
          </div>
        )}
        {position.target != null && (
          <div className="pos-stat">
            <span className="pos-stat-label">Target</span>
            <span className="pos-stat-value">{fmtPrice(position.target)}</span>
          </div>
        )}
        {position.stop != null && (
          <div className="pos-stat">
            <span className="pos-stat-label">Stop</span>
            <span className="pos-stat-value">{fmtPrice(position.stop)}</span>
          </div>
        )}
      </div>

      {canTrade && (
        <div className="position-trade-actions">
          {isCombo ? (
            <button type="button" className="position-trade-cta" onClick={onTradeCombo} data-testid="pos-trade-combo">
              Close / Adjust Combo
            </button>
          ) : (
            <button type="button" className="position-trade-cta" onClick={() => onTradeLeg(0)} data-testid="pos-trade-single">
              {position.legs[0].direction === "LONG" ? "Sell to Close" : "Buy to Close"}
            </button>
          )}
          {isCombo && <span className="position-trade-actions-hint">or trade a single leg below</span>}
        </div>
      )}

      {position.legs.length > 1 && (
        <LegsDisclosure position={position} prices={prices} onTradeLeg={onTradeLeg} />
      )}
    </div>
  );
}
