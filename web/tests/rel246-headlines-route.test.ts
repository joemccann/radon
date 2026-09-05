/**
 * REL-246 (R-655): a provider returning `{"data": []}` was cached as a healthy
 * snapshot for 30s and shown `live`, making a blocked provider invisible. The
 * flash feed has no signal distinguishing a quiet tape from a block, so an
 * empty payload is treated as a provider failure: stale-while-error / 503, and
 * a logged telemetry signal the watchdog can see.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  cachedReadResult: vi.fn(async (_key: string, _ttl: number, load: () => Promise<unknown>) => ({
    value: await load(),
    staleWhileError: false,
  })),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/dbCache", () => ({ cachedReadResult: mocks.cachedReadResult }));

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  mocks.requireRouteAccess.mockReset();
  mocks.requireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "demo-user", kind: "demo" },
  });
  mocks.cachedReadResult.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/headlines empty-payload and telemetry (REL-246)", () => {
  it("does not cache an empty provider payload as a healthy snapshot", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: [] }),
    }));
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();

    expect(response.status).toBe(503);
    expect(errorSpy).toHaveBeenCalledWith(
      expect.stringContaining("[headlines]"),
      expect.anything(),
    );
  });

  it("emits telemetry when the provider fetch fails outright", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 502 }));
    const { GET } = await import("../app/api/headlines/route");

    const response = await GET();

    expect(response.status).toBe(503);
    expect(errorSpy).toHaveBeenCalledWith(
      expect.stringContaining("[headlines]"),
      expect.anything(),
    );
  });
});
