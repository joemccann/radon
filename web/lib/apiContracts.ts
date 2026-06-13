import { NextResponse } from "next/server";

export type ErrorCode =
  | "BAD_REQUEST"
  | "UNAUTHORIZED"
  | "NOT_FOUND"
  | "CONFIG_ERROR"
  | "UPSTREAM_ERROR"
  | "VALIDATION_ERROR"
  | "INTERNAL_ERROR";

export type CacheState = "HIT" | "MISS" | "STALE" | "BYPASS";

export type ApiErrorPayload = {
  error: string;
  code: ErrorCode;
  detail?: string;
  requestId: string;
};

export type ApiOptions = {
  requestId?: string;
  cacheState?: CacheState;
};

export function getRequestId(): string {
  // Web Crypto API — available on globalThis.crypto in Node 19+, Next.js
  // Edge runtime, and every modern browser. Previously we imported
  // randomUUID from node:crypto, but `web/middleware.ts` runs in the
  // Edge runtime (no node:* modules) and pulling apiContracts.ts into
  // the middleware blew up production with "Native module not found:
  // node:crypto" the moment the auth-gate change shipped 2026-05-15.
  try {
    return globalThis.crypto.randomUUID();
  } catch {
    return `rid_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  }
}

export function setNoStoreResponseHeaders(response: NextResponse, requestId: string): NextResponse {
  response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  response.headers.set("Pragma", "no-cache");
  response.headers.set("X-Request-Id", requestId);
  return response;
}

export function setCacheResponseHeaders(
  response: NextResponse,
  {
    maxAgeSeconds,
    staleWhileRevalidateSeconds,
    requestId,
    cacheState,
    tags,
  }: {
    maxAgeSeconds: number;
    staleWhileRevalidateSeconds?: number;
    requestId: string;
    cacheState?: CacheState;
    tags?: string[];
  },
): NextResponse {
  const staleDirective = staleWhileRevalidateSeconds && staleWhileRevalidateSeconds > 0
    ? `, stale-while-revalidate=${staleWhileRevalidateSeconds}`
    : "";
  response.headers.set(
    "Cache-Control",
    `public, max-age=${Math.max(0, Math.trunc(maxAgeSeconds))}${staleDirective}`,
  );
  response.headers.set("Vary", "Accept, Accept-Encoding");
  response.headers.set("X-Request-Id", requestId);
  response.headers.set("X-Cache-State", cacheState ?? "BYPASS");
  if (tags?.length) {
    response.headers.set("X-Cache-Tags", tags.join(","));
  }
  return response;
}

// Routes pass raw err.message / detail (often a LibsqlError carrying the Turso
// URL + auth token, or an account id) straight into the client error body. Scrub
// at this single chokepoint every error response flows through — the same
// information-disclosure class as the /health account-id leak.
const SECRET_SCRUB_PATTERNS: Array<[RegExp, string]> = [
  [/libsql:\/\/[^\s'"]+/gi, "[redacted-db-url]"],
  [/https:\/\/[a-z0-9.-]+\.turso\.io[^\s'"]*/gi, "[redacted-db-url]"],
  [/(auth[_-]?token|authorization|bearer)(\s*[=:]\s*)\S+/gi, "$1$2[redacted]"],
  [/eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*/g, "[redacted-jwt]"],
  [/\bU\d{6,}\b/g, "[redacted-account]"],
];

export function scrubSecrets(value: string): string {
  return SECRET_SCRUB_PATTERNS.reduce((acc, [pat, repl]) => acc.replace(pat, repl), value);
}

export function jsonApiError(params: {
  message: string;
  status?: number;
  code?: ErrorCode;
  detail?: string;
  requestId: string;
}): NextResponse<ApiErrorPayload> {
  const status = params.status ?? 500;
  const code: ErrorCode =
    params.code ??
    (status === 404 ? "NOT_FOUND" : status >= 500 ? "INTERNAL_ERROR" : "BAD_REQUEST");
  const detail = params.detail ? scrubSecrets(params.detail) : undefined;
  return NextResponse.json(
    {
      error: scrubSecrets(params.message),
      code,
      ...(detail ? { detail } : {}),
      requestId: params.requestId,
    },
    { status },
  );
}

/** Alias for call sites that prefer a shorter name — same payload as `jsonApiError`. */
export const jsonError = jsonApiError;
