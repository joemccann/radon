/**
 * Bug guard: /api/menthorq/cta must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
    unref: vi.fn(),
  })),
}));

const mockExecute = vi.hoisted(() => vi.fn());
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));


function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/menthorq/cta — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    mockExecute.mockImplementation(async ({ sql }: { sql: string }) => ({
      rows: sql.includes("menthorq_cta")
        ? [{
            date: "2026-05-08",
            fetched_at: "2026-05-08T20:00:00Z",
            payload: JSON.stringify({
              date: "2026-05-08",
              fetched_at: "2026-05-08T20:00:00Z",
              tables: { main: [], index: [], commodity: [], currency: [] },
            }),
          }]
        : [],
    }));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store on the primary success path", async () => {
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expectNoStore(res);
  });

  it("GET sets no-store on the 503 cold-cache envelope", async () => {
    mockExecute.mockResolvedValue({ rows: [] });
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expectNoStore(res);
  });
});
