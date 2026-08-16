import { scanTimeToEtDate } from "./parseScanTime";

type PerformanceFreshness = {
  as_of?: string | null;
  last_sync?: string | null;
  /** Payload v2: the builder's own wall clock. */
  generated_at?: string | null;
  /** Payload v2: the last NAV session the snapshot measured through. */
  nav_as_of?: string | null;
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

/**
 * The ET session a payload v2 snapshot was BUILT in, or null when it carries no
 * build instant. `nav_as_of` is deliberately not a substitute: NAV is an
 * end-of-day figure and always trails a live portfolio sync by a session, so
 * reading it as a lag makes every snapshot permanently behind.
 */
function buildSessionEt(performance: NonNullable<PerformanceFreshness>): string | null {
  if (!performance.generated_at) return null;
  return scanTimeToEtDate(performance.generated_at);
}

/**
 * Whether the served performance snapshot predates the portfolio's own data.
 *
 * Payload v1 copied the portfolio's `last_sync` verbatim, so string equality
 * genuinely meant "built from this sync". Payload v2 emits `generated_at` and
 * `nav_as_of` instead, and `generated_at` is the builder's own wall clock — it
 * can never equal a portfolio sync instant. Applying the v1 equality test to a
 * v2 payload therefore answers "behind" for every row ever written, and each
 * GET fired a Flex rebuild against a single query id.
 *
 * A v2 snapshot is behind only when it was built in an EARLIER ET session than
 * the portfolio sync. Comparing instants instead would be true on nearly every
 * poll (the portfolio syncs minutes apart, the builder runs hourly), which is
 * the same request loop wearing a different shape.
 */
export function isPerformanceBehindPortfolioSync(
  performance: PerformanceFreshness,
  portfolioLastSync: string | null | undefined,
): boolean {
  const portfolioAsOf = portfolioAsOfFromLastSync(portfolioLastSync);
  if (!portfolioLastSync || !portfolioAsOf || !performance) return false;

  const builtOn = buildSessionEt(performance);
  if (builtOn !== null) return builtOn < portfolioAsOf;

  const performanceLastSync = performance.last_sync ?? null;
  const performanceAsOf = performance.as_of ?? null;

  return performanceLastSync !== portfolioLastSync || performanceAsOf !== portfolioAsOf;
}
