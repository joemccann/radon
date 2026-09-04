"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "@clerk/nextjs";
import { createReconnectStrategy, type ReconnectState } from "./reconnectStrategy";
import { buildAuthenticatedWebSocketUrl, resolveRealtimeWebSocketUrl } from "./realtimeSocketAuth";
import {
  REALTIME_HEALTH_TIMEOUT_MS,
  REALTIME_OPEN_TIMEOUT_MS,
} from "./realtimeDeadline";

/* ─── Types ───────────────────────────────────────────── */

export type ConnectionState = "connected" | "ib_offline" | "relay_offline";

/** Authoritative IB auth state from FastAPI /health.ib_gateway.auth_state.
 *  Mirrors scripts/api/ib_gateway.py auth-state machine. */
export type IBAuthState =
  | "authenticated"
  | "awaiting_2fa"
  | "unreachable"
  | "unknown"
  | "remote";

/** Authoritative IB service state from FastAPI /health.ib_gateway.service_state. */
export type IBServiceState = "healthy" | "unhealthy" | "starting" | "unknown";

/** Display-level status the footer/banner derive from the combined signal.
 *  Single source of truth so footer (sidebar) and banner (ConnectionBanner)
 *  never disagree again. Resolution priority (highest first):
 *    relay_offline > unreachable > awaiting_2fa > unhealthy > ib_offline > connected
 */
export type IBDisplayStatus =
  | "connected"
  | "awaiting_2fa"
  | "unhealthy"
  | "unreachable"
  | "ib_offline"
  | "relay_offline"
  /** Seed-data demo deployment (NEXT_PUBLIC_RADON_DEMO=1): no IB gateway, no
   *  realtime relay by design. Renders a calm "DEMO / SAMPLE DATA" indicator
   *  instead of the alarming offline treatment. */
  | "demo";

export type IBStatusState = {
  /** WebSocket to our realtime server is open */
  wsConnected: boolean;
  /** IB Gateway is connected (reported by relay WS) — DO NOT trust this for
   *  UI labels; the WS flag derives from the relay's long-held ib_insync
   *  socket which can stay "connected" while IB Gateway is actually sitting
   *  at the 2FA prompt (half-open socket, TCP alive but API mute). The
   *  authoritative signal is `authState` below, polled from FastAPI /health
   *  which checks Docker container health, pool client managed_accounts,
   *  and ibc auth_state. */
  ibConnected: boolean;
  /** Timestamp when connection was lost (null = connected) */
  disconnectedSince: number | null;
  /** Derived three-state legacy connection status (WS+ibConnected). Kept
   *  for ConnectionBanner backward-compat. New consumers should read
   *  `displayStatus` instead. */
  connectionState: ConnectionState;
  /** Authoritative IB auth state from FastAPI /health. */
  authState: IBAuthState | null;
  /** Authoritative IB service state from FastAPI /health. */
  serviceState: IBServiceState | null;
  /** True when FastAPI cannot reach IB Gateway (port closed or API mute). */
  upstreamDead: boolean | null;
  /** Single derived label for the footer / banner. */
  displayStatus: IBDisplayStatus;
};

type StatusMessage = {
  type: "status";
  ib_connected: boolean;
};

type PingMessage = {
  type: "ping";
};

/* ─── Context ─────────────────────────────────────────── */

const IBStatusContext = createContext<IBStatusState>({
  wsConnected: false,
  ibConnected: false,
  disconnectedSince: null,
  connectionState: "relay_offline",
  authState: null,
  serviceState: null,
  upstreamDead: null,
  displayStatus: "relay_offline",
});

/* ─── Staleness constants ─────────────────────────────── */

const STALENESS_CHECK_INTERVAL_MS = 15_000;
const STALENESS_THRESHOLD_MS = 60_000;
const HEALTH_POLL_MS = 15_000;
const HEALTH_STALE_AFTER_MS = 45_000;

type HealthPayload = {
  ib_gateway?: {
    auth_state?: IBAuthState;
    service_state?: IBServiceState;
    upstream_dead?: boolean;
  };
};

