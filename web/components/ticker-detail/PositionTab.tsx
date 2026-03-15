"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  fmtPrice,
  fmtUsd,
  resolveEntryCost,
  resolveMarketValue,
  getAvgEntry,
  getMultiplier,
  legPriceKey,
} from "@/lib/positionUtils";

type PositionTabProps = {
  position: PortfolioPosition;
  prices: Record<string, PriceData>;
};

function LegsDisclosure({ position, prices }: { position: PortfolioPosition; prices: Record<string, PriceData> }) {
  const [expanded, setExpanded] = useState(false);

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
        <table className="pos-legs-table">
          <thead>
            <tr>
              <th>Direction</th>
              <th>Type</th>
              <th className="right">Strike</th>
              <th className="right">Qty</th>
              <th className="right">Entry</th>
              <th className="right">Market</th>
            </tr>
          </thead>
          <tbody>
            {position.legs.map((leg, i) => {
              const key = legPriceKey(position.ticker, position.expiry, leg);
              const legPrice = key ? prices[key] : null;
              const legMkt = legPrice?.last != null && legPrice.last > 0 ? legPrice.last : (leg.market_price != null ? Math.abs(leg.market_price) : null);
              return (
                <tr key={i}>
                  <td>{leg.direction}</td>
                  <td>{leg.type}</td>
                  <td className="right">{leg.strike != null ? `$${leg.strike}` : "---"}</td>
                  <td className="right">{leg.contracts}</td>
                  <td className="right">{fmtPrice(Math.abs(leg.avg_cost) / (leg.type === "Stock" ? 1 : 100))}</td>
                  <td className="right">{legMkt != null ? fmtPrice(legMkt) : "---"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function PositionTab({ position, prices }: PositionTabProps) {
  const isStock = position.structure_type === "Stock";

  const rtData = useMemo(() => {
    if (isStock) {
      const rt = prices[position.ticker];
      const last = rt?.last != null && rt.last > 0 ? rt.last : null;
      return last != null ? { mv: last * position.contracts, lastPrice: last } : null;
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
  }, [isStock, prices, position]);

  const entryCost = resolveEntryCost(position);
  const avgEntry = getAvgEntry(position);
  const mv = rtData?.mv ?? resolveMarketValue(position);
  const lastPrice = rtData?.lastPrice ?? (mv != null ? mv / (position.contracts * getMultiplier(position)) : null);
  const pnl = mv != null ? mv - entryCost : null;
  const pnlPct = pnl != null && entryCost !== 0 ? (pnl / Math.abs(entryCost)) * 100 : null;

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
          <span className="pos-stat-value">{position.entry_date || "---"}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Avg Entry</span>
          <span className="pos-stat-value">{fmtPrice(avgEntry)}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Last Price</span>
          <span className="pos-stat-value">{lastPrice != null ? fmtPrice(lastPrice) : "---"}</span>
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
            {pnl != null ? `${pnl >= 0 ? "+" : ""}${fmtUsd(Math.abs(pnl))} (${pnlPct!.toFixed(1)}%)` : "---"}
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

      {position.legs.length > 1 && (
        <LegsDisclosure position={position} prices={prices} />
      )}
    </div>
  );
}
