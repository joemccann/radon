// Newsfeed timestamp formatters.
// Locale and hour12 are pinned because `undefined` locale resolved to a Chrome
// configuration that displayed local-noon as "12:20 AM" instead of "12:20 PM".
// Pinning to en-US h12 makes the meridiem deterministic for all viewers.

const ABSOLUTE_OPTS: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
};

const TIME_OPTS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
};

const LOCALE = "en-US";

export function formatAbsolute(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat(LOCALE, ABSOLUTE_OPTS).format(date);
}

export function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(LOCALE, TIME_OPTS).format(date);
}

export function formatRelative(timestamp: string, now: number = Date.now()): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const diff = now - date.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "moments ago";
  if (diff < hour) {
    const mins = Math.max(1, Math.round(diff / minute));
    return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  }
  if (diff < day) {
    const hours = Math.max(1, Math.round(diff / hour));
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.max(1, Math.round(diff / day));
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
