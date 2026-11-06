/**
 * Audit 2026-11-03 §3.5: gamma-rotation and internals hand-rolled their own
 * in-flight flag (no failure backoff), and internals ran a CRI trigger that
 * was separate from regime's, so the two routes could fire two concurrent
 * /regime/scan subprocesses for the same stale snapshot. Both now go through
 * createBackgroundScanTrigger, and internals + regime share one "cri" trigger.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: (...args: unknown[]) => mockRadonFetch(...args),
  RadonApiError: class RadonApiError extends Error {
    status = 502;
  },
}));

vi.mock("fs/promises", () => ({
  readFile: vi.fn(async () => {
    throw new Error("ENOENT");
  }),
  readdir: vi.fn(async () => []),
  writeFile: vi.fn(async () => undefined),
  stat: vi.fn(async () => {
    throw new Error("ENOENT");
  }),
  mkdir: vi.fn(async () => undefined),
}));

vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => {
    throw new Error("turso unreachable");
  },
}));

const flush = () => new Promise((r) => setTimeout(r, 0));
const scanCalls = (path: string) =>
  mockRadonFetch.mock.calls.filter(([p]) => p === path).length;

describe("background scans in gamma-rotation / internals / regime", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(async () => {
    await flush();
    vi.restoreAllMocks();
  });

  it("gamma-rotation: a failed background scan arms a backoff instead of re-firing on the next poll", async () => {
    mockRadonFetch.mockImplementation(async () => {
      throw new Error("Radon API 502: Subprocess capacity exhausted");
    });
    const { GET } = await import("../app/api/gamma-rotation/route");
    await GET();
    await flush();
    await GET();
    await flush();
    expect(scanCalls("/gamma-rotation/scan")).toBe(1);
  });

  it("internals: a failed CRI scan arms a backoff instead of re-firing on the next poll", async () => {
    mockRadonFetch.mockImplementation(async () => {
      throw new Error("Radon API 502: Subprocess capacity exhausted");
    });
    const { GET } = await import("../app/api/internals/route");
    await GET();
    await flush();
    await GET();
    await flush();
    expect(scanCalls("/regime/scan")).toBe(1);
  });

  it("internals and regime share one CRI trigger: a scan in flight from one dedupes the other", async () => {
    mockRadonFetch.mockImplementation((path: string) =>
      path === "/regime/scan" ? new Promise(() => {}) : Promise.reject(new Error("offline")),
    );
    const internals = await import("../app/api/internals/route");
    const regime = await import("../app/api/regime/route");
    await internals.GET();
    await regime.GET();
    await flush();
    expect(scanCalls("/regime/scan")).toBe(1);
  });
});
