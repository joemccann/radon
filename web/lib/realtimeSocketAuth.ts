"use client";

import { getWsTicket } from "./wsTicket";
import type { RealtimeTokenGetter } from "./RealtimeAuthContext";
import { withRealtimeDeadline } from "./realtimeDeadline";

function isLocalBrowserHost(hostname: string): boolean {
  return hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "::1" ||
    hostname === "[::1]";
}

export function resolveRealtimeWebSocketUrl(
  explicitUrl: string | undefined =
    process.env.NEXT_PUBLIC_IB_REALTIME_WS_URL ??
    process.env.IB_REALTIME_WS_URL,
): string {
  if (explicitUrl && explicitUrl.trim().length > 0) return explicitUrl;
  if (typeof window !== "undefined") {
    const { protocol, host, hostname } = window.location;
    if (!isLocalBrowserHost(hostname)) {
      return `${protocol === "https:" ? "wss:" : "ws:"}//${host}/ws`;
    }
  }
  return "ws://localhost:8765";
}

export async function buildAuthenticatedWebSocketUrl(
  baseUrl: string,
  getToken?: RealtimeTokenGetter,
): Promise<string> {
  if (!getToken) return baseUrl;
  const token = await withRealtimeDeadline(
    Promise.resolve(getToken()),
    "Realtime authentication",
  );
  if (!token) {
    throw new Error("Realtime auth token unavailable");
  }
  const ticket = await withRealtimeDeadline(
    getWsTicket(token),
    "Realtime ticket request",
  );
  const separator = baseUrl.includes("?") ? "&" : "?";
  return `${baseUrl}${separator}ticket=${encodeURIComponent(ticket)}`;
}
