/**
 * The realtime relay bounds what one authenticated client can make it do:
 * per-message list sizes, the WebSocket frame size, the snapshot queue, and
 * the snapshot work a client leaves behind when it disconnects.
 */
import { describe, expect, it } from "vitest";
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

  it("applies the cap to symbols, contracts and indexes", () => {
    expect(fnBody("normalizeSymbols")).toContain("capItems(");
    expect(fnBody("normalizeContracts")).toContain("capItems(");
    expect(fnBody("normalizeIndexes")).toContain("capItems(");
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