function deriveDisplayStatus(args: {
  wsConnected: boolean;
  ibConnected: boolean;
  authState: IBAuthState | null;
  serviceState: IBServiceState | null;
  upstreamDead: boolean | null;
}): IBDisplayStatus {
  // /health is the source of truth when we have it. Order matters — pick the
  // most severe applicable state first.
  if (!args.wsConnected) return "relay_offline";
  if (args.upstreamDead === true || args.authState === "unreachable") return "unreachable";
  if (args.authState === "awaiting_2fa") return "awaiting_2fa";
  if (args.serviceState === "unhealthy") return "unhealthy";
  if (args.authState === "unknown" || args.serviceState === "unknown") return "unhealthy";
  if (args.authState === "authenticated" && args.serviceState === "healthy") return "connected";
  // Fall back to the legacy WS-relay flag when /health hasn't responded yet.
  if (args.ibConnected) return "connected";
  return "ib_offline";
}

type IbHealthFields = {
  authState: IBAuthState | null;
  serviceState: IBServiceState | null;
  upstreamDead: boolean | null;
};

// The isolated daemon's /edge-health/status nests IB state under the radon-api
// probe; the rich /api/admin/health proxy returns it flat under ib_gateway.
type EdgeHealthPayload = {
  probes?: Record<string, { state?: string; payload?: HealthPayload["ib_gateway"] }>;
  schema_version?: number;
  overall_state?: string;
};

// The daemon trust-splits /status: a public-edge caller (which the browser
// unavoidably is — the shared bearer would have to ship in the client bundle)
// keeps the aggregate verdict and loses `probes` / `units` / `service_health`.
// The aggregate is still worth reading: it is non-healthy whenever the nested
// FastAPI payload reports awaiting_2fa, upstream_dead or an unhealthy service
// state, so it proves health even though it cannot attribute a fault.
export function isAggregateOnlyHealthPayload(
  payload: (HealthPayload & EdgeHealthPayload) | null | undefined,
): boolean {
  if (!payload) return false;
  return typeof payload.schema_version === "number" && payload.probes === undefined;
}

function readGatewayFields(gw: NonNullable<HealthPayload["ib_gateway"]>): IbHealthFields {
  return {
    authState: gw.auth_state ?? null,
    serviceState: gw.service_state ?? null,
    upstreamDead: typeof gw.upstream_dead === "boolean" ? gw.upstream_dead : null,
  };
}

// Normalise either health-source shape into the three IB fields the chip needs.
// Returns null when the source can't determine IB state, so the caller falls
// back (e.g. /edge-health reports the radon-api probe as "unknown" on a probe
// timeout — that's indeterminate, not "down").
export function parseIbHealth(
  payload: (HealthPayload & EdgeHealthPayload) | null | undefined,
): IbHealthFields | null {
  if (!payload) return null;
  const probe = payload.probes?.["radon-api"];
  if (probe) {
    if (probe.state === "up" && probe.payload) return readGatewayFields(probe.payload);
    if (probe.state === "down") {
      // The isolated daemon confirms radon-api is unreachable.
      return { authState: "unreachable", serviceState: null, upstreamDead: null };
    }
    return null; // indeterminate ("unknown") — let the caller fall back
  }
  if (payload.ib_gateway) return readGatewayFields(payload.ib_gateway);
  if (isAggregateOnlyHealthPayload(payload)) {
    // Healthy aggregate is definitive; anything else is a real fault whose
    // cause the redaction removed, so report it as unhealthy rather than
    // guessing a broker state (or going blank, which reads as fine).
    return payload.overall_state === "up"
      ? { authState: "authenticated", serviceState: "healthy", upstreamDead: false }
      : { authState: null, serviceState: "unhealthy", upstreamDead: null };
  }
  return null;
}

/* ─── Provider ────────────────────────────────────────── */

export function IBStatusProvider({
  children,
  authlessTestBypass = false,
}: {
  children: ReactNode;
  authlessTestBypass?: boolean;
}) {
  // Demo deployment is seed-data only — there is no IB gateway and no realtime
  // relay by design. Short-circuit with a static neutral "demo" context so we
  // never open the WS or poll /health (which 404s on /edge-health/status) and
  // every status surface reads as a calm "DEMO / SAMPLE DATA" indicator instead
  // of a red "RELAY OFFLINE". Production (flag unset) keeps the live providers.
  if (process.env.NEXT_PUBLIC_RADON_DEMO === "1") {
    return (
      <IBStatusContext.Provider
        value={{
          wsConnected: false,
          ibConnected: false,
          disconnectedSince: null,
          connectionState: "connected",
          authState: null,
          serviceState: null,
          upstreamDead: null,
          displayStatus: "demo",
        }}
      >
        {children}
      </IBStatusContext.Provider>
    );
  }
  return (
    <AuthenticatedIBStatusProvider authlessTestBypass={authlessTestBypass}>
      {children}
    </AuthenticatedIBStatusProvider>
  );
}

