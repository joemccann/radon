import type { CatalystRow, CatalystType } from "./useCatalysts";
import { catalystBadge } from "./catalystBadge";
import { upcomingCatalysts } from "./catalystUpcoming";

/** A catalyst row tagged with whether its ticker is currently held. */
export type CategorizedCatalystRow = CatalystRow & { isHeld: boolean };

export type CatalystCategory = {
  key: string;
  label: string;
  rows: CategorizedCatalystRow[];
  heldCount: number;
};

/** Non-position rows beyond this many days drop off the quadrant. */
const WEEK_WINDOW_DAYS = 5;

/** Operator-specified reading order; anything else follows alphabetically. */
const CATEGORY_ORDER = ["economic", "earnings", "fda"];

const CATEGORY_LABEL: Record<string, string> = {
  economic: "ECONOMIC DATA",
  earnings: "EARNINGS",
  fda: "FDA",
};

function categoryLabel(key: string): string {
  return CATEGORY_LABEL[key] ?? key.replace(/[_-]+/g, " ").toUpperCase();
}

function categoryRank(key: string): number {
  const index = CATEGORY_ORDER.indexOf(key);
  return index === -1 ? CATEGORY_ORDER.length : index;
}

function compareCategories(a: CatalystCategory, b: CatalystCategory): number {
  const rank = categoryRank(a.key) - categoryRank(b.key);
  return rank !== 0 ? rank : a.key.localeCompare(b.key);
}

/** Held rows lead their category so exposure survives the collapsed preview. */
function compareRows(a: CategorizedCatalystRow, b: CategorizedCatalystRow): number {
  if (a.isHeld !== b.isHeld) return a.isHeld ? -1 : 1;
  return a.days_until - b.days_until;
}

/**
 * Split an upcoming-catalyst snapshot into per-category sections in the
 * dashboard's reading order (economic data, earnings, FDA, then the rest).
 * Rows whose ticker is held survive at any upcoming distance and sort to the
 * top of their category; everything else is bounded to the trading week.
 */
export function groupCatalystsByCategory(
  rows: CatalystRow[],
  positionTickers: ReadonlySet<string>,
  now: Date = new Date(),
): CatalystCategory[] {
  const byKey = new Map<string, CatalystCategory>();

  for (const row of upcomingCatalysts(rows, now)) {
    const isHeld = Boolean(row.ticker && positionTickers.has(row.ticker));
    if (!isHeld && row.days_until > WEEK_WINDOW_DAYS) continue;

    const key = String(row.type);
    const category =
      byKey.get(key) ?? { key, label: categoryLabel(key), rows: [], heldCount: 0 };
    category.rows.push({ ...row, isHeld });
    if (isHeld) category.heldCount += 1;
    byKey.set(key, category);
  }

  const categories = [...byKey.values()];
  for (const category of categories) category.rows.sort(compareRows);
  return categories.sort(compareCategories);
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
