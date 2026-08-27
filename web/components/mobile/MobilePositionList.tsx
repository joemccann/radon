"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import {
  fmtUsd,
  fmtPrice,
  resolveMarketValue,
  getInitialValue,
  getPnlDollars,
  getPnlPct,
  resolveReturnCapital,
  describeReturnCapital,
  getOptionDailyChg,
  getTodayPnlDollars,
  resolveRealtimePrice,
  resolveRealtimeMarketValue,
} from "@/lib/positionUtils";
import { resolveUnderlyingSpot } from "@/lib/impliedValue";
import TickerLink from "@/components/TickerLink";
import InstrumentDetailModal from "@/components/InstrumentDetailModal";
import Card from "./Card";
import MetricCell from "./MetricCell";

type MobilePositionListProps = {
  positions: PortfolioPosition[];
  prices?: Record<string, PriceData>;
  showExpiry?: boolean;
  portfolio?: PortfolioData | null;
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

/**
 * Entry cost carrying its credit/debit sign. A net-credit combo (SPCX ratio
 * risk reversal) reads NEGATIVE — the operator was PAID to open it. Never
 * `Math.abs` this: that was the 2026-08-07 mobile repeat of the EWY
 * credit-combo bug. Only "-" is prefixed; a debit shows bare, matching the
 * desktop Initial Value column.
 */
function fmtEntryCost(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value < 0 ? "-" : ""}${fmtUsd(Math.abs(value))}`;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

function PositionCard({ pos, prices, showExpiry, onLegClick }: { pos: PortfolioPosition; prices?: Record<string, PriceData>; showExpiry: boolean; onLegClick: (leg: PortfolioLeg, pos: PortfolioPosition) => void }) {
  const [expanded, setExpanded] = useState(false);

  const isStock = pos.structure_type === "Stock";
  const stockLast = prices?.[pos.ticker.toUpperCase()]?.last;
  const rtStockLast = stockLast != null && stockLast > 0 ? stockLast : null;
  // ONE market value per position, shared with getTodayPnlDollars and the
  // desktop table. A card-local walk of the same legs is a second market value,
  // and a same-day position's Today P&L then contradicts its own total — the
  // 2026-08-26 META card read +$1,097 total against -$103 today. Sign-aware
  // throughout: `pos.contracts` is a positive magnitude, so a SHORT carries the
  // direction sign or `mv - ec` becomes a SUM (the MU +$2.19M bug).
  const mv = resolveRealtimeMarketValue(pos, prices) ?? resolveMarketValue(pos);
  // `ec` drives the P&L math (signed, unchanged); `displayEc` is the signed
  // credit/debit the operator reads, via the same helper as the desktop
  // Initial Value column.
  const displayEc = getInitialValue(pos);
  const pnl = getPnlDollars(pos, mv);
  const pnlPct = getPnlPct(pos, mv);
  const returnTitle = describeReturnCapital(resolveReturnCapital(pos));
  const todayPnl = getTodayPnlDollars(pos, prices);
  const dailyChg = isStock ? null : getOptionDailyChg(pos, prices);

  // Underlying spot for option structures: where the stock is trading relative
  // to the strikes. VIX-style forward-priced indexes resolve off the per-expiry
  // futures curve, never cash spot (feedback_vix_option_underlying_forward).
  const underlyingSpot = isStock ? null : resolveUnderlyingSpot(pos.ticker, pos.expiry, prices);

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
              <div data-testid="mobile-position-pnl" style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontWeight: 600, fontSize: 14, color: pnlTone === "pos" ? "var(--positive)" : pnlTone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
                {fmtPnl(pnl)}
              </div>
              <div title={returnTitle} style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: pnlTone === "pos" ? "var(--positive)" : pnlTone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
                {pnlPct == null ? "N/A" : fmtPct(pnlPct)}
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
          <MetricCell label="EC" value={fmtEntryCost(displayEc)} />
          <MetricCell
            label="Today"
            value={fmtPnl(todayPnl)}
            tone={toneFor(todayPnl)}
          />
          <MetricCell
            label="Return %"
            value={pnlPct == null ? "N/A" : fmtPct(pnlPct)}
            tone={pnlTone}
            title={returnTitle}
          />
        </div>

        {underlyingSpot != null && (
          <div
            className="mobile-card__underlying"
            data-testid={`mobile-position-${pos.ticker}-underlying`}
          >
            <span className="mobile-card__underlying-k">{pos.ticker} SPOT</span>
            <span className="mobile-card__underlying-v">{fmtPrice(underlyingSpot)}</span>
          </div>
        )}

        {expanded ? (
          <div className="mobile-card__detail" data-testid={`mobile-position-${pos.ticker}-legs`}>
            {pos.legs.map((leg, idx) => (
              <LegLine key={idx} leg={leg} prices={prices} ticker={pos.ticker} expiry={pos.expiry} onClick={() => onLegClick(leg, pos)} />
            ))}
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function LegLine({ leg, prices, ticker, expiry, onClick }: { leg: PortfolioLeg; prices?: Record<string, PriceData>; ticker: string; expiry: string; onClick: () => void }) {
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
    <div
      className="mobile-card__leg-row mobile-card__leg-row--tappable"
      role="button"
      tabIndex={0}
      aria-label={`${ticker} ${description}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
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

export default function MobilePositionList({ positions, prices, showExpiry = true, portfolio = null }: MobilePositionListProps) {
  const sorted = useMemo(() => [...positions].sort((a, b) => a.ticker.localeCompare(b.ticker)), [positions]);

  const [activeInstrument, setActiveInstrument] = useState<{ leg: PortfolioLeg; ticker: string; expiry: string } | null>(null);

  const handleLegClick = useCallback((leg: PortfolioLeg, pos: PortfolioPosition) => {
    setActiveInstrument({ leg, ticker: pos.ticker, expiry: pos.expiry });
  }, []);

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
        <PositionCard key={pos.id} pos={pos} prices={prices} showExpiry={showExpiry} onLegClick={handleLegClick} />
      ))}
      {activeInstrument && prices && (
        <InstrumentDetailModal
          leg={activeInstrument.leg}
          ticker={activeInstrument.ticker}
          expiry={activeInstrument.expiry}
          prices={prices}
          portfolio={portfolio}
          onClose={() => setActiveInstrument(null)}
        />
      )}
    </div>
  );
}
