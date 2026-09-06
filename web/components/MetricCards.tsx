"use client";

import {
  isIbDailyPnlFromCurrentSession,
  sessionPositions,
} from "@/lib/ibDailyPnlSession";
import { useState, useCallback, useId, useMemo, type KeyboardEvent, type ReactNode } from "react";
import type { PortfolioData, AccountSummary, ExecutedOrder } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { computeExposureDetailed, type ExposureDataWithBreakdown } from "@/lib/exposureBreakdown";
import { computeDayMoveBreakdown } from "@/lib/dayMoveBreakdown";
import { computeUnrealizedBreakdown, countUnmeasuredBasis, sumUnrealizedBreakdown } from "@/lib/unrealizedBreakdown";
import { resolveMarketValue } from "@/lib/positionUtils";
import { getMarketPhaseFromDate } from "@/lib/serviceHealthWindows";
import {
  computeLeverageRatio,
  classifyLeverageBias,
  formatLeveragePct,
  formatLeverageMultiplier,
} from "@/lib/dollarDeltaLeverage";
import { filterExecutedToEtToday } from "@/lib/orders/executedToday";
import ExposureBreakdownModal, { type ExposureMetric } from "./ExposureBreakdownModal";
import FillsModal from "./FillsModal";
import PnlBreakdownModal from "./PnlBreakdownModal";
import AccountMetricModal from "./AccountMetricModal";

type MetricCardsProps = {
  portfolio: PortfolioData | null;
  prices?: Record<string, PriceData>;
  realizedPnl?: number;
  executedOrders?: ExecutedOrder[];
  section?: string;
};

const fmt = (n: number) =>
  n >= 1_000_000
    ? `$${(n / 1_000_000).toFixed(2)}M`
    : `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

const fmtSigned = (n: number) =>
  `${n >= 0 ? "+" : "-"}${fmt(Math.abs(n))}`;

const fmtExact = (n: number) =>
  `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtSignedExact = (n: number) =>
  `${n >= 0 ? "+" : "-"}${fmtExact(n)}`;

const tone = (n: number) => (n > 0 ? "positive" as const : n < 0 ? "negative" as const : "neutral" as const);

function computeTodayUnrealizedPnl(
  portfolio: PortfolioData,
  prices: Record<string, PriceData>,
): { pnl: number; positionsWithData: number; totalPositions: number } {
  const { rows, total } = computeDayMoveBreakdown(portfolio, prices);
  return { pnl: total, positionsWithData: rows.length, totalPositions: portfolio.positions.length };
}

/* ─── Metric card helper ─────────────────────────────────── */

type CardDef = {
  label: string;
  value: string;
  change: string;
  tone: "positive" | "negative" | "neutral";
  /** Optional subtitle rendered between value and change line. */
  subtitle?: ReactNode;
};

function activateMetricControl(event: KeyboardEvent<HTMLDivElement>) {
  if (event.target !== event.currentTarget) return;
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  if (!event.repeat) event.currentTarget.click();
}

function MetricCard({ card, onClick }: { card: CardDef; onClick?: () => void }) {
  const descriptionId = useId();
  return (
    <div
      className={`metric-card${onClick ? " metric-card-clickable" : ""}`}
      onClick={onClick}
      onKeyDown={onClick ? activateMetricControl : undefined}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `View ${card.label} breakdown` : undefined}
      aria-haspopup={onClick ? "dialog" : undefined}
      aria-describedby={onClick ? `${descriptionId}-value ${descriptionId}-change${card.subtitle ? ` ${descriptionId}-subtitle` : ""}` : undefined}
    >
      <div className="metric-label">{card.label}</div>
      <div id={`${descriptionId}-value`} className={`metric-value ${card.tone !== "neutral" ? card.tone : ""}`}>{card.value}</div>
      {card.subtitle ? <div id={`${descriptionId}-subtitle`} className="metric-subtitle">{card.subtitle}</div> : null}
      <div id={`${descriptionId}-change`} className={`metric-change ${card.tone}`}>{card.change}</div>
    </div>
  );
}

/* ─── Collapsible section header ─────────────────────────── */

function SectionHeader({ label, collapsed, onToggle }: { label: string; collapsed: boolean; onToggle: () => void }) {
  return (
    <div
      className="section-label-mono section-label-toggle"
      role="button"
      tabIndex={0}
      aria-label={`${label} metrics`}
      aria-expanded={!collapsed}
      onClick={onToggle}
      onKeyDown={activateMetricControl}
    >
      <svg
        className={`section-chevron${collapsed ? "" : " section-chevron-open"}`}
        width="10"
        height="10"
        viewBox="0 0 10 10"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M3 1.5L7 5L3 8.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>{label}</span>
    </div>
  );
}

/* ─── Account row (IB authoritative) — 4 cards, no Realized duplicate ── */

