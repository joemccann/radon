"use client";

import { useState } from "react";
import type { PortfolioData, AccountSummary, ExecutedOrder } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { computeExposureDetailed, type ExposureDataWithBreakdown } from "@/lib/exposureBreakdown";
import { computeDayMoveBreakdown } from "@/lib/dayMoveBreakdown";
import ExposureBreakdownModal, { type ExposureMetric } from "./ExposureBreakdownModal";
import FillsModal from "./FillsModal";
import PnlBreakdownModal, { type PnlBreakdownRow } from "./PnlBreakdownModal";
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
  `${n >= 0 ? "+" : ""}${fmt(Math.abs(n))}`;

const fmtExact = (n: number) =>
  `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtSignedExact = (n: number) =>
  `${n >= 0 ? "+" : "-"}${fmtExact(n)}`;

const tone = (n: number) => (n > 0 ? "positive" as const : n < 0 ? "negative" as const : "neutral" as const);

function resolveMarketValue(pos: PortfolioData["positions"][number]): number | null {
  if (pos.market_value != null) return pos.market_value;
  const known = pos.legs.filter((l) => l.market_value != null);
  return known.length > 0 ? known.reduce((s, l) => s + l.market_value!, 0) : null;
}

/* ─── Unrealized P&L breakdown (IB total: entry cost vs market value) ─── */

function computeUnrealizedBreakdown(portfolio: PortfolioData): PnlBreakdownRow[] {
  return portfolio.positions.flatMap((pos) => {
    const mv = resolveMarketValue(pos);
    if (mv == null) return [];
    const pnl = mv - pos.entry_cost;
    const pnlPct = pos.entry_cost !== 0 ? (pnl / Math.abs(pos.entry_cost)) * 100 : null;
    return [{
      id: pos.id,
      ticker: pos.ticker,
      structure: pos.structure,
      col1: `$${Math.abs(pos.entry_cost).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      col2: `$${Math.abs(mv).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      pnl,
      pnlPct,
    }];
  });
}


function computeTodayUnrealizedPnl(
  portfolio: PortfolioData,
  prices: Record<string, PriceData>,
): { pnl: number; positionsWithData: number; totalPositions: number } {
  const { rows, total } = computeDayMoveBreakdown(portfolio, prices);
  return { pnl: total, positionsWithData: rows.length, totalPositions: portfolio.positions.length };
}

/* ─── Metric card helper ─────────────────────────────────── */

type CardDef = { label: string; value: string; change: string; tone: "positive" | "negative" | "neutral" };

function MetricCard({ card, onClick }: { card: CardDef; onClick?: () => void }) {
  return (
    <div className={`metric-card${onClick ? " metric-card-clickable" : ""}`} onClick={onClick}>
      <div className="metric-label">{card.label}</div>
      <div className={`metric-value ${card.tone !== "neutral" ? card.tone : ""}`}>{card.value}</div>
      <div className={`metric-change ${card.tone}`}>{card.change}</div>
    </div>
  );
}

/* ─── Account row (IB authoritative) — 4 cards, no Realized duplicate ── */

function AccountRow({
  acct,
  onNetLiqClick,
  onDayPnlClick,
  onUnrealizedClick,
  onDividendsClick,
}: {
  acct: AccountSummary;
  onNetLiqClick: () => void;
  onDayPnlClick: () => void;
  onUnrealizedClick: () => void;
  onDividendsClick: () => void;
}) {
  const dailyAvailable = acct.daily_pnl != null;
  return (
    <>
      <div className="section-label-mono">ACCOUNT</div>
      <div className="metrics-grid">
        <MetricCard
          card={{ label: "Net Liquidation", value: fmtExact(acct.net_liquidation), change: "BANKROLL", tone: "neutral" }}
          onClick={onNetLiqClick}
        />
        <MetricCard
          card={{ label: "Day P&L", value: dailyAvailable ? fmtSignedExact(acct.daily_pnl!) : "---", change: dailyAvailable ? "TODAY" : "MARKET CLOSED", tone: dailyAvailable ? tone(acct.daily_pnl!) : "neutral" }}
          onClick={onDayPnlClick}
        />
        <MetricCard
          card={{ label: "Unrealized P&L", value: fmtSignedExact(acct.unrealized_pnl), change: "OPEN POSITIONS", tone: acct.unrealized_pnl !== 0 ? tone(acct.unrealized_pnl) : "neutral" }}
          onClick={onUnrealizedClick}
        />
        <MetricCard
          card={{ label: "Dividends", value: fmtExact(acct.dividends), change: "ACCRUED", tone: acct.dividends > 0 ? "positive" : "neutral" }}
          onClick={onDividendsClick}
        />
      </div>
    </>
  );
}

/* ─── Risk row (margin / capacity) ───────────────────────── */

function RiskRow({
  acct,
  onBuyingPowerClick,
  onMarginClick,
  onExcessLiqClick,
  onSettledCashClick,
}: {
  acct: AccountSummary;
  onBuyingPowerClick: () => void;
  onMarginClick: () => void;
  onExcessLiqClick: () => void;
  onSettledCashClick: () => void;
}) {
  return (
    <>
      <div className="section-label-mono">RISK</div>
      <div className="metrics-grid">
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
      </div>
    </>
  );
}

/* ─── Exposure row (real-time computed, clickable) ────────── */

function ExposureRow({
  exposure,
  onCardClick,
}: {
  exposure: ExposureDataWithBreakdown | null;
  onCardClick: (metric: ExposureMetric) => void;
}) {
  return (
    <>
      <div className="section-label-mono">EXPOSURE</div>
      {exposure ? (
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
            card={{ label: "Dollar Delta", value: fmtSigned(exposure.dollarDelta), change: "NOTIONAL EXPOSURE", tone: tone(exposure.dollarDelta) }}
            onClick={() => onCardClick("dollarDelta")}
          />
          <MetricCard
            card={{ label: "Net Exposure", value: `${exposure.netExposurePct >= 0 ? "+" : ""}${exposure.netExposurePct.toFixed(1)}%`, change: "OF BANKROLL", tone: tone(exposure.netExposurePct) }}
            onClick={() => onCardClick("netExposure")}
          />
        </div>
      ) : (
        <div className="metrics-grid">
          {["Net Long", "Net Short", "Dollar Delta", "Net Exposure"].map((label) => (
            <div key={label} className="metric-card metric-card-loading">
              <div className="metric-label">{label}</div>
              <div className="metric-value">---</div>
              <div className="metric-change neutral">AWAITING PRICES</div>
            </div>
          ))}
        </div>
      )}
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
  onDayMoveClick: () => void;
  onRealizedClick: () => void;
  onTotalClick: () => void;
}) {
  return (
    <>
      <div className="section-label-mono">TODAY&apos;S P&amp;L</div>
      {hasDaily ? (
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
      ) : (
        <div className="metrics-grid-3">
          <div className="metric-card">
            <div className="metric-label">Day Move</div>
            <div className="metric-value">---</div>
            <div className="metric-change neutral">MARKET CLOSED</div>
          </div>
          <div className="metric-card metric-card-clickable" onClick={onRealizedClick}>
            <div className="metric-label">Realized</div>
            <div className={`metric-value ${tone(realizedPnl ?? 0) !== "neutral" ? tone(realizedPnl ?? 0) : ""}`}>
              {fmtSigned(realizedPnl ?? 0)}
            </div>
            <div className="metric-change neutral">TODAY&apos;S FILLS</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Total</div>
            <div className="metric-value">---</div>
            <div className="metric-change neutral">MARKET CLOSED</div>
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Legacy NET LEVERAGE row (no account_summary) ───────── */

function LegacyLeverageRow({ portfolio, pnl, pnlPct }: { portfolio: PortfolioData; pnl: number; pnlPct: number }) {
  const cards: CardDef[] = [
    { label: "Net Liquidation", value: fmt(portfolio.bankroll), change: "BANKROLL", tone: "neutral" },
    { label: "Positions", value: String(portfolio.position_count), change: `${portfolio.defined_risk_count} DEFINED / ${portfolio.undefined_risk_count} UNDEFINED`, tone: "neutral" },
    { label: "Deployed", value: fmt(portfolio.total_deployed_dollars), change: `${portfolio.total_deployed_pct.toFixed(1)}% OF BANKROLL`, tone: portfolio.total_deployed_pct > 100 ? "negative" : "neutral" },
    { label: "Open P&L", value: `${pnl >= 0 ? "+" : ""}${fmt(Math.abs(pnl))}`, change: `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%`, tone: tone(pnl) },
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

  const pnl = (() => {
    let total = 0;
    for (const pos of portfolio.positions) {
      const mv = resolveMarketValue(pos);
      if (mv != null) total += mv - pos.entry_cost;
    }
    return total;
  })();
  const pnlPct = portfolio.total_deployed_dollars > 0
    ? (pnl / portfolio.total_deployed_dollars) * 100
    : 0;

  const hasPrices = prices && Object.keys(prices).length > 0;
  const exposure = hasPrices ? computeExposureDetailed(portfolio, prices) : null;

  const todayUnrealized = hasPrices
    ? computeTodayUnrealizedPnl(portfolio, prices)
    : null;
  const hasDaily = todayUnrealized != null && todayUnrealized.positionsWithData > 0;
  const unrealized = todayUnrealized?.pnl ?? 0;
  const realized = realizedPnl ?? 0;
  const total = unrealized + realized;

  const acct = portfolio.account_summary;

  // Breakdown rows (computed lazily — only used when modals open)
  const unrealizedBreakdownRows = unrealizedModalOpen
    ? computeUnrealizedBreakdown(portfolio)
    : [];
  const dayMoveBreakdown = dayMoveModalOpen && hasPrices
    ? computeDayMoveBreakdown(portfolio, prices!)
    : { rows: [], total: 0 };

  if (!isPortfolio) return null;

  return (
    <>
      {/* Row 1: ACCOUNT (IB authoritative) or legacy NET LEVERAGE */}
      {acct ? (
        <AccountRow
          acct={acct}
          onNetLiqClick={() => setNetLiqModalOpen(true)}
          onDayPnlClick={() => setDayPnlModalOpen(true)}
          onUnrealizedClick={() => setUnrealizedModalOpen(true)}
          onDividendsClick={() => setDividendsModalOpen(true)}
        />
      ) : (
        <LegacyLeverageRow portfolio={portfolio} pnl={pnl} pnlPct={pnlPct} />
      )}

      {/* Row 2: RISK */}
      {acct && (
        <RiskRow
          acct={acct}
          onBuyingPowerClick={() => setBuyingPowerModalOpen(true)}
          onMarginClick={() => setMarginModalOpen(true)}
          onExcessLiqClick={() => setExcessLiqModalOpen(true)}
          onSettledCashClick={() => setSettledCashModalOpen(true)}
        />
      )}

      {/* Row 3: EXPOSURE (real-time, all 4 clickable) */}
      <ExposureRow exposure={exposure} onCardClick={setActiveMetric} />

      {/* Row 4: TODAY'S P&L — renamed "Unrealized" → "Day Move" */}
      <TodayPnlRow
        todayUnrealized={todayUnrealized}
        hasDaily={hasDaily}
        unrealized={unrealized}
        realized={realized}
        total={total}
        realizedPnl={realizedPnl}
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
          onClose={() => setActiveMetric(null)}
        />
      )}

      {/* ACCOUNT: Unrealized P&L → position-level open P&L (entry cost vs market value) */}
      <PnlBreakdownModal
        open={unrealizedModalOpen}
        title="Unrealized P&L — Open Positions"
        formula={
          "Unrealized P&L = SUM( market_value − entry_cost ) per position\n" +
          "Source: IB market data synced via IB Gateway"
        }
        col1Header="ENTRY COST"
        col2Header="MKT VALUE"
        rows={unrealizedBreakdownRows}
        total={acct?.unrealized_pnl ?? pnl}
        onClose={() => setUnrealizedModalOpen(false)}
      />

      {/* TODAY'S P&L: Day Move → per-position intraday change (close → current) */}
      <PnlBreakdownModal
        open={dayMoveModalOpen}
        title="Day Move — Intraday P&L"
        formula={
          "Day Move = SUM( sign × (last − close) × contracts × multiplier ) per position\n" +
          "sign = +1 LONG, −1 SHORT  |  multiplier = 100 for options, 1 for stocks\n" +
          "Source: Live IB realtime prices vs yesterday's closing price"
        }
        col1Header="CLOSE"
        col2Header="CURRENT"
        rows={dayMoveBreakdown.rows}
        total={unrealized}
        onClose={() => setDayMoveModalOpen(false)}
      />

      {/* TODAY'S P&L: Realized → session fills with P&L */}
      <FillsModal
        open={fillsModalOpen}
        fills={executedOrders}
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
          value={acct.daily_pnl != null ? fmtSignedExact(acct.daily_pnl) : "---"}
          formula={
            "Day P&L = SUM( current_price − yesterday_close ) × position_size\n" +
            "Source: Interactive Brokers reqPnL() — account-level, updated in real-time\n" +
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

      {/* TODAY'S P&L: Total → formula proof */}
      <PnlBreakdownModal
        open={totalModalOpen}
        title="Today's Total P&L"
        formula={
          `Total = Day Move + Realized\n` +
          `      = ${unrealized >= 0 ? "+" : ""}$${Math.abs(unrealized).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (day move)` +
          `  ${realized >= 0 ? "+" : "−"}  $${Math.abs(realized).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (fills)\n` +
          `      = ${total >= 0 ? "+" : ""}$${Math.abs(total).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        }
        col1Header="COMPONENT"
        col2Header="SOURCE"
        rows={[
          {
            id: "day-move",
            ticker: "DAY MOVE",
            structure: `${todayUnrealized?.positionsWithData ?? 0} of ${todayUnrealized?.totalPositions ?? 0} positions`,
            col1: "Day Move",
            col2: "Live WS prices",
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
