/**
 * Bearer-token auth for the DUR-16 freshness probe perimeter.
 *
 * IMPORTED BY web/middleware.ts — this file MUST stay Edge-runtime-safe:
 * Web APIs only (globalThis.crypto, TextEncoder), never node:* modules.
 * Enforced by the no-restricted-syntax block in web/eslint.config.mjs;
 * see feedback_middleware_edge_runtime.md for the production crash this
 * prevents.
 */

/**
 * Constant-time string comparison built on Web Crypto: digest both inputs
 * with SHA-256, then compare the fixed-length digests with a branch-free
 * XOR fold. Comparing digests (not the raw strings) means neither length
 * nor prefix overlap of the secret leaks through timing.
 */
export async function timingSafeStringEqual(a: string, b: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [digestA, digestB] = await Promise.all([
    globalThis.crypto.subtle.digest("SHA-256", encoder.encode(a)),
    globalThis.crypto.subtle.digest("SHA-256", encoder.encode(b)),
  ]);
  const bytesA = new Uint8Array(digestA);
  const bytesB = new Uint8Array(digestB);
  let mismatch = 0;
  for (let i = 0; i < bytesA.length; i += 1) {
    mismatch |= bytesA[i] ^ bytesB[i];
  }
  return mismatch === 0;
}

/** Extract the token from an `Authorization: Bearer <token>` header.
 * Scheme is case-insensitive per RFC 7235; missing/other-scheme/empty
 * tokens all return null. */
export function bearerTokenFrom(authorizationHeader: string | null): string | null {
  if (!authorizationHeader) return null;
  const match = /^Bearer\s+(\S.*)$/i.exec(authorizationHeader.trim());
  return match ? match[1] : null;
}

/**
 * True iff the Authorization header carries exactly the expected probe
 * token. An unset/empty server-side token CLOSES the perimeter (always
 * false) — a missing env var must never fail open.
 */
export async function isAuthorizedProbeRequest(
  authorizationHeader: string | null,
  expectedToken: string | undefined,
): Promise<boolean> {
  if (!expectedToken) return false;
  const presented = bearerTokenFrom(authorizationHeader);
  if (presented === null) return false;
  return timingSafeStringEqual(presented, expectedToken);
}
