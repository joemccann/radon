"use client";

import { useCallback, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  fmtUsd,
  fmtPrice,
  fmtPriceOrCalculated,
  resolveMarketValue,
  hasBlendedLegBasis,
  MIXED_BASIS_TITLE,
  getAvgEntry,
  getInitialValue,
  getPnlDollars,
  getPnlPct,
  resolveReturnCapital,
  describeReturnCapital,
  getMultiplier,
  getLastPrice,
  getLastPriceIsCalculated,
  legPriceKey,
  getOptionDailyChg,
  getStockDailyChg,
  getTodayPnlDollars,
  resolveRealtimePrice,
  resolveRealtimeMarketValue,
} from "@/lib/positionUtils";
import { computeLegImpliedValue, computePositionImpliedValue, resolveUnderlyingSpot } from "@/lib/impliedValue";
import { useRiskFreeRateState } from "@/lib/useRiskFreeRate";
import { usePriceDirection } from "@/lib/usePriceDirection";
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

function toneColor(tone: ToneKey): string {
  return tone === "pos" ? "var(--positive)" : tone === "neg" ? "var(--negative)" : "var(--text-muted)";
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
function fmtEntryCost(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value < 0 ? "-" : ""}${fmtUsd(Math.abs(value))}`;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

/** Day Chg rendered like the desktop column: sign on positives, two decimals. */
function fmtDayChg(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** `fmtUsd` keeps magnitudes; leg cells need the sign to match the header. */
function fmtSignedUsd(value: number): string {
  return `${value < 0 ? "-" : ""}${fmtUsd(Math.abs(value))}`;
}

/** Per-contract price from a market value — the desktop `finiteOrNull` guard.
 *  `pos.contracts` has no positivity constraint, so a row flattened mid-sync
 *  must not reach fmtPrice as Infinity/NaN (R-270). */
function perContractOrNull(mv: number | null | undefined, pos: PortfolioPosition): number | null {
  const divisor = pos.contracts * getMultiplier(pos);
  if (mv == null || !Number.isFinite(divisor) || divisor === 0) return null;
  const value = mv / divisor;
  return Number.isFinite(value) ? value : null;
}

function riskPillClass(pos: PortfolioPosition): string {
  return pos.risk_profile === "defined" ? "defined" : pos.risk_profile === "equity" ? "neutral" : "undefined";
}

function TrendArrow({ direction }: { direction: "up" | "down" | null }) {
  if (direction === "up") return <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />;
  if (direction === "down") return <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />;
  return null;
}

function PositionCard({ pos, prices, showExpiry, riskFreeRate, onLegClick }: { pos: PortfolioPosition; prices?: Record<string, PriceData>; showExpiry: boolean; riskFreeRate: number | null; onLegClick: (leg: PortfolioLeg, pos: PortfolioPosition) => void }) {
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
  // `displayEc` is the signed credit/debit the operator reads, via the same
  // helper as the desktop Initial Value column.
  const displayEc = getInitialValue(pos);
  const pnl = getPnlDollars(pos, mv);
  const pnlPct = getPnlPct(pos, mv);
  const returnTitle = describeReturnCapital(resolveReturnCapital(pos));
  const todayPnl = getTodayPnlDollars(pos, prices);
  // Stocks AND options: same helpers as the desktop Day Chg column.
  const dailyChg = isStock ? getStockDailyChg(pos, prices) : getOptionDailyChg(pos, prices);
  const avgEntry = getAvgEntry(pos);
  // Legs on disagreeing bases have no aggregate basis to show (T-253).
  const blendedBasis = hasBlendedLegBasis(pos);

  // Underlying spot for option structures: where the stock is trading relative
  // to the strikes. VIX-style forward-priced indexes resolve off the per-expiry
  // futures curve, never cash spot (feedback_vix_option_underlying_forward).
  const underlyingSpot = isStock ? null : resolveUnderlyingSpot(pos.ticker, pos.expiry, prices);

  // Position-level last price — the desktop PositionRow resolution, verbatim:
  // stock takes the live tick, an option structure whose legs ALL resolve
  // derives per-contract from the one shared market value, anything else falls
  // back to the synced mark. The walk below reads flags only — it accumulates
  // no market value of its own (market-value-single-source contract).
  const optionsResolved = useMemo(() => {
    if (isStock) return null;
    let priceIsCalculated = false;
    for (const leg of pos.legs) {
      const key = legPriceKey(pos.ticker, pos.expiry, leg);
      const lp = key && prices ? prices[key] : null;
      const resolved = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated));
      if (resolved.price == null) return null;
      priceIsCalculated = priceIsCalculated || resolved.isCalculated;
    }
    return { priceIsCalculated };
  }, [isStock, prices, pos.legs, pos.ticker, pos.expiry]);

  const lastPrice = isStock
    ? rtStockLast ?? getLastPrice(pos)
    : optionsResolved
      ? perContractOrNull(mv, pos)
      : getLastPrice(pos);
  const lastPriceIsCalculated = isStock
    ? rtStockLast != null
      ? false
      : getLastPriceIsCalculated(pos)
    : optionsResolved
      ? optionsResolved.priceIsCalculated
      : getLastPriceIsCalculated(pos);
  const { direction: priceDirection } = usePriceDirection(lastPrice);

  // Black-Scholes implied per-share, signed-summed across legs. null for
  // stocks or until FRED resolves the risk-free rate (R-229: pricing off a
  // defaulted r = 0 is a silently wrong number, not a conservative one).
  const impliedNet = useMemo(() => {
    if (isStock || !prices || riskFreeRate == null) return null;
    return computePositionImpliedValue(pos, prices, { riskFreeRate }).netPerContract;
  }, [isStock, pos, prices, riskFreeRate]);

  const pnlTone = toneFor(pnl);
  const cardTone = pnlTone === "pos" ? "positive" : pnlTone === "neg" ? "negative" : "default";

  const handleToggle = () => setExpanded((prev) => !prev);

  const expiryShown = showExpiry && pos.expiry && pos.expiry !== "N/A";

  return (
    <div className="m-card-press" data-testid={`mobile-position-${pos.ticker}`}>
      <Card
        onClick={handleToggle}
        tone={cardTone}
        ariaLabel={`${pos.ticker} ${pos.structure}`}
        ariaExpanded={expanded}
      >
        {/* Title row: ticker + direction pill + qty | P&L + chevron */}
        <div className="mobile-card__title-row">
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                <TickerLink ticker={pos.ticker} positionId={pos.id} />
              </span>
              <span className={`pill ${riskPillClass(pos)}`}>{pos.direction}</span>
              <span className="mobile-card__subtitle" data-testid={`mobile-position-${pos.ticker}-qty`} style={{ fontSize: "var(--text-meta)" }}>
                {pos.contracts}x
              </span>
            </div>
            <span className="mobile-card__subtitle" style={{ fontSize: "var(--text-meta)" }}>
              {pos.structure}
              {/* nowrap keeps the date from breaking mid-token at the hyphens */}
              {expiryShown ? <> · <span style={{ whiteSpace: "nowrap" }}>{pos.expiry}</span></> : null}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 4, flexShrink: 0 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
              <div data-testid="mobile-position-pnl" style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontWeight: 600, fontSize: 14, color: toneColor(pnlTone) }}>
                {fmtPnl(pnl)}
              </div>
              {/* Return % lives ONCE on the card, paired with its P&L exactly
                  like the desktop column pair. Value before label so the
                  provenance title wraps both. */}
              <div className="mobile-card__return" title={returnTitle}>
                <span className="mobile-card__return-v" style={{ color: toneColor(pnlTone) }}>
                  {pnlPct == null ? "N/A" : fmtPct(pnlPct)}
                </span>
                <span className="mobile-card__return-k">Return %</span>
              </div>
            </div>
            <span className="mobile-card__chevron" aria-hidden>
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </div>
        </div>

        {/* Quote strip: live last price (+trend arrow) and underlying spot */}
        <div className="mobile-card__quote">
          <span className="mobile-card__quote-seg" data-testid={`mobile-position-${pos.ticker}-last`}>
            <span className="mobile-card__quote-k">Last</span>
            <span className="mobile-card__quote-v">
              {lastPrice != null ? fmtPriceOrCalculated(lastPrice, lastPriceIsCalculated) : "—"}
              <TrendArrow direction={priceDirection} />
            </span>
          </span>
          {underlyingSpot != null && (
            <span
              className="mobile-card__quote-seg mobile-card__quote-seg--spot"
              data-testid={`mobile-position-${pos.ticker}-underlying`}
            >
              <span className="mobile-card__quote-k">{pos.ticker} Spot</span>
              <span className="mobile-card__quote-v">{fmtPrice(underlyingSpot)}</span>
            </span>
          )}
        </div>

        {/* 2x2 MetricCell grid — the scan set */}
        <div className="mobile-card__grid">
          <MetricCell label="MV" value={mv != null ? fmtUsd(mv) : "—"} />
          <MetricCell
            label="Today"
            value={fmtPnl(todayPnl)}
            tone={toneFor(todayPnl)}
            testId="mobile-position-today"
          />
          <MetricCell label="EC" value={fmtEntryCost(displayEc)} title={blendedBasis ? MIXED_BASIS_TITLE : undefined} />
          <MetricCell
            label="Day Chg"
            value={fmtDayChg(dailyChg)}
            tone={dailyChg != null ? toneFor(dailyChg) : undefined}
            testId={`mobile-position-${pos.ticker}-daychg`}
          />
        </div>

        {expanded ? (
          <>
            {/* Basis ledger — the desktop columns that don't earn collapsed space */}
            <div className="mobile-card__ledger" data-testid={`mobile-position-${pos.ticker}-basis`}>
              <MetricCell
                label="Avg Entry"
                value={avgEntry == null ? "—" : fmtPrice(avgEntry)}
                size="secondary"
                title={blendedBasis ? MIXED_BASIS_TITLE : undefined}
              />
              {!isStock && (
                <MetricCell
                  label="Implied"
                  value={impliedNet != null ? fmtPrice(impliedNet) : "—"}
                  size="secondary"
                  title="Black-Scholes implied value at current spot"
                />
              )}
            </div>
            <div className="mobile-card__detail" data-testid={`mobile-position-${pos.ticker}-legs`}>
              {pos.legs.map((leg, idx) => (
                <LegItem
                  key={idx}
                  leg={leg}
                  prices={prices}
                  ticker={pos.ticker}
                  expiry={pos.expiry}
                  showImplied={!isStock}
                  riskFreeRate={riskFreeRate}
                  onClick={() => onLegClick(leg, pos)}
                />
              ))}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

function LegItem({ leg, prices, ticker, expiry, showImplied, riskFreeRate, onClick }: { leg: PortfolioLeg; prices?: Record<string, PriceData>; ticker: string; expiry: string; showImplied: boolean; riskFreeRate: number | null; onClick: () => void }) {
  let realtimeLeg: PriceData | null = null;
  if (leg.type === "Stock") {
    realtimeLeg = prices?.[ticker.toUpperCase()] ?? null;
  } else {
    const key = legPriceKey(ticker, expiry, leg);
    realtimeLeg = key && prices ? prices[key] ?? null : null;
  }
  const resolved = resolveRealtimePrice(
    realtimeLeg,
    leg.market_price != null ? Math.abs(leg.market_price) : null,
    Boolean(leg.market_price_is_calculated),
  );
  const marketPrice = resolved.price;
  const isCalculated = resolved.isCalculated;
  const mult = leg.type === "Stock" ? 1 : 100;
  const legMv = marketPrice != null ? marketPrice * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;
  const tone = toneFor(legPnl);

  // A SHORT option leg's Avg Entry and Last Price are premium CREDITS and
  // display negative, matching the signed combo header row. Stock legs stay
  // positive — their Avg Entry is a per-instrument PRICE, same scoping as the
  // single-leg stock rule (desktop LegRow, 2026-08-23).
  const displaySign = leg.type === "Stock" ? 1 : sign;
  const signedLast = marketPrice != null ? displaySign * marketPrice : null;
  const { direction: legPriceDirection } = usePriceDirection(signedLast);
  const legAvg = displaySign * (Math.abs(leg.avg_cost) / mult);

  // Per-leg Black-Scholes, signed like the desktop LegRow implied cell.
  const legResult =
    leg.type === "Stock" || leg.strike == null || leg.strike === 0 || !prices || riskFreeRate == null
      ? null
      : computeLegImpliedValue(
          {
            ticker,
            expiry,
            strike: leg.strike,
            type: leg.type,
            direction: leg.direction,
            contracts: leg.contracts,
          },
          prices,
          { riskFreeRate },
        );
  const legImplied = legResult?.perContract != null ? sign * legResult.perContract : null;

  const description = `${leg.direction} ${leg.contracts}x ${leg.type}${leg.strike ? ` $${leg.strike}` : ""}`;

  return (
    <div
      className="mobile-card__leg mobile-card__leg-row--tappable"
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
      <div className="mobile-card__leg-head">
        <span className="mobile-card__leg-desc">{description}</span>
        <span className="mobile-card__leg-pnl" style={{ color: toneColor(tone) }}>{fmtPnl(legPnl)}</span>
      </div>
      <div className="mobile-card__leg-grid">
        <span className="mobile-card__leg-cell">
          <span className="mobile-card__leg-cell-k">Avg</span>
          <span className="mobile-card__leg-cell-v">{fmtPrice(legAvg)}</span>
        </span>
        <span className="mobile-card__leg-cell">
          <span className="mobile-card__leg-cell-k">Last</span>
          <span className="mobile-card__leg-cell-v">
            {signedLast != null ? fmtPriceOrCalculated(signedLast, isCalculated) : "—"}
            <TrendArrow direction={legPriceDirection} />
          </span>
        </span>
        {showImplied && (
          <span className="mobile-card__leg-cell" title="Black-Scholes implied value at current spot">
            <span className="mobile-card__leg-cell-k">Impl</span>
            <span className="mobile-card__leg-cell-v">{legImplied != null ? fmtPrice(legImplied) : "—"}</span>
          </span>
        )}
        {/* Signed like the desktop LegRow: a short leg's MV and Initial Value
            are credits and must reconcile to the signed header (R-244). */}
        <span className="mobile-card__leg-cell">
          <span className="mobile-card__leg-cell-k">MV</span>
          <span className="mobile-card__leg-cell-v">{legMv != null ? fmtSignedUsd(sign * legMv) : "—"}</span>
        </span>
        <span className="mobile-card__leg-cell">
          <span className="mobile-card__leg-cell-k">Init</span>
          <span className="mobile-card__leg-cell-v">{fmtSignedUsd(sign * legEc)}</span>
        </span>
      </div>
    </div>
  );
}

export default function MobilePositionList({ positions, prices, showExpiry = true, portfolio = null }: MobilePositionListProps) {
  const sorted = useMemo(() => [...positions].sort((a, b) => a.ticker.localeCompare(b.ticker)), [positions]);

  // null until FRED answers — same gate as the desktop Implied columns (R-229).
  const { rate: riskFreeRate } = useRiskFreeRateState();

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
        <PositionCard key={pos.id} pos={pos} prices={prices} showExpiry={showExpiry} riskFreeRate={riskFreeRate} onLegClick={handleLegClick} />
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
