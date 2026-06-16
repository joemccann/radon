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
  getOptionDailyChg,
  getTodayPnlDollars,
  resolveRealtimePrice,
} from "@/lib/positionUtils";
import TickerLink from "@/components/TickerLink";
import Card from "./Card";
import MetricCell from "./MetricCell";

type MobilePositionListProps = {
  positions: PortfolioPosition[];
  prices?: Record<string, PriceData>;
  showExpiry?: boolean;
};

type ToneKey = "pos" | "neg" | "mut";

function toneFor(value: number | null | undefined): ToneKey {
  if (value == null || !Number.isFinite(value)) return "mut";
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "mut";
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
  const cardTone = pnlTone === "pos" ? "positive" : pnlTone === "neg" ? "negative" : "default";

  const handleToggle = () => setExpanded((prev) => !prev);

  // Build compact subtitle: contracts x direction [· expiry] [· Day +x%]
  const subtitleParts: string[] = [`${pos.contracts}x ${pos.direction}`];
  if (showExpiry && pos.expiry && pos.expiry !== "N/A") subtitleParts.push(pos.expiry);
  if (dailyChg != null) subtitleParts.push(`Day ${fmtPct(dailyChg)}`);

  return (
    <div className="m-card-press" data-testid={`mobile-position-${pos.ticker}`}>
      <Card
        onClick={handleToggle}
        tone={cardTone}
        ariaLabel={`${pos.ticker} ${pos.structure}`}
      >
        {/* Title row: ticker + structure subtitle + P&L + chevron */}
        <div className="mobile-card__title-row">
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                <TickerLink ticker={pos.ticker} positionId={pos.id} />
              </span>
              <span className="mobile-card__subtitle" style={{ fontSize: 11 }}>{subtitleParts.join(" · ")}</span>
            </div>
            <span className="mobile-card__subtitle" style={{ fontSize: 11 }}>{pos.structure}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontWeight: 600, fontSize: 14, color: pnlTone === "pos" ? "var(--positive)" : pnlTone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
                {fmtPnl(pnl)}
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: pnlTone === "pos" ? "var(--positive)" : pnlTone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
                {fmtPct(pnlPct)}
              </div>
            </div>
            <span className="mobile-card__chevron" aria-hidden>
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </div>
        </div>

        {/* 2x2 MetricCell grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 12px", marginTop: 8 }}>
          <MetricCell label="MV" value={mv != null ? fmtUsd(mv) : "—"} />
          <MetricCell label="EC" value={fmtUsd(Math.abs(ec))} />
          <MetricCell
            label="Today"
            value={fmtPnl(todayPnl)}
            tone={toneFor(todayPnl)}
          />
          <MetricCell
            label="P&L %"
            value={fmtPct(pnlPct)}
            tone={pnlTone}
          />
        </div>

        {expanded ? (
          <div className="mobile-card__detail" data-testid={`mobile-position-${pos.ticker}-legs`}>
            {pos.legs.map((leg, idx) => (
              <LegLine key={idx} leg={leg} prices={prices} ticker={pos.ticker} expiry={pos.expiry} />
            ))}
          </div>
        ) : null}
      </Card>
    </div>
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
        <span style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontSize: 12, color: tone === "pos" ? "var(--positive)" : tone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
          {fmtPnl(legPnl)}
        </span>
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
