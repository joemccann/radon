import { isUsTradingDay } from "@/lib/serviceHealthWindows";

const MARKET_TIME_ZONE = "America/New_York";

function datePartsInMarketTime(date: Date): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: MARKET_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes): number =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  return { year: value("year"), month: value("month"), day: value("day") };
}

export function marketDateKey(date: Date): string {
  const { year, month, day } = datePartsInMarketTime(date);
  return `${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}-${day.toString().padStart(2, "0")}`;
}

export function shiftDateKey(dateKey: string, days: number): string {
  const [year, month, day] = dateKey.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days, 12));
  return shifted.toISOString().slice(0, 10);
}

export function businessDateKeys(count: number, now: Date): string[] {
  const dates: string[] = [];
  let cursor = marketDateKey(now);
  while (dates.length < count) {
    if (isUsTradingDay(cursor)) dates.push(cursor);
    cursor = shiftDateKey(cursor, -1);
  }
  return dates.reverse();
}

export function nextFridayDateKey(dateKey: string, minimumDays = 0): string {
  let cursor = shiftDateKey(dateKey, Math.max(0, Math.trunc(minimumDays)));
  while (new Date(`${cursor}T12:00:00.000Z`).getUTCDay() !== 5) {
    cursor = shiftDateKey(cursor, 1);
  }
  return cursor;
}

export function compactDate(dateKey: string): string {
  return dateKey.replaceAll("-", "");
}
