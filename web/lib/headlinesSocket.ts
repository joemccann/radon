import { buildAuthenticatedWebSocketUrl } from "./realtimeSocketAuth";
import type { RealtimeTokenGetter } from "./RealtimeAuthContext";

function isLocalBrowserHost(hostname: string): boolean {
  return hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "::1" ||
    hostname === "[::1]";
}

export const HEADLINES_WS_PATH = "/ws-headlines";
export const LOCAL_HEADLINES_WS_URL = `ws://localhost:8766${HEADLINES_WS_PATH}`;

export function resolveHeadlinesWebSocketUrl(
  explicitUrl: string | undefined =
    process.env.NEXT_PUBLIC_HEADLINES_WS_URL ??
    process.env.HEADLINES_WS_URL,
): string {
  const trimmed = explicitUrl?.trim();
  if (trimmed && !headlinesUrlLeaksUpstream(trimmed)) return trimmed;
  if (typeof window !== "undefined") {
    const { protocol, host, hostname } = window.location;
    if (!isLocalBrowserHost(hostname)) {
      return `${protocol === "https:" ? "wss:" : "ws:"}//${host}${HEADLINES_WS_PATH}`;
    }
  }
  return LOCAL_HEADLINES_WS_URL;
}

export async function buildHeadlinesWebSocketUrl(
  getToken?: RealtimeTokenGetter,
): Promise<string> {
  return buildAuthenticatedWebSocketUrl(resolveHeadlinesWebSocketUrl(), getToken);
}

export function headlinesUrlLeaksUpstream(url: string): boolean {
  return /mktnews\.net/i.test(url);
}
