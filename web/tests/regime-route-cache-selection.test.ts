import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockReaddir = vi.fn();
const mockWriteFile = vi.fn();
const mockStat = vi.fn();
const mockMkdir = vi.fn();
const mockSpawn = vi.fn();

vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  readdir: mockReaddir,
  writeFile: mockWriteFile,
  stat: mockStat,
  mkdir: mockMkdir,
}));

vi.mock("child_process", () => ({
  spawn: mockSpawn,
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

describe("/api/regime route cache selection", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockReaddir.mockReset();
    mockWriteFile.mockReset();
    mockStat.mockReset();
    mockMkdir.mockReset();
    mockSpawn.mockReset();
    mockWriteFile.mockResolvedValue(undefined);
    mockMkdir.mockResolvedValue(undefined);
  });

  it("prefers the richer cri.json cache when the latest scheduled file has incomplete RVOL history", async () => {
    mockReaddir.mockResolvedValue(["cri-2099-12-31T23-59.json"]);
    mockStat.mockImplementation(async (filePath: string) => {
      if (filePath.includes("cri-2099-12-31T23-59.json")) {
        return { mtimeMs: Date.parse("2026-03-11T21:00:00Z") };
      }
      return { mtimeMs: Date.parse("2026-03-11T21:05:00Z") };
    });
    mockReadFile.mockImplementation(async (filePath: string) => {
      if (filePath.includes("cri-2099-12-31T23-59.json")) {
        return JSON.stringify({
          scan_time: "2026-03-11T16:00:00-04:00",
          market_open: false,
          date: "2026-03-11",
          realized_vol: 9.5,
          history: makeHistory(10, false),
        });
      }

      if (filePath.includes("cri.json")) {
        return JSON.stringify({
          scan_time: "2026-03-11T16:05:00-04:00",
          market_open: false,
          date: "2026-03-11",
          realized_vol: 16.7,
          history: makeHistory(20, true),
        });
      }

      throw new Error(`unexpected read: ${filePath}`);
    });

    const { GET } = await import("../app/api/regime/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.realized_vol).toBe(16.7);
    expect(body.history).toHaveLength(20);
    expect(body.history.every((entry: { realized_vol: number | null }) => typeof entry.realized_vol === "number")).toBe(true);
    expect(mockSpawn).not.toHaveBeenCalled();
  });
});
