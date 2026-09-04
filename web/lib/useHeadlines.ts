"use client";

import { useEffect, useState } from "react";

import {
  buildHeadlinesWebSocketUrl,
  headlinesUrlLeaksUpstream,
} from "./headlinesSocket";
import { DEMO_HEADLINES_POLL_MS } from "./demo/headlinesPolicy";
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
  const [items, setItems] = useState<Headline[]>([]);
  const [status, setStatus] = useState<HeadlinesStatus>("connecting");

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_RADON_DEMO === "1") {
      let stopped = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let request: AbortController | null = null;

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
          if (!stopped) {
            setItems(payload.items as Headline[]);
            setStatus(payload.degraded === true ? "down" : "live");
          }
        } catch {
          if (!stopped) setStatus("down");
        } finally {
          request = null;
          if (!stopped) timer = setTimeout(() => void refresh(), DEMO_HEADLINES_POLL_MS);
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
        const url = await buildHeadlinesWebSocketUrl(getToken);
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
  }, [getToken]);

  return { items, status };
}
