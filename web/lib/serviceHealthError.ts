import { userErrorMessage } from "./userError";

/**
 * Normalize ``service_health.last_error`` payloads into a single human-
 * readable line of plain text suitable for direct rendering inside the
 * dashboard banner.
 *
 * Two entry points:
 *
 *   - ``formatServiceHealthError`` — pure pass-through formatter. Pulls
 *     a message string out of whatever shape the worker stored, strips
 *     JSON structural characters, truncates at a word boundary. Used by
 *     the API route to ship a defensive ``error_summary`` field. Never
 *     rewrites copy.
 *
 *   - ``humanizeServiceHealthError`` — opinionated formatter for
 *     direct user-facing rendering. Recognises the known error shapes
 *     (Flex throttle, Flex auth, timeout, WAL conflict, network blip,
 *     subprocess traceback) and rewrites them in plain language. Adds
 *     a ``retry in <window>`` suffix when the payload contains a future
 *     ``next_attempt_at``. Falls back to ``formatServiceHealthError``
 *     for unrecognised shapes so we never regress on novel errors.
 *
 * Why this exists
 * ---------------
 * The DB column stores ``last_error`` as a JSON-stringified object — the
 * Python writer at ``scripts/db/writer.py`` and the JS writer in
 * ``lib/serviceHealth.ts`` both hand the row whatever the worker raised
 * (Exception, dict, string) wrapped in ``json.dumps`` /
 * ``JSON.stringify``. The banner used to concatenate that raw string into
 * its copy, leaking ``{"message": "..."}`` braces and quotes to users —
 * worse, when the payload exceeded the visible width it would mid-word
 * cut off, exposing a syntactically broken JSON fragment.
 *
 * Beyond JSON safety, the readable form also avoids developer-flavoured
 * phrasing in the dashboard. ``ERR: cash flow fetch failed: Flex
 * SendRequest failed (code 1001): Statement could not be generated...``
 * becomes ``Flex Web Service rate-limited; retry in 1d`` — same
 * information, no stack-trace tone.
 */

const FALLBACK_MESSAGE = "service unavailable";
const DEFAULT_MAX_LENGTH = 80;
const ELLIPSIS = "...";
// Conventional keys, MOST common first. The first present and non-empty
// wins — order matters because some rows ship multiple fields.
const MESSAGE_KEYS = ["message", "error", "detail", "reason"] as const;

export type ServiceHealthErrorInput =
  | string
  | Record<string, unknown>
  | null
  | undefined;

export type FormatOptions = {
  maxLength?: number;
};

/**
 * Public entry point. Always returns a string suitable for direct
 * rendering — never null, never an object, never a JSON fragment.
 */
export function formatServiceHealthError(
  raw: ServiceHealthErrorInput,
  options: FormatOptions = {},
): string {
  const maxLength = options.maxLength ?? DEFAULT_MAX_LENGTH;
  const candidate = extractCandidate(raw);
  const sanitized = sanitize(candidate);
  if (!sanitized) return FALLBACK_MESSAGE;
  return truncateAtWordBoundary(sanitized, maxLength);
}

/**
 * Resolve the raw input down to a candidate string. Returns ``""`` when
 * nothing meaningful is available — callers translate that into the
 * fallback copy.
 */
function extractCandidate(raw: ServiceHealthErrorInput): string {
  if (raw == null) return "";
  if (typeof raw === "string") {
    const parsed = tryParseJson(raw);
    if (parsed && typeof parsed === "object") {
      // Successfully parsed JSON: trust the structured shape. If no
      // recognised message key exists, surface the fallback rather
      // than dumping the raw string back through the sanitiser — that
      // would expose internal field names like ``wal_conflicts_observed``.
      return pickFromObject(parsed as Record<string, unknown>) ?? "";
    }
    return raw;
  }
  if (typeof raw === "object") {
    return pickFromObject(raw as Record<string, unknown>) ?? "";
  }
  return String(raw);
}