function AccountRow({
  acct,
  ibDailyPnl,
  todayUnrealized,
  effectiveDayPnl,
  hasPositions,
  hasPrices,
  collapsed,
  onToggle,
  onNetLiqClick,
  onDayPnlClick,
  onUnrealizedClick,
  onDividendsClick,
}: {
  acct: AccountSummary;
  /** Client-computed day-move aggregate, used as the pre-market /
   *  off-hours fallback when IB's `dailyPnL` stream is not active.
   *  Pre-market (04:00-09:30 ET) and after-hours, IB returns NaN for
   *  `reqPnL().dailyPnL`, so `acct.daily_pnl` lands as null. The
   *  per-leg `last`/`close` math in `computeTodayUnrealizedPnl` still
   *  works because pre-market quotes populate `prices[*].last` — the
   *  Today's P&L breakout further down the page already uses this. */
  /** IB streamed dailyPnL, already gated on a trading day (null otherwise). */
  ibDailyPnl: number | null;
  todayUnrealized: { pnl: number; positionsWithData: number } | null;
  effectiveDayPnl: number | null;
  /** True when the portfolio has at least one open position. */
  hasPositions: boolean;
  /** True when the WS prices map has at least one entry. Used to
   *  distinguish "IB unreachable" (positions exist but no prices)
   *  from "market closed" (no live ticks because markets are dark). */
  hasPrices: boolean;
  collapsed: boolean;
  onToggle: () => void;
  onNetLiqClick: () => void;
  onDayPnlClick: () => void;
  onUnrealizedClick: () => void;
  onDividendsClick: () => void;
}) {
  const ibDaily = ibDailyPnl;
  const fallback =
    ibDaily == null && todayUnrealized != null && todayUnrealized.positionsWithData > 0
      ? todayUnrealized.pnl
      : null;
  const displayValue = effectiveDayPnl;
  // Label precedence:
  //   1. "TODAY"               — IB streamed dailyPnL (regular trading hours)
  //   2. "ESTIMATED (LIVE | PRE-MARKET | AFTER HOURS)" — IB silent but live
  //      quotes give a day-move; the parenthetical names the current ET session
  //   3. "WAITING FOR IB"      — positions exist but the WS prices feed is
  //      empty, so the IB Gateway is unreachable (not "closed")
  //   4. "MARKET CLOSED"       — no positions, or all positions stale (the
  //      honest "nothing to compute" state)
  let displayLabel: string;
  if (ibDaily != null) displayLabel = "TODAY";
  else if (fallback != null) {
    // IB's reqPnL aggregate is silent but live quotes give us a day-move
    // estimate. Name the SESSION honestly instead of always saying
    // PRE-MARKET; during RTH this is a live estimate, not pre-market.
    const phase = getMarketPhaseFromDate();
    displayLabel =
      phase === "open"
        ? "ESTIMATED (LIVE)"
        : phase === "after"
          ? "ESTIMATED (AFTER HOURS)"
          : phase === "pre"
            ? "ESTIMATED (PRE-MARKET)"
            : "ESTIMATED";
  }
  else if (hasPositions && !hasPrices) displayLabel = "WAITING FOR IB";
  else displayLabel = "MARKET CLOSED";

  // Day P&L %, shown alongside the notional. Denominator is the prior session's
  // net liq (start-of-day account value = current NLV minus today's P&L), per
  // the "Day Chg % off yesterday's value, never entry cost" rule.
  const priorNetLiq = displayValue != null ? acct.net_liquidation - displayValue : null;
  const dayPnlPct =
    displayValue != null && priorNetLiq != null && priorNetLiq > 0
      ? (displayValue / priorNetLiq) * 100
      : null;
  const dayPnlChange =
    dayPnlPct != null
      ? `${dayPnlPct >= 0 ? "+" : ""}${dayPnlPct.toFixed(2)}% · ${displayLabel}`
      : displayLabel;
  return (
    <>
      <SectionHeader label="ACCOUNT" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && <div className="metrics-grid">
        <MetricCard
          card={{ label: "Net Liquidation", value: fmtExact(acct.net_liquidation), change: "BANKROLL", tone: "neutral" }}
          onClick={onNetLiqClick}
        />
        <MetricCard
          card={{
            label: "Day P&L",
            value: displayValue != null ? fmtSignedExact(displayValue) : "---",
            change: dayPnlChange,
            tone: displayValue != null ? tone(displayValue) : "neutral",
          }}
          onClick={onDayPnlClick}
        />
        {/* When the fallback fires, the card is still clickable — the
            Day P&L modal already uses the client-computed breakdown
            via computeDayMoveBreakdown, so the drill-down is accurate
            even when IB's aggregate is unavailable. */}
        <MetricCard
          card={{ label: "Unrealized P&L", value: fmtSignedExact(acct.unrealized_pnl), change: "OPEN POSITIONS", tone: acct.unrealized_pnl !== 0 ? tone(acct.unrealized_pnl) : "neutral" }}
          onClick={onUnrealizedClick}
        />
        <MetricCard
          card={{ label: "Dividends", value: fmtExact(acct.dividends), change: "ACCRUED", tone: acct.dividends > 0 ? "positive" : "neutral" }}
          onClick={onDividendsClick}
        />
      </div>}
    </>
  );
}

/* ─── Risk row (margin / capacity) ───────────────────────── */

function RiskRow({
  acct,
  collapsed,
  onToggle,
  onBuyingPowerClick,
  onMarginClick,
  onExcessLiqClick,
  onSettledCashClick,
}: {
  acct: AccountSummary;
  collapsed: boolean;
  onToggle: () => void;
  onBuyingPowerClick: () => void;
  onMarginClick: () => void;
  onExcessLiqClick: () => void;
  onSettledCashClick: () => void;
}) {
  return (
    <>
      <SectionHeader label="RISK" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && <div className="metrics-grid">
        <MetricCard
          card={{ label: "Buying Power", value: fmtExact(acct.buying_power), change: "AVAILABLE", tone: "neutral" }}
          onClick={onBuyingPowerClick}
        />
        <MetricCard
          card={{ label: "Maintenance Margin", value: fmtExact(acct.maintenance_margin), change: "REQUIRED", tone: "neutral" }}
          onClick={onMarginClick}
        />
        <MetricCard
          card={{ label: "Excess Liquidity", value: fmtExact(acct.excess_liquidity), change: "CUSHION", tone: tone(acct.excess_liquidity) }}
          onClick={onExcessLiqClick}
        />
        <MetricCard
          card={{ label: "Settled Cash", value: fmtSignedExact(acct.settled_cash), change: "NET CASH", tone: tone(acct.settled_cash) }}
          onClick={onSettledCashClick}
        />
      </div>}
    </>
  );
}

