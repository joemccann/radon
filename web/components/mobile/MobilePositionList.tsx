"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import {
  fmtUsd,
  fmtPrice,
  resolveMarketValue,
  resolveEntryCost,
  getMultiplier,
  getOptionDailyChg,
  getTodayPnlDollars,
  resolveRealtimePrice,
} from "@/lib/positionUtils";
import TickerLink from "@/components/TickerLink";
import Card from "./Card";

type MobilePositionListProps = {
  positions: PortfolioPosition[];
  prices?: Record<string, PriceData>;
  showExpiry?: boolean;
};

type ToneKey = "positive" | "negative" | "muted";

function toneFor(value: number | null | undefined): ToneKey {
  if (value == null || !Number.isFinite(value)) return "muted";
  if (value > 0) return "positive";
  if (value < 0) return "negative";
  return "muted";
}

function fmtPnl(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${fmtUsd(Math.abs(value))}`;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

function getOptionRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") return null;
  let total = 0;
  let any = false;
  for (const leg of pos.legs) {
    if (leg.type === "Stock") {
      const stockKey = pos.ticker.toUpperCase();
      const stockLast = prices?.[stockKey]?.last;
      if (stockLast != null && stockLast > 0) {
        total += (leg.direction === "LONG" ? 1 : -1) * stockLast * leg.contracts;
        any = true;
      }
      continue;
    }
    if (leg.strike == null || !pos.expiry) continue;
    const expiry = pos.expiry.replace(/-/g, "");
    const right = leg.type === "Call" ? "C" : "P";
    const k = optionKey({ symbol: pos.ticker.toUpperCase(), expiry, strike: leg.strike, right });
    const last = prices?.[k]?.last;
    if (last != null && last > 0) {
      total += (leg.direction === "LONG" ? 1 : -1) * last * leg.contracts * 100;
      any = true;
    }
  }
  return any ? total : null;
}

function PositionCard({ pos, prices, showExpiry }: { pos: PortfolioPosition; prices?: Record<string, PriceData>; showExpiry: boolean }) {
  const [expanded, setExpanded] = useState(false);

  const isStock = pos.structure_type === "Stock";
  const stockLast = prices?.[pos.ticker.toUpperCase()]?.last;
  const rtStockLast = stockLast != null && stockLast > 0 ? stockLast : null;
  const optRtMv = getOptionRtMv(pos, prices);
  const mv = isStock && rtStockLast != null ? rtStockLast * pos.contracts : optRtMv ?? resolveMarketValue(pos);
  const ec = resolveEntryCost(pos);
  const pnl = mv != null ? mv - ec : null;
  const pnlPct = mv != null && ec !== 0 ? (pnl! / Math.abs(ec)) * 100 : null;
  const todayPnl = getTodayPnlDollars(pos, prices);
  const dailyChg = isStock ? null : getOptionDailyChg(pos, prices);

  const pnlTone = toneFor(pnl);
  const cardTone = pnlTone === "positive" ? "positive" : pnlTone === "negative" ? "negative" : "default";

  const handleToggle = () => setExpanded((prev) => !prev);

  return (
    <Card
      onClick={handleToggle}
      tone={cardTone}
      testId={`mobile-position-${pos.ticker}`}
      ariaLabel={`${pos.ticker} ${pos.structure}`}
    >
      <div className="mobile-card__title-row">
        <div className="mobile-card__title">
          <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
            <TickerLink ticker={pos.ticker} positionId={pos.id} />
          </span>
          <span className="mobile-card__subtitle">{pos.structure}</span>
        </div>
        <div className={`mobile-card__pnl mobile-card-row__value--${pnlTone}`}>
          <div className="mobile-card__pnl-value">{fmtPnl(pnl)}</div>
          <div className="mobile-card__pnl-pct">{fmtPct(pnlPct)}</div>
        </div>
      </div>

      <div className="mobile-card__metrics">
        <div className="mobile-card__metric">
          <span className="mobile-card__metric-label">MV</span>
          <span className="mobile-card__metric-value">{mv != null ? fmtUsd(mv) : "—"}</span>
        </div>
        <div className="mobile-card__metric">
          <span className="mobile-card__metric-label">EC</span>
          <span className="mobile-card__metric-value">{fmtUsd(Math.abs(ec))}</span>
        </div>
        <div className="mobile-card__metric">
          <span className="mobile-card__metric-label">Today</span>
          <span className={`mobile-card__metric-value mobile-card__metric-value--${toneFor(todayPnl)}`}>
            {fmtPnl(todayPnl)}
          </span>
        </div>
      </div>

      <div className="mobile-card__chevron-row">
        <span className="mobile-card__subtitle">
          {pos.contracts}x {pos.direction}
          {showExpiry && pos.expiry && pos.expiry !== "N/A" ? ` · ${pos.expiry}` : ""}
          {dailyChg != null ? ` · Day ${fmtPct(dailyChg)}` : ""}
        </span>
        <span className="mobile-card__chevron" aria-hidden>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>

      {expanded ? (
        <div className="mobile-card__detail" data-testid={`mobile-position-${pos.ticker}-legs`}>
          {pos.legs.map((leg, idx) => (
            <LegLine key={idx} leg={leg} prices={prices} ticker={pos.ticker} expiry={pos.expiry} />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function LegLine({ leg, prices, ticker, expiry }: { leg: PortfolioLeg; prices?: Record<string, PriceData>; ticker: string; expiry: string }) {
  let realtimeLeg: PriceData | null = null;
  if (leg.type !== "Stock" && leg.strike != null && expiry) {
    const k = optionKey({
      symbol: ticker.toUpperCase(),
      expiry: expiry.replace(/-/g, ""),
      strike: leg.strike,
      right: leg.type === "Call" ? "C" : "P",
    });
    realtimeLeg = prices?.[k] ?? null;
  } else if (leg.type === "Stock") {
    realtimeLeg = prices?.[ticker.toUpperCase()] ?? null;
  }
  const resolved = resolveRealtimePrice(
    realtimeLeg,
    leg.market_price != null ? Math.abs(leg.market_price) : null,
    Boolean(leg.market_price_is_calculated),
  );
  const marketPrice = resolved.price;
  const mult = leg.type === "Stock" ? 1 : 100;
  const legMv = marketPrice != null ? marketPrice * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;
  const tone = toneFor(legPnl);

  const description = `${leg.direction} ${leg.contracts}x ${leg.type}${leg.strike ? ` $${leg.strike}` : ""}`;

  return (
    <div className="mobile-card__leg-row">
      <div className="mobile-card__leg-desc">{description}</div>
      <div className="mobile-card__leg-metrics">
        <span className="mobile-card__leg-meta">{marketPrice != null ? fmtPrice(marketPrice) : "—"}</span>
        <span className={`mobile-card__leg-pnl mobile-card-row__value--${tone}`}>{fmtPnl(legPnl)}</span>
      </div>
    </div>
  );
}

export default function MobilePositionList({ positions, prices, showExpiry = true }: MobilePositionListProps) {
  const sorted = useMemo(() => [...positions].sort((a, b) => a.ticker.localeCompare(b.ticker)), [positions]);

  if (sorted.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-position-list-empty">
        <span>No positions to display.</span>
      </div>
    );
  }

  return (
    <div className="mobile-card-list" data-testid="mobile-position-list">
      {sorted.map((pos) => (
        <PositionCard key={pos.id} pos={pos} prices={prices} showExpiry={showExpiry} />
      ))}
    </div>
  );
}
