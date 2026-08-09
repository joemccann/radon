"use client";

import type { PortfolioData } from "@/lib/types";
import { deriveKpis, type KpiTone } from "@/lib/dashboardKpis";
import { useViewport } from "@/lib/useViewport";
import { MetricCell } from "@/components/mobile/MetricCell";

type Props = {
  portfolio: PortfolioData | null;
  realizedPnl?: number;
};

function metricTone(tone: KpiTone): "pos" | "neg" | "mut" {
  if (tone === "core") return "pos";
  if (tone === "fault") return "neg";
  return "mut";
}

/**
 * KpiStrip — full-width account telemetry under the header: Net Liquidation,
 * Today P&L, Buying Power (capacity meter), Margin Used (load meter). Reads
 * the portfolio prop wired by WorkspaceShell — no new data plumbing.
 */
export function KpiStrip({ portfolio, realizedPnl = 0 }: Props) {
  const { isMobile, hasMounted } = useViewport();
  const cells = deriveKpis(portfolio, realizedPnl);

  if (isMobile && hasMounted) {
    return (
      <div className="snap-mobile-grid" data-testid="kpi-strip-mobile">
        {cells.map((cell) => (
          <MetricCell
            key={cell.key}
            label={cell.label}
            value={cell.display}
            size={cell.key === "netLiq" || cell.key === "todayPnl" ? "primary" : "secondary"}
            tone={metricTone(cell.tone)}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="dashboard-kpi" data-testid="kpi-strip">
      {cells.map((cell) => (
        <div key={cell.key} className="dashboard-kpi__cell">
          <span className="dashboard-kpi__label">{cell.label}</span>
          <span className="dashboard-kpi__reading">
            <span className={`dashboard-kpi__value dashboard-kpi__value--${cell.tone}`}>
              {cell.display}
            </span>
            {cell.meta ? <span className="dashboard-kpi__meta">{cell.meta}</span> : null}
          </span>
          {cell.barPct != null ? (
            <span className="dashboard-kpi__bar">
              <span
                className={`dashboard-kpi__bar-fill dashboard-kpi__bar-fill--${cell.barTone ?? "core"}`}
                style={{ width: `${cell.barPct.toFixed(1)}%` }}
              />
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
