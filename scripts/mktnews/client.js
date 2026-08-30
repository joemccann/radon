import WebSocket from "ws";

import { reconnectDelayMs } from "../lib/reconnectGate.js";
import { MAX_FRAME_BYTES } from "./normalize.js";
import {
  DEFAULT_FLASH_URL,
  DEFAULT_ORIGIN,
  DEFAULT_URL,
  DEFAULT_USER_AGENT,
  parseFrame,
} from "./protocol.js";

const FLASH_HISTORY_TIMEOUT_MS = 5_000;

// R-435: a peer that drops without a FIN keeps `readyState` OPEN forever and
// `close` never fires, so nothing reconnected. The upstream sends periodic
// `time` heartbeats, so any frame within this bound is proof of life; past it
// the socket is torn down (`terminate`, not `close` -- a close handshake would
// wait on the same dead peer) and the ordinary reconnect path takes over.
export const UPSTREAM_IDLE_MS = 90_000;

export async function fetchFlashHistory({
  url = DEFAULT_FLASH_URL,
  origin = DEFAULT_ORIGIN,
  userAgent = DEFAULT_USER_AGENT,
  fetchImpl = fetch,
  timeoutMs = FLASH_HISTORY_TIMEOUT_MS,
} = {}) {
  try {
    const res = await fetchImpl(url, {
      headers: {
        Origin: origin,
        "User-Agent": userAgent,
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return [];
    const body = await res.json();
    const rows = Array.isArray(body?.data) ? body.data : Array.isArray(body) ? body : [];
    const frames = [];
    for (const row of rows.slice().reverse()) {
      const frame = parseFrame(JSON.stringify(row));
      if (frame.kind === "flash") frames.push(frame);
    }
    return frames;
  } catch {
    return [];
  }
}

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
  idleTimeoutMs = UPSTREAM_IDLE_MS,
} = {}) {
  let attempt = 0;
  let socket = null;
  let timer = null;
  let idleTimer = null;
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

  function clearIdleTimer() {
    if (idleTimer != null) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  function armIdleTimer(target) {
    clearIdleTimer();
    if (!(idleTimeoutMs > 0)) return;
    idleTimer = setTimeout(() => {
      idleTimer = null;
      if (stopped || socket !== target) return;
      status("idle", { idleMs: idleTimeoutMs });
      try {
        target.terminate();
      } catch {
        // already gone; the close handler reconnects either way
      }
    }, idleTimeoutMs);
  }

  function open() {
    if (stopped || signal?.aborted) return;
    clearTimer();
    const ws = new WebSocketImpl(url, {
      maxPayload,
      headers: {
        Origin: origin,
        "User-Agent": userAgent,
      },
    });
    socket = ws;
    ws.on("open", () => {
      attempt = 0;
      armIdleTimer(ws);
      status("open", { url });
    });
    ws.on("message", (data) => {
      armIdleTimer(ws);
      onMessage?.(parseFrame(data));
    });
    ws.on("error", (error) => {
      status("error", { error: error?.message ?? String(error) });
    });
    ws.on("close", (code, reason) => {
      if (socket === ws) {
        clearIdleTimer();
        socket = null;
      }
      status("close", { code, reason: reason?.toString?.() ?? String(reason ?? "") });
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
    clearIdleTimer();
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
