import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { catalogOperations } from "@/lib/assistant/catalog";

/**
 * REL-161 (R-452): the runtime catalog that gates `call_api` (TS `OPERATIONS`)
 * must agree with the pin that owns each path. FastAPI paths are pinned in
 * `scripts/api/assistant_catalog.py` (checked against `app.routes` by pytest);
 * Next paths are pinned by the route file's own `radonCapability` export
 * (checked against disk by assistant-catalog-pin.test.ts). Nothing compared
 * `OPERATIONS` to either, so a re-pin to `admin` / `mutate.trading` upstream
 * would leave the assistant still calling the path.
 */

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO_ROOT = resolve(WEB_ROOT, "..");

/** `{ticker}` and `{symbol}` are the same slot to a pin. */
function slot(template: string): string {
  return template.replace(/\{[^}]+\}/g, "{}");
}

function pythonPins(): Map<string, string> {
  const source = readFileSync(resolve(REPO_ROOT, "scripts/api/assistant_catalog.py"), "utf8");
  const pins = new Map<string, string>();
  const entry = /\(\s*"([A-Z]+)"\s*,\s*"([^"]+)"\s*\)\s*:\s*"([a-z.]+)"/g;
  let hit: RegExpExecArray | null;
  while ((hit = entry.exec(source))) {
    pins.set(`${hit[1]} ${slot(hit[2])}`, hit[3]);
  }
  return pins;
}

function nextRoutePin(method: string, template: string): string | undefined {
  const route = template.replace(/^\/api\//, "").replace(/\{([^}]+)\}/g, "[$1]");
  const ts = resolve(WEB_ROOT, "app", "api", route, "route.ts");
  const tsx = resolve(WEB_ROOT, "app", "api", route, "route.tsx");
  let source: string;
  try {
    source = readFileSync(ts, "utf8");
  } catch {
    source = readFileSync(tsx, "utf8");
  }
  const match = source.match(/export\s+const\s+radonCapability(?:\s*:\s*[^=;]+)?\s*=\s*([^;]+);/);
  if (!match) return undefined;
  const expr = match[1].trim();
  const asString = expr.match(/^["']([a-z.]+)["']$/);
  if (asString) return asString[1];
  const pair = new RegExp(`\\b${method}\\s*:\\s*["']([a-z.]+)["']`);
  return expr.match(pair)?.[1];
}

describe("assistant catalog parity", () => {
  const python = pythonPins();

  it("parses the Python pin map", () => {
    expect(python.size).toBeGreaterThan(40);
    expect(python.get("GET /quote/{}")).toBe("read");
  });

  it("every FastAPI OPERATIONS entry carries the Python pin's capability", () => {
    const disagreements: string[] = [];
    for (const item of catalogOperations()) {
      if (item.surface !== "fastapi") continue;
      const pinned = python.get(`${item.method} ${slot(item.path)}`);
      if (pinned !== item.capability) {
        disagreements.push(`${item.method} ${item.path}: OPERATIONS=${item.capability} python=${pinned ?? "unpinned"}`);
      }
    }
    expect(disagreements).toEqual([]);
  });

  it("every Next OPERATIONS entry carries the route file's radonCapability", () => {
    const disagreements: string[] = [];
    for (const item of catalogOperations()) {
      if (item.surface !== "next") continue;
      const pinned = nextRoutePin(item.method, item.path);
      if (pinned !== item.capability) {
        disagreements.push(`${item.method} ${item.path}: OPERATIONS=${item.capability} route=${pinned ?? "unpinned"}`);
      }
    }
    expect(disagreements).toEqual([]);
  });

  it("operatorOnly mirrors the Next twin of each FastAPI mutation", () => {
    // POST /portfolio/sync, /orders/refresh and /performance are reachable
    // from the UI only through Next routes that pass operatorOnly; the
    // catalog client must not be a cheaper door to the same backend action.
    const operatorOnly = catalogOperations()
      .filter((item) => item.operatorOnly)
      .map((item) => `${item.method} ${item.path}`);
    expect(operatorOnly).toEqual(
      expect.arrayContaining(["POST /orders/refresh", "POST /performance", "POST /portfolio/sync"]),
    );
    expect(catalogOperations().find((item) => item.path === "/quote/{ticker}")?.operatorOnly).toBeFalsy();
    const workspace = catalogOperations().filter(
      (item) => item.surface === "fastapi" && item.capability === "mutate.workspace",
    );
    expect(workspace.length).toBeGreaterThan(0);
    expect(workspace.every((item) => item.operatorOnly)).toBe(true);
  });
});
