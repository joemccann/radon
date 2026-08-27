"use client";

/**
 * Obtain a short-lived WebSocket ticket from the API.
 * Called from browser before establishing WebSocket connections.
 *
 * Routes through Next.js API (/api/ib/ws-ticket) which proxies to FastAPI
 * server-to-server. This avoids cross-origin issues in local dev (browser
 * on :3000, FastAPI on :8321) and works behind Caddy in production.
 */

export async function getWsTicket(clerkToken: string): Promise<string> {
  const res = await fetch("/api/ib/ws-ticket", {
    method: "POST",
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${clerkToken}`,
      "Content-Type": "application/json",
    },
    // Must outlast the edge's lb_try_duration for /api/ib/* (15s in
    // cloud/caddy/Caddyfile). At 8s the client abandoned the request seven
    // seconds before Caddy's retry loop would have reached the restarted
    // radon-api, so the ride-out that exists for exactly this call never
    // helped it — the ticket fetch still failed on every deploy gap and the
    // price-WebSocket reconnect backoff fired as before. R-218.
    signal: AbortSignal.timeout(16_000),
  });

  if (!res.ok) {
    throw new Error(`Failed to obtain WS ticket: ${res.status}`);
  }

  const data = await res.json();
  return data.ticket;
}