/* ─── Margin row (EWL / SMA) ───────────────────────────────── */

function MarginRow({
  acct,
  collapsed,
  onToggle,
  onEwlClick,
  onPrevEwlClick,
  onRegTClick,
  onSmaClick,
}: {
  acct: AccountSummary;
  collapsed: boolean;
  onToggle: () => void;
  onEwlClick: () => void;
  onPrevEwlClick: () => void;
  onRegTClick: () => void;
  onSmaClick: () => void;
}) {
  return (
    <>
      <SectionHeader label="MARGIN" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && <div className="metrics-grid">
        <MetricCard
          card={{ label: "Equity With Loan", value: acct.equity_with_loan != null ? fmtExact(acct.equity_with_loan) : "---", change: "EWL", tone: "neutral" }}
          onClick={onEwlClick}
        />
        <MetricCard
          card={{ label: "Previous Day EWL", value: acct.previous_day_ewl != null ? fmtExact(acct.previous_day_ewl) : "---", change: "PRIOR SESSION", tone: "neutral" }}
          onClick={onPrevEwlClick}
        />
        <MetricCard
          card={{ label: "Reg T Equity", value: acct.reg_t_equity != null ? fmtExact(acct.reg_t_equity) : "---", change: "REGULATION T", tone: "neutral" }}
          onClick={onRegTClick}
        />
        <MetricCard
          card={{ label: "SMA", value: acct.sma != null ? fmtExact(acct.sma) : "---", change: "SPECIAL MEMORANDUM", tone: "neutral" }}
          onClick={onSmaClick}
        />
      </div>}
    </>
  );
}

/* ─── Capital row (cash / funds / position value) ──────────── */

function CapitalRow({
  acct,
  collapsed,
  onToggle,
  onCashClick,
  onAvailFundsClick,
  onInitMarginClick,
  onGrossPosClick,
}: {
  acct: AccountSummary;
  collapsed: boolean;
  onToggle: () => void;
  onCashClick: () => void;
  onAvailFundsClick: () => void;
  onInitMarginClick: () => void;
  onGrossPosClick: () => void;
}) {
  return (
    <>
      <SectionHeader label="CAPITAL" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && <div className="metrics-grid">
        <MetricCard
          card={{ label: "Cash", value: acct.cash != null ? fmtSignedExact(acct.cash) : "---", change: "TOTAL CASH", tone: acct.cash != null ? tone(acct.cash) : "neutral" }}
          onClick={onCashClick}
        />
        <MetricCard
          card={{ label: "Available Funds", value: acct.available_funds != null ? fmtExact(acct.available_funds) : "---", change: "EWL - INITIAL MARGIN", tone: "neutral" }}
          onClick={onAvailFundsClick}
        />
        <MetricCard
          card={{ label: "Initial Margin", value: acct.initial_margin != null ? fmtExact(acct.initial_margin) : "---", change: "REQUIRED", tone: "neutral" }}
          onClick={onInitMarginClick}
        />
        <MetricCard
          card={{ label: "Gross Position Value", value: acct.gross_position_value != null ? fmtExact(acct.gross_position_value) : "---", change: "SECURITIES", tone: "neutral" }}
          onClick={onGrossPosClick}
        />
      </div>}
    </>
  );
}

/* ─── Exposure row (real-time computed, clickable) ────────── */

function buildDollarDeltaSubtitle(
  exposure: ExposureDataWithBreakdown,
  netLiquidation: number | undefined,
  hasApprox: boolean,
): { subtitle: ReactNode | undefined; change: string } {
  const leverage = netLiquidation != null
    ? computeLeverageRatio(exposure.dollarDelta, netLiquidation)
    : null;
  if (!leverage) return { subtitle: undefined, change: "NOTIONAL EXPOSURE" };

  const bias = classifyLeverageBias(leverage.pct);
  const biasLabel = bias === "long"
    ? "long-biased"
    : bias === "short"
      ? "short-biased"
      : "market-neutral";
  const biasClass = `metric-subtitle-${bias}`;

  return {
    subtitle: (
      <span className={`metric-subtitle-leverage ${biasClass}`} data-testid="dd-card-leverage">
        <span className="metric-subtitle-pct" data-testid="dd-card-leverage-pct">
          {formatLeveragePct(leverage.pct)} of NLV
        </span>
        <span className="metric-subtitle-divider" aria-hidden="true">/</span>
        <span className="metric-subtitle-multiplier" data-testid="dd-card-leverage-multiplier">
          {formatLeverageMultiplier(leverage.multiplier)} {biasLabel}
        </span>
        {hasApprox && (
          <span className="metric-subtitle-approx" data-testid="dd-card-leverage-approx">
            APPROX
          </span>
        )}
      </span>
    ),
    change: "NOTIONAL EXPOSURE",
  };
}

