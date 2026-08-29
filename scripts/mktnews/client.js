import WebSocket from "ws";

import { reconnectDelayMs } from "../lib/reconnectGate.js";
import { MAX_FRAME_BYTES } from "./normalize.js";
import {
  DEFAULT_ORIGIN,
  DEFAULT_URL,
  DEFAULT_USER_AGENT,
  parseFrame,
} from "./protocol.js";

export function connectMktnews({
  url = DEFAULT_URL,
  origin = DEFAULT_ORIGIN,
  userAgent = DEFAULT_USER_AGENT,
  WebSocketImpl = WebSocket,
  onMessage,
  onStatus,
  signal,
  reconnect = true,
  delayFn = reconnectDelayMs,
  maxPayload = MAX_FRAME_BYTES,
} = {}) {
  let attempt = 0;
  let socket = null;
  let timer = null;
  let stopped = false;

  function status(event, extra = {}) {
    onStatus?.({ event, ...extra });
  }

  function clearTimer() {
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function open() {
    if (stopped || signal?.aborted) return;
    clearTimer();
    socket = new WebSocketImpl(url, {
      maxPayload,
      headers: {
        Origin: origin,
        "User-Agent": userAgent,
      },
    });
    socket.on("open", () => {
      attempt = 0;
      status("open", { url });
    });
    socket.on("message", (data) => {
      onMessage?.(parseFrame(data));
    });
    socket.on("error", (error) => {
      status("error", { error: error?.message ?? String(error) });
    });
    socket.on("close", (code, reason) => {
      status("close", { code, reason: reason?.toString?.() ?? String(reason ?? "") });
      socket = null;
      scheduleReconnect();
    });
  }

  function scheduleReconnect() {
    if (stopped || !reconnect || signal?.aborted) return;
    const delay = delayFn(attempt);
    attempt += 1;
    status("reconnect", { attempt, delayMs: delay });
    timer = setTimeout(open, delay);
  }

  function stop() {
    stopped = true;
    clearTimer();
    if (socket) {
      try {
        socket.close();
      } catch {
        // already closing
      }
      socket = null;
    }
  }

  if (signal) {
    if (signal.aborted) {
      stopped = true;
      return { stop };
    }
    signal.addEventListener("abort", stop, { once: true });
  }

  open();
  return { stop };
}
