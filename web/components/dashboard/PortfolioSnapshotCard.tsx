"use client";

import type { PortfolioData } from "@/lib/types";
import { fmtMoney, fmtMoneySigned } from "@/lib/format/money";
import { useViewport } from "@/lib/useViewport";
import { MetricCell } from "@/components/mobile/MetricCell";

type Props = {
  portfolio: PortfolioData | null;
  realizedPnl?: number;
};

function pnlTone(value: number | null | undefined): "core" | "fault" | "neutral" {
  if (value == null || value === 0) return "neutral";
  return value > 0 ? "core" : "fault";
}

function metricTone(value: number | null | undefined): "pos" | "neg" | "mut" {
  if (value == null || value === 0) return "mut";
  return value > 0 ? "pos" : "neg";
}

/**
 * PortfolioSnapshotCard — the top-of-dashboard portfolio summary. Net Liq,
 * Today P&L, Open Risk (deployed capital), and free cash. Reads from the
 * existing portfolio prop wired by WorkspaceShell — no new data plumbing.
 */
export function PortfolioSnapshotCard({ portfolio, realizedPnl = 0 }: Props) {
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  const acct = portfolio?.account_summary;
  const netLiq = acct?.net_liquidation ?? null;
  const ibDaily = acct?.daily_pnl ?? null;
  // Prefer IB's streamed dailyPnL when available; fall back to realized fills.
  const todayPnl = ibDaily ?? (realizedPnl !== 0 ? realizedPnl : null);
  const cash = acct?.cash ?? acct?.settled_cash ?? null;
  const openRisk = portfolio?.total_deployed_dollars ?? null;
  const todayTone = pnlTone(todayPnl);

  if (mobile) {
    // Mobile: 2x2 MetricCell grid. Drop the panel-eyebrow/title header overhead —
    // the section toggle above already labels this block.
    return (
      <div className="snap-mobile-grid">
        <MetricCell label="Net Liq" value={fmtMoney(netLiq)} size="primary" />
        <MetricCell
          label="Today P&L"
          value={fmtMoneySigned(todayPnl)}
          size="primary"
          tone={metricTone(todayPnl)}
        />
        <MetricCell label="Open Risk" value={fmtMoney(openRisk)} size="secondary" />
        <MetricCell label="Cash" value={fmtMoney(cash)} size="secondary" />
      </div>
    );
  }

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Portfolio / 01</p>
        <h3 className="panel-title">Account</h3>
      </header>
      <div className="snapshot-grid snapshot-grid--portfolio">
        <div className="snapshot-cell">
          <span className="snapshot-cell__label">Net Liquidation</span>
          <span className="snapshot-cell__value">{fmtMoney(netLiq)}</span>
        </div>
        <div className="snapshot-cell">
          <span className="snapshot-cell__label">Today P&amp;L</span>
          <span className={`snapshot-cell__value snapshot-cell__value--${todayTone}`}>
            {fmtMoneySigned(todayPnl)}
          </span>
        </div>
        <div className="snapshot-cell">
          <span className="snapshot-cell__label">Open Risk</span>
          <span className="snapshot-cell__value">{fmtMoney(openRisk)}</span>
        </div>
        <div className="snapshot-cell">
          <span className="snapshot-cell__label">Cash</span>
          <span className="snapshot-cell__value">{fmtMoney(cash)}</span>
        </div>
      </div>
    </section>
  );
}
