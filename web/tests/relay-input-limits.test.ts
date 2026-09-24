/**
 * The realtime relay bounds what one authenticated client can make it do:
 * per-message list sizes, the WebSocket frame size, the snapshot queue, and
 * the snapshot work a client leaves behind when it disconnects.
 */
import { describe, expect, it, vi } from "vitest";
import { runInNewContext } from "node:vm";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  MAX_ITEMS_PER_MESSAGE,
  MAX_WS_PAYLOAD_BYTES,
  capItems,
} from "../../scripts/lib/relayLimits.js";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const source = readFileSync(resolve(__dirname, "..", "..", "scripts", "ib_realtime_server.js"), "utf8");

function fnBody(name: string): string {
  const start = source.indexOf(`function ${name}(`);
  expect(start, `${name} exists`).toBeGreaterThan(-1);
  const next = source.indexOf("\nfunction ", start + 1);
  const nextAsync = source.indexOf("\nasync function ", start + 1);
  const ends = [next, nextAsync].filter((i) => i > -1);
  return source.slice(start, ends.length ? Math.min(...ends) : undefined);
}

describe("relay input limits", () => {
  it("caps list length per message", () => {
    const huge = Array.from({ length: MAX_ITEMS_PER_MESSAGE * 3 }, (_, i) => `S${i}`);
    expect(capItems(huge)).toHaveLength(MAX_ITEMS_PER_MESSAGE);
    expect(capItems(["AAPL"])).toEqual(["AAPL"]);
    expect(MAX_ITEMS_PER_MESSAGE).toBeLessThanOrEqual(1000);
  });

  it("keeps the frame cap far below the ws 100 MiB default", () => {
    expect(MAX_WS_PAYLOAD_BYTES).toBeLessThanOrEqual(1024 * 1024);
    expect(source).toMatch(/new WebSocketServer\(\{[^}]*maxPayload:\s*MAX_WS_PAYLOAD_BYTES/);
  });

  it.each(["normalizeSymbols", "normalizeContracts", "normalizeIndexes"])(
    "%s bounds output and normalization work", (name) => {
      // Execute the production function with the real pure cap helper;
      // importing the relay itself would start sockets and a broker client.
      const normalize = runInNewContext(`${fnBody(name)}; ${name}`, { capItems });
      const item = name === "normalizeSymbols" ? " aapl "
        : name === "normalizeContracts"
          ? { symbol: " aapl ", expiry: "20261218", strike: 100, right: "C" }
          : { symbol: " vix ", exchange: " cboe " };
      const input = Array.from({ length: MAX_ITEMS_PER_MESSAGE * 3 }, () => item);
      const output = normalize(input);
      expect(output).toHaveLength(MAX_ITEMS_PER_MESSAGE);
      expect(output[0]).toEqual(name === "normalizeSymbols" ? "AAPL"
        : name === "normalizeContracts"
          ? { symbol: "AAPL", expiry: "20261218", strike: 100, right: "C" }
          : { symbol: "VIX", exchange: "CBOE" });
      // Reading any element beyond the budget is itself excess work,
      // even if an implementation trims its output afterwards.
      for (let i = MAX_ITEMS_PER_MESSAGE; i < input.length; i += 1) {
        Object.defineProperty(input, i, { get() { throw new Error("over budget"); } });
      }
      expect(normalize(input)).toHaveLength(MAX_ITEMS_PER_MESSAGE);
      expect(normalize([])).toEqual([]);
    },
  );

  it("cancels only the departed client's sent snapshots and drops its queued work", () => {
    const departed = {}, remaining = {};
    const pending = new Map([
      [1, { client: departed, sent: true }],
      [2, { client: departed, sent: false }],
      [3, { client: remaining, sent: true }],
    ]);
    const cancelMktData = vi.fn();
    const clearSnapshot = vi.fn((id: number) => pending.delete(id));
    const disconnect = runInNewContext(`${fnBody("disconnectClient")}; disconnectClient`, {
      removeBatchBuffer: vi.fn(), snapshotRequests: pending, clearSnapshot,
      ib: { cancelMktData }, clientLastPong: new Map(), clientDroppedFrames: new Map(),
      DEPTH_ENABLED: false, clientSymbols: new Map(), clientOwnerIds: new Map(),
    });
    disconnect(departed);
    expect([...pending.keys()]).toEqual([3]);
    expect(clearSnapshot.mock.calls).toEqual([[1], [2]]);
    expect(cancelMktData.mock.calls).toEqual([[1]]);
  });

  it("bounds the snapshot limiter queue", () => {
    expect(source).toMatch(/new RateLimiter\(50,\s*\{\s*maxQueue:/);
  });

  it("drops a disconnected client's pending snapshot work", () => {
    const disconnect = fnBody("disconnectClient");
    expect(disconnect).toMatch(/snapshotRequests/);
    expect(disconnect).toMatch(/req\.client [!=]== client/);
    const snapshot = fnBody("handleSnapshotRequest");
    expect(snapshot).toMatch(/if \(!clients\.has\(client\)\) return;/);
    expect(snapshot).toMatch(/if \(!snapshotRequests\.has\(requestId\)\) return;/);
  });
});
