"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  fmtPrice,
  hasBlendedLegBasis,
  MIXED_BASIS_TITLE,
  resolveEntryCost,
  resolveMarketValue,
  resolveRealtimeMarketValue,
  getAvgEntry,
  getMultiplier,
  legPriceKey,
  resolveRealtimePrice,
  resolveSpreadPriceData,
  getPnlDollars,
  getPnlPct,
  resolveReturnCapital,
  describeReturnCapital,
} from "@/lib/positionUtils";
import { fmtSignedPrice, fmtUsd, toneClass } from "@/lib/format";
import PositionTradeTicket from "./PositionTradeTicket";
import SortTh from "../SortTh";
import { heldComboUnits, type TradeTarget } from "@/lib/order/positionTrade";
import { useSort } from "@/lib/useSort";

type LegSortKey = "direction" | "type" | "strike" | "qty" | "entry" | "market";
type IndexedLeg = { leg: PortfolioPosition["legs"][number]; index: number };

function legExtract(row: IndexedLeg, key: LegSortKey): string | number | null {
  switch (key) {
    case "direction": return row.leg.direction;
    case "type": return row.leg.type;
    case "strike": return row.leg.strike ?? null;
    case "qty": return row.leg.contracts;
    case "entry": return row.leg.avg_cost;
    case "market": return row.leg.market_price;
    default: return null;
  }
}

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
  const indexedLegs = useMemo(
    () => position.legs.map((leg, index) => ({ leg, index })),
    [position.legs],
  );
  const { sorted, sort, toggle } = useSort(indexedLegs, legExtract);

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
              <SortTh<LegSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<LegSortKey> label="Type" sortKey="type" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<LegSortKey> label="Strike" sortKey="strike" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<LegSortKey> label="Qty" sortKey="qty" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<LegSortKey> label="Entry" sortKey="entry" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<LegSortKey> label="Market" sortKey="market" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <th className="right">Book</th>
              <th className="right">Trade</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ leg, index: i }) => {
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
                        title={focusedBookKey === key ? "Showing this leg's book, click to return" : "Show this leg's order book"}
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
      // Sign-aware via the shared resolver: `position.contracts` is a positive
      // magnitude, so a SHORT read as `last * contracts` turns `mv - entryCost`
      // into a SUM and reports a phantom gain.
      const mv = resolveRealtimeMarketValue(position, prices);
      return mv != null && last != null ? { mv, lastPrice: last } : null;
    }
    // A multi-leg position quoted as a spread prices off that quote, not off
    // the legs — the combo's own market is the better mark.
    if (spreadPriceData?.last != null) {
      // The combo quote is the better MARK, but it is not a market value:
      // `getMultiplier(position)` collapses to 1 the moment any leg is stock,
      // so a covered call valued its short calls at 1x and disagreed with
      // PositionTable by the option notional. Market value stays with the one
      // shared per-leg walk; the spread quote supplies only the price. R-285.
      const rtMv = resolveRealtimeMarketValue(position, prices);
      if (rtMv != null) return { mv: rtMv, lastPrice: spreadPriceData.last };
    }
    // Options: ONE market value per position, shared with the table, the
    // mobile card and getTodayPnlDollars. A tab-local walk of the same legs is
    // a second market value for the same position.
    const rtMv = resolveRealtimeMarketValue(position, prices);
    if (rtMv == null) return null;
    const mult = getMultiplier(position);
    const units = heldComboUnits(position);
    return { mv: rtMv, lastPrice: units > 0 ? rtMv / (units * mult) : null };
  }, [isStock, prices, position, spreadPriceData]);

  const entryCost = resolveEntryCost(position);
  const avgEntry = getAvgEntry(position);
  // Legs on disagreeing bases have no aggregate basis to show (T-253).
  const blendedBasis = hasBlendedLegBasis(position);
  const mv = rtData?.mv ?? resolveMarketValue(position);
  const markUnits = isCombo ? heldComboUnits(position) : position.contracts;
  const lastPrice = rtData?.lastPrice ?? (mv != null && markUnits > 0 ? mv / (markUnits * getMultiplier(position)) : null);
  const pnl = getPnlDollars(position, mv);
  const pnlPct = getPnlPct(position, mv);
  const returnTitle = describeReturnCapital(resolveReturnCapital(position));
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
          <span data-testid="pos-stat-avg-entry" className={`pos-stat-value ${avgEntry != null && toneClass(avgEntry) !== "neutral" ? toneClass(avgEntry) : ""}`}>
            {avgEntry == null ? "---" : fmtSignedPrice(avgEntry)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">{lastPriceLabel}</span>
          <span className={`pos-stat-value ${lastPrice != null && toneClass(lastPrice) !== "neutral" ? toneClass(lastPrice) : ""}`}>
            {fmtSignedPrice(lastPrice)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Entry Cost</span>
          <span data-testid="pos-stat-entry-cost" className="pos-stat-value" title={blendedBasis ? MIXED_BASIS_TITLE : undefined}>
            {entryCost == null ? "---" : fmtUsd(entryCost)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Market Value</span>
          <span className="pos-stat-value">{mv != null ? fmtUsd(mv) : "---"}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Unrealized P&L / Return</span>
          <span
            className={`pos-stat-value ${pnl != null ? (pnl >= 0 ? "positive" : "negative") : ""}`}
            title={returnTitle}
          >
            {pnl != null
              ? `${pnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(pnl))}${pnlPct != null ? ` (${pnlPct.toFixed(1)}%)` : " (Return N/A)"}`
              : "---"}
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
