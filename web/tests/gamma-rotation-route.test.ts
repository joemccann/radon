import { describe, expect, it, vi, beforeEach } from "vitest";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: (...args: unknown[]) => mockRadonFetch(...args) }));

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

function dbStub(rows: Array<Record<string, unknown>>) {
  return {
    execute: vi.fn().mockResolvedValue({ rows }),
  };
}

describe("/api/gamma-rotation", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockRadonFetch.mockImplementation(() => new Promise(() => {}));
    mockGetDb.mockReset();
  });

  it("GET returns the latest Turso snapshot", async () => {
    mockGetDb.mockReturnValue(dbStub([
      {
        payload: JSON.stringify({
          scan_time: new Date().toISOString(),
          signal: { grg_z: 1.23 },
          assets: { SPY: { ticker: "SPY" }, TLT: { ticker: "TLT" } },
          history: [],
        }),
      },
    ]));

    const { GET } = await import("../app/api/gamma-rotation/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.signal.grg_z).toBe(1.23);
  });

  it("GET serves the last good read from memory when Turso later has no row", async () => {
    const snapshot = {
      scan_time: new Date().toISOString(),
      signal: { grg_z: 1.23 },
      assets: { SPY: { ticker: "SPY" }, TLT: { ticker: "TLT" } },
      history: [],
    };
    mockGetDb.mockReturnValue(dbStub([{ payload: JSON.stringify(snapshot) }]));
    const { GET } = await import("../app/api/gamma-rotation/route");
    const { invalidateCache } = await import("@/lib/dbCache");
    await GET();
    invalidateCache("gamma-rotation:snapshot");
    mockGetDb.mockReturnValue(dbStub([]));
    const body = await (await GET()).json();
    expect(body.signal.grg_z).toBe(1.23);
    expect(body.scan_time).toBe(snapshot.scan_time);
  });

  it("POST returns fresh FastAPI scan data", async () => {
    mockRadonFetch.mockResolvedValue({
      scan_time: "2026-05-31T15:00:00Z",
      signal: { grg_z: 2.5 },
      assets: { SPY: { ticker: "SPY" }, TLT: { ticker: "TLT" } },
      history: [],
    });

    const { POST } = await import("../app/api/gamma-rotation/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.signal.grg_z).toBe(2.5);
    expect(mockRadonFetch).toHaveBeenCalledWith("/gamma-rotation/scan", { method: "POST", timeout: 130_000 });
  });
});
