/**
 * @vitest-environment node
 *
 * AI infrastructure has no demo producer: the demo VM FastAPI does not serve
 * /ai-cycle. Demo principals are completed at request time; operators keep
 * the FastAPI read unchanged.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildDemoAiCycle } from "@/lib/demo/fixtures/aiCycle";
import { fetchAiInfrastructure } from "@/lib/useAiInfrastructure";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

const NOW = new Date("2026-10-14T15:00:00.000Z");

function as(kind: "demo" | "operator"): void {
  mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: `${kind}-user`, kind } });
}

beforeEach(() => {
  vi.resetModules();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  as("demo");
  mocks.radonFetch.mockRejectedValue(new Error("FastAPI must not be reached"));
});

describe("demo AI infrastructure fixture", () => {
  it("passes the client's snapshot contract", async () => {
    const snapshot = buildDemoAiCycle(NOW);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(snapshot))));
    await expect(fetchAiInfrastructure("/api/ai-cycle")).resolves.toEqual(snapshot);
    vi.unstubAllGlobals();
  });

  it("is stamped at request time and labels every source as sample data", () => {
    const snapshot = buildDemoAiCycle(NOW);
    expect(snapshot.generated_at).toBe(NOW.toISOString());
    expect(snapshot.sources.length).toBeGreaterThan(0);
    expect(snapshot.sources.every((source) => source.name === "Sample data")).toBe(true);
    const sourceIds = new Set(snapshot.sources.map((source) => source.id));
    for (const indicator of snapshot.indicators) {
      expect(indicator.status).toBe("available");
      expect(indicator.metrics.length).toBeGreaterThan(0);
      expect(indicator.history.length).toBeGreaterThan(1);
      expect(indicator.source_ids.every((id) => sourceIds.has(id))).toBe(true);
    }
    expect(new Set(snapshot.indicators.map((indicator) => indicator.pane))).toEqual(
      new Set(["demand", "compute", "delivery", "finance"]),
    );
  });
});

describe("GET /api/ai-cycle", () => {
  it("serves demo principals the request-time sample without FastAPI", async () => {
    const { GET } = await import("../app/api/ai-cycle/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body = await response.json();
    expect(body.version).toBe(1);
    expect(body.sources[0].name).toBe("Sample data");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("keeps the operator FastAPI read", async () => {
    as("operator");
    mocks.radonFetch.mockResolvedValue({ version: 1, indicators: [], sources: [], shadow: {} });
    const { GET } = await import("../app/api/ai-cycle/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledWith("/ai-cycle", { timeout: 45_000, cache: "no-store" });
  });
});
