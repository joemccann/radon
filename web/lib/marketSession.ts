/**
 * Most-recent expected / completed trading-session dates (ET, YYYY-MM-DD).
 *
 * Staleness checks compare a payload's session date against "the session we
 * should have data for", not the raw calendar date, so weekends, pre-open and
 * holidays do not flag finalized data stale. Holiday-aware; the calendar
 * lives in lib/marketCalendar.ts.
 */
export { lastCompletedSessionDate, mostRecentSessionDate } from "./marketCalendar";
