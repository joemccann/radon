import type { PortfolioData } from "@/lib/types";

type MetricCardsProps = {
  portfolio: PortfolioData | null;
};

const fmt = (n: number) =>
  n >= 1_000_000
    ? `$${(n / 1_000_000).toFixed(2)}M`
    : `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

function resolveMarketValue(pos: PortfolioData["positions"][number]): number | null {
  if (pos.market_value != null) return pos.market_value;
  const known = pos.legs.filter((l) => l.market_value != null);
  return known.length > 0 ? known.reduce((s, l) => s + l.market_value!, 0) : null;
}

function computePnL(portfolio: PortfolioData) {
  let totalPnL = 0;
  for (const pos of portfolio.positions) {
    const mv = resolveMarketValue(pos);
    if (mv != null) {
      totalPnL += mv - pos.entry_cost;
    }
  }
  return totalPnL;
}

export default function MetricCards({ portfolio }: MetricCardsProps) {
  if (!portfolio) {
    const placeholders = ["Net Liquidation", "Positions", "Deployed", "Open P&L"];
    return (
      <div className="metrics-grid">
        {placeholders.map((label, i) => (
          <div key={i} className="metric-card metric-card-loading">
            <div className="metric-label">{label}</div>
            <div className="metric-value">$0,000</div>
            <div className="metric-change neutral">AWAITING SYNC</div>
          </div>
        ))}
      </div>
    );
  }

  const pnl = computePnL(portfolio);
  const pnlPct = portfolio.total_deployed_dollars > 0
    ? (pnl / portfolio.total_deployed_dollars) * 100
    : 0;

  const cards = [
    {
      label: "Net Liquidation",
      value: fmt(portfolio.bankroll),
      change: "BANKROLL",
      tone: "neutral" as const,
    },
    {
      label: "Positions",
      value: String(portfolio.position_count),
      change: `${portfolio.defined_risk_count} DEFINED / ${portfolio.undefined_risk_count} UNDEFINED`,
      tone: "neutral" as const,
    },
    {
      label: "Deployed",
      value: fmt(portfolio.total_deployed_dollars),
      change: `${portfolio.total_deployed_pct.toFixed(1)}% OF BANKROLL`,
      tone: portfolio.total_deployed_pct > 100 ? ("negative" as const) : ("neutral" as const),
    },
    {
      label: "Open P&L",
      value: `${pnl >= 0 ? "+" : ""}${fmt(Math.abs(pnl))}`,
      change: `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%`,
      tone: pnl >= 0 ? ("positive" as const) : ("negative" as const),
    },
  ];

  return (
    <div className="metrics-grid">
      {cards.map((item) => (
        <div key={item.label} className="metric-card">
          <div className="metric-label">{item.label}</div>
          <div className="metric-value">{item.value}</div>
          <div
            className={`metric-change ${
              item.tone === "positive" ? "positive" : item.tone === "negative" ? "negative" : "neutral"
            }`}
          >
            {item.change}
          </div>
        </div>
      ))}
    </div>
  );
}
