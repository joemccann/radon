"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type IBStatus = {
  /** WebSocket to our realtime server is open */
  wsConnected: boolean;
  /** IB Gateway is connected (reported by server) */
  ibConnected: boolean;
  /** Timestamp when connection was lost (null = connected) */
  disconnectedSince: number | null;
};

type StatusMessage = {
  type: "status";
  ib_connected: boolean;
};

export function useIBStatus(onTransition?: (connected: boolean) => void) {
  const [wsConnected, setWsConnected] = useState(false);
  const [ibConnected, setIbConnected] = useState(true); // assume connected until told otherwise
  const [disconnectedSince, setDisconnectedSince] = useState<number | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const prevConnectedRef = useRef<boolean | null>(null);
  const onTransitionRef = useRef(onTransition);
  onTransitionRef.current = onTransition;

  const socketUrl =
    process.env.NEXT_PUBLIC_IB_REALTIME_WS_URL ??
    process.env.IB_REALTIME_WS_URL ??
    "ws://localhost:8765";

  const connect = useCallback(() => {
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = new WebSocket(socketUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(event.data) as StatusMessage;
        if (msg.type === "status") {
          const nowConnected = msg.ib_connected;
          setIbConnected(nowConnected);

          if (nowConnected) {
            setDisconnectedSince(null);
          } else {
            setDisconnectedSince((prev) => prev ?? Date.now());
          }

          // Fire transition callback on change
          if (prevConnectedRef.current !== null && prevConnectedRef.current !== nowConnected) {
            onTransitionRef.current?.(nowConnected);
          }
          prevConnectedRef.current = nowConnected;
        }
      } catch {
        // ignore parse errors for non-status messages
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setWsConnected(false);

      // If WS drops, treat as disconnected
      if (prevConnectedRef.current !== false) {
        setIbConnected(false);
        setDisconnectedSince((prev) => prev ?? Date.now());
        if (prevConnectedRef.current !== null) {
          onTransitionRef.current?.(false);
        }
        prevConnectedRef.current = false;
      }

      // Reconnect after 5s
      reconnectRef.current = setTimeout(() => {
        if (mountedRef.current) connect();
      }, 5000);
    };

    ws.onerror = () => {
      if (!mountedRef.current) return;
      ws.close();
    };
  }, [socketUrl]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
    };
  }, [connect]);

  return { wsConnected, ibConnected, disconnectedSince } as IBStatus;
}
