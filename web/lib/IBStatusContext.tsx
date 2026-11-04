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
 *  Mirrors scripts/api/ib_gateway.py auth-state machine (`_derive_auth_state`
 *  plus cloud-mode `remote`: TCP alone cannot tell authenticated from
 *  awaiting_2fa, so the relay's broker report decides). */
export type IBAuthState =
  | "authenticated"
  | "awaiting_2fa"
  | "unreachable"
  | "unknown"
  | "remote";

/** Authoritative IB service state from FastAPI /health.ib_gateway.service_state.
 *  Literal backend vocabulary (scripts/api/ib_gateway.py):
 *    docker  `_check_docker`: healthy | unhealthy | starting | restarting | stopped | unknown
 *    cloud   `_check_cloud`:  reachable | unhealthy | unreachable
 *  launchd reports the raw IBC status string; anything unrecognised is
 *  treated as no verdict and the relay broker report decides. */
export type IBServiceState =
  | "healthy"
  | "reachable"
  | "unhealthy"
  | "starting"
  | "restarting"
  | "stopped"
  | "unreachable"
  | "unknown";

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
  /** IB Gateway is connected (reported by relay WS on the CURRENT socket;
   *  false until that socket's first status frame) — DO NOT trust this for
   *  UI labels; the WS flag derives from the relay's long-held ib_insync
   *  socket which can stay "connected" while IB Gateway is actually sitting
   *  at the 2FA prompt (half-open socket, TCP alive but API mute). The
   *  authoritative signal is `authState` below, polled from FastAPI /health
   *  which checks Docker container health, pool client managed_accounts,
   *  and ibc auth_state. */
  ibConnected: boolean;
  /** Timestamp when connection was lost (null = connected) */
  disconnectedSince: number | null;
  /** Legacy three-state projection of `displayStatus` (kept for
   *  backward-compat). New consumers should read `displayStatus`. */
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
  market_data_degraded?: boolean;
  ib_connected: boolean;
};

/** Relay status-socket machine. Broker and market-data state exist only while
 *  the socket is open and start `unknown` on every fresh socket, so a
 *  previous session's report can never stand in for the current one. */
export type IBRelayState =
  | { phase: "down"; disconnectedSince: number | null }
  | {
      phase: "open";
      broker: "unknown" | "connected" | "disconnected";
      marketDataDegraded: boolean;
      disconnectedSince: number | null;
    };

export type IBRelayEvent =
  | { type: "open" }
  | { type: "status"; ibConnected: boolean; marketDataDegraded: boolean; at: number }
  | { type: "down"; at: number };

export function ibRelayTransition(state: IBRelayState, event: IBRelayEvent): IBRelayState {
  switch (event.type) {
    case "open":
      return {
        phase: "open",
        broker: "unknown",
        marketDataDegraded: false,
        disconnectedSince: state.disconnectedSince,
      };
    case "status":
      if (state.phase !== "open") return state;
      return {
        phase: "open",
        broker: event.ibConnected ? "connected" : "disconnected",
        marketDataDegraded: event.marketDataDegraded,
        disconnectedSince: event.ibConnected ? null : (state.disconnectedSince ?? event.at),
      };
    case "down":
      return { phase: "down", disconnectedSince: state.disconnectedSince ?? event.at };
  }
}

const INITIAL_RELAY: IBRelayState = { phase: "down", disconnectedSince: null };

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

export function deriveDisplayStatus(args: {
  relay: IBRelayState;
  authState: IBAuthState | null;
  serviceState: IBServiceState | null;
  upstreamDead: boolean | null;
}): IBDisplayStatus {
  const { relay, authState, serviceState } = args;
  // /health is the source of truth when we have it. Order matters — pick the
  // most severe applicable state first.
  if (relay.phase !== "open") return "relay_offline";
  if (args.upstreamDead === true || authState === "unreachable" || serviceState === "unreachable") {
    return "unreachable";
  }
  if (serviceState === "stopped") return "ib_offline";
  if (authState === "awaiting_2fa") return "awaiting_2fa";
  if (
    serviceState === "unhealthy" ||
    serviceState === "starting" ||
    serviceState === "restarting" ||
    relay.marketDataDegraded
  ) {
    return "unhealthy";
  }
  if (authState === "unknown" || serviceState === "unknown") return "unhealthy";
  if (authState === "authenticated" && (serviceState === "healthy" || serviceState === "reachable")) {
    return "connected";
  }
  // No /health verdict yet, auth_state `remote`, or a non-docker service
  // string: the relay's report on THIS socket decides. Unknown is not
  // connected (fail closed).
  if (relay.broker === "connected") return "connected";
  if (relay.broker === "disconnected") return "ib_offline";
  return "unhealthy";
}

/** Legacy three-state projection, derived from the same verdict as the chip
 *  so the two can never disagree. */
export function legacyConnectionState(display: IBDisplayStatus): ConnectionState {
  if (display === "connected" || display === "demo") return "connected";
  if (display === "relay_offline") return "relay_offline";
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
  const [relay, setRelay] = useState<IBRelayState>(INITIAL_RELAY);
  const dispatchRelay = useCallback((event: IBRelayEvent) => {
    setRelay((prev) => ibRelayTransition(prev, event));
  }, []);
  const [authState, setAuthState] = useState<IBAuthState | null>(null);
  const [serviceState, setServiceState] = useState<IBServiceState | null>(null);
  const [upstreamDead, setUpstreamDead] = useState<boolean | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mountedRef = useRef(true);
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
      dispatchRelay({ type: "open" });
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
          dispatchRelay({
            type: "status",
            ibConnected: msg.ib_connected === true,
            marketDataDegraded: msg.market_data_degraded === true,
            at: Date.now(),
          });
        }
      } catch {
        // ignore parse errors for non-status messages
      }
    };

    ws.onclose = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      clearOpenTimer();
      clearStalenessTimer();
      // A dropped socket discards its broker / market-data report.
      dispatchRelay({ type: "down", at: Date.now() });

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
        dispatchRelay({ type: "down", at: Date.now() });
        if (strategyRef.current.canRetry()) {
          const delay = strategyRef.current.nextDelay();
          reconnectTimerRef.current = setTimeout(() => {
            if (mountedRef.current) connect();
          }, delay);
        }
      }
    })();
  }, [socketUrl, clearReconnectTimer, clearStalenessTimer, clearOpenTimer, dispatchRelay]);

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

  const displayStatus = deriveDisplayStatus({ relay, authState, serviceState, upstreamDead });
  const connectionState = legacyConnectionState(displayStatus);
  const wsConnected = relay.phase === "open";
  const ibConnected = relay.phase === "open" && relay.broker === "connected";
  const { disconnectedSince } = relay;

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