function ExposureRow({
  exposure,
  netLiquidation,
  collapsed,
  onToggle,
  onCardClick,
}: {
  exposure: ExposureDataWithBreakdown | null;
  netLiquidation: number | undefined;
  collapsed: boolean;
  onToggle: () => void;
  onCardClick: (metric: ExposureMetric) => void;
}) {
  const ddCard = exposure
    ? buildDollarDeltaSubtitle(
        exposure,
        netLiquidation,
        exposure.rows.some((r) => r.deltaSource !== "ib"),
      )
    : { subtitle: undefined, change: "NOTIONAL EXPOSURE" };

  return (
    <>
      <SectionHeader label="EXPOSURE" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && exposure ? (
        <div className="metrics-grid">
          <MetricCard
            card={{ label: "Net Long", value: fmt(exposure.netLong), change: "LONG BIASED", tone: "positive" }}
            onClick={() => onCardClick("netLong")}
          />
          <MetricCard
            card={{ label: "Net Short", value: fmt(exposure.netShort), change: "SHORT BIASED", tone: "negative" }}
            onClick={() => onCardClick("netShort")}
          />
          <MetricCard
            card={{
              label: "Dollar Delta",
              value: fmtSigned(exposure.dollarDelta),
              change: ddCard.change,
              tone: tone(exposure.dollarDelta),
              subtitle: ddCard.subtitle,
            }}
            onClick={() => onCardClick("dollarDelta")}
          />
          <MetricCard
            card={{ label: "Net Exposure", value: `${exposure.netExposurePct >= 0 ? "+" : ""}${exposure.netExposurePct.toFixed(1)}%`, change: "OF BANKROLL", tone: tone(exposure.netExposurePct) }}
            onClick={() => onCardClick("netExposure")}
          />
        </div>
      ) : !collapsed ? (
        <div className="metrics-grid">
          {["Net Long", "Net Short", "Dollar Delta", "Net Exposure"].map((label) => (
            <div key={label} className="metric-card metric-card-loading">
              <div className="metric-label">{label}</div>
              <div className="metric-value">---</div>
              <div className="metric-change neutral">AWAITING PRICES</div>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

/* ─── Today's P&L row ──────────────────────────────────────
 *  "Unrealized" renamed to "Day Move" to distinguish it from
 *  ACCOUNT "Unrealized P&L" (which is entry-cost-based, not intraday).
 *  All three cards are now clickable with proof modals.
 * ─────────────────────────────────────────────────────────── */

function TodayPnlRow({
  todayUnrealized,
  hasDaily,
  unrealized,
  realized,
  total,
  realizedPnl,
  collapsed,
  onToggle,
  onDayMoveClick,
  onRealizedClick,
  onTotalClick,
}: {
  todayUnrealized: { pnl: number; positionsWithData: number; totalPositions: number } | null;
  hasDaily: boolean;
  unrealized: number;
  realized: number;
  total: number;
  realizedPnl?: number;
  collapsed: boolean;
  onToggle: () => void;
  onDayMoveClick: () => void;
  onRealizedClick: () => void;
  onTotalClick: () => void;
}) {
  const realizedDescriptionId = useId();
  return (
    <>
      <SectionHeader label="TODAY'S P&L" collapsed={collapsed} onToggle={onToggle} />
      {!collapsed && hasDaily ? (
        <div className="metrics-grid-3">
          {/* Renamed: "Unrealized" → "Day Move" — intraday change from yesterday's close */}
          <MetricCard
            card={{
              label: "Day Move",
              value: fmtSigned(unrealized),
              change: `${todayUnrealized!.positionsWithData} OF ${todayUnrealized!.totalPositions} POSITIONS`,
              tone: tone(unrealized),
            }}
            onClick={onDayMoveClick}
          />
          <MetricCard
            card={{ label: "Realized", value: fmtSigned(realized), change: "TODAY'S FILLS", tone: tone(realized) }}
            onClick={onRealizedClick}
          />
          <MetricCard
            card={{ label: "Total", value: fmtSigned(total), change: "COMBINED", tone: tone(total) }}
            onClick={onTotalClick}
          />
        </div>
      ) : !collapsed ? (
        <div className="metrics-grid-3">
          <div className="metric-card">
            <div className="metric-label">Day Move</div>
            <div className="metric-value">---</div>
            <div className="metric-change neutral">MARKET CLOSED</div>
          </div>
          <div
            className="metric-card metric-card-clickable"
            role="button"
            tabIndex={0}
            aria-label="View Realized breakdown"
            aria-haspopup="dialog"
            aria-describedby={`${realizedDescriptionId}-value ${realizedDescriptionId}-change`}
            onClick={onRealizedClick}
            onKeyDown={activateMetricControl}
          >
            <div className="metric-label">Realized</div>
            <div id={`${realizedDescriptionId}-value`} className={`metric-value ${tone(realizedPnl ?? 0) !== "neutral" ? tone(realizedPnl ?? 0) : ""}`}>
              {fmtSigned(realizedPnl ?? 0)}
            </div>
            <div id={`${realizedDescriptionId}-change`} className="metric-change neutral">TODAY&apos;S FILLS</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Total</div>
            <div className="metric-value">---</div>
            <div className="metric-change neutral">MARKET CLOSED</div>
          </div>
        </div>
      ) : null}
    </>
  );
}

/* ─── Legacy NET LEVERAGE row (no account_summary) ───────── */

function LegacyLeverageRow({ portfolio, pnl, pnlPct, unmeasuredBasisCount }: {
  portfolio: PortfolioData; pnl: number; pnlPct: number; unmeasuredBasisCount: number;
}) {
  const pnlChange = `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%`;
  const cards: CardDef[] = [
    { label: "Net Liquidation", value: fmt(portfolio.bankroll), change: "BANKROLL", tone: "neutral" },
    { label: "Positions", value: String(portfolio.position_count), change: `${portfolio.defined_risk_count} DEFINED / ${portfolio.undefined_risk_count} UNDEFINED`, tone: "neutral" },
    { label: "Deployed", value: fmt(portfolio.total_deployed_dollars), change: `${portfolio.total_deployed_pct.toFixed(1)}% OF BANKROLL`, tone: portfolio.total_deployed_pct > 100 ? "negative" : "neutral" },
    {
      label: "Open P&L",
      value: fmtSigned(pnl),
      change: unmeasuredBasisCount > 0 ? `${pnlChange} · ${unmeasuredBasisCount} UNMEASURED BASIS` : pnlChange,
      tone: tone(pnl),
    },
  ];

  return (
    <>
      <div className="section-label-mono">NET LEVERAGE</div>
      <div className="metrics-grid">
        {cards.map((c) => <MetricCard key={c.label} card={c} />)}
      </div>
    </>
  );
}

/* ─── Main component ─────────────────────────────────────── */

export default function MetricCards({ portfolio, prices, realizedPnl, executedOrders = [], section }: MetricCardsProps) {
  // Section collapse state: Account + Today's P&L expanded by default, rest collapsed
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({
    account: false,
    risk: true,
    margin: true,
    capital: true,
    exposure: true,
    todayPnl: false,
  });
  const toggle = useCallback((key: string) => {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const [activeMetric, setActiveMetric] = useState<ExposureMetric | null>(null);
  const [fillsModalOpen, setFillsModalOpen] = useState(false);
  const [unrealizedModalOpen, setUnrealizedModalOpen] = useState(false);
  const [dayMoveModalOpen, setDayMoveModalOpen] = useState(false);
  const [totalModalOpen, setTotalModalOpen] = useState(false);
  const [netLiqModalOpen, setNetLiqModalOpen] = useState(false);
  const [dayPnlModalOpen, setDayPnlModalOpen] = useState(false);
  const [dividendsModalOpen, setDividendsModalOpen] = useState(false);
  const [buyingPowerModalOpen, setBuyingPowerModalOpen] = useState(false);
  const [marginModalOpen, setMarginModalOpen] = useState(false);
  const [excessLiqModalOpen, setExcessLiqModalOpen] = useState(false);
  const [settledCashModalOpen, setSettledCashModalOpen] = useState(false);
  // Margin row modals
  const [ewlModalOpen, setEwlModalOpen] = useState(false);
  const [prevEwlModalOpen, setPrevEwlModalOpen] = useState(false);
  const [regTModalOpen, setRegTModalOpen] = useState(false);
  const [smaModalOpen, setSmaModalOpen] = useState(false);
  // Capital row modals
  const [cashModalOpen, setCashModalOpen] = useState(false);
  const [availFundsModalOpen, setAvailFundsModalOpen] = useState(false);
  const [initMarginModalOpen, setInitMarginModalOpen] = useState(false);
  const [grossPosModalOpen, setGrossPosModalOpen] = useState(false);

  // Match the ET day-cut used by computeRealizedPnlFromFills / /orders so the
  // modal itemizes the same fills the total sums (NEW_FINDINGS wave-2).
  const todayExecutedFills = useMemo(
    () => filterExecutedToEtToday(executedOrders),
    [executedOrders],
  );

  const isPortfolio = section === "portfolio";
  if (!portfolio) {
    if (!isPortfolio) return null;
    const placeholders = ["Net Liquidation", "Day P&L", "Unrealized P&L", "Dividends"];
    return (
      <>
        <div className="section-label-mono">ACCOUNT</div>
        <div className="metrics-grid">
          {placeholders.map((label, i) => (
            <div key={i} className="metric-card metric-card-loading">
              <div className="metric-label">{label}</div>
              <div className="metric-value">$0,000</div>
              <div className="metric-change neutral">AWAITING SYNC</div>
            </div>
          ))}
        </div>
      </>
    );
  }

  const pnl = sumUnrealizedBreakdown(portfolio);
  const unmeasuredBasisCount = countUnmeasuredBasis(portfolio);
  const pnlPct = portfolio.total_deployed_dollars > 0
    ? (pnl / portfolio.total_deployed_dollars) * 100
    : 0;

  const hasPrices = prices && Object.keys(prices).length > 0;
  const exposure = hasPrices ? computeExposureDetailed(portfolio, prices) : null;

  // Outside a US trading day only spot crypto has a session: the day-move
  // math runs over that subset so stale Friday equity marks never read as a
  // weekend "day move" (2026-08-22: +$25,857 across 9 equity positions).
  const sessionToday = isIbDailyPnlFromCurrentSession(portfolio.last_sync);
  const sessionPortfolio = sessionToday
    ? portfolio
    : { ...portfolio, positions: sessionPositions(portfolio) };
  const todayUnrealized = hasPrices
    ? computeTodayUnrealizedPnl(sessionPortfolio, prices)
    : null;
  const hasDaily = todayUnrealized != null && todayUnrealized.positionsWithData > 0;
  const unrealized = todayUnrealized?.pnl ?? 0;
  const realized = realizedPnl ?? 0;
  const total = unrealized + realized;

  const acct = portfolio.account_summary;
  // IB still streams (and re-baselines) dailyPnL on weekends; only the
  // session-filtered client-side day move (spot crypto) may stand in.
  const ibDailyPnl = sessionToday ? acct?.daily_pnl ?? null : null;
  const effectiveDayPnl = ibDailyPnl
    ?? (hasDaily && todayUnrealized ? todayUnrealized.pnl : null);

  // Breakdown rows (computed lazily — only used when modals open)
  const unrealizedBreakdownRows = unrealizedModalOpen
    ? computeUnrealizedBreakdown(portfolio)
    : [];
  const dayMoveBreakdown = dayMoveModalOpen && hasPrices
    ? computeDayMoveBreakdown(sessionPortfolio, prices!)
    : { rows: [], total: 0 };

  if (!isPortfolio) return null;

  return (
    <>
      {/* Row 1: ACCOUNT (IB authoritative) or legacy NET LEVERAGE */}
      {acct ? (
        <AccountRow
          acct={acct}
          ibDailyPnl={ibDailyPnl}
          todayUnrealized={todayUnrealized}
          effectiveDayPnl={effectiveDayPnl}
          hasPositions={portfolio.positions.length > 0}
          hasPrices={!!hasPrices}
          collapsed={collapsed.account}
          onToggle={() => toggle("account")}
          onNetLiqClick={() => setNetLiqModalOpen(true)}
          onDayPnlClick={() => setDayPnlModalOpen(true)}
          onUnrealizedClick={() => setUnrealizedModalOpen(true)}
          onDividendsClick={() => setDividendsModalOpen(true)}
        />
      ) : (
        <LegacyLeverageRow portfolio={portfolio} pnl={pnl} pnlPct={pnlPct} unmeasuredBasisCount={unmeasuredBasisCount} />
      )}

      {/* Row 2: RISK */}
      {acct && (
        <RiskRow
          acct={acct}
          collapsed={collapsed.risk}
          onToggle={() => toggle("risk")}
          onBuyingPowerClick={() => setBuyingPowerModalOpen(true)}
          onMarginClick={() => setMarginModalOpen(true)}
          onExcessLiqClick={() => setExcessLiqModalOpen(true)}
          onSettledCashClick={() => setSettledCashModalOpen(true)}
        />
      )}

      {/* Row 3: MARGIN (EWL / SMA — only when data present) */}
      {acct && acct.equity_with_loan != null && (
        <MarginRow
          acct={acct}
          collapsed={collapsed.margin}
          onToggle={() => toggle("margin")}
          onEwlClick={() => setEwlModalOpen(true)}
          onPrevEwlClick={() => setPrevEwlModalOpen(true)}
          onRegTClick={() => setRegTModalOpen(true)}
          onSmaClick={() => setSmaModalOpen(true)}
        />
      )}

      {/* Row 4: CAPITAL (cash / funds / position value — only when data present) */}
      {acct && acct.available_funds != null && (
        <CapitalRow
          acct={acct}
          collapsed={collapsed.capital}
          onToggle={() => toggle("capital")}
          onCashClick={() => setCashModalOpen(true)}
          onAvailFundsClick={() => setAvailFundsModalOpen(true)}
          onInitMarginClick={() => setInitMarginModalOpen(true)}
          onGrossPosClick={() => setGrossPosModalOpen(true)}
        />
      )}

      {/* Row 5: EXPOSURE (real-time, all 4 clickable) */}
      <ExposureRow
        exposure={exposure}
        netLiquidation={acct?.net_liquidation}
        collapsed={collapsed.exposure}
        onToggle={() => toggle("exposure")}
        onCardClick={setActiveMetric}
      />

      {/* Row 6: TODAY'S P&L — renamed "Unrealized" → "Day Move" */}
      <TodayPnlRow
        todayUnrealized={todayUnrealized}
        hasDaily={hasDaily}
        unrealized={unrealized}
        realized={realized}
        total={total}
        realizedPnl={realizedPnl}
        collapsed={collapsed.todayPnl}
        onToggle={() => toggle("todayPnl")}
        onDayMoveClick={() => setDayMoveModalOpen(true)}
        onRealizedClick={() => setFillsModalOpen(true)}
        onTotalClick={() => setTotalModalOpen(true)}
      />

      {/* ── Modals ── */}

      {/* Exposure breakdown */}
      {exposure && (
        <ExposureBreakdownModal
          metric={activeMetric}
          exposure={exposure}
          bankroll={portfolio.bankroll}
          netLiquidation={acct?.net_liquidation}
          onClose={() => setActiveMetric(null)}
        />
      )}

      {/* ACCOUNT: Unrealized P&L → position-level open P&L (entry cost vs market value) */}
      <PnlBreakdownModal
        open={unrealizedModalOpen}
        title="Unrealized P&L: Open Positions"
        formula={
          "Unrealized P&L = SUM( market_value − entry_cost ) per position\n" +
          "Entry cost and market value are signed (credits and short marks negative)\n" +
          "so each row satisfies P&L = MKT VALUE − ENTRY COST.\n" +
          "Return uses verified max loss, net debit paid, or isolated broker-observed opening margin; opening credits stay N/A.\n" +
          "Source: IB market data synced via IB Gateway"
        }
        col1Header="ENTRY COST"
        col2Header="MKT VALUE"
        pctHeader="RETURN %"
        rows={unrealizedBreakdownRows}
        total={acct?.unrealized_pnl ?? pnl}
        onClose={() => setUnrealizedModalOpen(false)}
      />

      {/* TODAY'S P&L: Day Move → per-position intraday change (close → current) */}
      <PnlBreakdownModal
        open={dayMoveModalOpen}
        title="Day Move: Intraday P&L"
        formula={
          "Day Move = stocks: (last − close) × shares; options: IB reqPnLSingle daily P&L when available, else sign × (last − close) × contracts × multiplier\n" +
          "sign = +1 LONG, −1 SHORT  |  multiplier = 100 for options, 1 for stocks\n" +
          "Source: IB reqPnLSingle + live IB realtime prices"
        }
        col1Header="CLOSE"
        col2Header="CURRENT"
        rows={dayMoveBreakdown.rows}
        total={unrealized}
        onClose={() => setDayMoveModalOpen(false)}
      />

      {/* TODAY'S P&L: Realized → session fills with P&L (ET day-cut) */}
      <FillsModal
        open={fillsModalOpen}
        fills={todayExecutedFills}
        totalRealizedPnl={realized}
        netLiquidation={acct?.net_liquidation}
        onClose={() => setFillsModalOpen(false)}
      />

      {/* ACCOUNT: Net Liquidation */}
      {acct && (
        <AccountMetricModal
          open={netLiqModalOpen}
          title="Net Liquidation Value"
          value={fmtExact(acct.net_liquidation)}
          formula={
            "Net Liquidation = Cash + Stocks at Market Value + Options at Market Value + Bond Value\n" +
            "Source: Interactive Brokers account_summary (reqAccountSummary)\n" +
            "Updated: real-time during market hours"
          }
          onClose={() => setNetLiqModalOpen(false)}
        />
      )}

      {/* ACCOUNT: Day P&L */}
      {acct && (
        <AccountMetricModal
          open={dayPnlModalOpen}
          title="Day P&L"
          value={effectiveDayPnl != null ? fmtSignedExact(effectiveDayPnl) : "---"}
          formula={
            "Day P&L = SUM( current_price − yesterday_close ) × position_size\n" +
            (acct.daily_pnl != null
              ? "Source: Interactive Brokers reqPnL(), account-level, updated in real-time\n"
              : "Source: Current prices versus prior-session closes, estimated\n") +
            "Note: Includes all open positions across stocks, options, and other instruments"
          }
          onClose={() => setDayPnlModalOpen(false)}
        />
      )}

      {/* ACCOUNT: Dividends */}
      {acct && (
        <AccountMetricModal
          open={dividendsModalOpen}
          title="Accrued Dividends"
          value={fmtExact(acct.dividends)}
          formula={
            "Dividends = Accrued dividends from dividend-paying positions\n" +
            "Source: Interactive Brokers account_summary (DividendReceivedYear)\n" +
            "Note: Represents dividends accrued in the current calendar year"
          }
          onClose={() => setDividendsModalOpen(false)}
        />
      )}

      {/* RISK: Buying Power */}
      {acct && (
        <AccountMetricModal
          open={buyingPowerModalOpen}
          title="Buying Power"
          value={fmtExact(acct.buying_power)}
          formula={
            "Buying Power = Available margin capacity for new positions\n" +
            "Source: Interactive Brokers account_summary (BuyingPower)\n" +
            "= Excess Liquidity × Margin Multiplier\n" +
            "Note: For a Reg T margin account, typically 4× excess liquidity for day trades"
          }
          onClose={() => setBuyingPowerModalOpen(false)}
        />
      )}

      {/* RISK: Maintenance Margin */}
      {acct && (
        <AccountMetricModal
          open={marginModalOpen}
          title="Maintenance Margin"
          value={fmtExact(acct.maintenance_margin)}
          formula={
            "Maintenance Margin = Minimum equity required to maintain current positions\n" +
            "Source: Interactive Brokers account_summary (MaintMarginReq)\n" +
            "If Net Liquidation falls below this, IB may issue a margin call"
          }
          onClose={() => setMarginModalOpen(false)}
        />
      )}

      {/* RISK: Excess Liquidity */}
      {acct && (
        <AccountMetricModal
          open={excessLiqModalOpen}
          title="Excess Liquidity"
          value={fmtExact(acct.excess_liquidity)}
          formula={
            "Excess Liquidity = Net Liquidation − Maintenance Margin\n" +
            "Source: Interactive Brokers account_summary (ExcessLiquidity)\n" +
            "= Safety cushion above margin requirements\n" +
            "Green = healthy buffer | Red = dangerously close to margin call"
          }
          onClose={() => setExcessLiqModalOpen(false)}
        />
      )}

      {/* RISK: Settled Cash */}
      {acct && (
        <AccountMetricModal
          open={settledCashModalOpen}
          title="Settled Cash"
          value={fmtSignedExact(acct.settled_cash)}
          formula={
            "Settled Cash = Cash settled and available (T+1 for options, T+2 for stocks)\n" +
            "Source: Interactive Brokers account_summary (SettledCash)\n" +
            "Negative = you've spent unsettled funds (cash from recent sells not yet settled)"
          }
          onClose={() => setSettledCashModalOpen(false)}
        />
      )}

      {/* MARGIN: Equity With Loan */}
      {acct && (
        <AccountMetricModal
          open={ewlModalOpen}
          title="Equity With Loan Value"
          value={acct.equity_with_loan != null ? fmtExact(acct.equity_with_loan) : "---"}
          formula={
            "EWL = Cash + Stock Value + Bond Value + Fund Value + European & Asian Options Value\n" +
            "Source: Interactive Brokers accountValues (EquityWithLoanValue)\n" +
            "Excludes US options market value. Used as basis for margin calculations"
          }
          onClose={() => setEwlModalOpen(false)}
        />
      )}

      {/* MARGIN: Previous Day EWL */}
      {acct && (
        <AccountMetricModal
          open={prevEwlModalOpen}
          title="Previous Day Equity With Loan"
          value={acct.previous_day_ewl != null ? fmtExact(acct.previous_day_ewl) : "---"}
          formula={
            "Previous Day EWL = Equity With Loan at prior session close\n" +
            "Source: Interactive Brokers accountValues (PreviousDayEquityWithLoanValue)\n" +
            "Used for intraday margin checks and SMA calculation"
          }
          onClose={() => setPrevEwlModalOpen(false)}
        />
      )}

      {/* MARGIN: Reg T Equity */}
      {acct && (
        <AccountMetricModal
          open={regTModalOpen}
          title="Regulation T Equity"
          value={acct.reg_t_equity != null ? fmtExact(acct.reg_t_equity) : "---"}
          formula={
            "Reg T Equity = Cash + Stock Value + Bond Value + Fund Value\n" +
            "Source: Interactive Brokers accountValues (RegTEquity)\n" +
            "Federal Reserve Board Regulation T equity for margin requirements"
          }
          onClose={() => setRegTModalOpen(false)}
        />
      )}

      {/* MARGIN: SMA */}
      {acct && (
        <AccountMetricModal
          open={smaModalOpen}
          title="Special Memorandum Account"
          value={acct.sma != null ? fmtExact(acct.sma) : "---"}
          formula={
            "SMA = max(SMA_prior_day, EWL - Initial Margin)\n" +
            "Source: Interactive Brokers accountValues (SMA)\n" +
            "SMA never decreases due to market fluctuations, only from new trades.\n" +
            "Reg T requires SMA >= 0 for new positions"
          }
          onClose={() => setSmaModalOpen(false)}
        />
      )}

      {/* CAPITAL: Cash */}
      {acct && (
        <AccountMetricModal
          open={cashModalOpen}
          title="Total Cash"
          value={acct.cash != null ? fmtSignedExact(acct.cash) : "---"}
          formula={
            "Cash = Total cash balance including unsettled proceeds\n" +
            "Source: Interactive Brokers accountValues (TotalCashValue)\n" +
            "Includes cash from recent sells that haven't settled yet (T+1/T+2)"
          }
          onClose={() => setCashModalOpen(false)}
        />
      )}

      {/* CAPITAL: Available Funds */}
      {acct && (
        <AccountMetricModal
          open={availFundsModalOpen}
          title="Available Funds"
          value={acct.available_funds != null ? fmtExact(acct.available_funds) : "---"}
          formula={
            "Available Funds = Equity With Loan - Initial Margin Requirement\n" +
            "Source: Interactive Brokers accountValues (AvailableFunds)\n" +
            "Amount available to open new positions without triggering a margin call"
          }
          onClose={() => setAvailFundsModalOpen(false)}
        />
      )}

      {/* CAPITAL: Initial Margin */}
      {acct && (
        <AccountMetricModal
          open={initMarginModalOpen}
          title="Initial Margin"
          value={acct.initial_margin != null ? fmtExact(acct.initial_margin) : "---"}
          formula={
            "Initial Margin = Margin required to open current positions\n" +
            "Source: Interactive Brokers accountValues (InitMarginReq)\n" +
            "Higher than Maintenance Margin, must be met when entering a trade"
          }
          onClose={() => setInitMarginModalOpen(false)}
        />
      )}

      {/* CAPITAL: Gross Position Value */}
      {acct && (
        <AccountMetricModal
          open={grossPosModalOpen}
          title="Securities Gross Position Value"
          value={acct.gross_position_value != null ? fmtExact(acct.gross_position_value) : "---"}
          formula={
            "Gross Position Value = |Long Stock Value| + |Short Stock Value| + |Options Value|\n" +
            "Source: Interactive Brokers accountValues (GrossPositionValue)\n" +
            "Total absolute value of all securities positions"
          }
          onClose={() => setGrossPosModalOpen(false)}
        />
      )}

      {/* TODAY'S P&L: Total → formula proof */}
      <PnlBreakdownModal
        open={totalModalOpen}
        title="Today's Total P&L"
        formula={
          `Total = Day Move + Realized\n` +
          `      = ${unrealized >= 0 ? "+" : "-"}$${Math.abs(unrealized).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (day move)` +
          `  ${realized >= 0 ? "+" : "−"}  $${Math.abs(realized).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (fills)\n` +
          `      = ${total >= 0 ? "+" : "-"}$${Math.abs(total).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        }
        col1Header="COMPONENT"
        col2Header="SOURCE"
        rows={[
          {
            id: "day-move",
            ticker: "DAY MOVE",
            structure: `${todayUnrealized?.positionsWithData ?? 0} of ${todayUnrealized?.totalPositions ?? 0} positions`,
            col1: "Day Move",
            col2: "IB daily P&L + live prices",
            pnl: unrealized,
            pnlPct: null,
          },
          {
            id: "realized",
            ticker: "REALIZED",
            structure: `${executedOrders.length} fill${executedOrders.length !== 1 ? "s" : ""}`,
            col1: "Fills",
            col2: "IB executions",
            pnl: realized,
            pnlPct: null,
          },
        ]}
        total={total}
        totalLabel="COMBINED"
        onClose={() => setTotalModalOpen(false)}
      />
    </>
  );
}
