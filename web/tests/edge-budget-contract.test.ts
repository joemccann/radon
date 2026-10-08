import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS,
  BROWSER_PRODUCER_SYNC_TIMEOUT_MS,
  EDGE_RESPONSE_HEADER_TIMEOUT_MS,
  PRODUCER_SYNC_WAIT_MS,
} from "../lib/edgeBudget";
import { REALTIME_HEALTH_TIMEOUT_MS } from "../lib/realtimeDeadline";

/**
 * Caddy's catch-all `handle` cuts every non-/scan, non-/assistant route at
 * response_header_timeout. A Next route that waits on FastAPI past that bound
 * can never deliver its own fallback: the browser already has Caddy's 504.
 * Every browser-facing wait is derived from the edge, never the reverse.
 */
function caddyCatchAllHeaderTimeoutMs(): number {
  const caddyfile = readFileSync(
    path.resolve(__dirname, "../../cloud/caddy/Caddyfile"),
    "utf8",
  );
  const catchAll = caddyfile.match(/\n {4}handle \{[\s\S]*?response_header_timeout (\d+)s/);
  if (!catchAll) throw new Error("catch-all handle block not found in Caddyfile");
  return Number(catchAll[1]) * 1_000;
}

describe("edge budget contract", () => {
  it("mirrors the Caddy catch-all response_header_timeout", () => {
    expect(EDGE_RESPONSE_HEADER_TIMEOUT_MS).toBe(caddyCatchAllHeaderTimeoutMs());
  });

  it("leaves the producer-sync route time to serve its Turso fallback before the edge cuts it", () => {
    const fallbackReadMs = 3_000;
    expect(PRODUCER_SYNC_WAIT_MS + fallbackReadMs).toBeLessThanOrEqual(
      EDGE_RESPONSE_HEADER_TIMEOUT_MS - 5_000,
    );
  });

  it("lets the browser outlive the route's own deadline but not the edge", () => {
    expect(BROWSER_PRODUCER_SYNC_TIMEOUT_MS).toBeGreaterThan(PRODUCER_SYNC_WAIT_MS + 3_000);
    expect(BROWSER_PRODUCER_SYNC_TIMEOUT_MS).toBeLessThan(EDGE_RESPONSE_HEADER_TIMEOUT_MS);
  });

  it("answers /api/admin/health before the dashboard's own health abort", () => {
    expect(ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS).toBeLessThan(REALTIME_HEALTH_TIMEOUT_MS);
  });
});