function AuthenticatedIBStatusProvider({
  children,
  authlessTestBypass,
}: {
  children: ReactNode;
  authlessTestBypass: boolean;
}) {
  if (process.env.NODE_ENV === "test" || authlessTestBypass) {
    return <IBStatusCoreProvider getToken={undefined}>{children}</IBStatusCoreProvider>;
  }
  return <ClerkIBStatusProvider>{children}</ClerkIBStatusProvider>;
}

function ClerkIBStatusProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  return <IBStatusCoreProvider getToken={getToken}>{children}</IBStatusCoreProvider>;
}

function IBStatusCoreProvider({
  children,
  getToken,
}: {
  children: ReactNode;
  getToken: (() => Promise<string | null>) | undefined;
}) {
  const [wsConnected, setWsConnected] = useState(false);
  const [ibConnected, setIbConnected] = useState(true); // assume connected until told otherwise
  const [disconnectedSince, setDisconnectedSince] = useState<number | null>(null);
  const [authState, setAuthState] = useState<IBAuthState | null>(null);
  const [serviceState, setServiceState] = useState<IBServiceState | null>(null);
  const [upstreamDead, setUpstreamDead] = useState<boolean | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mountedRef = useRef(true);
  const prevConnectedRef = useRef<boolean | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stalenessTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastMessageRef = useRef<number>(Date.now());
  const strategyRef = useRef<ReconnectState>(
    createReconnectStrategy({ maxAttempts: 0 }) // unlimited for status
  );

  const socketUrl = resolveRealtimeWebSocketUrl();

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const clearStalenessTimer = useCallback(() => {
    if (stalenessTimerRef.current) {
      clearInterval(stalenessTimerRef.current);
      stalenessTimerRef.current = null;
    }
  }, []);

  const clearOpenTimer = useCallback(() => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const socketGenRef = useRef(0);

  const connect = useCallback(() => {
    clearReconnectTimer();

    const gen = ++socketGenRef.current;

    if (wsRef.current) {
      wsRef.current.close();
    }

    const openSocket = (url: string) => {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      clearOpenTimer();
      openTimerRef.current = setTimeout(() => {
        if (
          gen === socketGenRef.current &&
          mountedRef.current &&
          ws.readyState === WebSocket.CONNECTING
        ) {
          ws.close();
        }
      }, REALTIME_OPEN_TIMEOUT_MS);

    ws.onopen = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      clearOpenTimer();
      setWsConnected(true);
      strategyRef.current.reset();
      lastMessageRef.current = Date.now();

      // Start staleness check
      clearStalenessTimer();
      stalenessTimerRef.current = setInterval(() => {
        if (Date.now() - lastMessageRef.current > STALENESS_THRESHOLD_MS) {
          // Force reconnect on stale connection
          ws.close();
        }
      }, STALENESS_CHECK_INTERVAL_MS);
    };

    ws.onmessage = (event) => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      lastMessageRef.current = Date.now();
      try {
        const msg = JSON.parse(event.data) as StatusMessage | PingMessage;

        if (msg.type === "ping") {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "pong" }));
          }
          return;
        }

        if (msg.type === "status") {
          const nowConnected = (msg as StatusMessage).ib_connected;
          setIbConnected(nowConnected);

          if (nowConnected) {
            setDisconnectedSince(null);
          } else {
            setDisconnectedSince((prev) => prev ?? Date.now());
          }

          prevConnectedRef.current = nowConnected;
        }
      } catch {
        // ignore parse errors for non-status messages
      }
    };

    ws.onclose = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      clearOpenTimer();
      setWsConnected(false);
      clearStalenessTimer();

      // If WS drops, treat as disconnected
      if (prevConnectedRef.current !== false) {
        setIbConnected(false);
        setDisconnectedSince((prev) => prev ?? Date.now());
        prevConnectedRef.current = false;
      }

      // Schedule reconnect with backoff
      if (strategyRef.current.canRetry()) {
        const delay = strategyRef.current.nextDelay();
        reconnectTimerRef.current = setTimeout(() => {
          if (mountedRef.current) connect();
        }, delay);
      }
    };

    ws.onerror = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      ws.close();
    };
    };

    (async () => {
      try {
        const url = await buildAuthenticatedWebSocketUrl(socketUrl, getTokenRef.current);
        if (gen !== socketGenRef.current) return; // stale connect attempt
        openSocket(url);
      } catch {
        if (gen !== socketGenRef.current || !mountedRef.current) return;
        setWsConnected(false);
        setIbConnected(false);
        setDisconnectedSince((prev) => prev ?? Date.now());
        if (strategyRef.current.canRetry()) {
          const delay = strategyRef.current.nextDelay();
          reconnectTimerRef.current = setTimeout(() => {
            if (mountedRef.current) connect();
          }, delay);
        }
      }
    })();
  }, [socketUrl, clearReconnectTimer, clearStalenessTimer, clearOpenTimer]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearReconnectTimer();
      clearStalenessTimer();
      clearOpenTimer();
      if (wsRef.current) {
        if (wsRef.current.readyState !== WebSocket.CLOSED) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
    };
  }, [connect, clearReconnectTimer, clearStalenessTimer, clearOpenTimer]);

  // Poll the authoritative IB state — the only signal that catches the "TCP
  // socket alive but session sitting at 2FA prompt" case, which the relay WS
  // (long-held ib_insync socket) structurally can't distinguish.
  //
  // In production we read the ISOLATED edge surface: /edge-health/status is
  // served Caddy -> standalone health daemon, so the chip keeps reporting even
  // when radon-api / Next.js are down. That body is redacted for public-edge
  // callers (the browser is one), which still settles "healthy or not" — the
  // rich /api/admin/health proxy is consulted only to attribute a fault, and
  // is the primary in dev where there is no Caddy. Both reads are
  // side-effect-free; the 2FA pool-recovery heartbeat runs server-side in
  // FastAPI, not on this poll.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastSuccessfulHealthAt = Date.now();

    const fetchPayload = async (
      url: string,
    ): Promise<(HealthPayload & EdgeHealthPayload) | null> => {
      try {
        const res = await fetch(url, {
          cache: "no-store",
          signal: AbortSignal.timeout(REALTIME_HEALTH_TIMEOUT_MS),
        });
        if (!res.ok) return null;
        return (await res.json()) as HealthPayload & EdgeHealthPayload;
      } catch {
        return null;
      }
    };

    const poll = async () => {
      const isLocal = /^(localhost|127\.0\.0\.1|\[::1\])/.test(window.location.hostname);
      const primary = isLocal ? "/api/admin/health" : "/edge-health/status";
      const fallback = "/api/admin/health";

      const primaryPayload = await fetchPayload(primary);
      let parsed = parseIbHealth(primaryPayload);
      // A redacted aggregate can prove health but not attribute a fault, so ask
      // the rich proxy WHY only when something is actually wrong. If that proxy
      // is down too — the outage the edge surface exists for — the coarse
      // verdict stands instead of the chip going blank.
      const needsAttribution =
        isAggregateOnlyHealthPayload(primaryPayload) && primaryPayload?.overall_state !== "up";
      if ((!parsed || needsAttribution) && primary !== fallback) {
        const detailed = parseIbHealth(await fetchPayload(fallback));
        if (detailed) parsed = detailed;
      }

      // Keep previous cached state on a total failure — a transient blip
      // shouldn't flip the chip; the next poll resolves it.
      if (!cancelled && parsed) {
        lastSuccessfulHealthAt = Date.now();
        setAuthState(parsed.authState);
        setServiceState(parsed.serviceState);
        setUpstreamDead(parsed.upstreamDead);
      } else if (!cancelled && Date.now() - lastSuccessfulHealthAt >= HEALTH_STALE_AFTER_MS) {
        setAuthState("unknown");
        setServiceState("unknown");
        setUpstreamDead(null);
      }
      if (!cancelled) timer = setTimeout(poll, HEALTH_POLL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Derive three-state connection status (legacy)
  const connectionState: ConnectionState =
    wsConnected && ibConnected
      ? "connected"
      : wsConnected && !ibConnected
        ? "ib_offline"
        : "relay_offline";

  const displayStatus = deriveDisplayStatus({
    wsConnected,
    ibConnected,
    authState,
    serviceState,
    upstreamDead,
  });

  return (
    <IBStatusContext.Provider
      value={{
        wsConnected,
        ibConnected,
        disconnectedSince,
        connectionState,
        authState,
        serviceState,
        upstreamDead,
        displayStatus,
      }}
    >
      {children}
    </IBStatusContext.Provider>
  );
}

export function useIBStatusContext(): IBStatusState {
  return useContext(IBStatusContext);
}
