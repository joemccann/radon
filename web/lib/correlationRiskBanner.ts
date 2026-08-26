/**
 * Pure derivation of the Gate-3 correlation risk-budget banner.
 *
 * Mirrors the shape produced by `scripts/portfolio_risk.py:build_risk_budget_report`.
 * Turns the structured report into presentation props (level, headline, cluster
 * rows) so the banner component stays dumb. No DOM, no fetch — fully unit-testable.
 *
 * This is a guardrail surface, not an optimizer: it tells the operator that
 * several positions are secretly the same bet and that the cluster exceeds the
 * book budget. It never proposes resizing.
 */

export type RiskBudgetCluster = {
  tickers: string[];
  aggregate_exposure: number; // fraction of bankroll
  budget: number; // fraction of bankroll
  breached: boolean;
  max_pair_corr: number | null;
  per_ticker_exposure: Record<string, number>;
};

export type RiskBudgetReport = {
  clusters: RiskBudgetCluster[];
  breaches: RiskBudgetCluster[];
  aggregate_exposure: number;
  insufficient_data: string[];
  corr_threshold: number;
  book_budget: number;
};

export type CorrelationBannerLevel = "none" | "info" | "unmeasured" | "critical";

export type CorrelationClusterRow = {
  tickers: string[];
  exposurePctLabel: string;
  budgetPctLabel: string;
  maxPairCorrLabel: string;
};

export type CorrelationRiskBanner = {
  gate: 3;
  level: CorrelationBannerLevel;
  headline: string;
  detail: string;
  clusterCount: number;
  breachedClusters: CorrelationClusterRow[];
  insufficientData: string[];
};

function fmtFractionPct(fraction: number, decimals = 1): string {
  return `${(fraction * 100).toFixed(decimals)}%`;
}

function fmtCorr(corr: number | null): string {
  if (corr === null) return "n/a";
  return corr.toFixed(2);
}

function toClusterRow(cluster: RiskBudgetCluster): CorrelationClusterRow {
  return {
    tickers: cluster.tickers,
    exposurePctLabel: fmtFractionPct(cluster.aggregate_exposure),
    budgetPctLabel: fmtFractionPct(cluster.budget),
    maxPairCorrLabel: fmtCorr(cluster.max_pair_corr),
  };
}

export function correlationRiskBanner(
  report: RiskBudgetReport | null | undefined,
): CorrelationRiskBanner | null {
  if (!report) return null;

  const breachedClusters = report.breaches.map(toClusterRow);
  const insufficientData = report.insufficient_data ?? [];

  if (breachedClusters.length > 0) {
    const names = breachedClusters
      .map((c) => c.tickers.join("+"))
      .join(", ");
    return {
      gate: 3,
      level: "critical",
      headline: `Gate 3: correlated exposure over budget (${names})`,
      detail:
        "Highly-correlated positions stack into a single concentrated bet. " +
        "Trim or hedge before adding correlated risk.",
      clusterCount: report.clusters.length,
      breachedClusters,
      insufficientData,
    };
  }

  if (report.clusters.length > 0) {
    const measured = `${report.clusters.length} correlated cluster${
      report.clusters.length === 1 ? "" : "s"
    }`;

    // Gate 3 may not return a clean verdict on a book it only partly measured:
    // the backfill ladder leaves underlyings without price history unmeasured
    // for long stretches, so a "within budget" headline would be a false all
    // clear over exposure that was never correlated at all.
    if (insufficientData.length > 0) {
      const unmeasured = `${insufficientData.length} position${
        insufficientData.length === 1 ? "" : "s"
      }`;
      return {
        gate: 3,
        level: "unmeasured",
        headline: `Gate 3: partial correlation read, ${unmeasured} unmeasured`,
        detail:
          `${measured} ${report.clusters.length === 1 ? "sits" : "sit"} inside ` +
          `the book budget, but ${unmeasured} ` +
          `${insufficientData.length === 1 ? "lacks" : "lack"} the price ` +
          "history to correlate. This is not a full book verdict.",
        clusterCount: report.clusters.length,
        breachedClusters: [],
        insufficientData,
      };
    }

    return {
      gate: 3,
      level: "info",
      headline: `Gate 3: ${report.clusters.length} correlated cluster${
        report.clusters.length === 1 ? "" : "s"
      } within budget`,
      detail:
        "Correlated positions detected but aggregate exposure is within the book budget.",
      clusterCount: report.clusters.length,
      breachedClusters: [],
      insufficientData,
    };
  }

  return {
    gate: 3,
    level: "none",
    headline: "Gate 3: no correlated concentration",
    detail: "No clustered correlated exposure detected.",
    clusterCount: 0,
    breachedClusters: [],
    insufficientData,
  };
}
