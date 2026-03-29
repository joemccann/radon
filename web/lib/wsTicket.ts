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
    headers: {
      Authorization: `Bearer ${clerkToken}`,
      "Content-Type": "application/json",
    },
  });

  if (!res.ok) {
    throw new Error(`Failed to obtain WS ticket: ${res.status}`);
  }

  const data = await res.json();
  return data.ticket;
}
