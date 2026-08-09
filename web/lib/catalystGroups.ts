import type { CatalystRow, CatalystType } from "./useCatalysts";
import { catalystBadge } from "./catalystBadge";
import { upcomingCatalysts } from "./catalystUpcoming";

export type CatalystGroups = {
  today: CatalystRow[];
  thisWeek: CatalystRow[];
  positions: CatalystRow[];
};

/** Non-position rows beyond this many days drop off the quadrant. */
const WEEK_WINDOW_DAYS = 5;

/**
 * Split an upcoming-catalyst snapshot into the dashboard quadrant's three
 * groups. Position-linked rows (ticker held in the portfolio) surface only
 * under POSITIONS — at any upcoming distance — so a held ticker's earnings
 * never dupes into TODAY/THIS WEEK.
 */
export function groupCatalysts(
  rows: CatalystRow[],
  positionTickers: ReadonlySet<string>,
  now: Date = new Date(),
): CatalystGroups {
  const upcoming = upcomingCatalysts(rows, now);

  const today: CatalystRow[] = [];
  const thisWeek: CatalystRow[] = [];
  const positions: CatalystRow[] = [];

  for (const row of upcoming) {
    if (row.ticker && positionTickers.has(row.ticker)) {
      positions.push(row);
      continue;
    }
    if (row.days_until === 0) {
      today.push(row);
    } else if (row.days_until <= WEEK_WINDOW_DAYS) {
      thisWeek.push(row);
    }
  }

  return { today, thisWeek, positions };
}

const KIND_LABEL: Record<CatalystType, string> = {
  economic: "ECON",
  earnings: "ER",
  fda: "FDA",
};

export function catalystKindLabel(type: CatalystType): string {
  return KIND_LABEL[type] ?? String(type).toUpperCase();
}

export function catalystWhenLabel(row: CatalystRow): string {
  if (row.event_time) {
    const ms = Date.parse(row.event_time);
    if (Number.isFinite(ms)) {
      const time = new Date(ms).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: "America/New_York",
      });
      return `${time} ET`;
    }
  }
  return catalystBadge(row.days_until).label;
}
