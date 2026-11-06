import { beforeEach, describe, expect, it, vi } from "vitest";

const mockWriteFile = vi.fn();
const mockMkdir = vi.fn();
const mockSpawn = vi.fn();
const mockGetDb = vi.fn();

vi.mock("fs/promises", () => ({
  writeFile: mockWriteFile,
  mkdir: mockMkdir,
}));

vi.mock("child_process", () => ({
  spawn: mockSpawn,
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: () => mockGetDb(),
}));

function makeHistory(length: number, withRvol: boolean) {
  return Array.from({ length }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 20 + index,
    vvix: 90 + index,
    spy: 600 - index,
    cor1m: 30 + index,
    realized_vol: withRvol ? 10 + index : null,
    spx_vs_ma_pct: -1,
    vix_5d_roc: 2,
  }));
}

function criRow(overrides: Record<string, unknown>) {
  const payload = {
    scan_time: "2026-03-11T16:05:00-04:00",
    market_open: false,
    date: "2026-03-11",
    realized_vol: 16.7,
    history: makeHistory(20, true),
    ...overrides,
  };
  return { taken_at: "2026-03-11T20:05:00Z", payload: JSON.stringify(payload) };
}

describe("/api/regime route reads Turso, then memory", () => {
  beforeEach(() => {
    vi.resetModules();
    mockWriteFile.mockReset();
    mockMkdir.mockReset();
    mockSpawn.mockReset();
    mockWriteFile.mockResolvedValue(undefined);
    mockMkdir.mockResolvedValue(undefined);
    mockGetDb.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("keeps the last complete read when a newer row has an incomplete RVOL history", async () => {
    mockGetDb.mockReturnValue({ execute: async () => ({ rows: [criRow({})] }) });
    const { GET } = await import("../app/api/regime/route");
    await GET();
    mockGetDb.mockReturnValue({
      execute: async () => ({ rows: [criRow({ realized_vol: 9.5, history: makeHistory(10, false) })] }),
    });
    await new Promise((resolve) => setTimeout(resolve, 5_100));
    const body = await (await GET()).json();

    expect(body.realized_vol).toBe(16.7);
    expect(body.history).toHaveLength(20);
    expect(body.history.every((entry: { realized_vol: number | null }) => typeof entry.realized_vol === "number")).toBe(true);
    expect(mockSpawn).not.toHaveBeenCalled();
  }, 10_000);

  it("serves an incomplete row when nothing better is remembered", async () => {
    mockGetDb.mockReturnValue({
      execute: async () => ({ rows: [criRow({ realized_vol: 9.5, history: makeHistory(10, false) })] }),
    });
    const { GET } = await import("../app/api/regime/route");
    const body = await (await GET()).json();
    expect(body.realized_vol).toBe(9.5);
  });

  it("serves the last good read when the libsql snapshot read hangs (does not pin the loader)", async () => {
    // The decisive /regime/cri bug: db.execute() stalls for minutes on a reachable
    // Turso endpoint, and a hang is not a rejection. The read must stay bounded.
    mockGetDb.mockReturnValue({ execute: async () => ({ rows: [criRow({})] }) });
    const { GET } = await import("../app/api/regime/route");
    await GET();
    mockGetDb.mockReturnValue({
      execute: () => new Promise(() => {}), // never resolves — simulates the stall
    });
    await new Promise((resolve) => setTimeout(resolve, 5_100));

    const start = Date.now();
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.realized_vol).toBe(16.7);
    expect(Date.now() - start).toBeLessThan(6000);
  }, 20_000);

  it("reports missing when Turso hangs and nothing is remembered", async () => {
    mockGetDb.mockReturnValue({ execute: () => new Promise(() => {}) });
    const { GET } = await import("../app/api/regime/route");
    const body = await (await GET()).json();
    expect(body.missing).toBe(true);
    expect(body.cri).toBeNull();
  }, 10_000);
});
