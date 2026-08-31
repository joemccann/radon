/**
 * REL-171 (R-499): nested deadlines on the remote Gateway path are monotonic.
 *
 * Next route budget > FastAPI REMOTE_TIMEOUT_S > broker HELPER_TIMEOUT_S, so a
 * slow-but-successful broker cycle is never reported to the operator as a
 * timeout after a push is already in flight. The Python half of the chain
 * (REMOTE_TIMEOUT_S >= HELPER_TIMEOUT_S + 15) is pinned in
 * scripts/api/tests/test_services.py; this side pins the Next routes above it.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(__dirname, "..", "..");

function stripComments(text: string): string {
  return text
    .split("\n")
    .filter((line) => !line.trim().startsWith("#") && !line.trim().startsWith("//"))
    .join("\n");
}

function pythonSeconds(name: string): number {
  const src = stripComments(readFileSync(resolve(ROOT, "scripts/api/services.py"), "utf8"));
  const match = src.match(new RegExp(`^${name} = ([0-9.]+)$`, "m"));
  if (!match) throw new Error(`${name} must be a numeric literal in services.py`);
  return Number(match[1]);
}

function routeTimeoutMs(relPath: string): number {
  const src = stripComments(readFileSync(resolve(ROOT, relPath), "utf8"));
  const match = src.match(/timeout:\s*([0-9_]+)/);
  if (!match) throw new Error(`${relPath} has no radonFetch timeout`);
  return Number(match[1].replace(/_/g, ""));
}

describe("remote Gateway deadlines are monotonic (REL-171)", () => {
  const remoteMs = pythonSeconds("REMOTE_TIMEOUT_S") * 1000;

  it("the admin [unit]/[action] route outlives FastAPI's remote budget", () => {
    expect(routeTimeoutMs("web/app/api/admin/services/[unit]/[action]/route.ts")).toBeGreaterThan(remoteMs);
  });

  it("the /ib/restart route outlives FastAPI's remote budget", () => {
    expect(routeTimeoutMs("web/app/api/admin/ib/restart/route.ts")).toBeGreaterThan(remoteMs);
  });

  it("both routes stay inside the Caddy response-header backstop", () => {
    const caddy = stripComments(readFileSync(resolve(ROOT, "cloud/caddy/Caddyfile"), "utf8"));
    const backstops = [...caddy.matchAll(/response_header_timeout (\d+)s/g)].map((m) => Number(m[1]) * 1000);
    const ceiling = Math.max(...backstops);
    for (const rel of [
      "web/app/api/admin/services/[unit]/[action]/route.ts",
      "web/app/api/admin/ib/restart/route.ts",
    ]) {
      expect(routeTimeoutMs(rel)).toBeLessThan(ceiling);
    }
  });
});
