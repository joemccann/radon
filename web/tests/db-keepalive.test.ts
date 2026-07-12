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

  it("bounds a hung ping at 3s then continues the metronome", async () => {
    // R3: keepalive goes through dbExecute (3s timeout). A bare hanging
    // getDb().execute would block the metronome forever and never heal.
    execute.mockImplementation(() => new Promise<never>(() => {}));
    const stop = startDbKeepAlive(1000);

    await vi.advanceTimersByTimeAsync(1000); // first fire
    expect(execute).toHaveBeenCalledTimes(1);

    // While the underlying execute is still pending, the 3s dbExecute
    // timeout must fire so the finally-schedule can arm the next ping.
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(execute).toHaveBeenCalledTimes(2);
    expect(resetDb).toHaveBeenCalled();
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
