"use client";

import { useEffect, useRef, useState } from "react";

import {
  buildHeadlinesWebSocketUrl,
  headlinesUrlLeaksUpstream,
} from "./headlinesSocket";
import {
  DEMO_HEADLINES_POLL_BACKOFF_MAX_MS,
  DEMO_HEADLINES_POLL_MS,
} from "./demo/headlinesPolicy";
import { useRealtimeAuth } from "./RealtimeAuthContext";

function reconnectDelayMs(attempt: number): number {
  const exponent = Math.min(Math.max(attempt, 0), 10);
  const base = Math.min(5_000 * 2 ** exponent, 120_000);
  return Math.min(Math.round(base * (1 + 0.2 * Math.random())), 120_000);
}

export type HeadlineImpact = { symbol: string; impact: string };

export type Headline = {
  kind: "headline";
  id: string;
  time: string | null;
  important: boolean;
  content: string;
  impact: HeadlineImpact[];
};

export type HeadlinesStatus = "connecting" | "live" | "down";

type ClientFrame =
  | { type: "snapshot"; items: Headline[] }
  | { type: "headline"; item: Headline; degraded?: boolean }
  | { type: "status"; state: string };

export function useHeadlines() {
  const getToken = useRealtimeAuth();
  // REL-246 (R-654): the auth provider hands out a fresh getToken identity on
  // every render. Keying the socket effect on it tore down and reopened the
  // socket with attempt=0 on auth churn, defeating reconnect backoff. Mirror
  // IBStatusContext: read the latest getter through a ref.
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;
  const [items, setItems] = useState<Headline[]>([]);
  const [status, setStatus] = useState<HeadlinesStatus>("connecting");

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_RADON_DEMO === "1") {
      let stopped = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let request: AbortController | null = null;
      // REL-246 (R-663): back off the poll cadence on consecutive failures
      // instead of re-arming at the fixed interval forever.
      let failures = 0;

      async function refresh() {
        request = new AbortController();
        try {
          const response = await fetch("/api/headlines", {
            cache: "no-store",
            signal: request.signal,
          });
          if (!response.ok) throw new Error("Headline snapshot unavailable");
          const payload = await response.json() as { items?: unknown; degraded?: unknown };
          if (!Array.isArray(payload.items)) throw new Error("Invalid headline snapshot");
          failures = 0;
          if (!stopped) {
            setItems(payload.items as Headline[]);
            setStatus(payload.degraded === true ? "down" : "live");
          }
        } catch {
          failures += 1;
          if (!stopped) setStatus("down");
        } finally {
          request = null;
          if (!stopped) {
            const delay = Math.min(
              DEMO_HEADLINES_POLL_MS * 2 ** Math.min(failures, 10),
              DEMO_HEADLINES_POLL_BACKOFF_MAX_MS,
            );
            timer = setTimeout(() => void refresh(), delay);
          }
        }
      }

      void refresh();
      return () => {
        stopped = true;
        if (timer != null) clearTimeout(timer);
        request?.abort();
      };
    }

    if (typeof WebSocket === "undefined") return;
    let stopped = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const clearTimer = () => {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    function applyFrame(frame: ClientFrame) {
      if (frame.type === "snapshot" && Array.isArray(frame.items)) {
        setItems(frame.items);
        setStatus("live");
        return;
      }
      if (frame.type === "headline" && frame.item?.id) {
        setItems((prev) => {
          const next = prev.filter((row) => row.id !== frame.item.id);
          next.push(frame.item);
          return next.slice(-50);
        });
        // REL-182 (R-513): a flash-REST-fed row (degraded) updates the tape
        // but must not clear the down banner — the upstream is still dead.
        if (frame.degraded !== true) setStatus("live");
        return;
      }
      if (frame.type === "status" && frame.state === "upstream-down") {
        setStatus("down");
        return;
      }
      if (frame.type === "status" && frame.state === "upstream-open") {
        setStatus("live");
      }
    }

    async function open() {
      if (stopped) return;
      try {
        const url = await buildHeadlinesWebSocketUrl(getTokenRef.current);
        // R-460: cleanup can run during the await above; a socket built after
        // it would never be closed (its onclose returns on `stopped`).
        if (stopped) return;
        if (headlinesUrlLeaksUpstream(url)) {
          setStatus("down");
          return;
        }
        socket = new WebSocket(url);
        socket.onopen = () => {
          attempt = 0;
        };
        socket.onmessage = (event) => {
          try {
            applyFrame(JSON.parse(String(event.data)) as ClientFrame);
          } catch {
            // drop malformed frames
          }
        };
        socket.onerror = () => {
          setStatus("down");
        };
        socket.onclose = () => {
          socket = null;
          if (stopped) return;
          setStatus("down");
          const delay = reconnectDelayMs(attempt);
          attempt += 1;
          timer = setTimeout(() => {
            void open();
          }, delay);
        };
      } catch {
        if (stopped) return;
        setStatus("down");
        const delay = reconnectDelayMs(attempt);
        attempt += 1;
        timer = setTimeout(() => {
          void open();
        }, delay);
      }
    }

    void open();
    return () => {
      stopped = true;
      clearTimer();
      socket?.close();
    };
    // REL-246 (R-654): getToken is read via getTokenRef so auth-provider
    // identity churn cannot recycle the socket.
  }, []);

  return { items, status };
}
