/**
 * Recover API requests that raced Clerk's session-token refresh.
 *
 * Clerk session JWTs live ~60s and Clerk-js refreshes them on a timer and on
 * `visibilitychange`. iOS freezes a backgrounded PWA, so on resume the app's
 * own polls fire in the same tick as Clerk's refresh and reach the middleware
 * with the expired `__session` cookie. A fetch is never handshake-eligible and
 * a POST is never refresh-eligible server-side, so Clerk answers signed-out and
 * `middleware.ts` returns a JSON 401 BEFORE any route handler runs. The UI then
 * showed "Your session has expired" for a session that was fine a second later.
 *
 * A 401 carrying `x-clerk-auth-status: signed-out` is that middleware
 * short-circuit: the handler never ran, so nothing (an order included) was
 * executed and replaying the identical request once is safe. A 401 from a route
 * handler (the middleware saw a valid session) is never replayed.
 */

export type SessionTokenRefresher = () => Promise<string | null | undefined>;

type FetchTarget = { fetch: typeof fetch };

/** useAuth().getToken awaits Clerk-js readiness and never settles if Clerk-js
 *  failed to load; the original 401 must still come back. */
export const SESSION_REFRESH_TIMEOUT_MS = 5_000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>((resolve) => { timer = setTimeout(() => resolve(null), ms); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export function isMiddlewareSignedOut(response: Response): boolean {
  return response.status === 401 && response.headers.get("x-clerk-auth-status") === "signed-out";
}

function sameOriginApiPath(input: RequestInfo | URL): boolean {
  if (typeof input === "string" && input.startsWith("/")) return input.startsWith("/api/");
  if (typeof input !== "string" && !(input instanceof URL)) return false; // Request: body not replayable
  try {
    const url = new URL(String(input));
    return typeof window !== "undefined"
      && url.origin === window.location.origin
      && url.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

function replayableBody(init?: RequestInit): boolean {
  const body = init?.body;
  return body == null || !(typeof ReadableStream !== "undefined" && body instanceof ReadableStream);
}

/** Wraps `target.fetch`; returns the uninstaller. */
export function installSessionRefreshFetch(
  target: FetchTarget,
  refresh: SessionTokenRefresher,
  refreshTimeoutMs = SESSION_REFRESH_TIMEOUT_MS,
): () => void {
  const original = target.fetch;
  // The resume burst lands several 401s at once; they share one refresh.
  let pending: Promise<boolean> | null = null;
  const refreshOnce = () => {
    pending ??= withTimeout(Promise.resolve().then(refresh), refreshTimeoutMs)
      .then((token) => Boolean(token), () => false)
      .finally(() => { pending = null; });
    return pending;
  };

  const wrapped = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await original.call(target, input, init);
    if (!isMiddlewareSignedOut(response) || !sameOriginApiPath(input) || !replayableBody(init)) return response;
    if (!(await refreshOnce())) return response;
    return original.call(target, input, init);
  }) as typeof fetch;

  target.fetch = wrapped;
  return () => {
    if (target.fetch === wrapped) target.fetch = original;
  };
}
