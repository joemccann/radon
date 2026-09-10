import { beforeEach, describe, expect, it, vi } from "vitest";

import { isBpiPayload } from "@/lib/bpi";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  dbExecute: vi.fn(),
  getDb: vi.fn(),
  readFile: vi.fn(),
  readdir: vi.fn(),
  writeFile: vi.fn(),
  stat: vi.fn(),
  mkdir: vi.fn(),
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/dbExecute", () => ({ dbExecute: mocks.dbExecute }));
vi.mock("@/lib/db", () => ({ getDb: mocks.getDb, resetDb: vi.fn() }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: mocks.radonFetch }));
vi.mock("fs/promises", () => ({
  readFile: mocks.readFile,
  readdir: mocks.readdir,
  writeFile: mocks.writeFile,
  stat: mocks.stat,
  mkdir: mocks.mkdir,
}));

beforeEach(() => {
  vi.resetModules();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.requireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "demo-user", kind: "demo" },
  });
  mocks.dbExecute.mockRejectedValue(new Error("database must not be reached"));
  mocks.getDb.mockImplementation(() => { throw new Error("database must not be reached"); });
  mocks.readFile.mockRejectedValue(new Error("disk must not be reached"));
  mocks.readdir.mockRejectedValue(new Error("disk must not be reached"));
  mocks.radonFetch.mockRejectedValue(new Error("FastAPI must not be reached"));
});

function expectDemoResponse(response: Response): void {
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toContain("no-store");
}

function expectNoUpstreamWork(): void {
  expect(mocks.dbExecute).not.toHaveBeenCalled();
  expect(mocks.getDb).not.toHaveBeenCalled();
  expect(mocks.readFile).not.toHaveBeenCalled();
  expect(mocks.readdir).not.toHaveBeenCalled();
  expect(mocks.writeFile).not.toHaveBeenCalled();
  expect(mocks.stat).not.toHaveBeenCalled();
  expect(mocks.mkdir).not.toHaveBeenCalled();
  expect(mocks.radonFetch).not.toHaveBeenCalled();
}

describe("demo regime GET routes", () => {
  it("checks route access before selecting a demo fixture", async () => {
    const denied = new Response(JSON.stringify({ error: "Forbidden" }), { status: 403 });
    mocks.requireRouteAccess.mockResolvedValueOnce({ ok: false, response: denied });
    const { GET } = await import("../app/api/bpi/route");

    const response = await GET();

    expect(response).toBe(denied);
    expectNoUpstreamWork();
  });

  it("serves CRI without DB, disk, or FastAPI work", async () => {
    const { GET } = await import("../app/api/regime/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.cri.score).toBeGreaterThan(0);
    expectNoUpstreamWork();
  });

  it("serves VCG without DB, disk, or background work", async () => {
    const { GET } = await import("../app/api/vcg/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.signal.vcg_adj).not.toBeNull();
    expectNoUpstreamWork();
  });

  it("serves gamma rotation without DB, disk, or background work", async () => {
    const { GET } = await import("../app/api/gamma-rotation/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.signal.grg_z).not.toBeNull();
    expectNoUpstreamWork();
  });

  it("serves GEX without DB, disk, or background work", async () => {
    const { GET } = await import("../app/api/gex/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.profile.length).toBeGreaterThan(0);
    expectNoUpstreamWork();
  });

  it("serves dispersion without DB or disk work", async () => {
    const { GET } = await import("../app/api/dispersion/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.current).not.toBeNull();
    expectNoUpstreamWork();
  });

  it("serves TRIN without DB or disk work", async () => {
    const { GET } = await import("../app/api/trin/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(body.current.trin).not.toBeNull();
    expectNoUpstreamWork();
  });

  it("serves all three BPI indices without DB or disk work", async () => {
    const { GET } = await import("../app/api/bpi/route");
    const response = await GET();
    const body = await response.json();
    expectDemoResponse(response);
    expect(Object.values(body.indices).every(isBpiPayload)).toBe(true);
    expectNoUpstreamWork();
  });
});

describe("demo producer routes", () => {
  it.each([
    ["CRI", () => import("../app/api/regime/route")],
    ["gamma rotation", () => import("../app/api/gamma-rotation/route")],
    ["GEX", () => import("../app/api/gex/route")],
  ])("serves %s POST from the same fixture without spawning work", async (_label, loadRoute) => {
    const { POST } = await loadRoute();
    const response = await POST();
    expectDemoResponse(response);
    expectNoUpstreamWork();
  });
});
