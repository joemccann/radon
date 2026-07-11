import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { execute, resetDb } = vi.hoisted(() => ({
  execute: vi.fn(),
  resetDb: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute }),
  resetDb,
}));

import { startDbKeepAlive } from "@/lib/dbKeepAlive";

describe("startDbKeepAlive", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    execute.mockReset();
    resetDb.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("pings SELECT 1 on each interval to keep the pool warm", async () => {
    execute.mockResolvedValue({ rows: [] });
    const stop = startDbKeepAlive(1000);
    await vi.advanceTimersByTimeAsync(3500);
    expect(execute).toHaveBeenCalledTimes(3);
    expect(execute).toHaveBeenLastCalledWith("SELECT 1");
    stop();
  });

  it("drops the cached client when a ping fails (self-heal)", async () => {
    execute.mockRejectedValue(new Error("socket hang up"));
    const stop = startDbKeepAlive(1000);
    await vi.advanceTimersByTimeAsync(1100);
    expect(resetDb).toHaveBeenCalledTimes(1);
    stop();
  });

  it("never overlaps pings when the current heartbeat is still pending", async () => {
    execute.mockImplementation(() => new Promise<never>(() => {}));
    const stop = startDbKeepAlive(1000);

    await vi.advanceTimersByTimeAsync(10_000);

    expect(execute).toHaveBeenCalledTimes(1);
    stop();
  });

  it("stop() halts further pings", async () => {
    execute.mockResolvedValue({ rows: [] });
    const stop = startDbKeepAlive(1000);
    await vi.advanceTimersByTimeAsync(1100);
    const callsBeforeStop = execute.mock.calls.length;
    stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(execute.mock.calls.length).toBe(callsBeforeStop);
  });
});
