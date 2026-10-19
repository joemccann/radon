/**
 * @vitest-environment node
 *
 * Demo snapshot rows reach the demo Turso through the prod->demo mirror, not
 * from a writer of their own, so they are always older than the operator's
 * copy. Routes that collapse a stale snapshot to missing must widen their
 * window for demo principals only; the operator window stays exactly as it is.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  execute: vi.fn(),
  readFile: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mocks.execute }),
  resetDb: vi.fn(),
}));
vi.mock("fs/promises", () => ({ readFile: mocks.readFile }));

const HOUR = 60 * 60_000;
// Wednesday 15:00 UTC (11:00 ET, RTH): market-aware windows resolve to "open".
const NOW = new Date("2026-10-14T15:00:00.000Z");
// Older than every operator window on these routes (26h / 48h), inside the
// demo window.
const ROW_AGE_MS = 60 * HOUR;

function principal(kind: "operator" | "demo") {
  return { ok: true, principal: { userId: `${kind}-user`, kind } };
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.readFile.mockRejectedValue(new Error("no disk snapshot"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("demoSnapshotMaxAgeMs", () => {
  it("widens a demo principal's window to the mirror budget", async () => {
    const { demoSnapshotMaxAgeMs, DEMO_SNAPSHOT_MAX_AGE_MS } = await import("@/lib/demo/snapshotWindow");
    expect(DEMO_SNAPSHOT_MAX_AGE_MS).toBe(96 * HOUR);
    expect(demoSnapshotMaxAgeMs(48 * HOUR, "demo")).toBe(96 * HOUR);
    expect(demoSnapshotMaxAgeMs(26 * HOUR, "demo")).toBe(96 * HOUR);
  });

  it("never narrows a window that is already wider than the mirror budget", async () => {
    const { demoSnapshotMaxAgeMs } = await import("@/lib/demo/snapshotWindow");
    expect(demoSnapshotMaxAgeMs(14 * 24 * HOUR, "demo")).toBe(14 * 24 * HOUR);
  });

  it.each(["operator", "authenticated", "test", null] as const)(
    "leaves the %s window unchanged",
    async (kind) => {
      const { demoSnapshotMaxAgeMs } = await import("@/lib/demo/snapshotWindow");
      expect(demoSnapshotMaxAgeMs(48 * HOUR, kind)).toBe(48 * HOUR);
    },
  );

  it("keeps the operator window when route access is denied", async () => {
    mocks.requireRouteAccess.mockResolvedValue({ ok: false, response: new Response(null, { status: 403 }) });
    const { snapshotMaxAgeMs } = await import("@/lib/demo/snapshotWindow");
    await expect(snapshotMaxAgeMs(48 * HOUR)).resolves.toBe(48 * HOUR);
  });

  it("resolves the principal without a rate budget", async () => {
    mocks.requireRouteAccess.mockResolvedValue(principal("demo"));
    const { snapshotMaxAgeMs } = await import("@/lib/demo/snapshotWindow");
    await expect(snapshotMaxAgeMs(48 * HOUR)).resolves.toBe(96 * HOUR);
    expect(mocks.requireRouteAccess).toHaveBeenCalledWith();
  });
});

const SCAN_SNAPSHOT_ROUTES = [
  "vixts", "ivrank", "vixcor", "panic-index", "calm-streak", "credit-vix",
  "iei-hyg", "rsi-oversold", "iv-spread", "credit-spread", "ma-ratio",
  "vol-cone", "vix-pc", "skew", "skew2d",
] as const;

function agedScanRow(): void {
  const scanTime = new Date(NOW.getTime() - ROW_AGE_MS).toISOString();
  mocks.execute.mockResolvedValue({
    rows: [{ scan_time: scanTime, payload: JSON.stringify({ scan_time: scanTime, demo_window_marker: "mirrored" }) }],
  });
}

async function loadRoute(route: string): Promise<{ GET: (request?: Request) => Promise<Response> }> {
  return import(`../app/api/${route}/route`);
}

describe("snapshot routes at the principal boundary", () => {
  it.each(SCAN_SNAPSHOT_ROUTES)("%s: operator still collapses a 60h row to stale", async (route) => {
    mocks.requireRouteAccess.mockResolvedValue(principal("operator"));
    agedScanRow();
    const { GET } = await loadRoute(route);
    const body = await (await GET()).json();
    expect(body.demo_window_marker).toBeUndefined();
  });

  it.each(SCAN_SNAPSHOT_ROUTES)("%s: demo serves the mirrored 60h row", async (route) => {
    mocks.requireRouteAccess.mockResolvedValue(principal("demo"));
    agedScanRow();
    const { GET } = await loadRoute(route);
    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.demo_window_marker).toBe("mirrored");
    expect(body.stale).toBeUndefined();
  });

  it.each(SCAN_SNAPSHOT_ROUTES)("%s: demo still collapses a row older than the mirror budget", async (route) => {
    mocks.requireRouteAccess.mockResolvedValue(principal("demo"));
    const scanTime = new Date(NOW.getTime() - 120 * HOUR).toISOString();
    mocks.execute.mockResolvedValue({
      rows: [{ scan_time: scanTime, payload: JSON.stringify({ scan_time: scanTime, demo_window_marker: "mirrored" }) }],
    });
    const { GET } = await loadRoute(route);
    const body = await (await GET()).json();
    expect(body.demo_window_marker).toBeUndefined();
  });

  it.each([
    ["operator", true],
    ["demo", false],
  ] as const)("equibles-filing-forensics: %s dossier stale=%s at 60h", async (kind, stale) => {
    mocks.requireRouteAccess.mockResolvedValue(principal(kind));
    const asOf = new Date(NOW.getTime() - ROW_AGE_MS).toISOString();
    mocks.execute.mockResolvedValue({
      rows: [{ as_of: asOf, payload: JSON.stringify({ ticker: "AAPL", as_of: asOf, flags: [] }) }],
    });
    const { GET } = await loadRoute("equibles-filing-forensics");
    const body = await (await GET(new Request("http://localhost/api/equibles-filing-forensics?ticker=AAPL"))).json();
    expect(body.ticker).toBe("AAPL");
    expect(body.stale).toBe(stale);
  });
});

// Routes that read `fresh` off dbFirstRead but must not take the demo window,
// each with the reason. Anything else that gates on freshness is a demo
// surface fed by the mirror and has to resolve its window through the helper.
const EXEMPT_FROM_DEMO_WINDOW: Record<string, string> = {
  "app/api/dispersion/route.ts": "demo principals get a request-time fixture",
  "app/api/trin/route.ts": "demo principals get a request-time fixture",
  "app/api/performance/route.ts": "demo principals get a request-time fixture",
  "app/api/scanner/theta/route.ts": "demo principals get a request-time fixture",
  "app/api/scanner/bounce/route.ts": "freshness only labels scan metadata; the payload is served either way",
  "app/api/scanner/strength/route.ts": "freshness only labels scan metadata; the payload is served either way",
  "app/api/scanner/vol-skew-mr/route.ts": "freshness only labels scan metadata; the payload is served either way",
};

function routeFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return routeFiles(full);
    return entry.name === "route.ts" ? [full] : [];
  });
}

describe("demo snapshot window coverage", () => {
  const webRoot = resolve(__dirname, "..");
  const gated = routeFiles(join(webRoot, "app", "api"))
    .filter((file) => {
      const text = readFileSync(file, "utf8");
      return text.includes("dbFirstRead") && /maxAgeMs\s*:/.test(text) && /\.fresh\b/.test(text);
    })
    .map((file) => relative(webRoot, file))
    .sort();

  it("finds the freshness-gated routes", () => {
    expect(gated.length).toBeGreaterThanOrEqual(SCAN_SNAPSHOT_ROUTES.length);
  });

  it("every freshness-gated route resolves its window through snapshotMaxAgeMs", () => {
    const missing = gated.filter((file) => {
      if (file in EXEMPT_FROM_DEMO_WINDOW) return false;
      const text = readFileSync(join(webRoot, file), "utf8");
      // Operator-only routes refuse demo principals before any read.
      if (/operatorOnly:\s*true/.test(text)) return false;
      return !/maxAgeMs:\s*await snapshotMaxAgeMs\(/.test(text);
    });
    expect(missing).toEqual([]);
  });

  it("keeps the exemption list honest", () => {
    expect(Object.keys(EXEMPT_FROM_DEMO_WINDOW).filter((file) => !gated.includes(file))).toEqual([]);
  });
});
