"use client";

/**
 * Obtain a short-lived WebSocket ticket from the API.
 * Called from browser before establishing WebSocket connections.
 *
 * Routes through Next.js API (/api/ib/ws-ticket) which proxies to FastAPI
 * server-to-server. This avoids cross-origin issues in local dev (browser
 * on :3000, FastAPI on :8321) and works behind Caddy in production.
 */

const FAILURE_BASE_MS = 1_000;
const FAILURE_MAX_MS = 30_000;

let failureAttempt = 0;
let blockedUntil = 0;

/** Shared by the price socket and the headlines socket. One failure holds both. */
export function ticketBlockedMs(now = Date.now()): number {
  return Math.max(0, blockedUntil - now);
}

export function resetTicketBackoffForTests(): void {
  failureAttempt = 0;
  blockedUntil = 0;
}

export class TicketBackoffError extends Error {
  readonly retryMs: number;

  constructor(retryMs: number) {
    super("WS ticket backing off");
    this.name = "TicketBackoffError";
    this.retryMs = retryMs;
  }
}

/** Why a ticket request failed. Only `transport`, `server` and `throttled`
 *  say the ticket service is struggling, so only they raise the block shared
 *  by both sockets. `auth` (this tab's Clerk token; the next attempt fetches a
 *  fresh one), `client` and `aborted` (the caller gave up) are the caller's
 *  own problem and its own reconnect strategy paces it. */
export type TicketFailureKind = "transport" | "server" | "throttled" | "auth" | "client" | "aborted";

export function classifyTicketFailure(
  failure: { status: number } | { error: unknown; callerAborted: boolean },
): TicketFailureKind {
  if ("status" in failure) {
    if (failure.status === 401 || failure.status === 403) return "auth";
    if (failure.status === 429) return "throttled";
    if (failure.status >= 500) return "server";
    return "client";
  }
  if (failure.callerAborted) return "aborted";
  // Network errors and our own request timeout (TimeoutError).
  return "transport";
}

function backsOffSharedTicket(kind: TicketFailureKind): boolean {
  return kind === "transport" || kind === "server" || kind === "throttled";
}

class TicketHttpError extends Error {
  constructor(readonly status: number) {
    super(`Failed to obtain WS ticket: ${status}`);
  }
}

function noteTicketFailure(now = Date.now()): void {
  const delay = Math.min(FAILURE_BASE_MS * 2 ** failureAttempt, FAILURE_MAX_MS);
  failureAttempt = Math.min(failureAttempt + 1, 10);
  blockedUntil = Math.max(blockedUntil, now + delay);
}

function noteTicketSuccess(): void {
  failureAttempt = 0;
  blockedUntil = 0;
}

export async function getWsTicket(clerkToken: string, signal?: AbortSignal): Promise<string> {
  const retryMs = ticketBlockedMs();
  if (retryMs > 0) throw new TicketBackoffError(retryMs);

  // Must outlast the edge's lb_try_duration for /api/ib/* (15s in
  // cloud/caddy/Caddyfile). At 8s the client abandoned the request seven
  // seconds before Caddy's retry loop would have reached the restarted
  // radon-api, so the ride-out that exists for exactly this call never
  // helped it — the ticket fetch still failed on every deploy gap and the
  // price-WebSocket reconnect backoff fired as before. R-218.
  // The caller's deadline aborts this signal. Leaving the POST running was
  // how a timed-out ticket overlapped the retry.
  const timeoutSignal = AbortSignal.timeout(16_000);
  const combined = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

  try {
    const res = await fetch("/api/ib/ws-ticket", {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${clerkToken}`,
        "Content-Type": "application/json",
      },
      signal: combined,
    });

    if (!res.ok) throw new TicketHttpError(res.status);

    const data = await res.json();
    noteTicketSuccess();
    return data.ticket;
  } catch (err) {
    const kind = err instanceof TicketHttpError
      ? classifyTicketFailure({ status: err.status })
      : classifyTicketFailure({ error: err, callerAborted: signal?.aborted === true });
    if (backsOffSharedTicket(kind)) noteTicketFailure();
    throw err;
  }
}
