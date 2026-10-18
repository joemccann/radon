/**
 * @vitest-environment node
 *
 * The demo Turso never gets a `catalysts` table (only scripts/fetch_catalysts.py
 * creates it), so demo principals are completed at request time; operators keep
 * the Turso / disk read and its missing state unchanged.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildDemoCatalysts } from "@/lib/demo/fixtures/catalysts";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  dbExecute: vi.fn(),
  readFile: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/dbExecute", () => ({
  dbExecute: mocks.dbExecute,
  describeDbError: (error: unknown) => String(error),
}));
vi.mock("fs/promises", () => ({ readFile: mocks.readFile }));

const NOW = new Date("2026-10-14T15:00:00.000Z");

function as(kind: "demo" | "operator"): void {
  mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: `${kind}-user`, kind } });
}

beforeEach(() => {
  vi.resetModules();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  as("demo");
  mocks.dbExecute.mockRejectedValue(new Error("database must not be reached"));
  mocks.readFile.mockRejectedValue(new Error("disk must not be reached"));
});

describe("demo catalysts fixture", () => {
  it("returns upcoming rows relative to the injected clock with sample provenance", () => {
    const feed = buildDemoCatalysts(NOW);
    expect(feed.scan_time).toBe(NOW.toISOString());
    expect(feed.count).toBe(feed.catalysts.length);
    expect(feed.count).toBeGreaterThan(0);
    expect(feed.catalysts.every((row) => row.source === "Sample data")).toBe(true);
    expect(feed.catalysts.every((row) => row.days_until >= 0 && row.date >= "2026-10-14")).toBe(true);
    expect(new Set(feed.catalysts.map((row) => row.type))).toEqual(new Set(["earnings", "economic", "fda"]));
  });
});

describe("GET /api/catalysts", () => {
  it("serves demo principals the request-time sample without Turso", async () => {
    const { GET } = await import("../app/api/catalysts/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body = await response.json();
    expect(body.missing).toBeUndefined();
    expect(body.count).toBeGreaterThan(0);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(mocks.readFile).not.toHaveBeenCalled();
  });

  it("keeps the operator Turso read and its missing state", async () => {
    as("operator");
    mocks.dbExecute.mockRejectedValue(new Error("no such table: catalysts"));
    mocks.readFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/catalysts/route");
    const body = await (await GET()).json();
    expect(body.missing).toBe(true);
    expect(mocks.dbExecute).toHaveBeenCalled();
  });
});
