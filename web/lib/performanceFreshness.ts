import { scanTimeToEtDate } from "./parseScanTime";

type PerformanceFreshness = {
  as_of?: string | null;
  last_sync?: string | null;
} | null | undefined;

const ET_TIME_ZONE = "America/New_York";

/**
 * `last_sync` is a wall-clock timestamp produced by `scripts/ib_sync.py`.
 * On Hetzner (UTC host) older builds wrote `datetime.now().isoformat()` —
 * a naive ISO string. Naive slicing of those strings rolls the ET session
 * date forward the moment UTC midnight passes (~20:00 ET), even though
 * it is still the same trading day in ET.
 *
 * Treat naive strings as UTC and convert to the ET calendar day so the
 * portfolio freshness gate matches the trading session a human would.
 */
export function portfolioAsOfFromLastSync(lastSync: string | null | undefined): string | null {
  if (!lastSync || lastSync.length < 10) return null;
  return scanTimeToEtDate(lastSync);
}

function formatEtDate(value: Date): string {
  return value.toLocaleDateString("sv", { timeZone: ET_TIME_ZONE });
}

/**
 * Weekday evaluated IN ET from the true instant. Never re-parse a
 * toLocaleString() rendering back into a Date: that interprets ET wall
 * time as a host-local instant and the next ET conversion shifts it a
 * second time, rolling the session date early near ET midnight (the
 * offset depends on the host timezone, so the bug is invisible on UTC
 * CI and live on a PT laptop).
 */
function isTradingWeekdayEt(value: Date): boolean {
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: ET_TIME_ZONE,
    weekday: "short",
  }).format(value);
  return weekday !== "Sat" && weekday !== "Sun";
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function latestPortfolioTargetDateET(now: Date = new Date()): string {
  let candidate = now;

  while (!isTradingWeekdayEt(candidate)) {
    candidate = new Date(candidate.getTime() - DAY_MS);
  }

  return formatEtDate(candidate);
}

export function isPortfolioBehindCurrentEtSession(
  portfolioLastSync: string | null | undefined,
  targetDate: string = latestPortfolioTargetDateET(),
): boolean {
  const portfolioAsOf = portfolioAsOfFromLastSync(portfolioLastSync);
  return portfolioAsOf !== null && portfolioAsOf < targetDate;
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
