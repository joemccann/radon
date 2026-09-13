/**
 * Period P&L intent for the assistant loop.
 *
 * A "what is my September 2026 P&L" prompt is one get_realized_pnl call, not a
 * catalog walk. Parsing lives here (pure) so the loop can prefetch the window
 * and so a cap-hit / empty Grok completion can still render the dollars.
 */

export type PnlWindow = { from: string; to: string };

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const PNL_HINT =
  /\b(p\s*&\s*l|p\/l|pnl|profit|loss|realized|trades?|fills?|journal|performance|round trips?)\b/i;

const MONTHS: Record<string, number> = {
  january: 1, jan: 1,
  february: 2, feb: 2,
  march: 3, mar: 3,
  april: 4, apr: 4,
  may: 5,
  june: 6, jun: 6,
  july: 7, jul: 7,
  august: 8, aug: 8,
  september: 9, sept: 9, sep: 9,
  october: 10, oct: 10,
  november: 11, nov: 11,
  december: 12, dec: 12,
};

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function utcNoon(year: number, month1: number, day: number): Date {
  return new Date(Date.UTC(year, month1 - 1, day, 12));
}

function isoFromUtcNoon(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function parseIsoDay(day: string): { year: number; month: number; day: number } | null {
  if (!ISO_DAY.test(day)) return null;
  const year = Number(day.slice(0, 4));
  const month = Number(day.slice(5, 7));
  const d = Number(day.slice(8, 10));
  if (!year || month < 1 || month > 12 || d < 1 || d > 31) return null;
  return { year, month, day: d };
}

function addDays(day: string, delta: number): string {
  const parsed = parseIsoDay(day);
  if (!parsed) return day;
  const date = utcNoon(parsed.year, parsed.month, parsed.day);
  date.setUTCDate(date.getUTCDate() + delta);
  return isoFromUtcNoon(date);
}

function lastDayOfMonth(year: number, month1: number): number {
  return new Date(Date.UTC(year, month1, 0)).getUTCDate();
}

function mondayOnOrBefore(day: string): string {
  const parsed = parseIsoDay(day);
  if (!parsed) return day;
  const date = utcNoon(parsed.year, parsed.month, parsed.day);
  const dow = date.getUTCDay();
  const offset = dow === 0 ? 6 : dow - 1;
  date.setUTCDate(date.getUTCDate() - offset);
  return isoFromUtcNoon(date);
}

function monthYearWindow(text: string): PnlWindow | null {
  const match = text.match(
    /\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{4})\b/i,
  );
  if (!match) return null;
  const month = MONTHS[match[1].toLowerCase().replace(/\.$/, "")];
  const year = Number(match[2]);
  if (!month || !year) return null;
  return {
    from: `${year}-${pad2(month)}-01`,
    to: `${year}-${pad2(month)}-${pad2(lastDayOfMonth(year, month))}`,
  };
}

function isoRangeWindow(text: string): PnlWindow | null {
  const match = text.match(/(\d{4}-\d{2}-\d{2}).{1,24}(\d{4}-\d{2}-\d{2})/);
  if (!match) return null;
  const from = match[1];
  const to = match[2];
  if (!ISO_DAY.test(from) || !ISO_DAY.test(to) || from > to) return null;
  return { from, to };
}

export function detectRealizedPnlWindow(text: string, todayEt: string): PnlWindow | null {
  if (!PNL_HINT.test(text) || !ISO_DAY.test(todayEt)) return null;

  const iso = isoRangeWindow(text);
  if (iso) return iso;

  const month = monthYearWindow(text);
  if (month) return month;

  if (/\b(this month|mtd|month to date)\b/i.test(text)) {
    return { from: `${todayEt.slice(0, 8)}01`, to: todayEt };
  }
  if (/\b(ytd|year to date|this year)\b/i.test(text)) {
    return { from: `${todayEt.slice(0, 4)}-01-01`, to: todayEt };
  }
  if (/\bthis week\b/i.test(text)) {
    return { from: mondayOnOrBefore(todayEt), to: todayEt };
  }
  if (/\b(weekly|last 7 days|past week)\b/i.test(text)) {
    return { from: addDays(todayEt, -6), to: todayEt };
  }
  if (/\blast week\b/i.test(text)) {
    const thisMonday = mondayOnOrBefore(todayEt);
    return { from: addDays(thisMonday, -7), to: addDays(thisMonday, -1) };
  }
  if (/\b(today|today's)\b/i.test(text)) {
    return { from: todayEt, to: todayEt };
  }
  return null;
}

function asPnlSummary(data: unknown): Record<string, unknown> | null {
  if (!data || typeof data !== "object") return null;
  const row = data as Record<string, unknown>;
  if (typeof row.total_realized_pnl === "number" && Number.isFinite(row.total_realized_pnl)) return row;
  const body = row.body;
  if (body && typeof body === "object" && !Array.isArray(body)) {
    const inner = body as Record<string, unknown>;
    if (typeof inner.total_realized_pnl === "number" && Number.isFinite(inner.total_realized_pnl)) return inner;
  }
  return null;
}

export function formatRealizedPnlAnswer(data: unknown): string | null {
  const row = asPnlSummary(data);
  if (!row || typeof row.total_realized_pnl !== "number") return null;
  const from = typeof row.from === "string" ? row.from : "";
  const to = typeof row.to === "string" ? row.to : "";
  const count = typeof row.count === "number" ? row.count : 0;
  const total = row.total_realized_pnl;
  const signed = `${total >= 0 ? "+" : ""}${total.toFixed(2)}`;
  const window = from && to ? ` ${from} to ${to}` : "";
  const tripLabel = count === 1 ? "round trip" : "round trips";
  const lines = [`Realized P&L${window}: ${signed} USD across ${count} ${tripLabel}.`];
  const trips = Array.isArray(row.round_trips) ? row.round_trips : [];
  for (const raw of trips.slice(0, 25)) {
    if (!raw || typeof raw !== "object") continue;
    const trip = raw as Record<string, unknown>;
    if (typeof trip.realized_pnl !== "number" || !Number.isFinite(trip.realized_pnl)) continue;
    const ticker = typeof trip.ticker === "string" && trip.ticker ? trip.ticker : "?";
    const closed = typeof trip.closed === "string" ? trip.closed : "";
    const pnlSigned = `${trip.realized_pnl >= 0 ? "+" : ""}${trip.realized_pnl.toFixed(2)}`;
    lines.push(`${ticker}${closed ? ` ${closed}` : ""} ${pnlSigned}`);
  }
  if (typeof row.note === "string" && row.note.trim()) lines.push(row.note.trim());
  return lines.join("\n");
}
