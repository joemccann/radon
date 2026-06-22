"use client";

import { ShieldAlert } from "lucide-react";
import {
  correlationRiskBanner,
  type RiskBudgetReport,
} from "@/lib/correlationRiskBanner";

/**
 * Gate-3 correlation risk-budget banner.
 *
 * Renders the concentration verdict from `build_risk_budget_report`: when a
 * cluster of highly-correlated positions exceeds the book budget it shows a
 * critical Gate-3 warning; otherwise a calm or informational note. Guardrail
 * only — it never proposes resizing.
 */

const LEVEL_TOKEN: Record<string, string> = {
  critical: "var(--fault)",
  info: "var(--signal-core)",
  none: "var(--ok)",
};

export default function CorrelationRiskBanner({
  report,
}: {
  report: RiskBudgetReport | null | undefined;
}) {
  const banner = correlationRiskBanner(report);
  if (!banner || banner.level === "none") return null;

  const accent = LEVEL_TOKEN[banner.level] ?? "var(--signal-core)";

  return (
    <div
      className="sx"
      data-testid="correlation-risk-banner"
      data-level={banner.level}
      style={{
        borderRadius: 4,
        borderLeft: `3px solid ${accent}`,
        background: `color-mix(in srgb, ${accent} 8%, transparent)`,
      }}
    >
      <div className="s-hd">
        <div className="s-tt">
          <ShieldAlert size={14} style={{ color: accent }} />
          Correlation Risk Budget
        </div>
        <span
          className="pill"
          style={{
            color: accent,
            background: `color-mix(in srgb, ${accent} 14%, transparent)`,
          }}
        >
          GATE {banner.gate}
        </span>
      </div>
      <div className="s-bd">
        <div className="crb-headline" style={{ color: accent, fontWeight: 600 }}>
          {banner.headline}
        </div>
        <div className="crb-detail pe">{banner.detail}</div>

        {banner.breachedClusters.length > 0 && (
          <div className="crb-clusters" data-testid="crb-clusters">
            {banner.breachedClusters.map((cluster) => (
              <div key={cluster.tickers.join("-")} className="crb-cluster-row">
                <span className="fm">{cluster.tickers.join(" + ")}</span>
                <span className="fm" style={{ color: accent }}>
                  {cluster.exposurePctLabel} vs {cluster.budgetPctLabel} budget
                </span>
                <span className="fm pe">corr {cluster.maxPairCorrLabel}</span>
              </div>
            ))}
          </div>
        )}

        {banner.insufficientData.length > 0 && (
          <div className="crb-thin pe" data-testid="crb-insufficient-data">
            Insufficient price history for: {banner.insufficientData.join(", ")}
          </div>
        )}
      </div>
    </div>
  );
}
