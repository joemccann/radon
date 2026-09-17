/** Presentation boundary: transport bodies and infrastructure diagnostics are not UI copy. */
const DEFAULT_MESSAGE = "This request could not be completed. Please try again.";
const CAPACITY_MESSAGE = "This service is busy. Please try again shortly.";

export function errorMessageCandidate(input: unknown, depth = 0): string {
  if (depth > 4 || input == null) return "";
  if (input instanceof Error) return errorMessageCandidate(input.message, depth + 1);
  if (typeof input === "object") {
    for (const key of ["error", "detail", "message", "reason"]) {
      const value = (input as Record<string, unknown>)[key];
      if (value != null) { const result = errorMessageCandidate(value, depth + 1); if (result) return result; }
    }
    return "";
  }
  if (typeof input !== "string") return "";
  const text = input.trim();
  if (/^[\[{\"]/.test(text)) {
    try { return errorMessageCandidate(JSON.parse(text), depth + 1); } catch { return ""; }
  }
  return text;
}

/** Defend secondary surfaces too: titles, toast text and rejection details. */
export function isTechnicalError(message: string, maxLength = 600): boolean {
  return message.length > maxLength || /[{}]|<!?\/?[a-z][^>]*>|Traceback|\bat \S+\s*\([^)]*:\d+|\b(?:TypeError|SyntaxError|ReferenceError|ENOENT|ECONN\w*|EPIPE|SQLITE\w*|TURSO\w*|stderr|stdout|subprocess|stack trace|exception|libsql|fetch failed|failed to fetch)\b|(?:\/Users\/|\/home\/|\/opt\/|node_modules|site-packages)|\b(?:SELECT|INSERT INTO|UPDATE)\s+\w+|https?:\/\/|(?:api[_-]?key|token|password|secret)\s*[:=]/i.test(message);
}

export function userErrorMessage(error: unknown, fallback = DEFAULT_MESSAGE): string {
  const message = errorMessageCandidate(error);
  if (!message) return fallback;
  // These known failures have actionable copy even when wrapped by a transport.
  if (/subprocess capacity exhausted|scan lane (?:is|stayed) full|capacity (?:exhausted|unavailable)/i.test(message)) {
    return /after \d+ attempts?|retries exhausted|exhausted the retry|every retry/i.test(message)
      ? "This service is still busy after retrying. Please try again later."
      : CAPACITY_MESSAGE;
  }
  if (/rate.?limit|too many requests|\b429\b|quota (?:exceeded|exhausted)/i.test(message)) return "Too many requests. Please wait before trying again.";
  if (/timed?\s*out|timeout|\b(?:408|504)\b/i.test(message)) return "The request took too long. Please try again.";
  if (/failed to fetch|fetch failed|network (?:error|request failed)|ECONN\w*|offline/i.test(message)) return "Unable to connect. Check your connection and try again.";
  if (/\b401\b|unauthorized|session expired/i.test(message)) return "Your session has expired. Sign in again to continue.";
  if (/\b403\b|forbidden/i.test(message)) return "You do not have access to this request.";
  if (/\b(?:500|502|503)\b|bad gateway|internal server error|service unavailable/i.test(message)) return "This service is temporarily unavailable. Please try again shortly.";
  if (isTechnicalError(message) || /[\r\n]|\bHTTP\s+\d|^Radon API\b|^Error:/i.test(message)) return fallback;
  return message;
}

/** Preserve HTTP handling at the transport; only convert its presentation. */
export async function readErrorResponse(response: Response, fallback = DEFAULT_MESSAGE): Promise<string> {
  let body: unknown;
  try {
    const text = await response.text();
    try { body = JSON.parse(text); } catch { body = text; }
  } catch { body = null; }
  // Status handles opaque HTML/proxy errors; classified JSON carries finer context.
  const statusMessage = userErrorMessage(`HTTP ${response.status}`, fallback);
  return userErrorMessage(body, statusMessage);
}
