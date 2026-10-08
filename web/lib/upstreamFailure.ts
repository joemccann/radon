/**
 * Transport-level failures of a FastAPI call, as opposed to an HTTP error
 * FastAPI returned (RadonApiError). Kept free of radonApi imports so routes
 * whose tests mock radonApi still classify real errors.
 */

/**
 * Socket errors that mean a pooled keep-alive connection was closed by the
 * server between requests. Refused or unresolvable connections are absent:
 * the upstream is down, and an immediate second dial cannot help.
 */
const STALE_SOCKET_CODES = new Set(["ECONNRESET", "EPIPE", "UND_ERR_SOCKET", "UND_ERR_CLOSED"]);

const UNREACHABLE_CODES = new Set([
  ...STALE_SOCKET_CODES,
  "ECONNREFUSED",
  "EHOSTUNREACH",
  "ENOTFOUND",
  "ETIMEDOUT",
  "UND_ERR_CONNECT_TIMEOUT",
]);

export type UpstreamFailure = "timeout" | "unreachable";

function socketErrorCode(error: unknown): string | null {
  const cause = (error as { cause?: { code?: unknown } } | null)?.cause;
  return typeof cause?.code === "string" ? cause.code : null;
}

export function isStaleSocketFailure(error: unknown): boolean {
  const code = socketErrorCode(error);
  return code !== null && STALE_SOCKET_CODES.has(code);
}

/**
 * The timeout signal fired, or the socket never produced a response. Anything
 * else, including an HTTP error FastAPI answered with, returns null.
 */
export function classifyUpstreamFailure(error: unknown): UpstreamFailure | null {
  const name = (error as { name?: unknown } | null)?.name;
  if (name === "TimeoutError") return "timeout";
  const code = socketErrorCode(error);
  if (code !== null && UNREACHABLE_CODES.has(code)) return "unreachable";
  if (error instanceof TypeError && error.message === "fetch failed") return "unreachable";
  return null;
}
