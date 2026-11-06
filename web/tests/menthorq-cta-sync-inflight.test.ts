/**
 * Audit 2026-11-03 §3.5: the CTA route cleared its in-flight flag on a 30 s
 * timer while the detached sync child was still running, so the next stale
 * poll spawned a second sync against the same lock and status file. The flag
 * now clears only when the child exits (or fails to spawn).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("fs/promises", () => ({
  readdir: vi.fn(),
  readFile: vi.fn(),
  stat: vi.fn(),
}));

type Handler = (...args: unknown[]) => void;
const children: Array<{ handlers: Record<string, Handler> }> = [];
vi.mock("child_process", () => ({
  spawn: vi.fn(() => {
    const child = {
      handlers: {} as Record<string, Handler>,
      on(event: string, cb: Handler) {
        child.handlers[event] = cb;
        return child;
      },
      unref: vi.fn(),
    };
    children.push(child);
    return child;
  }),
}));

import { readFile, readdir, stat } from "fs/promises";
import { spawn } from "child_process";

describe("GET /api/menthorq/cta background sync lifecycle", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    children.length = 0;
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-12T22:15:00Z"));
    vi.mocked(readdir).mockResolvedValue(["cta_2026-03-11.json"] as never);
    vi.mocked(readFile).mockImplementation(async (path) => {
      if (String(path).includes("cta-sync")) throw new Error("ENOENT");
      return JSON.stringify({
        date: "2026-03-11",
        fetched_at: "2026-03-12T01:00:00Z",
        tables: { main: [{ underlying: "SPX" }], index: [], commodity: [], currency: [] },
      });
    });
    vi.mocked(stat).mockResolvedValue({ mtimeMs: Date.now() - 60_000 } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not spawn a second sync while the first child is still running", async () => {
    const { GET } = await import("../app/api/menthorq/cta/route");
    await GET();
    expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(31_000);
    await GET();
    expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);

    children[0].handlers.close?.(0);
    await GET();
    expect(vi.mocked(spawn)).toHaveBeenCalledTimes(2);
  });
});