function tryParseJson(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

/**
 * Walk the conventional message keys and return the first non-empty
 * string value. Numeric and boolean values are coerced; nested objects
 * are skipped so we never bubble structure back into the banner.
 */
function pickFromObject(obj: Record<string, unknown>): string | null {
  for (const key of MESSAGE_KEYS) {
    const value = obj[key];
    if (value == null) continue;
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
  }
  return null;
}

/**
 * Strip JSON structural characters and collapse whitespace so the final
 * string is a single clean line of plain text. Defensive — covers cases
 * where the candidate is a JSON fragment we couldn't parse.
 */
function sanitize(candidate: string): string {
  if (!candidate) return "";
  return candidate
    .replace(/[{}"\[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Trim to ``maxLength`` characters, preferring the last whitespace
 * boundary to avoid mid-word cuts. Falls back to a hard cut when the
 * remainder is so short the boundary would lose too much context.
 */
function truncateAtWordBoundary(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  const budget = Math.max(1, maxLength - ELLIPSIS.length);
  const slice = text.slice(0, budget);
  const lastSpace = slice.lastIndexOf(" ");
  // Only honor the boundary when it preserves meaningful context — at
  // least two-thirds of the budget. Otherwise hard-cut.
  if (lastSpace >= Math.floor(budget * 0.66)) {
    return `${slice.slice(0, lastSpace).trimEnd()}${ELLIPSIS}`;
  }
  return `${slice.trimEnd()}${ELLIPSIS}`;
}

// ---------------------------------------------------------------- humanizer

/**
 * Pattern → plain-language rewrite. Each entry recognises a known
 * error shape via a regex against the lower-cased message and returns
 * the operator-friendly copy that should replace it.
 *
 * Order matters — the FIRST match wins. Put the most specific patterns
 * (e.g. exact Flex error codes) above the generic fallbacks
 * (network / connection / timeout).
 */
const HUMAN_REWRITES: ReadonlyArray<{
  pattern: RegExp;
  rewrite: string;
}> = [
  // Flex throttle codes — sliding-window rate limit. These are the
  // codes that drove the May 9 incident; copy matches the daemon's
  // circuit-breaker mental model.
  {
    pattern: /code\s*1001\b/i,
    rewrite: "Flex Web Service rate limit hit",
  },
  {
    pattern: /code\s*1018\b/i,
    rewrite: "Flex Web Service rate limit hit",
  },
  {
    pattern: /code\s*1019\b/i,
    rewrite: "Flex Web Service still processing",
  },
  // Flex auth — operator action required, distinct from throttle.
  {
    pattern: /code\s*1012\b/i,
    rewrite: "Flex token expired - rotate the IB_FLEX_TOKEN",
  },
  // Generic Flex application error - we have a code but no specific
  // pattern. Better than dumping "SendRequest failed".
  {
    pattern: /flex\s+sendrequest\s+failed/i,
    rewrite: "Flex Web Service returned an error",
  },
  // Subprocess timeouts — strip the script name.
  {
    pattern: /timed\s+out\s+after\s+\d+s?/i,
    rewrite: "Background sync timed out",
  },
  // Python tracebacks — never show the stack to the user.
  {
    pattern: /traceback\s*\(most\s+recent\s+call\s+last\)/i,
    rewrite: "Background sync raised an unexpected error",
  },
  // Network / urllib errors.
  {
    pattern: /urlopen\s+error|errno\s+\d+/i,
    rewrite: "Network unreachable",
  },
  {
    pattern: /connection\s+(refused|reset|aborted)/i,
    rewrite: "Network connection refused",
  },
  // Database / replica errors.
  {
    pattern: /wal\s+(conflict|locked)/i,
    rewrite: "Database temporarily busy",
  },
];

// Trim leading developer-flavoured prefixes (ERR:, WARN:, FATAL:) from
// any candidate before further matching - they add no signal for users.
const DEV_PREFIX_RE = /^(?:err(?:or)?|warn(?:ing)?|fatal|info|debug)\s*:?\s*/i;

// "ERR: cash flow fetch failed: <real message>" or
// "ERR: portfolio sync failed: <real message>" - drop the synthetic
// prefix that monitor-daemon scripts prepend before the real cause.
const SCRIPT_PREFIX_RE = /^[a-z][a-z0-9_\s]*\s+(?:fetch|sync|scan|update|load|fill|pull|run)\s+failed\s*:\s*/i;

/**
 * Find the first ``HUMAN_REWRITES`` entry whose pattern matches the
 * given message. Returns null if nothing matches.
 */
function findHumanRewrite(message: string): string | null {
  for (const { pattern, rewrite } of HUMAN_REWRITES) {
    if (pattern.test(message)) return rewrite;
  }
  return null;
}

/**
 * Strip noise prefixes from a candidate before pattern matching or
 * fallback rendering. Removes one ERR:/WARN:/FATAL marker and one
 * "<script> failed:" wrapper, in that order, since both can appear
 * before the real cause.
 */
function stripNoisePrefixes(message: string): string {
  let out = message.replace(DEV_PREFIX_RE, "");
  out = out.replace(SCRIPT_PREFIX_RE, "");
  return out.trim();
}

const MILLIS_PER_MINUTE = 60_000;
const MILLIS_PER_HOUR = 3_600_000;
const MILLIS_PER_DAY = 86_400_000;

/**
 * Render an absolute UTC timestamp as a short "in N units" string
 * suitable for inline banner copy. Returns null when the timestamp is
 * not in the future or cannot be parsed.
 */
function formatRelativeRetry(nextAttemptAt: string, nowMs: number): string | null {
  const parsed = Date.parse(nextAttemptAt);
  if (Number.isNaN(parsed)) return null;
  const diffMs = parsed - nowMs;
  if (diffMs <= 0) return null;
  if (diffMs < MILLIS_PER_HOUR) {
    const minutes = Math.max(1, Math.round(diffMs / MILLIS_PER_MINUTE));
    return `${minutes}m`;
  }
  if (diffMs < MILLIS_PER_DAY) {
    const hours = Math.max(1, Math.round(diffMs / MILLIS_PER_HOUR));
    return `${hours}h`;
  }
  const days = Math.max(1, Math.round(diffMs / MILLIS_PER_DAY));
  return `${days}d`;
}

/**
 * Pull ``next_attempt_at`` from a raw payload if present and parseable
 * into a future-dated retry suffix. Returns the empty string when
 * absent or invalid.
 */
function extractRetrySuffix(raw: ServiceHealthErrorInput, nowMs: number): string {
  const obj = toObject(raw);
  if (!obj) return "";
  const value = obj["next_attempt_at"];
  if (typeof value !== "string" || value.length === 0) return "";
  const formatted = formatRelativeRetry(value, nowMs);
  return formatted ? ` retry in ${formatted}` : "";
}

/**
 * Coerce a raw input down to a plain object when one is recoverable.
 * Returns null for primitives and unparseable strings. Used by retry-
 * suffix extraction; the main candidate path uses ``extractCandidate``.
 */
function toObject(raw: ServiceHealthErrorInput): Record<string, unknown> | null {
  if (raw == null) return null;
  if (typeof raw === "object") return raw as Record<string, unknown>;
  if (typeof raw !== "string") return null;
  const parsed = tryParseJson(raw);
  return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
}

/**
 * Public entry point for the dashboard banner. Returns a
 * banner-ready, plain-language string. Falls back to
 * ``formatServiceHealthError`` whenever no rewrite pattern matches —
 * we never silently strip context, only rewrite known shapes.
 */
export function humanizeServiceHealthError(
  raw: ServiceHealthErrorInput,
  options: FormatOptions = {},
): string {
  const maxLength = options.maxLength ?? DEFAULT_MAX_LENGTH;

  const candidate = extractCandidate(raw);
  const sanitized = sanitize(candidate);
  if (!sanitized) return FALLBACK_MESSAGE;

  const stripped = stripNoisePrefixes(sanitized);

  const matched = findHumanRewrite(stripped);
  const retry = extractRetrySuffix(raw, Date.now());

  // Compose the final body — pattern rewrite when we have one, else
  // pass through the cleaned candidate so novel errors still surface.
  const body = matched ?? userErrorMessage(stripped, FALLBACK_MESSAGE);
  const composed = `${body}${retry}`.trim();

  return truncateAtWordBoundary(composed, maxLength);
}
