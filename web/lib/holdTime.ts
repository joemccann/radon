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
  const year = m ? Number(m[1]) : null;
  const month = m ? Number(m[2]) : null;
  const day = m ? Number(m[3]) : null;
  const d = m ? new Date(year!, month! - 1, day!) : new Date(input);
  if (m && (d.getFullYear() !== year || d.getMonth() !== month! - 1 || d.getDate() !== day)) return null;
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * A TRADING day, named explicitly rather than inherited from the process.
 *
 * `vitest.config.ts` pins `TZ=America/New_York` for the suite and nothing pins
 * TZ for the runtime, so every `getFullYear()/getMonth()/getDate()` here used
 * to answer ET in the tests and UTC on the server: an exit stamped
 * `2026-08-29T01:00:00Z` is Aug 28 in the pinned suite and Aug 29 in
 * production, flipping same-day-trade classification and the entry-before-exit
 * rejection between test and prod. The pin made TZ-independence untestable
 * rather than guaranteed. A trading day is ET by definition, so say so. R-405.
 */
const EXCHANGE_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** `YYYYMMDD` as a number, in the exchange timezone. */
function exchangeDayNumber(input: string | null | undefined, parsed: Date): number {
  const m = DATE_ONLY_RE.exec(input ?? "");
  // A bare "YYYY-MM-DD" already IS a calendar day; converting it through a
  // timezone would shift it for any viewer west of the exchange.
  if (m) return Number(m[1]) * 10_000 + Number(m[2]) * 100 + Number(m[3]);
  const [y, mo, d] = EXCHANGE_DAY.format(parsed).split("-");
  return Number(y) * 10_000 + Number(mo) * 100 + Number(d);
}

/**
 * True when `candidate` parses to an earlier LOCAL calendar day than
 * `reference`. Used to reject fallback entry dates that cannot precede the
 * exit they are paired with. False when either side is unparseable.
 */
export function isEarlierLocalDay(
  candidate: string | null | undefined,
  reference: string | null | undefined,
): boolean {
  const a = parseLocal(candidate);
  const b = parseLocal(reference);
  if (!a || !b) return false;
  return exchangeDayNumber(candidate, a) < exchangeDayNumber(reference, b);
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

  // A date-only entry carries no intra-day information: when it falls on the
  // exit's own local day, the only measurable span is "since local midnight",
  // which fabricates an hours-scale hold. Omit the field instead.
  if (
    entryTime != null
    && DATE_ONLY_RE.test(entryTime)
    && exchangeDayNumber(entryTime, entry) === exchangeDayNumber(exitTime, exit)
  ) {
    return null;
  }

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
