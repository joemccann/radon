const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Parse a timestamp, treating a bare "YYYY-MM-DD" as LOCAL midnight (not UTC
 * midnight) so a viewer west of UTC does not see the day shift back one. Mirrors
 * the journal date-only convention in lib/blotter/formatTradeDate.ts. Returns
 * null for missing or unparseable input.
 */
function parseLocal(input: string | null | undefined): Date | null {
  if (!input) return null;
  const m = DATE_ONLY_RE.exec(input);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(input);
  return Number.isNaN(d.getTime()) ? null : d;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Natural-language total hold time between an entry and an exit timestamp, e.g.
 * "37 minutes", "18 hours", "2 days". A single dominant unit, rounded:
 * minutes under an hour, hours under a day, days otherwise. Returns null when
 * either timestamp is missing/unparseable or the exit precedes the entry, so
 * the caller omits the field rather than rendering a bad value.
 */
export function formatHoldDuration(
  entryTime: string | null | undefined,
  exitTime: string | null | undefined,
): string | null {
  const entry = parseLocal(entryTime);
  const exit = parseLocal(exitTime);
  if (!entry || !exit) return null;

  const elapsedMs = exit.getTime() - entry.getTime();
  if (elapsedMs < 0) return null;

  if (elapsedMs < HOUR) {
    const n = Math.max(1, Math.round(elapsedMs / MINUTE));
    return `${n} minute${n === 1 ? "" : "s"}`;
  }
  if (elapsedMs < DAY) {
    const n = Math.round(elapsedMs / HOUR);
    if (n < 24) return `${n} hour${n === 1 ? "" : "s"}`;
    // rounded up to a full day; fall through to the days bucket
  }
  const n = Math.max(1, Math.round(elapsedMs / DAY));
  return `${n} day${n === 1 ? "" : "s"}`;
}
