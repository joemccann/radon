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

export default function CorrelationRiskBanner({
  report,
  showUnavailable = false,
}: {
  report: RiskBudgetReport | null | undefined;
  showUnavailable?: boolean;
}) {
  const banner = correlationRiskBanner(report);
  const measurementUnavailable =
    showUnavailable && (!banner || (banner.level === "none" && banner.insufficientData.length > 0));
  if ((!banner || banner.level === "none") && !measurementUnavailable) return null;

  const level = measurementUnavailable ? "info" : banner!.level;
  const headline = measurementUnavailable ? "Gate 3: correlation measurement unavailable" : banner!.headline;
  const detail = measurementUnavailable
    ? "Current portfolio price history is insufficient for a correlation risk-budget verdict."
    : banner!.detail;
  const breachedClusters = measurementUnavailable ? [] : banner!.breachedClusters;
  const insufficientData = banner?.insufficientData ?? [];

  return (
    <div
      className="crb"
      data-testid="correlation-risk-banner"
      data-level={level}
    >
      <div className="crb-header">
        <div className="crb-title">
          <ShieldAlert size={14} className="crb-icon" aria-hidden="true" />
          Correlation Risk Budget
        </div>
        <span className="pill crb-gate">GATE 3</span>
      </div>
      <div className="crb-body">
        <div className="crb-headline">{headline}</div>
        <div className="crb-detail">{detail}</div>

        {breachedClusters.length > 0 && (
          <div className="crb-clusters" data-testid="crb-clusters">
            {breachedClusters.map((cluster) => (
              <div key={cluster.tickers.join("-")} className="crb-cluster-row">
                <span className="crb-cluster-names">{cluster.tickers.join(" + ")}</span>
                <span className="crb-cluster-budget">
                  {cluster.exposurePctLabel} vs {cluster.budgetPctLabel} budget
                </span>
                <span className="crb-cluster-corr">corr {cluster.maxPairCorrLabel}</span>
              </div>
            ))}
          </div>
        )}

        {insufficientData.length > 0 && (
          <div className="crb-thin" data-testid="crb-insufficient-data">
            <div className="crb-tickers">
              {insufficientData.map((ticker) => (
                <span key={ticker} className="crb-ticker">{ticker}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
