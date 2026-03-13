type PerformanceFreshness = {
  as_of?: string | null;
  last_sync?: string | null;
} | null | undefined;

export function portfolioAsOfFromLastSync(lastSync: string | null | undefined): string | null {
  if (!lastSync || lastSync.length < 10) return null;
  return lastSync.slice(0, 10);
}

export function isPerformanceBehindPortfolioSync(
  performance: PerformanceFreshness,
  portfolioLastSync: string | null | undefined,
): boolean {
  const portfolioAsOf = portfolioAsOfFromLastSync(portfolioLastSync);
  if (!portfolioLastSync || !portfolioAsOf || !performance) return false;

  const performanceLastSync = performance.last_sync ?? null;
  const performanceAsOf = performance.as_of ?? null;

  return performanceLastSync !== portfolioLastSync || performanceAsOf !== portfolioAsOf;
}
