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

export type CorrelationBannerLevel =
  | "none"
  | "info"
  | "unmeasured"
  | "critical"
  | "reduce";

/**
 * What the WORKING ORDER under review does to the book. Gate 3 guards against
 * ADDING to a concentrated bet; a close that reduces a breached cluster is the
 * trim the gate asks for, so the breach must not present as a block on it.
 */
export type CorrelationOrderContext = {
  ticker: string;
  /** True when the order closes or reduces held exposure in `ticker`
   *  (the chokepoint's close-out branch). */
  reducesExposure: boolean;
};

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
  order?: CorrelationOrderContext | null,
): CorrelationRiskBanner | null {
  if (!report) return null;

  const insufficientData = report.insufficient_data ?? [];

  // A working order that reduces a ticker inside a breached cluster IS the
  // trim Gate 3 asks for. Its cluster must not present as a block on the
  // reduce; clusters the order does not touch stay critical.
  const orderTicker = order?.ticker?.toUpperCase() ?? null;
  const isReduce = order?.reducesExposure === true && orderTicker != null;
  const reducedBreaches = isReduce
    ? report.breaches.filter((cluster) =>
        cluster.tickers.some((t) => t.toUpperCase() === orderTicker),
      )
    : [];
  const activeBreaches = report.breaches.filter(
    (cluster) => !reducedBreaches.includes(cluster),
  );
  const breachedClusters = activeBreaches.map(toClusterRow);

  if (breachedClusters.length > 0) {
    const names = breachedClusters
      .map((c) => c.tickers.join("+"))
      .join(", ");
    // Never tell the operator they are "adding" risk when the working order
    // is a close/reduce (2026-09-01 TQQQ flatten misread).
    const guidance = isReduce
      ? "This working order is a close and does not add to it. The cluster stays over budget until trimmed or hedged."
      : "Trim or hedge before adding correlated risk.";
    return {
      gate: 3,
      level: "critical",
      headline: `Gate 3: correlated exposure over budget (${names})`,
      detail:
        `Highly-correlated positions stack into a single concentrated bet. ${guidance}`,
      clusterCount: report.clusters.length,
      breachedClusters,
      insufficientData,
    };
  }

  if (reducedBreaches.length > 0) {
    const names = reducedBreaches
      .map((cluster) => cluster.tickers.join("+"))
      .join(", ");
    return {
      gate: 3,
      level: "reduce",
      headline: `Gate 3: this order reduces the ${names} stack`,
      detail:
        "The working order closes exposure inside the breached cluster. " +
        "Reducing is the action Gate 3 asks for; it is not blocked.",
      clusterCount: report.clusters.length,
      breachedClusters: [],
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

  // No clusters AND nothing measurable is the case where the LEAST is known,
  // so it must not produce the strongest all-clear. "No correlated
  // concentration detected" over a book whose price history never arrived
  // reads as a verdict; it is the absence of one. R-283.
  if (insufficientData.length > 0) {
    const unmeasured = `${insufficientData.length} position${
      insufficientData.length === 1 ? "" : "s"
    }`;
    return {
      gate: 3,
      level: "unmeasured",
      headline: "Gate 3: correlation unmeasured",
      detail:
        `No correlated clusters were found, but ${unmeasured} ` +
        `${insufficientData.length === 1 ? "lacks" : "lack"} the price history ` +
        "to correlate. This is not a clean-book verdict.",
      clusterCount: 0,
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
