import type { CatalystRow, CatalystType } from "./useCatalysts";
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
  if (a.days_until !== b.days_until) return a.days_until - b.days_until;
  const aTime = a.event_time ? Date.parse(a.event_time) : NaN;
  const bTime = b.event_time ? Date.parse(b.event_time) : NaN;
  if (Number.isFinite(aTime) && Number.isFinite(bTime)) return aTime - bTime;
  if (Number.isFinite(aTime)) return -1;
  if (Number.isFinite(bTime)) return 1;
  return 0;
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

function isBlankPrint(value: unknown): boolean {
  return value == null || (typeof value === "string" && value.trim() === "");
}

function trimCompactNumber(value: number): string {
  return String(Number(value.toFixed(1)));
}

/** Compact a print figure: 221000 -> 221k. Percents and short strings pass through. */
function compactCatalystPrint(value: string | number): string {
  const raw = String(value).trim();
  if (!raw) return "";
  if (raw.includes("%")) return raw;
  const numeric = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(numeric)) return raw;
  const abs = Math.abs(numeric);
  if (abs >= 1_000_000) return `${trimCompactNumber(numeric / 1_000_000)}m`;
  if (abs >= 1_000) return `${trimCompactNumber(numeric / 1_000)}k`;
  return raw;
}

function printPart(prefix: string, value: unknown): string {
  if (isBlankPrint(value)) return "";
  const compact = compactCatalystPrint(value as string | number);
  return compact ? `${prefix} ${compact}` : "";
}

function joinPrintParts(parts: string[]): string {
  return parts.filter(Boolean).join("  ");
}

export function catalystPrintLabel(row: CatalystRow): string {
  if (row.type === "economic") {
    if (!isBlankPrint(row.actual)) {
      return joinPrintParts([printPart("A", row.actual), printPart("F", row.forecast)]);
    }
    return joinPrintParts([printPart("F", row.forecast), printPart("P", row.prev)]);
  }
  if (row.type === "earnings") {
    if (!isBlankPrint(row.actual_eps)) {
      return joinPrintParts([printPart("A", row.actual_eps), printPart("F", row.street_mean_est)]);
    }
    return joinPrintParts([printPart("F", row.street_mean_est)]);
  }
  return "";
}

function catalystCalendarDateLabel(isoDate: string): string {
  const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return "";
  return new Date(Date.UTC(year, month - 1, day, 12)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export function catalystWhenLabel(row: CatalystRow): string {
  const dateLabel = catalystCalendarDateLabel(row.date);
  if (row.event_time) {
    const ms = Date.parse(row.event_time);
    if (Number.isFinite(ms)) {
      const time = new Date(ms).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        hourCycle: "h23",
        timeZone: "America/New_York",
      });
      return dateLabel ? `${dateLabel} ${time} ET` : `${time} ET`;
    }
  }
  // R-631: an untimed row sorts to the end of its day bucket with nothing
  // marking the time as unknown, so an economic print that actually lands at
  // 08:30 ET renders BELOW the 14:00 ET events and an operator reading the
  // list top-down as a clock misses it. Only same-day rows need this — a row
  // three days out is read as a date either way.
  if (row.days_until === 0) {
    return dateLabel ? `${dateLabel} time TBD` : "time TBD";
  }
  return dateLabel;
}
